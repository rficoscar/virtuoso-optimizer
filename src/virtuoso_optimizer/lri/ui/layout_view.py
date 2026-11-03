# -*- coding: utf-8 -*-
"""lri.ui.layout_view -- interactive layout preview for the sym inductor.

Why this module exists
----------------------
The layout page used to draw the device into a fixed 280x210 tk.Canvas with a
fit-to-canvas scale of roughly 1.5 px/um.  On the factory default (rx=80 ry=60
w=8 s=3) that means an 8 um lead is 12 px wide, the 4x4 um notch at the port is
6 px, and there is no zoom, no pan, no export.  The layer order was inverted as
well: the TM1 underpass polygons were painted *over* the TM2 spiral, so the
turn-change landing pads hid the metal the via column actually connects to.

This module redraws the same geometry as vectors at the current zoom, in the
real layer order (TM1 below TM2, via cuts on top), with wheel zoom about the
cursor, drag panning, fit-to-window and PNG export.

Two renderers share one transform, so the on-screen view and the exported image
can never disagree:

  * draw_on_canvas(c, geo, layers, xf, ...) -- tkinter primitives, no deps;
  * render_image(geo, w, ...)               -- PIL, export only (Pillow is
    optional in this project; without it the view falls back to PostScript).

Nothing here imports tkinter or PIL at module import time: the package has to
stay importable from a headless CLI.
"""

import math
import os

from ..layout import (
    SYM_VIA_CUT, SYM_VIA_ENCL, SYM_VIA_SPACE,
    lri_i3_via_span, lri_sweep)

# ---------------------------------------------------------------- layers

# One palette for both renderers; keys are layer roles, not Tk item types.
LAYER_STYLE = {
    "tm1": {"fill": "#cfe0f2", "line": "#7aa2c8"},   # underpass / landing pad
    "tm2": {"fill": "#3d6fa5", "line": "#24476b"},   # spiral (main metal)
    "cut": {"fill": "#ffe08a", "line": "#b0913c"},   # via cut (single column)
    "bbox": "#b4b4b4",
    "port": "#c0392b",
    "grid": "#eaeef2",
    "bg": "#ffffff",
    "text": "#444444",
}

MIN_ZOOM = 0.2          # px/um
MAX_ZOOM = 160.0        # px/um -- a 0.5 um cut is 80 px wide here
FIT_MARGIN_PX = 16.0

# ---------------------------------------------------------------- geometry helpers


def geom_extent(geo):
    """World (x0, y0, x1, y1) covering the metal, the ports and the via spans."""
    x0, y0, x1, y1 = geo["bbox"]
    xs = [x0, x1]
    ys = [y0, y1]
    for _name, pt in (geo.get("ports") or []):
        xs.append(pt[0])
        ys.append(pt[1])
    for poly in (geo.get("under_poly") or []):
        for q in poly:
            xs.append(q[0])
            ys.append(q[1])
    for vx, vy, span in (geo.get("vias") or []):
        xs.append(vx)
        ys.append(vy + span / 2.0)
        ys.append(vy - span / 2.0)
    return (min(xs), min(ys), max(xs), max(ys))


def layout_layers(geo, w):
    """Resolve the geometry dict into drawable polygons, in paint order.

    tm1 holds the already-swept underpass polygons (under_poly); tm2 is the
    equal-width sweep of every main-metal centreline; cut holds one square per
    via cut, derived from the same span = n*cut + (n-1)*sp rule the kernel uses
    for a single column of cuts.
    """
    cut, sp = SYM_VIA_CUT, SYM_VIA_SPACE
    pitch = cut + sp
    tm2 = [lri_sweep(pts, w) for pts in (geo.get("metal") or [])]
    tm1 = [list(pp) for pp in (geo.get("under_poly") or []) if len(pp) >= 3]
    cuts = []
    ncut = 0
    spans = []
    for vx, vy, span in (geo.get("vias") or []):
        n = int(round((span + sp) / pitch))
        if n < 1:
            n = 1
        if not ncut:
            ncut = n
        spans.append(span)
        top = vy + span / 2.0
        for i in range(n):
            yc = top - cut / 2.0 - i * pitch
            cuts.append((vx - cut / 2.0, yc - cut / 2.0,
                         vx + cut / 2.0, yc + cut / 2.0))
    return {"tm1": tm1, "tm2": tm2, "cut": cuts, "ncut": ncut,
            "via_span": spans[0] if spans else 0.0}


