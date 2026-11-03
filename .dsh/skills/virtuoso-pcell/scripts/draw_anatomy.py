# -*- coding: utf-8 -*-
"""draw_anatomy.py - 按真实层序画 sym 电感解剖图：①叠层 ②仅 TM1 ③仅 TM2。

用法：
  python dump_geo.py -o geo.json                 # 先拿到 geo.json（带 w/s/n/ct）
  python draw_anatomy.py geo.json anatomy.png

要点（与 references/visualization.md 一致）：
- 层序：TM1 先画（最底）→ TM2 → via
- metal 存的是【中心线】，必须按线宽 w 展开成带；under_poly 才是多边形
- 叠层图里 TM2 必然盖住 TM1，这是图层序不是几何错 —— 所以必须另给 TM1 / TM2 单层图
- 所有数字从 geo.json 读，不要抄死参数（抄死就会在改参数后画错）
"""
import json
import math
import sys

from PIL import Image, ImageDraw, ImageFont

GEO = sys.argv[1] if len(sys.argv) > 1 else "geo.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "anatomy.png"

with open(GEO, encoding="utf-8") as fh:
    g = json.load(fh)

metal = g["metal"]
under = g["under"]
upoly = g.get("under_poly") or []
vias = g["vias"]
ports = g["ports"]
bbox = g["bbox"]
N = g.get("N", 0)
W = float(g.get("w", 8.0))
S = float(g.get("s", 3.0))
VIA = g.get("via") or {}
CUT = float(VIA.get("cut", 0.5))
SP = float(VIA.get("spacing", 0.5))
ENCL = float(VIA.get("enclosure", 0.2))

C_TM1, C_TM1L, C_TM2, C_TM2L = "#cfe0f0", "#3f6d9e", "#2f5d8f", "#173a5c"
C_VIA, C_VIAE, C_VIAF = "#f2c744", "#8a6410", "#7a5405"


def font(sz, bold=False):
    p = r"C:/Windows/Fonts/msyhbd.ttc" if bold else r"C:/Windows/Fonts/msyh.ttc"
    try:
        return ImageFont.truetype(p, sz)
    except Exception:
        return ImageFont.load_default()


F12, F14, F17, F26B = font(12), font(14), font(17), font(26, True)


def panel(px_w, px_h, world, scale, layers, border=True):
    x0, y0, x1, y1 = world
    im = Image.new("RGB", (px_w, px_h), "#ffffff")
    d = ImageDraw.Draw(im)
    ox = (px_w - (x1 - x0) * scale) / 2.0
    oy = (px_h - (y1 - y0) * scale) / 2.0
    T = lambda x, y: (ox + (x - x0) * scale, oy + (y1 - y) * scale)

    def dashed(p1, p2, fill, width=1, dash=6, gap=5):
        (ax, ay), (bx, by) = p1, p2
        L = math.hypot(bx - ax, by - ay)
        if L == 0:
            return
        ux, uy = (bx - ax) / L, (by - ay) / L
        t = 0.0
        while t < L:
            e = min(t + dash, L)
            d.line([(ax + ux * t, ay + uy * t), (ax + ux * e, ay + uy * e)],
                   fill=fill, width=width)
            t = e + gap

    if "tm1" in layers:
        for poly in upoly:
            d.polygon([T(x, y) for x, y in poly], fill=C_TM1)
        for poly in upoly:
            pts = [T(x, y) for x, y in poly]
            for i in range(len(pts)):
                d.line([pts[i], pts[(i + 1) % len(pts)]], fill=C_TM1L, width=2)
        for line in under:
            for i in range(len(line) - 1):
                dashed(T(*line[i]), T(*line[i + 1]), C_TM1L, 1, 4, 4)
    if "tm2" in layers:
        wpx = max(3, int(round(W * scale)))
        for line in metal:
            d.line([T(x, y) for x, y in line], fill=C_TM2, width=wpx, joint="curve")
        if scale > 4:
            for line in metal:
                for i in range(len(line) - 1):
                    dashed(T(*line[i]), T(*line[i + 1]), C_TM2L, 1, 5, 5)
    if "via" in layers:
        # SKILL lriViaColStack(librf.il:1927): ONE column -- x is fixed, the odd
        # ncut cuts are stacked along y, h = (ncut-1)(cut+sp)/2.  The span in
        # geo.json is exactly ncut*cut + (ncut-1)*sp, so ncut is recovered from
        # it directly; forcing it odd is what keeps the column centred (E09).
        for (x, y, span) in vias:
            n = int(math.floor((span + SP) / (CUT + SP) + 1e-9))
            n = max(1, n)
            if n % 2 == 0:
                n -= 1
            n = max(1, n)
            for i in range(n):
                cy = y + (i - (n - 1) / 2.0) * (CUT + SP)
                p = T(x - CUT / 2, cy + CUT / 2)
                q = T(x + CUT / 2, cy - CUT / 2)
                if q[0] - p[0] < 3.0:          # keep single cuts visible
                    m = (3.0 - (q[0] - p[0])) / 2.0
                    p = (p[0] - m, p[1] - m)
                    q = (q[0] + m, q[1] + m)
                d.rectangle([p[0], p[1], q[0], q[1]], fill=C_VIAF,
                            outline=C_VIAE, width=1)
    if border:
        d.rectangle([0, 0, px_w - 1, px_h - 1], outline="#d5dde5", width=2)
    return im, d, T


