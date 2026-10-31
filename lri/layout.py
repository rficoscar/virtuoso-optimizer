# -*- coding: utf-8 -*-
"""lri.layout — 电感几何内核（i3/对称/单端）、DRC 校验与表单映射。"""
from __future__ import print_function

import math

from .common import (
    DEFAULT_TECH, LibrfError, parse_eng)
from .tech import (
    tech_drc)

def lri_seglen(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])

def lri_dedup(pts):
    out = []
    for p in pts:
        if not out or lri_seglen(out[-1], p) > 1e-6:
            out.append(p)
    return out

def lri_shift(pts, dx, dy):
    return [(x + dx, y + dy) for x, y in pts]

def lri_mirrorx(pts):
    return [(-x, y) for x, y in pts]

def lri_mirrory(pts):
    return [(x, -y) for x, y in pts]

def lri_plen(pts):
    return sum(lri_seglen(a, b) for a, b in zip(pts[:-1], pts[1:]))

def lri_inter(p1, p2, q1, q2):
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
    if abs(det) < 1e-12:
        return None
    rx, ry = q1[0] - p1[0], q1[1] - p1[1]
    t = (rx * (-d2[1]) - ry * (-d2[0])) / det
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])

def lri_ring(a, b, c, seam="bottom"):
    c = max(0.0, min(c, 0.999 * min(a, b)))
    if seam == "top":
        r = [(0, b), (c - a, b), (-a, b - c), (-a, c - b)]
        r += [(c - a, -b), (a - c, -b), (a, c - b), (a, b - c)]
        r.append((a - c, b))
    elif seam == "left":
        r = [(-a, 0), (-a, c - b), (c - a, -b), (a - c, -b)]
        r += [(a, c - b), (a, b - c), (a - c, b), (c - a, b)]
        r.append((-a, b - c))
    elif seam == "right":
        r = [(a, 0), (a, b - c), (a - c, b), (c - a, b)]
        r += [(-a, b - c), (-a, c - b), (c - a, -b), (a - c, -b)]
        r.append((a, c - b))
    else:
        r = [(0, -b), (a - c, -b), (a, c - b), (a, b - c)]
        r += [(a - c, b), (c - a, b), (-a, b - c), (-a, c - b)]
        r.append((c - a, -b))
    return lri_dedup(r)

def lri_rings(W, H, w, s, n, corner, seam="bottom", pitch=None):
    p = pitch if pitch else (w + s)
    a0 = (W - w) / 2.0
    b0 = (H - w) / 2.0
    ratio = corner / min(a0, b0) if min(a0, b0) > 0 else 0.0
    nring = int(math.ceil(n)) + 2
    rs = [lri_ring(a0, b0, ratio * min(a0, b0), seam)]
    for k in range(1, nring):
        a, b = a0 - k * p, b0 - k * p
        if min(a, b) <= w / 2.0:
            break
        rs.append(lri_ring(a, b, ratio * min(a, b), seam))
    return rs, p

def lri_cut_open(pts, frac):
    if frac >= 0.9999:
        return list(pts)
    tot = lri_plen(pts)
    if tot < 1e-9:
        return list(pts)
    want, acc = tot * frac, 0.0
    out = [pts[0]]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        L = lri_seglen(a, b)
        if L < 1e-9:
            continue
        if acc + L >= want - 1e-9:
            t = (want - acc) / L
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t))
            return out
        out.append(b)
        acc += L
    return out

def lri_rot_list(l, i):
    n = len(l)
    k = i % n
    return [l[(k + j) % n] for j in range(n)]

def lri_step_edge(r):
    m = len(r)
    for i in range(m):
        a, b = r[i], r[(i + 1) % m]
        if (abs(b[0] - a[0]) < 1e-9 and abs(b[1] - a[1]) > 1e-9
                and a[0] < 0.0 and b[1] < a[1]):
            return i
    raise ValueError("lri: 环上没有可用的左侧竖直边，无法构造螺旋")

def lri_rot_exact(pts, ang):
    if ang == 90:
        return [(-y, x) for x, y in pts]
    if ang == 180:
        return [(-x, -y) for x, y in pts]
    if ang == 270:
        return [(y, -x) for x, y in pts]
    return list(pts)

def _lri_spiral_bottom(W, H, w, s, n, corner, pitch=None):
    rs, p = lri_rings(W, H, w, s, n, corner, "bottom", pitch)
    r0 = rs[0]
    i = lri_step_edge(r0)
    m = len(r0)
    b_idx = (i + 1) % m
    nfull = int(n + 1e-7)
    frac = n - nfull
    if len(rs) < nfull + 1:
        raise ValueError(
            "lri: 内孔已闭合，只能容纳 %.2f 圈 < 请求 %.2f 圈；"
            "请减小 n 或 s/w" % (max(0, len(rs) - 1), n))

    def on_left(k, y):
        r = rs[k]
        a, b = r[i], r[(i + 1) % m]
        t = (y - a[1]) / (b[1] - a[1])
        if t < -1e-9 or t > 1.0 + 1e-9:
            raise ValueError(
                "lri: 第 %d 圈左侧边长度不足，无法容纳螺旋台阶 "
                "(需要 y=%.3f, 边范围 %.3f..%.3f)；请减小 n 或 s/w"
                % (k, y, min(a[1], b[1]), max(a[1], b[1])))
        return (a[0], y)

    if nfull == 0:
        closed = lri_rot_list(r0, 0) + [r0[0]]
        return lri_dedup(lri_cut_open(closed, frac)), p

    l1 = rs[1][b_idx]
    d0 = on_left(0, l1[1])
    pts = lri_rot_list(r0, 0)[:i + 1] + [d0]

    lk = l1
    pts.append(lk)
    for k in range(1, nfull):
        r = rs[k]
        rot = lri_rot_list(r, b_idx)
        pts.append(r[b_idx])
        pts += rot[1:]
        if k + 1 < len(rs):
            dk = on_left(k, lk[1] + p)
            pts.append(dk)
            if k < nfull - 1 or frac > 1e-9:
                nxt = (rs[k + 1][i][0], dk[1])
                pts.append(nxt)
                lk = nxt
    if frac > 1e-9 and nfull < len(rs):
        r = rs[nfull]
        closed = lri_dedup([lk] + lri_rot_list(r, b_idx) + [lk])
        pts += lri_cut_open(closed, frac)[1:]
    return lri_dedup(pts), p