def params_summary(geo, params):
    """One-line geometry summary for the status bar and the exported image."""
    params = params or {}

    def _num(key):
        try:
            return float(params.get(key))
        except (TypeError, ValueError):
            return 0.0

    w = _num("w")
    s = _num("s")
    n = params.get("n", "")
    span, ncut = lri_i3_via_span(w, SYM_VIA_CUT, SYM_VIA_SPACE, SYM_VIA_ENCL) \
        if w else (0.0, 0)
    bx = float(geo.get("bx") or 0.0)
    by = float(geo.get("by") or 0.0)
    n_txt = ("%g" % n) if isinstance(n, (int, float)) else str(n or "?")
    return ("%g x %g um  |  n=%s  w=%g  s=%g  |  via %d cut x %.2f um"
            % (bx, by, n_txt, w, s, ncut, span))


class LayoutTransform(object):
    """World micrometres -> device pixels, y flipped (screen convention)."""

    def __init__(self, width_px, height_px, zoom, center):
        self.w = float(width_px)
        self.h = float(height_px)
        self.zoom = float(zoom)
        self.cx, self.cy = float(center[0]), float(center[1])

    def xy(self, pt):
        return (self.w / 2.0 + (pt[0] - self.cx) * self.zoom,
                self.h / 2.0 - (pt[1] - self.cy) * self.zoom)

    def flat(self, pts):
        out = []
        for pt in pts:
            x, y = self.xy(pt)
            out.extend((x, y))
        return out

    def world_at(self, px, py):
        return (self.cx + (px - self.w / 2.0) / self.zoom,
                self.cy - (py - self.h / 2.0) / self.zoom)

    def visible_rect(self):
        x0, y0 = self.world_at(0.0, self.h)
        x1, y1 = self.world_at(self.w, 0.0)
        return (x0, y0, x1, y1)

    def rect_xy(self, x0, y0, x1, y1):
        ax, ay = self.xy((x0, y1))
        bx, by = self.xy((x1, y0))
        return (ax, ay, bx, by)


def clamp_zoom(zoom):
    return max(MIN_ZOOM, min(MAX_ZOOM, float(zoom)))


def fit_transform(extent, width_px, height_px, margin_px=FIT_MARGIN_PX):
    x0, y0, x1, y1 = extent
    ew = max(x1 - x0, 1e-6)
    eh = max(y1 - y0, 1e-6)
    zoom = min(max(width_px - 2.0 * margin_px, 8.0) / ew,
               max(height_px - 2.0 * margin_px, 8.0) / eh)
    zoom = clamp_zoom(zoom)
    return LayoutTransform(width_px, height_px, zoom,
                           ((x0 + x1) / 2.0, (y0 + y1) / 2.0))


def _nice_bar_length(zoom, target_px=120.0):
    """Pick a 1/2/5-series length whose on-screen bar is close to target_px."""
    want = target_px / max(zoom, 1e-9)
    for scale in (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0):
        for mult in (1.0, 2.0, 5.0):
            cand = mult * scale
            if cand >= want:
                return cand
    return 1000.0


def _grid_step(zoom):
    """Grid spacing in um that stays >= 10 px on screen (None = no grid)."""
    for step in (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0):
        if step * zoom >= 10.0:
            return step
    return None


# ---------------------------------------------------------------- tkinter renderer


