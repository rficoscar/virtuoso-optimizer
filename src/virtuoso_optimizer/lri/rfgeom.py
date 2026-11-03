# -*- coding: utf-8 -*-
"""rfgeom — regular RFIC passive geometry kernel.

Layout law (all four devices):
  * every turn is a concentric octagon: 4 straight edges + 45 deg chamfers,
    inward offset by exactly one pitch per turn;
  * turn changes are symmetric 45 deg X crossings on the coil axis
    (bottom for even k, top for odd k), one branch on the main metal and the
    other on the under layer, joined by via blocks at both ring ends;
  * leads, taps and ports are straight, axis aligned, and flush with the coil.

This module is the reference implementation; the SKILL kernel in
``data/librf.il`` follows it line by line.  Roles are logical layer names
("main" / "aux" / "under" / "ct" / "gr"); the caller maps them onto the PDK.
"""
from __future__ import print_function

import math

SQ2 = math.sqrt(2.0)


class GeomError(ValueError):
    """Parameter set that cannot be laid out regularly."""


class Geo(object):
    """Shape accumulator: paths, rects, via blocks, pins — all in um."""

    __slots__ = ("paths", "rects", "viapads", "pins")

    def __init__(self):
        self.paths = []     # (pts, width, role)
        self.rects = []     # (x1, y1, x2, y2, role)
        self.viapads = []   # (x1, y1, x2, y2, chain)  chain = aux -> ... -> main
        self.pins = []      # (name, x, y, direction, role)

    def path(self, pts, width, role):
        pts = dedup(pts)
        if len(pts) >= 2:
            self.paths.append((pts, float(width), role))

    def rect(self, x1, y1, x2, y2, role):
        self.rects.append((min(x1, x2), min(y1, y2),
                           max(x1, x2), max(y1, y2), role))

    def viapad(self, x1, y1, x2, y2, chain):
        self.viapads.append((min(x1, x2), min(y1, y2),
                             max(x1, x2), max(y1, y2), tuple(chain)))

    def pin(self, name, x, y, direction, role):
        self.pins.append((str(name), float(x), float(y), direction, role))


def dedup(pts):
    out = []
    for p in pts:
        x, y = float(p[0]), float(p[1])
        if not out or abs(x - out[-1][0]) > 1e-9 or abs(y - out[-1][1]) > 1e-9:
            out.append((x, y))
    return out


def mirror_x(pts):
    return [(-x, y) for (x, y) in pts]


def mirror_y(pts):
    return [(x, -y) for (x, y) in pts]


def shift(pts, dx, dy):
    return [(x + dx, y + dy) for (x, y) in pts]


# ------------------------------------------------------------------ ring family
def ring_family(rx, ry, w, s, n_turns, chamfer):
    """Concentric inward-offset octagons -> (rings, pitch, N), ring=(a,b,c)."""
    if rx <= 0.0 or ry <= 0.0 or w <= 0.0 or s <= 0.0:
        raise GeomError("rx/ry/w/s 必须 > 0")
    if abs(float(n_turns) - round(float(n_turns))) > 1e-6:
        raise GeomError("n=%.3f 必须为整数（每臂圈数）" % float(n_turns))
    pitch = w + s
    a0, b0 = rx - w / 2.0, ry - w / 2.0
    if min(a0, b0) <= 0.0:
        raise GeomError("rx/ry 太小（需 > w=%.3f）" % w)
    c0 = max(0.0, min(float(chamfer), 1.0)) * min(a0, b0)
    n = max(1, int(round(float(n_turns))))
    rings = []
    for k in range(n):
        a, b = a0 - k * pitch, b0 - k * pitch
        if min(a, b) <= w / 2.0 + 1e-9:
            raise GeomError(
                "第 %d 圈放不下 (a=%.2f b=%.2f)；请增大 rx/ry 或减小 n/w/s" % (k, a, b))
        c = max(0.0, c0 - k * pitch * (2.0 - SQ2))
        if c > min(a, b):
            c = min(a, b)
        rings.append((a, b, c))
    return rings, pitch, n


