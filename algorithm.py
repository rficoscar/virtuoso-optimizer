#!/usr/bin/env python3
'Search algorithms for the Virtuoso parameter optimizer.'
import math
import random

# ---- Shared base contract and comparison helpers ----

class AlgorithmBase(object):
    'One black-box optimizer driven by ask/tell batches.'
    name = "base"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        if len(names) != len(bounds):
            raise ValueError("names/bounds length mismatch")
        self.names = list(names)
        self.bounds = [(float(lo), float(hi)) for lo, hi in bounds]
        self.n = len(self.names)
        self.n_obj = int(n_obj)
        self.n_constr = int(n_constr)
        self.budget = budget
        self.opts = dict(opts)
        self.rng = random.Random(seed)
        self.eval_count = 0
        self.restarts = 0
        self.last_restart_radius = 0.0
        if x0 is not None:
            self.x0 = self._clip(list(x0))
        else:
            self.x0 = [(lo + hi) / 2.0 for lo, hi in self.bounds]
        self.best_x = list(self.x0)
        self.best_y = None
    def _clip(self, pt):
        out = []
        for i, v in enumerate(pt):
            lo, hi = self.bounds[i]
            if v < lo:
                v = lo
            elif v > hi:
                v = hi
            out.append(float(v))
        return out
    def _to_unit(self, pt):
        out = []
        for i, v in enumerate(pt):
            lo, hi = self.bounds[i]
            out.append(0.0 if hi == lo else
                       min(1.0, max(0.0, (v - lo) / (hi - lo))))
        return out
    def _to_real(self, pt):
        out = []
        for i, u in enumerate(pt):
            lo, hi = self.bounds[i]
            out.append(lo + min(1.0, max(0.0, u)) * (hi - lo))
        return out
    def _random_point(self):
        return [self.rng.uniform(lo, hi) for lo, hi in self.bounds]
    def _record_best(self, pt, value):
        if value is None:
            return
        if self.best_y is None or value < self.best_y:
            self.best_y = value
            self.best_x = self._clip(list(pt))
    def ask(self, n=1):
        """Return a batch of up to n candidate points (list of lists)."""
        raise NotImplementedError
    def tell(self, X, F, G=None):
        'Report objectives for the points just asked, in order.'
        raise NotImplementedError
    def best(self):
        return (list(self.best_x), self.best_y)
    def snapshot(self):
        """State dict including RNG state (needed for strict resume)."""
        return {
            "name": self.name,
            "eval_count": self.eval_count,
            "restarts": self.restarts,
            "best_x": list(self.best_x),
            "best_y": self.best_y,
            "rng": self.rng.getstate(),
            "last_restart_radius": self.last_restart_radius,
        }
    def restore(self, state):
        """Restore from snapshot(). Subclasses extend then call this."""
        self.eval_count = state.get("eval_count", 0)
        self.restarts = state.get("restarts", 0)
        self.best_x = list(state.get("best_x", self.best_x))
        self.best_y = state.get("best_y", None)
        rng = state.get("rng")
        if rng is not None:
            self.rng.setstate(rng)
        self.last_restart_radius = float(state.get("last_restart_radius", 0.0))
    def restart(self, spread=0.15):
        self.restarts += 1
        self.last_restart_radius = spread
    def simplex_size(self):
        return 0.0
    def seed_history(self, points):
        'Warm-start from earlier evaluations without re-simulating them.'
        for item in points:
            if item is None:
                continue
            x = self._clip(list(item[0]))
            try:
                y = float(scalar_of(item[1]))
            except (TypeError, ValueError):
                continue
            g = item[2] if len(item) > 2 else None
            self.eval_count += 1
            if g is None or constraint_violation(g) <= 0.0:
                self._record_best(x, y)
    @staticmethod
    def _sort_seeds(points):
        def key(item):
            g = item[2] if len(item) > 2 else None
            v = constraint_violation(g)
            try:
                y = float(scalar_of(item[1]))
            except (TypeError, ValueError):
                y = float("inf")
            return (v, y)
        return sorted((p for p in points if p is not None), key=key)

# ---- Shared comparison helpers (Deb feasibility rules + batch utilities) ----

def constraint_violation(g):
    """Total violation of a constraint vector (<=0 means feasible)."""
    if not g:
        return 0.0
    return sum(v for v in g if v > 0.0)

def deb_better(fa, ga, fb, gb):
    'True when a is better than b under Deb constraint domination.'
    va = constraint_violation(ga)
    vb = constraint_violation(gb)
    fa_ok = va <= 0.0
    fb_ok = vb <= 0.0
    if fa_ok and not fb_ok:
        return True
    if fb_ok and not fa_ok:
        return False
    if not fa_ok and not fb_ok:
        return va < vb
    sa = sum(fa) if isinstance(fa, (list, tuple)) else fa
    sb = sum(fb) if isinstance(fb, (list, tuple)) else fb
    return sa < sb

def scalar_of(f):
    """Collapse F[i] to one float (weighted-ready single score)."""
    if isinstance(f, (list, tuple)):
        return sum(f)
    return f
# ---- Registry: name -> algorithm class ----
_REGISTRY = {}

def register(name, cls):
    """Register one algorithm class under a strategy name."""
    _REGISTRY[name] = cls
    return cls

def available():
    """Sorted list of registered strategy names."""
    return sorted(_REGISTRY)

def create(name, names, bounds, x0=None, n_obj=1, n_constr=0,
           seed=0, budget=None, **opts):
    """Build one algorithm instance by strategy name."""
    if name not in _REGISTRY:
        raise ValueError(
            "unknown strategy %r (have: %s)" % (name, ", ".join(available())))
    return _REGISTRY[name](names, bounds, x0=x0, n_obj=n_obj,
                           n_constr=n_constr, seed=seed, budget=budget,
                           **opts)

# ---- Random and grid baselines ----

def _latin_hypercube(count, n, rng):
    """LHS sample of `count` points in the unit cube."""
    pts = [[0.0] * n for _ in range(count)]
    for j in range(n):
        perm = list(range(count))
        rng.shuffle(perm)
        for i in range(count):
            pts[i][j] = (perm[i] + rng.random()) / count
    return pts

def _rng_unpack(state):
    """JSON-safe RNG state (list or tuple shape) -> setstate() tuple."""
    if state is None:
        return None
    return (int(state[0]), tuple(int(v) for v in state[1]), state[2])

def _rng_pack(state):
    """random.Random.getstate() -> JSON-safe list shape."""
    return [state[0], list(state[1]), state[2]]

def _fix_snap_rng(snap):
    """Copy a snapshot whose top-level "rng" entry is list-shaped."""
    if snap is None:
        return None
    out = dict(snap)
    out["rng"] = _rng_unpack(out.get("rng"))
    return out

class _LhsFallback(object):
    """LHS first batch, then uniform random (shared by random/grid)."""
    _lhs_done = False
    def _fallback_batch(self, count):
        if not self._lhs_done:
            self._lhs_done = True
            return [self._to_real(pt)
                    for pt in _latin_hypercube(count, self.n, self.rng)]
        return [self._random_point() for _ in range(count)]
    def tell(self, X, F, G=None):
        for pt, val in zip(X, F):
            self.eval_count += 1
            self._record_best(pt, scalar_of(val))

class RandomSearch(_LhsFallback, AlgorithmBase):
    """Uniform random search with an LHS first batch (baseline fuse)."""
    name = "random"
    def ask(self, n=1):
        count = int(n)
        if count <= 0:
            return []
        return self._fallback_batch(count)
    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["lhs_done"] = self._lhs_done
        return state
    def restore(self, state):
        state = dict(state)
        state["rng"] = _rng_unpack(state.get("rng"))
        AlgorithmBase.restore(self, state)
        self._lhs_done = bool(state.get("lhs_done", False))

class GridSearch(_LhsFallback, AlgorithmBase):
    """Lexicographic grid walk, random wrap-around; n>5 degrades to RandomSearch."""
    name = "grid"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        self._fallback = self.n > 5
        self.levels = 2
        self._grid = []
        self._order = []
        self._pos = 0
        if self._fallback:
            return
        self.levels = self._pick_levels()
        axes = []
        for lo, hi in self.bounds:
            axes.append([lo + (hi - lo) * k / (self.levels - 1)
                         for k in range(self.levels)])
        self._grid = self._build_grid(axes)
        self._order = list(range(len(self._grid)))
        self._pos = 0
    def _pick_levels(self):
        if "levels" in self.opts:
            return max(2, int(self.opts["levels"]))
        b = self.budget if (self.budget and self.budget > 0) else 64
        levels = max(2, int(round(b ** (1.0 / self.n))))
        return min(8, max(3, levels))
    def _build_grid(self, axes):
        pts = [[v] for v in axes[0]]
        for axis in axes[1:]:
            nxt = []
            for base in pts:
                for v in axis:
                    nxt.append(base + [v])
            pts = nxt
        return pts
    def ask(self, n=1):
        count = int(n)
        if count <= 0:
            return []
        if self._fallback:
            return self._fallback_batch(count)
        out = []
        for _ in range(count):
            out.append(list(self._grid[self._next_index()]))
        return out
    def _next_index(self):
        if self._pos >= len(self._order):
            self._order = list(range(len(self._grid)))
            self.rng.shuffle(self._order)
            self._pos = 0
        idx = self._order[self._pos]
        self._pos += 1
        return idx
    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["fallback"] = self._fallback
        state["lhs_done"] = self._lhs_done
        state["levels"] = self.levels
        state["order"] = list(self._order)
        state["pos"] = self._pos
        return state
    def restore(self, state):
        state = dict(state)
        state["rng"] = _rng_unpack(state.get("rng"))
        AlgorithmBase.restore(self, state)
        self._fallback = bool(state.get("fallback", self.n > 5))
        self._lhs_done = bool(state.get("lhs_done", False))
        self.levels = int(state.get("levels", self.levels))
        self._order = list(state.get("order", self._order))
        self._pos = int(state.get("pos", 0))
register("random", RandomSearch)
register("grid", GridSearch)

# ---- Bounded Nelder-Mead simplex ----

class _UnitSpace(object):
    """Shared unit-cube best tracker for the simplex/DE engines."""
    def _record_best(self, pt, value):
        if self.best_y is None or value < self.best_y:
            self.best_y = value
            self._best_u = list(pt)
            self.best_x = self._to_real(pt)

