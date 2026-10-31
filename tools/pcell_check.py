#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCell acceptance check: define once, instantiate in a *fresh* Virtuoso.

Why this exists
---------------
The geometry suites call ``lriI3BuildSym`` directly.  They pass even when the
PCell shell is broken, because the shell (``pcDefinePCell`` + CDF + instance
evaluation) is never exercised.  This check closes that gap:

1. session A loads librf.il/lri_pcell.il and registers the PCell in a scratch
   library;
2. session B starts with *no* SKILL files loaded and only the compiled pcell
   body from the library, instantiates it, changes parameters, and dumps the
   evaluated master's bbox/shapes/terminals/layer names/CDF parameters.

The default parameter set is the one in lri_pcell.il:

    rx=80 ry=60 n=3 w=8 s=3 chamfer=0.5857864376
    lead_len=20 lead_gap=3 lead_w=8 ct=0 stack=0

Requires a cds.lib that resolves ``--lib`` to a scratch, tech-attached library
(for example PCELL_RS4 assigned to the SMIC PDK).  Run on the Virtuoso host.

    python3 tools/pcell_check.py --lib PCELL_RS4 \
        --cdslib /home/IC/pcell_research/cds.lib \
        --data-dir src/virtuoso_optimizer/data --work work/pcell_check

Exit code 0 = all checks pass.
"""
from __future__ import print_function

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

DEF_VIRTUOSO = os.environ.get("LIBRF_VIRTUOSO",
                              "/opt/eda/cadence/IC618/tools/dfII/bin/virtuoso")
DEF_CXT = os.environ.get(
    "LIBRF_PCELLGEN_CXT",
    "/opt/eda/cadence/IC618/tools/dfII/etc/context/64bit/pCellGen.cxt")
DEF_TECH = "smic55ll_ulp_rf"
DEF_CELL = "pcell_chk_ind"
DEF_TOP = "pcell_chk_top"

# Defaults from lri_pcell.il (must match the CDF table)
DEF_PARAMS = dict(rx=80.0, ry=60.0, n=3, w=8.0, s=3.0,
                  chamfer=0.5857864376, lead_len=20.0, lead_gap=3.0,
                  lead_w=8.0, ct=0, stack=0)

CDF_NAMES = ("rx", "ry", "n", "w", "s", "chamfer", "lead_len", "lead_gap",
             "lead_w", "ct", "stack", "metal_main", "metal_aux")


def _skill_num(v):
    if isinstance(v, bool):
        return "t" if v else "nil"
    if isinstance(v, int):
        return "%d" % v
    t = "%.12g" % float(v)
    if "." not in t and "e" not in t and "E" not in t:
        t += ".0"
    return t


def _q(s):
    return '"%s"' % str(s).replace("\\", "/").replace('"', '\\"')


def _run(virtuoso, cxt, script, cwd, env, log, timeout):
    """Run one batch Virtuoso replay; retry once on the known silent crash."""
    out = os.path.join(os.path.dirname(log), os.path.basename(log) + ".out")
    err = os.path.join(os.path.dirname(log), os.path.basename(log) + ".err")
    for attempt in (1, 2):
        with open(out, "wb") as fo, open(err, "wb") as fe:
            pr = subprocess.run(
                [virtuoso, "-nograph", "-log", log, "-replay", script],
                cwd=cwd, env=env, stdout=fo, stderr=fe, timeout=timeout)
        res = {"rc": pr.returncode, "attempt": attempt,
               "stdout": _read(out), "stderr": _read(err)}
        if pr.returncode == 0 or attempt == 2:
            return res
        print("virtuoso rc=%s, retrying: %s" % (pr.returncode, script),
              file=sys.stderr, flush=True)
    return res


def _read(path, limit=4000):
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()[-limit:]


def _nums(text):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?",
                                         text or "")]


def _strings(text):
    return re.findall(r'"([^"]*)"', text or "")


def _bbox(text):
    v = _nums(text)
    return v[:4] if len(v) >= 4 else None


def _close(got, want, tol=1e-3):
    return got is not None and len(got) == len(want) and \
        all(abs(a - b) <= tol for a, b in zip(got, want))


def _define_il(lib, cell, work, tech, cxt):
    return """(errset (loadContext %(cxt)s) nil)
(let ((out (outfile %(out)s)))
  (fprintf out "load_librf=%%L\\n" (car (errset (load %(librf)s) t)))
  (fprintf out "load_pcell=%%L\\n" (car (errset (load %(pcell)s) t)))
  (fprintf out "tech=%%L\\n" (car (errset (lriPcellTechLoad %(tech)s) t)))
  (when (ddGetObj %(lib)s %(cell)s)
    (errset (ddDeleteObj (ddGetObj %(lib)s %(cell)s)) t))
  (fprintf out "define=%%L\\n"
           (car (errset (lriPcellDefine %(lib)s %(cell)s) t)))
  (fprintf out "== define done ==\\n")
  (close out))
