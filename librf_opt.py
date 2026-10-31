# -*- coding: utf-8 -*-
from __future__ import print_function
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
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
_HERE = os.path.dirname(os.path.abspath(__file__))
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
    from proc_cross_section import (
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
_TECH_CACHE = {}
def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
def load_tech_profile(work=None, path=None, use_cache=True):
    key = os.path.abspath(os.path.expanduser(path)) if path else (
        "auto|" + os.path.abspath(os.path.expanduser(work or os.getcwd())))
    if use_cache and key in _TECH_CACHE:
        return _TECH_CACHE[key]
    candidates = []
    if path:
        candidates.append(os.path.expanduser(str(path)))
    else:
        env = os.environ.get("LIBRF_TECH_PROFILE")
        if env:
            candidates.append(os.path.expanduser(env))
        if work:
            candidates.append(os.path.join(os.path.expanduser(str(work)), TECH_PROFILE_NAME))
        candidates.append(os.path.join(os.getcwd(), TECH_PROFILE_NAME))
        candidates.append(os.path.join(_HERE, TECH_PROFILE_NAME))
    raw = {}
    src = "default"
    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                with open(cand) as f:
                    raw = json.load(f) or {}
                src = cand
                break
            except (IOError, ValueError) as e:
                raise LibrfError("工艺配置 %s 解析失败: %s" % (cand, e))
    raw = dict((k, v) for k, v in raw.items() if not str(k).startswith("_doc"))
    prof = _deep_merge(DEFAULT_TECH, raw)
    prof["_src"] = src
    if use_cache:
        _TECH_CACHE[key] = prof
    return prof
TECH_REQUIRED_FILES = (
    ("cds_lib", "cds.lib（工作区库映射）"),
    ("layermap", "layermap（层名->GDS 层号）"),
    ("proc", "EMX proc（工艺叠层）"),
    ("techlib_dir", "工艺库目录（层定义）"),
)
TECH_METAL_ROLES = ("spiral", "underpass", "route", "ground")
TECH_VIA_ROLES = ("via_sp", "via_un")
TECH_MARKER_ROLES = ("marker",)
TECH_LAYER_ROLES = TECH_METAL_ROLES + TECH_VIA_ROLES + TECH_MARKER_ROLES
TECH_NEED_KEYS = ("layermap", "proc", "techlib_dir", "cds_lib")
def _tech_deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _tech_deep_merge(out[k], v)
        elif v not in (None, "", [], {}):
            out[k] = v
    return out
def build_tech(techlib=None, techlib_dir=None, layermap=None, proc=None,
               cds_lib=None, layers=None, text=None, drc=None, base=None):
    base = base if base is not None else load_tech_profile()
    prof = _tech_deep_merge(base, {})
    if techlib:
        prof["techlib"] = str(techlib).strip()
    if techlib_dir:
        prof["techlib_dir"] = os.path.abspath(
            os.path.expanduser(str(techlib_dir).strip()))
    if layermap:
        prof["layermap"] = os.path.abspath(
            os.path.expanduser(str(layermap).strip()))
    if proc:
        prof["proc"] = os.path.abspath(
            os.path.expanduser(str(proc).strip()))
    if cds_lib:
        prof["cds_lib"] = os.path.abspath(
            os.path.expanduser(str(cds_lib).strip()))
    if layers:
        prof["layers"] = _tech_deep_merge(prof.get("layers") or {}, layers)
    if text:
        prof["text"] = _tech_deep_merge(prof.get("text") or {}, text)
    if drc:
        prof["drc"] = _tech_deep_merge(prof.get("drc") or {}, drc)
    prof["_src"] = "(GUI/CLI 输入; base=%s)" % (base.get("_src") or "default")
    return prof
def _role_kind(role):
    if role in TECH_METAL_ROLES:
        return "metal"
    if role in TECH_VIA_ROLES:
        return "via"
    return "marker"
def _tech_role_layers(prof):
    layers = (prof or {}).get("layers") or {}
    out, seen = [], set()
    for role in TECH_LAYER_ROLES:
        name = layers.get(role)
        if name is None:
            continue
        name = str(name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((role, name))
    return out
def _tech_file_ok(path, want_dir=False):
    if not path:
        return False
    p = os.path.expanduser(str(path))
    try:
        return os.path.isdir(p) if want_dir else os.path.isfile(p)
    except (TypeError, ValueError):
        return False
def _resolve_tech_files(prof, work=None):
    prof = dict(prof or {})
    notes = []
    ws = os.path.expanduser(_env_path("LIBRF_WS", DEF_WS))
    anchor = work or os.getcwd()
    search_dirs = [os.environ.get("LIBRF_TECHLIB_DIR", ""),
                   prof.get("techlib_dir") or "", ws, anchor, _HERE,
                   os.path.join(_HERE, "tech"), os.path.join(ws, "tech")]
    search_dirs = [d for d in search_dirs
                   if d and os.path.isdir(os.path.expanduser(str(d)))]
    def _fill(key, env_vars, patterns, want_dir=False):
        given = str(prof.get(key) or "").strip()
        if given and _tech_file_ok(given, want_dir):
            prof[key] = os.path.abspath(os.path.expanduser(given))
            return
        found, _src = _probe_data_file(key, env_vars, search_dirs,
                                       patterns=tuple(patterns))
        if found:
            if given:
                notes.append("配置的 %s 不可用（%s），已改用探查到的 %s"
                             % (key, given, found))
            prof[key] = os.path.abspath(os.path.expanduser(str(found)))
        elif given:
            prof[key] = os.path.abspath(os.path.expanduser(given))
    _fill("layermap", ("LIBRF_LAYERMAP",), LAYERMAP_NAMES)
    _fill("proc", ("LIBRF_PROC",), PROC_NAMES)
    _fill("cds_lib", ("LIBRF_CDSSETUP",), ("cds.lib",))
    given = str(prof.get("techlib_dir") or "").strip()
    if given and _tech_file_ok(given, True):
        prof["techlib_dir"] = os.path.abspath(os.path.expanduser(given))
    else:
        techlib = str(prof.get("techlib") or "").strip()
        found = None
        if techlib:
            for d in search_dirs:
                if not d:
                    continue
                cand = os.path.join(os.path.expanduser(str(d)), techlib)
                if os.path.isdir(cand):
                    found = cand
                    break
        if found:
            if given:
                notes.append("配置的 techlib_dir 不可用（%s），已改用探查到的 %s"
                             % (given, found))
            prof["techlib_dir"] = os.path.abspath(os.path.expanduser(found))
        elif given:
            prof["techlib_dir"] = os.path.abspath(os.path.expanduser(given))
    return prof, notes
def _merge_tools_into_tech(tech, tools):
    out = dict(tech or {})
    for key in ("layermap", "proc", "techlib_dir"):
        want_dir = (key == "techlib_dir")
        if _tech_file_ok(out.get(key), want_dir):
            continue
        hit = (tools or {}).get(key) or {}
        path = hit.get("path") if hit.get("found") else None
        if path and _tech_file_ok(path, want_dir):
            out[key] = os.path.abspath(os.path.expanduser(str(path)))
    return out
def _parse_layermap_generic(path):
    by_name = {}
    try:
        with open(path) as fh:
            for raw in fh:
                toks = raw.split("#", 1)[0].split()
                if len(toks) < 4:
                    continue
                try:
                    by_name.setdefault(toks[0], (int(toks[2]), int(toks[3])))
                except ValueError:
                    pass
    except OSError as e:
        raise LibrfError("layermap 读取失败：%s\n文件：%s" % (e, path))
    return {"by_name": by_name}
def _parse_proc_generic(path):
    data = {"conductors": [], "vias": [], "stack": []}
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        raise LibrfError("proc 读取失败：%s\n文件：%s" % (e, path))
    for ln, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"conductor\s+(\S+)\s+(.+?)\s+(\S+)$", line, re.I)
        if m:
            try:
                thick = float(m.group(1))
            except ValueError:
                thick = None
            name = m.group(3)
            data["conductors"].append(
                {"name": name, "thickness": thick, "line": ln})
            data["stack"].append({"kind": "conductor", "name": name,
                                  "thickness": thick, "line": ln})
            continue
        m = re.match(r"via\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", line, re.I)
        if m:
            try:
                res = float(m.group(3))
            except ValueError:
                res = None
            data["vias"].append({"bottom": m.group(1), "top": m.group(2),
                                 "resistance": res, "name": m.group(4),
                                 "line": ln})
    if not data["conductors"]:
        raise LibrfError("proc 中未识别到任何 conductor 行：%s" % path)
    return data
def _tech_flow(prof):
    raw = (prof or {}).get("flow")
    if not raw:
        return None
    strict = bool((prof or {}).get("strict_flow"))
    if isinstance(raw, dict):
        metals = raw.get("metals") or raw.get("stack") or []
        vias = raw.get("vias") or []
        gaps = raw.get("gaps") or []
        via_res = raw.get("via_res") or {}
        strict = strict or bool(raw.get("strict"))
    elif isinstance(raw, (list, tuple)):
        metals, vias, gaps, via_res = list(raw), [], [], {}
    else:
        return None
    metals = [str(x).strip() for x in metals if str(x).strip()]
    vias_n = []
    for item in vias:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            vias_n.append((str(item[0]).strip(), str(item[1]).strip(),
                           str(item[2]).strip() if len(item) > 2 else ""))
    gaps_n = []
    for item in gaps:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            try:
                gaps_n.append((str(item[0]).strip(), str(item[1]).strip(),
                               float(item[2])))
            except (TypeError, ValueError):
                pass
    via_res_n = {}
    if isinstance(via_res, dict):
        for k, v in via_res.items():
            try:
                via_res_n[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
    if not metals and not vias_n:
        return None
    return {"metals": metals, "vias": vias_n, "gaps": gaps_n,
            "via_res": via_res_n, "strict": strict}
def _proc_conductor_z(proc_data):
    z, out = 0.0, {}
    for item in (proc_data or {}).get("stack") or []:
        thick = item.get("thickness")
        if thick is None:
            break
        try:
            thick = float(thick)
        except (TypeError, ValueError):
            break
        z0, z = z, z + thick
        if item.get("kind") == "conductor" and item.get("name"):
            out.setdefault(item["name"], (z0, z))
    return out
def _flow_warnings(flow, cond_names, proc_data, cond_z):
    warnings = []
    metals = flow.get("metals") or []
    pos = dict((n, i) for i, n in enumerate(cond_names))
    missing = [n for n in metals if n not in pos]
    if missing:
        warnings.append("flow 金属 %s 未在 proc 中找到（跳过对应顺序校验）"
                        % ", ".join(missing))
    expected = [n for n in metals if n in pos]
    actual = [n for _p, n in sorted((pos[n], n) for n in expected)]
    if expected and actual != expected:
        warnings.append("proc 流程金属顺序与 flow 不一致：flow=%s，实际=%s"
                        "（当前工艺，仅告警）"
                        % (" → ".join(expected), " → ".join(actual)))
    vias = proc_data.get("vias") or []
    for bottom, top, name in flow.get("vias") or []:
        hit = [v for v in vias
               if v.get("bottom") == bottom and v.get("top") == top
               and (not name or v.get("name") == name)]
        if not hit:
            warnings.append("flow 通孔 %s（%s→%s）未在 proc 中找到（当前工艺）"
                            % (name or "?", bottom, top))
    for low, high, want in flow.get("gaps") or []:
        lo, hi = cond_z.get(low), cond_z.get(high)
        if not lo or not hi:
            continue
        gap = hi[0] - lo[1]
        if abs(gap - want) > 0.02:
            warnings.append("间距偏差 %s→%s：实测 %.3f μm，期望 %.3f μm"
                            "（当前工艺，仅告警）" % (low, high, gap, want))
    want_res = flow.get("via_res") or {}
    for v in vias:
        want = want_res.get(v.get("name"))
        got = v.get("resistance")
        if want is None or got is None:
            continue
        if abs(got - want) > max(0.02, 0.1 * abs(want)):
            warnings.append("通孔电阻偏差 %s：实测 %s Ω/via，期望 %g Ω/via"
                            "（当前工艺，仅告警）" % (v.get("name"), got, want))
    return warnings
def _generic_stack_check(prof, lmap_by_name, proc_data=None):
    errors, warnings = [], []
    roles = _tech_role_layers(prof)
    if not roles:
        errors.append("工艺配置 layers 为空：请至少定义 spiral/underpass/route/"
                      "via_sp 等角色（当前工艺）")
    if lmap_by_name is not None:
        errors.extend(_tech_lm_errors({"by_name": lmap_by_name}, prof))
    if proc_data is not None:
        cond_names = [c.get("name") for c in (proc_data.get("conductors") or [])
                      if c.get("name")]
        for role, name in roles:
            if _role_kind(role) == "metal" and name not in cond_names:
                errors.append("proc 中缺少角色 %s 的导体层 %s（当前工艺）"
                              % (role, name))
        flow = _tech_flow(prof)
        if flow:
            warnings.extend(_flow_warnings(
                flow, cond_names, proc_data, _proc_conductor_z(proc_data)))
    elif _tech_flow(prof):
        warnings.append("工艺配置提供了 flow，但本次未提供 proc，"
                        "已跳过叠层顺序/通孔校验")
    return errors, warnings
def _tech_lm_errors(lmap_data, prof=None):
    if prof is None:
        return []
    by_name = {}
    if isinstance(lmap_data, dict):
        by_name = lmap_data.get("by_name") or {}
        if not isinstance(by_name, dict):
            by_name = {}
    errs = []
    for role, name in _tech_role_layers(prof):
        if name not in by_name:
            errs.append("layermap 缺少层 %s（角色 %s，当前工艺）"
                        % (name, role))
    return errs
def _tech_error_text(errors, prof):
    lm = os.path.expanduser(str((prof or {}).get("layermap") or "")) or "(未填)"
    pr = os.path.expanduser(str((prof or {}).get("proc") or "")) or "(未填)"
    return ("工艺检查未通过，已停止本次任务：\n  " + "\n  ".join(errors)
            + "\n文件：layermap=%s\n      proc=%s\n"
              "建议：核对 tech_profile.json 的 layers 角色映射与对应 PDK "
              "文件；换工艺只需改 layers（及可选 flow），无需改代码。"
            % (lm, pr))
def _missing_tech_error(missing):
    lines = ["  %s = (未填写/未探查到)" % label for _key, label in missing]
    return ("工艺文件缺失或不可读，已停止本次任务：\n" + "\n".join(lines)
            + "\n建议：在「工艺」页填写，或用环境变量指定 "
              "LIBRF_LAYERMAP / LIBRF_PROC / LIBRF_TECHLIB_DIR / "
              "LIBRF_CDSSETUP；也可把文件放到工作目录或工作空间中由工具"
              "自动探查。")
def _normalize_need(need):
    if need is None:
        return ("layermap",)
    if isinstance(need, str):
        need = (need,)
    keys = []
    for k in need:
        k = str(k).strip()
        if k in TECH_NEED_KEYS and k not in keys:
            keys.append(k)
    return tuple(keys)
def require_tech_ready(prof, need=None, work=None, probe=True):
    prof = dict(prof or {})
    notes = []
    if probe:
        prof, notes = _resolve_tech_files(prof, work=work)
    need = _normalize_need(need)
    warnings = list(notes)
    missing = []
    for key, label in TECH_REQUIRED_FILES:
        ok = _tech_file_ok(prof.get(key), key == "techlib_dir")
        if key in need:
            if not ok:
                missing.append((key, label))
        elif not ok and key in ("techlib_dir", "cds_lib"):
            warnings.append("%s 未配置/不可用（%s）；将回退默认探查或自动生成"
                            % (label, "未填"))
    if missing:
        raise LibrfError(_missing_tech_error(missing))
    lmap_path = os.path.expanduser(str(prof.get("layermap") or ""))
    lmap_data = None
    lmap_by_name = None
    if lmap_path and _tech_file_ok(lmap_path):
        if _xs_parse_layermap is not None:
            try:
                lmap_data = _xs_parse_layermap(lmap_path)
            except Exception as e:
                raise LibrfError(
                    "layermap 解析失败：%s\n文件：%s\n建议：确认每行格式为 "
                    "name purpose layer datatype（空格/制表符分隔）。"
                    % (e, lmap_path))
        else:
            lmap_data = _parse_layermap_generic(lmap_path)
        lmap_by_name = lmap_data.get("by_name") or {}
    proc_path = os.path.expanduser(str(prof.get("proc") or ""))
    proc_data = None
    if "proc" in need and _tech_file_ok(proc_path):
        if _xs_parse_proc is not None:
            try:
                proc_data = _xs_parse_proc(proc_path)
            except Exception as e:
                raise LibrfError(
                    "proc 解析失败：%s\n文件：%s\n建议：检查报错行号处的 "
                    "conductor/layer/via 语法，或改用 PDK 原始 proc。"
                    % (e, proc_path))
        else:
            proc_data = _parse_proc_generic(proc_path)
    errors, gen_warnings = _generic_stack_check(prof, lmap_by_name, proc_data)
    warnings.extend(gen_warnings)
    flow = _tech_flow(prof)
    strict = bool(prof.get("strict_flow") or (flow and flow.get("strict")))
    if strict:
        if flow is None:
            warnings.append("已请求 strict_flow，但 profile 未提供 flow 段，"
                            "已跳过叠层严格校验")
        elif proc_data is None:
            warnings.append("已请求 strict_flow，但本次未提供/未需要 proc，"
                            "已跳过 proc_cross_section 严格校验")
        elif _xs_validate_flow_stack is None:
            warnings.append("已请求 strict_flow，但 proc_cross_section 不可用，"
                            "已降级为通用检查")
        else:
            try:
                strict_errs, strict_warns = _xs_validate_flow_stack(
                    proc_data, lmap_data, flow=flow)
            except Exception as e:
                raise LibrfError("工艺叠层严格校验异常：%s\n文件：%s"
                                 % (e, proc_path))
            errors.extend(str(x) for x in (strict_errs or []))
            warnings.extend(str(x) for x in (strict_warns or []))
    if errors:
        raise LibrfError(_tech_error_text(errors, prof))
    out = []
    for w in warnings:
        s = str(w)
        if s and s not in out:
            out.append(s)
    return out
def tech_layer(prof, role):
    lay = (prof or {}).get("layers") or {}
    return lay.get(role, role)
def tech_text(prof, metal):
    txt = (prof or {}).get("text") or {}
    return txt.get(metal, "%sTXT" % metal)
def tech_drc(prof):
    return (prof or {}).get("drc") or DEFAULT_TECH["drc"]
def _skill_val(v):
    if isinstance(v, bool):
        return "t" if v else "nil"
    if v is None:
        return "nil"
    if isinstance(v, str):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    return str(v)

def _stack_il(prof):
    """工艺档可选 stack 表 -> SKILL 的叠层全局量。

    prof["stack"] 形如::

        {"metals": ["M1", "M2", ...],            # 自下而上
         "vias": {"V1": {"lower": "M1", "upper": "M2",
                         "cut": 0.09, "space": 0.11,
                         "enc_lo": 0.03, "enc_hi": 0.02}, ...}}

    也接受顶层 stack_order + metals/vias。缺任意一段就返回空串：
    SKILL 会退回 via_sp 角色单跳连接，不会因缺表而画错通孔。
    """
    prof = prof or {}
    st = prof.get("stack") or {}
    metals = st.get("metals") or prof.get("metals") or {}
    order = list(st.get("order") or prof.get("stack_order") or [])
    if not order and isinstance(metals, (list, tuple)):
        order = list(metals)
    vias = st.get("vias") or prof.get("vias") or {}
    if not order or not metals:
        return ""
    if any(m not in metals for m in order):
        return ""
    vd = {}
    if isinstance(vias, dict):
        for name, v in vias.items():
            if isinstance(v, dict):
                vv = dict(v)
                vv.setdefault("name", name)
                vd[name] = vv
    elif isinstance(vias, (list, tuple)):
        for v in vias:
            if isinstance(v, dict) and v.get("name"):
                vd[v["name"]] = v

    def _enc(v, key, alt):
        val = v.get(key, v.get(alt, 0.0))
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    rows = []
    for lo, hi in zip(order, order[1:]):
        hit = None
        for name, v in vd.items():
            if v.get("lower") == lo and v.get("upper") == hi:
                hit = (name, v)
                break
        if not hit:
            return ""
        name, v = hit
        try:
            cut = float(v.get("cut", 0.0))
            space = float(v.get("space", 0.0))
        except (TypeError, ValueError):
            return ""
        rows.append('    list("%s" "%s" "%s" %s %s %s %s)' % (
            name, lo, hi, _skill_val(cut), _skill_val(space),
            _skill_val(_enc(v, "enc_lo", "enclosure")),
            _skill_val(_enc(v, "enc_hi", "enclosure"))))
    return ("; 叠层表（主/副金属与逐级通孔）\n"
            "librfStackMetals = list(%s)\n"
            "librfStackVias = list(\n%s)\n"
            % (" ".join('"%s"' % str(m) for m in order), "\n".join(rows)))
def tech_to_skill(prof):
    lay = (prof or {}).get("layers") or {}
    txt = (prof or {}).get("text") or {}
    drc = (prof or {}).get("drc") or {}
    def _alist(d):
        return "list(" + " ".join( 'list("%s" %s)' % (k, _skill_val(v)) for k, v in sorted(d.items())) + ")"
    def _drc_alist(d):
        parts = []
        for k in sorted(d.keys()):
            v = d[k]
            if isinstance(v, dict):
                parts.append('list("%s" %s)' % (k, _alist(v)))
            else:
                parts.append('list("%s" %s)' % (k, _skill_val(v)))
        return "list(" + " ".join(parts) + ")"
    techlib = (prof or {}).get("techlib") or ""
    return ("; librf_opt 注入的工艺配置（勿手改）\n"
        "librfTech = %s\n" "librfTechText = %s\n"
        "librfDrc = %s\n"
        "librfTechLib = \"%s\"\n" % (_alist(lay), _alist(txt), _drc_alist(drc), techlib)
        + _stack_il(prof))
def _techlib_name(prof=None):
    if prof and prof.get("techlib"):
        return prof["techlib"]
    return _env_path("LIBRF_TECHLIB", (DEFAULT_TECH.get("techlib") or "RFCMOS"))
def _import_discover():
    try:
        import optimizer as _opt
        return _opt.find_prog, _opt.find_file
    except Exception:
        return None, None
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
def find_tools(proc=None, verbose=False, tech=None, work=None):
    if tech is None:
        tech = load_tech_profile(work=work)
    find_prog, _find_file = _import_discover()
    out = {}
    def _prog(key, env_name, name, subdirs):
        if find_prog is not None:
            path, src = find_prog(name, (env_name,), subdirs)
        else:
            path, src = _env_path(env_name, ""), ( "env" if os.environ.get(env_name) else "none")
            if not path:
                path, src = _which(name), "which"
        out[key] = {"path": path, "found": bool(path), "src": src}
        logv("tool %-9s %s (%s)" % (key, path, "found" if path else "MISSING"), verbose)
    _prog("emx", "LIBRF_EMX", "emx", ("bin",))
    _prog("dbaccess", "LIBRF_DBACCESS", "dbAccess", ("tools/dfII/bin", "bin"))
    _prog("strmout", "LIBRF_STRMOUT", "strmout", ("tools/dfII/bin", "bin"))
    ws = os.path.expanduser(_env_path("LIBRF_WS", DEF_WS))
    techdir_env = os.environ.get("LIBRF_TECHLIB_DIR", "") or \
        (tech.get("techlib_dir") or "")
    search_dirs = [techdir_env, os.environ.get("LIBRF_LAYERMAP", ""), tech.get("layermap") or "",
                   ws, work or os.getcwd(), _HERE,
                   os.path.join(_HERE, "tech"), os.path.join(ws, "tech")]
    search_dirs = [d for d in search_dirs if d]
    lmap_given = (os.environ.get("LIBRF_LAYERMAP") or "").strip() or \
        (tech.get("layermap") or "").strip()
    if lmap_given and os.path.isfile(os.path.expanduser(lmap_given)):
        layermap, lsrc = os.path.abspath(os.path.expanduser(lmap_given)), \
            "env" if os.environ.get("LIBRF_LAYERMAP") else "profile"
    else:
        layermap, lsrc = _probe_data_file( "layermap", ("LIBRF_LAYERMAP",), search_dirs,
            patterns=LAYERMAP_NAMES)
    out["layermap"] = {"path": layermap, "found": bool(layermap), "src": lsrc}
    logv("tool %-9s %s (%s)" % ("layermap", layermap, lsrc), verbose)
    proc_given = (proc or "").strip() or os.environ.get("LIBRF_PROC", "").strip() \
        or (tech.get("proc") or "").strip()
    if proc_given and os.path.isfile(os.path.expanduser(proc_given)):
        proc_path, psrc = os.path.abspath(os.path.expanduser(proc_given)), "given"
    else:
        proc_path, psrc = _probe_data_file(
            "*.proc", ("LIBRF_PROC",), search_dirs, patterns=PROC_NAMES)
    out["proc"] = {"path": proc_path, "found": bool(proc_path), "src": psrc}
    logv("tool %-9s %s (%s)" % ("proc", proc_path, psrc), verbose)
    techdir = techdir_env
    if not techdir or not os.path.isdir(os.path.expanduser(techdir)):
        techdir = None
        subs = tuple(TECHLIB_SUBDIRS) + (_techlib_name(tech), "")
        for d in search_dirs:
            for sub in subs:
                cand = os.path.join(os.path.expanduser(d), sub)
                if sub and os.path.isdir(cand):
                    techdir = cand
                    break
            if techdir:
                break
    techdir = os.path.expanduser(techdir) if techdir else None
    out["techlib_dir"] = {"path": techdir, "found": bool(techdir and os.path.isdir(techdir)),
                          "src": "env" if techdir_env else "probe"}
    out["techlib"] = {"path": _techlib_name(tech), "found": True,
                      "src": "profile" if tech.get("_src") not in (None, "default") else "default"}
    out["tech_profile"] = {"path": tech.get("_src"), "found": True, "src": tech.get("_src")}
    return out
def require_tool(tools, key, purpose):
    t = tools.get(key) or {}
    if not t.get("found"):
        raise LibrfError("找不到 %s（%s）。可用环境变量 LIBRF_%s 指定路径。" % (key, purpose, key.upper()))
    return t["path"]
_LOCK_HINTS = ("被 Virtuoso 占用", "被virtuoso占用", "被占用", "cdslck",
               "locked by", "is locked", "cellview is locked")
def _looks_like_lock(msg):
    if not msg:
        return False
    low = msg.lower()
    for h in _LOCK_HINTS:
        if h.lower() in low:
            return True
    return False
def raise_if_locked(msg, cell=None, view=None):
    if not _looks_like_lock(msg):
        return
    who = u""
    if cell:
        who = u" %s" % cell
        if view:
            who = u"%s/%s" % (who, view)
    raise LibrfError((u"目标 cellview%s 被 Virtuoso 占用（可能正被版图/符号/原理图编辑器打开）。"
         u"请先关闭 Virtuoso 中打开的对应编辑器，或换 cell 名重试。" u"未写入任何数据，已安全退出。原始信息: %s" % (who, msg)))
_SKILL_STR_KEYS = ("type", "lib", "cell", "view", "shape", "underpass",
                   "result", "style", "gnd_layer", "layer", "name", "note",
                   "snp", "kind", "libdir", "ct_dir")
_SKILL_NUM_KEYS = ("n", "w", "s", "dout", "din", "ct", "ports", "x0", "y0",
                   "len", "gap", "np", "ns", "gnd", "fence", "cpw",
                   "freq_hint", "overlap", "pitch", "ccw", "phi0", "ex", "ey",
                   "ind_mark", "rx", "ry", "chamfer", "lead_len", "lead_gap",
                   "lead_w", "stack")
def skill_literal(key, value):
    if value is None:
        return "nil"
    if key == "ports" and isinstance(value, str) \
            and not value.strip().isdigit():
        s = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"%s"' % s
    if key in _SKILL_STR_KEYS or isinstance(value, str):
        if key not in _SKILL_NUM_KEYS:
            s = str(value).replace("\\", "\\\\").replace('"', '\\"')
            return '"%s"' % s
    try:
        f = float(value)
        if f == int(f) and abs(f) < 1e15 and key in (
                "n", "ct", "ports", "np", "ns", "gnd", "fence", "cpw", "ccw",
                "ind_mark", "stack"):
            return str(int(f))
        s = repr(f) if abs(f) < 1e-6 or abs(f) >= 1e15 else ("%g" % f)
        if ("." not in s) and ("e" not in s) and ("E" not in s) \
                and ("inf" not in s) and ("nan" not in s):
            s += ".0"
        return s
    except (TypeError, ValueError):
        s = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return '"%s"' % s
def _lib_setup_block(lib, libdir, techlib):
    return ('let((lib)\n'
        '  lib = ddGetObj("%s")\n' '  unless(lib\n'
        '    lib = dbCreateLib("%s" "%s")\n' '  )\n'
        '  when(lib && lib->techLibName != "%s"\n' '    lib->techLibName = "%s"\n'
        '  )\n' ')\n' % (lib, lib, libdir.replace("\\", "/"), techlib, techlib))
def _driver_text(layout_il, tech, lib, libdir, call, body, comment):
    return ('%s'
        'load("%s")\n' '%s'
        'errset(progn(\n' '  %s(list(nil\n'
        '  %s\n' '  ))\n'
        ') t)\n' 'exit()\n' % (comment, layout_il.replace("\\", "/"),
                      tech_to_skill(tech) + _lib_setup_block(lib, libdir, _techlib_name(tech)),
                      call, body))
def gen_driver_il(req, layout_il=None, tech=None):
    if layout_il is None:
        layout_il = SKILL_IL
    layout_il = os.path.abspath(layout_il)
    if tech is None:
        tech = load_tech_profile()
    result = os.path.abspath(req.get("result", "res.txt"))
    lib = req.get("lib", DEF_LIB)
    libdir = req.get("libdir")
    if libdir is None:
        libdir = os.path.join(os.path.dirname(result), lib)
    libdir = os.path.abspath(libdir)
    order = ["type", "lib", "cell", "view", "kind", "shape", "n", "w", "s",
             "W", "H", "corner", "seam", "tangle", "stub", "dout",
             "rx", "ry", "chamfer", "lead_len", "lead_gap", "lead_w",
             "din", "ct", "ports", "x0", "y0", "underpass", "style", "np",
             "ns", "gap", "len", "gnd", "gnd_layer", "fence", "cpw",
             "freq_hint", "ex", "ey", "ind_mark", "ct_dir"]
    seen = set()
    req2 = dict(req)
    req2["result"] = result
    req2["libdir"] = libdir
    pairs = []
    for k in order:
        if k in req2:
            pairs.append("'%s %s" % (k, skill_literal(k, req2[k])))
            seen.add(k)
    for k in sorted(req2.keys()):
        if k in seen or k in ("libdir", "result"):
            continue
        pairs.append("'%s %s" % (k, skill_literal(k, req2[k])))
    pairs.append("'result \"%s\"" % result.replace("\\", "/"))
    pairs.append("'libdir \"%s\"" % libdir.replace("\\", "/"))
    body = "\n  ".join(pairs)
    return _driver_text(layout_il, tech, lib, libdir, "librfRun", body,
                        '; librf_opt auto-generated driver (do not edit)\n')
def gen_driver_views_il(req, layout_il=None):
    return _gen_views_driver(req, layout_il, "librfMakeViews", "res_views.txt",
                             "views driver(skill)")
def gen_driver_views_emx_il(req, layout_il=None):
    return _gen_views_driver(req, layout_il, "librfEmxViews",
                             "res_views_emx.txt", "views driver(emx)")
def _gen_views_driver(req, layout_il, entry, def_result, tag, tech=None):
    if layout_il is None:
        layout_il = SKILL_IL
    if tech is None:
        tech = load_tech_profile()
    layout_il = os.path.abspath(layout_il)
    result = os.path.abspath(req.get("result", def_result))
    lib = req.get("lib", DEF_LIB)
    cell = req.get("cell", "")
    snp = os.path.abspath(req.get("snp", ""))
    libdir = req.get("libdir")
    if libdir is None:
        libdir = os.path.join(os.path.dirname(result), lib)
    libdir = os.path.abspath(libdir)
    ports = req.get("ports", "")
    if isinstance(ports, (list, tuple)):
        ports = " ".join(str(p) for p in ports)
    kind = req.get("kind", "ind")
    pairs = [ "'lib \"%s\"" % str(lib).replace("\\", "/"),
        "'cell \"%s\"" % str(cell).replace("\\", "/"), "'snp \"%s\"" % snp.replace("\\", "/"),
        "'ports \"%s\"" % str(ports).replace("\\", "/"),
        "'kind \"%s\"" % str(kind).replace("\\", "/"), "'result \"%s\"" % result.replace("\\", "/"),
        "'libdir \"%s\"" % libdir.replace("\\", "/"), ]
    body = "\n  ".join(pairs)
    return _driver_text(layout_il, tech, lib, libdir, entry, body,
                        '; librf_opt auto-generated %s (do not edit)\n' % tag)
def parse_skill_result(path):
    res = {"status": "fail", "cell": None, "bbox": None, "ports": [],
           "layers": [], "warns": [], "errs": [], "raw": path, "cells": [], "pins": []}
    if not os.path.isfile(path):
        res["errs"].append("result 文件不存在: %s" % path)
        return res
    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            toks = line.split()
            head = toks[0].upper()
            if head == "STATUS":
                res["status"] = toks[1].lower() if len(toks) > 1 else "fail"
            elif head == "CELL":
                c = toks[1] if len(toks) > 1 else None
                if c:
                    res["cells"].append(c)
                    res["cell"] = c
            elif head == "BBOX" and len(toks) >= 5:
                try:
                    res["bbox"] = [float(x) for x in toks[1:5]]
                except ValueError:
                    pass
            elif head == "PORT" and len(toks) >= 4:
                p = {"name": toks[1], "x": None, "y": None,
                     "layer": toks[-1] if len(toks) > 4 else DEF_PORT_LAYER}
                try:
                    p["x"], p["y"] = float(toks[2]), float(toks[3])
                except ValueError:
                    pass
                res["ports"].append(p)
            elif head == "PIN" and len(toks) >= 2:
                res["pins"].append({"name": toks[1], "dir": toks[2] if len(toks) > 2
                                    else "inputOutput"})
            elif head == "LAYERS":
                res["layers"] = toks[1:]
            elif head == "WARN":
                res["warns"].append(line[5:].strip())
            elif head == "ERR":
                res["errs"].append(line[3:].strip())
    return res
def views_dict_from_result(res, lib, cell):
    views = {}
    for c in res.get("cells") or []:
        parts = str(c).split("/")
        if len(parts) >= 3 and parts[-1] in ("schematic", "symbol"):
            views[parts[-1]] = c
    if res.get("status") == "ok":
        views.setdefault("schematic", "%s/%s/schematic" % (lib, cell))
        views.setdefault("symbol", "%s/%s/symbol" % (lib, cell))
    return views
def default_ws():
    return os.path.expanduser(_env_path("LIBRF_WS", DEF_WS))
def ensure_ws_lib(ws, lib=DEF_LIB, libdir=None):
    if not ws:
        raise LibrfError("ensure_ws_lib: ws 为空")
    ws = os.path.abspath(os.path.expanduser(ws))
    if not os.path.isdir(ws):
        raise LibrfError("工作空间不存在: %s（请检查 LIBRF_WS）" % ws)
    if libdir is None:
        libdir = os.path.join(ws, lib)
    libdir = os.path.abspath(libdir)
    if not os.path.isdir(libdir):
        os.makedirs(libdir)
    cds = os.path.join(ws, "cds.lib")
    define_line = "DEFINE %s %s" % (lib, libdir.replace("\\", "/"))
    try:
        added, bak = append_unique(cds, [define_line], what="cds.lib")
    except IOError as e:
        raise LibrfError("无法写入 %s: %s（工作空间可能只读）" % (cds, e))
    if bak:
        _eprint("已在改动前备份 cds.lib -> %s" % bak)
    return libdir, bool(added)
def gen_cds_lib(work_dir, lib=DEF_LIB, libdir=None, tools=None, ws=None, tech=None):
    work_dir = os.path.abspath(work_dir)
    if not os.path.isdir(work_dir):
        os.makedirs(work_dir)
    refresh = False
    if ws is not None:
        libdir, refresh = ensure_ws_lib(ws, lib=lib, libdir=libdir)
    elif libdir is None:
        libdir = os.path.join(work_dir, lib)
    libdir = os.path.abspath(libdir)
    if not os.path.isdir(libdir):
        os.makedirs(libdir)
    techdir = None
    if tools and tools.get("techlib_dir"):
        techdir = tools["techlib_dir"].get("path")
    if not techdir:
        techdir = os.environ.get("LIBRF_TECHLIB_DIR", "")
    if not techdir and tech:
        techdir = tech.get("techlib_dir") or ""
    if tech is None:
        tech = load_tech_profile()
    techlib = _techlib_name(tech)
    path = os.path.join(work_dir, "cds.lib")
    existed = os.path.isfile(path)
    want = []
    if not existed:
        cdssetup, _csrc = _probe_data_file( "cds.lib", ("LIBRF_CDSSETUP",),
            [d for d in (os.environ.get("CDS", ""), os.environ.get("CDS_INST_DIR", ""),
                         os.path.join(os.environ.get("CDS", ""), "share", "cdssetup"),
                         os.path.join(os.environ.get("CDSDIR", ""), "share", "cdssetup")) if d],
            patterns=("cds.lib",), exclude=path)
        if cdssetup and os.path.abspath(cdssetup) != os.path.abspath(path):
            want.append("SOFTINCLUDE %s" % cdssetup.replace("\\", "/"))
    if techdir and techlib:
        want.append("DEFINE %s %s" % (techlib, os.path.abspath(techdir).replace("\\", "/")))
    want.append("DEFINE %s %s" % (lib, libdir.replace("\\", "/")))
    added, bak = append_unique(path, want, what="cds.lib")
    if bak:
        _eprint("已在改动前备份 cds.lib -> %s" % bak)
    if added:
        _eprint("已向 %s 追加 %d 行（原有内容未改动）: %s" % (path, len(added), "; ".join(added)))
    return path, libdir, refresh
def resolve_work(work=None, ws=None, lib=None, tools=None):
    lib = lib or _env_path("LIBRF_LIB", DEF_LIB)
    ws_env = os.environ.get("LIBRF_WS")
    work_in = work
    work_dir = os.path.abspath(work_in if work_in else "work")
    standalone = bool(work_in) and not (ws or ws_env)
    if standalone:
        ws_val = None
        libdir = os.path.join(work_dir, lib)
        refresh = False
    else:
        ws_val = os.path.abspath(os.path.expanduser(ws or default_ws()))
        libdir = None
        refresh = False
    cds, libdir, refresh = gen_cds_lib(work_dir, lib=lib, libdir=libdir, tools=tools, ws=ws_val)
    return {"work": work_dir, "ws": ws_val, "lib": lib, "libdir": libdir,
            "refresh": refresh, "cds": cds}
def run_dbaccess(driver_il, work_dir, tools, verbose=False, timeout=DEF_SKILL_TIMEOUT):
    dba = require_tool(tools, "dbaccess", "Virtuoso dbAccess")
    cmd = [dba, "-nograph", "-load", os.path.abspath(driver_il)]
    r = run_cmd(cmd, cwd=work_dir, timeout=timeout, verbose=verbose)
    logf = os.path.join(work_dir, "dbaccess.log")
    try:
        _assert_writable(logf, "dbAccess 日志")
        with open(logf, "w") as f:
            f.write("$ %s\n" % r["cmd"])
            f.write(r["stdout"])
            f.write("\n----- stderr -----\n")
            f.write(r["stderr"])
        note_created(logf)
    except (IOError, LibrfError):
        pass
    if r["timeout"]:
        raise LibrfError("dbAccess 超时（%ss），日志 %s" % (timeout, logf))
    blob = (r["stdout"] or "") + "\n" + (r["stderr"] or "")
    if _looks_like_lock(blob):
        raise_if_locked(blob)
    if not r["ok"]:
        raise_if_locked(blob)
        raise LibrfError("dbAccess 失败 rc=%s，日志 %s\n%s"
                         % (r["rc"], logf, (r["stderr"] or r["stdout"])[-800:]))
    return r
def run_strmout(lib, cell, gds_path, work_dir, tools, verbose=False,
                timeout=DEF_SKILL_TIMEOUT, tech=None):
    so = require_tool(tools, "strmout", "strmout 版图导出")
    lmap = require_tool(tools, "layermap", "PDK layermap")
    gds_path = os.path.abspath(gds_path)
    gds_dir = os.path.dirname(gds_path)
    if gds_dir and not os.path.isdir(gds_dir):
        os.makedirs(gds_dir)
    _assert_writable(gds_path, "GDS 文件")
    run_dir = os.path.join(work_dir, "strmout_run")
    if not os.path.isdir(run_dir):
        os.makedirs(run_dir)
    logf = os.path.join(work_dir, "strmout_%s.log" % cell)
    cmd = [so, "-library", lib, "-strmFile", gds_path, "-topCell", cell,
           "-view", "layout", "-layerMap", os.path.abspath(lmap), "-techLib", _techlib_name(tech),
           "-runDir", run_dir, "-logFile", logf]
    r = run_cmd(cmd, cwd=work_dir, timeout=timeout, verbose=verbose)
    if os.path.isfile(gds_path):
        note_created(gds_path)
    if r["timeout"]:
        raise LibrfError("strmout 超时，日志 %s" % logf)
    if (not r["ok"]) or (not os.path.isfile(gds_path)):
        raise LibrfError("strmout 失败 rc=%s，日志 %s\n%s"
                         % (r["rc"], logf, (r["stderr"] or r["stdout"])[-800:]))
    return gds_path
def _snp_has_explicit_port_order(path):
    try:
        with open(path, "r") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("!"):
                    if "port order" in line.lower():
                        return True
                    continue
                if line.startswith("#"):
                    continue
                break
    except IOError:
        return False
    return False
def _snp_append_port_order(path, names):
    _assert_writable(path, "sNp 文件")
    backup_file(path)
    with open(path, "r") as f:
        txt = f.read()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("! Port order: %s\n" % " ".join(str(n) for n in names))
        f.write(txt)
    os.replace(tmp, path)
    note_created(path)
def _views_prepare_ports(snp, ports):
    try:
        _f, _S, _z0, snp_ports = read_snp(snp)
    except Exception as e:
        raise LibrfError("读取 sNp 端口列序失败: %s" % e)
    if not _snp_has_explicit_port_order(snp) and ports:
        pl = [str(p) for p in ports]
        if len(pl) != len(snp_ports):
            _eprint("WARN views: 传入 ports %s 与 sNp 列序 %s 长度不一致，以 sNp 为准" % (pl, snp_ports))
        else:
            snp_ports = sorted(pl)
    return " ".join(str(p) for p in snp_ports), snp_ports
def _views_finish(res_path, cell, lib):
    res = parse_skill_result(res_path)
    if res["errs"]:
        for e in res["errs"]:
            raise_if_locked(e, cell=cell, view="schematic/symbol")
    if res["status"] != "ok":
        detail = "; ".join(res["errs"]) or ( "result 无 ERR 行，请打开 %s 查看" % res_path)
        raise LibrfError("views 生成失败: %s（详见 %s）" % (detail, res_path))
    for wmsg in res["warns"]:
        _eprint("WARN %s" % wmsg)
    return views_dict_from_result(res, lib, cell)
def run_views(cell, snp, ports, kind, lib, libdir, work, tools,
              verbose=False, timeout=DEF_SKILL_TIMEOUT, views_mode="emx"):
    mode = (views_mode or "emx").strip().lower()
    if mode in ("none", "off", "skip", ""):
        return {}
    if not snp or not os.path.isfile(snp):
        raise LibrfError("views 生成需要 sNp 文件: %s" % snp)
    port_str, _p = _views_prepare_ports(snp, ports)
    if mode == "skill":
        res_path = os.path.join(work, "res_views_%s.txt" % cell)
        driver_path = os.path.join(work, "driver_views_%s.il" % cell)
        gen_fn = gen_driver_views_il
    elif mode == "emx":
        res_path = os.path.join(work, "res_views_emx_%s.txt" % cell)
        driver_path = os.path.join(work, "driver_views_emx_%s.il" % cell)
        gen_fn = gen_driver_views_emx_il
    else:
        raise LibrfError("未知 views_mode %r（可选 emx|skill|none）" % views_mode)
    driver = gen_fn({"lib": lib, "cell": cell, "snp": snp, "ports": port_str, "kind": kind or "ind",
                     "result": res_path, "libdir": libdir})
    safe_write_text(driver_path, driver, what="views driver.il")
    logv("views driver -> %s" % driver_path, verbose)
    try:
        run_dbaccess(driver_path, work, tools, verbose=verbose, timeout=timeout)
        return _views_finish(res_path, cell, lib)
    except LibrfError as e:
        if mode == "emx":
            if u"被 Virtuoso 占用" in str(e):
                raise
            _eprint("WARN EMX views 失败（%s），回退 skill 路径" % e)
            return run_views(cell, snp, ports, kind, lib, libdir, work, tools,
                             verbose=verbose, timeout=timeout, views_mode="skill")
        raise
def parse_freq_spec(spec):
    if spec is None:
        return []
    s = str(spec).strip()
    if not s:
        return []
    if ":" in s:
        parts = s.split(":")
        if len(parts) != 3:
            raise LibrfError("频点语法错误 %r（示例 1e9:30e9:30）" % spec)
        start = parse_eng(parts[0])
        stop = parse_eng(parts[1])
        ntok = parts[2].strip().lower()
        logn = ntok.startswith("log")
        n = int(float(ntok[3:] if logn else ntok))
        if n < 1:
            raise LibrfError("频点数必须 >=1: %r" % spec)
        if n == 1:
            return [start]
        if logn:
            if start <= 0 or stop <= 0:
                raise LibrfError("对数扫频要求正频率: %r" % spec)
            lo, hi = math.log10(start), math.log10(stop)
            return [10.0 ** (lo + (hi - lo) * i / (n - 1)) for i in range(n)]
        return [start + (stop - start) * i / (n - 1) for i in range(n)]
    out = []
    for tok in s.replace(";", ",").split(","):
        tok = tok.strip()
        if tok:
            out.append(parse_eng(tok))
    return out
def build_emx_cmd(emx_bin, gds, struct, proc, freqs, out_snp,
                  edge=None, three_d=None, mode=None, ports=None,
                  extra=None, sweep=False, s_impedance=None):
    extra_list = [str(x) for x in (extra or [])]
    s_imp_flags = [a for a in extra_list if a.startswith("--s-impedance")]
    extra_rest = [a for a in extra_list if not a.startswith("--s-impedance")]
    if s_impedance is not None:
        s_imp_flags.append("--s-impedance=%g" % float(s_impedance))
    cmd = [emx_bin]
    cmd += s_imp_flags
    cmd += ["--format=touchstone", "--s-file=%s" % os.path.abspath(out_snp)]
    if three_d is None:
        three_d = "*"
    if edge is None:
        edge = 0.5
    if three_d:
        cmd.append("--3d=%s" % three_d if str(three_d) not in ("1", "true", "") else "--3d")
    if edge is not None:
        cmd += ["-e", "%g" % float(edge)]
    if mode:
        cmd.append("--mode=%s" % mode)
    for p in (ports or []):
        cmd.append("-p")
        cmd.append("%s=%s" % (p, p))
    if sweep and not any(a == "--sweep" or a.startswith("--multi-sweep") for a in extra_rest):
        cmd.append("--sweep")
    cmd += extra_rest
    cmd.append(os.path.abspath(gds))
    cmd.append(str(struct))
    cmd.append(os.path.abspath(proc))
    for f in (freqs or []):
        cmd.append("%.10g" % f)
    return cmd
def validate_snp(path, n_freq_expected=None, n_ports_expected=None):
    if not os.path.isfile(path):
        raise LibrfError("EMX 未产出 sNp 文件: %s" % path)
    if os.path.getsize(path) == 0:
        raise LibrfError("sNp 文件为空: %s" % path)
    ensure_snp_header(path)
    freqs, S, z0, ports = read_snp(path)
    if not freqs:
        raise LibrfError("sNp 无频点数据: %s" % path)
    nports = len(S[0])
    if n_ports_expected is not None and nports != n_ports_expected:
        po = " ".join(str(x) for x in (ports or [])) or "(文件未标注)"
        raise LibrfError("sNp 端口数 %d != 期望 %d，结果不可信。Port order: %s"
                         % (nports, n_ports_expected, po))
    warn = None
    if n_freq_expected is not None and len(freqs) != n_freq_expected:
        warn = "频点数 %d != 期望 %d" % (len(freqs), n_freq_expected)
    return {"ok": True, "warn": warn, "nports": nports, "nfreq": len(freqs),
            "ports": ports, "z0": z0}
def run_emx(gds, struct, proc, freqs, out_snp, tools,
            edge=None, three_d=None, mode=None, ports=None,
            timeout=DEF_EMX_TIMEOUT, verbose=False, extra=None,
            sweep=False, s_impedance=None, n_freq_check=None):
    emx = require_tool(tools, "emx", "EMX 电磁仿真")
    if not freqs:
        raise LibrfError("run_emx: 频点列表为空（freq 未指定）。EMX 无扫频参数会挂起，"
            "请传 freq 如 '1e9:20e9:11' 或 DEF_FREQ。")
    if not proc:
        raise LibrfError("EMX 工艺文件（proc）未指定。请用环境变量 LIBRF_PROC 指向 proc "
            "文件，或把 proc 放在工作/工艺目录中由工具自动探查。")
    if not os.path.isfile(proc):
        raise LibrfError("EMX 工艺文件不存在: %s" % proc)
    out_snp = os.path.abspath(out_snp)
    out_dir = os.path.dirname(out_snp)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    _assert_writable(out_snp, "sNp 文件")
    cmd = build_emx_cmd(emx, gds, struct, proc, freqs, out_snp,
                        edge=edge, three_d=three_d, mode=mode, ports=ports,
                        extra=extra, sweep=sweep, s_impedance=s_impedance)
    r = run_cmd(cmd, cwd=os.path.dirname(out_snp) or None, timeout=timeout, verbose=verbose)
    if os.path.isfile(out_snp):
        note_created(out_snp)
    logf = out_snp + ".emx.log"
    try:
        _assert_writable(logf, "EMX 日志")
        with open(logf, "w") as f:
            f.write("$ %s\n" % r["cmd"])
            f.write(r["stdout"])
            f.write("\n----- stderr -----\n")
            f.write(r["stderr"])
        note_created(logf)
    except (IOError, LibrfError):
        pass
    if r["timeout"]:
        raise LibrfError("EMX 超时（%ss），日志 %s。大器件请增大 timeout 后重试" % (timeout, logf))
    warns = []
    if n_freq_check is None and not sweep:
        n_freq_check = len(freqs) if freqs else None
    try:
        v = validate_snp(out_snp, n_freq_expected=n_freq_check,
                         n_ports_expected=(len(ports) if ports else None))
    except LibrfError as e:
        raise LibrfError("EMX 结果不合格: %s；日志 %s\n%s" % (e, logf, (r["stderr"] or r["stdout"])[-800:]))
    if v.get("warn"):
        warns.append(v["warn"])
    if ports and not _snp_has_explicit_port_order(out_snp):
        try:
            _snp_append_port_order(out_snp, sorted(str(p) for p in ports))
        except IOError as e:
            warns.append("补写 Port order 失败: %s" % e)
    out_txt = (r["stdout"] or "") + "\n" + (r["stderr"] or "")
    if "Results written" not in out_txt:
        raise LibrfError("EMX 未确认写出结果（输出无 'Results written'），"
                         "日志 %s\n%s" % (logf, (r["stderr"] or r["stdout"])[-800:]))
    if not r["ok"]:
        warns.append("EMX rc=%s 但产物合格，按成功收下（详见 %s）" % (r["rc"], logf))
    for w in warns:
        _eprint("WARN %s" % w)
    return {"ok": True, "cmd": r["cmd"], "out_snp": out_snp, "validate": v,
            "stdout": r["stdout"], "stderr": r["stderr"], "log": logf,
            "warn": "; ".join(warns) if warns else None}
def _mat_eye(n):
    a = [[0j] * n for _ in range(n)]
    for i in range(n):
        a[i][i] = 1.0 + 0j
    return a
def _mat_mul(a, b):
    n, k, m = len(a), len(b), len(b[0])
    out = [[0j] * m for _ in range(n)]
    for i in range(n):
        ai, oi = a[i], out[i]
        for p in range(k):
            x = ai[p]
            if x == 0:
                continue
            bp = b[p]
            for j in range(m):
                oi[j] += x * bp[j]
    return out
def _mat_inv(a):
    n = len(a)
    aug = [list(a[i]) + [1.0 + 0j if i == j else 0j for j in range(n)] for i in range(n)]
    for col in range(n):
        piv, best = col, abs(aug[col][col])
        for r in range(col + 1, n):
            v = abs(aug[r][col])
            if v > best:
                best, piv = v, r
        if best < 1e-300:
            raise LibrfError("矩阵奇异（sNp 数据异常，检查 EMX 输出与端口/网格；"
                "col=%d/%d）" % (col, n))
        if piv != col:
            aug[col], aug[piv] = aug[piv], aug[col]
        inv_d = 1.0 / aug[col][col]
        for j in range(2 * n):
            aug[col][j] *= inv_d
        for r in range(n):
            if r == col:
                continue
            f = aug[r][col]
            if f == 0:
                continue
            for j in range(2 * n):
                aug[r][j] -= f * aug[col][j]
    return [row[n:] for row in aug]
def s_to_y(s, z0=50.0):
    n = len(s)
    eye = _mat_eye(n)
    num = [[eye[i][j] - s[i][j] for j in range(n)] for i in range(n)]
    den = [[eye[i][j] + s[i][j] for j in range(n)] for i in range(n)]
    tmp = _mat_mul(num, _mat_inv(den))
    y0 = 1.0 / complex(z0)
    return [[x * y0 for x in row] for row in tmp]
def s_to_z(s, z0=50.0):
    n = len(s)
    eye = _mat_eye(n)
    num = [[eye[i][j] + s[i][j] for j in range(n)] for i in range(n)]
    den = [[eye[i][j] - s[i][j] for j in range(n)] for i in range(n)]
    tmp = _mat_mul(num, _mat_inv(den))
    return [[x * z0 for x in row] for row in tmp]
_TS_UNITS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
def _parse_ts_option(line):
    unit, param, fmt, r0 = "hz", "s", "ri", 50.0
    toks = line.lstrip("#").split()
    i = 0
    while i < len(toks):
        t = toks[i].lower()
        if t in _TS_UNITS:
            unit = t
        elif t in ("s", "y", "z"):
            param = t
        elif t in ("ri", "ma", "db"):
            fmt = t
        elif t == "r" and i + 1 < len(toks):
            try:
                r0 = float(toks[i + 1])
            except ValueError:
                pass
            i += 1
        i += 1
    return _TS_UNITS[unit], param, fmt, r0
def _ts_to_complex(re_v, im_v, fmt):
    if fmt == "ri":
        return complex(re_v, im_v)
    ph = math.radians(im_v)
    if fmt == "ma":
        return complex(re_v * math.cos(ph), re_v * math.sin(ph))
    mag = 10.0 ** (re_v / 20.0)
    return complex(mag * math.cos(ph), mag * math.sin(ph))
def read_snp(path):
    freqs, S = [], []
    unit, fmt, r0 = 1.0, "ri", 50.0
    ports = []
    n = None
    with open(path, "r") as f:
        for raw in f:
            line = raw.split("!")[0].strip()
            full = raw.strip()
            if full.startswith("!"):
                low = full.lower()
                if "port order" in low:
                    ports = full.split(":", 1)[1].split()
                elif re.match(r"!\s*\d+\s+\S+", full):
                    toks = full.lstrip("!").split()
                    try:
                        int(toks[0])
                        if len(toks) >= 2 and not _is_num(toks[1]):
                            ports = [t for t in toks[1:]]
                    except ValueError:
                        pass
                continue
            if not line:
                continue
            if line.startswith("#"):
                _u, _p, fmt, r0 = _parse_ts_option(line)
                unit = _u
                continue
            toks = line.split()
            nums = []
            for t in toks:
                try:
                    nums.append(float(t))
                except ValueError:
                    break
            if not nums:
                continue
            fval = nums[0] * unit
            rest = nums[1:]
            if n is None:
                if not rest:
                    continue
                n = int(round(math.sqrt(len(rest) / 2.0)))
            expect = 2 * n * n
            if len(rest) < expect:
                continue
            m = []
            for i in range(n):
                row = []
                for j in range(n):
                    k = 2 * (i * n + j)
                    row.append(_ts_to_complex(rest[k], rest[k + 1], fmt))
                m.append(row)
            freqs.append(fval)
            S.append(m)
    if n is not None and not ports:
        ports = ["p%02d" % (i + 1) for i in range(n)]
    return freqs, S, r0, ports
def write_snp(path, freqs, S, z0=50.0, fmt="RI", ports=None):
    _assert_writable(path, "sNp 文件")
    if os.path.isfile(path):
        backup_file(path)
    n = len(S[0])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        if ports:
            f.write("! Port order: %s\n" % " ".join(str(p) for p in ports))
        f.write("# HZ S %s R %g\n" % (fmt.upper(), float(z0)))
        for k, fr in enumerate(freqs):
            f.write("%.10g" % fr)
            for i in range(n):
                for j in range(n):
                    v = S[k][i][j]
                    if fmt.upper() == "RI":
                        f.write(" %.10g %.10g" % (v.real, v.imag))
                    else:
                        mag = abs(v)
                        ph = math.degrees(math.atan2(v.imag, v.real))
                        f.write(" %.10g %.10g" % (mag, ph))
            f.write("\n")
    os.replace(tmp, path)
    note_created(path)
def ensure_snp_header(path):
    _assert_writable(path, "sNp 文件")
    with open(path, "r") as f:
        txt = f.read()
    for line in txt.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            return
        if s.startswith("!"):
            continue
        break
    backup_file(path)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("# HZ S RI R 50\n")
        f.write(txt)
    os.replace(tmp, path)
def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
def _interp_at(freqs, values, f0):
    if not freqs:
        return None
    if f0 <= freqs[0]:
        return values[0]
    if f0 >= freqs[-1]:
        return values[-1]
    for i in range(1, len(freqs)):
        if freqs[i] >= f0:
            f1, f0v = freqs[i - 1], freqs[i]
            v1, v2 = values[i - 1], values[i]
            if f0v == f1:
                return v2
            t = (f0 - f1) / (f0v - f1)
            return v1 + (v2 - v1) * t
    return values[-1]
def _srf_from_lcurve(lcurve):
    for i in range(1, len(lcurve)):
        y0, y1 = lcurve[i - 1][1], lcurve[i][1]
        if y0 > 0 >= y1 and y0 != y1:
            x0, x1 = lcurve[i - 1][0], lcurve[i][0]
            return x0 + (x1 - x0) * (y0 / (y0 - y1))
    return None
def _s_to_abcd2(s, z0=50.0):
    s11, s12 = s[0][0], s[0][1]
    s21, s22 = s[1][0], s[1][1]
    den = 2.0 * s21
    if den == 0:
        raise LibrfError("extract: s21=0，无法转 ABCD")
    a = ((1 + s11) * (1 - s22) + s12 * s21) / den
    b = z0 * ((1 + s11) * (1 + s22) - s12 * s21) / den
    c = ((1 - s11) * (1 - s22) - s12 * s21) / (den * z0)
    d = ((1 - s11) * (1 + s22) + s12 * s21) / den
    return a, b, c, d
def _tl_z0_theta_abcd(s2, z0=50.0):
    a, b, c, _d = _s_to_abcd2(s2, z0)
    if c == 0:
        raise LibrfError("extract: C=0，不是传输线")
    import cmath
    z0c = cmath.sqrt(b / c)
    sin_t = b / (1j * z0c) if z0c != 0 else 0j
    th = math.atan2(sin_t.real, a.real)
    if th < 0:
        th += math.pi
    return z0c, th
def _l0_extrapolate(lc_v, f_max):
    pts = [(f, v) for f, v in lc_v if f_max is None or f <= f_max]
    if not pts:
        pts = lc_v[:3]
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0][1]
    n = float(len(pts))
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    den = n * sxx - sx * sx
    if den == 0:
        return sy / n
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return b
def _cpl_port_indices(ports, n):
    names = list(ports) if ports else ["p%02d" % (i + 1) for i in range(n)]
    if len(names) > n:
        names = names[:n]
    if n < 4 or len(names) < 4:
        raise LibrfError( "extract_fingerprint: cpl 需要 4 个端口名，收到 n=%d ports=%s" % (n, names))
    low = [str(x).lower() for x in names]
    role = {}
    for key in ("a1", "a2", "b1", "b2"):
        if key in low:
            role[key] = low.index(key)
    if len(role) == 4:
        return role["a1"], role["a2"], role["b1"], role["b2"]
    if n >= 4 and all(k in low for k in ("p01", "p02", "p03", "p04")):
        return (low.index("p01"), low.index("p02"),
                low.index("p04"), low.index("p03"))
    order = sorted(range(n), key=lambda i: str(names[i]))
    a1, a2, b1, b2 = order[0], order[1], order[2], order[3]
    return a1, a2, b1, b2
def extract_fingerprint(kind, ports, freqs, S, freq_of_interest=None):
    if not freqs or not S:
        raise LibrfError("extract_fingerprint: 空数据（kind=%s n_freq=%d）" % (kind, len(freqs or [])))
    n = len(S[0])
    if len(freqs) != len(S):
        raise LibrfError( "extract_fingerprint: freqs/S 长度不一致 (%d vs %d, kind=%s)"
            % (len(freqs), len(S), kind))
    canon = {"inductor": "ind", "ind2": "ind", "ind_ct": "ct", "center_tap": "ct",
             "transformer": "xfmr", "coupler": "cpl", "coupled": "cpl",
             "line": "tl", "transmission_line": "tl"}
    raw_kind = (kind or "ind").lower()
    kind_l = canon.get(raw_kind, raw_kind)
    req = {"ind": (2, 2, "kind=ind 需要 2 端口，收到 %d 个——3 端口（含中心抽头）请用 kind=ct"),
           "ct": (3, 99, "kind=ct 需要 >=3 端口，收到 %d 个"),
           "xfmr": (2, 99, "kind=xfmr 需要 >=2 端口，收到 %d 个"),
           "cpl": (4, 99, "kind=cpl 需要 >=4 端口，收到 %d 个"), "tl": (2, 99, "kind=tl 需要 >=2 端口，收到 %d 个")}
    if kind_l in req:
        lo, hi, msg = req[kind_l]
        if not lo <= n <= hi:
            raise LibrfError("extract_fingerprint: " + msg % n)
    ys = [s_to_y(s, 50.0) for s in S]
    f0 = freq_of_interest
    out = {"kind": raw_kind, "ports": list(ports) if ports
           else ["p%02d" % (i + 1) for i in range(n)]}
    def _port_idx_map(port_names):
        cts, sigs = [], []
        for i, p in enumerate(port_names):
            if str(p).lower().startswith("ct"):
                cts.append(i)
            else:
                sigs.append(i)
        sigs = sorted(sigs, key=lambda i: str(port_names[i]))
        return cts, sigs
    def _le_q_curves(y_list, iidx):
        lc, qc = [], []
        for k, f in enumerate(freqs):
            if f <= 0:
                continue
            y = y_list[k]
            yii = y[iidx][iidx]
            if yii == 0:
                continue
            zin = 1.0 / yii
            le = zin.imag / (2.0 * math.pi * f)
            lc.append([f, le])
            if zin.real > 0:
                qc.append([f, zin.imag / zin.real])
            else:
                qc.append([f, None])
        return lc, qc
    def _pack(lc, qc, extra=None):
        lc_v = [[f, v] for f, v in lc if v is not None]
        qc_v = [[f, v] for f, v in qc if v is not None]
        srf = _srf_from_lcurve(lc_v)
        f_max = 1e9
        if srf:
            f_max = min(f_max, 0.05 * srf)
        elif lc_v:
            f_max = min(f_max, 0.05 * lc_v[-1][0]) if lc_v[-1][0] > 0 else 1e9
        l0_dc = _l0_extrapolate(lc_v, f_max)
        if f0 is not None and lc_v:
            l0 = _interp_at([p[0] for p in lc_v], [p[1] for p in lc_v], f0)
        else:
            l0 = l0_dc if l0_dc is not None else (lc_v[0][1] if lc_v else None)
        qdict = dict(("%.6e" % f, q) for f, q in qc_v)
        res = {"L0": l0, "L0_dc": l0_dc, "Q": qdict, "SRF": srf, "Lcurve": lc_v, "Qcurve": qc_v,
               "Lunit": "H", "extract_window": {"L0_fmax": f_max, "f0": f0}}
        if extra:
            res.update(extra)
        return res
    if kind_l == "ind":
        lc, qc = _le_q_curves(ys, 0)
        out.update(_pack(lc, qc))
        return out
    if kind_l == "ct":
        cts, sigs = _port_idx_map(out["ports"])
        if not sigs:
            raise LibrfError("extract_fingerprint: ct 未找到信号端口")
        lc, qc = _le_q_curves(ys, sigs[0])
        out.update(_pack(lc, qc, {"ct_ports": [out["ports"][i] for i in cts]}))
        return out
    if kind_l == "xfmr":
        cts, sigs = _port_idx_map(out["ports"])
        if len(sigs) < 2:
            raise LibrfError("extract_fingerprint: xfmr 信号端口不足")
        i1, i2 = sigs[0], (sigs[2] if len(sigs) > 2 else sigs[1])
        lps, lss, ks = [], [], []
        for k, f in enumerate(freqs):
            if f <= 0:
                continue
            try:
                z = s_to_z(S[k], 50.0)
            except Exception:
                continue
            w = 2.0 * math.pi * f
            lp = z[i1][i1].imag / w
            ls = z[i2][i2].imag / w
            m = z[i1][i2].imag / w
            lps.append([f, lp])
            lss.append([f, ls])
            if lp > 0 and ls > 0:
                kk = m / math.sqrt(lp * ls)
                if kk > 1.0:
                    kk = 1.0
                elif kk < -1.0:
                    kk = -1.0
                ks.append([f, kk])
        l0 = None
        if lps:
            f_max = min(1e9, 0.05 * freqs[-1]) if freqs[-1] > 0 else 1e9
            l0 = _l0_extrapolate(lps, f_max)
            if l0 is None:
                l0 = lps[0][1]
        ls0 = _l0_extrapolate(lss, 1e9) if lss else None
        k0 = None
        if ks:
            k0 = ks[0][1] if f0 is None else _interp_at( [p[0] for p in ks], [p[1] for p in ks], f0)
        out.update({ "Lp": l0, "Ls": ls0, "k": k0, "L0": l0, "Ls0": ls0,
            "Lpcurve": lps, "Lscurve": lss, "kcurve": ks,
            "Lunit": "H", "k_unit": "1", "extract_window": {"f0": f0}, })
        return out
    if kind_l == "tl":
        if n < 2:
            raise LibrfError("extract_fingerprint: tl 需要 2 端口")
        z0c, thc, alpha = [], [], []
        for k, f in enumerate(freqs):
            if f <= 0:
                continue
            s2 = S[k] if n == 2 else [ [S[k][0][0], S[k][0][1]], [S[k][1][0], S[k][1][1]]]
            try:
                zc, th = _tl_z0_theta_abcd(s2, 50.0)
            except LibrfError:
                continue
            z0c.append([f, zc.real])
            thc.append([f, math.degrees(th)])
            s21 = S[k][1][0]
            mag = abs(s21)
            if mag > 0:
                alpha.append([f, -20.0 * math.log10(mag)])
        z0 = None
        th_deg = None
        if z0c:
            z0 = z0c[0][1] if f0 is None else _interp_at(
                [p[0] for p in z0c], [p[1] for p in z0c], f0)
        if thc:
            th_deg = thc[0][1] if f0 is None else _interp_at(
                [p[0] for p in thc], [p[1] for p in thc], f0)
        il = None
        if alpha:
            il = alpha[0][1] if f0 is None else _interp_at(
                [p[0] for p in alpha], [p[1] for p in alpha], f0)
        out.update({ "Z0": z0, "theta": th_deg, "IL": il,
            "Z0unit": "ohm", "theta_unit": "deg", "ILunit": "dB",
            "Z0curve": z0c, "thetacurve": thc, "ILcurve": alpha, "extract_window": {"f0": f0}, })
        return out
    if kind_l == "cpl":
        if n < 4:
            raise LibrfError( "extract_fingerprint: cpl 需要 4 端口，收到 %d（ports=%s）"
                % (n, out["ports"]))
        try:
            a1, a2, b1, b2 = _cpl_port_indices(out["ports"], n)
        except LibrfError:
            raise
        except Exception as e:
            raise LibrfError( "extract_fingerprint: cpl 端口映射失败 ports=%s: %s" % (out["ports"], e))
        zes, zos, thes, thos = [], [], [], []
        for k, f in enumerate(freqs):
            if f <= 0:
                continue
            s4 = S[k]
            s11 = s4[a1][a1]
            s13 = s4[a1][b1]
            s21 = s4[a2][a1]
            s41 = s4[b2][a1]
            se = [[s11 + s13, s21 + s41], [s21 + s41, s11 + s13]]
            so = [[s11 - s13, s21 - s41], [s21 - s41, s11 - s13]]
            try:
                ze, the = _tl_z0_theta_abcd(se, 50.0)
                zo, tho = _tl_z0_theta_abcd(so, 50.0)
            except LibrfError:
                continue
            zes.append([f, ze.real])
            zos.append([f, zo.real])
            thes.append([f, math.degrees(the)])
            thos.append([f, math.degrees(tho)])
        def _at(curve):
            if not curve:
                return None
            return curve[0][1] if f0 is None else _interp_at(
                [p[0] for p in curve], [p[1] for p in curve], f0)
        out.update({ "Ze": _at(zes), "Zo": _at(zos), "theta_e": _at(thes), "theta_o": _at(thos),
            "Zeunit": "ohm", "Zo_unit": "ohm", "theta_unit": "deg", "Zecurve": zes, "Zocurve": zos,
            "theta_e_curve": thes, "theta_o_curve": thos, "extract_window": {"f0": f0,
                               "port_map": {"a1": a1, "a2": a2, "b1": b1, "b2": b2}}, })
        return out
    raise LibrfError("extract_fingerprint: 未知 kind %r" % kind)
def fingerprint_from_snp(snp_path, kind, ports=None, f0=None):
    freqs, S, _z0, file_ports = read_snp(snp_path)
    use_ports = ports if ports else file_ports
    return extract_fingerprint(kind, use_ports, freqs, S, freq_of_interest=f0)
def fprint_summary(fpr):
    fpr = fpr or {}
    kind = fpr.get("kind")
    if kind == "tl":
        return "Z0=%sΩ  θ=%s°  IL=%s dB" % (
            fmt_eng(fpr.get("Z0")), fmt_eng(fpr.get("theta")),
            fmt_eng(fpr.get("IL")))
    if kind == "xfmr":
        return "Lp=%sH  Ls=%sH  k=%s" % (
            fmt_eng(fpr.get("Lp")), fmt_eng(fpr.get("Ls")),
            fmt_eng(fpr.get("k")))
    if kind == "cpl":
        return "Ze=%sΩ  Zo=%sΩ  θe=%s°  θo=%s°" % (
            fmt_eng(fpr.get("Ze")), fmt_eng(fpr.get("Zo")),
            fmt_eng(fpr.get("theta_e")), fmt_eng(fpr.get("theta_o")))
    return "L0=%sH  SRF=%s" % (fmt_eng(fpr.get("L0")), fmt_eng(fpr.get("SRF")))
def _round_geom(geom, nd=2):
    out = {}
    for k in sorted(geom.keys()):
        v = geom[k]
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = round(float(v), nd)
        else:
            out[k] = v
    return out
def geom_key(typ, geom, extra=None):
    return "%s|%s|%s" % (typ, json.dumps(_round_geom(geom), sort_keys=True),
                         json.dumps(extra or {}, sort_keys=True))
def default_cell_name(typ, geom):
    import hashlib as _hashlib
    typ_s = re.sub(r"[^A-Za-z0-9_]", "_", str(typ or "ind")) or "ind"
    tag = _hashlib.sha1(geom_key(typ, geom).encode("utf-8")).hexdigest()[:8]
    return "librf_%s_%s" % (typ_s, tag)
def _kind_of(typ, geom, port_names):
    t = (typ or "ind").lower()
    if t in ("ind", "inductor"):
        if int(geom.get("ct", 0) or 0):
            return "ct"
        if port_names and len(port_names) >= 3:
            return "ct"
        return "ind"
    if t in ("ct", "ind_ct"):
        return "ct"
    if t in ("xfmr", "transformer"):
        return "xfmr"
    if t in ("tl", "line"):
        return "tl"
    if t in ("cpl", "coupler"):
        return "cpl"
    return "ind"
def device_meta_path(work, cell):
    return os.path.join(os.path.abspath(os.path.expanduser(work or "work")),
                        "%s.meta.json" % cell)
def write_device_meta(work, cell, payload):
    path = device_meta_path(work, cell)
    try:
        atomic_write_json(path, payload)
    except (IOError, OSError, LibrfError) as exc:
        _eprint("WARN 写入器件元数据失败 %s: %s" % (path, exc))
        return None
    return path
def load_device_meta(work, cell):
    try:
        data = read_json(device_meta_path(work, cell))
    except (IOError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
def fingerprint_kind_for_device(meta, nports):
    kind = (meta or {}).get("kind")
    if kind:
        return kind
    if nports == 2:
        return "ind"
    if nports == 3:
        return "ct"
    return None
def make_layout_emx(typ, geom, cell=None, work=None, ws=None, lib=None,
                    freq=DEF_FREQ, tools=None, emx_opts=None,
                    views_mode="emx", snp_out=None, no_views=False,
                    no_gds=False, no_emx=False, verbose=False,
                    skill_timeout=None, emx_timeout=None, on_step=None,
                    tech=None, metal_override=None):
    if on_step is None:
        on_step = lambda msg: None
    emx_opts = emx_opts or {}
    st = skill_timeout or DEF_SKILL_TIMEOUT
    et = emx_timeout or DEF_EMX_TIMEOUT
    if tech is None:
        tech = load_tech_profile(work=work)
    if typ == "ind" and isinstance(geom, dict):
        geom = lri_norm_geom(geom)
        _ok, _msg = lri_validate(geom, tech=tech)
        if not _ok:
            raise LibrfError("电感几何不可行：%s" % _msg)
    if metal_override:
        tech = _deep_merge(tech, {"layers": dict(metal_override)})
    if tools is None:
        tools = find_tools(proc=emx_opts.get("proc"), verbose=verbose, tech=tech, work=work)
    tech = _merge_tools_into_tech(tech, tools)
    need = ["layermap"]
    if not (no_emx or no_gds):
        need.append("proc")
    tech_warns = require_tech_ready(tech, need=need, work=work)
    loc = resolve_work(work=work, ws=ws, lib=lib, tools=tools)
    work_dir, lib_name, libdir = loc["work"], loc["lib"], loc["libdir"]
    if loc.get("refresh"):
        _eprint("已向工作空间 cds.lib 追加 DEFINE %s——请在 Library Manager " "Refresh 可见" % lib_name)
    if cell is None:
        cell = default_cell_name(typ, geom)
    on_step("生成版图 %s ..." % cell)
    _assert_cell_writable(libdir, cell)
    res_path = os.path.join(work_dir, "res_%s.txt" % cell)
    req = {"type": typ, "lib": lib_name, "cell": cell, "view": "layout",
           "x0": 0.0, "y0": 0.0, "result": res_path, "libdir": libdir}
    req.update(geom)
    driver_path = os.path.join(work_dir, "driver_%s.il" % cell)
    _assert_writable(driver_path, "driver.il")
    safe_write_text(driver_path, gen_driver_il(req, tech=tech), what="driver.il")
    run_dbaccess(driver_path, work_dir, tools, verbose=verbose, timeout=st)
    res = parse_skill_result(res_path)
    if res["errs"]:
        for e in res["errs"]:
            raise_if_locked(e, cell=cell, view="layout")
    if res["status"] != "ok":
        raise LibrfError("SKILL 版图生成失败: %s" % ("; ".join(res["errs"]) or "见 %s" % res_path))
    for wmsg in res["warns"]:
        _eprint("WARN %s" % wmsg)
    _mark_cell_owned(libdir, cell)
    ports = [p["name"] for p in res["ports"]]
    out = {"cell": cell, "lib": lib_name, "libdir": libdir,
           "type": typ, "geom": geom, "bbox": res["bbox"], "ports": ports,
           "ports_detail": res["ports"], "layers": res["layers"], "views": {},
           "warn": None, "tech_warnings": tech_warns}
    out["kind"] = _kind_of(typ, geom, ports)
    write_device_meta(work_dir, cell, {"type": typ, "kind": out["kind"],
                                       "ports": ports, "bbox": res["bbox"],
                                       "geom": geom})
    gds_path = None
    if not no_gds:
        gds_path = os.path.join(work_dir, "%s.gds" % cell)
        _assert_writable(gds_path, "GDS 文件")
        on_step("导出 GDS ...")
        out["gds"] = run_strmout(lib_name, cell, gds_path, work_dir, tools,
                                 verbose=verbose, timeout=st, tech=tech)
    freqs = parse_freq_spec(freq)
    if not no_emx and not no_gds and not freqs:
        freqs = parse_freq_spec(DEF_FREQ)
        _eprint("WARN make_layout_emx: freq 为空，回退 DEF_FREQ=%s" % DEF_FREQ)
    nports = max(2, len(ports))
    if snp_out is None:
        snp_dir = os.path.join(work_dir, "snps")
    else:
        snp_dir = snp_out if os.path.isdir(snp_out) else os.path.dirname(snp_out)
    snp_dir = os.path.abspath(snp_dir)
    if not os.path.isdir(snp_dir):
        os.makedirs(snp_dir)
    if snp_out and not os.path.isdir(snp_out):
        snp_path = os.path.abspath(snp_out)
        _assert_writable(snp_path, "sNp 文件")
    else:
        snp_path = os.path.join(snp_dir, "%s.s%dp" % (cell, nports))
    mode = emx_opts.get("mode")
    if not mode and int(geom.get("ct", 0) or 0):
        mode = "+p01-p02"
    if no_emx or no_gds:
        return out
    on_step("EMX 仿真 ...")
    if not out.get("gds"):
        raise LibrfError("无 GDS 可供 EMX（strmout 被跳过？）")
    emx_res = run_emx(out["gds"], cell, tools["proc"]["path"], freqs,
                      snp_path, tools, edge=emx_opts.get("edge"),
                      three_d=emx_opts.get("3d"), mode=mode, ports=ports,
                      extra=emx_opts.get("extra"), sweep=emx_opts.get("sweep"),
                      s_impedance=emx_opts.get("s_impedance"), timeout=et, verbose=verbose)
    out["snp"] = emx_res["out_snp"]
    out["emx_cmd"] = emx_res["cmd"]
    if emx_res.get("warn"):
        out["warn"] = emx_res["warn"]
    on_step("提取指纹 ...")
    kind = out["kind"]
    out["fingerprint"] = fingerprint_from_snp(out["snp"], kind, ports=ports)
    if not no_views:
        on_step("生成 schematic/symbol ...")
        out["views"] = run_views(cell, out["snp"], ports, kind, lib_name,
                                 libdir, work_dir, tools, verbose=verbose,
                                 timeout=st, views_mode=views_mode)
    return out
_STOP_REQUESTED = threading.Event()
_ACTIVE_RUNNERS = set()
_ACTIVE_LOCK = threading.Lock()
def stop_all():
    _STOP_REQUESTED.set()
    with _ACTIVE_LOCK:
        runners = list(_ACTIVE_RUNNERS)
    for r in runners:
        try:
            r.terminate()
        except Exception:
            pass
    _STOP_REQUESTED.clear()
class GeomRunner(object):
    def __init__(self, cfg, delegate, mock=None):
        self.cfg = cfg
        self.delegate = delegate
        le = cfg.get("layout_em") or {}
        self.le = le
        self.tech = build_tech(
            techlib=cfg.get("techlib"),
            techlib_dir=cfg.get("techlib_dir"),
            layermap=cfg.get("layermap"),
            proc=cfg.get("proc"),
            cds_lib=cfg.get("cds_lib"))
        self.geom_names = list(le.get("geom_vars") or [])
        self.fixed = dict(le.get("fixed_geom") or {})
        self.type = le.get("type") or "ind"
        self.cell = le.get("cell") or ("opt_%s" % self.type)
        self.work = le.get("work")
        self.lib = le.get("lib")
        self.views_mode = le.get("views_mode") or "none"
        self.geom_outputs = bool(le.get("geom_outputs", True))
        if mock is None:
            mock = le.get("mock") or type(delegate).__name__ == "MockRunner"
        self.mock = bool(mock)
        self.snp_path = le.get("snp")
        if not self.snp_path:
            work = os.path.expanduser(self.work or "work")
            self.snp_path = os.path.join(work, "snps", "%s.s2p" % self.cell)
        self.snp_path = os.path.abspath(os.path.expanduser(self.snp_path))
        if os.path.exists(self.snp_path):
            _assert_writable(self.snp_path, "sNp 文件")
        self.emx_opts = dict(le.get("emx") or {})
        self.verbose = bool(le.get("verbose"))
        self.cache = {}
        self.cache_path = os.path.join( os.path.expanduser(self.work or "work"), ".geom_cache.json")
        self._load_cache()
        self._stopped = False
    def _load_cache(self):
        try:
            data = read_json(self.cache_path)
            if isinstance(data, dict):
                self.cache = data
        except Exception:
            self.cache = {}
    def _save_cache(self):
        try:
            atomic_write_json(self.cache_path, self.cache)
        except Exception:
            pass
    def terminate(self):
        self._stopped = True
    def run(self, values, iter_dir):
        import time
        t0 = time.time()
        if _STOP_REQUESTED.is_set() or self._stopped:
            return _fail("已停止")
        geom = {}
        circ = {}
        for name, val in values.items():
            if name in self.geom_names:
                geom[name] = val
            else:
                circ[name] = val
        geom_all = dict(self.fixed)
        geom_all.update(geom)
        fpr = None
        try:
            fpr = self._ensure_geom(geom_all)
        except LibrfError as e:
            return _fail(str(e))
        except Exception as e:
            try:
                import traceback as _tb
                tb = _tb.format_exc()
            except Exception:
                tb = str(e)
            logp = os.path.join(os.path.expanduser(self.work or "work"), "geom_runner_error.log")
            try:
                with open(logp, "a") as f:
                    f.write("---- %s ----\n%s\n" % (time.time(), tb))
            except Exception:
                logp = "(日志写入失败)"
            return _fail("版图/EM 内部错误: %s: %s（traceback 见 %s）" % (type(e).__name__, e, logp))
        if _STOP_REQUESTED.is_set() or self._stopped:
            return _fail("已停止")
        try:
            self._patch_deck()
        except Exception as e:
            return _fail("网表 nport 路径改写失败: %s" % e)
        sub = self.delegate.run(circ, iter_dir)
        curves = dict(sub.curves or {})
        if fpr and self.geom_outputs and sub.status == "ok":
            self._merge_geom_curves(curves, fpr)
        sub.curves = curves
        sub.wall = time.time() - t0
        return sub
    def _patch_deck(self):
        delegate = self.delegate
        load = getattr(delegate, "_load_deck", None)
        if load is None:
            return
        deck = getattr(delegate, "deck", None)
        if deck is None:
            try:
                deck = load()
            except Exception:
                return
        if not deck:
            return
        text, params = deck
        snp = self.snp_path.replace("\\", "/")
        target = re.compile( r'((?:file|dataFile)\s*=\s*")([^"]+\.s\d+p)(")', re.I)
        def _sub(m):
            cur = m.group(2).replace("\\", "/")
            if cur == snp:
                return m.group(0)
            return m.group(1) + snp + m.group(3)
        lines = text.splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            if "nport" in line.lower():
                new_line, n = target.subn(_sub, line)
                if n and new_line != line:
                    lines[i] = new_line
                    changed = True
        if changed:
            delegate.deck = ("".join(lines), params)
    def _ensure_geom(self, geom):
        if self.type == "ind":
            geom = lri_norm_geom(geom)
        key = geom_key(self.type, geom, {"freq": self.emx_opts.get("freq"),
                        "edge": self.emx_opts.get("edge"), "3d": self.emx_opts.get("3d"),
                        "mode": self.emx_opts.get("mode")})
        if self.mock:
            return self._mock_fingerprint(geom)
        hit = self.cache.get(key)
        if hit and hit.get("snp") and os.path.isfile(hit["snp"]):
            self._publish_snp(hit["snp"])
            return hit.get("fingerprint")
        result = make_layout_emx(
            self.type, geom, cell=self.cell, work=self.work, ws=self.le.get("ws"),
            lib=self.lib, freq=self.emx_opts.get("freq") or DEF_FREQ,
            emx_opts=self.emx_opts, views_mode=self.views_mode,
            snp_out=self.snp_path, no_views=True, verbose=self.verbose,
            tech=self.tech,
            on_step=lambda m: logv(m, self.verbose))
        try:
            import hashlib as _hashlib
            key_id = _hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        except Exception:
            key_id = "%08x" % (abs(hash(key)) % (10 ** 8))
        snp_saved = os.path.join(os.path.dirname(self.snp_path), "%s_%s.s%dp" % ( self.cell, key_id,
                                     result.get("kind") in ("ct", "xfmr") and 4 or 2))
        try:
            _assert_writable(snp_saved, "缓存 sNp")
            shutil.copyfile(result["snp"], snp_saved)
            note_created(snp_saved)
        except IOError:
            snp_saved = result["snp"]
        self.cache[key] = {"snp": snp_saved, "fingerprint": result.get("fingerprint"),
                           "ports": result.get("ports"), "kind": result.get("kind"),
                           "bbox": result.get("bbox")}
        self._save_cache()
        self._publish_snp(result["snp"])
        return result.get("fingerprint")
    def _mock_fingerprint(self, geom):
        dout = float(geom.get("dout", 150) or 150)
        n = float(geom.get("n", 2.5) or 2.5)
        l0 = 1.2e-9 * (dout / 150.0) ** 1.6 * (n / 2.5) ** 1.8
        srf = 45e9 / max(0.5, (n / 2.5)) * (150.0 / max(10.0, dout)) ** 0.5
        q0 = 8.0 + 2.0 * (dout / 150.0)
        fpr = {"L0": l0, "SRF": srf, "Q": {"1e9": q0},
               "Lcurve": [[1e9, l0], [5e9, l0 * 0.97], [10e9, l0 * 0.9]],
               "Qcurve": [[1e9, q0], [5e9, q0 * 1.2], [10e9, q0 * 1.1]]}
        try:
            d = os.path.dirname(self.snp_path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            if not os.path.isfile(self.snp_path):
                write_snp(self.snp_path, [1e9, 5e9, 10e9], [[[0.1j, 0.9], [0.9, 0.1j]],
                           [[0.2j, 0.8], [0.8, 0.2j]], [[0.3j, 0.7], [0.7, 0.3j]]])
        except Exception:
            pass
        return fpr
    def _publish_snp(self, src):
        if os.path.abspath(src) == self.snp_path:
            return
        _assert_writable(self.snp_path, "sNp 文件")
        try:
            d = os.path.dirname(self.snp_path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            shutil.copyfile(src, self.snp_path)
            note_created(self.snp_path)
        except (IOError, OSError) as e:
            raise LibrfError("无法刷新 sNp 到 %s: %s" % (self.snp_path, e))
    def _merge_geom_curves(self, curves, fpr):
        if not fpr:
            return
        def _scalar(name, val):
            if val is not None:
                curves[name] = ([0.0], [float(val)])
        _scalar("L0", fpr.get("L0"))
        _scalar("SRF", fpr.get("SRF"))
        q = fpr.get("Q") or {}
        if q:
            f0 = self.emx_opts.get("f0")
            if f0:
                try:
                    _scalar("Q@f0", _interp_at( [float(k) for k in q.keys()],
                        [float(v) for v in q.values()], float(f0)))
                except Exception:
                    pass
            if "Q@f0" not in curves:
                first = sorted(q.keys())
                if first:
                    _scalar("Q@f0", q[first[0]])
        lc = fpr.get("Lcurve") or []
        if lc:
            curves["L(f)"] = ([p[0] for p in lc], [p[1] for p in lc])
        qc = fpr.get("Qcurve") or []
        if qc:
            curves["Q(f)"] = ([p[0] for p in qc], [p[1] for p in qc])
class _FallbackSimResult(object):
    def __init__(self, curves=None, status="fail", message="", raw_path=None,
                 timing=None, wall=0.0):
        self.curves = curves or {}
        self.status = status
        self.message = message
        self.raw_path = raw_path
        self.timing = timing or {}
        self.wall = wall
def _fail(msg):
    try:
        from optimizer import SimResult
    except Exception:
        SimResult = _FallbackSimResult
    return SimResult(status="fail", message=msg)
def default_emx_settings():
    return { "freq": {"mode": "list", "spec": DEF_FREQ, "discrete": []}, "accuracy": {
            "edge_width": 0.5, "3d": "*", "sidewalls": "*",
            "via_sidewalls": "-*", "via_inductance": "-*", "via_separation": 0.5, "thickness": None,
            "max_splits": None, "via_edge_factor": 1, "internal_units": None, }, "ports": {
            "label_snap": 4, "label_depth": 0, "cadence_pins": None,
            "modes": {}, "internal": None, "collapse": None, }, "solver": {
            "temperature": None, "corner": None, "parallel": None,
            "simultaneous_frequencies": None, "physics": "full", },
        "output": {"print_currents": False, "print_mesh": False, "s_impedance": 50}, }
def load_emx_settings(path=None):
    path = path or os.path.join(_HERE, "librf_emx_settings.json")
    raw = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            raw = d
    except (IOError, OSError, ValueError):
        pass
    return _deep_merge(default_emx_settings(), raw)
def save_emx_settings(settings, path=None):
    path = path or os.path.join(_HERE, "librf_emx_settings.json")
    try:
        if os.path.isfile(path):
            backup_file(path)
        atomic_write_json(path, settings, backup=False)
        return True, path
    except (IOError, OSError, LibrfError) as e:
        return False, str(e)
def emx_settings_to_opts(st, freq_override=None):
    st = _deep_merge(default_emx_settings(), st or {})
    acc, por = st["accuracy"], st["ports"]
    sol, out = st["solver"], st["output"]
    extra = []
    for key, flag in (("sidewalls", "--sidewalls"), ("via_sidewalls", "--via-sidewalls"),
                      ("via_inductance", "--via-inductance")):
        v = acc.get(key)
        if v:
            extra.append("%s=%s" % (flag, v))
    for key, flag, conv in (("via_separation", "--via-separation=%g", float),
                            ("thickness", "--thickness=%g", float),
                            ("max_splits", "--max-splits=%d", int)):
        if acc.get(key) is not None:
            extra.append(flag % conv(acc[key]))
    if acc.get("via_edge_factor") not in (None, 1):
        extra.append("--via-edge-factor=%g" % float(acc["via_edge_factor"]))
    if acc.get("internal_units"):
        extra.append("--internal-units=%s" % acc["internal_units"])
    if por.get("label_snap") is not None:
        extra.append("--label-snap-distance=%g" % float(por["label_snap"]))
    if por.get("label_depth"):
        extra.append("--label-depth=%g" % float(por["label_depth"]))
    if por.get("cadence_pins"):
        extra.append("--cadence-pins")
    if por.get("internal"):
        extra.append("--internal=%s" % por["internal"])
    if por.get("collapse"):
        for tok in str(por["collapse"]).split():
            extra.append("--collapse%s" % tok)
    for pair, mode in (por.get("modes") or {}).items():
        if mode == "diff":
            extra.append("--mode=%s" % pair)
    if sol.get("temperature") is not None:
        extra.append("--temperature=%g" % float(sol["temperature"]))
    if sol.get("corner"):
        extra.append("--corner=%s" % sol["corner"])
    if sol.get("parallel"):
        extra.append("--parallel=%d" % int(sol["parallel"]))
    if sol.get("simultaneous_frequencies"):
        extra.append("--simultaneous-frequencies=%d" % int(sol["simultaneous_frequencies"]))
    phys = sol.get("physics") or "full"
    if phys == "ind":
        extra.append("-L")
    elif phys == "cap":
        extra.append("-C")
    elif phys == "res":
        extra.append("-R")
    if out.get("print_currents"):
        extra.append("--print-currents")
    if out.get("print_mesh"):
        extra.append("--print-mesh")
    opts = { "freq": freq_override or st["freq"].get("spec") or DEF_FREQ,
        "edge": acc.get("edge_width"), "3d": acc.get("3d"), "extra": extra, }
    if out.get("s_impedance"):
        opts["s_impedance"] = out["s_impedance"]
    modes = por.get("modes") or {}
    for pair, mode in modes.items():
        if mode == "diff":
            opts["mode"] = pair
            break
    return opts
def emx_cmd_preview(st, gds="/path/cell.gds", struct="cell", proc=None, out_snp="/path/cell.s2p"):
    tools = find_tools()
    opts = emx_settings_to_opts(st)
    freqs = parse_freq_spec(opts.get("freq"))
    proc = proc or tools["proc"]["path"] or "/path/to/process.proc"
    emx_bin = (tools.get("emx") or {}).get("path") or "emx"
    cmd = build_emx_cmd(emx_bin, gds, struct, proc, freqs, out_snp,
                        edge=opts.get("edge"), three_d=opts.get("3d"),
                        mode=opts.get("mode"), ports=None, extra=opts.get("extra"),
                        s_impedance=opts.get("s_impedance"))
    return " ".join(cmd)
def lri_seglen(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])

def lri_dedup(pts):
    out = []
    for p in pts:
        if not out or lri_seglen(out[-1], p) > 1e-6:
            out.append(p)
    return out

def lri_shift(pts, dx, dy):
    return [(x + dx, y + dy) for x, y in pts]

def lri_mirrorx(pts):
    return [(-x, y) for x, y in pts]

def lri_mirrory(pts):
    return [(x, -y) for x, y in pts]

def lri_plen(pts):
    return sum(lri_seglen(a, b) for a, b in zip(pts[:-1], pts[1:]))

def lri_inter(p1, p2, q1, q2):
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
    if abs(det) < 1e-12:
        return None
    rx, ry = q1[0] - p1[0], q1[1] - p1[1]
    t = (rx * (-d2[1]) - ry * (-d2[0])) / det
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])

def lri_ring(a, b, c, seam="bottom"):
    c = max(0.0, min(c, 0.999 * min(a, b)))
    if seam == "top":
        r = [(0, b), (c - a, b), (-a, b - c), (-a, c - b)]
        r += [(c - a, -b), (a - c, -b), (a, c - b), (a, b - c)]
        r.append((a - c, b))
    elif seam == "left":
        r = [(-a, 0), (-a, c - b), (c - a, -b), (a - c, -b)]
        r += [(a, c - b), (a, b - c), (a - c, b), (c - a, b)]
        r.append((-a, b - c))
    elif seam == "right":
        r = [(a, 0), (a, b - c), (a - c, b), (c - a, b)]
        r += [(-a, b - c), (-a, c - b), (c - a, -b), (a - c, -b)]
        r.append((a, c - b))
    else:
        r = [(0, -b), (a - c, -b), (a, c - b), (a, b - c)]
        r += [(a - c, b), (c - a, b), (-a, b - c), (-a, c - b)]
        r.append((c - a, -b))
    return lri_dedup(r)
def lri_rings(W, H, w, s, n, corner, seam="bottom", pitch=None):
    p = pitch if pitch else (w + s)
    a0 = (W - w) / 2.0
    b0 = (H - w) / 2.0
    ratio = corner / min(a0, b0) if min(a0, b0) > 0 else 0.0
    nring = int(math.ceil(n)) + 2
    rs = [lri_ring(a0, b0, ratio * min(a0, b0), seam)]
    for k in range(1, nring):
        a, b = a0 - k * p, b0 - k * p
        if min(a, b) <= w / 2.0:
            break
        rs.append(lri_ring(a, b, ratio * min(a, b), seam))
    return rs, p
def lri_cut_open(pts, frac):
    if frac >= 0.9999:
        return list(pts)
    tot = lri_plen(pts)
    if tot < 1e-9:
        return list(pts)
    want, acc = tot * frac, 0.0
    out = [pts[0]]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        L = lri_seglen(a, b)
        if L < 1e-9:
            continue
        if acc + L >= want - 1e-9:
            t = (want - acc) / L
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t))
            return out
        out.append(b)
        acc += L
    return out

