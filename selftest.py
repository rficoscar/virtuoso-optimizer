# -*- coding: utf-8 -*-
"""Virtuoso Optimizer regression suite for optimizer/algorithm/GUI/lib/EM (stdlib only).

Run via `python optimizer.py --selftest` or `python selftest.py`; the exit
code is the failure count. Windows failures: none.
"""
from __future__ import print_function

import copy
import glob
import gzip
import hashlib
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Public engine symbols (the suite uses them unqualified).
from optimizer import (  # noqa: F401
    EXAMPLE_CFG, LOG_MODE_COMPACT, LOG_MODE_FULL, MANIFEST_NAME,
    NETLIST_BASE_NAME, NETLIST_META_NAME, Optimization, RealRunner,
    RunLogger, Settings, SimResult, Target, Transform, build_optimizer,
    circuit_id,
    compact_netlist_file, compact_spectre_log, discover_tools, find_prog,
    format_eng, gzip_file_in_place, make_run_dir, parse_em_bridge_cfg,
    config_fingerprint, load_config, load_previous, parse_eng,
    parse_netlist_parameters,
    MockRunner, netlist_analyses, preflight_netlist,
    read_manifest, reconstruct_input_scs,
    resolve_netlist_path, resolve_prog, resolve_run_dir,
    rewrite_netlist_parameters,
    run_em_align, run_optimization, stop_all_runners,
    suggest_bounds, tool_report, to_jsonable, from_jsonable,
)
from optimizer import (  # private helpers referenced by the suite
    _TOOL_CACHE, _archive_aside, _log_mode_of, _native_open, _read_text,
    _sha1_file, _sha1_text,
)
from optimizer import open  # noqa: A001 - UTF-8 wrapper, shadowed on purpose
from optimizer import audit, audit_dump, audit_file, audit_sink
from algorithm import _latin_hypercube  # LHS moved here after the old engine was dropped


def example_config():
    return copy.deepcopy(EXAMPLE_CFG)


def mock_config(out_dir, max_iter):
    cfg = example_config()
    cfg["output_dir"] = out_dir
    # Goals the mock model can not meet, so the run always uses max_iter.
    cfg["targets"] = [
        {"output": "S21 dB20", "metric": "band_min", "band": ["24G", "44G"],
         "op": ">=", "goal": 6, "tol": 0.5},
        {"output": "S21 dB20", "metric": "band_flatness", "band": ["24G", "44G"],
         "op": "<=", "goal": 0.2, "tol": 0.2},
    ]
    cfg["settings"] = {"max_iter": max_iter, "patience": 100, "seed": 0}
    return cfg


def phys_opt(strategy, names, x0, bounds, seed=0):
    """Build a strategy over physical bounds: the production run-loop path."""
    tr = Transform([{"name": n, "init": x0[i], "min": bounds[i][0],
                     "max": bounds[i][1]} for i, n in enumerate(names)])
    return build_optimizer(strategy, tr, dict(zip(names, x0)), names, seed=seed)


class TestEng(unittest.TestCase):
    def test_parse(self):
        for text, want in (("250p", 250e-12), ("1.5G", 1.5e9), ("3meg", 3e6), (12.5, 12.5)):
            self.assertAlmostEqual(parse_eng(text), want)
        self.assertAlmostEqual(parse_eng(-4.7e-6), parse_eng("-4.7u"))
    def test_format(self):
        for value, want in ((0, "0"), (250e-12, "250p"), (1.5e9, "1.5G"), (-4.5e-12, "-4.5p")):
            self.assertEqual(format_eng(value), want)
    def test_bad(self):
        self.assertRaises(ValueError, parse_eng, "abc")
        self.assertRaises(ValueError, parse_eng, "12x")
    def test_suggest_bounds(self):
        lo, hi = suggest_bounds("72f")
        self.assertAlmostEqual(parse_eng(lo), 36e-15)
        self.assertAlmostEqual(parse_eng(hi), 144e-15)
        self.assertEqual(suggest_bounds("0"), ("0", ""))
        lo, hi = suggest_bounds("-2")
        self.assertLess(parse_eng(lo), parse_eng(hi))


class TestTargets(unittest.TestCase):
    curves = {"S21 dB20": ([1e9, 2e9, 3e9], [1.0, 5.0, 2.0])}
    def error(self, spec):
        return Target(spec).error(self.curves)
    def test_point_targets(self):
        spec = {"output": "S21 dB20", "metric": "band_max", "op": "<=",
                "goal": 4, "tol": 0.5}
        self.assertGreater(self.error(spec), 0.0)
        spec["goal"] = 6
        self.assertLessEqual(self.error(spec), 0.0)
    def test_missing_curve_is_large_error(self):
        spec = {"output": "none", "metric": "band_min", "op": ">=", "goal": 1, "tol": 0.1}
        self.assertGreaterEqual(self.error(spec), 1e6)
    def test_curve_target(self):
        spec = {"output": "S21 dB20", "curve_op": "above",
                "limit": {"x": [1, 2, 3], "y": [0, 4, 1]}}
        self.assertGreater(self.error(spec), 0.0)
    def test_band_metrics_known_answers(self):
        """Band metrics on a known curve: min 1, max 5, flatness 4, mean 8/3."""
        base = {"output": "S21 dB20", "op": ">=", "goal": 0,
                "band": ["1G", "3G"]}
        cases = (("band_min", 1.0), ("band_max", 5.0),
                 ("band_flatness", 4.0), ("band_mean", 8.0 / 3.0))
        for metric, want in cases:
            spec = dict(base, metric=metric)
            self.assertAlmostEqual(Target(spec).metric_value(self.curves),
                                   want, msg=metric)
        # A band that keeps only the last two points: y = 5, 2.
        narrow = dict(base, metric="band_mean", band=["2G", "3G"])
        self.assertAlmostEqual(Target(narrow).metric_value(self.curves), 3.5)
    def test_value_at_known_answer(self):
        """Linear interpolation midway between 1e9 (1.0) and 2e9 (5.0)."""
        spec = {"output": "S21 dB20", "metric": "value_at", "op": ">=",
                "goal": 0, "freq": "1.5G"}
        self.assertAlmostEqual(Target(spec).metric_value(self.curves), 3.0)
    def test_band_order_is_normalized(self):
        """Reversed band must sample the same points as the ordered one.

        em_align._parse_band already swaps lo/hi; the engine used to keep
        the reversed order, cut every point and return BIG_ERROR forever.
        """
        good = {"output": "S21 dB20", "metric": "band_mean", "op": ">=",
                "goal": 1, "tol": 1, "band": ["1G", "3G"]}
        rev = dict(good, band=["3G", "1G"])
        self.assertEqual(Target(rev).band, Target(good).band)
        self.assertAlmostEqual(Target(rev).error(self.curves),
                               Target(good).error(self.curves))
    def test_range_goal_order_is_normalized(self):
        """Swapped range bounds must not make the target unsatisfiable."""
        spec = {"output": "S21 dB20", "metric": "band_min", "op": "range",
                "goal": 5, "goal2": 1, "tol": 0.5, "band": ["1G", "3G"]}
        target = Target(spec)
        self.assertEqual((target.goal, target.goal2), (1.0, 5.0))
        self.assertLessEqual(target.error(self.curves), 0.0)

    def test_bad_band_shape_is_actionable(self):
        """One-value / string bands raise ValueError, not IndexError."""
        for band in (["1G"], "1G 3G", {"lo": "1G", "hi": "3G"}, []):
            spec = {"output": "S21 dB20", "metric": "band_mean", "band": band}
            with self.assertRaises(ValueError) as ctx:
                Target(spec)
            self.assertIn("band", str(ctx.exception), band)
    def test_value_at_must_lie_in_band(self):
        """value_at outside its own band can never resolve; fail early."""
        spec = {"output": "S21 dB20", "metric": "value_at", "op": ">=",
                "goal": 0, "freq": "4G", "band": ["1G", "3G"]}
        with self.assertRaises(ValueError) as ctx:
            Target(spec)
        self.assertIn("band", str(ctx.exception))


class TestAlgorithm(unittest.TestCase):
    """Old AskTellOptimizer simplex tests, now on build_optimizer + SpaceAdapter."""
    def test_goes_to_the_minimum(self):
        algo = phys_opt("simplex", ["a", "b"], [1.0, 0.0], [(0.0, 10.0), (-5.0, 5.0)], seed=3)
        for _ in range(120):
            pt = algo.ask()
            algo.tell((pt[0] - 2.0) ** 2 + (pt[1] + 1.0) ** 2)
        self.assertIsNotNone(algo.inner.best_y)
        self.assertLess(algo.inner.best_y, 1e-6)
    def test_points_stay_in_bounds(self):
        algo = phys_opt("simplex", ["a"], [5.0], [(0.0, 10.0)], seed=1)
        for _ in range(40):
            pt = algo.ask()
            self.assertGreaterEqual(pt[0], 0.0)
            self.assertLessEqual(pt[0], 10.0)
            algo.tell(abs(pt[0] - 8.0))
    def test_degenerate_bounds(self):
        algo = phys_opt("simplex", ["a"], [5.0], [(5.0, 5.0)], seed=1)
        self.assertEqual(algo.ask(), [5.0])
        algo.tell(0.0)
        # A zero-width variable stays pinned at its only value after a tell.
        self.assertEqual(algo.ask(), [5.0])


class TestGlobalSearch(unittest.TestCase):
    """Global search (LHS + DE) and the strategy switch."""
    def test_lhs_is_stratified(self):
        rng = random.Random(7)
        count, n = 12, 3
        pts = _latin_hypercube(count, n, rng)
        self.assertEqual(len(pts), count)
        for pt in pts:
            self.assertEqual(len(pt), n)
        for j in range(n):
            buckets = set()
            for i in range(count):
                self.assertGreaterEqual(pts[i][j], 0.0)
                self.assertLess(pts[i][j], 1.0)
                buckets.add(int(pts[i][j] * count))
            self.assertEqual(buckets, set(range(count)))
    def test_de_converges_on_quadratic(self):
        algo = phys_opt("global", ["a", "b"], [1.0, 0.0], [(0.0, 10.0), (-5.0, 5.0)], seed=3)
        for _ in range(200):
            pt = algo.ask()
            algo.tell((pt[0] - 2.0) ** 2 + (pt[1] + 1.0) ** 2)
        self.assertIsNotNone(algo.inner.best_y)
        self.assertLess(algo.inner.best_y, 1e-4)
    def test_de_handles_six_variables(self):
        names = ["v%d" % i for i in range(6)]
        algo = phys_opt("global", names, [0.0] * 6, [(-2.0, 2.0)] * 6, seed=11)
        for _ in range(1200):
            pt = algo.ask()
            algo.tell(sum((pt[i] - 0.5) ** 2 for i in range(6)))
        self.assertLess(algo.inner.best_y, 1e-3)
    def test_de_escapes_local_trap(self):
        # A shallow basin at (0.2, 0.2) traps a greedy descent; the global
        # basin at (0.85, 0.85) must win for most seeds.
        def trap(u):
            g1 = (u[0] - 0.2) ** 2 + (u[1] - 0.2) ** 2 + 0.05
            g2 = (u[0] - 0.85) ** 2 + (u[1] - 0.85) ** 2
            return min(g1, g2)
        escapes = 0
        for seed in range(1, 13):
            algo = phys_opt("global", ["a", "b"], [0.2, 0.2], [(0.0, 1.0)] * 2, seed=seed)
            for _ in range(300):
                pt = algo.ask()
                algo.tell(trap(pt))
            if algo.inner.best_y < 0.01:
                escapes += 1
        self.assertGreaterEqual(escapes, 8)
    def test_de_points_stay_in_bounds(self):
        algo = phys_opt("global", ["a", "b"], [5.0, 0.0], [(0.0, 10.0), (-5.0, 5.0)], seed=2)
        for _ in range(60):
            pt = algo.ask()
            self.assertGreaterEqual(pt[0], 0.0)
            self.assertLessEqual(pt[0], 10.0)
            self.assertGreaterEqual(pt[1], -5.0)
            self.assertLessEqual(pt[1], 5.0)
            algo.tell(1e300)  # huge values must not push later points out of the box
        # A normal evaluation afterwards still becomes the best (no corruption).
        pt = algo.ask()
        algo.tell(abs(pt[0] - 8.0))
        self.assertLess(algo.inner.best_y, 1e3)
    def test_restart_keeps_best_and_decays(self):
        for strategy in ("global", "simplex"):
            algo = phys_opt(strategy, ["a", "b"], [1.0, 0.0],
                            [(0.0, 10.0), (-5.0, 5.0)], seed=5)
            for _ in range(20):
                pt = algo.ask()
                algo.tell((pt[0] - 2.0) ** 2 + (pt[1] + 1.0) ** 2)
            best_y = algo.inner.best_y
            self.assertIsNotNone(best_y)
            algo.restart(spread=0.25)
            self.assertEqual(algo.inner.restarts, 1)
            self.assertAlmostEqual(algo.inner.last_restart_radius, 0.25)
            self.assertEqual(algo.inner.best_y, best_y)  # best survives the restart
            for _ in range(6):
                pt = algo.ask()
                self.assertGreaterEqual(pt[0], 0.0)
                self.assertLessEqual(pt[0], 10.0)
                self.assertGreaterEqual(pt[1], -5.0)
                self.assertLessEqual(pt[1], 5.0)
                algo.tell((pt[0] - 2.0) ** 2 + (pt[1] + 1.0) ** 2)
            algo.restart(spread=0.25)
            self.assertEqual(algo.inner.restarts, 2)
            self.assertLess(algo.inner.last_restart_radius, 0.25)
    def test_strategy_validation(self):
        self.assertEqual(Settings({}).strategy, "simplex")
        self.assertEqual(Settings({"strategy": "global"}).strategy, "global")
        self.assertEqual(Settings({"strategy": ""}).strategy, "simplex")
        self.assertRaises(ValueError, Settings, {"strategy": "bad"})
        try:
            Settings({"strategy": "anneal"})
        except ValueError as exc:
            msg = str(exc)
            self.assertIn("simplex", msg)
            self.assertIn("global", msg)
        else:
            self.fail("Settings must reject an illegal strategy")
    def test_strategy_selects_algorithm(self):
        import algorithm as A
        work = tempfile.mkdtemp()
        cfg = {
            "variables": [{"name": "x", "init": "1", "min": "0", "max": "2"}],
            "targets": [{"output": "o", "metric": "scalar", "op": ">=", "goal": 0, "tol": 1}],
            "output_dir": os.path.join(work, "out"),
            "settings": {"max_iter": 3, "seed": 0, "strategy": "global"},
        }
        opt = Optimization(cfg, mock=True, max_iter=3)
        self.assertIsInstance(opt.algo.inner, A.DifferentialEvolution)
        self.assertEqual(opt.algo.name, "global")
        cfg["settings"]["strategy"] = "simplex"
        cfg["output_dir"] = os.path.join(work, "out2")
        opt2 = Optimization(cfg, mock=True, max_iter=3)
        self.assertIsInstance(opt2.algo.inner, A.NelderMead)
        self.assertEqual(opt2.algo.name, "simplex")