WP, HP = 1760, 1170
img = Image.new("RGB", (WP, HP), "#ffffff")
D = ImageDraw.Draw(img)
p_pitch = W + S
xj = p_pitch / 2.0
e = max(0.0, math.sqrt(2.0) * (S + W / 2.0) - p_pitch + W / 2.0)
arm_end = max(abs(x) for line in metal for x, _y in line)
D.text((30, 16), "sym 对称差分电感解剖：一条臂 + 中心桥 + 差分引线", font=F26B, fill="#101820")
D.text((30, 54),
       "w=%.1f  s=%.1f  N=%d   →   p = w+s = %.2f   xj = p/2 = %.2f   "
       "e = (√2−1)(s+w/2) = %.3f   臂端 x = ±%.2f   bbox = (%.0f, %.0f, %.0f, %.0f)"
       % (W, S, N, p_pitch, xj, e, arm_end, bbox[0], bbox[1], bbox[2], bbox[3]),
       font=F14, fill="#31424f")
D.line([(30, 88), (WP - 30, 88)], fill="#d5dde5", width=2)

WLD = (bbox[0] - 10.0, bbox[1] - 8.0, bbox[2] + 10.0, bbox[3] + 14.0)
PANEL_X, PANEL_Y = 26, 100
A_IM, _, TA = panel(1070, 850, WLD, 5.3, ["tm1", "tm2", "via"])
img.paste(A_IM, (PANEL_X, PANEL_Y))
D.text((36, 108), "① 真实叠层（TM1 → TM2 → via）", font=F17, fill="#101820")
for name, (px, py) in ports:
    ax, ay = TA(px, py)
    a = (ax + PANEL_X, ay + PANEL_Y)          # 面板内坐标 -> 整图坐标
    right = px >= 0
    lx = a[0] + (16 if right else -16)
    D.ellipse([a[0] - 5, a[1] - 5, a[0] + 5, a[1] + 5], fill="#ffffff",
              outline="#c0392b", width=2)
    D.text((lx, a[1]), "%s (%.1f, %g)" % (name, px, py), font=F12,
           fill="#c0392b", anchor=("lm" if right else "rm"))

BW, BH, SC = 620, 400, 2.45
B_IM, _, _ = panel(BW, BH, WLD, SC, ["tm1"])
img.paste(B_IM, (1128, 152))
D.text((1136, 122), "② 只画 TM1：跨圈靠它（underpass 扫掠面）", font=F14, fill="#101820")
C_IM, _, _ = panel(BW, BH, WLD, SC, ["tm2"])
img.paste(C_IM, (1128, 640))
D.text((1136, 610), "③ 只画 TM2：metal 存中心线，带宽 w", font=F14, fill="#101820")

ly = 980
x = 30
for lab, col in [("TM2  主金属（中心线折线，带宽 w=%.1f）" % W, C_TM2),
                 ("TM1  underpass（扫掠多边形）", C_TM1),
                 ("via 一列奇数 cut（x 固定，沿 y 排布）", C_VIA)]:
    D.rectangle([x, ly, x + 26, ly + 14], fill=col, outline="#4a5a68")
    D.text((x + 34, ly - 3), lab, font=F14, fill="#22303c")
    x += 400
D.text((30, ly + 36),
       "判图规则：叠层图里 TM2 必然盖住 TM1，看着像 underpass 断了 —— 这是图层序，不是几何错。"
       "要判断层间关系，切到 ② 或直接量顶点距离。", font=F14, fill="#8a3b12")
D.text((30, ly + 66),
       "另一条：① 里的 X 交叉点在 x=0，两臂在此换层。图上断开的几处正是 via 落点，不是漏画。",
       font=F14, fill="#8a3b12")
sx, sy = 1592, 66
D.line([(sx, sy), (sx + 10 * 5.3, sy)], fill="#101820", width=3)
for dx in (0, 10 * 5.3):
    D.line([(sx + dx, sy - 6), (sx + dx, sy + 6)], fill="#101820", width=3)
D.text((sx + 10 * 5.3 + 10, sy - 9), "10 µm", font=F14, fill="#101820")

img.save(OUT)
print("saved", OUT, img.size)