def lri_spiral(W, H, w, s, n, corner, seam="bottom", pitch=None):
    if seam == "bottom":
        return _lri_spiral_bottom(W, H, w, s, n, corner, pitch)[0]
    if seam == "top":
        return lri_rot_exact(_lri_spiral_bottom(W, H, w, s, n, corner,
                                                pitch)[0], 180)
    if seam == "right":
        return lri_rot_exact(_lri_spiral_bottom(H, W, w, s, n, corner,
                                                pitch)[0], 90)
    if seam == "left":
        return lri_rot_exact(_lri_spiral_bottom(H, W, w, s, n, corner,
                                                pitch)[0], 270)
    return _lri_spiral_bottom(W, H, w, s, n, corner, pitch)[0]

def lri_point_at(pts, frac):
    tot = lri_plen(pts)
    want = tot * frac
    acc = 0.0
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        L = lri_seglen(a, b)
        if acc + L >= want - 1e-9:
            t = (want - acc) / L if L > 0 else 0.0
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += L
    return pts[-1]

def lri_sweep(pts, w):
    h = w / 2.0
    segs = []
    for a, b in zip(pts[:-1], pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L > 1e-9:
            segs.append((a, b, -dy / L, dx / L))
    if not segs:
        return []

    def bound(sign):
        out = [(segs[0][0][0] + sign * h * segs[0][2],
                segs[0][0][1] + sign * h * segs[0][3])]
        for i in range(1, len(segs)):
            q = lri_inter(
                (segs[i - 1][0][0] + sign * h * segs[i - 1][2],
                 segs[i - 1][0][1] + sign * h * segs[i - 1][3]),
                (segs[i - 1][1][0] + sign * h * segs[i - 1][2],
                 segs[i - 1][1][1] + sign * h * segs[i - 1][3]),
                (segs[i][0][0] + sign * h * segs[i][2],
                 segs[i][0][1] + sign * h * segs[i][3]),
                (segs[i][1][0] + sign * h * segs[i][2],
                 segs[i][1][1] + sign * h * segs[i][3]))
            out.append(q if q else (segs[i][0][0] + sign * h * segs[i][2],
                                    segs[i][0][1] + sign * h * segs[i][3]))
        la = segs[-1]
        out.append((la[1][0] + sign * h * la[2],
                    la[1][1] + sign * h * la[3]))
        return out

    return bound(1) + bound(-1)[::-1]

SEAM_MAP = {"N": "top", "S": "bottom", "E": "right", "W": "left",
            "TOP": "top", "BOTTOM": "bottom", "LEFT": "left",
            "RIGHT": "right", "T": "top", "B": "bottom"}

def lri_norm_geom(g):
    g = dict(g)
    raw_kind = str(g.get("kind", "")).strip().lower()
    if raw_kind in ("sym", "symmetric", "wiki", "rfw"):
        is_sym = True
    elif (raw_kind in ("", "auto")
          and "dout" in g and "W" not in g and "H" not in g):
        is_sym = True
    else:
        is_sym = False
    if is_sym:
        # 参数集：rx/ry（缺省由 dout 推出）、n/w/s、chamfer、lead_*。
        dout = float(g.get("dout", g.get("W", 160.0)))
        rx = float(g.get("rx") or dout / 2.0)
        ry = float(g.get("ry") or dout / 2.0)
        w = float(g.get("w", 10.0))
        s = float(g.get("s", 3.2))

        def _f(key, dflt):
            v = g.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return dflt
        chamfer = _f("chamfer", 2.0 - math.sqrt(2.0))
        lead_len = _f("lead_len", 20.0)
        lead_gap = _f("lead_gap", s)
        lead_w = _f("lead_w", w)
        g.update(kind="sym", dout=2.0 * rx, rx=rx, ry=ry,
                 W=2.0 * rx, H=2.0 * ry, w=w, s=s, n=float(g.get("n", 3.0)),
                 chamfer=chamfer, lead_len=lead_len, lead_gap=lead_gap,
                 lead_w=lead_w)
        g["ct"] = int(float(g.get("ct", 0) or 0))
        g["shape"] = "oct" if chamfer > 0.0 else "rect"
        g["corner"] = chamfer
        g["seam"] = "bottom"
        g["tangle"] = 180
        return g
    W = float(g.get("W", g.get("dout", 150.0)))
    H = float(g.get("H", W))
    w = float(g.get("w", 10.0))
    shape = str(g.get("shape", "rect")).strip().lower() or "rect"
    kind = str(g.get("kind", "se")).strip().lower() or "se"
    if kind in ("figure8", "fig8", "f8"):
        kind = "f8"
    if kind not in ("se", "diff", "f8"):
        kind = "se"
    if shape in ("octagon", "oct"):
        shape = "oct"
    elif shape in ("square", "sq", "rect", "rectangle"):
        shape = "rect"
    else:
        shape = "rect"
    g["kind"] = kind
    g["shape"] = shape
    g["W"] = W
    g["H"] = H
    g["w"] = w
    g["s"] = float(g.get("s", 2.6))
    g["n"] = float(g.get("n", 2.5))
    g["ct"] = int(float(g.get("ct", 0) or 0))
    sm = str(g.get("seam", "S")).strip().upper()
    g["seam"] = SEAM_MAP.get(sm, "bottom")
    try:
        g["tangle"] = int(float(g.get("tangle", 180) or 180))
    except (TypeError, ValueError):
        g["tangle"] = 180
    if g["tangle"] not in (0, 90, 180):
        g["tangle"] = 180
    if g.get("corner") in ("", None):
        if shape == "oct":
            g["corner"] = ((2.0 - math.sqrt(2.0))
                           * min((W - w) / 2.0, (H - w) / 2.0))
        else:
            g["corner"] = 0.0
    else:
        g["corner"] = float(g["corner"])
    return g

def _drc_limits(tech=None):
    drc = tech_drc(tech) or {}

    def _num(key, dflt):
        try:
            return float(drc.get(key, dflt))
        except (TypeError, ValueError):
            return float(dflt)
    return _num("min_w", 1.0), _num("max_w", 30.0), _num("min_s", 1.0)

def _drc_veto(w, s, tech=None):
    min_w, max_w, min_s = _drc_limits(tech)
    if w < min_w:
        return "线宽 w=%g 小于工艺最小线宽 %g（drc.min_w）" % (w, min_w)
    if w > max_w:
        return "线宽 w=%g 超过工艺最大线宽 %g（drc.max_w）" % (w, max_w)
    if s < min_s:
        return "间距 s=%g 小于工艺最小间距 %g（drc.min_s）" % (s, min_s)
    if s < 2.0 * min_s:
        return "电感区间距 s=%g 需 >= 2*min_s=%g" % (s, 2.0 * min_s)
    if w >= 30.0 and s < 1.5 * min_s:
        return "大块金属 w=%g(>=30) 间距 s=%g 需 >= 1.5*min_s=%g" % (
            w, s, 1.5 * min_s)
    return None

def _wide_space_veto(s, lead_gap, tech=None):
    """差分两臂与两条引线都是独立多边形：s / lead_gap 须 >= 该层宽金属间距。"""
    try:
        drc = tech_drc(tech) or {}
        ws = drc.get("wide_space") or {}
        lay = (tech or {}).get("layers") or {}
        spot = lay.get("spiral")
        lim = None
        if isinstance(ws, dict) and spot in ws:
            lim = float(ws[spot])
        elif isinstance(ws, (int, float)):
            lim = float(ws)
        if lim is None:
            return None
        if s < lim - 1e-9:
            return "间距 s=%g 小于 %s 宽金属长平行间距 %g" % (s, spot, lim)
        if lead_gap not in (None, ""):
            lg = float(lead_gap)
            if lg < lim - 1e-9:
                return "引线间距 lead_gap=%g 小于 %s 宽金属长平行间距 %g" % (
                    lg, spot, lim)
    except (TypeError, ValueError):
        return None
    return None

def lri_validate(g, tech=None):
    kind0 = str(g.get("kind", "se")).strip().lower()
    if kind0 == "sym":
        dout = float(g.get("dout", g.get("W", 160.0)))
        rx = float(g.get("rx") or dout / 2.0)
        ry = float(g.get("ry") or dout / 2.0)
        w = float(g.get("w", 10.0))
        s = float(g.get("s", 3.2))
        n = float(g.get("n", 3.0))
        msg = _drc_veto(w, s, tech)
        if msg:
            return False, msg
        msg = _wide_space_veto(s, g.get("lead_gap"), tech)
        if msg:
            return False, msg
        try:
            lead_gap = g.get("lead_gap")
            lead_gap = float(lead_gap) if lead_gap not in (None, "") else None
            lead_w = g.get("lead_w")
            lead_w = float(lead_w) if lead_w not in (None, "") else None
            lri_i3_build(
                rx, ry, n, w, s,
                chamfer=float(g.get("chamfer", 2.0 - math.sqrt(2.0))),
                lead_len=float(g.get("lead_len", 20.0) or 20.0),
                lead_gap=lead_gap, lead_w=lead_w,
                ct=int(float(g.get("ct", 0) or 0)))
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""
    W = float(g.get("W", 150.0))
    H = float(g.get("H", W))
    w = float(g.get("w", 10.0))
    s = float(g.get("s", 2.6))
    n = float(g.get("n", 2.5))
    kind = g.get("kind", "se")
    if min(W, H) <= 0.0 or w <= 0.0 or s <= 0.0:
        return False, "W/H/w/s 必须 > 0"
    if w >= min(W, H):
        return False, "线宽 w=%g 不小于外径" % w
    msg = _drc_veto(w, s, tech)
    if msg:
        return False, msg
    p = w + s
    a0, b0 = (W - w) / 2.0, (H - w) / 2.0
    units = 1.0 if kind != "diff" else 2.0
    n_max = (min(a0, b0) - w / 2.0) / (p * units)
    if n > n_max + 1e-9:
        return False, ("匝数 n=%.2f 超出可行域: W=%g H=%g w=%g s=%g "
                       "最多 %.2f 圈" % (n, W, H, w, s, n_max))
    if n < 1.0:
        return False, "匝数 n 至少 1 圈（不足一圈无法形成螺旋）"
    if kind == "f8" and min(W, H) - 8.0 <= 0:
        return False, "f8 的 W 太小"
    try:
        corner = float(g.get("corner") or 0.0)
        if kind == "diff":
            p2 = w + s
            lri_spiral(W, H, w, s, n, corner, "bottom", 2.0 * p2)
            lri_spiral(W - 2.0 * p2, H - 2.0 * p2, w, s, n, corner,
                       "bottom", 2.0 * p2)
        elif kind == "f8":
            gapf = g.get("gap")
            gapf = 8.0 if gapf in (None, "") else float(gapf)
            gapf = max(gapf, w + s)
            d = (W - gapf) / 2.0
            cf8 = corner
            if min(a0, b0) > 0.0:
                cf8 = corner * min((d - w) / 2.0, b0) / min(a0, b0)
            lri_spiral(d, H, w, s, n / 2.0, cf8, "left")
        else:
            lri_spiral(W, H, w, s, n, corner, g.get("seam", "bottom"))
    except ValueError as exc:
        return False, str(exc)
    return True, ""

_DIRS = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}

