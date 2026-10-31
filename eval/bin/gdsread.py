# -*- coding: utf-8 -*-
"""gdsread.py - 纯标准库 GDSII 读取 + 展平（供几何检查与连通性追踪）。

只解析评估需要的记录：BOUNDARY / PATH / TEXT / SREF，坐标统一换算成 um。
用法：
    g = read_gds("x.gds")
    cell = g["cells"]["TOP"]                 # 原始（未展平）
    flat = flatten(g, "TOP")                 # 展平后的元素列表
"""
from __future__ import print_function

import math
import struct

# record types
R_HEADER, R_BGNLIB, R_LIBNAME, R_UNITS, R_ENDLIB = 0x00, 0x01, 0x02, 0x03, 0x04
R_BGNSTR, R_STRNAME, R_ENDSTR = 0x05, 0x06, 0x07
R_BOUNDARY, R_PATH, R_SREF, R_AREF, R_TEXT, R_LAYER, R_DATATYPE = 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E
R_WIDTH, R_XY, R_ENDEL, R_SNAME, R_COLROW = 0x0F, 0x10, 0x11, 0x12, 0x13
R_TEXTTYPE, R_PRESENTATION, R_STRING = 0x16, 0x17, 0x19
R_STRANS, R_MAG, R_ANGLE = 0x1A, 0x1B, 0x1C


def _real8(b):
    if len(b) < 8:
        raise ValueError("real8 长度不足")
    s = b[0]
    if s & 0x80:
        raise ValueError("GDS real8 不支持负指数符号（异常数据）")
    exp = (s & 0x7F) - 64
    mant = 0
    for c in b[1:]:
        mant = (mant << 8) | c
    if mant == 0:
        return 0.0
    return mant / float(1 << 56) * (16.0 ** exp)


def _i16(b):
    return struct.unpack(">h", b)[0]


def _bitarray(b):
    return struct.unpack(">H", b)[0]


def _xy(b):
    n = len(b) // 8
    return [(struct.unpack(">i", b[i * 8:i * 8 + 4])[0],
             struct.unpack(">i", b[i * 8 + 4:i * 8 + 8])[0]) for i in range(n)]


def _read_records(raw):
    i, out = 0, []
    while i + 4 <= len(raw):
        ln = struct.unpack(">H", raw[i:i + 2])[0]
        if ln < 4:
            break
        rt = raw[i + 2]
        dt = raw[i + 3]
        data = raw[i + 4:i + ln]
        out.append((rt, dt, data))
        i += ln
    return out


