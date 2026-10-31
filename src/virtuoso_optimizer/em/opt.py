# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import math
import os
import sys
import time

try:
    from .. import librf_opt as _lo
except Exception:
    _lo = None
try:
    from .. import algorithm as _alg
except Exception:
    _alg = None
try:
    from .bridge import (EmLibrary as _RealLibrary,
                           DEVICE_STAGES as _DEV_STAGES,
                           stage_report as _stage_report,
                           clip_to_region as _clip_to_region)
except Exception:
    _RealLibrary = None
    _DEV_STAGES = {
        "xfmr": (("Lp",), ("Lp", "Ls"), ("Lp", "Ls", "k")),
        "cpl": (("Ze",), ("Ze", "Zo"), ("Ze", "Zo", "theta_e", "theta_o")),
        "ind": (("L0",),), "ct": (("L0",),),
        "tl": (("Z0",), ("Z0", "theta")),
    }
    _stage_report = None
    _clip_to_region = None

__all__ = ["EmOptimizer", "build_request", "distance_of", "error_kernel", "RECORD_SCHEMA"]

RECORD_SCHEMA = "em_record/1"

DEV_BUDGET = {"tl": 10, "cpl": 15, "ind": 25, "ct": 25, "xfmr": 25}
DEV_ALGO = {"tl": "simplex", "cpl": "de", "ind": "lshade", "ct": "lshade", "xfmr": "cmaes"}
DEV_TIME_CAP = {"tl": 1800, "cpl": 2700, "ind": 1800, "ct": 1800, "xfmr": 2700}

GEOM_BOUNDS_DEFAULT = {
    "ind": {"n": (1.5, 5.5), "w": (2.5, 25.0), "s": (1.5, 8.0),
            "dout": (50.0, 300.0), "ex": (0.0, 80.0)},
    "ct": {"n": (1.5, 5.5), "w": (2.5, 25.0), "s": (1.5, 8.0),
           "dout": (50.0, 300.0), "ex": (0.0, 80.0)},
    "tl": {"w": (2.5, 25.0), "len": (50.0, 2000.0)},
    "cpl": {"w": (2.5, 25.0), "s": (1.5, 20.0), "len": (50.0, 1500.0)},
    "xfmr": {"np": (1.5, 4.5), "ns": (1.5, 4.5), "w": (2.5, 25.0),
             "s": (1.5, 8.0), "dout": (50.0, 300.0), "gap": (2.0, 30.0)},
}
_ANG_KEYS = ("theta", "theta_e", "theta_o")
_META_KEYS = ("f0", "band", "kind", "ports", "band_hi")
_MISSING_PER = {"delta": None, "normalized": 99.0, "in_tol": False}

def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)

def _tol_eff(tol, key, t):
    if key in _ANG_KEYS:
        if tol is None:
            return 5.0
        if isinstance(tol, str) and tol.strip().endswith("%"):
            return float(tol.strip()[:-1]) / 100.0 * 90.0
        return float(tol)
    if tol is None:
        rel = 0.05
    elif isinstance(tol, str):
        ts = tol.strip()
        rel = float(ts[:-1]) / 100.0 if ts.endswith("%") else float(ts)
    else:
        rel = float(tol)
    return rel * max(abs(float(t)), 1e-30)

def error_kernel(actual, target, tol, key):
    if actual is None or target is None:
        return 1.0
    try:
        a = float(actual)
        t = float(target)
    except (TypeError, ValueError):
        return 1.0
    tol_eff = _tol_eff(tol, key, t)
    scale = max(1.0, abs(t)) if key in _ANG_KEYS else max(abs(t), 1e-30)
    raw = abs(a - t)
    if raw <= tol_eff:
        return 0.0
    return (raw - tol_eff) / max(tol_eff, 1e-3 * scale)

def _metric_val(fp, key, f0=None):
    av = (fp or {}).get(key)
    if av is None and key == "Q@f0":
        av = _q_at_f0(fp, f0)
    return av

def distance_of(actual_fp, target, tolerances=None, f0=None):
    tolerances = tolerances or {}
    f0 = f0 if f0 is not None else (target or {}).get("f0")
    per = {}
    dmax = 0.0
    any_m = False
    for key, tv in (target or {}).items():
        if key.startswith("_") or key in _META_KEYS:
            continue
        if tv is None or isinstance(tv, bool):
            continue
        try:
            tv = float(tv)
        except (TypeError, ValueError):
            continue
        any_m = True
        av = _metric_val(actual_fp, key, f0)
        if av is None:
            per[key] = dict(_MISSING_PER)
            dmax = max(dmax, 99.0)
            continue
        tol_eff = _tol_eff(tolerances.get(key), key, tv)
        delta = abs(float(av) - float(tv))
        norm = delta / max(tol_eff, 1e-12 if key in _ANG_KEYS else 1e-30)
        per[key] = {"delta": delta, "normalized": norm, "in_tol": norm <= 1.0}
        dmax = max(dmax, norm)
    if not any_m:
        return float("inf"), {}
    return dmax, per

