#!/usr/bin/env python3
"""gui.py - Tkinter front end of the Virtuoso parameter optimizer."""
import glob
import json
import math
import os
import queue
import re
import sys
import threading
import time
from optimizer import (  # noqa: E402
    EXAMPLE_CFG,
    VERSION,
    RealRunner,
    Settings,
    Target,
    _iter_order,
    _read_text,
    _value_at,
    build_variables,
    format_eng,
    load_config,
    parse_eng,
    parse_netlist_parameters,
    resolve_netlist_path,
    resolve_run_dir,
    run_optimization,
    stop_all_runners,
    suggest_bounds,
    summarize
)

# Tkinter GUI
_SET_DEFAULTS = {"max_iter": 100, "patience": 10, "improve_rel": 0.001,
                 "fail_limit": 5, "max_restarts": 2, "seed": 0,
                 "strategy": "simplex"}
# Config keys the form owns. Everything else is passed through untouched
# in self.extra; owning these here keeps a cleared tech field from being
# resurrected by the previous config's value.
_MANAGED_CFG_KEYS = (
    "name", "library", "cell", "view", "state", "state_dir",
    "work_dir", "virtuoso_bin", "output_dir", "analyses", "save_raw",
    "sim_timeout", "variables", "fixed_variables", "targets", "settings",
    "netlist", "sim_root", "spectre_bin", "env", "path_append",
    "auto_netlist", "netlist_view", "em_align",
    "techlib", "techlib_dir", "layermap", "proc", "cds_lib",
)
_VAR_PALETTE = ("#c23b22", "#2266cc", "#2a9d3a", "#d17a00",
                "#7a3cb0", "#00a0a0", "#d4368c", "#5a5a5a")
_AGE_OLD_RGB = (0xCC, 0xCC, 0xCC)  # #cccccc
_AGE_NEW_RGB = (0x00, 0x55, 0xCC)  # #0055cc
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
    """Interactive plot on a plain tk.Canvas. Pure tkinter."""
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
    # public
    def show(self, series, title="", xlabel="", ylabel="", legend=False,
             keep_view=False, empty_text=None):
        """Replace the plotted series. keep_view preserves user zoom."""
        self.series = [s for s in series if s.get("xs") and s.get("ys")]
        self.title, self.xlabel, self.ylabel = title, xlabel, ylabel
        self.legend = legend
        if empty_text is not None:
            self.empty_text = empty_text
        self._bounds = self._data_bounds()
        if not keep_view:
            self._view = None
        self.redraw()
    # geometry
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
    # ticks
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
        for _guard in range(64):  # never hang on tiny steps
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
    # drawing
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
    # interactions
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
        """Reset the view to show all data (same as double-click)."""
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

# Sim-settings display helpers (pure functions, no tkinter)
_FREQ_ANALYSES = ("ac", "sp", "noise", "snoise", "pac", "psp", "xf")
_FREQ_LO_KEYS = ("start", "fstart", "f1", "fmin", "begin", "from")
_FREQ_HI_KEYS = ("stop", "fstop", "f2", "fmax", "end", "to")
_FREQ_ONE_KEYS = ("freq", "frequency", "f")
_NETLIST_ANALYSES = ("ac", "dc", "tran", "sp", "noise", "pss", "pac",
                     "hb", "xf", "snoise", "noise1")
_UNIT_HINT = ("提示: 数值无工程后缀时按 SI Hz 解读 (start=1 即 1 Hz, 不是 1 GHz);"
              " ADE/网表请写全后缀 (1G/10G)。若你的流程约定裸数字=GHz,"
              " 覆盖判断可能偏严, 请以网表后缀为准。")

def _eng_or_none(text):
    """parse_eng, but None on empty/bad input (display only)."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        return parse_eng(s)
    except Exception:
        return None

def _eng_bare(text):
    """(value_or_None, had_eng_suffix). Bare number => suffix False."""
    if text is None:
        return None, False
    s = str(text).strip()
    if not s:
        return None, False
    return _eng_or_none(s), bool(re.search(r"[A-Za-z]+$", s))

def _lower_fields(fields):
    """Case-insensitive view of one analyses.fields dict."""
    return {str(k).strip().lower(): v for k, v in (fields or {}).items()}

def _freq_from_fields(fields, bare_flag):
    lo = hi = None
    for keys, is_hi in ((_FREQ_LO_KEYS, False), (_FREQ_HI_KEYS, True)):
        for k in keys:
            if k in fields:
                v, bare = _eng_bare(fields[k])
                if v is not None:
                    if bare:
                        bare_flag[0] = True
                    if is_hi:
                        hi = v
                    else:
                        lo = v
                    break
    if lo is None and hi is None:
        for k in _FREQ_ONE_KEYS:
            if k in fields:
                v, bare = _eng_bare(fields[k])
                if v is not None:
                    if bare:
                        bare_flag[0] = True
                    lo = hi = v
                    break
    if lo is None or hi is None:
        return None, None
    return (hi, lo) if lo > hi else (lo, hi)

def _freq_union(entries, missing, tag_prefix, tag_src):
    """Union (lo, hi, source_label) of frequency sweeps in parsed entries."""
    los, his, labels, bare_flag = [], [], [], [False]
    for a in entries or []:
        atype = str(a.get("type", "")).strip().lower()
        if atype not in _FREQ_ANALYSES:
            continue
        lo, hi = _freq_from_fields(_lower_fields(a.get("fields")), bare_flag)
        if lo is None or hi is None:
            continue
        los.append(lo)
        his.append(hi)
        labels.append(atype)
    if not los:
        return None, None, missing
    tag = tag_prefix + (",".join(labels) if labels else tag_src)
    if bare_flag[0]:
        tag += " [含无后缀数值,按Hz]"
    return min(los), max(his), tag

def freq_range_from_analyses(analyses):
    """Union (lo, hi) of frequency sweeps in parsed analyses."""
    return _freq_union(analyses, "分析字段中未找到频率范围", "analyses.state:", "analyses")

def _strip_inline_comment(s):
    while "/*" in s and "*/" in s:
        i = s.find("/*")
        j = s.find("*/", i + 2)
        if j < 0:
            break
        s = s[:i] + " " + s[j + 2:]
    for mark in ("//", "#", ";"):
        if mark in s:
            s = s.split(mark, 1)[0]
    return s.strip()

def _join_continuations(text):
    out, buf = [], ""
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if buf:
            piece = line.lstrip()
            if piece.startswith("+"):
                piece = piece[1:].lstrip()
            buf = buf + " " + piece
        else:
            buf = line
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out

def _split_tokens(s):
    """Whitespace split that keeps quoted values together."""
    toks, cur, in_q = [], [], None
    for ch in s:
        if in_q:
            cur.append(ch)
            if ch == in_q:
                in_q = None
        elif ch in ('"', "'"):
            in_q = ch
            cur.append(ch)
        elif ch.isspace():
            if cur:
                toks.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        toks.append("".join(cur))
    return toks

def parse_netlist_analyses(text):
    """Spectre analysis statements of a deck: name type key=val ..."""
    out = []
    for raw in _join_continuations(text):
        s = _strip_inline_comment(raw)
        if not s or s.startswith(("//", "*", ";", "#")):
            continue
        toks = _split_tokens(s)
        if len(toks) < 2 or toks[1].lower() not in _NETLIST_ANALYSES:
            continue
        fields = {}
        for tok in toks[2:]:
            if "=" in tok:
                key, _eq, val = tok.partition("=")
                fields[key.strip().lower()] = val.strip().strip('"').strip("'")
        out.append({"type": toks[1].lower(), "fields": fields, "name": toks[0]})
    return out

def freq_range_from_netlist(text):
    """Union (lo, hi) of frequency sweeps written in the spectre deck."""
    return _freq_union(parse_netlist_analyses(text), "网表中未找到频率扫描语句", "netlist:", "netlist")

def coverage_label(sim_lo, sim_hi, band):
    """How one target band sits inside the simulated frequency range."""
    if band is None:
        return "目标无band"
    try:
        if len(band) < 2:
            return "非法band"
        blo, bhi = _eng_or_none(band[0]), _eng_or_none(band[1])
    except Exception:
        return "非法band"
    if blo is None or bhi is None:
        return "非法band"
    if blo > bhi:
        blo, bhi = bhi, blo
    if sim_lo is None or sim_hi is None:
        return "仿真频率未知"
    if sim_lo > sim_hi:
        sim_lo, sim_hi = sim_hi, sim_lo
    if sim_lo <= blo and sim_hi >= bhi:
        return "完全覆盖"
    if sim_hi < blo or sim_lo > bhi:
        return "不覆盖"
    return "部分重叠"

def _fmt_hz(v):
    if v is None:
        return "?"
    try:
        return format_eng(v)
    except Exception:
        return str(v)

def build_simset_text(state_dir, info, netlist_path, netlist_params,
                      netlist_text, targets, extra_notes=None):
    """Build the read-only sim-settings report. Pure string assembly."""
    nl = chr(10)
    lines = [
        "仿真设置（只读 · 实时展示 · 优化器不修改 ADE）", "=" * 52,
        "说明: 优化时 spectre 直跑网表快照; 每轮只改写顶层",
        "      parameters 行(优化+固定变量)。分析/模型/温度等",
        "      继承自网表快照, 运行中改 ADE 不影响本轮优化。", "",
        "【分析 analyses】 来源: analyses.state"]
    if info is None:
        lines.append("  (状态目录不可用或解析失败)")
    else:
        analyses = info.get("analyses") or []
        if not analyses:
            lines.append("  (analyses.state 中没有分析定义)")
        for i, a in enumerate(analyses, 1):
            atype = a.get("type", "?")
            fields = a.get("fields") or {}
            lines.append(f"  [{i}] 类型: {atype}   字段数: {len(fields)}")
            if not fields:
                lines.append("      (无 fields)")
            for key in sorted(fields.keys()):
                lines.append(f"      {key} = {fields[key]}")
    lines.append("")
    st_lo, st_hi, st_src = freq_range_from_analyses((info or {}).get("analyses"))
    nl_lo, nl_hi, nl_src = freq_range_from_netlist(netlist_text)
    if nl_lo is not None or nl_hi is not None:
        sim_lo, sim_hi = nl_lo, nl_hi
    else:
        sim_lo, sim_hi = st_lo, st_hi
    lines += [
        "【仿真频率范围】",
        f"  analyses.state : {_fmt_hz(st_lo)} ~ {_fmt_hz(st_hi)}   ({st_src})",
        f"  网表快照       : {_fmt_hz(nl_lo)} ~ {_fmt_hz(nl_hi)}   ({nl_src})",
        # Never silently treat bare numbers as GHz.
        "  " + _UNIT_HINT,
        f"  用于覆盖判断   : {_fmt_hz(sim_lo)} ~ {_fmt_hz(sim_hi)}", ""]
    lines.append("【网表快照】")
    if netlist_path:
        lines.append(f"  路径: {netlist_path}")
        try:
            mtime = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(os.path.getmtime(netlist_path)))
            lines.append(f"  修改时间: {mtime}")
        except OSError:
            lines.append("  修改时间: (读取失败)")
        lines += ["  机制: 启动时读取一次; 每轮仅改写顶层 parameters 行", "        后直跑 spectre; 中途改 ADE/网表不影响本轮。"]
    else:
        lines.append("  路径: (未找到; 设计页填「网表」或点「查找网表」)")
    lines += ["", "【parameters 行当前值】"]
    if netlist_params is None:
        lines.append("  (网表不可用或解析失败)")
    elif not netlist_params:
        lines.append("  (顶层没有 parameters 行, 优化器将无法写入变量)")
    else:
        lines.append(f"  共 {len(netlist_params)} 项:")
        for key in sorted(netlist_params.keys()):
            lines.append(f"    {key} = {netlist_params[key]}")
    lines += ["", "【模型文件】 来源: modelSetup.state"]
    models = (info or {}).get("model_files") or []
    if info is None:
        lines.append("  (状态目录不可用)")
    elif not models:
        lines.append("  (modelSetup.state 未列出模型文件; 网表内 include 仍生效)")
    else:
        lines.extend("  %s" % p for p in models)
    lines += ["", "【目标频段 vs 仿真频率范围】"]
    if not targets:
        lines.append("  (目标页还没有目标)")
    for i, spec in enumerate(targets or [], 1):
        out = spec.get("output", "?")
        metric = spec.get("metric", "scalar")
        band = spec.get("band")
        band_txt = "无"
        if band:
            try:
                band_txt = ("[%s, %s]" % (band[0], band[1]) if len(band) >= 2
                            else "非法(%r)" % (band,))
            except Exception:
                band_txt = "非法(%r)" % (band,)
        cov = coverage_label(sim_lo, sim_hi, band if band else None)
        extra = ""
        freq = spec.get("freq")
        if freq:
            fv = _eng_or_none(freq)
            if fv is not None and sim_lo is not None and sim_hi is not None:
                lo2, hi2 = ((sim_lo, sim_hi) if sim_lo <= sim_hi else (sim_hi, sim_lo))
                extra = ("; 频率 %s 在仿真范围内" % freq if lo2 <= fv <= hi2 else "; 频率 %s 不在仿真范围内" % freq)
            else:
                extra = f"; 频率 {freq}"
        if spec.get("curve_op"):
            extra += f"; 限值线 curve_op={spec.get('curve_op')}"
        lines.append(f"  [{i}] {out}  metric={metric}  band={band_txt}" f"  -> {cov}{extra}")
    lines.append("")
    if extra_notes:
        lines.append("【提示】")
        lines.extend("  %s" % note for note in extra_notes)
        lines.append("")
    lines.append(f"状态目录: {state_dir or '(未填写)'}")
    return nl.join(lines)

def read_result_any(iter_dir):
    """Read one iteration's result.txt, then result.txt.gz (A4 compact)."""
    plain = os.path.join(iter_dir, "result.txt")
    gz_path = os.path.join(iter_dir, "result.txt.gz")
    if os.path.isfile(plain):
        try:
            curves, status, msg, timing = RealRunner._read_result(plain)
            return curves, status, msg, timing, "plain"
        except Exception:
            pass
    if not os.path.isfile(gz_path):
        return None
    try:
        import gzip
        with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    # RealRunner._read_result takes a path; hand it a temp file so the
    # parsing logic stays a single copy (no drift).
    import tempfile
    fd = tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="gui_res_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            fd = None
            out.write(text)
        curves, status, msg, timing = RealRunner._read_result(tmp)
        return curves, status, msg, timing, "gz"
    except Exception:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

# EM display: contract field names, statuses, formatters
# Contract field names (C-1..C-4): rename ONLY here.
F_DIST = "distance"            # 排序/验收键 d=max|Δ|/tol（主列）
F_DELTA = "delta_vs_request"   # 物理相对偏差（次列，非排序键）
F_DELIVERED = "delivered"
F_ACTUAL = "actual_fingerprint"
F_STATUS = "status"
F_SOURCE = "source"

def _f_get(d, const_key, default=None):
    """按常量取字段；const_key 为 F_* 值。"""
    return d.get(const_key, default) if isinstance(d, dict) else default

SCHEMA = "gui_em_panel/1"
STATUS_HIT_GRID = "hit_grid"
STATUS_HIT_NEAREST = "hit_nearest"
STATUS_BEST_EFFORT = "best_effort"
STATUS_MISS = "miss"
STATUS_INFRA = "infra_fail"
STATUS_ERROR = "error"
KNOWN_STATUSES = (STATUS_HIT_GRID, STATUS_HIT_NEAREST, STATUS_BEST_EFFORT,
                  STATUS_MISS, STATUS_INFRA, STATUS_ERROR)
STATUS_LABELS = {
    STATUS_HIT_GRID: "格点命中", STATUS_HIT_NEAREST: "近邻命中",
    STATUS_BEST_EFFORT: "尽力交付", STATUS_MISS: "库未命中",
    STATUS_INFRA: "工具链失败", STATUS_ERROR: "查询错误"}
HISTORY_LIMIT = 50
WARN_GATE = "GATE"  # 契约 L2 硬门槛(SRF); 勿与已弃 xfmr L1/L2 混淆
WARN_SOFT = "SOFT"  # 契约 L3 软告警(Q)
WARN_INFO = "INFO"
WEAK_HIT_DISTANCE = 10.0

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _fmt_eng(v, unit=""):
    if v is None:
        return "?"
    if not _is_num(v):
        return str(v)
    x = float(v)
    if not math.isfinite(x):
        return str(x)
    ax = abs(x)
    if ax == 0:
        return "0" + unit
    for scale, suf in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"),
                       (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
                       (1e-12, "p"), (1e-15, "f")):
        if ax >= scale:
            return "%g%s%s" % (x / scale, suf, unit)
    return "%g%s" % (x, unit)

def _fmt_pct(x, digits=2):
    if x is None or not _is_num(x):
        return "n/a"
    return "%+.*f%%" % (digits, float(x) * 100.0)

def _fmt_delta(delta):
    return "n/a" if delta is None or not _is_num(delta) else _fmt_pct(delta)

def status_label(status):
    """枚举中文名；未知值原样括注，不抛。"""
    s = str(status or "").strip()
    return STATUS_LABELS[s] if s in STATUS_LABELS else "未知(%s)" % (s or "空")

def _q_of_fingerprint(fp):
    """Q@f0 from fingerprint (stub semantics)."""
    if not isinstance(fp, dict):
        return None
    if "Q_at" in fp:
        v = fp.get("Q_at")
        return float(v) if _is_num(v) else None
    q = fp.get("Q")
    if isinstance(q, dict) and q:
        try:
            return float(q[sorted(q.keys(), key=lambda s: float(s))[0]])
        except Exception:
            return None
    return float(q) if _is_num(q) else None

def _metric_value(fp, key):
    if not isinstance(fp, dict):
        return None
    if key == "Q":
        return _q_of_fingerprint(fp)
    v = fp.get(key)
    return float(v) if _is_num(v) else None

def iter_triple_rows(em_actual, tol=None):
    """Triple rows requested/grid/delivered (stub fields)."""
    if not isinstance(em_actual, dict):
        return []
    tol = tol or {}
    target = em_actual.get("target")
    target = target if isinstance(target, dict) else {}
    delivered_map = _f_get(em_actual, F_DELIVERED, {}) or {}
    actual = _f_get(em_actual, F_ACTUAL, {}) or {}
    deltas = _f_get(em_actual, F_DELTA, {}) or {}
    delivered_map = delivered_map if isinstance(delivered_map, dict) else {}
    actual = actual if isinstance(actual, dict) else {}
    deltas = deltas if isinstance(deltas, dict) else {}
    skip = ("kind", "ports", "band_hi", "Lcurve", "Qcurve", "kcurve",
            "Z0curve", "ILcurve", "extract_window", "warnings", "Q_at")
    keys = list(target.keys())
    for extra in list(delivered_map.keys()) + list(actual.keys()):
        if extra not in keys and extra not in skip:
            keys.append(extra)
    grid_map = em_actual.get("grid_values")
    grid_all = em_actual.get("grid")
    rows = []
    for key in keys:
        req = target.get(key)
        if _is_num(req):
            req_v = float(req)
        elif isinstance(req, str) and req.strip():
            try:
                req_v = float(req)
            except ValueError:
                req_v = None
        else:
            req_v = None
        if isinstance(grid_map, dict) and _is_num(grid_map.get(key)):
            grid_v = float(grid_map[key])
        elif req_v is not None and isinstance(grid_all, dict) and _is_num(grid_all.get(key)):
            grid_v = float(grid_all[key])
        else:
            grid_v = req_v  # 无独立格点时退化为请求值
        del_v = delivered_map.get(key)
        del_v = float(del_v) if _is_num(del_v) else _metric_value(actual, key)
        delta = deltas.get(key)
        if not _is_num(delta) and req_v is not None and del_v is not None and req_v != 0:
            delta = (del_v - req_v) if key == "theta" else (del_v - req_v) / abs(req_v)
        in_tol = None
        if key == "theta":
            t_abs = tol.get("theta_tol_deg", tol.get("theta"))
            if _is_num(t_abs) and _is_num(delta):
                ref = (del_v - req_v if req_v is not None and del_v is not None else delta)
                in_tol = abs(float(ref)) <= float(t_abs)
        else:
            t = tol.get(key, tol.get(key.lower()))
            if _is_num(t) and _is_num(delta):
                in_tol = abs(float(delta)) <= float(t)
        rows.append({"metric": key, "requested": req_v if req_v is not None else req,
                     "grid": grid_v, "delivered": del_v,
                     "delta": delta if _is_num(delta) else None, "in_tol": in_tol})
    return rows

