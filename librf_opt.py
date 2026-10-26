# -*- coding: utf-8 -*-
"""librf_opt.py — optimizer_v06 版图自动化 + 电磁仿真自动化（LIBRF 精简整合版）"""
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
# 文本 I/O 一律 UTF-8：目标机 py3.6 + C locale 下 open() 默认解码是 ASCII，读中文即崩；文本读 replace、写严格 utf-8，二进制放行。
import builtins
_native_open = builtins.open
def open(file, mode="r", *args, **kwargs):  # noqa: A001 - 有意覆盖内建 open
    """UTF-8 文本打开（仅本模块）：读 replace 容错，写严格 utf-8；二进制照旧。"""
    if "b" not in mode and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
        if not any(c in mode for c in "wxa+"):
            kwargs.setdefault("errors", "replace")
    return _native_open(file, mode, *args, **kwargs)
def _force_utf8_stdio():
    """stdout/stderr 重包成 UTF-8（仅当 locale 把它留成 ASCII 时）。"""
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
SKILL_IL = os.path.join(_HERE, "librf.il")  # 合并后的 SKILL：版图引擎 + 求值服务
DEF_LIB = "LIBRF"
DEF_WS = "~/virtuoso_work"
DEF_FREQ = "1e9:30e9:21"
DEF_EMX_TIMEOUT = 1800
DEF_SKILL_TIMEOUT = 600
# PDK 中 layermap / 工艺文件的常见文件名与相对位置（用于现场搜索）
LAYERMAP_NAMES = ("layer.map", "layermap.txt", "*.layermap")
PROC_NAMES = ("*.proc",)
TECHLIB_SUBDIRS = ()  # 额外搜索的工艺库子目录名；空=只按 techlib 名搜索
TECH_PROFILE_NAME = "tech_profile.json"  # LIBRF_TECH_PROFILE 可覆盖
# 内建默认工艺：通用占位。换工艺时在 tech_profile.json 里覆盖，缺项逐项回落本表。
DEFAULT_TECH = { "name": "Generic RF CMOS (built-in default)", "techlib": "RFCMOS",
    "techlib_dir": "", "layermap": "", "proc": "", "layers": {
        "spiral": "M5", "underpass": "M4", "route": "M3", "ground": "M3",
        "via_sp": "V4", "via_un": "V3", "marker": "MARKER", }, "text": {
        "M5": "M5TXT", "M4": "M4TXT", "M3": "M3TXT", "M2": "M2TXT", "M1": "M1TXT", }, "drc": {
        "min_w": 1.0, "max_w": 30.0, "min_s": 1.0, "min_area": {"M5": 4.0, "M4": 4.0, "M3": 4.0},
        "via": {"cut": 0.5, "spacing": 0.5, "enclosure": 0.2}, }, }
class LibrfError(Exception):
    """librf_opt 统一错误类型（中文信息，GeomRunner 转 SimResult）"""
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
# 铁律：只写尚不存在的文件或本工具自建产物；已有非本工具文件拒绝覆盖（领地不豁免）；追加不截断、改动先留 .bak，归属登记在 work/.librf_owned.json。
_TOOL_OWNED = set()
_OWNED_LOADED = False
def _manifest_path():
    """归属清单：落在工具输出领地 work/ 下，绝不写到 home 根/临时目录。"""
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
    """持久化归属清单（仅新建/更新自己领地内的清单文件）。"""
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
    """登记：这个文件是本工具创建的（会话内 + 落盘清单）。"""
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
    """改已有文件之前留副本 -> <path>.bak-YYYYMMDD-HHMMSS。返回备份路径。"""
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
    """工具自己的输出领地（收窄）：仅 work/、logs/ 与 <WS>/<库> 子树。"""
    roots = []
    for cand in (os.path.join(os.getcwd(), "work"), os.path.join(os.getcwd(), "logs")):
        if cand:
            roots.append(os.path.abspath(cand))
    ws = os.environ.get("LIBRF_WS")
    if ws:
        ws_abs = os.path.abspath(os.path.expanduser(ws))
        # 只放行工作空间下的库目录（如 <WS>/LIBRF），不是整棵 WS
        roots.append(os.path.join(ws_abs, DEF_LIB))
    return tuple(roots)
def _assert_writable(path, what, managed=True):
    """已有文件且不是本工具产物 -> 拒绝（铁律，领地不豁免）。"""
    path = os.path.abspath(os.path.expanduser(str(path)))
    if os.path.exists(path) and not is_tool_owned(path):
        raise LibrfError("拒绝覆盖已有%s：%s\n"
            "本工具不删除/覆盖目标机中已存在的文件（含输出领地内的" "非本工具产物）。\n"
            "若确实要重建它，请自行删除后重试（工具不会代劳）。\n"
            "（工具输出领地：%s）" % (what, path, "; ".join(_managed_roots()) or "(空)"))
    return path
