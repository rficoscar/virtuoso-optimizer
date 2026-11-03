#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_tech.py - process profile JSON -> SKILL profile + GDS layer map + evaluation config.

One JSON is the single source of truth: the SKILL geometry kernel, the strmout
layer map and the EMX/DRC evaluation config are all derived from it, so a new
process does not require code changes.

    python3 tools/gen_tech.py src/virtuoso_optimizer/data/tech/smic55ll_ulp_rf.json
    python3 tools/gen_tech.py src/virtuoso_optimizer/data/tech/x.json \
        --eval-config config_x.json

Outputs:
    data/tech/<key>_profile.il    sets librfTech / librfStack* / librfDrc after load()
    data/stream/<key>.map         only the layers used by this PCell, for strmout -map
    config_<key>.json             for the evaluation scripts (layers/text/limits/emx/calibre)

See data/tech/smic55ll_ulp_rf.json for the JSON structure. Checks:
  * every metal in stack_order must be defined in metals;
  * each via lower/upper must be an adjacent pair in stack_order;
  * defaults.metal_main must be above metal_aux, with a complete via chain between them.
"""
import argparse
import io
import json
import os
import sys

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "src", "virtuoso_optimizer", "data")


def load(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate(d):
    errs = []
    order = d.get("stack_order") or []
    metals = d.get("metals") or {}
    vias = d.get("vias") or {}
    if not order:
        errs.append("stack_order 为空")
    for nm in order:
        if nm not in metals:
            errs.append("metals 缺 %s" % nm)
    idx = {nm: i for i, nm in enumerate(order)}
    step = {}
    for vn, v in vias.items():
        lo, hi = v.get("lower"), v.get("upper")
        if lo not in idx or hi not in idx:
            errs.append("via %s 的 lower/upper (%s/%s) 不在 stack_order"
                        % (vn, lo, hi))
            continue
        if idx[hi] != idx[lo] + 1:
            errs.append("via %s 跨层 %s->%s 不相邻" % (vn, lo, hi))
            continue
        step[(lo, hi)] = vn
    main = (d.get("defaults") or {}).get("metal_main")
    aux = (d.get("defaults") or {}).get("metal_aux")
    if main not in idx or aux not in idx:
        errs.append("defaults.metal_main/metal_aux 未定义")
    elif idx[main] <= idx[aux]:
        errs.append("metal_main(%s) 必须在 metal_aux(%s) 之上" % (main, aux))
    else:
        for k in range(idx[aux], idx[main]):
            if (order[k], order[k + 1]) not in step:
                errs.append("通孔链断在 %s->%s" % (order[k], order[k + 1]))
    return errs


def _num(x):
    """SKILL numeric literal: integers need a decimal point to avoid sprintf %f issues."""
    if isinstance(x, bool):
        return "t" if x else "nil"
    if isinstance(x, int):
        return "%d.0" % x
    return repr(float(x))


def _q(s):
    return '"%s"' % str(s).replace('\\', '\\\\').replace('"', '\\"')


def _via_by_pair(d):
    return {(v["lower"], v["upper"]): (vn, v) for vn, v in d["vias"].items()}


def emit_il(d, src_name):
    order = d["stack_order"]
    metals, vias = d["metals"], d["vias"]
    idx = {nm: i for i, nm in enumerate(order)}
    pair = _via_by_pair(d)
    main = d["defaults"]["metal_main"]
    aux = d["defaults"]["metal_aux"]
    route = order[idx[aux] - 1] if idx[aux] > 0 else aux
    vmain = pair[(aux, main)][0]
    vroute = pair[(route, aux)][0] if (route, aux) in pair else vmain
    drc = d.get("drc") or {}
    v_main = vias[vmain]
    v_rt = vias.get(vroute, v_main)

    L = []
    w = L.append
    w("; auto-generated, do not edit — source tech/%s, generator tools/gen_tech.py" % src_name)
    w("; overrides librf.il built-in defaults when loaded (SMIC55 values are the factory default).")
    w("setq(librfTechName %s)" % _q(d.get("name", d["key"])))
    w("setq(librfTechLib %s)" % _q(d.get("techlib", d.get("name", d["key"]))))
    w("setq(librfTechKey %s)" % _q(d["key"]))
    w("")
    w("setq(librfStackMetals")
    w("  list(%s))" % " ".join(_q(nm) for nm in order))
    w("setq(librfStackVias")
    w("  list(")
    rows = []
    for nm in order[:-1]:
        if (nm, order[idx[nm] + 1]) not in pair:
            continue
        vn, v = pair[(nm, order[idx[nm] + 1])]
        rows.append('    list(%s %s %s %s %s %s %s)'
                    % (_q(vn), _q(v["lower"]), _q(v["upper"]),
                       _num(v["cut"]), _num(v["space"]),
                       _num(v["enc_lo"]), _num(v["enc_hi"])))
    w("\n".join(rows) + "))")
    w("setq(librfMetalGds")
    w("  list(%s))" % " ".join(
        "list(%s %d %d)" % (_q(nm), metals[nm]["gds"][0], metals[nm]["gds"][1])
        for nm in order))
    w("setq(librfViaGds")
    w("  list(%s))" % " ".join(
        "list(%s %d %d)" % (_q(vn), v["gds"][0], v["gds"][1])
        for vn, v in vias.items()))
    w("")
    w("; role table: spiral/underpass = main/aux metal, route = the layer below aux.")
    w("setq(librfTech")
    w("  list(")
    w("    list(\"spiral\" %s) list(\"underpass\" %s)" % (_q(main), _q(aux)))
    w("    list(\"route\" %s) list(\"ground\" %s)" % (_q(route), _q(route)))
    w("    list(\"via_sp\" %s) list(\"via_un\" %s)" % (_q(vmain), _q(vroute)))
    w("    list(\"via_st\" %s)" % _q(vroute))
    w("    list(\"marker\" %s)))" % _q(d["marker"]["oa"][0]))
    w("setq(librfTechText")
    w("  list(")
    w("    " + "\n    ".join(
        'list(%s list(%s %s))' % (_q(nm), _q(metals[nm]["text_oa"][0]),
                                  _q(metals[nm]["text_oa"][1]))
        for nm in order) + "))")
    w("")
    enc_main = drc.get("via_enclosure", max(v_main["enc_lo"], v_main["enc_hi"]))
    w("setq(librfDrc")
    w("  list(")
    w("    list(\"min_w\" %s) list(\"max_w\" %s) list(\"min_s\" %s)"
      % (_num(drc.get("min_w", 1.0)), _num(drc.get("max_w", 30.0)),
         _num(drc.get("min_s", 1.0))))
    w("    list(\"via\" list(list(\"cut\" %s) list(\"spacing\" %s)"
      " list(\"enclosure\" %s)))"
      % (_num(v_main["cut"]), _num(v_main["space"]), _num(enc_main)))
    w("    list(\"via_st\" list(list(\"cut\" %s) list(\"spacing\" %s)"
      " list(\"enclosure\" %s)))"
      % (_num(v_rt["cut"]),
         _num(drc.get("via_st_space", v_rt["space"])),
         _num(drc.get("via_st_enclosure", v_rt["enc_lo"]))))
    w("    list(\"min_area\"")
    w("      list(")
    for nm in order:
        w("        list(%s %s)" % (_q(nm), _num(metals[nm].get("min_area", 0.0))))
    w("      )")
    w("    )")
    w("    list(\"wide_space\"")
    w("      list(")
    for nm in order:
        ws = metals[nm].get("wide_space",
                            metals[nm].get("min_s", drc.get("min_s", 1.0)))
        w("        list(%s %s)" % (_q(nm), _num(ws)))
    w("      )")
    w("    )")
    w("  )")
    w(")")
    w("")
    w("t")
    return "\n".join(L) + "\n"


def _balance(text):
    """Bracket balance outside strings and ; line comments; used to catch generation errors before writing."""
    depth = 0
    in_str = False
    in_com = False
    esc = False
    for ch in text:
        if in_com:
            if ch == "\n":
                in_com = False
            continue
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == ";":
            in_com = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth


def emit_map(d):
    order = d["stack_order"]
    metals = d["metals"]
    L = ["; auto-generated, do not edit — source tech/%s.json, generator tools/gen_tech.py"
         % d["key"],
         "; only layers used by this PCell; usable directly with strmout -map."]
    for nm in order:
        m = metals[nm]
        L.append("%s drawing %d %d" % (m["oa"], m["gds"][0], m["gds"][1]))
    for vn, v in d["vias"].items():
        L.append("%s drawing %d %d" % (v["oa"], v["gds"][0], v["gds"][1]))
    for nm in order:
        m = metals[nm]
        L.append("%s %s %d %d" % (m["text_oa"][0], m["text_oa"][1],
                                  m["text_gds"][0], m["text_gds"][1]))
    mk = d["marker"]
    L.append("%s %s %d %d" % (mk["oa"][0], mk["oa"][1],
                              mk["gds"][0], mk["gds"][1]))
    return "\n".join(L) + "\n"


def emit_eval(d):
    order = d["stack_order"]
    metals, vias = d["metals"], d["vias"]
    idx = {nm: i for i, nm in enumerate(order)}
    pair = _via_by_pair(d)
    main = d["defaults"]["metal_main"]
    aux = d["defaults"]["metal_aux"]
    route = order[idx[aux] - 1] if idx[aux] > 0 else aux
    vmain = pair[(aux, main)][0]
    vroute = pair[(route, aux)][0] if (route, aux) in pair else vmain
    drc = d.get("drc") or {}
    cfg = {
        "_doc": "由 tech/%s.json 生成，勿手改（tools/gen_tech.py）" % d["key"],
        "tech": d.get("name", d["key"]),
        "pdk": d.get("pdk", ""),
        "emx": d.get("emx") or {},
        "calibre": {"bin": "", "turbo": 8},
        "layers": {
            "spiral": metals[main]["gds"],
            "under": metals[aux]["gds"],
            "route": metals[route]["gds"],
            "via": vias[vmain]["gds"],
            "via_st": vias[vroute]["gds"],
        },
        "text": {
            "spiral": metals[main]["text_gds"],
            "under": metals[aux]["text_gds"],
            "route": metals[route]["text_gds"],
            "m1": metals[order[0]]["text_gds"],
        },
        "limits": {
            "min_w": drc.get("min_w", 0.4),
            "min_s": drc.get("min_s", 0.5),
            "via_cut": vias[vmain]["cut"],
            "via_space": vias[vmain]["space"],
            "via_enc": drc.get("via_enclosure",
                               max(vias[vmain]["enc_lo"], vias[vmain]["enc_hi"])),
            "bbox_tol": 0.5,
            "sym_tol": 0.05,
            "lead_match": 1.0,
        },
        "expect": {"ports": ["p01", "p02"]},
        # Layer role table: the consumer re-parses metal_main/metal_aux per case,
        # so non-default metal pairs also resolve the correct layers and envelope limits.
        "defaults": {"metal_main": main, "metal_aux": aux},
        "stack": {
            "order": list(order),
            "defaults": {"metal_main": main, "metal_aux": aux},
            "metals": {nm: {"gds": metals[nm]["gds"],
                            "text_gds": metals[nm]["text_gds"],
                            "wide_space": metals[nm].get(
                                "wide_space",
                                metals[nm].get("min_s", drc.get("min_s", 1.0)))}
                       for nm in order},
            "vias": {vn: {"gds": v["gds"], "lower": v["lower"],
                          "upper": v["upper"], "cut": v["cut"],
                          "space": v["space"], "enc_lo": v["enc_lo"],
                          "enc_hi": v["enc_hi"]}
                     for vn, v in vias.items()},
        },
    }
    ecfg = cfg["emx"]
    for k in ("bin", "proc_glob", "stack", "freqs", "f0", "edge", "three_d",
              "label_snap"):
        if k in (d.get("emx") or {}):
            ecfg[k] = d["emx"][k]
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description="Tech profile JSON -> SKILL / layermap / eval config")
    ap.add_argument("tech_json")
    ap.add_argument("--out-dir", default=os.path.join(DATA, "tech"))
    ap.add_argument("--map-dir", default=os.path.join(DATA, "stream"))
    ap.add_argument("--eval-config", default=None,
                    help="write an eval config (omitted by default)")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    a = ap.parse_args(argv)
    d = load(a.tech_json)
    errs = validate(d)
    if errs:
        for e in errs:
            print("ERR %s" % e, file=sys.stderr)
        return 2
    if a.check:
        print("%s: ok (%d metals, %d vias)"
              % (d["key"], len(d["stack_order"]), len(d["vias"])))
        return 0
    key = d["key"]
    src = os.path.basename(a.tech_json)
    outs = []
    if a.out_dir:
        p = os.path.join(a.out_dir, key + "_profile.il")
        il = emit_il(d, src)
        bal = _balance(il)
        if bal != 0:
            print("ERR 生成的 SKILL 括号不平衡（%+d）: %s" % (bal, p),
                  file=sys.stderr)
            return 3
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(il)
        outs.append(p)
    if a.map_dir:
        p = os.path.join(a.map_dir, key + ".map")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(emit_map(d))
        outs.append(p)
    if a.eval_config:
        with io.open(a.eval_config, "w", encoding="utf-8") as f:
            f.write(emit_eval(d))
        outs.append(a.eval_config)
    for p in outs:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