def draw_on_canvas(c, geo, layers, xf, show_grid=True, show_ports=True,
                   show_scale=True, bg=LAYER_STYLE["bg"]):
    """Paint one frame with tkinter primitives (no PIL, no image scaling)."""
    c.configure(background=bg)
    c.delete("all")
    style = LAYER_STYLE

    if show_grid:
        step = _grid_step(xf.zoom)
        if step:
            vx0, vy0, vx1, vy1 = xf.visible_rect()
            gx = math.ceil(vx0 / step) * step
            while gx <= vx1:
                x, _y = xf.xy((gx, 0.0))
                c.create_line(x, 0, x, xf.h, fill=style["grid"], width=1)
                gx += step
            gy = math.ceil(vy0 / step) * step
            while gy <= vy1:
                _x, y = xf.xy((0.0, gy))
                c.create_line(0, y, xf.w, y, fill=style["grid"], width=1)
                gy += step

    for poly in layers["tm1"]:
        if len(poly) >= 3:
            c.create_polygon(xf.flat(poly), fill=style["tm1"]["fill"],
                             outline=style["tm1"]["line"], width=1)
    for poly in layers["tm2"]:
        if len(poly) >= 3:
            c.create_polygon(xf.flat(poly), fill=style["tm2"]["fill"],
                             outline=style["tm2"]["line"], width=1)
    for x0, y0, x1, y1 in layers["cut"]:
        a, b, d, e = xf.rect_xy(x0, y0, x1, y1)
        c.create_rectangle(a, b, d, e, fill=style["cut"]["fill"],
                           outline=style["cut"]["line"], width=1)

    # bbox: the size contract of the cell, so it stays visible at every zoom
    a, b, d, e = xf.rect_xy(*geo["bbox"])
    c.create_rectangle(a, b, d, e, outline=style["bbox"], width=1, dash=(4, 4))

    if show_ports:
        for name, pt in (geo.get("ports") or []):
            x, y = xf.xy(pt)
            r = 3.5
            c.create_oval(x - r, y - r, x + r, y + r,
                          fill=style["port"], outline=style["port"])
            if name == "ct1":
                c.create_text(x + 7, y, text=name, anchor="w",
                              fill=style["port"], font=("TkFixedFont", 8))
            else:
                c.create_text(x + 6, y - 6, text=name, anchor="sw",
                              fill=style["port"], font=("TkFixedFont", 8))

    if show_scale:
        length = _nice_bar_length(xf.zoom)
        px = length * xf.zoom
        x0, y1 = 14.0, xf.h - 16.0
        x2 = x0 + px
        c.create_line(x0, y1, x2, y1, fill=style["text"], width=2)
        c.create_line(x0, y1 - 4, x0, y1 + 4, fill=style["text"], width=2)
        c.create_line(x2, y1 - 4, x2, y1 + 4, fill=style["text"], width=2)
        label = ("%g um" % length) if length >= 1.0 else ("%.1f um" % length)
        c.create_text(x0, y1 - 8, text=label, anchor="sw",
                      fill=style["text"], font=("TkFixedFont", 8))


# ---------------------------------------------------------------- PIL renderer