class TestPipeline(unittest.TestCase):
    def test_mock_run_and_resume(self):
        work = tempfile.mkdtemp()
        out_dir = os.path.join(work, "out")
        report = run_optimization(mock_config(out_dir, 20), mock=True)
        self.assertEqual(report["iterations"], 20)
        self.assertIsNotNone(report["best_total_error"])
        self.assertTrue(os.path.exists(os.path.join(out_dir, "report.json")))
        for name in ("L1", "L2", "k0", "cin"):
            self.assertIn(name, report["best_variables"])
        # Resume must continue from iteration 20.
        report2 = run_optimization(mock_config(out_dir, 30), mock=True)
        self.assertEqual(report2["iterations"], 10)
        self.assertLessEqual(report2["best_total_error"], report["best_total_error"])
    def test_config_resume_false_starts_fresh(self):
        work = tempfile.mkdtemp()
        out_dir = os.path.join(work, "out")
        run_optimization(mock_config(out_dir, 5), mock=True)
        # An explicit resume argument outranks the config key.
        cfg = mock_config(out_dir, 7)
        cfg["resume"] = False
        self.assertEqual(
            run_optimization(cfg, mock=True, resume=True)["iterations"], 2)
        # Without the explicit flag "resume": false starts over: 7 - 5
        # means the historical 5 iterations were wrongly reused.
        self.assertEqual(run_optimization(cfg, mock=True)["iterations"], 7)
        # JSON string form is accepted as well.
        cfg["resume"] = "false"
        self.assertEqual(run_optimization(cfg, mock=True)["iterations"], 7)
    def test_fresh_run_archives_journal_and_ignores_snapshot(self):
        """`"resume": false` must not inherit algorithm state or events.

        A fresh start archived history/manifest/iter_* but kept
        snapshot.json and events.jsonl, so run() restored the old
        algorithm state and a later resume replayed the discarded
        run's events.
        """
        work = tempfile.mkdtemp()
        out = os.path.join(work, "out")
        run_optimization(mock_config(out, 12), mock=True, resume=False)
        snap = os.path.join(out, "snapshot.json")
        events = os.path.join(out, "events.jsonl")
        self.assertTrue(os.path.exists(snap))
        self.assertTrue(os.path.exists(events))
        opt = Optimization(mock_config(out, 7), mock=True, resume=False)
        restored = []
        opt._try_restore_snapshot = lambda: restored.append(True) or True
        opt.run()
        self.assertEqual(restored, [], "fresh run consulted the old snapshot")
        self.assertTrue(glob.glob(snap + ".old-*"), "snapshot not archived")
        self.assertTrue(glob.glob(events + ".old-*"), "events not archived")

    def test_snapshot_replays_only_events_after_it(self):
        """Kicks already folded into the snapshot must not run twice.

        The snapshot is written before the stall action of that
        iteration, so replay must be trimmed by event sequence, not by
        iteration index (a kick at the snapshot iteration is newer).
        """
        work = tempfile.mkdtemp()
        out = os.path.join(work, "out")
        opt = Optimization(mock_config(out, 20), mock=True, resume=False)
        names = opt.names
        opt.journal.log_event({"type": "kick", "index": 0,
                               "points": [dict((n, 0.1) for n in names)]})
        opt._write_snapshot()
        opt.journal.log_event({"type": "kick", "index": 1,
                               "points": [dict((n, 0.9) for n in names)]})
        state = opt.journal.load_latest()
        self.assertEqual(state.get("events"), 1)
        opt2 = Optimization(mock_config(out, 20), mock=True, resume=True)
        self.assertTrue(opt2._try_restore_snapshot())
        self.assertEqual(len(getattr(opt2.algo, "_inject", [])), 1)

    def test_simulation_failures_stop_the_run(self):
        work = tempfile.mkdtemp()
        cfg = mock_config(os.path.join(work, "out"), 20)
        cfg["virtuoso_bin"] = "/bin/false"
        cfg["settings"]["fail_limit"] = 3
        report = run_optimization(cfg, mock=False)
        self.assertEqual(report["stop_reason"], "sim_failures")
        self.assertEqual(report["iterations"], 3)
    def test_all_failed_iterations_report_sim_failures(self):
        # A bad netlist fails every iteration. With fail_limit above
        # max_iter the loop simply runs out of budget; that must not be
        # reported as a normal max_iterations completion.
        # The deck itself must pass preflight (exists, has an analysis);
        # it then fails at simulation time on every iteration.
        work = tempfile.mkdtemp()
        cfg = mock_config(os.path.join(work, "out"), 2)
        deck = os.path.join(work, "input.scs")
        with open(deck, "w") as fh:
            fh.write("simulator lang=spectre\n"
                     "sp sp ports=[PORT0 PORT1] start=1G stop=2G step=.1G\n")
        cfg["netlist"] = deck
        cfg["state_dir"] = os.path.join(work, "missing_state")
        cfg["settings"]["fail_limit"] = 5
        report = run_optimization(cfg, mock=False)
        self.assertEqual(report["stop_reason"], "sim_failures")
        self.assertEqual(report["iterations"], 2)
        self.assertIsNone(report["best_total_error"])
        self.assertEqual(report["summary"]["failures"], {"fail": 2})
    def test_stall_stops_the_run(self):
        # A goal the mock model can not meet forces a stall stop.
        work = tempfile.mkdtemp()
        cfg = mock_config(os.path.join(work, "out"), 200)
        cfg["settings"] = {"max_iter": 200, "patience": 3, "seed": 0,
                           "max_restarts": 1, "improve_rel": 1e-6}
        cfg["targets"] = [{"output": "S21 dB20", "metric": "band_min",
                           "band": ["24G", "44G"], "op": ">=", "goal": 100, "tol": 0.5}]
        report = run_optimization(cfg, mock=True)
        self.assertIn(report["stop_reason"], ("no_feasible_solution", "no_improvement"))
        self.assertLess(report["iterations"], 200)


class TestStopSemantics(unittest.TestCase):
    """targets_met must cover every target, not only the scalarized total."""

    def _cfg(self, out, mode, objective):
        cfg = mock_config(out, 3)
        cfg["settings"]["mode"] = mode
        cfg["targets"] = [{
            "output": "S21 dB20", "metric": "band_min",
            "band": ["24G", "44G"], "op": ">=", "goal": 1000,
            "tol": 1, "role": "constraint"}]
        if objective:
            cfg["targets"].insert(0, {
                "output": "S21 dB20", "metric": "band_min",
                "band": ["24G", "44G"], "op": ">=", "goal": -1000,
                "tol": 1, "role": "objective"})
        return cfg

    def test_spec_violated_constraint_not_targets_met(self):
        work = tempfile.mkdtemp()
        report = run_optimization(
            self._cfg(os.path.join(work, "out"), "spec", True), mock=True)
        self.assertNotEqual(report["stop_reason"], "targets_met")
        self.assertEqual(report["iterations"], 3)
        self.assertFalse(report["best_met"])

    def test_pareto_violated_constraint_not_targets_met(self):
        work = tempfile.mkdtemp()
        report = run_optimization(
            self._cfg(os.path.join(work, "out"), "pareto", True), mock=True)
        self.assertNotEqual(report["stop_reason"], "targets_met")
        self.assertEqual(report["iterations"], 3)

    def test_pareto_without_objective_does_not_auto_pass(self):
        # total is 0.0 when pareto has no objective; that used to make
        # every first iteration look like a success.
        work = tempfile.mkdtemp()
        report = run_optimization(
            self._cfg(os.path.join(work, "out"), "pareto", False), mock=True)
        self.assertNotEqual(report["stop_reason"], "targets_met")
        self.assertEqual(report["iterations"], 3)

    def test_spec_all_satisfied_still_stops(self):
        work = tempfile.mkdtemp()
        cfg = self._cfg(os.path.join(work, "out"), "spec", True)
        for target in cfg["targets"]:
            target["goal"] = -1000
        report = run_optimization(cfg, mock=True)
        self.assertEqual(report["stop_reason"], "targets_met")
        self.assertEqual(report["iterations"], 1)


class TestResumeIdentity(unittest.TestCase):
    """A resume must not adopt another config's history."""

    def test_settings_change_still_resumes(self):
        work = tempfile.mkdtemp()
        out = os.path.join(work, "out")
        run_optimization(mock_config(out, 4), mock=True)
        report = run_optimization(mock_config(out, 7), mock=True)
        self.assertEqual(report["iterations"], 3)

    def test_changed_targets_cannot_resume(self):
        work = tempfile.mkdtemp()
        out = os.path.join(work, "out")
        run_optimization(mock_config(out, 4), mock=True)
        changed = mock_config(out, 4)
        changed["targets"][0]["goal"] = 50
        with self.assertRaises(ValueError):
            run_optimization(changed, mock=True)

    def test_fingerprint_ignores_run_settings(self):
        cfg = mock_config("x", 1)
        same = mock_config("x", 1)
        same["settings"]["max_iter"] = 99
        same["settings"]["seed"] = 7
        self.assertEqual(config_fingerprint(cfg), config_fingerprint(same))

    def test_archived_dirs_are_not_history(self):
        work = tempfile.mkdtemp()
        out = os.path.join(work, "out")
        run_optimization(mock_config(out, 1), mock=True, resume=False)
        os.rename(os.path.join(out, "iter_0000"),
                  os.path.join(out, "iter_0000.old-20260101"))
        self.assertEqual(load_previous(out), [])


class TestFixes(unittest.TestCase):
    """Regression tests for the 2026-10-14 hardening."""
    def test_ticks_never_hang(self):
        ticks = PlotCanvas._nice_ticks(2.4e9, 2.4e9 + 1e-6)
        self.assertTrue(ticks)
        self.assertIn(0.0, PlotCanvas._nice_ticks(0.0, 10.0))
    def test_target_limit_validation(self):
        base = {"output": "o", "metric": "scalar", "op": ">=", "goal": 1}
        Target(dict(base))
        bad = dict(base)
        bad["curve_op"] = "above"
        bad["limit"] = [1, 2]
        self.assertRaises(ValueError, Target, bad)
        bad2 = dict(base)
        bad2["curve_op"] = "above"
        bad2["limit"] = {"x": [1, 2], "y": [3]}
        self.assertRaises(ValueError, Target, bad2)
        good = dict(base)
        good["curve_op"] = "below"
        good["limit"] = {"x": ["1G", "2G"], "y": ["35", "35"]}
        Target(good)
        # A range target needs goal2.
        self.assertRaises(ValueError, Target,
                          {"output": "o", "metric": "scalar", "op": "range",
                           "goal": 1, "tol": 0.1})
    def test_env_merge(self):
        r = RealRunner({"env": {"A": "1"}, "path_append": "/opt/x/bin"}, ".",
                       env={"PATH": "/usr/bin", "DISPLAY": ":0"})
        env = r._environment()
        self.assertEqual(env.get("A"), "1")
        self.assertNotIn("DISPLAY", env)
        self.assertTrue(env.get("PATH", "").endswith("/opt/x/bin"))
    def test_settings_seed(self):
        self.assertIsNone(Settings({"seed": None}).seed)
        self.assertEqual(Settings({}).seed, 0)
    def test_netlist_parameter_rewrite(self):
        # A PCell subckt keeps its own parameters line (_par0 style names);
        # only the top level line may be rewritten.
        deck = (
            "simulator lang=spectre\n"
            "global 0\n"
            "parameters k=0.6 R1=1k L1=250p\n"
            "subckt parxfmr (t1 b1 t2 b2)\n"
            "parameters _par0=k _par1=L1 _par2=250p\n"
            "K4 (t1 b1 t2 b2) ind coupling=_par0\n"
            "ends parxfmr\n"
            "I3 (a 0 b 0) parxfmr\n"
            "op\n"
        )
        self.assertEqual(parse_netlist_parameters(deck),
                         {"k": "0.6", "R1": "1k", "L1": "250p"})
        line = "parameters k=0.75 R1=1k L1=300p"
        new_text, n = rewrite_netlist_parameters(deck, line)
        self.assertEqual(n, 1)
        self.assertIn(line, new_text)
        self.assertIn("parameters _par0=k _par1=L1 _par2=250p", new_text)
        self.assertIn("coupling=_par0", new_text)
        # Multiple top-level parameters merge into one line.
        deck = ("parameters a=1\nparameters b=2\nsubckt s (p n)\n"
                "parameters a=9\nends s\n")
        self.assertEqual(parse_netlist_parameters(deck), {"a": "1", "b": "2"})
        line = "parameters a=5 b=2"
        new_text, n = rewrite_netlist_parameters(deck, line)
        self.assertEqual(n, 2)
        self.assertEqual(new_text.count(line), 1)
        self.assertIn("parameters a=9", new_text)
        self.assertNotIn("parameters b=2\nparameters", new_text)
        # Continuation lines fold into the rewriting.
        deck = "parameters a=1 \\\n+ b=2\nsubckt s (p n)\nends s\n"
        self.assertEqual(parse_netlist_parameters(deck), {"a": "1", "b": "2"})
        new_text, n = rewrite_netlist_parameters(deck, "parameters a=3 b=4")
        self.assertEqual(n, 1)
        self.assertIn("parameters a=3 b=4", new_text)
        self.assertNotIn("+ b=2", new_text)
        # No top-level parameters: report 0 and leave the deck untouched.
        deck = "subckt s (p n)\nparameters a=1\nends s\n"
        self.assertEqual(parse_netlist_parameters(deck), {})
        new_text, n = rewrite_netlist_parameters(deck, "parameters a=2")
        self.assertEqual(n, 0)
        self.assertEqual(new_text, deck)


class TestCurvesTabRemoved(unittest.TestCase):
    """The removed curves tab stays gone; the live-plot helpers remain."""
    def test_live_plot_helpers_kept(self):
        for name in ("_record_curves", "_draw_live_curves", "_output_name_list",
                     "_var_bounds_from_table", "_load_history_data"):
            self.assertTrue(hasattr(OptimizerGui, name), name)