def lri_rot_list(l, i):
    n = len(l)
    k = i % n
    return [l[(k + j) % n] for j in range(n)]

def lri_step_edge(r):
    m = len(r)
    for i in range(m):
        a, b = r[i], r[(i + 1) % m]
        if (abs(b[0] - a[0]) < 1e-9 and abs(b[1] - a[1]) > 1e-9
                and a[0] < 0.0 and b[1] < a[1]):
            return i
    raise ValueError("lri: 环上没有可用的左侧竖直边，无法构造螺旋")

def lri_rot_exact(pts, ang):
    if ang == 90:
        return [(-y, x) for x, y in pts]
    if ang == 180:
        return [(-x, -y) for x, y in pts]
    if ang == 270:
        return [(y, -x) for x, y in pts]
    return list(pts)

def _lri_spiral_bottom(W, H, w, s, n, corner, pitch=None):
    rs, p = lri_rings(W, H, w, s, n, corner, "bottom", pitch)
    r0 = rs[0]
    i = lri_step_edge(r0)
    m = len(r0)
    b_idx = (i + 1) % m
    nfull = int(n + 1e-7)
    frac = n - nfull
    if len(rs) < nfull + 1:
        raise ValueError(
            "lri: 内孔已闭合，只能容纳 %.2f 圈 < 请求 %.2f 圈；"
            "请减小 n 或 s/w" % (max(0, len(rs) - 1), n))

    def on_left(k, y):
        r = rs[k]
        a, b = r[i], r[(i + 1) % m]
        t = (y - a[1]) / (b[1] - a[1])
        if t < -1e-9 or t > 1.0 + 1e-9:
            raise ValueError(
                "lri: 第 %d 圈左侧边长度不足，无法容纳螺旋台阶 "
                "(需要 y=%.3f, 边范围 %.3f..%.3f)；请减小 n 或 s/w"
                % (k, y, min(a[1], b[1]), max(a[1], b[1])))
        return (a[0], y)

    if nfull == 0:
        closed = lri_rot_list(r0, 0) + [r0[0]]
        return lri_dedup(lri_cut_open(closed, frac)), p

    l1 = rs[1][b_idx]
    d0 = on_left(0, l1[1])
    pts = lri_rot_list(r0, 0)[:i + 1] + [d0]

    lk = l1
    pts.append(lk)
    for k in range(1, nfull):
        r = rs[k]
        rot = lri_rot_list(r, b_idx)
        pts.append(r[b_idx])
        pts += rot[1:]
        if k + 1 < len(rs):
            dk = on_left(k, lk[1] + p)
            pts.append(dk)
            if k < nfull - 1 or frac > 1e-9:
                nxt = (rs[k + 1][i][0], dk[1])
                pts.append(nxt)
                lk = nxt
    if frac > 1e-9 and nfull < len(rs):
        r = rs[nfull]
        closed = lri_dedup([lk] + lri_rot_list(r, b_idx) + [lk])
        pts += lri_cut_open(closed, frac)[1:]
    return lri_dedup(pts), p

