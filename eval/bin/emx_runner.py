# -*- coding: utf-8 -*-
"""emx_runner.py - EMX 6.0 调用、进程文件派生、sNp 提取与收敛扫描。

实测要点（SMIC55LL + EMX 6.0）：
  * 端口来自 GDS 文本标签；EMX 按端口名字典序输出（ct < p01 < p02）。
  * 标签必须距最近金属边 <= --label-snap-distance（默认 4um）；
    焊盘中心到边 8um，所以本框架统一用 10。
  * PDK 的 MnTXT 文本在 122:3 / 120:3，而 EMX 自带 proc 只认 t0；
    需要派生 proc：define TM2 = fill(l122t0,fillsize)+l122t3。
  * 差分模式 --mode=+p01-p02 只改内部激励，最终 sNp 仍按单端端口给出。
"""
from __future__ import print_function

import glob
import json
import os
import shutil
import subprocess
import time

import sparam

DEF_EMX = "/opt/eda/cadence/INTEGRAND60/bin/emx"
DEF_PDK = ("/home/IC/Tech/SMIC55LL_2023/installed_PDK/"
           "smic55ll_ulp_rf_09121825_oa_cds_v1.16_2")


def find_emx(cfg=None):
    p = os.environ.get("EMX_BIN")
    if p and os.path.isfile(p):
        return p
    p = ((cfg or {}).get("emx") or {}).get("bin")
    if p and os.path.isfile(p):
        return p
    if os.path.isfile(DEF_EMX):
        return DEF_EMX
    raise RuntimeError("找不到 emx 可执行文件（设 EMX_BIN 或 config.emx.bin）")


def find_proc(cfg=None):
    """定位 EMX proc。PDK 里有 40+ 套金属叠层，必须显式选中一套：
    cfg.emx.proc 给绝对路径最优先；否则用 proc_glob + stack 子串过滤，
    过滤后必须唯一，避免静默选到别的叠层（TM2 厚度不同，L/Q 全错）。
    """
    cfg = cfg or {}
    ecfg = cfg.get("emx") or {}
    p = ecfg.get("proc")
    if p:
        return p
    pdk = cfg.get("pdk") or DEF_PDK
    pats = ecfg.get("proc_glob") or "*_TYP.proc"
    stack = ecfg.get("stack")
    hits = sorted(glob.glob(os.path.join(pdk, "models", "EMX_tech_file", "*", pats)))
    if not hits:
        hits = sorted(glob.glob(os.path.join(pdk, "**", pats), recursive=True))
    if stack:
        hits = [h for h in hits if stack in h]
    if not hits:
        raise RuntimeError("找不到 EMX proc 文件（%s/%s, stack=%s）" % (pdk, pats, stack))
    if len(hits) > 1:
        raise RuntimeError("EMX proc 不唯一（%d 个），请设 emx.proc 或 emx.stack：%s"
                           % (len(hits), hits[:5]))
    return hits[0]


def proc_info(path):
    """读 proc 的 technology / TM2_thick，写进报告用于复现核对。"""
    info = {"path": path}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith("#") and "technology" in s:
                    info["technology"] = s.split(":=", 1)[-1].strip()
                elif s.startswith("define TM2_thick"):
                    info["TM2_thick"] = s.split("=", 1)[-1].strip()
                if "technology" in info and "TM2_thick" in info:
                    break
    except OSError as e:
        info["error"] = str(e)
    return info


def derive_proc(src_proc, dst_proc, text_layers):
    """给 proc 的 define 行补 text datatype，例如 TM2 -> +l122t3。

    text_layers: {'TM2': [(122,3)], 'TM1': [(120,3)]}
    返回 (dst, n_changed)。
    """
    with open(src_proc, "r", encoding="utf-8") as f:
        lines = f.readlines()
    n = 0
    out = []
    for ln in lines:
        s = ln.strip()
        changed = False
        for name, dts in text_layers.items():
            prefix = "define %s =" % name
            has_any = ("+l%dt%d" % (dts[0][0], dts[0][1])) in s
            if s.startswith(prefix) and not has_any:
                add = "".join("+l%dt%d" % (l, d) for (l, d) in dts
                              if ("+l%dt%d" % (l, d)) not in s)
                if add:
                    ln = ln.rstrip() + add + "\n"
                    n += 1
                    changed = True
                    break
        out.append(ln)
    with open(dst_proc, "w", encoding="utf-8") as f:
        f.writelines(out)
    return dst_proc, n


def parse_port_order(text, expected):
    """从 EMX stderr 的矩阵表头抓端口顺序；失败则用字典序。"""
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        toks = s.split()
        if len(toks) >= 2 and all(t in expected for t in toks):
            if sorted(toks) == sorted(expected):
                return toks
    return sorted(expected)


def build_cmd(emx, gds, cell, proc, out_snp, freqs, edge=0.5, three_d="*",
              mode=None, ports=None, label_snap=10.0, extra=None):
    cmd = [emx, "--format=touchstone", "--s-file=%s" % os.path.abspath(out_snp)]
    if three_d:
        cmd.append("--3d=%s" % three_d if str(three_d) not in ("1", "true", "") else "--3d")
    if edge is not None:
        cmd += ["-e", "%g" % float(edge)]
    if label_snap is not None:
        cmd.append("--label-snap-distance=%g" % float(label_snap))
    if mode:
        cmd.append("--mode=%s" % mode)
    for p in (ports or []):
        cmd += ["-p", "%s=%s" % (p, p)]
    cmd += [str(x) for x in (extra or [])]
    cmd += [os.path.abspath(gds), str(cell), os.path.abspath(proc)]
    cmd += ["%.10g" % float(f) for f in freqs]
    return cmd