def cross_gap(w, s, pitch):
    """Outward pull-back of a ring end next to a 45 deg crossing.

    The over branch's diagonal passes the under branch's ring-end corner at
    (p-w/2)/sqrt(2) from its centre line; pulling the ring end out by e makes
    the edge-to-edge gap equal to the coil spacing s.
    """
    return max(0.0, SQ2 * (s + w / 2.0) - pitch + w / 2.0)


# ------------------------------------------------------------------ half turns
def half_top_to_bot(a, b, c, x_top, x_bot, side):
    """Half ring, top -> bottom. side=+1 right half, -1 left half."""
    pts = [(x_top, b), (a - c, b), (a, b - c), (a, c - b), (a - c, -b), (x_bot, -b)]
    if side < 0:
        pts = [(-x, y) for (x, y) in pts]
    return dedup(pts)


def half_bot_to_top(a, b, c, x_bot, x_top, side):
    """Half ring, bottom -> top."""
    pts = [(x_bot, -b), (a - c, -b), (a, c - b), (a, b - c), (a - c, b), (x_top, b)]
    if side < 0:
        pts = [(-x, y) for (x, y) in pts]
    return dedup(pts)


def quarter_from_mid(a, b, c, x_bot, side):
    """Quarter turn from mid height to the bottom (terminal 180 outer ring)."""
    pts = [(a, 0.0), (a, c - b), (a - c, -b), (x_bot, -b)]
    if side < 0:
        pts = [(-x, y) for (x, y) in pts]
    return dedup(pts)


def guard_octagon(rx, ry, chamfer, w, gr_space, gr_w, slit):
    """Open octagon on the guard-ring layer; slit breaks the eddy loop."""
    d = gr_space + gr_w / 2.0
    ag, bg = rx + d, ry + d
    cg = max(0.0, min(chamfer, 1.0)) * min(rx - w / 2.0, ry - w / 2.0)
    cg = cg + d * (2.0 - SQ2)
    cg = min(cg, min(ag, bg))
    h = slit / 2.0
    return dedup([
        (h, bg), (ag - cg, bg), (ag, bg - cg), (ag, cg - bg), (ag - cg, -bg),
        (cg - ag, -bg), (-ag, cg - bg), (-ag, bg - cg), (cg - ag, bg), (-h, bg)])


