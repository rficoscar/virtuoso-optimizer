# -*- coding: utf-8 -*-
"""gdsaddtext.py - 给现成 GDS 的指定结构注入 TEXT 标签（纯标准库）。

用途：foundry 参考 PCell 的 GDS 没有端口标签，EMX 的 -p name=label 需要
文本标签；在结构末尾（ENDSTR 前）插入 TEXT 元素即可。

    python3 gdsaddtext.py in.gds out.gds --cell ind_rf \
        --text p01:122:3:12.765:-104 --text p02:122:3:-12.765:-104

坐标单位 um；GDS 单位从 UNITS 记录读取（通常 0.001um）。
"""
from __future__ import print_function

import struct
import sys

R_UNITS, R_BGNSTR, R_STRNAME, R_ENDSTR, R_ENDLIB = 0x03, 0x05, 0x06, 0x07, 0x04
R_TEXT, R_LAYER, R_TEXTTYPE, R_XY, R_STRING, R_ENDEL = 0x0C, 0x0D, 0x16, 0x10, 0x19, 0x11


def _real8(b):
    s = b[0]
    sign = -1.0 if (s & 0x80) else 1.0
    exp = (s & 0x7F) - 64
    mant = 0
    for c in b[1:]:
        mant = (mant << 8) | c
    if mant == 0:
        return 0.0
    return sign * mant / float(1 << 56) * (16.0 ** exp)


def _rec(rt, dt, data=b""):
    return struct.pack(">HBB", 4 + len(data), rt, dt) + data


def _text_elem(x, y, layer, datatype, string, db):
    data = string.encode("latin-1")
    if len(data) % 2:
        data += b"\x00"
    return b"".join((
        _rec(R_TEXT, 0x00),
        _rec(R_LAYER, 0x02, struct.pack(">h", layer)),
        _rec(R_TEXTTYPE, 0x02, struct.pack(">h", datatype)),
        _rec(R_XY, 0x03, struct.pack(">ii", int(round(x / db)), int(round(y / db)))),
        _rec(R_STRING, 0x06, data),
        _rec(R_ENDEL, 0x00),
    ))


def add_texts(src, dst, cell, texts):
    """texts: [(name, layer, datatype, x_um, y_um), ...]"""
    raw = open(src, "rb").read()
    db = 0.001
    i = 0
    in_target = False
    inserts = []
    while i + 4 <= len(raw):
        ln = struct.unpack(">H", raw[i:i + 2])[0]
        if ln < 4:  # 尾部零填充
            break
        rt, dt = raw[i + 2], raw[i + 3]
        data = raw[i + 4:i + ln]
        if rt == R_UNITS:
            db = _real8(data[0:8])
        elif rt == R_BGNSTR:
            in_target = False
        elif rt == R_STRNAME:
            name = data.rstrip(b"\x00").decode("latin-1")
            in_target = (name == cell)
        elif rt == R_ENDSTR:
            if in_target:
                inserts.append(i)  # insert before ENDSTR
            in_target = False
        i += ln
    if not inserts:
        raise KeyError("GDS 里没有结构 %r" % cell)
    blob = b"".join(_text_elem(x, y, lay, dt, s, db) for (s, lay, dt, x, y) in texts)
    pos = inserts[-1]
    out = raw[:pos] + blob + raw[pos:]
    open(dst, "wb").write(out)
    return dst


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="向 GDS 结构注入 TEXT 标签")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--text", action="append", required=True,
                    help="name:layer:datatype:x:y（um）")
    a = ap.parse_args(argv)
    texts = []
    for spec in a.text:
        name, lay, dt, x, y = spec.split(":")
        texts.append((name, int(lay), int(dt), float(x), float(y)))
    print(add_texts(a.src, a.dst, a.cell, texts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