class TestRunDirs(unittest.TestCase):
    """Run folder layout and fresh numbering (logs structure)."""
    def test_circuit_id(self):
        self.assertEqual(circuit_id({"library": "lib", "cell": "cell"}), "lib_cell")
        self.assertEqual(circuit_id({"library_cell": "lib_cell"}), "lib_cell")
        self.assertEqual(circuit_id({"cell": "onlycell"}), "onlycell")
        self.assertEqual(circuit_id({"name": "mytask"}), "mytask")
        self.assertEqual(circuit_id({}), "circuit")
        # Two circuits of one task must not share a logs folder.
        self.assertNotEqual(circuit_id({"name": "t", "library": "a", "cell": "b"}),
                            circuit_id({"name": "t", "library": "a", "cell": "c"}))
        # The id is always a single path element (no separators or wildcards).
        cid = circuit_id({"library": "a/b", "cell": 'c:d*e?f'})
        self.assertNotIn(os.sep, cid)
        for bad in (":", "*", "?", '"'):
            self.assertNotIn(bad, cid)
        self.assertEqual(cid, os.path.basename(cid))
        self.assertEqual(circuit_id({"name": "  .. "}), "circuit")
    def test_load_config_relative_state_dir(self):
        work = tempfile.mkdtemp()
        path = os.path.join(work, "task.json")
        def load(payload):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            return load_config(path)
        # "" / null / non-string mean "not set": they must not become the
        # config folder, and must not reach expanduser() as None either.
        self.assertEqual(load({"state_dir": ""})["state_dir"], "")
        self.assertEqual(load({"state_dir": None})["state_dir"], "")
        self.assertEqual(load({"state_dir": 123})["state_dir"], "")
        self.assertNotIn("state_dir", load({}))
        # Relative stays anchored to the config file; absolute is untouched.
        self.assertEqual(load({"state_dir": "ade"})["state_dir"],
                         os.path.join(work, "ade"))
        self.assertEqual(load({"state_dir": work})["state_dir"], work)
        # The consumers of a "not set" value must fail cleanly, not with
        # TypeError/KeyError deep inside expanduser().
        from optimizer import dump_state
        for cfg in (load({"state_dir": None}), load({})):
            with self.assertRaises(SystemExit) as ctx:
                dump_state(cfg)
            self.assertIn("state_dir", str(ctx.exception))
        runner = RealRunner({"state_dir": None}, ".", env={})
        self.assertFalse(runner._ensure_eval())
    def test_make_run_dir_layout_and_collision(self):
        root = tempfile.mkdtemp()
        path = make_run_dir(root, "cc")
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(os.path.dirname(path), os.path.join(os.path.abspath(root), "cc"))
        self.assertTrue(os.path.basename(path).startswith("run_"))
        path2 = make_run_dir(root, "cc")
        self.assertNotEqual(path, path2)
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.path.isdir(path2))
    def test_resolve_run_dir_rules(self):
        # 1) Default: logs/<circuit>/run_*. The typed field keeps our run
        # folder, so the next start builds a sibling, never a child.
        work = tempfile.mkdtemp()
        old = os.getcwd()
        os.chdir(work)
        try:
            cfg = {"output_dir": "logs/run", "name": "滤波器"}
            path, state = resolve_run_dir(cfg)
            self.assertEqual(os.path.dirname(path), os.path.join(work, "logs", circuit_id(cfg)))
            self.assertTrue(os.path.isdir(path))
            path2, _state2 = resolve_run_dir(dict(cfg, output_dir=path), prev=state)
            self.assertNotEqual(path2, path)
            self.assertEqual(os.path.dirname(path2), os.path.dirname(path))
        finally:
            os.chdir(old)
        # 2) A typed empty folder is used as-is; once it holds data the next
        # start gets a circuit folder with a run folder inside it.
        base = os.path.join(tempfile.mkdtemp(), "myout")
        cfg = {"output_dir": base, "library": "L", "cell": "C"}
        path, state = resolve_run_dir(cfg)
        self.assertEqual(path, os.path.abspath(base))
        self.assertTrue(os.path.isdir(path))
        with open(os.path.join(path, "history.csv"), "w", encoding="utf-8") as f:
            f.write("index\n")
        path2, _state2 = resolve_run_dir(dict(cfg, output_dir=path), prev=state)
        self.assertNotEqual(path2, path)
        self.assertEqual(os.path.dirname(path2), os.path.join(path, "L_C"))
        self.assertTrue(os.path.basename(path2).startswith("run_"))
        # 3) Resume reuses the folder; a later fresh start is a sibling.
        work = tempfile.mkdtemp()
        cfg = {"output_dir": os.path.join(work, "task"), "name": "n"}
        path, state = resolve_run_dir(cfg)
        with open(os.path.join(path, "history.csv"), "w", encoding="utf-8") as f:
            f.write("index\n")
        again, _state2 = resolve_run_dir(dict(cfg, output_dir=path), resume=True)
        self.assertEqual(again, path)
        fresh, _state3 = resolve_run_dir(dict(cfg, output_dir=again), prev=state)
        self.assertNotEqual(fresh, path)
        self.assertTrue(fresh.startswith(path + os.sep))
        # 4) A fresh start gets a sibling run folder, never a deeper nest.
        work = tempfile.mkdtemp()
        cfg = {"output_dir": os.path.join(work, "logs"), "name": "a"}
        d1, s1 = resolve_run_dir(cfg)
        with open(os.path.join(d1, "history.csv"), "w", encoding="utf-8") as f:
            f.write("index\n")
        d2, s2 = resolve_run_dir(dict(cfg, output_dir=d1), prev=s1)
        self.assertNotEqual(d1, d2)
        self.assertTrue(d2.startswith(d1 + os.sep))
        self.assertTrue(os.path.basename(d2).startswith("run_"))
        with open(os.path.join(d2, "history.csv"), "w", encoding="utf-8") as f:
            f.write("index\n")
        d3, _s3 = resolve_run_dir(dict(cfg, output_dir=d2), prev=s2)
        self.assertNotEqual(d2, d3)
        self.assertEqual(os.path.dirname(d3), os.path.dirname(d2))
    def test_run_index_lifecycle(self):
        work = tempfile.mkdtemp()
        out = os.path.join(work, "out")
        report = run_optimization(mock_config(out, 4), mock=True, resume=False)
        self.assertEqual(report["iterations"], 4)
        # A fresh start labels its folder (circuit, start time) and starts at 0.
        with open(os.path.join(out, "run_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["circuit"], circuit_id(mock_config(out, 1)))
        self.assertFalse(meta["resumed"])
        with open(os.path.join(out, "iter_0000", "record.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["index"], 0)
        with open(os.path.join(out, "history.csv"), encoding="utf-8") as f:
            rows = [ln for ln in f.read().splitlines() if ln.strip()]
        self.assertEqual(len(rows), 5)  # header + 4 iterations
        self.assertTrue(rows[1].startswith("0,"))
        # Resume continues the index.
        report = run_optimization(mock_config(out, 7), mock=True, resume=True)
        self.assertEqual(report["iterations"], 3)
        # A new fresh start never continues an older run's numbering.
        out2 = os.path.join(work, "out2")
        run_optimization(mock_config(out2, 5), mock=True, resume=False)
        report = run_optimization(mock_config(out2, 2), mock=True, resume=False)
        self.assertEqual(report["iterations"], 2)
        with open(os.path.join(out2, "history.csv"), encoding="utf-8") as f:
            rows = [ln for ln in f.read().splitlines() if ln.strip()]
        self.assertEqual(len(rows), 3)  # header + 2, old rows dropped


class TestHistorySeedV05(unittest.TestCase):
    """History warm-start feeds algorithm internals."""
    def test_seed_history_fills_models(self):
        import algorithm as A
        algo = A.create("tpe", ["a", "b"], [(0.0, 10.0)] * 2, seed=1)
        algo.seed_history([([1.0, 2.0], 5.0), ([3.0, 4.0], 1.0)])
        self.assertEqual(len(algo._X), 2)
        self.assertEqual(algo.eval_count, 2)
        self.assertAlmostEqual(algo.best_y, 1.0)
        algo = A.create("de", ["a", "b"], [(0.0, 10.0)] * 2, seed=1)
        algo.seed_history([([float(i), float(i) + 1], float(i)) for i in range(4)])
        filled = sum(1 for v in algo.pop_values if v is not None)
        self.assertGreaterEqual(filled, 4)
    def test_snapshot_restore_beats_seeding(self):
        # 12 mock iterations write a snapshot at iteration 9 (seed 7 never
        # hits the total=0 early stop).
        work = tempfile.mkdtemp()
        out = os.path.join(work, "run")
        cfg = example_config()
        cfg["output_dir"] = out
        cfg["settings"]["strategy"] = "cmaes_ipop"
        cfg["settings"]["seed"] = 7
        run_optimization(copy.deepcopy(cfg), mock=True, max_iter=12)
        self.assertTrue([f for f in os.listdir(out) if f.startswith("snapshot")])
        cfg2 = copy.deepcopy(cfg)
        cfg2["settings"]["max_iter"] = 14
        opt = Optimization(cfg2, mock=True, resume=True)
        self.assertTrue(opt._try_restore_snapshot())
    def test_no_snapshot_seeds_history(self):
        work = tempfile.mkdtemp()
        out = os.path.join(work, "run")
        cfg = example_config()
        cfg["output_dir"] = out
        cfg["settings"]["strategy"] = "tpe"
        cfg["settings"]["seed"] = 5
        run_optimization(copy.deepcopy(cfg), mock=True, max_iter=3)
        cfg2 = copy.deepcopy(cfg)
        cfg2["settings"]["max_iter"] = 5
        opt = Optimization(cfg2, mock=True, resume=True)
        self.assertTrue(opt._history_seeds)
        self.assertFalse(opt._try_restore_snapshot())
        opt._seed_from_history()
        self.assertGreaterEqual(opt.algo.inner.eval_count, len(opt._history_seeds))


class TestResumeEquivalenceV05(unittest.TestCase):
    """Interrupted + resumed run equals the uninterrupted run."""
    def test_resume_matches_uninterrupted(self):
        work = tempfile.mkdtemp()
        cfg = example_config()
        cfg["settings"]["strategy"] = "cmaes_ipop"
        cfg["settings"]["seed"] = 7
        cfg_a = copy.deepcopy(cfg)
        cfg_a["output_dir"] = os.path.join(work, "a")
        rep_a = run_optimization(cfg_a, mock=True, max_iter=24)
        cfg_b = copy.deepcopy(cfg)
        cfg_b["output_dir"] = os.path.join(work, "b")
        run_optimization(copy.deepcopy(cfg_b), mock=True, max_iter=12)
        rep_b = run_optimization(cfg_b, mock=True, max_iter=24)
        self.assertAlmostEqual(rep_a["best_total_error"], rep_b["best_total_error"], places=9)


class TestV06Hooks(unittest.TestCase):
    """librf_opt hook present/absent: behaviour and runner selection."""
    def test_runners_without_layout_em(self):
        stop_all_runners()  # must not raise with or without librf_opt
        cfg = example_config()
        cfg["settings"]["seed"] = 1
        opt = Optimization(cfg, mock=True, max_iter=1)
        self.assertTrue(hasattr(opt.runner, "run"))
        self.assertEqual(type(opt.runner).__name__, "MockRunner")
    def test_runner_with_layout_em_mock(self):
        import librf_opt  # noqa: F401 - must be importable in this deployment
        work = tempfile.mkdtemp()
        cfg = example_config()
        cfg["settings"]["seed"] = 1
        cfg["output_dir"] = os.path.join(work, "out")
        cfg["variables"] = [
            {"name": "dout", "init": "150", "min": "80", "max": "250"},
            {"name": "L1", "init": "100p", "min": "50p", "max": "200p"},
        ]
        cfg["targets"] = [{"output": "L0", "metric": "scalar", "goal": 2e-9,
                           "op": ">=", "tol": 0.5, "weight": 1.0}]
        cfg["layout_em"] = {
            "enabled": True, "type": "ind", "cell": "t", "work": work,
            "geom_vars": ["dout"], "fixed_geom": {"n": 2.5},
            "snp": os.path.join(work, "snps", "t.s2p"),
            "emx": {"freq": "1e9:2e9:2"}, "geom_outputs": True, "mock": True}
        opt = Optimization(cfg, mock=True, max_iter=1)
        self.assertEqual(type(opt.runner).__name__, "GeomRunner")
        rep = run_optimization(cfg, mock=True, max_iter=2)
        self.assertIn(rep.get("stop_reason") or rep.get("reason"),
                      ("max_iterations", "targets_met"))
        recs = glob.glob(os.path.join(cfg["output_dir"], "iter_*", "record.json"))
        self.assertTrue(recs)
        with open(recs[0]) as fh:
            rec = json.load(fh)
        self.assertEqual(rec.get("status"), "ok")
        self.assertIn("L0", json.dumps(rec))


class TestToolDiscovery(unittest.TestCase):
    """Tool paths probed at run time; no absolute paths in the config."""
    def test_find_prog_env_override(self):
        # Cross-platform: /bin/sh does not exist on Windows. The env
        # override contract is what is under test, not that shell path.
        exe = sys.executable
        name = os.path.basename(exe)
        os.environ["OPT_TEST_PROG"] = exe
        try:
            path, src = find_prog(name, ("OPT_TEST_PROG",))
            self.assertEqual(src, "env")
            self.assertEqual(os.path.normcase(os.path.abspath(path)),
                             os.path.normcase(os.path.abspath(exe)))
        finally:
            del os.environ["OPT_TEST_PROG"]
    def test_find_prog_fallbacks(self):
        work = tempfile.mkdtemp(prefix="vo_probe_")
        name = "vo_probe_tool.exe" if os.name == "nt" else "vo_probe_tool"
        exe = os.path.join(work, name)
        with open(exe, "w") as fh:
            fh.write("")
        if os.name != "nt":
            os.chmod(exe, 0o755)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = work + os.pathsep + old_path
        try:
            path, src = find_prog(name, ())
            self.assertEqual(src, "which")
            self.assertEqual(os.path.normcase(os.path.abspath(path)),
                             os.path.normcase(os.path.abspath(exe)))
        finally:
            os.environ["PATH"] = old_path
            os.remove(exe)
            os.rmdir(work)
        path, src = find_prog("no_such_tool_xyz", ("OPT_NOPE",))
        self.assertIsNone(path)
        self.assertEqual(src, "none")
    def test_resolve_prog_rules(self):
        self.assertEqual(resolve_prog("spectre", "/bin/sh"), "/bin/sh")
        # Explicit junk but a probe hit -> probe wins (cache injected so this
        # runs without Cadence).
        saved = dict(_TOOL_CACHE)
        _TOOL_CACHE.clear()
        try:
            _TOOL_CACHE["spectre"] = {"path": "/bin/sh", "src": "which"}
            self.assertEqual(resolve_prog("spectre", "/no/such/spectre-xyz"), "/bin/sh")
            # Nothing found -> explicit value or the command name.
            _TOOL_CACHE.clear()
            self.assertEqual(resolve_prog("xyz_tool", "/no/such/xyz"), "/no/such/xyz")
            self.assertEqual(resolve_prog("xyz_tool", None), "xyz_tool")
        finally:
            _TOOL_CACHE.clear()
            _TOOL_CACHE.update(saved)
        # Restored cache: a name with no probe entry still returns the
        # explicit value instead of the discovered path of another tool.
        saved = dict(_TOOL_CACHE)
        try:
            self.assertEqual(
                resolve_prog("no_such_tool_xyz", "/explicit/tool"),
                "/explicit/tool")
        finally:
            _TOOL_CACHE.clear()
            _TOOL_CACHE.update(saved)
    def test_discover_tools_shape(self):
        tools = discover_tools()
        for key in ("spectre", "virtuoso", "emx", "dbaccess", "strmout", "ocean"):
            self.assertIn(key, tools)
            self.assertIn("path", tools[key])
            self.assertIn("src", tools[key])
            self.assertIn(tools[key]["src"], ("env", "hint", "which", "root", "none"))
        rep = tool_report()
        self.assertIn("spectre", rep)
        self.assertIn("virtuoso", rep)


class TestTextEncoding(unittest.TestCase):
    """Chinese text I/O must never raise UnicodeDecodeError in any locale.

    Target machine: Python 3.6 + C locale (ANSI_X3.4-1968); a bare open()
    reading Chinese crashes (the layout-log failure root cause).
    """
    def test_utf8_text_roundtrip(self):
        work = tempfile.mkdtemp()
        p = os.path.join(work, "cjk.txt")
        with _native_open(p, "wb") as f:
            f.write("画矩形失败 layer=M4\n".encode("utf-8"))
        with open(p) as f:
            self.assertIn("画矩形失败", f.read())
        p = os.path.join(work, "cjk2.json")
        with open(p, "w") as f:
            json.dump({"name": "测试电感"}, f, ensure_ascii=False)
        with open(p) as f:
            self.assertEqual(json.load(f)["name"], "测试电感")
        p = os.path.join(work, "cjk3.txt")
        with _native_open(p, "wb") as f:
            f.write("北京".encode("utf-8"))
        self.assertIn("北京", _read_text(p))
        # run folders keep Chinese names in record.json
        p = os.path.join(work, "record.json")
        with open(p, "w") as f:
            json.dump({"index": 0, "name": "电感优化"}, f, ensure_ascii=False)
        with open(p) as f:
            self.assertEqual(json.load(f)["name"], "电感优化")
    def test_binary_mode_untouched(self):
        p = os.path.join(tempfile.mkdtemp(), "bin.dat")
        data = bytes(range(256))
        with open(p, "wb") as f:
            f.write(data)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), data)


class TestAuditTrail(unittest.TestCase):
    """Operation audit: file I/O and external calls carry time and abs path."""

    def test_audit_records_absolute_path(self):
        rec = audit("write", os.path.join("relative", "f.txt"), "test")
        self.assertTrue(os.path.isabs(rec["path"]),
                        "audit path must be absolute: %s" % rec["path"])
        self.assertTrue(rec["ts"])

    def test_audit_kinds_classified(self):
        p = os.path.join(tempfile.mkdtemp(), "k.txt")
        with open(p, "w") as f:
            f.write("a")
        with open(p, "a") as f:
            f.write("b")
        kinds = [r["kind"] for r in audit_dump() if r["path"] == p]
        self.assertIn("write", kinds)
        self.assertIn("append", kinds, "mode=a must be append, not write")

    def test_audit_sink_callback(self):
        got = []
        audit_sink(got.append)
        try:
            audit("exec", "/bin/echo", "hello")
            self.assertTrue(got, "sink callback must fire")
            self.assertEqual(got[-1]["kind"], "exec")
        finally:
            audit_sink(None)

    def test_audit_file_sink(self):
        p = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
        audit_file(p)
        try:
            rec = audit("refuse", p, "test")
        finally:
            audit_file(None)
        with open(p) as f:
            lines = [json.loads(x) for x in f if x.strip()]
        self.assertEqual(lines[-1], rec)


class _CompactBase(unittest.TestCase):
    DECK = (
        "// sim deck header\n"
        "simulator lang=spectre\n"
        "global 0\n"
        "parameters L1=250p L2=280p k0=0.75 cin=100f\n"
        "subckt pcell(a)\n"
        "parameters _par0=k\n"
        "R1 (a 0) resistor r=50\n"
        "ends pcell\n"
        "V1 (in 0) vdc dc=1\n"
        "X1 (in 0) pcell\n"
        "sp sp1 ports=[in 0] start=1 stop=10\n"
    )
    def _run_dir(self):
        run_dir = os.path.join(tempfile.mkdtemp(), "run")
        os.makedirs(run_dir)
        return run_dir
    def _write_iter(self, run_dir, index, params_line, full_text=None):
        """Write one iteration folder: netlist, big spectre log, psf, result."""
        iter_dir = os.path.join(run_dir, "iter_%04d" % index)
        os.makedirs(iter_dir, exist_ok=True)
        text = (full_text if full_text is not None
                else rewrite_netlist_parameters(self.DECK, params_line)[0])
        with open(os.path.join(iter_dir, "input.scs"), "w", encoding="utf-8") as f:
            f.write(text)
        # A spectre log with a big repeated header (typical).
        header = "Sun Spectre 23.1\nLicensed by cadence\n" + ("x" * 200)
        with open(os.path.join(iter_dir, "spectre.log"), "w", encoding="utf-8") as f:
            f.write(header * 80 + "\nError: none really\nTAIL-%d\n" % index)
        return iter_dir, text


class TestCompactLog(_CompactBase):
    """Q4: compact iteration storage + lossless reconstruct."""
    def test_netlist_dedup_and_reconstruct(self):
        run_dir = self._run_dir()
        texts = []
        for i in range(5):
            params_line = "parameters L1=%dp L2=280p k0=0.7 cin=100f" % (100 + i)
            iter_dir, text = self._write_iter(run_dir, i, params_line)
            texts.append(text)
            meta = compact_netlist_file(iter_dir, text, params_line)
            self.assertEqual(meta["base"], NETLIST_BASE_NAME)
            self.assertFalse(os.path.isfile(os.path.join(iter_dir, "input.scs")))
            # Iron rule: the full copy is archived under a new name, never deleted.
            self.assertTrue(any(n.startswith("input.scs.old-") for n in os.listdir(iter_dir)))
            self.assertTrue(os.path.isfile(os.path.join(iter_dir, NETLIST_META_NAME)))
        self.assertTrue(os.path.isfile(os.path.join(run_dir, NETLIST_BASE_NAME)))
        for i, want in enumerate(texts):
            got, _path = reconstruct_input_scs(run_dir, i)
            self.assertEqual(got, want)
            self.assertEqual(_sha1_text(got), _sha1_text(want))
        # Corrupt the recorded hash: reconstruct must refuse.
        meta_path = os.path.join(iter_dir, NETLIST_META_NAME)
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["sha1"] = "0" * 40
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        self.assertRaises(ValueError, reconstruct_input_scs, run_dir, len(texts) - 1)
    def test_spectre_log_gzip_keeps_tail_and_errors(self):
        iter_dir = os.path.join(tempfile.mkdtemp(), "iter_0001")
        os.makedirs(iter_dir)
        log = os.path.join(iter_dir, "spectre.log")
        body = ("header line\n" * 500 + "Error: fatal thing\n" + "end\n").encode("utf-8")
        with open(log, "wb") as f:
            f.write(body)
        self.assertIsNotNone(compact_spectre_log(iter_dir))
        self.assertTrue(os.path.isfile(log + ".gz"))
        with gzip.open(log + ".gz", "rb") as gz:
            raw = gz.read()
        self.assertEqual(hashlib.sha1(raw).hexdigest(), hashlib.sha1(body).hexdigest())
        kept = open(log, encoding="utf-8").read()
        self.assertIn("spectre.log.gz", kept)
        self.assertIn("Error: fatal thing", kept)
        self.assertIn("end", kept)
        # The kept log must shrink: tail is capped below the original.
        self.assertLess(len(kept), len(body))
        total = os.path.getsize(log) + os.path.getsize(log + ".gz")
        self.assertLess(total, len(body) + 512)
    def test_save_raw_0_and_1_unchanged(self):
        work = tempfile.mkdtemp()
        lg0 = RunLogger(os.path.join(work, "a"), save_raw=0, log_mode=LOG_MODE_FULL)
        p0 = lg0.iter_dir(0)
        os.makedirs(os.path.join(p0, "psf"), exist_ok=True)
        lg0.log_iteration(0, {"x": "1"}, {"total": 1.0, "errors": {}}, "ok")
        # save_raw=0 is applied by the runner, not the logger.
        self.assertTrue(os.path.isdir(os.path.join(p0, "psf")))
        lg1 = RunLogger(os.path.join(work, "b"), save_raw=1, log_mode=LOG_MODE_FULL)
        p1 = lg1.iter_dir(0)
        os.makedirs(os.path.join(p1, "psf"), exist_ok=True)
        lg1.log_iteration(0, {"x": "1"}, {"total": 1.0, "errors": {}}, "ok")
        lg1.finalize()
        self.assertTrue(os.path.isdir(os.path.join(p1, "psf")))
    def test_manifest_lists_files_and_hashes(self):
        work = tempfile.mkdtemp()
        logger = RunLogger(work, save_raw=1, log_mode=LOG_MODE_COMPACT)
        path = logger.iter_dir(0)
        with open(os.path.join(path, "result.txt"), "w", encoding="utf-8") as f:
            f.write("STATUS ok\n")
        logger.log_iteration(0, {"L1": "250p"}, {"total": 1.0, "errors": {}}, "ok")
        self.assertTrue(os.path.isfile(os.path.join(work, MANIFEST_NAME)))
        with open(os.path.join(work, MANIFEST_NAME), encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["index"], 0)
        self.assertEqual(entry["log_mode"], LOG_MODE_COMPACT)
        self.assertIn("result.txt", entry["files"])
        self.assertEqual(len(entry["files"]["result.txt"]["sha1"]), 40)
    def test_full_mode_leaves_netlist_and_log_alone(self):
        run_dir = self._run_dir()
        params_line = "parameters L1=1 L2=2 k0=3 cin=4"
        _iter_dir, text = self._write_iter(run_dir, 0, params_line)
        cfg = {"log_mode": "full"}
        self.assertEqual(_log_mode_of(cfg), LOG_MODE_FULL)
        # full mode: the compaction policy is a no-op, so the per-iteration
        # netlist stays in place and no shared base netlist is written.
        self.assertTrue(os.path.isfile(os.path.join(_iter_dir, "input.scs")))
        self.assertFalse(os.path.isfile(os.path.join(run_dir, NETLIST_BASE_NAME)))
        # A full-mode iteration reconstructs straight from the on-disk netlist.
        self.assertEqual(reconstruct_input_scs(run_dir, 0)[0], text)
    def test_compact_mock_run_writes_manifest_and_zips(self):
        out_dir = os.path.join(tempfile.mkdtemp(), "out")
        cfg = mock_config(out_dir, 6)
        cfg["log_mode"] = "compact"
        report = run_optimization(cfg, mock=True)
        self.assertEqual(report["iterations"], 6)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, MANIFEST_NAME)))
        # mock_s21.txt.gz exists; result.txt stays for the GUI.
        self.assertTrue(glob.glob(os.path.join(out_dir, "iter_*", "mock_s21.txt.gz")))
        self.assertTrue(glob.glob(os.path.join(out_dir, "iter_*", "result.txt")))
        # history + resume untouched by compact mode.
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "history.csv")))
        report2 = run_optimization(mock_config(out_dir, 8), mock=True)
        self.assertEqual(report2["iterations"], 2)
    def test_e2e_size_compact_beats_full(self):
        """Synthetic RealRunner-sized artifacts: compact saves >=50%.

        Sizes follow the user report (tens to 100KB of repeated netlist+log
        per iter). live = working set (excludes .old-* archives, which the
        iron rule never deletes).
        """
        def walk_size(root, live_only=True):
            total = 0
            for dirpath, dirnames, files in os.walk(root):
                if live_only:
                    dirnames[:] = [d for d in dirnames if ".old-" not in d]
                for name in files:
                    if ".old-" not in name:
                        total += os.path.getsize(os.path.join(dirpath, name))
            return total
        def build(mode, save_raw):
            run_dir = self._run_dir()
            logger = RunLogger(run_dir, save_raw=save_raw, log_mode=mode)
            # ~80KB deck and ~80KB spectre.log: the repetitive per-iter bulk.
            deck = self.DECK + ("// pad line for realistic size\n" * 2600)
            for i in range(20):
                params_line = ("parameters L1=%dp L2=%dp k0=0.%d cin=100f"
                               % (150 + i, 200 + i, 40 + (i % 20)))
                iter_dir, text = self._write_iter(
                    run_dir, i, params_line,
                    full_text=rewrite_netlist_parameters(deck, params_line)[0])
                os.makedirs(os.path.join(iter_dir, "psf"), exist_ok=True)
                with open(os.path.join(iter_dir, "psf", "data.psf"), "wb") as f:
                    f.write(b"\x00" * 100000)
                with open(os.path.join(iter_dir, "result.txt"), "w", encoding="utf-8") as f:
                    f.write("STATUS ok\nCURVE_BEGIN S21\n")
                    for k in range(400):
                        f.write("%d %d\n" % (k, k * 2))
                    f.write("CURVE_END\n")
                if mode == LOG_MODE_COMPACT:
                    compact_netlist_file(iter_dir, text, params_line)
                    compact_spectre_log(iter_dir)
                if save_raw == 0:
                    _archive_aside(os.path.join(iter_dir, "psf"))
                if i == 5:
                    total, status = 1e6, "fail: x"
                elif i < 4:
                    total, status = float(5 - i), "ok"
                else:
                    total, status = 2.5 + 0.01 * i, "ok"
                logger.log_iteration(i, {"L1": "1p"}, {"total": total, "errors": {}}, status)
            logger.finalize()
            return run_dir, walk_size(run_dir, True), walk_size(run_dir, False)
        _run_full, full_live, full_all = build(LOG_MODE_FULL, 1)
        run_cmp, cmp_live, cmp_all = build(LOG_MODE_COMPACT, 2)
        self.assertLess(cmp_live, full_live)
        self.assertLessEqual(cmp_live, full_live * 0.5)
        # compact+save_raw=2 live size is the working set; .old-* archives
        # stay on disk (iron rule) and are reported.
        self.assertLess(cmp_all, full_all)
        # Reconstruct every iter of the compact dir.
        for i in range(20):
            text, _p = reconstruct_input_scs(run_cmp, i)
            self.assertIn("parameters", text)
            self.assertGreater(len(text), 1000)