# ------------------------------------------------------------------ symmetric coil
def build_sym(rx, ry, n, w, s, chamfer=None, lead_len=20.0, lead_gap=None,
              lead_w=None, ct=0, ct_len=20.0, term=0, gr=0, gr_space=10.0,
              gr_w=4.0, via_len=None):
    """Symmetric (differential) octagonal inductor with axis X crossings.

    term=0   : both leads straight up at the top, outer ring complete
    term=180 : leads horizontal at mid height, outer ring only below y=0

    Returns (Geo, info).
    """
    if chamfer is None:
        chamfer = 2.0 - SQ2
    lead_gap = s if lead_gap is None else float(lead_gap)
    lead_w = w if lead_w is None else float(lead_w)
    term = 180 if int(term) == 180 else 0
    rings, pitch, nring = ring_family(rx, ry, w, s, n, chamfer)
    a0, b0, c0 = rings[0]
    if lead_gap < s - 1e-9:
        raise GeomError("lead_gap=%.3f < s=%.3f" % (lead_gap, s))
    if lead_w < w - 1e-9:
        raise GeomError("lead_w=%.3f < w=%.3f" % (lead_w, w))
    xj = pitch / 2.0
    e = cross_gap(w, s, pitch)
    xe = xj + e
    xport = (lead_gap + lead_w) / 2.0
    if term == 0:
        if lead_len < w / 2.0 - 1e-9:
            raise GeomError("lead_len < w/2，端口会落在外圈金属内")
        if xport + lead_w / 2.0 > a0 - c0 + 1e-9:
            raise GeomError(
                "lead_gap/lead_w 把引线放到外圈直边外 (x=%.3f > 直边 %.3f)"
                % (xport + lead_w / 2.0, a0 - c0))
    elif lead_len < w / 2.0 - 1e-9:
        raise GeomError("term_len < w/2，端口会落在外圈金属内")
    lmax = min(a0 - c0, rings[-1][0] - rings[-1][2]) - xe
    vlen = min(w, lmax) if via_len is None else float(via_len)
    if vlen < 2.0:
        raise GeomError(
            "45 度跨线放不下通孔区 (可用 %.3f)；请增大 rx/ry 或减小 w/s/n" % lmax)

    g = Geo()

    def via_block(x, y, sign):
        x1, x2 = sorted((x, x + sign * vlen))
        g.viapad(x1, y - w / 2.0, x2, y + w / 2.0, ("aux", "main"))

    for arm in (1, -1):
        under_here = ((lambda k: k % 2 == 1) if arm > 0
                      else (lambda k: k % 2 == 0))
        runs, run = [], []
        for k in range(nring):
            a, b, c = rings[k]
            side = arm * (1 if k % 2 == 0 else -1)
            last = (k == nring - 1)
            # half_* / quarter_from_mid take UNSIGNED x and mirror by `side`
            # themselves.  Passing a pre-signed `side * x` here negates the
            # endpoints twice and drags each arm's ring ends onto the opposite
            # side, laying a spurious bar across the lead row (shorts p01/p02)
            # and another across the first crossing (shorts the two coils).
            x_end = (xj if last else xe)
            if term == 180 and k == 0:
                half = quarter_from_mid(a, b, c, x_end, side)
            else:
                # The free end of the innermost ring must land on xj to meet
                # the bridge; only ring changes use the pulled-out xe.
                x_lead = (xport if (k == 0 and term == 0)
                          else (xj if last else xe))
                half = (half_top_to_bot(a, b, c, x_lead, x_end, side)
                        if k % 2 == 0 else
                        half_bot_to_top(a, b, c, x_end, x_lead, side))
            run = run + half[1:] if run else list(half)
            if last:
                break
            y_end = -b if k % 2 == 0 else b
            y_next = -(b - pitch) if k % 2 == 0 else (b - pitch)
            jog = dedup([(side * xe, y_end), (side * xj, y_end),
                         (-side * xj, y_next), (-side * xe, y_next)])
            if under_here(k):
                runs.append(run)
                run = []
                g.path([(side * (xe + vlen), y_end)] + jog[1:]
                       + [(-side * (xe + vlen), y_next)], w, "under")
                via_block(side * xe, y_end, side)
                via_block(-side * xe, y_next, -side)
            else:
                run = run + jog[1:]
        runs.append(run)
        for r in runs:
            g.path(r, w, "main")
        if term == 0:
            x0 = arm * xport
            g.path([(x0, b0 + lead_len), (x0, b0 - w / 2.0)], lead_w, "main")
            g.pin("p01" if arm > 0 else "p02", x0, b0 + lead_len, "N", "main")
        else:
            x0 = arm * (a0 + lead_len)
            g.path([(x0, 0.0), (arm * (a0 - w / 2.0), 0.0)], lead_w, "main")
            g.pin("p01" if arm > 0 else "p02", x0, 0.0,
                  "E" if arm > 0 else "W", "main")

    # innermost bridge = electrical midpoint
    aN, bN, _c = rings[-1]
    y_bridge = -bN if (nring - 1) % 2 == 0 else bN
    g.path([(-xj, y_bridge), (xj, y_bridge)], w, "main")
    if ct:
        sgn = 1.0 if y_bridge > 0 else -1.0
        y_tip = sgn * (ry + float(ct_len))
        g.viapad(-w / 2.0, min(y_bridge, y_bridge + sgn * w),
                 w / 2.0, max(y_bridge, y_bridge + sgn * w), ("ct", "main"))
        g.path([(0.0, y_bridge), (0.0, y_tip)], w, "ct")
        g.pin("ct1", 0.0, y_tip, "N" if sgn > 0 else "S", "ct")
    if gr:
        slit = max(gr_w, 2.0 * s)
        g.path(guard_octagon(rx, ry, chamfer, w, gr_space, gr_w, slit),
               gr_w, "gr")
        g.pin("gnd", 0.0, -(ry + gr_space + gr_w / 2.0), "S", "gr")
    return g, {"rings": rings, "pitch": pitch, "N": nring, "xj": xj,
               "xe": xe, "vlen": vlen, "e": e, "y_bridge": y_bridge}


