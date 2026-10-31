# -*- coding: utf-8 -*-
"""calibre_runner.py - Calibre 2019 DRC/LVS 调用与结果机读解析。

实测（SMIC55LLULPGE V1.13 DRC / V1.16 LVS）：
  * DRC:  calibre -drc -hier -turbo 8 deck.drc
          产物 drc.results(ASCII) + drc_CAL.SUM(摘要)。
          drc_CAL.SUM 里 "RULECHECK <name> ... TOTAL Result Count = N" 最稳，
          drc.results 是逐规则块：规则名一行，下一行首个数是命中数。
  * LVS:  calibre -lvs -hier -turbo 8 deck.lvs
          产物 lvs.rep / erc.rep / svdb/<cell>.sp。
          纯金属无源结构没有 device，比较结果必然是 NOT COMPARED(rc=4)，
          但 svdb/*.sp 仍给出提取到的端口/网络数；端口名重复落在一个网络上
          会报 "Short circuit - Different names on one net"（2 端电感本就
          是一根导体，属正常）。
"""
from __future__ import print_function

import os
import re
import shutil
import subprocess
import time

DEF_PDK = ("/home/IC/Tech/SMIC55LL_2023/installed_PDK/"
           "smic55ll_ulp_rf_09121825_oa_cds_v1.16_2")
DEF_DRC = "Calibre/DRC/SMIC_CalDRC_55LLULPGE_0912182533_V1.13_REV1_0.drc"
DEF_LVS = "Calibre/LVS/SMIC_CalLVS_55LGLLULPRF_09121825_V1.16_1.lvs"

DENSITY_RE = re.compile(r"(AA_10[ab]|GT_7[abc]|M[1-8]_6|TM[12]_6)", re.I)


def find_calibre(cfg=None):
    p = os.environ.get("CALIBRE_BIN")
    if p and os.path.isfile(p):
        return p
    p = ((cfg or {}).get("calibre") or {}).get("bin")
    if p and os.path.isfile(p):
        return p
    for c in (shutil.which("calibre"),
              os.path.join(os.environ.get("MGC_HOME", ""), "bin", "calibre"),
              os.path.join(os.environ.get("CALIBRE_HOME", ""), "bin", "calibre")):
        if c and os.path.isfile(c):
            return c
    raise RuntimeError("找不到 calibre（设 CALIBRE_BIN / CALIBRE_HOME / MGC_HOME）")


def find_deck(cfg, kind):
    ccfg = cfg.get("calibre") or {}
    if ccfg.get(kind):
        return ccfg[kind]
    pdk = cfg.get("pdk") or DEF_PDK
    rel = DEF_DRC if kind == "drc" else DEF_LVS
    p = os.path.join(pdk, rel)
    if not os.path.isfile(p):
        raise RuntimeError("找不到 %s deck: %s" % (kind, p))
    return p


def patch_deck(src, dst, gds, cell, source=None):
    """把 deck 里的 LAYOUT/SOURCE 路径与 top cell 换成本次 run 的值。"""
    with open(src, "r", errors="replace") as f:
        txt = f.read()
    txt = re.sub(r'(?m)^LAYOUT PATH\s+".*?"', 'LAYOUT PATH "%s"' % os.path.abspath(gds), txt)
    txt = re.sub(r'(?m)^LAYOUT PRIMARY\s+".*?"', 'LAYOUT PRIMARY "%s"' % cell, txt)
    if source:
        txt = re.sub(r'(?m)^SOURCE PATH\s+".*?"', 'SOURCE PATH "%s"' % os.path.abspath(source), txt)
        txt = re.sub(r'(?m)^SOURCE PRIMARY\s+".*?"', 'SOURCE PRIMARY "%s"' % cell, txt)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(txt)
    return dst


def _run(cmd, cwd, log_name, timeout):
    cwd = os.path.abspath(cwd)
    t0 = time.time()
    try:
        pr = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
        rc, out = pr.returncode, pr.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rc = -9
        out = (e.stdout or b"").decode("utf-8", "replace")
    elapsed = time.time() - t0
    with open(os.path.join(cwd, log_name), "w", encoding="utf-8") as f:
        f.write(out)
    return rc, out, round(elapsed, 3)


def parse_drc_sum(sum_path):
    """解析 drc_CAL.SUM -> {checks, total, by_rule, executed}。"""
    if not os.path.isfile(sum_path):
        return None
    with open(sum_path, "r", errors="replace") as f:
        txt = f.read()
    by_rule = {}
    for m in re.finditer(r"RULECHECK\s+(\S+)\s+\.*\s*TOTAL Result Count\s*=\s*(\d+)", txt):
        by_rule[m.group(1)] = int(m.group(2))
    total = None
    m = re.search(r"TOTAL DRC Results Generated:\s*(\d+)", txt)
    if m:
        total = int(m.group(1))
    m = re.search(r"TOTAL DRC RuleChecks Executed:\s*(\d+)", txt)
    executed = int(m.group(1)) if m else None
    m = re.search(r"TOTAL REAL Time:\s*(\d+)", txt)
    real = int(m.group(1)) if m else None
    return {"total": total, "executed": executed, "real_sec": real,
            "by_rule": by_rule}


