# -*- coding: utf-8 -*-
"""report.py - 汇总评分、配置哈希、JSON + 可读摘要。

评分口径：
  - 任一 fail -> 整体 FAIL；
  - 无 fail 但有 warn -> WARN；
  - 全 pass/info -> PASS。
  - DRC：几何规则必须 0 违规（geometry_clean），密度/宏观规则单列，不算 fail。
  - LVS：纯无源结构 NOT_COMPARED 记为 warn（工具正常、语义如此），
    unattached 标签或提取端口与期望不符记 fail。
"""
from __future__ import print_function

import hashlib
import json
import os

WEIGHTS = {"fail": 0, "warn": 1, "pass": 2, "info": 2}


def config_hash(cfg):
    """稳定配置哈希：排序键 + 去掉注释字段，便于复现。"""
    def norm(o):
        if isinstance(o, dict):
            return {k: norm(v) for k, v in sorted(o.items()) if not str(k).startswith("_")}
        if isinstance(o, list):
            return [norm(v) for v in o]
        return o
    blob = json.dumps(norm(cfg or {}), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def grade(checks):
    st = [c.get("status", "info") for c in checks]
    if "fail" in st:
        return "FAIL"
    if "warn" in st:
        return "WARN"
    return "PASS"


def summarize_case(case, geom_checks, emx=None, drc=None, lvs=None):
    checks = list(geom_checks or [])
    if drc and drc.get("summary"):
        s = drc["summary"]
        checks.append({"id": "DRC_geometry", "status": "pass" if s.get("geometry_clean") else "fail",
                       "value": s.get("geometry"), "limit": {},
                       "detail": "几何类 DRC 必须 0 违规"})
        checks.append({"id": "DRC_density", "status": "info" if s.get("density") else "pass",
                       "value": s.get("density"), "limit": {},
                       "detail": "密度/宏观规则，单 cell 评估通常豁免"})
    if emx:
        checks.append({"id": "EMX_run", "status": "pass" if emx.get("ok") else "fail",
                       "value": emx.get("elapsed"), "limit": None,
                       "detail": emx.get("error", "EMX 完成")})
        m = emx.get("metrics") or {}
        _l = m.get("L@f0")
        _q = m.get("Q@f0")
        if _l is None:
            _l, _q = m.get("Ldiff@f0"), m.get("Qdiff@f0")
        if _l is not None:
            checks.append({"id": "EMX_L", "status": "info", "value": _l,
                           "limit": None, "detail": "L@f0 (H)"})
            checks.append({"id": "EMX_Q", "status": "info", "value": _q,
                           "limit": None, "detail": "Q@f0"})
    if lvs:
        unatt = lvs.get("unattached") or []
        checks.append({"id": "LVS_labels", "status": "pass" if not unatt else "fail",
                       "value": unatt, "limit": [], "detail": "未挂上的端口标签"})
        checks.append({"id": "LVS_compare", "status": "warn" if lvs.get("passive_lvs") else
                       ("pass" if lvs.get("compare") == "CORRECT" else "warn"),
                       "value": lvs.get("compare"), "limit": "CORRECT",
                       "detail": "无源结构无 device，NOT_COMPARED 属预期；端口看提取网表"})
    return {"id": case.get("id"), "kind": case.get("kind"), "params": case.get("params"),
            "status": grade(checks), "checks": checks,
            "emx": (emx or {}).get("metrics"), "drc": (drc or {}).get("summary"),
            "lvs": {k: lvs.get(k) for k in ("compare", "extracted_ports", "nets",
                                            "explicit_ports", "unattached", "short_warning")}
            if lvs else None}


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    return path


def summary_md(report):
    lines = []
    lines.append("# PCell 评估报告\n")
    lines.append("- 配置哈希: `%s`" % report.get("config_hash", "?"))
    lines.append("- 生成时间: %s" % report.get("timestamp", "?"))
    tool = report.get("tool") or {}
    pinfo = tool.get("proc_info") or {}
    if pinfo:
        lines.append("- EMX proc: `%s`（%s, TM2_thick=%s）" % (
            os.path.basename(pinfo.get("path", "?")), pinfo.get("technology", "?"),
            pinfo.get("TM2_thick", "?")))
    lines.append("- 整体结论: **%s**\n" % report.get("status", "?"))
    lines.append("| case | kind | status | L@f0 | Q@f0 | DRC geom | LVS |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in report.get("cases", []):
        emx = c.get("emx") or {}
        drc = c.get("drc") or {}
        lvs = c.get("lvs") or {}
        L = emx.get("L@f0") or emx.get("Ldiff@f0")
        Q = emx.get("Q@f0") or emx.get("Qdiff@f0")
        g = "clean" if drc.get("geometry_clean") else ("%d" % sum((drc.get("geometry") or {}).values()))
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            c.get("id"), c.get("kind"), c.get("status"),
            ("%.4f nH" % (L * 1e9)) if L else "-",
            ("%.3f" % Q) if Q else "-", g,
            lvs.get("compare", "-")))
    fails = [c for c in report.get("cases", []) if c.get("status") == "FAIL"]
    if fails:
        lines.append("\n## 失败项\n")
        for c in fails:
            for chk in c["checks"]:
                if chk["status"] == "fail":
                    lines.append("- `%s` %s: %s" % (c["id"], chk["id"], chk["detail"]))
    return "\n".join(lines) + "\n"
