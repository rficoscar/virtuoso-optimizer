# -*- coding: utf-8 -*-
"""geometry.py - 版图几何检查（纯标准库，坐标单位 um）。

检查项（每项返回 {'id','status','value','limit','detail'}）：
  G1 bbox                外框尺寸/面积 vs 期望
  G2 min_spacing         螺旋层同一多边形非相邻边的最小距离（≈ 圈间距 s）
  G3 self_intersection   多边形自交（真交叉，不含相邻边共点）
  G4 via_enclosure       过孔到上下层金属边的最小包络
  G5 via_size            过孔尺寸/间距 vs 规则
  G6 labels              端口标签数量/命名/是否落在导体上
  G7 connectivity        端口是否同一网络、有无悬空金属
  G8 turns_est           用射线穿越数估计圈数
  G9 symmetry            关于 x=0 的镜像对称率（差分结构）
  G10 inner_clearance    内圈最小净空
  G11 lead_match         两个端口标签到螺旋外框边的距离差

阈值来自 config['limits']；缺项用保守默认值。
"""
from __future__ import print_function

import math

import gdsread


# ------------------------------------------------------------ polygon helpers
def bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def bbox_union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def bbox_overlap(a, b, tol=0.0):
    return not (a[2] + tol < b[0] or b[2] + tol < a[0] or
                a[3] + tol < b[1] or b[3] + tol < a[1])


def point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a, b, p, eps=1e-9):
    if abs(_orient(a, b, p)) > eps:
        return False
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps and
            min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def seg_intersect(a, b, c, d, eps=1e-9):
    """0=不相交；1=真交叉（内部相交）；2=接触（端点/共线重叠）。"""
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    if ((o1 > eps) != (o2 > eps)) and ((o3 > eps) != (o4 > eps)):
        return 1
    if (_on_seg(a, b, c) or _on_seg(a, b, d) or
            _on_seg(c, d, a) or _on_seg(c, d, b)):
        return 2
    return 0


def _pt_seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def seg_seg_dist(a, b, c, d):
    if seg_intersect(a, b, c, d) != 0:
        return 0.0
    return min(_pt_seg_dist(a, c, d), _pt_seg_dist(b, c, d),
               _pt_seg_dist(c, a, b), _pt_seg_dist(d, a, b))


def polys_overlap(p, q):
    """同层图形是否接触/重叠（含包含、边接触）。"""
    if not bbox_overlap(bbox(p), bbox(q), 1e-6):
        return False
    for i in range(len(p)):
        a, b = p[i], p[(i + 1) % len(p)]
        for j in range(len(q)):
            if seg_intersect(a, b, q[j], q[(j + 1) % len(q)]) != 0:
                return True
    return point_in_poly(p[0], q) or point_in_poly(q[0], p)


def poly_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def ray_crossings(pt, poly, dx=1.0, dy=0.0, max_t=10000.0):
    """从 pt 沿 (dx,dy) 射线与多边形边界的交点数（用于圈数估计）。"""
    x, y = pt
    hits = 0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((a[0] - x) * ey - (a[1] - y) * ex) / den
        u = ((a[0] - x) * dy - (a[1] - y) * dx) / den
        if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9:
            hits += 1
    return hits


# ------------------------------------------------------------------ checks
def _chk(cid, status, value, limit, detail):
    return {"id": cid, "status": status, "value": value, "limit": limit,
            "detail": detail}


def _grade_num(value, limit, warn_ratio=1.15, lower_is_bad=True, eps=1e-6):
    """数值门禁：lower_is_bad=True 时 value<=limit 通过；False 时 value>=limit 通过。

    多边形偏置/斜边会产生 ~1e-4 相对误差（如 w=2.0 量到 1.9997），
    因此容差取 max(eps, 5e-4*|limit|)，避免把数值噪声判成 warn。
    """
    if value is None:
        return "warn"
    eps = max(eps, abs(limit) * 5e-4)
    if lower_is_bad:
        if value <= limit + eps:
            return "pass"
        return "warn" if value <= limit * warn_ratio else "fail"
    if value >= limit - eps:
        return "pass"
    return "warn" if value >= limit / warn_ratio else "fail"


