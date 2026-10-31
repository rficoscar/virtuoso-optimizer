# -*- coding: utf-8 -*-
"""lri.selftest_librf — librf_opt 自测（python librf_opt.py --selftest）。"""
from __future__ import print_function

import math
import os
import re
import sys

from .common import (
    _native_open, open, _HERE, TEST_FIXTURES, DEF_FREQ, DEFAULT_TECH, LibrfError, note_created,
    safe_write_text, atomic_write_json, read_json, parse_eng, fmt_eng, run_cmd, _probe_data_file)
from .tech import (
    load_tech_profile, _tech_deep_merge, build_tech, require_tech_ready, tech_layer, tech_text,
    tech_drc, tech_to_skill)
from .tools import (
    find_tools)
from .skillio import (
    skill_literal, gen_driver_il, gen_driver_views_emx_il, parse_skill_result, gen_cds_lib)
from .emx import (
    parse_freq_spec)
from .snp import (
    read_snp, write_snp, ensure_snp_header, _cpl_port_indices, extract_fingerprint,
    fprint_summary)
from .device import (
    geom_key, default_cell_name, _kind_of, write_device_meta, load_device_meta,
    fingerprint_kind_for_device, make_layout_emx)
from .runner import (
    _STOP_REQUESTED, GeomRunner, _FallbackSimResult, default_emx_settings, load_emx_settings,
    save_emx_settings, emx_settings_to_opts, emx_cmd_preview)
from .layout import (
    lri_norm_geom, lri_validate, lri_build_ind, form_to_geom)
from .ui_em import (
    _EmOptTab, _health_judge)

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
    _fx = TEST_FIXTURES
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
