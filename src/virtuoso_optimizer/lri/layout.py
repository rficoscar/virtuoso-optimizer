# -*- coding: utf-8 -*-
"""lri.layout — inductor geometry kernel (i3/symmetric/single-ended), DRC checks and form mapping."""
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
        # Parameter set: rx/ry (defaults derived from dout), n/w/s, chamfer, lead_*.
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
        _tv = g.get("tangle", 180)
        g["tangle"] = int(float(180 if _tv in (None, "") else _tv))
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
    """Both differential arms and both leads are separate polygons: s / lead_gap must be >= the wide-metal spacing for this layer."""
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
        # abs(): the end flare must not depend on the sign of (x1-x0), else
        # one side of the crossing gets a shorter band end and its via column
        # sticks out (G4b floating vias).  Symmetric in x by construction.
        (x0, y0 + sg * (h + back + abs(x1 - x0))),
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

# ---------------------------------------------------------------- i3 kernel
# Second-generation symmetric inductor: independent rx/ry, any integer turns,
# adjustable chamfer and differential leads. Line-by-line counterpart of SKILL
# lriI3Rings/HalfR/Arm/BuildSym; see the SKILL source for the specification.
SYM_I3_WEDGE_MIN = 2.6

SYM_I3_DELTA_FRAC = 0.5

def lri_i3_rings(rx, ry, w, s, n_turns, chamfer):
    """Parallel inward-offset ring family -> (rings, p, N), ring=(a,b,c)."""
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

def lri_i3_cross_gap(w, s, p):
    """rfgeom.cross_gap: ring-end pull-back so the gap at a crossing is s.

    Algebraically identical to the old lri_i3_window dmin
    ``(sqrt(2)-1)*(s + w/2)``.
    """
    return max(0.0, math.sqrt(2.0) * (s + w / 2.0) - p + w / 2.0)


