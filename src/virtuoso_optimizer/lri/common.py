# -*- coding: utf-8 -*-
"""lri.common — 编码/输出/路径/错误/文件与命令等基础工具。"""
from __future__ import print_function

import glob
import json
import os
import shutil
import subprocess
import sys
import time
import builtins

_native_open = builtins.open

def open(file, mode="r", *args, **kwargs):  # noqa: A001 - 有意覆盖内建 open
    if "b" not in mode and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
        if not any(c in mode for c in "wxa+"):
            kwargs.setdefault("errors", "replace")
    return _native_open(file, mode, *args, **kwargs)

def _force_utf8_stdio():
    import io as _io
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        _buf = getattr(_stream, "buffer", None)
        _enc = (getattr(_stream, "encoding", "") or "").lower()
        if _buf is not None and _enc not in ("utf-8", "utf8"):
            try:
                setattr(sys, _name, _io.TextIOWrapper(
                    _buf, encoding="utf-8", errors="replace", line_buffering=True))
            except Exception:
                pass

_force_utf8_stdio()

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # virtuoso_optimizer/
_HERE = os.path.join(_PKG_ROOT, "data")                                  # 随包数据目录
REPO_ROOT = os.path.dirname(os.path.dirname(_PKG_ROOT))                  # 仓库根（src/ 上一级）
TEST_FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")

SKILL_IL = os.path.join(_HERE, "librf.il")

DEF_LIB = "LIBRF"

DEF_WS = "~/virtuoso_work"

DEF_FREQ = "1e9:30e9:21"

DEF_EMX_TIMEOUT = 1800

DEF_SKILL_TIMEOUT = 600

LAYERMAP_NAMES = ("*.layermap", "layer.map", "layermap.txt", "layermap")

PROC_NAMES = ("*.proc",)

TECHLIB_SUBDIRS = ()

TECH_PROFILE_NAME = "tech_profile.json"

try:
    from ..netlist.cross_section import (
        parse_proc as _xs_parse_proc,
        parse_layermap as _xs_parse_layermap,
        validate_flow_stack as _xs_validate_flow_stack)
except Exception:
    _xs_parse_proc = _xs_parse_layermap = _xs_validate_flow_stack = None

DEFAULT_TECH = { "name": "Generic RF CMOS (built-in default)", "techlib": "RFCMOS",
    "techlib_dir": "", "layermap": "", "proc": "", "layers": {
        "spiral": "M5", "underpass": "M4", "route": "M3", "ground": "M3",
        "via_sp": "V4", "via_un": "V3", "marker": "MARKER", }, "text": {
        "M5": "M5TXT", "M4": "M4TXT", "M3": "M3TXT", "M2": "M2TXT", "M1": "M1TXT", }, "drc": {
        "min_w": 1.0, "max_w": 30.0, "min_s": 1.0, "min_area": {"M5": 4.0, "M4": 4.0, "M3": 4.0},
        "via": {"cut": 0.5, "spacing": 0.5, "enclosure": 0.2}, }, }

DEF_PORT_LAYER = DEFAULT_TECH["text"].get(DEFAULT_TECH["layers"]["spiral"],
                                          "%sTXT" % DEFAULT_TECH["layers"]["spiral"])

class LibrfError(Exception):
    pass

def _eprint(*a):
    print(*a, file=sys.stderr)

def logv(msg, verbose):
    if verbose:
        _eprint("[librf] %s" % msg)

def _env_path(name, default):
    v = os.environ.get(name)
    return v if v else default

def _which(name):
    try:
        return shutil.which(name)
    except AttributeError:
        pass
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None

_TOOL_OWNED = set()

_OWNED_LOADED = False

def _manifest_path():
    return os.path.join(os.getcwd(), "work", ".librf_owned.json")

def _load_owned_once():
    global _OWNED_LOADED
    if _OWNED_LOADED:
        return
    _OWNED_LOADED = True
    mp = _manifest_path()
    try:
        with open(mp) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            for p in data["files"]:
                if p:
                    _TOOL_OWNED.add(os.path.abspath(os.path.expanduser(str(p))))
    except (IOError, OSError, ValueError):
        pass

def _save_owned():
    mp = _manifest_path()
    try:
        d = os.path.dirname(mp)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        tmp = mp + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"files": sorted(_TOOL_OWNED)}, f, indent=1)
            f.write("\n")
        os.replace(tmp, mp)
    except (IOError, OSError):
        pass