def lri_dir_key(v):
    x, y = v
    if abs(x) >= abs(y):
        return "E" if x >= 0.0 else "W"
    return "N" if y >= 0.0 else "S"

def lri_angle_deg(k1, k2):
    v1, v2 = _DIRS[k1], _DIRS[k2]
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    if dot > 0.5:
        return 0
    if dot < -0.5:
        return 180
    return 90

def lri_pick_dirs(tangle, nat1, nat2):
    best = None
    for k1 in ("N", "S", "E", "W"):
        for k2 in ("N", "S", "E", "W"):
            if lri_angle_deg(k1, k2) != tangle:
                continue
            cost = (0 if k1 == nat1 else 1, 0 if k2 == nat2 else 1)
            if best is None or cost < best[0]:
                best = (cost, k1, k2)
    return best[1], best[2]

def _lri_lead(pt, k, length):
    dx, dy = _DIRS[k]
    return [(pt[0], pt[1]), (pt[0] + dx * length, pt[1] + dy * length)]

def _lri_exit_pad(pt, k, bbox_w, bbox_h, length):
    dx, dy = _DIRS[k]
    reach = max(bbox_w, bbox_h) + length
    return (pt[0] + dx * reach, pt[1] + dy * reach)

def lri_half_right(R, xj):
    c = (2.0 - math.sqrt(2.0)) * R
    return [(xj, R), (R - c, R), (R, R - c),
            (R, -(R - c)), (R - c, -R), (xj, -R)]

def lri_half_left(R, xj):
    return [(-x, y) for x, y in reversed(lri_half_right(R, xj))]

SYM_VIA_CUT = float(DEFAULT_TECH["drc"]["via"]["cut"])

SYM_VIA_SPACE = float(DEFAULT_TECH["drc"]["via"]["spacing"])

SYM_VIA_ENCL = float(DEFAULT_TECH["drc"]["via"]["enclosure"])