def lri_spiral(W, H, w, s, n, corner, seam="bottom", pitch=None):
    if seam == "bottom":
        return _lri_spiral_bottom(W, H, w, s, n, corner, pitch)[0]
    if seam == "top":
        return lri_rot_exact(_lri_spiral_bottom(W, H, w, s, n, corner,
                                                pitch)[0], 180)
    if seam == "right":
        return lri_rot_exact(_lri_spiral_bottom(H, W, w, s, n, corner,
                                                pitch)[0], 90)
    if seam == "left":
        return lri_rot_exact(_lri_spiral_bottom(H, W, w, s, n, corner,
                                                pitch)[0], 270)
    return _lri_spiral_bottom(W, H, w, s, n, corner, pitch)[0]

def lri_point_at(pts, frac):
    tot = lri_plen(pts)
    want = tot * frac
    acc = 0.0
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        L = lri_seglen(a, b)
        if acc + L >= want - 1e-9:
            t = (want - acc) / L if L > 0 else 0.0
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += L
    return pts[-1]

def lri_sweep(pts, w):
    h = w / 2.0
    segs = []
    for a, b in zip(pts[:-1], pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L > 1e-9:
            segs.append((a, b, -dy / L, dx / L))
    if not segs:
        return []

    def bound(sign):
        out = [(segs[0][0][0] + sign * h * segs[0][2],
                segs[0][0][1] + sign * h * segs[0][3])]
        for i in range(1, len(segs)):
            q = lri_inter(
                (segs[i - 1][0][0] + sign * h * segs[i - 1][2],
                 segs[i - 1][0][1] + sign * h * segs[i - 1][3]),
                (segs[i - 1][1][0] + sign * h * segs[i - 1][2],
                 segs[i - 1][1][1] + sign * h * segs[i - 1][3]),
                (segs[i][0][0] + sign * h * segs[i][2],
                 segs[i][0][1] + sign * h * segs[i][3]),
                (segs[i][1][0] + sign * h * segs[i][2],
                 segs[i][1][1] + sign * h * segs[i][3]))
            out.append(q if q else (segs[i][0][0] + sign * h * segs[i][2],
                                    segs[i][0][1] + sign * h * segs[i][3]))
        la = segs[-1]
        out.append((la[1][0] + sign * h * la[2],
                    la[1][1] + sign * h * la[3]))
        return out

    return bound(1) + bound(-1)[::-1]

SEAM_MAP = {"N": "top", "S": "bottom", "E": "right", "W": "left",
            "TOP": "top", "BOTTOM": "bottom", "LEFT": "left",
            "RIGHT": "right", "T": "top", "B": "bottom"}

def lri_norm_geom(g):
    g = dict(g)
    raw_kind = str(g.get("kind", "")).strip().lower()
    if raw_kind in ("sym", "symmetric", "wiki", "rfw"):
        is_sym = True
    elif (raw_kind in ("", "auto")
          and "dout" in g and "W" not in g and "H" not in g):
        is_sym = True
    else:
        is_sym = False
    if is_sym:
        # 参数集：rx/ry（缺省由 dout 推出）、n/w/s、chamfer、lead_*。
        dout = float(g.get("dout", g.get("W", 160.0)))
        rx = float(g.get("rx") or dout / 2.0)
        ry = float(g.get("ry") or dout / 2.0)
        w = float(g.get("w", 10.0))
        s = float(g.get("s", 3.2))

        def _f(key, dflt):
            v = g.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return dflt
        chamfer = _f("chamfer", 2.0 - math.sqrt(2.0))
        lead_len = _f("lead_len", 20.0)
        lead_gap = _f("lead_gap", s)
        lead_w = _f("lead_w", w)
        g.update(kind="sym", dout=2.0 * rx, rx=rx, ry=ry,
                 W=2.0 * rx, H=2.0 * ry, w=w, s=s, n=float(g.get("n", 3.0)),
                 chamfer=chamfer, lead_len=lead_len, lead_gap=lead_gap,
                 lead_w=lead_w)
        g["ct"] = int(float(g.get("ct", 0) or 0))
        g["shape"] = "oct" if chamfer > 0.0 else "rect"
        g["corner"] = chamfer
        g["seam"] = "bottom"
        g["tangle"] = 180
        return g
    W = float(g.get("W", g.get("dout", 150.0)))
    H = float(g.get("H", W))
    w = float(g.get("w", 10.0))
    shape = str(g.get("shape", "rect")).strip().lower() or "rect"
    kind = str(g.get("kind", "se")).strip().lower() or "se"
    if kind in ("figure8", "fig8", "f8"):
        kind = "f8"
    if kind not in ("se", "diff", "f8"):
        kind = "se"
    if shape in ("octagon", "oct"):
        shape = "oct"
    elif shape in ("square", "sq", "rect", "rectangle"):
        shape = "rect"
    else:
        shape = "rect"
    g["kind"] = kind
    g["shape"] = shape
    g["W"] = W
    g["H"] = H
    g["w"] = w
    g["s"] = float(g.get("s", 2.6))
    g["n"] = float(g.get("n", 2.5))
    g["ct"] = int(float(g.get("ct", 0) or 0))
    sm = str(g.get("seam", "S")).strip().upper()
    g["seam"] = SEAM_MAP.get(sm, "bottom")
    try:
        g["tangle"] = int(float(g.get("tangle", 180) or 180))
    except (TypeError, ValueError):
        g["tangle"] = 180
    if g["tangle"] not in (0, 90, 180):
        g["tangle"] = 180
    if g.get("corner") in ("", None):
        if shape == "oct":
            g["corner"] = ((2.0 - math.sqrt(2.0))
                           * min((W - w) / 2.0, (H - w) / 2.0))
        else:
            g["corner"] = 0.0
    else:
        g["corner"] = float(g["corner"])
    return g

def _drc_limits(tech=None):
    drc = tech_drc(tech) or {}

    def _num(key, dflt):
        try:
            return float(drc.get(key, dflt))
        except (TypeError, ValueError):
            return float(dflt)
    return _num("min_w", 1.0), _num("max_w", 30.0), _num("min_s", 1.0)

def _drc_veto(w, s, tech=None):
    min_w, max_w, min_s = _drc_limits(tech)
    if w < min_w:
        return "线宽 w=%g 小于工艺最小线宽 %g（drc.min_w）" % (w, min_w)
    if w > max_w:
        return "线宽 w=%g 超过工艺最大线宽 %g（drc.max_w）" % (w, max_w)
    if s < min_s:
        return "间距 s=%g 小于工艺最小间距 %g（drc.min_s）" % (s, min_s)
    if s < 2.0 * min_s:
        return "电感区间距 s=%g 需 >= 2*min_s=%g" % (s, 2.0 * min_s)
    if w >= 30.0 and s < 1.5 * min_s:
        return "大块金属 w=%g(>=30) 间距 s=%g 需 >= 1.5*min_s=%g" % (
            w, s, 1.5 * min_s)
    return None

def _wide_space_veto(s, lead_gap, tech=None):
    """差分两臂与两条引线都是独立多边形：s / lead_gap 须 >= 该层宽金属间距。"""
    try:
        drc = tech_drc(tech) or {}
        ws = drc.get("wide_space") or {}
        lay = (tech or {}).get("layers") or {}
        spot = lay.get("spiral")
        lim = None
        if isinstance(ws, dict) and spot in ws:
            lim = float(ws[spot])
        elif isinstance(ws, (int, float)):
            lim = float(ws)
        if lim is None:
            return None
        if s < lim - 1e-9:
            return "间距 s=%g 小于 %s 宽金属长平行间距 %g" % (s, spot, lim)
        if lead_gap not in (None, ""):
            lg = float(lead_gap)
            if lg < lim - 1e-9:
                return "引线间距 lead_gap=%g 小于 %s 宽金属长平行间距 %g" % (
                    lg, spot, lim)
    except (TypeError, ValueError):
        return None
    return None


def lri_validate(g, tech=None):
    kind0 = str(g.get("kind", "se")).strip().lower()
    if kind0 == "sym":
        dout = float(g.get("dout", g.get("W", 160.0)))
        rx = float(g.get("rx") or dout / 2.0)
        ry = float(g.get("ry") or dout / 2.0)
        w = float(g.get("w", 10.0))
        s = float(g.get("s", 3.2))
        n = float(g.get("n", 3.0))
        msg = _drc_veto(w, s, tech)
        if msg:
            return False, msg
        msg = _wide_space_veto(s, g.get("lead_gap"), tech)
        if msg:
            return False, msg
        try:
            lead_gap = g.get("lead_gap")
            lead_gap = float(lead_gap) if lead_gap not in (None, "") else None
            lead_w = g.get("lead_w")
            lead_w = float(lead_w) if lead_w not in (None, "") else None
            lri_i3_build(
                rx, ry, n, w, s,
                chamfer=float(g.get("chamfer", 2.0 - math.sqrt(2.0))),
                lead_len=float(g.get("lead_len", 20.0) or 20.0),
                lead_gap=lead_gap, lead_w=lead_w,
                ct=int(float(g.get("ct", 0) or 0)))
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""
    W = float(g.get("W", 150.0))
    H = float(g.get("H", W))
    w = float(g.get("w", 10.0))
    s = float(g.get("s", 2.6))
    n = float(g.get("n", 2.5))
    kind = g.get("kind", "se")
    if min(W, H) <= 0.0 or w <= 0.0 or s <= 0.0:
        return False, "W/H/w/s 必须 > 0"
    if w >= min(W, H):
        return False, "线宽 w=%g 不小于外径" % w
    msg = _drc_veto(w, s, tech)
    if msg:
        return False, msg
    p = w + s
    a0, b0 = (W - w) / 2.0, (H - w) / 2.0
    units = 1.0 if kind != "diff" else 2.0
    n_max = (min(a0, b0) - w / 2.0) / (p * units)
    if n > n_max + 1e-9:
        return False, ("匝数 n=%.2f 超出可行域: W=%g H=%g w=%g s=%g "
                       "最多 %.2f 圈" % (n, W, H, w, s, n_max))
    if n < 1.0:
        return False, "匝数 n 至少 1 圈（不足一圈无法形成螺旋）"
    if kind == "f8" and min(W, H) - 8.0 <= 0:
        return False, "f8 的 W 太小"
    try:
        corner = float(g.get("corner") or 0.0)
        if kind == "diff":
            p2 = w + s
            lri_spiral(W, H, w, s, n, corner, "bottom", 2.0 * p2)
            lri_spiral(W - 2.0 * p2, H - 2.0 * p2, w, s, n, corner,
                       "bottom", 2.0 * p2)
        elif kind == "f8":
            gapf = g.get("gap")
            gapf = 8.0 if gapf in (None, "") else float(gapf)
            gapf = max(gapf, w + s)
            d = (W - gapf) / 2.0
            cf8 = corner
            if min(a0, b0) > 0.0:
                cf8 = corner * min((d - w) / 2.0, b0) / min(a0, b0)
            lri_spiral(d, H, w, s, n / 2.0, cf8, "left")
        else:
            lri_spiral(W, H, w, s, n, corner, g.get("seam", "bottom"))
    except ValueError as exc:
        return False, str(exc)
    return True, ""
_DIRS = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}