def render_image(geo, w, params=None, width_px=2400, title=None,
                 supersample=3, show_ports=True, show_scale=True):
    """Rasterise the full device to a PIL image (PNG export / batch figures)."""
    from PIL import Image, ImageDraw

    extent = geom_extent(geo)
    ew = max(extent[2] - extent[0], 1e-6)
    eh = max(extent[3] - extent[1], 1e-6)
    header = 46.0 if (title or params) else 22.0
    inner_w = float(width_px)
    inner_h = inner_w * (eh / ew)
    height_px = int(round(inner_h)) + int(header) + 34

    ss = max(1, int(supersample))
    big = Image.new("RGB", (int(inner_w * ss), int(inner_h * ss)),
                    LAYER_STYLE["bg"])
    d = ImageDraw.Draw(big)
    xf = fit_transform(extent, inner_w * ss, inner_h * ss, margin_px=18.0 * ss)
    layers = layout_layers(geo, w)

    def polygon(pts, fill, outline, width=1):
        if len(pts) < 3:
            return
        d.polygon(xf.flat(pts), fill=fill)
        if outline:
            closed = list(pts) + [pts[0]]
            d.line(xf.flat(closed), fill=outline, width=width * ss)

    for poly in layers["tm1"]:
        polygon(poly, LAYER_STYLE["tm1"]["fill"], LAYER_STYLE["tm1"]["line"])
    for poly in layers["tm2"]:
        polygon(poly, LAYER_STYLE["tm2"]["fill"], LAYER_STYLE["tm2"]["line"])
    for x0, y0, x1, y1 in layers["cut"]:
        a, b, cc, dd = xf.rect_xy(x0, y0, x1, y1)
        d.rectangle((a, b, cc, dd), fill=LAYER_STYLE["cut"]["fill"],
                    outline=LAYER_STYLE["cut"]["line"],
                    width=max(1, ss // 2))
    a, b, cc, dd = xf.rect_xy(*geo["bbox"])
    _dashed_rect(d, (a, b, cc, dd), LAYER_STYLE["bbox"], width=ss)

    img = big.resize((int(inner_w), int(inner_h)), Image.LANCZOS)
    out = Image.new("RGB", (int(inner_w), int(height_px)), LAYER_STYLE["bg"])
    out.paste(img, (0, int(header)))
    d2 = ImageDraw.Draw(out)
    font_small = _load_font(15)
    font_bold = _load_font(17)

    xf2 = fit_transform(extent, inner_w, inner_h, margin_px=18.0)
    if show_ports:
        for name, pt in (geo.get("ports") or []):
            x, y = xf2.xy(pt)
            y += header
            r = 4.0
            d2.ellipse((x - r, y - r, x + r, y + r),
                       fill=LAYER_STYLE["port"])
            if name == "ct1":
                d2.text((x + 8, y), name, fill=LAYER_STYLE["port"],
                        font=font_small, anchor="lm")
            else:
                d2.text((x + 7, y - 6), name, fill=LAYER_STYLE["port"],
                        font=font_small, anchor="ls")
    if show_scale:
        length = _nice_bar_length(xf2.zoom, target_px=150.0)
        px = length * xf2.zoom
        x0 = 24.0
        y0 = float(height_px - 16)
        d2.line((x0, y0, x0 + px, y0), fill=LAYER_STYLE["text"], width=2)
        d2.line((x0, y0 - 5, x0, y0 + 5), fill=LAYER_STYLE["text"], width=2)
        d2.line((x0 + px, y0 - 5, x0 + px, y0 + 5),
                fill=LAYER_STYLE["text"], width=2)
        label = ("%g um" % length) if length >= 1 else ("%.1f um" % length)
        d2.text((x0, y0 - 9), label, fill=LAYER_STYLE["text"],
                font=font_small, anchor="ls")
    if title:
        d2.text((24.0, 12.0), title, fill="#1f2b38", font=font_bold)
    if params is not None:
        d2.text((24.0, 32.0 if title else 6.0), params_summary(geo, params),
                fill="#5b6b7b", font=font_small)
    return out


def _dashed_rect(d, box, colour, width=1, dash=10, gap=6):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1),
                             (x1, y1, x0, y1), (x0, y1, x0, y0)):
        length = math.hypot(bx - ax, by - ay)
        if length <= 1e-9:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        pos = 0.0
        while pos < length:
            end = min(pos + dash, length)
            d.line((ax + ux * pos, ay + uy * pos,
                    ax + ux * end, ay + uy * end), fill=colour, width=width)
            pos = end + gap


def _load_font(size):
    from PIL import ImageFont
    names = ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf")
    roots = []
    if os.name == "nt":
        roots.append(os.path.join(os.environ.get("WINDIR", "C:\\Windows"),
                                  "Fonts"))
    roots.extend(("/usr/share/fonts/truetype/dejavu",
                  "/usr/share/fonts", "/System/Library/Fonts"))
    for root in roots:
        for name in names:
            path = os.path.join(root, name)
            try:
                if os.path.exists(path):
                    return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()

# ---------------------------------------------------------------- widget