SYM_VIA_EDGE = SYM_VIA_CUT / 2.0 + SYM_VIA_ENCL

SYM_IND_S = 2.5

def lri_sym_via_width(w):
    for vw in (7.5, 4.5, 1.5):
        if vw <= w - 2.0 * SYM_VIA_ENCL + 1e-9:
            return vw
    return 1.5

def lri_sym_stub(w):
    back = (w / 2.0) * (math.sqrt(2.0) - 1.0)
    return max(SYM_VIA_EDGE, 2.6 - back)

def lri_sym_under_poly(pts, w):
    if len(pts) != 5:
        return lri_sweep(pts, w)
    (x0, y0), (x1, _y1), (x2, y2), _p3, (x4, _y4) = pts
    h = w / 2.0
    back = h * (math.sqrt(2.0) - 1.0)
    sg = 1.0 if y2 >= pts[1][1] else -1.0
    ya = y0 - sg * h
    yb = y2 + sg * h
    return [
        (x0, ya),
        (x1 + back, ya),
        (x2 + back, y2 - sg * h),
        (x4, y2 - sg * h),
        (x4, yb),
        (x2 - back, yb),
        (x0, y0 + sg * (h + back - (x1 - x0))),
    ]

def lri_sym_delta_window(w, s, R, p):
    a = math.sqrt(2.0) * (w / 2.0 + SYM_IND_S)
    dmin = a - p + w / 2.0
    dmax = (math.sqrt(2.0) - 1.0) * R + p / 2.0 - w / 2.0 - a
    return dmin, dmax

def lri_sym_min_dout(n, w, s):
    p = w + s
    xj = p / 2.0
    rt2 = math.sqrt(2.0)
    N = 2 * int(math.floor(float(n) / 2.0)) + 1
    if N < 1:
        N = 1
    if N == 1:
        return 2.0 * (xj / (rt2 - 1.0) + w / 2.0)
    a = rt2 * (w / 2.0 + SYM_IND_S)
    dmin = a - p + w / 2.0
    r1 = (dmin + p / 2.0) / (rt2 - 1.0) + w / 2.0
    r2 = (2.0 * a + w - p * (0.5 + rt2)) / (rt2 - 1.0)
    r3 = xj / (rt2 - 1.0)
    Rlast = max(r1, r2, r3, 0.0)
    return 2.0 * (Rlast + (N - 1) * p + w / 2.0)

# ---------------------------------------------------------------- i3 内核
# 二代对称电感：rx/ry 独立、任意整数圈、可调切角、可调差分引线。
# 与 SKILL lriI3Rings/HalfR/Arm/BuildSym 逐行对应；规格见
# DEVELOPMENT_GUIDE §11.2。
SYM_I3_WEDGE_MIN = 2.6

SYM_I3_DELTA_FRAC = 0.5

def lri_i3_rings(rx, ry, w, s, n_turns, chamfer):
    """平行内偏移环族 -> (rings, p, N)，ring=(a,b,c)。"""
    if rx <= 0.0 or ry <= 0.0 or w <= 0.0 or s <= 0.0:
        raise ValueError("rx/ry/w/s 必须 > 0")
    if abs(float(n_turns) - round(float(n_turns))) > 1e-6:
        raise ValueError("n=%.3f 必须为整数（每臂圈数）" % float(n_turns))
    p = w + s
    a0, b0 = rx - w / 2.0, ry - w / 2.0
    if min(a0, b0) <= 0.0:
        raise ValueError("rx/ry 太小（需 > w=%.3f）" % w)
    c0 = max(0.0, min(float(chamfer), 1.0)) * min(a0, b0)
    N = max(1, int(round(float(n_turns))))
    out = []
    for k in range(N):
        a, b = a0 - k * p, b0 - k * p
        if min(a, b) <= w / 2.0 + 1e-9:
            raise ValueError(
                "第 %d 圈放不下 (a=%.2f b=%.2f)；请增大 rx/ry 或减小 n/w/s"
                % (k, a, b))
        c = max(0.0, c0 - k * p * (2.0 - math.sqrt(2.0)))
        if c > min(a, b) + 1e-9:
            raise ValueError("第 %d 圈切角 %.3f 超过半轴（减小 chamfer）"
                             % (k, c))
        out.append((a, b, c))
    return out, p, N

def lri_i3_half_r(a, b, c, xj):
    if xj > a - c + 1e-9:
        raise ValueError(
            "xj=%.3f 超出顶边直段 (a-c=%.3f)；减小 w/s、增大 rx/ry 或减小 "
            "chamfer" % (xj, a - c))
    return [(xj, b), (a - c, b), (a, b - c), (a, c - b), (a - c, -b),
            (xj, -b)]

def lri_i3_half_l(a, b, c, xj):
    return [(-x, y) for x, y in reversed(lri_i3_half_r(a, b, c, xj))]

def lri_i3_window(w, s_req, ring_k, ring_k1, xj, via_edge, stub):
    a, b, c = ring_k
    a1, b1, c1 = ring_k1
    rt2 = math.sqrt(2.0)
    dmin = (rt2 - 1.0) * (s_req + w / 2.0)
    dm1 = (a - c) - xj - (via_edge + stub)
    dm2 = (a1 - c1) - w * (rt2 - 1.0) / 2.0 - xj
    dm3 = (a - c) + s_req / 2.0 - rt2 * (w / 2.0 + s_req)
    return dmin, min(dm1, dm2, dm3)

def lri_i3_via_span(w, cut, sp, encl):
    sm = w - 2.0 * encl
    n = int(math.floor((sm + sp) / (cut + sp) + 1e-9))
    n = max(1, n)
    if n % 2 == 0:
        n -= 1
    n = max(1, n)
    return n * cut + (n - 1) * sp, n

