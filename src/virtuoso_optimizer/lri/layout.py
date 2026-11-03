# -*- coding: utf-8 -*-
"""lri.layout — inductor geometry kernel (i3/symmetric/single-ended), DRC checks and form mapping."""
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

def lri_inter(p1, p2, q1, q2):
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
    if abs(det) < 1e-12:
        return None
    rx, ry = q1[0] - p1[0], q1[1] - p1[1]
    t = (rx * (-d2[1]) - ry * (-d2[0])) / det
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])

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

def lri_norm_geom(g):
    """Normalise a sym (differential symmetric) inductor geometry dict.

    The only inductor topology left is ``sym``: the two-arm octagonal
    symmetric coil.  ``ind`` / empty / auto are treated as sym; any other
    kind name is left untouched so ``lri_validate`` can reject it
    explicitly instead of silently coercing.
    """
    g = dict(g)
    raw_kind = str(g.get("kind", "")).strip().lower()
    if raw_kind in ("sym", "symmetric", "wiki", "rfw", "ind", "", "auto"):
        is_sym = True
    else:
        is_sym = False
    if not is_sym:
        g["kind"] = raw_kind or "sym"
        return g
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
    kind0 = str(g.get("kind", "sym")).strip().lower()
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
    # only the sym (differential symmetric) topology remains
    return False, "未知/已删除拓扑 kind=%s（只剩 sym 对称差分电感）" % kind0

SYM_VIA_CUT = float(DEFAULT_TECH["drc"]["via"]["cut"])

SYM_VIA_SPACE = float(DEFAULT_TECH["drc"]["via"]["spacing"])

SYM_VIA_ENCL = float(DEFAULT_TECH["drc"]["via"]["enclosure"])

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
        # Inner miter of the end flare, then close the band at y0 +/- h: the
        # end has to be the *equal-width* band, i.e. exactly lri_sweep() of
        # this polyline.  The old tail y0 + sg*(h + back + |x1-x0|) added
        # |x1-x0| - back too much, so every underpass painted an extra wedge
        # (~55 um2 at w=8/s=3) that spilled TM1 under the next ring's TM2.
        # The miter sign follows (x1-x0) so the band end never depends on it.
        (x1 - (back if x1 >= x0 else -back), y0 + sg * h),
        (x0, y0 + sg * h),
    ]

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
        # both abscissae UNSIGNED -- lri_i3_half_xy mirrors by kside.
        # entry: lead (k == 0) or a crossing (pulled out to xe); exit: the
        # next crossing (xe) or, on the innermost ring, the bridge (xj).
        # half_xy takes (x_top, x_bot); odd rings run bottom -> top, so
        # their entry is the BOTTOM end.  Giving the innermost ring's entry
        # xj left 0.95 um to the crossing diagonal at s = 3.
        x_exit = xj if last else xe
        x_entry = xj if k == 0 else xe
        if k % 2 == 0:
            seg = lri_i3_half_xy(a, b, c, x_entry, x_exit, kside)
        else:
            seg = list(reversed(
                lri_i3_half_xy(a, b, c, x_exit, x_entry, kside)))
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
    if lead_w < w / 2.0 - 1e-9:
        raise ValueError(
            "lead_w=%.3f < w/2=%.3f，引线比半圈金属窄，端口根部会留台阶"
            % (lead_w, w / 2.0))
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
    # The lead starts at the inner edge of the outer ring band (b0 - w/2), not
    # at its centreline, so the lead wall and the ring wall form one straight
    # line.  A centreline start leaves a w/2 x (x_port - xj) notch on each side
    # of the port stub (32 um^2 total at the factory default).
    lead_a = [(x_port, b0 - w / 2.0), (x_port, y_port)]
    lead_b = [(-x_port, b0 - w / 2.0), (-x_port, y_port)]
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

def lri_build_ind(g):
    """Build the sym (differential symmetric) inductor geometry.

    ``ind`` and ``sym`` both mean the two-arm octagonal symmetric coil;
    any other kind raises (lri_validate already rejects it, but a direct
    call should not silently coerce).
    """
    kind = str(g.get("kind", "sym")).strip().lower() or "sym"
    if kind not in ("sym", "ind"):
        raise ValueError("未知/已删除拓扑 kind=%s（只剩 sym 对称差分电感）" % kind)
    w = float(g["w"])
    s = float(g["s"])
    n = float(g.get("n", 3.0))
    W = float(g.get("W", g.get("dout", 160.0)))
    dout = float(g.get("dout", W))
    rx = float(g.get("rx", dout / 2.0))
    ry = float(g.get("ry", float(g.get("H", W)) / 2.0))
    m = lri_i3_build(
        rx, ry, n, w, s,
        chamfer=float(g.get("chamfer", 2.0 - math.sqrt(2.0))),
        lead_len=float(g.get("lead_len", 20.0)),
        lead_gap=g.get("lead_gap"), lead_w=g.get("lead_w"),
        ct=int(float(g.get("ct", 0) or 0)))
    out = {"metal": list(m["tops"]), "under": list(m["under"]),
           "under_poly": [lri_sym_under_poly(p, w) for p in m["under"]],
           "vias": list(m["vias"]), "ports": list(m["ports"]),
           "topcaps": [(False, False)] * len(m["tops"]),
           "bx": 2.0 * rx, "by": 2.0 * ry, "N": m["N"], "bbox": m["bbox"],
           "kind": "sym"}
    return out

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