def lri_dir_key(v):
    x, y = v
    if abs(x) >= abs(y):
        return "E" if x >= 0.0 else "W"
    return "N" if y >= 0.0 else "S"

def lri_angle_deg(k1, k2):
    v1, v2 = _DIRS[k1], _DIRS[k2]
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    if dot > 0.5:
        return 0
    if dot < -0.5:
        return 180
    return 90

def lri_pick_dirs(tangle, nat1, nat2):
    best = None
    for k1 in ("N", "S", "E", "W"):
        for k2 in ("N", "S", "E", "W"):
            if lri_angle_deg(k1, k2) != tangle:
                continue
            cost = (0 if k1 == nat1 else 1, 0 if k2 == nat2 else 1)
            if best is None or cost < best[0]:
                best = (cost, k1, k2)
    return best[1], best[2]

def _lri_lead(pt, k, length):
    dx, dy = _DIRS[k]
    return [(pt[0], pt[1]), (pt[0] + dx * length, pt[1] + dy * length)]

def _lri_exit_pad(pt, k, bbox_w, bbox_h, length):
    dx, dy = _DIRS[k]
    reach = max(bbox_w, bbox_h) + length
    return (pt[0] + dx * reach, pt[1] + dy * reach)

def lri_half_right(R, xj):
    c = (2.0 - math.sqrt(2.0)) * R
    return [(xj, R), (R - c, R), (R, R - c),
            (R, -(R - c)), (R - c, -R), (xj, -R)]

