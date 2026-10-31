# -*- coding: utf-8 -*-
"""lri.ui.tech_tab — 工艺档表单页。"""
from __future__ import print_function

class _TechTab:
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
        cfg = cfg if isinstance(cfg, dict) else {}
        for key, _label, default, _kind in self.FIELDS:
            val = cfg.get(key)
            self.vars[key].set(default if val is None else str(val))
    def collect_tech(self):
        import librf_opt
        v = {k: (var.get() or "").strip() for k, var in self.vars.items()}
        return librf_opt.build_tech(
            techlib=v.get("techlib") or None,
            techlib_dir=v.get("techlib_dir") or None,
            layermap=v.get("layermap") or None,
            proc=v.get("proc") or None,
            cds_lib=v.get("cds_lib") or None)
    def tech_cfg_keys(self):
        out = {}
        for key, _label, _default, _kind in self.FIELDS:
            v = (self.vars[key].get() or "").strip()
            if v:
                out[key] = v
        return out
    def check(self, parent=None, show_dialog=False):
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
        return self.check(parent=parent, show_dialog=True)
    def _msg(self, title, text, error=False):
        try:
            from tkinter import messagebox
            fn = messagebox.showerror if error else messagebox.showwarning
            fn(title, text, parent=self.root)
        except Exception:
            print(text)
    def show_cross_section(self):
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