PRESETS = [
    # One entry per panel of the six-parameter preview figure, so the GUI can
    # reproduce exactly what was reviewed.  Every n is an integer (the kernel
    # rejects 2.5 turns) and rx/ry are explicit instead of being derived from a
    # square dout.
    {"name": "A 出厂默认 (n3 w8 s3 rx80 ry60)", "type": "ind",
     "freq": "1e9:30e9:30",
     "geom": {"kind": "sym", "n": 3, "w": 8, "s": 3, "rx": 80, "ry": 60,
              "ct": 0}},
    {"name": "B 宽金属 (n2 w12 s4 rx80 ry60)", "type": "ind",
     "freq": "1e9:30e9:30",
     "geom": {"kind": "sym", "n": 2, "w": 12, "s": 4, "rx": 80, "ry": 60,
              "ct": 0}},
    {"name": "C 多圈 (n5 w5 s2.5 rx90 ry70)", "type": "ind",
     "freq": "1e9:30e9:30",
     "geom": {"kind": "sym", "n": 5, "w": 5, "s": 2.5, "rx": 90, "ry": 70,
              "ct": 0}},
    {"name": "D 中心抽头 (n4 w8 s3 rx85 ry65)", "type": "ind",
     "freq": "1e9:30e9:21",
     "geom": {"kind": "sym", "n": 4, "w": 8, "s": 3, "rx": 85, "ry": 65,
              "ct": 1}},
    {"name": "E 扁宽 (n3 w6 s2.5 rx110 ry45)", "type": "ind",
     "freq": "1e9:30e9:21",
     "geom": {"kind": "sym", "n": 3, "w": 6, "s": 2.5, "rx": 110, "ry": 45,
              "ct": 0}},
    {"name": "F 方形 (n3 w10 s3.5 rx70 ry70)", "type": "ind",
     "freq": "1e9:30e9:21",
     "geom": {"kind": "sym", "n": 3, "w": 10, "s": 3.5, "rx": 70, "ry": 70,
              "ct": 0}},
]

GEOM_FORM = { "ind": [("kind", u"拓扑 kind (sym)", "sym", False),
            ("n", u"每臂匝数 n (整数)", "3", True),
            ("w", u"线宽 w (µm)", "8", True),
            ("s", u"间距 s (µm)", "3", True),
            ("rx", u"横向半径 rx (µm)", "80", True),
            ("ry", u"纵向半径 ry (µm)", "60", True),
            ("ct", u"中心抽头 ct (0/1)", "0", True),
            ("dout", u"外径 dout (µm, 留空则用 rx/ry)", "", True),
            ("chamfer", u"拐角切角 chamfer 0..1 (留空=2-sqrt2)", "", True),
            ("lead_len", u"差分引线长 lead_len (µm)", "20", True),
            ("lead_gap", u"差分线间距 lead_gap (µm, 留空=s)", "", True),
            ("lead_w", u"差分线宽 lead_w (µm, 留空=w)", "", True),
            ("stack", u"叠层 stack (0/1)", "0", True),
            ("metal_main", u"主金属 (留空=工艺档)", "", False),
            ("metal_aux", u"副金属/跨线层 (留空=工艺档)", "", False),
            ("ind_mark", u"MARKER 标记层 (0/1)", "0", True)], }

GEOM_NUM = set()

for _rows in GEOM_FORM.values():
    for _k, _lab, _d, _num in _rows:
        if _num:
            GEOM_NUM.add(_k)

GEOM_CHOICES = {
    "kind": ("sym",),
}

def form_to_geom(typ, raw):
    if typ != "ind":
        raise LibrfError("未知器件类型 %s（只剩 ind 对称差分电感）" % typ)
    geom = {}
    for key, _lab, _d, num in GEOM_FORM["ind"]:
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
    return lri_norm_geom(geom)
