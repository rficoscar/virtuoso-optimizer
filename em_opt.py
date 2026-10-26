# -*- coding: utf-8 -*-
"""em_opt.py — 电磁优化器 EmOptimizer（Python 3.6+ 纯 stdlib）。"""
from __future__ import print_function

import json
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import librf_opt as _lo
except Exception:
    _lo = None
try:
    import algorithm as _alg
except Exception:
    _alg = None
# C-5：正式库来自 em_bridge.EmLibrary；缺失时退 _MockLibrary（仅 selftest）。
# 双实现 fallback：distance_of / _q_at_f0 与 em_bridge 同名函数语义不同，
# 改动任一侧需同步评估另一侧（本文件为离线最小实现）。
try:
    from em_bridge import (EmLibrary as _RealLibrary,
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
_MISSING_PER = {"delta": None, "normalized": 99.0, "in_tol": False}

def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)

# 距离 / 误差核（契约 D4/D5）
def _tol_eff(tol, key, t):
    """有效容差：θ 为绝对度（"3%"→2.7°）；其余为 rel×|t|；缺省 0.05/5°。"""
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
    """φ = max(0, |Δ|-tol_eff) / max(tol_eff, ε·scale)。"""
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
    """取值；Q@f0 缺失时回退 Qcurve 插值。"""
    av = (fp or {}).get(key)
    if av is None and key == "Q@f0":
        av = _q_at_f0(fp, f0)
    return av

def distance_of(actual_fp, target, tolerances=None):
    """验收距离 d = max_i |Δ_i|/tol_i（θ 用 |Δθ|/theta_tol_deg）。"""
    tolerances = tolerances or {}
    f0 = (target or {}).get("f0")
    per = {}
    dmax = 0.0
    for key, tv in (target or {}).items():
        if key.startswith("_"):
            continue
        av = _metric_val(actual_fp, key, f0)
        if av is None or (key not in _ANG_KEYS and tv is None):
            per[key] = dict(_MISSING_PER)
            dmax = max(dmax, 99.0)
            continue
        tol_eff = _tol_eff(tolerances.get(key), key, tv)
        delta = abs(float(av) - float(tv))
        norm = delta / max(tol_eff, 1e-12 if key in _ANG_KEYS else 1e-30)
        per[key] = {"delta": delta, "normalized": norm, "in_tol": norm <= 1.0}
        dmax = max(dmax, norm)
    return dmax, per

def _q_at_f0(fp, f0):
    """Q@f0 回退：仅 Qcurve 线性插值（em_bridge 版另支持 Q dict，需同步评估）。"""
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

def J_of(actual_fp, target, tolerances=None, weights=None):
    """搜索目标 J = Σ w_i φ_i。weight 只进 J，不进验收 d。"""
    tolerances = tolerances or {}
    weights = weights or {}
    f0 = (target or {}).get("f0")
    total = 0.0
    for key, tv in (target or {}).items():
        if key.startswith("_"):
            continue
        av = _metric_val(actual_fp, key, f0)
        w = float(weights.get(key, 1.0) or 1.0)
        total += w * error_kernel(av, tv, tolerances.get(key), key)
    return total

def _scalar_projection(fp):
    """C-3：delivered = actual_fingerprint 的标量投影（history 列用）。"""
    out = {}
    for k, v in (fp or {}).items():
        if k.startswith("_") or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v)
    return out

def _penalty_tell(algo, x, best_J):
    """失败点 tell 大惩罚（不产生 BIG_ERROR 语义：仅在算法内部记账）。"""
    if algo is None:
        return
    try:
        algo.tell([x], [max(1e3, best_J * 2.0 if best_J < 1e29 else 1e3)])
    except Exception:
        pass

# 结果库（最小实现；生产用 em_bridge.EmLibrary。API 冻结 query/put/nearest）
class _MockLibrary(object):
    """结果库 fallback（em_bridge 缺失 / selftest 显式注入）。"""

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
        """入库（幂等）。返回 record_id。失败点 usable=false 可入但 nearest 过滤。"""
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
        """库侧匹配；签名/返回对齐 em_bridge.EmLibrary.query（C-4）。"""
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
        """按验收距离 d 排序取 k 条可用记录（签名对齐 EmLibrary.nearest）。"""
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