def lri_i3_half_xy(a, b, c, x_top, x_bot, sg):
    """Half ring with independent end abscissae (rfgeom.half_top_to_bot).

    x_top/x_bot are UNSIGNED and sg picks the side: +1 right half, -1 mirrored
    left half.  Passing a pre-signed x negates the endpoints twice and drags
    the ring end onto the opposite side.
    """
    if max(x_top, x_bot) > a - c + 1e-9:
        raise ValueError(
            "环端 x=%.3f 超出顶边直段 (a-c=%.3f)；减小 w/s、增大 rx/ry 或减小 "
            "chamfer" % (max(x_top, x_bot), a - c))
    pts = [(x_top, b), (a - c, b), (a, b - c), (a, c - b), (a - c, -b),
           (x_bot, -b)]
    if sg < 0:
        pts = [(-x, y) for x, y in pts]
    return lri_dedup(pts)

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
    """Single arm -> (tops, unders, vias, trim_pts). Edges are flat via point lists.

    Turn changes follow the rfgeom law (line-by-line counterpart of SKILL
    lriI3Arm): ring ends pulled out by the closed-form cross gap e, joined by
    a symmetric 45-degree X crossing on the coil axis, one branch on the main
    metal and the other on the under layer.  The old lri_i3_window /
    delta_frac sliding heuristic is gone -- it picked a fit-dependent offset,
    which is what made the windings irregular.  Only its dmax feasibility
    bound is kept: it accounts for the 45-degree chamfer miter
    w*(sqrt(2)-1)/2 at the inner ring end, which the plain edge length does
    not, and dropping it let e.g. w=14/rx=60 build when the suite expects a
    rejection.
    """
    e = lri_i3_cross_gap(w, s, p)
    xe = xj + e
    a0, _b0, c0 = rings[0]
    aN, _bN, cN = rings[-1]
    lmax = min(a0 - c0, aN - cN) - xe
    if lmax < via_edge + stub:
        raise ValueError(
            "45° 跨线放不下通孔区 (可用 %.3f < 需要 %.3f)；请增大 rx/ry 或减小 "
            "w/s/n" % (lmax, via_edge + stub))
    unders, vias, trim_pts = [], [], []
    tops = []
    run = None
    for k in range(N):
        a, b, c = rings[k]
        kside = side * (1 if k % 2 == 0 else -1)
        last = (k == N - 1)
        # both abscissae UNSIGNED -- lri_i3_half_xy mirrors by kside.  The
        # free end of the innermost ring lands on xj to meet the bridge; only
        # ring changes use the pulled-out xe.
        x_end = xj if last else xe
        x_lead = xj if (k == 0 or last) else xe
        seg = lri_i3_half_xy(a, b, c, x_lead, x_end, kside)
        if k % 2 == 1:
            seg = list(reversed(seg))
        run = (run + seg[1:]) if run else list(seg)
        if last:
            break
        y_end = -b if k % 2 == 0 else b
        y_next = -(b - p) if k % 2 == 0 else (b - p)
        r1 = rings[k + 1]
        dmax = min(
            (a - c) - xj - (via_edge + stub),
            (r1[0] - r1[2]) - w * (math.sqrt(2.0) - 1.0) / 2.0 - xj,
            (a - c) + s / 2.0 - math.sqrt(2.0) * (w / 2.0 + s))
        if dmax < e - 1e-9:
            raise ValueError(
                "换圈 k=%d 的 45° 夹缝放不下（需 %.3f > 可用 %.3f）；请增大 "
                "rx/ry 或减小 w/s" % (k, e, dmax))
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        at = (kside * xe, y_end)
        bt = (-kside * xe, y_next)
        av = (kside * (xe + via_edge), y_end)
        bv = (-kside * (xe + via_edge), y_next)
        ae = (kside * (xe + via_edge + stub), y_end)
        be = (-kside * (xe + via_edge + stub), y_next)
        trim_pts.extend([at, bt])
        if under_here:
            tops.append(run)
            run = None
            # rfgeom law: the X crossing is centred on the coil axis -- the
            # diagonal runs (kside*xj, y_end) -> (-kside*xj, y_next), so
            # dx = dy = p and the crossing point is exactly x = 0.  Starting
            # the diagonal at the via pad (av, cc) puts it off-axis; that
            # asymmetry is the irregularity this rewrite removes.  Both
            # horizontal runs still sweep through the via pads.
            unders.append([ae, (kside * xj, y_end), (-kside * xj, y_next),
                           (-kside * xe, y_next), be])
            vias.extend([av, bv])
        else:
            # the X crossing stays on the main metal this turn: same 4-point
            # jog, folded into the run
            jog = [(kside * xe, y_end), (kside * xj, y_end),
                   (-kside * xj, y_next), (-kside * xe, y_next)]
            run.extend(jog[1:])
    tops.append(run)
    return tops, unders, vias, trim_pts

def lri_i3_build(rx, ry, n, w, s, chamfer=2.0 - math.sqrt(2.0),
                 lead_len=20.0, lead_gap=None, lead_w=None, ct=0):
    """Complete second-generation symmetric inductor -> dict (metal/under/vias/ports/bbox)."""
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