def parse_drc_results(path):
    """解析 drc.results 文本：规则名行 + 下一行首个数为命中数。"""
    if not os.path.isfile(path):
        return {}
    counts = {}
    lines = [ln.rstrip("\n") for ln in open(path, errors="replace")]
    i = 1  # 第一行是 "<cell> <max results>"
    while i < len(lines):
        s = lines[i].strip()
        if re.match(r"^[A-Za-z][\w.+-]*$", s) and i + 1 < len(lines):
            nxt = lines[i + 1].strip().split()
            if nxt and re.match(r"^\d+$", nxt[0]):
                counts[s] = int(nxt[0])
                i += 2
                continue
        i += 1
    return counts


def run_drc(cfg, gds, cell, run_dir, timeout=1800, turbo=None):
    ccfg = cfg.get("calibre") or {}
    turbo = turbo or ccfg.get("turbo", 8)
    os.makedirs(run_dir, exist_ok=True)
    deck = patch_deck(find_deck(cfg, "drc"), os.path.join(run_dir, "deck.drc"), gds, cell)
    deck = os.path.abspath(deck)
    rc, out, elapsed = _run([find_calibre(cfg), "-drc", "-hier", "-turbo", str(turbo), deck],
                            run_dir, "calibre_drc.log", timeout)
    summ = parse_drc_sum(os.path.join(run_dir, "drc_CAL.SUM"))
    res = {"ok": False, "rc": rc, "elapsed": elapsed, "run_dir": run_dir,
           "results": os.path.join(run_dir, "drc.results"),
           "sum": os.path.join(run_dir, "drc_CAL.SUM"), "parser": "sum"}
    if summ is None:
        summ = {"by_rule": parse_drc_results(os.path.join(run_dir, "drc.results"))}
    if summ:
        by = summ.get("by_rule") or {}
        viol = {k: v for k, v in by.items() if v}
        summ["violations"] = viol
        summ["violation_types"] = len(viol)
        summ["density"] = {k: v for k, v in viol.items() if DENSITY_RE.search(k)}
        summ["geometry"] = {k: v for k, v in viol.items() if not DENSITY_RE.search(k)}
        summ["drc_clean"] = (len(viol) == 0)
        summ["geometry_clean"] = (len(summ["geometry"]) == 0)
        res["summary"] = summ
        res["ok"] = True
    return res


def write_source_netlist(path, cell, ports):
    with open(path, "w", encoding="utf-8") as f:
        f.write(".SUBCKT %s %s\n.ENDS\n" % (cell, " ".join(ports)))
    return path


def run_lvs(cfg, gds, cell, run_dir, ports, source=None, timeout=1800, turbo=None):
    ccfg = cfg.get("calibre") or {}
    turbo = turbo or ccfg.get("turbo", 8)
    os.makedirs(run_dir, exist_ok=True)
    if source is None:
        source = write_source_netlist(os.path.join(run_dir, "src.cdl"), cell, ports)
    deck = patch_deck(find_deck(cfg, "lvs"), os.path.join(run_dir, "deck.lvs"),
                      gds, cell, source=source)
    deck = os.path.abspath(deck)
    rc, out, elapsed = _run([find_calibre(cfg), "-lvs", "-hier", "-turbo", str(turbo), deck],
                            run_dir, "calibre_lvs.log", timeout)
    res = {"ok": False, "rc": rc, "elapsed": elapsed, "run_dir": run_dir,
           "source": source}
    rep = os.path.join(run_dir, "lvs.rep")
    ext = os.path.join(run_dir, "lvs.rep.ext")
    txt = open(rep, errors="replace").read() if os.path.isfile(rep) else ""
    txt_ext = open(ext, errors="replace").read() if os.path.isfile(ext) else ""
    if re.search(r"\bCORRECT\b", txt) and "INCORRECT" not in txt:
        res["compare"] = "CORRECT"
    elif "INCORRECT" in txt:
        res["compare"] = "INCORRECT"
    elif "NOT COMPARED" in txt:
        res["compare"] = "NOT_COMPARED"
    else:
        res["compare"] = "UNKNOWN"
    res["unattached"] = re.findall(r'Unattached (?:label|port)\s+"([^"]+)"', txt_ext)
    res["shorts"] = re.findall(r'Short circuit[^\n]*\n(?:[^\n]*\n){0,3}', txt_ext)
    sp = os.path.join(run_dir, "svdb", cell + ".sp")
    res["extracted_sp"] = sp if os.path.isfile(sp) else None
    if os.path.isfile(sp):
        spt = open(sp, errors="replace").read()
        m = re.search(r"\.SUBCKT\s+%s\s+([^\n]*)" % re.escape(cell), spt)
        res["extracted_ports"] = m.group(1).split() if m else []
        m = re.search(r"\*\* N=(\d+) EP=(\d+)", spt)
        if m:
            res["nets"] = int(m.group(1))
            res["explicit_ports"] = int(m.group(2))
        res["short_warning"] = "CALIBRE WARNING SHORT" in spt
    # 结论：无 device 的纯无源结构，LVS 只能给出网络/端口提取结果
    res["ok"] = res["compare"] in ("CORRECT", "NOT_COMPARED")
    res["passive_lvs"] = (res["compare"] == "NOT_COMPARED")
    return res


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Calibre DRC/LVS 单次运行")
    ap.add_argument("kind", choices=["drc", "lvs"])
    ap.add_argument("gds")
    ap.add_argument("cell")
    ap.add_argument("--ports", default="p01,p02")
    ap.add_argument("--run-dir", default="cal_run")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8")) if a.config else {}
    if a.kind == "drc":
        r = run_drc(cfg, a.gds, a.cell, a.run_dir)
    else:
        r = run_lvs(cfg, a.gds, a.cell, a.run_dir, a.ports.split(","))
    print(json.dumps(r, indent=2, ensure_ascii=False))