def read_gds(path):
    """返回 {'libname','units':(db_in_user,db_in_m),'cells':{name:{...}}}。

    cell 内元素坐标已换算为 um：
      boundaries: [{'layer','datatype','pts':[(x,y)...]}]
      paths:      [{'layer','datatype','width','pts'}]
      texts:      [{'layer','datatype','xy':(x,y),'string','size'}]
      srefs:      [{'name','xy','mag','angle','mirror'}]
    """
    with open(path, "rb") as f:
        raw = f.read()
    recs = _read_records(raw)
    units = (0.001, 1e-9)
    db_in_user = 0.001
    libname = ""
    cells = {}
    cur = None
    el = None
    for rt, dt, data in recs:
        if rt == R_UNITS:
            units = (_real8(data[0:8]), _real8(data[8:16]))
            db_in_user = units[0]
        elif rt == R_LIBNAME:
            libname = data.rstrip(b"\x00").decode("latin-1")
        elif rt == R_BGNSTR:
            cur = None
        elif rt == R_STRNAME:
            name = data.rstrip(b"\x00").decode("latin-1")
            cells[name] = {"boundaries": [], "paths": [], "texts": [], "srefs": []}
            cur = cells[name]
        elif rt == R_BOUNDARY:
            el = {"kind": "boundary", "layer": 0, "datatype": 0, "pts": []}
        elif rt == R_PATH:
            el = {"kind": "path", "layer": 0, "datatype": 0, "width": 0.0, "pts": []}
        elif rt == R_TEXT:
            el = {"kind": "text", "layer": 0, "datatype": 0, "xy": (0, 0),
                  "string": "", "size": 0.0}
        elif rt == R_SREF:
            el = {"kind": "sref", "name": "", "xy": (0, 0), "mag": 1.0,
                  "angle": 0.0, "mirror": False}
        elif rt == R_LAYER and el is not None:
            el["layer"] = _i16(data)
        elif rt == R_DATATYPE and el is not None:
            el["datatype"] = _i16(data)
        elif rt == R_TEXTTYPE and el is not None:
            el["datatype"] = _i16(data)
        elif rt == R_WIDTH and el is not None:
            # GDS 规范里 WIDTH 可以是 2 字节（旧）或 4 字节（现行）；
            # strmout/Calibre 写的是 4 字节，早期 gdsgen 写 2 字节。
            if len(data) == 4:
                el["width"] = struct.unpack(">i", data)[0] * db_in_user
            else:
                el["width"] = _i16(data) * db_in_user
        elif rt == R_XY and el is not None:
            el["pts"] = [(x * db_in_user, y * db_in_user) for (x, y) in _xy(data)]
            if el["kind"] in ("text", "sref"):
                el["xy"] = el["pts"][0]
        elif rt == R_STRING and el is not None:
            el["string"] = data.rstrip(b"\x00").decode("latin-1")
        elif rt == R_SNAME and el is not None:
            el["name"] = data.rstrip(b"\x00").decode("latin-1")
        elif rt == R_STRANS and el is not None:
            el["mirror"] = bool(_bitarray(data) & 0x8000)
        elif rt == R_MAG and el is not None:
            el["mag"] = _real8(data)
        elif rt == R_ANGLE and el is not None:
            el["angle"] = _real8(data)
        elif rt == R_ENDEL and el is not None and cur is not None:
            if el["kind"] == "boundary":
                cur["boundaries"].append(el)
            elif el["kind"] == "path":
                cur["paths"].append(el)
            elif el["kind"] == "text":
                cur["texts"].append(el)
            elif el["kind"] == "sref":
                cur["srefs"].append(el)
            el = None
        elif rt == R_ENDSTR:
            cur = None
    return {"libname": libname, "units": units, "cells": cells}


def _xform(pt, sref):
    x, y = pt
    if sref.get("mirror"):
        x = -x
    m = float(sref.get("mag") or 1.0)
    x, y = x * m, y * m
    a = math.radians(float(sref.get("angle") or 0.0))
    if a:
        ca, sa = math.cos(a), math.sin(a)
        x, y = x * ca - y * sa, x * sa + y * ca
    ox, oy = sref["xy"]
    return (x + ox, y + oy)


def flatten(g, top, _depth=0, _seen=None):
    """展平 top cell（SREF 递归），返回 {'boundaries','paths','texts'}。"""
    cells = g["cells"]
    if top not in cells:
        raise KeyError("GDS 里没有 cell %r（现有：%s）" % (top, ", ".join(sorted(cells))))
    _seen = _seen or ()
    if top in _seen:
        raise ValueError("SREF 循环引用: %s" % " -> ".join(_seen + (top,)))
    if _depth > 32:
        raise ValueError("SREF 嵌套过深")
    src = cells[top]
    out = {"boundaries": [], "paths": [], "texts": []}
    for b in src["boundaries"]:
        out["boundaries"].append({"layer": b["layer"], "datatype": b["datatype"],
                                  "pts": list(b["pts"])})
    for p in src["paths"]:
        out["paths"].append({"layer": p["layer"], "datatype": p["datatype"],
                             "width": p["width"], "pts": list(p["pts"])})
    for t in src["texts"]:
        out["texts"].append({"layer": t["layer"], "datatype": t["datatype"],
                             "xy": t["xy"], "string": t["string"], "size": t["size"]})
    for s in src["srefs"]:
        sub = flatten(g, s["name"], _depth + 1, _seen + (top,))
        for b in sub["boundaries"]:
            out["boundaries"].append({"layer": b["layer"], "datatype": b["datatype"],
                                      "pts": [_xform(p, s) for p in b["pts"]]})
        for p in sub["paths"]:
            out["paths"].append({"layer": p["layer"], "datatype": p["datatype"],
                                 "width": p["width"] * float(s.get("mag") or 1.0),
                                 "pts": [_xform(q, s) for q in p["pts"]]})
        for t in sub["texts"]:
            out["texts"].append({"layer": t["layer"], "datatype": t["datatype"],
                                 "xy": _xform(t["xy"], s), "string": t["string"],
                                 "size": t["size"]})
    return out