def _q_at_f0(fp, f0):
    if not fp or f0 is None:
        return None
    qc = fp.get("Qcurve") or []
    if not qc:
        return None
    try:
        f0 = float(f0)
    except (TypeError, ValueError):
        return None
    if f0 <= qc[0][0]:
        return qc[0][1]
    if f0 >= qc[-1][0]:
        return qc[-1][1]
    for i in range(1, len(qc)):
        if qc[i][0] >= f0:
            f1, f2 = qc[i - 1][0], qc[i][0]
            v1, v2 = qc[i - 1][1], qc[i][1]
            if f2 == f1:
                return v2
            if v1 is None or v2 is None:
                return v1 or v2
            return v1 + (v2 - v1) * (f0 - f1) / (f2 - f1)
    return qc[-1][1]

def J_of(actual_fp, target, tolerances=None, weights=None, f0=None):
    tolerances = tolerances or {}
    weights = weights or {}
    f0 = f0 if f0 is not None else (target or {}).get("f0")
    total = 0.0
    for key, tv in (target or {}).items():
        if key.startswith("_") or key in _META_KEYS:
            continue
        if tv is None or isinstance(tv, bool):
            continue
        try:
            tv = float(tv)
        except (TypeError, ValueError):
            continue
        av = _metric_val(actual_fp, key, f0)
        w = float(weights.get(key, 1.0) or 1.0)
        total += w * error_kernel(av, tv, tolerances.get(key), key)
    return total

def _scalar_projection(fp):
    out = {}
    for k, v in (fp or {}).items():
        if k.startswith("_") or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v)
    return out

def _penalty_tell(algo, x, best_J):
    if algo is None:
        return
    try:
        algo.tell([x], [max(1e3, best_J * 2.0 if best_J < 1e29 else 1e3)])
    except Exception:
        pass

class _MockLibrary(object):
    def __init__(self, root=None):
        self.root = os.path.abspath(os.path.expanduser(
            root or os.path.join(os.getcwd(), "work", "em_lib")))
        self.path = os.path.join(self.root, "em_lib.jsonl")
        self._records = []
        self._load()

    def _load(self):
        self._records = []
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._records.append(json.loads(line))
                    except ValueError:
                        continue
        except IOError:
            pass

    def _append(self, rec):
        _ensure_dir(self.path)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def put(self, record):
        if not record:
            return None
        rec = dict(record)
        rec.setdefault("schema_version", 1)
        rec.setdefault("schema", RECORD_SCHEMA)
        rid = rec.get("snp_sha1") or rec.get("geom_hash") or ("r%d" % int(time.time() * 1000))
        rec["record_id"] = rid
        for old in self._records:
            if old.get("snp_sha1") and old.get("snp_sha1") == rec.get("snp_sha1"):
                return old.get("record_id")
        self._records.append(rec)
        self._append(rec)
        return rid

    def get_by_key(self, grid_key):
        for rec in self._records:
            if rec.get("grid_key") == grid_key:
                return rec
        return None

    def query(self, device_type, target_fingerprint, tolerances=None,
              tech=None, emx_cfg_hash=None, constraints=None, f0=None,
              grid_spec=None):
        try:
            hits = self.nearest(device_type, target_fingerprint, k=5,
                                tech=tech, emx_cfg_hash=emx_cfg_hash)
        except Exception as e:
            return {"status": "error", "record": None,
                    "distance": None, "warns": ["query error: %s" % e]}
        if not hits:
            return {"status": "miss", "record": None, "distance": None, "warns": []}
        best = hits[0]
        d = best.get("distance", 99)
        if constraints:
            fp = best.get("actual_fingerprint") or {}
            for ck, cv in constraints.items():
                if ck.endswith("_min") or ck.startswith("min_"):
                    name = ck.replace("_min", "").replace("min_", "")
                    av = fp.get(name)
                    if av is not None and float(av) < float(cv):
                        return {"status": "miss", "record": best,
                                "distance": d, "warns": ["constraint fail"]}
        if d <= 1.0:
            status = ("hit_grid" if best.get("source") == "catalog" else "hit_nearest")
        else:
            status = "miss"
        return {"status": status, "record": best, "distance": d,
                "delta_vs_request": best.get("delta_vs_request"),
                "warns": []}

    def nearest(self, device_type, target_fingerprint, k=5, tech=None,
                emx_cfg_hash=None, tolerances=None, f0=None):
        target = dict(target_fingerprint or {})
        if f0 is not None:
            target.setdefault("f0", f0)
        out = []
        for rec in self._records:
            if rec.get("usable") is False:
                continue
            if device_type and rec.get("device_type") not in (None, device_type):
                continue
            if tech and rec.get("tech") and rec.get("tech") != tech:
                continue
            if (emx_cfg_hash and rec.get("emx_cfg_hash")
                    and rec.get("emx_cfg_hash") != emx_cfg_hash):
                continue
            d, _per = distance_of(
                rec.get("actual_fingerprint") or {}, target,
                tolerances or rec.get("tolerances") or {})
            item = dict(rec)
            item["distance"] = d
            out.append(item)
        out.sort(key=lambda r: r.get("distance", 99))
        return out[: max(1, int(k))]

def build_request(device_type, target, tolerances=None, f_op=None,
                  weights=None, geom_prior=None, fixed_geom=None,
                  budget=None, tech=None, mock=False, library=None,
                  verbose=False):
    if not f_op:
        raise ValueError("em_request: f_op 必填（C-O4，不猜）")
    dev = (device_type or "ind").lower()
    if dev not in DEV_BUDGET:
        raise ValueError("em_request: 未知 device_type=%r" % device_type)
    return {
        "schema": "em_request/1", "device_type": dev,
        "target": dict(target or {}), "tolerances": dict(tolerances or {}),
        "weight": dict(weights or {}), "f_op": float(f_op),
        "geom_prior": dict(geom_prior or {}),
        "fixed_geom": dict(fixed_geom or {}),
        "budget": dict(budget or {}), "tech": tech, "mock": bool(mock),
        "library": library, "verbose": bool(verbose),
    }