def format_query_triple(name, em_actual, tol=None, show_geom=False):
    """356->358 table. C-2: distance (sort key) + delta_vs_request (physical)."""
    nl = "\n"
    if not isinstance(em_actual, dict):
        return "  (本轮无 EM 信息)"
    status = _f_get(em_actual, F_STATUS, "")
    src = _f_get(em_actual, F_SOURCE, "")
    dist = _f_get(em_actual, F_DIST)
    head = "【器件 %s】状态=%s" % (name or "?", status_label(status))
    if src:
        head += "  来源=%s" % src
    lines = [head]
    meta_bits = []
    if _is_num(dist):
        meta_bits.append("distance=%s" % ("%.3g" % float(dist)))
    if em_actual.get("grid_key"):
        meta_bits.append("grid_key=%s" % em_actual.get("grid_key"))
    if em_actual.get("snp_path"):
        meta_bits.append("snp=%s" % os.path.basename(str(em_actual.get("snp_path"))))
    if meta_bits:
        lines.append("  " + "  ".join(meta_bits))
    rows = iter_triple_rows(em_actual, tol)
    if not rows:
        lines.append("  (无指纹指标)")
    else:
        first = rows[0]
        lines.append("  %s:  %s  →  %s  →  %s   (%s)"
                     % (first["metric"], _fmt_eng(first["requested"]), _fmt_eng(first["grid"]),
                        _fmt_eng(first["delivered"]), _fmt_delta(first["delta"])))
        lines.append("         请求      格点      交付")
        lines.append("  %-8s %10s %10s %10s %10s %s"
                     % ("指标", "requested", "grid", "delivered", "delta", "in_tol"))
        for r in rows:
            if r["metric"] == "theta":
                d_txt = ("n/a" if r["delta"] is None else ("%+.2f°" % float(r["delta"])))
            else:
                d_txt = _fmt_delta(r["delta"])
            flag = "-" if r["in_tol"] is None else ("Y" if r["in_tol"] else "N")
            lines.append("  %-8s %10s %10s %10s %10s %s"
                         % (r["metric"], _fmt_eng(r["requested"]), _fmt_eng(r["grid"]),
                            _fmt_eng(r["delivered"]), d_txt, flag))
    geom = None
    rec = em_actual.get("record")
    if isinstance(rec, dict):
        geom = rec.get("geom") or rec.get("geom_json")
    if not geom:
        geom = em_actual.get("geom")
    if geom:
        if show_geom:
            lines.append("  geom: %s" % json.dumps(geom, ensure_ascii=False, sort_keys=True))
        elif isinstance(geom, dict):
            keys = sorted(geom.keys())
            brief = ", ".join("%s=%s" % (k, geom[k]) for k in keys[:6])
            if len(keys) > 6:
                brief += ", …"
            lines.append("  geom: {%s}  (折叠，show_geom=True 展开)" % brief)
        else:
            lines.append("  geom: {%s}  (折叠，show_geom=True 展开)" % str(geom)[:80])
    return nl.join(lines)

def format_library_stats(stats, hit_counts=None):
    """Library stats; empty placeholder."""
    stats = stats if isinstance(stats, dict) else {}
    hit_counts = hit_counts if isinstance(hit_counts, dict) else {}
    nl = "\n"
    lines = ["【库统计】"]
    count = stats.get("count")
    if not count and not hit_counts:
        lines.append("  (库为空：尚未 put，或库文件未创建)")
        return nl.join(lines)
    lines.append("  条目 %s   唯一格点 %s" % (_fmt_eng(count), _fmt_eng(stats.get("unique_keys"))))
    hg = int(hit_counts.get("hit_grid", 0) or 0)
    hn = int(hit_counts.get("hit_nearest", 0) or 0)
    ms = int(hit_counts.get("miss", 0) or 0)
    be = int(hit_counts.get("best_effort", 0) or 0)
    er = int(hit_counts.get("error", 0) or 0)
    inf = int(hit_counts.get("infra_fail", 0) or 0)
    total_q = hg + hn + ms + be + er + inf
    if total_q > 0:
        lines.append("  查询 %d 次：" % total_q)
        for label, n in (("hit_grid", hg), ("hit_nearest", hn),
                         ("best_effort", be), ("miss", ms),
                         ("error", er), ("infra_fail", inf)):
            lines.append("    %-12s %4d  (%.0f%%)" % (label, n, 100.0 * n / total_q))
        p_hit = float(hg + hn) / float(total_q)
        lines.append("  命中率 p_hit=%.2f  %s" % (p_hit, "达标(≥0.7)" if p_hit >= 0.7 else "偏低"))
    else:
        lines.append("  (尚无查询记录)")
    mr = stats.get("miss_rate")
    if _is_num(mr):
        lines.append("  miss_rate=%.2f  %s" % (float(mr), "偏高(>0.2)" if float(mr) > 0.2 else "ok"))
    ad = stats.get("avg_d_on_hit")
    if _is_num(ad):
        lines.append("  avg_d_on_hit=%.3f  %s"
                     % (float(ad), "ok" if float(ad) < 0.5
                        else ("弱" if float(ad) > 1.0 else "一般")))
    gu = stats.get("grid_utilization")
    if _is_num(gu):
        lines.append("  grid_utilization=%.1f%%  %s"
                     % (float(gu) * 100.0,
                        "过密(<5%)" if float(gu) < 0.05 else "ok"))
    return nl.join(lines)

