# -*- coding: utf-8 -*-
"""lri.ui.plots — 曲线画布与配色/数值格式工具。"""
from __future__ import print_function

import math

_VAR_PALETTE = ("#c23b22", "#2266cc", "#2a9d3a", "#d17a00",
                "#7a3cb0", "#00a0a0", "#d4368c", "#5a5a5a")

_AGE_OLD_RGB = (0xCC, 0xCC, 0xCC)

_AGE_NEW_RGB = (0x00, 0x55, 0xCC)

_MARGIN_LEFT, _MARGIN_RIGHT, _MARGIN_TOP, _MARGIN_BOTTOM = 58, 16, 36, 34

_MIN_PLOT = 40

def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)

def _lerp(a, b, t):
    return a + (b - a) * t

def _age_color(t):
    t = _clamp(float(t), 0.0, 1.0)
    rgb = [int(round(_lerp(a, b, t))) for a, b in zip(_AGE_OLD_RGB, _AGE_NEW_RGB)]
    return "#%02x%02x%02x" % tuple(rgb)

def _format_num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(v):
        return str(v)
    av = abs(v)
    if av == 0.0:
        return "0"
    if av >= 1e5 or av < 1e-3:
        return "%.2e" % v
    if av >= 100:
        return "%.1f" % v
    if av >= 1:
        return "%.3g" % v
    return "%.4g" % v