def lri_i3_spiral_run(rings, p, xj, x_lead, w, s, arm):
    """One inward spiral on a SINGLE layer -> point list.

    Counterpart of SKILL lriI3SpiralRun / rfgeom.spiral_run.  A single
    winding never crosses itself at a turn change -- the jog only carries it
    from one side of the coil axis to the other -- so the X crossing stays on
    the same metal and no underpass is needed.  ``arm=-1`` is the 180 deg
    rotation of ``arm=+1``, which is how a transformer secondary sits
    opposite its primary.
    """
    e = lri_i3_cross_gap(w, s, p)
    xe = xj + e
    a0, _b0, c0 = rings[0]
    aN, _bN, cN = rings[-1]
    lmax = min(a0 - c0, aN - cN) - xe
    if lmax < 0.0:
        raise ValueError(
            "45° 跨线放不下 (可用 %.3f)；请增大 rx/ry 或减小 w/s/n" % lmax)
    N = len(rings)
    pts = None
    for k in range(N):
        a, b, c = rings[k]
        kside = arm * (1 if k % 2 == 0 else -1)
        last = (k == N - 1)
        x_end = xj if last else xe
        # both abscissae UNSIGNED -- lri_i3_half_xy mirrors by kside.  The
        # free end of the innermost ring lands on xj; only ring changes use
        # the pulled-out xe.
        xl = x_lead if k == 0 else (xj if last else xe)
        seg = lri_i3_half_xy(a, b, c, xl, x_end, kside)
        if k % 2 == 1:
            seg = list(reversed(seg))
        pts = (pts + seg[1:]) if pts else list(seg)
        if last:
            break
        y_end = -b if k % 2 == 0 else b
        y_next = -(b - p) if k % 2 == 0 else (b - p)
        jog = lri_dedup([(kside * xe, y_end), (kside * xj, y_end),
                         (-kside * xj, y_next), (-kside * xe, y_next)])
        pts = pts + jog[1:]
    return pts


def lri_i3_build_xfmr(rx, ry, np_turns=2.0, ns_turns=2.0, w=8.0, s=3.0,
                      gap=None, chamfer=None, ct=0, shrink=1,
                      lead_len=20.0):
    """Interleaved / stacked transformer -> dict.

    Line-by-line counterpart of SKILL lriI3BuildXfmr and rfgeom.build_xfmr:
    two concentric octagonal spirals, primary on the main metal (``tops``) and
    secondary on the under metal (``under``) rotated 180 deg, so the layer
    change does the crossing work and neither winding needs an underpass.
    ``shrink`` pulls the secondary outer radius in by that many primary
    pitches -- the interleaving knob; 0 degenerates to a plain stack.
    """
    if chamfer is None:
        chamfer = 2.0 - math.sqrt(2.0)
    gap = s if gap is None else float(gap)
    if gap < s - 1e-9:
        raise ValueError("gap=%.3f < s=%.3f" % (gap, s))
    # np/ns count FULL turns of each winding (same convention as
    # lri_i3_build_se): one winding covers the whole coil, so two half-rings
    # per turn
    for _t in (np_turns, ns_turns):
        if abs(2.0 * float(_t) - round(2.0 * float(_t))) > 1e-6:
            raise ValueError("np/ns=%.3f 必须是 0.5 的整数倍（半圈）" % float(_t))
    rings_p, p, nP = lri_i3_rings(rx, ry, w, s, 2.0 * float(np_turns), chamfer)
    rs = max(0, int(round(float(shrink))))
    rings_s, p_s, nS = lri_i3_rings(max(0.0, rx - rs * p),
                                    max(0.0, ry - rs * p),
                                    w, s, 2.0 * float(ns_turns), chamfer)
    if abs(p_s - p) > 1e-9:
        raise ValueError("primary/secondary pitch mismatch")
    xj = p / 2.0
    p_pts = lri_i3_spiral_run(rings_p, p, xj, xj, w, s, +1)
    s_pts = lri_i3_spiral_run(rings_s, p, xj, xj, w, s, -1)
    p_out, s_out = p_pts[0], s_pts[0]
    p_in, s_in = p_pts[-1], s_pts[-1]
    p_lead = [p_out, (p_out[0], p_out[1] + lead_len)]
    s_lead = [s_out, (s_out[0], s_out[1] + lead_len)]
    tops = [p_pts, p_lead]
    under = [s_pts, s_lead]
    ports = [("p01", p_lead[-1]), ("p02", p_in),
             ("p03", s_lead[-1]), ("p04", s_in)]
    ct_geom = None
    if ct:
        # the stub starts at s_in itself, not on the axis -- an axis-only
        # start would leave it detached from the winding by |s_in.x|
        y_tip = s_in[1] + (lead_len if s_in[1] >= 0.0 else -lead_len)
        ct_geom = {"stem": [s_in, (0.0, s_in[1]), (0.0, y_tip)]}
        under.append(ct_geom["stem"])
        ports.append(("ct1", (0.0, y_tip)))
    ymax = max(p_lead[-1][1], s_lead[-1][1])
    ymin = min(p_in[1], s_in[1])
    return {
        "tops": tops, "under": under, "under_poly": [], "vias": [],
        "ports": ports, "ct": ct_geom, "kind": "xfmr",
        "rings_p": rings_p, "rings_s": rings_s,
        "nP": nP, "nS": nS, "p": p, "xj": xj,
        "bx": 2.0 * rx, "by": 2.0 * ry,
        "bbox": (-rx, ymin, rx, ymax),
    }


