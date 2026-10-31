# -*- coding: utf-8 -*-
"""skillgen.py - 用真实 SKILL PCell 生成 GDS（virtuoso -nograph + strmout 可执行文件）。

链路：
    .il 脚本 --virtuoso -nograph -replay--> 建 cellview + dbSave
            --strmout(独立可执行文件)--> GDS

为什么不用 SKILL 里的 strmout：
  batch virtuoso 会话没有 translator 上下文，(strmout ...) 报
  "eval: undefined function - strmout"；loadContext(transUI.cxt) 也不定义它，
  PDK 的 libInit.il 也不定义。xstOutDoTranslate 依赖 GUI 表单的字段状态。
  IC618 自带 /opt/eda/cadence/IC618/tools/dfII/bin/strmout 独立程序，
  读 cds.lib 定位库、用 -layerMap 指定层映射，是批处理下的正解。

层映射：官方 map 没有 pin purpose，pin 图形会被 XSTRM-35 丢掉；这里用一个
派生 map（官方 map + TM1/TM2/M8 的 pin 行），pin 落到未占用的 datatype 1。
"""
from __future__ import print_function

import os
import re
import shutil
import subprocess
import time

DEF_CWD = os.environ.get("LRI_WORKDIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src", "virtuoso_optimizer", "data")
DEF_LIB = "PCELL_RS4"
DEF_VIRTUOSO = os.environ.get("LIBRF_VIRTUOSO",
                              "/opt/eda/cadence/IC618/tools/dfII/bin/virtuoso")
DEF_STRMOUT = os.environ.get("LIBRF_STRMOUT",
                             "/opt/eda/cadence/IC618/tools/dfII/bin/strmout")
DEF_MAP_SRC = os.environ.get(
    "LIBRF_PDK_MAP",
    ("/home/IC/Tech/SMIC55LL_2023/installed_PDK/"
     "smic55ll_ulp_rf_09121825_oa_cds_v1.16_2/stream/"
     "SMIC_VTMP_55LGLLULP_0912182533_V1.13_REV1_0.map"))

# kind -> (构建函数, 是否差分/对称)
KIND_FN = {"sym": "lriI3BuildSym", "ct": "lriBuildInd", "se": "lriBuildInd",
           "ind": "lriBuildInd"}

_NUM_KEYS = ("rx", "ry", "n", "w", "s", "chamfer", "lead_len", "lead_gap",
             "lead_w", "ct", "stack", "dout", "W", "H", "corner", "stub",
             "gap", "seam_len")


def _skill_num(v):
    if isinstance(v, bool):
        return "t" if v else "nil"
    if isinstance(v, int):
        return "%d" % v
    t = "%.12g" % float(v)
    if ("." not in t) and ("e" not in t) and ("E" not in t):
        t += ".0"   # SKILL 里 "3" 是整数，%.3f 会报格式错
    return t


def _skill_req(params):
    """dict -> SKILL 属性列表 'k1 v1 k2 v2 ...'（canonical 括号，别用 f(...) 糖）。"""
    out = []
    for k, v in params.items():
        if k in _NUM_KEYS:
            out.append("'%s %s" % (k, _skill_num(v)))
        else:
            out.append("'%s \"%s\"" % (k, str(v)))
    return " ".join(out)


