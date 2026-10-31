# -*- coding: utf-8 -*-
"""lri.runner — GeomRunner、停止控制与 EMX 设置。"""
from __future__ import print_function

import json
import os
import re
import shutil
import threading
import time

from .common import (
    open, _HERE, DEF_FREQ, LibrfError, logv, note_created, backup_file, _assert_writable,
    atomic_write_json, read_json)
from .tech import (
    _deep_merge, build_tech)
from .tools import (
    find_tools)
from .emx import (
    parse_freq_spec, build_emx_cmd)
from .snp import (
    write_snp, _interp_at)
from .device import (
    geom_key, make_layout_emx)
from .layout import (
    lri_norm_geom)

_STOP_REQUESTED = threading.Event()

_ACTIVE_RUNNERS = set()

_ACTIVE_LOCK = threading.Lock()

def stop_all():
    _STOP_REQUESTED.set()
    with _ACTIVE_LOCK:
        runners = list(_ACTIVE_RUNNERS)
    for r in runners:
        try:
            r.terminate()
        except Exception:
            pass
    _STOP_REQUESTED.clear()

class GeomRunner(object):
    def __init__(self, cfg, delegate, mock=None):
        self.cfg = cfg
        self.delegate = delegate
        le = cfg.get("layout_em") or {}
        self.le = le
        self.tech = build_tech(
            techlib=cfg.get("techlib"),
            techlib_dir=cfg.get("techlib_dir"),
            layermap=cfg.get("layermap"),
            proc=cfg.get("proc"),
            cds_lib=cfg.get("cds_lib"))
        self.geom_names = list(le.get("geom_vars") or [])
        self.fixed = dict(le.get("fixed_geom") or {})
        self.type = le.get("type") or "ind"
        self.cell = le.get("cell") or ("opt_%s" % self.type)
        self.work = le.get("work")
        self.lib = le.get("lib")
        self.views_mode = le.get("views_mode") or "none"
        self.geom_outputs = bool(le.get("geom_outputs", True))
        if mock is None:
            mock = le.get("mock") or type(delegate).__name__ == "MockRunner"
        self.mock = bool(mock)
        self.snp_path = le.get("snp")
        if not self.snp_path:
            work = os.path.expanduser(self.work or "work")
            self.snp_path = os.path.join(work, "snps", "%s.s2p" % self.cell)
        self.snp_path = os.path.abspath(os.path.expanduser(self.snp_path))
        if os.path.exists(self.snp_path):
            _assert_writable(self.snp_path, "sNp 文件")
        self.emx_opts = dict(le.get("emx") or {})
        self.verbose = bool(le.get("verbose"))
        self.cache = {}
        self.cache_path = os.path.join( os.path.expanduser(self.work or "work"), ".geom_cache.json")
        self._load_cache()
        self._stopped = False
    def _load_cache(self):
        try:
            data = read_json(self.cache_path)
            if isinstance(data, dict):
                self.cache = data
        except Exception:
            self.cache = {}
    def _save_cache(self):
        try:
            atomic_write_json(self.cache_path, self.cache)
        except Exception:
            pass
    def terminate(self):
        self._stopped = True
    def run(self, values, iter_dir):
        import time
        t0 = time.time()
        if _STOP_REQUESTED.is_set() or self._stopped:
            return _fail("已停止")
        geom = {}
        circ = {}
        for name, val in values.items():
            if name in self.geom_names:
                geom[name] = val
            else:
                circ[name] = val
        geom_all = dict(self.fixed)
        geom_all.update(geom)
        fpr = None
        try:
            fpr = self._ensure_geom(geom_all)
        except LibrfError as e:
            return _fail(str(e))
        except Exception as e:
            try:
                import traceback as _tb
                tb = _tb.format_exc()
            except Exception:
                tb = str(e)
            logp = os.path.join(os.path.expanduser(self.work or "work"), "geom_runner_error.log")
            try:
                with open(logp, "a") as f:
                    f.write("---- %s ----\n%s\n" % (time.time(), tb))
            except Exception:
                logp = "(日志写入失败)"
            return _fail("版图/EM 内部错误: %s: %s（traceback 见 %s）" % (type(e).__name__, e, logp))
        if _STOP_REQUESTED.is_set() or self._stopped:
            return _fail("已停止")
        try:
            self._patch_deck()
        except Exception as e:
            return _fail("网表 nport 路径改写失败: %s" % e)
        sub = self.delegate.run(circ, iter_dir)
        curves = dict(sub.curves or {})
        if fpr and self.geom_outputs and sub.status == "ok":
            self._merge_geom_curves(curves, fpr)
        sub.curves = curves
        sub.wall = time.time() - t0
        return sub
    def _patch_deck(self):
        delegate = self.delegate
        load = getattr(delegate, "_load_deck", None)
        if load is None:
            return
        deck = getattr(delegate, "deck", None)
        if deck is None:
            try:
                deck = load()
            except Exception:
                return
        if not deck:
            return
        text, params = deck
        snp = self.snp_path.replace("\\", "/")
        target = re.compile( r'((?:file|dataFile)\s*=\s*")([^"]+\.s\d+p)(")', re.I)
        def _sub(m):
            cur = m.group(2).replace("\\", "/")
            if cur == snp:
                return m.group(0)
            return m.group(1) + snp + m.group(3)
        lines = text.splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            if "nport" in line.lower():
                new_line, n = target.subn(_sub, line)
                if n and new_line != line:
                    lines[i] = new_line
                    changed = True
        if changed:
            delegate.deck = ("".join(lines), params)
    def _ensure_geom(self, geom):
        if self.type == "ind":
            geom = lri_norm_geom(geom)
        key = geom_key(self.type, geom, {"freq": self.emx_opts.get("freq"),
                        "edge": self.emx_opts.get("edge"), "3d": self.emx_opts.get("3d"),
                        "mode": self.emx_opts.get("mode")})
        if self.mock:
            return self._mock_fingerprint(geom)
        hit = self.cache.get(key)
        if hit and hit.get("snp") and os.path.isfile(hit["snp"]):
            self._publish_snp(hit["snp"])
            return hit.get("fingerprint")
        result = make_layout_emx(
            self.type, geom, cell=self.cell, work=self.work, ws=self.le.get("ws"),
            lib=self.lib, freq=self.emx_opts.get("freq") or DEF_FREQ,
            emx_opts=self.emx_opts, views_mode=self.views_mode,
            snp_out=self.snp_path, no_views=True, verbose=self.verbose,
            tech=self.tech,
            on_step=lambda m: logv(m, self.verbose))
        try:
            import hashlib as _hashlib
            key_id = _hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        except Exception:
            key_id = "%08x" % (abs(hash(key)) % (10 ** 8))
        snp_saved = os.path.join(os.path.dirname(self.snp_path), "%s_%s.s%dp" % ( self.cell, key_id,
                                     result.get("kind") in ("ct", "xfmr") and 4 or 2))
        try:
            _assert_writable(snp_saved, "缓存 sNp")
            shutil.copyfile(result["snp"], snp_saved)
            note_created(snp_saved)
        except IOError:
            snp_saved = result["snp"]
        self.cache[key] = {"snp": snp_saved, "fingerprint": result.get("fingerprint"),
                           "ports": result.get("ports"), "kind": result.get("kind"),
                           "bbox": result.get("bbox")}
        self._save_cache()
        self._publish_snp(result["snp"])
        return result.get("fingerprint")
    def _mock_fingerprint(self, geom):
        dout = float(geom.get("dout", 150) or 150)
        n = float(geom.get("n", 2.5) or 2.5)
        l0 = 1.2e-9 * (dout / 150.0) ** 1.6 * (n / 2.5) ** 1.8
        srf = 45e9 / max(0.5, (n / 2.5)) * (150.0 / max(10.0, dout)) ** 0.5
        q0 = 8.0 + 2.0 * (dout / 150.0)
        fpr = {"L0": l0, "SRF": srf, "Q": {"1e9": q0},
               "Lcurve": [[1e9, l0], [5e9, l0 * 0.97], [10e9, l0 * 0.9]],
               "Qcurve": [[1e9, q0], [5e9, q0 * 1.2], [10e9, q0 * 1.1]]}
        try:
            d = os.path.dirname(self.snp_path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            if not os.path.isfile(self.snp_path):
                write_snp(self.snp_path, [1e9, 5e9, 10e9], [[[0.1j, 0.9], [0.9, 0.1j]],
                           [[0.2j, 0.8], [0.8, 0.2j]], [[0.3j, 0.7], [0.7, 0.3j]]])
        except Exception:
            pass
        return fpr
    def _publish_snp(self, src):
        if os.path.abspath(src) == self.snp_path:
            return
        _assert_writable(self.snp_path, "sNp 文件")
        try:
            d = os.path.dirname(self.snp_path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            shutil.copyfile(src, self.snp_path)
            note_created(self.snp_path)
        except (IOError, OSError) as e:
            raise LibrfError("无法刷新 sNp 到 %s: %s" % (self.snp_path, e))
    def _merge_geom_curves(self, curves, fpr):
        if not fpr:
            return
        def _scalar(name, val):
            if val is not None:
                curves[name] = ([0.0], [float(val)])
        _scalar("L0", fpr.get("L0"))
        _scalar("SRF", fpr.get("SRF"))
        q = fpr.get("Q") or {}
        if q:
            f0 = self.emx_opts.get("f0")
            if f0:
                try:
                    _scalar("Q@f0", _interp_at( [float(k) for k in q.keys()],
                        [float(v) for v in q.values()], float(f0)))
                except Exception:
                    pass
            if "Q@f0" not in curves:
                first = sorted(q.keys())
                if first:
                    _scalar("Q@f0", q[first[0]])
        lc = fpr.get("Lcurve") or []
        if lc:
            curves["L(f)"] = ([p[0] for p in lc], [p[1] for p in lc])
        qc = fpr.get("Qcurve") or []
        if qc:
            curves["Q(f)"] = ([p[0] for p in qc], [p[1] for p in qc])

class _FallbackSimResult(object):
    def __init__(self, curves=None, status="fail", message="", raw_path=None,
                 timing=None, wall=0.0):
        self.curves = curves or {}
        self.status = status
        self.message = message
        self.raw_path = raw_path
        self.timing = timing or {}
        self.wall = wall

def _fail(msg):
    try:
        from optimizer import SimResult
    except Exception:
        SimResult = _FallbackSimResult
    return SimResult(status="fail", message=msg)

def default_emx_settings():
    return { "freq": {"mode": "list", "spec": DEF_FREQ, "discrete": []}, "accuracy": {
            "edge_width": 0.5, "3d": "*", "sidewalls": "*",
            "via_sidewalls": "-*", "via_inductance": "-*", "via_separation": 0.5, "thickness": None,
            "max_splits": None, "via_edge_factor": 1, "internal_units": None, }, "ports": {
            "label_snap": 4, "label_depth": 0, "cadence_pins": None,
            "modes": {}, "internal": None, "collapse": None, }, "solver": {
            "temperature": None, "corner": None, "parallel": None,
            "simultaneous_frequencies": None, "physics": "full", },
        "output": {"print_currents": False, "print_mesh": False, "s_impedance": 50}, }

def load_emx_settings(path=None):
    path = path or os.path.join(_HERE, "librf_emx_settings.json")
    raw = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            raw = d
    except (IOError, OSError, ValueError):
        pass
    return _deep_merge(default_emx_settings(), raw)

def save_emx_settings(settings, path=None):
    path = path or os.path.join(_HERE, "librf_emx_settings.json")
    try:
        if os.path.isfile(path):
            backup_file(path)
        atomic_write_json(path, settings, backup=False)
        return True, path
    except (IOError, OSError, LibrfError) as e:
        return False, str(e)

def emx_settings_to_opts(st, freq_override=None):
    st = _deep_merge(default_emx_settings(), st or {})
    acc, por = st["accuracy"], st["ports"]
    sol, out = st["solver"], st["output"]
    extra = []
    for key, flag in (("sidewalls", "--sidewalls"), ("via_sidewalls", "--via-sidewalls"),
                      ("via_inductance", "--via-inductance")):
        v = acc.get(key)
        if v:
            extra.append("%s=%s" % (flag, v))
    for key, flag, conv in (("via_separation", "--via-separation=%g", float),
                            ("thickness", "--thickness=%g", float),
                            ("max_splits", "--max-splits=%d", int)):
        if acc.get(key) is not None:
            extra.append(flag % conv(acc[key]))
    if acc.get("via_edge_factor") not in (None, 1):
        extra.append("--via-edge-factor=%g" % float(acc["via_edge_factor"]))
    if acc.get("internal_units"):
        extra.append("--internal-units=%s" % acc["internal_units"])
    if por.get("label_snap") is not None:
        extra.append("--label-snap-distance=%g" % float(por["label_snap"]))
    if por.get("label_depth"):
        extra.append("--label-depth=%g" % float(por["label_depth"]))
    if por.get("cadence_pins"):
        extra.append("--cadence-pins")
    if por.get("internal"):
        extra.append("--internal=%s" % por["internal"])
    if por.get("collapse"):
        for tok in str(por["collapse"]).split():
            extra.append("--collapse%s" % tok)
    for pair, mode in (por.get("modes") or {}).items():
        if mode == "diff":
            extra.append("--mode=%s" % pair)
    if sol.get("temperature") is not None:
        extra.append("--temperature=%g" % float(sol["temperature"]))
    if sol.get("corner"):
        extra.append("--corner=%s" % sol["corner"])
    if sol.get("parallel"):
        extra.append("--parallel=%d" % int(sol["parallel"]))
    if sol.get("simultaneous_frequencies"):
        extra.append("--simultaneous-frequencies=%d" % int(sol["simultaneous_frequencies"]))
    phys = sol.get("physics") or "full"
    if phys == "ind":
        extra.append("-L")
    elif phys == "cap":
        extra.append("-C")
    elif phys == "res":
        extra.append("-R")
    if out.get("print_currents"):
        extra.append("--print-currents")
    if out.get("print_mesh"):
        extra.append("--print-mesh")
    opts = { "freq": freq_override or st["freq"].get("spec") or DEF_FREQ,
        "edge": acc.get("edge_width"), "3d": acc.get("3d"), "extra": extra, }
    if out.get("s_impedance"):
        opts["s_impedance"] = out["s_impedance"]
    modes = por.get("modes") or {}
    for pair, mode in modes.items():
        if mode == "diff":
            opts["mode"] = pair
            break
    return opts

def emx_cmd_preview(st, gds="/path/cell.gds", struct="cell", proc=None, out_snp="/path/cell.s2p"):
    tools = find_tools()
    opts = emx_settings_to_opts(st)
    freqs = parse_freq_spec(opts.get("freq"))
    proc = proc or tools["proc"]["path"] or "/path/to/process.proc"
    emx_bin = (tools.get("emx") or {}).get("path") or "emx"
    cmd = build_emx_cmd(emx_bin, gds, struct, proc, freqs, out_snp,
                        edge=opts.get("edge"), three_d=opts.get("3d"),
                        mode=opts.get("mode"), ports=None, extra=opts.get("extra"),
                        s_impedance=opts.get("s_impedance"))
    return " ".join(cmd)