def point_at(pts, frac):
    """Point at arc-length fraction ``frac`` of a polyline."""
    seg = []
    total = 0.0
    for i in range(len(pts) - 1):
        d = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        seg.append(d)
        total += d
    want = max(0.0, min(1.0, float(frac))) * total
    acc = 0.0
    for i, d in enumerate(seg):
        if acc + d >= want - 1e-12 or i == len(seg) - 1:
            t = 0.0 if d < 1e-12 else (want - acc) / d
            t = max(0.0, min(1.0, t))
            return (pts[i][0] + t * (pts[i + 1][0] - pts[i][0]),
                    pts[i][1] + t * (pts[i + 1][1] - pts[i][1]))
        acc += d
    return pts[-1]


# ------------------------------------------------------------------ single ended
def build_se(rx, ry, n, w, s, chamfer=None, lead_len=20.0, ct=0, ct_len=20.0):
    """Single-ended octagonal inductor: one inward spiral on one layer.

    The winding follows the rfgeom law -- concentric octagons one pitch
    apart, turn changes as symmetric 45 deg X crossings on the coil axis --
    and, being a single winding, never crosses itself, so it needs no
    underpass at all.  Leads are straight and axis aligned: p01 goes up from
    the outer end, p02 leaves the inner end along that end's own y sign (up
    when the last half-ring finishes on the top, down when it finishes on
    the bottom), so the two never collide whatever the turn count.

    ``ct`` adds a centre tap: a via pad at the winding's mid-length point
    plus a straight radial stub on the "ct" layer out past the coil edge.
    The stub is on its own layer, so it may cross the winding it taps.
    ``n`` counts FULL turns, as the legacy ``lriSpiral`` did -- a single
    winding covers the whole coil, so it needs two half-rings per turn and
    the ring family is built with ``2*n`` of them.  Half turns are legal,
    quarter turns are not.
    """
    if chamfer is None:
        chamfer = 2.0 - SQ2
    if abs(2.0 * float(n) - round(2.0 * float(n))) > 1e-6:
        raise GeomError("n=%.3f 必须是 0.5 的整数倍（半圈）" % float(n))
    n_half = int(round(2.0 * float(n)))
    rings, pitch, nring = ring_family(rx, ry, w, s, (n_half + 1) // 2,
                                      chamfer)
    a0, b0, _c0 = rings[0]
    xj = pitch / 2.0
    pts, inf = spiral_run(rings, pitch, xj, w, s, chamfer, arm=+1,
                          n_half=n_half)
    g = Geo()
    g.path(pts, w, "main")
    p_out, p_in = pts[0], pts[-1]
    g.path([p_out, (p_out[0], p_out[1] + lead_len)], w, "main")
    g.pin("p01", p_out[0], p_out[1] + lead_len, "N", "main")
    # The inner end is buried inside the turns, so its lead escapes on the
    # under layer -- the radial underpass of the reference layout -- and the
    # port sits flush with the coil edge.  A via pad at the winding's inner
    # end joins the two layers.
    sgn = 1.0 if p_in[1] >= 0.0 else -1.0
    y_tip = sgn * (ry + float(lead_len))
    g.viapad(p_in[0] - w / 2.0, min(p_in[1], p_in[1] + sgn * w),
             p_in[0] + w / 2.0, max(p_in[1], p_in[1] + sgn * w),
             ("under", "main"))
    g.path([p_in, (p_in[0], y_tip)], w, "under")
    g.pin("p02", p_in[0], y_tip, "N" if sgn > 0.0 else "S", "under")
    if ct:
        mid = point_at(pts, 0.5)
        sgc = 1.0 if mid[1] >= 0.0 else -1.0
        y_tip = sgc * (ry + float(ct_len))
        g.viapad(mid[0] - w / 2.0, min(mid[1], mid[1] + sgc * w),
                 mid[0] + w / 2.0, max(mid[1], mid[1] + sgc * w),
                 ("ct", "main"))
        g.path([mid, (mid[0], y_tip)], w, "ct")
        g.pin("ct1", mid[0], y_tip, "N" if sgc > 0.0 else "S", "ct")
    return g, {"rings": rings, "pitch": pitch, "N": nring, "xj": xj,
               "e": inf["e"], "p_out": p_out, "p_in": p_in}