def lri_i3_build_se(rx, ry, n, w, s, chamfer=2.0 - math.sqrt(2.0),
                    lead_len=20.0, ct=0, ct_len=20.0):
    """Single-ended octagonal inductor -> dict.

    Line-by-line counterpart of SKILL lriI3BuildSe and rfgeom.build_se: one
    inward spiral on a single layer -- a single winding never crosses
    itself, so no underpass is needed -- with straight axis-aligned leads.
    p02 leaves the inner end along that end's own y sign so the two leads
    never collide whatever the turn count.  ``n`` counts FULL turns, as the
    legacy lriSpiral did: one winding covers the whole coil, so the ring
    family holds ``2*n`` half-rings.  Half turns are legal, quarter turns
    are not.
    """
    if abs(2.0 * float(n) - round(2.0 * float(n))) > 1e-6:
        raise ValueError("n=%.3f 必须是 0.5 的整数倍（半圈）" % float(n))
    rings, p, N = lri_i3_rings(rx, ry, w, s, 2.0 * float(n), chamfer)
    a0, b0, _c0 = rings[0]
    xj = p / 2.0
    pts = lri_i3_spiral_run(rings, p, xj, xj, w, s, +1)
    p_out, p_in = pts[0], pts[-1]
    out_lead = [p_out, (p_out[0], p_out[1] + lead_len)]
    sgn = 1.0 if p_in[1] >= 0.0 else -1.0
    in_lead = [p_in, (p_in[0], p_in[1] + sgn * lead_len)]
    tops = [pts, out_lead, in_lead]
    ports = [("p01", out_lead[-1]), ("p02", in_lead[-1])]
    under = []
    ct_geom = None
    if ct:
        # the tap leaves on the under metal so it may cross the winding it
        # taps; the via pad at `mid` joins the two
        mid = lri_point_at(pts, 0.5)
        sgc = 1.0 if mid[1] >= 0.0 else -1.0
        y_tip = sgc * (ry + float(ct_len))
        ct_geom = {"tap": mid, "stem": [mid, (mid[0], y_tip)],
                   "pad": (mid[0], mid[1], w)}
        under.append(ct_geom["stem"])
        ports.append(("ct1", (mid[0], y_tip)))
    ymax = max(out_lead[-1][1], in_lead[-1][1])
    # single winding: even k fills the right, odd k the left, so the box is
    # one pitch narrower than the symmetric coil on the left
    xmin = -(rx - p) if N >= 2 else (p / 2.0 - w / 2.0)
    return {
        "tops": tops, "under": under, "under_poly": [], "vias": [],
        "ports": ports, "ct": ct_geom, "kind": "se",
        "rings": rings, "N": N, "p": p, "xj": xj,
        "bx": 2.0 * rx, "by": 2.0 * ry,
        "bbox": (xmin, -ry, rx, ymax),
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
        # Second-generation kernel: independent rx/ry (defaults derived from dout),
        # any integer turns, adjustable chamfer and differential leads; same
        # geometry as SKILL lriI3BuildSym.
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

    if kind == "se":
        # Second-generation kernel: rfgeom law, one winding on one layer and
        # no underpass (a single winding never crosses itself); same geometry
        # as SKILL lriI3BuildSe.  The legacy seam/tangle/corner knobs are gone.
        dout = float(g.get("dout", W))
        rx = float(g.get("rx", dout / 2.0))
        ry = float(g.get("ry", float(g.get("H", W)) / 2.0))
        m = lri_i3_build_se(
            rx, ry, n, w, s,
            chamfer=float(g.get("chamfer", 2.0 - math.sqrt(2.0))),
            lead_len=float(g.get("lead_len", 20.0) or 20.0),
            ct=int(float(g.get("ct", 0) or 0)),
            ct_len=float(g.get("ct_len", 20.0) or 20.0))
        out["metal"] = list(m["tops"])
        out["topcaps"] = [(False, False)] * len(out["metal"])
        out["under"] = list(m["under"])
        out["under_poly"] = [lri_sym_under_poly(p, w) for p in m["under"]]
        out["vias"] = list(m["vias"])
        out["ports"] = list(m["ports"])
        out["bx"] = m["bx"]
        out["by"] = m["by"]
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
    # Include port leads and underpasses in the bounding box so the sketch is not clipped by the canvas edge
    xs, ys = [-W / 2.0, W / 2.0], [-H / 2.0, H / 2.0]
    for _name, _pt in geo["ports"]:
        xs.append(_pt[0])
        ys.append(_pt[1])
    for _pp in (geo.get("under_poly") or []):
        for _q in _pp:
            xs.append(_q[0])
            ys.append(_q[1])
    for _seg in (geo.get("under") or []):
        for _q in _seg:
            xs.append(_q[0])
            ys.append(_q[1])
    for _vx, _vy, _vw in (geo.get("vias") or []):
        ys.append(_vy + _vw / 2.0)
        ys.append(_vy - _vw / 2.0)
    _pad = w / 2.0
    minx, maxx = min(xs) - _pad, max(xs) + _pad
    miny, maxy = min(ys) - _pad, max(ys) + _pad
    sc = min(240.0 / max(maxx - minx, 1e-9),
             132.0 / max(maxy - miny, 1e-9))
    cxm, cym = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    cx, cy = 140.0, 85.0

    def tx(p):
        return (cx + (p[0] - cxm) * sc, cy - (p[1] - cym) * sc)

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
        # Place port names beside the dots so they do not cover dimension lines or metal
        if x >= cx:
            c.create_text(x + 7, y, text=name, fill=col,
                          font=("fixed", 7), anchor="w")
        else:
            c.create_text(x - 7, y, text=name, fill=col,
                          font=("fixed", 7), anchor="e")
    bb = c.bbox("all") or (cx - 60, cy - 60, cx + 60, cy + 60)
    yb = bb[3] + 10
    c.create_line(x1, yb, x2, yb, fill="#a03030", arrow="both")
    c.create_text((x1 + x2) / 2.0, yb + 8, text="W=%g" % W, fill="#a03030",
                  font=("fixed", 8))
    xl = bb[0] - 12
    c.create_line(xl, y1, xl, y2, fill="#a03030", arrow="both")
    c.create_text(xl - 2, (y1 + y2) / 2.0, text="H=%g" % H, fill="#a03030",
                  font=("fixed", 8), angle=90)
    if g.get("kind") == "sym":
        c.create_text(140, 186, fill="#444444", font=("fixed", 8),
                      text="dout=%g  w=%g  s=%g  实际圈数 N=%d"
                           % (float(g.get("dout", W)), w, float(g["s"]),
                              int(geo.get("N", 0))))
        c.create_text(140, 199, fill="#666666", font=("fixed", 8),
                      text="kind=sym  端口同侧顶部  内端下方对称轴")
    else:
        c.create_text(140, 186, fill="#444444", font=("fixed", 8),
                      text="n=%g  w=%g  s=%g  corner=%g"
                           % (float(g["n"]), w, float(g["s"]),
                              float(g["corner"])))
        c.create_text(140, 199, fill="#666666", font=("fixed", 8),
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
        typ = tab.a_type.get()
    except Exception:
        typ = "ind"
    try:
        tab._draw_geom_sketch(typ)
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