def elements_at(flat, layer, datatype=None):
    """取某层（可选 datatype）的 boundary+path，path 近似为中心线宽度的矩形串。"""
    out = []
    for b in flat["boundaries"]:
        if b["layer"] == layer and (datatype is None or b["datatype"] == datatype):
            out.append(b)
    for p in flat["paths"]:
        if p["layer"] == layer and (datatype is None or p["datatype"] == datatype):
            out.append({"layer": p["layer"], "datatype": p["datatype"],
                        "pts": _path_poly(p["pts"], p["width"])})
    return out


def _offset_side(pts, d):
    """折线单侧偏移 d（左法向为正），拐角取两偏移线交点（斜接）。

    与 Virtuoso dbCreatePath(pathType=0, 平头端点) 的金属外形一致：
    端点处偏移点由垂线决定，相邻段之间取斜接。
    """
    n = len(pts)
    if n == 0:
        return []
    out = []
    for i in range(n):
        segs = []
        if i > 0:
            segs.append((pts[i - 1], pts[i]))
        if i < n - 1:
            segs.append((pts[i], pts[i + 1]))
        if len(segs) == 1:
            a, b = segs[0]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy) or 1.0
            out.append((pts[i][0] - dy / L * d, pts[i][1] + dx / L * d))
            continue
        (p0, p1), (p1b, p2) = segs
        d1x, d1y = p1[0] - p0[0], p1[1] - p0[1]
        L1 = math.hypot(d1x, d1y) or 1.0
        d2x, d2y = p2[0] - p1[0], p2[1] - p1[1]
        L2 = math.hypot(d2x, d2y) or 1.0
        n1x, n1y = -d1y / L1 * d, d1x / L1 * d
        n2x, n2y = -d2y / L2 * d, d2x / L2 * d
        # 两条偏移线求交
        ax, ay = p1[0] + n1x, p1[1] + n1y
        bx, by = p1[0] + n2x, p1[1] + n2y
        det = d1x * (-d2y) - d1y * (-d2x)
        if abs(det) < 1e-12:
            out.append((ax, ay))
            continue
        rx, ry = bx - ax, by - ay
        t = (rx * (-d2y) - ry * (-d2x)) / det
        out.append((ax + t * d1x, ay + t * d1y))
    return out


def _path_poly(pts, w):
    """PATH -> 金属外形多边形（两侧偏移 + 平头端点）。"""
    if len(pts) < 2:
        return list(pts)
    h = 0.5 * float(w or 0.0)
    left = _offset_side(pts, h)
    right = _offset_side(pts, -h)
    return left + list(reversed(right))


if __name__ == "__main__":
    import json
    import sys
    g = read_gds(sys.argv[1])
    top = sys.argv[2] if len(sys.argv) > 2 else sorted(g["cells"])[0]
    f = flatten(g, top)
    print(json.dumps({"lib": g["libname"], "units": g["units"], "top": top,
                      "cells": sorted(g["cells"]),
                      "n_boundaries": len(f["boundaries"]),
                      "n_texts": len(f["texts"]),
                      "texts": [(t["string"], t["layer"], t["datatype"], t["xy"])
                                for t in f["texts"]]}, indent=2, ensure_ascii=False))