# 请求构造（em_request/1）
def build_request(device_type, target, tolerances=None, f_op=None,
                  weights=None, geom_prior=None, fixed_geom=None,
                  budget=None, tech=None, mock=False, library=None,
                  verbose=False):
    """构造 EmOptimizer.solve 的请求 dict（schema em_request/1）。"""
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

# Mock EM（无 license 全流程）
def _mock_fingerprint(device_type, geom, f_op):
    """合成单调指纹（与 v07 GeomRunner mock 同族；tl/cpl/xfmr 解析近似）。"""
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

# EmOptimizer
class EmOptimizer(object):
    """电磁优化器。solve(request) -> em_record/1。"""

    def __init__(self, library=None, work=None, make_em=None):
        # C-5：优先正式库 em_bridge.EmLibrary；缺失才用 _MockLibrary
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
        self._make_em = make_em   # 可注入：geom -> fingerprint dict
        self._n_em = 0
        self._n_fail = 0
        self._cache = {}
        self._cache_path = os.path.join(self.work, ".em_opt_geom_cache.json")
    # 几何 <-> 向量
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
                g[k] = round(float(v) * 2.0) / 2.0   # 匝数半步进
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
        """跑一点 EM（或 mock）。返回 (fingerprint|None, status, message)。"""
        dev = req["device_type"]
        key = self._geom_hash(geom)
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
            # cell 名用 sha1 前 8 位（跨进程稳定，不用内建 hash）
            try:
                import hashlib as _hl
                cell_tag = _hl.sha1(key.encode("utf-8")).hexdigest()[:8]
            except Exception:
                cell_tag = "%08x" % (abs(hash(key)) % (10 ** 8))
            # 频点必须非空：None/空会让 EMX 无扫频参数而挂起
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
    # 热启动
    def warm_start_geom(self, req):
        """库 nearest(k=5) -> x0。返回 (geom|None, origin)。"""
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
    # 主入口
    def solve(self, request):
        """求解。返回 em_record/1 dict。不抛业务异常。"""
        req = dict(request or {})
        dev = (req.get("device_type") or "ind").lower()
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
            if n_stage == 1:
                b_s = max_em
            else:
                b_s = max(2, max_em // n_stage + (2 if si == n_stage - 1 else 0))
            fp, geom, J, d, st = self._run_search_stage(
                req, sub_t, sub_tol, sub_w, keys, bounds, x_stage,
                b_s, time_cap, t0)
            stage_rows.append({"stage": si, "keys": list(keep),
                               "distance": d, "J": J, "status": st})
            if fp is not None and J < best_J:
                best_J, best_fp, best_geom, best_d = J, fp, geom, d
            if geom:
                x_stage = [float(geom.get(k, x_stage[i])) for i, k in enumerate(keys)]
            if d is not None and d <= 1.0 and si == n_stage - 1:
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
        """单 stage 搜索循环。返回 (fp, geom, J, d, status)。"""
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
        while n_eval < max_em and (time.time() - t0) < time_cap:
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
            if st != "ok" or fp is None:
                # 失败记账只在 _eval_geom（避免 em_fail 双计）
                _penalty_tell(algo, x, best_J)
                continue
            J = J_of(fp, target, tol, wts)
            d, per = distance_of(fp, target, tol)
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
            d, per = distance_of(fp, target, dict(req.get("tolerances") or {}))
        weak = bool(d is not None and d > 10.0)
        rec = self._base_record(req, fp, geom, d, per)
        rec.update({
            "status": status, "reason": reason,
            "geom_hash": self._geom_hash(geom) if geom else None,
            "in_tol": {k: bool((per or {}).get(k, {}).get("in_tol"))
                       for k in target},
            "weak_hit": weak,
            "warns": ["weak_hit distance=%.2f" % (d or 0)] if weak else [],
            "snp": (fp or {}).get("snp"),
            "source": "optimization",
            "em_meta": {"iters": self._n_em,
                        "em_ok": self._n_em - self._n_fail,
                        "em_fail": self._n_fail, "wall_s": wall,
                        "origin": origin, "J": J, "distance": d},
        })
        return rec

# CLI 冒烟（mock）
def _selftest():
    """mock 全流程自测（无 license）。返回失败数。"""
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
    print("em_opt selftest: %s" % ("PASS" if fails == 0 else "FAIL(%d)" % fails))
    return fails

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(1 if _selftest() else 0)
    print("em_opt.py — EmOptimizer (v09)")
    print("用法: python em_opt.py --selftest")