class LayoutView(object):
    """Zoomable, pannable layout preview.

    Composition instead of inheritance keeps tkinter out of this module's
    import path: build the widgets with the caller's tk/ttk modules and pack
    .frame.  The canvas is exposed as .canvas so existing helpers that expect a
    tk.Canvas keep working.
    """

    def __init__(self, master, tk, ttk, height=430, width=440):
        self.tk = tk
        self.ttk = ttk
        self._geo = None
        self._params = None
        self._layers = None
        self._error = None
        self._zoom = None
        self._center = None
        self._drag = None
        self._after = None
        self._extent = None

        self.frame = ttk.Frame(master)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x", pady=(0, 2))
        self._btn_fit = ttk.Button(bar, text="适应窗口", width=9,
                                   command=self.fit)
        self._btn_fit.pack(side="left")
        ttk.Button(bar, text="+", width=3,
                   command=lambda: self.zoom_by(1.6)).pack(side="left",
                                                           padx=(4, 0))
        ttk.Button(bar, text="-", width=3,
                   command=lambda: self.zoom_by(1.0 / 1.6)).pack(side="left",
                                                               padx=(2, 0))
        self._btn_export = ttk.Button(bar, text="导出 PNG...", width=11,
                                      command=self.export)
        self._btn_export.pack(side="left", padx=(8, 0))
        self.v_grid = tk.IntVar(value=1)
        ttk.Checkbutton(bar, text="网格", variable=self.v_grid,
                        command=self.redraw).pack(side="left", padx=(10, 0))
        self.v_ports = tk.IntVar(value=1)
        ttk.Checkbutton(bar, text="端口标注", variable=self.v_ports,
                        command=self.redraw).pack(side="left", padx=(4, 0))
        self._zoom_lbl = ttk.Label(bar, text="", anchor="e")
        self._zoom_lbl.pack(side="right")

        self.canvas = tk.Canvas(self.frame, height=height, width=width,
                                background=LAYER_STYLE["bg"],
                                highlightthickness=1,
                                highlightbackground="#c4c4c4")
        self.canvas.pack(fill="both", expand=True)
        self._status = ttk.Label(self.frame, anchor="w", text="—",
                                 foreground="#555555")
        self._status.pack(fill="x", pady=(2, 0))

        c = self.canvas
        c.bind("<Configure>", lambda _e: self._schedule())
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            c.bind(seq, self._on_wheel)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", lambda _e: self.fit())

    # ---- data plumbing ----

    def show(self, geo, params=None):
        """Display a built geometry dict; keeps the current zoom and pan."""
        self._geo = geo
        self._params = params or {}
        self._error = None
        try:
            w = float((params or {}).get("w") or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        self._layers = layout_layers(geo, w)
        self._extent = geom_extent(geo)
        self.redraw()

    def show_error(self, message):
        """Display a validation/build failure instead of a geometry."""
        self._error = str(message or "参数不可行")
        self._geo = None
        self._layers = None
        self.redraw()

    def has_geometry(self):
        return self._geo is not None

    # ---- view control ----

    def fit(self):
        self._zoom = None
        self._center = None
        self.redraw()

    def zoom_by(self, factor):
        self._zoom_at(factor, self._size()[0] / 2.0, self._size()[1] / 2.0)

    def _zoom_at(self, factor, px, py):
        if self._geo is None:
            return
        wpx, hpx = self._size()
        xf = self._transform(wpx, hpx)
        anchor = xf.world_at(px, py)
        zoom = clamp_zoom(xf.zoom * factor)
        cx = anchor[0] - (px - wpx / 2.0) / zoom
        cy = anchor[1] + (py - hpx / 2.0) / zoom
        self._zoom = zoom
        self._center = (cx, cy)
        self._clamp_center(wpx, hpx)
        self.redraw()

    def _size(self):
        c = self.canvas
        try:
            wpx = int(c.winfo_width())
            hpx = int(c.winfo_height())
        except Exception:
            wpx, hpx = 0, 0
        if wpx <= 1:
            wpx = int(c.winfo_reqwidth()) or 440
        if hpx <= 1:
            hpx = int(c.winfo_reqheight()) or 430
        return (max(wpx, 40), max(hpx, 40))

    def _transform(self, wpx, hpx):
        if self._extent is None:
            return LayoutTransform(wpx, hpx, 1.0, (0.0, 0.0))
        if self._zoom is None or self._center is None:
            base = fit_transform(self._extent, wpx, hpx)
            self._zoom = base.zoom
            self._center = (base.cx, base.cy)
            return base
        return LayoutTransform(wpx, hpx, self._zoom, self._center)

    def _clamp_center(self, wpx, hpx):
        """Keep at least 20% of the device visible so panning cannot lose it."""
        if self._extent is None or self._zoom is None:
            return
        x0, y0, x1, y1 = self._extent
        ew, eh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        half_w = wpx / (2.0 * self._zoom)
        half_h = hpx / (2.0 * self._zoom)
        cx, cy = self._center
        lo, hi = x0 - half_w + 0.2 * ew, x1 + half_w - 0.2 * ew
        cx = (lo + hi) / 2.0 if lo > hi else min(max(cx, lo), hi)
        lo, hi = y0 - half_h + 0.2 * eh, y1 + half_h - 0.2 * eh
        cy = (lo + hi) / 2.0 if lo > hi else min(max(cy, lo), hi)
        self._center = (cx, cy)

    # ---- events ----

    def _on_wheel(self, event):
        if self._geo is None:
            return
        if getattr(event, "num", None) == 4:
            delta = 1
        elif getattr(event, "num", None) == 5:
            delta = -1
        else:
            delta = float(event.delta) / 120.0
        if not delta:
            return
        self._zoom_at(1.15 ** delta, event.x, event.y)

    def _on_press(self, event):
        self._drag = (event.x, event.y, self._transform(*self._size()))
        self.canvas.configure(cursor="fleur")

    def _on_drag(self, event):
        if self._drag is None or self._geo is None:
            return
        x0, y0, xf = self._drag
        dx = event.x - x0
        dy = event.y - y0
        self._zoom = xf.zoom
        self._center = (xf.cx - dx / xf.zoom, xf.cy + dy / xf.zoom)
        self._clamp_center(*self._size())
        self.redraw()

    def _on_release(self, _event):
        self._drag = None
        self.canvas.configure(cursor="")

    # ---- drawing ----

    def _schedule(self):
        try:
            if self._after is not None:
                self.canvas.after_cancel(self._after)
        except Exception:
            pass
        try:
            self._after = self.canvas.after(30, self.redraw)
        except Exception:
            self._after = None

    def redraw(self):
        """Repaint the canvas; safe to call from any binding."""
        self._after = None
        c = self.canvas
        wpx, hpx = self._size()
        if self._error is not None or self._geo is None:
            try:
                c.delete("all")
            except Exception:
                return
            msg = self._error or "尚无几何"
            try:
                c.create_text(wpx / 2.0, hpx / 2.0 - 8, text="参数不可行",
                              fill="#a03030", font=("TkDefaultFont", 10))
                c.create_text(wpx / 2.0, hpx / 2.0 + 12, text=msg[:120],
                              fill="#a03030", font=("TkDefaultFont", 9),
                              width=max(wpx - 40.0, 80.0), justify="center")
                self._zoom_lbl.configure(text="")
            except Exception:
                pass
            self._status.configure(text=str(msg), foreground="#a03030")
            return

        self._transform(wpx, hpx)
        self._clamp_center(wpx, hpx)
        xf = LayoutTransform(wpx, hpx, self._zoom, self._center)
        draw_on_canvas(c, self._geo, self._layers, xf,
                       show_grid=bool(self.v_grid.get()),
                       show_ports=bool(self.v_ports.get()))
        self._zoom_lbl.configure(text="%.2f px/um" % xf.zoom)
        text = params_summary(self._geo, self._params)
        try:
            ok, msg = _validate_for_status(self._params)
        except Exception:
            ok, msg = True, ""
        self._status.configure(text=text + ("  |  " + msg if msg else ""),
                               foreground="#2c5f2d" if ok else "#a03030")

    # ---- export ----

    def export(self, path=None):
        """Write the whole device to PNG (Pillow) or PostScript (Tk only)."""
        if self._geo is None:
            return None
        if path is None:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                parent=self.frame, title="导出版图图片",
                defaultextension=".png",
                filetypes=[("PNG 图片", "*.png"), ("所有文件", "*")])
            if not path:
                return None
        try:
            img = render_image(self._geo,
                               float(self._params.get("w") or 0.0),
                               params=self._params, width_px=2600,
                               title="sym differential inductor")
        except Exception as exc:
            self._status.configure(
                text="Pillow 不可用（%s），改为导出 PostScript" % str(exc)[:60],
                foreground="#8a6d00")
            try:
                ps = path[:-4] + ".ps" if path.lower().endswith(".png") \
                    else path + ".ps"
                self.canvas.postscript(file=ps, colormode="color")
                return ps
            except Exception as exc2:
                self._status.configure(text="导出失败: %s" % str(exc2)[:80],
                                       foreground="#a03030")
                return None
        try:
            img.save(path)
        except Exception as exc:
            self._status.configure(text="保存失败: %s" % str(exc)[:80],
                                   foreground="#a03030")
            return None
        self._status.configure(
            text="已导出 %s（%d x %d）" % (path, img.size[0], img.size[1]),
            foreground="#2c5f2d")
        return path


def _validate_for_status(params):
    """Validity note for the status bar (params come straight from the form)."""
    if not params:
        return (True, "")
    from ..layout import lri_validate
    ok, msg = lri_validate(dict(params))
    return (bool(ok), ("DRC 通过" if ok else str(msg)[:90]))