# --------------------------------------------------------------- centre tap
def build_diff(rx, ry, n, w, s, chamfer=None, lead_len=20.0, ct=0,
               ct_len=20.0):
    """Centre-tapped inductor: two concentric counter-wound spirals.

    The outer coil spirals inward on the main metal; the inner coil sits a
    whole ring family inside it and is y-mirrored, so the two are wound in
    opposite senses.  Because the two occupy disjoint radial ranges,
    neither one's turn changes cross the other -- unlike the legacy
    interleaved layout, no turn change needs an underpass at all.  The
    coils are in series: the two INNER ends meet on the under metal (the
    bridge has to cross the outer coil's rings on its way out), and the
    two OUTER ends are the ports.

    Ports: p01 at the outer coil's outer end (lead up), p02 at the inner
    coil's outer end (lead down, the y-mirror image of p01), ct1 at the
    series junction when ``ct`` is set.  ``n`` counts the turns of EACH
    coil (full turns; half turns legal).
    """
    if chamfer is None:
        chamfer = 2.0 - SQ2
    if abs(2.0 * float(n) - round(2.0 * float(n))) > 1e-6:
        raise GeomError("n=%.3f 必须是 0.5 的整数倍（半圈）" % float(n))
    nh = int(round(2.0 * float(n)))
    nt = (nh + 1) // 2
    rings_a, pitch, N = ring_family(rx, ry, w, s, nt, chamfer)
    a0, b0, _c0 = rings_a[0]
    rings_b, _pb, _Nb = ring_family(rx - N * pitch, ry - N * pitch,
                                    w, s, nt, chamfer)
    xj = pitch / 2.0
    pts_a, _ia = spiral_run(rings_a, pitch, xj, w, s, chamfer, arm=+1,
                            n_half=nh)
    pts_b = mirror_y(spiral_run(rings_b, pitch, xj, w, s, chamfer, arm=+1,
                                n_half=nh)[0])
    g = Geo()
    g.path(pts_a, w, "main")
    g.path(pts_b, w, "main")
    a_in, b_in = pts_a[-1], pts_b[-1]
    g.path(dedup([a_in, (b_in[0], a_in[1]), b_in]), w, "under")
    for p in (a_in, b_in):
        g.viapad(p[0] - w / 2.0, p[1] - w / 2.0,
                 p[0] + w / 2.0, p[1] + w / 2.0, ("under", "main"))
    a_out, b_out = pts_a[0], pts_b[0]
    g.path([a_out, (a_out[0], a_out[1] + lead_len)], w, "main")
    g.pin("p01", a_out[0], a_out[1] + lead_len, "N", "main")
    g.path([b_out, (b_out[0], b_out[1] - lead_len)], w, "main")
    g.pin("p02", b_out[0], b_out[1] - lead_len, "S", "main")
    if ct:
        jx, jy = (b_in[0], a_in[1])
        sgn = 1.0 if jy >= 0.0 else -1.0
        y_tip = sgn * (ry + float(ct_len))
        g.viapad(jx - w / 2.0, min(jy, jy + sgn * w),
                 jx + w / 2.0, max(jy, jy + sgn * w), ("ct", "under"))
        g.path([(jx, jy), (jx, y_tip)], w, "ct")
        g.pin("ct1", jx, y_tip, "N" if sgn > 0.0 else "S", "ct")
    return g, {"rings": (rings_a, rings_b), "pitch": pitch, "N": N,
               "xj": xj, "p_out": (a_out, b_out), "p_in": (a_in, b_in)}