class TestCompactRobust(_CompactBase):
    """Q4 crash injection + state-machine hardening (P0/P1 regressions)."""
    def _psf_ok(self, run_dir, index):
        return os.path.isdir(os.path.join(run_dir, "iter_%04d" % index, "psf"))
    def _archived(self, run_dir, index, prefix):
        names = os.listdir(os.path.join(run_dir, "iter_%04d" % index))
        return any(n.startswith(prefix) for n in names)
    def _logger(self, save_raw=2):
        work = tempfile.mkdtemp()
        return work, RunLogger(work, save_raw=save_raw, log_mode=LOG_MODE_COMPACT)
    def _log_one(self, logger, index, total, status="ok"):
        path = logger.iter_dir(index)
        os.makedirs(os.path.join(path, "psf"), exist_ok=True)
        with open(os.path.join(path, "psf", "data.psf"), "wb") as f:
            f.write(b"\x01" * 64)
        with open(os.path.join(path, "result.txt"), "w", encoding="utf-8") as f:
            f.write("STATUS ok\nCURVE_BEGIN S21\n0 %d\nCURVE_END\n" % index)
        return logger.log_iteration(index, {"x": "1"}, {"total": total, "errors": {}}, status)
    def test_p0_gzip_does_not_lose_new_content(self):
        work = tempfile.mkdtemp()
        path = os.path.join(work, "dump.txt")
        with open(path, "wb") as f:
            f.write(b"A" * 20000)
        gz1 = gzip_file_in_place(path, keep_stub=False)
        self.assertEqual(gz1, path + ".gz")  # stable name
        # A same-name smaller file replaces the old gzip content (the old gz
        # is bigger than the new raw: the P0 bug skipped the rewrite).
        small = b"B" * 40
        with open(path, "wb") as f:
            f.write(small)
        self.assertEqual(gzip_file_in_place(path, keep_stub=False), gz1)
        with gzip.open(gz1, "rb") as gz:
            self.assertEqual(gz.read(), small)
        # Iron rule: raw archived under .old-, never deleted.
        self.assertFalse(os.path.isfile(path))
        self.assertTrue(any(n.startswith("dump.txt.old-") for n in os.listdir(work)))
        # A corrupt .gz is rewritten from the raw bytes.
        with open(path, "wb") as f:
            f.write(b"C" * 100)
        with open(gz1, "wb") as f:
            f.write(b"not gzip at all")
        self.assertEqual(gzip_file_in_place(path, keep_stub=False), gz1)
        with gzip.open(gz1, "rb") as gz:
            self.assertEqual(gz.read(), b"C" * 100)
    def test_p1_resume_seeds_best(self):
        work, logger = self._logger()
        logger.seed_best(1.0)  # historical best 1.0
        self._log_one(logger, 5, 5.0)  # worse -> ordinary, not best
        entries = read_manifest(os.path.join(work, MANIFEST_NAME))
        self.assertEqual(entries[5]["psf_kept"], "ordinary",
                         "the first ok after resume must not be mistaken for best")
        self._log_one(logger, 6, 0.5)  # a real new best
        self.assertTrue(self._psf_ok(work, 6))
        self._log_one(logger, 7, 0.7)  # ordinary -> expire 5
        self.assertFalse(self._psf_ok(work, 5), "ordinary 5 is archived when replaced")
        self.assertTrue(self._archived(work, 5, "psf.old-"))
        logger.finalize()
        self.assertTrue(self._psf_ok(work, 7), "last ordinary kept")
        self.assertTrue(self._psf_ok(work, 6), "best kept")
        # Without a seed the first ok is treated as best (keep more, never lose).
        work, logger = self._logger()
        self._log_one(logger, 3, 9.0)
        self.assertTrue(self._psf_ok(work, 3))
    def test_p1_manifest_sync_after_expire(self):
        work, logger = self._logger()
        self._log_one(logger, 0, 1.0)  # first
        self._log_one(logger, 1, 2.0)  # ordinary pending
        self._log_one(logger, 2, 3.0)  # ordinary -> expire 1
        logger.finalize()              # keep 2 as last
        entries = read_manifest(os.path.join(work, MANIFEST_NAME))
        self.assertIn(1, entries)
        e1 = entries[1]
        self.assertFalse(e1.get("provisional", False))
        self.assertNotIn("result.txt", e1["files"],
                         "after expire the manifest must not list the gone plain result.txt")
        self.assertIn("result.txt.gz", e1["files"])
        for name, info in e1["files"].items():
            fp = os.path.join(work, "iter_0001", name)
            self.assertTrue(os.path.isfile(fp), "manifest files must exist on disk")
            self.assertEqual(info["sha1"], _sha1_file(fp))
        # At least one retention refresh; read_manifest keeps the newest per index.
        raw_lines = open(os.path.join(work, MANIFEST_NAME), encoding="utf-8").read()
        self.assertIn('"type": "retention"', raw_lines)
        self.assertEqual(entries[1].get("type"), "retention")
    def test_manifest_sha1_matches_disk_after_finalize(self):
        work, logger = self._logger()
        for i, total in enumerate([1.0, 5.0, 4.0, 3.0, 2.0, 6.0]):
            self._log_one(logger, i, total, "fail: x" if i == 2 else "ok")
        logger.finalize()
        entries = read_manifest(os.path.join(work, MANIFEST_NAME))
        self.assertTrue(entries)
        for index, entry in entries.items():
            path = os.path.join(work, "iter_%04d" % index)
            for name, info in (entry.get("files") or {}).items():
                fp = os.path.join(path, name)
                self.assertTrue(os.path.isfile(fp), "iter %d missing %s" % (index, name))
                self.assertEqual(info["sha1"], _sha1_file(fp),
                                 "iter %d %s sha1 mismatch" % (index, name))
                self.assertEqual(info["bytes"], os.path.getsize(fp))
    def _run_order(self, plan):
        """plan = [(index, total, status), ...]; return the kept set."""
        work, logger = self._logger()
        for index, total, status in plan:
            self._log_one(logger, index, total, status)
        logger.finalize()
        kept = set()
        for index, _t, _s in plan:
            if self._psf_ok(work, index):
                kept.add(index)
            else:
                # A replaced psf must be archived, not deleted.
                self.assertTrue(self._archived(work, index, "psf.old-"),
                                "iter %d psf must be archived, not deleted" % index)
        return kept
    def test_save_raw2_retention_matrix(self):
        cases = [
            ([(0, 5.0, "ok"), (1, 9.0, "fail: e"), (2, 3.0, "ok"), (3, 4.0, "ok")],
             {0, 1, 2, 3}),  # 3 = last ordinary
            ([(0, 5.0, "ok"), (1, 4.0, "ok"), (2, 1.0, "ok")], {0, 1, 2}),  # best is last
            ([(0, 9.0, "ok"), (1, 8.0, "ok"), (2, 7.0, "ok"), (3, 6.0, "ok"),
              (4, 5.0, "ok")], {0, 1, 2, 3, 4}),  # every iter improves
            ([(0, 1.0, "ok"), (1, 2.0, "ok"), (2, 2.1, "ok"), (3, 2.2, "ok"),
              (4, 2.3, "ok")], {0, 4}),  # plateau keeps first and last
            ([(0, 1.0, "ok"), (1, 2.0, "ok"), (2, 3.0, "fail: z")], {0, 2}),
            ([(0, 1.0, "ok"), (1, 1e6, "fail: a"), (2, 1e6, "fail: b"),
              (3, 2.0, "ok")], {0, 1, 2, 3}),  # consecutive fails all kept
        ]
        for plan, want in cases:
            self.assertEqual(self._run_order(plan), want, "plan=%s" % (plan,))
    def test_crash_before_finalize_keeps_bytes(self):
        work, logger = self._logger()
        for i, total in enumerate([1.0, 2.0, 3.0, 4.0]):
            self._log_one(logger, i, total)
        # Crash: no finalize. pending=3 stays; it must not be archived.
        self.assertTrue(self._psf_ok(work, 0))
        self.assertFalse(self._psf_ok(work, 1))
        self.assertTrue(self._psf_ok(work, 3))
        # Every iteration is recoverable from its own record.
        for i in range(4):
            rec = json.load(open(os.path.join(work, "iter_%04d" % i, "record.json"),
                                 encoding="utf-8"))
            self.assertEqual(rec["index"], i)
        # Resume: a new logger continues; the old pending 3 stays (keep more).
        logger2 = RunLogger(work, save_raw=2, log_mode=LOG_MODE_COMPACT)
        logger2.seed_best(1.0)
        self._log_one(logger2, 4, 5.0)
        logger2.finalize()
        self.assertTrue(self._psf_ok(work, 3),
                        "a crash-left pending psf must not be archived on resume")
        self.assertTrue(self._psf_ok(work, 4))
    def test_crash_mid_finalize_leaves_key_iters(self):
        work, logger = self._logger()
        self._log_one(logger, 0, 1.0)
        self._log_one(logger, 1, 2.0)             # pending=1
        self._log_one(logger, 2, 3.0)             # expire 1, pending=2
        self._log_one(logger, 3, 9.0, "fail: x")  # last=3 != pending 2
        # Inject: finalize raises while expiring 2.
        boom = []
        def _bad_expire(path, index=None):
            boom.append(path)
            raise RuntimeError("injected")
        logger._expire_ordinary = _bad_expire
        logger.finalize()  # must not propagate
        self.assertEqual(len(boom), 1)
        self.assertTrue(self._psf_ok(work, 0), "first must not be lost")
        self.assertTrue(self._psf_ok(work, 3), "fail must not be lost")
        self.assertTrue(self._psf_ok(work, 2),
                        "pending must stay when expire fails (never lose)")
    def test_crash_manifest_then_resume_ids_stale(self):
        work, logger = self._logger()
        self._log_one(logger, 0, 1.0)
        self._log_one(logger, 1, 2.0)
        self._log_one(logger, 2, 3.0)  # expire 1
        # Kill-window half line, then another one on an existing index.
        with open(os.path.join(work, MANIFEST_NAME), "a", encoding="utf-8") as f:
            f.write('{"index": 99, "broken')
        with open(os.path.join(work, MANIFEST_NAME), "a", encoding="utf-8") as f:
            f.write('{"index": 0, "half')
        entries = read_manifest(os.path.join(work, MANIFEST_NAME))
        self.assertNotIn(99, entries, "half lines must be recognised as invalid/stale")
        self.assertEqual(entries[0]["index"], 0)  # intact line still readable
        # Every valid entry's files must exist on disk.
        for index, entry in entries.items():
            for name, info in (entry.get("files") or {}).items():
                fp = os.path.join(work, "iter_%04d" % index, name)
                self.assertTrue(os.path.isfile(fp))
                self.assertEqual(info["sha1"], _sha1_file(fp))
    def test_template_drift_fallback_and_reconstruct(self):
        run_dir = self._run_dir()
        deck_a = "// deck A\nparameters L1=1 L2=2\nR1 (a 0) r=1\n"
        deck_b = "// deck B CHANGED\nparameters L1=1 L2=2\nR1 (a 0) r=2\n"
        # iter0 uses deck A.
        p0 = "parameters L1=10 L2=2"
        t0 = rewrite_netlist_parameters(deck_a, p0)[0]
        it0 = os.path.join(run_dir, "iter_0000")
        os.makedirs(it0)
        open(os.path.join(it0, "input.scs"), "w", encoding="utf-8").write(t0)
        self.assertIsNone(compact_netlist_file(it0, t0, p0).get("fallback"))
        # iter1: the template drifts (the user re-netlists in ADE mid-run).
        p1 = "parameters L1=11 L2=2"
        t1 = rewrite_netlist_parameters(deck_b, p1)[0]
        it1 = os.path.join(run_dir, "iter_0001")
        os.makedirs(it1)
        open(os.path.join(it1, "input.scs"), "w", encoding="utf-8").write(t1)
        meta1 = compact_netlist_file(it1, t1, p1)
        self.assertEqual(meta1.get("fallback"), "full_input_kept")
        self.assertTrue(os.path.isfile(os.path.join(it1, "input.scs")),
                        "a drifted iter must keep the full text")
        # Reconstruct: iter0 from base; iter1 from the kept full text.
        self.assertEqual(reconstruct_input_scs(run_dir, 0)[0], t0)
        g1, _ = reconstruct_input_scs(run_dir, 1)
        self.assertEqual(g1, t1)
        self.assertEqual(_sha1_text(g1), meta1["sha1"])
        # Tampered params but the full text still exists -> still refuses
        # (never silently uses base).
        meta_path = os.path.join(it1, NETLIST_META_NAME)
        meta = json.load(open(meta_path, encoding="utf-8"))
        meta["params_line"] = "parameters L1=99 L2=99"
        meta["fallback"] = None
        meta["base"] = NETLIST_BASE_NAME
        meta["sha1"] = "0" * 40
        open(meta_path, "w", encoding="utf-8").write(json.dumps(meta))
        self.assertRaises(ValueError, reconstruct_input_scs, run_dir, 1)
    def test_reconstruct_accepts_full_mode_iter(self):
        run_dir = self._run_dir()
        it = os.path.join(run_dir, "iter_0000")
        os.makedirs(it)
        body = "parameters L1=1\nX\n"
        open(os.path.join(it, "input.scs"), "w", encoding="utf-8").write(body)
        self.assertEqual(reconstruct_input_scs(run_dir, 0)[0], body)