def note_created(path):
    if not path:
        return
    p = os.path.abspath(os.path.expanduser(str(path)))
    _load_owned_once()
    if p not in _TOOL_OWNED:
        _TOOL_OWNED.add(p)
        _save_owned()

def is_tool_owned(path):
    _load_owned_once()
    return os.path.abspath(os.path.expanduser(str(path))) in _TOOL_OWNED

def backup_file(path):
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = "%s.bak-%s" % (path, stamp)
    n = 1
    while os.path.exists(bak):
        bak = "%s.bak-%s.%d" % (path, stamp, n)
        n += 1
    shutil.copyfile(path, bak)
    return bak

def _managed_roots():
    roots = []
    for cand in (os.path.join(os.getcwd(), "work"), os.path.join(os.getcwd(), "logs")):
        if cand:
            roots.append(os.path.abspath(cand))
    ws = os.environ.get("LIBRF_WS")
    if ws:
        ws_abs = os.path.abspath(os.path.expanduser(ws))
        roots.append(os.path.join(ws_abs, DEF_LIB))
    return tuple(roots)

def _assert_writable(path, what, managed=True):
    path = os.path.abspath(os.path.expanduser(str(path)))
    if os.path.exists(path) and not is_tool_owned(path):
        raise LibrfError("拒绝覆盖已有%s：%s\n"
            "本工具不删除/覆盖目标机中已存在的文件（含输出领地内的" "非本工具产物）。\n"
            "若确实要重建它，请自行删除后重试（工具不会代劳）。\n"
            "（工具输出领地：%s）" % (what, path, "; ".join(_managed_roots()) or "(空)"))
    return path

_INTERNAL_STATE_NAMES = ( ".librf_owned.json", ".geom_cache.json", )

_INTERNAL_STATE_PREFIXES = ("driver_", "res_", "dbaccess.log", "strmout_")

_INTERNAL_STATE_SUFFIXES = (".emx.log", ".tmp", ".old-", ".bak-")

def _is_internal_state(path):
    base = os.path.basename(path)
    if base in _INTERNAL_STATE_NAMES:
        return True
    for p in _INTERNAL_STATE_PREFIXES:
        if base.startswith(p):
            return True
    for s in _INTERNAL_STATE_SUFFIXES:
        if base.endswith(s) or s in base:
            return True
    return False

def _prepare_write(path, what, backup):
    path = _assert_writable(path, what)
    if backup is None:
        backup = not _is_internal_state(path)
    if backup and os.path.isfile(path):
        backup_file(path)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    return path

def safe_write_text(path, text, what="文件", backup=None):
    path = _prepare_write(path, what, backup)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    note_created(path)
    return path

def _assert_cell_writable(libdir, cell, view="layout"):
    cell = str(cell or "")
    libdir = os.path.abspath(os.path.expanduser(str(libdir or "")))
    if not cell or not libdir:
        return
    view_dir = os.path.join(libdir, cell, view)
    if os.path.isdir(view_dir):
        for name in os.listdir(view_dir):
            if "cdslck" in name.lower():
                raise LibrfError("目标 cellview %s/%s/%s 被 Virtuoso 占用"
                    "（存在 %s）。请先关闭 Virtuoso 中打开的对应编辑器，"
                    "或换 cell 名重试。未写入任何数据，已安全退出。" % (libdir, cell, view, name))
    marker = os.path.join(libdir, cell, ".librf_owned")
    cv_file = os.path.join(view_dir, "layout.oa")
    cv_alt = os.path.join(view_dir, "layout.cdb")
    has_data = os.path.isfile(cv_file) or os.path.isfile(cv_alt)
    if has_data and not (is_tool_owned(marker) or is_tool_owned(view_dir)
                         or os.path.isfile(marker)):
        raise LibrfError("拒绝覆盖已有 cellview：%s/%s/%s\n"
            "该 cell 不是本工具产物（无 .librf_owned 标记）。\n"
            "若确实要重建它，请自行删除库目录后重试（工具不会代劳）。" % (libdir, cell, view))

def _mark_cell_owned(libdir, cell):
    cell = str(cell or "")
    libdir = os.path.abspath(os.path.expanduser(str(libdir or "")))
    if not cell or not libdir:
        return
    marker = os.path.join(libdir, cell, ".librf_owned")
    try:
        d = os.path.dirname(marker)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        if not os.path.isfile(marker):
            with open(marker, "w") as f:
                f.write("created by librf_opt\n")
        note_created(marker)
    except (IOError, OSError):
        pass