def layer_map(dst):
    """官方 map + pin 行（pin 图形落到 datatype 1，PDK 未占用）。"""
    if os.path.isfile(dst):
        return dst
    pin = ["TM2\tpin\t\t122\t\t1\n", "TM1\tpin\t\t120\t\t1\n",
           "M8\tpin\t\t68\t\t1\n", "M7\tpin\t\t67\t\t1\n"]
    with open(DEF_MAP_SRC, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    with open(dst, "w", encoding="utf-8") as f:
        f.writelines(lines + pin)
    return dst


def _pcellgen_cxt(virtuoso=None):
    """PCell 生成上下文：优先环境变量，否则从 virtuoso 安装树推。"""
    p = os.environ.get("LIBRF_PCELLGEN_CXT")
    if p:
        return p
    v = virtuoso or DEF_VIRTUOSO
    return os.path.join(os.path.dirname(os.path.dirname(v)),
                        "etc", "context", "64bit", "pCellGen.cxt")


def _il_script(cell, kind, params, lib, cwd, tech=None, cxt=None):
    fn = KIND_FN.get(kind, "lriBuildInd")
    req = _skill_req(params)
    techCall = ('(lriPcellTechLoad "%s")' % tech if tech
                else "(lriPcellTechSmic55)")
    return """(printf "SKILLGEN START %(cell)s\\n")
(errset (loadContext "%(cxt)s") nil)
(load "%(cwd)s/librf.il")
(load "%(cwd)s/lri_pcell.il")
(prog (cv req r bb nsh ntm)
  %(tech)s
  (when (ddGetObj "%(lib)s" "%(cell)s")
    (errset (ddDeleteObj (ddGetObj "%(lib)s" "%(cell)s")) t))
  cv = car(errset(dbOpenCellViewByType("%(lib)s" "%(cell)s" "layout" "maskLayout" "w") t))
  (if cv
    (progn
      req = list(%(req)s)
      r = car(errset(%(fn)s(cv req) t))
      bb = car(errset(cv~>bBox t))
      nsh = car(errset(length(cv~>shapes) t))
      ntm = car(errset(length(cv~>terminals) t))
      (printf "SKILLGEN RESULT ret=%%L errs=%%L bbox=%%L shapes=%%L terms=%%L tech=%%L\\n"
              r librf__errs bb nsh ntm librfTechName)
      (dbSave cv)
      (dbClose cv))
    (printf "SKILLGEN RESULT ret=nil errs=OPEN_FAIL\\n"))
  (printf "SKILLGEN DONE\\n"))
(exit 0)
""" % dict(cell=cell, cwd=cwd, lib=lib, req=req, fn=fn, tech=techCall,
         cxt=cxt or _pcellgen_cxt())


def _parse_result(txt):
    """从 virtuoso 日志里取 SKILLGEN RESULT 行（bbox 里带空格，按字段名切）。"""
    for ln in txt.splitlines():
        if not ln.startswith("\\o"):
            continue
        i = ln.find("SKILLGEN RESULT")
        if i < 0:
            continue
        s = ln[i:]
        d = {}
        for key in ("ret", "errs", "bbox", "shapes", "terms", "tech"):
            j = s.find(key + "=")
            if j < 0:
                continue
            k = j + len(key) + 1
            e = len(s)
            for other in ("errs=", "bbox=", "shapes=", "terms=", "tech="):
                q = s.find(other, k)
                if q > 0:
                    e = min(e, q)
            d[key] = s[k:e].strip()
        return d
    return None


def build(case, work, cfg=None, cwd=DEF_CWD, lib=DEF_LIB, timeout=600,
          virtuoso=DEF_VIRTUOSO, strmout=DEF_STRMOUT, map_path=None, tech=None):
    """case: {'cell':str,'kind':str,'params':dict}。返回 dict（ok/gds/...）。

    tech: 工艺档 key（<data>/tech/<key>_profile.il）；None = SMIC55 内置档。
    """
    work = os.path.abspath(work)
    os.makedirs(work, exist_ok=True)
    cell = case.get("cell") or ("eval_%s" % case["id"])
    kind = case.get("kind", "sym")
    params = case.get("params") or {}
    ilp = os.path.join(work, cell + ".il")
    logp = os.path.join(work, cell + "_virtuoso.log")
    gds = os.path.join(work, cell + ".gds")
    slog = os.path.join(work, cell + "_strmout.log")
    ssum = os.path.join(work, cell + "_strmout.sum")
    mp = layer_map(map_path or os.path.join(work, "pcell_rs4.map"))

    with open(ilp, "w", encoding="utf-8") as f:
        f.write(_il_script(cell, kind, params, lib, cwd, tech=tech))

    t0 = time.time()
    res = {"ok": False, "cell": cell, "kind": kind, "params": params,
           "il": ilp, "gds": gds, "work": work}
    vout = os.path.join(work, cell + "_virtuoso.out")
    verr = os.path.join(work, cell + "_virtuoso.err")
    # 输出重定向到文件而不是 PIPE：批处理 virtuoso 偶尔留下持有 stdout 的
    # 子进程，communicate() 会一直等到超时；文件没有这个阻塞点。
    # 批处理 virtuoso 还会偶发启动即静默退出（无 -log、无 stdout/stderr、
    # rc=0），同一 .il 手动重放就正常；真正的构建错误一定会在日志里留下
    # SKILLGEN RESULT ret=nil，所以"没有 RESULT 行"重试一次不掩盖参数错误。
    m = None
    txt = ""
    env = dict(os.environ)
    if os.path.isdir(os.path.join(cwd, "tech")):
        env.setdefault("LRI_TECH_DIR", os.path.join(cwd, "tech"))
    for attempt in (1, 2):
        try:
            with open(vout, "wb") as fo, open(verr, "wb") as fe:
                pr = subprocess.run([virtuoso, "-nograph", "-log", logp,
                                     "-replay", ilp], cwd=cwd, timeout=timeout,
                                    stdout=fo, stderr=fe, env=env)
        except subprocess.TimeoutExpired:
            res["error"] = "virtuoso 超时（%ss）" % timeout
            return res
        res["virtuoso_rc"] = pr.returncode
        txt = ""
        if os.path.isfile(logp):
            with open(logp, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        m = _parse_result(txt)
        if m or attempt == 2:
            break
        res["retried"] = True
        print("skillgen: %s 无 SKILLGEN RESULT（rc=%s），重试"
              % (cell, pr.returncode), flush=True)
    if not m:
        res["error"] = ("virtuoso 日志里没有 SKILLGEN RESULT（构建失败，rc=%s）"
                        % res.get("virtuoso_rc"))
        res["log_tail"] = txt[-1200:]
        # 进程级失败（锁、license、启动崩溃）不会写进 -log，补上进程输出
        for key, path in (("stdout_tail", vout), ("stderr_tail", verr)):
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    res[key] = f.read().decode("utf-8", "replace")[-600:]
        return res
    res["build_ret"] = m.get("ret")
    res["build_errs"] = m.get("errs")
    res["tech"] = m.get("tech")
    res["bbox"] = m.get("bbox")
    res["shapes"] = m.get("shapes")
    res["terms"] = m.get("terms")
    if m.get("ret") != "t":
        res["error"] = "PCell 构建返回 %s，errs=%s" % (m.get("ret"), m.get("errs"))
        return res

    sout = os.path.join(work, cell + "_strmout.out")
    serr = os.path.join(work, cell + "_strmout.err")
    with open(sout, "wb") as fo, open(serr, "wb") as fe:
        pr = subprocess.run([strmout, "-library", lib, "-topCell", cell, "-view",
                             "layout", "-strmFile", gds, "-layerMap", mp,
                             "-logFile", slog, "-summaryFile", ssum,
                             "-hierDepth", "32", "-convertPin", "ignore",
                             "-pathToPolygon", "-diagonalPathToPolygon"],
                            cwd=cwd, timeout=timeout, stdout=fo, stderr=fe)
    res["strmout_rc"] = pr.returncode
    if not os.path.isfile(gds) or os.path.getsize(gds) == 0:
        res["error"] = "strmout 没有产出 GDS（rc=%s）" % pr.returncode
        if os.path.isfile(slog):
            with open(slog, "r", encoding="utf-8", errors="replace") as f:
                res["log_tail"] = f.read()[-1200:]
        return res
    res["elapsed"] = round(time.time() - t0, 2)
    res["ok"] = True
    return res


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="SKILL PCell -> GDS")
    ap.add_argument("cell")
    ap.add_argument("--kind", default="sym")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--work", default=os.path.join(os.getcwd(), "runs", "skill"))
    ap.add_argument("--lib", default=DEF_LIB)
    a = ap.parse_args()
    r = build({"cell": a.cell, "kind": a.kind,
               "params": json.loads(a.params)}, a.work, lib=a.lib)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    raise SystemExit(0 if r["ok"] else 1)