def run(cfg, gds, cell, ports, kind="ind", run_dir=None, freqs=None, edge=None,
        three_d=None, mode=None, label_snap=None, timeout=600, tag="", extra=None):
    """跑一次 EMX，返回 {'ok','cmd','rc','elapsed','snp','port_order','metrics','log'}。"""
    ecfg = cfg.get("emx") or {}
    freqs = freqs or ecfg.get("freqs") or [1e9, 2.4e9, 5e9]
    edge = ecfg.get("edge", 0.5) if edge is None else edge
    three_d = ecfg.get("three_d", "*") if three_d is None else three_d
    label_snap = ecfg.get("label_snap", 10.0) if label_snap is None else label_snap
    f0 = ecfg.get("f0", 2.4e9)
    emx = find_emx(cfg)
    proc = find_proc(cfg)
    run_dir = run_dir or os.path.join(os.getcwd(), "emx_run")
    os.makedirs(run_dir, exist_ok=True)

    # 文本层：PDK 用 122:3/120:3；如果 proc 只认 t0 就派生一份。
    # 键名 -> proc 层名；foundry 的 B 端标签在 M1/M2/M3 的 141/142/143。
    tcfg = cfg.get("text") or {}
    name_map = {"spiral": "TM2", "under": "TM1", "alpa": "ALPA",
                "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4",
                "m5": "M5", "m6": "M6", "m7": "M7", "m8": "M8"}
    dts = {}
    for key, name in name_map.items():
        if tcfg.get(key):
            dts[name] = [tuple(tcfg[key])]
    # 主/副金属可换时端口标签画在各金属自己的文本层（config stack 表）；
    # 只看 text 表会让 M6/M5 上的标签在 EMX 里“不存在”。
    for _nm, _m in ((cfg.get("stack") or {}).get("metals") or {}).items():
        _tg = _m.get("text_gds")
        if _tg:
            dts[str(_nm)] = [tuple(_tg)]
    proc_use = proc
    derived = 0
    if dts:
        proc_use, derived = derive_proc(proc, os.path.join(run_dir, "proc_eval.proc"), dts)

    snp = os.path.join(run_dir, (tag + "_" if tag else "") + "out.s%dp" % len(ports))
    cmd = build_cmd(emx, gds, cell, proc_use, snp, freqs, edge=edge, three_d=three_d,
                    mode=mode, ports=ports, label_snap=label_snap, extra=extra)
    with open(os.path.join(run_dir, "cmd.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(cmd) + "\n")
    t0 = time.time()
    try:
        pr = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout)
        out = pr.stdout.decode("utf-8", "replace")
        err = pr.stderr.decode("utf-8", "replace")
        rc = pr.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        err = (e.stderr or b"").decode("utf-8", "replace")
        rc = -9
    elapsed = time.time() - t0
    with open(os.path.join(run_dir, "emx.out"), "w", encoding="utf-8") as f:
        f.write(out)
    with open(os.path.join(run_dir, "emx.err"), "w", encoding="utf-8") as f:
        f.write(err)

    order = parse_port_order(err + out, ports)
    res = {"ok": False, "cmd": cmd, "rc": rc, "elapsed": round(elapsed, 3),
           "snp": snp, "port_order": order, "proc": proc_use, "proc_src": proc,
           "proc_info": proc_info(proc), "derived": derived,
           "run_dir": run_dir, "freqs": freqs}
    if rc != 0 or not os.path.isfile(snp) or os.path.getsize(snp) == 0:
        res["error"] = (err.strip().splitlines() or ["emx 失败（rc=%s）" % rc])[-1]
        return res
    parsed = sparam.parse_touchstone(snp)
    metrics = sparam.analyze(parsed, kind, f0=f0, port_order=order)
    res["ok"] = True
    res["nports"] = parsed["nports"]
    res["nfreq"] = len(parsed["freqs"])
    res["metrics"] = _jsonable(metrics)
    return res


def convergence(cfg, gds, cell, ports, kind="ind", run_dir=None, edges=(1.0, 0.5, 0.25),
                three_ds=(None, None), f0=None):
    """edge/3D 收敛：返回每次结果与相对变化。"""
    f0 = f0 or (cfg.get("emx") or {}).get("f0", 2.4e9)
    base = None
    rows = []
    for i, e in enumerate(edges):
        r = run(cfg, gds, cell, ports, kind=kind,
                run_dir=os.path.join(run_dir or ".", "edge_%g" % e),
                edge=e, tag="e%g" % e)
        row = {"edge": e, "ok": r["ok"], "elapsed": r["elapsed"]}
        if r["ok"]:
            row["L"] = r["metrics"].get("L@f0") or r["metrics"].get("Ldiff@f0")
            row["Q"] = r["metrics"].get("Q@f0") or r["metrics"].get("Qdiff@f0")
            if base is None:
                base = (row["L"], row["Q"])
            if base[0]:
                row["dL_pct"] = 100.0 * (row["L"] - base[0]) / base[0]
            if base[1]:
                row["dQ_pct"] = 100.0 * (row["Q"] - base[1]) / base[1]
        rows.append(row)
    return {"rows": rows, "base_f0": f0}


def _jsonable(o):
    import math
    if isinstance(o, complex):
        return [o.real, o.imag]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="EMX 单次运行 + 指标")
    ap.add_argument("gds")
    ap.add_argument("cell")
    ap.add_argument("--ports", default="p01,p02")
    ap.add_argument("--kind", default="ind", choices=["ind", "se", "diff", "ct"])
    ap.add_argument("--run-dir", default="emx_run")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8")) if a.config else {}
    r = run(cfg, a.gds, a.cell, a.ports.split(","), kind=a.kind, run_dir=a.run_dir)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    raise SystemExit(0 if r["ok"] else 1)