# ------------------------------------------------------------------ figure 8
def build_f8(rx, ry, n, w, s, gap=None, chamfer=None, lead_len=20.0):
    """Figure-8 inductor: two counter-wound lobes in series.

    Each lobe is one inward spiral following the rfgeom law.  The right
    lobe is the x-mirror of the left one, so the two are wound in opposite
    senses and their fluxes add -- the point of a figure-8 -- and the
    finished device is x-mirror symmetric.  The inner ends meet at the coil
    centre through an underpass; the bridge has to leave lobe A across its
    own outer ring, so it lives on its own layer and taps back with a via
    block at each end.

    ``rx``/``ry`` are PER-LOBE half extents, ``gap`` the spacing between the
    two lobes, ``n`` the turns of each lobe (full turns; half turns legal).
    """
    if chamfer is None:
        chamfer = 2.0 - SQ2
    gap = s if gap is None else float(gap)
    if gap < s - 1e-9:
        raise GeomError("gap=%.3f < s=%.3f" % (gap, s))
    if abs(2.0 * float(n) - round(2.0 * float(n))) > 1e-6:
        raise GeomError("n=%.3f 必须是 0.5 的整数倍（半圈）" % float(n))
    n_half = int(round(2.0 * float(n)))
    rings, pitch, nring = ring_family(rx, ry, w, s, (n_half + 1) // 2,
                                      chamfer)
    a0, b0, _c0 = rings[0]
    xj = pitch / 2.0
    dx = rx + gap / 2.0
    # arm=-1 puts lobe A's free end on the far left; its mirror lands on the
    # far right, so both ports sit on the outside of the device
    run_a, _inf = spiral_run(rings, pitch, xj, w, s, chamfer, arm=-1,
                             n_half=n_half)
    pts_a = shift(run_a, -dx, 0.0)
    pts_b = mirror_x(pts_a)
    g = Geo()
    g.path(pts_a, w, "main")
    g.path(pts_b, w, "main")
    a_in, b_in = pts_a[-1], pts_b[-1]
    bridge = dedup([a_in, (a_in[0], 0.0), (0.0, 0.0), (b_in[0], 0.0), b_in])
    g.path(bridge, w, "under")
    for p in (a_in, b_in):
        g.viapad(p[0] - w / 2.0, p[1] - w / 2.0,
                 p[0] + w / 2.0, p[1] + w / 2.0, ("under", "main"))
    for pts, name in ((pts_a, "p01"), (pts_b, "p02")):
        p0 = pts[0]
        g.path([p0, (p0[0], p0[1] + lead_len)], w, "main")
        g.pin(name, p0[0], p0[1] + lead_len, "N", "main")
    return g, {"rings": rings, "pitch": pitch, "N": nring, "xj": xj,
               "dx": dx, "p_out": (pts_a[0], pts_b[0]),
               "p_in": (a_in, b_in)}


