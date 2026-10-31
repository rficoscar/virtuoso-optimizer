# -*- coding: utf-8 -*-
"""lri.device — 器件元数据与 make_layout_emx 主流程。"""
from __future__ import print_function

import json
import os
import re

from .common import (
    DEF_FREQ, DEF_EMX_TIMEOUT, DEF_SKILL_TIMEOUT, LibrfError, _eprint, _assert_writable,
    safe_write_text, _assert_cell_writable, _mark_cell_owned, atomic_write_json, read_json)
from .tech import (
    _deep_merge, load_tech_profile, _merge_tools_into_tech, require_tech_ready)
from .tools import (
    find_tools)
from .skillio import (
    raise_if_locked, gen_driver_il, parse_skill_result, resolve_work, run_dbaccess,
    run_strmout, run_views)
from .emx import (
    parse_freq_spec, run_emx)
from .snp import (
    fingerprint_from_snp)
from .layout import (
    lri_norm_geom, lri_validate)

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