def lri_half_left(R, xj):
    return [(-x, y) for x, y in reversed(lri_half_right(R, xj))]

SYM_VIA_CUT = float(DEFAULT_TECH["drc"]["via"]["cut"])
SYM_VIA_SPACE = float(DEFAULT_TECH["drc"]["via"]["spacing"])
SYM_VIA_ENCL = float(DEFAULT_TECH["drc"]["via"]["enclosure"])
SYM_VIA_EDGE = SYM_VIA_CUT / 2.0 + SYM_VIA_ENCL
SYM_IND_S = 2.5

def lri_sym_via_width(w):
    for vw in (7.5, 4.5, 1.5):
        if vw <= w - 2.0 * SYM_VIA_ENCL + 1e-9:
            return vw
    return 1.5

def lri_sym_stub(w):
    back = (w / 2.0) * (math.sqrt(2.0) - 1.0)
    return max(SYM_VIA_EDGE, 2.6 - back)

def lri_sym_under_poly(pts, w):
    if len(pts) != 5:
        return lri_sweep(pts, w)
    (x0, y0), (x1, _y1), (x2, y2), _p3, (x4, _y4) = pts
    h = w / 2.0
    back = h * (math.sqrt(2.0) - 1.0)
    sg = 1.0 if y2 >= pts[1][1] else -1.0
    ya = y0 - sg * h
    yb = y2 + sg * h
    return [
        (x0, ya),
        (x1 + back, ya),
        (x2 + back, y2 - sg * h),
        (x4, y2 - sg * h),
        (x4, yb),
        (x2 - back, yb),
        (x0, y0 + sg * (h + back - (x1 - x0))),
    ]

def lri_sym_delta_window(w, s, R, p):
    a = math.sqrt(2.0) * (w / 2.0 + SYM_IND_S)
    dmin = a - p + w / 2.0
    dmax = (math.sqrt(2.0) - 1.0) * R + p / 2.0 - w / 2.0 - a
    return dmin, dmax

def lri_sym_min_dout(n, w, s):
    p = w + s
    xj = p / 2.0
    rt2 = math.sqrt(2.0)
    N = 2 * int(math.floor(float(n) / 2.0)) + 1
    if N < 1:
        N = 1
    if N == 1:
        return 2.0 * (xj / (rt2 - 1.0) + w / 2.0)
    a = rt2 * (w / 2.0 + SYM_IND_S)
    dmin = a - p + w / 2.0
    r1 = (dmin + p / 2.0) / (rt2 - 1.0) + w / 2.0
    r2 = (2.0 * a + w - p * (0.5 + rt2)) / (rt2 - 1.0)
    r3 = xj / (rt2 - 1.0)
    Rlast = max(r1, r2, r3, 0.0)
    return 2.0 * (Rlast + (N - 1) * p + w / 2.0)

# ---------------------------------------------------------------- i3 内核
# 二代对称电感：rx/ry 独立、任意整数圈、可调切角、可调差分引线。
# 与 SKILL lriI3Rings/HalfR/Arm/BuildSym 逐行对应；规格见
# DEVELOPMENT_GUIDE §11.2。
SYM_I3_WEDGE_MIN = 2.6
SYM_I3_DELTA_FRAC = 0.5


def lri_i3_rings(rx, ry, w, s, n_turns, chamfer):
    """平行内偏移环族 -> (rings, p, N)，ring=(a,b,c)。"""
    if rx <= 0.0 or ry <= 0.0 or w <= 0.0 or s <= 0.0:
        raise ValueError("rx/ry/w/s 必须 > 0")
    if abs(float(n_turns) - round(float(n_turns))) > 1e-6:
        raise ValueError("n=%.3f 必须为整数（每臂圈数）" % float(n_turns))
    p = w + s
    a0, b0 = rx - w / 2.0, ry - w / 2.0
    if min(a0, b0) <= 0.0:
        raise ValueError("rx/ry 太小（需 > w=%.3f）" % w)
    c0 = max(0.0, min(float(chamfer), 1.0)) * min(a0, b0)
    N = max(1, int(round(float(n_turns))))
    out = []
    for k in range(N):
        a, b = a0 - k * p, b0 - k * p
        if min(a, b) <= w / 2.0 + 1e-9:
            raise ValueError(
                "第 %d 圈放不下 (a=%.2f b=%.2f)；请增大 rx/ry 或减小 n/w/s"
                % (k, a, b))
        c = max(0.0, c0 - k * p * (2.0 - math.sqrt(2.0)))
        if c > min(a, b) + 1e-9:
            raise ValueError("第 %d 圈切角 %.3f 超过半轴（减小 chamfer）"
                             % (k, c))
        out.append((a, b, c))
    return out, p, N


