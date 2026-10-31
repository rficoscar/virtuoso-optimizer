# -*- coding: utf-8 -*-
"""lri.ui.results — 结果读取、状态判定与查询/指标格式化。"""
from __future__ import print_function

import json
import math
import os

from ...cli import (
    RealRunner)

def read_result_any(iter_dir):
    plain = os.path.join(iter_dir, "result.txt")
    gz_path = os.path.join(iter_dir, "result.txt.gz")
    if os.path.isfile(plain):
        try:
            curves, status, msg, timing = RealRunner._read_result(plain)
            return curves, status, msg, timing, "plain"
        except Exception:
            pass
    if not os.path.isfile(gz_path):
        return None
    try:
        import gzip
        with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    import tempfile
    fd = tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="gui_res_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            fd = None
            out.write(text)
        curves, status, msg, timing = RealRunner._read_result(tmp)
        return curves, status, msg, timing, "gz"
    except Exception:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

F_DIST = "distance"

F_DELTA = "delta_vs_request"

F_DELIVERED = "delivered"

F_ACTUAL = "actual_fingerprint"

F_STATUS = "status"

F_SOURCE = "source"

def _f_get(d, const_key, default=None):
    return d.get(const_key, default) if isinstance(d, dict) else default

SCHEMA = "gui_em_panel/1"

STATUS_HIT_GRID = "hit_grid"

STATUS_HIT_NEAREST = "hit_nearest"

STATUS_BEST_EFFORT = "best_effort"

STATUS_MISS = "miss"

STATUS_INFRA = "infra_fail"

STATUS_ERROR = "error"

KNOWN_STATUSES = (STATUS_HIT_GRID, STATUS_HIT_NEAREST, STATUS_BEST_EFFORT,
                  STATUS_MISS, STATUS_INFRA, STATUS_ERROR)

STATUS_LABELS = {
    STATUS_HIT_GRID: "格点命中", STATUS_HIT_NEAREST: "近邻命中",
    STATUS_BEST_EFFORT: "尽力交付", STATUS_MISS: "库未命中",
    STATUS_INFRA: "工具链失败", STATUS_ERROR: "查询错误"}

HISTORY_LIMIT = 50

WARN_GATE = "GATE"

WARN_SOFT = "SOFT"

WARN_INFO = "INFO"

WEAK_HIT_DISTANCE = 10.0

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _fmt_eng(v, unit=""):
    if v is None:
        return "?"
    if not _is_num(v):
        return str(v)
    x = float(v)
    if not math.isfinite(x):
        return str(x)
    ax = abs(x)
    if ax == 0:
        return "0" + unit
    for scale, suf in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"),
                       (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
                       (1e-12, "p"), (1e-15, "f")):
        if ax >= scale:
            return "%g%s%s" % (x / scale, suf, unit)
    return "%g%s" % (x, unit)

def _fmt_pct(x, digits=2):
    if x is None or not _is_num(x):
        return "n/a"
    return "%+.*f%%" % (digits, float(x) * 100.0)

def _fmt_delta(delta):
    return "n/a" if delta is None or not _is_num(delta) else _fmt_pct(delta)

def status_label(status):
    s = str(status or "").strip()
    return STATUS_LABELS[s] if s in STATUS_LABELS else "未知(%s)" % (s or "空")

def _q_of_fingerprint(fp):
    if not isinstance(fp, dict):
        return None
    if "Q_at" in fp:
        v = fp.get("Q_at")
        return float(v) if _is_num(v) else None
    q = fp.get("Q")
    if isinstance(q, dict) and q:
        try:
            return float(q[sorted(q.keys(), key=lambda s: float(s))[0]])
        except Exception:
            return None
    return float(q) if _is_num(q) else None

def _metric_value(fp, key):
    if not isinstance(fp, dict):
        return None
    if key == "Q":
        return _q_of_fingerprint(fp)
    v = fp.get(key)
    return float(v) if _is_num(v) else None

