#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_geometry.py - Virtuoso 版图 PCell 几何不变量检查器（零依赖）

它检查"几何对不对"，不检查"代码跑没跑通"。能挡住历史上真实出现过的
版图错误：underpass 端头多画楔形、段角被写成非 45、via 落在金属外面、
via cut 数落成偶数、两臂金属粘连、端口不在引线端点上。

数据模型（与 virtuoso_optimizer.lri.layout.lri_build_ind 的返回一致）：
  metal      list[list[(x,y)]]   TM2/TM1 主金属的【中心线折线】，
                                 未扫掠；首尾不闭合；一段一条
  under      list[list[(x,y)]]   underpass（下层 TM1）中心线折线
  under_poly list[list[(x,y)]]   underpass 已扫掠多边形（可选，缺省可由
                                 under + w 推出）
  vias       list[(x, y, span)]  via 阵列中心与边长
  ports      list[(name,(x,y))]  端口名与坐标
  bbox       (x0, y0, x1, y1)    期望边界
  w, s, n, ct                    设计参数（µm 浮点）

用法
  python check_geometry.py geo.json           # 人读
  python check_geometry.py geo.json --json    # 机器读
  python check_geometry.py --selftest         # 检查器自检（含 5 个反例）

退出码：0 通过（可能带 WARN）；1 有 FAIL；2 输入有问题。
角度只允许 45 的整数倍；所有长度单位 µm。
"""
import json
import math
import sys

EPS = 1e-6
ANG_TOL = 1e-4      # 45 度整数倍的容差（度）
AREA_ABS_TOL = 2.0  # um2
AREA_REL_TOL = 0.01
PARALLEL_DEG = 20.0  # 两条中心线夹角小于它才算"平行走线"，才可能粘连


# ------------------------------------------------------------------ 基础几何
def segs_of(pts):
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def cross3(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def point_seg(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    if d2 < 1e-18:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / d2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def parallel_run(a, b, c, d):
    """返回 (夹角°, 沿走向的重叠段长度)。夹角取 0..90°，重叠段用于区分
    「平行走线贴太近」（真短路）与「T 接 / 拐角 / 交叉」（同网络合法）。"""
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = d[0] - c[0], d[1] - c[1]
    lu = math.hypot(ux, uy) or 1.0
    lv = math.hypot(vx, vy) or 1.0
    ux, uy = ux / lu, uy / lu
    vx, vy = vx / lv, vy / lv
    dot = abs(ux * vx + uy * vy)
    ang = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    p0 = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
    p1 = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
    lo = max(0.0, min(p0, p1))
    hi = min(lu, max(p0, p1))
    return ang, max(0.0, hi - lo)


def seg_seg(a, b, c, d):
    """两线段最近距离（相交为 0）。"""
    d1 = cross3(c, d, a)
    d2 = cross3(c, d, b)
    d3 = cross3(a, b, c)
    d4 = cross3(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    if abs(d1) <= EPS and point_seg(a, c, d) <= EPS:
        return 0.0
    if abs(d2) <= EPS and point_seg(b, c, d) <= EPS:
        return 0.0
    if abs(d3) <= EPS and point_seg(c, a, b) <= EPS:
        return 0.0
    if abs(d4) <= EPS and point_seg(d, a, b) <= EPS:
        return 0.0
    return min(point_seg(a, c, d), point_seg(b, c, d),
               point_seg(c, a, b), point_seg(d, a, b))


def properly_cross(a, b, c, d):
    """严格穿越（不含端点接触），用于自交判定。"""
    d1 = cross3(c, d, a)
    d2 = cross3(c, d, b)
    d3 = cross3(a, b, c)
    d4 = cross3(a, b, d)
    return ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
           ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS))


def area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def point_in_poly(p, poly):
    n = len(poly)
    for i in range(n):
        if point_seg(p, poly[i], poly[(i + 1) % n]) <= EPS:
            return True
    inside = False
    x, y = p
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def _line_hit(p1, p2, q1, q2):
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
    if abs(det) < 1e-12:
        return None
    rx, ry = q1[0] - p1[0], q1[1] - p1[1]
    t = (rx * (-d2[1]) - ry * (-d2[0])) / det
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def sweep(pts, w):
    """中心线 -> 等宽多边形（斜接 miter），与内核 lri_sweep 同算法。"""
    h = w / 2.0
    segs = []
    for a, b in zip(pts[:-1], pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        if ln > 1e-9:
            segs.append((a, b, -dy / ln, dx / ln))
    if not segs:
        return []

    def side(sg):
        out = [(segs[0][0][0] + sg * h * segs[0][2],
                segs[0][0][1] + sg * h * segs[0][3])]
        for i in range(1, len(segs)):
            p1 = (segs[i - 1][0][0] + sg * h * segs[i - 1][2],
                  segs[i - 1][0][1] + sg * h * segs[i - 1][3])
            p2 = (segs[i - 1][1][0] + sg * h * segs[i - 1][2],
                  segs[i - 1][1][1] + sg * h * segs[i - 1][3])
            q1 = (segs[i][0][0] + sg * h * segs[i][2],
                  segs[i][0][1] + sg * h * segs[i][3])
            q2 = (segs[i][1][0] + sg * h * segs[i][2],
                  segs[i][1][1] + sg * h * segs[i][3])
            out.append(_line_hit(p1, p2, q1, q2) or q1)
        la = segs[-1]
        out.append((la[1][0] + sg * h * la[2], la[1][1] + sg * h * la[3]))
        return out

    return side(1) + side(-1)[::-1]


# ------------------------------------------------------------------ 报告
class Report(object):
    def __init__(self):
        self.items = []

    def add(self, level, code, msg):
        self.items.append({"level": level, "code": code, "msg": msg})

    def fail(self, code, msg):
        self.add("FAIL", code, msg)

    def warn(self, code, msg):
        self.add("WARN", code, msg)

    def info(self, code, msg):
        self.add("INFO", code, msg)

    def ok(self, code, msg):
        self.add("PASS", code, msg)

    @property
    def failures(self):
        return [i for i in self.items if i["level"] == "FAIL"]


# ------------------------------------------------------------------ 各检查项
def check_angles(named, rep):
    bad = []
    total = 0
    for tag, pts in named:
        for a, b in zip(pts[:-1], pts[1:]):
            if dist(a, b) < EPS:
                continue
            total += 1
            deg = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            r = deg % 45.0
            dev = min(r, 45.0 - r)
            if dev > ANG_TOL:
                bad.append("%s (%.3f,%.3f)->(%.3f,%.3f) 方向 %.4f°"
                           % (tag, a[0], a[1], b[0], b[1], deg))
    if bad:
        rep.fail("E03", "有 %d 段不在 45° 整数倍上（只列前 3）：%s"
                 % (len(bad), "；".join(bad[:3])))
    else:
        rep.ok("E03", "%d 段全部落在 0/45/90/135° 上" % total)


def check_dup(named, rep):
    bad = []
    for tag, pts in named:
        for i in range(len(pts) - 1):
            if dist(pts[i], pts[i + 1]) < EPS:
                bad.append("%s 点%d(%.3f,%.3f) 与点%d 重合"
                           % (tag, i, pts[i][0], pts[i][1], i + 1))
    if bad:
        rep.warn("E01", "零长度段/重复点（%d 处）：%s。SKILL 里这种段会被吞掉，"
                        "端口可能整体偏移；根因通常是 lead_gap/lead_w 取默认值使 "
                        "x_port == xj" % (len(bad), "；".join(bad[:3])))
    else:
        rep.ok("E01", "无零长度段")


def check_selfcross(named, rep):
    bad = []
    for tag, pts in named:
        n = len(pts)
        for i in range(n - 1):
            for j in range(i + 2, n - 1):
                if properly_cross(pts[i], pts[i + 1], pts[j], pts[j + 1]):
                    bad.append("%s 段%d x 段%d" % (tag, i, j))
    if bad:
        rep.fail("E02", "中心线自交（%d 处）：%s —— 同一层金属自我交叉会短路"
                 % (len(bad), "；".join(bad[:4])))
    else:
        rep.ok("E02", "中心线无自交")


def check_spacing(metal, w, s, rep):
    """不同中心线之间的最小距离：< w 视为粘连，< w+s 视为间距不足。"""
    merged = []
    tight = []
    close = []
    touch = []
    joint = []
    n = len(metal)
    for i in range(n):
        for j in range(i + 1, n):
            best = None
            same = False
            for a, b in segs_of(metal[i]):
                for c, d in segs_of(metal[j]):
                    dd = seg_seg(a, b, c, d)
                    if best is None or dd < best[0]:
                        best = (dd, a, b, c, d)
                    if dd <= EPS:
                        same = True
            if best is None:
                continue
            dd, a, b, c, d = best
            ang, ovl = parallel_run(a, b, c, d)
            # 只有"平行且重叠"的两条线才算间距不足：
            # 垂直/斜交的近距一律是 T 接、拐角、交叉，属同网络合法连接
            if same:
                touch.append("lines[%d]x[%d]" % (i, j))
            elif ang <= PARALLEL_DEG and ovl > w / 2.0 and dd < w - EPS:
                merged.append("lines[%d]x[%d] d=%.3f ovl=%.2f" % (i, j, dd, ovl))
            elif ang <= PARALLEL_DEG and dd < w + 0.5 * s - EPS:
                tight.append("lines[%d]x[%d] d=%.3f (设计 w+s=%.2f)"
                             % (i, j, dd, w + s))
            elif ang <= PARALLEL_DEG and dd < w + s - EPS:
                close.append("lines[%d]x[%d] d=%.3f (设计 %.2f)"
                             % (i, j, dd, w + s))
            elif dd < w / 2.0 - EPS:
                joint.append("lines[%d]x[%d] d=%.3f" % (i, j, dd))
    if merged:
        rep.fail("E05", "平行走线中心距 < w=%.2f 且重叠段长 > w/2，金属会连成一片"
                 "（短路风险）：%s" % (w, "；".join(merged[:4])))
    elif tight:
        rep.warn("E05", "有 %d 对中心线间距 < w+s=%.2f：%s"
                 % (len(tight), w + s, "；".join(tight[:4])))
    else:
        rep.ok("E05", "任意两条中心线间距 >= w+s=%.2f" % (w + s))
    if close:
        rep.info("E05b", "%d 对中心线间距介于 w+s 与 w+s/2 之间（换圈 jog 处允许）："
                         "例如 %s" % (len(close), close[0]))
    if touch:
        rep.info("E05c", "%d 对中心线端点相接（合法：桥/引线/抽头）" % len(touch))
    if joint:
        rep.info("E05d", "%d 对中心线近距但非平行（T 接/拐角/交叉，同网络合法）：例如 %s"
                 % (len(joint), joint[0]))


def check_under_area(geo, w, rep):
    unders = geo.get("under") or []
    upoly = geo.get("under_poly") or []
    if not unders:
        rep.ok("E06", "无 underpass")
        return
    if len(upoly) != len(unders):
        rep.warn("E06", "under_poly %d 个与 under 中心线 %d 条不配对，按中心线扫掠自算"
                 % (len(upoly), len(unders)))
        upoly = [sweep(c, w) for c in unders]
    worst = None
    for k, (poly, ctr) in enumerate(zip(upoly, unders)):
        if len(poly) < 3:
            rep.fail("E04", "under_poly[%d] 只有 %d 个点" % (k, len(poly)))
            continue
        n = len(poly)
        for i in range(n):
            if dist(poly[i], poly[(i + 1) % n]) < EPS:
                rep.fail("E04", "under_poly[%d] 有零长度边（点%d）" % (k, i))
                break
        for i in range(n):
            for j in range(i + 1, n):
                if (j + 1) % n == i or (i + 1) % n == j:
                    continue
                if properly_cross(poly[i], poly[(i + 1) % n],
                                  poly[j], poly[(j + 1) % n]):
                    rep.fail("E04", "under_poly[%d] 自交：边%d x 边%d" % (k, i, j))
                    break
        a = area(poly)
        b = area(sweep(ctr, w))
        if abs(a) < EPS:
            rep.fail("E04", "under_poly[%d] 面积为零" % k)
            continue
        d = abs(abs(a) - abs(b))
        if worst is None or d > worst[0]:
            worst = (d, k, a, b)
    if worst:
        d, k, a, b = worst
        if d > max(AREA_ABS_TOL, AREA_REL_TOL * abs(b)):
            rep.fail("E06", "under_poly[%d] 面积 %.2f 与等宽扫掠 %.2f 差 %.2f um2"
                            "（>%.0f um2 且 >%.0f%%）——典型症状：端头多画楔形，"
                            "会溢到相邻圈金属"
                     % (k, a, b, d, AREA_ABS_TOL, AREA_REL_TOL * 100))
        else:
            rep.ok("E06", "under_poly 面积与等宽扫掠一致（最大偏差 %.3f um2）" % d)


def check_under_clearance(geo, w, s, rep):
    """underpass 多边形不得侵入主金属的 w/2 带内（换圈处除外，那里本来就重叠）。"""
    ups = geo.get("under_poly") or []
    metal = geo.get("metal") or []
    vias = geo.get("vias") or []
    if not ups or not metal:
        rep.ok("E07", "无 underpass 或 metal，跳过层间间距")
        return
    # 换圈连接处 underpass 本来就压在环端金属上：via 周围 1.5w 内不判侵入
    skip_r = max(1.5 * w, max([float(v[2]) for v in vias], default=0.0) / 2.0 + 1.0)
    bad = []
    for k, poly in enumerate(ups):
        for p in poly:
            near_via = any(dist(p, (float(v[0]), float(v[1]))) <= skip_r for v in vias)
            if near_via:
                continue
            d = min([point_seg(p, a, b) for t in metal for a, b in segs_of(t)]
                    or [1e9])
            if d < w / 2.0 - 0.5:
                bad.append("under_poly[%d] 顶点(%.2f,%.2f) 距主金属中心线 %.2f < w/2"
                           % (k, p[0], p[1], d))
    if bad:
        rep.warn("E07", "underpass 侵入主金属带内（%d 处，非 via 附近）：%s"
                 % (len(bad), "；".join(bad[:3])))
    else:
        rep.ok("E07", "underpass 未侵入主金属 w/2 带")


def check_vias(geo, w, rep):
    vias = geo.get("vias") or []
    if not vias:
        rep.ok("E08", "无 via")
        return
    metal = geo.get("metal") or []
    under = geo.get("under") or []
    cut = float(geo.get("via_cut", 0.5))
    sp = float(geo.get("via_space", 0.5))
    off = []
    even = []
    for k, v in enumerate(vias):
        x, y, span = float(v[0]), float(v[1]), float(v[2])
        dm = min([point_seg((x, y), a, b) for t in metal for a, b in segs_of(t)]
                 or [1e9])
        du = min([point_seg((x, y), a, b) for t in under for a, b in segs_of(t)]
                 or [1e9])
        if dm > 0.01 or du > 0.01:
            off.append("via%d(%.2f,%.2f) d_TM2=%.3f d_TM1=%.3f"
                       % (k, x, y, dm, du))
        ncut = int(round((span + sp) / (cut + sp)))
        if ncut >= 1 and ncut % 2 == 0:
            even.append("via%d span=%.2f -> %d 个 cut" % (k, span, ncut))
    if off:
        rep.fail("E08", "via 没有同时落在 TM2 与 TM1 中心线上（%d 个）：%s"
                 % (len(off), "；".join(off[:3])))
    if even:
        rep.fail("E09", "via cut 数为偶数（%d 个），中心不居中、会偏出：%s"
                 % (len(even), "；".join(even[:3])))
    if not off and not even:
        rep.ok("E08", "%d 个 via 均落在两层中心线交点、cut 数为奇数" % len(vias))


def check_ports(geo, rep):
    ports = geo.get("ports") or []
    metal = geo.get("metal") or []
    ct = int(float(geo.get("ct", 0) or 0))
    want = 3 if ct else 2
    w = float(geo.get("w", 0) or 0)
    # geo.json 不一定记着 ct：多出来的那个 x≈0 的端口就是中心抽头，别误判成多画
    if len(ports) == 3 and not ct and w > 0:
        near0 = [p for p in ports if abs(float(p[1][0])) <= w / 2.0 + 0.01]
        if near0:
            ct, want = 1, 3
            rep.info("E10", "检测到中心抽头端口 %s（geo 未记 ct），按 ct=1 校验"
                     % near0[0][0])
    if len(ports) != want:
        rep.fail("E10", "端口 %d 个，期望 %d 个（ct=%d）——ct=1 时中心抽头必须带自己的端口"
                 % (len(ports), want, ct))
        return
    bad = []
    loose = []
    for p in ports:
        name, pt = p[0], (float(p[1][0]), float(p[1][1]))
        dcl = min([point_seg(pt, a, b) for t in metal for a, b in segs_of(t)]
                  or [1e9])
        dend = min([dist(pt, t[0]) for t in metal] +
                   [dist(pt, t[-1]) for t in metal] or [1e9])
        if dcl > w / 2.0 + 0.01:
            bad.append("%s(%.2f,%.2f) 距最近中心线 %.3f > w/2=%.2f（端口悬空）"
                       % (name, pt[0], pt[1], dcl, w / 2.0))
        elif dend > 0.01:
            loose.append("%s 距端点 %.2f" % (name, dend))
    if bad:
        rep.fail("E10", "端口没落在金属上：%s" % "；".join(bad[:3]))
    elif loose:
        rep.warn("E10", "端口在金属上但不在中心线端点（%s）——确认是有意为之"
                 % "；".join(loose[:3]))
    else:
        rep.ok("E10", "端口 %d 个且都落在中心线端点：%s"
               % (len(ports), ", ".join("%s" % p[0] for p in ports)))


def check_bbox(geo, rep):
    bbox = geo.get("bbox")
    metal = geo.get("metal") or []
    if not bbox:
        rep.ok("E11", "无 bbox，跳过")
        return
    x0, y0, x1, y1 = [float(q) for q in bbox]
    outs = []
    for tag in ("metal", "under", "under_poly"):
        for k, poly in enumerate(geo.get(tag) or []):
            for p in poly:
                if p[0] < x0 - EPS or p[0] > x1 + EPS or p[1] < y0 - EPS or p[1] > y1 + EPS:
                    outs.append("%s[%d](%.2f,%.2f)" % (tag, k, p[0], p[1]))
    for k, v in enumerate(geo.get("vias") or []):
        cx, cy, sp2 = float(v[0]), float(v[1]), float(v[2]) / 2.0
        if cx - sp2 < x0 - EPS or cx + sp2 > x1 + EPS or \
           cy - sp2 < y0 - EPS or cy + sp2 > y1 + EPS:
            outs.append("via[%d](%.2f,%.2f)" % (k, cx, cy))
    if outs:
        rep.fail("E11", "有几何跑出 bbox (%g,%g)-(%g,%g)：%s"
                 % (x0, y0, x1, y1, "；".join(outs[:3])))
    else:
        rep.ok("E11", "全部几何在 bbox 内")


def check_rings(geo, rep):
    # Both kernels round: Python layout.py:239 max(1, round(n)) and SKILL
    # librf.il:1712 max(1, fix(n + 0.5)).  Each also rejects a non-integral n
    # (layout.py:233 / librf.il:1701), so an even n is perfectly legal here --
    # the old "SKILL forces odd n" rule belongs to the previous engine.
    n = geo.get("n")
    N = geo.get("N")
    if n is None or N is None:
        rep.ok("E12", "无 n/N，跳过圈数检查")
        return
    nf = float(n)
    want = int(round(nf))
    if abs(nf - want) > 1e-6:
        rep.fail("E12", "n=%g 不是整数；两侧内核都会拒绝（Python layout.py:233 / "
                        "librf.il:1701）" % nf)
    elif int(N) != max(1, want):
        rep.fail("E12", "圈数 N=%d 与 round(n)=%d 不一致" % (int(N), max(1, want)))
    else:
        rep.ok("E12", "圈数 N=%d 与 n=%g 自洽（两侧同为 round，偶数 n 合法）"
                      % (int(N), nf))


def check_mirror(geo, rep):
    runs = geo.get("_runs") or []
    if not runs:
        rep.ok("E13", "无 runs 分组，跳过镜像统计")
        return
    pts = set()
    for t in runs:
        for p in t:
            pts.add((round(p[0], 5), round(p[1], 5)))
    miss = [p for p in pts if (-p[0], p[1]) not in pts]
    if not miss:
        rep.ok("E13", "两臂顶点在 x 镜像下完全对应（%d 点）" % len(pts))
    else:
        rep.info("E13", "两臂顶点在 x 镜像下有 %d/%d 个不对应"
                        "（换圈 jog 处允许不对称）：%s"
                 % (len(miss), len(pts),
                    ", ".join("(%.2f,%.2f)" % m for m in miss[:3])))


# ------------------------------------------------------------------ 入口
def run(geo):
    rep = Report()
    metal = [list(map(lambda q: (float(q[0]), float(q[1])), t))
             for t in (geo.get("metal") or geo.get("tops") or [])]
    under = [list(map(lambda q: (float(q[0]), float(q[1])), t))
             for t in (geo.get("under") or [])]
    w = float(geo.get("w", 0) or 0)
    s = float(geo.get("s", 0) or 0)
    if not metal:
        rep.fail("E00", "geo 里没有 metal（中心线折线）")
        return rep
    if w <= 0:
        rep.fail("E00", "缺 w（金属宽度），大部分检查无法进行")
        return rep
    named = [("metal[%d]" % k, t) for k, t in enumerate(metal)] + \
            [("under[%d]" % k, t) for k, t in enumerate(under)]
    check_dup(named, rep)
    check_angles(named, rep)
    check_selfcross(named, rep)
    check_spacing(metal, w, s, rep)
    check_under_area(geo, w, rep)
    check_under_clearance(geo, w, s, rep)
    check_vias(geo, w, rep)
    check_ports(geo, rep)
    check_bbox(geo, rep)
    check_rings(geo, rep)
    check_mirror(geo, rep)
    return rep


# ------------------------------------------------------------------ 自检
def _good():
    w, s = 8.0, 3.0
    a, b = 60.0, 50.0
    run_a = [(5.5, b), (a, b), (a, -b), (5.5, -b)]
    run_b = [(-5.5, b), (-a, b), (-a, -b), (-5.5, -b)]
    metal = [run_a, run_b,
             [(5.5, -b), (-5.5, -b)],
             [(5.5, b), (5.5, b + 20.0)],
             [(-5.5, b), (-5.5, b + 20.0)]]
    under = [[(-5.5, b), (-5.5, b - 11.0), (5.5, b - 11.0), (5.5, -b)]]
    return {
        "w": w, "s": s, "n": 1, "N": 1, "ct": 0,
        "metal": metal, "under": under,
        "under_poly": [sweep(under[0], w)],
        "vias": [(-5.5, b, 2.5), (5.5, -b, 2.5)],
        "ports": [("p01", (5.5, b + 20.0)), ("p02", (-5.5, b + 20.0))],
        "bbox": (-a - 8, -b - 8, a + 8, b + 28),
        "_runs": [run_a, run_b],
    }


def selftest():
    cases = []
    g = _good()
    cases.append(("good", g, "PASS"))
    import copy
    bad = copy.deepcopy(g)
    bad["metal"][1][1] = (bad["metal"][1][0][0] + 10.0, bad["metal"][1][0][1] + 5.0)
    cases.append(("bad_angle", bad, "FAIL"))
    bad = copy.deepcopy(g)
    bad["metal"][1] = [(-1.0, 55.0), (-1.0, -55.0)]
    cases.append(("bad_merge", bad, "FAIL"))
    bad = copy.deepcopy(g)
    bad["under_poly"] = [sweep(g["under"][0], g["w"] * 1.6)]
    cases.append(("bad_area", bad, "FAIL"))
    bad = copy.deepcopy(g)
    bad["vias"] = [(8.0, 45.0, 2.5), (20.0, 3.0, 2.5)]
    cases.append(("bad_via", bad, "FAIL"))
    bad = copy.deepcopy(g)
    bad["metal"][3] = [(5.5, 50.0), (5.5, 50.0), (5.5, 70.0)]
    cases.append(("bad_dup", bad, "WARN"))
    rc = 0
    for name, geo, want in cases:
        rep = run(geo)
        if rep.failures:
            got = "FAIL"
        elif any(i["level"] == "WARN" for i in rep.items):
            got = "WARN"
        else:
            got = "PASS"
        first = rep.failures[0]["msg"] if rep.failures else ""
        print("%-10s 期望%-4s 实际%-4s %s" % (name, want, got, first[:58]))
        if got != want:
            rc = 1
    return rc


def _force_utf8_stdout():
    # --json has to stay machine-readable: on Windows the console code page
    # (cp936 etc.) turns Chinese messages into bytes no other tool can decode.
    for st in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(st, "encoding", "") or "").lower().replace("-", "")
            if enc != "utf8" and hasattr(st, "reconfigure"):
                st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv):
    _force_utf8_stdout()
    if "--selftest" in argv:
        return selftest()
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    try:
        if sys.version_info[0] >= 3:
            f = open(args[0], "r", encoding="utf-8")
        else:
            f = open(args[0], "r")
        geo = json.load(f)
        f.close()
    except Exception as exc:
        print("读不了 %s: %s" % (args[0], exc))
        return 2
    rep = run(geo)
    if "--json" in argv:
        print(json.dumps({"ok": not rep.failures, "items": rep.items},
                         ensure_ascii=False))
    else:
        order = {"FAIL": 0, "WARN": 1, "INFO": 2, "PASS": 3}
        for it in sorted(rep.items, key=lambda x: order[x["level"]]):
            print("%-4s %-4s %s" % (it["level"], it["code"], it["msg"]))
        print("\n%s" % ("全部通过" if not rep.failures
                        else "%d 项 FAIL" % len(rep.failures)))
    return 0 if not rep.failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
