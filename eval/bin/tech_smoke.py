# -*- coding: utf-8 -*-
"""tech_smoke.py - 换工艺冒烟：按 tech/<key>.json 生成的档构建一版真实 PCell。

    python3 eval/bin/tech_smoke.py demo2m \
        --config eval/config_demo2m.json --work /abs/path/tech_demo2m

前置（tools/gen_tech.py 已跑过）：
    tech/<key>_profile.il  stream/<key>.map  eval/config_<key>.json

流程：skillgen(tech=key) 建 cell -> strmout 用 <key>.map 导出 GDS ->
      geometry 检查（层角色按 config.stack 表重解析）。
不跑 EMX/DRC：冒烟只验证「换工艺后几何链路仍然成立」。
"""
from __future__ import print_function

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gdsread            # noqa: E402
import geometry           # noqa: E402
import report as report_mod  # noqa: E402
import skillgen           # noqa: E402

DEF_CWD = os.getcwd()

BASE = {"rx": 60.0, "ry": 60.0, "n": 2, "w": 8.0, "s": 3.0,
        "chamfer": 0.5857864376, "lead_len": 20.0, "lead_gap": 3.0,
        "lead_w": 8.0, "ct": 1}


def main(argv=None):
    ap = argparse.ArgumentParser(description="换工艺冒烟（SKILL PCell + 几何检查）")
    ap.add_argument("tech", help="工艺档 key（tech/<key>_profile.il）")
    ap.add_argument("--config", default=None,
                    help="评估配置；缺省 eval/config_<key>.json")
    ap.add_argument("--work", default=None, help="工作目录（必须是绝对路径）")
    ap.add_argument("--params", default=None, help="覆盖几何参数 JSON")
    ap.add_argument("--cwd", default=DEF_CWD, help="librf.il/lri_pcell.il 所在目录")
    a = ap.parse_args(argv)

    cfg_path = a.config or os.path.join(HERE, "..", "config_%s.json" % a.tech)
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    work = os.path.abspath(a.work or os.path.join(HERE, "..", "runs", "tech_" + a.tech))
    params = dict(BASE)
    if a.params:
        params.update(json.loads(a.params))

    case = {"id": "tech_" + a.tech, "kind": "sym", "params": params}
    map_path = os.path.join(a.cwd, "stream", a.tech + ".map")
    r = skillgen.build(case, work, cwd=a.cwd, map_path=map_path, tech=a.tech)
    out = {"tech": a.tech, "build": r}
    if not r.get("ok"):
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 1

    flat = gdsread.flatten(gdsread.read_gds(r["gds"]), r["cell"])
    # 期望端口随结构走：中心抽头 ct=1 时 PCell 会多打一个 ct1 标签。
    # config 里的 expect.ports 是出厂〔p01,p02〕，冒烟用例按参数修正。
    ports = ["p01", "p02"] + (["ct1"] if params.get("ct") else [])
    cfg_case = dict(cfg, params=params,
                    expect=dict(cfg.get("expect") or {}, ports=ports))
    info = {"ports": sorted(t["string"] for t in flat["texts"]
                            if (t["layer"], t["datatype"]) == tuple(
                                (cfg.get("text") or {}).get("spiral") or (0, 3))),
            "bbox": None, "n": None, "stack": False}
    checks = geometry.run_all(flat, cfg_case)
    out["ports"] = info["ports"]
    out["checks"] = checks
    out["status"] = report_mod.grade(checks)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