class TestTechProfile(unittest.TestCase):
    """tech_profile.json is explicit and functionally equal to the built-in default."""
    HERE = os.path.dirname(os.path.abspath(__file__))
    TP_PATH = os.path.join(HERE, "tech_profile.json")
    def _load(self, **kw):
        import librf_opt
        kw.setdefault("use_cache", False)
        return librf_opt.load_tech_profile(**kw)
    def test_profile_functional_equals_default(self):
        self.assertTrue(os.path.isfile(self.TP_PATH), "v08+ ships tech_profile.json")
        t = self._load(path=self.TP_PATH)
        import librf_opt
        d = librf_opt.DEFAULT_TECH
        for key in ("techlib", "techlib_dir", "layermap", "proc", "layers", "text", "drc"):
            self.assertEqual(t[key], d[key], "tech_profile.%s must equal DEFAULT_TECH" % key)
        self.assertEqual([k for k in t if str(k).startswith("_doc")], [],
                         "_doc* keys must be stripped by the loader")
        # The source file does carry _doc* -> stripping happens on load.
        with open(self.TP_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertTrue([k for k in raw if str(k).startswith("_doc")])
    def test_profile_values_are_applied(self):
        # The shipped template equals the built-in default, so comparing it
        # with DEFAULT_TECH alone would also pass if the loader ignored the
        # file. Load a deliberately different profile instead.
        import librf_opt
        work = tempfile.mkdtemp()
        path = os.path.join(work, "tech_profile.json")
        payload = {"techlib": "DEMO65",
                   "layers": {"spiral": "M7"},
                   "text": {"M7": "M7LABEL"},
                   "drc": {"min_w": 0.2, "min_area": {"M7": 0.5}}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        t = self._load(path=path)
        self.assertEqual(t["techlib"], "DEMO65")
        self.assertEqual(t["layers"]["spiral"], "M7")
        self.assertEqual(t["text"]["M7"], "M7LABEL")
        self.assertEqual(t["drc"]["min_w"], 0.2)
        self.assertEqual(t["drc"]["min_area"]["M7"], 0.5)
        # Missing keys fall back to the built-in default.
        self.assertEqual(t["layers"]["route"], librf_opt.DEFAULT_TECH["layers"]["route"])
        self.assertEqual(t["drc"]["max_w"], librf_opt.DEFAULT_TECH["drc"]["max_w"])
        self.assertEqual(t["drc"]["min_area"]["M3"],
                         librf_opt.DEFAULT_TECH["drc"]["min_area"]["M3"])
        self.assertTrue(t["_src"].endswith("tech_profile.json"))
    def test_env_profile_is_loaded(self):
        # tech_profile.json suggests LIBRF_TECH_PROFILE for a custom PDK;
        # that documented path must work and outrank the shipped template.
        import librf_opt
        work = tempfile.mkdtemp()
        path = os.path.join(work, "custom.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"techlib": "DEMO65", "layers": {"spiral": "MA"}}, f)
        old = os.environ.get("LIBRF_TECH_PROFILE")
        os.environ["LIBRF_TECH_PROFILE"] = path
        try:
            t = self._load()
        finally:
            if old is None:
                os.environ.pop("LIBRF_TECH_PROFILE", None)
            else:
                os.environ["LIBRF_TECH_PROFILE"] = old
        self.assertEqual(t["techlib"], "DEMO65")
        self.assertEqual(t["layers"]["spiral"], "MA")
        self.assertEqual(t["layers"]["route"], librf_opt.DEFAULT_TECH["layers"]["route"])
        self.assertTrue(t["_src"].endswith("custom.json"))
    def test_auto_load_no_self_disturb(self):
        """Auto-load order hits the local profile but still equals the default."""
        cwd = os.getcwd()
        # The documented env override would otherwise make this depend on
        # the machine running the suite (test_env_profile_is_loaded covers it).
        old = os.environ.pop("LIBRF_TECH_PROFILE", None)
        try:
            os.chdir(self.HERE)
            t = self._load(work=".")
        finally:
            os.chdir(cwd)
            if old is not None:
                os.environ["LIBRF_TECH_PROFILE"] = old
        import librf_opt
        d = librf_opt.DEFAULT_TECH
        self.assertEqual(t["techlib"], d["techlib"])
        self.assertEqual(librf_opt.tech_layer(t, "spiral"), librf_opt.tech_layer(d, "spiral"))
        self.assertEqual(librf_opt.tech_layer(t, "via_sp"), librf_opt.tech_layer(d, "via_sp"))
        self.assertEqual(librf_opt.tech_drc(t)["min_w"], librf_opt.tech_drc(d)["min_w"])
        self.assertEqual(t["layers"], d["layers"])
        self.assertEqual(t["drc"], d["drc"])


class TestS3EmBridge(unittest.TestCase):
    """S3 circuit integration: em_bridge parse + isolation rules.

    S3-ISO-01/02/03/05/06/07 + parse validation. S3-ISO-04 bit-exact
    comparison lives in s3_iso_baseline.json (maintained by mock e2e step 1.4).
    """
    def _base_cfg(self, **extra):
        cfg = {
            "name": "s3", "library": "MyLib", "cell": "c",
            "state_dir": "", "work_dir": ".", "output_dir": "work/_s3_iso",
            "save_raw": 0, "log_mode": "compact",
            "settings": {"max_iter": 3, "patience": 10, "seed": 3},
            "variables": [{"name": "L1", "init": "250p", "min": "150p", "max": "350p"}],
            "targets": [{"output": "S21 dB20", "metric": "band_min", "op": ">=",
                         "goal": 3, "tol": 0.5, "band": ["24G", "44G"]}],
        }
        cfg.update(extra)
        return cfg
    def _opt(self, cfg, parsed=True):
        opt = Optimization.__new__(Optimization)
        opt.cfg = cfg
        opt.em_bridge_cfg = parse_em_bridge_cfg(cfg) if parsed else None
        return opt
    def _em(self, **kw):
        em = {"enabled": True, "tech": "T",
              "devices": [{"device_type": "ind", "f_op": "28G",
                           "target_var": "L1", "tech": "T"}]}
        em.update(kw)
        return em
    def test_s3_iso_01_no_section_is_none(self):
        """S3-ISO-01/02: no em_bridge section, or enabled=false -> None."""
        self.assertIsNone(parse_em_bridge_cfg(self._base_cfg()))
        self.assertIsNone(parse_em_bridge_cfg(self._base_cfg(em_bridge={"enabled": False})))
    def test_s3_iso_03_mutual_exclusion(self):
        """S3-ISO-03 em_bridge + layout_em both on -> mutual-exclusion assert."""
        opt = self._opt(self._base_cfg(em_bridge=self._em(), layout_em={"enabled": True}))
        with self.assertRaises(ValueError):
            opt._build_runner(mock=True)
    def test_s3_iso_05_lazy_import_ok_when_off(self):
        """S3-ISO-05 lazy import: em_bridge.py missing + enabled=false still builds."""
        opt = self._opt(self._base_cfg(), parsed=False)  # no em_bridge section
        runner = opt._build_runner(mock=True)
        self.assertEqual(type(runner).__name__, "MockRunner")
    def test_s3_iso_06_enabled_without_module_raises(self):
        """S3-ISO-06 enabled=true but import fails -> Chinese ImportError, no silence."""
        import sys
        opt = self._opt(self._base_cfg(em_bridge=self._em()))
        saved = sys.modules.pop("em_bridge", None)
        # None makes import raise ImportError.
        sys.modules["em_bridge"] = None
        try:
            with self.assertRaises(ImportError) as cm:
                opt._build_runner(mock=True)
            self.assertIn("em_bridge", str(cm.exception))
        finally:
            if saved is not None:
                sys.modules["em_bridge"] = saved
            else:
                sys.modules.pop("em_bridge", None)
    def test_s3_iso_07_layout_em_still_works(self):
        """S3-ISO-07 em_bridge absent + layout_em on -> GeomRunner (v07 behaviour)."""
        try:
            import librf_opt  # noqa: F401 - layout_em needs the optional module
        except ImportError as exc:
            self.skipTest("librf_opt unavailable (%s)" % exc)
        cfg = self._base_cfg(layout_em={"enabled": True, "type": "ind", "mock": True,
                                        "geom_vars": [], "cell": "t", "work": "work"})
        opt = self._opt(cfg, parsed=False)
        runner = opt._build_runner(mock=True)
        self.assertEqual(type(runner).__name__, "GeomRunner")
    def test_parse_rejects_invalid(self):
        """Bad mode / missing f_op / sparse grid all raise ValueError."""
        cases = (
            self._em(mode="nope"),
            self._em(devices=[{"device_type": "ind", "target_var": "L1", "tech": "T"}]),
            self._em(devices=[{"device_type": "ind", "f_op": "28G", "target_var": "L1",
                               "tech": "T", "tolerances": {"L0": 0.01},
                               "grid_spec": {"step_rel": 0.05}}]),
        )
        for em in cases:
            with self.assertRaises(ValueError):
                parse_em_bridge_cfg(self._base_cfg(em_bridge=em))
    def test_parse_norm_ok(self):
        cfg = self._base_cfg(em_bridge=self._em(
            mode="track", fallback="actual", library="work/em_lib",
            devices=[{"device_type": "ind", "f_op": "28G", "target_var": "L1"}]))
        eb = parse_em_bridge_cfg(cfg)
        self.assertTrue(eb["enabled"])
        self.assertEqual(eb["mode"], "track")
        self.assertEqual(eb["devices"][0]["tech"], "T")
        self.assertAlmostEqual(eb["devices"][0]["tolerances"]["L0"], 0.02)
        self.assertAlmostEqual(eb["em_opt"]["budget"]["max_em"], 20)

    def test_solve_registers_snp_before_put(self):
        """em_opt 用 "snp" 带回路径：入库前先内容寻址登记，行里有 sha1。"""
        import shutil
        import em_bridge
        tmp = tempfile.mkdtemp(prefix="em_snp_")
        try:
            lib = em_bridge.EmLibrary(os.path.join(tmp, "lib.sqlite3"))
            src = os.path.join(tmp, "dev.s2p")
            with open(src, "w") as f:
                f.write("# Hz S RI R 50\n1e9 0 0 0 0 0 0 0 0\n")
            class _Opt(object):
                def solve(self, req):
                    return {"device_type": "ind", "geom": {"n": 2.5},
                            "actual_fingerprint": {"L0": 1e-9},
                            "source": "optimization", "snp": src}
            br = em_bridge.EmBridge({"em_bridge": {}}, em_optimizer=_Opt(),
                                    library=lib)
            qr = br._solve({}, "ind", {"L0": 1e-9}, {}, None, None,
                           {"step_rel": 0.01}, 1e9)
            self.assertEqual(qr["status"], "hit_nearest")
            self.assertTrue(qr["snp_path"].startswith(lib.snp_dir))
            self.assertTrue(os.path.isfile(qr["snp_path"]))
            rec = lib.get_by_key(qr["grid_key"])
            self.assertIsNotNone(rec)
            self.assertTrue(rec.get("snp_sha1"))
            self.assertEqual(rec.get("snp_path"), qr["snp_path"])
            lib.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_payload_has_gui_query_fields(self):
        """EmBridge.run 的 em_actual 必须带 GUI 读取的 target/status/snp_path/record。"""
        import em_bridge
        class _Result(object):
            pass
        class _Delegate(object):
            def run(self, values, iter_dir):
                res = _Result()
                res.values = dict(values)
                return res
        class _Lib(object):
            def close(self):
                pass
        qr = {"status": "hit_nearest", "distance": 0.2, "delivered": 2e-9,
              "actual_fingerprint": {"L0": 2e-9}, "delta_vs_request": {"L0": 1.0},
              "snp_path": os.path.join("work", "x.s2p"),
              "grid_key": "ind:L0=2e-09", "source": "catalog",
              "record": {"geom": {"n": 2.0}}, "clipped": False}
        cfg = {"em_bridge": {"devices": [
            {"device_type": "ind", "target_var": "L1", "metric": "L0"}]}}
        br = em_bridge.EmBridge(cfg, delegate=_Delegate(), library=_Lib())
        br.resolve = lambda req: qr
        res = br.run({"L1": 1e-9}, os.path.join("work", "iter_0000"))
        ea = res.em_actual["L1"]
        self.assertEqual(ea["target"], {"L0": 1e-9})
        self.assertEqual(ea["status"], "hit_nearest")
        self.assertEqual(ea["snp_path"], qr["snp_path"])
        self.assertEqual(ea["snp"], qr["snp_path"])
        self.assertEqual(ea["record"], qr["record"])
        self.assertEqual(res.values["L1"], 2e-9)

    def test_solve_injects_dev_f0_and_skips_hard_failures(self):
        """_solve 必须把解析后的 dev/f0 补进请求；error/miss 不得入库。"""
        import shutil
        import em_bridge
        tmp = tempfile.mkdtemp(prefix="em_solve_")
        try:
            lib = em_bridge.EmLibrary(os.path.join(tmp, "lib.sqlite3"))
            class _Cap(object):
                def __init__(self):
                    self.seen = None
                    self.entry = {}
                def solve(self, req):
                    self.seen = dict(req)
                    return dict(self.entry)
            opt = _Cap()
            br = em_bridge.EmBridge({"em_bridge": {}}, em_optimizer=opt,
                                    library=lib)
            gspec = {"step_rel": 0.01}
            opt.entry = {"status": "error", "reason": "f_op 缺失"}
            qr = br._solve({}, "tl", {"Z0": 50.0}, {}, None, None, gspec, 2e9)
            self.assertEqual(qr["status"], "error")
            self.assertEqual(opt.seen.get("device_type"), "tl")
            self.assertEqual(opt.seen.get("f_op"), 2e9)
            self.assertIsNone(lib.get_by_key("tl:Z0=50"))
            opt.entry = {"status": "miss"}
            qr2 = br._solve({}, "tl", {"Z0": 50.0}, {}, None, None, gspec, 2e9)
            self.assertEqual(qr2["status"], "miss")
            self.assertIsNone(lib.get_by_key("tl:Z0=50"))
            lib.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sqlite_records_keep_usable_key(self):
        """_row_to_rec 不能删掉 _F.USABLE（SQLite 与 JSONL 记录字段一致）。"""
        import shutil
        import em_bridge
        tmp = tempfile.mkdtemp(prefix="em_usable_")
        try:
            lib = em_bridge.EmLibrary(os.path.join(tmp, "lib.sqlite3"))
            rec = {"device_type": "ind", "grid_key": "ind:L0=1e-09",
                   "actual_fingerprint": {"L0": 1e-9},
                   "request_fingerprint": {"L0": 1e-9}, "source": "catalog"}
            lib.put(dict(rec))
            got = lib.get_by_key("ind:L0=1e-09")
            self.assertIn("usable", got)
            self.assertTrue(got["usable"])
            bad = dict(rec)
            bad["usable"] = False
            bad["grid_key"] = "ind:L0=2e-09"
            bad["actual_fingerprint"] = {"L0": 2e-9}
            lib.put(bad)
            self.assertIsNone(lib.get_by_key("ind:L0=2e-09"))
            lib.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_bad_f_op_does_not_abort(self):
        """坏 f_op 只标记该器件 error，不得让整次电路优化抛异常。"""
        import em_bridge
        class _Result(object):
            pass
        class _Delegate(object):
            def run(self, values, iter_dir):
                res = _Result()
                res.values = dict(values)
                return res
        class _Lib(object):
            def close(self):
                pass
        cfg = {"em_bridge": {"devices": [
            {"device_type": "ind", "target_var": "L1",
             "f_op": "not-a-frequency"}]}}
        br = em_bridge.EmBridge(cfg, delegate=_Delegate(), library=_Lib())
        res = br.run({"L1": 1e-9}, os.path.join("work", "iter_0000"))
        ea = res.em_actual["L1"]
        self.assertEqual(ea["status"], "error")
        self.assertTrue(any("f_op" in w for w in ea["warns"]))
        self.assertEqual(res.values["L1"], 1e-9)


class TestV12Modules(unittest.TestCase):
    """V12: auto netlist, scoped parameter patching, EM two-stage alignment."""

    DECK = (
        "simulator lang=spectre\n"
        "parameters L1=250p L2=280p \\\n"
        "  k0=0.6 cin=100f\n"
        "subckt ind a b\n"
        "parameters L1=200p k0=0.5\n"
        "L1 a b L1\n"
        "ends ind\n"
        "X1 n1 n2 ind L1=210p k0=0.55\n"
    )

    def test_max_iter_must_be_positive(self):
        """settings.max_iter <= 0 is a config error, not 'run nothing quietly'."""
        with self.assertRaises(ValueError):
            Settings({"max_iter": 0})
        with self.assertRaises(ValueError):
            Settings({"max_iter": -3})

    def test_fixed_and_optimized_variable_conflict(self):
        """A name in both variables and fixed_variables is rejected."""
        from optimizer import build_variables
        cfg = example_config()
        cfg["fixed_variables"] = [{"name": "L1", "value": "250p"}]
        with self.assertRaises(ValueError) as ctx:
            build_variables(cfg)
        self.assertIn("L1", str(ctx.exception))

    def test_resume_finished_run_keeps_targets_met(self):
        """Re-running a finished run with no new budget keeps its verdict."""
        tmp = tempfile.mkdtemp(prefix="v12_resume_")
        cfg = example_config()
        cfg["output_dir"] = os.path.join(tmp, "run")
        # Goals the mock model always meets on the first evaluation.
        cfg["targets"] = [
            {"output": "S21 dB20", "metric": "band_min", "band": ["24G", "44G"],
             "op": ">=", "goal": -100, "tol": 0.5},
            {"output": "S21 dB20", "metric": "band_flatness", "band": ["24G", "44G"],
             "op": "<=", "goal": 100, "tol": 0.5},
        ]
        cfg["settings"] = {"max_iter": 3, "patience": 100, "seed": 0}
        first = run_optimization(cfg, mock=True)
        self.assertEqual(first["stop_reason"], "targets_met")
        # Budget is already spent: start == max_iter, no new simulation.
        cfg["settings"]["max_iter"] = 1
        again = run_optimization(cfg, mock=True)
        self.assertEqual(again["stop_reason"], "targets_met")
        self.assertEqual(again["best_variables"], first["best_variables"])
        self.assertEqual(again["iterations"], 0)
        self.assertEqual(again["iterations_total"], 1)

    def test_coordinate_search_converges(self):
        """Coordinate strategy walks down each axis from x0."""
        import algorithm
        alg = algorithm.create("coordinate", ["x"], [(0.0, 1.0)],
                               x0=[0.7], seed=0)
        for _ in range(80):
            x = alg.ask()[0]
            alg.tell([x], [(x[0] - 0.4) ** 2])
        self.assertAlmostEqual(alg.best()[0][0], 0.4, places=2)

    def test_netlist_params_continuation_and_scopes(self):
        """Continuation lines, subckt defaults and instance overrides patched."""
        import netlist_params
        text, rep = netlist_params.patch_netlist(
            self.DECK, {"L1": "260p", "L2": "290p", "k0": "0.7",
                        "cin": "110f"})
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["missing"], [])
        self.assertIn("parameters L1=260p L2=290p", text)
        self.assertIn("k0=700m", text)
        self.assertIn("cin=110f", text)
        self.assertNotIn("L1=250p", text)
        self.assertNotIn("L1=200p", text)
        self.assertNotIn("L1=210p", text)

    def test_netlist_params_missing_is_loud(self):
        """A variable absent everywhere must fail, not be silently skipped."""
        import netlist_params
        text, rep = netlist_params.patch_netlist(self.DECK, {"nope": "1"})
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["missing"], ["nope"])
        self.assertEqual(text, self.DECK)

    def test_netlist_params_rewrite_compat(self):
        """The old top-level rewrite entry stays usable by other callers."""
        line = parse_netlist_parameters(self.DECK)
        self.assertIn("L1", line)
        out, n = rewrite_netlist_parameters(self.DECK, "L1=2n")
        self.assertEqual(n, 1)
        self.assertIn("L1=2n", out)

    def test_netlist_analyses_detected(self):
        """ADE writes `<name> <type>`; comments and instances never count."""
        self.assertEqual(list(netlist_analyses(self.DECK)), [])
        deck = self.DECK + ("ac1 ac start=1G stop=2G dec=10\n"
                            "sp1 sp ports=[P0 P1] start=1G stop=2G\n")
        self.assertEqual(list(netlist_analyses(deck)), ["ac", "sp"])
        # Hand-written type-first decks still count.
        self.assertEqual(
            list(netlist_analyses("sp sp ports=[a b] start=1 stop=2\n")),
            ["sp"])
        self.assertEqual(list(netlist_analyses("tf tf start=1 stop=2\n")),
                         ["tf"])
        for line in ("save sp\n", "sp (a 0) capacitor c=1p\n",
                     "// ac1 ac start=1\n"):
            self.assertEqual(list(netlist_analyses(line)), [], line)
        # 三个分析类型集必须完全一致：少一个会让合法网表被预检判无分析、
        # 或让分析行的参数被当成设计变量改写；多一个也会让预检口径漂移。
        # 语句关键字集只需覆盖 optimizer 的指令集（merger 还认 `inc`）。
        import netlist_create
        import netlist_params
        from optimizer import _ANALYSIS_KEYWORDS, _NETLIST_DIRECTIVES
        drift = sorted(set(_ANALYSIS_KEYWORDS) ^ netlist_create._ANALYSES)
        self.assertEqual(drift, [], drift)
        drift = sorted(set(_ANALYSIS_KEYWORDS) ^ set(netlist_params._ANALYSIS))
        self.assertEqual(drift, [], drift)
        missing = sorted(set(_NETLIST_DIRECTIVES)
                         - netlist_create._NON_ANALYSIS_HEADS)
        self.assertEqual(missing, [], missing)

    def test_preflight_rejects_unusable_deck(self):
        """A deck that cannot produce curves fails at start, not after 50 rounds."""
        tmp = tempfile.mkdtemp(prefix="v12_pf_")
        missing = os.path.join(tmp, "missing.scs")
        with self.assertRaises(RuntimeError) as ctx:
            preflight_netlist({"netlist": missing})
        self.assertIn("网表不存在", str(ctx.exception))

        bare = os.path.join(tmp, "input.scs")
        with open(bare, "w") as f:
            f.write(self.DECK)
        with self.assertRaises(RuntimeError) as ctx:
            preflight_netlist({"netlist": bare})
        self.assertIn("没有任何分析语句", str(ctx.exception))

        good = os.path.join(tmp, "good.scs")
        with open(good, "w") as f:
            f.write(self.DECK + "sp sp ports=[P0 P1] start=1G stop=2G\n")
        self.assertIn("sp", preflight_netlist({"netlist": good}))

        # ADE names analyses (`ac1 ac ...`); a valid deck must not be rejected.
        named = os.path.join(tmp, "named.scs")
        with open(named, "w") as f:
            f.write(self.DECK + "ac1 ac start=1G stop=2G dec=10\n")
        self.assertIn("ac", preflight_netlist({"netlist": named}))

        with self.assertRaises(RuntimeError) as ctx:
            preflight_netlist({"netlist": good, "analyses": "sp dc"})
        self.assertIn("dc", str(ctx.exception))

        self.assertEqual(preflight_netlist({}), "")

    def test_netlist_create_read_design_and_path(self):
        """ADE state + explicit view -> mixed source, expected config path."""
        import netlist_create
        tmp = tempfile.mkdtemp(prefix="v12_nc_")
        state = os.path.join(tmp, "spectre_state1")
        os.makedirs(state)
        with open(os.path.join(state, "ADE_state.info"), "w") as f:
            f.write("designInfo = '(\"LibA\" \"CellA\" \"schematic\" "
                    "\"spectre\")\n")
        design = netlist_create.read_design({
            "state_dir": state, "library": "LibB", "netlist_view": "config"})
        self.assertEqual(design["library"], "LibB")
        self.assertEqual(design["cell"], "CellA")
        self.assertEqual(design["view"], "config")
        self.assertEqual(design["source"], "mixed")
        path = netlist_create.expected_netlist_path(
            design, {"sim_root": tmp})
        self.assertTrue(path.replace("\\", "/").endswith(
            "/spectre/config/netlist/input.scs"), path)

    def test_netlist_create_merge_testbench(self):
        """Model includes and ADE analyses survive a config createNetlist."""
        import netlist_create
        old = (
            "simulator lang=spectre\n"
            "parameters fin=36G\n"
            "include \"/pdk/models.lib\" section=tt\n"
            "subckt s a b\nends s\n"
            "I0 (a b) s\n"
            "sp1 sp ports=[PORT0 PORT1] start=16G stop=56G lin=81\n"
            "sweep1 sweep param=pin start=-20 stop=10 step=1 {\n"
            "  // bias sweep\n"
            "  hb hb fundfreqs=[(fin)]\n"
            "}\n"
            "save PORT1:p\n"
            "saveOptions options save=selected\n"
            "include \"/proj/cell.pex.netlist\"\n")
        new = (
            "simulator lang=spectre\n"
            "global 0\n"
            "parameters fin=36G\n"
            "subckt s a b\nends s\n"
            "I0 (a b) s\n"
            "saveOptions options save=selected\n"
            "include \"/proj/cell.pex.netlist\"\n")
        merged, tb = netlist_create.merge_testbench(new, old)
        self.assertEqual(tb["includes"], 1, tb)
        self.assertEqual(sorted(tb["analyses"]), ["sp", "sweep"])
        self.assertIn('include "/pdk/models.lib"', merged)
        self.assertIn("sp1 sp ports=[PORT0 PORT1]", merged)
        self.assertIn("sweep1 sweep param=pin", merged)
        self.assertIn("hb hb fundfreqs", merged)
        self.assertIn("save PORT1:p", merged)
        self.assertEqual(merged.count("include \"/proj/cell.pex.netlist\""),
                         1)

    def test_netlist_create_carry_over_bare_params(self):
        """ADE bare declarations keep the previous deck's effective values."""
        import netlist_create
        old = ("simulator lang=spectre\n"
               "parameters Ls2=140p Lp2=400p k2=475m \\\n"
               "    cneu=40f\n"
               "I3 (a b) xfmr_tap _par0=Ls2 _par1=Lp2 _par2=k2\n")
        new = ("simulator lang=spectre\n"
               "parameters Ls2 Lp2 k2=475m cneu=40f\n"
               "I3 (a b) xfmr_tap _par0=Ls2 _par1=Lp2 _par2=k2\n")
        merged, changes = netlist_create.carry_over_parameters(new, old)
        self.assertEqual(sorted(c["name"] for c in changes),
                         ["Lp2", "Ls2"])
        self.assertIn("Ls2=140p", merged)
        self.assertIn("Lp2=400p", merged)
        self.assertIn("_par0=Ls2", merged)

    def test_netlist_create_log_classification(self):
        """Echoed IL source is not an error; warnings stay warnings."""
        import netlist_create
        text = ("\\i printf(\"OPT_NETLIST_FAIL createNetlist returned nil\")\n"
                "\\o WARNING (OSSHNL-379): unable to descend\n"
                "\\o ERROR (ADE-5726): Unable to open config\n"
                "\\o Errors: 0\tWarnings: 0\n")
        errors, warnings = netlist_create._scan_lines(text)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("ADE-5726", errors[0])
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("OSSHNL-379", warnings[0])

    def test_netlist_create_work_dir_finds_cds_lib(self):
        """Virtuoso cwd walks up to the project root that owns cds.lib."""
        import netlist_create
        tmp = tempfile.mkdtemp(prefix="v12_cds_")
        root = os.path.join(tmp, "proj")
        state = os.path.join(root, "sim", "Cell", "spectre_state1")
        os.makedirs(state)
        with open(os.path.join(root, "cds.lib"), "w") as f:
            f.write("DEFINE LibA ./LibA\n")
        got = netlist_create._work_dir({"state_dir": state})
        self.assertEqual(os.path.normcase(os.path.abspath(got)),
                         os.path.normcase(os.path.abspath(root)))

    def test_em_align_csv_and_match_targets(self):
        """Generic CSV headers survive; targets carry x/y limits."""
        import em_align
        tmp = tempfile.mkdtemp(prefix="v12_em_")
        path = os.path.join(tmp, "em.csv")
        with open(path, "w") as f:
            f.write("freq,S21 dB20\n")
            for i in range(11):
                f.write("%.6e,%.6f\n" % (24e9 + i * 1e9, -3.0 + 0.1 * i))
        curves = em_align.load_curves(path)
        self.assertIn("S21 dB20", curves)
        targets = em_align.build_match_targets(curves, max_points=6,
                                               tol=0.05)
        self.assertTrue(targets)
        t = targets[0]
        self.assertEqual(t["curve_op"], "match")
        self.assertEqual(t["output"], "S21 dB20")
        self.assertLessEqual(len(t["limit"]["x"]), 6)
        self.assertEqual(len(t["limit"]["x"]), len(t["limit"]["y"]))

    def test_em_align_correction_serializable(self):
        """correction_report is JSON-safe and flags a close alignment."""
        import em_align
        xs = [24e9 + i * 1e9 for i in range(11)]
        ys = [-3.0 + 0.1 * i for i in range(11)]
        rep = em_align.correction_report(
            {"L1": 250e-12}, {"L1": 260e-12},
            {"S21 dB20": (xs, ys)},
            {"S21 dB20": (xs, [y - 0.05 for y in ys])},
            {"S21 dB20": (xs, [y - 0.04 for y in ys])},
            var_specs={"L1": {"name": "L1", "min": "150p", "max": "350p"}},
            rms_tol=0.1, max_tol=0.5)
        json.dumps(rep)
        self.assertTrue(rep["aligned"])
        self.assertEqual(rep["summary"]["n_vars_changed"], 1)

    def test_run_em_align_mock_known_answer(self):
        """Stage1 -> y_em -> stage2 produces x_em_ideal and both reports."""
        tmp = tempfile.mkdtemp(prefix="v12_e2e_")
        netlist = os.path.join(tmp, "dummy.scs")
        with open(netlist, "w") as f:
            f.write("// dummy\n")
        cfg = mock_config(os.path.join(tmp, "stage1"), max_iter=6)
        cfg["netlist"] = netlist
        cfg["auto_netlist"] = False
        cfg["state_dir"] = ""
        run_optimization(cfg, mock=True, max_iter=6)
        truth = {"L1": 260e-12, "L2": 290e-12, "k0": 0.70,
                 "cin": 110e-15}
        res = MockRunner(cfg).run(truth, tempfile.mkdtemp(prefix="v12_em_"))
        xs, ys = res.curves["S21 dB20"]
        em_csv = os.path.join(tmp, "em.csv")
        with open(em_csv, "w") as f:
            f.write("freq,S21 dB20\n")
            for xi, yi in zip(xs, ys):
                f.write("%.9e,%.9f\n" % (xi, yi))
        report = run_em_align(cfg, os.path.join(tmp, "stage1"),
                              em_curve=em_csv, max_iter=12, mock=True)
        payload = report["em_align"]
        self.assertTrue(os.path.isfile(payload["report_json"]))
        self.assertTrue(os.path.isfile(payload["report_md"]))
        self.assertEqual(payload["stage1_variables"]["L1"], "250p")
        self.assertTrue(payload["stage2_variables"])
        self.assertNotEqual(payload["stage2_variables"], payload["stage1_variables"])
        outs = [t["output"] for t in payload["targets"]]
        self.assertEqual(len(outs), len(set(outs)), outs)
        self.assertEqual(set(payload["correction"]["curves"]), set(outs))
        self.assertIn("correction", payload)


class _ScriptRunner:
    """Sim runner stub that returns scripted results in order."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def run(self, values, iter_dir):
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return SimResult(status="fail", message="script exhausted")


class TestPreflightAndCurveGuard(unittest.TestCase):
    """1.4.1: analysis preflight and the zero-curve abort guard."""

    def _opt(self):
        out = os.path.join(tempfile.mkdtemp(prefix="v141_opt_"), "out")
        return Optimization(mock_config(out, 3), mock=True, max_iter=3)

    def test_zero_curve_abort_direct(self):
        opt = self._opt()
        opt.runner = _ScriptRunner(
            [SimResult(status="ok", message="no outputs")])
        with self.assertRaises(RuntimeError) as ctx:
            opt.run()
        msg = str(ctx.exception)
        self.assertIn("第 0 轮", msg)
        self.assertIn("输出曲线", msg)
        self.assertNotIn("首轮", msg)

    def test_zero_curve_abort_after_infra_retry(self):
        # A first INFRA failure triggers one retry; that retry must obey
        # the same "ok but no curves" guard as the first attempt.
        opt = self._opt()
        opt.runner = _ScriptRunner([
            SimResult(status="fail", message="cannot start eval serve"),
            SimResult(status="ok", message="no outputs"),
        ])
        with self.assertRaises(RuntimeError) as ctx:
            opt.run()
        self.assertIn("第 0 轮", str(ctx.exception))
        self.assertEqual(opt.runner.calls, 2)

    def test_preflight_checks_discovered_deck(self):
        # No explicit netlist: the runner finds input.scs under the cell's
        # simulation tree, so preflight must check that same file.
        tmp = tempfile.mkdtemp(prefix="v141_disc_")
        deck_dir = os.path.join(tmp, "Lib", "Cell", "spectre",
                                "schematic", "netlist")
        os.makedirs(deck_dir)
        deck = os.path.join(deck_dir, "input.scs")
        with open(deck, "w") as f:
            f.write("simulator lang=spectre\n"
                    "sp1 sp ports=[a b] start=1 stop=2\n")
        cfg = {"sim_root": tmp, "library": "Lib", "cell": "Cell"}
        self.assertEqual(os.path.abspath(deck), resolve_netlist_path(cfg))
        self.assertEqual(os.path.abspath(deck),
                         RealRunner(dict(cfg), ".")._find_deck())
        self.assertIn("sp", preflight_netlist(cfg))
        with open(deck, "w") as f:
            f.write("simulator lang=spectre\nparameters a=1\n")
        with self.assertRaises(RuntimeError) as ctx:
            preflight_netlist(cfg)
        self.assertIn("没有任何分析语句", str(ctx.exception))

    def test_dcop_analysis_consistent_across_modules(self):
        """The deck merger accepts dcop; preflight/patcher must agree.

        A deck whose only analysis is ``dcop1 dcop ...`` was accepted by
        netlist_create but rejected by preflight as "no analysis", and
        netlist_params would patch its options as design parameters.
        """
        tmp = tempfile.mkdtemp(prefix="v141_dcop_")
        deck = os.path.join(tmp, "input.scs")
        with open(deck, "w") as f:
            f.write('simulator lang=spectre\n'
                    'dcop1 dcop write="spectre.dc"\n')
        self.assertEqual(list(netlist_analyses("dcop1 dcop\n")), ["dcop"])
        self.assertIn("dcop", preflight_netlist({"netlist": deck}))
        import netlist_create
        import netlist_params
        self.assertIn("dcop", netlist_create._ANALYSES)
        self.assertIn("dcop", netlist_params._ANALYSIS)

    def test_run_optimization_preflight_rejects_bad_deck(self):
        # The rejection must fire through the real entry point, not only
        # when preflight_netlist() is called directly.
        tmp = tempfile.mkdtemp(prefix="v141_pf_")
        deck = os.path.join(tmp, "input.scs")
        with open(deck, "w") as f:
            f.write("simulator lang=spectre\nparameters L1=1n\n")
        cfg = example_config()
        cfg["netlist"] = deck
        cfg["auto_netlist"] = False
        cfg["output_dir"] = os.path.join(tmp, "out")
        with self.assertRaises(RuntimeError) as ctx:
            run_optimization(cfg, mock=False)
        self.assertIn("分析语句", str(ctx.exception))


class TestSearchStrategyFixes(unittest.TestCase):
    """1.4.2: grid resume, scalariser wiring and CMA h_sigma scale."""

    def test_grid_resume_rebuilds_grid(self):
        # A snapshot taken with levels=8 used to be restored next to a
        # grid rebuilt from the new (smaller) budget: the restored order
        # indices then ran off the end and ask() raised IndexError.
        import algorithm as A
        old = A.create("grid", ["a", "b"], [(0.0, 1.0)] * 2, seed=1,
                       budget=100)
        for _ in range(5):
            pt = old.ask(1)[0]
            old.tell([pt], [sum(v * v for v in pt)])
        state = from_jsonable(json.loads(
            json.dumps(to_jsonable(old.snapshot()))))
        resumed = A.create("grid", ["a", "b"], [(0.0, 1.0)] * 2, seed=1,
                           budget=10)
        self.assertNotEqual(len(resumed._grid), len(old._grid))
        resumed.restore(state)
        self.assertEqual(resumed.levels, old.levels)
        self.assertEqual(len(resumed._grid), len(old._grid))
        allowed = set(tuple(p) for p in resumed._grid)
        for _ in range(20):
            pt = resumed.ask(1)[0]
            self.assertIn(tuple(pt), allowed)

    def test_worstspec_scalarises_the_objective_vector(self):
        tr = Transform([
            {"name": "a", "init": 0.5, "min": 0.0, "max": 1.0},
            {"name": "b", "init": 0.5, "min": 0.0, "max": 1.0}])
        algo = build_optimizer("worstspec", tr, {"a": 0.5, "b": 0.5},
                               ["a", "b"], seed=1, n_obj=2)
        self.assertEqual(algo.inner.n_obj, 2)
        algo.ask()
        algo.tell(1.0, F=[0.2, 0.9], G=[])
        # worstspec = max of the objectives, not their sum (1.1).
        self.assertAlmostEqual(algo.inner.inner.best_y, 0.9)

    def test_weighted_uses_target_weights(self):
        work = tempfile.mkdtemp()
        cfg = example_config()
        cfg["output_dir"] = os.path.join(work, "out")
        cfg["settings"] = {"strategy": "weighted", "mode": "pareto",
                           "max_iter": 3, "seed": 1, "patience": 99}
        cfg["targets"] = [
            {"output": "S21 dB20", "metric": "band_min",
             "band": ["24G", "44G"], "op": ">=", "goal": 6, "tol": 0.5,
             "role": "objective", "weight": 1.0},
            {"output": "S21 dB20", "metric": "band_flatness",
             "band": ["24G", "44G"], "op": "<=", "goal": 0.2, "tol": 0.2,
             "role": "objective", "weight": 3.0}]
        opt = Optimization(cfg, mock=True, max_iter=3)
        import algorithm as A
        self.assertIsInstance(opt.algo.inner, A.WeightedScalar)
        self.assertEqual(opt.algo.inner.weights, [1.0, 3.0])
        opt.algo.ask()
        opt.algo.tell(1.0, F=[0.2, 0.9], G=[])
        self.assertAlmostEqual(opt.algo.inner.inner.best_y,
                               (0.2 + 3.0 * 0.9) / 4.0)
        # All-zero weights keep the equal-weight fallback instead of
        # raising "weights sum to zero" in the middle of a run.
        cfg["output_dir"] = os.path.join(work, "out_zero")
        for target in cfg["targets"]:
            target["weight"] = 0.0
        opt0 = Optimization(cfg, mock=True, max_iter=3)
        self.assertIsNone(opt0.algo.inner.weights)
        opt0.algo.ask()
        opt0.algo.tell(1.0, F=[0.2, 0.9], G=[])
        self.assertAlmostEqual(opt0.algo.inner.inner.best_y, 0.55)

    def test_scalar_wrapper_restart_reaches_inner(self):
        import algorithm as A
        for name in ("weighted", "worstspec"):
            algo = A.create(name, ["a", "b"], [(0.0, 1.0)] * 2, seed=2,
                            budget=100)
            for _ in range(20):
                pt = algo.ask(1)[0]
                algo.tell([pt], [sum((v - 0.4) ** 2 for v in pt)])
            best = algo.best()[1]
            algo.restart(spread=0.25)
            self.assertEqual(algo.inner.restarts, 1)
            self.assertAlmostEqual(algo.inner.last_restart_radius, 0.25)
            self.assertEqual(algo.best()[1], best)  # inner best survives

    def test_constraint_vector_reaches_inner_as_one_point(self):
        # SpaceAdapter handed the flat per-point G straight to engines
        # that index G[i], so G[0] was a float: spec/pareto runs with a
        # constraint or error-role target raised TypeError at tell.
        tr = Transform([
            {"name": "a", "init": 0.5, "min": 0.0, "max": 1.0},
            {"name": "b", "init": 0.5, "min": 0.0, "max": 1.0}])
        for name in ("lshade", "cmaes", "tpe", "nsga2"):
            algo = build_optimizer(name, tr, {"a": 0.5, "b": 0.5},
                                   ["a", "b"], seed=1, n_obj=1,
                                   n_constr=1)
            algo.ask()
            algo.tell(0.3, F=[0.3], G=[0.25])
        # Deb feasibility still applies: infeasible points never win.
        algo = build_optimizer("lshade", tr, {"a": 0.5, "b": 0.5},
                               ["a", "b"], seed=1, n_obj=1, n_constr=1)
        algo.ask()
        algo.tell(0.3, F=[0.3], G=[0.25])
        self.assertIsNone(algo.inner.best_y)
        algo.ask()
        algo.tell(0.2, F=[0.2], G=[0.0])
        self.assertAlmostEqual(algo.inner.best_y, 0.2)

    def test_cmaes_hsig_uses_generation_scale(self):
        import algorithm as A
        algo = A.create("cmaes", ["x%d" % i for i in range(5)],
                        [(0.0, 1.0)] * 5, x0=[0.5] * 5, seed=1)
        algo._gen_count = 2
        # A path length modestly above the random-selection expectation
        # is a normal step: the canonical 2*(g+1) denominator gives
        # hsig=1, while the old 2*g/lambda scale forced hsig=0 here.
        self.assertEqual(algo._hsig(1.4 * algo.chiN), 1.0)
        self.assertEqual(algo._hsig(10.0 * algo.chiN), 0.0)


class _GVar(object):
    """Minimal StringVar stand-in for headless GUI tests."""

    def __init__(self, text=""):
        self.text = text

    def get(self):
        return self.text

    def set(self, value):
        self.text = str(value)


class _GTree(object):
    """Minimal Treeview stand-in (rows are tuples of strings)."""

    def __init__(self, rows=()):
        self.rows = [tuple(r) for r in rows]

    def get_children(self):
        return [str(i) for i in range(len(self.rows))]

    def item(self, iid, what):
        if what == "values":
            return self.rows[int(iid)]
        raise KeyError(what)

    def delete(self, iid):
        self.rows.pop(int(iid))

    def insert(self, _parent, _index, values=()):
        self.rows.append(tuple(values))
        return str(len(self.rows) - 1)

    def selection(self):
        return []


def _fake_gui(**attrs):
    """OptimizerGui methods without creating a Tk root."""
    import gui

    class _Fake(gui.OptimizerGui):
        def __init__(self):
            self.__dict__.update(attrs)

    fake = _Fake()
    fake._dialog = lambda *a, **k: None
    fake._set_status = lambda *a, **k: None
    return fake


def _gui_fields(**overrides):
    keys = ("name", "library", "cell", "view", "state", "state_dir",
            "work_dir", "virtuoso_bin", "output_dir", "analyses", "save_raw",
            "sim_timeout", "netlist", "sim_root", "netlist_view")
    fields = {key: _GVar("") for key in keys}
    fields.update({"name": _GVar("t"), "analyses": _GVar("sp"),
                   "save_raw": _GVar("1"), "sim_timeout": _GVar("3600")})
    for key, value in overrides.items():
        fields[key] = _GVar(value)
    return fields


def _collect_cfg_stub(**overrides):
    """A GUI whose _collect_cfg() can run without widgets."""
    attrs = {
        "fields": _gui_fields(),
        "spectre_bin_var": _GVar("spectre"),
        "path_append_var": _GVar(""),
        "env_fields": {},
        "env_extra_text": type("T", (), {"get": lambda self, *a: ""})(),
        "var_extra": {},
        "var_tree": _GTree(),
        "fix_tree": _GTree(),
        "target_tree": _GTree(),
        "target_specs": {},
        "auto_netlist_var": _GVar(False),
        "set_fields": {},
        "extra_settings": {},
        "extra": {},
        "em_align_fields": {},
        "em_align_extra": {},
        "tech_tab": None,
    }
    attrs.update(overrides)
    return _fake_gui(**attrs)


class TestGuiConfigRoundTrip(unittest.TestCase):
    """GUI form round-trips: the run must see what the page shows."""

    def test_simset_netlist_uses_runtime_view_preference(self):
        import gui
        root = tempfile.mkdtemp()
        picks = {}
        for view, mtime in (("schematic", 2000), ("config", 1000)):
            d = os.path.join(root, "lib", "cell", "spectre", view)
            os.makedirs(d)
            p = os.path.join(d, "input.scs")
            with open(p, "w", encoding="utf-8") as f:
                f.write("parameters a=1\n")
            os.utime(p, (mtime, mtime))
            picks[view] = p
        g = _collect_cfg_stub(fields=_gui_fields(
            sim_root=root, library="lib", cell="cell",
            view="schematic", netlist_view="config"))
        got = g._simset_netlist_path(rescan=True)
        want = resolve_netlist_path({
            "sim_root": root, "library": "lib", "cell": "cell",
            "view": "schematic", "netlist_view": "config"})
        # The newest deck is schematic, but the run prefers netlist_view.
        self.assertEqual(os.path.normcase(got), os.path.normcase(want))
        self.assertEqual(os.path.normcase(got), os.path.normcase(picks["config"]))
        g.fields["netlist_view"].set("schematic")
        got2 = g._simset_netlist_path(rescan=True)
        self.assertEqual(os.path.normcase(got2), os.path.normcase(picks["schematic"]))

    def test_simset_signature_tracks_target_edits(self):
        fields = _gui_fields(state_dir="", sim_root=tempfile.mkdtemp())
        spec = {"output": "S21", "metric": "band_min", "op": ">=",
                "goal": 3, "band": ["24G", "44G"]}
        g = _collect_cfg_stub(fields=fields,
                              target_specs={"iid1": dict(spec)})
        sig1 = g._simset_signature()
        g.target_specs = {"iid1": dict(spec, band=["40G", "60G"])}
        sig2 = g._simset_signature()
        self.assertNotEqual(sig1, sig2)
        # Same definitions keep the 2 s refresh quiet.
        g.target_specs = {"iid1": dict(spec, band=["40G", "60G"])}
        self.assertEqual(sig2, g._simset_signature())

    def test_variable_metadata_survives_rename_and_undo(self):
        import gui
        extra = gui._var_extra_rename(
            {"w": {"scale": "log", "integer": True}}, "w", "w2")
        self.assertEqual(extra["w2"], {"scale": "log", "integer": True})
        self.assertNotIn("w", extra)
        self.assertEqual(gui._var_extra_drop(extra, ["w2"]), {})
        g = _collect_cfg_stub(
            var_tree=_GTree([("w2", "1u", "0.1u", "10u")]),
            var_extra=extra)
        cfg = g._collect_cfg(validate=False)
        self.assertEqual(cfg["variables"][0]["scale"], "log")
        self.assertIs(cfg["variables"][0]["integer"], True)
        # Undo must restore the metadata together with the row.
        g._vars_push_undo()
        g.var_extra = {}
        g._vars_restore(g._vars_undo_stack.pop())
        self.assertEqual(g.var_extra["w2"]["scale"], "log")

    def test_tech_fields_reset_and_stay_out_of_extra(self):
        import gui
        for key in ("techlib", "techlib_dir", "layermap", "proc", "cds_lib"):
            self.assertIn(key, gui._MANAGED_CFG_KEYS)
        tab = object.__new__(gui._TechTab)
        tab.vars = {k: _GVar(default) for k, _l, default, _k in gui._TechTab.FIELDS}
        tab.fill_from_cfg({"layermap": "/a.map", "proc": "/a.proc"})
        tab.fill_from_cfg({"cell": "x"})
        self.assertEqual(tab.vars["layermap"].get(), "")
        self.assertEqual(tab.vars["proc"].get(), "")
        self.assertEqual(tab.vars["techlib"].get(), "RFCMOS")
        # A cleared tech field must not be resurrected from the old config.
        class _Tech(object):
            def tech_cfg_keys(self):
                return {}
        g = _collect_cfg_stub(extra={}, tech_tab=_Tech())
        self.assertNotIn("layermap", g._collect_cfg(validate=False))

    def test_collect_cfg_runs_validation(self):
        rows = _GTree([("w", "1", "0", "2")])
        dup = _collect_cfg_stub(var_tree=rows, fix_tree=_GTree([("w", "1")]))
        self.assertIsNone(dup._collect_cfg())
        bad = _collect_cfg_stub(var_tree=_GTree([("w", "1", "0", "2")]),
                                set_fields={"max_iter": _GVar("0")})
        self.assertIsNone(bad._collect_cfg())

    def test_param_help_names_only_live_settings(self):
        """Help may not advertise a settings key the engine never reads."""
        import inspect
        import gui
        text = inspect.getsource(gui.OptimizerGui._show_param_help)
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "optimizer.py"), encoding="utf-8") as f:
            engine = f.read()
        for key in ("mode", "portfolio", "init_points"):
            self.assertIn(key, engine)
        self.assertEqual("noise_sigma" in engine, "noise_sigma" in text,
                         "help must not name a setting the engine lacks")


def _optional_tests_module(name):
    """Import an optional sibling test module; None only when it is absent.

    A SyntaxError or an import-time failure must fail the gate instead of
    silently shrinking the suite the way ``except Exception`` would.
    """
    try:
        return __import__(name)
    except ImportError:
        return None


def _test_classes_in(module):
    """Every unittest.TestCase subclass defined in one test module."""
    return [obj for _name, obj in sorted(vars(module).items())
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase)
            and obj is not unittest.TestCase]


class TestOptionalTestImport(unittest.TestCase):
    """Optional test modules are skipped only when genuinely absent."""

    def test_missing_module_is_none(self):
        self.assertIsNone(_optional_tests_module("_opt_missing_xyz"))

    def test_broken_module_raises(self):
        import shutil
        work = tempfile.mkdtemp()
        try:
            for name, body in (("_opt_broken_xyz", "raise RuntimeError('boo')\n"),
                               ("_opt_syntax_xyz", "def broken(:\n")):
                with open(os.path.join(work, name + ".py"), "w",
                          encoding="utf-8") as f:
                    f.write(body)
            sys.path.insert(0, work)
            try:
                self.assertRaises(RuntimeError, _optional_tests_module,
                                  "_opt_broken_xyz")
                self.assertRaises(SyntaxError, _optional_tests_module,
                                  "_opt_syntax_xyz")
            finally:
                sys.path.remove(work)
                sys.modules.pop("_opt_broken_xyz", None)
                sys.modules.pop("_opt_syntax_xyz", None)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_proc_suite_classes_are_discovered(self):
        proc_tests = _optional_tests_module("test_proc_cross_section")
        if proc_tests is None:
            self.skipTest("proc suite not deployed")
        names = [cls.__name__ for cls in _test_classes_in(proc_tests)]
        self.assertIn("ProcCrossSectionTest", names)


class TestSuiteRegistry(unittest.TestCase):
    """A new Test* class that is not listed below silently never runs."""

    def test_all_test_classes_are_registered(self):
        module = sys.modules[__name__]
        defined = set(name for name, obj in vars(module).items()
                      if name.startswith("Test") and isinstance(obj, type)
                      and issubclass(obj, unittest.TestCase))
        registered = set(cls.__name__ for cls in _TEST_CLASSES)
        self.assertEqual(sorted(defined - registered), [])


_TEST_CLASSES = (
    TestEng, TestTargets, TestAlgorithm, TestGlobalSearch, TestPipeline,
    TestStopSemantics, TestResumeIdentity,
    TestFixes, TestCurvesTabRemoved, TestRunDirs, TestHistorySeedV05,
    TestResumeEquivalenceV05, TestV06Hooks, TestToolDiscovery,
    TestTextEncoding, TestCompactLog, TestCompactRobust, TestTechProfile,
    TestS3EmBridge, TestAuditTrail, TestV12Modules,
    TestPreflightAndCurveGuard, TestSearchStrategyFixes,
    TestGuiConfigRoundTrip, TestSuiteRegistry, TestOptionalTestImport,
)


def run_selftests():
    """Run the regression tests. Return the number of failures."""
    # GUI classes stay here so a headless CLI run never imports tkinter.
    global OptimizerGui, PlotCanvas
    from gui import OptimizerGui, PlotCanvas
    # The proc cross-section suite lives in its own module; run every
    # Test* class it defines when the optional module is deployed.
    proc_tests = _optional_tests_module("test_proc_cross_section")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(cls) for cls in _TEST_CLASSES])
    if proc_tests is not None:
        for cls in _test_classes_in(proc_tests):
            suite.addTests(loader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return len(result.failures) + len(result.errors)


if __name__ == "__main__":
    sys.exit(1 if run_selftests() else 0)