def lri_i3_arm(rings, p, N, xj, w, s, side, via_edge, stub,
               delta_frac=SYM_I3_DELTA_FRAC):
    """单臂 -> (tops, unders, vias, trim_pts)。边为扁平通孔点表。"""
    halfs = []
    for k, (a, b, c) in enumerate(rings):
        seg = (lri_i3_half_r(a, b, c, xj) if k % 2 == 0
               else lri_i3_half_l(a, b, c, xj))
        if side < 0:
            seg = [(-x, y) for x, y in seg]
        halfs.append(seg)
    unders, vias, trim_pts = [], [], []
    for k in range(N - 1):
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if not under_here:
            continue
        dmin, dmax = lri_i3_window(w, s, rings[k], rings[k + 1], xj,
                                   via_edge, stub)
        if dmax < dmin - 1e-9:
            raise ValueError(
                "换圈 k=%d 的 45° 夹缝放不下 w=%.2f 端头（窗口 %.3f..%.3f）"
                % (k, w, dmin, dmax))
        delta = dmin + delta_frac * (dmax - dmin)
        a = halfs[k][-1]
        b = halfs[k + 1][0]
        sa = 1.0 if a[0] >= 0.0 else -1.0
        sb = 1.0 if b[0] >= 0.0 else -1.0
        at = (a[0] + sa * delta, a[1])
        bt = (b[0] + sb * delta, b[1])
        halfs[k][-1] = at
        halfs[k + 1][0] = bt
        av = (at[0] + sa * via_edge, at[1])
        bv = (bt[0] + sb * via_edge, bt[1])
        ae = (av[0] + sa * stub, av[1])
        be = (bv[0] + sb * stub, bv[1])
        cc = (av[0] - sa * p, bv[1])
        unders.append([ae, av, cc, bv, be])
        vias.extend([av, bv])
        trim_pts.extend([at, bt])
    tops = []
    run = list(halfs[0])
    for k in range(N - 1):
        nxt = halfs[k + 1][0]
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if under_here:
            tops.append(run)
            run = list(halfs[k + 1])
        else:
            run.append(nxt)
            run.extend(halfs[k + 1][1:])
    tops.append(run)
    return tops, unders, vias, trim_pts

def lri_i3_build(rx, ry, n, w, s, chamfer=2.0 - math.sqrt(2.0),
                 lead_len=20.0, lead_gap=None, lead_w=None, ct=0):
    """完整二代对称电感 -> dict（metal/under/vias/ports/bbox）。"""
    rings, p, N = lri_i3_rings(rx, ry, w, s, n, chamfer)
    a0, b0, _c0 = rings[0]
    xj = p / 2.0
    lead_gap = s if lead_gap is None else float(lead_gap)
    lead_w = w if lead_w is None else float(lead_w)
    if lead_gap < s - 1e-9:
        raise ValueError("lead_gap=%.3f < s=%.3f" % (lead_gap, s))
    x_port = (lead_gap + lead_w) / 2.0
    y_port = b0 + lead_len
    if lead_len < w / 2.0 - 1e-9:
        raise ValueError("lead_len < w/2，端口会落在外圈金属内")
    if x_port < xj - 1e-9:
        raise ValueError("lead_gap 太小（引线落进换圈夹缝）")
    if x_port > a0 + 1e-9:
        raise ValueError(
            "lead_gap=%.3f 把引线推到外圈之外（x_port=%.3f > a0=%.3f）"
            % (lead_gap, x_port, a0))
    cut, sp, encl = SYM_VIA_CUT, SYM_VIA_SPACE, SYM_VIA_ENCL
    via_edge = cut / 2.0 + encl
    back = w / 2.0 * (math.sqrt(2.0) - 1.0)
    stub = max(via_edge, max(0.0, SYM_I3_WEDGE_MIN - back))
    span, ncut = lri_i3_via_span(w, cut, sp, encl)
    qa = lri_i3_arm(rings, p, N, xj, w, s, +1, via_edge, stub)
    qb = lri_i3_arm(rings, p, N, xj, w, s, -1, via_edge, stub)
    tops_a, under_a, vias_a, _ta = qa
    tops_b, under_b, vias_b, _tb = qb
    bridge = [tops_a[-1][-1], tops_b[-1][-1]]
    bridge_y = bridge[0][1]
    if abs(bridge[1][1] - bridge_y) > 1e-9:
        raise ValueError("桥两端不等高")
    lead_a = [(xj, b0), (x_port, b0), (x_port, y_port)]
    lead_b = [(-xj, b0), (-x_port, b0), (-x_port, y_port)]
    metal = list(tops_a) + list(tops_b) + [bridge, lead_a, lead_b]
    ports = [("p01", (x_port, y_port)), ("p02", (-x_port, y_port))]
    ct_geom = None
    if ct:
        bl = rings[-1][1]
        if bl < 1.5 * w + s:
            raise ValueError("内孔太小放不下中心抽头（b_last=%.2f）" % bl)
        ct_geom = {"stem": [(0.0, bridge_y), (0.0, 0.0)],
                   "pad": (0.0, 0.0, w)}
        metal.append(ct_geom["stem"])
        ports.append(("ct1", (0.0, bridge_y - (1.5 if bridge_y >= 0 else -1.5))))
    under = list(under_a) + list(under_b)
    return {
        "tops": metal, "under": under, "under_poly": [],
        "vias": [(x, y, span) for (x, y) in (vias_a + vias_b)],
        "ports": ports, "bridge": bridge, "bridge_y": bridge_y,
        "ct": ct_geom, "N": N, "rings": rings, "p": p, "xj": xj,
        "rx": rx, "ry": ry, "ncut": ncut, "via_span": span,
        "bx": 2.0 * rx, "by": 2.0 * ry,
        "bbox": (-rx, -ry, rx, y_port),
    }

