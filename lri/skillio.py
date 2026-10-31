# -*- coding: utf-8 -*-
"""lri.skillio — SKILL 驱动生成、结果解析、视图与 strmout/cds.lib。"""
from __future__ import print_function

import os

from .common import (
    open, SKILL_IL, DEF_LIB, DEF_WS, DEF_SKILL_TIMEOUT, DEF_PORT_LAYER, LibrfError, _eprint,
    logv, _env_path, note_created, backup_file, _assert_writable, safe_write_text,
    append_unique, run_cmd, _probe_data_file)
from .tech import (
    load_tech_profile, tech_to_skill, _techlib_name)
from .tools import (
    require_tool)
from .snp import (
    read_snp)

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