# ------------------------------------------------------------------ single winding
def spiral_run(rings, pitch, x_lead, w, s, chamfer, term=0, arm=+1,
               n_half=None):
    """One inward spiral on a SINGLE layer: each turn is a full concentric
    octagon drawn as two half-rings.

    ``rings`` holds one entry per FULL turn and is inset by exactly one pitch
    per turn, so both halves of a turn share the same (a, b, c).  Within a
    turn the two halves sit on opposite sides of the coil axis and are joined
    by a jog across that axis; between turns the jog is a 45 deg diagonal
    across the axis stepping inward one pitch -- the turn-change crossing.
    ``arm`` picks the starting side; the -1 case is the 180 deg rotation of
    the +1 case, which is how the transformer secondary sits opposite its
    primary.

    ``n_half`` is how many half-rings to draw (default 2 per ring) -- an odd
    value gives a legal half turn.

    Returns (pts, info).  ``pts`` is one polyline.
    """
    n_half = (2 * len(rings)) if n_half is None else int(n_half)
    a0, b0, c0 = rings[0]
    xj = pitch / 2.0
    e = cross_gap(w, s, pitch)
    xe = xj + e
    lmax = min(a0 - c0, rings[-1][0] - rings[-1][2]) - xe
    if lmax < 0.0:
        raise GeomError(
            "45 度跨线放不下 (可用 %.3f)；请增大 rx/ry 或减小 w/s/n" % lmax)
    pts = []
    for k in range(n_half):
        # both halves of a turn live on the same ring -> one pitch per turn
        a, b, c = rings[k // 2]
        side = arm * (1 if k % 2 == 0 else -1)
        last = (k == n_half - 1)
        x_end = (xj if last else xe)
        xl = (x_lead if k == 0 else (xj if last else xe))
        half = (half_top_to_bot(a, b, c, xl, x_end, side) if k % 2 == 0
                else half_bot_to_top(a, b, c, x_end, xl, side))
        pts = pts + half[1:] if pts else list(half)
        if last:
            break
        y_end = -b if k % 2 == 0 else b
        _a2, b2, _c2 = rings[min((k + 1) // 2, len(rings) - 1)]
        y_next = b2 if (k + 1) % 2 == 0 else -b2
        pts = pts + dedup([(side * xe, y_end), (side * xj, y_end),
                           (-side * xj, y_next), (-side * xe, y_next)])[1:]
    return pts, {"xj": xj, "xe": xe, "e": e, "N": n_half}


def build_xfmr(rx, ry, np_turns, ns_turns, w, s, gap=None, chamfer=None,
               ct=0, shrink=1):
    """Interleaved / stacked transformer: two concentric octagonal spirals.

    Primary on the main metal, secondary on the under layer rotated 180 deg
    (``arm=-1``), so the two windings pass over each other without any extra
    underpass -- the layer change does the work.  Each winding follows the
    rfgeom law: concentric octagons one pitch apart, turn changes as
    symmetric 45 deg X crossings on the coil axis.

    ``shrink`` is the interleaving knob from the legacy kernel: the secondary
    outer radius is pulled in by ``shrink`` pitches so the two spirals
    interleave instead of stacking exactly; ``shrink=0`` degenerates to a
    plain stacked transformer (a warning in the old code).

    Ports: p01/p02 primary outer/inner, p03/p04 secondary outer/inner, plus
    ct1 on the secondary winding midpoint when ``ct=1``.
    """
    if chamfer is None:
        chamfer = 2.0 - SQ2
    gap = s if gap is None else float(gap)
    if gap < s - 1e-9:
        raise GeomError("gap=%.3f < s=%.3f" % (gap, s))
    # np/ns count FULL turns of each winding (same convention as build_se):
    # one winding covers the whole coil, so two half-rings per turn
    for _t in (np_turns, ns_turns):
        if abs(2.0 * float(_t) - round(2.0 * float(_t))) > 1e-6:
            raise GeomError("np/ns=%.3f 必须是 0.5 的整数倍（半圈）" % float(_t))
    nh_p = int(round(2.0 * float(np_turns)))
    nh_s = int(round(2.0 * float(ns_turns)))
    rings_p, pitch, nP = ring_family(rx, ry, w, s, (nh_p + 1) // 2, chamfer)
    # interleaving: pull the secondary in by `shrink` pitches of the primary
    rs = max(0, int(round(float(shrink))))
    rings_s, pitch_s, nS = ring_family(rx - rs * pitch, ry - rs * pitch,
                                       w, s, (nh_s + 1) // 2, chamfer)
    if abs(pitch_s - pitch) > 1e-9:
        raise GeomError("primary/secondary pitch mismatch")

    xj = pitch / 2.0
    g = Geo()
    a0, b0, _c0 = rings_p[0]
    as0, bs0, _cs0 = rings_s[0]

    p_pts, p_inf = spiral_run(rings_p, pitch, xj, w, s, chamfer, arm=+1,
                              n_half=nh_p)
    s_pts, s_inf = spiral_run(rings_s, pitch, xj, w, s, chamfer, arm=-1,
                              n_half=nh_s)
    g.path(p_pts, w, "main")
    g.path(s_pts, w, "aux")

    # outer leads: straight, axis aligned, flush with the coil
    p_out, s_out = p_pts[0], s_pts[0]
    g.path([(p_out[0], p_out[1]), (p_out[0], p_out[1] + 20.0)], w, "main")
    g.path([(s_out[0], s_out[1]), (s_out[0], s_out[1] + 20.0)], w, "aux")
    g.pin("p01", p_out[0], p_out[1] + 20.0, "N", "main")
    g.pin("p03", s_out[0], s_out[1] + 20.0, "N", "aux")
    # inner ends terminate on the coil axis
    p_in, s_in = p_pts[-1], s_pts[-1]
    g.pin("p02", p_in[0], p_in[1], "S", "main")
    g.pin("p04", s_in[0], s_in[1], "S", "aux")
    if ct:
        y_tip = s_in[1] + (20.0 if s_in[1] >= 0.0 else -20.0)
        # start at s_in itself, not on the axis -- the axis point alone would
        # leave the stub detached from the winding by |s_in.x|
        g.path([s_in, (0.0, s_in[1]), (0.0, y_tip)], w, "ct")
        g.pin("ct1", 0.0, y_tip, "N" if y_tip > 0 else "S", "ct")
    return g, {"rings_p": rings_p, "rings_s": rings_s, "pitch": pitch,
               "nP": nP, "nS": nS, "xj": xj,
               "p": p_inf, "s": s_inf}