class PlotCanvas:
    def __init__(self, parent, log_y=False, font=None, small_font=None, empty_text="无数据", **kw):
        import tkinter as tk
        kw.setdefault("background", "white")
        self.canvas = tk.Canvas(parent, **kw)
        self.font = font or ("TkDefaultFont", 10)
        self.small_font = small_font or ("TkDefaultFont", 8)
        self.log_y = log_y
        self.empty_text = empty_text
        self.series = []
        self.title = self.xlabel = self.ylabel = ""
        self.legend = False
        self._view = self._bounds = self._press = self._box_start = None
        c = self.canvas
        c.bind("<Configure>", lambda _e: self.redraw())
        c.bind("<Leave>", lambda _e: self._clear_cross())
        c.bind("<Double-Button-1>", self._on_double)
        for seq, cb in (("<Motion>", self._on_motion),
                        ("<ButtonPress-1>", self._on_press1),
                        ("<B1-Motion>", self._on_drag1),
                        ("<ButtonRelease-1>", self._on_release1),
                        ("<ButtonPress-3>", self._on_press3),
                        ("<B3-Motion>", self._on_drag3),
                        ("<MouseWheel>", self._on_wheel),
                        ("<Button-4>", self._on_wheel_up),
                        ("<Button-5>", self._on_wheel_down)):
            c.bind(seq, cb)
    def __getattr__(self, name):
        return getattr(self.canvas, name)
    def show(self, series, title="", xlabel="", ylabel="", legend=False,
             keep_view=False, empty_text=None):
        self.series = [s for s in series if s.get("xs") and s.get("ys")]
        self.title, self.xlabel, self.ylabel = title, xlabel, ylabel
        self.legend = legend
        if empty_text is not None:
            self.empty_text = empty_text
        self._bounds = self._data_bounds()
        if not keep_view:
            self._view = None
        self.redraw()
    def _ty(self, y):
        return math.log10(max(float(y), 1e-12)) if self.log_y else float(y)
    def _data_bounds(self):
        xs, ys = [], []
        for s in self.series:
            for v in s.get("xs", ()):
                try:
                    xs.append(float(v))
                except (TypeError, ValueError):
                    pass
            for v in s.get("ys", ()):
                try:
                    ys.append(self._ty(v))
                except (TypeError, ValueError):
                    pass
        if not xs or not ys:
            return None
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if x1 - x0 < 1e-30:
            x1 = x0 + 1.0
        if y1 - y0 < 1e-30:
            y0, y1 = y0 - 0.5, y1 + 0.5
        return (x0, x1, y0, y1)
    def _view_bounds(self):
        return self._view if self._view is not None else self._bounds
    def _rect(self):
        w = max(int(self.canvas.winfo_width()), 2)
        h = max(int(self.canvas.winfo_height()), 2)
        l, t = _MARGIN_LEFT, _MARGIN_TOP
        r = max(l + _MIN_PLOT, w - _MARGIN_RIGHT)
        b = max(t + _MIN_PLOT, h - _MARGIN_BOTTOM)
        return l, t, r, b
    def _to_px(self, x, y, vb, rect):
        x0, x1, y0, y1 = vb
        l, t, r, b = rect
        return (l + (float(x) - x0) / ((x1 - x0) or 1.0) * (r - l),
                b - (self._ty(y) - y0) / ((y1 - y0) or 1.0) * (b - t))
    def _to_data(self, px, py, vb, rect):
        x0, x1, y0, y1 = vb
        l, t, r, b = rect
        return (x0 + (px - l) / ((r - l) or 1.0) * (x1 - x0),
                y0 + (b - py) / ((b - t) or 1.0) * (y1 - y0))
    @staticmethod
    def _nice_ticks(lo, hi, target=5):
        if not (hi > lo):
            hi = lo + 1.0
        raw = (hi - lo) / max(int(target), 1)
        if raw <= 0:
            return [lo, hi]
        mag = 10.0 ** math.floor(math.log10(raw))
        step = next((m * mag for m in (1.0, 2.0, 5.0, 10.0) if raw <= m * mag), mag)
        ticks = []
        t = math.floor(lo / step) * step
        for _guard in range(64):
            t2 = t + step
            if t2 <= t:
                break
            if t >= lo - step * 0.5 and t <= hi + step * 0.5:
                ticks.append(t)
            t = t2
            if t > hi + step * 0.5:
                break
        return ticks or [lo, hi]
    def _y_ticks(self, y0, y1):
        if not self.log_y:
            return self._nice_ticks(y0, y1)
        lo, hi = int(math.floor(y0)), int(math.ceil(y1))
        if hi <= lo:
            hi = lo + 1
        step = max(1, int((hi - lo) / 5) + 1)
        return [float(v) for v in range(lo, hi + 1, step)]
    def redraw(self):
        c = self.canvas
        c.delete("all")
        vb, rect = self._view_bounds(), self._rect()
        if vb is None or not self.series:
            w = max(int(c.winfo_width()), 2)
            h = max(int(c.winfo_height()), 2)
            c.create_text(w / 2.0, h / 2.0, text=self.empty_text, fill="#888888", font=self.font)
            return
        x0, x1, y0, y1 = vb
        l, t, r, b = rect
        for xt in self._nice_ticks(x0, x1):
            if not (x0 <= xt <= x1):
                continue
            px = l + (xt - x0) / ((x1 - x0) or 1.0) * (r - l)
            c.create_line(px, t, px, b, fill="#e2e2e2")
            c.create_line(px, b, px, b + 4, fill="#444444")
            c.create_text(px, b + 6, anchor="n", text=_format_num(xt), fill="#444444", font=self.small_font)
        for yt in self._y_ticks(y0, y1):
            if not (y0 <= yt <= y1):
                continue
            py = b - (yt - y0) / ((y1 - y0) or 1.0) * (b - t)
            c.create_line(l, py, r, py, fill="#e2e2e2")
            c.create_line(l - 4, py, l, py, fill="#444444")
            label = _format_num(10.0 ** yt) if self.log_y else _format_num(yt)
            c.create_text(l - 6, py, anchor="e", text=label, fill="#444444", font=self.small_font)
        c.create_line(l, t, l, b, fill="#444444")
        c.create_line(l, b, r, b, fill="#444444")
        if self.title:
            c.create_text((l + r) / 2.0, 6, anchor="n", text=self.title, fill="#333333", font=self.font)
        if self.xlabel:
            c.create_text((l + r) / 2.0, b + 22, anchor="n", text=self.xlabel, fill="#666666", font=self.small_font)
        if self.ylabel:
            c.create_text(l - 6, t - 2, anchor="sw", text=self.ylabel, fill="#666666", font=self.small_font)
        for s in self.series:
            color, width, dash = (s.get("color", "#1f77b4"), s.get("width", 1), s.get("dash"))
            pts = []
            for xv, yv in zip(s.get("xs", ()), s.get("ys", ())):
                try:
                    pts.append(self._to_px(float(xv), yv, vb, rect))
                except (TypeError, ValueError):
                    pass
            if len(pts) == 1:
                x, y = pts[0]
                c.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color)
            elif pts:
                flat = [v for xy in pts for v in xy]
                kw = {"fill": color, "width": width}
                if dash:
                    kw["dash"] = dash
                c.create_line(*flat, **kw)
        if self.legend:
            y = t + 8
            named = [s for s in self.series if s.get("name")]
            width = sum(28 + 8 * len(str(s.get("name"))) for s in named)
            x = max(l + 10, r - width - 10)
            for s in named:
                name = str(s.get("name"))
                color = s.get("color", "#1f77b4")
                c.create_rectangle(x, y - 4, x + 14, y + 4, fill=color, outline=color)
                c.create_text(x + 18, y, anchor="w", text=name, fill="#333333", font=self.small_font)
                x += 28 + 8 * len(name)
        c.create_line(0, 0, 0, 0, fill="#999999", dash=(2, 2), tags="cross")
        c.create_line(0, 0, 0, 0, fill="#999999", dash=(2, 2), tags="cross")
        c.create_text(0, 0, anchor="se", text="", fill="#333333", font=self.small_font, tags="crossinfo")
        c.itemconfigure("cross", state="hidden")
        c.itemconfigure("crossinfo", state="hidden")
    def _clear_cross(self):
        self.canvas.itemconfigure("cross", state="hidden")
        self.canvas.itemconfigure("crossinfo", state="hidden")
    def _on_motion(self, event):
        vb = self._view_bounds()
        if vb is None or not self.series:
            return
        rect = self._rect()
        l, t, r, b = rect
        if not (l <= event.x <= r and t <= event.y <= b):
            self._clear_cross()
            return
        dx, dy = self._to_data(event.x, event.y, vb, rect)
        ytxt = _format_num(10.0 ** dy) if self.log_y else _format_num(dy)
        items = self.canvas.find_withtag("cross")
        self.canvas.coords(items[0], event.x, t, event.x, b)
        self.canvas.coords(items[1], l, event.y, r, event.y)
        self.canvas.itemconfigure("cross", state="normal")
        infos = self.canvas.find_withtag("crossinfo")
        self.canvas.coords(infos[0], r - 4, t + 2)
        self.canvas.itemconfigure(infos[0], text="x=%s  y=%s" % (_format_num(dx), ytxt), state="normal")
    def _on_press1(self, event):
        self._box_start = (event.x, event.y)
    def _on_drag1(self, event):
        if self._box_start is None:
            return
        x0, y0 = self._box_start
        self.canvas.delete("zoombox")
        self.canvas.create_rectangle(x0, y0, event.x, event.y, outline="#1f77b4", dash=(2, 2), tags="zoombox")
    def _on_release1(self, event):
        start, self._box_start = self._box_start, None
        self.canvas.delete("zoombox")
        if start is None:
            return
        x0, y0 = start
        if abs(event.x - x0) < 6 or abs(event.y - y0) < 6:
            return
        vb = self._view_bounds()
        if vb is None:
            return
        rect = self._rect()
        xa, ya = self._to_data(min(x0, event.x), max(y0, event.y), vb, rect)
        xb, yb = self._to_data(max(x0, event.x), min(y0, event.y), vb, rect)
        if xb - xa < 1e-30 or yb - ya < 1e-30:
            return
        self._view = (xa, xb, ya, yb)
        self.redraw()
    def _on_press3(self, event):
        self._press = (event.x, event.y)
    def _on_drag3(self, event):
        vb = self._view_bounds()
        if vb is None or self._press is None:
            return
        x0, y0 = self._press
        self._press = (event.x, event.y)
        rect = self._rect()
        l, t, r, b = rect
        dx = (event.x - x0) / max(r - l, 1) * (vb[1] - vb[0])
        dy = (event.y - y0) / max(b - t, 1) * (vb[3] - vb[2])
        self._view = (vb[0] - dx, vb[1] - dx, vb[2] + dy, vb[3] + dy)
        self.redraw()
    def fit(self):
        self._view = None
        self.redraw()
    def _on_double(self, _event):
        self.fit()
    def _zoom_at(self, px, py, factor):
        vb = self._view_bounds()
        if vb is None:
            return
        cx, cy = self._to_data(px, py, vb, self._rect())
        x0, x1, y0, y1 = vb
        self._view = (cx - (cx - x0) * factor, cx + (x1 - cx) * factor,
                      cy - (cy - y0) * factor, cy + (y1 - cy) * factor)
        self.redraw()
    def _on_wheel(self, event):
        if event.delta > 0:
            self._zoom_at(event.x, event.y, 1 / 1.2)
        elif event.delta < 0:
            self._zoom_at(event.x, event.y, 1.2)
    def _on_wheel_up(self, event):
        self._zoom_at(event.x, event.y, 1 / 1.2)
    def _on_wheel_down(self, event):
        self._zoom_at(event.x, event.y, 1.2)
