# -*- coding: utf-8 -*-
"""gdsgen.py - 纯标准库 GDSII 写出器 + 可参数化电感版图生成器。

用途：
  1) 给 EMX / Calibre 提供受控的合成版图（不依赖 Virtuoso），用于工具链验证；
  2) 给几何检查模块提供同一份"真值"多边形，便于与 GDS 回读结果对照。

GDS 约定：
  - 用户单位 1um，数据库单位 1nm（UNITS = 1e-3, 1e-9）；坐标以 DB 单位存放。
  - 每个 BOUNDARY 顶点闭合且按 GDS 要求顺时针排列。
  - TEXT 用 FONT 0、presentation=middle-center(5)。
  - 只写顶层 cell（无 SREF/AREF），EMX/Calibre 都按顶层处理。

命令行：
  python gdsgen.py OUT.gds --kind ind --n 2.5 --w 8 --s 3 --dout 120 \
                           --metal TM2 --under TM1 --via TV2 [--ct] [--diff]
"""

from __future__ import print_function

import argparse
import math
import struct


# ---------------------------------------------------------------- GDS records
RT_HEADER, RT_BGNLIB, RT_LIBNAME, RT_UNITS = 0x00, 0x01, 0x02, 0x03
RT_ENDLIB, RT_BGNSTR, RT_STRNAME, RT_ENDSTR = 0x04, 0x05, 0x06, 0x07
RT_BOUNDARY, RT_PATH, RT_SREF, RT_TEXT = 0x08, 0x09, 0x0A, 0x0C
RT_LAYER, RT_DATATYPE, RT_WIDTH, RT_XY, RT_ENDEL = 0x0D, 0x0E, 0x0F, 0x10, 0x11
RT_SNAME, RT_TEXTTYPE, RT_PRESENTATION, RT_STRING = 0x12, 0x16, 0x17, 0x19

DT_NONE, DT_BITARRAY, DT_INT16, DT_INT32, DT_REAL8, DT_STRING = 0, 1, 2, 3, 5, 6

_GDS_MAGIC_DATE = (2026, 1, 1, 0, 0, 0, 2026, 1, 1, 0, 0, 0)


def _rec(rt, dt, payload=b""):
    if len(payload) % 2:
        payload += b"\x00"
    return struct.pack(">HBB", 4 + len(payload), rt, dt) + payload


def _i16(v):
    return struct.pack(">h", int(v))


def _i32s(vals):
    return b"".join(struct.pack(">i", int(v)) for v in vals)


def _str(s):
    return s.encode("ascii")


def gds_real8(x):
    """IEEE double -> GDS 8-byte real（base16，excess-64）。"""
    if x == 0:
        return b"\x00" * 8
    sign = 0x80 if x < 0 else 0
    x = abs(float(x))
    exp = 0
    while x >= 1.0:
        x /= 16.0
        exp += 1
    while x < 1.0 / 16.0:
        x *= 16.0
        exp -= 1
    mant = int(round(x * (1 << 56)))
    if mant >= (1 << 56):
        mant >>= 4
        exp += 1
    return bytes([sign | (exp + 64)]) + mant.to_bytes(7, "big")


def read_gds_real8(b):
    sign = -1.0 if b[0] & 0x80 else 1.0
    exp = (b[0] & 0x7F) - 64
    mant = int.from_bytes(b[1:], "big") / float(1 << 56)
    return sign * mant * (16.0 ** exp)


def db_units_per_um():
    return 1000  # 1nm DB unit


def _xy(points):
    vals = []
    for (x, y) in points:
        vals.append(int(round(x * db_units_per_um())))
        vals.append(int(round(y * db_units_per_um())))
    return _i32s(vals)


def _signed_area(pts):
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _clockwise(pts):
    """GDS 要求外环顺时针（屏幕坐标 y 向上时面积为负）。"""
    return list(pts) if _signed_area(pts) < 0 else list(reversed(pts))


