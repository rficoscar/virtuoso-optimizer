# -*- coding: utf-8 -*-
"""lri.ui.reports — 库统计、EM 报告与网表/对齐事件文本。"""
from __future__ import print_function

import glob
import json
import os

from .results import (
    F_DIST, F_ACTUAL, _f_get, SCHEMA, STATUS_HIT_GRID, STATUS_HIT_NEAREST, STATUS_BEST_EFFORT,
    STATUS_MISS, STATUS_INFRA, KNOWN_STATUSES, HISTORY_LIMIT, WARN_GATE, WARN_SOFT, WARN_INFO,
    WEAK_HIT_DISTANCE, _is_num, _fmt_eng, _fmt_pct, _q_of_fingerprint, _metric_value,
    format_query_triple)

def format_library_stats(stats, hit_counts=None):
    stats = stats if isinstance(stats, dict) else {}
    hit_counts = hit_counts if isinstance(hit_counts, dict) else {}
    nl = "\n"
    lines = ["【库统计】"]
    count = stats.get("count")
    if not count and not hit_counts:
        lines.append("  (库为空：尚未 put，或库文件未创建)")
        return nl.join(lines)
    lines.append("  条目 %s   唯一格点 %s" % (_fmt_eng(count), _fmt_eng(stats.get("unique_keys"))))
    hg = int(hit_counts.get("hit_grid", 0) or 0)
    hn = int(hit_counts.get("hit_nearest", 0) or 0)
    ms = int(hit_counts.get("miss", 0) or 0)
    be = int(hit_counts.get("best_effort", 0) or 0)
    er = int(hit_counts.get("error", 0) or 0)
    inf = int(hit_counts.get("infra_fail", 0) or 0)
    total_q = hg + hn + ms + be + er + inf
    if total_q > 0:
        lines.append("  查询 %d 次：" % total_q)
        for label, n in (("hit_grid", hg), ("hit_nearest", hn),
                         ("best_effort", be), ("miss", ms),
                         ("error", er), ("infra_fail", inf)):
            lines.append("    %-12s %4d  (%.0f%%)" % (label, n, 100.0 * n / total_q))
        p_hit = float(hg + hn) / float(total_q)
        lines.append("  命中率 p_hit=%.2f  %s" % (p_hit, "达标(≥0.7)" if p_hit >= 0.7 else "偏低"))
    else:
        lines.append("  (尚无查询记录)")
    mr = stats.get("miss_rate")
    if _is_num(mr):
        lines.append("  miss_rate=%.2f  %s" % (float(mr), "偏高(>0.2)" if float(mr) > 0.2 else "ok"))
    ad = stats.get("avg_d_on_hit")
    if _is_num(ad):
        lines.append("  avg_d_on_hit=%.3f  %s"
                     % (float(ad), "ok" if float(ad) < 0.5
                        else ("弱" if float(ad) > 1.0 else "一般")))
    gu = stats.get("grid_utilization")
    if _is_num(gu):
        lines.append("  grid_utilization=%.1f%%  %s"
                     % (float(gu) * 100.0,
                        "过密(<5%)" if float(gu) < 0.05 else "ok"))
    return nl.join(lines)

