#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gds_render.py -- GDSII -> PNG renderer (for verification/visual checks; stdlib + PIL)

Usage: python3 gds_render.py in.gds out.png [--scale N] [--dpi D]
Draws BOUNDARY/PATH/BOX only; sref/aref are ignored (this toolchain emits flat geometry).
Layer colors follow XMC 55RFSOI conventions (layer number -> color); unknown layers are gray.
"""
from __future__ import print_function
import struct
import sys

# GDSII record types
RT_HEADER = 0x00
RT_BGNLIB = 0x01
RT_LIBNAME = 0x02
RT_UNITS = 0x03
RT_ENDLIB = 0x04
RT_BGNSTR = 0x05
RT_STRNAME = 0x06
RT_ENDSTR = 0x07
RT_BOUNDARY = 0x08
RT_PATH = 0x09
RT_SREF = 0x0A
RT_AREF = 0x0B
RT_TEXT = 0x0C
RT_LAYER = 0x0D
RT_DATATYPE = 0x0E
RT_WIDTH = 0x0F
RT_XY = 0x10
RT_ENDEL = 0x11
RT_SNAME = 0x12
RT_COLROW = 0x13
RT_TEXTNODE = 0x14
RT_NODE = 0x15
RT_TEXTTYPE = 0x16
RT_PRESENTATION = 0x17
RT_STRING = 0x19
RT_STRANS = 0x1A
RT_MAG = 0x1B
RT_ANGLE = 0x1C
RT_PATHTYPE = 0x21
RT_BOX = 0x2D
RT_BOXTYPE = 0x2E

# Layer number -> color (XMC 55RFSOI)
LAYER_COLORS = {
    61: (80, 120, 200),    # M1
    70: (200, 160, 60),    # V1
    62: (200, 80, 80),     # M2
    71: (160, 120, 40),    # V2
    63: (80, 170, 90),     # M3
    78: (120, 90, 40),     # TV
    69: (180, 70, 180),    # TM
    98: (140, 100, 50),    # TV1
    99: (210, 90, 40),     # TM2
    80: (120, 120, 120),   # PA
    5: (220, 190, 40),     # ALPA
    174: (90, 160, 190),   # MIMB
    175: (150, 90, 160),   # MIMT
    132: (100, 100, 100),  # IND marker
}
# Text layers (dat=5) drawn in bright cyan
TXT_COLOR = (0, 220, 220)


def read_records(path):
    with open(path, "rb") as f:
        data = f.read()
    off = 0
    n = len(data)
    while off + 4 <= n:
        (ln, rt, dt) = struct.unpack(">HBB", data[off:off + 4])
        if ln < 4:
            break
        payload = data[off + 4:off + ln]
        yield rt, dt, payload
        off += ln


def parse_xy(payload):
    if len(payload) % 8:
        return []
    npts = len(payload) // 8
    out = []
    for i in range(npts):
        x, y = struct.unpack(">ii", payload[i * 8:i * 8 + 8])
        out.append((x, y))
    return out


def render(gds_path, png_path, scale=None):
    from PIL import Image, ImageDraw

    elems = []   # (kind, layer, datatype, pts, width)
    cur = None
    units_user = 1e-6
    for rt, dt, payload in read_records(gds_path):
        if rt == RT_UNITS and len(payload) >= 16:
            # Two 8-byte reals: user_unit/meter, db_unit/meter
            # GDS real: 8-byte excess-64 float
            def gds_real(b):
                e = b[0] & 0x7F
                sign = -1.0 if (b[0] & 0x80) else 1.0
                m = 0.0
                for i in range(1, 8):
                    m = m * 256.0 + b[i]
                return sign * m * (16.0 ** (e - 64)) / (256.0 ** 7)
            try:
                units_user = gds_real(payload[8:16])
            except Exception:
                pass
        elif rt == RT_BOUNDARY or rt == RT_PATH or rt == RT_BOX:
            cur = {"type": rt, "layer": 0, "dt": 0, "xy": [], "width": 0}
        elif rt == RT_LAYER and cur is not None:
            cur["layer"] = struct.unpack(">h", payload[:2])[0]
        elif rt in (RT_DATATYPE, RT_BOXTYPE) and cur is not None:
            cur["dt"] = struct.unpack(">h", payload[:2])[0]
        elif rt == RT_WIDTH and cur is not None:
            cur["width"] = struct.unpack(">i", payload[:4])[0]
        elif rt == RT_XY and cur is not None:
            cur["xy"] = parse_xy(payload)
        elif rt == RT_ENDEL and cur is not None:
            if cur["xy"]:
                elems.append(cur)
            cur = None
    if not elems:
        raise SystemExit("no drawable elements in %s" % gds_path)

    xs = [p[0] for e in elems for p in e["xy"]]
    ys = [p[1] for e in elems for p in e["xy"]]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = max(2000, int((maxx - minx) * 0.05))
    minx -= pad; maxx += pad; miny -= pad; maxy += pad
    span = max(maxx - minx, maxy - miny)
    if not scale:
        scale = 900.0 / span
    W = int((maxx - minx) * scale) + 1
    H = int((maxy - miny) * scale) + 1

    img = Image.new("RGB", (W, H), (18, 18, 18))
    dr = ImageDraw.Draw(img, "RGBA")

    def T(p):
        return ((p[0] - minx) * scale, H - (p[1] - miny) * scale)

    for e in elems:
        is_txt_layer = (e["dt"] == 5)
        col = TXT_COLOR if is_txt_layer else LAYER_COLORS.get(
            e["layer"], (120, 120, 120))
        alpha = 110 if not is_txt_layer else 230
        pts = [T(p) for p in e["xy"]]
        if e["type"] == RT_BOUNDARY or e["type"] == RT_BOX:
            if len(pts) >= 3:
                dr.polygon(pts, fill=col + (alpha,),
                           outline=col + (min(255, alpha + 60),))
        elif e["type"] == RT_PATH:
            if len(pts) >= 2:
                wpx = max(1, int(abs(e["width"]) * scale))
                dr.line(pts, fill=col + (alpha + 40,), width=wpx, joint="curve")
    img.save(png_path)
    print("rendered %d elems -> %s  (%dx%d, dbu=%.3g m)" % (
        len(elems), png_path, W, H, units_user))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    scale = None
    if "--scale" in sys.argv:
        scale = float(sys.argv[sys.argv.index("--scale") + 1])
    render(sys.argv[1], sys.argv[2], scale)