(exit 0)
""" % dict(cxt=_q(cxt), out=_q(os.path.join(work, "define.out")),
           librf=_q(os.path.join(work, "librf.il")),
           pcell=_q(os.path.join(work, "lri_pcell.il")),
           tech=_q(tech), lib=_q(lib), cell=_q(cell))


def _fresh_il(lib, cell, top, work, cxt, explicit):
    params = " ".join("(list \"%s\" \"%s\" %s)" % (
        k, "int" if isinstance(v, int) else "float", _skill_num(v))
        for k, v in explicit.items())
    return """(errset (loadContext %(cxt)s) nil)
(let ((out (outfile %(out)s)))
  (fprintf out "fresh_before_kernel=%%L\\n" (fboundp 'lriI3BuildSym))
  (when (ddGetObj %(lib)s %(top)s)
    (errset (ddDeleteObj (ddGetObj %(lib)s %(top)s)) t))
  (let ((cv (dbOpenCellViewByType %(lib)s %(top)s "layout" "maskLayout" "w")))
    (when cv
      (let ((i (car (errset (dbCreateParamInstByMasterName cv %(lib)s %(cell)s
                 "layout" "I0" list(0.0 0.0) "R0" 1 nil) t))))
        (fprintf out "default_inst=%%L\\n" i)
        (fprintf out "default_bbox=%%L\\n" (car (errset i~>master~>bBox t)))
        (fprintf out "default_nshp=%%L\\n" (car (errset length(i~>master~>shapes) t)))
        (fprintf out "default_nterm=%%L\\n" (car (errset length(i~>master~>terminals) t)))
        (let ((seen nil))
          (foreach sh (car (errset i~>master~>shapes t))
            (let ((ln (car (errset sh~>layerName t))))
              (unless (member ln seen) (setq seen (cons ln seen)))))
          (fprintf out "default_layers=%%L\\n" seen)))
      (let ((i2 (car (errset (dbCreateParamInstByMasterName cv %(lib)s %(cell)s
                 "layout" "I1" list(200.0 0.0) "R0" 1
                 (list %(params)s)) t))))
        (fprintf out "explicit_inst=%%L\\n" i2)
        (fprintf out "explicit_bbox=%%L\\n" (car (errset i2~>master~>bBox t)))
        (fprintf out "explicit_nshp=%%L\\n" (car (errset length(i2~>master~>shapes) t)))
        (when i2
          (let ((r (car (errset (dbReplaceInstParamList i2
                     (list (list "rx" "float" 70.0)
                           (list "n" "int" 3))) t))))
            (fprintf out "change_ret=%%L\\n" r)
            (fprintf out "change_bbox=%%L\\n" (car (errset i2~>master~>bBox t)))
            (fprintf out "change_nshp=%%L\\n" (car (errset length(i2~>master~>shapes) t))))))
      (let ((cdf (car (errset (cdfGetBaseCellCDF (ddGetObj %(lib)s %(cell)s)) t))))
        (fprintf out "cdf_params=%%L\\n"
                 (and cdf (mapcar (lambda (p) (car (errset p~>name t))) cdf~>parameters))))
      (dbSave cv) (dbClose cv)))
  (fprintf out "fresh_after_kernel=%%L\\n" (fboundp 'lriI3BuildSym))
  (fprintf out "== fresh done ==\\n")
  (close out))
(exit 0)
""" % dict(cxt=_q(cxt), out=_q(os.path.join(work, "fresh.out")),
           lib=_q(lib), cell=_q(cell), top=_q(top), params=params)


def _parse(path):
    d = {}
    if not os.path.isfile(path):
        return d
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            if "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="PCell fresh-session acceptance check")
    ap.add_argument("--lib", required=True,
                    help="scratch tech-attached library resolved by --cdslib")
    ap.add_argument("--cdslib", required=True, help="cds.lib used for the session")
    ap.add_argument("--data-dir", default=os.path.join(
        here, "src", "virtuoso_optimizer", "data"),
        help="directory containing librf.il / lri_pcell.il / tech")
    ap.add_argument("--work", default=os.path.join(here, "work", "pcell_check"))
    ap.add_argument("--tech", default=DEF_TECH)
    ap.add_argument("--cell", default=DEF_CELL)
    ap.add_argument("--top", default=DEF_TOP)
    ap.add_argument("--virtuoso", default=DEF_VIRTUOSO)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--keep", action="store_true",
                    help="keep generated .il/out files and test cells")
    a = ap.parse_args(argv)

    data = os.path.abspath(a.data_dir)
    work = os.path.abspath(a.work)
    cdslib = os.path.abspath(a.cdslib)
    for fn in ("librf.il", "lri_pcell.il"):
        if not os.path.isfile(os.path.join(data, fn)):
            ap.error("missing %s in --data-dir" % fn)
    if not os.path.isfile(cdslib):
        ap.error("missing --cdslib")
    if not os.path.isdir(os.path.join(data, "tech")):
        ap.error("missing tech/ in --data-dir")

    os.makedirs(work, exist_ok=True)
    for fn in ("librf.il", "lri_pcell.il"):
        shutil.copy2(os.path.join(data, fn), os.path.join(work, fn))
    techdir = os.path.join(work, "tech")
    if os.path.isdir(techdir):
        shutil.rmtree(techdir)
    os.makedirs(techdir)
    for fn in os.listdir(os.path.join(data, "tech")):
        if fn.endswith(".il"):
            shutil.copy2(os.path.join(data, "tech", fn),
                         os.path.join(techdir, fn))

    # cds.lib may use relative paths; start in its directory and point at data via LRI_*.
    cwd = os.path.dirname(cdslib)
    env = dict(os.environ)
    env["LRI_DATA_DIR"] = work
    env["LRI_TECH_DIR"] = techdir

    d_il = os.path.join(work, "define.il")
    f_il = os.path.join(work, "fresh.il")
    with open(d_il, "w", encoding="utf-8") as f:
        f.write(_define_il(a.lib, a.cell, work, a.tech, DEF_CXT))
    explicit = dict(rx=50.0, ry=40.0, n=2, w=6.0, s=3.0)
    with open(f_il, "w", encoding="utf-8") as f:
        f.write(_fresh_il(a.lib, a.cell, a.top, work, DEF_CXT, explicit))

    r1 = _run(a.virtuoso, DEF_CXT, d_il, cwd, env,
              os.path.join(work, "define.log"), a.timeout)
    d = _parse(os.path.join(work, "define.out"))
    checks = []

    def chk(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    chk("define: librf loaded", d.get("load_librf") == "t",
        "load_librf=%s" % d.get("load_librf"))
    chk("define: lri_pcell loaded", d.get("load_pcell") == "t",
        "load_pcell=%s" % d.get("load_pcell"))
    chk("define: pcell registered",
        "registered" in (d.get("define") or ""), d.get("define"))
    ok_define = all(c["ok"] for c in checks)

    r2 = _run(a.virtuoso, DEF_CXT, f_il, cwd, env,
              os.path.join(work, "fresh.log"), a.timeout)
    f = _parse(os.path.join(work, "fresh.out"))
    chk("fresh: kernel absent before evaluation",
        f.get("fresh_before_kernel") == "nil", f.get("fresh_before_kernel"))
    chk("fresh: instance created", "db:" in (f.get("default_inst") or ""),
        f.get("default_inst"))
    bb = _bbox(f.get("default_bbox"))
    chk("fresh: default bbox", _close(bb, (-80.0, -60.0, 80.0, 76.0)),
        f.get("default_bbox"))
    chk("fresh: two terminals", f.get("default_nterm") == "2",
        f.get("default_nterm"))
    lay = _strings(f.get("default_layers"))
    chk("fresh: SMIC layers", set(("TM2", "TM1", "TV2", "MnTXT")) <= set(lay),
        f.get("default_layers"))
    xb = _bbox(f.get("explicit_bbox"))
    chk("fresh: explicit params bbox", _close(xb, (-50.0, -40.0, 50.0, 57.0)),
        f.get("explicit_bbox"))
    cb = _bbox(f.get("change_bbox"))
    chk("fresh: param change rebuilds", f.get("change_ret") == "t"
        and _close(cb, (-70.0, -40.0, 70.0, 57.0)), f.get("change_bbox"))
    cdf = _strings(f.get("cdf_params"))
    chk("fresh: CDF params present", set(CDF_NAMES) <= set(cdf),
        f.get("cdf_params"))
    chk("fresh: kernel loaded by pcell body",
        f.get("fresh_after_kernel") not in (None, "nil"),
        f.get("fresh_after_kernel"))

    ok = ok_define and all(c["ok"] for c in checks)
    out = {"ok": ok, "lib": a.lib, "cell": a.cell, "top": a.top,
           "tech": a.tech, "checks": checks,
           "define_rc": r1["rc"], "fresh_rc": r2["rc"],
           "work": work}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if ok and not a.keep:
        # Remove the test cells on success; keep everything for debugging on failure.
        cleanup = """(errset (progn
  (when (ddGetObj %(lib)s %(top)s) (ddDeleteObj (ddGetObj %(lib)s %(top)s)))
  (when (ddGetObj %(lib)s %(cell)s) (ddDeleteObj (ddGetObj %(lib)s %(cell)s)))
) t)
(exit 0)
""" % dict(lib=_q(a.lib), top=_q(a.top), cell=_q(a.cell))
        cp = os.path.join(work, "cleanup.il")
        with open(cp, "w", encoding="utf-8") as fh:
            fh.write(cleanup)
        _run(a.virtuoso, DEF_CXT, cp, cwd, env,
             os.path.join(work, "cleanup.log"), a.timeout)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