def lri_sym_arm(dout, n, w, s, side=1):
    if dout <= 0.0 or w <= 0.0 or s <= 0.0:
        raise ValueError("dout/w/s 必须 > 0")
    p = w + s
    xj = p / 2.0
    R0 = dout / 2.0 - w / 2.0
    if R0 <= 0.0:
        raise ValueError("dout=%.3f 太小（需 > w=%.3f）" % (dout, w))
    N = 2 * int(math.floor(float(n) / 2.0)) + 1
    if N < 1:
        N = 1
    Rlast = R0 - (N - 1) * p
    if Rlast <= 0.0:
        raise ValueError(
            "dout=%.3f 放不下 %d 圈；请把 dout 增大到 >= %.1f 或减小 n/w/s"
            % (dout, N, lri_sym_min_dout(n, w, s)))
    if Rlast * (math.sqrt(2.0) - 1.0) < xj - 1e-9:
        raise ValueError(
            "最内半圈 R=%.3f 的直边太短，放不下 xj=%.3f；"
            "请把 dout 增大到 >= %.1f 或减小 w/s"
            % (Rlast, xj, lri_sym_min_dout(n, w, s)))
    halfs = []
    for k in range(N):
        R = R0 - k * p
        seg = (lri_half_right(R, xj) if k % 2 == 0
               else lri_half_left(R, xj))
        if side < 0:
            seg = [(-x, y) for x, y in seg]
        halfs.append(seg)
    unders = []
    vias = []
    trim_pts = []
    via_w = lri_sym_via_width(w)
    stub = lri_sym_stub(w)
    for k in range(N - 1):
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if not under_here:
            continue
        R = R0 - k * p
        dmin, dmax = lri_sym_delta_window(w, s, R, p)
        Rin = R - p
        chord_max = (math.sqrt(2.0) - 1.0) * (Rin - w / 2.0) - xj
        tm_max = ((math.sqrt(2.0) - 1.0) * R - xj
                  - (SYM_VIA_EDGE + stub))
        upper = min(dmax, chord_max, tm_max)
        if upper < dmin - 1e-9:
            raise ValueError(
                "换圈半径 R=%.3f（内圈 %.3f）的 45° 夹缝放不下 w=%.2f 端头"
                "（下界 %.2f，上界 min(%.2f,直边 %.2f,TM端 %.2f)=%.2f）；"
                "请把 dout 增大到 >= %.1f 或减小 w/s"
                % (R, Rin, w, dmin, dmax, chord_max, tm_max, upper,
                   lri_sym_min_dout(n, w, s)))
        delta = max(0.0, 0.5 * (dmin + upper))
        a = halfs[k][-1]
        b = halfs[k + 1][0]
        sa = 1.0 if a[0] >= 0.0 else -1.0
        sb = 1.0 if b[0] >= 0.0 else -1.0
        at = (a[0] + sa * delta, a[1])
        bt = (b[0] + sb * delta, b[1])
        halfs[k][-1] = at
        halfs[k + 1][0] = bt
        av = (at[0] + sa * SYM_VIA_EDGE, at[1])
        bv = (bt[0] + sb * SYM_VIA_EDGE, bt[1])
        ae = (av[0] + sa * stub, av[1])
        be = (bv[0] + sb * stub, bv[1])
        c = (av[0] - sa * p, bv[1])
        unders.append([ae, av, c, bv, be])
        vias.append([av[0], av[1], via_w])
        vias.append([bv[0], bv[1], via_w])
        trim_pts.extend([at, bt])
    tops = []
    run = list(halfs[0])
    for k in range(N - 1):
        nxt = halfs[k + 1][0]
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if under_here:
            tops.append(run)
            run = list(halfs[k + 1])
        else:
            run.append(nxt)
            run.extend(halfs[k + 1][1:])
    tops.append(run)
    def _is_trim(pt):
        return any(abs(pt[0] - q[0]) <= 1e-6 and abs(pt[1] - q[1]) <= 1e-6
                   for q in trim_pts)
    topcaps = [(not _is_trim(rr[0]), not _is_trim(rr[-1])) for rr in tops]
    return tops, unders, vias, topcaps, N, R0, Rlast, xj

def lri_build_ind(g):
    W = float(g.get("W", g.get("dout", 160.0)))
    H = float(g.get("H", W))
    w = float(g["w"])
    s = float(g["s"])
    n = float(g.get("n", 3.0))
    corner = float(g.get("corner") or 0.0)
    seam = g.get("seam", "bottom")
    kind = g.get("kind", "se")
    ct = int(g.get("ct", 0) or 0)
    _tv = g.get("tangle", 180)
    tangle = int(float(180 if _tv in (None, "") else _tv))
    stub = float(g.get("stub", 40.0) or 40.0)
    out = {"metal": [], "under": [], "ports": [],
           "bx": W, "by": H, "kind": kind}

    if kind == "sym":
        # 二代内核：rx/ry 独立（缺省由 dout 推出）、任意整数圈、可调切角、
        # 可调差分引线；与 SKILL lriI3BuildSym 同几何。
        dout = float(g.get("dout", W))
        rx = float(g.get("rx", dout / 2.0))
        ry = float(g.get("ry", dout / 2.0))
        m = lri_i3_build(
            rx, ry, n, w, s,
            chamfer=float(g.get("chamfer", 2.0 - math.sqrt(2.0))),
            lead_len=float(g.get("lead_len", 20.0)),
            lead_gap=g.get("lead_gap"), lead_w=g.get("lead_w"),
            ct=int(float(g.get("ct", 0) or 0)))
        out["metal"] = list(m["tops"])
        out["topcaps"] = [(False, False)] * len(out["metal"])
        out["under"] = list(m["under"])
        out["under_poly"] = [lri_sym_under_poly(p, w)
                             for p in m["under"]]
        out["vias"] = list(m["vias"])
        out["ports"] = list(m["ports"])
        out["bx"] = 2.0 * rx
        out["by"] = 2.0 * ry
        out["N"] = m["N"]
        out["bbox"] = m["bbox"]
        return out

    if kind == "diff":
        p2 = w + s
        A = lri_spiral(W, H, w, s, n, corner, "bottom", 2.0 * p2)
        B = lri_mirrory(lri_spiral(W - 2.0 * p2, H - 2.0 * p2, w, s, n,
                                   corner, "bottom", 2.0 * p2))
        out["metal"] = [A, B]
        out["under"] = [[A[-1], (B[-1][0], A[-1][1]), B[-1]]]
        lead1 = _lri_lead(A[0], "S", stub)
        bx, _by = B[0]
        pex, pey = bx, H / 2.0 + w + 40.0 + stub
        out["under"].append([B[0], (pex, pey)])
        lead2 = [(pex, pey), (pex, pey + stub)]
        out["metal"] += [lead1, lead2]
        out["ports"] = [("p01", lead1[-1]), ("p02", lead2[-1])]
        if ct:
            tip = (B[-1][0], -(H / 2.0 + w + 40.0 + stub))
            out["under"].append([B[-1], tip])
            out["ports"].append(("ct1", tip))
        return out

    if kind == "f8":
        gapf = g.get("gap")
        gapf = 8.0 if gapf in (None, "") else float(gapf)
        gapf = max(gapf, w + s)
        d = (W - gapf) / 2.0
        nA = n / 2.0
        dx = (gapf + d) / 2.0
        a0m, b0m = (W - w) / 2.0, (H - w) / 2.0
        cf8 = corner
        if min(a0m, b0m) > 0.0:
            cf8 = corner * min((d - w) / 2.0, b0m) / min(a0m, b0m)
        Lf = lri_shift(lri_spiral(d, H, w, s, nA, cf8, "left"), -dx, 0.0)
        Rg = lri_shift(lri_mirrorx(
            lri_spiral(d, H, w, s, nA, cf8, "left")), dx, 0.0)
        out["metal"] = [Lf, Rg]
        out["under"] = [[Lf[-1], (Lf[-1][0], 0.0), (0.0, 0.0),
                         (Rg[-1][0], 0.0), Rg[-1]]]
        nat1 = lri_dir_key((Lf[0][0] + dx, Lf[0][1]))
        nat2 = lri_dir_key((Rg[0][0] - dx, Rg[0][1]))
        k1, k2 = lri_pick_dirs(tangle, nat1, nat2)
        lead1 = _lri_lead(Lf[0], k1, stub)
        lead2 = _lri_lead(Rg[0], k2, stub)
        out["metal"] += [lead1, lead2]
        out["ports"] = [("p01", lead1[-1]), ("p02", lead2[-1])]
        return out

    body = lri_spiral(W, H, w, s, n, corner, seam)
    out["metal"] = [body]
    nat1 = {"top": "N", "left": "W", "right": "E"}.get(seam, "S")
    ex, ey = body[-1][0], body[-1][1]
    Lr = math.hypot(ex, ey)
    nat2 = lri_dir_key((ex, ey) if Lr > 1e-6 else (0.0, 1.0))
    k1, k2 = lri_pick_dirs(tangle, nat1, nat2)
    lead1 = _lri_lead(body[0], k1, stub)
    out["metal"].append(lead1)
    tip = _lri_exit_pad(body[-1], k2, W, H, stub)
    out["under"].append([body[-1], tip])
    out["ports"] = [("p01", lead1[-1]), ("p02", tip)]
    if ct:
        cut = lri_point_at(body, 0.5)
        k3 = "S" if k1 != "S" else "N"
        tip2 = _lri_exit_pad(cut, k3, W, H, stub)
        out["under"].append([cut, tip2])
        out["ports"].append(("ct1", tip2))
    return out