def _mock_fingerprint(device_type, geom, f_op):
    dev = (device_type or "ind").lower()
    g = geom or {}
    if dev in ("ind", "ct"):
        dout = float(g.get("dout", 150) or 150)
        n = float(g.get("n", 2.5) or 2.5)
        l0 = 587e-12 * (dout / 150.0) ** 1.6 * (n / 2.5) ** 1.8
        srf = 45e9 / max(0.5, n / 2.5) * (150.0 / max(10.0, dout)) ** 0.5
        q0 = 8.0 + 2.0 * (dout / 150.0)
        return {"kind": dev, "L0": l0, "SRF": srf, "Q": {"%.6e" % f_op: q0},
                "Q@f0": q0,
                "Lcurve": [[1e9, l0], [5e9, l0 * 0.97], [10e9, l0 * 0.9]],
                "Qcurve": [[1e9, q0], [5e9, q0 * 1.2], [10e9, q0 * 1.1]],
                "Lunit": "H"}
    if dev == "tl":
        w = float(g.get("w", 10) or 10)
        ln = float(g.get("len", 100) or 100)
        z0 = 50.0 * (10.0 / max(2.5, w)) ** 0.5
        theta = 360.0 * (ln / 1e6) * float(f_op) / 3e8 * 2.5
        return {"kind": "tl", "Z0": z0, "theta": theta, "Z0unit": "ohm",
                "theta_unit": "deg", "IL": 0.1}
    if dev == "cpl":
        w = float(g.get("w", 10) or 10)
        s = float(g.get("s", 3) or 3)
        ln = float(g.get("len", 200) or 200)
        th = 360.0 * (ln / 1e6) * float(f_op) / 3e8 * 2.5
        return {"kind": "cpl", "Ze": 40.0 + 2.5 * w + 1.5 * s,
                "Zo": 30.0 + 1.2 * w, "theta_e": th * 0.95,
                "theta_o": th * 1.05, "Zeunit": "ohm", "theta_unit": "deg"}
    if dev == "xfmr":
        np_ = float(g.get("np", 2) or 2)
        ns = float(g.get("ns", 2) or 2)
        dout = float(g.get("dout", 150) or 150)
        gap = float(g.get("gap", 5) or 5)
        lp = 0.8e-9 * (dout / 150.0) ** 1.5 * (np_ / 2.0) ** 1.6
        ls = 0.8e-9 * (dout / 150.0) ** 1.5 * (ns / 2.0) ** 1.6
        k = 0.8 * math.exp(-gap / 40.0) * min(1.0, dout / 150.0)
        return {"kind": "xfmr", "Lp": lp, "Ls": ls, "k": k, "L0": lp, "Ls0": ls, "Lunit": "H"}
    return {}

