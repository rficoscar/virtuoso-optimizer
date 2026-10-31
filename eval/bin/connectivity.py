# -*- coding: utf-8 -*-
"""connectivity.py - 版图连通性追踪（跨层并查集），替代 LVS 做端口/短路门禁。

模型：
  * 同层金属图形重叠/接触 -> 同一网络；
  * 过孔图形同时落在下层与上层金属里 -> 把两层连起来；
  * 文本标签（按层号匹配，忽略 datatype）落在哪个图形里 -> 该图形所属网络
    就是该端口所在的网络。
输出：
  nets:   [{'ports':[...], 'n_shapes':int, 'layers':[...], 'bbox':[...]}]
  floating: 没有端口标签的网络数
  shorts:  同一网络里出现 >=2 个端口（2 端电感本就是一根导体，属正常）
"""
from __future__ import print_function

import gdsread
import geometry as G


def build(flat, layers, pairs=None, text_layers=None):
    """layers: {'spiral':(122,0),'under':(120,0),'via':(123,0),'lower':(68,0),'via2':(121,0)}

    pairs: {'via':['spiral','under'],'via2':['under','lower']}；缺省单层。
    text_layers: {role:(layer,datatype)}；主/副金属文本层与导体层号不同时
    （如 M6 导体 66、MnTXT 146）必须显式给，缺省仍按同层号匹配。
    过孔只与它声明的那两层金属缝合（2D 投影下不同层的假重叠不算连接）。
    """
    pairs = pairs or {"via": ["spiral", "under"]}
    text_layers = text_layers or {}
    via_roles = list(pairs.keys())
    metal_roles = [r for r in layers if r not in via_roles]
    groups = {}
    for role, ld in layers.items():
        groups[role] = [s for s in gdsread.elements_at(flat, ld[0], ld[1])]
    all_shapes = []
    index = {}
    for role in metal_roles + via_roles:
        for s in groups.get(role, []):
            s["_role"] = role
            index[id(s)] = len(all_shapes)
            all_shapes.append(s)
    parent = list(range(len(all_shapes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    boxes = [G.bbox(s["pts"]) for s in all_shapes]
    # 同层互连
    for i in range(len(all_shapes)):
        if all_shapes[i]["_role"] in via_roles:
            continue
        for j in range(i + 1, len(all_shapes)):
            if all_shapes[j]["_role"] in via_roles:
                continue
            if all_shapes[i]["_role"] != all_shapes[j]["_role"]:
                continue
            if not G.bbox_overlap(boxes[i], boxes[j], 1e-6):
                continue
            if G.polys_overlap(all_shapes[i]["pts"], all_shapes[j]["pts"]):
                union(i, j)
    # 过孔连接：只连本过孔声明的两层
    for i, s in enumerate(all_shapes):
        if s["_role"] not in via_roles:
            continue
        want = set(pairs[s["_role"]])
        for j, t in enumerate(all_shapes):
            if t["_role"] not in want:
                continue
            if not G.bbox_overlap(boxes[i], boxes[j], 1e-6):
                continue
            if G.polys_overlap(s["pts"], t["pts"]):
                union(i, j)

    nets = {}
    for i, s in enumerate(all_shapes):
        r = find(i)
        nets.setdefault(r, {"idx": [], "layers": set()})
        nets[r]["idx"].append(i)
        nets[r]["layers"].add(s["_role"])

    # 端口标签
    by_role = {r: groups.get(r, []) for r in metal_roles}
    for t in flat["texts"]:
        for role, pool in by_role.items():
            if not pool:
                continue
            tl = text_layers.get(role)
            if tl is not None:
                if (t["layer"], t["datatype"]) != tuple(tl):
                    continue
            elif t["layer"] != pool[0]["layer"]:
                continue
            for s in pool:
                if G.point_in_poly(t["xy"], s["pts"]):
                    r = find(index[id(s)])
                    nets.setdefault(r, {"idx": [], "layers": set()})
                    nets[r].setdefault("ports", []).append(t["string"])
                    break

    out = []
    for r, n in nets.items():
        shapes = [all_shapes[i] for i in n["idx"]]
        b = G.bbox_union([G.bbox(s["pts"]) for s in shapes])
        out.append({"ports": sorted(n.get("ports", [])), "n_shapes": len(shapes),
                    "layers": sorted(n["layers"]), "bbox": [round(v, 3) for v in b]})
    out.sort(key=lambda n: -n["n_shapes"])
    floating = [n for n in out if not n["ports"]]
    shorted = [n for n in out if len(n["ports"]) > 1]
    return {"nets": out, "n_nets": len(out), "floating": len(floating),
            "multi_port_nets": shorted}


def check(flat, layers, pairs=None, expect_ports=None, text_layers=None):
    c = build(flat, layers, pairs, text_layers)
    ports = sorted(p for n in c["nets"] for p in n["ports"])
    ok_ports = (sorted(expect_ports) == ports) if expect_ports else True
    status = "pass"
    detail = "网络 %d，端口 %s，悬空金属 %d" % (c["n_nets"], ports, c["floating"])
    if not ok_ports:
        status = "fail"
        detail += "（期望端口 %s）" % sorted(expect_ports)
    elif c["floating"]:
        status = "warn"
    return {"id": "G7_connectivity", "status": status, "value": c,
            "limit": {"ports": expect_ports}, "detail": detail}


if __name__ == "__main__":
    import json
    import sys
    gds, cell = sys.argv[1], sys.argv[2]
    lay = {"spiral": (122, 0), "under": (120, 0), "via": (123, 0),
           "lower": (68, 0), "via2": (121, 0)}
    pairs = {"via": ["spiral", "under"], "via2": ["under", "lower"]}
    flat = gdsread.flatten(gdsread.read_gds(gds), cell)
    print(json.dumps(build(flat, lay, pairs), indent=2, ensure_ascii=False))