def lri_draw_sketch(c, raw):
    try:
        g = form_to_geom("ind", raw)
        ok, msg = lri_validate(g)
        if not ok:
            raise ValueError(msg)
        geo = lri_build_ind(g)
    except Exception as exc:
        c.create_text(140, 86, text="参数不可行", fill="#a03030",
                      font=("fixed", 10))
        c.create_text(140, 110, text=str(exc)[:42], fill="#a03030",
                      font=("fixed", 8))
        return
    W, H = geo["bx"], geo["by"]
    w = float(g["w"])
    wu = w
    sc = min(196.0 / max(W, 1e-9), 128.0 / max(H, 1e-9))
    cx, cy = 140.0, 94.0

    def tx(p):
        return (cx + p[0] * sc, cy - p[1] * sc)

    def poly(pts, width, fill, edge):
        pp = lri_sweep(pts, width)
        if len(pp) < 3:
            return
        flat = []
        for q in pp:
            x, y = tx(q)
            flat.extend((x, y))
        c.create_polygon(flat, fill=fill, outline=edge, width=1)

    for pts in geo["metal"]:
        poly(pts, w, "#3a6ea5", "#1f4064")
    if geo.get("under_poly"):
        for pp in geo["under_poly"]:
            if len(pp) >= 3:
                flat = []
                for q in pp:
                    x, y = tx(q)
                    flat.extend((x, y))
                c.create_polygon(flat, fill="#c0504d", outline="#7a2e2c",
                                 width=1)
    else:
        for seg in geo["under"]:
            poly(seg, wu, "#c0504d", "#7a2e2c")
    for vx, vy, vw in geo.get("vias", []):
        pitch = SYM_VIA_CUT + SYM_VIA_SPACE
        n = max(1, int(round((vw + SYM_VIA_SPACE) / pitch)))
        for i in range(n):
            yy = vy + (i - (n - 1) / 2.0) * pitch
            px, py = tx((vx, yy))
            hw = max(0.8, SYM_VIA_CUT / 2.0 * sc)
            c.create_rectangle(px - hw, py - hw, px + hw, py + hw,
                               fill="#0a7d33", outline="#064d1f")
    x1, y1 = tx((-W / 2.0, H / 2.0))
    x2, y2 = tx((W / 2.0, -H / 2.0))
    c.create_rectangle(x1, y1, x2, y2, outline="#9a9a9a", dash=(3, 3))
    for name, pt in geo["ports"]:
        x, y = tx(pt)
        col = "#b02020" if name.startswith("ct") else "#0a7d33"
        c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=col, outline="")
        c.create_text(x, y - 9, text=name, fill=col, font=("fixed", 7))
    yb = max(y1, y2) + 13
    c.create_line(x1, yb, x2, yb, fill="#a03030", arrow="both")
    c.create_text((x1 + x2) / 2.0, yb + 8, text="W=%g" % W, fill="#a03030",
                  font=("fixed", 8))
    xl = min(x1, x2) - 14
    c.create_line(xl, y1, xl, y2, fill="#a03030", arrow="both")
    c.create_text(xl - 2, (y1 + y2) / 2.0, text="H=%g" % H, fill="#a03030",
                  font=("fixed", 8), angle=90)
    if g.get("kind") == "sym":
        c.create_text(140, 182, fill="#444444", font=("fixed", 8),
                      text="dout=%g  w=%g  s=%g  实际圈数 N=%d"
                           % (float(g.get("dout", W)), w, float(g["s"]),
                              int(geo.get("N", 0))))
        c.create_text(140, 197, fill="#666666", font=("fixed", 8),
                      text="kind=sym  端口同侧顶部  内端下方对称轴")
    else:
        c.create_text(140, 182, fill="#444444", font=("fixed", 8),
                      text="n=%g  w=%g  s=%g  corner=%g"
                           % (float(g["n"]), w, float(g["s"]),
                              float(g["corner"])))
        c.create_text(140, 197, fill="#666666", font=("fixed", 8),
                      text="kind=%s  shape=%s  ct=%d  seam=%s"
                           % (g["kind"], g["shape"], int(g["ct"]), g["seam"]))

def lri_sketch_after(tab):
    try:
        if getattr(tab, "_lri_after", None):
            tab.sketch.after_cancel(tab._lri_after)
    except Exception:
        pass
    try:
        tab._lri_after = tab.sketch.after(
            140, lambda: lri_redraw_sketch(tab))
    except Exception:
        tab._lri_after = None

def lri_redraw_sketch(tab):
    try:
        tab._draw_geom_sketch("ind")
    except Exception:
        pass