class GdsWriter(object):
    def __init__(self, libname="PCellEval", unit_um=1e-6, db_m=1e-9):
        self.libname = libname
        self.unit_um = unit_um
        self.db_m = db_m
        self.cells = []

    def boundary(self, layer, datatype, points):
        pts = _clockwise([(float(x), float(y)) for (x, y) in points])
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return ("BOUNDARY", int(layer), int(datatype), pts, None, None)

    def text(self, layer, datatype, xy, string, size=1.0):
        return ("TEXT", int(layer), int(datatype), [tuple(xy)], str(string), float(size))

    def path(self, layer, datatype, points, width_um):
        return ("PATH", int(layer), int(datatype),
                [(float(x), float(y)) for (x, y) in points], None, float(width_um))

    def add_cell(self, name, elements):
        self.cells.append((str(name), elements))

    def write(self, path):
        out = bytearray()
        out += _rec(RT_HEADER, DT_INT16, _i16(600))
        out += _rec(RT_BGNLIB, DT_INT16, b"".join(_i16(v) for v in _GDS_MAGIC_DATE))
        out += _rec(RT_LIBNAME, DT_STRING, _str(self.libname))
        # GDS UNITS = (数据库单位折合多少用户单位, 数据库单位多少米)
        # 用户单位 1um、DB 1nm -> (0.001, 1e-9)。Calibre DRC 会校验这对值，
        # 写成 (1000, 1e-9) 会报 "precision 1000 ... not consistent ... 0.001"。
        out += _rec(RT_UNITS, DT_REAL8,
                    gds_real8(self.db_m / self.unit_um) + gds_real8(self.db_m))
        for name, elements in self.cells:
            out += _rec(RT_BGNSTR, DT_INT16, b"".join(_i16(v) for v in _GDS_MAGIC_DATE))
            out += _rec(RT_STRNAME, DT_STRING, _str(name))
            for el in elements:
                kind, layer, dtype, pts, s, w = el
                if kind == "BOUNDARY":
                    out += _rec(RT_BOUNDARY, DT_NONE)
                    out += _rec(RT_LAYER, DT_INT16, _i16(layer))
                    out += _rec(RT_DATATYPE, DT_INT16, _i16(dtype))
                    out += _rec(RT_XY, DT_INT32, _xy(pts))
                    out += _rec(RT_ENDEL, DT_NONE)
                elif kind == "PATH":
                    out += _rec(RT_PATH, DT_NONE)
                    out += _rec(RT_LAYER, DT_INT16, _i16(layer))
                    out += _rec(RT_DATATYPE, DT_INT16, _i16(dtype))
                    out += _rec(RT_WIDTH, DT_INT32,
                                _i32s([int(round(w * db_units_per_um()))]))
                    out += _rec(RT_XY, DT_INT32, _xy(pts))
                    out += _rec(RT_ENDEL, DT_NONE)
                elif kind == "TEXT":
                    # 注意：Cadence strmout 与 EMX 的 TEXT 记录顺序是
                    # LAYER, TEXTTYPE, PRESENTATION, [STRANS, MAG], XY, STRING
                    # （XY 在 STRING 之前）。按 GDS 规范把 STRING 放前面会被
                    # EMX 拒绝："required record of type 0x10 missing; found 0x19"。
                    out += _rec(RT_TEXT, DT_NONE)
                    out += _rec(RT_LAYER, DT_INT16, _i16(layer))
                    out += _rec(RT_TEXTTYPE, DT_INT16, _i16(dtype))
                    out += _rec(RT_PRESENTATION, DT_BITARRAY, _i16(0x0005))
                    out += _rec(RT_XY, DT_INT32, _xy(pts))
                    out += _rec(RT_STRING, DT_STRING, _str(s))
                    out += _rec(RT_ENDEL, DT_NONE)
            out += _rec(RT_ENDSTR, DT_NONE)
        out += _rec(RT_ENDLIB, DT_NONE)
        with open(path, "wb") as f:
            f.write(bytes(out))
        return path