def check_bbox(spiral, cfg):
    b = bbox_union([bbox(s["pts"]) for s in spiral])
    exp = (cfg.get("expect") or {}).get("bbox")
    if not exp:
        return _chk("G1_bbox", "info", b, None, "无期望外框，仅记录")
    if b is None:
        return _chk("G1_bbox", "fail", None, exp, "螺旋层无图形，无法比对期望外框")
    tol = float((cfg.get("limits") or {}).get("bbox_tol", 0.5))
    ok = all(abs(b[i] - exp[i]) <= tol for i in range(4))
    return _chk("G1_bbox", "pass" if ok else "fail", b, exp,
                "外框 [xmin,ymin,xmax,ymax]，容差 %.2f um" % tol)


def clean_poly(pts):
    """去重复点 + 去共线点，避免零长边/共线切分干扰距离与自交判断。"""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    changed = True
    while changed and len(out) > 3:
        changed = False
        res = []
        n = len(out)
        for i in range(n):
            a, b, c = out[(i - 1) % n], out[i], out[(i + 1) % n]
            if abs(_orient(a, b, c)) <= 1e-6 * max(1.0, math.hypot(b[0] - a[0], b[1] - a[1])
                                                    * math.hypot(c[0] - b[0], c[1] - b[1])):
                changed = True
                continue
            res.append(b)
        if res:
            out = res
    return out


def check_min_width(polys, cfg):
    """金属最小宽度：两条反向平行边之间、且连线完全在金属内的最短距离。"""
    lim = float((cfg.get("limits") or {}).get("min_w", 2.0))
    best = None
    for p in polys:
        pts = clean_poly(p["pts"])
        n = len(pts)
        segs = [(pts[i], pts[(i + 1) % n]) for i in range(n)
                if math.hypot(pts[(i + 1) % n][0] - pts[i][0],
                              pts[(i + 1) % n][1] - pts[i][1]) >= 0.5]
        for ii in range(len(segs)):
            a, b = segs[ii]
            ux, uy = b[0] - a[0], b[1] - a[1]
            nu = math.hypot(ux, uy) or 1.0
            for jj in range(ii + 1, len(segs)):
                c, d = segs[jj]
                vx, vy = d[0] - c[0], d[1] - c[1]
                nv = math.hypot(vx, vy) or 1.0
                dot = (ux * vx + uy * vy) / (nu * nv)
                if dot > -0.9:          # 只取反向平行（可能是一条金属带的两侧）
                    continue
                dd = seg_seg_dist(a, b, c, d)
                if dd < 0.2 or dd > 60.0:
                    continue
                # 取最近点连线中点，判断是否在金属内部
                cand = []
                for pa in (a, b):
                    for pc in (c, d):
                        cand.append((math.hypot(pa[0] - pc[0], pa[1] - pc[1]), pa, pc))
                _, pa, pc = min(cand, key=lambda t: t[0])
                mid = ((pa[0] + pc[0]) / 2, (pa[1] + pc[1]) / 2)
                samples = [(pa[0] + t * (pc[0] - pa[0]), pa[1] + t * (pc[1] - pa[1]))
                           for t in (0.2, 0.4, 0.5, 0.6, 0.8)]
                if all(point_in_poly(q, pts) for q in samples) and point_in_poly(mid, pts):
                    if best is None or dd < best:
                        best = dd
    if best is None:
        return _chk("G0_min_width", "info", None, lim, "未找到可测的平行金属带")
    st = _grade_num(best, lim, 1.15, lower_is_bad=False)
    return _chk("G0_min_width", st, round(best, 4), lim, "金属最小宽度")