def lri_i3_half_r(a, b, c, xj):
    if xj > a - c + 1e-9:
        raise ValueError(
            "xj=%.3f 超出顶边直段 (a-c=%.3f)；减小 w/s、增大 rx/ry 或减小 "
            "chamfer" % (xj, a - c))
    return [(xj, b), (a - c, b), (a, b - c), (a, c - b), (a - c, -b),
            (xj, -b)]


def lri_i3_half_l(a, b, c, xj):
    return [(-x, y) for x, y in reversed(lri_i3_half_r(a, b, c, xj))]


def lri_i3_window(w, s_req, ring_k, ring_k1, xj, via_edge, stub):
    a, b, c = ring_k
    a1, b1, c1 = ring_k1
    rt2 = math.sqrt(2.0)
    dmin = (rt2 - 1.0) * (s_req + w / 2.0)
    dm1 = (a - c) - xj - (via_edge + stub)
    dm2 = (a1 - c1) - w * (rt2 - 1.0) / 2.0 - xj
    dm3 = (a - c) + s_req / 2.0 - rt2 * (w / 2.0 + s_req)
    return dmin, min(dm1, dm2, dm3)


def lri_i3_via_span(w, cut, sp, encl):
    sm = w - 2.0 * encl
    n = int(math.floor((sm + sp) / (cut + sp) + 1e-9))
    n = max(1, n)
    if n % 2 == 0:
        n -= 1
    n = max(1, n)
    return n * cut + (n - 1) * sp, n


def lri_i3_arm(rings, p, N, xj, w, s, side, via_edge, stub,
               delta_frac=SYM_I3_DELTA_FRAC):
    """单臂 -> (tops, unders, vias, trim_pts)。边为扁平通孔点表。"""
    halfs = []
    for k, (a, b, c) in enumerate(rings):
        seg = (lri_i3_half_r(a, b, c, xj) if k % 2 == 0
               else lri_i3_half_l(a, b, c, xj))
        if side < 0:
            seg = [(-x, y) for x, y in seg]
        halfs.append(seg)
    unders, vias, trim_pts = [], [], []
    for k in range(N - 1):
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if not under_here:
            continue
        dmin, dmax = lri_i3_window(w, s, rings[k], rings[k + 1], xj,
                                   via_edge, stub)
        if dmax < dmin - 1e-9:
            raise ValueError(
                "换圈 k=%d 的 45° 夹缝放不下 w=%.2f 端头（窗口 %.3f..%.3f）"
                % (k, w, dmin, dmax))
        delta = dmin + delta_frac * (dmax - dmin)
        a = halfs[k][-1]
        b = halfs[k + 1][0]
        sa = 1.0 if a[0] >= 0.0 else -1.0
        sb = 1.0 if b[0] >= 0.0 else -1.0
        at = (a[0] + sa * delta, a[1])
        bt = (b[0] + sb * delta, b[1])
        halfs[k][-1] = at
        halfs[k + 1][0] = bt
        av = (at[0] + sa * via_edge, at[1])
        bv = (bt[0] + sb * via_edge, bt[1])
        ae = (av[0] + sa * stub, av[1])
        be = (bv[0] + sb * stub, bv[1])
        cc = (av[0] - sa * p, bv[1])
        unders.append([ae, av, cc, bv, be])
        vias.extend([av, bv])
        trim_pts.extend([at, bt])
    tops = []
    run = list(halfs[0])
    for k in range(N - 1):
        nxt = halfs[k + 1][0]
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if under_here:
            tops.append(run)
            run = list(halfs[k + 1])
        else:
            run.append(nxt)
            run.extend(halfs[k + 1][1:])
    tops.append(run)
    return tops, unders, vias, trim_pts


def lri_i3_build(rx, ry, n, w, s, chamfer=2.0 - math.sqrt(2.0),
                 lead_len=20.0, lead_gap=None, lead_w=None, ct=0):
    """完整二代对称电感 -> dict（metal/under/vias/ports/bbox）。"""
    rings, p, N = lri_i3_rings(rx, ry, w, s, n, chamfer)
    a0, b0, _c0 = rings[0]
    xj = p / 2.0
    lead_gap = s if lead_gap is None else float(lead_gap)
    lead_w = w if lead_w is None else float(lead_w)
    if lead_gap < s - 1e-9:
        raise ValueError("lead_gap=%.3f < s=%.3f" % (lead_gap, s))
    x_port = (lead_gap + lead_w) / 2.0
    y_port = b0 + lead_len
    if lead_len < w / 2.0 - 1e-9:
        raise ValueError("lead_len < w/2，端口会落在外圈金属内")
    if x_port < xj - 1e-9:
        raise ValueError("lead_gap 太小（引线落进换圈夹缝）")
    if x_port > a0 + 1e-9:
        raise ValueError(
            "lead_gap=%.3f 把引线推到外圈之外（x_port=%.3f > a0=%.3f）"
            % (lead_gap, x_port, a0))
    cut, sp, encl = SYM_VIA_CUT, SYM_VIA_SPACE, SYM_VIA_ENCL
    via_edge = cut / 2.0 + encl
    back = w / 2.0 * (math.sqrt(2.0) - 1.0)
    stub = max(via_edge, max(0.0, SYM_I3_WEDGE_MIN - back))
    span, ncut = lri_i3_via_span(w, cut, sp, encl)
    qa = lri_i3_arm(rings, p, N, xj, w, s, +1, via_edge, stub)
    qb = lri_i3_arm(rings, p, N, xj, w, s, -1, via_edge, stub)
    tops_a, under_a, vias_a, _ta = qa
    tops_b, under_b, vias_b, _tb = qb
    bridge = [tops_a[-1][-1], tops_b[-1][-1]]
    bridge_y = bridge[0][1]
    if abs(bridge[1][1] - bridge_y) > 1e-9:
        raise ValueError("桥两端不等高")
    lead_a = [(xj, b0), (x_port, b0), (x_port, y_port)]
    lead_b = [(-xj, b0), (-x_port, b0), (-x_port, y_port)]
    metal = list(tops_a) + list(tops_b) + [bridge, lead_a, lead_b]
    ports = [("p01", (x_port, y_port)), ("p02", (-x_port, y_port))]
    ct_geom = None
    if ct:
        bl = rings[-1][1]
        if bl < 1.5 * w + s:
            raise ValueError("内孔太小放不下中心抽头（b_last=%.2f）" % bl)
        ct_geom = {"stem": [(0.0, bridge_y), (0.0, 0.0)],
                   "pad": (0.0, 0.0, w)}
        metal.append(ct_geom["stem"])
        ports.append(("ct1", (0.0, bridge_y - (1.5 if bridge_y >= 0 else -1.5))))
    under = list(under_a) + list(under_b)
    return {
        "tops": metal, "under": under, "under_poly": [],
        "vias": [(x, y, span) for (x, y) in (vias_a + vias_b)],
        "ports": ports, "bridge": bridge, "bridge_y": bridge_y,
        "ct": ct_geom, "N": N, "rings": rings, "p": p, "xj": xj,
        "rx": rx, "ry": ry, "ncut": ncut, "via_span": span,
        "bx": 2.0 * rx, "by": 2.0 * ry,
        "bbox": (-rx, -ry, rx, y_port),
    }


def lri_sym_arm(dout, n, w, s, side=1):
    if dout <= 0.0 or w <= 0.0 or s <= 0.0:
        raise ValueError("dout/w/s 必须 > 0")
    p = w + s
    xj = p / 2.0
    R0 = dout / 2.0 - w / 2.0
    if R0 <= 0.0:
        raise ValueError("dout=%.3f 太小（需 > w=%.3f）" % (dout, w))
    N = 2 * int(math.floor(float(n) / 2.0)) + 1
    if N < 1:
        N = 1
    Rlast = R0 - (N - 1) * p
    if Rlast <= 0.0:
        raise ValueError(
            "dout=%.3f 放不下 %d 圈；请把 dout 增大到 >= %.1f 或减小 n/w/s"
            % (dout, N, lri_sym_min_dout(n, w, s)))
    if Rlast * (math.sqrt(2.0) - 1.0) < xj - 1e-9:
        raise ValueError(
            "最内半圈 R=%.3f 的直边太短，放不下 xj=%.3f；"
            "请把 dout 增大到 >= %.1f 或减小 w/s"
            % (Rlast, xj, lri_sym_min_dout(n, w, s)))
    halfs = []
    for k in range(N):
        R = R0 - k * p
        seg = (lri_half_right(R, xj) if k % 2 == 0
               else lri_half_left(R, xj))
        if side < 0:
            seg = [(-x, y) for x, y in seg]
        halfs.append(seg)
    unders = []
    vias = []
    trim_pts = []
    via_w = lri_sym_via_width(w)
    stub = lri_sym_stub(w)
    for k in range(N - 1):
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if not under_here:
            continue
        R = R0 - k * p
        dmin, dmax = lri_sym_delta_window(w, s, R, p)
        Rin = R - p
        chord_max = (math.sqrt(2.0) - 1.0) * (Rin - w / 2.0) - xj
        tm_max = ((math.sqrt(2.0) - 1.0) * R - xj
                  - (SYM_VIA_EDGE + stub))
        upper = min(dmax, chord_max, tm_max)
        if upper < dmin - 1e-9:
            raise ValueError(
                "换圈半径 R=%.3f（内圈 %.3f）的 45° 夹缝放不下 w=%.2f 端头"
                "（下界 %.2f，上界 min(%.2f,直边 %.2f,TM端 %.2f)=%.2f）；"
                "请把 dout 增大到 >= %.1f 或减小 w/s"
                % (R, Rin, w, dmin, dmax, chord_max, tm_max, upper,
                   lri_sym_min_dout(n, w, s)))
        delta = max(0.0, 0.5 * (dmin + upper))
        a = halfs[k][-1]
        b = halfs[k + 1][0]
        sa = 1.0 if a[0] >= 0.0 else -1.0
        sb = 1.0 if b[0] >= 0.0 else -1.0
        at = (a[0] + sa * delta, a[1])
        bt = (b[0] + sb * delta, b[1])
        halfs[k][-1] = at
        halfs[k + 1][0] = bt
        av = (at[0] + sa * SYM_VIA_EDGE, at[1])
        bv = (bt[0] + sb * SYM_VIA_EDGE, bt[1])
        ae = (av[0] + sa * stub, av[1])
        be = (bv[0] + sb * stub, bv[1])
        c = (av[0] - sa * p, bv[1])
        unders.append([ae, av, c, bv, be])
        vias.append([av[0], av[1], via_w])
        vias.append([bv[0], bv[1], via_w])
        trim_pts.extend([at, bt])
    tops = []
    run = list(halfs[0])
    for k in range(N - 1):
        nxt = halfs[k + 1][0]
        under_here = ((k % 2 == 0) if side < 0 else (k % 2 == 1))
        if under_here:
            tops.append(run)
            run = list(halfs[k + 1])
        else:
            run.append(nxt)
            run.extend(halfs[k + 1][1:])
    tops.append(run)
    def _is_trim(pt):
        return any(abs(pt[0] - q[0]) <= 1e-6 and abs(pt[1] - q[1]) <= 1e-6
                   for q in trim_pts)
    topcaps = [(not _is_trim(rr[0]), not _is_trim(rr[-1])) for rr in tops]
    return tops, unders, vias, topcaps, N, R0, Rlast, xj

def lri_build_ind(g):
    W = float(g.get("W", g.get("dout", 160.0)))
    H = float(g.get("H", W))
    w = float(g["w"])
    s = float(g["s"])
    n = float(g.get("n", 3.0))
    corner = float(g.get("corner") or 0.0)
    seam = g.get("seam", "bottom")
    kind = g.get("kind", "se")
    ct = int(g.get("ct", 0) or 0)
    _tv = g.get("tangle", 180)
    tangle = int(float(180 if _tv in (None, "") else _tv))
    stub = float(g.get("stub", 40.0) or 40.0)
    out = {"metal": [], "under": [], "ports": [],
           "bx": W, "by": H, "kind": kind}

    if kind == "sym":
        # 二代内核：rx/ry 独立（缺省由 dout 推出）、任意整数圈、可调切角、
        # 可调差分引线；与 SKILL lriI3BuildSym 同几何。
        dout = float(g.get("dout", W))
        rx = float(g.get("rx", dout / 2.0))
        ry = float(g.get("ry", dout / 2.0))
        m = lri_i3_build(
            rx, ry, n, w, s,
            chamfer=float(g.get("chamfer", 2.0 - math.sqrt(2.0))),
            lead_len=float(g.get("lead_len", 20.0)),
            lead_gap=g.get("lead_gap"), lead_w=g.get("lead_w"),
            ct=int(float(g.get("ct", 0) or 0)))
        out["metal"] = list(m["tops"])
        out["topcaps"] = [(False, False)] * len(out["metal"])
        out["under"] = list(m["under"])
        out["under_poly"] = [lri_sym_under_poly(p, w)
                             for p in m["under"]]
        out["vias"] = list(m["vias"])
        out["ports"] = list(m["ports"])
        out["bx"] = 2.0 * rx
        out["by"] = 2.0 * ry
        out["N"] = m["N"]
        out["bbox"] = m["bbox"]
        return out

    if kind == "diff":
        p2 = w + s
        A = lri_spiral(W, H, w, s, n, corner, "bottom", 2.0 * p2)
        B = lri_mirrory(lri_spiral(W - 2.0 * p2, H - 2.0 * p2, w, s, n,
                                   corner, "bottom", 2.0 * p2))
        out["metal"] = [A, B]
        out["under"] = [[A[-1], (B[-1][0], A[-1][1]), B[-1]]]
        lead1 = _lri_lead(A[0], "S", stub)
        bx, _by = B[0]
        pex, pey = bx, H / 2.0 + w + 40.0 + stub
        out["under"].append([B[0], (pex, pey)])
        lead2 = [(pex, pey), (pex, pey + stub)]
        out["metal"] += [lead1, lead2]
        out["ports"] = [("p01", lead1[-1]), ("p02", lead2[-1])]
        if ct:
            tip = (B[-1][0], -(H / 2.0 + w + 40.0 + stub))
            out["under"].append([B[-1], tip])
            out["ports"].append(("ct1", tip))
        return out

    if kind == "f8":
        gapf = g.get("gap")
        gapf = 8.0 if gapf in (None, "") else float(gapf)
        gapf = max(gapf, w + s)
        d = (W - gapf) / 2.0
        nA = n / 2.0
        dx = (gapf + d) / 2.0
        a0m, b0m = (W - w) / 2.0, (H - w) / 2.0
        cf8 = corner
        if min(a0m, b0m) > 0.0:
            cf8 = corner * min((d - w) / 2.0, b0m) / min(a0m, b0m)
        Lf = lri_shift(lri_spiral(d, H, w, s, nA, cf8, "left"), -dx, 0.0)
        Rg = lri_shift(lri_mirrorx(
            lri_spiral(d, H, w, s, nA, cf8, "left")), dx, 0.0)
        out["metal"] = [Lf, Rg]
        out["under"] = [[Lf[-1], (Lf[-1][0], 0.0), (0.0, 0.0),
                         (Rg[-1][0], 0.0), Rg[-1]]]
        nat1 = lri_dir_key((Lf[0][0] + dx, Lf[0][1]))
        nat2 = lri_dir_key((Rg[0][0] - dx, Rg[0][1]))
        k1, k2 = lri_pick_dirs(tangle, nat1, nat2)
        lead1 = _lri_lead(Lf[0], k1, stub)
        lead2 = _lri_lead(Rg[0], k2, stub)
        out["metal"] += [lead1, lead2]
        out["ports"] = [("p01", lead1[-1]), ("p02", lead2[-1])]
        return out

    body = lri_spiral(W, H, w, s, n, corner, seam)
    out["metal"] = [body]
    nat1 = {"top": "N", "left": "W", "right": "E"}.get(seam, "S")
    ex, ey = body[-1][0], body[-1][1]
    Lr = math.hypot(ex, ey)
    nat2 = lri_dir_key((ex, ey) if Lr > 1e-6 else (0.0, 1.0))
    k1, k2 = lri_pick_dirs(tangle, nat1, nat2)
    lead1 = _lri_lead(body[0], k1, stub)
    out["metal"].append(lead1)
    tip = _lri_exit_pad(body[-1], k2, W, H, stub)
    out["under"].append([body[-1], tip])
    out["ports"] = [("p01", lead1[-1]), ("p02", tip)]
    if ct:
        cut = lri_point_at(body, 0.5)
        k3 = "S" if k1 != "S" else "N"
        tip2 = _lri_exit_pad(cut, k3, W, H, stub)
        out["under"].append([cut, tip2])
        out["ports"].append(("ct1", tip2))
    return out

def lri_draw_sketch(c, raw):
    try:
        g = form_to_geom("ind", raw)
        ok, msg = lri_validate(g)
        if not ok:
            raise ValueError(msg)
        geo = lri_build_ind(g)
    except Exception as exc:
        c.create_text(140, 86, text="参数不可行", fill="#a03030",
                      font=("fixed", 10))
        c.create_text(140, 110, text=str(exc)[:42], fill="#a03030",
                      font=("fixed", 8))
        return
    W, H = geo["bx"], geo["by"]
    w = float(g["w"])
    wu = w
    sc = min(196.0 / max(W, 1e-9), 128.0 / max(H, 1e-9))
    cx, cy = 140.0, 94.0

    def tx(p):
        return (cx + p[0] * sc, cy - p[1] * sc)

    def poly(pts, width, fill, edge):
        pp = lri_sweep(pts, width)
        if len(pp) < 3:
            return
        flat = []
        for q in pp:
            x, y = tx(q)
            flat.extend((x, y))
        c.create_polygon(flat, fill=fill, outline=edge, width=1)

    for pts in geo["metal"]:
        poly(pts, w, "#3a6ea5", "#1f4064")
    if geo.get("under_poly"):
        for pp in geo["under_poly"]:
            if len(pp) >= 3:
                flat = []
                for q in pp:
                    x, y = tx(q)
                    flat.extend((x, y))
                c.create_polygon(flat, fill="#c0504d", outline="#7a2e2c",
                                 width=1)
    else:
        for seg in geo["under"]:
            poly(seg, wu, "#c0504d", "#7a2e2c")
    for vx, vy, vw in geo.get("vias", []):
        pitch = SYM_VIA_CUT + SYM_VIA_SPACE
        n = max(1, int(round((vw + SYM_VIA_SPACE) / pitch)))
        for i in range(n):
            yy = vy + (i - (n - 1) / 2.0) * pitch
            px, py = tx((vx, yy))
            hw = max(0.8, SYM_VIA_CUT / 2.0 * sc)
            c.create_rectangle(px - hw, py - hw, px + hw, py + hw,
                               fill="#0a7d33", outline="#064d1f")
    x1, y1 = tx((-W / 2.0, H / 2.0))
    x2, y2 = tx((W / 2.0, -H / 2.0))
    c.create_rectangle(x1, y1, x2, y2, outline="#9a9a9a", dash=(3, 3))
    for name, pt in geo["ports"]:
        x, y = tx(pt)
        col = "#b02020" if name.startswith("ct") else "#0a7d33"
        c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=col, outline="")
        c.create_text(x, y - 9, text=name, fill=col, font=("fixed", 7))
    yb = max(y1, y2) + 13
    c.create_line(x1, yb, x2, yb, fill="#a03030", arrow="both")
    c.create_text((x1 + x2) / 2.0, yb + 8, text="W=%g" % W, fill="#a03030",
                  font=("fixed", 8))
    xl = min(x1, x2) - 14
    c.create_line(xl, y1, xl, y2, fill="#a03030", arrow="both")
    c.create_text(xl - 2, (y1 + y2) / 2.0, text="H=%g" % H, fill="#a03030",
                  font=("fixed", 8), angle=90)
    if g.get("kind") == "sym":
        c.create_text(140, 182, fill="#444444", font=("fixed", 8),
                      text="dout=%g  w=%g  s=%g  实际圈数 N=%d"
                           % (float(g.get("dout", W)), w, float(g["s"]),
                              int(geo.get("N", 0))))
        c.create_text(140, 197, fill="#666666", font=("fixed", 8),
                      text="kind=sym  端口同侧顶部  内端下方对称轴")
    else:
        c.create_text(140, 182, fill="#444444", font=("fixed", 8),
                      text="n=%g  w=%g  s=%g  corner=%g"
                           % (float(g["n"]), w, float(g["s"]),
                              float(g["corner"])))
        c.create_text(140, 197, fill="#666666", font=("fixed", 8),
                      text="kind=%s  shape=%s  ct=%d  seam=%s"
                           % (g["kind"], g["shape"], int(g["ct"]), g["seam"]))

def lri_sketch_after(tab):
    try:
        if getattr(tab, "_lri_after", None):
            tab.sketch.after_cancel(tab._lri_after)
    except Exception:
        pass
    try:
        tab._lri_after = tab.sketch.after(
            140, lambda: lri_redraw_sketch(tab))
    except Exception:
        tab._lri_after = None

def lri_redraw_sketch(tab):
    try:
        tab._draw_geom_sketch("ind")
    except Exception:
        pass

PRESETS = [ {"name": "1nH 八角电感", "type": "ind", "freq": "1e9:30e9:30",
     "geom": {"kind": "se", "shape": "octagon", "n": 2.5, "w": 10, "s": 2.6,
              "dout": 150, "ct": 0, "underpass": "direct"}},
    {"name": "2nH 方形电感", "type": "ind", "freq": "1e9:30e9:30",
     "geom": {"kind": "se", "shape": "square", "n": 3.5, "w": 12, "s": 2.6,
              "dout": 220, "ct": 0, "underpass": "direct"}},
    {"name": "中心抽头电感", "type": "ind", "freq": "1e9:30e9:21",
     "geom": {"kind": "se", "shape": "octagon", "n": 2, "w": 10, "s": 2.6,
              "dout": 160, "ct": 1, "underpass": "direct"}},
    {"name": "差分电感 (diff)", "type": "ind", "freq": "1e9:30e9:21",
     "geom": {"kind": "diff", "shape": "octagon", "n": 2.5, "w": 10,
              "s": 2.6, "dout": 220, "ct": 0}},
    {"name": "8 字电感 (f8)", "type": "ind", "freq": "1e9:30e9:21",
     "geom": {"kind": "f8", "shape": "octagon", "n": 3, "w": 10, "s": 2.6,
              "dout": 200, "ct": 0}},
    {"name": "50Ω 微带线", "type": "tl", "freq": "1e9:30e9:21",
     "geom": {"w": 20, "len": 300, "gnd": 1, "gnd_layer": "M1", "fence": 0, "cpw": 0, "gap": 5}},
    {"name": "CPW 线", "type": "tl", "freq": "1e9:30e9:21",
     "geom": {"w": 15, "len": 250, "gnd": 1, "gnd_layer": "M1", "fence": 0, "cpw": 1, "gap": 6}},
    {"name": "1:1 balun", "type": "xfmr", "freq": "1e9:30e9:21",
     "geom": {"style": "balun", "np": 2, "ns": 2, "w": 10, "s": 2.6,
              "dout": 180, "gap": 3, "ct": 0}},
    {"name": "叠层变压器", "type": "xfmr", "freq": "1e9:30e9:21",
     "geom": {"style": "stacked", "np": 2, "ns": 2, "w": 10, "s": 2.6,
              "dout": 160, "gap": 3, "ct": 0}},
    {"name": "平行耦合线", "type": "cpl", "freq": "1e9:30e9:21",
     "geom": {"w": 10, "gap": 3, "len": 200, "gnd": 1}}, ]
