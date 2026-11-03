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
            # entry = where this ring is entered (lead, or a crossing ->
            # pulled out to xe); exit = the crossing to the next ring (xe) or,
            # on the innermost ring, the bridge (xj).  Even rings run
            # top -> bottom, odd rings bottom -> top.  The innermost ring's
            # ENTRY sits next to a crossing and must stay at xe; giving it xj
            # leaves only (p-w)/sqrt(2)-ish clearance to the diagonal.
            x_exit = (xj if last else xe)
            if term == 180 and k == 0:
                half = quarter_from_mid(a, b, c, x_exit, side)
            else:
                x_entry = (xport if term == 0 else xj) if k == 0 else xe
                half = (half_top_to_bot(a, b, c, x_entry, x_exit, side)
                        if k % 2 == 0 else
                        half_bot_to_top(a, b, c, x_entry, x_exit, side))
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