def format_em_opt_status(em_meta, budget=None, device=None):
    """EmOptimizer status or placeholder."""
    em_meta = em_meta if isinstance(em_meta, dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    nl = "\n"
    lines = ["【EM 优化器】"]
    if not em_meta and not budget:
        lines.append("  (本轮走库命中，未启动内层 EM 优化器)")
        return nl.join(lines)
    dev = device or em_meta.get("device") or em_meta.get("device_type") or "?"
    iters = em_meta.get("iters")
    max_em = budget.get("max_em", em_meta.get("max_em"))
    wall = em_meta.get("wall_s", em_meta.get("wall"))
    cap = budget.get("time_cap_s", em_meta.get("time_cap_s"))
    em_ok, em_fail = em_meta.get("em_ok"), em_meta.get("em_fail")
    bits = ["器件 %s" % dev]
    if iters is not None:
        bits.append("迭代 %s%s" % (iters, ("/%s" % max_em) if max_em is not None else ""))
    if em_ok is not None or em_fail is not None:
        bits.append("EM 评测 ok=%s fail=%s"
                    % (em_ok if em_ok is not None else "?", em_fail if em_fail is not None else "?"))
    lines.append("  " + " · ".join(bits))
    left_em = (int(max_em) - int(iters) if _is_num(iters) and _is_num(max_em) else None)
    left_t = (float(cap) - float(wall) if _is_num(wall) and _is_num(cap) else None)
    if left_em is not None or left_t is not None:
        em_txt = ("剩 %d 次" % left_em) if left_em is not None else "次数 n/a"
        if left_em is not None and left_em <= 0:
            em_txt = "次数尽(0)"
        t_txt = ("剩 %.0f min" % (left_t / 60.0)) if left_t is not None else "时间 n/a"
        if left_t is not None and left_t <= 0:
            t_txt = "时间尽"
        lines.append("  预算: %s / %s" % (em_txt, t_txt))
    meta = []
    for key in ("mode", "strategy"):
        if em_meta.get(key):
            meta.append("%s=%s" % (key, em_meta[key]))
    if em_meta.get("cache_hits") is not None:
        meta.append("cache_hits=%s" % em_meta.get("cache_hits"))
    if _is_num(wall):
        meta.append(("wall=%s" % _fmt_eng(float(wall), "s")) if abs(float(wall)) >= 1
                    else ("wall=%.1fs" % float(wall)))
    if em_meta.get("source"):
        meta.append("source=%s" % em_meta.get("source"))
    if meta:
        lines.append("  " + " · ".join(meta))
    tgt = em_meta.get("target_fp") or em_meta.get("target")
    act = em_meta.get("actual_fp") or em_meta.get("actual_fingerprint")
    if isinstance(tgt, dict) or isinstance(act, dict):
        lines.append("  指纹 目标 vs 实际:")
        tgt = tgt if isinstance(tgt, dict) else {}
        act = act if isinstance(act, dict) else {}
        skip = ("kind", "ports", "band_hi", "Lcurve", "Qcurve", "kcurve", "warnings", "extract_window")
        for k in list(tgt.keys()) + [k for k in act.keys() if k not in tgt]:
            if k in skip:
                continue
            tv = tgt.get(k)
            av = _metric_value(act, k) if k not in tgt or _is_num(tgt.get(k)) else act.get(k)
            if not _is_num(av):
                av = act.get(k) if isinstance(act.get(k), str) else av
            if isinstance(tv, dict):
                tv = _q_of_fingerprint({"Q": tv}) if k == "Q" else None
            if k == "Q":
                tv = _q_of_fingerprint({"Q_at": tv}) if _is_num(tv) else _q_of_fingerprint(tgt)
                av = _q_of_fingerprint(act) if not _is_num(av) else av
            t_s = _fmt_eng(tv) if _is_num(tv) else ("?" if tv is None else str(tv))
            a_s = _fmt_eng(av) if _is_num(av) else ("?" if av is None else str(av))
            d_s = ""
            if _is_num(tv) and _is_num(av):
                if k == "theta":
                    d_s = "  Δ%+.2f°" % (float(av) - float(tv))
                elif tv != 0:
                    d_s = "  Δ%+.2f%%" % ((float(av) - float(tv)) / abs(float(tv)) * 100)
            lines.append("    %-6s %s → %s%s" % (k, t_s, a_s, d_s))
    return nl.join(lines)

def build_warns(em_actual, em_meta=None, f_op=None, srf_margin=1.5, q_tol=0.30):
    """Synthesize warns: GATE(SRF hard) / SOFT(Q) / INFO."""
    out, seen = [], set()
    def add(level, text, metric=None, detail=None, device=None):
        key = (level, text)
        if key not in seen:
            seen.add(key)
            out.append({"level": level, "text": text, "metric": metric,
                        "detail": detail, "device": device})
    if not isinstance(em_actual, dict):
        return out
    name = em_actual.get("device") or em_actual.get("name")
    actual = _f_get(em_actual, F_ACTUAL, {}) or {}
    actual = actual if isinstance(actual, dict) else {}
    delivered = em_actual.get("delivered")
    delivered = delivered if isinstance(delivered, dict) else {}
    target = em_actual.get("target")
    target = target if isinstance(target, dict) else {}
    status = str(em_actual.get("status") or "")
    srf = _metric_value(actual, "SRF")
    if srf is None and _is_num(delivered.get("SRF")):
        srf = float(delivered["SRF"])
    if f_op is not None and _is_num(f_op) and srf is not None:
        need = float(f_op) * float(srf_margin)
        if float(srf) < need:
            add(WARN_GATE,
                "SRF 门槛不过: 实际 %s < %s×f_op (%s)"
                % (_fmt_eng(srf, "Hz"), srf_margin, _fmt_eng(need, "Hz")),
                metric="SRF", detail={"actual": srf, "need": need, "f_op": float(f_op)}, device=name)
    q_a = _metric_value(actual, "Q")
    if q_a is None:
        q_a = _q_of_fingerprint(actual)
    q_t = None
    if "Q" in target:
        tv = target.get("Q")
        if _is_num(tv):
            q_t = float(tv)
        elif isinstance(tv, dict):
            q_t = _q_of_fingerprint({"Q": tv})
    if "Q@f0" in target and _is_num(target.get("Q@f0")):
        q_t = float(target["Q@f0"])
    if q_a is not None and q_t is not None and q_t != 0:
        rel = (q_a - q_t) / abs(q_t)
        if abs(rel) > float(q_tol):
            add(WARN_SOFT,
                "Q 告警: 实际 %s vs 目标 %s (%s, tol %s)"
                % (_fmt_eng(q_a), _fmt_eng(q_t), _fmt_pct(rel, 1), _fmt_pct(q_tol, 0)),
                metric="Q", detail={"actual": q_a, "target": q_t, "rel": rel}, device=name)
    dist = _f_get(em_actual, F_DIST)
    if _is_num(dist) and float(dist) > WEAK_HIT_DISTANCE:
        add(WARN_INFO, "弱命中: distance=%.3g > %g" % (float(dist), WEAK_HIT_DISTANCE),
            metric="distance", detail={"distance": float(dist)}, device=name)
    if status == STATUS_BEST_EFFORT:
        add(WARN_INFO, "best_effort：预算尽仍交付实际值（fallback=actual）", device=name)
    if status == STATUS_INFRA:
        add(WARN_INFO, "infra_fail：工具链失败，不占预算，将重试", device=name)
    for w in (em_actual.get("warns") or []):
        add(WARN_SOFT if "Q" in str(w) else WARN_INFO, str(w), device=name)
    if isinstance(em_meta, dict):
        for w in (em_meta.get("warns") or []):
            add(WARN_SOFT if "Q" in str(w) else WARN_INFO, str(w), device=name)
    return out

def format_warns(warns):
    """Warns text."""
    nl = "\n"
    lines = ["【告警】"]
    if not warns:
        lines.append("  (无告警)")
        return nl.join(lines)
    icon = {WARN_GATE: "硬!", WARN_SOFT: "软*", WARN_INFO: "-"}
    for w in warns:
        lvl = w.get("level", WARN_INFO)
        lines.append("  %s %s" % (icon.get(lvl, "·"), w.get("text", "")))
    return nl.join(lines)

def build_em_report(model):
    """Full page text. Never raises."""
    try:
        model = model if isinstance(model, dict) else {}
        nl = "\n"
        lines = [
            "EM 融合（只读 · 实时展示 · 优化器不修改 ADE）", "=" * 52,
            "说明: 电路优化请求 y* → 查结果库 → 命中用实际指纹 ŷ；",
            "      未命中由 EM 优化器几何搜索并入库。展示 requested/",
            "      grid/delivered 三值与告警，不做控制面。", ""]
        if model.get("disabled"):
            lines.append("未启用 EM 融合（配置无 em_bridge 段）")
            return nl.join(lines)
        lines.append(format_library_stats(model.get("library"), model.get("hit_counts")))
        lines.append("")
        queries = model.get("queries") or []
        lines.append("【当前查询】（最近 %d 条）" % HISTORY_LIMIT)
        if not queries:
            lines.append("  (尚无查询记录)")
        for q in queries[-HISTORY_LIMIT:]:
            lines.append(format_query_triple(
                q.get("name"), q.get("em_actual"), q.get("tol"),
                show_geom=bool(q.get("show_geom"))))
            lines.append("")
        lines.append(format_em_opt_status(
            model.get("em_opt"), model.get("budget"),
            device=model.get("device")))
        lines.append("")
        warns = model.get("warns")
        if warns is None:
            warns = []
            for q in queries[-HISTORY_LIMIT:]:
                warns.extend(build_warns(
                    q.get("em_actual") or {}, model.get("em_opt"),
                    f_op=model.get("f_op"),
                    srf_margin=model.get("srf_margin", 1.5),
                    q_tol=model.get("q_tol", 0.30)))
        lines.append(format_warns(warns))
        lines.append("")
        lines.append("运行模式: %s" % (model.get("mode") or "(未设)"))
        return nl.join(lines)
    except Exception as exc:
        return "EM_DISPLAY_ERROR: %s" % exc

def em_watch_paths(work_dir, out_dir):
    """Fixed paths to stat (no recursive glob)."""
    work = os.path.expanduser(str(work_dir or "work"))
    lib = os.path.join(work, "em_lib")
    paths = [os.path.join(lib, name) for name in
             ("em_lib.sqlite3", "em_lib.json", "em_lib_stat.jsonl")]
    if out_dir:
        out = os.path.expanduser(str(out_dir))
        paths += [os.path.join(out, name) for name in
                  ("history.csv", "events.jsonl", "report.json")]
    return paths

def em_signature(paths):
    """(path, mtime_ns, size); size catches same-second writes."""
    sig = []
    for p in paths or []:
        try:
            st = os.stat(p)
            mt = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            sig.append((p, int(mt), int(st.st_size)))
        except OSError:
            sig.append((p, None, None))
    return tuple(sig)

def read_jsonl_tail(path, limit=200):
    """jsonl tail, skip bad lines."""
    if not path or not os.path.isfile(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    return out[-limit:]

def collect_em_model(work_dir=None, out_dir=None, history=None,
                     em_meta=None, budget=None, f_op=None,
                     srf_margin=1.5, q_tol=0.30, mode=None,
                     disabled=False, tol=None):
    """Read-only EmUiModel. Never raises."""
    model = {
        "schema": SCHEMA, "disabled": bool(disabled), "library": {},
        "hit_counts": {}, "queries": [], "em_opt": em_meta or {},
        "budget": budget or {}, "warns": None, "f_op": f_op,
        "srf_margin": srf_margin, "q_tol": q_tol, "mode": mode,
        "tol": tol or {},
    }
    try:
        work = os.path.expanduser(str(work_dir or "work"))
        lib = os.path.join(work, "em_lib")
        counts = {}
        for row in read_jsonl_tail(os.path.join(lib, "em_lib_stat.jsonl"), 1000):
            if not isinstance(row, dict):
                continue
            st = str(row.get("status") or row.get("kind") or "")
            if st in KNOWN_STATUSES:
                counts[st] = counts.get(st, 0) + 1
        model["hit_counts"] = counts
        for name in ("em_lib_stat.json", "em_lib_stats.json"):
            p = os.path.join(lib, name)
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        model["library"].update(data)
                except Exception:
                    pass
        if not model["library"] and counts:
            total = sum(counts.values())
            hits = counts.get(STATUS_HIT_GRID, 0) + counts.get(STATUS_HIT_NEAREST, 0)
            model["library"] = {
                "count": total,
                "miss_rate": (float(counts.get(STATUS_MISS, 0)) / total if total else None),
                "p_hit": (float(hits) / total) if total else None}
        hist = history
        if hist is None and out_dir:
            hist = []
            out = os.path.expanduser(str(out_dir))
            paths = glob.glob(os.path.join(out, "iter_*", "record.json"))
            paths.sort()
            for p in paths[-HISTORY_LIMIT:]:
                try:
                    with open(p, encoding="utf-8") as f:
                        hist.append(json.load(f))
                except Exception:
                    continue
        queries = []
        for rec in (hist or [])[-HISTORY_LIMIT:]:
            if not isinstance(rec, dict):
                continue
            em = rec.get("em_actual")
            if not isinstance(em, dict):
                # LogWriter stores EM data under record.extra.
                extra = rec.get("extra")
                em = extra.get("em_actual") if isinstance(extra, dict) else None
            if not isinstance(em, dict):
                continue
            if "status" in em and ("delivered" in em or "actual_fingerprint" in em or "target" in em):
                queries.append({"name": rec.get("device") or "em", "em_actual": dict(em),
                                "tol": tol or {}, "show_geom": False, "index": rec.get("index")})
            else:
                for name, payload in em.items():
                    if isinstance(payload, dict):
                        queries.append({"name": name, "em_actual": payload, "tol": tol or {},
                                        "show_geom": False, "index": rec.get("index")})
        model["queries"] = queries[-HISTORY_LIMIT:]
    except Exception as exc:
        model["error"] = str(exc)
    return model

def _auto_netlist_on(value):
    """Map a config value (bool / "auto" / "off") to the checkbox state."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "auto", "always")
    return bool(value)

def _var_extra_rename(extra, old, new):
    """Move per-variable metadata (scale/integer/...) to the new name."""
    out = {k: dict(v) for k, v in (extra or {}).items()}
    if old != new and old in out:
        out[new] = out.pop(old)
    return out

def _var_extra_drop(extra, names):
    """Forget metadata of variables removed from the optimized table."""
    drop = set(names or ())
    return {k: v for k, v in (extra or {}).items() if k not in drop}

def _targets_signature(target_specs):
    """Stable digest of target definitions for the 2 s sim-settings refresh.

    The count alone misses edits to an existing target's band/goal, which
    leaves the coverage hint stale forever.
    """
    try:
        items = []
        for iid, spec in (target_specs or {}).items():
            items.append((str(iid), json.dumps(spec, sort_keys=True,
                                               ensure_ascii=False, default=str)))
        items.sort()
        return tuple(items)
    except Exception:
        return None

def netlist_report_text(report):
    """Human-readable summary of netlist_create.ensure_netlist() output."""
    if not isinstance(report, dict):
        return "网表生成返回异常: %r" % (report,)
    status = str(report.get("status") or "unknown")
    lines = ["状态: %s" % status]
    design = report.get("design")
    if isinstance(design, dict) and design:
        bits = [str(design.get(k)) for k in ("library", "cell", "view") if design.get(k)]
        if bits:
            lines.append("设计: %s" % " / ".join(bits))
    for key, label in (("path", "网表"), ("expected", "预期路径"),
                       ("previous", "原网表"), ("backup", "备份"),
                       ("wall", "耗时(秒)"), ("log", "日志"),
                       ("message", "说明")):
        val = report.get(key)
        if val not in (None, "", [], {}):
            lines.append("%s: %s" % (label, val))
    for key, label in (("errors", "错误"), ("warnings", "告警")):
        items = report.get(key) or []
        if items:
            lines.append("%s(%d):" % (label, len(items)))
            for item in list(items)[:20]:
                lines.append("  " + str(item))
    if report.get("restored"):
        lines.append("已恢复备份网表")
    return "\n".join(lines)

def em_align_event_text(event):
    """Render one on_event() payload for the EM 对齐 log box."""
    if isinstance(event, str):
        return event
    if isinstance(event, dict):
        for key in ("message", "msg", "text", "log"):
            val = event.get(key)
            if val not in (None, ""):
                return str(val)
        kind = event.get("type") or event.get("event")
        try:
            rest = json.dumps(event, ensure_ascii=False, sort_keys=True)
        except Exception:
            rest = str(event)
        return ("[%s] %s" % (kind, rest)) if kind else rest
    return str(event)

def find_em_report_md(report, dirs):
    """Locate em_align_report.md from the report or the usual output dirs."""
    keys = ("report_md", "em_align_md", "markdown_path", "md_path")
    if isinstance(report, dict):
        for key in keys:
            val = report.get(key)
            if val and os.path.isfile(str(val)):
                return os.path.abspath(str(val))
        em = report.get("em_align")
        if isinstance(em, dict):
            for key in keys + ("report_path", "path"):
                val = em.get(key)
                if val and os.path.isfile(str(val)):
                    return os.path.abspath(str(val))
    for base in dirs or ():
        if not base:
            continue
        path = os.path.join(base, "em_align_report.md")
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None

class _TechTab:
    """「工艺」页：工艺文件路径输入 + require_tech_ready 门禁 + 横截面（懒加载）。

    真源优先级：内建默认 < tech_profile.json < 本页填写项（见 build_tech）。
    示教 fixture 会出 1 条警告，状态显示「通过（1 条警告）」。
    """
    FIELDS = (
        ("techlib", "工艺库名 techlib", "RFCMOS", "text"),
        ("techlib_dir", "工艺库目录 techlib_dir", "", "dir"),
        ("layermap", "layermap（层名->GDS 层号）", "", "file"),
        ("proc", "EMX proc（工艺叠层）", "", "file"),
        ("cds_lib", "cds.lib（工作区库映射）", "", "file"),
    )
    def __init__(self, gui, parent):
        self.gui, self.tk, self.ttk = gui, gui.tk, gui.ttk
        self.root = parent
        self.vars = {}
        self._xs_frame = None
        self._build(parent)
    def _build(self, parent):
        self.ttk.Label(
            parent, anchor="w", justify="left", foreground="#555555",
            text=("开始版图/EM 前会自动执行工艺检查（路径存在性 -> proc/layermap "
                  "解析 -> 叠层/层号核对），硬错误直接中止任务，警告仅在状态栏提示。"
                  "留空的项沿用 tech_profile.json / 内建 RFCMOS 默认。")
        ).pack(fill="x", pady=(0, 6))
        box = self.ttk.LabelFrame(parent, text="工艺输入")
        box.pack(fill="x", pady=(0, 4))
        for key, label, default, kind in self.FIELDS:
            row = self.ttk.Frame(box)
            row.pack(fill="x", pady=2)
            self.ttk.Label(row, text=label, width=26, anchor="w").pack(side="left")
            var = self.tk.StringVar(value=default)
            self.ttk.Entry(row, textvariable=var, width=58).pack(side="left", fill="x", expand=True)
            if kind != "text":
                self.ttk.Button(row, text="浏览",
                                command=lambda k=key, t=kind: self._browse(k, t)
                                ).pack(side="left", padx=4)
            self.vars[key] = var
        bar = self.ttk.Frame(parent)
        bar.pack(fill="x", pady=2)
        self.ttk.Button(bar, text="检查工艺", command=self.check).pack(side="left", padx=2)
        self.ttk.Button(bar, text="显示横截面",
                        command=self.show_cross_section).pack(side="left", padx=2)
        self.status_var = self.tk.StringVar(value="尚未检查（任务开始时会自动检查）")
        self.status_label = self.ttk.Label(bar, textvariable=self.status_var,
                                           foreground="#555555", anchor="w")
        self.status_label.pack(side="left", padx=8)
        self.detail = self._text(parent, height=7)
        self.detail.pack(fill="x", pady=(2, 4))
        self.xs_host = self.ttk.Frame(parent)
        self.xs_host.pack(fill="both", expand=True, pady=(2, 0))
    def _text(self, parent, height=6):
        from tkinter.scrolledtext import ScrolledText
        box = ScrolledText(parent, height=height, width=80,
                           font=("WenQuanYi Micro Hei", 9))
        box.configure(state="disabled")
        return box
    def _set_detail(self, text):
        try:
            self.detail.configure(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.insert("1.0", text)
            self.detail.configure(state="disabled")
        except Exception:
            pass
    def _set_status(self, text, color):
        self.status_var.set(text)
        try:
            self.status_label.configure(foreground=color)
        except Exception:
            pass
    def _browse(self, key, kind):
        try:
            from tkinter import filedialog
        except Exception:
            return
        if kind == "dir":
            path = filedialog.askdirectory(parent=self.root, title="选择工艺库目录")
        else:
            path = filedialog.askopenfilename(
                parent=self.root, title="选择 %s" % key,
                filetypes=[("所有文件", "*")])
        if path:
            self.vars[key].set(path)
    def fill_from_cfg(self, cfg):
        """配置载入时回填 5 个键；配置里没有的键回到该字段默认值。

        必须清空缺省键，否则打开另一份不含工艺段的配置（或点「新建」）
        会沿用上一份配置的 layermap/proc，工艺门禁与保存结果都串味。
        """
        cfg = cfg if isinstance(cfg, dict) else {}
        for key, _label, default, _kind in self.FIELDS:
            val = cfg.get(key)
            self.vars[key].set(default if val is None else str(val))
    def collect_tech(self):
        """本页输入 -> tech dict（显式项覆盖 profile，留空沿用 profile）。"""
        import librf_opt
        v = {k: (var.get() or "").strip() for k, var in self.vars.items()}
        return librf_opt.build_tech(
            techlib=v.get("techlib") or None,
            techlib_dir=v.get("techlib_dir") or None,
            layermap=v.get("layermap") or None,
            proc=v.get("proc") or None,
            cds_lib=v.get("cds_lib") or None)
    def tech_cfg_keys(self):
        """非空填写项 -> config 顶层键（保存/载入往返用）。"""
        out = {}
        for key, _label, _default, _kind in self.FIELDS:
            v = (self.vars[key].get() or "").strip()
            if v:
                out[key] = v
        return out
    def check(self, parent=None, show_dialog=False):
        """执行 require_tech_ready；状态栏分色。返回是否通过。"""
        import librf_opt
        try:
            prof = self.collect_tech()
            warns = librf_opt.require_tech_ready(prof)
        except librf_opt.LibrfError as exc:
            self._set_status("错误：工艺检查未通过", "#b3261e")
            self._set_detail(str(exc))
            if show_dialog:
                self._msg("工艺检查未通过", str(exc), error=True)
            return False
        except Exception as exc:
            self._set_status("错误：无法构造工艺配置", "#b3261e")
            self._set_detail(str(exc))
            if show_dialog:
                self._msg("工艺配置错误", str(exc), error=True)
            return False
        if warns:
            self._set_status("通过（%d 条警告）" % len(warns), "#b7791f")
            self._set_detail("工艺检查通过，%d 条警告：\n  " % len(warns)
                             + "\n  ".join(warns))
        else:
            self._set_status("通过", "#0a7d33")
            self._set_detail("工艺检查通过（无警告）。proc/layermap 叠层与层号一致。")
        return True
    def require_ready_ui(self, parent=None):
        """任务启动入口调用：失败弹窗并返回 False；警告只更新状态栏。"""
        return self.check(parent=parent, show_dialog=True)
    def _msg(self, title, text, error=False):
        try:
            from tkinter import messagebox
            fn = messagebox.showerror if error else messagebox.showwarning
            fn(title, text, parent=self.root)
        except Exception:
            print(text)
    def show_cross_section(self):
        """懒加载 proc_cross_section.build_cross_section_frame（第一次点才导入）。"""
        if self._xs_frame is not None:
            return
        try:
            from proc_cross_section import build_cross_section_frame
        except Exception as exc:
            self._xs_frame = "unavailable"
            self.ttk.Label(self.xs_host, foreground="#888888",
                           text="横截面模块不可用（未部署 proc_cross_section.py）：%s"
                                % exc).pack(anchor="w")
            return
        try:
            frame = build_cross_section_frame(
                self.xs_host, self.vars["proc"], self.vars["layermap"], height=420)
            frame.pack(fill="both", expand=True)
            self._xs_frame = frame
        except Exception as exc:
            self._xs_frame = "unavailable"
            self.ttk.Label(self.xs_host, foreground="#b3261e",
                           text="横截面加载失败：%s" % exc).pack(anchor="w")


class OptimizerGui:
    """Tkinter front end. Edit the config. Run and watch the optimizer."""
    def __init__(self, root, smoke=False):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.smoke = root, smoke
        self.tk, self.ttk = tk, ttk
        self._setup_fonts()
        self.queue = queue.Queue()
        self.stop_event = self.worker = self.report = None
        self.running = self.finished = False
        self.real_stdout = sys.stdout
        self.plot_points, self.best_points = [], []
        self.drawn_points = self.iter_events = 0
        self.extra, self.extra_settings, self.target_specs = {}, {}, {}
        self.ade_outputs = []
        self.curve_cache, self.var_cache = {}, {}
        self.curve_stats = {"plain": 0, "gz": 0, "missing": 0, "corrupt": 0, "total": 0}
        self._vars_undo_stack, self._vars_redo_stack = [], []
        self.best_var_values = None
        root.title("Virtuoso Optimizer %s" % VERSION)
        root.geometry("1200x800")
        root.minsize(900, 600)
        self.mock_var = self.tk.BooleanVar(value=False)
        self.resume_var = self.tk.BooleanVar(value=False)
        self._run_dir_state = None
        self._netlist_busy = False
        self._em_align_running = False
        self.em_align_report_dir = ""
        self._build_menubar()
        self._build_toolbar()
        self._build_tabs()
        self._build_status()
        self._new_config()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)
    def _setup_fonts(self):
        """Pick a CJK-capable font and wire it into every widget."""
        self.ui_font = self.mono_font = None
        try:
            import tkinter.font as tkfont
        except Exception:
            return
        preferred = ("WenQuanYi Micro Hei", "Noto Sans CJK SC", "Microsoft YaHei UI", "PingFang SC",
                     "song ti", "fangsong ti", "mincho")
        families = set(tkfont.families())
        family = next((f for f in preferred if f in families), None)
        mono = ("WenQuanYi Micro Hei Mono" if "WenQuanYi Micro Hei Mono" in families else family)
        for fam, attr in ((family, "ui_font"), (mono, "mono_font")):
            if fam:
                try:
                    setattr(self, attr, tkfont.Font(family=fam, size=12))
                except Exception:
                    pass
        if family:
            for name in ("TkDefaultFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont"):
                try:
                    tkfont.nametofont(name).configure(family=family)
                except Exception:
                    pass
        if mono:
            try:
                tkfont.nametofont("TkFixedFont").configure(family=mono)
            except Exception:
                pass
        if self.ui_font is not None:
            try:
                style = self.ttk.Style()
                for s in ("TEntry", "TCombobox", "TLabel", "TButton", "TMenubutton",
                          "TCheckbutton", "TRadiobutton", "TSpinbox", "Treeview", "TNotebook.Tab"):
                    style.configure(s, font=self.ui_font)
                style.configure("Treeview.Heading", font=self.ui_font)
            except Exception:
                pass
    def _make_text(self, parent, **kw):
        """tk.Text with the proven ui_font (see _setup_fonts rules)."""
        if self.ui_font is not None:
            kw["font"] = self.ui_font
        elif self.mono_font is not None:
            kw["font"] = self.mono_font
        # CJK 字形的实际高度常等于字体的 linespace，X11 上的 song ti 等
        # 甚至更紧；不给换行与逻辑行留行距时中文会上下压叠（实测 16px 字
        # 压 16px 行）。给所有文本区补行距：spacing2 管自动换行，1/3 管段。
        kw.setdefault("spacing1", 2)
        kw.setdefault("spacing2", 4)
        kw.setdefault("spacing3", 2)
        return self.tk.Text(parent, **kw)
    def _scrolled_text(self, parent, **kw):
        box = self._make_text(parent, **kw)
        box.pack(side="left", fill="both", expand=True)
        scroll = self.ttk.Scrollbar(parent, command=box.yview)
        scroll.pack(side="right", fill="y")
        box.configure(yscrollcommand=scroll.set)
        return box
    def _set_text(self, box, text, keep_view=False):
        try:
            yview = box.yview() if keep_view else None
            state = str(box.cget("state"))
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.configure(state=state)
            if yview is not None:
                box.yview_moveto(yview[0])
        except Exception:
            pass
    def _build_poll_tab(self, title, hint, refresh_cb):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text=title)
        bar = self.ttk.Frame(tab)
        bar.pack(side="top", fill="x", pady=(0, 4))
        self.ttk.Button(bar, text="手动刷新", command=refresh_cb).pack(side="left", padx=2)
        auto_var = self.tk.BooleanVar(value=True)
        self.ttk.Checkbutton(bar, text="自动刷新(2s)", variable=auto_var).pack(side="left", padx=8)
        status_var = self.tk.StringVar(value="就绪")
        self.ttk.Label(bar, textvariable=status_var, foreground="#555555").pack(side="left", padx=8)
        self.ttk.Label(bar, foreground="#888888", text=hint).pack(side="left", padx=8)
        body = self.ttk.Frame(tab)
        body.pack(side="top", fill="both", expand=True)
        box = self._scrolled_text(body, wrap="none", height=24)
        box.configure(state="disabled")
        # Callers may inject their own strip into the tab, above this body.
        tab._poll_body = body
        return tab, box, status_var, auto_var
    def _add_field(self, parent, row, key, label, store, choices=None, width=16, sticky="w"):
        """One label + entry/combo grid row; the StringVar lands in store."""
        self.ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=2)
        var = self.tk.StringVar()
        if choices:
            widget = self.ttk.Combobox(parent, textvariable=var, width=width, values=choices)
        else:
            widget = self.ttk.Entry(parent, textvariable=var, width=width)
        widget.grid(row=row, column=1, sticky=sticky, padx=4)
        store[key] = var
        return widget
    def _build_toolbar(self):
        bar = self.ttk.Frame(self.root, padding=4)
        bar.pack(side="top", fill="x")
        self.ttk.Label(bar, text="配置文件").pack(side="left")
        self.cfg_path_var = self.tk.StringVar()
        self.ttk.Entry(bar, textvariable=self.cfg_path_var, width=28).pack(side="left", padx=2)
        for label, cmd, pad in (("打开", self._open_dialog, 2),
                                ("新建", self._new_config, 2),
                                ("另存为", self._save_dialog, 2),
                                ("开始", self.start, 8),
                                ("停止", self.stop, 2)):
            self.ttk.Button(bar, text=label, command=cmd).pack(side="left", padx=pad)
        self.ttk.Checkbutton(bar, text="续跑(不勾=新建 run 目录, 迭代从 0)",
                             variable=self.resume_var
                             ).pack(side="left", padx=8)
    # layout
    def _build_menubar(self):
        bar = self.tk.Menu(self.root)
        mfile = self.tk.Menu(bar, tearoff=0)
        for label, accel, cmd in (("打开配置…", "Ctrl+O", self._open_dialog),
                                  ("新建配置", "Ctrl+N", self._new_config),
                                  ("另存为…", "Ctrl+S", self._save_dialog)):
            mfile.add_command(label=label, accelerator=accel, command=cmd)
        mfile.add_separator()
        mfile.add_command(label="退出", command=self._on_close)
        bar.add_cascade(label="文件", menu=mfile)
        medit = self.tk.Menu(bar, tearoff=0)
        medit.add_command(label="导入 ADE L 变量/输出", command=self._import_ade)
        medit.add_separator()
        medit.add_command(label="撤销变量编辑", accelerator="Ctrl+Z", command=self._vars_undo)
        medit.add_command(label="重做变量编辑", accelerator="Ctrl+Y", command=self._vars_redo)
        bar.add_cascade(label="编辑", menu=medit)
        mview = self.tk.Menu(bar, tearoff=0)
        for label, attr in (("环境", "env_tab"), ("设计", "design_tab"),
                            ("变量", "vars_tab"), ("目标", "targets_tab"),
                            ("仿真设置", "simset_tab"),
                            ("进度", "progress_tab"), ("结果", "result_tab"),
                            ("工艺", "tech_tab"),
                            ("EM 对齐", "em_align_tab"), ("EM 融合", "em_tab")):
            mview.add_command(label=label, command=lambda a=attr: self._show_tab(a))
        bar.add_cascade(label="视图", menu=mview)
        mrun = self.tk.Menu(bar, tearoff=0)
        mrun.add_command(label="开始", accelerator="F5", command=self.start)
        mrun.add_command(label="停止", accelerator="F6", command=self.stop)
        mrun.add_separator()
        mrun.add_checkbutton(label="mock 仿真", variable=self.mock_var)
        mrun.add_checkbutton(label="续跑(沿用输出目录)", variable=self.resume_var)
        bar.add_cascade(label="运行", menu=mrun)
        mhelp = self.tk.Menu(bar, tearoff=0)
        for label, cmd in (("使用说明", self._show_help),
                           ("算法说明", self._show_algo_help),
                           ("参数说明", self._show_param_help),
                           ("关于", self._show_about)):
            mhelp.add_command(label=label, command=cmd)
        bar.add_cascade(label="帮助", menu=mhelp)
        self.root.config(menu=bar)
        for seq, cb in (("<F5>", lambda _e: self.start()),
                        ("<F6>", lambda _e: self.stop()),
                        ("<Control-o>", lambda _e: self._open_dialog()),
                        ("<Control-n>", lambda _e: self._new_config()),
                        ("<Control-s>", lambda _e: self._save_dialog()),
                        ("<Control-z>", self._on_undo_key),
                        ("<Control-y>", self._on_redo_key),
                        ("<Control-Shift-Z>", self._on_redo_key),
                        ("<Delete>", self._on_delete_key),
                        ("<BackSpace>", self._on_delete_key)):
            self.root.bind(seq, cb)
    def _show_tab(self, attr):
        tab = getattr(self, attr, None)
        if tab is not None:
            self.notebook.select(tab)
    def _show_help(self):
        self._show_text_dialog("使用说明", (
            "基本流程:\n  1. 设计页填写状态目录, 点「导入 ADE L 变量/输出」\n"
            "  2. 变量页区分: 优化变量(参与搜索) / 固定变量(不参与, 值直接写入仿真)\n"
            "  3. 目标页定义约束, 「添加上下界」一键生成上下两条约束\n"
            "  4. 菜单「运行-开始」启动; 进度页看变量柱状图/日志/动态曲线\n\n"
            "变量页快捷键:\n  Delete/Backspace  删除选中的变量行\n"
            "  Ctrl+Z            撤销上一步变量编辑 (删除可恢复)\n"
            "  Ctrl+Y            重做 (Ctrl+Shift+Z 同效)\n\n"
            "动态曲线: 工具条可自选横轴与纵轴。\n  横轴: 频率 / 迭代 / 任一优化变量\n"
            "  纵轴: 输出值(曲线叠加) / 输出@频点 / 总目标 / 最优目标 /\n"
            "        全部变量(归一化) / 任一优化变量\n"
            "  颜色: 最优=红, 最近=蓝, 其余按迭代由旧到新渐变。\n\n"
            "变量柱状图: 蓝色填充=最近迭代取值;\n"
            "  醒目红线=最优变量组合在该变量上的取值, 便于对照当前偏离。\n"
            "算法选择: 见菜单「帮助-算法说明」; 各参数含义: 「帮助-参数说明」。\n\n"
            "模块名称对照:\n  工具栏/菜单栏/状态栏, 环境页/设计页/变量页/目标页/\n"
            "  进度页(收敛图/变量柱状图/日志栏/动态曲线栏)/结果页\n"
            "  仿真设置页: ADE 分析/网表 parameters/模型文件/目标频段覆盖,\n"
            "    只读实时展示, 2 秒轮询自动刷新 (也可手动刷新)\n\n"
            "运行机制: 启动时读取一次网表快照, 每轮迭代只改写网表的"
            " parameters 行并直接运行 spectre, 不重新生成网表。\n"
            "  设计页勾选「开始前自动生成网表」时, 启动前先调用 netlist_create;\n"
            "  「立即生成网表」按钮可随时单独生成。EM 对齐页做两阶段对齐。\n"
            "  因此第一轮约 7 秒(求值服务冷启动), 之后每轮约 0.5 秒。\n"
            "注意: 优化期间可以开着 Virtuoso 和 ADE L 查看原理图。\n"
            "  本工具对 ADE L 只读, 仿真与求值都是独立进程。\n"
            "  但优化进行中不要改原理图/变量/输出(快照已缓存)。\n"
        ))
    def _show_algo_help(self):
        try:
            from algorithm import help_text
            text = help_text()
        except Exception as exc:
            text = "算法说明加载失败: %s" % exc
        self._show_long_text("算法说明", text)
    def _show_param_help(self):
        """Field reference for 搜索设置 / 运行选项 / 历史与续跑."""
        self._show_long_text("参数说明", (
            "搜索设置详细说明（取值 / 影响）—— 目标页底部\n\n"
            "最大迭代 max_iter —— 整数 ≥1。累计到这么多轮停止（stop_reason=max_iterations）。默认 100。续跑时沿用此前迭代数：历史 N 次 + 本次 (max_iter-N) 次。\n"
            "停滞耐心 patience —— 整数 ≥1。连续这么多轮无实质改进时触发停滞动作（重启/踢点）；重启次数用完仍无改进则停止（stop_reason=no_improvement）。默认 10。\n"
            "改进阈值 improve_rel —— 小数 0~1。相对改善 < improve_rel 视为“无改进”。默认 0.001。目标噪声大时可调大（如 0.01），避免把噪声误判为改进。\n"
            "失败上限 fail_limit —— 整数 ≥1。连续仿真失败达到即停止（stop_reason=sim_failures）。默认 5。基础设施类错误（license/网表/求值服务）会免费重试一次且不计入。\n"
            "最大重启 max_restarts —— 整数 ≥0。停滞时允许的最大重启次数。默认 2。重启围绕当前最优点重新布点（带衰减扰动 + 少量随机移民）。\n"
            "随机种子 seed —— 整数（含 0）。同种子 + 同配置 → 结果可复现。留空=每次不同。默认 0。\n"
            "搜索策略 strategy —— 取值见「算法说明」。另有隐藏项可写进 JSON 的 settings：mode（scalar/spec/pareto）、portfolio（成员配置）、noise_sigma（噪声估计）、init_points（预热 CSV）。\n\n"
            "运行选项详细说明 —— 仿真设置页\n\n"
            "保存原始数据 save_raw —— 1（默认）：每轮迭代保留 psf/ 原始数据，可用 ViVA 打开复查波形；代价是磁盘占用大（每轮数 MB）。0：提取输出曲线后不保留原始数据，只留 result.txt 中的曲线。长时间大规模优化建议设 0。\n"
            "仿真超时 sim_timeout —— 正数，秒。单轮 spectre 超过该时间判为 TIMEOUT 失败并终止进程。默认 3600。设小可防挂死占用 license；但小于真实仿真耗时会误杀。实际单轮耗时可从进度页日志的“耗时”估计（求值循环约 0.5 秒/次）。\n\n"
            "历史数据与续跑机制 —— 输出目录 + 「续跑」勾选\n\n"
            "1) 精确续跑：输出目录中的 snapshot*.json（约每 10 轮写一次）+ events.jsonl 可逐位还原算法内部状态（种群 / RNG / 代理模型），再补演快照之后的事件。续跑结果与不间断跑完全一致（回归测试保证）。\n"
            "2) 历史热启动（无快照可还原时自动启用）：所有历史成功评测（变量+目标值）被注入算法，且不再重新仿真——TPE 进 Parzen 核密度；DE/L-SHADE 填入种群槽位；NSGA-II 进初始父代池与 Pareto 档案；CMA-ES 均值移到历史最优；单纯形用历史最优点重建顶点；portfolio 每条成员线都接收全部历史。失败评测不注入（惩罚值会污染代理模型）。\n"
            "3) 预热布点 settings.init_points：指向一个 CSV，每行一组变量值（列序=variables 顺序，工程计数法可写）。这些点会作为第一批被真实仿真（与历史注入不同——需要仿真预算），适合手工放几个经验设计点。\n"
            "4) 预算一致：历史点计入评测数（eval_count），L-SHADE 种群缩减、portfolio 的 UCB 配额等预算感知机制据此分配剩余预算，与运行循环一致。\n"
        ))
    def _show_long_text(self, title, text):
        """Scrollable read-only window for long help."""
        win = self.tk.Toplevel(self.root)
        win.title(title)
        win.geometry("900x620")
        self.ttk.Button(win, text="关闭", command=win.destroy).pack(
            side="bottom", pady=4)
        frame = self.ttk.Frame(win)
        frame.pack(side="top", fill="both", expand=True)
        box = self._scrolled_text(frame, wrap="word")
        box.insert("1.0", text)
        box.configure(state="disabled")
        return win
    def _show_about(self):
        self._dialog("关于",
                     "Virtuoso Optimizer\n"
                     "Version %s · MIT\n"
                     "spectre 直连快速引擎 (engine: fast)\n"
                     "ADE L 状态驱动, 无 Maestro 依赖" % VERSION)
    def _build_tabs(self):
        self.notebook = self.ttk.Notebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True, padx=4, pady=4)
        # Build order follows data dependencies (设计页 first: it owns
        # self.fields). Display order is set afterwards by _reorder_tabs.
        self._build_design_tab()
        self._build_vars_tab()
        self._build_targets_tab()
        self._build_env_tab()
        self._build_tech_tab()
        self._build_simset_tab()
        self._build_em_tab()
        self._build_em_align_tab()
        self._build_progress_tab()
        self._build_result_tab()
        self._em_hook = None
        try:
            import librf_opt
            self._em_hook = librf_opt.register(self)
        except Exception as exc:
            print("librf_opt 未加载 (版图 EM 页不可用): %s" % exc)
        self._reorder_tabs()
    # 环境 → 设计 → 变量 → 目标 → 仿真设置 → 进度 → 结果 → 工艺
    #   → 版图 EM → EM 对齐 → EM 融合
    TAB_ORDER = ("环境", "设计", "变量", "目标", "仿真设置", "进度", "结果",
                 "工艺", "版图 EM", "EM 对齐", "EM 融合")
    def _reorder_tabs(self):
        """Put the notebook pages in the release order.

        Pages are built in dependency order; ttk moves an existing tab
        when it is inserted again. Unknown pages keep their position at
        the end so a missing/extra module never hides a page.
        """
        wanted = list(self.TAB_ORDER)
        placed = []
        pos = 0
        for want in wanted:
            for tab in self.notebook.tabs():
                if tab in placed:
                    continue
                try:
                    text = self.notebook.tab(tab, "text")
                except Exception:
                    continue
                if text.strip() != want:
                    continue
                self.notebook.insert(pos, tab, text=text)
                placed.append(tab)
                pos += 1
                break
    def _build_status(self):
        self.status_var = self.tk.StringVar(value="就绪")
        self.ttk.Label(self.root, textvariable=self.status_var,
                       anchor="w", padding=2).pack(side="bottom", fill="x")
    def _build_design_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="设计")
        self.design_tab = tab
        self.fields = {}
        keys = [
            ("name", "名称"), ("library", "库"), ("cell", "单元"), ("view", "视图"),
            ("netlist_view", "网表视图"),
            ("state", "状态"), ("state_dir", "状态目录"), ("work_dir", "工作目录"),
            ("sim_root", "网表根目录"), ("netlist", "网表"), ("virtuoso_bin", "virtuoso 路径"),
            ("output_dir", "输出目录"), ("analyses", "分析"),
        ]
        choices = {
            "view": ("schematic", "symbol", "layout"),
            "netlist_view": ("schematic", "config"),
            "analyses": ("sp", "ac", "dc", "tran", "noise", "pss"),
        }
        browse = ("state_dir", "work_dir", "output_dir", "sim_root", "netlist")
        for row, (key, label) in enumerate(keys):
            chs = choices.get(key)
            self._add_field(tab, row, key, label, self.fields, chs, 57 if chs else 60, "we")
            col = 2
            if key in browse:
                self.ttk.Button(tab, text="浏览", command=lambda k=key: self._browse_path(k)).grid(row=row, column=col, padx=4)
                col += 1
            if key == "state_dir":
                self.ttk.Button(tab, text="探查", command=self._explore_state).grid(row=row, column=col, padx=4)
        btns = self.ttk.Frame(tab)
        btns.grid(row=len(keys), column=0, columnspan=4, sticky="w", pady=8)
        self.ttk.Button(btns, text="导入 ADE L 变量/输出", command=self._import_ade).pack(side="left", padx=2)
        self.ttk.Button(btns, text="查找网表", command=self._find_netlist).pack(side="left", padx=2)
        self.auto_netlist_var = self.tk.BooleanVar(value=False)
        self.ttk.Checkbutton(btns, text="开始前自动生成网表",
                             variable=self.auto_netlist_var).pack(side="left", padx=8)
        self.netlist_btn = self.ttk.Button(btns, text="立即生成网表",
                                           command=self._netlist_now)
        self.netlist_btn.pack(side="left", padx=2)
        self.ttk.Label(btns, text="导入: 变量进变量页, 输出名进目标页下拉框").pack(side="left", padx=8)
        tab.columnconfigure(1, weight=1)
    def _browse_path(self, key):
        if key == "netlist":
            from tkinter import filedialog
            path = filedialog.askopenfilename(parent=self.root,
                                              filetypes=[("网表", "*.scs"), ("全部文件", "*.*")])
            if path:
                self.fields[key].set(path)
            return
        self._browse_dir(key)
    def _find_netlist(self):
        root = os.path.expanduser(self.fields["sim_root"].get().strip() or "~/simulation")
        lib = self.fields["library"].get().strip()
        cell = self.fields["cell"].get().strip()
        base = os.path.join(root, lib, cell) if (lib and cell) else root
        try:
            hits = glob.glob(os.path.join(base, "**", "input.scs"), recursive=True)
        except Exception as exc:
            self._dialog("查找网表", str(exc), error=True)
            return
        hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        if not hits:
            self._dialog("查找网表",
                         "在 %s 下没有找到 input.scs。" % base +
                         "可改「网表根目录」重试，或直接在「网表」栏填路径。", error=True)
            return
        self.fields["netlist"].set(hits[0])
        lines = ["找到 %d 个候选（新→旧）：" % len(hits)]
        for p2 in hits[:20]:
            lines.append("  " + p2)
        lines.append("")
        lines.append("已把最新的一个填入「网表」栏；要换用请手动改。")
        self._show_text_dialog("查找网表", chr(10).join(lines))
    def _netlist_cfg(self):
        """Build the subset of cfg netlist_create needs, without validation."""
        cfg = self._collect_cfg(validate=False)
        if cfg is None:
            return None
        cfg = dict(cfg)
        # 「立即生成」= 强制执行, 不受复选框的 auto 语义影响。
        cfg["auto_netlist"] = "always"
        view = ""
        try:
            view = self.fields["netlist_view"].get().strip()
        except Exception:
            pass
        if view:
            cfg["netlist_view"] = view
        return cfg
    def _netlist_now(self):
        if getattr(self, "_netlist_busy", False):
            self._dialog("生成网表", "正在生成，请稍候。", error=True)
            return
        cfg = self._netlist_cfg()
        if cfg is None:
            return
        if not (cfg.get("cell") or "").strip() and not (cfg.get("state_dir") or "").strip():
            self._dialog("生成网表",
                         "请先填写「单元」，或填写「状态目录」（用于读取 ADE 状态）。",
                         error=True)
            return
        self._netlist_busy = True
        try:
            self.netlist_btn.configure(state="disabled")
        except Exception:
            pass
        self._set_status("正在生成网表…")
        threading.Thread(target=self._netlist_worker, args=(cfg,), daemon=True).start()
    def _netlist_worker(self, cfg):
        """Background thread: never touch Tk; report through the queue."""
        try:
            try:
                import netlist_create as _nc
            except Exception as exc:
                raise RuntimeError("netlist_create 模块不可用: %s" % exc)
            fn = getattr(_nc, "ensure_netlist", None)
            if fn is None:
                raise RuntimeError("netlist_create 缺少 ensure_netlist()")
            rep = fn(cfg)
        except Exception as exc:
            import traceback
            self.queue.put({"type": "netlist_now", "ok": False,
                            "error": "%s: %s" % (type(exc).__name__, exc),
                            "traceback": traceback.format_exc()})
            return
        self.queue.put({"type": "netlist_now", "ok": True, "report": rep})
    def _handle_netlist_now(self, ev):
        self._netlist_busy = False
        try:
            self.netlist_btn.configure(state="normal")
        except Exception:
            pass
        if not ev.get("ok"):
            msg = ev.get("error") or "未知错误"
            self._set_status("生成网表失败: %s" % msg)
            self._log("生成网表失败: %s\n" % msg)
            self._dialog("生成网表失败", msg, error=True)
            return
        rep = ev.get("report") or {}
        text = netlist_report_text(rep)
        self._log("[生成网表] %s\n" % text.replace(chr(10), " | "))
        status = str(rep.get("status") or "")
        design = rep.get("design") if isinstance(rep.get("design"), dict) else {}
        if status in ("ok", "fallback"):
            path = rep.get("path") or rep.get("expected")
            if path and status == "ok":
                try:
                    self.fields["netlist"].set(str(path))
                except Exception:
                    pass
            view = design.get("view")
            if view:
                try:
                    self.fields["netlist_view"].set(str(view))
                except Exception:
                    pass
        if status == "ok":
            self._set_status("网表已生成: %s" % (rep.get("path") or rep.get("expected") or ""))
            self._dialog("生成网表", text)
        elif status == "fallback":
            self._set_status("网表生成失败，沿用旧网表")
            self._dialog("生成网表", text, error=True)
        else:
            self._set_status("网表生成未完成: %s" % status)
            self._dialog("生成网表", text, error=True)
    def _env_probe(self):
        """Fill empty rows by probing this machine (no absolute paths)."""
        import optimizer as _opt
        filled = []
        for name, var in self.env_fields.items():
            if not var.get().strip():
                val = os.environ.get(name, "")
                if val:
                    var.set(val)
                    filled.append(name)
        tools = _opt.discover_tools()
        sp = (tools.get("spectre") or {}).get("path")
        if sp and not self.spectre_bin_var.get().strip():
            self.spectre_bin_var.set(sp)
            filled.append("spectre_bin")
        vb = (tools.get("virtuoso") or {}).get("path")
        if vb and not self.fields["virtuoso_bin"].get().strip():
            self.fields["virtuoso_bin"].set(vb)
            filled.append("virtuoso_bin")
        nl = chr(10)
        msg = "自动探测：本机探查，填入 %d 项空行。" % len(filled)
        msg += nl + "（未填的栏保持空白=使用系统默认；程序路径按本机实测填入）"
        msg += nl + nl + _opt.tool_report(tools)
        self._dialog("自动探测", msg)
    def _env_report(self):
        import subprocess as _sp
        import optimizer as _opt
        nl = chr(10)
        lines = ["环境自检（本机软件探查 + 进程环境）", ""]
        tools = _opt.discover_tools()
        lines.append("程序探查结果（自动，无需填写绝对路径）:")
        for ln in _opt.tool_report(tools).splitlines():
            lines.append("  " + ln)
        lines.append("")
        spec = _opt.resolve_prog("spectre", self.spectre_bin_var.get().strip() or "spectre")
        lines.append("实际调用 spectre = %s" % spec)
        try:
            out = _sp.check_output([spec, "-v"], stderr=_sp.STDOUT, timeout=20)
            head = out.decode("utf-8", "replace").strip().splitlines()[:3]
            lines.append("spectre -v: %s" % (" / ".join(head)))
        except Exception as exc:
            lines.append("spectre -v 失败: %s" % exc)
        lines.append("")
        lines.append("面板取值（空=继承系统默认）:")
        for name, var in self.env_fields.items():
            cur = var.get().strip()
            if cur:
                lines.append("  %s = %s  [覆盖]" % (name, cur))
            else:
                lines.append("  %s = (继承) %s" % (name, os.environ.get(name, "(未设置)")))
        vbin = _opt.resolve_prog("virtuoso", self.fields["virtuoso_bin"].get().strip() or "virtuoso")
        lines.append("  virtuoso 程序 = %s" % vbin)
        papp = self.path_append_var.get().strip()
        lines.append("  PATH 追加 = %s" % (papp or "(无)"))
        lines.append("")
        deck = self.fields["netlist"].get().strip()
        if deck:
            ok = os.path.exists(os.path.expanduser(deck))
            lines.append("网表: %s (%s)" % (deck, "存在" if ok else "不存在"))
        else:
            lines.append("网表: 未指定（自动查找）；找不到时点设计页「查找网表」")
        self._show_text_dialog("自检报告", nl.join(lines))
    def _browse_dir(self, key):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self.root,
                                       initialdir=self.fields[key].get().strip() or os.path.expanduser("~"))
        if path:
            self.fields[key].set(path)
    def _edit_fields(self, parent, fields, store):
        for col, (key, label) in enumerate(fields):
            self.ttk.Label(parent, text=label).grid(row=0, column=col, padx=2)
            var = self.tk.StringVar()
            self.ttk.Entry(parent, textvariable=var, width=16).grid(row=1, column=col, padx=2)
            store[key] = var
    def _build_vars_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="变量")
        self.vars_tab = tab
        # optimized variables (take part in the search)
        self.ttk.Label(tab, text="优化变量（参与优化搜索）").grid(row=0, column=0, columnspan=4, sticky="w")
        self.var_tree = self.ttk.Treeview(tab, columns=("name", "init", "min", "max"), show="headings", height=7)
        for col, label, width in (("name", "名称", 160), ("init", "初值", 120),
                                  ("min", "最小", 120), ("max", "最大", 120)):
            self.var_tree.heading(col, text=label)
            self.var_tree.column(col, width=width, anchor="w")
        self.var_tree.grid(row=1, column=0, columnspan=4, sticky="nsew")
        self.var_tree.bind("<<TreeviewSelect>>", self._var_select)
        edit = self.ttk.Frame(tab)
        edit.grid(row=2, column=0, columnspan=4, sticky="we", pady=4)
        self.var_edit = {}
        self._edit_fields(edit, (("name", "名称"), ("init", "初值"), ("min", "最小"), ("max", "最大")), self.var_edit)
        btns = self.ttk.Frame(tab)
        btns.grid(row=3, column=0, columnspan=4, sticky="w")
        for text, cmd in (("添加", self._var_add), ("更新", self._var_update),
                          ("删除", self._var_delete), ("移至固定", self._var_move_to_fixed)):
            self.ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=2)
        self.ttk.Label(btns, text="快捷键: Del=删除 · Ctrl+Z=撤销 · Ctrl+Y=重做", foreground="#888888").pack(side="left", padx=10)
        # fixed variables (value is written to the sim as is)
        self.ttk.Label(tab, text="固定变量（不参与优化，值写入仿真）").grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))
        self.fix_tree = self.ttk.Treeview(tab, columns=("name", "value"), show="headings", height=5)
        for col, label, width in (("name", "名称", 160), ("value", "值", 160)):
            self.fix_tree.heading(col, text=label)
            self.fix_tree.column(col, width=width, anchor="w")
        self.fix_tree.grid(row=5, column=0, columnspan=4, sticky="nsew")
        self.fix_tree.bind("<<TreeviewSelect>>", self._fix_select)
        fedit = self.ttk.Frame(tab)
        fedit.grid(row=6, column=0, columnspan=4, sticky="we", pady=4)
        self.fix_edit = {}
        self._edit_fields(fedit, (("name", "名称"), ("value", "值")), self.fix_edit)
        fbtns = self.ttk.Frame(tab)
        fbtns.grid(row=7, column=0, columnspan=4, sticky="w")
        for text, cmd in (("添加", self._fix_add), ("更新", self._fix_update),
                          ("删除", self._fix_delete), ("移至优化", self._fix_move_to_opt)):
            self.ttk.Button(fbtns, text=text, command=cmd).pack(side="left", padx=2)
        self.ttk.Button(fbtns, text="撤销", command=self._vars_undo).pack(side="left", padx=10)
        self.ttk.Button(fbtns, text="重做", command=self._vars_redo).pack(side="left", padx=2)
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(5, weight=1)
        tab.columnconfigure(0, weight=1)
    def _build_targets_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="目标")
        self.targets_tab = tab
        cols = ("output", "metric", "op", "goal", "goal2", "tol", "weight",
                "band_lo", "band_hi", "freq", "curve_op")
        self.target_tree = self.ttk.Treeview(tab, columns=cols, show="headings", height=8)
        labels = (("output", "输出", 120), ("metric", "指标", 100),
                  ("op", "算子", 60), ("goal", "目标", 80),
                  ("goal2", "目标2(range上限)", 95),
                  ("tol", "容差", 80), ("weight", "权重", 60),
                  ("band_lo", "频段下", 80), ("band_hi", "频段上", 80),
                  ("freq", "频率", 80), ("curve_op", "限值比较", 75))
        for col, label, width in labels:
            self.target_tree.heading(col, text=label)
            self.target_tree.column(col, width=width, anchor="w")
        self.target_tree.grid(row=0, column=0, columnspan=6, sticky="nsew")
        self.target_tree.bind("<<TreeviewSelect>>", self._target_select)
        edit = self.ttk.Frame(tab)
        edit.grid(row=1, column=0, columnspan=6, sticky="we", pady=4)
        self.target_edit = {}
        fields = (("output", "输出", 16, ()),
                  ("metric", "指标", 12,
                   ("band_min", "band_max", "band_flatness",
                    "band_mean", "value_at", "scalar")),
                  ("op", "算子", 6, (">=", "<=", "==", "range")),
                  ("goal", "目标", 8, ()), ("goal2", "目标2(range上限)", 10, ()),
                  ("tol", "容差", 8, ()), ("weight", "权重", 8, ()),
                  ("band_lo", "频段下", 8, ()), ("band_hi", "频段上", 8, ()),
                  ("freq", "频率", 8, ()),
                  ("curve_op", "限值比较", 10, ("", "above", "below", "match")))
        for col, (key, label, width, choices) in enumerate(fields):
            self.ttk.Label(edit, text=label).grid(row=0, column=col, padx=2)
            var = self.tk.StringVar()
            if choices or key == "output":
                widget = self.ttk.Combobox(edit, textvariable=var, width=width, values=choices)
            else:
                widget = self.ttk.Entry(edit, textvariable=var, width=width)
            widget.grid(row=1, column=col, padx=2, sticky="we")
            self.target_edit[key] = var
            if key == "output":
                self.target_out_combo = widget
        self.ttk.Label(edit, text="限值曲线JSON").grid(row=2, column=0, padx=2)
        var = self.tk.StringVar()
        self.ttk.Entry(edit, textvariable=var).grid(row=2, column=1, columnspan=8, sticky="we", padx=2)
        self.target_edit["limit"] = var
        self.ttk.Button(edit, text="从CSV载入", command=self._target_limit_from_csv).grid(row=2, column=9, padx=2)
        self.ttk.Button(edit, text="字段说明", command=self._target_help).grid(row=2, column=10, padx=2)
        btns = self.ttk.Frame(tab)
        btns.grid(row=2, column=0, columnspan=6, sticky="w")
        for text, cmd in (("添加", self._target_add),
                          ("添加上下界", self._target_add_window),
                          ("更新", self._target_update),
                          ("删除", self._target_delete)):
            self.ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=2)
        help_line = self.ttk.Label(
            tab, anchor="w", foreground="#555555",
            text=('一条目标=对一条曲线提要求，多条目标加权求和=总误差，0 即全部达标。'
                  '算子=range 时目标2=区间上限；「添加上下界」时目标2=窗口上界。'
                  '限值比较 above/below/match：曲线逐点与限值线比较'
                  '(above=须高于限值线, below=须低于, match=须贴合)，'
                  '限值曲线 JSON 形如 {"x":["1G","2G"],"y":["35","45"]}，'
                  '一般用「从CSV载入」自动生成。点「字段说明」看全部字段。'))
        help_line.grid(row=3, column=0, columnspan=6, sticky="we", pady=(6, 0))
        self.ttk.Label(btns, text="上下界: 目标=下界 目标2=上界").pack(side="left", padx=8)
        # 搜索设置（原「设置」页的搜索组）：目标定义优化问题，这里定义怎么搜。
        self.set_fields = {}
        sbar = self.ttk.Frame(tab)
        sbar.grid(row=4, column=0, columnspan=6, sticky="we", pady=(12, 0))
        self.ttk.Label(sbar, text="搜索设置", foreground="#0066cc").grid(
            row=0, column=0, columnspan=8, sticky="w", pady=(0, 2))
        search_keys = (("max_iter", "最大迭代", ()),
                       ("patience", "停滞耐心", ()),
                       ("improve_rel", "改进阈值", ()),
                       ("fail_limit", "失败上限", ()),
                       ("max_restarts", "最大重启", ()),
                       ("seed", "随机种子", ()),
                       ("strategy", "搜索策略",
                        tuple(Settings.allowed_strategies())))
        for col, (key, label, chs) in enumerate(search_keys):
            cell = self.ttk.Frame(sbar)
            cell.grid(row=1, column=col, padx=(0, 8), sticky="w")
            self.ttk.Label(cell, text=label).pack(anchor="w")
            var = self.tk.StringVar()
            if chs:
                widget = self.ttk.Combobox(cell, textvariable=var, width=13,
                                           values=list(chs), state="readonly")
            else:
                widget = self.ttk.Entry(cell, textvariable=var, width=10)
            widget.pack(anchor="w")
            self.set_fields[key] = var
        self.ttk.Label(
            sbar, foreground="#555555",
            text=("策略选型见菜单「帮助 → 算法说明」，各参数含义见「帮助 → 参数说明」；"
                  "最大迭代是本次会话的预算上限，续跑时沿用历史迭代数。")
        ).grid(row=2, column=0, columnspan=8, sticky="w", pady=(4, 0))
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
    def _build_tech_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="工艺")
        self.tech_tab = _TechTab(self, tab)
    def _build_env_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="环境")
        self.env_tab = tab
        self.env_fields = {}
        self.spectre_bin_var = self.tk.StringVar()
        self.path_append_var = self.tk.StringVar()
        self.ttk.Label(tab, anchor="w", foreground="#555555",
                       text=("软件与 Cadence 环境变量一览（即原 cds_env.sh 的内容）。"
                             "留空=用系统默认（默认 shell 已注册的值）；填写=仅对仿真子进程覆盖。"
                             "「自动探测」把当前环境已有值填进空行；「自检报告」验证软件能否解析。")
                       ).grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, 6))
        rows = (
            ("spectre_bin", "spectre 程序", "仿真器命令或绝对路径（空=spectre）"),
            ("virtuoso_bin", "virtuoso 程序", "求值服务用；与设计页「virtuoso 路径」同一项"),
            ("CADHOME", "CADHOME", "Cadence 安装根目录"),
            ("CDS", "CDS", "Virtuoso/IC 安装目录（如 .../IC618）"),
            ("CDSDIR", "CDSDIR", "同 CDS（兼容别名）"),
            ("CDS_INST_DIR", "CDS_INST_DIR", "同 CDS（兼容别名）"),
            ("CDSHOME", "CDSHOME", "同 CDS（兼容别名）"),
            ("CDS_ROOT", "CDS_ROOT", "同 CDS（兼容别名）"),
            ("CDSROOT", "CDSROOT", "同 CDS（兼容别名）"),
            ("SPECTRE_HOME", "SPECTRE_HOME", "spectre 安装目录"),
            ("CDS_LIC_FILE", "CDS_LIC_FILE", "license 文件路径"),
            ("CDS_LIC_ONLY", "CDS_LIC_ONLY", "只用 Cadence license（填 1）"),
            ("CDS_Netlisting_Mode", "CDS_Netlisting_Mode", "网表模式（Analog）"),
            ("CDS_LOAD_ENV", "CDS_LOAD_ENV", "（CWD）"),
            ("CDS_AUTO_64BIT", "CDS_AUTO_64BIT", "（ALL）"),
            ("VIRTUOSO_BIN", "VIRTUOSO_BIN", "virtuoso 路径的环境变量形式"),
            ("OCEAN_BIN", "OCEAN_BIN", "ocean 路径（本工具不使用，供对照）"),
            ("path_append", "PATH 追加", "追加到 PATH 末尾（冒号分隔）"))
        specials = {"virtuoso_bin": self.fields["virtuoso_bin"],
                    "spectre_bin": self.spectre_bin_var,
                    "path_append": self.path_append_var}
        for i, (key, label, note) in enumerate(rows):
            r = 1 + i
            self.ttk.Label(tab, text=label).grid(row=r, column=0, sticky="e", padx=4, pady=1)
            var = specials.get(key)
            if var is None:
                var = self.tk.StringVar()
                self.env_fields[key] = var
            self.ttk.Entry(tab, textvariable=var, width=42).grid(row=r, column=1, sticky="we", padx=4)
            self.ttk.Label(tab, text=note, anchor="w", foreground="#666666").grid(row=r, column=2, sticky="we", padx=4)
        r2 = 1 + len(rows)
        btns = self.ttk.Frame(tab)
        btns.grid(row=r2, column=0, columnspan=3, sticky="w", pady=(8, 2))
        for text, cmd in (("自动探测", self._env_probe), ("自检报告", self._env_report)):
            self.ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=2)
        self.ttk.Label(tab, text="其他附加变量").grid(row=r2 + 1, column=0, sticky="e", padx=4)
        self.env_extra_text = self._make_text(tab, wrap="none", height=5, width=60)
        self.env_extra_text.grid(row=r2 + 1, column=1, columnspan=2, sticky="nsew", padx=4, pady=2)
        self.ttk.Label(tab, anchor="w", foreground="#666666",
                       text="KEY=VALUE 每行一个；# 开头为注释"
                       ).grid(row=r2 + 2, column=1, columnspan=2, sticky="w", padx=4)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(r2 + 1, weight=1)
    # sim-settings live display (read-only)
    def _build_simset_tab(self):
        tab, self.simset_text, self.simset_status_var, self.simset_auto_var = \
            self._build_poll_tab(
                "仿真设置",
                "只读展示: 在 ADE 里改设置即可, 本页自动跟随;优化器只展示不修改",
                self._simset_refresh)
        self.simset_tab = tab
        # 运行选项（原「设置」页）：属于仿真执行，放这一页。
        opt = self.ttk.Frame(tab)
        opt.pack(side="top", fill="x", pady=(0, 4), before=tab._poll_body)
        self.ttk.Label(opt, text="运行选项", foreground="#0066cc").pack(
            side="left", padx=(0, 10))
        self.ttk.Label(opt, text="保存原始数据").pack(side="left")
        raw_var = self.tk.StringVar()
        self.ttk.Combobox(opt, textvariable=raw_var, width=5, state="readonly",
                          values=("1", "2", "0")).pack(side="left", padx=(2, 12))
        self.fields["save_raw"] = raw_var
        self.ttk.Label(opt, text="仿真超时(秒)").pack(side="left")
        tout_var = self.tk.StringVar()
        self.ttk.Entry(opt, textvariable=tout_var, width=10).pack(side="left", padx=2)
        self.fields["sim_timeout"] = tout_var
        self.ttk.Label(opt, text="1=保留全部 psf 2=只留关键轮 0=不留",
                       foreground="#888888").pack(side="left", padx=10)
        self._simset_sig = self._simset_last_text = None
        self._simset_closed = False
        self._simset_after_id = None
        self._simset_nl_cache = None
        self._simset_sig_fail_n = 0
        self._simset_refresh(force=True)
        self._simset_after_id = self.root.after(2000, self._simset_tick)
    def _simset_netlist_path(self, rescan=False):
        """The deck a real run will read, from the shared resolver.

        The 仿真设置 page exists to show the settings of the deck that will
        actually be simulated, so discovery (explicit path, newest
        input.scs, netlist_view preference) must be the runner's rules,
        not a second copy that picks a different view.
        """
        def field(key):
            try:
                return self.fields[key].get().strip()
            except Exception:
                return ""
        raw = field("netlist")
        path = os.path.expanduser(raw) if raw else ""
        root = os.path.expanduser(field("sim_root") or "~/simulation")
        lib, cell = field("library"), field("cell")
        view, nl_view = field("view"), field("netlist_view")
        key = (path, root, lib, cell, view, nl_view)
        now = time.time()
        cache = getattr(self, "_simset_nl_cache", None)
        if (not rescan) and cache and cache.get("key") == key:
            age = now - cache.get("t", 0)
            if age < 10.0:
                return cache.get("path")
            cached = cache.get("path")
            if cached and os.path.isfile(cached):
                parent = cache.get("dir") or os.path.dirname(cached)
                try:
                    dmt = os.path.getmtime(parent)
                except OSError:
                    dmt = None
                if dmt is not None and dmt == cache.get("dir_mtime"):
                    cache["t"] = now
                    return cached
        cfg = {"netlist": path, "sim_root": root, "library": lib,
               "cell": cell, "view": view, "netlist_view": nl_view}
        try:
            hit = resolve_netlist_path(cfg) or None
        except Exception:
            # Display only: a deck disappearing mid-scan must not break
            # the 2 s refresh chain.
            hit = None
        dir_mtime = None
        if hit:
            try:
                dir_mtime = os.path.getmtime(os.path.dirname(hit))
            except OSError:
                dir_mtime = None
        self._simset_nl_cache = {"key": key, "t": now, "path": hit,
                                 "dir": os.path.dirname(hit) if hit else None,
                                 "dir_mtime": dir_mtime}
        return hit
    def _simset_watch_paths(self):
        paths = []
        state_dir = self.fields["state_dir"].get().strip() if self.fields else ""
        if state_dir:
            state_dir = os.path.expanduser(state_dir)
            for name in ("analyses.state", "variables.state", "outputs.state", "modelSetup.state"):
                paths.append(os.path.join(state_dir, name))
        nl = self._simset_netlist_path()
        if nl:
            paths.append(nl)
        return paths
    def _simset_signature(self):
        sig = []
        for path in self._simset_watch_paths():
            try:
                st = os.stat(path)
                sig.append((path, int(st.st_mtime), st.st_size))
            except OSError:
                sig.append((path, None, None))
        try:
            sig.append(("__targets__", _targets_signature(self.target_specs)))
        except Exception:
            n_targets = len(self.target_specs)
            sig.append(("__targets__", n_targets))
        try:
            sig.append(("__netlist_field__", self.fields["netlist"].get().strip()))
        except Exception:
            pass
        return tuple(sig)
    def _simset_collect(self):
        """Gather display inputs. Never raises."""
        notes = []
        state_dir = ""
        info = None
        try:
            state_dir = self.fields["state_dir"].get().strip()
        except Exception:
            state_dir = ""
        if state_dir:
            state_dir = os.path.expanduser(state_dir)
            try:
                info = summarize(state_dir)
            except Exception as exc:
                info = None
                notes.append("summarize 失败: %s" % exc)
        else:
            notes.append("状态目录未填写 (设计页「状态目录」)")
        netlist_path = self._simset_netlist_path()
        netlist_params = None
        netlist_text = ""
        if netlist_path and os.path.isfile(netlist_path):
            try:
                netlist_text = _read_text(netlist_path)
                netlist_params = parse_netlist_parameters(netlist_text)
            except Exception as exc:
                notes.append("网表读取失败: %s" % exc)
                netlist_params = None
        else:
            notes.append("网表未找到 (设计页「网表」或「查找网表」)")
        targets = []
        try:
            targets = [self.target_specs[iid] for iid in self.target_tree.get_children()
                       if iid in self.target_specs]
        except Exception:
            targets = list(getattr(self, "target_specs", {}) or {}).values()
        return state_dir, info, netlist_path, netlist_params, netlist_text, targets, notes
    def _simset_refresh(self, force=False):
        """Rebuild the report text. Skip work when nothing changed."""
        if getattr(self, "_simset_closed", False):
            return
        try:
            sig = self._simset_signature()
        except Exception:
            sig = None
        if sig is None and not force:
            n = getattr(self, "_simset_sig_fail_n", 0) + 1
            self._simset_sig_fail_n = n
            if n % 10 != 0:
                return
        else:
            self._simset_sig_fail_n = 0
        if (not force) and sig is not None and sig == self._simset_sig:
            return
        try:
            parts = self._simset_collect()
            text = build_simset_text(*parts)
        except Exception as exc:
            text = "SIMSET_DISPLAY_ERROR: %s" % exc
        self._simset_sig = sig
        if text == self._simset_last_text and not force:
            return
        self._simset_last_text = text
        if self.simset_text is not None:
            self._set_text(self.simset_text, text, keep_view=not force)
        try:
            self.simset_status_var.set("已刷新 %s" % time.strftime("%H:%M:%S"))
        except Exception:
            pass
    def _poll_text_tick(self, closed_attr, auto_var, refresh, tick, after_attr):
        if getattr(self, closed_attr, False):
            return
        try:
            if auto_var.get():
                refresh(force=False)
        except Exception:
            pass
        try:
            setattr(self, after_attr, self.root.after(2000, tick))
        except Exception:
            setattr(self, after_attr, None)
    def _poll_text_shutdown(self, closed_attr, after_attr):
        setattr(self, closed_attr, True)
        aid = getattr(self, after_attr, None)
        if aid is not None:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
            setattr(self, after_attr, None)
    def _simset_tick(self):
        self._poll_text_tick("_simset_closed", self.simset_auto_var,
                             self._simset_refresh, self._simset_tick,
                             "_simset_after_id")
    def _simset_shutdown(self):
        """Stop the simset poll chain. Safe to call twice."""
        self._poll_text_shutdown("_simset_closed", "_simset_after_id")
    # EM fusion tab (read-only + 2s poll; S4 spec)
    def _build_em_tab(self):
        tab, self.em_text, self.em_status_var, self.em_auto_var = \
            self._build_poll_tab("EM 融合",
                                 "只读: requested/grid/delivered 与告警",
                                 self._em_refresh)
        self.em_tab = tab
        self._em_sig = self._em_last_text = None
        self._em_closed = False
        self._em_after_id = None
        self._em_refresh(force=True)
        self._em_after_id = self.root.after(2000, self._em_tick)
    def _em_collect(self):
        work = "work"
        out_dir = ""
        try:
            work = self.fields["work_dir"].get().strip() or "work"
            out_dir = self.fields["output_dir"].get().strip()
        except Exception:
            pass
        history = []
        try:
            import glob as _g
            out = os.path.expanduser(out_dir or "logs/run")
            paths = sorted(_g.glob(os.path.join(out, "iter_*", "record.json")))
            for p in paths[-HISTORY_LIMIT:]:
                try:
                    with open(p, encoding="utf-8") as f:
                        history.append(json.load(f))
                except Exception:
                    continue
        except Exception:
            pass
        mode = None
        try:
            mode = ((self.extra or {}).get("em_bridge") or {}).get("mode")
        except Exception:
            pass
        return collect_em_model(work_dir=work, out_dir=out_dir, history=history, mode=mode)
    def _em_refresh(self, force=False):
        if getattr(self, "_em_closed", False):
            return
        try:
            sig = em_signature(em_watch_paths(
                self.fields["work_dir"].get().strip() or "work",
                self.fields["output_dir"].get().strip()))
        except Exception:
            sig = None
        if (not force) and sig is not None and sig == self._em_sig:
            return
        try:
            text = build_em_report(self._em_collect())
        except Exception as exc:
            text = "EM_DISPLAY_ERROR: %s" % exc
        self._em_sig = sig
        if text == self._em_last_text and not force:
            return
        self._em_last_text = text
        if self.em_text is not None:
            self._set_text(self.em_text, text, keep_view=not force)
        try:
            self.em_status_var.set("已刷新 %s" % time.strftime("%H:%M:%S"))
        except Exception:
            pass
        self._em_update_statusbar(text)
    def _em_update_statusbar(self, text):
        lbl = getattr(self, "em_status_label", None)
        if lbl is None:
            return
        try:
            one = "EM 融合"
            for line in (text or "").splitlines():
                if "命中率" in line or "状态=" in line:
                    one = line.strip()[:80]
                    break
                if line.startswith("【库统计"):
                    continue
            lbl.configure(text=one)
        except Exception:
            pass
    def _em_tick(self):
        self._poll_text_tick("_em_closed", self.em_auto_var,
                             self._em_refresh, self._em_tick,
                             "_em_after_id")
    def _em_shutdown(self):
        self._poll_text_shutdown("_em_closed", "_em_after_id")
    # EM 两阶段对齐（需求 E / C 的入口）
    def _build_em_align_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="EM 对齐")
        self.em_align_tab = tab
        self.em_align_fields = {}
        self.em_align_extra = {}
        form = self.ttk.Frame(tab)
        form.pack(side="top", fill="x")
        rows = (("stage1_dir", "第一阶段 run 目录", "dir"),
                ("em_curve", "EM 曲线文件", "file"),
                ("em_netlist", "EM 网表", "file"),
                ("output_dir", "输出目录", "dir"),
                ("max_iter", "最大迭代", None),
                ("tol", "匹配容差", None))
        for row, (key, label, kind) in enumerate(rows):
            self.ttk.Label(form, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=2)
            var = self.tk.StringVar()
            self.ttk.Entry(form, textvariable=var, width=64).grid(
                row=row, column=1, sticky="we", padx=4)
            self.em_align_fields[key] = var
            if kind:
                self.ttk.Button(form, text="浏览",
                                command=lambda k=key, t=kind: self._browse_em_align(k, t)
                                ).grid(row=row, column=2, padx=4)
        form.columnconfigure(1, weight=1)
        self.ttk.Label(tab, foreground="#888888",
                       text="EM 曲线文件与 EM 网表至少填一个；输出目录留空=<第一阶段>/em_align；"
                            "最大迭代/容差留空=用优化器默认").pack(anchor="w", pady=(2, 4))
        bar = self.ttk.Frame(tab)
        bar.pack(side="top", fill="x", pady=(0, 4))
        self.em_align_start_btn = self.ttk.Button(bar, text="开始 EM 对齐",
                                                  command=self._start_em_align)
        self.em_align_start_btn.pack(side="left", padx=2)
        self.ttk.Button(bar, text="打开报告目录",
                        command=self._open_em_align_dir).pack(side="left", padx=2)
        self.em_align_status_var = self.tk.StringVar(value="就绪")
        self.ttk.Label(bar, textvariable=self.em_align_status_var,
                       foreground="#555555").pack(side="left", padx=8)
        self.em_align_path_var = self.tk.StringVar(value="")
        self.ttk.Label(bar, textvariable=self.em_align_path_var,
                       foreground="#2266cc").pack(side="left", padx=8)
        body = self.ttk.Frame(tab)
        body.pack(side="top", fill="both", expand=True)
        self.ttk.Label(body, text="进度/日志").pack(anchor="w")
        log_wrap = self.ttk.Frame(body)
        log_wrap.pack(side="top", fill="both", expand=True)
        self.em_align_log_text = self._scrolled_text(log_wrap, wrap="none", height=10)
        self.em_align_log_text.configure(state="disabled")
        self.ttk.Label(body, text="对齐报告 (em_align_report.md)").pack(anchor="w", pady=(4, 0))
        res_wrap = self.ttk.Frame(body)
        res_wrap.pack(side="top", fill="both", expand=True)
        self.em_align_result_text = self._scrolled_text(res_wrap, wrap="none")
        self.em_align_result_text.configure(state="disabled")
    def _browse_em_align(self, key, kind):
        from tkinter import filedialog
        var = self.em_align_fields.get(key)
        if var is None:
            return
        cur = os.path.expanduser(var.get().strip()) or os.path.expanduser("~")
        if kind == "dir":
            path = filedialog.askdirectory(parent=self.root, initialdir=cur)
        else:
            path = filedialog.askopenfilename(
                parent=self.root, initialdir=cur,
                filetypes=[("曲线/数据文件",
                            ("*.txt", "*.csv", "*.json", "*.s2p", "*.s3p", "*.s4p")),
                           ("全部文件", "*.*")])
        if path:
            var.set(path)
    def _collect_em_align(self):
        fields = getattr(self, "em_align_fields", None)
        if not fields:
            return {}
        # 保留 em_align 里本页不显示的键 (band/outputs/max_points/...).
        sec = dict(getattr(self, "em_align_extra", {}) or {})
        for key, var in fields.items():
            text = (var.get() or "").strip()
            if not text:
                continue
            if key == "max_iter":
                try:
                    sec[key] = int(parse_eng(text))
                except Exception:
                    sec[key] = text
            elif key == "tol":
                try:
                    sec[key] = float(parse_eng(text))
                except Exception:
                    sec[key] = text
            else:
                sec[key] = text
        return sec
    def _load_em_align(self, section):
        fields = getattr(self, "em_align_fields", None)
        if not fields:
            return
        sec = section if isinstance(section, dict) else {}
        self.em_align_extra = {k: v for k, v in sec.items() if k not in fields}
        for key, var in fields.items():
            val = sec.get(key)
            var.set("" if val is None else str(val))
        self.em_align_report_dir = ""
        try:
            self.em_align_path_var.set("")
            self.em_align_status_var.set("就绪")
        except Exception:
            pass
        if not getattr(self, "_em_align_running", False):
            try:
                self._set_text(self.em_align_log_text, "")
                self._set_text(self.em_align_result_text, "")
            except Exception:
                pass
    def _em_align_append_log(self, text):
        if not text:
            return
        try:
            box = self.em_align_log_text
            box.configure(state="normal")
            box.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), str(text).rstrip()))
            box.see("end")
            box.configure(state="disabled")
        except Exception:
            pass
    def _start_em_align(self):
        if getattr(self, "_em_align_running", False):
            self._dialog("EM 对齐", "正在运行，请等待完成。", error=True)
            return
        vals = {k: (v.get() or "").strip() for k, v in self.em_align_fields.items()}
        stage1 = os.path.expanduser(vals.get("stage1_dir", ""))
        if not stage1:
            self._dialog("EM 对齐", "请填写第一阶段 run 目录。", error=True)
            return
        if not os.path.isdir(stage1):
            self._dialog("EM 对齐", "第一阶段 run 目录不存在：\n%s" % stage1, error=True)
            return
        curve = os.path.expanduser(vals.get("em_curve", ""))
        netlist = os.path.expanduser(vals.get("em_netlist", ""))
        if not curve and not netlist:
            self._dialog("EM 对齐", "EM 曲线文件与 EM 网表至少填一个。", error=True)
            return
        if curve and not os.path.isfile(curve):
            self._dialog("EM 对齐", "EM 曲线文件不存在：\n%s" % curve, error=True)
            return
        if netlist and not os.path.isfile(netlist):
            self._dialog("EM 对齐", "EM 网表不存在：\n%s" % netlist, error=True)
            return
        max_iter = None
        if vals.get("max_iter"):
            try:
                max_iter = int(parse_eng(vals["max_iter"]))
            except Exception:
                self._dialog("EM 对齐", "最大迭代不是整数：%s" % vals["max_iter"], error=True)
                return
            if max_iter <= 0:
                self._dialog("EM 对齐", "最大迭代必须为正数。", error=True)
                return
        tol = None
        if vals.get("tol"):
            try:
                tol = float(parse_eng(vals["tol"]))
            except Exception:
                self._dialog("EM 对齐", "匹配容差不是数值：%s" % vals["tol"], error=True)
                return
            if tol <= 0:
                self._dialog("EM 对齐", "匹配容差必须为正数。", error=True)
                return
        out_dir = os.path.expanduser(vals.get("output_dir", "")) or None
        cfg = self._collect_cfg(validate=False)
        if cfg is None:
            return
        self._em_align_running = True
        try:
            self.em_align_start_btn.configure(state="disabled")
        except Exception:
            pass
        self._set_text(self.em_align_log_text, "")
        self._set_text(self.em_align_result_text, "运行中…")
        self.em_align_path_var.set("")
        self.em_align_report_dir = ""
        self.em_align_status_var.set("运行中…")
        self._set_status("EM 对齐运行中…")
        self._em_align_append_log("开始 EM 对齐: %s" % stage1)
        if curve:
            self._em_align_append_log("EM 曲线: %s" % curve)
        if netlist:
            self._em_align_append_log("EM 网表: %s" % netlist)
        threading.Thread(target=self._em_align_worker,
                         args=(cfg, stage1, curve or None, netlist or None,
                               out_dir, max_iter, tol, bool(self.mock_var.get())),
                         daemon=True).start()
    def _em_align_worker(self, cfg, stage1, curve, netlist, out_dir,
                         max_iter, tol, mock):
        """Background thread: never touch Tk; report through the queue."""
        try:
            try:
                import optimizer as _opt
            except Exception as exc:
                raise RuntimeError("无法导入 optimizer: %s" % exc)
            fn = getattr(_opt, "run_em_align", None)
            if fn is None:
                raise RuntimeError(
                    "optimizer.run_em_align 尚未实现（等待集成线按契约提供）；"
                    "EM 对齐页与配置项已就绪。")
            report = fn(cfg, stage1, em_curve=curve, em_netlist=netlist,
                        output_dir=out_dir, max_iter=max_iter, tol=tol,
                        mock=mock, on_event=self._em_align_event)
        except Exception as exc:
            import traceback
            default_dir = out_dir or os.path.join(stage1, "em_align")
            self.queue.put({"type": "em_align_done", "ok": False,
                            "error": "%s: %s" % (type(exc).__name__, exc),
                            "traceback": traceback.format_exc(),
                            "report_dir": default_dir})
            return
        default_dir = out_dir or os.path.join(stage1, "em_align")
        self.queue.put({"type": "em_align_done", "ok": True, "report": report,
                        "report_dir": default_dir})
    def _em_align_event(self, event):
        """on_event callback. Runs in the worker thread."""
        self.queue.put({"type": "em_align_log", "text": em_align_event_text(event)})
    def _handle_em_align_done(self, ev):
        self._em_align_running = False
        try:
            self.em_align_start_btn.configure(state="normal")
        except Exception:
            pass
        if not ev.get("ok"):
            msg = ev.get("error") or "未知错误"
            self.em_align_status_var.set("失败")
            self._em_align_append_log("失败: %s" % msg)
            if ev.get("traceback"):
                self._em_align_append_log(str(ev["traceback"]).rstrip())
            self._set_text(self.em_align_result_text, "EM 对齐失败\n%s" % msg)
            self.em_align_report_dir = ev.get("report_dir") or ""
            self._set_status("EM 对齐失败: %s" % msg)
            self._log("EM 对齐失败: %s\n" % msg)
            self._dialog("EM 对齐失败", msg, error=True)
            return
        report = ev.get("report")
        dirs = []
        for cand in (ev.get("report_dir"),):
            if cand:
                dirs.append(cand)
        vals = {k: (v.get() or "").strip() for k, v in self.em_align_fields.items()}
        if vals.get("stage1_dir"):
            dirs.append(os.path.join(os.path.expanduser(vals["stage1_dir"]), "em_align"))
        if vals.get("output_dir"):
            dirs.append(os.path.expanduser(vals["output_dir"]))
        md = find_em_report_md(report, dirs)
        text = None
        if md:
            try:
                with open(md, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except Exception as exc:
                self._em_align_append_log("读取报告失败: %s" % exc)
                text = None
        em = report.get("em_align") if isinstance(report, dict) else None
        if not text and isinstance(em, dict):
            try:
                import em_align as _ea
                text = _ea.format_markdown(em)
            except Exception as exc:
                self._em_align_append_log("em_align.format_markdown 不可用: %s" % exc)
        if not text:
            try:
                text = json.dumps(report, indent=2, ensure_ascii=False)
            except Exception:
                text = str(report)
        head = []
        if md:
            head.append("报告: %s" % md)
            self.em_align_report_dir = os.path.dirname(md)
            self.em_align_path_var.set(md)
        else:
            head.append("报告文件未找到（输出目录: %s）" % (dirs[0] if dirs else "(未指定)"))
            self.em_align_report_dir = ev.get("report_dir") or ""
            self.em_align_path_var.set("")
        aligned = em.get("aligned") if isinstance(em, dict) else None
        if aligned is not None:
            head.append("aligned: %s" % aligned)
        self._set_text(self.em_align_result_text, "\n".join(head + ["", text]))
        state = "完成"
        if aligned is True:
            state += "（已对齐）"
        elif aligned is False:
            state += "（未对齐）"
        self.em_align_status_var.set(state)
        self._set_status("EM 对齐完成")
        self._dialog("EM 对齐", "\n".join([state] + head))
    def _open_em_align_dir(self):
        vals = {k: (v.get() or "").strip() for k, v in self.em_align_fields.items()}
        cands = [getattr(self, "em_align_report_dir", "") or ""]
        if vals.get("output_dir"):
            cands.append(os.path.expanduser(vals["output_dir"]))
        if vals.get("stage1_dir"):
            cands.append(os.path.join(os.path.expanduser(vals["stage1_dir"]), "em_align"))
        for path in cands:
            if path and os.path.isdir(path):
                try:
                    if hasattr(os, "startfile"):
                        os.startfile(path)
                    else:
                        import subprocess
                        opener = "open" if sys.platform == "darwin" else "xdg-open"
                        subprocess.Popen([opener, path])
                    return
                except Exception as exc:
                    self._dialog("打开报告目录", "无法打开 %s:\n%s" % (path, exc), error=True)
                    return
        self._dialog("打开报告目录",
                     "报告目录不存在。先运行 EM 对齐，或填写输出目录。", error=True)
    def _build_progress_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=4)
        self.notebook.add(tab, text="进度")
        self.progress_tab = tab
        em_bar = self.ttk.Frame(tab)
        em_bar.pack(side="top", fill="x", pady=(0, 2))
        self.em_status_label = self.ttk.Label(em_bar, text="EM 融合: (尚未刷新)", foreground="#0066cc", anchor="w")
        self.em_status_label.pack(side="left", fill="x", expand=True)
        self.em_status_label.bind("<Button-1>", lambda _e: self._show_tab("em_tab"))
        self.ttk.Button(em_bar, text="→EM 融合", command=lambda: self._show_tab("em_tab")).pack(side="right", padx=2)
        vpane = self.ttk.PanedWindow(tab, orient="vertical")
        vpane.pack(fill="both", expand=True)
        top_pane = self.ttk.PanedWindow(vpane, orient="horizontal")
        vpane.add(top_pane, weight=3)
        conv_frame = self.ttk.Frame(top_pane)
        conv_bar = self.ttk.Frame(conv_frame)
        conv_bar.pack(fill="x")
        self.ttk.Label(conv_bar, text="收敛图").pack(side="left")
        self.ttk.Button(conv_bar, text="fit", width=4, command=lambda: self.canvas.fit()).pack(side="right", padx=2)
        self.canvas = PlotCanvas(conv_frame, log_y=True, height=300, font=self.ui_font)
        self.canvas.pack(fill="both", expand=True)
        top_pane.add(conv_frame, weight=3)
        bar_frame = self.ttk.Frame(top_pane)
        self.ttk.Label(bar_frame, text="调优变量 (蓝色填充=最近迭代 · 红线=最优组合)").pack(fill="x")
        self.var_bar_canvas = self.tk.Canvas(bar_frame, background="white", height=300)
        self.var_bar_canvas.pack(fill="both", expand=True)
        self.var_bar_canvas.bind("<Configure>", lambda _e: self._draw_var_bars())
        top_pane.add(bar_frame, weight=2)
        log_frame = self.ttk.Frame(vpane)
        vpane.add(log_frame, weight=1)
        self.log_text = self._scrolled_text(log_frame, wrap="none", height=8)
        live_frame = self.ttk.Frame(vpane)
        vpane.add(live_frame, weight=2)
        live = self.ttk.Frame(live_frame)
        live.pack(fill="x")
        self.ttk.Label(live, text="动态曲线 横轴:").pack(side="left")
        self.live_x_var = self.tk.StringVar(value="频率")
        self.live_x_combo = self.ttk.Combobox(live, textvariable=self.live_x_var, width=12, values=("频率", "迭代"), state="readonly")
        self.live_x_combo.pack(side="left", padx=2)
        self.live_x_combo.bind("<<ComboboxSelected>>", lambda _e: self._draw_live_curves())
        self.ttk.Label(live, text="纵轴:").pack(side="left")
        self.live_y_var = self.tk.StringVar(value="输出值")
        self.live_y_combo = self.ttk.Combobox(live, textvariable=self.live_y_var, width=16, values=("输出值", "输出@频点", "总目标", "最优目标", "全部变量(归一化)"), state="readonly")
        self.live_y_combo.pack(side="left", padx=2)
        self.live_y_combo.bind("<<ComboboxSelected>>", lambda _e: self._draw_live_curves())
        self.ttk.Label(live, text="输出:").pack(side="left")
        self.live_out_var = self.tk.StringVar()
        self.live_combo = self.ttk.Combobox(live, textvariable=self.live_out_var, width=14, values=())
        self.live_combo.pack(side="left", padx=2)
        self.live_combo.bind("<<ComboboxSelected>>", lambda _e: self._draw_live_curves())
        self.ttk.Label(live, text="频点:").pack(side="left")
        self.live_freq_var = self.tk.StringVar(value="")
        self.ttk.Entry(live, textvariable=self.live_freq_var, width=8).pack(side="left", padx=2)
        self.live_freq_var.trace_add("write", lambda *_a: self._draw_live_curves())
        self.ttk.Button(live, text="fit", width=4, command=lambda: self.live_canvas.fit()).pack(side="right", padx=2)
        self.ttk.Label(live, text="叠加色: 最优=红 最近=蓝 其余=灰").pack(side="left", padx=8)
        self.live_canvas = PlotCanvas(live_frame, height=240, font=self.ui_font)
        self.live_canvas.pack(fill="both", expand=True)
        self._live_refresh_axis_options()
    def _build_result_tab(self):
        tab = self.ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="结果")
        self.result_tab = tab
        self.result_text = self._scrolled_text(tab, wrap="none")
    # dialogs and log
    def _dialog(self, title, text, error=False):
        """Show a message box. Log only in smoke mode."""
        if self.smoke:
            self._log(f"[{title}] {text}\n")
            return
        from tkinter import messagebox
        if error:
            messagebox.showerror(title, text, parent=self.root)
        else:
            messagebox.showinfo(title, text, parent=self.root)
    def _show_text_dialog(self, title, text):
        if self.smoke:
            self._log(f"[{title}]\n{text}\n")
            return
        top = self.tk.Toplevel(self.root)
        top.title(title)
        top.geometry("680x480")
        box = self._scrolled_text(top, wrap="none")
        box.insert("1.0", text)
        self.ttk.Button(top, text="关闭", command=top.destroy).pack(side="bottom", pady=4)
    def _log(self, text):
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {text}")
        self.log_text.see("end")
        try:
            self.log_text.yview_moveto(1.0)
        except Exception:
            pass
    def _set_status(self, text):
        self.status_var.set(text)
    # config load and save
    def _new_config(self):
        self._run_dir_state = None
        self._load_cfg_to_ui({
            "name": "优化任务", "library": "", "cell": "", "view": "schematic",
            "state": "", "state_dir": "", "work_dir": "",
            "virtuoso_bin": "virtuoso", "output_dir": "logs/run",
            "analyses": "sp", "save_raw": 1, "sim_timeout": 3600,
            "variables": [], "targets": [],
            "settings": {"max_iter": 100, "patience": 10, "improve_rel": 0.001,
                         "fail_limit": 5, "max_restarts": 2, "seed": 0},
        })
    def open_config(self, path, fresh_output=False):
        """Load one config file into the form."""
        cfg = load_config(path)
        if fresh_output:
            import tempfile
            cfg["output_dir"] = tempfile.mkdtemp(prefix="gui_smoke_")
        self._run_dir_state = None
        self._load_cfg_to_ui(cfg)
        self.cfg_path_var.set(path)
    def _open_dialog(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[("JSON", "*.json"), ("全部文件", "*.*")])
        if not path:
            return
        try:
            self.open_config(path)
        except Exception as exc:
            self._dialog("打开失败", str(exc), error=True)
    def _save_dialog(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        cfg = self._collect_cfg()
        if cfg is None:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        self.cfg_path_var.set(path)
        self._dialog("另存为", f"已保存到 {path}")
    def _load_cfg_to_ui(self, cfg):
        managed = _MANAGED_CFG_KEYS
        self.extra = {k: v for k, v in cfg.items() if k not in managed}
        tech = getattr(self, "tech_tab", None)
        if tech is not None:
            tech.fill_from_cfg(cfg)
        for key, var in self.fields.items():
            var.set(str(cfg.get(key, "")))
        env_map = cfg.get("env") or {}
        for name, var in self.env_fields.items():
            var.set(str(env_map.get(name, "")))
        self.spectre_bin_var.set(str(cfg.get("spectre_bin", "")))
        self.path_append_var.set(str(cfg.get("path_append", "")))
        extras = [k2 + "=" + str(v2) for k2, v2 in sorted(env_map.items()) if k2 not in self.env_fields]
        self._set_text(self.env_extra_text, chr(10).join(extras))
        for iid in self.var_tree.get_children():
            self.var_tree.delete(iid)
        # The table only shows four columns; keep scale/integer (and any
        # future per-variable key) so a GUI round trip does not silently
        # turn an integer/log variable into a continuous/linear one.
        self.var_extra = {}
        for v in cfg.get("variables", []):
            extra = {k: v[k] for k in v
                     if k not in ("name", "init", "min", "max")}
            if extra:
                self.var_extra[str(v.get("name", ""))] = extra
        for v in cfg.get("variables", []):
            self.var_tree.insert("", "end", values=(v.get("name", ""), v.get("init", ""), v.get("min", ""), v.get("max", "")))
        for iid in self.fix_tree.get_children():
            self.fix_tree.delete(iid)
        for v in cfg.get("fixed_variables", []):
            self.fix_tree.insert("", "end", values=(v.get("name", ""), v.get("value", "")))
        for iid in self.target_tree.get_children():
            self.target_tree.delete(iid)
        self.target_specs = {}
        for spec in cfg.get("targets", []):
            spec = dict(spec)
            iid = self.target_tree.insert("", "end", values=self._target_row(spec))
            self.target_specs[iid] = spec
        settings = cfg.get("settings", {}) or {}
        self.extra_settings = {k: v for k, v in settings.items() if k not in self.set_fields}
        for key, var in self.set_fields.items():
            val = settings.get(key)
            if key == "seed" and val is None and "seed" in settings:
                var.set("")
            else:
                var.set(str(_SET_DEFAULTS[key] if val is None else val))
        self._vars_undo_stack = []
        self._vars_redo_stack = []
        if hasattr(self, "auto_netlist_var"):
            self.auto_netlist_var.set(_auto_netlist_on(cfg.get("auto_netlist")))
        self._load_em_align(cfg.get("em_align"))
        self._live_refresh_axis_options()
    def _collect_cfg(self, validate=True):
        """Build the config dict from the form. Return None on bad input."""
        try:
            f = {k: v.get().strip() for k, v in self.fields.items()}
            cfg = dict(self.extra)
            cfg.update({
                "name": f["name"], "library": f["library"], "cell": f["cell"],
                "view": f["view"], "state": f["state"],
                "state_dir": os.path.expanduser(f["state_dir"]),
                "work_dir": os.path.expanduser(f["work_dir"]),
                "virtuoso_bin": f["virtuoso_bin"],
                "output_dir": os.path.expanduser(f["output_dir"]),
                "analyses": f["analyses"],
                "save_raw": int(parse_eng(f["save_raw"] or "1")),
                "sim_timeout": parse_eng(f["sim_timeout"] or "3600"),
            })
            if cfg["sim_timeout"] <= 0:
                raise ValueError("仿真超时必须为正数（秒）")
            cfg["netlist"] = f["netlist"]
            cfg["sim_root"] = f["sim_root"]
            cfg["spectre_bin"] = (self.spectre_bin_var.get().strip() or "spectre")
            cfg["path_append"] = self.path_append_var.get().strip()
            env_map = {}
            for name, var in self.env_fields.items():
                text = var.get().strip()
                if text:
                    env_map[name] = text
            for line in self.env_extra_text.get("1.0", "end").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _eq, v = line.partition("=")
                env_map[k.strip()] = v.strip()
            cfg["env"] = env_map
            # 工艺页输入随配置往返（非空项）；门禁本身在任务启动时执行。
            tech = getattr(self, "tech_tab", None)
            if tech is not None:
                cfg.update(tech.tech_cfg_keys())
            variables = []
            extra_map = getattr(self, "var_extra", {}) or {}
            for iid in self.var_tree.get_children():
                vals = self.var_tree.item(iid, "values")
                spec = {"name": vals[0], "init": vals[1],
                        "min": vals[2], "max": vals[3]}
                spec.update(extra_map.get(vals[0], {}))
                variables.append(spec)
            cfg["variables"] = variables
            fixed = []
            opt_names = {v["name"] for v in variables}
            for iid in self.fix_tree.get_children():
                vals = self.fix_tree.item(iid, "values")
                parse_eng(vals[1])
                if vals[0] in opt_names:
                    raise ValueError(f"{vals[0]} 同时出现在优化变量和固定变量中")
                fixed.append({"name": vals[0], "value": vals[1]})
            cfg["fixed_variables"] = fixed
            cfg["targets"] = [self.target_specs[iid] for iid in self.target_tree.get_children()]
            cfg["auto_netlist"] = (bool(self.auto_netlist_var.get())
                                   if hasattr(self, "auto_netlist_var") else False)
            cfg["netlist_view"] = f.get("netlist_view", "")
            cfg["em_align"] = self._collect_em_align()
            s = dict(self.extra_settings)
            for key in self.set_fields:
                text = self.set_fields[key].get().strip()
                if not text:
                    s[key] = (None if key == "seed" else _SET_DEFAULTS[key])
                elif key == "strategy":
                    s[key] = text
                elif key in ("max_iter", "patience", "fail_limit", "max_restarts", "seed"):
                    s[key] = int(parse_eng(text))
                else:
                    s[key] = parse_eng(text)
            cfg["settings"] = s
            if validate:
                build_variables(cfg)
                Settings(s)
                for spec in cfg["targets"]:
                    Target(spec)
        except Exception as exc:
            self._dialog("配置错误", str(exc), error=True)
            return None
        return cfg
    # variable table
    # variable-table undo/redo (v05)
    def _vars_snapshot(self):
        var_rows = [tuple(self.var_tree.item(i, "values")) for i in self.var_tree.get_children()]
        fix_rows = [tuple(self.fix_tree.item(i, "values")) for i in self.fix_tree.get_children()]
        extra = {k: dict(v) for k, v in (getattr(self, "var_extra", {}) or {}).items()}
        return (var_rows, fix_rows, extra)
    def _vars_restore(self, snap):
        var_rows, fix_rows, extra = snap
        for iid in self.var_tree.get_children():
            self.var_tree.delete(iid)
        for row in var_rows:
            self.var_tree.insert("", "end", values=row)
        for iid in self.fix_tree.get_children():
            self.fix_tree.delete(iid)
        for row in fix_rows:
            self.fix_tree.insert("", "end", values=row)
        self.var_extra = {k: dict(v) for k, v in (extra or {}).items()}
    def _vars_push_undo(self):
        """Record the pre-change state. Call BEFORE every mutation."""
        if not hasattr(self, "_vars_undo_stack"):
            self._vars_undo_stack = []
            self._vars_redo_stack = []
        self._vars_undo_stack.append(self._vars_snapshot())
        if len(self._vars_undo_stack) > 50:
            del self._vars_undo_stack[0]
        self._vars_redo_stack = []
    def _vars_undo(self):
        if not getattr(self, "_vars_undo_stack", None):
            self._set_status("没有可撤销的变量编辑")
            return
        current = self._vars_snapshot()
        snap = self._vars_undo_stack.pop()
        self._vars_redo_stack.append(current)
        self._vars_restore(snap)
        self._set_status("已撤销变量编辑")
    def _vars_redo(self):
        if not getattr(self, "_vars_redo_stack", None):
            self._set_status("没有可重做的变量编辑")
            return
        current = self._vars_snapshot()
        snap = self._vars_redo_stack.pop()
        self._vars_undo_stack.append(current)
        self._vars_restore(snap)
        self._set_status("已重做变量编辑")
    def _focus_table(self):
        """The variable table the keyboard event applies to, or None."""
        try:
            w = self.root.focus_get()
        except Exception:
            return None
        if w is None:
            return None
        for tree in (self.var_tree, self.fix_tree):
            if w is tree:
                return tree
        return None
    def _on_delete_key(self, _event):
        tree = self._focus_table()
        if tree is None:
            return None   # let Backspace edit text fields as usual
        sel = tree.selection()
        if not sel:
            return "break"
        names = [tree.item(i, "values")[0] for i in sel]
        self._vars_push_undo()
        for iid in sel:
            tree.delete(iid)
        if tree is self.var_tree:
            self.var_extra = _var_extra_drop(
                getattr(self, "var_extra", {}) or {}, names)
        self._set_status("已删除 %d 个变量 (Ctrl+Z 撤销)" % len(sel))
        return "break"
    def _on_undo_key(self, event):
        w = None
        try:
            w = self.root.focus_get()
        except Exception:
            pass
        cls = w.winfo_class() if w is not None else ""
        if cls in ("Entry", "TEntry", "Text"):
            return None
        self._vars_undo()
        return "break"
    def _on_redo_key(self, event):
        w = None
        try:
            w = self.root.focus_get()
        except Exception:
            pass
        cls = w.winfo_class() if w is not None else ""
        if cls in ("Entry", "TEntry", "Text"):
            return None
        self._vars_redo()
        return "break"
    def _var_select(self, _event):
        sel = self.var_tree.selection()
        if not sel:
            return
        vals = self.var_tree.item(sel[0], "values")
        for key, val in zip(("name", "init", "min", "max"), vals):
            self.var_edit[key].set(val)
    def _var_add(self):
        vals = self._var_values()
        if vals is None:
            return
        self._vars_push_undo()
        self.var_tree.insert("", "end", values=vals)
    def _var_update(self):
        sel = self.var_tree.selection()
        if not sel:
            self._dialog("变量", "请先选择一行", error=True)
            return
        old_name = self.var_tree.item(sel[0], "values")[0]
        vals = self._var_values()
        if vals is None:
            return
        self._vars_push_undo()
        self.var_tree.item(sel[0], values=vals)
        self.var_extra = _var_extra_rename(
            getattr(self, "var_extra", {}) or {}, old_name, vals[0])
    def _var_delete(self):
        sel = self.var_tree.selection()
        if not sel:
            return
        names = [self.var_tree.item(i, "values")[0] for i in sel]
        self._vars_push_undo()
        for iid in sel:
            self.var_tree.delete(iid)
        self.var_extra = _var_extra_drop(
            getattr(self, "var_extra", {}) or {}, names)
    def _var_values(self):
        e = {k: v.get().strip() for k, v in self.var_edit.items()}
        try:
            if not e["name"]:
                raise ValueError("变量名不能为空")
            for key in ("init", "min", "max"):
                parse_eng(e[key])
        except Exception as exc:
            self._dialog("变量错误", str(exc), error=True)
            return None
        return (e["name"], e["init"], e["min"], e["max"])
    # fixed variable table
    def _fix_select(self, _event):
        sel = self.fix_tree.selection()
        if not sel:
            return
        vals = self.fix_tree.item(sel[0], "values")
        for key, val in zip(("name", "value"), vals):
            self.fix_edit[key].set(val)
    def _fix_add(self):
        vals = self._fix_values()
        if vals is None:
            return
        self._vars_push_undo()
        self.fix_tree.insert("", "end", values=vals)
    def _fix_update(self):
        sel = self.fix_tree.selection()
        if not sel:
            self._dialog("固定变量", "请先选择一行", error=True)
            return
        vals = self._fix_values()
        if vals is None:
            return
        self._vars_push_undo()
        self.fix_tree.item(sel[0], values=vals)
    def _fix_delete(self):
        sel = self.fix_tree.selection()
        if not sel:
            return
        self._vars_push_undo()
        for iid in sel:
            self.fix_tree.delete(iid)
    def _fix_values(self):
        e = {k: v.get().strip() for k, v in self.fix_edit.items()}
        try:
            if not e["name"]:
                raise ValueError("变量名不能为空")
            parse_eng(e["value"])
        except Exception as exc:
            self._dialog("固定变量错误", str(exc), error=True)
            return None
        return (e["name"], e["value"])
    def _var_move_to_fixed(self):
        sel = self.var_tree.selection()
        if not sel:
            self._dialog("变量", "请先选择一行", error=True)
            return
        vals = self.var_tree.item(sel[0], "values")
        name, init = vals[0], vals[1]
        existing = {self.fix_tree.item(i, "values")[0] for i in self.fix_tree.get_children()}
        if name in existing:
            self._dialog("变量", f"{name} 已在固定变量表中", error=True)
            return
        self._vars_push_undo()
        self.fix_tree.insert("", "end", values=(name, init))
        self.var_tree.delete(sel[0])
        self.var_extra = _var_extra_drop(
            getattr(self, "var_extra", {}) or {}, [name])
    def _fix_move_to_opt(self):
        sel = self.fix_tree.selection()
        if not sel:
            self._dialog("固定变量", "请先选择一行", error=True)
            return
        vals = self.fix_tree.item(sel[0], "values")
        name, value = vals[0], vals[1]
        existing = {self.var_tree.item(i, "values")[0] for i in self.var_tree.get_children()}
        if name in existing:
            self._dialog("固定变量", f"{name} 已在优化变量表中", error=True)
            return
        lo, hi = suggest_bounds(value)
        self._vars_push_undo()
        self.var_tree.insert("", "end", values=(name, value, lo, hi))
        self.fix_tree.delete(sel[0])
    # target table
    def _target_select(self, _event):
        sel = self.target_tree.selection()
        if not sel:
            return
        iid = sel[0]
        spec = self.target_specs.get(iid, {})
        band = spec.get("band") or ["", ""]
        vals = {"output": spec.get("output", ""), "metric": spec.get("metric", ""),
                "op": spec.get("op", ""), "goal": spec.get("goal", ""),
                "goal2": spec.get("goal2", ""), "tol": spec.get("tol", ""),
                "weight": spec.get("weight", ""),
                "band_lo": band[0], "band_hi": band[1],
                "freq": spec.get("freq", ""),
                "curve_op": spec.get("curve_op", "")}
        for key, var in self.target_edit.items():
            if key == "limit":
                continue
            var.set(str(vals.get(key, "")))
        limit = spec.get("limit")
        self.target_edit["limit"].set(json.dumps(limit) if limit else "")
    def _target_add(self):
        spec = self._target_from_edit()
        if spec is None:
            return
        iid = self.target_tree.insert("", "end", values=self._target_row(spec))
        self.target_specs[iid] = spec
    def _target_update(self):
        sel = self.target_tree.selection()
        if not sel:
            self._dialog("目标", "请先选择一行", error=True)
            return
        spec = self._target_from_edit(self.target_specs.get(sel[0]))
        if spec is None:
            return
        iid = sel[0]
        self.target_tree.item(iid, values=self._target_row(spec))
        self.target_specs[iid] = spec
    def _target_delete(self):
        for iid in self.target_tree.selection():
            self.target_tree.delete(iid)
            self.target_specs.pop(iid, None)
    def _target_from_edit(self, base=None):
        e = {k: v.get().strip() for k, v in self.target_edit.items()}
        try:
            if not e["output"]:
                raise ValueError("输出名不能为空")
            spec = dict(base or {})
            spec["output"] = e["output"]
            for key in ("metric", "op", "curve_op", "goal", "goal2", "tol", "weight", "freq"):
                if e[key]:
                    spec[key] = e[key]
                else:
                    spec.pop(key, None)
            if e["band_lo"] and e["band_hi"]:
                spec["band"] = [e["band_lo"], e["band_hi"]]
            elif e["band_lo"] or e["band_hi"]:
                raise ValueError("频段下/频段上需同时填写或同时留空")
            else:
                spec.pop("band", None)
            if e["limit"]:
                try:
                    spec["limit"] = json.loads(e["limit"])
                except ValueError:
                    raise ValueError("限值曲线 JSON 解析失败：数值要加引号" "（格式见「字段说明」，或点「从CSV载入」）")
            else:
                spec.pop("limit", None)
            if spec.get("curve_op") and "limit" not in spec:
                raise ValueError("限值比较(above/below/match)需要限值曲线：" "用「从CSV载入」或填写 JSON")
            Target(spec)
        except Exception as exc:
            self._dialog("目标错误", str(exc), error=True)
            return None
        return spec
    def _target_row(self, spec):
        band = spec.get("band") or ["", ""]
        return (spec.get("output", ""), spec.get("metric", ""),
                spec.get("op", ""), spec.get("goal", ""), spec.get("goal2", ""),
                spec.get("tol", ""), spec.get("weight", ""),
                str(band[0]), str(band[1]), spec.get("freq", ""),
                spec.get("curve_op", ""))
    def _target_limit_from_csv(self):
        """Load a limit curve from a data file with x,y columns."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root,
                                          filetypes=[("数据文件", "*.csv *.txt *.dat"), ("全部文件", "*.*")])
        if not path:
            return
        text = None
        used = ""
        for enc in ("utf-8-sig", "gb18030", "latin-1"):
            try:
                with open(path, encoding=enc) as f:
                    text = f.read()
                used = enc
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            self._dialog("载入失败", "无法识别文件编码", error=True)
            return
        xs, ys, skipped = [], [], 0
        sep = "[,;" + chr(9) + " ]+"
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(sep, line)
            if len(parts) < 2:
                skipped += 1
                continue
            a = parts[0].strip().lstrip(chr(65279))
            b = parts[1].strip()
            try:
                parse_eng(a)
                parse_eng(b)
            except Exception:
                skipped += 1
                continue
            xs.append(a)
            ys.append(b)
        if len(xs) < 2:
            self._dialog("载入失败", "文件里没有找到至少两行 x,y 数据（按 %s 解码）" % used, error=True)
            return
        self.target_edit["limit"].set(json.dumps({"x": xs, "y": ys}, ensure_ascii=False))
        msg = "已载入 %d 个点（按 %s 解码）" % (len(xs), used)
        if skipped:
            msg += "，跳过 %d 行非数值内容" % skipped
        self._dialog("限值曲线", msg)
    def _target_help(self):
        nl = chr(10)
        text = nl.join([
            '一条目标 = 对一条输出曲线提一个要求。\n'
            '全部目标的误差加权求和 = 总误差；总误差为 0 即全部达标。',
            '\n字段：',
            '  输出：ADE L 输出名（下拉框，导入 ADE L 后自动填充）\n'
            '  指标：如何取值——band_min 频带最小 / band_max 频带最大 /',
            '        band_flatness 起伏(最大减最小) / band_mean 频带均值 /\n'
            '        value_at 某频点值(需填频率) / scalar 第一个点',
            '  算子：>= 大于等于 / <= 小于等于 / == 等于 / range 区间\n'
            '  目标：期望值（算子=range 时为区间下限）',
            '  目标2：算子=range 时=区间上限；「添加上下界」时=窗口上界(目标=下界)\n'
            '  容差：>=/<=/above/below 每违规 1 个容差记 1.0 误差；',
            '        ==/range/match 容差为达标死区，超出部分才计误差\n'
            '  权重：该目标在总误差中的占比',
            '  频段下/频段上：限定曲线参与计算的横轴范围，留空=全频段(两项同填或同空)\n'
            '  频率：指标=value_at 时的取值频点',
            '  限值比较：曲线逐点对限值线比较(填写后指标/算子/目标/目标2/频率均忽略)——\n'
            '        above=曲线须高于限值线；below=须低于；match=须贴合',
            '  限值曲线JSON：限值线坐标，数值加引号，形如 {"x":["1G"],"y":["35"]}。\n'
            '        一般不手写：点「从CSV载入」选两列数据文件自动生成。',
            '\n常用例子（某曲线 30G~42G 保持 35~45 欧姆）：\n'
            '  填输出名、目标=35、目标2=45、频段下=30G、频段上=42G，',
            '  然后点「添加上下界」，自动生成 band_min>=35 与\n'
            '  band_max<=45 两条目标。',
        ])
        self._show_text_dialog("目标字段说明", text)
    # ADE state probe
    def _explore_state(self):
        state_dir = self.fields["state_dir"].get().strip()
        if not state_dir:
            self._dialog("探查", "请先填写状态目录", error=True)
            return
        try:
            info = summarize(os.path.expanduser(state_dir))
        except Exception as exc:
            self._dialog("探查失败", str(exc), error=True)
            return
        lines = ["可用输出 outputs:"]
        for o in info.get("outputs", []):
            raw = str(o.get("expression_raw", ""))[:60]
            lines.append(f"  {o.get('name')} = {raw}")
        lines.append("分析 analyses:")
        for a in info.get("analyses", []):
            lines.append(f"  {a.get('type')} {a.get('fields')}")
        lines.append("变量 variables:")
        for v in info.get("variables", []):
            lines.append(f"  {v.get('name')} = {v.get('expression', '')}")
        self._show_text_dialog("ADE 状态探查", "\n".join(lines))
    def _import_ade(self):
        state_dir = self.fields["state_dir"].get().strip()
        if not state_dir:
            self._dialog("导入", "请先填写状态目录", error=True)
            return
        try:
            state_dir = os.path.expanduser(state_dir)
            info = summarize(state_dir)
        except Exception as exc:
            self._dialog("导入失败", str(exc), error=True)
            return
        try:
            text = _read_text(os.path.join(state_dir, "ADE_state.info"))
            m = re.search(r"designInfo = '\(([^)]*)\)", text)
            if m:
                names = re.findall(r'"([^"]*)"', m.group(1))
                for key, val in zip(("library", "cell", "view"), names):
                    if val and not self.fields[key].get().strip():
                        self.fields[key].set(val)
        except Exception:
            pass
        rows = {}
        for iid in self.var_tree.get_children():
            rows[self.var_tree.item(iid, "values")[0]] = iid
        added = 0
        updated = 0
        need_bounds = []
        for v in info.get("variables", []):
            name = v.get("name")
            if not name:
                continue
            init = str(v.get("expression", ""))
            lo, hi = suggest_bounds(init)
            if name in rows:
                iid = rows[name]
                old = self.var_tree.item(iid, "values")
                self.var_tree.item(iid, values=(name, init, old[2], old[3]))
                updated += 1
                if not str(old[3]):
                    need_bounds.append(name)
            else:
                self.var_tree.insert("", "end", values=(name, init, lo, hi))
                added += 1
                if not hi:
                    need_bounds.append(name)
        self.ade_outputs = [o.get("name") for o in info.get("outputs", []) if o.get("name")]
        self.target_out_combo["values"] = self.ade_outputs
        msg = (f"已导入 {added + updated} 个变量"
               f"（新增 {added}，更新 {updated}），"
               f"{len(self.ade_outputs)} 个输出。\n"
               "最小/最大按初值建议（0.5×/2×），请在“变量”页检查。\n"
               "输出名可在“目标”页的输出下拉框中选择。")
        if need_bounds:
            msg += ("\n以下变量初值为 0，请手动填写最大值: " + ", ".join(need_bounds))
        self._dialog("导入 ADE L", msg)
        self._set_status(f"导入完成: {added + updated} 变量, " f"{len(self.ade_outputs)} 输出")
        self.notebook.select(self.vars_tab)
    def _target_add_window(self):
        e = {k: v.get().strip() for k, v in self.target_edit.items()}
        try:
            if not e["output"]:
                raise ValueError("输出名不能为空")
            if not e["goal"] or not e["goal2"]:
                raise ValueError("目标=下界 目标2=上界，两个都要填")
            lo = parse_eng(e["goal"])
            hi = parse_eng(e["goal2"])
            if lo >= hi:
                raise ValueError("下界必须小于上界")
            specs = []
            for metric, op, goal in (("band_min", ">=", e["goal"]), ("band_max", "<=", e["goal2"])):
                spec = {"output": e["output"], "metric": metric,
                        "op": op, "goal": goal,
                        "comment": "auto window"}
                if e["band_lo"] or e["band_hi"]:
                    spec["band"] = [e["band_lo"], e["band_hi"]]
                if e["tol"]:
                    spec["tol"] = e["tol"]
                if e["weight"]:
                    spec["weight"] = e["weight"]
                Target(spec)
                specs.append(spec)
        except Exception as exc:
            self._dialog("目标错误", str(exc), error=True)
            return
        for spec in specs:
            iid = self.target_tree.insert("", "end", values=self._target_row(spec))
            self.target_specs[iid] = spec
    def _output_name_list(self):
        names = list(self.ade_outputs)
        if names:
            return names
        seen = []
        for series in self.curve_cache.values():
            for name in series:
                if name not in seen:
                    seen.append(name)
        return seen
    def _load_history_data(self):
        out_dir = os.path.expanduser(self.fields["output_dir"].get().strip() or "logs/run")
        paths = sorted(glob.glob(os.path.join(out_dir, "iter_*", "record.json")), key=_iter_order)
        self.curve_stats = {"plain": 0, "gz": 0, "missing": 0, "corrupt": 0, "total": 0}
        hist_pts = []
        for path in paths:
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception:
                continue
            idx = rec.get("index")
            if idx is None:
                continue
            vals = {}
            for name, text in (rec.get("variables") or {}).items():
                try:
                    vals[name] = parse_eng(text)
                except Exception:
                    pass
            self.var_cache[idx] = vals
            total = (rec.get("objective") or {}).get("total")
            if isinstance(total, (int, float)):
                hist_pts.append((idx, float(total)))
            iter_dir = os.path.dirname(path)
            self.curve_stats["total"] += 1
            got = read_result_any(iter_dir)
            if got is None:
                has_any = (os.path.isfile(os.path.join(iter_dir, "result.txt"))
                           or os.path.isfile(os.path.join(iter_dir, "result.txt.gz")))
                self.curve_stats["corrupt" if has_any else "missing"] += 1
            else:
                self.curve_cache[idx] = got[0]
                self.curve_stats[got[4]] += 1
        self.plot_points = hist_pts
        run_best = None
        self.best_points = []
        for pidx, ptotal in hist_pts:
            run_best = ptotal if run_best is None else min(run_best, ptotal)
            self.best_points.append((pidx, run_best))
        self.best_var_values = None
        if hist_pts:
            best_idx = min(hist_pts, key=lambda p: p[1])[0]
            if self.var_cache.get(best_idx):
                self.best_var_values = dict(self.var_cache[best_idx])
        self._live_refresh_axis_options()
        self._update_curve_coverage_hint()
    def _curve_coverage_text(self):
        st = getattr(self, "curve_stats", None) or {}
        total = st.get("total", 0)
        if not total:
            return ""
        have = st.get("plain", 0) + st.get("gz", 0)
        lack = st.get("missing", 0) + st.get("corrupt", 0)
        bits = ["曲线 %d/%d 轮" % (have, total)]
        if st.get("gz"):
            bits.append("gz %d" % st["gz"])
        if st.get("corrupt"):
            bits.append("损坏 %d" % st["corrupt"])
        if lack:
            bits.append("缺 %d 轮无曲线数据" % lack)
        return " · ".join(bits)
    def _update_curve_coverage_hint(self):
        text = self._curve_coverage_text()
        if not text:
            return
        try:
            self._set_status(text)
        except Exception:
            pass
    def _var_bounds_from_table(self):
        bounds = {}
        for iid in self.var_tree.get_children():
            vals = self.var_tree.item(iid, "values")
            try:
                bounds[vals[0]] = (parse_eng(vals[2]), parse_eng(vals[3]))
            except Exception:
                continue
        return bounds
    def _record_curves(self, eidx, vars_ev):
        if eidx is None:
            return
        self.var_cache[eidx] = dict(vars_ev)
        out_dir = os.path.expanduser(self.fields["output_dir"].get().strip() or "logs/run")
        iter_dir = os.path.join(out_dir, "iter_%04d" % int(eidx))
        st = getattr(self, "curve_stats", None)
        if st is None:
            st = {"plain": 0, "gz": 0, "missing": 0, "corrupt": 0, "total": 0}
            self.curve_stats = st
        got = read_result_any(iter_dir)
        if got is None:
            has_any = (os.path.isfile(os.path.join(iter_dir, "result.txt"))
                       or os.path.isfile(os.path.join(iter_dir, "result.txt.gz")))
            key = "corrupt" if has_any else "missing"
            st[key] += 1
            st["total"] += 1
        else:
            self.curve_cache[eidx] = got[0]
            st[got[4]] += 1
            st["total"] += 1
        self._update_curve_coverage_hint()
        self._draw_live_curves()
    # dynamic curve window with user-chosen axes (v05)
    _X_FREQ = "频率"
    _X_ITER = "迭代"
    _X_VAR_PREFIX = "变量:"
    _Y_CURVE = "输出值"
    _Y_AT = "输出@频点"
    _Y_TOTAL = "总目标"
    _Y_BEST = "最优目标"
    _Y_ALLVARS = "全部变量(归一化)"
    _Y_VAR_PREFIX = "变量:"
    def _live_var_names(self):
        names = []
        for idx in sorted(self.var_cache or {}):
            for name in self.var_cache[idx]:
                if name not in names:
                    names.append(name)
        for name in self._var_bounds_from_table():
            if name not in names:
                names.append(name)
        names.sort()
        return names
    def _live_refresh_axis_options(self):
        if not hasattr(self, "live_x_combo"):
            return
        var_sel = [self._X_VAR_PREFIX + n for n in self._live_var_names()]
        x_vals = (self._X_FREQ, self._X_ITER) + tuple(var_sel)
        y_vals = (self._Y_CURVE, self._Y_AT, self._Y_TOTAL, self._Y_BEST,
                  self._Y_ALLVARS) + tuple(var_sel)
        self.live_x_combo["values"] = x_vals
        self.live_y_combo["values"] = y_vals
        if self.live_x_var.get() not in x_vals:
            self.live_x_var.set(self._X_FREQ)
        if self.live_y_var.get() not in y_vals:
            self.live_y_var.set(self._Y_CURVE)
    def _draw_live_curves(self):
        canvas = getattr(self, "live_canvas", None)
        if canvas is None:
            return
        self._live_refresh_axis_options()
        y_sel = self.live_y_var.get() or self._Y_CURVE
        if y_sel == self._Y_CURVE:
            self._draw_live_overlay()
        elif y_sel == self._Y_ALLVARS:
            self._draw_live_vars()
        else:
            self._draw_live_scalar()
    def _best_plot_idx(self):
        best_idx = best_val = None
        for pidx, ptotal in self.plot_points:
            if best_val is None or ptotal < best_val:
                best_val, best_idx = ptotal, pidx
        return best_idx
    def _draw_live_overlay(self):
        canvas = self.live_canvas
        canvas.log_y = False
        names = self._output_name_list()
        out = self.live_out_var.get()
        if out not in names:
            out = names[0] if names else ""
            self.live_out_var.set(out)
            self.live_combo["values"] = names
        key = ("out", out)
        keep = getattr(self, "_live_key", None) == key
        self._live_key = key
        items = []
        for idx in sorted(self.curve_cache):
            pair = (self.curve_cache.get(idx) or {}).get(out)
            if pair and pair[0] and pair[1]:
                items.append((idx, pair[0], pair[1]))
        if not items:
            hint = self._curve_coverage_text()
            canvas.show([], title=str(out),
                        empty_text="无数据" + ((" · " + hint) if hint else ""),
                        keep_view=False)
            return
        best_idx = self._best_plot_idx()
        latest_idx = items[-1][0]
        series = []
        for idx, xs, ys in items:
            fx, fy = [], []
            for xv, yv in zip(xs, ys):
                try:
                    fx.append(float(xv))
                    fy.append(float(yv))
                except (TypeError, ValueError):
                    pass
            if not fx:
                continue
            if best_idx is not None and idx == best_idx:
                color, width = "#d62728", 2   # red = best
            elif idx == latest_idx:
                color, width = "#1f77b4", 2   # blue = latest
            else:
                color, width = "#cccccc", 1   # light gray = rest
            series.append({"xs": fx, "ys": fy, "color": color, "width": width})
            if best_idx is not None and best_idx == latest_idx and idx == best_idx:
                series.append({"xs": fx, "ys": fy, "color": "#1f77b4", "width": 1, "dash": (4, 2)})
        title = "%s · %d 条曲线 (X=频率 Y=输出值 红=最优 蓝=最近)" % (out, len(items))
        st = getattr(self, "curve_stats", None) or {}
        lack = st.get("missing", 0) + st.get("corrupt", 0)
        if lack:
            title += " · %d 轮无曲线数据" % lack
        canvas.show(series, title=title, xlabel="频率", ylabel=str(out) or "值", keep_view=keep)
    def _live_x_value(self, idx):
        x_sel = self.live_x_var.get() or self._X_FREQ
        if x_sel == self._X_ITER:
            return float(idx)
        if x_sel.startswith(self._X_VAR_PREFIX):
            v = (self.var_cache.get(idx) or {}).get(x_sel[len(self._X_VAR_PREFIX):])
            return float(v) if v is not None else None
        return None
    def _live_y_value(self, idx):
        y_sel = self.live_y_var.get() or self._Y_CURVE
        if y_sel in (self._Y_TOTAL, self._Y_BEST):
            pts = self.plot_points if y_sel == self._Y_TOTAL else self.best_points
            for pidx, ptotal in pts:
                if pidx == idx:
                    return float(ptotal)
            return None
        if y_sel == self._Y_AT:
            out = self.live_out_var.get()
            pair = (self.curve_cache.get(idx) or {}).get(out)
            if not pair or not pair[0]:
                return None
            pts = []
            for xv, yv in zip(pair[0], pair[1]):
                try:
                    pts.append((float(xv), float(yv)))
                except (TypeError, ValueError):
                    pass
            if not pts:
                return None
            pts.sort()
            text = (self.live_freq_var.get() or "").strip()
            if text:
                try:
                    freq = parse_eng(text)
                except Exception:
                    return None
            else:
                freq = pts[len(pts) // 2][0]
            return _value_at(pts, freq)
        if y_sel.startswith(self._Y_VAR_PREFIX):
            v = (self.var_cache.get(idx) or {}).get(y_sel[len(self._Y_VAR_PREFIX):])
            return float(v) if v is not None else None
        return None
    def _draw_live_scalar(self):
        canvas = self.live_canvas
        x_sel = self.live_x_var.get() or self._X_FREQ
        y_sel = self.live_y_var.get() or self._Y_TOTAL
        key = ("scalar", x_sel, y_sel, self.live_out_var.get(), self.live_freq_var.get())
        keep = getattr(self, "_live_key", None) == key
        self._live_key = key
        if x_sel == self._X_FREQ:
            canvas.log_y = False
            canvas.show([], title="横轴「频率」只用于纵轴=输出值", empty_text="请把横轴改为 迭代 或 变量", keep_view=False)
            return
        canvas.log_y = y_sel in (self._Y_TOTAL, self._Y_BEST)
        iters = sorted(set(self.var_cache) | {p[0] for p in self.plot_points} | set(self.curve_cache))
        pts = []
        for idx in iters:
            xv, yv = self._live_x_value(idx), self._live_y_value(idx)
            if xv is not None and yv is not None:
                pts.append((idx, xv, yv))
        if not pts:
            canvas.show([], title="%s vs %s" % (y_sel, x_sel), empty_text="无数据", keep_view=False)
            return
        n = len(pts)
        series = [{"xs": [p[1] for p in pts], "ys": [p[2] for p in pts],
                   "color": "#bbbbbb", "width": 1}]  # path in iter order
        for i, (_idx, xv, yv) in enumerate(pts):
            t = 0.0 if n < 2 else float(i) / (n - 1)
            series.append({"xs": [xv], "ys": [yv], "color": _age_color(t), "width": 1})
        note = ""
        if y_sel in (self._Y_TOTAL, self._Y_BEST):
            _bidx, bx, by = min(pts, key=lambda p: p[2])
            series.append({"xs": [bx], "ys": [by], "color": "#dd0000", "width": 1, "name": "最优点"})
            note = " · 红点=最优"
        y_label = y_sel
        if y_sel == self._Y_AT:
            y_label = "%s@%s" % (self.live_out_var.get() or "输出", self.live_freq_var.get() or "自动")
        title = "%s vs %s · %d 点(旧→新 蓝渐变)%s" % (y_label, x_sel, n, note)
        canvas.show(series, title=title, xlabel=x_sel, ylabel=y_label,
                    legend=bool(note), keep_view=keep)
    def _draw_live_vars(self):
        canvas = self.live_canvas
        canvas.log_y = False
        history = self.var_cache or {}
        key = ("vars",)
        keep = getattr(self, "_live_key", None) == key
        self._live_key = key
        if not history:
            canvas.show([], title="变量轨迹（各自边界归一化）", empty_text="无数据", keep_view=False)
            return
        iters = sorted(history)
        names = sorted({name for idx in iters for name in history[idx]})
        bounds = self._var_bounds_from_table()
        series = []
        for vi, name in enumerate(names):
            raw = [(float(idx), history[idx][name]) for idx in iters if name in history[idx]]
            if not raw:
                continue
            vals = [v for _i, v in raw]
            b_lo, b_hi = bounds.get(name, (min(vals), max(vals)))
            span = (b_hi - b_lo) or 1.0
            series.append({"xs": [i for i, _v in raw],
                           "ys": [0.5 if b_hi == b_lo else (v - b_lo) / span for _i, v in raw],
                           "color": _VAR_PALETTE[vi % len(_VAR_PALETTE)],
                           "width": 2, "name": str(name)})
        canvas.show(series, title="变量轨迹（各自边界归一化 · 横=迭代）",
                    xlabel="迭代", ylabel="归一化值(0~1)", legend=True, keep_view=keep)
    def _draw_var_bars(self):
        c = getattr(self, "var_bar_canvas", None)
        if c is None:
            return
        c.delete("all")
        w = max(int(c.winfo_width()), 2)
        h = max(int(c.winfo_height()), 2)
        bounds = self._var_bounds_from_table()
        if not bounds:
            c.create_text(w / 2, h / 2, text="无变量数据", fill="#888888", font=self.ui_font)
            return
        history = self.var_cache or {}
        latest = (history.get(max(history)) or {}) if history else {}
        best = self.best_var_values or {}
        names = sorted(bounds.keys())
        n = len(names)
        if n == 0:
            return
        margin_l, margin_r, margin_t, margin_b = 50, 20, 30, 50
        plot_w = max(w - margin_l - margin_r, 10)
        plot_h = max(h - margin_t - margin_b, 10)
        bar_gap = min(24, plot_w // (n * 3 + 1))
        bar_w = max(8, (plot_w - bar_gap * (n + 1)) // max(n, 1))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = margin_t + plot_h * (1.0 - frac)
            c.create_line(margin_l, y, w - margin_r, y, fill="#e8e8e8", dash=(2, 2))
            c.create_text(margin_l - 4, y, anchor="e", text="%.0f%%" % (frac * 100), fill="#888888", font=self.ui_font)
        for i, name in enumerate(names):
            lo, hi = bounds[name]
            if hi < lo:
                lo, hi = hi, lo
            span = (hi - lo) or 1.0
            x0 = margin_l + bar_gap + i * (bar_w + bar_gap)
            x1 = x0 + bar_w
            y_top, y_bot = margin_t, margin_t + plot_h
            cx = (x0 + x1) / 2
            c.create_rectangle(x0, y_top, x1, y_bot, fill="#f0f0f0", outline="#cccccc")
            val = latest.get(name)
            if val is not None:
                frac = max(0.0, min(1.0, (val - lo) / span))
                y_fill = y_bot - frac * plot_h
                c.create_rectangle(x0, y_fill, x1, y_bot, fill="#2266cc", outline="#2266cc")
                c.create_text(cx, y_fill - 4, anchor="s", text=format_eng(val), fill="#222222", font=self.ui_font)
            c.create_text(cx, y_bot + 6, anchor="n", text=str(name), fill="#444444", font=self.ui_font)
            c.create_text(cx, y_bot - 2, anchor="s", text=format_eng(lo), fill="#888888", font=self.ui_font)
            c.create_text(cx, y_top + 2, anchor="n", text=format_eng(hi), fill="#888888", font=self.ui_font)
            bval = best.get(name)
            if bval is not None:
                bfrac = max(0.0, min(1.0, (bval - lo) / span))
                y_best = y_bot - bfrac * plot_h
                pad = max(4, bar_w // 3)
                c.create_line(x0 - pad, y_best, x1 + pad, y_best, fill="#dd0000", width=3)
                c.create_text(cx, y_best + 2, anchor="n", text=format_eng(bval), fill="#dd0000", font=self.ui_font)
    def start(self, force_mock=False, max_iter=None, cfg=None):
        """Start the optimization in a worker thread. Return True on start."""
        if self.running:
            self._dialog("运行", "优化正在运行", error=True)
            return False
        if cfg is None:
            cfg = self._collect_cfg()
        if cfg is None:
            return False
        if max_iter is not None:
            cfg.setdefault("settings", {})["max_iter"] = int(max_iter)
        mock = force_mock or self.mock_var.get()
        resume = bool(self.resume_var.get())
        run_dir, state = resolve_run_dir(cfg, resume=resume, prev=self._run_dir_state)
        self._run_dir_state = state
        cfg["output_dir"] = run_dir
        self.fields["output_dir"].set(run_dir)
        self.plot_points = []
        self.best_points = []
        self.drawn_points = 0
        self.iter_events = 0
        self.report = None
        self.curve_cache = {}
        self.var_cache = {}
        self.curve_stats = {"plain": 0, "gz": 0, "missing": 0, "corrupt": 0, "total": 0}
        self._draw_live_curves()
        self._draw_var_bars()
        self._draw_plot()
        self.stop_event = threading.Event()
        self.running = True
        self.finished = False
        self._set_status("运行中" + (" (mock)" if mock else ""))
        self.notebook.select(self.progress_tab)
        if resume:
            old = len(glob.glob(os.path.join(run_dir, "iter_*")))
            self._log(f"续跑 run 目录={run_dir} (已有 {old} 次迭代)\n")
            if old < 1:
                self._log("续跑目录里没有迭代记录, 本次从第 0 次开始\n")
            self._load_history_data()
        else:
            self._log(f"新 run 目录={run_dir}\n" f"  (迭代编号从 0 重新开始; 基础目录={state[0]})\n")
        self.worker = threading.Thread(target=self._worker, args=(cfg, mock, resume), daemon=True)
        self.worker.start()
        return True
    def _start_with_cfg(self, cfg):
        """v06 librf_opt hook: start with an externally built config."""
        return self.start(cfg=cfg)
    def stop(self):
        """Ask the worker to stop. Kill the running virtuoso process."""
        if self.stop_event is not None:
            self.stop_event.set()
            self._log("停止请求已发送\n")
        stop_all_runners()
    def _worker(self, cfg, mock, resume=None):
        """Run one optimization session. Send all events to the queue."""
        class Writer:
            """Capture print output of the optimizer into the queue."""
            def __init__(self, q):
                self.q = q
            def write(self, text):
                self.q.put({"type": "log", "text": text})
                return len(text)
            def flush(self):
                pass
            def isatty(self):
                return False
        real = sys.stdout
        sys.stdout = Writer(self.queue)
        report = None
        try:
            report = run_optimization(cfg, mock=mock,
                                      on_event=self._on_event,
                                      stop_check=self._stop_check,
                                      resume=resume)
        except Exception as exc:
            import traceback
            self.queue.put({"type": "log", "text": traceback.format_exc()})
            # Preflight failures (missing analysis, bad netlist) are user
            # mistakes, not crashes: say it in a dialog, not only a log.
            self.queue.put({"type": "error", "text": str(exc) or repr(exc)})
        finally:
            sys.stdout = real
        self.queue.put({"type": "done", "report": report})
    def _on_event(self, event):
        """Optimizer callback. Runs in the worker thread."""
        self.queue.put(dict(event))
    def _stop_check(self):
        """Optimizer stop check. Runs in the worker thread."""
        return self.stop_event is not None and self.stop_event.is_set()
    # queue polling and progress
    def _poll(self):
        self._drain()
        self.root.after(100, self._poll)
    def _drain(self):
        for _ in range(200):
            try:
                ev = self.queue.get_nowait()
            except Exception:
                return
            self._handle(ev)
    def _handle(self, ev):
        kind = ev.get("type")
        if kind == "log":
            self._log(ev.get("text", ""))
        elif kind == "iteration":
            self._handle_iteration(ev)
        elif kind == "restart":
            self._log(f"[{ev.get('index')}] 重启单纯形 {ev.get('count')}\n")
        elif kind == "netlist_now":
            self._handle_netlist_now(ev)
        elif kind == "em_align_log":
            self._em_align_append_log(ev.get("text", ""))
        elif kind == "em_align_done":
            self._handle_em_align_done(ev)
        elif kind == "error":
            self._dialog("运行中止", ev.get("text", ""), error=True)
        elif kind == "done":
            self._handle_done(ev)
    def _handle_iteration(self, ev):
        status = ev.get("status", "")
        total = ev.get("total")
        vars_ev = ev.get("vars") or {}
        self.iter_events += 1
        idx = ev.get("index", self.iter_events)
        if isinstance(total, (int, float)):
            self.plot_points.append((idx, float(total)))
            best = ev.get("best_total")
            if isinstance(best, (int, float)):
                self.best_points.append((idx, float(best)))
            else:
                prev = (self.best_points[-1][1] if self.best_points else float(total))
                self.best_points.append((idx, min(prev, float(total))))
        best_vars = ev.get("best_vars")
        if isinstance(best_vars, dict) and best_vars:
            self.best_var_values = dict(best_vars)
        self._record_curves(ev.get("index"), vars_ev)
        self._draw_var_bars()
        if status == "fail":
            self._log(f"[{ev.get('index')}] 仿真失败: {ev.get('message', '')}\n")
        if self.plot_points:
            self._draw_plot()
    def _handle_done(self, ev):
        self.report = ev.get("report")
        self.running = False
        self.finished = True
        if (self.report or {}).get("stop_reason") == "stopped":
            self._set_status("已停止")
        else:
            self._set_status("完成" if self.report else "失败")
        self._show_report(self.report)
    def iter_rows(self):
        """Number of completed iterations (kept for --gui-smoke)."""
        return self.iter_events
    def _draw_plot(self):
        pts = self.plot_points
        self.drawn_points = 0
        if not pts:
            self.canvas.show([], title="收敛: total/best（对数轴）", empty_text="暂无数据", keep_view=False)
            return
        xs = [p[0] for p in pts]
        ys = [max(p[1], 1e-12) for p in pts]
        bxs = [p[0] for p in self.best_points]
        bys = [max(p[1], 1e-12) for p in self.best_points]
        self.canvas.show(
            [{"xs": xs, "ys": ys, "color": "#1f77b4", "width": 2,
              "name": "total"},
             {"xs": bxs, "ys": bys, "color": "#d62728", "width": 2,
              "dash": (4, 2), "name": "best"}],
            title="收敛: total/best（对数轴）",
            xlabel="迭代", ylabel="总误差", legend=True, keep_view=True)
        self.drawn_points = len(pts)
    def _show_report(self, report):
        if not report:
            self._set_text(self.result_text, "无报告。运行失败或被取消。")
            return
        lines = [
            f"stop_reason: {report.get('stop_reason')}",
            f"best_total_error: {report.get('best_total_error')}",
            f"best_variables: {report.get('best_variables')}",
            f"best_errors: {report.get('best_errors')}",
            f"iterations: {report.get('iterations')}",
            f"restarts: {report.get('restarts')}",
            f"convergence_plot: {report.get('convergence_plot')}",
            "曲线数据: " + (self._curve_coverage_text() or "(无)"),
            "", "完整报告:",
            json.dumps(report, indent=2, ensure_ascii=False),
        ]
        self._set_text(self.result_text, "\n".join(lines))
    def _on_close(self):
        self._simset_shutdown()
        self._em_shutdown()
        self.stop()
        self.root.destroy()

# GUI entry point
# Demo (synthetic data) — embed in a tk.Tk root.

def run_gui(cfg_path=None, smoke=False):
    """Start the GUI. Return 0 on success, 1 on failure."""
    import time
    import tkinter as tk
    try:
        root = tk.Tk()
    except Exception as exc:
        print(f"GUI_SMOKE_FAIL: no display: {exc}")
        return 1
    gui = OptimizerGui(root, smoke=smoke)
    out = gui.real_stdout
    if not smoke:
        if cfg_path:
            try:
                gui.open_config(cfg_path)
            except Exception as exc:
                gui._dialog("打开失败", str(exc), error=True)
        root.mainloop()
        return 0
    if not cfg_path:
        import tempfile
        fd, cfg_path = tempfile.mkstemp(prefix="gui_smoke_cfg_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(EXAMPLE_CFG, f, ensure_ascii=False)
    try:
        gui.open_config(cfg_path, fresh_output=True)
    except Exception as exc:
        print(f"GUI_SMOKE_FAIL: load config: {exc}", file=out)
        return 1
    if not gui.start(force_mock=True, max_iter=3):
        print("GUI_SMOKE_FAIL: start failed", file=out)
        return 1
    deadline = time.time() + 60
    while not gui.finished and time.time() < deadline:
        root.update()
        time.sleep(0.01)
    reason = None
    if not gui.finished:
        reason = "60 秒超时"
    elif gui.iter_events < 1:
        reason = "没有迭代事件"
    elif gui.iter_rows() < 1:
        reason = "迭代计数为 0"
    elif gui.drawn_points < 1:
        reason = "收敛图无绘制点"
    elif not getattr(gui, "simset_text", None):
        reason = "仿真设置页未创建"
    else:
        try:
            body = gui.simset_text.get("1.0", "end")
            if "SIMSET_DISPLAY_ERROR" in body:
                reason = "仿真设置页展示失败: " + body.split("SIMSET_DISPLAY_ERROR", 1)[-1][:80]
            elif "【分析 analyses】" not in body:
                reason = "仿真设置页缺少分析区"
            elif "【仿真频率范围】" not in body:
                reason = "仿真设置页缺少频率区"
        except Exception as exc:
            reason = "仿真设置页读取失败: %s" % exc
    if reason is None and not getattr(gui, "em_text", None):
        reason = "EM 融合页未创建"
    elif reason is None:
        try:
            em_body = gui.em_text.get("1.0", "end")
            if "EM_DISPLAY_ERROR" in em_body:
                reason = "EM 融合页展示失败: " + em_body.split("EM_DISPLAY_ERROR", 1)[-1][:80]
            elif "【库统计】" not in em_body and "库" not in em_body:
                reason = "EM 融合页缺少库统计区"
        except Exception as exc:
            reason = "EM 融合页读取失败: %s" % exc
    if reason is None and not getattr(gui, "em_align_tab", None):
        reason = "EM 对齐页未创建"
    elif reason is None and (not getattr(gui, "em_align_log_text", None)
                             or not getattr(gui, "em_align_result_text", None)):
        reason = "EM 对齐页文本框未创建"
    elif reason is None and not getattr(gui, "netlist_btn", None):
        reason = "设计页缺少「立即生成网表」按钮"
    elif reason is None:
        try:
            probe = gui._collect_cfg(validate=False)
            if not isinstance(probe, dict):
                reason = "配置收集失败"
            else:
                missing = [k for k in ("auto_netlist", "netlist_view", "em_align")
                           if k not in probe]
                if missing:
                    reason = "配置缺少键: " + ", ".join(missing)
                elif not isinstance(probe.get("em_align"), dict):
                    reason = "em_align 不是配置段"
        except Exception as exc:
            reason = "配置键检查失败: %s" % exc
    # P2-2: stop the poll chain before destroy (same as _on_close).
    try:
        gui._simset_shutdown()
        gui._em_shutdown()
    except Exception:
        pass
    import contextlib
    with contextlib.suppress(Exception):
        root.destroy()
    if reason:
        print(f"GUI_SMOKE_FAIL: {reason}", file=out)
        return 1
    print("GUI_SMOKE_OK", file=out)
    return 0