GEOM_FORM = { "ind": [("kind", "拓扑 kind (se/diff/f8/sym)", "se", False),
            ("shape", "形状 rect/oct", "octagon", False),
            ("seam", "端口边 seam (N/S/E/W)", "S", False),
            ("tangle", "端口夹角 tangle (0/90/180)", "180", True),
            ("n", "匝数 n", "3", True), ("w", "线宽 w (µm)", "10", True),
            ("s", "间距 s (µm)", "3.2", True), ("dout", "外径 dout (µm)", "160", True),
            ("rx", "横向半径 rx (µm, 留空=dout/2) [仅 sym]", "", True),
            ("ry", "纵向半径 ry (µm, 留空=dout/2) [仅 sym]", "", True),
            ("chamfer", "拐角切角 chamfer 0..1 (留空=2-sqrt2) [仅 sym]", "", True),
            ("lead_len", "差分引线长 lead_len (µm) [仅 sym]", "20", True),
            ("lead_gap", "差分线间距 lead_gap (µm, 留空=s) [仅 sym]", "", True),
            ("lead_w", "差分线宽 lead_w (µm, 留空=w) [仅 sym]", "", True),
            ("W", "宽 W (µm, 留空=dout)", "", True),
            ("H", "高 H (µm, 留空=dout)", "", True),
            ("corner", "45° 切角 corner (µm, 留空=八角自动)", "", True),
            ("din", "内径 din (µm, 留空=闭合) [仅旧内核]", "", True),
            ("ct", "中心抽头 ct (0/1)", "0", True),
            ("ct_dir", "抽头方向 ct_dir (N/S/E/W) [仅旧内核]", "S", False),
            ("underpass", "underpass direct/route [仅旧内核]", "direct", False),
            ("ex", "横向拉长 ex (µm) [仅旧内核]", "", True),
            ("ey", "纵向拉长 ey (µm) [仅旧内核]", "", True),
            ("stack", "叠层 stack (0/1) [仅 sym]", "0", True),
            ("metal_main", "主金属 (留空=工艺档) [仅 sym]", "", False),
            ("metal_aux", "副金属/跨线层 (留空=工艺档) [仅 sym]", "", False),
            ("ind_mark", "MARKER 标记层 (0/1)", "0", True)],
    "tl": [("w", "线宽 w (µm)", "20", True), ("len", "长度 len (µm)", "300", True),
           ("gnd", "带地 gnd (0/1)", "1", True), ("gnd_layer", "地层", "M1", False),
           ("fence", "围栏 fence (0/1)", "0", True), ("cpw", "CPW 模式 (0/1)", "0", True),
           ("gap", "CPW gap (µm)", "5", True)],
    "xfmr": [("style", "风格 balun/stacked/interleaved", "balun", False),
             ("np", "初级匝数 np", "2", True), ("ns", "次级匝数 ns", "2", True),
             ("w", "线宽 w (µm)", "10", True), ("s", "间距 s (µm)", "2.6", True),
             ("dout", "外径 dout (µm)", "180", True), ("gap", "级间 gap (µm)", "3", True),
             ("ct", "中心抽头 ct (0/1)", "0", True)], "cpl": [("w", "线宽 w (µm)", "10", True),
            ("gap", "耦合 gap (µm)", "3", True), ("len", "长度 len (µm)", "200", True),
            ("gnd", "带地 gnd (0/1)", "1", True)], }
GEOM_NUM = set()
for _rows in GEOM_FORM.values():
    for _k, _lab, _d, _num in _rows:
        if _num:
            GEOM_NUM.add(_k)
GEOM_CHOICES = {
    "kind": ("se", "diff", "f8", "sym"),
    "shape": ("rect", "oct", "octagon", "square"),
    "seam": ("S", "N", "E", "W"),
    "tangle": ("180", "90", "0"),
    "style": ("balun", "stacked", "interleaved"),
}
def form_to_geom(typ, raw):
    geom = {}
    for key, _lab, _d, num in GEOM_FORM.get(typ, []):
        v = (raw.get(key) or "").strip()
        if v == "":
            continue
        if num:
            try:
                geom[key] = parse_eng(v)
            except ValueError as e:
                raise LibrfError("%s: %s" % (key, e))
        else:
            geom[key] = v
    if typ == "ind":
        geom = lri_norm_geom(geom)
    return geom