class NelderMead(_UnitSpace, AlgorithmBase):
    """Bounded Nelder-Mead simplex (single point ask/tell)."""
    name = "simplex"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        self._best_u = self._to_unit(self.x0)
        self.simplex = [list(self._best_u)]
        for i in range(self.n):
            pt = list(self._best_u)
            pt[i] = min(1.0, pt[i] + 0.15)
            self.simplex.append(pt)
        self._center = []
        self._xr = []
        self._fr = float("inf")
        self._xe = []
        self._xc = []
        self._reset_cycle()
    def _clip_u(self, pt):
        return [min(1.0, max(0.0, v)) for v in pt]
    def ask(self, n=1):
        if int(n) < 1:
            return []
        if self._queue:
            return [self._to_real(self._queue.pop(0))]
        pending = self._pending_index()
        if pending is not None:
            return [self._to_real(self.simplex[pending])]
        self._order()
        self._make_reflection()
        return [self._to_real(self._queue.pop(0))]
    def tell(self, X, F, G=None):
        if len(X) != 1 or len(F) != 1:
            raise ValueError("NelderMead evaluates one point per ask")
        value = scalar_of(F[0])
        self.eval_count += 1
        pending = self._pending_index()
        if pending is not None:
            self.values[pending] = value
            self._record_best(self.simplex[pending], value)
            return
        self._accept_candidate(value)
    def restart(self, spread=0.15):
        'Restart the simplex around the best point.'
        self.restarts += 1
        radius = spread * (0.6 ** (self.restarts - 1))
        self.last_restart_radius = radius
        self.simplex = [list(self._best_u)]
        for i in range(self.n):
            pt = list(self._best_u)
            pt[i] = min(1.0, max(0.0,
                        pt[i] + self.rng.uniform(-radius, radius)))
            self.simplex.append(pt)
        n_imm = max(1, self.n // 2)
        for k in range(n_imm):
            slot = len(self.simplex) - 1 - k
            if slot <= 0:
                break
            self.simplex[slot] = [self.rng.random()
                                  for _ in range(self.n)]
        self._reset_cycle()
    def simplex_size(self):
        if len(self.simplex) < 2:
            return 0.0
        worst = self.simplex[-1]
        best = self.simplex[0]
        return sum((a - b) ** 2 for a, b in zip(worst, best)) ** 0.5
    def seed_history(self, points):
        'Warm-start: rebuild the simplex from the best past points.'
        seeds = self._sort_seeds(points)
        if not seeds:
            return
        for item in seeds:
            self.eval_count += 1
            y = float(scalar_of(item[1]))
            self._record_best(self._to_unit(self._clip(list(item[0]))), y)
        self._best_u = self._to_unit(self.best_x)
        for i, item in enumerate(seeds):
            if i >= len(self.simplex):
                break
            self.simplex[i] = self._to_unit(self._clip(list(item[0])))
            self.values[i] = float(scalar_of(item[1]))
        self._queue = []
        self._phase = "init"
        self._shrink_pts = []
        self._shrink_idx = 0
    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["best_u"] = list(self._best_u)
        state["simplex"] = [list(p) for p in self.simplex]
        state["values"] = list(self.values)
        state["queue"] = [list(p) for p in self._queue]
        state["phase"] = self._phase
        state["shrink_pts"] = [list(p) for p in self._shrink_pts]
        state["shrink_idx"] = self._shrink_idx
        state["center"] = list(self._center)
        state["xr"] = list(self._xr)
        state["fr"] = self._fr
        state["xe"] = list(self._xe)
        state["xc"] = list(self._xc)
        return state
    def restore(self, state):
        state = dict(state)
        state["rng"] = _rng_unpack(state.get("rng"))
        AlgorithmBase.restore(self, state)
        self._best_u = list(state.get("best_u", self._to_unit(self.best_x)))
        self.simplex = [list(p) for p in state["simplex"]]
        self.values = list(state["values"])
        self._queue = [list(p) for p in state.get("queue", [])]
        self._phase = state.get("phase", "init")
        self._shrink_pts = [list(p) for p in state.get("shrink_pts", [])]
        self._shrink_idx = int(state.get("shrink_idx", 0))
        self._center = list(state.get("center", []))
        self._xr = list(state.get("xr", []))
        self._fr = state.get("fr", float("inf"))
        self._xe = list(state.get("xe", []))
        self._xc = list(state.get("xc", []))
    def _reset_cycle(self):
        self.values = [None] * len(self.simplex)
        self._queue = []
        self._phase = "init"
        self._shrink_pts = []
        self._shrink_idx = 0
    def _pending_index(self):
        for i, value in enumerate(self.values):
            if value is None:
                return i
        return None
    def _order(self):
        pairs = sorted(zip(self.values, self.simplex),
                       key=lambda p: p[0])
        self.values = [p[0] for p in pairs]
        self.simplex = [list(p[1]) for p in pairs]
    def _centroid(self):
        c = [0.0] * self.n
        for pt in self.simplex[:-1]:
            for i in range(self.n):
                c[i] += pt[i] / self.n
        return c
    def _make_reflection(self):
        self._center = self._centroid()
        worst = self.simplex[-1]
        self._xr = self._clip_u([self._center[i] + (self._center[i] - worst[i])
                                 for i in range(self.n)])
        self._queue = [self._xr]
        self._phase = "reflect"
    def _accept_candidate(self, value):
        handler = {
            "reflect": self._on_reflect,
            "expand": self._on_expand,
            "contract": self._on_contract,
            "shrink": self._on_shrink,
        }
        step = handler.get(self._phase)
        if step:
            step(value)
    def _on_reflect(self, value):
        self._fr = value
        if value < self.values[0]:
            self._xe = self._clip_u([
                self._center[i] + 2.0 * (self._xr[i] - self._center[i])
                for i in range(self.n)])
            self._queue = [self._xe]
            self._phase = "expand"
            return
        if value < self.values[-2]:
            self._replace_worst(self._xr, value)
            self._phase = "init"
            return
        if value < self.values[-1]:
            self._xc = self._clip_u([
                self._center[i] + 0.5 * (self._xr[i] - self._center[i])
                for i in range(self.n)])
        else:
            self._xc = self._clip_u([
                self._center[i] + 0.5 * (self.simplex[-1][i] - self._center[i])
                for i in range(self.n)])
        self._queue = [self._xc]
        self._phase = "contract"
    def _on_expand(self, value):
        if value < self._fr:
            self._replace_worst(self._xe, value)
        else:
            self._replace_worst(self._xr, self._fr)
        self._phase = "init"
    def _on_contract(self, value):
        if value < min(self._fr, self.values[-1]):
            self._replace_worst(self._xc, value)
            self._phase = "init"
            return
        # Shrink all points toward the best point.
        pts = []
        for i in range(1, self.n + 1):
            pts.append(self._clip_u([
                self.simplex[0][j] + 0.5 * (self.simplex[i][j] - self.simplex[0][j])
                for j in range(self.n)]))
        self._shrink_pts = pts
        self._shrink_idx = 1
        self._queue = [pts[0]]
        self._phase = "shrink"
    def _on_shrink(self, value):
        idx = self._shrink_idx
        self.values[idx] = value
        self.simplex[idx] = self._shrink_pts[idx - 1]
        self._record_best(self.simplex[idx], value)
        self._shrink_idx += 1
        if self._shrink_idx <= self.n:
            self._queue = [self._shrink_pts[self._shrink_idx - 1]]
        else:
            self._phase = "init"
    def _replace_worst(self, pt, value):
        self.simplex[-1] = list(pt)
        self.values[-1] = value
        self._record_best(pt, value)
register("simplex", NelderMead)


class CoordinateSearch(_UnitSpace, AlgorithmBase):
    """Compass/coordinate pattern search around x0.

    One simulation per step, each axis tried +step then -step; a failed
    pair halves that axis step.  Meant for EM alignment, where the new
    target usually sits near x_opt and a global search would waste
    expensive simulations.
    """
    name = "coordinate"

    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        self._best_u = self._to_unit(self.x0)
        self.cur = list(self._best_u)
        init = float(opts.get("step", 0.1))
        self.step = [max(1e-6, min(0.5, init))] * self.n
        self.axis = 0
        self.sign = 1
        self.pending_pt = None
        self.pending_val = None
        self.started = False

    def _candidate(self):
        pt = list(self.cur)
        pt[self.axis] = min(1.0, max(0.0, pt[self.axis]
                                     + self.sign * self.step[self.axis]))
        return pt

    def _advance(self, improved):
        if improved:
            self.step[self.axis] = min(0.5, self.step[self.axis] * 1.2)
            self.axis = 0
            self.sign = 1
            return
        if self.sign > 0:
            self.sign = -1
            return
        self.step[self.axis] *= 0.5
        self.sign = 1
        self.axis = (self.axis + 1) % self.n

    def ask(self, n=1):
        if int(n) < 1:
            return []
        if self.pending_pt is not None:
            return [self._to_real(self.pending_pt)]
        if not self.started:
            self.pending_pt = list(self.cur)
            return [self._to_real(self.pending_pt)]
        pt = self._candidate()
        if pt == self.cur:
            # At a bound: treat this direction as failed and try next.
            self._advance(False)
            pt = self._candidate()
        self.pending_pt = pt
        return [self._to_real(pt)]

    def tell(self, X, F, G=None):
        if len(X) != 1 or len(F) != 1:
            raise ValueError("CoordinateSearch evaluates one point per ask")
        value = scalar_of(F[0])
        self.eval_count += 1
        cand = self.pending_pt if self.pending_pt is not None else self.cur
        improved = self.best_y is None or value < self.best_y
        if improved:
            self._record_best(cand, value)
            self.cur = list(cand)
        if not self.started:
            # First ask is x0 itself: record the baseline, then step.
            self.started = True
            self._record_best(self.cur, value)
        else:
            self._advance(improved)
        self.pending_pt = None

    def simplex_size(self):
        return 0.0

    def snapshot(self):
        return {
            "name": self.name,
            "eval_count": self.eval_count,
            "best_x": list(self.best_x),
            "best_y": self.best_y,
            "rng": self.rng.getstate(),
            "cur": list(self.cur),
            "step": list(self.step),
            "axis": self.axis,
            "sign": self.sign,
            "started": self.started,
        }

    def restore(self, state):
        self.eval_count = state.get("eval_count", 0)
        self.best_x = list(state.get("best_x", self.best_x))
        self.best_y = state.get("best_y", None)
        self.cur = list(state.get("cur", self.cur))
        self.step = list(state.get("step", self.step))
        self.axis = int(state.get("axis", 0))
        self.sign = int(state.get("sign", 1))
        self.started = bool(state.get("started", False))
        rng = state.get("rng")
        if rng is not None:
            self.rng.setstate(rng)


register("coordinate", CoordinateSearch)


# ---- Differential evolution (adaptive F/CR) ----

def _trial_tuple(state):
    if state is None:
        return None
    items = list(state)
    return (items[0], items[1], list(items[2]))

class DifferentialEvolution(_UnitSpace, AlgorithmBase):
    'Global search: LHS + mixed DE/rand/1 and current-to-pbest/1.'
    name = "global"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        self.F = 0.7
        self.CR = 0.9
        self.popsize = max(8, 4 * self.n)
        self._best_u = self._to_unit(self.x0)
        # LHS start, then pin the resume point into slot 0 so the
        # driver evaluates it first.
        self.population = _latin_hypercube(self.popsize, self.n, self.rng)
        self.population[0] = list(self._best_u)
        self.pop_values = [None] * self.popsize
        self._current = None
        self._queue = []
        self._next_trial = 0
    def ask(self, n=1):
        if int(n) < 1:
            return []
        if self._current is not None:
            return [self._to_real(self._current[2])]
        if not self._queue:
            self._fill_queue()
        self._current = self._queue.pop(0)
        return [self._to_real(self._current[2])]
    def tell(self, X, F, G=None):
        if len(X) != 1 or len(F) != 1:
            raise ValueError("%s evaluates one point per ask" % self.name)
        value = scalar_of(F[0])
        self.eval_count += 1
        if self._current is None:
            return
        kind, idx, pt = self._current
        self._current = None
        self._record_best(pt, value)
        if kind == "init":
            self.pop_values[idx] = value
            self.population[idx] = list(pt)
        elif value < self.pop_values[idx]:
            self.pop_values[idx] = value
            self.population[idx] = list(pt)

    def restart(self, spread=0.15):
        'Re-seed the population around the best point.'
        self.restarts += 1
        radius = spread * (0.6 ** (self.restarts - 1))
        self.last_restart_radius = radius
        if self.best_y is None:
            self.population = _latin_hypercube(self.popsize, self.n,
                                               self.rng)
            self.pop_values = [None] * self.popsize
        else:
            self.population = [list(self._best_u)]
            while len(self.population) < self.popsize:
                pt = [min(1.0, max(0.0, self._best_u[i]
                                   + self.rng.uniform(-radius, radius)))
                      for i in range(self.n)]
                self.population.append(pt)
            n_imm = max(1, self.popsize // 4)
            for k in range(n_imm):
                self.population[-(k + 1)] = [self.rng.random()
                                             for _ in range(self.n)]
            self.pop_values = [self.best_y] + [None] * (self.popsize - 1)
        self._current = None
        self._queue = []
        self._next_trial = 0
        self._on_restart()
    def _on_restart(self):
        return
    def simplex_size(self):
        if not self.population:
            return 0.0
        worst = max(self.population,
                    key=lambda p: sum((a - b) ** 2
                                      for a, b in zip(p, self._best_u)))
        return sum((a - b) ** 2
                   for a, b in zip(worst, self._best_u)) ** 0.5
    def seed_history(self, points):
        'Warm-start: fill population slots with past evaluations.'
        seeds = self._sort_seeds(points)
        if not seeds:
            return
        for item in seeds:
            self.eval_count += 1
            y = float(scalar_of(item[1]))
            self._record_best(self._to_unit(self._clip(list(item[0]))), y)
        for i, item in enumerate(seeds):
            if i >= self.popsize:
                break
            pt = self._to_unit(self._clip(list(item[0])))
            self.population[i] = list(pt)
            self.pop_values[i] = float(scalar_of(item[1]))
    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["best_u"] = list(self._best_u)
        state["F"] = self.F
        state["CR"] = self.CR
        state["popsize"] = self.popsize
        state["population"] = [list(p) for p in self.population]
        state["pop_values"] = list(self.pop_values)
        state["current"] = _trial_tuple(self._current)
        state["queue"] = [_trial_tuple(t) for t in self._queue]
        state["next_trial"] = self._next_trial
        return state
    def restore(self, state):
        state = dict(state)
        state["rng"] = _rng_unpack(state.get("rng"))
        AlgorithmBase.restore(self, state)
        self._best_u = list(state.get("best_u", self._to_unit(self.best_x)))
        self.F = float(state.get("F", self.F))
        self.CR = float(state.get("CR", self.CR))
        self.popsize = int(state.get("popsize", self.popsize))
        self.population = [list(p) for p in state["population"]]
        self.pop_values = list(state["pop_values"])
        self._current = _trial_tuple(state.get("current"))
        self._queue = [_trial_tuple(t) for t in state.get("queue", [])]
        self._next_trial = int(state.get("next_trial", 0))
    def _repair(self, pt):
        out = []
        for v in pt:
            if v < 0.0:
                v = -v
            elif v > 1.0:
                v = 2.0 - v
            out.append(min(1.0, max(0.0, v)))
        return out
    def _fill_queue(self):
        for i in range(self.popsize):
            if self.pop_values[i] is None:
                self._queue.append(("init", i, list(self.population[i])))
                return
        i = self._next_trial
        self._queue.append(("trial", i, self._make_trial(i)))
        self._next_trial += 1
        if self._next_trial >= self.popsize:
            self._next_trial = 0
    def _make_trial(self, i):
        n = self.n
        pool = [k for k in range(self.popsize) if k != i]
        self.rng.shuffle(pool)
        a = self.population[pool[0]]
        b = self.population[pool[1]]
        cur = self.population[i]
        if self.rng.random() < 0.5:
            # DE/rand/1: explore the whole population.
            c = self.population[pool[2]]
            mutant = self._repair([a[d] + self.F * (b[d] - c[d])
                                   for d in range(n)])
        else:
            # DE/current-to-pbest/1: exploit without collapsing
            # onto the single best member.
            top = max(1, self.popsize // 4)
            order = sorted(range(self.popsize),
                           key=lambda t: self.pop_values[t])
            pbest = self.population[order[self.rng.randrange(top)]]
            mutant = self._repair([
                cur[d] + self.F * (pbest[d] - cur[d])
                + self.F * (a[d] - b[d]) for d in range(n)])
        jrand = self.rng.randrange(n)
        trial = []
        for d in range(n):
            if d == jrand or self.rng.random() < self.CR:
                trial.append(mutant[d])
            else:
                trial.append(cur[d])
        return trial

class AdaptiveDe(DifferentialEvolution):
    'DE plus simplified SHADE: adaptive F/CR memory and parent archive.'
    name = "de"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        DifferentialEvolution.__init__(self, names, bounds, x0=x0,
                                       n_obj=n_obj, n_constr=n_constr,
                                       seed=seed, budget=budget, **opts)
        self.mem_size = int(opts.get("mem_size", 5))
        if self.mem_size < 1:
            self.mem_size = 1
        self._m_f = [0.5] * self.mem_size
        self._m_cr = [0.5] * self.mem_size
        self._mem_idx = 0
        self._succ_f = []
        self._succ_cr = []
        self._succ_w = []
        self._archive = []
        self._archive_max = self.popsize
        self._cur_params = None
    def _sample_f(self):
        mu = self._m_f[self._mem_idx]
        for _ in range(64):
            f = mu + 0.1 * math.tan(math.pi * (self.rng.random() - 0.5))
            if f > 1.0:
                f = 1.0
            if f > 0.0:
                return f
        return 0.5
    def _sample_cr(self):
        cr = self.rng.gauss(self._m_cr[self._mem_idx], 0.1)
        if cr < 0.0:
            cr = 0.0
        elif cr > 1.0:
            cr = 1.0
        return cr
    def _draw_r2(self, i, r1):
        while True:
            k = self.rng.randrange(self.popsize + len(self._archive))
            if k >= self.popsize:
                return self._archive[k - self.popsize]
            if k != i and k != r1:
                return self.population[k]
    def _make_trial(self, i):
        n = self.n
        f = self._sample_f()
        cr = self._sample_cr()
        self._cur_params = (f, cr)
        pool = [k for k in range(self.popsize) if k != i]
        self.rng.shuffle(pool)
        a = self.population[pool[0]]
        cur = self.population[i]
        if self.rng.random() < 0.5:
            # DE/rand/1: explore the whole population.
            b = self.population[pool[1]]
            c = self.population[pool[2]]
            mutant = self._repair([a[d] + f * (b[d] - c[d])
                                   for d in range(n)])
        else:
            # DE/current-to-pbest/1/archive/1: exploit with archived
            # parents as extra difference sources.
            top = max(1, self.popsize // 4)
            order = sorted(range(self.popsize),
                           key=lambda t: self.pop_values[t])
            pbest = self.population[order[self.rng.randrange(top)]]
            if self._archive:
                b = self._draw_r2(i, pool[0])
            else:
                b = self.population[pool[1]]
            mutant = self._repair([
                cur[d] + f * (pbest[d] - cur[d])
                + f * (a[d] - b[d]) for d in range(n)])
        jrand = self.rng.randrange(n)
        trial = []
        for d in range(n):
            if d == jrand or self.rng.random() < cr:
                trial.append(mutant[d])
            else:
                trial.append(cur[d])
        return trial
    def _update_memory(self):
        if not self._succ_f:
            return
        wsum = sum(self._succ_w)
        if wsum <= 0.0:
            w = [1.0 / len(self._succ_f)] * len(self._succ_f)
        else:
            w = [x / wsum for x in self._succ_w]
        denom = sum(wi * fi for wi, fi in zip(w, self._succ_f))
        if denom > 0.0:
            self._m_f[self._mem_idx] = (
                sum(wi * fi * fi for wi, fi in zip(w, self._succ_f)) / denom)
        self._m_cr[self._mem_idx] = (
            sum(wi * cr for wi, cr in zip(w, self._succ_cr)))
        self._mem_idx = (self._mem_idx + 1) % self.mem_size
        self._succ_f = []
        self._succ_cr = []
        self._succ_w = []

    def tell(self, X, F, G=None):
        if len(X) != 1 or len(F) != 1:
            raise ValueError("%s evaluates one point per ask" % self.name)
        value = scalar_of(F[0])
        self.eval_count += 1
        if self._current is None:
            return
        kind, idx, pt = self._current
        self._current = None
        params, self._cur_params = self._cur_params, None
        self._record_best(pt, value)
        if kind == "init":
            self.pop_values[idx] = value
            self.population[idx] = list(pt)
            return
        old = self.pop_values[idx]
        if value < old:
            # Archive the replaced parent (SHADE) and remember F/CR.
            self._archive.append(list(self.population[idx]))
            while len(self._archive) > self._archive_max:
                self._archive.pop(self.rng.randrange(len(self._archive)))
            self.pop_values[idx] = value
            self.population[idx] = list(pt)
            if params is not None:
                self._succ_f.append(params[0])
                self._succ_cr.append(params[1])
                self._succ_w.append(old - value)
        if idx == self.popsize - 1:
            self._update_memory()
    def _on_restart(self):
        self._succ_f = []
        self._succ_cr = []
        self._succ_w = []
        self._archive = []
        self._cur_params = None
    def snapshot(self):
        state = DifferentialEvolution.snapshot(self)
        state["mem_size"] = self.mem_size
        state["m_f"] = list(self._m_f)
        state["m_cr"] = list(self._m_cr)
        state["mem_idx"] = self._mem_idx
        state["succ_f"] = list(self._succ_f)
        state["succ_cr"] = list(self._succ_cr)
        state["succ_w"] = list(self._succ_w)
        state["archive"] = [list(p) for p in self._archive]
        state["cur_params"] = (None if self._cur_params is None
                               else list(self._cur_params))
        return state
    def restore(self, state):
        DifferentialEvolution.restore(self, state)
        self.mem_size = int(state.get("mem_size", self.mem_size))
        self._m_f = list(state.get("m_f", self._m_f))
        self._m_cr = list(state.get("m_cr", self._m_cr))
        self._mem_idx = int(state.get("mem_idx", 0))
        self._succ_f = list(state.get("succ_f", []))
        self._succ_cr = list(state.get("succ_cr", []))
        self._succ_w = list(state.get("succ_w", []))
        self._archive = [list(p) for p in state.get("archive", [])]
        params = state.get("cur_params")
        self._cur_params = (None if params is None
                            else (float(params[0]), float(params[1])))
register("global", DifferentialEvolution)
register("de", AdaptiveDe)

# ---- L-SHADE success-history DE ----

class Lshade(AlgorithmBase):
    """Success-history DE with linear population size reduction."""
    name = "lshade"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        if self.n_obj > 1:
            raise ValueError(
                "lshade is single-objective; use nsga2 or a scalarize wrapper")
        pop_opt = self.opts.get("popsize")
        if pop_opt is None:
            n_init = max(50, min(200, 18 * self.n))
        else:
            n_init = int(pop_opt)
        self.N_init = max(4, n_init)
        self.N_min = 4
        self.memory_size = max(1, int(self.opts.get("memory_size", 10)))
        self.p_best = float(self.opts.get("p_best", 0.25))
        self.mem_f = [0.5] * self.memory_size
        self.mem_cr = [0.5] * self.memory_size
        self.mem_k = 0
        self.archive = []
        self.generation = 0
        self._next_parent = 0
        self.pending = []
        self._succ_buf = []
        self._trials_since_mem = 0
        self.pop = []
        self.pop_f = []
        self.pop_g = []
        for i in range(self.N_init):
            pt = list(self.x0) if i == 0 else self._random_point()
            self.pop.append(self._clip(pt))
            self.pop_f.append(None)
            self.pop_g.append(None)
    def _cauchy(self, loc, scale):
        u = self.rng.random()
        return loc + scale * math.tan(math.pi * (u - 0.5))
    def _sample_f(self, loc):
        f = self._cauchy(loc, 0.1)
        while f <= 0.0:
            f = self._cauchy(loc, 0.1)
        if f > 1.0:
            f = 1.0
        return f
    def _sample_cr(self, loc):
        cr = self.rng.gauss(loc, 0.1)
        if cr < 0.0:
            return 0.0
        if cr > 1.0:
            return 1.0
        return cr
    def _feasible(self, g):
        return constraint_violation(g) <= 0.0
    def _note_best(self, x, f, g):
        if f is None:
            return
        if not self._feasible(g):
            return
        self._record_best(x, scalar_of(f))
    def _rank(self):
        idxs = [i for i in range(len(self.pop)) if self.pop_f[i] is not None]
        def key(i):
            return (constraint_violation(self.pop_g[i]),
                    scalar_of(self.pop_f[i]))
        idxs.sort(key=key)
        return idxs
    def seed_history(self, points):
        """Warm-start: paste past evaluations into empty pop slots."""
        seeds = self._sort_seeds(points)
        if not seeds:
            return
        slot = 0
        for item in seeds:
            x = self._clip(list(item[0]))
            y = float(scalar_of(item[1]))
            g = item[2] if len(item) > 2 else None
            self.eval_count += 1
            if g is None or constraint_violation(g) <= 0.0:
                self._record_best(x, y)
            while slot < len(self.pop) and self.pop_f[slot] is not None:
                slot += 1
            if slot >= len(self.pop):
                break
            self.pop[slot] = x
            self.pop_f[slot] = y
            self.pop_g[slot] = (list(g) if g is not None else None)
            slot += 1
    def _has_unevaluated(self):
        for v in self.pop_f:
            if v is None:
                return True
        return False
    def _is_pending(self, idx):
        for rec in self.pending:
            if rec["kind"] == "init" and rec["idx"] == idx:
                return True
        return False
    def _rand_index(self, n_total, exclude=()):
        if n_total <= 0:
            return 0
        for _ in range(64):
            j = self.rng.randrange(n_total)
            if j not in exclude:
                return j
        for j in range(n_total):
            if j not in exclude:
                return j
        return 0
    def _repair(self, trial, parent):
        out = []
        for j in range(self.n):
            lo, hi = self.bounds[j]
            v = trial[j]
            if v < lo:
                v = 0.5 * (lo + parent[j])
            elif v > hi:
                v = 0.5 * (hi + parent[j])
            out.append(float(v))
        return out
    def _make_trial(self, idx):
        n = self.n
        pop = self.pop
        size = len(pop)
        f = self._sample_f(self.mem_f[self.mem_k])
        cr = self._sample_cr(self.mem_cr[self.mem_k])
        order = self._rank()
        n_top = max(1, int(math.ceil(self.p_best * size)))
        if n_top > len(order):
            n_top = len(order)
        p_idx = order[self.rng.randrange(n_top)] if order else idx
        r1 = self._rand_index(size, exclude=(idx,))
        r2 = self._rand_index(size + len(self.archive), exclude=(idx, r1))
        x = pop[idx]
        xp = pop[p_idx]
        x1 = pop[r1]
        x2 = pop[r2] if r2 < size else self.archive[r2 - size]
        v = []
        for j in range(n):
            v.append(x[j] + f * (xp[j] - x[j]) + f * (x1[j] - x2[j]))
        jrand = self.rng.randrange(n)
        u = []
        for j in range(n):
            if j == jrand or self.rng.random() < cr:
                u.append(v[j])
            else:
                u.append(x[j])
        return self._repair(u, x), f, cr
    def _update_memory(self):
        successes = self._succ_buf
        self._succ_buf = []
        self._trials_since_mem = 0
        if not successes:
            return
        w_sum = 0.0
        num_f = 0.0
        den_f = 0.0
        num_cr = 0.0
        for f, cr, w in successes:
            w_sum += w
            num_f += w * f * f
            den_f += w * f
            num_cr += w * cr
        if w_sum <= 0.0:
            return
        k = self.mem_k
        if den_f > 0.0:
            self.mem_f[k] = num_f / den_f
        self.mem_cr[k] = num_cr / w_sum
        self.mem_k = (k + 1) % self.memory_size
    def _pop_target(self):
        if self.budget is not None and self.budget > 0:
            prog = float(self.eval_count) / float(self.budget)
        else:
            horizon = self.opts.get("max_gen")
            if not horizon:
                return self.N_init
            prog = float(self.generation) / float(horizon)
        if prog < 0.0:
            prog = 0.0
        if prog > 1.0:
            prog = 1.0
        target = int(round(
            self.N_init + (self.N_min - self.N_init) * prog))
        return max(self.N_min, min(self.N_init, target))
    def _drop(self, idx):
        del self.pop[idx]
        del self.pop_f[idx]
        del self.pop_g[idx]
    def _lpsr(self):
        if self._has_unevaluated():
            return
        target = self._pop_target()
        while len(self.pop) > target:
            order = self._rank()
            if not order:
                self._drop(len(self.pop) - 1)
                continue
            self._drop(order[-1])
    def _trim_archive(self):
        limit = 1.4 * len(self.pop)
        while len(self.archive) > limit:
            del self.archive[self.rng.randrange(len(self.archive))]

    def ask(self, n=1):
        if n < 1:
            n = 1
        out = []
        for i in range(len(self.pop)):
            if len(out) >= n:
                break
            if self.pop_f[i] is not None:
                continue
            if self._is_pending(i):
                continue
            self.pending.append({"kind": "init", "idx": i,
                                 "x": list(self.pop[i])})
            out.append(list(self.pop[i]))
        if len(out) < n and not self._has_unevaluated() and self.pop:
            k = min(n - len(out), len(self.pop))
            for _ in range(k):
                idx = self._next_parent % len(self.pop)
                self._next_parent = (idx + 1) % len(self.pop)
                x, f, cr = self._make_trial(idx)
                self.pending.append({"kind": "trial", "idx": idx,
                                     "F": f, "CR": cr, "x": list(x)})
                out.append(list(x))
        while len(out) < n and not self.pop:
            # fallback: nothing searchable left, feed random points
            pt = self._random_point()
            self.pending.append({"kind": "random", "idx": -1, "x": list(pt)})
            out.append(list(pt))
        return out

    def tell(self, X, F, G=None):
        if F is None:
            return
        m = len(F)
        if G is None:
            G = [None] * m
        for i in range(m):
            if not self.pending:
                break
            rec = self.pending.pop(0)
            f_i = F[i]
            g_i = G[i] if i < len(G) else None
            if X is not None and i < len(X) and X[i] is not None:
                x_i = self._clip(list(X[i]))
            else:
                x_i = list(rec["x"])
            self.eval_count += 1
            if rec["kind"] == "init":
                idx = rec["idx"]
                if 0 <= idx < len(self.pop):
                    self.pop[idx] = x_i
                    self.pop_f[idx] = f_i
                    self.pop_g[idx] = g_i
                    self._note_best(x_i, f_i, g_i)
            elif rec["kind"] == "trial":
                self._trials_since_mem += 1
                idx = rec["idx"]
                if (f_i is not None and 0 <= idx < len(self.pop)
                        and self.pop_f[idx] is not None):
                    if deb_better(f_i, g_i, self.pop_f[idx], self.pop_g[idx]):
                        w = abs(scalar_of(f_i) - scalar_of(self.pop_f[idx]))
                        w += abs(constraint_violation(g_i) -
                                 constraint_violation(self.pop_g[idx]))
                        if w <= 0.0:
                            w = 1e-12
                        self._succ_buf.append((rec["F"], rec["CR"], w))
                        self.archive.append(list(self.pop[idx]))
                        self.pop[idx] = x_i
                        self.pop_f[idx] = f_i
                        self.pop_g[idx] = g_i
                    self._note_best(x_i, f_i, g_i)
            else:
                self._note_best(x_i, f_i, g_i)
        if self._trials_since_mem >= max(1, len(self.pop)):
            self._update_memory()
        self.generation += 1
        if not self.pending:
            self._lpsr()
            self._trim_archive()

    def restart(self, spread=0.15):
        AlgorithmBase.restart(self, spread)
        self.pending = []
        self._next_parent = 0
        size = len(self.pop)
        if size == 0:
            return
        order = self._rank()
        keep = set(order[:max(1, len(order) // 4)])
        new_pop = []
        new_f = []
        new_g = []
        for i in sorted(keep):
            new_pop.append(list(self.pop[i]))
            new_f.append(self.pop_f[i])
            new_g.append(self.pop_g[i])
        n_rest = size - len(new_pop)
        n_imm = int(round(0.25 * n_rest))
        n_near = n_rest - n_imm
        base_pt = list(self.best_x)
        for _ in range(n_near):
            pt = []
            for j in range(self.n):
                lo, hi = self.bounds[j]
                r = spread * (hi - lo)
                v = base_pt[j] + self.rng.uniform(-r, r)
                if v < lo:
                    v = lo
                elif v > hi:
                    v = hi
                pt.append(float(v))
            new_pop.append(pt)
            new_f.append(None)
            new_g.append(None)
        for _ in range(n_imm):
            new_pop.append(self._random_point())
            new_f.append(None)
            new_g.append(None)
        self.pop = new_pop
        self.pop_f = new_f
        self.pop_g = new_g
        self._trim_archive()
    def simplex_size(self):
        if not self.pop:
            return 0.0
        total = 0.0
        for p in self.pop:
            s = 0.0
            for j in range(self.n):
                d = p[j] - self.best_x[j]
                s += d * d
            total += s
        return math.sqrt(total / len(self.pop))

    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["population"] = [list(p) for p in self.pop]
        state["pop_values"] = list(self.pop_f)
        state["pop_g"] = [list(g) if g is not None else None
                          for g in self.pop_g]
        state["memory"] = {"f": list(self.mem_f), "cr": list(self.mem_cr),
                           "k": self.mem_k}
        state["archive"] = [list(p) for p in self.archive]
        state["generation"] = self.generation
        state["N_init"] = self.N_init
        state["N_min"] = self.N_min
        state["next_parent"] = self._next_parent
        state["pending"] = [
            {"kind": r["kind"], "idx": r["idx"], "x": list(r["x"]),
             "F": r.get("F"), "CR": r.get("CR")}
            for r in self.pending]
        state["succ_buf"] = [[f, cr, w] for f, cr, w in self._succ_buf]
        state["trials_since_mem"] = self._trials_since_mem
        return state

    def restore(self, state):
        st = dict(state)
        rng = st.get("rng")
        if rng is not None:
            st["rng"] = _rng_unpack(rng)
        AlgorithmBase.restore(self, st)
        if "population" in st:
            self.pop = [list(p) for p in st["population"]]
        if "pop_values" in st:
            self.pop_f = list(st["pop_values"])
        if "pop_g" in st:
            self.pop_g = [list(g) if g is not None else None
                          for g in st["pop_g"]]
        mem = st.get("memory")
        if mem is not None:
            self.mem_f = list(mem["f"])
            self.mem_cr = list(mem["cr"])
            self.mem_k = int(mem["k"])
        self.archive = [list(p) for p in st.get("archive", self.archive)]
        self.generation = int(st.get("generation", self.generation))
        self.N_init = int(st.get("N_init", self.N_init))
        self.N_min = int(st.get("N_min", self.N_min))
        self._next_parent = int(st.get("next_parent", self._next_parent))
        self.pending = [
            {"kind": r["kind"], "idx": r["idx"], "x": list(r["x"]),
             "F": r.get("F"), "CR": r.get("CR")}
            for r in st.get("pending", [])]
        self._succ_buf = [[f, cr, w] for f, cr, w in st.get("succ_buf", [])]
        self._trials_since_mem = int(st.get("trials_since_mem", 0))
register("lshade", Lshade)

# ---- CMA-ES with IPOP-lite restarts ----

def jacobi_eig(a):
    'Cyclic Jacobi eigen-decomposition of a symmetric matrix.'
    n = len(a)
    m = [list(row) for row in a]
    b = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _sweep in range(60):
        off = 0.0
        for p in range(n - 1):
            for q in range(p + 1, n):
                off += m[p][q] * m[p][q]
        scale = 1.0 + sum(abs(m[i][i]) for i in range(n))
        if off <= 1e-24 * scale * scale:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                apq = m[p][q]
                if apq == 0.0:
                    continue
                theta = (m[q][q] - m[p][p]) / (2.0 * apq)
                t = 1.0 / (abs(theta) + math.sqrt(theta * theta + 1.0))
                if theta < 0.0:
                    t = -t
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    if k == p or k == q:
                        continue
                    mkp = m[k][p]
                    mkq = m[k][q]
                    m[k][p] = m[p][k] = c * mkp - s * mkq
                    m[k][q] = m[q][k] = s * mkp + c * mkq
                m[p][p] -= t * apq
                m[q][q] += t * apq
                m[p][q] = m[q][p] = 0.0
                for k in range(n):
                    bkp = b[k][p]
                    bkq = b[k][q]
                    b[k][p] = c * bkp - s * bkq
                    b[k][q] = s * bkp + c * bkq
    pairs = [(m[i][i], i) for i in range(n)]
    pairs.sort(key=lambda pr: -pr[0])
    evals = [pr[0] for pr in pairs]
    order = [pr[1] for pr in pairs]
    bo = [[b[j][order[i]] for i in range(n)] for j in range(n)]
    return evals, bo

class CmaEs(AlgorithmBase):
    """Standard CMA-ES driven by generational ask/tell batches."""
    name = "cmaes"
    ipop = False
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        span = [hi - lo for lo, hi in self.bounds]
        mean_span = sum(span) / float(self.n) if self.n else 1.0
        self._domain_scale = mean_span if mean_span > 0.0 else 1.0
        self._sigma_floor = 1e-12 * self._domain_scale
        self.sep = bool(opts.get("sep", False)) or self.n > 50
        lam = opts.get("popsize", opts.get("lam", 0)) or 0
        if lam <= 0:
            lam = int(max(4.0 + 3.0 * math.log(max(self.n, 2)), 20.0))
            if budget is not None and budget < 200:
                lam = 12  # small-budget mode (lambda in 10..15)
        self._set_lambda(int(lam))
        sigma0 = opts.get("sigma0", 0.15 * self._domain_scale)
        self.m = self._clip(list(self.x0))
        self.sigma = max(float(sigma0), self._sigma_floor)
        self.C = [[1.0 if i == j else 0.0 for j in range(self.n)]
                  for i in range(self.n)]
        self.B = [[1.0 if i == j else 0.0 for j in range(self.n)]
                  for i in range(self.n)]
        self.D = [1.0] * self.n
        self.Cdiag = [1.0] * self.n
        self.p_sigma = [0.0] * self.n
        self.p_c = [0.0] * self.n
        self._gen_count = 0
        self._hist = []
        self._ps_stall = 0
        self.stagnated = False
        self._pending = []
        self._done = []
        self._gen_xs = []

    def seed_history(self, points):
        'Warm-start: centre the search on the historical best.'
        seeds = self._sort_seeds(points)
        if not seeds:
            return
        for item in seeds:
            self.eval_count += 1
            y = float(scalar_of(item[1]))
            self._record_best(self._clip(list(item[0])), y)
        self.m = list(self.best_x)
        acc = 0.0
        used = 0
        for item in seeds:
            x = self._clip(list(item[0]))
            acc += sum((a - b) ** 2 for a, b in zip(x, self.m))
            used += 1
        if used:
            spread = math.sqrt(acc / used) / math.sqrt(max(self.n, 1))
            lo = 0.02 * self._domain_scale
            hi = 0.30 * self._domain_scale
            self.sigma = max(self._sigma_floor,
                             min(hi, max(lo, spread)))
    def _set_lambda(self, lam):
        n = self.n
        self.lam = max(2, int(lam))
        self.mu = max(1, self.lam // 2)
        raw = [math.log(self.mu + 0.5) - math.log(i + 1.0)
               for i in range(self.mu)]
        sraw = sum(raw)
        self.weights = [w / sraw for w in raw]
        self.mueff = sraw * sraw / sum(w * w for w in raw)
        self.cc = (4.0 + self.mueff / n) / (n + 4.0 + 2.0 * self.mueff / n)
        self.c1 = 2.0 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(1.0 - self.c1,
                       2.0 * (self.mueff - 2.0 + 1.0 / self.mueff)
                       / ((n + 2.0) ** 2 + self.mueff))
        self.csigma = (self.mueff + 2.0) / (n + self.mueff + 5.0)
        self.damps = (1.0 + 2.0 * max(0.0,
                      math.sqrt((self.mueff - 1.0) / (n + 1.0)) - 1.0)
                      + self.csigma)
        self.chiN = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n)
                                    + 1.0 / (21.0 * n * n))
        self.hist_window = max(5, int(math.ceil(30.0 * n / self.lam)))
    def _repair(self, pt):
        out = []
        for i, v in enumerate(pt):
            lo, hi = self.bounds[i]
            if v < lo:
                v = lo + (lo - v)
            elif v > hi:
                v = hi - (v - hi)
            if v < lo:
                v = lo
            elif v > hi:
                v = hi
            out.append(float(v))
        return out
    def _sample_generation(self):
        n = self.n
        self._pending = []
        self._done = []
        self._gen_xs = []
        for _k in range(self.lam):
            z = [self.rng.gauss(0.0, 1.0) for _ in range(n)]
            if self.sep:
                x = [self.m[j] + self.sigma * math.sqrt(self.Cdiag[j]) * z[j]
                     for j in range(n)]
            else:
                x = [0.0] * n
                for j in range(n):
                    acc = 0.0
                    row = self.B[j]
                    for i in range(n):
                        acc += row[i] * self.D[i] * z[i]
                    x[j] = self.m[j] + self.sigma * acc
            x = self._repair(x)
            # Use the repaired point so the update sees the true step y.
            y = [(x[j] - self.m[j]) / self.sigma for j in range(n)]
            self._pending.append([x, y])
            self._gen_xs.append(list(x))
    def ask(self, n=1):
        if not self._pending:
            self._sample_generation()
        k = min(int(n), len(self._pending))
        return [list(item[0]) for item in self._pending[:k]]
    def _invsqrt_C_times(self, v):
        n = self.n
        t = [0.0] * n
        for i in range(n):
            acc = 0.0
            for j in range(n):
                acc += self.B[j][i] * v[j]
            t[i] = acc / self.D[i]
        out = [0.0] * n
        for j in range(n):
            acc = 0.0
            row = self.B[j]
            for i in range(n):
                acc += row[i] * t[i]
            out[j] = acc
        return out
    def _decompose(self):
        evals, b = jacobi_eig(self.C)
        self.B = b
        maxev = max(abs(ev) for ev in evals) if evals else 1.0
        floor = max(1e-20, 1e-20 * maxev)
        self.D = [math.sqrt(max(ev, floor)) for ev in evals]
    def tell(self, X, F, G=None):
        if len(X) == 0:
            return
        if len(X) > len(self._pending):
            raise ValueError("tell received more points than were asked")
        for i in range(len(X)):
            item = self._pending.pop(0)
            f = F[i]
            g = None if G is None else G[i]
            self.eval_count += 1
            if (g is None or constraint_violation(g) <= 0.0):
                self._record_best(item[0], scalar_of(f))
            self._done.append([item[0], item[1], f, g])
        if not self._pending and self._done:
            self._update_generation()
    def _update_generation(self):
        done = self._done
        self._done = []
        n = self.n
        def _cmp(i, j):
            if deb_better(done[i][2], done[i][3], done[j][2], done[j][3]):
                return -1
            if deb_better(done[j][2], done[j][3], done[i][2], done[i][3]):
                return 1
            return 0
        from functools import cmp_to_key
        order = sorted(range(len(done)), key=cmp_to_key(_cmp))
        ranked = [done[i] for i in order]
        m_old = list(self.m)
        sigma = self.sigma
        # Recombination of the mu best points.
        new_m = [0.0] * n
        for i in range(self.mu):
            w = self.weights[i]
            xi = ranked[i][0]
            for j in range(n):
                new_m[j] += w * xi[j]
        dm = [(new_m[j] - m_old[j]) / sigma for j in range(n)]
        self.m = self._clip(new_m)
        # Evolution path of sigma.
        cs = self.csigma
        if self.sep:
            cinv = [dm[j] / math.sqrt(max(self.Cdiag[j], 1e-20))
                    for j in range(n)]
        else:
            cinv = self._invsqrt_C_times(dm)
        ps = self.p_sigma
        pscale = math.sqrt(cs * (2.0 - cs) * self.mueff)
        self.p_sigma = [(1.0 - cs) * ps[j] + pscale * cinv[j]
                        for j in range(n)]
        ps_norm = math.sqrt(sum(v * v for v in self.p_sigma))
        # h_sigma indicator (pc update only along a successful path).
        self._gen_count += 1
        denom = math.sqrt(1.0 - (1.0 - cs)
                          ** (2.0 * self._gen_count / self.lam))
        hsig = 1.0
        if denom <= 0.0 or (ps_norm / denom / self.chiN
                            >= (1.4 + 2.0 / (n + 1.0))):
            hsig = 0.0
        cc = self.cc
        pc = self.p_c
        cscale = math.sqrt(cc * (2.0 - cc) * self.mueff)
        self.p_c = [(1.0 - cc) * pc[j] + hsig * cscale * dm[j]
                    for j in range(n)]
        delta_h = (1.0 - hsig) * cc * (2.0 - cc)
        # Rank-mu covariance update on the scaled steps y.
        ys = [ranked[i][1] for i in range(self.mu)]
        if self.sep:
            for j in range(n):
                acc = 0.0
                for i in range(self.mu):
                    acc += self.weights[i] * ys[i][j] * ys[i][j]
                cd = self.Cdiag[j]
                self.Cdiag[j] = ((1.0 - self.c1 - self.cmu
                                  + self.c1 * delta_h) * cd
                                 + self.c1 * self.p_c[j] * self.p_c[j]
                                 + self.cmu * acc)
                self.Cdiag[j] = max(self.Cdiag[j], 1e-20)
        else:
            decay = 1.0 - self.c1 - self.cmu + self.c1 * delta_h
            newC = [[0.0] * n for _ in range(n)]
            for j in range(n):
                for k in range(j, n):
                    acc = 0.0
                    for i in range(self.mu):
                        acc += self.weights[i] * ys[i][j] * ys[i][k]
                    val = (decay * self.C[j][k]
                           + self.c1 * self.p_c[j] * self.p_c[k]
                           + self.cmu * acc)
                    newC[j][k] = val
                    newC[k][j] = val
            self.C = newC
        # Step-size update with a floor against underflow.
        self.sigma = self.sigma * math.exp(
            (cs / self.damps) * (ps_norm / self.chiN - 1.0))
        if self.sigma < self._sigma_floor:
            self.sigma = self._sigma_floor
        if not self.sep:
            # One decomposition per generation keeps sampling aligned with
            # the updated C; Jacobi on n<=50 is cheap enough in pure python.
            self._decompose()
        self._track_stagnation(ranked[0], ps_norm)
    def _track_stagnation(self, top, ps_norm):
        """Update tolfunhist / ps-stagnation bookkeeping (IPOP trigger)."""
        self._hist.append(self.best_y if self.best_y is not None
                          else scalar_of(top[2]))
        if len(self._hist) > self.hist_window:
            self._hist.pop(0)
        self.stagnated = False
        if len(self._hist) >= self.hist_window:
            improve = self._hist[-self.hist_window] - self._hist[-1]
            scale = max(1.0, abs(self._hist[-self.hist_window]))
            if improve <= 0.01 * scale:
                self.stagnated = True
        if ps_norm < 1e-8:
            self._ps_stall += 1
            if self._ps_stall >= 5:
                self.stagnated = True
        else:
            self._ps_stall = 0
        if self.opts.get("stop_signal"):
            self.stagnated = True
        if self.stagnated and self.ipop:
            self._maybe_ipop_restart()
    def _maybe_ipop_restart(self):
        max_restarts = int(self.opts.get("max_restarts", 3))
        if self.restarts >= max_restarts:
            return
        new_lam = min(2 * self.lam, 20 + 2 * self.n)
        if self.budget is not None:
            if self.eval_count + 2 * new_lam > self.budget:
                return  # too little budget left to pay the restart back
        self.restart(spread=0.15)

    def restart(self, spread=0.15):
        'Restart around best_x with decayed spread (unit-cube fraction).'
        self.restarts += 1
        radius = spread * (0.6 ** (self.restarts - 1))
        self.last_restart_radius = radius
        self.sigma = max(radius * self._domain_scale, self._sigma_floor)
        if self.best_y is not None:
            self.m = self._clip(list(self.best_x))
        else:
            self.m = self._clip(list(self.x0))
        n = self.n
        self.C = [[1.0 if i == j else 0.0 for j in range(n)]
                  for i in range(n)]
        self.B = [[1.0 if i == j else 0.0 for j in range(n)]
                  for i in range(n)]
        self.D = [1.0] * n
        self.Cdiag = [1.0] * n
        self.p_sigma = [0.0] * n
        self.p_c = [0.0] * n
        self._hist = []
        self._ps_stall = 0
        self.stagnated = False
        self._pending = []
        self._done = []
        self._set_lambda(min(2 * self.lam, 20 + 2 * self.n))
    def simplex_size(self):
        pts = self._gen_xs
        if not pts:
            return 0.0
        k = float(len(pts))
        cen = [sum(p[j] for p in pts) / k for j in range(self.n)]
        acc = 0.0
        for p in pts:
            acc += sum((p[j] - cen[j]) ** 2 for j in range(self.n))
        return math.sqrt(acc / k)
    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state.update({
            "m": list(self.m),
            "sigma": self.sigma,
            "C": [list(row) for row in self.C],
            "B": [list(row) for row in self.B],
            "D": list(self.D),
            "Cdiag": list(self.Cdiag),
            "sep": self.sep,
            "p_sigma": list(self.p_sigma),
            "p_c": list(self.p_c),
            "lam": self.lam,
            "mu": self.mu,
            "weights": list(self.weights),
            "gen_count": self._gen_count,
            "hist": list(self._hist),
            "ps_stall": self._ps_stall,
            "stagnated": self.stagnated,
            "pending": [[list(x), list(y)] for x, y in self._pending],
            "done": [[list(x), list(y), f, g] for x, y, f, g in self._done],
            "gen_xs": [list(p) for p in self._gen_xs],
        })
        return state

    def restore(self, state):
        st = dict(state)
        if st.get("rng") is not None:
            st["rng"] = _rng_unpack(st["rng"])
        AlgorithmBase.restore(self, st)
        self.m = [float(v) for v in st["m"]]
        self.sigma = float(st["sigma"])
        self.C = [[float(v) for v in row] for row in st["C"]]
        self.B = [[float(v) for v in row] for row in st["B"]]
        self.D = [float(v) for v in st["D"]]
        self.Cdiag = [float(v) for v in st["Cdiag"]]
        self.sep = bool(st["sep"])
        self.p_sigma = [float(v) for v in st["p_sigma"]]
        self.p_c = [float(v) for v in st["p_c"]]
        self._set_lambda(int(st["lam"]))
        self.weights = [float(w) for w in st["weights"]]
        self._gen_count = int(st["gen_count"])
        self._hist = [float(v) for v in st["hist"]]
        self._ps_stall = int(st["ps_stall"])
        self.stagnated = bool(st["stagnated"])
        self._pending = [[[float(v) for v in x], [float(v) for v in y]]
                         for x, y in st["pending"]]
        self._done = [[[float(v) for v in x], [float(v) for v in y], f, g]
                      for x, y, f, g in st["done"]]
        self._gen_xs = [[float(v) for v in p] for p in st["gen_xs"]]

class CmaEsIpop(CmaEs):
    """CMA-ES with IPOP-lite restarts on stagnation."""
    name = "cmaes_ipop"
    ipop = True
register("cmaes", CmaEs)
register("cmaes_ipop", CmaEsIpop)
# ---- Tree-structured Parzen estimator ----
# Candidate-pool and split constants (Bergstra et al. style TPE).
_GAMMA = 0.25
_MAX_CANDIDATES = 64
_DIVERSITY_RADIUS = 0.05

def _logsumexp(values):
    top = max(values)
    if top == float("-inf"):
        return top
    total = 0.0
    for v in values:
        total += math.exp(v - top)
    return top + math.log(total)

class TpeOptimizer(AlgorithmBase):
    """Parzen-tree estimator: sample good-kernel candidates, take max l/g."""
    name = "tpe"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        super(TpeOptimizer, self).__init__(
            names, bounds, x0=x0, n_obj=n_obj, n_constr=n_constr,
            seed=seed, budget=budget, **opts)
        self._X = []   # evaluated points, real coordinates
        self._Y = []   # scalar objective per point
        self._G = []   # constraint vector per point, or None
        mask = self.opts.get("integer_mask")
        if mask is not None and len(mask) != self.n:
            raise ValueError("integer_mask length mismatch")
    def tell(self, X, F, G=None):
        if not X:
            return
        for i, pt in enumerate(X):
            y = float(scalar_of(F[i]))
            if G is None:
                g = None
            else:
                g = G[i]
                if g is not None:
                    g = [float(v) for v in g]
            self._X.append(self._clip(list(pt)))
            self._Y.append(y)
            self._G.append(g)
            self.eval_count += 1
            if g is None or constraint_violation(g) <= 0.0:
                self._record_best(self._X[-1], y)
    def seed_history(self, points):
        'Warm-start: every past evaluation joins the Parzen history.'
        for item in points:
            if item is None:
                continue
            x = self._clip(list(item[0]))
            try:
                y = float(scalar_of(item[1]))
            except (TypeError, ValueError):
                continue
            g = item[2] if len(item) > 2 else None
            if g is not None:
                g = [float(v) for v in g]
            self._X.append(x)
            self._Y.append(y)
            self._G.append(g)
            self.eval_count += 1
            if g is None or constraint_violation(g) <= 0.0:
                self._record_best(x, y)
    def _split_good_bad(self):
        feas = []
        infeas = []
        for i in range(len(self._Y)):
            g = self._G[i]
            if g is not None and constraint_violation(g) > 0.0:
                infeas.append(i)
            else:
                feas.append(i)
        feas.sort(key=lambda i: self._Y[i])
        n_f = len(feas)
        if n_f == 0:
            return [], infeas
        n_good = int(math.floor(_GAMMA * n_f))
        if n_good < 1:
            n_good = 1
        good = feas[:n_good]
        bad = feas[n_good:] + infeas
        return good, bad
    def _latin_hypercube(self, m):
        m = max(1, int(m))
        cols = []
        for j in range(self.n):
            col = [(i + self.rng.random()) / m for i in range(m)]
            self.rng.shuffle(col)
            cols.append(col)
        pts = []
        for i in range(m):
            pts.append(self._to_real([cols[j][i] for j in range(self.n)]))
        return pts
    def _snap_integers(self, pt):
        """Force integer-mask dimensions onto integral values in bounds."""
        mask = self.opts.get("integer_mask")
        if not mask:
            return pt
        out = list(pt)
        for j in range(self.n):
            if j < len(mask) and mask[j]:
                lo, hi = self.bounds[j]
                ilo = int(math.ceil(lo))
                ihi = int(math.floor(hi))
                if ilo > ihi:
                    out[j] = min(hi, max(lo, float(round(out[j]))))
                else:
                    v = int(round(out[j]))
                    if v < ilo:
                        v = ilo
                    elif v > ihi:
                        v = ihi
                    out[j] = float(v)
        return out
    def _bandwidths(self, unit_pts):
        'Per-dim Scott bandwidth, floored by the TPE magic-clip rule.'
        m = len(unit_pts)
        floor = 1.0 / min(100.0, 1.0 + m)
        if floor < 1e-3:
            floor = 1e-3
        hs = []
        for j in range(self.n):
            col = [p[j] for p in unit_pts]
            mean = sum(col) / m
            var = sum((v - mean) ** 2 for v in col) / m
            bw = 1.06 * math.sqrt(var) * (m ** -0.2)
            if bw < floor:
                bw = floor
            if bw > 1.0:
                bw = 1.0
            hs.append(bw)
        return hs
    @staticmethod
    def _log_kernel_density(x, centers, bw):
        if not centers:
            return float("-inf")
        if bw < 1e-12:
            bw = 1e-12
        const = -math.log(bw) - 0.5 * math.log(2.0 * math.pi)
        terms = []
        for c in centers:
            z = (x - c) / bw
            terms.append(const - 0.5 * z * z)
        return _logsumexp(terms) - math.log(len(centers))
    def _sample_from_good(self, good_u, hs):
        src = good_u[self.rng.randrange(len(good_u))]
        pt = []
        for j in range(self.n):
            v = src[j] + self.rng.gauss(0.0, hs[j])
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            pt.append(v)
        return pt
    def _log_ratio(self, u, good_cols, bad_cols, hs_g, hs_b):
        total = 0.0
        for j in range(self.n):
            lg = self._log_kernel_density(u[j], good_cols[j], hs_g[j])
            if bad_cols:
                lb = self._log_kernel_density(u[j], bad_cols[j], hs_b[j])
                total += lg - lb
            else:
                total += lg
        return total

    def ask(self, n=1):
        n = max(1, int(n))
        good, bad = self._split_good_bad()
        if len(self._X) < max(5, self.n) or not good:
            pts = self._latin_hypercube(n)
            return [self._snap_integers(p) for p in pts]
        good_u = [self._to_unit(self._X[i]) for i in good]
        bad_u = [self._to_unit(self._X[i]) for i in bad]
        hs_g = self._bandwidths(good_u)
        hs_b = self._bandwidths(bad_u) if bad_u else None
        good_cols = [[p[j] for p in good_u] for j in range(self.n)]
        bad_cols = [[p[j] for p in bad_u] for j in range(self.n)] if bad_u else None
        k = _MAX_CANDIDATES
        four = 4 * max(self.n, n)
        if four < k:
            k = four
        ranked = []
        for _ in range(k):
            u = self._sample_from_good(good_u, hs_g)
            ranked.append((self._log_ratio(u, good_cols, bad_cols, hs_g, hs_b), u))
        ranked.sort(key=lambda t: t[0], reverse=True)
        selected = []
        for _, u in ranked:
            if len(selected) >= n:
                break
            clash = False
            for v in selected:
                d2 = 0.0
                for j in range(self.n):
                    diff = u[j] - v[j]
                    d2 += diff * diff
                if d2 < _DIVERSITY_RADIUS * _DIVERSITY_RADIUS:
                    clash = True
                    break
            if not clash:
                selected.append(u)
        while len(selected) < n:
            selected.append([self.rng.random() for _ in range(self.n)])
        return [self._snap_integers(self._to_real(u)) for u in selected]
    def simplex_size(self):
        good, _bad = self._split_good_bad()
        if len(good) < 2:
            return 0.0
        idx = sorted(good)[-20:]
        pts = [self._X[i] for i in idx]
        m = len(pts)
        centroid = [0.0] * self.n
        for p in pts:
            for j in range(self.n):
                centroid[j] += p[j]
        centroid = [c / m for c in centroid]
        acc = 0.0
        for p in pts:
            for j in range(self.n):
                diff = p[j] - centroid[j]
                acc += diff * diff
        return math.sqrt(acc / m)

    def restart(self, spread=0.15):
        self.restarts += 1
        self.last_restart_radius = spread
        _good, bad = self._split_good_bad()
        if bad:
            order = list(bad)
            self.rng.shuffle(order)
            drop = set(order[:len(order) // 2])
            nx, ny, ng = [], [], []
            for i in range(len(self._X)):
                if i not in drop:
                    nx.append(self._X[i])
                    ny.append(self._Y[i])
                    ng.append(self._G[i])
            self._X, self._Y, self._G = nx, ny, ng
        if self.best_y is None:
            return
        for _ in range(3):
            pt = []
            for j in range(self.n):
                lo, hi = self.bounds[j]
                v = self.best_x[j] + self.rng.gauss(0.0, spread) * (hi - lo)
                if v < lo:
                    v = lo
                elif v > hi:
                    v = hi
                pt.append(float(v))
            self._X.append(self._snap_integers(self._clip(pt)))
            self._Y.append(float(self.best_y))
            self._G.append(None)
    def snapshot(self):
        state = super(TpeOptimizer, self).snapshot()
        state["rng"] = _rng_pack(self.rng.getstate())
        state["samples"] = [list(p) for p in self._X]
        state["values"] = list(self._Y)
        state["constr"] = [None if g is None else list(g) for g in self._G]
        return state
    def restore(self, state):
        st = dict(state)
        st["rng"] = _rng_unpack(st.get("rng"))
        super(TpeOptimizer, self).restore(st)
        self._X = [list(p) for p in st.get("samples", [])]
        self._Y = [float(v) for v in st.get("values", [])]
        raw = st.get("constr")
        if raw is None:
            self._G = [None] * len(self._X)
        else:
            self._G = [None if g is None else [float(v) for v in g]
                       for g in raw]
        if len(self._G) != len(self._X):
            self._G = (self._G + [None] * len(self._X))[:len(self._X)]
register("tpe", TpeOptimizer)
# ---- NSGA-II multi-objective GA ----
INF = float("inf")

def _jsonify(obj):
    if isinstance(obj, tuple):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, dict):
        return dict((k, _jsonify(v)) for k, v in obj.items())
    return obj

class Nsga2(AlgorithmBase):
    'NSGA-II for multi-objective problems (n_obj >= 2).'
    name = "nsga2"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        self.eta_c = 20.0
        self.eta_m = 20.0
        self.pc = 0.9
        self.pm = 1.0 / max(1, self.n)
        self.archive_cap = 100
        b = self.budget
        if b is None:
            b = self.opts.get("budget", None)
        if self.opts.get("popsize", None) is not None:
            self.popsize = max(4, int(self.opts["popsize"]))
        elif b is not None and b < 300:
            self.popsize = 24
        else:
            self.popsize = 40
        self.pop = []          # evaluated parents, each {"x","f","g"}
        self._gen = []         # evaluated offspring of current generation
        self.archive = []      # feasible non-dominated records across runs
        self._queue = []       # [(x, tag)] ready for ask(), tag init/gen
        self._inflight = []    # tags of asked-but-not-told points
        self._init_made = 0    # LHS points allocated so far
        self._off_made = 0     # offspring allocated in current generation
    def seed_history(self, points):
        'Warm-start: past evaluations join the initial parent pool.'
        seeds = self._sort_seeds(points)
        if not seeds:
            return
        for item in seeds:
            x = self._clip(list(item[0]))
            f_in = item[1]
            if isinstance(f_in, (list, tuple)):
                f = [float(v) for v in f_in]
            else:
                f = [float(f_in)]
            g_in = item[2] if len(item) > 2 else None
            g = [] if g_in is None else [float(v) for v in g_in]
            rec = {"x": x, "f": f, "g": g}
            self.eval_count += 1
            self._record_best(x, scalar_of(f))
            self._archive_add(rec)
            if len(self.pop) < self.popsize:
                self.pop.append(rec)
    def ask(self, n=1):
        out = []
        while len(out) < n:
            if not self._queue and not self._fill_queue():
                break
            if not self._queue:
                break
            x, tag = self._queue.pop(0)
            self._inflight.append(tag)
            out.append(list(x))
        return out
    def tell(self, X, F, G=None):
        for i in range(len(X)):
            f = F[i]
            if isinstance(f, (list, tuple)):
                f = [float(v) for v in f]
            else:
                f = [float(f)]
            if G is None:
                g = []
            else:
                gi = G[i]
                g = [] if gi is None else [float(v) for v in gi]
            rec = {"x": self._clip(list(X[i])), "f": f, "g": g}
            self.eval_count += 1
            self._record_best(rec["x"], scalar_of(f))
            self._archive_add(rec)
            tag = self._inflight.pop(0) if self._inflight else None
            if tag == "init" or (tag is None and len(self.pop) < self.popsize):
                self.pop.append(rec)
            else:
                self._gen.append(rec)
            if len(self._gen) >= self.popsize:
                self._select()
    def best(self):
        """Return (archive_X, archive_F) of the Pareto archive."""
        xs = [list(r["x"]) for r in self.archive]
        fs = [list(r["f"]) for r in self.archive]
        return xs, fs
    def _fill_queue(self):
        if self._queue:
            return True
        if self._init_made < self.popsize:
            for pt in self._lhs(self.popsize - self._init_made):
                self._queue.append((pt, "init"))
                self._init_made += 1
            return bool(self._queue)
        if len(self.pop) >= self.popsize and self._off_made < self.popsize:
            self._breed(self.popsize - self._off_made)
            return bool(self._queue)
        return False
    def _lhs(self, count):
        pts = [[0.0] * self.n for _ in range(count)]
        for j in range(self.n):
            perm = list(range(count))
            self.rng.shuffle(perm)
            for i in range(count):
                pts[i][j] = (perm[i] + self.rng.random()) / count
        return [self._to_real(p) for p in pts]
    def _breed(self, count):
        ranks, crowd = self._rank_crowd(self.pop)
        made = 0
        while made < count:
            i = self._tournament(ranks, crowd, self.pop)
            j = self._tournament(ranks, crowd, self.pop)
            c1, c2 = self._sbx(self.pop[i]["x"], self.pop[j]["x"])
            self._queue.append((self._mutate(c1), "gen"))
            self._off_made += 1
            made += 1
            if made < count:
                self._queue.append((self._mutate(c2), "gen"))
                self._off_made += 1
                made += 1
    def _dominates(self, fa, ga, fb, gb):
        """True when a constraint-dominates b (Deb 2000 rules)."""
        va = constraint_violation(ga)
        vb = constraint_violation(gb)
        a_ok = va <= 0.0
        b_ok = vb <= 0.0
        if a_ok and not b_ok:
            return True
        if b_ok and not a_ok:
            return False
        if not a_ok and not b_ok:
            return va < vb
        strictly = False
        for u, v in zip(fa, fb):
            if u > v:
                return False
            if u < v:
                strictly = True
        return strictly
    def _fast_sort(self, recs):
        n = len(recs)
        sp = [[] for _ in range(n)]
        np_ = [0] * n
        for p in range(n):
            fp, gp = recs[p]["f"], recs[p]["g"]
            for q in range(p + 1, n):
                fq, gq = recs[q]["f"], recs[q]["g"]
                if self._dominates(fp, gp, fq, gq):
                    sp[p].append(q)
                    np_[q] += 1
                elif self._dominates(fq, gq, fp, gp):
                    sp[q].append(p)
                    np_[p] += 1
        ranks = [0] * n
        fronts = []
        current = [i for i in range(n) if np_[i] == 0]
        while current:
            fronts.append(current)
            nxt = []
            for p in current:
                for q in sp[p]:
                    np_[q] -= 1
                    if np_[q] == 0:
                        ranks[q] = len(fronts)
                        nxt.append(q)
            current = nxt
        return fronts, ranks
    def _crowd_of(self, recs, idxs):
        cd = {}
        m = len(idxs)
        if m == 0:
            return cd
        if m <= 2:
            for i in idxs:
                cd[i] = INF
            return cd
        for i in idxs:
            cd[i] = 0.0
        n_obj = len(recs[idxs[0]]["f"])
        for k in range(n_obj):
            order = sorted(idxs, key=lambda i: recs[i]["f"][k])
            cd[order[0]] = INF
            cd[order[-1]] = INF
            span = recs[order[-1]]["f"][k] - recs[order[0]]["f"][k]
            if span <= 0.0:
                continue
            for t in range(1, m - 1):
                if cd[order[t]] == INF:
                    continue
                cd[order[t]] += ((recs[order[t + 1]]["f"][k]
                                  - recs[order[t - 1]]["f"][k]) / span)
        return cd
    def _rank_crowd(self, recs):
        fronts, ranks = self._fast_sort(recs)
        crowd = [0.0] * len(recs)
        for front in fronts:
            cd = self._crowd_of(recs, front)
            for i in front:
                crowd[i] = cd[i]
        return ranks, crowd
    def _tournament(self, ranks, crowd, recs):
        a = self.rng.randrange(len(recs))
        b = self.rng.randrange(len(recs))
        ga = recs[a]["g"]
        gb = recs[b]["g"]
        a_ok = constraint_violation(ga) <= 0.0
        b_ok = constraint_violation(gb) <= 0.0
        if a_ok != b_ok:
            return a if a_ok else b
        if ranks[a] != ranks[b]:
            return a if ranks[a] < ranks[b] else b
        if crowd[a] != crowd[b]:
            return a if crowd[a] > crowd[b] else b
        return a if self.rng.random() < 0.5 else b
    def _select(self):
        combined = self.pop + self._gen
        fronts, _ = self._fast_sort(combined)
        new_pop = []
        for front in fronts:
            if len(new_pop) + len(front) <= self.popsize:
                for i in front:
                    new_pop.append(combined[i])
            else:
                cd = self._crowd_of(combined, front)
                order = sorted(front,
                               key=lambda i: (-cd[i], combined[i]["f"]))
                room = self.popsize - len(new_pop)
                for i in order[:room]:
                    new_pop.append(combined[i])
                break
        self.pop = new_pop
        self._gen = []
        self._off_made = 0
    def _sbx(self, p1, p2):
        c1 = list(p1)
        c2 = list(p2)
        if self.rng.random() >= self.pc:
            return self._clip(c1), self._clip(c2)
        for j in range(self.n):
            if self.rng.random() > 0.5:
                continue
            y1 = p1[j]
            y2 = p2[j]
            if abs(y1 - y2) < 1e-14:
                continue
            lo, hi = self.bounds[j]
            if y1 > y2:
                y1, y2 = y2, y1
            u = self.rng.random()
            beta = 1.0 + 2.0 * (y1 - lo) / (y2 - y1)
            alpha = 2.0 - beta ** (-(self.eta_c + 1.0))
            if u <= 1.0 / alpha:
                betaq = (u * alpha) ** (1.0 / (self.eta_c + 1.0))
            else:
                betaq = (1.0 / (2.0 - u * alpha)) ** (1.0 / (self.eta_c + 1.0))
            c1[j] = 0.5 * ((y1 + y2) - betaq * (y2 - y1))
            beta = 1.0 + 2.0 * (hi - y2) / (y2 - y1)
            alpha = 2.0 - beta ** (-(self.eta_c + 1.0))
            if u <= 1.0 / alpha:
                betaq = (u * alpha) ** (1.0 / (self.eta_c + 1.0))
            else:
                betaq = (1.0 / (2.0 - u * alpha)) ** (1.0 / (self.eta_c + 1.0))
            c2[j] = 0.5 * ((y1 + y2) + betaq * (y2 - y1))
            if self.rng.random() < 0.5:
                c1[j], c2[j] = c2[j], c1[j]
        return self._clip(c1), self._clip(c2)
    def _mutate(self, pt):
        x = list(pt)
        for j in range(self.n):
            if self.rng.random() >= self.pm:
                continue
            lo, hi = self.bounds[j]
            if hi <= lo:
                continue
            y = x[j]
            d1 = (y - lo) / (hi - lo)
            d2 = (hi - y) / (hi - lo)
            u = self.rng.random()
            if u < 0.5:
                xy = 1.0 - d1
                val = 2.0 * u + (1.0 - 2.0 * u) * (xy ** (self.eta_m + 1.0))
                dq = val ** (1.0 / (self.eta_m + 1.0)) - 1.0
            else:
                xy = 1.0 - d2
                val = (2.0 * (1.0 - u)
                       + 2.0 * (u - 0.5) * (xy ** (self.eta_m + 1.0)))
                dq = 1.0 - val ** (1.0 / (self.eta_m + 1.0))
            x[j] = y + dq * (hi - lo)
        return self._clip(x)
    def _archive_add(self, rec):
        if rec["g"] and constraint_violation(rec["g"]) > 0.0:
            return
        survivors = []
        for old in self.archive:
            if old["f"] == rec["f"] or old["x"] == rec["x"]:
                return
            if self._dominates(old["f"], old["g"], rec["f"], rec["g"]):
                return
            if not self._dominates(rec["f"], rec["g"], old["f"], old["g"]):
                survivors.append(old)
        survivors.append(rec)
        self.archive = survivors
        if len(self.archive) > self.archive_cap:
            self._archive_trim()
    def _archive_trim(self):
        idxs = list(range(len(self.archive)))
        cd = self._crowd_of(self.archive, idxs)
        order = sorted(idxs, key=lambda i: -cd[i])
        self.archive = [self.archive[i] for i in order[:self.archive_cap]]
    @staticmethod
    def _norm_record(rec):
        out = dict(rec)
        out["x"] = list(out.get("x", []))
        f = out.get("f", [])
        out["f"] = [float(v) for v in f] if isinstance(
            f, (list, tuple)) else [float(f)]
        g = out.get("g", [])
        out["g"] = [] if not g else [float(v) for v in g]
        return out
    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["rng"] = _rng_pack(state["rng"])
        state["pop"] = _jsonify(self.pop)
        state["gen"] = _jsonify(self._gen)
        state["archive"] = _jsonify(self.archive)
        state["queue"] = _jsonify(self._queue)
        state["inflight"] = list(self._inflight)
        state["init_made"] = self._init_made
        state["off_made"] = self._off_made
        state["popsize"] = self.popsize
        return state
    def restore(self, state):
        st = dict(state)
        if st.get("rng") is not None:
            st["rng"] = _rng_unpack(st["rng"])
        AlgorithmBase.restore(self, st)
        self.popsize = int(state.get("popsize", self.popsize))
        self.pop = [self._norm_record(r) for r in state.get("pop", [])]
        self._gen = [self._norm_record(r) for r in state.get("gen", [])]
        self.archive = [self._norm_record(r) for r in state.get("archive", [])]
        self._queue = [([float(v) for v in q[0]], q[1])
                       for q in state.get("queue", [])]
        self._inflight = list(state.get("inflight", []))
        self._init_made = int(state.get("init_made", 0))
        self._off_made = int(state.get("off_made", 0))
register("nsga2", Nsga2)

# ---- Scalarisation wrappers (weighted / worstspec) ----

def weighted(f_list, weights=None):
    """Weighted average of objective values (minimisation score)."""
    vals = [float(v) for v in f_list]
    if not vals:
        return 0.0
    if weights is None:
        w = [1.0] * len(vals)
    else:
        w = [float(v) for v in weights]
        if len(w) != len(vals):
            raise ValueError("weights length mismatch")
    total = sum(w)
    if total == 0.0:
        raise ValueError("weights sum to zero")
    return sum(wi * vi for wi, vi in zip(w, vals)) / total

def worstspec(errors):
    """Worst-spec score: largest error/violation (minimise the max)."""
    vals = [float(v) for v in errors]
    if not vals:
        return 0.0
    return max(vals)

# ---- Wrappers: scalarise multi-objective F, delegate search to one engine ----

class _ScalarWrapper(AlgorithmBase):
    """Single-objective engine fed with scalarised multi-objective F."""
    name = "scalar_wrapper"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        w = self.opts.get("weights", None)
        self.weights = None if w is None else [float(v) for v in w]
        inner_opts = dict(self.opts)
        inner_opts.pop("inner", None)
        inner_opts.pop("weights", None)
        inner_name = self.opts.get("inner", "lshade")
        self.inner = create(inner_name, names, bounds, x0=x0, n_obj=1,
                            n_constr=n_constr, seed=seed, budget=budget,
                            **inner_opts)
    def _scalar(self, f_vals):
        raise NotImplementedError
    def ask(self, n=1):
        return self.inner.ask(n)
    def tell(self, X, F, G=None):
        scalars = []
        for i in range(len(X)):
            f = F[i]
            if isinstance(f, (list, tuple)):
                s = self._scalar([float(v) for v in f])
            else:
                s = float(f)
            scalars.append(s)
            self.eval_count += 1
            self._record_best(X[i], s)
        self.inner.tell(X, scalars, G)
    def seed_history(self, points):
        fwd = []
        for item in points:
            if item is None:
                continue
            f_in = item[1]
            if isinstance(f_in, (list, tuple)):
                s = self._scalar([float(v) for v in f_in])
            else:
                s = float(f_in)
            g = item[2] if len(item) > 2 else None
            fwd.append((item[0], s, g))
            self.eval_count += 1
            self._record_best(list(item[0]), s)
        if fwd and hasattr(self.inner, "seed_history"):
            self.inner.seed_history(fwd)
    def best(self):
        return self.inner.best()
    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["rng"] = _rng_pack(state["rng"])
        state["weights"] = None if self.weights is None else list(self.weights)
        state["inner"] = _jsonify(self.inner.snapshot())
        return state
    def restore(self, state):
        st = dict(state)
        if st.get("rng") is not None:
            st["rng"] = _rng_unpack(st["rng"])
        w = st.get("weights", None)
        self.weights = None if w is None else [float(v) for v in w]
        AlgorithmBase.restore(self, st)
        inner_state = state.get("inner")
        if inner_state is not None:
            self.inner.restore(_fix_snap_rng(inner_state))

class WeightedScalar(_ScalarWrapper):
    """Weighted-sum wrapper: multi-objective F -> one weighted score."""
    name = "weighted"
    def _scalar(self, f_vals):
        return weighted(f_vals, self.weights)

class WorstspecScalar(_ScalarWrapper):
    """Worst-spec wrapper: multi-objective F -> max objective error."""
    name = "worstspec"
    def _scalar(self, f_vals):
        return worstspec(f_vals)
register("weighted", WeightedScalar)
register("worstspec", WorstspecScalar)

# ---- Multi-line racing portfolio ----

class Portfolio(AlgorithmBase):
    """Races member optimisers under a UCB budget scheduler."""
    name = "portfolio"
    WINDOW = 30       # W: improvement look-back per line
    RESCHED = 10      # evaluations between UCB recomputes
    BATCH_CAP = 4     # max points dispatched per ask
    GUARANTEE = 0.15  # share of B each line gets up front
    ZERO_LIMIT = 2    # stagnant windows before a line is paused
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts):
        AlgorithmBase.__init__(self, names, bounds, x0=x0, n_obj=n_obj,
                               n_constr=n_constr, seed=seed, budget=budget,
                               **opts)
        self.ucb_c = float(opts.get("ucb_c", 1.0))
        self.B = self.budget if self.budget is not None else opts.get("budget")
        if self.B is not None:
            self.B = int(self.B)
        self.members = []
        self.member_names = []
        self._build_members(opts)
        k = len(self.members)
        self.n_members = k
        self.consumed = [0] * k
        self.quota = [int(self.GUARANTEE * self.B)] * k if self.B else [0] * k
        self.wake_credit = [0] * k
        self.zero_windows = [0] * k
        self.paused = [False] * k
        self.line_best = [None] * k
        self.hist = [[] for _ in range(k)]  # (cum_evals, line_best) per line
        self._win_best = [None] * k
        self._win_cons = [0] * k
        self.ucb_scores = [0.0] * k
        self.winner = 0
        self.pause_count = 0
        self.wake_count = 0
        self._m_restarts = [0] * k
        self._last_sched = 0
        self._batches = []  # (member_index, [points]) awaiting tell
        self._archive = []  # (x, f_vec) running Pareto archive (n_obj>1)
    def seed_history(self, points):
        'Warm-start: every member line learns from the whole history.'
        seeds = [p for p in points if p is not None]
        if not seeds:
            return
        for item in seeds:
            self.eval_count += 1
            try:
                s = float(scalar_of(item[1]))
            except (TypeError, ValueError):
                s = None
            if s is not None:
                if self.best_y is None or s < self.best_y:
                    self._record_best(list(item[0]), s)
                for j in range(self.n_members):
                    if self.line_best[j] is None or s < self.line_best[j]:
                        self.line_best[j] = s
        for member in self.members:
            if hasattr(member, "seed_history"):
                member.seed_history(seeds)
    def _build_members(self, opts):
        injected = opts.get("member_instances")
        if injected:
            for i, inst in enumerate(injected):
                self.members.append(inst)
                self.member_names.append(getattr(inst, "name", "m%d" % i))
            return
        names = list(opts.get("members") or ["cmaes_ipop", "tpe"])
        if self.B is not None and self.B >= 1000 and len(names) >= 3:
            names = names[:3]
        elif len(names) > 2:
            names = names[:2]
        skip = ("members", "member_instances", "member_opts", "ucb_c")
        mopts = dict(opts.get("member_opts") or {})
        for key in opts:
            if key not in skip:
                mopts.setdefault(key, opts[key])
        for nm in names:
            seed_i = self.rng.randint(0, 2 ** 30 - 1)
            inst = create(nm, self.names, self.bounds, x0=self.x0,
                          n_obj=self.n_obj, n_constr=self.n_constr,
                          seed=seed_i, budget=self.B, **mopts)
            self.members.append(inst)
            self.member_names.append(nm)
    def _improve(self, i, window=None):
        if window is None:
            window = self.WINDOW
        h = self.hist[i]
        if not h:
            return 0.0
        now_e, now_b = h[-1]
        if now_b is None:
            return 0.0
        target = now_e - window
        ref = None
        for e, b in h:
            if e >= target:
                ref = b
                break
        if ref is None:
            ref = h[0][1]
        if ref is None:
            return 0.0
        return max(0.0, ref - now_b)
    def _ucb_score(self, i):
        ni = self.consumed[i]
        if ni <= 0:
            return float("inf")
        t = max(2, self.eval_count)
        rate = self._improve(i) / float(ni)
        explore = self.ucb_c * math.sqrt(math.log(float(t)) / float(ni))
        return rate + explore
    def _wake_all(self, credit):
        for i in range(self.n_members):
            if self.paused[i]:
                self.wake_count += 1
            self.paused[i] = False
            self.zero_windows[i] = 0
            self.wake_credit[i] += credit
        self.winner = 0
    def _schedule(self):
        self._last_sched = self.eval_count
        if self.B is None:
            q = int(self.GUARANTEE * self.eval_count)
            self.quota = [q] * self.n_members
        scores = []
        for i in range(self.n_members):
            scores.append(self._ucb_score(i))
        self.ucb_scores = scores
        for i in range(self.n_members):
            cur = self.line_best[i]
            prev = self._win_best[i]
            win_imp = 0.0
            if cur is not None and prev is not None:
                win_imp = max(0.0, prev - cur)
            if not self.paused[i]:
                if self._win_cons[i] > 0 and win_imp <= 0.0:
                    self.zero_windows[i] += 1
                elif win_imp > 0.0:
                    self.zero_windows[i] = 0
                if (self.zero_windows[i] >= self.ZERO_LIMIT
                        and self.consumed[i] >= self.quota[i]):
                    self.paused[i] = True
                    self.pause_count += 1
            self._win_best[i] = cur
            self._win_cons[i] = 0
        if self.n_members > 0 and all(self.paused):
            self._wake_all(5)
        active = [i for i in range(self.n_members) if not self.paused[i]]
        if active:
            self.winner = max(active, key=lambda i: (scores[i], -i))
    def _choose_member(self):
        active = [i for i in range(self.n_members) if not self.paused[i]]
        if not active:
            self._wake_all(5)
            active = [i for i in range(self.n_members) if not self.paused[i]]
        if not active:
            return None
        needy = []
        for i in active:
            target = self.quota[i] + self.wake_credit[i]
            deficit = target - self.consumed[i]
            if deficit > 0:
                needy.append((deficit, i))
        if needy:
            needy.sort(key=lambda t: (-t[0], t[1]))
            top = needy[0][0]
            ties = [i for d, i in needy if d == top]
            return ties[self.rng.randrange(len(ties))]
        if not self.paused[self.winner]:
            return self.winner
        best_i, best_s = active[0], None
        for i in active:
            s = self.ucb_scores[i] if self.ucb_scores else self._ucb_score(i)
            if best_s is None or s > best_s:
                best_s, best_i = s, i
        return best_i

    def ask(self, n=1):
        """Route a small batch to one active line (deficit then UCB)."""
        n = int(n)
        if n <= 0 or self.n_members == 0:
            return []
        if self.B is not None:
            remain = self.B - self.eval_count
            if remain <= 0:
                return []
            n = min(n, remain)
        k = min(n, self.BATCH_CAP)
        idx = self._choose_member()
        if idx is None:
            return []
        pts = list(self.members[idx].ask(k))
        if not pts:
            for j in range(self.n_members):
                if j == idx or self.paused[j]:
                    continue
                pts = list(self.members[j].ask(k))
                if pts:
                    idx = j
                    break
        if not pts:
            return []
        self._batches.append((idx, pts))
        return [list(p) for p in pts]

    def tell(self, X, F, G=None):
        'Feed objectives back to the owning member lines, in order.'
        if not self._batches:
            return
        total = sum(len(pts) for _, pts in self._batches)
        if len(F) > total:
            raise ValueError("tell length %d > pending %d" % (len(F), total))
        pos = 0
        member_restarted = False
        while pos < len(F) and self._batches:
            idx, pts = self._batches[0]
            take = min(len(pts), len(F) - pos)
            chunk = [list(p) for p in pts[:take]]
            fs = list(F[pos:pos + take])
            gs = None if G is None else list(G[pos:pos + take])
            self.members[idx].tell(chunk, fs, gs)
            self.consumed[idx] += take
            self._win_cons[idx] += take
            self.eval_count += take
            for j in range(take):
                s = scalar_of(fs[j])
                if s is not None:
                    if self.line_best[idx] is None or s < self.line_best[idx]:
                        self.line_best[idx] = s
                    if self.n_obj == 1:
                        self._record_best(chunk[j], s)
                    else:
                        self._add_archive(chunk[j], fs[j])
            self.hist[idx].append((self.consumed[idx], self.line_best[idx]))
            if self.members[idx].restarts > self._m_restarts[idx]:
                self._m_restarts[idx] = self.members[idx].restarts
                member_restarted = True
            pos += take
            if take < len(pts):
                self._batches[0] = (idx, pts[take:])
            else:
                self._batches.pop(0)
        if member_restarted:
            self._wake_all(5)
        if self.eval_count - self._last_sched >= self.RESCHED:
            self._schedule()
    def best(self):
        """Single objective: best (x, y) across lines; multi: merged front."""
        if self.n_obj > 1:
            return self._merged_front()
        bx, by = None, None
        for m in self.members:
            mx, my = m.best()
            if my is None:
                continue
            if by is None or deb_better(my, None, by, None):
                bx, by = mx, my
        if by is None:
            return (list(self.best_x), self.best_y)
        return (list(bx), by)
    def _add_archive(self, x, f):
        if not isinstance(f, (list, tuple)):
            return
        fv = list(f)
        keep = []
        dominated = False
        for x2, f2 in self._archive:
            if self._dominates(f2, fv):
                dominated = True
                break
            if not self._dominates(fv, f2):
                keep.append((x2, f2))
        if not dominated:
            keep.append((list(x), fv))
        self._archive = keep
    @staticmethod
    def _dominates(fa, fb):
        le = True
        lt = False
        for a, b in zip(fa, fb):
            if a > b:
                le = False
                break
            if a < b:
                lt = True
        return le and lt
    def _merged_front(self):
        pairs = [(list(x), list(f)) for x, f in self._archive]
        for m in self.members:
            try:
                res = m.best()
            except Exception:
                continue
            if not res:
                continue
            xs, fs = res
            if fs is None:
                continue
            if isinstance(fs, (list, tuple)) and fs and isinstance(fs[0], (list, tuple)):
                for x, f in zip(xs, fs):
                    pairs.append((list(x), list(f)))
            elif isinstance(fs, (list, tuple)):
                pairs.append((list(xs), list(fs)))
        keep = []
        for x, f in pairs:
            dominated = False
            drop = []
            for j, (x2, f2) in enumerate(keep):
                if self._dominates(f2, f):
                    dominated = True
                    break
                if self._dominates(f, f2):
                    drop.append(j)
            if not dominated:
                for j in reversed(drop):
                    keep.pop(j)
                keep.append((x, f))
        xs = [x for x, _ in keep]
        fs = [f for _, f in keep]
        return (xs, fs)
    def restart(self, spread=0.15):
        self.restarts += 1
        self.last_restart_radius = spread
        for m in self.members:
            m.restart(spread)
        self._wake_all(5)
    def simplex_size(self):
        vals = [float(m.simplex_size()) for i, m in enumerate(self.members)
                if not self.paused[i]]
        if not vals:
            vals = [float(m.simplex_size()) for m in self.members]
        return max(vals) if vals else 0.0

    def snapshot(self):
        state = AlgorithmBase.snapshot(self)
        state["member_names"] = list(self.member_names)
        state["members"] = [m.snapshot() for m in self.members]
        state["n_members"] = self.n_members
        state["consumed"] = list(self.consumed)
        state["quota"] = list(self.quota)
        state["wake_credit"] = list(self.wake_credit)
        state["zero_windows"] = list(self.zero_windows)
        state["paused"] = list(self.paused)
        state["line_best"] = list(self.line_best)
        state["hist"] = [[list(pair) for pair in lst] for lst in self.hist]
        state["win_best"] = list(self._win_best)
        state["win_cons"] = list(self._win_cons)
        state["ucb_scores"] = list(self.ucb_scores)
        state["winner"] = self.winner
        state["pause_count"] = self.pause_count
        state["wake_count"] = self.wake_count
        state["m_restarts"] = list(self._m_restarts)
        state["last_sched"] = self._last_sched
        state["batches"] = [[idx, [list(p) for p in pts]]
                            for idx, pts in self._batches]
        state["ucb_c"] = self.ucb_c
        state["B"] = self.B
        state["archive"] = [[list(x), list(f)] for x, f in self._archive]
        return state

    def restore(self, state):
        st = dict(state)
        st["rng"] = _rng_unpack(st.get("rng"))
        AlgorithmBase.restore(self, st)
        snaps = st.get("members") or []
        for i, snap in enumerate(snaps):
            if i < len(self.members):
                self.members[i].restore(_fix_snap_rng(snap))
        k = self.n_members
        self.consumed = list(st.get("consumed", self.consumed))[:k]
        self.quota = list(st.get("quota", self.quota))[:k]
        self.wake_credit = list(st.get("wake_credit", self.wake_credit))[:k]
        self.zero_windows = list(st.get("zero_windows", self.zero_windows))[:k]
        self.paused = list(st.get("paused", self.paused))[:k]
        self.line_best = list(st.get("line_best", self.line_best))[:k]
        self._win_best = list(st.get("win_best", self._win_best))[:k]
        self._win_cons = list(st.get("win_cons", self._win_cons))[:k]
        self.ucb_scores = list(st.get("ucb_scores", self.ucb_scores))[:k]
        self.hist = [[(e, b) for e, b in lst]
                     for lst in (st.get("hist") or [])][:k]
        while len(self.hist) < k:
            self.hist.append([])
        self.winner = int(st.get("winner", 0))
        self.pause_count = int(st.get("pause_count", 0))
        self.wake_count = int(st.get("wake_count", 0))
        self._m_restarts = list(st.get("m_restarts", self._m_restarts))[:k]
        self._last_sched = int(st.get("last_sched", 0))
        self._batches = [(int(idx), [list(p) for p in pts])
                         for idx, pts in (st.get("batches") or [])]
        self.ucb_c = float(st.get("ucb_c", self.ucb_c))
        self.B = st.get("B", self.B)
        self._archive = [(list(x), list(f))
                         for x, f in (st.get("archive") or [])]
register("portfolio", Portfolio)
# ---- Human-readable descriptions (GUI 算法说明) ----
# keys: short=one-liner, how=working principle, when=use cases,
#       pro=strengths, con=limits, hist=how past evaluations are used.
DESCRIPTIONS = {
    "simplex": {
        "title": "单纯形 / Nelder-Mead (simplex)", "short": "有界单纯形局部搜索，默认策略",
        "how": "维护 n+1 个顶点的单纯形，反复反射/扩张/收缩/收缩整体，逐步贴合目标的局部形状。一次只要一个仿真点，适合昂贵仿真。",
        "when": "变量少(≤5)、目标平滑、单峰、已有较好初值时的精调；收敛速度快。",
        "pro": "实现稳健、每次迭代只增加一次仿真；对光滑问题收敛快。",
        "con": "纯局部方法，多峰问题易陷局部最优；变量多时顶点数变多、更慢。",
        "hist": "v05：历史仿真点按目标值排序直接重建单纯形顶点(免重仿)；无快照续跑时不再冷启动。",
    },
    "coordinate": {
        "title": "坐标模式搜索 (coordinate)", "short": "逐轴 ±步长的模式搜索，EM 对齐默认",
        "how": "以 x0 为中心，每次只沿一个变量增大或减小一个步长；同轴两个方向都变差就把该轴步长减半，改进则回到第一轴继续。",
        "when": "已有可信初值(x_opt)、只需小范围修正；昂贵仿真、需要少量评估就贴近目标时。",
        "pro": "每步一次仿真；对非光滑/带深谷的匹配曲线比单纯形稳；无随机性，结果可复现。",
        "con": "纯局部；目标需要大范围改动或强变量耦合时不如 CMA-ES / 全局搜索。",
        "hist": "v12：EM 两阶段对齐新增；第二阶段默认用它从 x_opt 收敛到 x_em_ideal。",
    },
    "global": {
        "title": "全局差分进化 (global, v03 DE)", "short": "LHS 布点 + DE/rand/1 与 current-to-pbest/1 混合",
        "how": "初始用拉丁超立方铺满空间；每代对每个成员抛硬币选 DE/rand/1(探索)或 DE/current-to-pbest/1(开发)产生试验点，一对一竞争替换。",
        "when": "变量较多(5~7)、多峰、易陷局部最优、需要全局覆盖时；比单纯形更抗局部陷阱。",
        "pro": "全局探索能力强；变异策略混合兼顾探索与开发；对噪声较稳。",
        "con": "收敛到高精度解比单纯形慢；需要更多仿真次数才能出结果。",
        "hist": "v05：历史点填入种群槽位(带已知目标值)，初始种群立即可用，不再浪费仿真次数重新铺点。",
    },
    "de": {
        "title": "自适应差分进化 (de, SHADE 简化版)", "short": "在 global 基础上自适应 F/CR + 替换者档案",
        "how": "F 从截断 Cauchy 记忆采样、CR 从截断正态记忆采样，按成功改进加权更新记忆；被替换的父代进档案，供 current-to-pbest 差分向量使用，延缓种群塌缩。",
        "when": "与 global 相同的场景，但希望算法在线调参、预算更紧时优先选它。",
        "pro": "参数自整定，无需手调 F/CR；档案机制保持多样性。",
        "con": "实现更复杂，小预算(<30 次)下自适应记忆还学不出来，此时不如 global。",
        "hist": "v05：同 global，历史点填种群；同时这些点构成的改进信息会在后续成功试验中进入 F/CR 记忆。",
    },
    "lshade": {
        "title": "L-SHADE (成功历史 DE)", "short": "CEC2014 冠军算法：成功历史记忆 + 线性种群缩减",
        "how": "current-to-pbest/1 变异 + 外部档案；F/CR 用成功历史记忆加权 Lehmer/算术均值更新；种群规模随评测进度线性缩减，前期广搜、后期精收。",
        "when": "连续单目标、预算中等(≥50 次)、希望一次跑出高质量解；公认最强的 DE 变体之一。",
        "pro": "探索-开发自动平衡；种群缩减天然适配“先粗后精”的调参节奏。",
        "con": "仅单目标；记忆需要一定成功样本才生效，极小预算优势不大。",
        "hist": "v05：历史点填入初始种群(免重估)；种群缩减按真实评测进度(含历史)计算，衔接更自然。",
    },
    "cmaes": {
        "title": "CMA-ES (协方差矩阵自适应进化策略)", "short": "学习变量间相关性的模型法",
        "how": "维护搜索分布的均值与协方差矩阵，按排名加权更新步长与协方差，自动学习变量耦合关系(如 w 与 s 的折中方向)。",
        "when": "变量间强耦合、目标呈弯曲谷形(如电感 w/s/间距折中)时表现极佳；变量 ≤20。",
        "pro": "对变量相关性建模，沿谷方向收敛快；步长自适应。",
        "con": "多峰易早收敛(配 cmaes_ipop 重启缓解)；纯 Python 实现在高维略慢。",
        "hist": "v05：均值移到历史最优点；初始步长按历史点散布自动估计(聚集史→小步精搜，离散史→大步探搜)。",
    },
    "cmaes_ipop": {
        "title": "CMA-ES + IPOP 重启 (cmaes_ipop)", "short": "停滞就加倍种群重启，专治多峰",
        "how": "在 cmaes 基础上监控停滞(步长与历史改进)，停滞时按 IPOP策略放大种群重启，每次重启覆盖不同尺度的盆地。",
        "when": "多峰、需要反复跳出局部最优、预算较充裕(≥100 次)。",
        "pro": "多峰鲁棒性显著强于单次 cmaes；保留 cmaes 的耦合学习。",
        "con": "重启消耗预算；预算不足时重启未及生效就结束了。",
        "hist": "v05：同 cmaes 的历史热启动；重启中心优先取历史最优而非仅本次会话最优。",
    },
    "tpe": {
        "title": "TPE (树形 Parzen 估计器)", "short": "贝叶斯优化：用历史样本建好坏核密度模型",
        "how": "把历史评测按目标值分成好/坏两组，各维独立高斯核密度估计 l(x) 与 g(x)，采样候选点取 l/g 最大者，即“最像好点的点”。",
        "when": "仿真昂贵(每次几十秒以上)、希望用尽量少的评测次数出结果；变量混合(连续/整数)也适用。",
        "pro": "样本效率最高的方法之一；完全由历史数据驱动，评测越多模型越准。",
        "con": "早期样本不足时模型粗糙；高维(>20)核密度估计变慢变糊。",
        "hist": "v05：历史评测(变量+目标值)直接进入 Parzen 核密度历史，第一批候选就基于全部历史建模——最充分的史利用。",
    },
    "nsga2": {
        "title": "NSGA-II (多目标遗传算法)", "short": "Pareto 档案 + 拥挤距离的多目标搜索",
        "how": "非支配排序 + 拥挤距离选择，SBX 交叉与多项式变异繁殖；维护跨会话 Pareto 档案，给出目标之间的折中解集。",
        "when": "settings.mode=pareto(多目标)且想看目标间折中曲线时；如 Q 与 SRN 同时优化。",
        "pro": "一次运行给出整条 Pareto 前沿；档案可跨会话累积。",
        "con": "单目标时浪费；解集大时需要人工挑选最终点。",
        "hist": "v05：历史评测进入初始父代池与 Pareto 档案，前沿从历史延续而不是从零开始。",
    },
    "weighted": {
        "title": "加权标量化 (weighted)", "short": "多目标加权求和→单目标→喂给内层引擎",
        "how": "把各目标误差按权重线性加权成一个标量，再交给内层单目标算法(默认 lshade)搜索。",
        "when": "多目标但已知各目标相对重要性、想快速得到单一折中解。",
        "pro": "把多目标问题转给最强单目标引擎；权重直观可调。",
        "con": "权重敏感；非凸 Pareto 前沿上的折中点找不到。",
        "hist": "v05：历史按同样加权公式标量化后热启动内层引擎。",
    },
    "worstspec": {
        "title": "最差规格标量化 (worstspec)", "short": "取各目标相对规格裕度的最大违背→单目标",
        "how": "以各目标距规格的相对裕度衡量，标量=最差那项的违背程度；瓶颈目标自动主导搜索。",
        "when": "多规格约束且“木桶效应”明显(最差指标决定成败)时。",
        "pro": "与规格驱动的工程目标天然对齐；自动聚焦瓶颈。",
        "con": "只有一个瓶颈时其他目标可能过优化不足；平坦区梯度信息弱。",
        "hist": "v05：历史标量化后热启动内层引擎，同 weighted。",
    },
    "portfolio": {
        "title": "组合调度 (portfolio, 多算法竞跑)", "short": "2~3 条算法线并跑，UCB 分配预算",
        "how": "同时运行 cmaes_ipop/tpe 等成员线，按 UCB 得分分配评测预算；停滞的线被暂停并保留唤醒额度，防止误选的算法饿死全局。",
        "when": "不确定哪个算法适合当前问题、或问题前后期特性不同；预算 ≥60 次时收益明显。",
        "pro": "“算法选择”自动化；坏线被暂停，好线多拿预算。",
        "con": "成员本身有开销；小预算(<30)时摊薄了每条线的学习机会。",
        "hist": "v05：历史评测同时喂给每条成员线(各自建模/填种群)，竞跑从“已知历史”的状态起步。",
    },
    "random": {
        "title": "随机搜索 (random)", "short": "均匀随机采样，基线对照",
        "how": "在边界内均匀随机取点评测，只保留最优。",
        "when": "基线对照、烟雾测试、或目标噪声极大时的稳妥基线。",
        "pro": "永不被目标形状欺骗；实现最简。",
        "con": "样本效率最低，不适合昂贵仿真。",
        "hist": "v05：历史最优直接继承，不再丢。",
    },
    "grid": {
        "title": "网格搜索 (grid)", "short": "逐维等分扫描，可复现基线",
        "how": "把每维等分为若干档遍历组合，顺序固定、完全可复现。",
        "when": "变量少(≤3)且需要可复现的全面扫描；或摸底看响应面。",
        "pro": "完全确定性；覆盖均匀。",
        "con": "维度灾难：变量一多组合数爆炸。",
        "hist": "v05：历史最优继承；网格仍按固定顺序继续。",
    },
}

def help_text():
    """Full Chinese help text covering every registered strategy."""
    lines = [
        "算法说明 (v05)",
        "",
        "一、如何选择",
        "  变量少且平滑 → simplex; 变量多/多峰 → global 或 de;",
        "  预算≥50 且单目标 → lshade; 变量强耦合(如 w-s 折中) → cmaes(_ipop);",
        "  仿真极贵、想省次数 → tpe; 多目标 → nsga2 / weighted / worstspec;",
        "  拿不准选哪个 → portfolio 让它自己竞跑。",
        "",
        "二、算法如何参考历史仿真结果 (v05 新机制)",
        "  1) 精确续跑: 输出目录里的 snapshot+events 可逐位还原算法状态",
        "     (种群/RNG/模型), 续跑与不间断跑完全等价。",
        "  2) 历史热启动: 没有快照可还原时, 所有历史成功评测(变量+目标值)",
        "     会被注入算法内部 —— TPE 直接进核密度历史建模; DE/L-SHADE/",
        "     NSGA-II 填入种群槽位且免重仿; CMA-ES 均值移到历史最优并按",
        "     历史散布定初始步长; 单纯形用历史最优點重建顶点; portfolio",
        "     每条成员线都接收全部历史。",
        "  3) 预热布点: settings.init_points 指定 CSV(每行一个变量值组合),",
        "     这些点会作为第一批评测被真实仿真(与历史注入不同, 需要仿真)。",
        "  4) 预算一致: 历史点计入评测数, 预算感知机制(LPSR/UCB 配额)按",
        "     真实花费分配剩余预算。",
        "",
        "三、各算法详情",
        "",
    ]
    for key in sorted(DESCRIPTIONS):
        d = DESCRIPTIONS[key]
        lines.append("* %s  [%s]" % (d["title"], key))
        lines.append("  一句话: %s" % d["short"])
        lines.append("  原理:   %s" % d["how"])
        lines.append("  适用:   %s" % d["when"])
        lines.append("  优势:   %s" % d["pro"])
        lines.append("  局限:   %s" % d["con"])
        lines.append("  历史:   %s" % d["hist"])
        lines.append("")
    return "\n".join(lines)