# 纯工具内部状态：每轮重生成、无用户数据，更新时免 .bak（避免磁盘膨胀）
_INTERNAL_STATE_NAMES = ( ".librf_owned.json", ".geom_cache.json", )
_INTERNAL_STATE_PREFIXES = ("driver_", "res_", "dbaccess.log", "strmout_")
_INTERNAL_STATE_SUFFIXES = (".emx.log", ".tmp", ".old-", ".bak-")
def _is_internal_state(path):
    """工具内部状态文件（manifest/缓存/每轮 driver|res|日志）-> 更新免备份。"""
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
def safe_write_text(path, text, what="文件", backup=None):
    """原子写新建文件或本工具产物。已有非本工具文件 -> 抛错。"""
    path = _assert_writable(path, what)
    if backup is None:
        backup = not _is_internal_state(path)
    if backup and os.path.isfile(path):
        backup_file(path)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    note_created(path)
    return path
def _assert_cell_writable(libdir, cell, view="layout"):
    """cellview 并发/归属检查（铁律 + cdslck 锁）。"""
    cell = str(cell or "")
    libdir = os.path.abspath(os.path.expanduser(str(libdir or "")))
    if not cell or not libdir:
        return
    view_dir = os.path.join(libdir, cell, view)
    # 1) 锁：Virtuoso 打开编辑器时会在 view 目录留下 *.cdslck*
    if os.path.isdir(view_dir):
        for name in os.listdir(view_dir):
            if "cdslck" in name.lower():
                raise LibrfError("目标 cellview %s/%s/%s 被 Virtuoso 占用"
                    "（存在 %s）。请先关闭 Virtuoso 中打开的对应编辑器，"
                    "或换 cell 名重试。未写入任何数据，已安全退出。" % (libdir, cell, view, name))
    # 2) 归属：已有图形数据且未登记 -> 视为用户 cell，拒绝清空重画
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
    """生成成功后写归属标记（.librf_owned），供跨会话识别自有 cell。"""
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
    """向文件追加行（缺失的才加）。绝不截断、绝不改写已有行。"""
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
    """JSON 原子写。已有非本工具文件 -> 拒绝（同铁律）。备份语义同 safe_write_text。"""
    path = _assert_writable(path, "JSON 文件")
    if backup is None:
        backup = not _is_internal_state(path)
    if backup and os.path.isfile(path):
        backup_file(path)
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
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
    """工程记数法 '1.2n' / '24G' / '150u' -> float（3.6 兼容）"""
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
    """bytes -> str。永远 UTF-8 且不抛异常（C locale 下 locale 解码是 ASCII）。"""
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    return b.decode("utf-8", "replace")
def run_cmd(cmd, cwd=None, timeout=None, verbose=False, env=None):
    """运行外部命令 -> {"ok","rc","stdout","stderr","cmd","timeout"}"""
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
        # 不用 universal_newlines：按字节收再 _decode 显式 UTF-8（subprocess 默认 locale 解码，C locale 必崩）。
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
# 工艺配置 tech_profile.json：techlib/layermap/proc、金属角色→层(layers)、文字层(text)、DRC(drc)；缺项按内建默认逐级回落。
_TECH_CACHE = {}
def _deep_merge(base, over):
    """dict 递归合并：over 覆盖 base，返回新 dict（不改入参）。"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
def load_tech_profile(work=None, path=None, use_cache=True):
    """载入工艺配置 -> dict（已与内建默认合并）。"""
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
def tech_layer(prof, role):
    """角色 -> 层名（找不到角色名时按原样返回，允许直接传层名）。"""
    lay = (prof or {}).get("layers") or {}
    return lay.get(role, role)
def tech_text(prof, metal):
    """金属层名 -> 该金属的 pin/label 文字层。"""
    txt = (prof or {}).get("text") or {}
    return txt.get(metal, "%sTXT" % metal)
def tech_drc(prof):
    """DRC 阈值 dict（与内建默认合并后）。"""
    return (prof or {}).get("drc") or DEFAULT_TECH["drc"]
def tech_to_skill(prof):
    """把 profile 的 layers/text/drc 摊平成 SKILL driver.il 的注入块。"""
    lay = (prof or {}).get("layers") or {}
    txt = (prof or {}).get("text") or {}
    drc = (prof or {}).get("drc") or {}
    def _alist(d):
        return "list(" + " ".join( 'list("%s" "%s")' % (k, v) for k, v in sorted(d.items())) + ")"
    def _drc_alist(d):
        parts = []
        for k in sorted(d.keys()):
            v = d[k]
            if isinstance(v, dict):
                parts.append('list("%s" %s)' % (k, _alist(v)))
            else:
                parts.append('list("%s" %s)' % (k, v))
        return "list(" + " ".join(parts) + ")"
    techlib = (prof or {}).get("techlib") or ""
    return ("; librf_opt 注入的工艺配置（勿手改）\n"
        "librfTech = %s\n" "librfTechText = %s\n"
        "librfDrc = %s\n"
        "librfTechLib = \"%s\"\n" % (_alist(lay), _alist(txt), _drc_alist(drc), techlib) )
def _techlib_name(prof=None):
    if prof and prof.get("techlib"):
        return prof["techlib"]
    return _env_path("LIBRF_TECHLIB", (DEFAULT_TECH.get("techlib") or "RFCMOS"))
def _import_discover():
    """共用探查逻辑放在 optimizer.py；独立运行时回退到本地最小实现。"""
    try:
        import optimizer as _opt
        return _opt.find_prog, _opt.find_file
    except Exception:
        return None, None
def _probe_data_file(basename, env_vars, extra_dirs, patterns=(), exclude=None):
    """在给定目录/环境中按名字或通配符找数据文件 -> (path|None, src)。"""
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
                # 也试一层子目录（PDK 常把 layermap 放在 <tech>/<techlib>/ 里）
                for hit in sorted(glob.glob(os.path.join(d, "*", pat))):
                    if _ok(hit):
                        return hit, "given"
    return None, "none"
def find_tools(proc=None, verbose=False, tech=None, work=None):
    """现场探查 emx/dbaccess/strmout/layermap/proc/techlib。"""
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
    # 工艺文件（EMX proc）：完全按路径确定，不内嵌内容
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
                   "freq_hint", "overlap", "pitch", "ccw", "phi0", "ex", "ey", "ind_mark")
def skill_literal(key, value):
    """Python 值 -> SKILL 字面量（数值必须带小数点防 sprintf 崩）"""
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
                "n", "ct", "ports", "np", "ns", "gnd", "fence", "cpw", "ccw", "ind_mark"):
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
    """建库配方（实测锁定）：dbCreateLib 2 参；techLibName 无条件确保"""
    return ('let((lib)\n'
        '  lib = ddGetObj("%s")\n' '  unless(lib\n'
        '    lib = dbCreateLib("%s" "%s")\n' '  )\n'
        '  when(lib && lib->techLibName != "%s"\n' '    lib->techLibName = "%s"\n'
        '  )\n' ')\n' % (lib, lib, libdir.replace("\\", "/"), techlib, techlib))
def gen_driver_il(req, layout_il=None, tech=None):
    """生成 driver.il：load librf.il -> 注入工艺 -> 建库 -> librfRun(...)"""
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
    order = ["type", "lib", "cell", "view", "shape", "n", "w", "s", "dout",
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
    txt = ('; librf_opt auto-generated driver (do not edit)\n'
        'load("%s")\n' '%s'
        'errset(progn(\n' '  librfRun(list(nil\n'
        '  %s\n' '  ))\n'
        ') t)\n' 'exit()\n' % (layout_il.replace("\\", "/"),
                      tech_to_skill(tech) + _lib_setup_block(lib, libdir, _techlib_name(tech)),
                      body))
    return txt
def gen_driver_views_il(req, layout_il=None):
    """views(driver) skill 模式：librfMakeViews（回退路径）"""
    return _gen_views_driver(req, layout_il, "librfMakeViews", "res_views.txt",
                             "views driver(skill)")
def gen_driver_views_emx_il(req, layout_il=None):
    """views(driver) emx 模式：librfEmxViews（EMX create-view 配方）"""
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
    txt = ('; librf_opt auto-generated %s (do not edit)\n'
        'load("%s")\n' '%s'
        'errset(progn(\n' '  %s(list(nil\n'
        '  %s\n' '  ))\n'
        ') t)\n' 'exit()\n' % (tag, layout_il.replace("\\", "/"),
                      tech_to_skill(tech) + _lib_setup_block(lib, libdir, _techlib_name(tech)),
                      entry, body))
    return txt
def parse_skill_result(path):
    """解析 SKILL result 行协议 -> dict（layout/views 共用）"""
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
                     "layer": toks[-1] if len(toks) > 4 else "M5TXT"}
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
    # 铁律：只追加、不覆盖；改动前由 append_unique 自动留 .bak
    try:
        added, bak = append_unique(cds, [define_line], what="cds.lib")
    except IOError as e:
        raise LibrfError("无法写入 %s: %s（工作空间可能只读）" % (cds, e))
    if bak:
        _eprint("已在改动前备份 cds.lib -> %s" % bak)
    return libdir, bool(added)
def gen_cds_lib(work_dir, lib=DEF_LIB, libdir=None, tools=None, ws=None, tech=None):
    """向 <work>/cds.lib **追加**库定义 —— 绝不覆盖已有内容。"""
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
    # 只收集「缺失才需要补」的行。空路径的 DEFINE 是非法语法，一律不写。
    want = []
    if not existed:
        # 新建文件才考虑 SOFTINCLUDE；已有文件的 include 一个字都不动。
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
    """work/ws/libdir 解析（工作空间模式默认；显式 work 且无 ws -> 独立模式）"""
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
    # 铁律：改动已有文件前先备份（S2）
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
    """sNp 列序端口名（EMX 列序=名字典序，绝不用创建序）"""
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
    """生成 schematic/symbol -> {"schematic":..., "symbol":...}"""
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
    """频点语法 -> Hz 列表"""
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
    """组装 EMX 命令行：--format/--s-impedance 必须在 --s-file 之前"""
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
    """sNp 校验：存在、可解析、端口数一致；无头行补 '# HZ S RI R 50'"""
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
    """运行 EMX -> {"ok","cmd","out_snp","validate","warn","log"}"""
    emx = require_tool(tools, "emx", "EMX 电磁仿真")
    # 频点为空会让 EMX 无扫频参数而挂起至 timeout —— 快速失败（根因修复）
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
    # 铁律：目标 sNp 若已存在且非本工具产物 -> 拒绝（不得先 note_created 认领）
    _assert_writable(out_snp, "sNp 文件")
    cmd = build_emx_cmd(emx, gds, struct, proc, freqs, out_snp,
                        edge=edge, three_d=three_d, mode=mode, ports=ports,
                        extra=extra, sweep=sweep, s_impedance=s_impedance)
    r = run_cmd(cmd, cwd=os.path.dirname(out_snp) or None, timeout=timeout, verbose=verbose)
    # EMX 产出后登记归属（供后续补头/补端口序/下一轮刷新）
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
# ---- RF 数学 + Touchstone + 指纹（librf_rf.py 精简：ind/ct/xfmr 为主） ----
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
    """S -> Z（n 端口）。Z = (I+S)(I-S)^{-1} * z0。v08 提取升级用。"""
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
    """读 Touchstone v1 -> (freqs, S, z0, ports)"""
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
    """无 '#' 头行则补 '# HZ S RI R 50'（spectre 无头行报 SPECTRE-10007）"""
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
    # 铁律：改动已有文件前先备份（S2）；原子替换
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
    """L 曲线过零（正到负）线性插值 -> SRF；无过零返回 None"""
    for i in range(1, len(lcurve)):
        y0, y1 = lcurve[i - 1][1], lcurve[i][1]
        if y0 > 0 >= y1 and y0 != y1:
            x0, x1 = lcurve[i - 1][0], lcurve[i][0]
            return x0 + (x1 - x0) * (y0 / (y0 - y1))
    return None
def _s_to_abcd2(s, z0=50.0):
    """S -> ABCD (2-port, 等实参考阻抗)。v08 提取升级用。"""
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
    """ABCD 真 Z0/θ（v08 推荐口径，合成验证 ≤1e-16）。"""
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
    """低频线性外推 L(f)=L0+a·f 截距。窗口无点时退化最低频段。"""
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
    """cpl 四端口下标 (a1,a2,b1,b2)。按端口名映射，不写死 0/1/2/3。"""
    names = list(ports) if ports else ["p%02d" % (i + 1) for i in range(n)]
    if len(names) > n:
        names = names[:n]
    if n < 4 or len(names) < 4:
        raise LibrfError( "extract_fingerprint: cpl 需要 4 个端口名，收到 n=%d ports=%s" % (n, names))
# 角色识别：a1/a2/b1/b2 或线名对；否则按名字排序，前两个=线A、后两个=线B。
    low = [str(x).lower() for x in names]
    role = {}
    for key in ("a1", "a2", "b1", "b2"):
        if key in low:
            role[key] = low.index(key)
    if len(role) == 4:
        return role["a1"], role["a2"], role["b1"], role["b2"]
    order = sorted(range(n), key=lambda i: str(names[i]))
    a1, a2, b1, b2 = order[0], order[1], order[2], order[3]
    return a1, a2, b1, b2
def extract_fingerprint(kind, ports, freqs, S, freq_of_interest=None):
    """S 参数 -> 电学指纹 dict。"""
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
        """ct* 中心抽头，其余按名字排序配对（EMX 字典序 ct1<p01，勿写死下标）"""
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
        # v08：L0_dc=低频外推（f≤min(1GHz,0.05·SRF)）；L0=工作点 L@f0（无 f0 用外推）。
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
        # v08 口径：Lp/Ls/k 来自 Z 参数（k=Im(Z12)/√(Im(Z11)·Im(Z22))）
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
                # P2: k 夹紧 [0,1]（数值噪声可略超）
                if kk > 1.0:
                    kk = 1.0
                elif kk < -1.0:
                    kk = -1.0
                ks.append([f, kk])
        l0 = None
        if lps:
            # P2: xfmr 窗口用 0.05·SRF 估计（无 SRF 时用最低频段）
            f_max = min(1e9, 0.05 * freqs[-1]) if freqs[-1] > 0 else 1e9
            l0 = _l0_extrapolate(lps, f_max)
            if l0 is None:
                l0 = lps[0][1]
        ls0 = _l0_extrapolate(lss, 1e9) if lss else None
        k0 = None
        if ks:
            k0 = ks[0][1] if f0 is None else _interp_at( [p[0] for p in ks], [p[1] for p in ks], f0)
        # 别名：Lp/Ls 为契约主键；L0/Ls0 保留 v07 兼容
        out.update({ "Lp": l0, "Ls": ls0, "k": k0, "L0": l0, "Ls0": ls0,
            "Lpcurve": lps, "Lscurve": lss, "kcurve": ks,
            "Lunit": "H", "k_unit": "1", "extract_window": {"f0": f0}, })
        return out
    if kind_l == "tl":
        # v08 口径：ABCD Z0=sqrt(B/C)，θ=atan2(Im(B/Z0), Re(A)) 度
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
        # v08 新增：混合模 Se/So -> ABCD Ze/Zo/θe/θo；端口按名映射 a1—a2 / b1—b2（1/3 近端 2/4 远端）。
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
# ---- 几何缓存 / 一键管线 make_layout_emx ----
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
def _kind_of(typ, geom, port_names):
    """type+geom+端口 -> 指纹 kind（ind/ct/xfmr/tl/cpl）"""
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
def make_layout_emx(typ, geom, cell=None, work=None, ws=None, lib=None,
                    freq=DEF_FREQ, tools=None, emx_opts=None,
                    views_mode="emx", snp_out=None, no_views=False,
                    no_gds=False, no_emx=False, verbose=False,
                    skill_timeout=None, emx_timeout=None, on_step=None,
                    tech=None, metal_override=None):
    """一键管线：layout -> strmout -> EMX -> views -> 指纹。"""
    if on_step is None:
        on_step = lambda msg: None
    emx_opts = emx_opts or {}
    st = skill_timeout or DEF_SKILL_TIMEOUT
    et = emx_timeout or DEF_EMX_TIMEOUT
    if tech is None:
        tech = load_tech_profile(work=work)
    # 每轮可覆盖金属角色：metal_override = {"spiral": "M4", ...}
    if metal_override:
        tech = _deep_merge(tech, {"layers": dict(metal_override)})
    if tools is None:
        tools = find_tools(proc=emx_opts.get("proc"), verbose=verbose, tech=tech, work=work)
    loc = resolve_work(work=work, ws=ws, lib=lib, tools=tools)
    work_dir, lib_name, libdir = loc["work"], loc["lib"], loc["libdir"]
    if loc.get("refresh"):
        _eprint("已向工作空间 cds.lib 追加 DEFINE %s——请在 Library Manager " "Refresh 可见" % lib_name)
    if cell is None:
        cell = "librf_%s_%s" % (typ, geom_key(typ, geom)[:8].replace("|", ""))
    # 1) layout
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
           "ports_detail": res["ports"], "layers": res["layers"], "views": {}, "warn": None}
    # 2) GDS
    gds_path = None
    if not no_gds:
        gds_path = os.path.join(work_dir, "%s.gds" % cell)
        _assert_writable(gds_path, "GDS 文件")
        on_step("导出 GDS ...")
        out["gds"] = run_strmout(lib_name, cell, gds_path, work_dir, tools,
                                 verbose=verbose, timeout=st, tech=tech)
    # 3) EMX
    freqs = parse_freq_spec(freq)
    if not no_emx and not no_gds and not freqs:
        # 空频点会挂起 EMX；回退 DEF_FREQ 并告警
        freqs = parse_freq_spec(DEF_FREQ)
        _eprint("WARN make_layout_emx: freq 为空，回退 DEF_FREQ=%s" % DEF_FREQ)
    nports = max(2, len(ports))
    if snp_out is None:
        # 默认落在 work/snps/（不再写 work/../snps 逃出 work 领地）
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
        # 仅出图：不跑 EMX/views/指纹，GDS 仍产出供渲染目视
        out["kind"] = _kind_of(typ, geom, ports)
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
    # 4) 指纹
    on_step("提取指纹 ...")
    kind = _kind_of(typ, geom, ports)
    out["kind"] = kind
    out["fingerprint"] = fingerprint_from_snp(out["snp"], kind, ports=ports)
    # 5) views（EMX create-view 优先）
    if not no_views:
        on_step("生成 schematic/symbol ...")
        out["views"] = run_views(cell, out["snp"], ports, kind, lib_name,
                                 libdir, work_dir, tools, verbose=verbose,
                                 timeout=st, views_mode=views_mode)
    return out
# ---- GeomRunner：优化循环内的 版图+EM 前置步骤 ----
_STOP_REQUESTED = threading.Event()
_ACTIVE_RUNNERS = set()
_ACTIVE_LOCK = threading.Lock()
def stop_all():
    """请求所有在跑的 make/EM 子进程结束（子进程 run_cmd 已带 kill）"""
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
    """把「几何 -> 版图 -> EMX -> sNp」包在 runner.run() 前面。"""
    def __init__(self, cfg, delegate, mock=None):
        self.cfg = cfg
        self.delegate = delegate
        le = cfg.get("layout_em") or {}
        self.le = le
        self.geom_names = list(le.get("geom_vars") or [])
        self.fixed = dict(le.get("fixed_geom") or {})
        self.type = le.get("type") or "ind"
        self.cell = le.get("cell") or ("opt_%s" % self.type)
        self.work = le.get("work")
        self.lib = le.get("lib")
        self.views_mode = le.get("views_mode") or "none"
        self.geom_outputs = bool(le.get("geom_outputs", True))
        # mock 模式：合成指纹，不碰 dbAccess/EMX（--mock / --selftest 用）
        if mock is None:
            mock = le.get("mock") or type(delegate).__name__ == "MockRunner"
        self.mock = bool(mock)
        self.snp_path = le.get("snp")
        if not self.snp_path:
            work = os.path.expanduser(self.work or "work")
            self.snp_path = os.path.join(work, "snps", "%s.s2p" % self.cell)
        self.snp_path = os.path.abspath(os.path.expanduser(self.snp_path))
        # 铁律：固定 sNp 每轮刷新；指向已有非本工具产物时拒绝——不覆盖用户既有文件。
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
            # 不吞 traceback：写进 work/ 日志，报错里给出路径（E1）
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
        # nport dataFile 防御性改写：sNp 非固定路径时改写之（views 已按固定路径生成时为无操作）。
        try:
            self._patch_deck()
        except Exception as e:
            return _fail("网表 nport 路径改写失败: %s" % e)
        # 电路部分委托
        sub = self.delegate.run(circ, iter_dir)
        curves = dict(sub.curves or {})
        if fpr and self.geom_outputs and sub.status == "ok":
            self._merge_geom_curves(curves, fpr)
        sub.curves = curves
        sub.wall = time.time() - t0
        return sub
    def _patch_deck(self):
        """确保委托 runner 的网表缓存里 nport 的 file/dataFile 指向固定 sNp。"""
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
            on_step=lambda m: logv(m, self.verbose))
        # 命中过的 snp 按 key 另存（缓存复用），固定路径保持最新；用 sha1 而非 hash()（跨进程不稳）。
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
        """mock 模式：几何 -> 合理量级假指纹（电感量随 dout/n 单调）"""
        dout = float(geom.get("dout", 150) or 150)
        n = float(geom.get("n", 2.5) or 2.5)
        l0 = 1.2e-9 * (dout / 150.0) ** 1.6 * (n / 2.5) ** 1.8
        srf = 45e9 / max(0.5, (n / 2.5)) * (150.0 / max(10.0, dout)) ** 0.5
        q0 = 8.0 + 2.0 * (dout / 150.0)
        fpr = {"L0": l0, "SRF": srf, "Q": {"1e9": q0},
               "Lcurve": [[1e9, l0], [5e9, l0 * 0.97], [10e9, l0 * 0.9]],
               "Qcurve": [[1e9, q0], [5e9, q0 * 1.2], [10e9, q0 * 1.1]]}
        # mock 也要发布 sNp 副本（下游 dataFile 改写路径可用）
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
    """standalone 自测时的 SimResult 替身（optimizer 在场时不用）"""
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
# ---- EMX 设置（对应 EMX GUI/emxform 核心字段，profile JSON 保存/载入） ----
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
        # 铁律：settings 属用户数据 -> 改动前必 .bak（S2b）
        if os.path.isfile(path):
            backup_file(path)
        atomic_write_json(path, settings, backup=False)
        return True, path
    except (IOError, OSError, LibrfError) as e:
        return False, str(e)
def emx_settings_to_opts(st, freq_override=None):
    """嵌套 settings -> make_layout_emx 的 emx_opts dict"""
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
    # 差分模式并进 mode（make_layout_emx 的 mode 参数）
    modes = por.get("modes") or {}
    for pair, mode in modes.items():
        if mode == "diff":
            opts["mode"] = pair
            break
    return opts
def emx_cmd_preview(st, gds="/path/cell.gds", struct="cell", proc=None, out_snp="/path/cell.s2p"):
    """给 GUI 展示「等效 emx 命令行」（保留 forms 的务实解）"""
    tools = find_tools()
    opts = emx_settings_to_opts(st)
    freqs = parse_freq_spec(opts.get("freq"))
    # 预览允许 proc 尚未探查到：用占位符展示命令形状
    proc = proc or tools["proc"]["path"] or "/path/to/process.proc"
    emx_bin = (tools.get("emx") or {}).get("path") or "emx"
    cmd = build_emx_cmd(emx_bin, gds, struct, proc, freqs, out_snp,
                        edge=opts.get("edge"), three_d=opts.get("3d"),
                        mode=opts.get("mode"), ports=None, extra=opts.get("extra"),
                        s_impedance=opts.get("s_impedance"))
    return " ".join(cmd)
# ---- GUI：register(gui) -> 「版图 EM」页（子页：版图生成 / EM 仿真 / EM 优化迭代） ----
PRESETS = [ {"name": "1nH 八角电感", "type": "ind", "freq": "1e9:30e9:30",
     "geom": {"shape": "octagon", "n": 2.5, "w": 10, "s": 2.6, "dout": 150,
              "ct": 0, "underpass": "direct"}},
    {"name": "2nH 方形电感", "type": "ind", "freq": "1e9:30e9:30",
     "geom": {"shape": "square", "n": 3.5, "w": 12, "s": 2.6, "dout": 220,
              "ct": 0, "underpass": "direct"}}, {"name": "中心抽头电感", "type": "ind", "freq": "1e9:30e9:21",
     "geom": {"shape": "octagon", "n": 2, "w": 10, "s": 2.6, "dout": 160,
              "ct": 1, "underpass": "direct"}}, {"name": "50Ω 微带线", "type": "tl", "freq": "1e9:30e9:21",
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
# 各 type 的几何表单项：(key, 中文标签, 默认值, 数值?)
GEOM_FORM = { "ind": [("shape", "形状 square/octagon", "octagon", False), ("n", "匝数 n", "2.5", True),
            ("w", "线宽 w (µm)", "10", True), ("s", "间距 s (µm)", "2.6", True),
            ("dout", "外径 dout (µm)", "150", True), ("din", "内径 din (µm, 留空=闭合)", "", True),
            ("ct", "中心抽头 ct (0/1)", "0", True), ("ct_dir", "抽头方向 ct_dir (N/S/E/W)", "S", False),
            ("underpass", "underpass direct/route", "direct", False), ("ex", "横向拉长 ex (µm)", "", True),
            ("ey", "纵向拉长 ey (µm)", "", True), ("ind_mark", "IND 标记层 (0/1)", "0", True)],
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
def form_to_geom(typ, raw):
    """表单字符串 dict -> 几何 dict（数值键转 float，空项丢弃）"""
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
    return geom
class _EmOptTab(object):
    """「版图 EM」页签：A 版图生成 / B EM 仿真 / C EM 优化迭代"""
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
        """把目标机上探查到的 proc 路径填进输入框（不覆盖已有输入）。"""
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
        """手动选 proc 文件。"""
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
        self.geom_frame = self.ttk.Frame(f)
        self.geom_frame.pack(fill="x")
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
        # 工艺文件 proc：按路径指定（目标机上的新 proc），可一键探查
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
            self.ttk.Entry(r, textvariable=var, width=12).pack(side="left")
            self.geom_vars[key] = var
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
    def _do_make(self, task):
        if self._busy:
            self._log(self.a_log, "已有任务在跑")
            return
        try:
            geom = self._collect_geom()
        except LibrfError as e:
            self._log(self.a_log, "几何错误: %s" % e)
            return
        opts = self._emx_opts_from_b()
        # 仅出图=layout+GDS（渲染需要），不出 EMX/views；full=layout+GDS+EMX+views。
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
            fpr = payload.get("fingerprint") or {}
            self._log(self.a_log, "sNp: %s  L0=%s H  SRF=%s"
                      % (payload["snp"], fmt_eng(fpr.get("L0")), fmt_eng(fpr.get("SRF"))))
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
            ports = None
            meta_path = os.path.join(loc["work"], "%s.meta.json" % cell)
            if os.path.isfile(meta_path):
                try:
                    meta = read_json(meta_path)
                    ports = meta.get("ports")
                except Exception:
                    pass
            nports = max(2, len(ports or []))
            out_snp = os.path.join(loc["work"], "snps", "%s.s%dp" % (cell, nports))
            res = run_emx(gds, cell, tools["proc"]["path"], freqs, out_snp,
                          tools, edge=_opt_num(self.b_edge.get()),
                          three_d=self.b_3d.get().strip() or None,
                          mode=self.b_mode.get().strip() or None,
                          ports=ports, timeout=DEF_EMX_TIMEOUT)
            kind = "ct" if (ports and any(str(p).lower().startswith("ct")
                                          for p in ports)) else "ind"
            fpr = fingerprint_from_snp(res["out_snp"], kind, ports=ports)
            return {"snp": res["out_snp"], "cmd": res["cmd"],
                    "fingerprint": fpr, "warn": res.get("warn")}
        self._log(self.b_log, "EM 仿真开始 ...")
        self._run_bg(worker, {}, self.b_log, self._emx_done)
    def _emx_done(self, payload):
        if payload.get("_error"):
            self._log(self.b_log, "失败: %s" % payload["_error"])
            return
        fpr = payload.get("fingerprint") or {}
        self._log(self.b_log, "完成 sNp: %s" % payload.get("snp"))
        self._log(self.b_log, "L0=%s H  SRF=%s" % (fmt_eng(fpr.get("L0")), fmt_eng(fpr.get("SRF"))))
        if payload.get("warn"):
            self._log(self.b_log, "WARN %s" % payload["warn"])
        if self.curve:
            self._plot_fprint(fpr)
    def _plot_fprint(self, fpr):
        if not self.curve or not fpr:
            return
        lc = fpr.get("Lcurve") or []
        qc = fpr.get("Qcurve") or []
        series = []
        if lc:
            series.append(("L(f) H", ([p[0] for p in lc], [p[1] for p in lc]), "#2266cc"))
        if qc:
            series.append(("Q(f)", ([p[0] for p in qc], [p[1] for p in qc]), "#22aa44"))
        srf = fpr.get("SRF")
        try:
            self.curve.set_series(series, srf=srf)
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
        for fn in sorted(os.listdir(d)):
            if fn.startswith(cell) and fn.endswith(("s2p", "s3p", "s4p", "s5p")):
                try:
                    fpr = fingerprint_from_snp(os.path.join(d, fn))
                    self._log(self.b_log, "%s: L0=%s SRF=%s"
                              % (fn, fmt_eng(fpr.get("L0")), fmt_eng(fpr.get("SRF"))))
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
        """C 页 -> (layout_em_cfg, geom_vars)（供 _collect_cfg 挂钩注入）"""
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
        # _EmHook.collect 已把几何变量并入 cfg["variables"] 并附 layout_em
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
    """轻量 L/Q 曲线画布（纯 tk，沿用 optimizer PlotCanvas 风格）"""
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
    """曲线健康判读（精简版 v03 health_judge）：[(level, text), ...]"""
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
    """挂在 OptimizerGui 上的挂钩（页签 + cfg 注入 + 队列分发）"""
    def __init__(self, gui, tab):
        self.gui = gui
        self.tab = tab
        orig_collect = gui._collect_cfg
        def collect(validate=True):
            cfg = orig_collect(validate=validate)
            if cfg is not None:
                try:
                    le, geom_vars = tab._layout_em_cfg()
                    # 「启用」勾选且有几何变量才注入；否则与 v05 行为一致
                    if le.get("enabled") and geom_vars:
                        exist = {v.get("name") for v in cfg.get("variables") or []}
                        merged = list(cfg.get("variables") or [])
                        for g in geom_vars:
                            if g["name"] not in exist:
                                merged.append({ "name": g["name"], "init": g["init"],
                                    "min": g["min"], "max": g["max"]})
                        cfg["variables"] = merged
                        cfg["layout_em"] = le
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
    """optimizer GUI 挂「版图 EM」页。返回 hook（保活）。"""
    from tkinter import ttk
    frame = ttk.Frame(gui.notebook)
    tab = _EmOptTab(gui, frame)
    gui.notebook.add(frame, text=" 版图 EM ")
    hook = _EmHook(gui, tab)
    return hook
# ---- 自测（python librf_opt.py --selftest；无 Cadence 依赖） ----
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
    k1 = geom_key("ind", {"dout": 150.001, "n": 2.5})
    k2 = geom_key("ind", {"n": 2.5, "dout": 150.0001})
    check("geom_key round", k1 == k2)
    st = default_emx_settings()
    opts = emx_settings_to_opts(st)
    check("emx opts freq", "1e9" in opts["freq"] or "1000000000" in opts["freq"]
          or opts["freq"] == DEF_FREQ)
    check("emx opts 3d", opts.get("3d") == "*")
    gds_p = os.path.join(tmp, "a.gds")
    snp_p = os.path.join(tmp, "a.s2p")
    cmd = emx_cmd_preview(st, gds=gds_p, struct="c", out_snp=snp_p)
    check("emx preview order", cmd.index("--format") < cmd.index("--s-file") < cmd.index(gds_p))
    g = form_to_geom("ind", {"shape": "octagon", "n": "2.5", "w": "10",
                             "s": "2.6", "dout": "150", "ct": "0", "underpass": "direct", "din": ""})
    check("form_to_geom", g.get("shape") == "octagon" and g.get("n") == 2.5 and "din" not in g)
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
    key = geom_key("ind", {"n": 2.5, "w": 10, "dout": 150.0},
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
    # 回归：_make_worker 必须接受 **kwargs（曾因收单个 args 位置参数崩溃）
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
    # 目标机 py3.6 + C locale：getpreferredencoding() 是 ASCII，open() 不带 encoding 读中文即崩。
    cjk = (b"STATUS fail\n" b"ERR \xe7\x94\xbb\xe7\x9f\xa9\xe5\xbd\xa2\xe5\xa4\xb1\xe8\xb4\xa5 "
           b"layer=TM\n" b"WARN \xe8\xa2\xab Virtuoso \xe5\x8d\xa0\xe7\x94\xa8\n"
           b"CELL ind_test\n")
    pcjk = os.path.join(tmp, "res_cjk.txt")
    with _native_open(pcjk, "wb") as f:
        f.write(cjk)
    try:
        rcjk = parse_skill_result(pcjk)
        check("skill result 中文不崩", rcjk["cell"] == "ind_test"
              and rcjk["errs"] and "TM" in rcjk["errs"][0], str(rcjk["errs"]))
    except UnicodeDecodeError as e:
        check("skill result 中文不崩", False, str(e))
    sh = os.path.join(tmp, "cjk.sh")
    with _native_open(sh, "wb") as f:
        f.write(b"#!/bin/sh\necho 'ERR \xe7\x94\xbb\xe7\x9f\xa9\xe5\xbd\xa2'\n")
    os.chmod(sh, 0o755)
    try:
        ro = run_cmd([sh])
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
    atomic_write_json(tp, { "techlib": "DEMOTECH",
        "layers": {"spiral": "M6", "underpass": "M5", "route": "M4",
                   "ground": "M4", "via_sp": "V5", "via_un": "V4", "marker": "MARKER"},
        "text": {"M6": "M6TXT"}, "drc": {"min_w": 0.2, "min_area": {"M6": 0.1}},
        "layermap": _lm_real, "proc": _pr_real})
    d1 = load_tech_profile(path=tp, use_cache=False)
    check("tech 自定义 techlib", d1["techlib"] == "DEMOTECH")
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
    check("skill 注入 techlib", 'librfTechLib = "DEMOTECH"' in sk)
    check("skill 注入 DRC", "librfDrc = list(" in sk)
    d2 = gen_driver_il({"type": "ind", "cell": "c_tech", "n": 2.5, "w": 10,
                        "s": 2.5, "dout": 100, "result": os.path.join(tmp, "r.txt"),
                        "libdir": os.path.join(tmp, "L")}, tech=d1)
    check("driver 含 librf.il", "librf.il" in d2)
    check("driver 含工艺注入", 'list("spiral" "M6")' in d2)
    check("driver 含 techLibName", "DEMOTECH" in d2)
    t3 = find_tools(tech=d1, work=tmp)
    check("find_tools layermap=profile", (t3["layermap"]["path"] == _lm_real), str(t3["layermap"]))
    check("find_tools proc=profile", (t3["proc"]["path"] == _pr_real), str(t3["proc"]))
    check("find_tools techlib=profile", t3["techlib"]["path"] == "DEMOTECH")
    # cds.lib 必须追加，不能像旧版那样 open(...,"w") 整个截断
    wdir = os.path.join(tmp, "ws_protect")
    os.makedirs(wdir)
    cds = os.path.join(wdir, "cds.lib")
    orig = ("DEFINE myPDK /project/Tech/myPDK\n" "DEFINE MyLib /project/WS/MyLib\n"
            "DEFINE analog_lib /home/u/libs/analog\n"
            "SOFTINCLUDE /opt/eda/cadence/share/cdssetup/cds.lib\n")
    with _native_open(cds, "w") as fh:
        fh.write(orig)
    note_created(cds)          # 本次会话内允许追加（模拟已登记）
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
    # 非本工具文件 -> 拒绝写入（领地内/外一律拒绝，S1 回归）
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
            note_created(ownf)   # 删不掉就认领（模拟清单恢复）
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