class EmOptimizer(object):
    def __init__(self, library=None, work=None, make_em=None):
        if library is not None:
            self.library = library
        elif _RealLibrary is not None:
            try:
                self.library = _RealLibrary()
            except Exception:
                self.library = _MockLibrary()
        else:
            self.library = _MockLibrary()
        self.work = os.path.abspath(os.path.expanduser(work or os.path.join(os.getcwd(), "work")))
        self._make_em = make_em
        self._n_em = 0
        self._n_fail = 0
        self._cache = {}
        self._cache_path = os.path.join(self.work, ".em_opt_geom_cache.json")
    def _bounds(self, dev, geom_prior, fixed_geom):
        base = dict(GEOM_BOUNDS_DEFAULT.get(dev, GEOM_BOUNDS_DEFAULT["ind"]))
        keys = [k for k in base if k not in (fixed_geom or {})]
        bounds = [base[k] for k in keys]
        x0 = []
        for k in keys:
            if geom_prior and k in geom_prior:
                x0.append(float(geom_prior[k]))
            else:
                lo, hi = base[k]
                x0.append(0.5 * (lo + hi))
        return keys, bounds, x0

    def _to_geom(self, keys, x, fixed_geom):
        g = dict(fixed_geom or {})
        for k, v in zip(keys, x):
            if k == "n" or k in ("np", "ns"):
                g[k] = round(float(v) * 2.0) / 2.0
            else:
                g[k] = round(float(v), 2)
        return g

    def _geom_hash(self, geom):
        try:
            if _lo is not None:
                return _lo.geom_key("x", geom)
        except Exception:
            pass
        return json.dumps(geom, sort_keys=True)

    def _eval_geom(self, req, geom):
        dev = req["device_type"]
        ghash = self._geom_hash(geom)
        f_op = req.get("f_op")
        try:
            f0tag = "%.17g" % float(f_op)
        except (TypeError, ValueError):
            f0tag = str(f_op)
        key = "%s|%s|%s|%s" % (dev, f0tag, ghash,
                               "mock" if req.get("mock") else "real")
        if key in self._cache:
            return self._cache[key], "ok", "cache"
        if os.path.isfile(self._cache_path):
            try:
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                if key in disk:
                    self._cache[key] = disk[key]
                    return disk[key], "ok", "cache"
            except Exception:
                pass
        if req.get("mock") or self._make_em is not None:
            try:
                if self._make_em is not None:
                    fp = self._make_em(dev, geom, req["f_op"])
                else:
                    fp = _mock_fingerprint(dev, geom, req["f_op"])
                self._n_em += 1
                self._cache[key] = fp
                self._save_cache()
                return fp, "ok", "mock"
            except Exception as e:
                self._n_fail += 1
                return None, "dnc", "mock 失败: %s" % e
        if _lo is None:
            return None, "infra", "librf_opt 不可用"
        try:
            try:
                import hashlib as _hl
                cell_tag = _hl.sha1(ghash.encode("utf-8")).hexdigest()[:8]
            except Exception:
                cell_tag = "%08x" % (abs(hash(ghash)) % (10 ** 8))
            out = _lo.make_layout_emx(
                dev if dev != "ct" else "ind", geom,
                cell="emopt_%s_%s" % (dev, cell_tag), work=self.work,
                freq=req.get("freq") or getattr(_lo, "DEF_FREQ",
                                                "1e9:20e9:11"),
                views_mode="none", no_views=True,
                verbose=req.get("verbose", False),
                emx_timeout=req.get("emx_timeout") or 120,
                skill_timeout=req.get("skill_timeout") or 60)
            fp = out.get("fingerprint") or {}
            snp = out.get("snp")
            if snp and req.get("f_op"):
                try:
                    fp = _lo.fingerprint_from_snp(
                        snp, out.get("kind") or dev,
                        ports=out.get("ports"),
                        f0=float(req["f_op"])) or fp
                except Exception:
                    pass
            if snp:
                fp = dict(fp)
                fp["_snp"] = snp
            self._n_em += 1
            self._cache[key] = fp
            self._save_cache()
            return fp, "ok", ""
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            self._n_fail += 1
            if any(h in low for h in ("lock", "占用", "cdslck", "timeout",
                                      "超时", "找不到", "not found", "infra")):
                return None, "infra", msg
            return None, "dnc", msg

    def _save_cache(self):
        try:
            _ensure_dir(self._cache_path)
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
        except Exception:
            pass
    def warm_start_geom(self, req):
        dev = req["device_type"]
        lib = req.get("library") or self.library
        try:
            warm_tol = dict(req.get("tolerances") or {})
            warm_tol["Q_min"] = None
            warm_tol["q_min"] = None
            hits = lib.nearest(dev, req.get("target") or {}, k=5,
                               tech=req.get("tech"), tolerances=warm_tol)
        except Exception:
            hits = []
        if not hits:
            return None, "center"
        geom = hits[0].get("geom") or hits[0].get("geom_json") or {}
        if isinstance(geom, str):
            try:
                geom = json.loads(geom)
            except ValueError:
                geom = {}
        return (dict(geom), "lib_kNN") if geom else (None, "center")
    def solve(self, request):
        req = dict(request or {})
        dev = (req.get("device_type") or "ind").lower()
        req["device_type"] = dev
        target = dict(req.get("target") or {})
        tol = dict(req.get("tolerances") or {})
        wts = dict(req.get("weight") or {})
        f_op = req.get("f_op")
        if not f_op:
            return self._result("error", None, None, req, reason="f_op 缺失")
        t0 = time.time()
        clipped_events = []
        try:
            lib = req.get("library") or self.library
            region = lib.achievable_region(dev) if hasattr(lib, "achievable_region") else None
            if region and _clip_to_region is not None:
                target, clipped_events = _clip_to_region(target, region, device_type=dev)
                if clipped_events:
                    req["target"] = target
        except Exception:
            pass
        budget = dict(req.get("budget") or {})
        max_em = int(budget.get("max_em") or DEV_BUDGET.get(dev, 20))
        time_cap = float(budget.get("time_cap_s") or DEV_TIME_CAP.get(dev, 1800))
        fixed_geom = dict(req.get("fixed_geom") or {})
        keys, bounds, x0 = self._bounds(dev, req.get("geom_prior"), fixed_geom)
        warm_geom, origin = self.warm_start_geom(req)
        if warm_geom:
            for i, k in enumerate(keys):
                if k in warm_geom:
                    try:
                        x0[i] = float(warm_geom[k])
                    except (TypeError, ValueError):
                        pass
        stage_seq = _DEV_STAGES.get(dev) or (("L0",),)
        n_stage = len(stage_seq)
        best_fp = best_geom = None
        best_J, best_d = 1e300, 1e300
        stage_rows = []
        x_stage = list(x0)
        for si in range(n_stage):
            keep = set(stage_seq[si])
            sub_t = {k: v for k, v in target.items() if k in keep}
            sub_tol = {k: tol.get(k) for k in keep}
            sub_w = {k: wts.get(k, 1.0) for k in keep}
            if not sub_t:
                sub_t = dict(target)
                sub_tol = dict(tol)
                sub_w = dict(wts)
            if n_stage == 1:
                b_s = max_em
            else:
                b_s = max(2, max_em // n_stage + (2 if si == n_stage - 1 else 0))
            fp, geom, J, d, st = self._run_search_stage(
                req, sub_t, sub_tol, sub_w, keys, bounds, x_stage,
                b_s, time_cap, t0)
            full_d = full_J = None
            if fp is not None:
                full_d, _full_per = distance_of(fp, target, tol, f0=f_op)
                full_J = J_of(fp, target, tol, wts, f0=f_op)
                if (best_fp is None or full_d < best_d - 1e-12
                        or (abs(full_d - best_d) <= 1e-12
                            and full_J < best_J)):
                    best_J, best_fp, best_geom, best_d = (
                        full_J, fp, geom, full_d)
            stage_rows.append({"stage": si, "keys": list(keep),
                               "distance": d, "J": J, "status": st,
                               "full_distance": full_d, "full_J": full_J})
            if geom:
                x_stage = [float(geom.get(k, x_stage[i])) for i, k in enumerate(keys)]
            if full_d is not None and full_d <= 1.0 and si == n_stage - 1:
                break
        stage_in_tol = {}
        if _stage_report is not None and best_fp:
            try:
                stage_in_tol = _stage_report(best_fp, target, tol, f0=f_op, device_type=dev)
            except Exception:
                pass
        if best_fp is None:
            return self._result("miss", None, None, req,
                                reason="无可行点", J=best_J, d=best_d,
                                origin=origin, t0=t0)
        status = "hit_grid" if best_d <= 1.0 else "best_effort"
        rec = self._result(status, best_fp, best_geom, req,
                           reason="" if best_d <= 1 else "预算尽未进容差带",
                           J=best_J, d=best_d, per=None,
                           origin=origin, t0=t0)
        if clipped_events:
            rec.setdefault("warns", []).append("clipped: %s" % "; ".join(clipped_events))
            rec["clipped"] = clipped_events
        rec["stage_in_tol"] = stage_in_tol
        rec["stages"] = stage_rows
        return rec

    def _run_search_stage(self, req, target, tol, wts, keys, bounds, x0, max_em, time_cap, t0):
        dev = req["device_type"]
        fixed_geom = dict(req.get("fixed_geom") or {})
        algo_name = (req.get("budget") or {}).get("strategy") or DEV_ALGO.get(dev, "lshade")
        algo = None
        if _alg is not None:
            try:
                algo = _alg.create(algo_name, keys, bounds, x0=list(x0))
            except Exception:
                algo = None
        best_fp = best_geom = None
        best_J = best_d = 1e300
        n_eval = 0
        patience = max(4, max_em // 3)
        min_evals = max(2, max_em // 3)
        stall = 0
        infra_streak = 0
        n_attempt = 0
        xs = [list(x0)]
        if algo is None:
            for i in range(max(1, max_em - 1)):
                x = list(x0)
                j = i % len(x) if x else 0
                if x:
                    lo, hi = bounds[j]
                    span = hi - lo
                    x[j] = min(hi, max(lo, x[j] + (i + 1) * 0.03 * span))
                xs.append(x)
        iter_i = 0
        while (n_eval < max_em and n_attempt < max_em
               and (time.time() - t0) < time_cap):
            if algo is not None:
                try:
                    cand = algo.ask()
                    if not cand:
                        break
                    x = list(cand[0])
                except Exception:
                    break
            else:
                if iter_i >= len(xs):
                    break
                x = xs[iter_i]
            iter_i += 1
            geom = self._to_geom(keys, x, fixed_geom)
            fp, st, _msg = self._eval_geom(req, geom)
            if st == "infra":
                infra_streak += 1
                _penalty_tell(algo, x, best_J)
                if infra_streak >= 3:
                    return best_fp, best_geom, best_J, best_d, "infra_fail"
                continue
            infra_streak = 0
            n_attempt += 1
            if st != "ok" or fp is None:
                _penalty_tell(algo, x, best_J)
                continue
            J = J_of(fp, target, tol, wts, f0=req.get("f_op"))
            d, per = distance_of(fp, target, tol, f0=req.get("f_op"))
            rec = self._make_record(req, geom, fp, J, d, per, source="optimization", usable=True)
            try:
                (req.get("library") or self.library).put(rec)
            except Exception:
                pass
            try:
                if algo is not None:
                    algo.tell([x], [J])
            except Exception:
                pass
            if J < best_J:
                best_J, best_fp, best_geom, best_d = J, fp, geom, d
                stall = 0
            else:
                stall += 1
            n_eval += 1
            if d <= 1.0:
                break
            if n_eval >= min_evals and stall >= patience:
                break
        if best_fp is None:
            if infra_streak > 0:
                return None, None, best_J, best_d, "infra_fail"
            return None, None, best_J, best_d, "miss"
        return (best_fp, best_geom, best_J, best_d, "hit_grid" if best_d <= 1.0 else "best_effort")

    def _base_record(self, req, fp, geom, d, per):
        return {
            "schema": RECORD_SCHEMA,
            "schema_version": 1,
            "device_type": req.get("device_type"),
            "request_fingerprint": dict(req.get("target") or {}),
            "actual_fingerprint": dict(fp or {}),
            "delivered": _scalar_projection(fp),
            "geom": dict(geom or {}),
            "distance": d,
            "delta_vs_request": dict(per or {}),
        }

    def _make_record(self, req, geom, fp, J, d, per, source="optimization", usable=True):
        rec = self._base_record(req, fp, geom, d, per)
        rec.update({
            "geom_hash": self._geom_hash(geom),
            "tolerances": dict(req.get("tolerances") or {}),
            "grid_key": "%s:%s" % (req.get("device_type"),
                                   json.dumps(geom, sort_keys=True)),
            "source": source, "usable": usable, "tech": req.get("tech"),
            "em_meta": {"J": J, "f_op": req.get("f_op"), "n_em": self._n_em},
        })
        return rec

    def _result(self, status, fp, geom, req, reason="", J=None, d=None,
                per=None, origin=None, t0=None):
        wall = (time.time() - t0) if t0 else 0.0
        target = dict(req.get("target") or {})
        if fp is not None and per is None:
            d, per = distance_of(fp, target, dict(req.get("tolerances") or {}),
                                 f0=req.get("f_op"))
        weak = bool(d is not None and d > 10.0)
        rec = self._base_record(req, fp, geom, d, per)
        rec.update({
            "status": status, "reason": reason,
            "geom_hash": self._geom_hash(geom) if geom else None,
            "in_tol": {k: bool((per or {}).get(k, {}).get("in_tol"))
                       for k in target
                       if not k.startswith("_") and k not in _META_KEYS},
            "weak_hit": weak,
            "warns": ["weak_hit distance=%.2f" % (d or 0)] if weak else [],
            "snp": (fp or {}).get("snp") or (fp or {}).get("_snp"),
            "source": "optimization",
            "em_meta": {"iters": self._n_em,
                        "em_ok": self._n_em - self._n_fail,
                        "em_fail": self._n_fail, "wall_s": wall,
                        "origin": origin, "J": J, "distance": d},
        })
        return rec

def _selftest():
    fails = 0
    work = os.path.join(os.getcwd(), "work", "emopt_selftest")
    lib_root = os.path.join(work, "em_lib")
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    lib = _MockLibrary(root=lib_root)
    opt = EmOptimizer(library=lib, work=work)
    req = build_request("ind", {"L0": 587e-12, "SRF": 45e9, "Q@f0": 12.0},
                        tolerances={"L0": 0.03, "SRF": 0.25, "Q@f0": 0.2},
                        f_op=10e9, mock=True,
                        geom_prior={"n": 2.5, "w": 10, "s": 2.6,
                                    "dout": 150})
    rec = opt.solve(req)
    print("[selftest] ind status=%s L0=%s d=%s" % (
        rec.get("status"), (rec.get("actual_fingerprint") or {}).get("L0"),
        rec.get("distance")))
    if rec.get("status") not in ("hit_grid", "best_effort", "hit_nearest"):
        fails += 1
        print("  FAIL 无结果")
    d = rec.get("distance")
    if d is None or d > 50:
        fails += 1
        print("  FAIL distance=%s" % d)
    else:
        print("  ok distance=%.3f" % d)
    rec2 = opt.solve(req)
    print("[selftest] 二次 status=%s d=%s" % (rec2.get("status"), rec2.get("distance")))
    if (rec2.get("distance") or 99) > (d or 99) + 1e-9:
        print("  WARN 二次未改善（库命中预期）")
    if (rec2.get("em_meta") or {}).get("origin") != "lib_kNN":
        print("  WARN 二次未走库热启动")
    req_t = build_request("tl", {"Z0": 50.0, "theta": 90.0},
                          tolerances={"Z0": 0.05, "theta": 5.0},
                          f_op=2e9, mock=True,
                          geom_prior={"w": 10, "len": 200})
    rec_t = opt.solve(req_t)
    print("[selftest] tl status=%s Z0=%s θ=%s" % (
        rec_t.get("status"),
        (rec_t.get("actual_fingerprint") or {}).get("Z0"),
        (rec_t.get("actual_fingerprint") or {}).get("theta")))
    if rec_t.get("status") == "error":
        fails += 1
    def _chk(cond, label, ok_d, fail_d):
        if cond:
            print("  ok %s: %s" % (label, ok_d))
            return 0
        print("  FAIL %s: %s" % (label, fail_d))
        return 1

    tl_fp = rec_t.get("actual_fingerprint") or {}
    tl_d, tl_st, tl_th = (rec_t.get("distance"), rec_t.get("status"),
                          tl_fp.get("theta"))
    fails += _chk(tl_d is not None and tl_d > 1.0, "tl 全指标距离",
                  "d=%s" % tl_d, "d=%s 应 >1" % tl_d)
    fails += _chk((tl_st == "hit_grid") == (tl_d is not None and tl_d <= 1.0),
                  "tl status/距离一致", "status=%s d=%s" % (tl_st, tl_d),
                  "status=%s d=%s" % (tl_st, tl_d))
    fails += _chk(tl_th is not None and float(tl_th) > 1.2, "tl 选中 stage-1",
                  "theta=%s status=%s" % (tl_th, tl_st),
                  "theta=%s <=1.2（仍选 stage-0）" % tl_th)
    fails += _chk(tl_st != "hit_grid", "tl 不再伪报 hit_grid",
                  "status=%s" % tl_st, "status=hit_grid")
    q_fp, q_tg, q_tol = ({"Qcurve": [[1e9, 7.0], [2e10, 9.0]]},
                         {"Q@f0": 8.0}, {"Q@f0": 0.2})
    d_q, per_q = distance_of(q_fp, q_tg, q_tol, f0=1e10)
    d_q0, _ = distance_of(q_fp, q_tg, q_tol)
    j_q, j_q0 = J_of(q_fp, q_tg, q_tol, f0=1e10), J_of(q_fp, q_tg, q_tol)
    fails += _chk(d_q < 1.0 and per_q["Q@f0"]["in_tol"], "Q@f0 f0 接线",
                  "d=%.6f" % d_q, "d=%s" % d_q)
    fails += _chk(d_q0 == 99.0, "Q@f0 无 f0 回退 99", "d=99", "d=%s" % d_q0)
    fails += _chk(j_q == 0.0 and j_q0 == 1.0, "Q@f0 J f0 生效", "J=0/1",
                  "J=%s/%s" % (j_q, j_q0))
    def _q_make(dev, geom, fop):
        return {"kind": "ind", "L0": 5.87e-10,
                "Qcurve": [[1e9, 7.0], [2e10, 9.0]]}

    q_opt = EmOptimizer(library=_MockLibrary(root=os.path.join(work, "q_lib")),
                        work=os.path.join(work, "q_work"), make_em=_q_make)
    q_rec = q_opt.solve(build_request(
        "ind", {"Q@f0": 8.0}, tolerances={"Q@f0": 0.2}, f_op=1e10,
        geom_prior={"n": 2.5}, mock=True))
    q_rd = q_rec.get("distance")
    fails += _chk(q_rd is not None and q_rd < 1.0
                  and q_rec.get("status") == "hit_grid", "solve Q@f0 接线",
                  "d=%s status=%s" % (q_rd, q_rec.get("status")),
                  "d=%s status=%s" % (q_rd, q_rec.get("status")))
    meta_tg = {"L0": 1e-9, "kind": "ind", "ports": ["p1"], "f0": 1e9,
               "band": "b", "band_hi": 2e9}
    d_m, per_m = distance_of({"L0": 1e-9}, meta_tg, {"L0": 0.1})
    j_m = J_of({"L0": 1e-9}, meta_tg, {"L0": 0.1})
    bad_meta = [k for k in ("kind", "ports", "f0", "band", "band_hi")
                if k in per_m]
    fails += _chk(d_m == 0.0 and j_m == 0.0 and not bad_meta, "元数据键跳过",
                  "d=0 J=0 keys=%s" % sorted(per_m),
                  "d=%s J=%s keys=%s" % (d_m, j_m, bad_meta))
    try:
        d_n, per_n = distance_of({"Z0": 50.0, "theta": 12.0},
                                 {"Z0": 50.0, "theta": None}, {"Z0": 0.05})
        j_n = J_of({"Z0": 50.0, "theta": 12.0},
                   {"Z0": 50.0, "theta": None}, {"Z0": 0.05})
        theta_err = None
    except Exception as exc:  # noqa: BLE001 - selftest 需报告任意异常
        theta_err = exc
    fails += _chk(theta_err is None and d_n == 0.0 and j_n == 0.0
                  and "theta" not in per_n, "theta=None 不崩", "d=0 J=0",
                  "err=%s" % theta_err)
    c_opt = EmOptimizer(library=_MockLibrary(root=os.path.join(work, "c_lib")),
                        work=os.path.join(work, "c_work"))
    c_geom = {"w": 10.0, "len": 200.0}
    c2 = build_request("tl", {"Z0": 50.0, "theta": 90.0}, f_op=2e9, mock=True)
    c5 = build_request("tl", {"Z0": 50.0, "theta": 90.0}, f_op=5e9, mock=True)
    c_fp2 = c_opt._eval_geom(c2, c_geom)[0]
    c_fp5 = c_opt._eval_geom(c5, c_geom)[0]
    c_hit, _s, c_msg = c_opt._eval_geom(c2, c_geom)
    c_th2, c_th5 = c_fp2.get("theta"), c_fp5.get("theta")
    fails += _chk(bool(c_th2 and c_th5) and abs(c_th5 / c_th2 - 2.5) < 1e-9,
                  "缓存按 f_op 分离",
                  "theta2=%s theta5=%s" % (c_th2, c_th5),
                  "theta2=%s theta5=%s" % (c_th2, c_th5))
    fails += _chk(c_msg == "cache" and c_hit is c_fp2, "同键仍命中缓存",
                  c_msg, c_msg)
    c_g2 = {"dout": 150.0, "n": 2.5}
    c_ind = c_opt._eval_geom(build_request("ind", {"L0": 1e-9}, f_op=2e9,
                                           mock=True), c_g2)[0]
    c_ct = c_opt._eval_geom(build_request("ct", {"L0": 1e-9}, f_op=2e9,
                                          mock=True), c_g2)[0]
    fails += _chk(c_ind.get("kind") == "ind" and c_ct.get("kind") == "ct",
                  "缓存按 device 分离", "ind/ct",
                  "%s/%s" % (c_ind.get("kind"), c_ct.get("kind")))
    try:
        r_nd = EmOptimizer(library=_MockLibrary(root=os.path.join(work, "nd_lib")),
                           work=os.path.join(work, "nd_work")).solve(
            {"target": {"L0": 5.87e-10}, "f_op": 1e9, "mock": True,
             "geom_prior": {"n": 2.5}})
        nd_err = None
    except Exception as exc:  # noqa: BLE001
        r_nd, nd_err = {}, exc
    fails += _chk(nd_err is None and r_nd.get("status") in
                  ("hit_grid", "best_effort"), "省略 device_type 不崩",
                  "status=%s" % r_nd.get("status"), "err=%r" % nd_err)
    d_s, per_s = distance_of({"L0": 5.87e-10},
                             {"L0": "587p", "kind": "ind"}, {"L0": 0.05})
    d_e, per_e = distance_of({"L0": 5.87e-10}, {"kind": "ind"}, {"L0": 0.05})
    fails += _chk(d_s == float("inf") and not per_s
                  and d_e == float("inf") and not per_e,
                  "非数值/空目标返回 inf", "d=%s/%s" % (d_s, d_e),
                  "d=%s/%s per=%s/%s" % (d_s, d_e, sorted(per_s), sorted(per_e)))
    dnc_calls = [0]
    dnc_opt = EmOptimizer(library=_MockLibrary(root=os.path.join(work, "dnc_lib")),
                          work=os.path.join(work, "dnc_work"))
    def _dnc_eval(req, geom):
        dnc_calls[0] += 1
        return None, "dnc", "boom"
    dnc_opt._eval_geom = _dnc_eval
    dnc_rec = dnc_opt.solve(build_request(
        "ind", {"L0": 5.87e-10}, f_op=1e9, mock=True,
        budget={"max_em": 5, "time_cap_s": 3.0}))
    fails += _chk(dnc_calls[0] <= 5 and dnc_rec.get("status") == "miss",
                  "dnc 占预算", "calls=%d status=%s"
                  % (dnc_calls[0], dnc_rec.get("status")),
                  "calls=%d status=%s" % (dnc_calls[0], dnc_rec.get("status")))
    seq = ["infra", "ok", "infra", "infra", "ok", "ok", "ok", "ok", "ok"]
    seq_i = [0]
    inf_opt = EmOptimizer(library=_MockLibrary(root=os.path.join(work, "inf_lib")),
                          work=os.path.join(work, "inf_work"))
    def _seq_eval(req, geom):
        i = seq_i[0]
        seq_i[0] += 1
        st = seq[i] if i < len(seq) else "ok"
        if st == "infra":
            return None, "infra", "lock"
        return {"kind": "ind", "L0": 5.87e-10,
                "Qcurve": [[1e9, 8.0], [2e10, 8.0]]}, "ok", ""
    inf_opt._eval_geom = _seq_eval
    inf_rec = inf_opt.solve(build_request(
        "ind", {"Q@f0": 100.0}, tolerances={"Q@f0": 0.2}, f_op=1e10,
        mock=True, budget={"max_em": 9, "time_cap_s": 3.0}))
    inf_stage = (inf_rec.get("stages") or [{}])[0]
    fails += _chk(seq_i[0] >= 5 and inf_stage.get("status") != "infra_fail",
                  "infra 计数成功清零", "calls=%d stage=%s"
                  % (seq_i[0], inf_stage.get("status")),
                  "calls=%d stage=%s" % (seq_i[0], inf_stage.get("status")))
    if _lo is not None:
        saved_make = getattr(_lo, "make_layout_emx", None)
        saved_fp = getattr(_lo, "fingerprint_from_snp", None)
        real_calls = []
        snp_fake = os.path.join(work, "fake.s2p")
        def _fake_make(*a, **k):
            return {"snp": snp_fake, "kind": "ind",
                    "ports": ["p01", "p02"],
                    "fingerprint": {"kind": "ind", "L0": 5.87e-10,
                                    "Qcurve": [[1e9, 5.0], [2e10, 5.0]]}}
        def _fake_fp(path, kind, ports=None, f0=None):
            real_calls.append((path, kind, f0))
            return {"kind": "ind", "L0": 5.87e-10,
                    "Qcurve": [[1e9, 7.0], [2e10, 9.0]]}
        try:
            _lo.make_layout_emx = _fake_make
            _lo.fingerprint_from_snp = _fake_fp
            real_opt = EmOptimizer(
                library=_MockLibrary(root=os.path.join(work, "real_lib")),
                work=os.path.join(work, "real_work"))
            real_rec = real_opt.solve(build_request(
                "ind", {"L0": 5.87e-10, "Q@f0": 8.0},
                tolerances={"L0": 0.05, "Q@f0": 0.2}, f_op=1e10,
                geom_prior={"n": 2.5}))
            real_err = None
        except Exception as exc:  # noqa: BLE001
            real_rec, real_err = {}, exc
        finally:
            if saved_make is not None:
                _lo.make_layout_emx = saved_make
            if saved_fp is not None:
                _lo.fingerprint_from_snp = saved_fp
        real_ok = (real_err is None and bool(real_calls)
                   and real_calls[0][0] == snp_fake
                   and real_calls[0][2] == 1e10
                   and real_rec.get("snp") == snp_fake
                   and (real_rec.get("distance") or 99.0) < 1.0)
        fails += _chk(real_ok, "真实路径 sNp/f_op",
                      "snp=%s f0=%s d=%s" % (real_rec.get("snp"),
                                             real_calls[0][2] if real_calls else None,
                                             real_rec.get("distance")),
                      "err=%r calls=%s snp=%s d=%s"
                      % (real_err, real_calls[:1], real_rec.get("snp"),
                         real_rec.get("distance")))
    print("em_opt selftest: %s" % ("PASS" if fails == 0 else "FAIL(%d)" % fails))
    return fails

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(1 if _selftest() else 0)
    print("em_opt.py — Virtuoso Optimizer EM 融合优化模块")
    print("用法: python em_opt.py --selftest")