PRESETS = [ {"name": "1nH 八角电感", "type": "ind", "freq": "1e9:30e9:30",
     "geom": {"kind": "se", "shape": "octagon", "n": 2.5, "w": 10, "s": 2.6,
              "dout": 150, "ct": 0, "underpass": "direct"}},
    {"name": "2nH 方形电感", "type": "ind", "freq": "1e9:30e9:30",
     "geom": {"kind": "se", "shape": "square", "n": 3.5, "w": 12, "s": 2.6,
              "dout": 220, "ct": 0, "underpass": "direct"}},
    {"name": "中心抽头电感", "type": "ind", "freq": "1e9:30e9:21",
     "geom": {"kind": "se", "shape": "octagon", "n": 2, "w": 10, "s": 2.6,
              "dout": 160, "ct": 1, "underpass": "direct"}},
    {"name": "差分电感 (diff)", "type": "ind", "freq": "1e9:30e9:21",
     "geom": {"kind": "diff", "shape": "octagon", "n": 2.5, "w": 10,
              "s": 2.6, "dout": 220, "ct": 0}},
    {"name": "8 字电感 (f8)", "type": "ind", "freq": "1e9:30e9:21",
     "geom": {"kind": "f8", "shape": "octagon", "n": 3, "w": 10, "s": 2.6,
              "dout": 200, "ct": 0}},
    {"name": "50Ω 微带线", "type": "tl", "freq": "1e9:30e9:21",
     "geom": {"w": 20, "len": 300, "gnd": 1, "gnd_layer": "M1", "fence": 0, "cpw": 0, "gap": 5}},
    {"name": "CPW 线", "type": "tl", "freq": "1e9:30e9:21",
     "geom": {"w": 15, "len": 250, "gnd": 1, "gnd_layer": "M1", "fence": 0, "cpw": 1, "gap": 6}},
    {"name": "1:1 balun", "type": "xfmr", "freq": "1e9:30e9:21",
     "geom": {"style": "balun", "np": 2, "ns": 2, "w": 10, "s": 2.6,
              "dout": 180, "gap": 3, "ct": 0}},
    {"name": "叠层变压器", "type": "xfmr", "freq": "1e9:30e9:21",
     "geom": {"style": "stacked", "np": 2, "ns": 2, "w": 10, "s": 2.6,
              "dout": 160, "gap": 3, "ct": 0}},
    {"name": "平行耦合线", "type": "cpl", "freq": "1e9:30e9:21",
     "geom": {"w": 10, "gap": 3, "len": 200, "gnd": 1}}, ]

GEOM_FORM = { "ind": [("kind", "拓扑 kind (se/diff/f8/sym)", "se", False),
            ("shape", "形状 rect/oct", "octagon", False),
            ("seam", "端口边 seam (N/S/E/W)", "S", False),
            ("tangle", "端口夹角 tangle (0/90/180)", "180", True),
            ("n", "匝数 n", "3", True), ("w", "线宽 w (µm)", "10", True),
            ("s", "间距 s (µm)", "3.2", True), ("dout", "外径 dout (µm)", "160", True),
            ("rx", "横向半径 rx (µm, 留空=dout/2) [仅 sym]", "", True),
            ("ry", "纵向半径 ry (µm, 留空=dout/2) [仅 sym]", "", True),
            ("chamfer", "拐角切角 chamfer 0..1 (留空=2-sqrt2) [仅 sym]", "", True),
            ("lead_len", "差分引线长 lead_len (µm) [仅 sym]", "20", True),
            ("lead_gap", "差分线间距 lead_gap (µm, 留空=s) [仅 sym]", "", True),
            ("lead_w", "差分线宽 lead_w (µm, 留空=w) [仅 sym]", "", True),
            ("W", "宽 W (µm, 留空=dout)", "", True),
            ("H", "高 H (µm, 留空=dout)", "", True),
            ("corner", "45° 切角 corner (µm, 留空=八角自动)", "", True),
            ("din", "内径 din (µm, 留空=闭合) [仅旧内核]", "", True),
            ("ct", "中心抽头 ct (0/1)", "0", True),
            ("ct_dir", "抽头方向 ct_dir (N/S/E/W) [仅旧内核]", "S", False),
            ("underpass", "underpass direct/route [仅旧内核]", "direct", False),
            ("ex", "横向拉长 ex (µm) [仅旧内核]", "", True),
            ("ey", "纵向拉长 ey (µm) [仅旧内核]", "", True),
            ("stack", "叠层 stack (0/1) [仅 sym]", "0", True),
            ("metal_main", "主金属 (留空=工艺档) [仅 sym]", "", False),
            ("metal_aux", "副金属/跨线层 (留空=工艺档) [仅 sym]", "", False),
            ("ind_mark", "MARKER 标记层 (0/1)", "0", True)],
    "tl": [("w", "线宽 w (µm)", "20", True), ("len", "长度 len (µm)", "300", True),
           ("gnd", "带地 gnd (0/1)", "1", True), ("gnd_layer", "地层", "M1", False),
           ("fence", "围栏 fence (0/1)", "0", True), ("cpw", "CPW 模式 (0/1)", "0", True),
           ("gap", "CPW gap (µm)", "5", True)],
    "xfmr": [("style", "风格 balun/stacked/interleaved", "balun", False),
             ("np", "初级匝数 np", "2", True), ("ns", "次级匝数 ns", "2", True),
             ("w", "线宽 w (µm)", "10", True), ("s", "间距 s (µm)", "2.6", True),
             ("dout", "外径 dout (µm)", "180", True), ("gap", "级间 gap (µm)", "3", True),
             ("ct", "中心抽头 ct (0/1)", "0", True)], "cpl": [("w", "线宽 w (µm)", "10", True),
            ("gap", "耦合 gap (µm)", "3", True), ("len", "长度 len (µm)", "200", True),
            ("gnd", "带地 gnd (0/1)", "1", True)], }

GEOM_NUM = set()

for _rows in GEOM_FORM.values():
    for _k, _lab, _d, _num in _rows:
        if _num:
            GEOM_NUM.add(_k)

GEOM_CHOICES = {
    "kind": ("se", "diff", "f8", "sym"),
    "shape": ("rect", "oct", "octagon", "square"),
    "seam": ("S", "N", "E", "W"),
    "tangle": ("180", "90", "0"),
    "style": ("balun", "stacked", "interleaved"),
}

def form_to_geom(typ, raw):
    geom = {}
    for key, _lab, _d, num in GEOM_FORM.get(typ, []):
        v = (raw.get(key) or "").strip()
        if v == "":
            continue
        if num:
            try:
                geom[key] = parse_eng(v)
            except ValueError as e:
                raise LibrfError("%s: %s" % (key, e))
        else:
            geom[key] = v
    if typ == "ind":
        geom = lri_norm_geom(geom)
    return geom
