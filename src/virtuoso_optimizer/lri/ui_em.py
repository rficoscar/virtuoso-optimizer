# -*- coding: utf-8 -*-
"""lri.ui_em — layout page, EM page and registration hooks (tkinter imported locally)."""
from __future__ import print_function

import json
import os
import sys
import threading
import unicodedata

from .common import (
    _HERE, REPO_ROOT, DEF_FREQ, DEF_EMX_TIMEOUT, LibrfError, _eprint, _which,
    parse_eng, fmt_eng, run_cmd)
from .tech import (
    build_tech, require_tech_ready)
from .tools import (
    find_tools)
from .skillio import (
    resolve_work)
from .emx import (
    parse_freq_spec, run_emx)
from .snp import (
    read_snp, fingerprint_from_snp, fprint_summary)
from .device import (
    load_device_meta, fingerprint_kind_for_device, make_layout_emx)
from .runner import (
    load_emx_settings, save_emx_settings, emx_settings_to_opts, emx_cmd_preview)
from .layout import (
    lri_validate, lri_draw_sketch, lri_sketch_after, PRESETS, GEOM_FORM, GEOM_CHOICES,
    form_to_geom)


def _disp_cols(text):
    """Display width (CJK wide characters count as 2 columns) for aligning form labels."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in str(text))


# The Layout page main form shows only common fields (matching the 08c workflow);
# advanced fields live in the "Advanced..." window; behavior and config round-tripping are unchanged.
GEOM_PRIMARY = {
    "ind": ("kind", "shape", "seam", "n", "w", "s", "dout", "ct"),
}


class _TabBase(object):
    """Shared by the layout and EM pages: forms, log, scrolling, background tasks."""
    label_cols = 22
    def __init__(self, gui, parent, state=None):
        self.gui = gui
        self.tk = gui.tk
        self.ttk = gui.ttk
        self.root = parent
        self._state = state if state is not None else {"busy": False}
        self._build(parent)
    @property
    def _busy(self):
        return bool(self._state.get("busy"))
    @_busy.setter
    def _busy(self, value):
        self._state["busy"] = bool(value)
    # ---- basic widgets ----
    def _row(self, parent, pady=2):
        f = self.ttk.Frame(parent)
        f.pack(fill="x", pady=pady)
        return f
    def _entry(self, parent, label, default="", width=14):
        r = self._row(parent)
        lab = self.ttk.Label(r, text=label, anchor="w", width=self.label_cols)
        lab.pack(side="left")
        setattr(lab, "_lri_form_label", True)
        var = self.tk.StringVar(value=str(default))
        self.ttk.Entry(r, textvariable=var, width=width).pack(side="left", padx=4)
        return var
    def _fit_form_labels(self):
        """Normalize form label widths to the longest label on this page so text is not truncated."""
        labs = []
        def walk(widget):
            for child in widget.winfo_children():
                if getattr(child, "_lri_form_label", False):
                    labs.append(child)
                walk(child)
        walk(self.root)
        if not labs:
            return
        widest = max(_disp_cols(lab.cget("text")) for lab in labs)
        cols = max(self.label_cols, widest + 1)
        for lab in labs:
            try:
                lab.configure(width=cols)
            except Exception:
                pass
    def _text(self, parent, height=8):
        from tkinter.scrolledtext import ScrolledText
        t = ScrolledText(parent, height=height, width=70, spacing3=3,
                         wrap="char")
        font = getattr(self.gui, "ui_font", None)
        if font is not None:
            try:
                t.configure(font=font)
            except Exception:
                pass
        return t
    def _log(self, widget, msg):
        widget.insert("end", msg if msg.endswith("\n") else msg + "\n")
        widget.see("end")
    def _scrollable(self, parent, height=None):
        """Return (inner, outer): a vertical scroll container that reacts to the wheel anywhere inside."""
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(parent)
        kw = {"highlightthickness": 0, "borderwidth": 0}
        try:
            bg = ttk.Style().lookup("TFrame", "background")
        except Exception:
            bg = None
        kw["background"] = bg or "#f0f0f0"
        if height:
            kw["height"] = height
        canvas = tk.Canvas(outer, **kw)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))

        def _in_canvas(e):
            cx, cy = canvas.winfo_rootx(), canvas.winfo_rooty()
            return (cx <= e.x_root <= cx + canvas.winfo_width()
                    and cy <= e.y_root <= cy + canvas.winfo_height())

        def _active():
            # bind_all applies to every page; only the currently visible page reacts to the wheel.
            try:
                return bool(canvas.winfo_ismapped())
            except Exception:
                return False

        def _on_scroller(e):
            # When the wheel is over a widget with its own scrolling (log/tree), scroll only that widget.
            try:
                w = e.widget
                while w is not None:
                    if w.winfo_class() in ("Text", "Listbox",
                                           "Treeview", "TCombobox"):
                        return True
                    w = w.master
            except Exception:
                return False
            return False

        def _scroll_clamped(pixels):
            bbox = canvas.bbox("all")
            if not bbox:
                return
            content_h = bbox[3] - bbox[1]
            view_h = canvas.winfo_height()
            if content_h <= view_h:
                return
            first, _ = canvas.yview()
            cur = first * content_h
            new = max(0, min(content_h - view_h, cur + pixels))
            canvas.yview_moveto(new / content_h)

        def _wheel(e):
            if not _active() or _on_scroller(e) or not _in_canvas(e):
                return
            delta = getattr(e, "delta", 0)
            if delta:
                _scroll_clamped(-int(delta / 120) * 60)

        def _wheel_up(e):
            if _active() and not _on_scroller(e) and _in_canvas(e):
                _scroll_clamped(-60)

        def _wheel_down(e):
            if _active() and not _on_scroller(e) and _in_canvas(e):
                _scroll_clamped(60)

        for seq, fn in (("<MouseWheel>", _wheel),
                        ("<Button-4>", _wheel_up),
                        ("<Button-5>", _wheel_down)):
            outer.bind_all(seq, fn, add="+")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        return inner, outer
    # ---- process / background tasks ----
    def _collect_tech(self):
        tab = getattr(self.gui, "tech_tab", None)
        if tab is not None and hasattr(tab, "collect_tech"):
            return tab.collect_tech()
        return build_tech()
    def _require_tech_ui(self, log=None):
        log = log if log is not None else getattr(self, "a_log", None)

        def _say(msg):
            if log is not None:
                self._log(log, msg)
        tab = getattr(self.gui, "tech_tab", None)
        if tab is not None and hasattr(tab, "require_ready_ui"):
            ok = tab.require_ready_ui(parent=self.root)
            if not ok:
                _say("工艺检查未通过，任务已取消（见「工艺」页）")
            return ok
        try:
            warns = require_tech_ready(build_tech())
        except LibrfError as e:
            _say("工艺检查未通过: %s" % e)
            return False
        if warns:
            _say("工艺检查通过（%d 条警告）: %s" % (len(warns), warns[0]))
        return True
    def _run_bg(self, fn, args, logw, done_cb):
        self._busy = True
        def worker():
            try:
                payload = fn(**args) if args else fn()
                if not isinstance(payload, dict):
                    payload = {"value": payload}
            except Exception as e:
                payload = {"_error": "%s: %s" % (type(e).__name__, e)}
            self.gui.queue.put({"type": "librf_done", "cb": done_cb, "payload": payload})
        th = threading.Thread(target=worker, daemon=True)
        th.start()
        self._log(logw, "（后台执行中）")
    def on_done(self, cb, payload):
        self._busy = False
        try:
            cb(payload)
        except Exception as e:
            _eprint("librf done callback failed: %s" % e)


class _LayoutTab(_TabBase):
    """Layout page: device type + geometry form + sketch + run/screenshot."""

    def __init__(self, gui, parent, state=None):
        self.em_tab = None
        self.a_cdslib = None
        self._cdslib_dirty = False
        self._cdslib_auto_last = ""
        self._cdslib_loading = False
        super(_LayoutTab, self).__init__(gui, parent, state=state)

    def _build(self, parent):
        body, outer = self._scrollable(parent)
        outer.pack(fill="both", expand=True)
        f = body

        # --- cds.lib: default = design-page work dir / cds.lib; used by the process check ---
        rc = self._row(f)
        lab = self.ttk.Label(rc, text="cds.lib 路径", anchor="w",
                             width=self.label_cols)
        lab.pack(side="left")
        setattr(lab, "_lri_form_label", True)
        tech = getattr(self.gui, "tech_tab", None)
        if tech is not None and "cds_lib" in getattr(tech, "vars", {}):
            self.a_cdslib = tech.vars["cds_lib"]
        else:
            self.a_cdslib = self.tk.StringVar(value="")
        self.ttk.Entry(rc, textvariable=self.a_cdslib).pack(
            side="left", fill="x", expand=True, padx=4)
        self.ttk.Button(rc, text="浏览",
                        command=self._browse_cdslib).pack(side="left")
        self.a_cdslib.trace_add("write", self._on_cdslib_manual)
        self._sync_cdslib_from_design()
        try:
            self.gui.fields["work_dir"].trace_add(
                "write", lambda *_a: self._sync_cdslib_from_design())
        except Exception:
            pass

        # --- device type: changing it reloads the geometry form ---
        r = self._row(f)
        lab = self.ttk.Label(r, text="器件类型", anchor="w",
                             width=self.label_cols)
        lab.pack(side="left")
        setattr(lab, "_lri_form_label", True)
        self.a_type = self.tk.StringVar(value="ind")
        cb = self.ttk.Combobox(r, textvariable=self.a_type, width=12,
                               values=("ind", "tl", "xfmr", "cpl"),
                               state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._rebuild_geom())
        self.ttk.Button(r, text="预设…", command=self._preset_dialog).pack(
            side="left", padx=6)

        # --- geometry form + sketch ---
        mid = self.ttk.Frame(f)
        mid.pack(fill="x", pady=2)
        self.geom_frame = self.ttk.Frame(mid)
        self.geom_frame.pack(side="left", fill="x", expand=True)
        self.sketch = self.tk.Canvas(mid, width=280, height=210,
                                     highlightthickness=1,
                                     highlightbackground="#aaaaaa",
                                     background="#fafafa")
        self.sketch.pack(side="right", padx=6, pady=2, anchor="n")
        self.geom_vars = {}
        self._rebuild_geom()

        self.a_cell = self._entry(f, "cell 名", "")
        # EM frequencies live on the EM page; the work directory comes from the design-page work_dir
        self.a_views = self.tk.StringVar(value="emx")
        r3 = self._row(f)
        lab = self.ttk.Label(r3, text="views 模式", anchor="w",
                             width=self.label_cols)
        lab.pack(side="left")
        setattr(lab, "_lri_form_label", True)
        self.ttk.Combobox(r3, textvariable=self.a_views, width=10,
                          values=("emx", "skill", "none"),
                          state="readonly").pack(side="left", padx=4)
        r4 = self._row(f, pady=6)
        self.ttk.Button(r4, text="仅出图",
                        command=lambda: self._do_make("layout")).pack(
            side="left", padx=4)
        self.ttk.Button(r4, text="出图 + EM + views",
                        command=lambda: self._do_make("full")).pack(
            side="left", padx=4)
        self.ttk.Button(r4, text="渲染截图",
                        command=self._render).pack(side="left", padx=4)
        self.a_log = self._text(f, height=10)
        self.a_log.pack(fill="both", expand=True, pady=4)
        self._fit_form_labels()

    # ---- cds.lib auto-sync + manual-edit protection ----
    def _browse_cdslib(self):
        try:
            from tkinter import filedialog
        except Exception:
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="选择 cds.lib",
            filetypes=[("cds.lib", "cds.lib"), ("所有文件", "*")])
        if path:
            self.a_cdslib.set(path)
    def _on_cdslib_manual(self, *_a):
        if self._cdslib_loading:
            return
        cur = self.a_cdslib.get().strip()
        self._cdslib_dirty = bool(cur and cur != self._cdslib_auto_last)
    def _design_workdir(self):
        try:
            return self.gui.fields["work_dir"].get().strip()
        except Exception:
            return ""
    def _sync_cdslib_from_design(self):
        """cds.lib default = design-page work dir / cds.lib; never overwrite after a manual edit."""
        if self._cdslib_dirty:
            return
        wd = self._design_workdir()
        if not wd:
            return
        val = os.path.join(os.path.expanduser(wd), "cds.lib")
        if self.a_cdslib.get() == val:
            return
        cur = self.a_cdslib.get().strip()
        if cur and cur != self._cdslib_auto_last:
            # Custom values loaded from a config are kept and never overwritten automatically.
            self._cdslib_dirty = True
            return
        self._cdslib_auto_last = val
        self._cdslib_loading = True
        try:
            self.a_cdslib.set(val)
        finally:
            self._cdslib_loading = False

    # ---- geometry form / sketch ----
    def _rebuild_geom(self):
        for child in self.geom_frame.winfo_children():
            child.destroy()
        if getattr(self, "_adv_win", None) is not None:
            try:
                self._adv_win.destroy()
            except Exception:
                pass
            self._adv_win = None
        self.geom_vars = {}
        typ = self.a_type.get()
        primary = GEOM_PRIMARY.get(typ)
        adv = []
        row = 0
        for key, lab, default, _num in GEOM_FORM.get(typ, []):
            var = self.tk.StringVar(value=default)
            self.geom_vars[key] = var
            if primary is not None and key not in primary:
                adv.append((key, lab))
                continue
            label = self.ttk.Label(self.geom_frame, text=lab, anchor="w")
            label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=1)
            if key in GEOM_CHOICES:
                ent = self.ttk.Combobox(self.geom_frame, textvariable=var,
                                        width=10, values=GEOM_CHOICES[key],
                                        state="readonly")
                ent.grid(row=row, column=1, sticky="w", pady=1)
                ent.bind("<<ComboboxSelected>>",
                         lambda _e: lri_sketch_after(self))
            else:
                ent = self.ttk.Entry(self.geom_frame, textvariable=var,
                                     width=14)
                ent.grid(row=row, column=1, sticky="w", pady=1)
                ent.bind("<KeyRelease>", lambda _e: lri_sketch_after(self))
            row += 1
        self._geom_adv = adv
        if adv:
            self.ttk.Button(self.geom_frame, text="高级…（%d 项，通常不用改）"
                            % len(adv), command=self._geom_advanced).grid(
                                row=row, column=0, columnspan=2,
                                sticky="w", pady=(6, 0))
        self._draw_geom_sketch(typ)
        self._fit_form_labels()

    def _geom_advanced(self):
        """Advanced geometry parameters: less common fields go into a separate window to keep the form compact."""
        win = getattr(self, "_adv_win", None)
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                return
            except Exception:
                self._adv_win = None
        if not self._geom_adv:
            return
        typ = self.a_type.get()
        win = self.tk.Toplevel(self.root)
        self._adv_win = win
        win.title("高级几何参数（%s）" % typ)
        win.transient(self.root)
        win.geometry("620x560")
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_adv())
        self.ttk.Label(
            win, anchor="w", justify="left", foreground="#555555",
            wraplength=580,
            text="这些参数通常留空即可（自动推导或沿用工艺档）。\n"
                 "改动会立即反映到示意图与出图几何。"
        ).pack(fill="x", padx=10, pady=(8, 4))
        body, outer = self._scrollable(win)
        outer.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        for r, (key, lab) in enumerate(self._geom_adv):
            var = self.geom_vars[key]
            label = self.ttk.Label(body, text=lab, anchor="w")
            label.grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            if key in GEOM_CHOICES:
                ent = self.ttk.Combobox(body, textvariable=var, width=12,
                                        values=GEOM_CHOICES[key],
                                        state="readonly")
                ent.grid(row=r, column=1, sticky="w", pady=2)
                ent.bind("<<ComboboxSelected>>",
                         lambda _e: lri_sketch_after(self))
            else:
                ent = self.ttk.Entry(body, textvariable=var, width=16)
                ent.grid(row=r, column=1, sticky="w", pady=2)
                ent.bind("<KeyRelease>", lambda _e: lri_sketch_after(self))
        bar = self.ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 8))
        self.ttk.Button(bar, text="关闭", command=self._close_adv).pack(
            side="right")

    def _close_adv(self):
        win = getattr(self, "_adv_win", None)
        self._adv_win = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
    def _draw_geom_sketch(self, typ):
        """Device geometry sketch with parameter labels (tl/xfmr/cpl)."""
        c = getattr(self, "sketch", None)
        if c is None:
            return
        try:
            c.delete("all")
        except Exception:
            return
        typ = typ or self.a_type.get()
        c.create_text(274, 8, text=("%s sketch" % typ), fill="#333333",
                      font=("fixed", 9), anchor="ne")
        if typ == "ind":
            raw = {k: (v.get() if v is not None else "")
                   for k, v in (self.geom_vars or {}).items()}
            try:
                lri_draw_sketch(c, raw)
            except Exception as exc:
                c.create_text(140, 100, text="预览失败: %s" % str(exc)[:40],
                              fill="#a03030", font=("fixed", 8))
        elif typ == "tl":
            c.create_rectangle(40, 80, 240, 110, outline="#2060a0", width=2)
            c.create_text(140, 95, text="signal", fill="#2060a0",
                          font=("fixed", 9))
            c.create_line(40, 70, 240, 70, fill="#a03030", arrow="both")
            c.create_text(140, 58, text="len", fill="#a03030",
                          font=("fixed", 9))
            c.create_line(30, 80, 30, 110, fill="#0a7d33", arrow="both")
            c.create_text(18, 95, text="w", fill="#0a7d33",
                          font=("fixed", 9))
            c.create_line(40, 130, 240, 130, fill="#888888", width=3)
            c.create_text(140, 145, text="gnd (M1)", fill="#888888",
                          font=("fixed", 9))
            c.create_text(140, 175, text="cpw gap (each side)",
                          fill="#555555", font=("fixed", 9))
        elif typ == "xfmr":
            c.create_rectangle(30, 50, 120, 160, outline="#2060a0", width=2)
            c.create_rectangle(160, 50, 250, 160, outline="#a03030", width=2)
            c.create_text(75, 40, text="primary np", fill="#2060a0",
                          font=("fixed", 9))
            c.create_text(205, 40, text="secondary ns", fill="#a03030",
                          font=("fixed", 9))
            c.create_line(120, 105, 160, 105, fill="#0a7d33", arrow="both")
            c.create_text(140, 92, text="gap", fill="#0a7d33",
                          font=("fixed", 9))
            c.create_line(30, 175, 120, 175, fill="#a03030", arrow="both")
            c.create_text(75, 188, text="dout / w / s", fill="#a03030",
                          font=("fixed", 9))
        elif typ == "cpl":
            c.create_rectangle(40, 60, 240, 85, outline="#2060a0", width=2)
            c.create_rectangle(40, 115, 240, 140, outline="#a03030", width=2)
            c.create_text(140, 72, text="line1", fill="#2060a0",
                          font=("fixed", 9))
            c.create_text(140, 127, text="line2", fill="#a03030",
                          font=("fixed", 9))
            c.create_line(250, 85, 250, 115, fill="#0a7d33", arrow="both")
            c.create_text(265, 100, text="gap", fill="#0a7d33",
                          font=("fixed", 9))
            c.create_line(30, 60, 30, 85, fill="#555555", arrow="both")
            c.create_text(18, 72, text="w", fill="#555555",
                          font=("fixed", 9))
            c.create_text(140, 170, text="len (horizontal)", fill="#555555",
                          font=("fixed", 9))
        else:
            c.create_text(140, 100, text="(no sketch)", fill="#888888",
                          font=("fixed", 10))

    # ---- presets ----
    def _preset_dialog(self):
        win = self.tk.Toplevel(self.root)
        win.title("套用预设")
        win.transient(self.root)
        win.geometry("420x150")
        body = self.ttk.Frame(win, padding=10)
        body.pack(fill="both", expand=True)
        self.ttk.Label(body, text="选择预设：").pack(anchor="w")
        var = self.tk.StringVar(value=PRESETS[0]["name"])
        self.ttk.Combobox(body, textvariable=var, width=36, state="readonly",
                          values=[p["name"] for p in PRESETS]).pack(
            fill="x", pady=4)
        bar = self.ttk.Frame(body)
        bar.pack(anchor="w", pady=(8, 0))
        def apply():
            self._apply_preset(var.get())
            win.destroy()
        self.ttk.Button(bar, text="应用", command=apply).pack(side="left", padx=2)
        self.ttk.Button(bar, text="关闭", command=win.destroy).pack(side="left", padx=2)
    def _apply_preset(self, name):
        for p in PRESETS:
            if p["name"] != name:
                continue
            self.a_type.set(p["type"])
            self._rebuild_geom()
            for key, val in (p.get("geom") or {}).items():
                if key in self.geom_vars:
                    self.geom_vars[key].set(str(val))
            if p.get("freq") and self.em_tab is not None:
                self.em_tab.b_freq.set(p["freq"])
            self._log(self.a_log, "已应用预设：%s" % name)
            self._draw_geom_sketch(p["type"])
            return

    # ---- run layout ----
    def _collect_geom(self):
        raw = {k: v.get() for k, v in self.geom_vars.items()}
        return form_to_geom(self.a_type.get(), raw)
    def _emx_opts(self):
        if self.em_tab is None:
            return None
        return self.em_tab._emx_opts_from_b()
    def _do_make(self, task):
        if not self._require_tech_ui():
            return
        if self._busy:
            self._log(self.a_log, "已有任务在跑")
            return
        try:
            geom = self._collect_geom()
            if self.a_type.get() == "ind":
                _ok, _msg = lri_validate(geom, tech=self._collect_tech())
                if not _ok:
                    raise LibrfError(_msg)
        except LibrfError as e:
            self._log(self.a_log, "几何错误: %s" % e)
            return
        work = self._design_workdir() or None
        try:
            freq = self.em_tab.b_freq.get().strip() or DEF_FREQ
        except Exception:
            freq = DEF_FREQ
        args = dict(typ=self.a_type.get(), geom=geom,
                    cell=self.a_cell.get().strip() or None,
                    work=work, freq=freq, emx_opts=self._emx_opts(),
                    views_mode=self.a_views.get(),
                    no_views=(task != "full"), no_emx=(task == "layout"),
                    tech=self._collect_tech())
        self._log(self.a_log, "任务 %s 开始 ..." % task)
        self._run_bg(self._make_worker, args, self.a_log, self._make_done)
    def _make_worker(self, **kwargs):
        return make_layout_emx(**kwargs)
    def _make_done(self, payload):
        if payload.get("_error"):
            self._log(self.a_log, "失败: %s" % payload["_error"])
            return
        self._log(self.a_log, "完成 cell=%s/%s bbox=%s"
                  % (payload.get("lib"), payload.get("cell"), payload.get("bbox")))
        self._log(self.a_log, "端口: %s" % ", ".join(payload.get("ports") or []))
        if payload.get("gds"):
            self._log(self.a_log, "GDS: %s" % payload["gds"])
        if payload.get("snp"):
            self._log(self.a_log, "sNp: %s  %s"
                      % (payload["snp"], fprint_summary(payload.get("fingerprint"))))
        if payload.get("views"):
            self._log(self.a_log, "views: %s" % payload["views"])
        em = self.em_tab
        if em is not None:
            em.b_cell.set(payload.get("cell") or "")
            wd = self._design_workdir()
            if wd:
                em.b_work.set(wd)
            em.b_gds.set(payload.get("gds") or "")
            if payload.get("fingerprint"):
                em._plot_fprint(payload.get("fingerprint"))
        self._log(self.a_log, "已联动填入 EM 仿真页参数")
    def _render(self):
        gds = ""
        if self.em_tab is not None:
            gds = self.em_tab.b_gds.get().strip()
        if not gds:
            work = self._design_workdir() or "work"
            cell = self.a_cell.get().strip()
            gds = os.path.join(work, "%s.gds" % cell) if cell else ""
        if not gds or not os.path.isfile(gds):
            self._log(self.a_log, "找不到 GDS: %s" % gds)
            return
        png = os.path.splitext(gds)[0] + ".png"
        script = None
        for cand in (os.path.join(_HERE, "gds_render.py"),
                     os.path.join(_HERE, "tools", "gds_render.py"),
                     os.path.join(os.path.dirname(_HERE), "tools", "gds_render.py"),
                     os.path.join(REPO_ROOT, "tools", "gds_render.py")):
            if os.path.isfile(cand):
                script = cand
                break
        if not script:
            self._log(self.a_log, "找不到 gds_render.py（截图工具不随交付）")
            return
        r = run_cmd([sys.executable, script, gds, png], timeout=120)
        if r["ok"] and os.path.isfile(png):
            self._log(self.a_log, "截图: %s" % png)
            opener = getattr(os, "startfile", None)
            if opener is not None:
                try:
                    opener(png)
                except Exception:
                    pass
            else:
                for prog in ("xdg-open", "eog", "display"):
                    if _which(prog):
                        run_cmd([prog, png], timeout=5)
                        break
        else:
            self._log(self.a_log, "渲染失败: %s" % (r["stderr"] or r["stdout"]))


class _EmOptTab(_TabBase):
    """EM page: EM simulation + EM optimization loop (layout parts moved to the Layout page)."""
    def _build(self, parent):
        nb = self.ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)
        self.sub_b = self.ttk.Frame(nb)
        self.sub_c = self.ttk.Frame(nb)
        nb.add(self.sub_b, text=" EM 仿真 ")
        nb.add(self.sub_c, text=" EM 优化迭代 ")
        body_b, outer_b = self._scrollable(self.sub_b)
        outer_b.pack(fill="both", expand=True)
        self._build_b(body_b)
        body_c, outer_c = self._scrollable(self.sub_c)
        outer_c.pack(fill="both", expand=True)
        self._build_c(body_c)
    def _sync_proc_label(self):
        """proc is filled on the Process page; here it is read-only and mirrored for display."""
        path = ""
        try:
            tab = getattr(self.gui, "tech_tab", None)
            if tab is not None:
                path = tab.vars["proc"].get().strip()
        except Exception:
            path = ""
        self.b_proc.set(path)
        try:
            self.b_proc_label.set(path or "未填（可在工艺页浏览选择）")
        except Exception:
            pass
    def _build_b(self, f):
        self.b_cell = self._entry(f, "cell 名", "")
        self.b_gds = self._entry(f, "GDS 路径（留空=work/cell.gds）", "", 30)
        self.b_freq = self._entry(f, "EM 频率", DEF_FREQ, 22)
        self.b_work = self._entry(f, "工作目录 work", "work", 26)
        pr = self._row(f, pady=2)
        lab = self.ttk.Label(pr, text="proc（见工艺页）", anchor="w",
                             width=self.label_cols)
        lab.pack(side="left")
        setattr(lab, "_lri_form_label", True)
        self.b_proc = self.tk.StringVar()
        self.b_proc_label = self.tk.StringVar(value="")
        self.ttk.Label(pr, textvariable=self.b_proc_label, anchor="w",
                       foreground="#8a8880").pack(side="left", padx=4)
        self._sync_proc_label()
        proc_var = getattr(getattr(self.gui, "tech_tab", None), "vars", {}).get("proc")
        if proc_var is not None:
            proc_var.trace_add("write", lambda *_a: self._sync_proc_label())
        acc = self.ttk.LabelFrame(f, text="EMX 精度设置（对应 EMX 界面）")
        acc.pack(fill="x", padx=4, pady=3)
        self.b_edge = self._entry(acc, "edge-width 网格边缘宽", "0.5")
        self.b_3d = self._entry(acc, "3d 模式 */-/列表", "*")
        self.b_sidewalls = self._entry(acc, "sidewalls 侧壁", "*")
        self.b_via_sep = self._entry(acc, "via-separation", "0.5")
        por = self.ttk.LabelFrame(f, text="端口 / 求解")
        por.pack(fill="x", padx=4, pady=3)
        self.b_snap = self._entry(por, "label-snap (µm)", "4")
        self.b_mode = self._entry(por, "差分模式 mode (如 +p01-p02)", "")
        self.b_temp = self._entry(por, "温度 (°C)", "")
        self.b_parallel = self._entry(por, "并行核数", "")
        r = self._row(f, pady=6)
        self.ttk.Button(r, text="开始 EM 仿真", command=self._run_emx).pack(side="left", padx=4)
        self.ttk.Button(r, text="查看指纹", command=self._show_fprint).pack(side="left", padx=4)
        self.ttk.Button(r, text="EMX 命令行预览", command=self._emx_preview).pack(side="left", padx=4)
        self.ttk.Button(r, text="保存/载入设置", command=self._save_settings).pack(side="left", padx=4)
        self.b_log = self._text(f, height=10)
        self.b_log.pack(fill="x", pady=4)
        try:
            self.curve = _CurveCanvas(f, height=220)
            self.curve.pack(fill="x", expand=False, pady=2)
        except Exception:
            self.curve = None
        self._fit_form_labels()
    def _build_c(self, f):
        info = self.ttk.Label( f, anchor="w", justify="left",
            text=("流程：①设几何变量与固定几何 → ②「生成器件与视图」得到 symbol"
                  "（其 schematic 含 nport 指向固定 sNp）→ 在电路原理图中例化该 symbol"
                  " → ③设好目标页/变量页后「开始 EM 优化」。每轮迭代自动按几何变量"
                  "重画版图+EMX 并刷新 nport 的 sNp；L0/SRF/Q@f0 可作优化目标。" "不勾选「启用」则不影响常规优化。"))
        info.pack(fill="x", padx=6, pady=4)
        r0 = self._row(f)
        self.c_enable = self.tk.BooleanVar(value=False)
        self.ttk.Checkbutton(r0, text="启用版图 EM 优化（勾选后几何变量并入优化变量、"
                     "每轮迭代自动重生成版图+EM）", variable=self.c_enable).pack(side="left")
        self.c_type = self._entry(f, "器件类型", "ind", 12)
        self.c_shape = self._entry(f, "形状 shape", "octagon", 12)
        self.c_cell = self._entry(f, "cell 名", "opt_ind", 16)
        self.c_work = self._entry(f, "工作目录 work", "work", 26)
        self.c_freq = self._entry(f, "EM 频率", DEF_FREQ, 22)
        self.c_snp = self._entry(f, "固定 sNp 路径（留空=work/snps/cell.s2p）", "", 30)
        gv = self.ttk.LabelFrame(f, text="几何优化变量（name / init / min / max）")
        gv.pack(fill="x", padx=4, pady=3)
        self.c_gtree = self.ttk.Treeview(gv, columns=("n", "i", "lo", "hi"),
                                         show="headings", height=5)
        for col, txt, w in (("n", "变量名", 10), ("i", "init", 10),
                            ("lo", "min", 10), ("hi", "max", 10)):
            self.c_gtree.heading(col, text=txt)
            self.c_gtree.column(col, width=w)
        self.c_gtree.pack(side="left", fill="both", expand=True)
        gbtn = self.ttk.Frame(gv)
        gbtn.pack(side="left", fill="y", padx=4)
        self.c_gname = self.tk.StringVar(value="dout")
        self.c_ginit = self.tk.StringVar(value="150")
        self.c_gmin = self.tk.StringVar(value="80")
        self.c_gmax = self.tk.StringVar(value="250")
        for lab, var in (("名", self.c_gname), ("init", self.c_ginit),
                         ("min", self.c_gmin), ("max", self.c_gmax)):
            rr = self._row(gbtn, pady=1)
            self.ttk.Label(rr, text=lab, width=5, anchor="w").pack(side="left")
            self.ttk.Entry(rr, textvariable=var, width=8).pack(side="left")
        self.ttk.Button(gbtn, text="添加", command=self._gv_add).pack(fill="x", pady=2)
        self.ttk.Button(gbtn, text="删除选中", command=self._gv_del).pack(fill="x")
        self.c_fixed = self._entry(f, "固定几何 JSON", '{"n": 2.5, "w": 10, "s": 2.6, "ct": 0}', 36)
        r = self._row(f, pady=6)
        self.ttk.Button(r, text="生成器件与视图", command=self._gen_device).pack(side="left", padx=4)
        self.ttk.Button(r, text="开始 EM 优化", command=self._start_em_opt).pack(side="left", padx=4)
        self.c_log = self._text(f, height=8)
        self.c_log.pack(fill="x", pady=4)
        self._fit_form_labels()
    def _emx_opts_from_b(self):
        st = load_emx_settings()
        st["accuracy"]["edge_width"] = _opt_num(self.b_edge.get()) or 0.5
        st["accuracy"]["3d"] = self.b_3d.get().strip() or "*"
        st["accuracy"]["sidewalls"] = self.b_sidewalls.get().strip() or "*"
        st["accuracy"]["via_separation"] = _opt_num(self.b_via_sep.get())
        st["ports"]["label_snap"] = _opt_num(self.b_snap.get(), int) or 4
        st["ports"]["modes"] = {}
        m = self.b_mode.get().strip()
        if m:
            st["ports"]["modes"][m] = "diff"
        st["solver"]["temperature"] = _opt_num(self.b_temp.get())
        st["solver"]["parallel"] = _opt_num(self.b_parallel.get(), int)
        return emx_settings_to_opts(st)
    def _run_emx(self):
        if not self._require_tech_ui(log=getattr(self, "b_log", None)):
            return
        if self._busy:
            self._log(self.b_log, "已有任务在跑")
            return
        cell = self.b_cell.get().strip()
        if not cell:
            self._log(self.b_log, "请填 cell 名（可先在版图页出图）")
            return
        work = self.b_work.get().strip() or "work"
        gds = self.b_gds.get().strip() or os.path.join(work, "%s.gds" % cell)
        def worker():
            proc_arg = self.b_proc.get().strip() or None
            try:
                tech = self._collect_tech()
            except Exception:
                tech = None
            tools = find_tools(proc=proc_arg, tech=tech)
            freqs = parse_freq_spec(self.b_freq.get().strip() or DEF_FREQ)
            loc = resolve_work(work=work, tools=tools)
            meta = load_device_meta(loc["work"], cell)
            ports = meta.get("ports") or None
            nports = max(2, len(ports or []))
            out_snp = os.path.join(loc["work"], "snps", "%s.s%dp" % (cell, nports))
            res = run_emx(gds, cell, tools["proc"]["path"], freqs, out_snp,
                          tools, edge=_opt_num(self.b_edge.get()),
                          three_d=self.b_3d.get().strip() or None,
                          mode=self.b_mode.get().strip() or None,
                          ports=ports, timeout=DEF_EMX_TIMEOUT)
            val = res.get("validate") or {}
            kind = fingerprint_kind_for_device(meta, val.get("nports") or nports)
            payload = {"snp": res["out_snp"], "cmd": res["cmd"],
                       "warn": res.get("warn")}
            if kind:
                payload["fingerprint"] = fingerprint_from_snp(
                    res["out_snp"], kind, ports=ports)
            else:
                payload["note"] = (
                    "缺少 %s.meta.json，无法判定 %d 端口器件的类型（变压器/耦合线），"
                    "未提取指纹。请先在「版图」页重新生成该 cell 再仿真。"
                    % (cell, nports))
            return payload
        self._log(self.b_log, "EM 仿真开始 ...")
        self._run_bg(worker, {}, self.b_log, self._emx_done)
    def _emx_done(self, payload):
        if payload.get("_error"):
            self._log(self.b_log, "失败: %s" % payload["_error"])
            return
        fpr = payload.get("fingerprint") or {}
        self._log(self.b_log, "完成 sNp: %s" % payload.get("snp"))
        if payload.get("note"):
            self._log(self.b_log, payload["note"])
        if fpr:
            self._log(self.b_log, self._fprint_summary(fpr))
        if payload.get("warn"):
            self._log(self.b_log, "WARN %s" % payload["warn"])
        if self.curve:
            self._plot_fprint(fpr)
    def _fprint_summary(self, fpr):
        return fprint_summary(fpr)
    def _plot_fprint(self, fpr):
        if not self.curve or not fpr:
            return
        series = []

        def _curve(key, label, color):
            pts = fpr.get(key) or []
            if pts:
                series.append((label, ([p[0] for p in pts], [p[1] for p in pts]), color))

        kind = fpr.get("kind") or "ind"
        if kind == "tl":
            _curve("Z0curve", "Z0(f) Ω", "#2266cc")
            _curve("thetacurve", "θ(f)°", "#22aa44")
            _curve("ILcurve", "IL(f) dB", "#c05a00")
        elif kind == "xfmr":
            _curve("Lpcurve", "Lp(f) H", "#2266cc")
            _curve("Lscurve", "Ls(f) H", "#22aa44")
            _curve("kcurve", "k(f)", "#c05a00")
        elif kind == "cpl":
            _curve("Zecurve", "Ze(f) Ω", "#2266cc")
            _curve("Zocurve", "Zo(f) Ω", "#22aa44")
            _curve("theta_e_curve", "θe(f)°", "#c05a00")
            _curve("theta_o_curve", "θo(f)°", "#7a3cb0")
        else:
            _curve("Lcurve", "L(f) H", "#2266cc")
            _curve("Qcurve", "Q(f)", "#22aa44")
        srf = fpr.get("SRF")
        try:
            self.curve.set_series(series, srf=srf)
            if kind in ("ind", "ct"):
                self._set_health(_health_judge(fpr))
        except Exception:
            pass
    def _set_health(self, msgs):
        for level, text in msgs:
            tag = {"err": "!", "warn": "?", "ok": " "}.get(level, " ")
            self._log(self.b_log, "[%s] %s" % (tag, text))
    def _show_fprint(self):
        cell = self.b_cell.get().strip()
        work = self.b_work.get().strip() or "work"
        if not cell:
            self._log(self.b_log, "请填 cell 名")
            return
        d = os.path.abspath(os.path.join(work, "snps"))
        if not os.path.isdir(d):
            self._log(self.b_log, "未找到 snps 目录")
            return
        meta = load_device_meta(work, cell)
        for fn in sorted(os.listdir(d)):
            if fn.startswith(cell) and fn.endswith(("s2p", "s3p", "s4p", "s5p")):
                try:
                    path = os.path.join(d, fn)
                    files, S, _z0, _ports = read_snp(path)
                    n_ports = len(S[0]) if S else 0
                    kind = fingerprint_kind_for_device(meta, n_ports)
                    if not kind:
                        self._log(self.b_log, "%s: 缺少 %s.meta.json，无法判定 "
                                  "%d 端口器件类型（变压器/耦合线）" % (fn, cell, n_ports))
                        continue
                    fpr = fingerprint_from_snp(path, kind,
                                               ports=(meta.get("ports") or None))
                    self._log(self.b_log, "%s: %s" % (fn, self._fprint_summary(fpr)))
                    self._plot_fprint(fpr)
                except Exception as e:
                    self._log(self.b_log, "%s 解析失败: %s" % (fn, e))
    def _emx_preview(self):
        st = load_emx_settings()
        st["accuracy"]["edge_width"] = _opt_num(self.b_edge.get()) or 0.5
        cmd = emx_cmd_preview(st, proc=self.b_proc.get().strip() or None)
        self._log(self.b_log, "等效 emx 命令行:\n%s" % cmd)
    def _save_settings(self):
        st = load_emx_settings()
        st["accuracy"]["edge_width"] = _opt_num(self.b_edge.get()) or 0.5
        st["accuracy"]["3d"] = self.b_3d.get().strip() or "*"
        ok, path = save_emx_settings(st)
        self._log(self.b_log, ("已保存设置 %s" % path) if ok else ("保存失败 %s" % path))
    def _gv_add(self):
        name = self.c_gname.get().strip()
        if not name:
            self._log(self.c_log, "变量名不能为空")
            return
        try:
            parse_eng(self.c_ginit.get())
            parse_eng(self.c_gmin.get())
            parse_eng(self.c_gmax.get())
        except ValueError as e:
            self._log(self.c_log, "数值错误: %s" % e)
            return
        self.c_gtree.insert("", "end", values=(
            name, self.c_ginit.get().strip(), self.c_gmin.get().strip(), self.c_gmax.get().strip()))
    def _gv_del(self):
        for iid in self.c_gtree.selection():
            self.c_gtree.delete(iid)
    def _layout_em_cfg(self):
        geom_vars = []
        for iid in self.c_gtree.get_children():
            vals = self.c_gtree.item(iid, "values")
            geom_vars.append({"name": vals[0], "init": vals[1], "min": vals[2], "max": vals[3]})
        enabled = bool(self.c_enable.get())
        if not enabled:
            return {"enabled": False}, []
        try:
            fixed = json.loads(self.c_fixed.get() or "{}")
        except ValueError as e:
            raise LibrfError("固定几何 JSON 有误: %s" % e)
        if not isinstance(fixed, dict):
            raise LibrfError("固定几何必须是 JSON 对象")
        typ = (self.c_type.get() or "ind").strip()
        shape = (self.c_shape.get() or "").strip()
        if shape and typ == "ind":
            fixed.setdefault("shape", shape)
        cell = (self.c_cell.get() or "opt_%s" % typ).strip()
        work = (self.c_work.get() or "work").strip()
        snp = (self.c_snp.get() or "").strip()
        if not snp:
            snp = os.path.join(work, "snps", "%s.s2p" % cell)
        opts = {"freq": self.c_freq.get().strip() or DEF_FREQ}
        return { "enabled": True, "type": typ, "cell": cell, "work": work,
            "geom_vars": [g["name"] for g in geom_vars], "geom_var_specs": geom_vars,
            "fixed_geom": fixed, "snp": snp, "emx": opts, "geom_outputs": True, "views_mode": "emx",
        }, geom_vars
    def _gen_device(self):
        if not self._require_tech_ui(log=getattr(self, "c_log", None)):
            return
        if self._busy:
            self._log(self.c_log, "已有任务在跑")
            return
        try:
            le, geom_vars = self._layout_em_cfg()
        except LibrfError as e:
            self._log(self.c_log, "配置错误: %s" % e)
            return
        geom = dict(le["fixed_geom"])
        for g in geom_vars:
            geom[g["name"]] = parse_eng(g["init"])
        def worker():
            return make_layout_emx( le["type"], geom, cell=le["cell"], work=le["work"],
                freq=le["emx"]["freq"], emx_opts=le["emx"],
                views_mode=le["views_mode"], snp_out=le["snp"],
                tech=self._collect_tech())
        self._log(self.c_log, "生成器件与视图（init 点）...")
        self._run_bg(worker, {}, self.c_log, self._gen_done)
    def _gen_done(self, payload):
        if payload.get("_error"):
            self._log(self.c_log, "失败: %s" % payload["_error"])
            return
        self._log(self.c_log, "完成: %s/%s views=%s"
                  % (payload.get("lib"), payload.get("cell"), payload.get("views")))
        self._log(self.c_log, "请在电路原理图中例化 %s/%s/symbol（其 schematic 的 nport "
                  "已指向固定 sNp），然后在设计页填好网表即可开始 EM 优化。" % (payload.get("lib"), payload.get("cell")))
    def _start_em_opt(self):
        if not self._require_tech_ui(log=getattr(self, "c_log", None)):
            return
        try:
            le, geom_vars = self._layout_em_cfg()
        except LibrfError as e:
            self._log(self.c_log, "配置错误: %s" % e)
            return
        if not le.get("enabled"):
            self._log(self.c_log, "请先勾选「启用版图 EM 优化」")
            return
        if not geom_vars:
            self._log(self.c_log, "请至少添加一个几何优化变量")
            return
        gui = self.gui
        cfg = gui._collect_cfg()
        if cfg is None:
            return
        if not (cfg.get("layout_em") or {}).get("enabled"):
            self._log(self.c_log, "内部错误：layout_em 未注入")
            return
        self._log(self.c_log, "EM 优化启动：%d 个几何变量 + %d 个电路变量"
                  % (len(geom_vars), len(cfg["variables"]) - len(geom_vars)))
        gui._start_with_cfg(cfg)


class _CurveCanvas(object):
    def __init__(self, parent, height=220):
        import tkinter as tk
        self.tk = tk
        self.canvas = tk.Canvas(parent, height=height, bg="white", highlightthickness=1,
                                highlightbackground="#cccccc")
        self.series = []
        self.srf = None
        # Draw a hint even when empty so a blank panel does not look like a stray widget.
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
    def pack(self, **kw):
        self.canvas.pack(**kw)
    def set_series(self, series, srf=None):
        self.series = series or []
        self.srf = srf
        self.redraw()
    def redraw(self):
        c = self.canvas
        c.delete("all")
        w = int(c.winfo_width() or 600)
        h = int(c.winfo_height() or 220)
        pad_l, pad_r, pad_t, pad_b = 46, 12, 10, 24
        pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
        if pw < 40 or ph < 40:
            return
        pts_all = [(name, xs, ys) for name, xs, ys in self.series if xs and ys]
        if not pts_all:
            c.create_text(w // 2, h // 2, text="暂无曲线", fill="#888888")
            return
        xmin = min(min(xs) for _n, xs, _ys in pts_all)
        xmax = max(max(xs) for _n, xs, _ys in pts_all)
        ymin = min(min(ys) for _n, _xs, ys in pts_all)
        ymax = max(max(ys) for _n, _xs, ys in pts_all)
        if xmax <= xmin:
            xmax = xmin + 1
        if ymax <= ymin:
            ymax = ymin + 1
        ypad = (ymax - ymin) * 0.05
        ymin -= ypad
        ymax += ypad
        def X(x):
            return pad_l + (x - xmin) / (xmax - xmin) * pw
        def Y(y):
            return pad_t + (1.0 - (y - ymin) / (ymax - ymin)) * ph
        c.create_rectangle(pad_l, pad_t, pad_l + pw, pad_t + ph, outline="#dddddd")
        c.create_text(pad_l + pw // 2, h - 8, text="频率 Hz",
                      fill="#666666", font=("WenQuanYi Micro Hei", 8))
        for item in self.series:
            name, xs, ys = item[0], item[1], item[2]
            color = item[3] if len(item) > 3 else "#2266cc"
            coords = []
            for x, y in zip(xs, ys):
                coords.extend([X(x), Y(y)])
            if len(coords) >= 4:
                c.create_line(*coords, fill=color, width=2)
            if xs:
                c.create_text(X(xs[0]) + 4, Y(ys[0]) - 8, text=name, fill=color, anchor="w",
                              font=("WenQuanYi Micro Hei", 8))
        if self.srf and xmin <= self.srf <= xmax:
            c.create_line(X(self.srf), pad_t, X(self.srf), pad_t + ph, fill="#dd0000", dash=(4, 3))
            c.create_text(X(self.srf), pad_t + 4, text="SRF", fill="#dd0000", anchor="n",
                          font=("WenQuanYi Micro Hei", 8))

def _opt_num(s, cast=float):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return cast(s)
    except (TypeError, ValueError):
        return None

def _health_judge(fpr):
    msgs = []
    lc = fpr.get("Lcurve") or []
    qc = fpr.get("Qcurve") or []
    srf = fpr.get("SRF")
    if len(lc) < 3:
        msgs.append(("warn", "L 曲线数据不足，无法判读"))
    else:
        ys = [p[1] for p in lc]
        if any(y < 0 for y in ys):
            msgs.append(("err", "L 曲线异常：出现负值，检查 mesh/端口/版图"))
        else:
            l0 = ys[0] if ys[0] != 0 else max(abs(y) for y in ys)
            body = [y for y in ys if y > 0.25 * abs(l0)]
            if len(body) < 3:
                msgs.append(("warn", "L 曲线在低频即快速跌落，检查 mesh/端口/版图与扫频"))
            else:
                rel = []
                for i in range(1, len(body)):
                    sc = max(abs(body[i]), abs(body[i - 1]), 1e-30)
                    rel.append((body[i] - body[i - 1]) / sc)
                max_rel = max(abs(r) for r in rel) if rel else 0.0
                flips = sum(1 for i in range(1, len(rel)) if rel[i] * rel[i - 1] < -1e-12)
                if max_rel > 0.20:
                    msgs.append(("err", "L 曲线异常：存在陡降（单点相对变化 %.0f%%），"
                                 "检查 mesh/端口/版图" % (max_rel * 100)))
                elif flips > max(3, len(rel) // 3):
                    msgs.append(("err", "L 曲线异常：出现振荡，检查 mesh/端口/版图"))
                else:
                    msgs.append(("ok", "L 曲线平滑（正常）"))
    if srf and srf > 0:
        fmax = lc[-1][0] if lc else None
        if fmax and srf > fmax * 1.05:
            msgs.append(("ok", "SRF = %s Hz（高于扫频上限，正常）" % fmt_eng(srf)))
        else:
            msgs.append(("ok", "SRF = %s Hz（已扫到）" % fmt_eng(srf)))
    else:
        msgs.append(("warn", "未扫到 SRF，建议提高频率上限"))
    if len(qc) < 3:
        msgs.append(("warn", "Q 曲线数据不足，无法判读"))
    else:
        qy = [p[1] for p in qc]
        if any(q < 0 for q in qy):
            msgs.append(("err", "Q 异常：出现负值，检查端口连接/设置"))
        else:
            qi = 0
            for i, q in enumerate(qy):
                if q > qy[qi]:
                    qi = i
            if qi in (0, len(qy) - 1):
                msgs.append(("warn", "Q 峰值在频段边缘/外，建议扩大扫频"))
            else:
                msgs.append(("ok", "Q 峰值正常（Qmax=%.3f）" % qy[qi]))
    return msgs

class _EmHook(object):
    def __init__(self, gui, layout, em):
        self.gui = gui
        self.layout = layout
        self.em = em
        orig_collect = gui._collect_cfg
        def collect(validate=True):
            cfg = orig_collect(validate=validate)
            if cfg is not None:
                try:
                    le, geom_vars = em._layout_em_cfg()
                    if le.get("enabled") and geom_vars:
                        exist = {v.get("name") for v in cfg.get("variables") or []}
                        merged = list(cfg.get("variables") or [])
                        for g in geom_vars:
                            if g["name"] not in exist:
                                merged.append({ "name": g["name"], "init": g["init"],
                                    "min": g["min"], "max": g["max"]})
                        cfg["variables"] = merged
                        cfg["layout_em"] = le
                        tech = layout._collect_tech()
                        warns = require_tech_ready(
                            tech, need=("layermap", "proc"))
                        for k in ("techlib", "techlib_dir", "layermap",
                                  "proc", "cds_lib"):
                            if tech.get(k):
                                cfg[k] = tech[k]
                        if warns:
                            layout._log(layout.a_log, "工艺检查通过（%d 条警告）：%s"
                                        % (len(warns), warns[0]))
                except Exception as exc:
                    if validate:
                        gui._dialog("版图 EM 配置错误", str(exc), error=True)
                        return None
            return cfg
        gui._collect_cfg = collect
        orig_handle = gui._handle
        def handle(ev):
            if isinstance(ev, dict) and ev.get("type") == "librf_done":
                owner = getattr(ev.get("cb"), "__self__", None)
                target = owner if isinstance(owner, _TabBase) else em
                target.on_done(ev.get("cb"), ev.get("payload") or {})
                return
            orig_handle(ev)
        gui._handle = handle

def register(gui):
    from tkinter import ttk
    state = {"busy": False}
    em_frame = ttk.Frame(gui.notebook)
    em = _EmOptTab(gui, em_frame, state=state)
    layout_frame = ttk.Frame(gui.notebook)
    layout = _LayoutTab(gui, layout_frame, state=state)
    layout.em_tab = em
    gui.notebook.add(layout_frame, text=" 版图 ")
    gui.notebook.add(em_frame, text=" EM ")
    gui.layout_tab = layout_frame
    gui.em_sim_tab = em_frame
    gui.layout_page = layout
    gui.em_page = em
    hook = _EmHook(gui, layout, em)
    return hook