def append_unique(path, lines, what="文件"):
    path = os.path.abspath(os.path.expanduser(str(path)))
    want = [l for l in lines if l and str(l).strip()]
    existing = ""
    if os.path.isfile(path):
        with open(path) as f:
            existing = f.read()
        have = set(l.strip() for l in existing.splitlines())
        want = [l for l in want if str(l).strip() not in have]
        if not want:
            return [], None
        bak = backup_file(path)
    else:
        bak = None
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
    with open(path, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        for l in want:
            f.write(str(l).rstrip() + "\n")
    note_created(path)
    return want, bak

def atomic_write_json(path, obj, backup=None):
    path = _prepare_write(path, "JSON 文件", backup)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    note_created(path)

def read_json(path):
    with open(path, "r") as f:
        return json.load(f)

def parse_eng(s):
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("_", "")
    try:
        return float(t)
    except ValueError:
        pass
    table = [("meg", 1e6), ("T", 1e12), ("G", 1e9), ("M", 1e6), ("k", 1e3),
             ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15), ("a", 1e-18)]
    neg = t.startswith("-") or t.startswith("+")
    t2 = t[1:] if neg else t
    low2 = t2.lower()
    for suf, mul in table:
        if suf in ("M", "m"):
            hit = t2.endswith(suf)
        else:
            hit = low2.endswith(suf.lower())
        if hit and len(t2) > len(suf):
            try:
                v = float(t2[:-len(suf)]) * mul
                return -v if t.startswith("-") else v
            except ValueError:
                break
    raise ValueError("parse_eng: 无法解析 %r（示例: 1.2n / 24G / 150u）" % (s,))

def fmt_eng(v, unit=""):
    if v is None:
        return "None"
    v = float(v)
    if v == 0.0:
        return "0" + unit
    sign = "-" if v < 0 else ""
    a = abs(v)
    for mul, suf in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"),
                     (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
                     (1e-12, "p"), (1e-15, "f"), (1e-18, "a")):
        if a >= mul * 0.999:
            mant = a / mul
            if abs(mant - round(mant)) < 1e-9:
                return sign + str(int(round(mant))) + suf + unit
            return sign + ("%.4f" % mant).rstrip("0").rstrip(".") + suf + unit
    return "%g%s" % (v, unit)

def _decode(b):
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    return b.decode("utf-8", "replace")

def run_cmd(cmd, cwd=None, timeout=None, verbose=False, env=None):
    if isinstance(cmd, (list, tuple)):
        cmd_list = [str(c) for c in cmd]
        cmd_show = " ".join(cmd_list)
    else:
        cmd_list = cmd
        cmd_show = cmd
    logv("run: %s (cwd=%s)" % (cmd_show, cwd), verbose)
    env_all = None
    if env is not None:
        env_all = os.environ.copy()
        env_all.update(env)
    try:
        proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=cwd, env=env_all)
    except OSError as e:
        return {"ok": False, "rc": -1, "stdout": "",
                "stderr": "启动失败: %s" % e, "cmd": cmd_show, "timeout": False}
    timed_out = False
    try:
        if timeout:
            so, se = proc.communicate(timeout=timeout)
        else:
            so, se = proc.communicate()
    except TypeError:
        so, se = proc.communicate()
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        so, se = proc.communicate()
        timed_out = True
    return {"ok": (proc.returncode == 0 and not timed_out),
            "rc": proc.returncode, "stdout": _decode(so),
            "stderr": _decode(se), "cmd": cmd_show, "timeout": timed_out}

def _probe_data_file(basename, env_vars, extra_dirs, patterns=(), exclude=None):
    skip = os.path.abspath(os.path.expanduser(str(exclude))) if exclude else None
    def _ok(hit):
        if not os.path.isfile(hit):
            return False
        return not (skip and os.path.abspath(hit) == skip)
    for var in env_vars:
        val = os.environ.get(var)
        if not val:
            continue
        val = os.path.expanduser(val.strip())
        if _ok(val):
            return val, "env"
        if os.path.isdir(val):
            for pat in ((basename,) if not patterns else patterns):
                for hit in sorted(glob.glob(os.path.join(val, pat))):
                    if _ok(hit):
                        return hit, "env"
    for d in extra_dirs:
        if not d:
            continue
        d = os.path.expanduser(str(d))
        if _ok(d):
            return d, "given"
        if os.path.isdir(d):
            names = patterns or (basename,)
            for pat in names:
                for hit in sorted(glob.glob(os.path.join(d, pat))):
                    if _ok(hit):
                        return hit, "given"
                for hit in sorted(glob.glob(os.path.join(d, "*", pat))):
                    if _ok(hit):
                        return hit, "given"
    return None, "none"