def iter_triple_rows(em_actual, tol=None):
    if not isinstance(em_actual, dict):
        return []
    tol = tol or {}
    target = em_actual.get("target")
    target = target if isinstance(target, dict) else {}
    delivered_map = _f_get(em_actual, F_DELIVERED, {}) or {}
    actual = _f_get(em_actual, F_ACTUAL, {}) or {}
    deltas = _f_get(em_actual, F_DELTA, {}) or {}
    delivered_map = delivered_map if isinstance(delivered_map, dict) else {}
    actual = actual if isinstance(actual, dict) else {}
    deltas = deltas if isinstance(deltas, dict) else {}
    skip = ("kind", "ports", "band_hi", "Lcurve", "Qcurve", "kcurve",
            "Z0curve", "ILcurve", "extract_window", "warnings", "Q_at")
    keys = list(target.keys())
    for extra in list(delivered_map.keys()) + list(actual.keys()):
        if extra not in keys and extra not in skip:
            keys.append(extra)
    grid_map = em_actual.get("grid_values")
    grid_all = em_actual.get("grid")
    rows = []
    for key in keys:
        req = target.get(key)
        if _is_num(req):
            req_v = float(req)
        elif isinstance(req, str) and req.strip():
            try:
                req_v = float(req)
            except ValueError:
                req_v = None
        else:
            req_v = None
        if isinstance(grid_map, dict) and _is_num(grid_map.get(key)):
            grid_v = float(grid_map[key])
        elif req_v is not None and isinstance(grid_all, dict) and _is_num(grid_all.get(key)):
            grid_v = float(grid_all[key])
        else:
            grid_v = req_v
        del_v = delivered_map.get(key)
        del_v = float(del_v) if _is_num(del_v) else _metric_value(actual, key)
        delta = deltas.get(key)
        if not _is_num(delta) and req_v is not None and del_v is not None and req_v != 0:
            delta = (del_v - req_v) if key == "theta" else (del_v - req_v) / abs(req_v)
        in_tol = None
        if key == "theta":
            t_abs = tol.get("theta_tol_deg", tol.get("theta"))
            if _is_num(t_abs) and _is_num(delta):
                ref = (del_v - req_v if req_v is not None and del_v is not None else delta)
                in_tol = abs(float(ref)) <= float(t_abs)
        else:
            t = tol.get(key, tol.get(key.lower()))
            if _is_num(t) and _is_num(delta):
                in_tol = abs(float(delta)) <= float(t)
        rows.append({"metric": key, "requested": req_v if req_v is not None else req,
                     "grid": grid_v, "delivered": del_v,
                     "delta": delta if _is_num(delta) else None, "in_tol": in_tol})
    return rows

def format_query_triple(name, em_actual, tol=None, show_geom=False):
    nl = "\n"
    if not isinstance(em_actual, dict):
        return "  (本轮无 EM 信息)"
    status = _f_get(em_actual, F_STATUS, "")
    src = _f_get(em_actual, F_SOURCE, "")
    dist = _f_get(em_actual, F_DIST)
    head = "【器件 %s】状态=%s" % (name or "?", status_label(status))
    if src:
        head += "  来源=%s" % src
    lines = [head]
    meta_bits = []
    if _is_num(dist):
        meta_bits.append("distance=%s" % ("%.3g" % float(dist)))
    if em_actual.get("grid_key"):
        meta_bits.append("grid_key=%s" % em_actual.get("grid_key"))
    if em_actual.get("snp_path"):
        meta_bits.append("snp=%s" % os.path.basename(str(em_actual.get("snp_path"))))
    if meta_bits:
        lines.append("  " + "  ".join(meta_bits))
    rows = iter_triple_rows(em_actual, tol)
    if not rows:
        lines.append("  (无指纹指标)")
    else:
        first = rows[0]
        lines.append("  %s:  %s  →  %s  →  %s   (%s)"
                     % (first["metric"], _fmt_eng(first["requested"]), _fmt_eng(first["grid"]),
                        _fmt_eng(first["delivered"]), _fmt_delta(first["delta"])))
        lines.append("         请求      格点      交付")
        lines.append("  %-8s %10s %10s %10s %10s %s"
                     % ("指标", "requested", "grid", "delivered", "delta", "in_tol"))
        for r in rows:
            if r["metric"] == "theta":
                d_txt = ("n/a" if r["delta"] is None else ("%+.2f°" % float(r["delta"])))
            else:
                d_txt = _fmt_delta(r["delta"])
            flag = "-" if r["in_tol"] is None else ("Y" if r["in_tol"] else "N")
            lines.append("  %-8s %10s %10s %10s %10s %s"
                         % (r["metric"], _fmt_eng(r["requested"]), _fmt_eng(r["grid"]),
                            _fmt_eng(r["delivered"]), d_txt, flag))
    geom = None
    rec = em_actual.get("record")
    if isinstance(rec, dict):
        geom = rec.get("geom") or rec.get("geom_json")
    if not geom:
        geom = em_actual.get("geom")
    if geom:
        if show_geom:
            lines.append("  geom: %s" % json.dumps(geom, ensure_ascii=False, sort_keys=True))
        elif isinstance(geom, dict):
            keys = sorted(geom.keys())
            brief = ", ".join("%s=%s" % (k, geom[k]) for k in keys[:6])
            if len(keys) > 6:
                brief += ", …"
            lines.append("  geom: {%s}  (折叠，show_geom=True 展开)" % brief)
        else:
            lines.append("  geom: {%s}  (折叠，show_geom=True 展开)" % str(geom)[:80])
    return nl.join(lines)