class _EmOptTab(object):
    def __init__(self, gui, parent):
        self.gui = gui
        self.tk = gui.tk
        self.ttk = gui.ttk
        self.root = parent
        self._busy = False
        self._build(parent)
    def _row(self, parent, pady=2):
        f = self.ttk.Frame(parent)
        f.pack(fill="x", pady=pady)
        return f
    def _entry(self, parent, label, default="", width=14):
        r = self._row(parent)
        self.ttk.Label(r, text=label, width=22, anchor="w").pack(side="left")
        var = self.tk.StringVar(value=str(default))
        self.ttk.Entry(r, textvariable=var, width=width).pack(side="left")
        return var
    def _text(self, parent, height=8):
        from tkinter.scrolledtext import ScrolledText
        return ScrolledText(parent, height=height, width=70, font=("WenQuanYi Micro Hei", 9))
    def _log(self, widget, msg):
        widget.insert("end", msg if msg.endswith("\n") else msg + "\n")
        widget.see("end")
    def _probe_proc(self, silent=False):
        tools = find_tools()
        hit = (tools.get("proc") or {}).get("path")
        if hit and not self.b_proc.get().strip():
            self.b_proc.set(hit)
        if silent:
            return hit
        msg = ("proc 探查：%s  [来源 %s]\n"
               % (hit or "未找到", (tools.get("proc") or {}).get("src") or "-"))
        msg += ("未找到时请用环境变量 LIBRF_PROC 指定，或点「浏览」选文件。\n" "目标机上的新 proc 文件同样按路径指定，不写进源码。")
        self._info(msg)
        return hit
    def _browse_proc(self):
        try:
            from tkinter import filedialog
        except Exception:
            return
        path = filedialog.askopenfilename( parent=self.root, title="选择 EMX 工艺文件 (proc)",
            filetypes=[("proc 工艺文件", "*.proc"), ("所有文件", "*")])
        if path:
            self.b_proc.set(path)
    def _info(self, msg):
        try:
            from tkinter import messagebox
            messagebox.showinfo("proc 工艺文件", msg, parent=self.root)
        except Exception:
            print(msg)
    def _build(self, parent):
        nb = self.ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)
        self.sub_a = self.ttk.Frame(nb)
        self.sub_b = self.ttk.Frame(nb)
        self.sub_c = self.ttk.Frame(nb)
        nb.add(self.sub_a, text=" 版图生成 ")
        nb.add(self.sub_b, text=" EM 仿真 ")
        nb.add(self.sub_c, text=" EM 优化迭代 ")
        self._build_a()
        self._build_b()
        self._build_c()
    def _build_a(self):
        f = self.sub_a
        r = self._row(f)
        self.ttk.Label(r, text="器件类型", width=22, anchor="w").pack(side="left")
        self.a_type = self.tk.StringVar(value="ind")
        self.ttk.Combobox(r, textvariable=self.a_type, width=12,
                          values=("ind", "tl", "xfmr", "cpl"), state="readonly").pack(side="left")
        self.ttk.Button(r, text="重载几何表单", command=self._rebuild_geom).pack(side="left", padx=6)
        r2 = self._row(f)
        self.ttk.Label(r2, text="预设", width=22, anchor="w").pack(side="left")
        self.a_preset = self.tk.StringVar(value=PRESETS[0]["name"])
        self.ttk.Combobox(r2, textvariable=self.a_preset, width=22,
                          values=[p["name"] for p in PRESETS], state="readonly").pack(side="left")
        self.ttk.Button(r2, text="应用预设", command=self._apply_preset).pack(side="left", padx=6)
        gbody = self.ttk.Frame(f)
        gbody.pack(fill="x")
        self.geom_frame = self.ttk.Frame(gbody)
        self.geom_frame.pack(side="left", fill="x", expand=True)
        try:
            self.sketch = self.tk.Canvas(gbody, width=280, height=210, bg="white",
                                         highlightthickness=1,
                                         highlightbackground="#cccccc")
            self.sketch.pack(side="left", padx=(6, 0), anchor="n")
        except Exception:
            self.sketch = None
        self.geom_vars = {}
        self._rebuild_geom()
        self.a_cell = self._entry(f, "cell 名", "")
        self.a_freq = self._entry(f, "EM 频率 (start:stop:n)", DEF_FREQ, 22)
        self.a_work = self._entry(f, "工作目录 work", "work", 26)
        self.a_views = self.tk.StringVar(value="emx")
        r3 = self._row(f)
        self.ttk.Label(r3, text="views 模式", width=22, anchor="w").pack(side="left")
        self.ttk.Combobox(r3, textvariable=self.a_views, width=10, values=("emx", "skill", "none"),
                          state="readonly").pack(side="left")
        r4 = self._row(f, pady=6)
        self.ttk.Button(r4, text="仅出图", command=lambda: self._do_make("layout")).pack(
            side="left", padx=4)
        self.ttk.Button(r4, text="出图 + EM + views", command=lambda: self._do_make("full")).pack(
            side="left", padx=4)
        self.ttk.Button(r4, text="渲染截图", command=self._render).pack(side="left", padx=4)
        self.a_log = self._text(f, height=10)
        self.a_log.pack(fill="both", expand=True, pady=4)
    def _build_b(self):
        f = self.sub_b
        self.b_cell = self._entry(f, "cell 名", "")
        self.b_gds = self._entry(f, "GDS 路径 (留空=work/cell.gds)", "", 30)
        self.b_freq = self._entry(f, "EM 频率", DEF_FREQ, 22)
        self.b_work = self._entry(f, "工作目录 work", "work", 26)
        pr = self._row(f, pady=2)
        self.b_proc = self._entry(pr, "proc 工艺文件路径", "", 34)
        self.ttk.Button(pr, text="探查", command=self._probe_proc).pack(side="left", padx=2)
        self.ttk.Button(pr, text="浏览", command=self._browse_proc).pack(side="left", padx=2)
        self._probe_proc(silent=True)
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
        self.b_log.pack(fill="both", expand=True, pady=4)
        try:
            self.curve = _CurveCanvas(f, height=220)
            self.curve.pack(fill="both", expand=False, pady=2)
        except Exception:
            self.curve = None
    def _build_c(self):
        f = self.sub_c
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
        self.c_snp = self._entry(f, "固定 sNp 路径 (留空=work/snps/cell.s2p)", "", 30)
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
        self.c_fixed = self._entry(f, '固定几何 JSON（如 {"n":2.5,"w":10,"s":2.6}）',
                                   '{"n": 2.5, "w": 10, "s": 2.6, "ct": 0}', 36)
        r = self._row(f, pady=6)
        self.ttk.Button(r, text="生成器件与视图", command=self._gen_device).pack(side="left", padx=4)
        self.ttk.Button(r, text="开始 EM 优化", command=self._start_em_opt).pack(side="left", padx=4)
        self.c_log = self._text(f, height=8)
        self.c_log.pack(fill="both", expand=True, pady=4)
    def _rebuild_geom(self):
        for child in self.geom_frame.winfo_children():
            child.destroy()
        self.geom_vars = {}
        typ = self.a_type.get()
        for key, lab, d, _num in GEOM_FORM.get(typ, []):
            r = self._row(self.geom_frame, pady=1)
            self.ttk.Label(r, text=lab, width=26, anchor="w").pack(side="left")
            var = self.tk.StringVar(value=d)
            if key in GEOM_CHOICES:
                ent = self.ttk.Combobox(r, textvariable=var, width=10,
                                        values=GEOM_CHOICES[key], state="readonly")
                ent.pack(side="left")
                ent.bind("<<ComboboxSelected>>", lambda _e: lri_sketch_after(self))
            else:
                ent = self.ttk.Entry(r, textvariable=var, width=12)
                ent.pack(side="left")
                ent.bind("<KeyRelease>", lambda _e: lri_sketch_after(self))
            self.geom_vars[key] = var
        self._draw_geom_sketch(typ)
    def _draw_geom_sketch(self, typ):
        c = getattr(self, "sketch", None)
        if c is None:
            return
        try:
            c.delete("all")
        except Exception:
            return
        if typ != "ind":
            c.create_text(140, 100, text="%s 预览暂未接入" % typ,
                          fill="#777777", font=("fixed", 9))
            return
        raw = {k: (v.get() if v is not None else "")
               for k, v in (self.geom_vars or {}).items()}
        try:
            lri_draw_sketch(c, raw)
        except Exception as exc:
            c.create_text(140, 100, text="预览失败: %s" % str(exc)[:40],
                          fill="#a03030", font=("fixed", 8))
    def _apply_preset(self):
        name = self.a_preset.get()
        for p in PRESETS:
            if p["name"] == name:
                self.a_type.set(p["type"])
                self._rebuild_geom()
                for k, v in (p.get("geom") or {}).items():
                    if k in self.geom_vars:
                        self.geom_vars[k].set(str(v))
                if p.get("freq") and "a_freq" in dir(self):
                    self.a_freq.set(p["freq"])
                self._log(self.a_log, "已应用预设：%s" % name)
                return
    def _collect_geom(self):
        raw = {k: v.get() for k, v in self.geom_vars.items()}
        return form_to_geom(self.a_type.get(), raw)
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
        opts = self._emx_opts_from_b()
        args = dict(typ=self.a_type.get(), geom=geom, cell=self.a_cell.get().strip() or None,
                    work=self.a_work.get().strip() or None,
                    freq=self.a_freq.get().strip() or DEF_FREQ,
                    emx_opts=opts, views_mode=self.a_views.get(), no_views=(task != "full"),
                    no_emx=(task == "layout"))
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
        self.b_cell.set(payload.get("cell") or "")
        self.b_work.set(self.a_work.get())
        self.b_freq.set(self.a_freq.get())
        self.b_gds.set(payload.get("gds") or "")
        self._log(self.a_log, "已联动填入 EM 仿真页参数")
        if payload.get("fingerprint") and self.curve:
            self._plot_fprint(payload.get("fingerprint"))
    def _render(self):
        gds = self.b_gds.get().strip()
        if not gds:
            work = self.a_work.get().strip() or "work"
            cell = self.a_cell.get().strip()
            gds = os.path.join(work, "%s.gds" % cell) if cell else ""
        if not gds or not os.path.isfile(gds):
            self._log(self.a_log, "找不到 GDS: %s" % gds)
            return
        png = os.path.splitext(gds)[0] + ".png"
        script = None
        for cand in (os.path.join(_HERE, "gds_render.py"),
                     os.path.join(_HERE, "tools", "gds_render.py"),
                     os.path.join(os.path.dirname(_HERE), "tools", "gds_render.py")):
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
            self._log(self.b_log, "请填 cell 名（可先在版图生成页出图）")
            return
        work = self.b_work.get().strip() or "work"
        gds = self.b_gds.get().strip() or os.path.join(work, "%s.gds" % cell)
        def worker():
            proc_arg = self.b_proc.get().strip() or None
            tools = find_tools(proc=proc_arg)
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
                    "未提取指纹。请先在「版图生成」页重新生成该 cell 再仿真。"
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
                views_mode=le["views_mode"], snp_out=le["snp"])
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
class _CurveCanvas(object):
    def __init__(self, parent, height=220):
        import tkinter as tk
        self.tk = tk
        self.canvas = tk.Canvas(parent, height=height, bg="white", highlightthickness=1,
                                highlightbackground="#cccccc")
        self.series = []
        self.srf = None
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
    def __init__(self, gui, tab):
        self.gui = gui
        self.tab = tab
        orig_collect = gui._collect_cfg
        def collect(validate=True):
            cfg = orig_collect(validate=validate)
            if cfg is not None:
                try:
                    le, geom_vars = tab._layout_em_cfg()
                    if le.get("enabled") and geom_vars:
                        exist = {v.get("name") for v in cfg.get("variables") or []}
                        merged = list(cfg.get("variables") or [])
                        for g in geom_vars:
                            if g["name"] not in exist:
                                merged.append({ "name": g["name"], "init": g["init"],
                                    "min": g["min"], "max": g["max"]})
                        cfg["variables"] = merged
                        cfg["layout_em"] = le
                        tech = tab._collect_tech()
                        warns = require_tech_ready(
                            tech, need=("layermap", "proc"))
                        for k in ("techlib", "techlib_dir", "layermap",
                                  "proc", "cds_lib"):
                            if tech.get(k):
                                cfg[k] = tech[k]
                        if warns:
                            tab._log(tab.a_log, "工艺检查通过（%d 条警告）：%s"
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
                tab.on_done(ev.get("cb"), ev.get("payload") or {})
                return
            orig_handle(ev)
        gui._handle = handle
def register(gui):
    from tkinter import ttk
    frame = ttk.Frame(gui.notebook)
    tab = _EmOptTab(gui, frame)
    gui.notebook.add(frame, text=" 版图 EM ")
    hook = _EmHook(gui, tab)
    return hook
def _selftest():
    import tempfile
    ok = [0]
    fail = [0]
    def check(name, cond, detail=""):
        if cond:
            ok[0] += 1
            print("  ok   %s" % name)
        else:
            fail[0] += 1
            print("  FAIL %s %s" % (name, detail))
    print("librf_opt selftest")
    check("parse_eng n", abs(parse_eng("2.5n") - 2.5e-9) < 1e-20)
    check("parse_eng G", abs(parse_eng("24G") - 24e9) < 1)
    check("parse_eng u", abs(parse_eng("150u") - 150e-6) < 1e-12)
    check("fmt_eng round", fmt_eng(2.5e-9) == "2.5n")
    fs = parse_freq_spec("1e9:3e9:3")
    check("freq linear", len(fs) == 3 and abs(fs[0] - 1e9) < 1 and abs(fs[-1] - 3e9) < 1)
    fs = parse_freq_spec("1G,5G")
    check("freq list", len(fs) == 2 and fs[1] == 5e9)
    fs = parse_freq_spec("24G")
    check("freq single", len(fs) == 1)
    check("skill_literal int key", skill_literal("n", 2.5) == "2.5")
    check("skill_literal w=10 float", skill_literal("w", 10) == "10.0")
    check("skill_literal ct=0", skill_literal("ct", 0) == "0")
    check("skill_literal str", skill_literal("type", "ind") == '"ind"')
    d = gen_driver_il({"type": "ind", "cell": "c1", "n": 2.5, "w": 10, "result": "/tmp/r.txt"})
    check("driver has librfRun", "librfRun" in d and "load(" in d)
    check("driver float fix", "'w 10.0" in d)
    dv = gen_driver_views_emx_il({"lib": "LIBRF", "cell": "c1",
                                  "snp": "/tmp/a.s2p", "ports": "p01 p02", "result": "/tmp/rv.txt"})
    check("views driver entry", "librfEmxViews" in dv)
    tmp = tempfile.mkdtemp()
    snp = os.path.join(tmp, "t.s2p")
    write_snp(snp, [1e9, 2e9], [[[0.1 + 0.2j, 0.01 + 0j]], [[0.15 + 0.1j, 0.02 + 0j]]],
              ports=["p01", "p02"])
    check("snp header", os.path.isfile(snp))
    f, S, z0, ports = read_snp(snp)
    check("snp roundtrip f", len(f) == 2 and abs(f[1] - 2e9) < 1)
    check("snp roundtrip ports", ports == ["p01", "p02"])
    check("snp roundtrip S", abs(S[0][0][0] - (0.1 + 0.2j)) < 1e-9)
    snp2 = os.path.join(tmp, "t2.s2p")
    safe_write_text(snp2, "1e9 0.1 0.2 0.01 0.0 0.02 0.0 0.01 0.0\n", what="sNp 文件")
    ensure_snp_header(snp2)
    with open(snp2) as fh:
        check("ensure_snp_header", fh.readline().startswith("#"))
    fpr = extract_fingerprint("ind", ["p01", "p02"], [1e9, 2e9, 3e9], [[[0.01j, 0.9], [0.9, 0.01j]],
                               [[0.02j, 0.85], [0.85, 0.02j]], [[0.03j, 0.8], [0.8, 0.03j]]])
    check("fingerprint L0", fpr.get("L0") is not None and fpr["L0"] > 0)
    check("fingerprint keys", "SRF" in fpr and "Qcurve" in fpr)
    def _tl_s2(_z, _th, _z0=50.0):
        _a = math.cos(_th)
        _b = 1j * _z * math.sin(_th)
        _c = 1j * math.sin(_th) / _z
        _d = math.cos(_th)
        _den = _a + _b / _z0 + _c * _z0 + _d
        return [[(_a + _b / _z0 - _c * _z0 - _d) / _den, 2 * (_a * _d - _b * _c) / _den],
                [2 / _den, (-_a + _b / _z0 - _c * _z0 + _d) / _den]]
    _se = _tl_s2(60.0, math.radians(60.0))
    _so = _tl_s2(35.0, math.radians(80.0))
    _s11 = (_se[0][0] + _so[0][0]) / 2.0
    _st = (_se[1][0] + _so[1][0]) / 2.0
    _sc = (_se[0][0] - _so[0][0]) / 2.0
    _si = (_se[1][0] - _so[1][0]) / 2.0
    _s4 = [[0j, _st, _si, _sc],
           [_st, 0j, _sc, _si],
           [_si, _sc, 0j, _st],
           [_sc, _si, _st, 0j]]
    for _i in range(4):
        _s4[_i][_i] = _s11
    _fpr4 = extract_fingerprint("cpl", ["p01", "p02", "p03", "p04"],
                                [1e9], [_s4], freq_of_interest=1e9)
    check("cpl 端口映射 Ze/Zo", abs(_fpr4.get("Ze", 0) - 60.0) < 1e-3
          and abs(_fpr4.get("Zo", 0) - 35.0) < 1e-3,
          "Ze=%s Zo=%s" % (_fpr4.get("Ze"), _fpr4.get("Zo")))
    check("cpl 端口映射 θe/θo", abs(_fpr4.get("theta_e", 0) - 60.0) < 1e-3
          and abs(_fpr4.get("theta_o", 0) - 80.0) < 1e-3,
          "θe=%s θo=%s" % (_fpr4.get("theta_e"), _fpr4.get("theta_o")))
    check("cpl 端口映射 a1/b1 同端", _cpl_port_indices(["p01", "p02", "p03", "p04"], 4)
          == (0, 1, 3, 2))
    check("cpl 端口映射随 Port order 重排",
          _cpl_port_indices(["p03", "p01", "p04", "p02"], 4) == (1, 3, 2, 0))
    _mp = write_device_meta(tmp, "dev1", {"type": "xfmr", "kind": "xfmr",
                                          "ports": ["p01", "p02", "p03", "p04"]})
    _m = load_device_meta(tmp, "dev1")
    check("device meta roundtrip", bool(_mp) and _m.get("kind") == "xfmr"
          and _m.get("ports") == ["p01", "p02", "p03", "p04"], str(_m))
    check("device meta missing", load_device_meta(tmp, "nope") == {})
    check("fingerprint kind 由元数据决定", fingerprint_kind_for_device({"kind": "cpl"}, 4) == "cpl")
    check("fingerprint kind 无元数据兜底", fingerprint_kind_for_device({}, 2) == "ind"
          and fingerprint_kind_for_device({}, 3) == "ct"
          and fingerprint_kind_for_device({}, 4) is None)
    check("kind_of tl/cpl", _kind_of("tl", {}, ["p01", "p02"]) == "tl"
          and _kind_of("cpl", {}, ["p01", "p02", "p03", "p04"]) == "cpl")
    check("指纹摘要按器件类型", "Z0=" in fprint_summary(
        {"kind": "tl", "Z0": 50, "theta": 90, "IL": 0.2})
          and "Ze=" in fprint_summary(
              {"kind": "cpl", "Ze": 60, "Zo": 35, "theta_e": 60, "theta_o": 80})
          and "L0=" in fprint_summary({"kind": "ind", "L0": 1e-9, "SRF": 1e10}))
    _vd = lri_norm_geom({"kind": "se", "dout": 200, "n": 2.5, "w": 4, "s": 1.5})
    _vd_ok, _vd_msg = lri_validate(_vd)
    check("DRC 电感区间距 s<2*min_s 拒绝", not _vd_ok, _vd_msg)
    _vo = lri_norm_geom({"kind": "se", "dout": 200, "n": 2.5, "w": 4, "s": 2.0})
    _vo_ok, _vo_msg = lri_validate(_vo)
    check("DRC 边界 s=2*min_s 放行", _vo_ok, _vo_msg)
    _vm = lri_norm_geom({"kind": "se", "dout": 200, "n": 2.5, "w": 0.5, "s": 3.0})
    _vm_ok, _vm_msg = lri_validate(_vm)
    check("DRC min_w 拒绝", not _vm_ok, _vm_msg)
    _vx = lri_norm_geom({"kind": "se", "dout": 200, "n": 2.5, "w": 31, "s": 5.0})
    _vx_ok, _vx_msg = lri_validate(_vx)
    check("DRC max_w 拒绝", not _vx_ok, _vx_msg)
    _vs = lri_norm_geom({"kind": "sym", "dout": 200, "n": 2.5, "w": 4, "s": 1.5})
    _vs_ok, _vs_msg = lri_validate(_vs)
    check("DRC sym 间距拒绝", not _vs_ok, _vs_msg)
    _loose = build_tech(base=DEFAULT_TECH, drc={"min_s": 0.5})
    check("DRC 阈值随 tech", lri_validate(_vd, tech=_loose)[0])
    k1 = geom_key("ind", {"dout": 150.001, "n": 2.5})
    k2 = geom_key("ind", {"n": 2.5, "dout": 150.0001})
    check("geom_key round", k1 == k2)
    _c1 = default_cell_name("ind", {"dout": 150, "n": 2.5})
    _c2 = default_cell_name("ind", {"dout": 250, "n": 2.5})
    check("默认 cell 名合法且随几何变化",
          re.match(r"^[A-Za-z0-9_]+$", _c1) is not None and _c1 != _c2,
          "%r vs %r" % (_c1, _c2))
    st = default_emx_settings()
    opts = emx_settings_to_opts(st)
    check("emx opts freq", "1e9" in opts["freq"] or "1000000000" in opts["freq"]
          or opts["freq"] == DEF_FREQ)
    check("emx opts 3d", opts.get("3d") == "*")
    gds_p = os.path.join(tmp, "a.gds")
    snp_p = os.path.join(tmp, "a.s2p")
    cmd = emx_cmd_preview(st, gds=gds_p, struct="c", out_snp=snp_p)
    check("emx preview order", cmd.index("--format") < cmd.index("--s-file") < cmd.index(gds_p))
    g = form_to_geom("ind", {"kind": "se", "shape": "octagon", "n": "2.5",
                             "w": "10", "s": "2.6", "dout": "150", "ct": "0",
                             "seam": "S", "tangle": "180", "underpass": "direct",
                             "din": ""})
    check("form_to_geom", g.get("shape") == "oct" and g.get("n") == 2.5 and "din" not in g)
    check("lri norm W/H/seam", g.get("W") == 150.0 and g.get("H") == 150.0
          and g.get("seam") == "bottom" and g.get("corner") > 0)
    gn = lri_norm_geom({"kind": "se", "seam": "N", "dout": 160, "n": 3,
                        "w": 10, "s": 3.2})
    check("lri seam map N->top", gn.get("seam") == "top")
    for _kind in ("se", "diff", "f8", "sym"):
        _n = 3 if _kind == "sym" else 2.5
        _g = lri_norm_geom({"kind": _kind, "shape": "oct", "dout": 220,
                            "n": _n, "w": 10, "s": 2.6})
        _ok, _msg = lri_validate(_g)
        check("lri validate %s" % _kind, _ok, _msg)
        if _ok:
            _geo = lri_build_ind(_g)
            check("lri build %s ports" % _kind,
                  len(_geo["ports"]) >= 2 and len(_geo["metal"]) >= 2)
    _half = lri_norm_geom({"kind": "sym", "dout": 220, "n": 2.5,
                           "w": 10, "s": 2.6})
    _ok, _msg = lri_validate(_half)
    check("lri sym 半圈拒绝", (not _ok) and "整数" in (_msg or ""), _msg)
    _bad = lri_norm_geom({"kind": "se", "dout": 30, "n": 9, "w": 10, "s": 3.2})
    _ok, _msg = lri_validate(_bad)
    check("lri validate rejects", (not _ok) and bool(_msg), _msg)
    _d = gen_driver_il({"type": "ind", "kind": "se", "seam": "bottom",
                        "tangle": 180, "W": 160.0, "H": 160.0, "corner": 10.0,
                        "cell": "c1", "n": 3, "w": 10, "result": "/tmp/r.txt"})
    check("driver carries kind/seam", "'kind \"se\"" in _d and "'seam \"bottom\"" in _d)
    check("driver carries W/H numeric", "'W 160.0" in _d and "'tangle 180.0" in _d)
    _fx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    _cds = os.path.join(tmp, "cds.lib")
    with _native_open(_cds, "w") as _fh:
        _fh.write("DEFINE keepMe /k\n")
    _tdir = os.path.join(tmp, "techlib")
    if not os.path.isdir(_tdir):
        os.makedirs(_tdir)
    _tech = build_tech(techlib_dir=_tdir,
                       layermap=os.path.join(_fx, "demo_stack.layermap"),
                       proc=os.path.join(_fx, "demo_stack.proc"),
                       cds_lib=_cds, base=DEFAULT_TECH)
    check("build_tech abspath", os.path.isabs(_tech["proc"])
          and os.path.isabs(_tech["layermap"]))
    check("build_tech keeps defaults", _tech["layers"]["spiral"] == "M5"
          and _tech["drc"]["via"]["cut"] == 0.5)
    check("tech deep merge keeps empty",
          _tech_deep_merge({"a": {"b": 1}}, {"a": {"b": ""}})["a"]["b"] == 1)
    try:
        _warns = require_tech_ready(_tech, need=("layermap", "proc"), probe=False)
        check("require_tech_ready 默认 profile", _warns == [], str(_warns))
    except LibrfError as _e:
        check("require_tech_ready 默认 profile", False, str(_e))
    try:
        _sw = require_tech_ready(dict(_tech, strict_flow=True),
                                 need=("layermap", "proc"), probe=False)
        check("require_tech_ready strict_flow 跑通", isinstance(_sw, list), str(_sw))
    except LibrfError as _e:
        check("require_tech_ready strict_flow 跑通", False, str(_e))
    _glmap = os.path.join(tmp, "generic.layermap")
    with open(_glmap, "w") as _fh:
        _fh.write("M5 drawing 90 0\nM4 drawing 80 0\nM3 drawing 70 0\n"
                  "V4 drawing 91 0\nV3 drawing 71 0\nMARKER drawing 199 0\n")
    _gproc = os.path.join(tmp, "generic.proc")
    with open(_gproc, "w") as _fh:
        _fh.write("conductor 0.5 0.1 M3\nconductor 0.5 0.1 M4\n"
                  "conductor 1.0 0.1 M5\nvia M3 M4 0.1 V3\n"
                  "via M4 M5 0.1 V4\n")
    _gtech = build_tech(techlib_dir=_tdir, layermap=_glmap, proc=_gproc,
                        cds_lib=_cds, base={
        "layers": {"spiral": "M5", "underpass": "M4", "route": "M3",
                   "ground": "M3", "via_sp": "V4", "via_un": "V3",
                   "marker": "MARKER"}, "text": {}, "drc": {}})
    try:
        _gw = require_tech_ready(_gtech, need=("layermap", "proc"), probe=False)
        check("require_tech_ready 通用层名", _gw == [], str(_gw))
    except LibrfError as _e:
        check("require_tech_ready 通用层名", False, str(_e))
    _gflow = dict(_gtech)
    _gflow["flow"] = {"metals": ["M5", "M4", "M3"],
                      "vias": [["M3", "M4", "V3"], ["M4", "M5", "V4"]]}
    try:
        _fw = require_tech_ready(_gflow, need=("layermap", "proc"), probe=False)
        check("require_tech_ready flow 顺序仅告警",
              any("顺序" in w for w in _fw)
              and not any("未在 proc 中找到" in w for w in _fw), str(_fw))
    except LibrfError as _e:
        check("require_tech_ready flow 顺序仅告警", False, str(_e))
    try:
        require_tech_ready(build_tech(base=DEFAULT_TECH), probe=False)
        check("require_tech_ready missing paths", False, "居然通过了")
    except LibrfError as _e:
        check("require_tech_ready missing paths", "工艺文件缺失" in str(_e))
    _glmap2 = os.path.join(tmp, "generic_missing.layermap")
    with open(_glmap2, "w") as _fh:
        _fh.write("M4 drawing 80 0\nM3 drawing 70 0\n"
                  "V3 drawing 71 0\nMARKER drawing 199 0\n")
    try:
        require_tech_ready(build_tech(base=_gtech, layermap=_glmap2),
                           need=("layermap",), probe=False)
        check("require_tech_ready 缺角色层", False, "居然通过了")
    except LibrfError as _e:
        check("require_tech_ready 缺角色层",
              "M5" in str(_e) and "spiral" in str(_e), str(_e))
    msgs = _health_judge({"Lcurve": [[1e9, 1e-9], [2e9, 0.98e-9], [3e9, 0.95e-9]],
                          "Qcurve": [[1e9, 5], [2e9, 10], [3e9, 8]], "SRF": 30e9})
    check("health ok L", any(l == "ok" for l, _t in msgs))
    class _Delegate(object):
        def run(self, values, iter_dir):
            r = _FallbackSimResult(curves={"S21": ([1e9], [0.5])}, status="ok")
            return r
    cfg = {"layout_em": { "enabled": True, "type": "ind", "cell": "t_ind", "work": tmp,
        "geom_vars": ["dout"], "fixed_geom": {"n": 2.5, "w": 10},
        "snp": os.path.join(tmp, "snps", "t_ind.s2p"),
        "emx": {"freq": "1e9:3e9:3"}, "geom_outputs": True}}
    gr = GeomRunner(cfg, _Delegate())
    _seed = lri_norm_geom({"n": 2.5, "w": 10, "dout": 150.0})
    key = geom_key("ind", _seed,
                   {"freq": "1e9:3e9:3", "edge": None, "3d": None, "mode": None})
    fake_snp = os.path.join(tmp, "fake.s2p")
    write_snp(fake_snp, [1e9], [[[0.1j, 0.9], [0.9, 0.1j]]])
    gr.cache[key] = {"snp": fake_snp, "fingerprint": {"L0": 1.2e-9, "SRF": 40e9, "Q": {"1e9": 8.0},
                                     "Qcurve": [[1e9, 8.0]], "Lcurve": [[1e9, 1.2e-9]]},
                     "ports": ["p01", "p02"], "kind": "ind"}
    os.makedirs(os.path.dirname(gr.snp_path), exist_ok=True)
    r = gr.run({"dout": 150.0, "L1": 100e-12}, tmp)
    check("GeomRunner status", r.status == "ok", r.message)
    check("GeomRunner circ curves", "S21" in (r.curves or {}))
    check("GeomRunner geom curves", "L0" in (r.curves or {})
          and "SRF" in (r.curves or {}) and "Q@f0" in (r.curves or {}))
    check("GeomRunner snp published", os.path.isfile(gr.snp_path))
    _STOP_REQUESTED.set()
    r2 = gr.run({"dout": 150.0}, tmp)
    check("GeomRunner stopped", r2.status != "ok")
    _STOP_REQUESTED.clear()
    class _DeckDelegate(object):
        def __init__(self):
            self.deck = None
        def _load_deck(self):
            text = ('X1 (a b) nport file="/old/path/dev.s2p" p=2\n' 'X2 (c d) resistor r=50\n')
            self.deck = (text, {})
            return self.deck
        def run(self, values, iter_dir):
            return _FallbackSimResult(status="ok")
    dd = _DeckDelegate()
    gr2 = GeomRunner(cfg, dd)
    gr2._patch_deck()
    text2 = dd.deck[0]
    check("deck dataFile rewritten", gr2.snp_path.replace("\\", "/") in text2
          and "/old/path" not in text2, text2)
    check("deck non-nport untouched", 'resistor r=50' in text2)
    cfg_mock = dict(cfg)
    cfg_mock["layout_em"] = dict(cfg["layout_em"])
    cfg_mock["layout_em"]["mock"] = True
    gr3 = GeomRunner(cfg_mock, _Delegate(), mock=True)
    r3 = gr3.run({"dout": 120.0, "n": 2.0, "L1": 1e-10}, tmp)
    check("mock GeomRunner ok", r3.status == "ok", r3.message)
    check("mock geom curves", "L0" in (r3.curves or {}) and r3.curves["L0"][1][0] > 0)
    r4 = gr3.run({"dout": 200.0, "n": 3.5, "L1": 1e-10}, tmp)
    l_big = r4.curves["L0"][1][0]
    l_small = r3.curves["L0"][1][0]
    check("mock L0 monotone", l_big > l_small, "%s vs %s" % (l_big, l_small))
    import inspect as _insp
    sig = _insp.signature(_EmOptTab._make_worker)
    kinds = [p.kind for p in sig.parameters.values()]
    check("make_worker **kwargs", _insp.Parameter.VAR_KEYWORD in kinds, str(sig))
    sig2 = _insp.signature(make_layout_emx)
    check("make_layout_emx no_emx", "no_emx" in sig2.parameters)
    st2 = default_emx_settings()
    st2["accuracy"]["edge_width"] = 1.0
    pset = os.path.join(tmp, "emx.json")
    okw, _p = save_emx_settings(st2, pset)
    st3 = load_emx_settings(pset)
    check("emx settings roundtrip", okw and st3["accuracy"]["edge_width"] == 1.0)
    cjk = (b"STATUS fail\n" b"ERR \xe7\x94\xbb\xe7\x9f\xa9\xe5\xbd\xa2\xe5\xa4\xb1\xe8\xb4\xa5 "
           b"layer=M5\n" b"WARN \xe8\xa2\xab Virtuoso \xe5\x8d\xa0\xe7\x94\xa8\n"
           b"CELL ind_test\n")
    pcjk = os.path.join(tmp, "res_cjk.txt")
    with _native_open(pcjk, "wb") as f:
        f.write(cjk)
    try:
        rcjk = parse_skill_result(pcjk)
        check("skill result 中文不崩", rcjk["cell"] == "ind_test"
              and rcjk["errs"] and "M5" in rcjk["errs"][0], str(rcjk["errs"]))
    except UnicodeDecodeError as e:
        check("skill result 中文不崩", False, str(e))
    try:
        ro = run_cmd([sys.executable, "-c",
                      "import sys;sys.stdout.buffer.write("
                      "'ERR \\u753b\\u77e9\\u5f62\\n'.encode('utf-8'))"])
        check("run_cmd 中文输出不崩", ro["ok"] and "画" in ro["stdout"], repr(ro.get("stdout", "")))
    except UnicodeDecodeError as e:
        check("run_cmd 中文输出不崩", False, str(e))
    try:
        pj = os.path.join(tmp, "cjk.json")
        atomic_write_json(pj, {"name": "测试电感", "n": 3})
        check("json 中文读写", read_json(pj)["name"] == "测试电感")
    except UnicodeDecodeError as e:
        check("json 中文读写", False, str(e))
    d0 = load_tech_profile(use_cache=False)
    check("tech 默认 techlib", d0["techlib"] == "RFCMOS")
    check("tech 默认 spiral=M5", tech_layer(d0, "spiral") == "M5")
    check("tech 默认 via_sp=V4", tech_layer(d0, "via_sp") == "V4")
    check("tech 未知角色回退原名", tech_layer(d0, "M9") == "M9")
    check("tech 文字层映射", tech_text(d0, "M5") == "M5TXT")
    check("tech 文字层兜底", tech_text(d0, "Z9") == "Z9TXT")
    check("tech DRC min_w", tech_drc(d0)["min_w"] == 1.0)
    tp = os.path.join(tmp, "tech_profile.json")
    _lm_real = os.path.join(tmp, "my.layermap")
    _pr_real = os.path.join(tmp, "my.proc")
    with open(_lm_real, "w") as fh:
        fh.write("layermap placeholder\n")
    with open(_pr_real, "w") as fh:
        fh.write("# proc placeholder\n")
    atomic_write_json(tp, { "techlib": "DEMO65",
        "layers": {"spiral": "M6", "underpass": "M5", "route": "M4",
                   "ground": "M4", "via_sp": "V5", "via_un": "V4", "marker": "MARKER"},
        "text": {"M6": "M6TXT"}, "drc": {"min_w": 0.2, "min_area": {"M6": 0.1}},
        "layermap": _lm_real, "proc": _pr_real})
    d1 = load_tech_profile(path=tp, use_cache=False)
    check("tech 自定义 techlib", d1["techlib"] == "DEMO65")
    check("tech 自定义 spiral=M6", tech_layer(d1, "spiral") == "M6")
    check("tech 自定义 via_sp=V5", tech_layer(d1, "via_sp") == "V5")
    check("tech 自定义文字层", tech_text(d1, "M6") == "M6TXT")
    check("tech 未列出金属兜底", tech_text(d1, "M4") == "M4TXT")
    check("tech DRC 覆盖", tech_drc(d1)["min_w"] == 0.2)
    check("tech DRC 缺项回落", tech_drc(d1)["max_w"] == 30.0)
    check("tech 路径传导 layermap", d1["layermap"] == _lm_real)
    check("tech 路径传导 proc", d1["proc"] == _pr_real)
    sk = tech_to_skill(d1)
    check("skill 注入含 librfTech", "librfTech = list(" in sk)
    check("skill 注入 spiral", 'list("spiral" "M6")' in sk)
    check("skill 注入 techlib", 'librfTechLib = "DEMO65"' in sk)
    check("skill 注入 DRC", "librfDrc = list(" in sk)
    check("skill 注入 DRC 数值不加引号", 'list("min_w" 0.2)' in sk, sk)
    check("skill 注入 min_area 数值不加引号", 'list("M6" 0.1)' in sk, sk)
    check("skill 注入 via 数值不加引号", 'list("cut" 0.5)' in sk, sk)
    d2 = gen_driver_il({"type": "ind", "cell": "c_tech", "n": 2.5, "w": 10,
                        "s": 2.5, "dout": 100, "result": os.path.join(tmp, "r.txt"),
                        "libdir": os.path.join(tmp, "L")}, tech=d1)
    check("driver 含 librf.il", "librf.il" in d2)
    check("driver 含工艺注入", 'list("spiral" "M6")' in d2)
    check("driver 含 techLibName", "DEMO65" in d2)
    t3 = find_tools(tech=d1, work=tmp)
    check("find_tools layermap=profile", (t3["layermap"]["path"] == _lm_real), str(t3["layermap"]))
    check("find_tools proc=profile", (t3["proc"]["path"] == _pr_real), str(t3["proc"]))
    check("find_tools techlib=profile", t3["techlib"]["path"] == "DEMO65")
    wdir = os.path.join(tmp, "ws_protect")
    os.makedirs(wdir)
    cds = os.path.join(wdir, "cds.lib")
    orig = ("DEFINE myPDK /project/Tech/myPDK\n" "DEFINE MyLib /project/WS/MyLib\n"
            "DEFINE analog_lib /home/u/libs/analog\n"
            "SOFTINCLUDE /opt/eda/cadence/share/cdssetup/cds.lib\n")
    with _native_open(cds, "w") as fh:
        fh.write(orig)
    note_created(cds)
    gen_cds_lib(wdir, lib="LIBRF", tools={})
    with _native_open(cds) as fh:
        after = fh.read()
    check("cds.lib 原有行全保留", all(l in after for l in orig.strip().splitlines()), after)
    check("cds.lib 追加了 LIBRF", "DEFINE LIBRF" in after, after)
    check("cds.lib 未截断", after.count("DEFINE") >= 4, after)
    gen_cds_lib(wdir, lib="LIBRF", tools={})
    with _native_open(cds) as fh:
        after2 = fh.read()
    check("cds.lib 重复追加幂等", after2.count("DEFINE LIBRF") == 1, after2)
    check("cds.lib 内容稳定", after2 == after)
    baks = [x for x in os.listdir(wdir) if x.startswith("cds.lib.bak-")]
    check("cds.lib 改前有备份", bool(baks), str(os.listdir(wdir)))
    outside = os.path.join(tmp, "user_owned.txt")
    with _native_open(outside, "w") as fh:
        fh.write("precious user data\n")
    try:
        safe_write_text(outside, "clobbered\n", what="用户文件")
        check("拒绝覆盖用户文件", False, "居然写成功了")
    except LibrfError as e:
        check("拒绝覆盖用户文件", "拒绝覆盖" in str(e))
    with _native_open(outside) as fh:
        check("用户文件内容未变", fh.read() == "precious user data\n")
    owndir = os.path.join(os.getcwd(), "work", "_t_safe")
    if not os.path.isdir(owndir):
        os.makedirs(owndir)
    user_in_work = os.path.join(owndir, "user_precious.json")
    with _native_open(user_in_work, "w") as fh:
        fh.write("user data in work\n")
    try:
        safe_write_text(user_in_work, "clobbered\n", what="领地内用户文件")
        check("拒绝覆盖领地内用户文件", False, "居然写成功了")
    except LibrfError as e:
        check("拒绝覆盖领地内用户文件", "拒绝覆盖" in str(e))
    with _native_open(user_in_work) as fh:
        check("领地内用户文件未变", fh.read() == "user data in work\n")
    ownf = os.path.join(owndir, "gen.json")
    if os.path.isfile(ownf):
        try:
            os.remove(ownf)
        except OSError:
            note_created(ownf)
    try:
        safe_write_text(ownf, '{"a":1}\n', what="自有产物")
        safe_write_text(ownf, '{"a":2}\n', what="自有产物")
        with _native_open(ownf) as fh:
            check("自有产物可更新", fh.read() == '{"a":2}\n')
    except LibrfError as e:
        check("自有产物可更新", False, str(e))
    selfdir = os.path.join(tmp, "selfinc")
    os.makedirs(selfdir)
    selfcds = os.path.join(selfdir, "cds.lib")
    with _native_open(selfcds, "w") as fh:
        fh.write("DEFINE keepMe /k\n")
    note_created(selfcds)
    hit, _src = _probe_data_file("cds.lib", (), [selfdir], patterns=("cds.lib",), exclude=selfcds)
    check("cdssetup 不自引用", hit != selfcds, str(hit))
    print("selftest: %d ok, %d fail" % (ok[0], fail[0]))
    return fail[0] == 0
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(0 if _selftest() else 1)
    print("librf_opt.py — optimizer_v06 版图/EM 自动化模块")
    print("用法: python librf_opt.py --selftest")
