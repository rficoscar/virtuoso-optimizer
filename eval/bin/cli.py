# -*- coding: utf-8 -*-
"""cli.py - PCell 质量评估命令行入口。

子命令：
  gen      生成受控 GDS（单端 / 中心抽头）
  geom     只跑几何检查
  emx      跑 EMX 并出 L/Q/R/SRF/混合模指标
  drc      Calibre DRC
  lvs      Calibre LVS（纯无源结构 -> 端口/网络提取）
  run      单条用例全流程（gen+geom+emx[+drc+lvs]）
  matrix   跑测试矩阵
  compare  两份结果对比（PCell vs foundry 基准）

示例：
  python3 cli.py run --config config_smic55.json --params '{"n":2.5,"w":8}' \
      --work work/ind_base --with-drc --with-lvs
  python3 cli.py matrix --config config_smic55.json --work work/matrix --with-drc
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time

import gdsgen
import gdsread
import geometry
import matrix as matrix_mod
import report as report_mod

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load_cfg(path):
    if not path:
        # 优先用 gen_tech 生成的 eval/config_smic55.json（带 stack 表）；
        # bin/ 下的旧配置仅是历史遗留，缺 stack 会让主/副金属解析失败。
        cands = [os.path.join(HERE, "..", "config_smic55.json"),
                 os.path.join(HERE, "config_smic55.json")]
        path = next((p for p in cands if os.path.isfile(p)), None)
    cfg = json.load(open(path, encoding="utf-8")) if path else {}
    return cfg


def _gen(cfg, case, work):
    os.makedirs(work, exist_ok=True)
    kind = case.get("kind", "ind")
    params = case.get("params") or {}
    cell = case.get("cell") or ("eval_%s" % case["id"])
    gds = os.path.join(work, cell + ".gds")
    if kind == "ext":
        gds = case["gds"]
        return gds, case["cell"], None
    if case.get("source") == "skill":
        # 真 SKILL PCell：virtuoso -nograph 建库 + strmout 可执行文件导出
        import skillgen
        r = skillgen.build(case, work)
        if not r.get("ok"):
            msg = str(r.get("error"))
            # virtuoso 没起来时错误只在进程输出里（license/锁/崩溃），别丢
            for k in ("stderr_tail", "stdout_tail"):
                if r.get(k):
                    msg += " [%s] %s" % (k, str(r[k]).strip().splitlines()[-1][:200])
            raise RuntimeError("skillgen 失败：%s" % msg)
        g = gdsread.read_gds(r["gds"])
        flat = gdsread.flatten(g, r["cell"])
        pr = case.get("params") or {}
        # 端口标签在主金属文本层；主金属可换，按用例参数解析
        cfgp = dict(cfg)
        cfgp["params"] = pr
        ctx = geometry.stack_roles(cfgp)
        if ctx:
            tsp = tuple((cfg.get("stack") or {})["metals"][ctx["main"]]["text_gds"])
        elif pr.get("metal_main"):
            # 指定了主金属却解析不出层角色：配置缺 stack 表，早期直接
            # fallback 到 TM2 会读出空螺旋层，后面只剩一个 NoneType 报错。
            raise RuntimeError(
                "配置缺少 stack 表，无法解析 metal_main=%s；"
                "请用 tools/gen_tech.py 生成 eval/config_<key>.json"
                % pr.get("metal_main"))
        else:
            lay0 = cfg.get("layers") or {}
            tsp = tuple((cfg.get("text") or {}).get("spiral")
                        or (lay0.get("spiral", (122, 0))[0], 3))
        ports = sorted(t["string"] for t in flat["texts"]
                       if (t["layer"], t["datatype"]) == tsp)
        bb = None
        if pr.get("rx") and pr.get("ry"):
            ry_ = float(pr["ry"])
            ll = float(pr.get("lead_len", 0.0))
            # I3 语义：lead_len 从内圈金属边 b0 = ry - w/2 起算（见 librf.il:3887）
            bb = [-float(pr["rx"]), -ry_, float(pr["rx"]),
                  ry_ - float(pr.get("w", 8.0)) / 2.0 + ll]
        leads = None
        if pr.get("rx") and pr.get("ry"):
            _w = float(pr.get("w", 8.0))
            _ll = float(pr.get("lead_len", 0.0))
            _lw = float(pr.get("lead_w", _w))
            _lg = float(pr.get("lead_gap", float(pr.get("s", 3.0))))
            leads = {"x": (_lg + _lw) / 2.0,
                     # 标签打在引线端头内侧 1.5um（librf.il:530 librfTipLabel）
                     "y": float(pr["ry"]) - _w / 2.0 + _ll - 1.5,
                     "w": _lw, "gap": _lg, "len": _ll}
        info = {"ports": ports, "bbox": bb, "n": None, "build": r,
                "leads": leads, "stack": bool(pr.get("stack"))}
        return r["gds"], r["cell"], info
    kw = dict(n=params.get("n", 2.5), w=params.get("w", 8.0), s=params.get("s", 3.0),
              dout=params.get("dout", 120.0), shape=params.get("shape", "oct"),
              chamfer=params.get("chamfer", 0.0), lead=params.get("lead", 12.0))
    lay = cfg.get("layers") or {}
    metal = tuple(lay.get("spiral", (122, 0)))
    under = tuple(lay.get("under", (120, 0)))
    via = tuple(lay.get("via", (123, 0)))
    txt = cfg.get("text") or {}
    text_dt = (txt.get("spiral") or [122, 3])[1]
    if kind == "ct":
        W, info = gdsgen.make_ct_spiral(cell, metal=metal, under=under, via=via,
                                        text_dt=text_dt, **kw)
    else:
        W, info = gdsgen.make_spiral(cell, metal=metal, under=under, via=via,
                                     text_dt=text_dt, **kw)
    W.write(gds)
    return gds, cell, info


def _geom(cfg, gds, cell, info=None, params=None):
    g = gdsread.read_gds(gds)
    flat = gdsread.flatten(g, cell)
    cfg2 = dict(cfg)
    # 主/副金属可换：几何检查按用例参数重解析层角色（见 geometry.stack_roles）
    cfg2["params"] = dict(params or {})
    exp = dict(cfg.get("expect") or {})
    if info:
        exp["bbox"] = [round(v, 3) for v in info["bbox"]]
        exp["ports"] = list(info["ports"])
        exp["turns"] = info["n"]
        exp["stack"] = bool(info.get("stack"))
        if info.get("leads"):
            exp["leads"] = info["leads"]
    cfg2["expect"] = exp
    checks = geometry.run_all(flat, cfg2)
    return checks, exp


def _emx(cfg, gds, cell, info, kind, work, edge=None):
    import emx_runner
    ports = (info or {}).get("ports") or ["p01", "p02"]
    return emx_runner.run(cfg, gds, cell, ports, kind=kind,
                          run_dir=os.path.join(work, "emx"), edge=edge)


def _drc(cfg, gds, cell, work):
    import calibre_runner
    return calibre_runner.run_drc(cfg, gds, cell, os.path.join(work, "drc"))


def _lvs(cfg, gds, cell, info, work):
    import calibre_runner
    ports = (info or {}).get("ports") or ["p01", "p02"]
    return calibre_runner.run_lvs(cfg, gds, cell, os.path.join(work, "lvs"), ports)


def cmd_run(a):
    cfg = _load_cfg(a.config)
    case = {"id": a.case or "case", "kind": a.kind,
            "params": json.loads(a.params) if a.params else {}}
    gds, cell, info = _gen(cfg, case, a.work)
    checks, exp = _geom(cfg, gds, cell, info, case.get("params"))
    emx = None
    try:
        emx = _emx(cfg, gds, cell, info, a.kind, a.work, edge=a.edge)
        drc = _drc(cfg, gds, cell, a.work) if a.with_drc else None
        lvs = _lvs(cfg, gds, cell, info, a.work) if a.with_lvs else None
        one = report_mod.summarize_case(case, checks, emx=emx, drc=drc, lvs=lvs)
    except Exception as e:
        one = {"id": case.get("id"), "kind": case.get("kind"),
               "params": case.get("params"), "status": "FAIL",
               "checks": list(checks) + [{"id": "case_error", "status": "fail",
                                          "value": repr(e), "limit": None,
                                          "detail": "用例异常终止（生成/求解失败）"}],
               "emx": None, "drc": None, "lvs": None}
    rep = {"config_hash": report_mod.config_hash(cfg),
           "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
           "status": one["status"], "cases": [one],
           "tool": {"emx": (emx or {}).get("cmd"), "elapsed": (emx or {}).get("elapsed"),
                    "proc": (emx or {}).get("proc_src"),
                    "proc_info": (emx or {}).get("proc_info")}}
    report_mod.write_json(os.path.join(a.work, "report.json"), rep)
    with open(os.path.join(a.work, "report.md"), "w", encoding="utf-8") as f:
        f.write(report_mod.summary_md(rep))
    print(report_mod.summary_md(rep))
    print("report: %s" % os.path.join(a.work, "report.json"))
    return 0 if one["status"] != "FAIL" else 1


def cmd_matrix(a):
    cfg = _load_cfg(a.config)
    if getattr(a, "suite", None) == "sym":
        rows = matrix_mod.build_sym()
    else:
        rows = matrix_mod.build((cfg.get("limits") or {}))
    if a.only:
        want = set(a.only.split(","))
        rows = [r for r in rows if r["id"] in want or (set(r.get("tags") or []) & want)]
    if a.max_cases:
        rows = rows[:a.max_cases]
    out = {"config_hash": report_mod.config_hash(cfg),
           "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": []}
    tool = {"emx": None, "elapsed": None, "proc": None, "proc_info": None}
    for case in rows:
        w = os.path.join(a.work, case["id"])
        print("== %s ==" % case["id"], flush=True)
        try:
            gds, cell, info = _gen(cfg, case, w)
            checks, exp = _geom(cfg, gds, cell, info, case.get("params"))
            kind = case.get("emx_kind") or (
                "ct" if (info or {}).get("ports") and len(info["ports"]) >= 3
                else "ind")
            emx = _emx(cfg, gds, cell, info, kind, w, edge=a.edge)
            if emx and emx.get("ok"):
                tool = {"emx": emx.get("cmd"), "elapsed": emx.get("elapsed"),
                        "proc": emx.get("proc_src"), "proc_info": emx.get("proc_info")}
            drc = _drc(cfg, gds, cell, w) if a.with_drc else None
            lvs = None
            one = report_mod.summarize_case(case, checks, emx=emx, drc=drc, lvs=lvs)
        except Exception as e:
            if case.get("expect_fail"):
                # 该参数组合在几何上不可行：构建被明确拒绝即视为通过
                one = {"id": case.get("id"), "kind": case.get("kind"),
                       "params": case.get("params"), "status": "PASS",
                       "checks": [{"id": "expected_reject", "status": "pass",
                                   "value": str(e)[:200], "limit": None,
                                   "detail": "参数不可行，构建按预期报错拒绝"}],
                       "emx": None, "drc": None, "lvs": None}
                print("   REJECT-OK %s" % str(e)[:120], flush=True)
            else:
                one = {"id": case.get("id"), "kind": case.get("kind"),
                       "params": case.get("params"), "status": "FAIL",
                       "checks": [{"id": "case_error", "status": "fail", "value": repr(e),
                                   "limit": None, "detail": "用例异常终止（生成/求解失败）"}],
                       "emx": None, "drc": None, "lvs": None}
                print("   ERROR %r" % e, flush=True)
        out["cases"].append(one)
        _m = one.get("emx") or {}
        print("   %s L=%s Q=%s" % (one["status"], _m.get("L@f0") or _m.get("Ldiff@f0"),
                                   _m.get("Q@f0") or _m.get("Qdiff@f0")), flush=True)
    st = [c["status"] for c in out["cases"]]
    out["tool"] = tool
    out["status"] = "FAIL" if "FAIL" in st else ("WARN" if "WARN" in st else "PASS")
    report_mod.write_json(os.path.join(a.work, "report.json"), out)
    with open(os.path.join(a.work, "report.md"), "w", encoding="utf-8") as f:
        f.write(report_mod.summary_md(out))
    print(report_mod.summary_md(out))
    return 0 if out["status"] != "FAIL" else 1


def cmd_gen(a):
    cfg = _load_cfg(a.config)
    case = {"id": a.case or "gen", "kind": a.kind,
            "params": json.loads(a.params) if a.params else {}}
    gds, cell, info = _gen(cfg, case, os.path.dirname(os.path.abspath(a.out)))
    if os.path.abspath(gds) != os.path.abspath(a.out):
        os.replace(gds, a.out)
    print(json.dumps({"gds": a.out, "cell": cell, "info": info}, indent=2, ensure_ascii=False))
    return 0


def cmd_geom(a):
    cfg = _load_cfg(a.config)
    checks, exp = _geom(cfg, a.gds, a.cell)
    print(json.dumps({"checks": checks, "expect": exp,
                      "status": report_mod.grade(checks)}, indent=2, ensure_ascii=False))
    return 0


def cmd_emx(a):
    cfg = _load_cfg(a.config)
    import emx_runner
    ports = a.ports.split(",") if a.ports else ["p01", "p02"]
    r = emx_runner.run(cfg, a.gds, a.cell, ports, kind=a.kind, run_dir=a.run_dir,
                       edge=a.edge)
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
    return 0 if r["ok"] else 1


def cmd_drc(a):
    cfg = _load_cfg(a.config)
    import calibre_runner
    r = calibre_runner.run_drc(cfg, a.gds, a.cell, a.run_dir)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0 if (r.get("summary") or {}).get("geometry_clean") else 1


def cmd_lvs(a):
    cfg = _load_cfg(a.config)
    import calibre_runner
    r = calibre_runner.run_lvs(cfg, a.gds, a.cell, a.run_dir, a.ports.split(","))
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0 if not r.get("unattached") else 1


def cmd_compare(a):
    A = json.load(open(a.a, encoding="utf-8"))
    B = json.load(open(a.b, encoding="utf-8"))
    ca = (A.get("cases") or [A])[0]
    cb = (B.get("cases") or [B])[0]
    ma, mb = ca.get("emx") or {}, cb.get("emx") or {}
    rows = []
    for key in ("L@f0", "Q@f0", "R@f0", "SRF", "Ldiff@f0", "Qdiff@f0", "SRF_diff"):
        va, vb = ma.get(key), mb.get(key)
        if va is None and vb is None:
            continue
        d = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va:
            d = 100.0 * (vb - va) / abs(va)
        rows.append({"metric": key, "a": va, "b": vb, "delta_pct": d})
    out = {"a": ca.get("id"), "b": cb.get("id"), "metrics": rows,
           "drc": {"a": (ca.get("drc") or {}).get("geometry"),
                   "b": (cb.get("drc") or {}).get("geometry")}}
    if a.out:
        report_mod.write_json(a.out, out)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="RF 电感 PCell 质量评估框架")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("gen"); p.add_argument("out"); p.add_argument("--config")
    p.add_argument("--kind", default="ind", choices=["ind", "ct"]); p.add_argument("--case")
    p.add_argument("--params"); p.set_defaults(fn=cmd_gen)

    p = sub.add_parser("geom"); p.add_argument("gds"); p.add_argument("cell")
    p.add_argument("--config"); p.set_defaults(fn=cmd_geom)

    p = sub.add_parser("emx"); p.add_argument("gds"); p.add_argument("cell")
    p.add_argument("--ports"); p.add_argument("--kind", default="ind",
                                              choices=["ind", "se", "diff", "ct"])
    p.add_argument("--run-dir", default="emx_run"); p.add_argument("--config")
    p.add_argument("--edge", type=float); p.set_defaults(fn=cmd_emx)

    p = sub.add_parser("drc"); p.add_argument("gds"); p.add_argument("cell")
    p.add_argument("--run-dir", default="drc_run"); p.add_argument("--config")
    p.set_defaults(fn=cmd_drc)

    p = sub.add_parser("lvs"); p.add_argument("gds"); p.add_argument("cell")
    p.add_argument("--ports", default="p01,p02"); p.add_argument("--run-dir", default="lvs_run")
    p.add_argument("--config"); p.set_defaults(fn=cmd_lvs)

    p = sub.add_parser("run"); p.add_argument("--config"); p.add_argument("--case")
    p.add_argument("--kind", default="ind", choices=["ind", "ct"])
    p.add_argument("--params"); p.add_argument("--work", default="work/case")
    p.add_argument("--edge", type=float)
    p.add_argument("--with-drc", action="store_true"); p.add_argument("--with-lvs", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("matrix"); p.add_argument("--config"); p.add_argument("--work", default="work/matrix")
    p.add_argument("--only"); p.add_argument("--max-cases", type=int)
    p.add_argument("--edge", type=float); p.add_argument("--with-drc", action="store_true")
    p.add_argument("--suite", default=None, help="sym = 真 SKILL 差分对称扫掠")
    p.set_defaults(fn=cmd_matrix)

    p = sub.add_parser("compare"); p.add_argument("a"); p.add_argument("b")
    p.add_argument("--out"); p.set_defaults(fn=cmd_compare)

    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