def format_em_opt_status(em_meta, budget=None, device=None):
    em_meta = em_meta if isinstance(em_meta, dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    nl = "\n"
    lines = ["【EM 优化器】"]
    if not em_meta and not budget:
        lines.append("  (本轮走库命中，未启动内层 EM 优化器)")
        return nl.join(lines)
    dev = device or em_meta.get("device") or em_meta.get("device_type") or "?"
    iters = em_meta.get("iters")
    max_em = budget.get("max_em", em_meta.get("max_em"))
    wall = em_meta.get("wall_s", em_meta.get("wall"))
    cap = budget.get("time_cap_s", em_meta.get("time_cap_s"))
    em_ok, em_fail = em_meta.get("em_ok"), em_meta.get("em_fail")
    bits = ["器件 %s" % dev]
    if iters is not None:
        bits.append("迭代 %s%s" % (iters, ("/%s" % max_em) if max_em is not None else ""))
    if em_ok is not None or em_fail is not None:
        bits.append("EM 评测 ok=%s fail=%s"
                    % (em_ok if em_ok is not None else "?", em_fail if em_fail is not None else "?"))
    lines.append("  " + " · ".join(bits))
    left_em = (int(max_em) - int(iters) if _is_num(iters) and _is_num(max_em) else None)
    left_t = (float(cap) - float(wall) if _is_num(wall) and _is_num(cap) else None)
    if left_em is not None or left_t is not None:
        em_txt = ("剩 %d 次" % left_em) if left_em is not None else "次数 n/a"
        if left_em is not None and left_em <= 0:
            em_txt = "次数尽(0)"
        t_txt = ("剩 %.0f min" % (left_t / 60.0)) if left_t is not None else "时间 n/a"
        if left_t is not None and left_t <= 0:
            t_txt = "时间尽"
        lines.append("  预算: %s / %s" % (em_txt, t_txt))
    meta = []
    for key in ("mode", "strategy"):
        if em_meta.get(key):
            meta.append("%s=%s" % (key, em_meta[key]))
    if em_meta.get("cache_hits") is not None:
        meta.append("cache_hits=%s" % em_meta.get("cache_hits"))
    if _is_num(wall):
        meta.append(("wall=%s" % _fmt_eng(float(wall), "s")) if abs(float(wall)) >= 1
                    else ("wall=%.1fs" % float(wall)))
    if em_meta.get("source"):
        meta.append("source=%s" % em_meta.get("source"))
    if meta:
        lines.append("  " + " · ".join(meta))
    tgt = em_meta.get("target_fp") or em_meta.get("target")
    act = em_meta.get("actual_fp") or em_meta.get("actual_fingerprint")
    if isinstance(tgt, dict) or isinstance(act, dict):
        lines.append("  指纹 目标 vs 实际:")
        tgt = tgt if isinstance(tgt, dict) else {}
        act = act if isinstance(act, dict) else {}
        skip = ("kind", "ports", "band_hi", "Lcurve", "Qcurve", "kcurve", "warnings", "extract_window")
        for k in list(tgt.keys()) + [k for k in act.keys() if k not in tgt]:
            if k in skip:
                continue
            tv = tgt.get(k)
            av = _metric_value(act, k) if k not in tgt or _is_num(tgt.get(k)) else act.get(k)
            if not _is_num(av):
                av = act.get(k) if isinstance(act.get(k), str) else av
            if isinstance(tv, dict):
                tv = _q_of_fingerprint({"Q": tv}) if k == "Q" else None
            if k == "Q":
                tv = _q_of_fingerprint({"Q_at": tv}) if _is_num(tv) else _q_of_fingerprint(tgt)
                av = _q_of_fingerprint(act) if not _is_num(av) else av
            t_s = _fmt_eng(tv) if _is_num(tv) else ("?" if tv is None else str(tv))
            a_s = _fmt_eng(av) if _is_num(av) else ("?" if av is None else str(av))
            d_s = ""
            if _is_num(tv) and _is_num(av):
                if k == "theta":
                    d_s = "  Δ%+.2f°" % (float(av) - float(tv))
                elif tv != 0:
                    d_s = "  Δ%+.2f%%" % ((float(av) - float(tv)) / abs(float(tv)) * 100)
            lines.append("    %-6s %s → %s%s" % (k, t_s, a_s, d_s))
    return nl.join(lines)

def build_warns(em_actual, em_meta=None, f_op=None, srf_margin=1.5, q_tol=0.30):
    out, seen = [], set()
    def add(level, text, metric=None, detail=None, device=None):
        key = (level, text)
        if key not in seen:
            seen.add(key)
            out.append({"level": level, "text": text, "metric": metric,
                        "detail": detail, "device": device})
    if not isinstance(em_actual, dict):
        return out
    name = em_actual.get("device") or em_actual.get("name")
    actual = _f_get(em_actual, F_ACTUAL, {}) or {}
    actual = actual if isinstance(actual, dict) else {}
    delivered = em_actual.get("delivered")
    delivered = delivered if isinstance(delivered, dict) else {}
    target = em_actual.get("target")
    target = target if isinstance(target, dict) else {}
    status = str(em_actual.get("status") or "")
    srf = _metric_value(actual, "SRF")
    if srf is None and _is_num(delivered.get("SRF")):
        srf = float(delivered["SRF"])
    if f_op is not None and _is_num(f_op) and srf is not None:
        need = float(f_op) * float(srf_margin)
        if float(srf) < need:
            add(WARN_GATE,
                "SRF 门槛不过: 实际 %s < %s×f_op (%s)"
                % (_fmt_eng(srf, "Hz"), srf_margin, _fmt_eng(need, "Hz")),
                metric="SRF", detail={"actual": srf, "need": need, "f_op": float(f_op)}, device=name)
    q_a = _metric_value(actual, "Q")
    if q_a is None:
        q_a = _q_of_fingerprint(actual)
    q_t = None
    if "Q" in target:
        tv = target.get("Q")
        if _is_num(tv):
            q_t = float(tv)
        elif isinstance(tv, dict):
            q_t = _q_of_fingerprint({"Q": tv})
    if "Q@f0" in target and _is_num(target.get("Q@f0")):
        q_t = float(target["Q@f0"])
    if q_a is not None and q_t is not None and q_t != 0:
        rel = (q_a - q_t) / abs(q_t)
        if abs(rel) > float(q_tol):
            add(WARN_SOFT,
                "Q 告警: 实际 %s vs 目标 %s (%s, tol %s)"
                % (_fmt_eng(q_a), _fmt_eng(q_t), _fmt_pct(rel, 1), _fmt_pct(q_tol, 0)),
                metric="Q", detail={"actual": q_a, "target": q_t, "rel": rel}, device=name)
    dist = _f_get(em_actual, F_DIST)
    if _is_num(dist) and float(dist) > WEAK_HIT_DISTANCE:
        add(WARN_INFO, "弱命中: distance=%.3g > %g" % (float(dist), WEAK_HIT_DISTANCE),
            metric="distance", detail={"distance": float(dist)}, device=name)
    if status == STATUS_BEST_EFFORT:
        add(WARN_INFO, "best_effort：预算尽仍交付实际值（fallback=actual）", device=name)
    if status == STATUS_INFRA:
        add(WARN_INFO, "infra_fail：工具链失败，不占预算，将重试", device=name)
    for w in (em_actual.get("warns") or []):
        add(WARN_SOFT if "Q" in str(w) else WARN_INFO, str(w), device=name)
    if isinstance(em_meta, dict):
        for w in (em_meta.get("warns") or []):
            add(WARN_SOFT if "Q" in str(w) else WARN_INFO, str(w), device=name)
    return out

def format_warns(warns):
    nl = "\n"
    lines = ["【告警】"]
    if not warns:
        lines.append("  (无告警)")
        return nl.join(lines)
    icon = {WARN_GATE: "硬!", WARN_SOFT: "软*", WARN_INFO: "-"}
    for w in warns:
        lvl = w.get("level", WARN_INFO)
        lines.append("  %s %s" % (icon.get(lvl, "·"), w.get("text", "")))
    return nl.join(lines)

def build_em_report(model):
    try:
        model = model if isinstance(model, dict) else {}
        nl = "\n"
        lines = [
            "EM 融合（只读 · 实时展示 · 优化器不修改 ADE）", "=" * 52,
            "说明: 电路优化请求 y* → 查结果库 → 命中用实际指纹 ŷ；",
            "      未命中由 EM 优化器几何搜索并入库。展示 requested/",
            "      grid/delivered 三值与告警，不做控制面。", ""]
        if model.get("disabled"):
            lines.append("未启用 EM 融合（配置无 em_bridge 段）")
            return nl.join(lines)
        lines.append(format_library_stats(model.get("library"), model.get("hit_counts")))
        lines.append("")
        queries = model.get("queries") or []
        lines.append("【当前查询】（最近 %d 条）" % HISTORY_LIMIT)
        if not queries:
            lines.append("  (尚无查询记录)")
        for q in queries[-HISTORY_LIMIT:]:
            lines.append(format_query_triple(
                q.get("name"), q.get("em_actual"), q.get("tol"),
                show_geom=bool(q.get("show_geom"))))
            lines.append("")
        lines.append(format_em_opt_status(
            model.get("em_opt"), model.get("budget"),
            device=model.get("device")))
        lines.append("")
        warns = model.get("warns")
        if warns is None:
            warns = []
            for q in queries[-HISTORY_LIMIT:]:
                warns.extend(build_warns(
                    q.get("em_actual") or {}, model.get("em_opt"),
                    f_op=model.get("f_op"),
                    srf_margin=model.get("srf_margin", 1.5),
                    q_tol=model.get("q_tol", 0.30)))
        lines.append(format_warns(warns))
        lines.append("")
        lines.append("运行模式: %s" % (model.get("mode") or "(未设)"))
        return nl.join(lines)
    except Exception as exc:
        return "EM_DISPLAY_ERROR: %s" % exc

def em_watch_paths(work_dir, out_dir):
    work = os.path.expanduser(str(work_dir or "work"))
    lib = os.path.join(work, "em_lib")
    paths = [os.path.join(lib, name) for name in
             ("em_lib.sqlite3", "em_lib.json", "em_lib_stat.jsonl")]
    if out_dir:
        out = os.path.expanduser(str(out_dir))
        paths += [os.path.join(out, name) for name in
                  ("history.csv", "events.jsonl", "report.json")]
    return paths

def em_signature(paths):
    sig = []
    for p in paths or []:
        try:
            st = os.stat(p)
            mt = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            sig.append((p, int(mt), int(st.st_size)))
        except OSError:
            sig.append((p, None, None))
    return tuple(sig)

def read_jsonl_tail(path, limit=200):
    if not path or not os.path.isfile(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    return out[-limit:]

def collect_em_model(work_dir=None, out_dir=None, history=None,
                     em_meta=None, budget=None, f_op=None,
                     srf_margin=1.5, q_tol=0.30, mode=None,
                     disabled=False, tol=None):
    model = {
        "schema": SCHEMA, "disabled": bool(disabled), "library": {},
        "hit_counts": {}, "queries": [], "em_opt": em_meta or {},
        "budget": budget or {}, "warns": None, "f_op": f_op,
        "srf_margin": srf_margin, "q_tol": q_tol, "mode": mode,
        "tol": tol or {},
    }
    try:
        work = os.path.expanduser(str(work_dir or "work"))
        lib = os.path.join(work, "em_lib")
        counts = {}
        for row in read_jsonl_tail(os.path.join(lib, "em_lib_stat.jsonl"), 1000):
            if not isinstance(row, dict):
                continue
            st = str(row.get("status") or row.get("kind") or "")
            if st in KNOWN_STATUSES:
                counts[st] = counts.get(st, 0) + 1
        model["hit_counts"] = counts
        for name in ("em_lib_stat.json", "em_lib_stats.json"):
            p = os.path.join(lib, name)
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        model["library"].update(data)
                except Exception:
                    pass
        if not model["library"] and counts:
            total = sum(counts.values())
            hits = counts.get(STATUS_HIT_GRID, 0) + counts.get(STATUS_HIT_NEAREST, 0)
            model["library"] = {
                "count": total,
                "miss_rate": (float(counts.get(STATUS_MISS, 0)) / total if total else None),
                "p_hit": (float(hits) / total) if total else None}
        hist = history
        if hist is None and out_dir:
            hist = []
            out = os.path.expanduser(str(out_dir))
            paths = glob.glob(os.path.join(out, "iter_*", "record.json"))
            paths.sort()
            for p in paths[-HISTORY_LIMIT:]:
                try:
                    with open(p, encoding="utf-8") as f:
                        hist.append(json.load(f))
                except Exception:
                    continue
        queries = []
        for rec in (hist or [])[-HISTORY_LIMIT:]:
            if not isinstance(rec, dict):
                continue
            em = rec.get("em_actual")
            if not isinstance(em, dict):
                extra = rec.get("extra")
                em = extra.get("em_actual") if isinstance(extra, dict) else None
            if not isinstance(em, dict):
                continue
            if "status" in em and ("delivered" in em or "actual_fingerprint" in em or "target" in em):
                queries.append({"name": rec.get("device") or "em", "em_actual": dict(em),
                                "tol": tol or {}, "show_geom": False, "index": rec.get("index")})
            else:
                for name, payload in em.items():
                    if isinstance(payload, dict):
                        queries.append({"name": name, "em_actual": payload, "tol": tol or {},
                                        "show_geom": False, "index": rec.get("index")})
        model["queries"] = queries[-HISTORY_LIMIT:]
    except Exception as exc:
        model["error"] = str(exc)
    return model

def _auto_netlist_on(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "auto", "always")
    return bool(value)

def _var_extra_rename(extra, old, new):
    out = {k: dict(v) for k, v in (extra or {}).items()}
    if old != new and old in out:
        out[new] = out.pop(old)
    return out

def _var_extra_drop(extra, names):
    drop = set(names or ())
    return {k: v for k, v in (extra or {}).items() if k not in drop}

def _targets_signature(target_specs):
    try:
        items = []
        for iid, spec in (target_specs or {}).items():
            items.append((str(iid), json.dumps(spec, sort_keys=True,
                                               ensure_ascii=False, default=str)))
        items.sort()
        return tuple(items)
    except Exception:
        return None

def netlist_report_text(report):
    if not isinstance(report, dict):
        return "网表生成返回异常: %r" % (report,)
    status = str(report.get("status") or "unknown")
    lines = ["状态: %s" % status]
    design = report.get("design")
    if isinstance(design, dict) and design:
        bits = [str(design.get(k)) for k in ("library", "cell", "view") if design.get(k)]
        if bits:
            lines.append("设计: %s" % " / ".join(bits))
    for key, label in (("path", "网表"), ("expected", "预期路径"),
                       ("previous", "原网表"), ("backup", "备份"),
                       ("wall", "耗时(秒)"), ("log", "日志"),
                       ("message", "说明")):
        val = report.get(key)
        if val not in (None, "", [], {}):
            lines.append("%s: %s" % (label, val))
    for key, label in (("errors", "错误"), ("warnings", "告警")):
        items = report.get(key) or []
        if items:
            lines.append("%s(%d):" % (label, len(items)))
            for item in list(items)[:20]:
                lines.append("  " + str(item))
    if report.get("restored"):
        lines.append("已恢复备份网表")
    return "\n".join(lines)

def em_align_event_text(event):
    if isinstance(event, str):
        return event
    if isinstance(event, dict):
        for key in ("message", "msg", "text", "log"):
            val = event.get(key)
            if val not in (None, ""):
                return str(val)
        kind = event.get("type") or event.get("event")
        try:
            rest = json.dumps(event, ensure_ascii=False, sort_keys=True)
        except Exception:
            rest = str(event)
        return ("[%s] %s" % (kind, rest)) if kind else rest
    return str(event)

def find_em_report_md(report, dirs):
    keys = ("report_md", "em_align_md", "markdown_path", "md_path")
    if isinstance(report, dict):
        for key in keys:
            val = report.get(key)
            if val and os.path.isfile(str(val)):
                return os.path.abspath(str(val))
        em = report.get("em_align")
        if isinstance(em, dict):
            for key in keys + ("report_path", "path"):
                val = em.get(key)
                if val and os.path.isfile(str(val)):
                    return os.path.abspath(str(val))
    for base in dirs or ():
        if not base:
            continue
        path = os.path.join(base, "em_align_report.md")
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None