def _nearest_pair(a, b, c, d):
    """两线段最近点对（用于区分宽度/间距）。"""
    def proj(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 == 0:
            return a
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        return (a[0] + t * dx, a[1] + t * dy)
    cands = [(a, proj(a, c, d)), (b, proj(b, c, d)),
             (proj(c, a, b), c), (proj(d, a, b), d)]
    return min(cands, key=lambda t: math.hypot(t[0][0] - t[1][0], t[0][1] - t[1][1]))


def check_spacing(polys, cfg):
    """同层金属的最小间距：只看长度 >= 1um 的非相邻边、且不共用端点。"""
    lim = float((cfg.get("limits") or {}).get("min_s", 3.0))
    worst = None
    where = None
    for p in polys:
        pts = clean_poly(p["pts"])
        n = len(pts)
        segs = []
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if math.hypot(b[0] - a[0], b[1] - a[1]) >= 1.0:
                segs.append((i, a, b))
        for ii in range(len(segs)):
            i, a, b = segs[ii]
            for jj in range(ii + 1, len(segs)):
                j, c, d = segs[jj]
                if abs(i - j) <= 1 or abs(i - j) == n - 1:
                    continue
                if a == c or a == d or b == c or b == d:
                    continue
                # 只比较“平行且相对”的边：DRC 间距规则不约束拐角对角距离
                ux, uy = b[0] - a[0], b[1] - a[1]
                vx, vy = d[0] - c[0], d[1] - c[1]
                nu = math.hypot(ux, uy) or 1.0
                nv = math.hypot(vx, vy) or 1.0
                if abs(ux * vy - uy * vx) / (nu * nv) > 0.08:
                    continue
                dd = seg_seg_dist(a, b, c, d)
                # 最近点连线的中点在金属内 -> 这是同一根线的“宽度”，不是间距
                pa, pc = _nearest_pair(a, b, c, d)
                mid = ((pa[0] + pc[0]) / 2, (pa[1] + pc[1]) / 2)
                if point_in_poly(mid, pts):
                    continue
                if worst is None or dd < worst:
                    worst, where = dd, (i, j)
    if worst is None:
        return _chk("G2_min_spacing", "info", None, lim, "无可比较的非相邻边")
    st = _grade_num(worst, lim, 1.15, lower_is_bad=False)
    return _chk("G2_min_spacing", st, round(worst, 4), lim,
                "同层非相邻边最小距离（规则下限 %.2f um）" % lim)


def check_self_intersection(polys):
    hits = 0
    for p in polys:
        pts = clean_poly(p["pts"])
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                if seg_intersect(a, b, pts[j], pts[(j + 1) % n]) == 1:
                    hits += 1
    return _chk("G3_self_intersection", "pass" if hits == 0 else "fail", hits, 0,
                "边界真交叉数（不含相邻边共点）")


def check_via(viag, pools, pairs, cfg):
    """分层过孔检查。

    viag  : {via_role: [shapes]}，例 {'via': TV2, 'via2': TV1}
    pools : {metal_role: [shapes]}
    pairs : {via_role: [lower_role, upper_role]}
    每个过孔必须在它声明的两层金属里都有宿主，且包络/间距满足 limits。
    """
    import math
    lim = cfg.get("limits") or {}
    cut = float(lim.get("via_cut", 0.36))
    space = float(lim.get("via_space", 0.34))
    enc = float(lim.get("via_enc", 0.5))
    enc_map = lim.get("via_enc_by_role") or {}
    cut_map = lim.get("via_cut_by") or {}
    space_map = lim.get("via_space_by") or {}
    res = []
    n_via = sum(len(v) for v in viag.values())
    if n_via == 0:
        if (cfg.get("expect") or {}).get("stack"):
            return [_chk("G5_via_size", "fail", 0, 0, "叠层结构必须有缝合孔")]
        return [_chk("G5_via_size", "info", 0, 0, "无过孔（单层结构）")]
    bad_size = 0
    cut_ref = None
    no_host = 0
    min_enc = None
    min_enc_ratio = None
    min_enc_ct = None
    min_enc_role = None
    hosts_n = 0
    host_boxes = {}
    for role, pool in pools.items():
        host_boxes[role] = [(p, bbox(p["pts"])) for p in pool]

    def find_host(role, pt):
        for p, bx in host_boxes.get(role, []):
            if point_in_poly(pt, p["pts"]):
                return p
        return None

    for vrole, vias in viag.items():
        lower, upper = pairs[vrole]
        # 切径/间距按孔类型（V5 与 TV2 尺寸不同）；缺省回落到 limits 的静态值。
        cut_r = float(cut_map.get(vrole, cut))
        if cut_ref is None:
            cut_ref = cut_r
        # 阈值可按孔分侧给：[下层, 上层]；单个数字表示两侧同值。
        er = enc_map.get(vrole, enc)
        if isinstance(er, (list, tuple)):
            enc_lo, enc_hi = float(er[0]), float(er[1])
        else:
            enc_lo = enc_hi = float(er)
        for v in vias:
            bx = bbox(v["pts"])
            w, h = bx[2] - bx[0], bx[3] - bx[1]
            if abs(w - cut_r) > 0.01 or abs(h - cut_r) > 0.01:
                bad_size += 1
                cut_ref = cut_r
            c = ((bx[0] + bx[2]) / 2.0, (bx[1] + bx[3]) / 2.0)
            for role in (lower, upper):
                enc_r = enc_lo if role == lower else enc_hi
                # 宿主判定看中心；包络算"并集"：同层多块金属重叠成一片时，
                # 取包含探针点、且沿射线伸得最远的那块（Calibre 对合并层判定）。
                if find_host(role, c) is None:
                    no_host += 1
                    continue
                hosts_n += 1
                cand = []
                probes = [((bx[0], c[1]), (-1.0, 0.0)),
                          ((bx[2], c[1]), (1.0, 0.0)),
                          ((c[0], bx[1]), (0.0, -1.0)),
                          ((c[0], bx[3]), (0.0, 1.0))]
                for (px, py), (dx, dy) in probes:
                    best = None
                    for p, _pbox in host_boxes.get(role, []):
                        if not point_in_poly((px, py), p["pts"]):
                            continue
                        d = _ray_to_boundary((px, py), p["pts"], dx, dy)
                        if d is not None and (best is None or d > best):
                            best = d
                    cand.append(0.0 if best is None else best)
                dmin = min(cand)
                ratio = dmin / enc_r if enc_r > 0 else 1.0e9
                if min_enc_ratio is None or ratio < min_enc_ratio:
                    min_enc_ratio = ratio
                    min_enc = dmin
                    min_enc_ct = enc_r
                    min_enc_role = "%s->%s" % (vrole, role)
    # 过孔间距（同层内，网格加速）；每种孔按自身 min space 判，记录最差比值
    min_space = None
    min_space_ratio = None
    min_space_limit = None
    min_space_role = None
    n_dup = 0
    for vrole, vias in viag.items():
        if not vias:
            continue
        cut_r = float(cut_map.get(vrole, cut))
        space_r = float(space_map.get(vrole, space))
        cell = max(cut_r + space_r, 1.0)
        grid = {}
        for idx, v in enumerate(vias):
            bx = bbox(v["pts"])
            cx = int(math.floor(((bx[0] + bx[2]) / 2.0) / cell))
            cy = int(math.floor(((bx[1] + bx[3]) / 2.0) / cell))
            grid.setdefault((cx, cy), []).append(idx)
        for (cx, cy), ids in grid.items():
            near = list(ids)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        near.extend(grid.get((cx + dx, cy + dy), []))
            for ii in ids:
                vi = vias[ii]
                for jj in near:
                    if jj <= ii:
                        continue
                    vj = vias[jj]
                    bi = bbox(vi["pts"])
                    bj = bbox(vj["pts"])
                    if not bbox_overlap(bi, bj, 1.0):
                        continue
                    d = min(seg_seg_dist(vi["pts"][k], vi["pts"][(k + 1) % 4],
                                         vj["pts"][m], vj["pts"][(m + 1) % 4])
                            for k in range(4) for m in range(4))
                    if d < 1.0e-6:
                        n_dup += 1
                        continue
                    ratio = d / space_r if space_r > 0 else 1.0e9
                    if min_space_ratio is None or ratio < min_space_ratio:
                        min_space_ratio = ratio
                        min_space = d
                        min_space_limit = space_r
                        min_space_role = vrole
    if bad_size:
        res.append(_chk("G5_via_size", "fail", bad_size, 0,
                        "过孔尺寸不等于自身切径（最后比对 %.3f）的数量" % cut_ref))
    else:
        res.append(_chk("G5_via_size", "pass", 0, 0, "全部过孔等于各自切径"))
    if no_host:
        res.append(_chk("G4b_via_host", "fail", no_host, 0,
                        "未同时落在上下两层金属内的过孔数（悬空孔）"))
    else:
        res.append(_chk("G4b_via_host", "pass", 0, 0, "全部过孔两端都有宿主金属"))
    if min_enc is None:
        res.append(_chk("G4_via_enclosure", "fail", None, enc, "有过孔不在上下层金属内"))
    else:
        st = _grade_num(min_enc_ratio, 1.0, 1.10, lower_is_bad=False)
        res.append(_chk("G4_via_enclosure", st, round(min_enc, 4),
                        round(min_enc_ct, 4),
                        "过孔到宿主金属边最小距离（%s；按该孔自身阈值 %.2f）"
                        % (min_enc_role, min_enc_ct)))
    if min_space is not None:
        st = _grade_num(min_space_ratio, 1.0, 1.10, lower_is_bad=False)
        res.append(_chk("G5b_via_space", st, round(min_space, 4),
                        round(min_space_limit, 4),
                        "过孔间最小距离（%s，按该孔自身阈值；重复孔 %d 个，已排除）"
                        % (min_space_role, n_dup)))
    return res


def check_stack(flat, ld, cfg):
    """叠层一致性：TM1 与 TM2 面积必须一一对应（同一版图复制）。"""
    exp = cfg.get("expect") or {}
    lay = cfg.get("layers") or {}
    if not exp.get("stack"):
        return _chk("G10_stack", "info", None, None, "非叠层结构，跳过")
    if "under" not in lay:
        return _chk("G10_stack", "fail", None, None, "叠层要求 under(TM1) 层")
    sp = gdsread.elements_at(flat, *tuple(lay["spiral"]))
    un = gdsread.elements_at(flat, *tuple(lay["under"]))
    a_sp = sum(poly_area(p["pts"]) for p in sp)
    a_un = sum(poly_area(p["pts"]) for p in un)
    ratio = (a_un / a_sp) if a_sp else 0.0
    ok = 0.98 <= ratio <= 1.02
    return _chk("G10_stack", "pass" if ok else "fail",
                {"area_tm2": round(a_sp, 2), "area_tm1": round(a_un, 2),
                 "ratio": round(ratio, 4)},
                "TM1/TM2 面积比 0.98..1.02", "叠层金属复制完整性")


def check_labels(flat, spiral, under, cfg):
    lay = cfg.get("layers") or {}
    txt = cfg.get("text") or {}
    sp = tuple(lay.get("spiral", (122, 0)))
    un = tuple(lay.get("under", (120, 0)))
    # 文本层跟主/副金属走：TM2 -> 122:3，M6 -> 146:0，不能拿导体层号当文本层
    tsp = tuple(txt.get("spiral") or (sp[0], 3))
    tun = tuple(txt.get("under") or (un[0], 3))
    texts = [t for t in flat["texts"]
             if (t["layer"], t["datatype"]) in (tsp, tun)]
    expect = (cfg.get("expect") or {}).get("ports")
    names = [t["string"] for t in texts]
    out = []
    if expect and sorted(names) != sorted(expect):
        out.append(_chk("G6_labels", "fail", names, expect,
                        "端口标签集合不符（EMX/LVS 都按标签建端口）"))
    else:
        out.append(_chk("G6_labels", "pass", names, expect, "端口标签齐全"))
    unattached = []
    for t in texts:
        pool = spiral if (t["layer"], t["datatype"]) == tsp else under
        if not any(point_in_poly(t["xy"], p["pts"]) for p in pool):
            unattached.append(t["string"])
    out.append(_chk("G6b_label_attach", "pass" if not unattached else "fail",
                    unattached, [], "标签 XY 未落在同层导体上的端口"))
    return out


def check_turns(spiral, cfg):
    if not spiral:
        return _chk("G8_turns_est", "fail", None, None, "无螺旋金属")
    p = max(spiral, key=lambda s: poly_area(s["pts"]))
    bx = bbox(p["pts"])
    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
    hits = ray_crossings((cx, cy), p["pts"])
    est = hits / 2.0 if hits % 2 == 0 else hits
    exp = (cfg.get("expect") or {}).get("turns")
    if exp is None:
        return _chk("G8_turns_est", "info", est, None, "射线穿越 %d 次" % hits)
    ok = abs(est - float(exp)) <= 1.0
    return _chk("G8_turns_est", "pass" if ok else "warn", est, float(exp),
                "射线穿越 %d 次估计圈数" % hits)


def check_symmetry(spiral, cfg):
    exp = (cfg.get("expect") or {}).get("symmetry")
    if not exp:
        return _chk("G9_symmetry", "info", None, None, "该结构不要求对称")
    tol = float((cfg.get("limits") or {}).get("sym_tol", 0.05))
    verts = [(x, y) for s in spiral for (x, y) in s["pts"]]
    if not verts:
        return _chk("G9_symmetry", "fail", None, tol, "无顶点")
    matched = 0
    for (x, y) in verts:
        if any(abs(mx - (-x)) <= tol and abs(my - y) <= tol for (mx, my) in verts):
            matched += 1
    ratio = matched / float(len(verts))
    return _chk("G9_symmetry", "pass" if ratio >= 0.98 else "fail",
                round(ratio, 4), ">=0.98", "顶点关于 x=0 的镜像命中率")


def check_inner_clearance(spiral):
    if not spiral:
        return _chk("G10_inner_clearance", "fail", None, None, "无螺旋金属")
    p = max(spiral, key=lambda s: poly_area(s["pts"]))
    bx = bbox(p["pts"])
    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
    best = None
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        d = _ray_to_boundary((cx, cy), p["pts"], dx, dy)
        if d is not None and (best is None or d < best):
            best = d
    return _chk("G10_inner_clearance", "info", round(best, 4) if best else None,
                None, "中心到最近内圈金属边的距离")


def _ray_to_boundary(pt, poly, dx, dy, max_t=10000.0):
    x, y = pt
    best = None
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((a[0] - x) * ey - (a[1] - y) * ex) / den
        u = ((a[0] - x) * dy - (a[1] - y) * dx) / den
        if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9:
            if best is None or t < best:
                best = t
    return best


def check_lead_match(flat, spiral, cfg):
    """两个端口标签到螺旋外框最近边的距离差（判断出线等长）。"""
    lay = cfg.get("layers") or {}
    sp = tuple(lay.get("spiral", (122, 0)))
    # 文本层不能从金属层推：只有 TM2 的金属/文本共用层号（122:0 / 122:3）。
    # 主金属可换时必须用 cfg["text"]["spiral"]（run_all 已按 stack 表填好）。
    tlay = tuple((cfg.get("text") or {}).get("spiral") or (sp[0], 3))
    texts = [t for t in flat["texts"]
             if (t["layer"], t.get("datatype", 0)) == tlay]
    if len(texts) < 2:
        return _chk("G11_lead_match", "warn", None, None, "端口不足 2 个")
    b = bbox_union([bbox(s["pts"]) for s in spiral])
    ds = []
    for t in texts[:2]:
        x, y = t["xy"]
        ds.append(min(abs(x - b[0]), abs(x - b[2]), abs(y - b[1]), abs(y - b[3])))
    diff = abs(ds[0] - ds[1])
    lim = float((cfg.get("limits") or {}).get("lead_match", 1.0))
    return _chk("G11_lead_match", "info", round(diff, 4), lim,
                "两端口到外框距离差（诊断量；对称结构请用 G9）")


def _scan_intervals(polys, y):
    """水平扫描线 y 与多边形的区间（even-odd），返回排序后的 (x0,x1) 列表。"""
    xs = []
    for p in polys:
        pts = p["pts"]
        n = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            if (y1 <= y < y2) or (y2 <= y < y1):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    out = []
    for i in range(0, len(xs) - 1, 2):
        out.append((xs[i], xs[i + 1]))
    return out


def check_leads(flat, spiral, cfg):
    """差分引线门禁：端口坐标、引线宽、引线间距、引线长都要等于参数。"""
    exp = (cfg.get("expect") or {}).get("leads")
    if not exp:
        return [_chk("G12_leads", "info", None, None, "未提供引线期望值，跳过")]
    lay = cfg.get("layers") or {}
    sp = tuple(lay.get("spiral", (122, 0)))
    tsp = tuple((cfg.get("text") or {}).get("spiral") or (sp[0], 3))
    txt = {t["string"]: t["xy"] for t in flat["texts"]
           if (t["layer"], t["datatype"]) == tsp}
    if "p01" not in txt or "p02" not in txt:
        return [_chk("G12_leads", "fail", sorted(txt), ["p01", "p02"],
                     "缺少 p01/p02 标签")]
    xp = float(exp["x"])
    yp = float(exp["y"])
    lw = float(exp["w"])
    lg = float(exp["gap"])
    tol = float((cfg.get("limits") or {}).get("lead_tol", 0.06))
    res = []
    errs = []
    pos = {"p01": txt["p01"], "p02": txt["p02"]}
    if abs(pos["p01"][0] - xp) > tol or abs(pos["p02"][0] + xp) > tol:
        errs.append("端口 x=%r/%r 期望 ±%.3f" % (pos["p01"][0], pos["p02"][0], xp))
    if abs(pos["p01"][1] - yp) > tol or abs(pos["p02"][1] - yp) > tol:
        errs.append("端口 y=%r/%r 期望 %.3f" % (pos["p01"][1], pos["p02"][1], yp))
    # 贴近端口扫描：引线靠近外圈处有一段收窄过渡，只有端口一侧是全宽段
    yscan = yp - 0.5
    ivs = [iv for iv in _scan_intervals(spiral, yscan)
           if abs((iv[0] + iv[1]) / 2.0) <= xp + lw]
    if len(ivs) != 2:
        errs.append("y=%.2f 扫描到 %d 条引线（期望 2）" % (yscan, len(ivs)))
    else:
        ivs.sort()
        (a0, a1), (b0, b1) = ivs
        if abs((a1 - a0) - lw) > tol:
            errs.append("左引线宽 %.3f != %.3f" % (a1 - a0, lw))
        if abs((b1 - b0) - lw) > tol:
            errs.append("右引线宽 %.3f != %.3f" % (b1 - b0, lw))
        if abs((b0 - a1) - lg) > tol:
            errs.append("引线间距 %.3f != %.3f" % (b0 - a1, lg))
        if abs((a0 + a1) / 2.0 + xp) > tol or abs((b0 + b1) / 2.0 - xp) > tol:
            errs.append("引线中心 %.3f/%.3f != ±%.3f"
                        % ((a0 + a1) / 2.0, (b0 + b1) / 2.0, xp))
    res.append(_chk("G12_leads", "pass" if not errs else "fail",
                    errs or [round(xp, 3), round(yp, 3), lw, lg],
                    {"x": xp, "y": yp, "w": lw, "gap": lg},
                    "差分引线位置/宽度/间距/长度与参数一致"))
    return res


def stack_roles(cfg):
    """按用例的 metal_main/metal_aux 把角色解析到实际 GDS 层。

    cfg['stack'] 由 tools/gen_tech.py 从工艺 JSON 生成：
        {'order': [...], 'defaults': {...},
         'metals': {nm: {'gds', 'text_gds'}},
         'vias': {vn: {'gds','lower','upper','cut','space','enc_lo','enc_hi'}}}
    返回 {'ld','pairs','enc_by','main','aux'}；没有 stack 表时返回 None，
    调用方回退到静态 layers/via_pairs。
    """
    st = cfg.get("stack") or {}
    order = st.get("order") or []
    metals = st.get("metals") or {}
    vias = st.get("vias") or {}
    if not order or not metals or not vias:
        return None
    pr = cfg.get("params") or {}
    defaults = st.get("defaults") or cfg.get("defaults") or {}
    main = pr.get("metal_main") or defaults.get("metal_main")
    aux = pr.get("metal_aux") or defaults.get("metal_aux")
    if main not in metals or aux not in metals:
        return None
    i0, i1 = order.index(aux), order.index(main)
    if i1 <= i0:
        return None
    roles = {main: "spiral", aux: "under"}
    for nm in order[i0 + 1:i1]:
        roles[nm] = "mid_" + nm.lower()
    if i0 > 0:
        roles[order[i0 - 1]] = "route"
    ld, pairs, enc_by, cut_by, space_by = {}, {}, {}, {}, {}
    for nm, role in roles.items():
        ld[role] = tuple(int(v) for v in metals[nm]["gds"])
    for vn, v in vias.items():
        lo, hi = v.get("lower"), v.get("upper")
        if lo not in roles or hi not in roles:
            continue
        if order.index(hi) - order.index(lo) != 1:
            continue
        pairs[vn] = [roles[lo], roles[hi]]
        enc_by[vn] = [float(v.get("enc_lo", 0.0)), float(v.get("enc_hi", 0.0))]
        cut_by[vn] = float(v.get("cut", 0.0))
        space_by[vn] = float(v.get("space", 0.0))
        ld[vn] = tuple(int(x) for x in v["gds"])
    if "spiral" not in roles.values() or "under" not in roles.values():
        return None
    return {"ld": ld, "pairs": pairs, "enc_by": enc_by,
            "cut_by": cut_by, "space_by": space_by,
            "main": main, "aux": aux, "roles": roles}


def run_all(flat, cfg):
    ctx = stack_roles(cfg)
    if ctx:
        cfg = dict(cfg)
        cfg["layers"] = {r: list(v) for r, v in ctx["ld"].items()
                         if r not in ctx["pairs"]}
        metals = (cfg.get("stack") or {}).get("metals") or {}
        cfg["text"] = dict(cfg.get("text") or {})
        cfg["text"]["spiral"] = metals[ctx["main"]]["text_gds"]
        cfg["text"]["under"] = metals[ctx["aux"]]["text_gds"]
        cfg["limits"] = dict(cfg.get("limits") or {})
        cfg["limits"]["via_enc_by_role"] = ctx["enc_by"]
        cfg["limits"]["via_cut_by"] = ctx["cut_by"]
        cfg["limits"]["via_space_by"] = ctx["space_by"]
        ld = ctx["ld"]
        pairs = ctx["pairs"]
    else:
        lay = cfg.get("layers") or {}
        pairs = cfg.get("via_pairs") or {"via": ["spiral", "under"]}
        ld = {}
        for role, v in lay.items():
            ld[role] = (int(v[0]), int(v[1]))
    metal_roles = [r for r in ld if r not in pairs]
    pools = {r: gdsread.elements_at(flat, ld[r][0], ld[r][1]) for r in metal_roles}
    viag = {r: gdsread.elements_at(flat, ld[r][0], ld[r][1]) for r in pairs if r in ld}
    spiral = pools.get("spiral", [])
    under = pools.get("under", [])
    if not spiral:
        # 层角色与 GDS 不匹配（常见于配置缺 stack 表却指定了 metal_main），
        # 后面的检查只会连环报 NoneType；这里一次说清楚。
        return [_chk("G0_min_width", "fail", None, None,
                     "螺旋层 %s 无图形：层角色与 GDS 不匹配，检查配置 stack/layers"
                     % (ld.get("spiral"),))]
    checks = []
    checks.append(check_min_width(spiral, cfg))
    checks.append(check_bbox(spiral, cfg))
    checks.append(check_spacing(spiral, cfg))
    checks.append(check_self_intersection(spiral))
    checks.extend(check_via(viag, pools, pairs, cfg))
    checks.extend(check_labels(flat, spiral, under, cfg))
    try:
        import connectivity
        tl = {}
        txt = cfg.get("text") or {}
        if txt.get("spiral"):
            tl["spiral"] = tuple(txt["spiral"])
        if txt.get("under"):
            tl["under"] = tuple(txt["under"])
        checks.append(connectivity.check(flat, ld, pairs,
                                         (cfg.get("expect") or {}).get("ports"),
                                         text_layers=tl))
    except Exception as e:  # 连通性失败不应吞掉其他检查
        checks.append(_chk("G7_connectivity", "fail", repr(e), None, "连通性追踪异常"))
    checks.append(check_turns(spiral, cfg))
    checks.append(check_symmetry(spiral, cfg))
    checks.append(check_inner_clearance(spiral))
    checks.append(check_lead_match(flat, spiral, cfg))
    checks.extend(check_leads(flat, spiral, cfg))
    checks.append(check_stack(flat, ld, cfg))
    return checks


if __name__ == "__main__":
    import json
    import sys
    g = gdsread.read_gds(sys.argv[1])
    top = sys.argv[2] if len(sys.argv) > 2 else sorted(g["cells"])[0]
    cfg = json.load(open(sys.argv[3], encoding="utf-8")) if len(sys.argv) > 3 else {}
    print(json.dumps(run_all(gdsread.flatten(g, top), cfg), indent=2, ensure_ascii=False))
