# -*- coding: utf-8 -*-
"""lri.tools — EDA 工具探测（virtuoso/strmout/emx/calibre）。"""
from __future__ import print_function

import os

from .common import (
    _HERE, DEF_WS, LAYERMAP_NAMES, PROC_NAMES, TECHLIB_SUBDIRS, LibrfError, logv, _env_path,
    _which, _probe_data_file)
from .tech import (
    load_tech_profile, _techlib_name)

def _import_discover():
    try:
        from .. import cli as _opt
        return _opt.find_prog, _opt.find_file
    except Exception:
        return None, None

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
