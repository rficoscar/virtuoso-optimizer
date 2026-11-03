#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump_geo.py - 从仓库内的 Python 几何内核倒出一份可供检查器使用的 geo.json

用法（在仓库根目录）：
  python .dsh/skills/virtuoso-pcell/scripts/dump_geo.py -o geo.json
  python .dsh/skills/virtuoso-pcell/scripts/dump_geo.py --w 8 --s 3 --n 3 --rx 80 --ry 60 -o geo.json
  python .dsh/skills/virtuoso-pcell/scripts/dump_geo.py --ct 1 --dout 200 -o ct.json

要点：lri_build_ind(g) 只返回**几何**（metal/under/under_poly/vias/ports/bbox）。
check_geometry.py 还需要设计参数 w/s/n/ct 才能判间距与圈数，所以这里把参数一起写进去。
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))


def _bootstrap(src=None):
    cands = []
    if src:
        cands.append(os.path.abspath(src))
    cands.append(os.path.join(REPO, "src"))
    cands.append(os.path.join(os.getcwd(), "src"))
    cands.append(os.getcwd())
    for c in cands:
        if os.path.isdir(os.path.join(c, "virtuoso_optimizer")):
            sys.path.insert(0, c)
            return c
    raise SystemExit("找不到 virtuoso_optimizer 包；用 --src <repo>/src 指定")


def main(argv=None):
    ap = argparse.ArgumentParser(description="dump sym inductor geometry to geo.json")
    ap.add_argument("--src", default=None, help="仓库 src 目录（默认从脚本位置推断）")
    ap.add_argument("-o", "--out", default="geo.json")
    ap.add_argument("--kind", default="sym", help="只有 sym 受支持")
    ap.add_argument("--w", type=float, default=8.0)
    ap.add_argument("--s", type=float, default=3.0)
    ap.add_argument("--n", type=float, default=3.0)
    ap.add_argument("--rx", type=float, default=80.0)
    ap.add_argument("--ry", type=float, default=60.0)
    ap.add_argument("--chamfer", type=float, default=None, help="缺省 2-sqrt(2)")
    ap.add_argument("--lead-len", type=float, default=20.0)
    ap.add_argument("--lead-gap", type=float, default=None, help="缺省 = s")
    ap.add_argument("--lead-w", type=float, default=None, help="缺省 = w")
    ap.add_argument("--ct", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    src = _bootstrap(a.src)
    from virtuoso_optimizer.lri import layout as L

    p = {
        "kind": a.kind, "w": a.w, "s": a.s, "n": a.n, "rx": a.rx, "ry": a.ry,
        "lead_len": a.lead_len, "ct": a.ct,
    }
    if a.chamfer is not None:
        p["chamfer"] = a.chamfer
    if a.lead_gap is not None:
        p["lead_gap"] = a.lead_gap
    if a.lead_w is not None:
        p["lead_w"] = a.lead_w

    ok, msg = L.lri_validate(dict(p))
    if not ok:
        sys.stderr.write("参数不可行（内核会直接拒绝，别改几何绕过）：%s\n" % msg)
        return 2
    g = L.lri_build_ind(dict(p))
    # 检查器需要设计参数；内核返回的只有几何
    g.update({k: p[k] for k in ("w", "s", "n", "ct")})
    g["params"] = dict(p)
    g["via"] = {"cut": L.SYM_VIA_CUT, "spacing": L.SYM_VIA_SPACE,
                "enclosure": L.SYM_VIA_ENCL}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(g, fh)
    if not a.quiet:
        print("src      :", src)
        print("out      :", a.out)
        print("N / bbox : %d / %s" % (g["N"], tuple(round(v, 3) for v in g["bbox"])))
        print("metal    : %d 条中心线, under %d 条, under_poly %d 块, vias %d"
              % (len(g["metal"]), len(g["under"]), len(g["under_poly"]), len(g["vias"])))
        print("ports    :", ", ".join("%s(%.2f,%.2f)" % (n, x, y) for n, (x, y) in g["ports"]))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