# ------------------------------------------------------------- geometry utils
def offset_polyline(pts, width):
    """把中心线折线膨胀成闭合多边形（miter 接头）。

    返回一组点（未闭合）。拐角为尖角；对 90/135 度折线足够。
    端点用平头（butt）。
    """
    if len(pts) < 2:
        raise ValueError("offset_polyline 需要 >=2 个点")
    h = 0.5 * float(width)
    left, right = [], []
    n = len(pts)

    def unit(vx, vy):
        d = math.hypot(vx, vy)
        return (vx / d, vy / d) if d else (0.0, 0.0)

    segs = []
    for i in range(n - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        ux, uy = unit(x2 - x1, y2 - y1)
        segs.append((ux, uy, (-uy, ux)))  # 方向 + 左法线

    for i, (x, y) in enumerate(pts):
        if i == 0:
            ux, uy, nrm = segs[0]
            nx, ny = nrm
            left.append((x + nx * h, y + ny * h))
            right.append((x - nx * h, y - ny * h))
        elif i == n - 1:
            ux, uy, nrm = segs[-1]
            nx, ny = nrm
            left.append((x + nx * h, y + ny * h))
            right.append((x - nx * h, y - ny * h))
        else:
            u1x, u1y, n1 = segs[i - 1]
            u2x, u2y, n2 = segs[i]
            n1x, n1y = n1
            n2x, n2y = n2
            # 两条偏移线的交点（miter）
            p1 = (x + n1x * h, y + n1y * h)
            p2 = (x + n2x * h, y + n2y * h)
            left.append(_line_intersect(p1, u1x, u1y, p2, u2x, u2y, p1))
            q1 = (x - n1x * h, y - n1y * h)
            q2 = (x - n2x * h, y - n2y * h)
            right.append(_line_intersect(q1, u1x, u1y, q2, u2x, u2y, q1))
    return left + list(reversed(right))


def _line_intersect(p1, u1x, u1y, p2, u2x, u2y, fallback):
    den = u1x * u2y - u1y * u2x
    if abs(den) < 1e-12:
        return fallback
    t = ((p2[0] - p1[0]) * u2y - (p2[1] - p1[1]) * u2x) / den
    return (p1[0] + u1x * t, p1[1] + u1y * t)


def square_spiral_centerline(n_turns, w, s, dout, lead=10.0):
    """方螺旋中心线（外圈起，顺时针），n_turns 可为 0.5 的倍数。

    返回 (points, inner_xy)。points 已包含起点外引线延伸。
    """
    p = float(w) + float(s)
    a0 = 0.5 * float(dout) - 0.5 * float(w)
    nseg = int(round(float(n_turns) * 4.0))
    if nseg < 4:
        raise ValueError("n_turns 太小（至少 1 圈）")
    a = a0
    pts = [(-a, -a)]
    for k in range(nseg):
        d = k % 4
        if d == 0:
            pts.append((a, -a))
        elif d == 1:
            pts.append((a, a))
        elif d == 2:
            pts.append((-a, a))
        else:
            # 只收缩 y（沿左边向下），下一段再水平走到新半径；
            # 若同时改 x/y 会得到一条斜穿内圈的线段。
            a_new = a - p
            if a_new <= 0.5 * w:
                raise ValueError("dout 太小：内圈半径不足（a=%g）" % a_new)
            pts.append((-a, -a_new))
            a = a_new
    inner = pts[-1]
    # 外引线：从起点向左延伸
    x0, y0 = pts[0]
    pts.insert(0, (x0 - float(lead), y0))
    return pts, inner


def via_array(cx, cy, size, pitch, nx, ny):
    out = []
    x0 = cx - 0.5 * (nx - 1) * pitch
    y0 = cy - 0.5 * (ny - 1) * pitch
    for i in range(nx):
        for j in range(ny):
            x = x0 + i * pitch
            y = y0 + j * pitch
            out.append([(x - size / 2, y - size / 2), (x + size / 2, y - size / 2),
                        (x + size / 2, y + size / 2), (x - size / 2, y + size / 2)])
    return out


def make_spiral(cell, n=2.5, w=8.0, s=3.0, dout=120.0, shape="oct",
                chamfer=0.0, lead=12.0, under_len=0.0,
                metal=(122, 0), under=(120, 0), via=(123, 0), text_dt=0,
                port_names=("p01", "p02"), label_size=2.0, via_size=0.36,
                via_pitch=0.70, via_n=(2, 2), ct=False, label_pos="edge"):
    """生成单端（或带中心抽头）电感版图元素列表。

    shape='oct' 时把 90 度角切成 45 度斜边（chamfer 为斜边长度，0=自动 w）。
    返回 (writer, info)。

    label_pos:
      "edge"（默认）—— 标签放目标端口边内侧 1um：Calibre 认为落在导体上，
          EMX 吸附到该边（端口面 = 该边）；两侧工具口径一致。
      "center" —— 标签放焊盘/引线端帽中心。两个工具都能挂上，但 EMX 会
          吸到最近的边，宽焊盘上可能吸到侧边，导致端口面与预期不同，
          仅作对照，不作为基准口径：
         
          Calibre 按文字 XY 点是否落在导体图形内来挂标签（不吸附），
          靠边放一旦越界就被判 unattached；EMX 需要标签距金属边
          <= label-snap-distance，中心到边 8um，所以 EMX 必须配
          --label-snap-distance=10（见 info["label_snap"]）。
      "edge"    —— 老行为，标签贴近金属边（EMX 默认 4um 可过）；
          但 p01 的落点会落到引线端头外侧，Calibre 判 unattached，
          只用于 EMX-only 场景。
    """
    W = GdsWriter()
    if float(n) < 1.0:
        raise ValueError("n >= 1")
    cl, inner = square_spiral_centerline(n, w, s, dout, lead=lead)
    if str(shape).lower().startswith("oct"):
        cl = _chamfer_polyline(cl, float(chamfer) if chamfer else 0.5 * float(w))
    poly = offset_polyline(cl, w)
    els = [W.boundary(metal[0], metal[1], poly)]

    # 内端：过孔 -> 下穿金属 -> 外引线（简单竖直向下，长度保证穿出螺旋）
    ix, iy = inner
    # 过孔阵列必须完全落在上下层金属内：沿末段方向内缩 1.5um 放阵列，
    # 下穿金属再从过孔中心向两端各伸出 1.5um（PDK UTV2 包络规则）。
    sx, sy = cl[-2]
    dx, dy = ix - sx, iy - sy
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    vx, vy = ix - ux * 1.5, iy - uy * 1.5
    exit_y = -0.5 * float(dout) - float(lead)
    if under_len:
        exit_y = min(exit_y, iy - float(under_len))
    # 下穿：从内端竖直向下到出口，再水平到右侧
    under_pts = [(vx, vy + 1.5), (vx, exit_y), (0.6 * float(dout) + lead + 1.5, exit_y)]
    upoly = offset_polyline(under_pts, max(float(w), 2.0))
    els.append(W.boundary(under[0], under[1], upoly))
    for v in via_array(vx, vy, via_size, via_pitch, *via_n):
        els.append(W.boundary(via[0], via[1], v))
    # 下穿末端焊盘（TM2）+ 过孔
    pad_cx = 0.6 * float(dout) + lead
    pad = [(pad_cx - w, exit_y - w), (pad_cx + w, exit_y - w),
           (pad_cx + w, exit_y + w), (pad_cx - w, exit_y + w)]
    els.append(W.boundary(metal[0], metal[1], pad))
    for v in via_array(pad_cx, exit_y, via_size, via_pitch, *via_n):
        els.append(W.boundary(via[0], via[1], v))
    # 端口标签：
    #   center 模式 —— 引线端帽中心 + 焊盘中心。Calibre 按文字 XY 点是否落在
    #   导体图形内挂标签（不吸附），靠边放越界就 unattached；EMX 要求标签到
    #   最近金属边 <= label-snap-distance，所以 center 模式必须配
    #   --label-snap-distance=10（焊盘半宽 8um）。
    if str(label_pos).lower().startswith("center"):
        p1 = (min(p[0] for p in poly) + 6.0, cl[0][1])
        p2 = (pad_cx, exit_y)
    else:
        # 距目标端口边 1um 的金属内侧：Calibre 能挂上（点在导体内），
        # EMX 会把标签吸到最近的这条边（端口面就是这条边）。
        p1 = (min(p[0] for p in poly) + 1.0, cl[0][1])
        p2 = (pad_cx, exit_y - w + 1.0)
    els.append(W.text(metal[0], text_dt, p1, port_names[0], label_size))
    els.append(W.text(metal[0], text_dt, p2, port_names[1], label_size))
    info = {"n": float(n), "w": float(w), "s": float(s), "dout": float(dout),
            "shape": shape, "lead": float(lead), "bbox": _bbox_of([poly, upoly, pad]),
            "inner": inner, "pad": (pad_cx, exit_y), "ports": list(port_names),
            "label_pos": str(label_pos), "label_snap": 10.0,
            "label_xy": [p1, p2]}
    W.add_cell(cell, els)
    return W, info


def _shift(pts, dx, dy=0.0):
    return [(x + dx, y + dy) for (x, y) in pts]


def _arc_point(pts, frac):
    """折线按弧长取点：frac=0 起点，1 终点。返回 (x, y)。"""
    seg = []
    total = 0.0
    for i in range(1, len(pts)):
        d = ((pts[i][0] - pts[i - 1][0]) ** 2 + (pts[i][1] - pts[i - 1][1]) ** 2) ** 0.5
        seg.append(d)
        total += d
    target = max(0.0, min(1.0, float(frac))) * total
    acc = 0.0
    for i, d in enumerate(seg):
        if acc + d >= target and d > 0:
            t = (target - acc) / d
            x = pts[i][0] + t * (pts[i + 1][0] - pts[i][0])
            y = pts[i][1] + t * (pts[i + 1][1] - pts[i][1])
            return (x, y)
        acc += d
    return pts[-1]


def make_ct_spiral(cell, n=2.5, w=8.0, s=3.0, dout=120.0, shape="oct",
                   chamfer=0.0, lead=12.0, metal=(122, 0), under=(120, 0),
                   via=(123, 0), text_dt=0, port_names=("p01", "p02", "ct"),
                   label_size=2.0, via_size=0.36, via_pitch=0.70,
                   via_n=(2, 2), label_pos="edge", tap_frac=0.5):
    """单端螺旋 + 中心抽头（3 端口），用于验证 ct 端口/端口序/三端口 Z 参数。

    注意：这里 p01/p02/ct 是同一根导体上的三个抽头（ct 在螺旋中点），
    因此 ct 对 p01-p02 的差模阻抗没有影响——ct_metrics 的值退化为串联
    电感。真正的对称中心抽头差分电感由 foundry pcell 提供，框架的
    kind=ct 分析路径同样适用。

    ct 走线：中点打过孔到 TM1，竖直向下（x 取在中点左侧、避开内端
    underpass 的 x），再水平到螺旋外左下角的 ct 焊盘。
    """
    W = GdsWriter()
    cl, inner = square_spiral_centerline(n, w, s, dout, lead=lead)
    if str(shape).lower().startswith("oct"):
        cl = _chamfer_polyline(cl, float(chamfer) if chamfer else 0.5 * float(w))
    poly = offset_polyline(cl, w)
    els = [W.boundary(metal[0], metal[1], poly)]

    # 中点抽头：沿中心线取弧长 50%，必要时往两端挪，保证 x < 内端 x - 2w
    tx, ty = _arc_point(cl, tap_frac)
    x_lim = inner[0] - 2.0 * float(w)
    f = float(tap_frac)
    while tx >= x_lim and 0.05 < f < 0.95:
        f += 0.02 if ty < 0 else -0.02
        tx, ty = _arc_point(cl, f)
    if tx >= x_lim:
        raise ValueError("找不到合适的中心抽头位置（tap_frac=%g, x=%g）" % (tap_frac, tx))

    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    pad_x = min(xs) - 12.0
    pad_y = min(ys) - 20.0
    pad_h = float(w)
    # TM1 落地焊盘必须覆盖整个过孔阵列：终点取 pad_x-pad_h（与 TM2 pad 外沿对齐），
    # 否则过孔阵列外排会越过 TM1 端帽，Calibre 报 UTV2_3_4（TM1 enclosure 0.17）。
    under_pts = [(tx, ty + 1.5), (tx, pad_y), (pad_x - pad_h, pad_y)]
    upoly = offset_polyline(under_pts, max(float(w), 2.0))
    els.append(W.boundary(under[0], under[1], upoly))
    for v in via_array(tx, ty, via_size, via_pitch, *via_n):
        els.append(W.boundary(via[0], via[1], v))
    ctpad = [(pad_x - pad_h, pad_y - pad_h), (pad_x + pad_h, pad_y - pad_h),
             (pad_x + pad_h, pad_y + pad_h), (pad_x - pad_h, pad_y + pad_h)]
    els.append(W.boundary(metal[0], metal[1], ctpad))
    for v in via_array(pad_x, pad_y, via_size, via_pitch, *via_n):
        els.append(W.boundary(via[0], via[1], v))

    # p01 外端、p02 内端下穿焊盘（与 make_spiral 一致）
    ix, iy = inner
    sx, sy = cl[-2]
    dx, dy = ix - sx, iy - sy
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    vx, vy = ix - ux * 1.5, iy - uy * 1.5
    exit_y = -0.5 * float(dout) - float(lead)
    p2_under = [(vx, vy + 1.5), (vx, exit_y), (0.6 * float(dout) + lead + 1.5, exit_y)]
    upoly2 = offset_polyline(p2_under, max(float(w), 2.0))
    els.append(W.boundary(under[0], under[1], upoly2))
    for v in via_array(vx, vy, via_size, via_pitch, *via_n):
        els.append(W.boundary(via[0], via[1], v))
    pad_cx = 0.6 * float(dout) + lead
    pad2 = [(pad_cx - w, exit_y - w), (pad_cx + w, exit_y - w),
            (pad_cx + w, exit_y + w), (pad_cx - w, exit_y + w)]
    els.append(W.boundary(metal[0], metal[1], pad2))
    for v in via_array(pad_cx, exit_y, via_size, via_pitch, *via_n):
        els.append(W.boundary(via[0], via[1], v))

    if str(label_pos).lower().startswith("center"):
        p1 = (min(xs) + 6.0, cl[0][1])
        p2 = (pad_cx, exit_y)
        p3 = (pad_x, pad_y)
    else:
        p1 = (min(xs) + 1.0, cl[0][1])
        p2 = (pad_cx, exit_y - w + 1.0)
        p3 = (pad_x, pad_y - pad_h + 1.0)
    els.append(W.text(metal[0], text_dt, p1, port_names[0], label_size))
    els.append(W.text(metal[0], text_dt, p2, port_names[1], label_size))
    els.append(W.text(metal[0], text_dt, p3, port_names[2], label_size))

    info = {"kind": "ct", "n": float(n), "w": float(w), "s": float(s),
            "dout": float(dout), "shape": shape, "lead": float(lead),
            "bbox": _bbox_of([poly, upoly, upoly2, ctpad, pad2]),
            "inner": inner, "tap_frac": f, "tap_xy": (tx, ty),
            "ct_pad": (pad_x, pad_y), "ports": list(port_names),
            "label_pos": str(label_pos), "label_snap": 10.0,
            "label_xy": [p1, p2, p3]}
    W.add_cell(cell, els)
    return W, info


def _chamfer_polyline(pts, c):
    """把折线每个拐角替换成 45 度斜边（斜边长度 c）。"""
    if len(pts) < 3 or c <= 0:
        return list(pts)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        d1 = math.hypot(x1 - x0, y1 - y0)
        d2 = math.hypot(x2 - x1, y2 - y1)
        t1 = min(c / d1, 0.45) if d1 else 0.0
        t2 = min(c / d2, 0.45) if d2 else 0.0
        out.append((x1 - (x1 - x0) * t1, y1 - (y1 - y0) * t1))
        out.append((x1 + (x2 - x1) * t2, y1 + (y2 - y1) * t2))
    out.append(pts[-1])
    return out


def _bbox_of(polys):
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def main():
    ap = argparse.ArgumentParser(description="生成受控电感 GDS（EMX/Calibre 验证用）")
    ap.add_argument("out")
    ap.add_argument("--cell", default=None)
    ap.add_argument("--n", type=float, default=2.5)
    ap.add_argument("--w", type=float, default=8.0)
    ap.add_argument("--s", type=float, default=3.0)
    ap.add_argument("--dout", type=float, default=120.0)
    ap.add_argument("--shape", default="oct")
    ap.add_argument("--chamfer", type=float, default=0.0)
    ap.add_argument("--lead", type=float, default=12.0)
    ap.add_argument("--metal", default="122:0")
    ap.add_argument("--under", default="120:0")
    ap.add_argument("--via", default="123:0")
    ap.add_argument("--text-dt", type=int, default=0,
                    help="端口标签的 datatype（PDK MnTXT 为 3；EMX proc 只认 t0 时用 0）")
    ap.add_argument("--label-pos", default="edge", choices=["center", "edge"],
                    help="center=焊盘/端帽中心（Calibre+EMX 通用，EMX 需 --label-snap-distance=10）")
    ap.add_argument("--ct", action="store_true", help="生成带中心抽头的 3 端口电感（p01/p02/ct）")
    ap.add_argument("--tap-frac", type=float, default=0.5, help="中心抽头在中心线上的弧长比例")
    args = ap.parse_args()

    def ldp(s):
        a, b = str(s).split(":")
        return (int(a), int(b))

    cell = args.cell or (("eval_ct_%gx%g" if args.ct else "eval_ind_%gx%g")
                         % (args.n, args.w))
    if args.ct:
        W, info = make_ct_spiral(cell, n=args.n, w=args.w, s=args.s, dout=args.dout,
                                 shape=args.shape, chamfer=args.chamfer, lead=args.lead,
                                 metal=ldp(args.metal), under=ldp(args.under),
                                 via=ldp(args.via), text_dt=args.text_dt,
                                 label_pos=args.label_pos, tap_frac=args.tap_frac)
    else:
        W, info = make_spiral(cell, n=args.n, w=args.w, s=args.s, dout=args.dout,
                              shape=args.shape, chamfer=args.chamfer, lead=args.lead,
                              metal=ldp(args.metal), under=ldp(args.under), via=ldp(args.via),
                              text_dt=args.text_dt, label_pos=args.label_pos)
    W.write(args.out)
    print("wrote %s cell=%s bbox=%s" % (args.out, cell, info["bbox"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
