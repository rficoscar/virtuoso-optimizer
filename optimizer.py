VERSION = "1.6.1"
import argparse
import csv
import glob
import gzip
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import zlib
import builtins
_native_open = builtins.open

_AUDIT = []
_AUDIT_SINK = None
_AUDIT_FILE = None

def audit_sink(fn):
    global _AUDIT_SINK
    _AUDIT_SINK = fn

def audit_file(path):
    global _AUDIT_FILE
    _AUDIT_FILE = path

def audit_dump():
    return list(_AUDIT)

def audit(kind, path=None, detail=""):
    if path:
        try:
            path = os.path.abspath(os.path.expanduser(str(path)))
        except Exception:
            path = str(path)
    rec = {"ts": time.strftime("%H:%M:%S"), "kind": kind,
           "path": path or "", "detail": str(detail or "")}
    _AUDIT.append(rec)
    if _AUDIT_FILE:
        try:
            with _native_open(_AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
    if _AUDIT_SINK:
        try:
            _AUDIT_SINK(rec)
        except Exception:
            pass
    return rec

def open(file, mode="r", *args, **kwargs):  # noqa: A001 - 有意覆盖内建 open
    if "b" not in mode:
        if "encoding" not in kwargs:
            kwargs["encoding"] = "utf-8"
            if not any(c in mode for c in "wxa+"):
                kwargs.setdefault("errors", "replace")
        try:
            if "r" in mode and "+" not in mode:
                kind = "read"
            elif "a" in mode:
                kind = "append"
            elif "x" in mode:
                kind = "create"
            else:
                kind = "write"
            audit(kind, file, "mode=" + mode)
        except Exception:
            pass
    return _native_open(file, mode, *args, **kwargs)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from algorithm import available, create  # noqa: E402
sys.modules.setdefault("optimizer", sys.modules[__name__])
EDA_ROOT_GLOBS = (
    "/opt/eda/*", "/opt/eda/*/*", "/eda/*", "/eda/*/*",
    "/usr/eda/*", "/usr/eda/*/*", "/opt/cadence/*", "/usr/cadence/*",
    "/opt/INTEGRAND*", "/opt/*/INTEGRAND*", "/tools/eda/*", "/tools/eda/*/*",
)

def _eda_root_dirs():
    out = []
    for var in ("CADHOME", "CDS", "CDSDIR", "CDS_INST_DIR", "CDSHOME",
                "CDS_ROOT", "CDSROOT", "SPECTRE_HOME"):
        val = os.environ.get(var)
        if val and os.path.isdir(val):
            out.append(val)
    for pattern in EDA_ROOT_GLOBS:
        for hit in glob.glob(pattern):
            if os.path.isdir(hit):
                out.append(hit)
    seen, uniq = set(), []
    for d in out:
        d = os.path.normpath(d)
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq

def _runnable(path):
    return bool(path) and os.path.isfile(path)

def find_prog(name, env_vars=(), subdirs=(), must_be="file"):
    def _ok(p):
        if not p:
            return False
        if must_be == "dir":
            return os.path.isdir(p)
        return _runnable(p)
    for var in env_vars:
        val = os.environ.get(var)
        if val:
            val = os.path.expanduser(val.strip())
            if _ok(val):
                return val, "env"
            cand = os.path.join(val, name)
            if _ok(cand):
                return cand, "env"
    for root in _eda_root_dirs():
        for sub in subdirs:
            cand = os.path.join(root, sub, name) if sub else os.path.join(root, name)
            if _ok(cand):
                return cand, "hint"
    hit = shutil.which(name)
    if hit and _ok(hit):
        return hit, "which"
    for root in _eda_root_dirs():
        for child in sorted(glob.glob(os.path.join(root, "*"))):
            if not os.path.isdir(child):
                continue
            for sub in subdirs:
                cand = os.path.join(child, sub, name) if sub else os.path.join(child, name)
                if _ok(cand):
                    return cand, "root"
    return None, "none"

def find_file(basename, env_vars=(), extra_dirs=(), exts=("",)):
    def _ok(p):
        return bool(p) and os.path.isfile(p)
    for var in env_vars:
        val = os.environ.get(var)
        if val:
            val = os.path.expanduser(val.strip())
            if _ok(val):
                return val, "env"
            if os.path.isdir(val):
                for ext in exts:
                    cand = os.path.join(val, basename + ext)
                    if _ok(cand):
                        return cand, "env"
    for d in extra_dirs:
        if not d:
            continue
        d = os.path.expanduser(str(d))
        for ext in exts:
            cand = os.path.join(d, basename + ext)
            if _ok(cand):
                return cand, "given"
        if os.path.isdir(d):
            for hit in sorted(glob.glob(os.path.join(d, "*", basename + "*"))):
                if _ok(hit):
                    return hit, "given"
    return None, "none"

def discover_tools():
    tools = {}
    tools["spectre"] = dict(zip(("path", "src"), find_prog(
        "spectre", ("SPECTRE_BIN",),
        ("bin", "tools/bin", "SPECTRE/bin", "spectre/bin"))))
    tools["virtuoso"] = dict(zip(("path", "src"), find_prog(
        "virtuoso", ("VIRTUOSO_BIN",),
        ("bin", "tools/dfII/bin", "tools/bin"))))
    tools["emx"] = dict(zip(("path", "src"), find_prog(
        "emx", ("LIBRF_EMX",), ("bin",))))
    tools["dbaccess"] = dict(zip(("path", "src"), find_prog(
        "dbAccess", ("LIBRF_DBACCESS",),
        ("tools/dfII/bin", "bin"))))
    tools["strmout"] = dict(zip(("path", "src"), find_prog(
        "strmout", ("LIBRF_STRMOUT",),
        ("tools/dfII/bin", "bin"))))
    tools["ocean"] = dict(zip(("path", "src"), find_prog(
        "ocean", ("OCEAN_BIN",), ("bin", "tools/dfII/bin"))))
    for key in ("spectre", "virtuoso", "emx", "dbaccess", "strmout", "ocean"):
        tools[key]["found"] = tools[key]["path"] is not None
    return tools

def tool_report(tools=None):
    tools = tools if tools is not None else discover_tools()
    src_cn = {"env": "环境变量", "hint": "环境目录", "which": "PATH",
              "root": "安装目录扫描", "none": "未找到"}
    lines = []
    for key in ("spectre", "virtuoso", "emx", "dbaccess", "strmout", "ocean"):
        t = tools.get(key) or {}
        tag = src_cn.get(t.get("src"), t.get("src"))
        lines.append("%-9s %s  [%s]" % (key, t.get("path") or "未找到", tag))
    return "\n".join(lines)
_TOOL_CACHE = {}

def resolve_prog(kind, explicit=None):
    if explicit:
        exp = os.path.expanduser(str(explicit).strip())
        if exp and os.path.isfile(exp):
            return exp
        hit = shutil.which(exp) if exp else None
        if hit:
            return hit
    if kind not in _TOOL_CACHE:
        tools = discover_tools()
        for k, v in tools.items():
            _TOOL_CACHE[k] = v
    hit = (_TOOL_CACHE.get(kind) or {}).get("path")
    if hit:
        return hit
    return explicit or kind
_ENG_SUFFIX = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
    "a": 1e-18,
}
_ENG_RE = re.compile(
    r"^\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*([A-Za-z]*)\s*$")

def parse_eng(text):
    if isinstance(text, (int, float)):
        return float(text)
    m = _ENG_RE.match(str(text))
    if not m:
        raise ValueError(f"bad number: {text}")
    value = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix:
        if suffix not in _ENG_SUFFIX:
            raise ValueError(f"bad suffix: {text}")
        value *= _ENG_SUFFIX[suffix]
    return value

def format_eng(value):
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    value = abs(value)
    for scale, suffix in ((1e12, "T"), (1e9, "G"), (1e6, "meg"), (1e3, "k"),
                          (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
                          (1e-12, "p"), (1e-15, "f"), (1e-18, "a")):
        if value >= scale:
            return f"{sign}{value / scale:g}{suffix}"
    return f"{sign}{value:g}"

def suggest_bounds(init_text):
    value = parse_eng(init_text)
    if value > 0:
        return format_eng(0.5 * value), format_eng(2.0 * value)
    if value < 0:
        return format_eng(2.0 * value), format_eng(0.5 * value)
    return "0", ""

def _read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()

def parse_variables(state_dir):
    text = _read_text(os.path.join(state_dir, "variables.state"))
    names = re.findall(r'->name = "([^"]*)"', text)
    exprs = re.findall(r'->expression = "([^"]*)"', text)
    out = []
    for i, name in enumerate(names):
        expr = exprs[i] if i < len(exprs) else ""
        out.append({"name": name, "expression": expr})
    return out

def parse_outputs(state_dir):
    path = os.path.join(state_dir, "outputs.state")
    text = _read_text(path)
    out = []
    for m in re.finditer(r'->name = "([^"]*)"', text):
        name = m.group(1)
        tail = text[m.end():m.end() + 4000]
        em = re.search(r"->expression = ", tail)
        raw = ""
        if em:
            raw = tail[em.end():em.end() + 300].strip()
        out.append({"name": name, "expression_raw": raw})
    return out

def parse_analyses(state_dir):
    path = os.path.join(state_dir, "analyses.state")
    text = _read_text(path)
    items = {}
    pat = re.compile(r'analysis\((\w+) fields (\w+)\) (.*)')
    for line in text.splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        atype, field, value = m.groups()
        items.setdefault(atype, {})[field] = value.strip()
    out = []
    for atype, fields in items.items():
        out.append({"type": atype, "fields": fields})
    return out

def parse_model_files(state_dir):
    path = os.path.join(state_dir, "modelSetup.state")
    if not os.path.exists(path):
        return []
    text = _read_text(path)
    return re.findall(r'"(/[^"]+)"', text)

def summarize(state_dir):
    return {
        "variables": parse_variables(state_dir),
        "outputs": parse_outputs(state_dir),
        "analyses": parse_analyses(state_dir),
        "model_files": parse_model_files(state_dir),
    }
BIG_ERROR = 1e6

class Target:
    def __init__(self, spec):
        self.output = spec["output"]
        self.metric = spec.get("metric", "scalar")
        self.role = spec.get("role", "error")
        if self.role not in ("error", "constraint", "objective"):
            raise ValueError("bad role: %s" % self.role)
        band = spec.get("band")
        self.band = None
        if band is not None:
            if not isinstance(band, (list, tuple)) or len(band) != 2:
                raise ValueError("band must be [lo, hi]: %r" % (band,))
            lo, hi = parse_eng(band[0]), parse_eng(band[1])
            self.band = [min(lo, hi), max(lo, hi)]
        self.op = spec.get("op", ">=")
        self.goal = parse_eng(spec.get("goal", 0))
        self.goal2 = parse_eng(spec["goal2"]) if "goal2" in spec else None
        self.tol = abs(parse_eng(spec.get("tol", 1e-30))) or 1e-30
        self.weight = float(spec.get("weight", 1.0))
        self.freq = parse_eng(spec["freq"]) if "freq" in spec else None
        if self.metric == "value_at" and self.freq is None \
                and not spec.get("curve_op"):
            raise ValueError("value_at target needs a freq")
        if self.metric == "value_at" and self.freq is not None \
                and self.band is not None and not spec.get("curve_op") \
                and not (self.band[0] <= self.freq <= self.band[1]):
            raise ValueError("value_at freq %r is outside band %s"
                             % (spec.get("freq"), self.band))
        self.curve_op = spec.get("curve_op")
        self.limit = spec.get("limit")
        if self.op not in (">=", "<=",
                           "==", "range"):
            raise ValueError("bad op: %s" % self.op)
        if self.op == "range" and self.goal2 is not None \
                and self.goal > self.goal2:
            self.goal, self.goal2 = self.goal2, self.goal
        if self.metric not in ("scalar", "band_min",
                               "band_max", "band_flatness",
                               "band_mean", "value_at"):
            raise ValueError("bad metric: %s" % self.metric)
        if self.curve_op:
            if self.curve_op not in ("above", "below",
                                     "match"):
                raise ValueError("bad curve_op: %s" % self.curve_op)
            lim = self.limit
            if not isinstance(lim, dict):
                raise ValueError("limit must be a dict with x/y lists")
            lxs = lim.get("x")
            lys = lim.get("y")
            if not isinstance(lxs, list) or not isinstance(lys, list) \
                    or len(lxs) != len(lys) or len(lxs) < 2:
                raise ValueError("limit needs equal-length x/y lists (>=2 points)")
            for _v in list(lxs) + list(lys):
                parse_eng(_v)
        if self.op == "range" and self.goal2 is None:
            raise ValueError("range needs goal2")
        self.limit_pts = None
        if self.curve_op:
            self.limit_pts = [(parse_eng(a), parse_eng(b))
                              for a, b in zip(self.limit["x"], self.limit["y"])]

    def _points(self, curves):
        data = get_curve(curves, self.output)
        if data is None:
            return None
        pts = _cut_band(data[0], data[1], self.band)
        return pts or None
    def metric_value(self, curves):
        pts = self._points(curves)
        if pts is None:
            return None
        ys = [p[1] for p in pts]
        if self.metric == "scalar":
            return ys[0]
        if self.metric == "band_min":
            return min(ys)
        if self.metric == "band_max":
            return max(ys)
        if self.metric == "band_flatness":
            return max(ys) - min(ys)
        if self.metric == "band_mean":
            return sum(ys) / len(ys)
        if self.metric == "value_at":
            return _value_at(pts, self.freq)
        raise ValueError(f"bad metric: {self.metric}")

    def error(self, curves):
        if self.curve_op:
            return self._curve_error(curves)
        value = self.metric_value(curves)
        if value is None:
            return BIG_ERROR
        return self._point_error(value)
    def _point_error(self, value):
        if self.op == ">=":
            raw = self.goal - value
        elif self.op == "<=":
            raw = value - self.goal
        elif self.op == "==":
            raw = abs(value - self.goal) - self.tol
        elif self.op == "range":
            if self.goal2 is None:
                raise ValueError("bad op: range needs goal2")
            lo, hi = self.goal, self.goal2
            raw = max(lo - value, value - hi) - self.tol
        else:
            raise ValueError(f"bad op: {self.op}")
        return raw / self.tol
    def _curve_error(self, curves):
        pts = self._points(curves)
        if pts is None:
            return BIG_ERROR
        worst = None
        for xi, yi in pts:
            ly = _value_at(self.limit_pts, xi)
            if ly is None:
                continue
            if self.curve_op == "above":
                raw = ly - yi
            elif self.curve_op == "below":
                raw = yi - ly
            elif self.curve_op == "match":
                raw = abs(yi - ly) - self.tol
            else:
                raise ValueError(f"bad curve_op: {self.curve_op}")
            if worst is None or raw > worst:
                worst = raw
        if worst is None:
            return BIG_ERROR
        return worst / self.tol

def get_curve(curves, name):
    if name in curves:
        return curves[name]
    hits = [k for k in curves if k.startswith(name)]
    if len(hits) == 1:
        return curves[hits[0]]
    return None

def _cut_band(x, y, band):
    if band is None:
        return list(zip(x, y))
    lo, hi = band
    return [(xi, yi) for xi, yi in zip(x, y) if lo <= xi <= hi]

def _value_at(pts, freq):
    if freq is None or not pts:
        return None
    pts = sorted(pts)
    if freq < pts[0][0] or freq > pts[-1][0]:
        return None
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= freq <= x1:
            if x1 == x0:
                return y0
            t = (freq - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return None

class Transform(object):
    def __init__(self, variables, linking=None):
        if not variables:
            raise ValueError("variables must be non-empty")
        self._vars = []
        self._by_name = {}
        for spec in variables:
            for key in ("name", "init", "min", "max"):
                if key not in spec:
                    raise ValueError("variable missing %r" % key)
            name = spec["name"]
            if not isinstance(name, str) or not name:
                raise ValueError("variable name must be a non-empty string")
            if name in self._by_name:
                raise ValueError("duplicate variable %r" % name)
            lo = float(spec["min"])
            hi = float(spec["max"])
            if not (lo <= hi):
                raise ValueError("min > max for %r" % name)
            init = float(spec["init"])
            if init < lo:
                init = lo
            elif init > hi:
                init = hi
            scale_req = spec.get("scale", "auto")
            if scale_req not in ("auto", "linear", "log"):
                raise ValueError("bad scale %r for %r" % (scale_req, name))
            if scale_req == "log":
                use_log = True
            elif scale_req == "linear":
                use_log = False
            else:
                use_log = (lo > 0.0 and hi / lo >= 10.0)
            if use_log and lo <= 0.0:
                raise ValueError("log scale needs min > 0 for %r" % name)
            rec = {
                "name": name,
                "init": init,
                "min": lo,
                "max": hi,
                "scale": "log" if use_log else "linear",
                "integer": bool(spec.get("integer", False)),
            }
            self._vars.append(rec)
            self._by_name[name] = rec
        self._links = {}
        for lk in (linking or []):
            target = lk.get("target")
            source = lk.get("source")
            mode = lk.get("mode", "equal")
            if target not in self._by_name:
                raise ValueError("link target %r not declared" % (target,))
            if source not in self._by_name:
                raise ValueError("link source %r not declared" % (source,))
            if mode not in ("equal", "ratio"):
                raise ValueError("bad link mode %r" % (mode,))
            if target in self._links:
                raise ValueError("duplicate link for %r" % (target,))
            factor = 1.0
            if mode == "ratio":
                if "factor" not in lk:
                    raise ValueError("ratio link for %r needs factor" % (target,))
                factor = float(lk["factor"])
            self._links[target] = (source, mode, factor)
        self._free = [v["name"] for v in self._vars
                      if v["name"] not in self._links]
        self._order = self._resolve_order()
    def _resolve_order(self):
        resolved = set(self._free)
        pending = [v["name"] for v in self._vars if v["name"] in self._links]
        order = []
        progress = True
        while pending and progress:
            progress = False
            still = []
            for name in pending:
                source = self._links[name][0]
                if source in resolved:
                    order.append(name)
                    resolved.add(name)
                    progress = True
                else:
                    still.append(name)
            pending = still
        if pending:
            raise ValueError("linking cycle detected: %s"
                             % ", ".join(pending))
        return order

    @property
    def free_names(self):
        return list(self._free)

    @property
    def n_free(self):
        return len(self._free)

    def to_real(self, z):
        z = list(z)
        if len(z) != len(self._free):
            raise ValueError("expected %d free coords, got %d"
                             % (len(self._free), len(z)))
        values = {}
        for i, name in enumerate(self._free):
            values[name] = self._from_unit(name, z[i])
        return self._expand(values)

    def to_unit(self, values):
        out = []
        for name in self._free:
            if name not in values:
                raise ValueError("missing value for %r" % name)
            out.append(self._to_unit_one(name, float(values[name])))
        return out
    def _from_unit(self, name, u):
        var = self._by_name[name]
        if u < 0.0:
            u = 0.0
        elif u > 1.0:
            u = 1.0
        lo, hi = var["min"], var["max"]
        if var["scale"] == "log":
            lo_l = math.log10(lo)
            hi_l = math.log10(hi)
            if hi_l == lo_l:
                v = lo
            else:
                v = 10.0 ** (lo_l + u * (hi_l - lo_l))
        else:
            v = lo + u * (hi - lo)
        return self._finish(name, v)
    def _to_unit_one(self, name, v):
        var = self._by_name[name]
        lo, hi = var["min"], var["max"]
        if hi == lo:
            return 0.0
        if var["scale"] == "log":
            if v <= 0.0:
                return 0.0
            lo_l = math.log10(lo)
            hi_l = math.log10(hi)
            u = (math.log10(v) - lo_l) / (hi_l - lo_l)
        else:
            u = (v - lo) / (hi - lo)
        if u < 0.0:
            return 0.0
        if u > 1.0:
            return 1.0
        return u
    def _finish(self, name, v):
        var = self._by_name[name]
        lo, hi = var["min"], var["max"]
        if var["integer"]:
            v = int(math.floor(float(v) + 0.5))
        if v < lo:
            v = lo
        elif v > hi:
            v = hi
        return v
    def _expand(self, values):
        out = {}
        for var in self._vars:
            name = var["name"]
            if name in self._links:
                continue
            if name not in values:
                raise ValueError("missing value for %r" % name)
            out[name] = self._finish(name, float(values[name]))
        for name in self._order:
            source, mode, factor = self._links[name]
            sv = out[source]
            if mode == "ratio":
                v = sv * factor
            else:
                v = sv
            out[name] = self._finish(name, v)
        return dict((var["name"], out[var["name"]]) for var in self._vars)

def _role_of(target):
    role = getattr(target, "role", "error")
    if role is None:
        role = "error"
    if role not in ("error", "constraint", "objective"):
        raise ValueError("bad role %r for %r" % (role, target))
    return role

def _weight_of(target):
    return float(getattr(target, "weight", 1.0))

def _key_of(target, index):
    return "%s:%s:%d" % (target.output, target.metric, index)

def _safe_error(target, curves):
    try:
        e = target.error(curves)
    except Exception:
        return BIG_ERROR
    if e is None:
        return BIG_ERROR
    try:
        e = float(e)
    except (TypeError, ValueError):
        return BIG_ERROR
    if not math.isfinite(e):
        return BIG_ERROR
    return e

def classify_roles(targets):
    err_t, obj_t, con_t = [], [], []
    for t in targets:
        role = _role_of(t)
        if role == "objective":
            obj_t.append(t)
        elif role == "constraint":
            con_t.append(t)
        else:
            err_t.append(t)
    return err_t, obj_t, con_t

def evaluate_roles(targets, curves, mode="scalar"):
    if mode not in ("scalar", "pareto", "spec"):
        raise ValueError("bad mode %r" % (mode,))
    errors = {}
    if mode == "scalar":
        total = 0.0
        met = True
        for i, t in enumerate(targets):
            e = _safe_error(t, curves)
            if e > 0.0:
                met = False
            clamped = max(e, 0.0)
            errors[_key_of(t, i)] = clamped
            total += clamped * _weight_of(t)
        return {"total": float(total), "F": [], "G": [], "errors": errors,
                "met": met}
    F, G = [], []
    obj_vals = []
    for i, t in enumerate(targets):
        e = _safe_error(t, curves)
        errors[_key_of(t, i)] = e
        role = _role_of(t)
        if role == "objective":
            F.append(e)
            obj_vals.append((e, _weight_of(t)))
        else:
            G.append(e)
    met = all(e <= 0.0 for e in errors.values())
    if mode == "pareto":
        total = sum(e * w for e, w in obj_vals) if obj_vals else 0.0
        return {"total": float(total), "F": F, "G": G, "errors": errors,
                "met": met}
    if obj_vals:
        total = sum(e * w for e, w in obj_vals)
    else:
        worst = max([0.0] + G) if G else 0.0
        F = [worst]
        total = worst
    return {"total": float(total), "F": F, "G": G, "errors": errors,
            "met": met}
INFRA = "INFRA"
DNC = "DNC"
TIMEOUT = "TIMEOUT"
_INFRA_KEYS = ("license", "netlist", "eval serve")
_INFRA_KEYS_CN = ("找不到",)

def classify(status, message, wall=None, timeout=None):
    if status is not None and str(status).lower() == "ok":
        return None
    text = "" if message is None else str(message)
    low = text.lower()
    for key in _INFRA_KEYS:
        if key in low:
            return INFRA
    for key in _INFRA_KEYS_CN:
        if key in text:
            return INFRA
    if wall is not None and timeout is not None and timeout > 0:
        if float(wall) >= 0.95 * float(timeout):
            return TIMEOUT
    return DNC

class PenaltyLedger(object):
    def __init__(self):
        self.fail_count = 0
        self.infra_count = 0

    def dnc_penalty(self, fail_index, f_worst):
        return float(f_worst) + 1.0e4 * (1.0 + float(fail_index))

    def penalty(self, kind, f_worst):
        if kind is None:
            return None
        if kind == INFRA:
            self.infra_count += 1
            return None
        value = self.dnc_penalty(self.fail_count, f_worst)
        self.fail_count += 1
        return value

def _clip_point(pt, bounds):
    out = []
    for i, v in enumerate(pt):
        lo, hi = bounds[i]
        if v < lo:
            v = lo
        elif v > hi:
            v = hi
        out.append(float(v))
    return out

def _kick_logmul(x_best, bounds, rng, spread):
    pt = []
    for i, v in enumerate(x_best):
        lo, hi = bounds[i]
        if lo > 0.0 and hi > 0.0 and v > 0.0:
            lz = math.log10(v) + rng.gauss(0.0, spread)
            pt.append(10.0 ** lz)
        else:
            pt.append(v + rng.gauss(0.0, spread) * (hi - lo))
    return _clip_point(pt, bounds)

def _kick_additive(x_best, bounds, rng, spread):
    pt = []
    for i, v in enumerate(x_best):
        lo, hi = bounds[i]
        pt.append(v + rng.gauss(0.0, spread) * (hi - lo))
    return _clip_point(pt, bounds)

def _kick_mixed(x_best, bounds, rng, spread):
    pt = []
    for i, v in enumerate(x_best):
        lo, hi = bounds[i]
        if lo > 0.0 and hi > 0.0 and v > 0.0 and rng.random() < 0.5:
            lz = math.log10(v) + rng.gauss(0.0, spread)
            pt.append(10.0 ** lz)
        else:
            pt.append(v + rng.gauss(0.0, spread) * (hi - lo))
    return _clip_point(pt, bounds)

def _ensure_moved(y, x_best, bounds, rng):
    n = len(x_best)
    for i in range(n):
        lo, hi = bounds[i]
        span = hi - lo
        if span <= 0.0:
            continue
        if abs(y[i] - x_best[i]) > 1.0e-9 * max(1.0, span):
            return y
    for i in range(n):
        lo, hi = bounds[i]
        span = hi - lo
        if span <= 0.0:
            continue
        u = (x_best[i] - lo) / span
        y[i] = lo + (0.75 if u < 0.5 else 0.25) * span
        return y
    return y

def make_kick(x_best, bounds, rng, spread=0.2):
    if not isinstance(rng, random.Random):
        raise TypeError("rng must be random.Random")
    x_best = [float(v) for v in x_best]
    kicks = [
        _kick_logmul(x_best, bounds, rng, spread),
        _kick_additive(x_best, bounds, rng, spread),
        _kick_mixed(x_best, bounds, rng, spread),
    ]
    for i in range(len(kicks)):
        kicks[i] = _ensure_moved(kicks[i], x_best, bounds, rng)
    return kicks

def to_jsonable(obj):
    if isinstance(obj, tuple):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        out = {}
        for key in obj:
            out[str(key)] = to_jsonable(obj[key])
        return out
    return obj

def from_jsonable(obj):
    if isinstance(obj, list):
        return tuple(from_jsonable(v) for v in obj)
    if isinstance(obj, dict):
        out = {}
        for key in obj:
            out[key] = from_jsonable(obj[key])
        return out
    return obj

def _append_jsonl(path, obj, fsync=False):
    line = json.dumps(to_jsonable(obj), ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        if fsync:
            try:
                f.flush()
                os.fsync(f.fileno())
            except (OSError, ValueError):
                pass

def _read_jsonl(path, skip_bad=False):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                if not skip_bad:
                    raise
    return out

def _values_from_record(rec, names=None, require_all=False, strict=False):
    raw = rec.get("variables") or {}
    keys = list(raw) if names is None else list(names)
    values = {}
    for name in keys:
        if name not in raw:
            if require_all:
                return None
            continue
        text = raw[name]
        try:
            values[name] = (text if isinstance(text, (int, float))
                            else parse_eng(text))
        except Exception:
            if strict:
                raise
            if require_all:
                return None
    return values

class RunJournal(object):
    keep_snapshots = 3
    def __init__(self, directory):
        self.directory = directory
        self.events_path = os.path.join(directory, "events.jsonl")
        self.snapshot_path = os.path.join(directory, "snapshot.json")
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        self._snap_seq = self._scan_seq()

    def log_event(self, event):
        _append_jsonl(self.events_path, event)
        return event

    def read_events(self):
        return _read_jsonl(self.events_path)

    def snapshot(self, state_dict):
        payload = json.dumps(to_jsonable(state_dict), indent=2,
                             sort_keys=True)
        self._atomic_write(self.snapshot_path, payload)
        self._snap_seq += 1
        numbered = os.path.join(
            self.directory, "snapshot-%d.json" % self._snap_seq)
        self._atomic_write(numbered, payload)
        self._prune()
        return numbered

    def load_latest(self):
        path = self.snapshot_path
        if not os.path.isfile(path):
            numbered = self._numbered()
            if not numbered:
                return None
            path = numbered[-1][1]
        try:
            f = open(path, "r", encoding="utf-8")
            try:
                raw = json.load(f)
            finally:
                f.close()
        except (IOError, OSError, ValueError):
            return None
        return from_jsonable(raw)
    def _atomic_write(self, path, text):
        tmp = path + ".tmp"
        f = open(tmp, "w", encoding="utf-8")
        try:
            f.write(text)
        finally:
            f.close()
        os.replace(tmp, path)
        audit("write", path, "atomic")
    def _numbered(self):
        entries = []
        try:
            names = os.listdir(self.directory)
        except OSError:
            return entries
        for name in names:
            if name.startswith("snapshot-") and name.endswith(".json"):
                mid = name[len("snapshot-"):-len(".json")]
                if mid.isdigit():
                    entries.append((int(mid),
                                    os.path.join(self.directory, name)))
        entries.sort()
        return entries
    def _scan_seq(self):
        entries = self._numbered()
        return entries[-1][0] if entries else 0
    def _prune(self):
        entries = self._numbered()
        while len(entries) > self.keep_snapshots:
            _seq, path = entries.pop(0)
            try:
                _archive_aside(path)
            except OSError:
                pass

def _ranks(xs):
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = 0.5 * (i + j) + 1.0
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks

def spearman_rank(xs, ys):
    if xs is None or ys is None:
        return 0.0
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    x = [float(v) for v in xs[:n]]
    y = [float(v) for v in ys[:n]]
    rx = _ranks(x)
    ry = _ranks(y)
    mx = sum(rx) / float(n)
    my = sum(ry) / float(n)
    num = 0.0
    dx = 0.0
    dy = 0.0
    for i in range(n):
        a = rx[i] - mx
        b = ry[i] - my
        num += a * b
        dx += a * a
        dy += b * b
    if dx <= 0.0 or dy <= 0.0:
        return 0.0
    rho = num / math.sqrt(dx * dy)
    if rho > 1.0:
        rho = 1.0
    elif rho < -1.0:
        rho = -1.0
    return rho

def sensitivity_report(history_vars, history_vals):
    if not history_vars or not history_vals:
        return []
    n = min(len(history_vars), len(history_vals))
    names = []
    seen = set()
    for i in range(n):
        row = history_vars[i]
        if not isinstance(row, dict):
            continue
        for name in row:
            if name not in seen:
                seen.add(name)
                names.append(name)
    out = []
    for name in names:
        xs = []
        ys = []
        for i in range(n):
            row = history_vars[i]
            if not isinstance(row, dict):
                continue
            if name not in row:
                continue
            if row[name] is None or history_vals[i] is None:
                continue
            xs.append(row[name])
            ys.append(history_vals[i])
        rho = spearman_rank(xs, ys)
        out.append((name, rho, len(xs)))
    out.sort(key=lambda t: (-abs(t[1]), t[0]))
    return out

def move_table(init_vars, best_vars, bounds):
    if not isinstance(init_vars, dict):
        raise TypeError("init_vars must be a dict")
    if not isinstance(best_vars, dict):
        raise TypeError("best_vars must be a dict")
    rows = []
    keys = list(init_vars)
    for i, name in enumerate(keys):
        if isinstance(bounds, dict):
            lo, hi = bounds.get(name, (0.0, 1.0))
        elif bounds is None:
            lo, hi = 0.0, 1.0
        else:
            lo, hi = bounds[i]
        lo = float(lo)
        hi = float(hi)
        init = float(init_vars[name])
        best = float(best_vars.get(name, init))
        span = hi - lo
        move = 0.0 if span == 0.0 else (best - init) / span
        rows.append({
            "name": name,
            "init": init,
            "best": best,
            "normalized_move": move,
        })
    return rows

def build_summary(history, best, extra=None):
    hist_vars = list(history.get("vars", []))
    hist_vals = list(history.get("vals", []))
    init_vars = history.get("init_vars")
    if init_vars is None:
        init_vars = hist_vars[0] if hist_vars else {}
    summary = {
        "sensitivity": sensitivity_report(hist_vars, hist_vals),
        "move_table": move_table(init_vars, dict(best), history.get("bounds")),
        "n_evals": history.get("evals", len(hist_vals)),
        "failures": history.get("failures", {}),
        "restarts": history.get("restarts", 0),
    }
    if extra:
        summary.update(extra)
    return summary

class SpaceAdapter(object):
    batch_api = True
    def __init__(self, algo, transform, all_names):
        self.inner = algo
        self.transform = transform
        self.all_names = list(all_names)
        self.name = getattr(algo, "name", "algorithm")
        self.n = transform.n_free
        self._last_z = None
        self._inject = []

    def inject(self, points_phys):
        for vals in points_phys:
            self._inject.append(self.transform.to_unit(vals))

    def seed_history(self, points_phys):
        pts = []
        for item in points_phys:
            if item is None:
                continue
            vals = item[0]
            if not isinstance(vals, dict):
                vals = dict(zip(self.all_names, list(vals)))
            try:
                y = float(item[1])
            except (TypeError, ValueError):
                continue
            g = item[2] if len(item) > 2 else None
            try:
                z = self.transform.to_unit(vals)
            except Exception:
                continue
            pts.append((z, y, g))
        if pts and hasattr(self.inner, "seed_history"):
            self.inner.seed_history(pts)

    @property
    def eval_count(self):
        return self.inner.eval_count

    def ask(self):
        pts = self.inner.ask(1)
        if self._inject:
            z = self._inject.pop(0)
        else:
            z = pts[0] if pts else [0.5] * self.n
        self._last_z = list(z)
        phys = self.transform.to_real(z)
        return [phys[name] for name in self.all_names]

    def tell(self, value, F=None, G=None):
        z = self._last_z if self._last_z is not None else [0.5] * self.n
        self._last_z = None
        if F is None:
            f = value
        elif self.inner.n_obj > 1:
            f = F
        else:
            f = F[0] if (F and isinstance(F[0], (list, tuple))) else F
            if isinstance(f, (list, tuple)):
                f = sum(f)
        self.inner.tell([z], [f], [list(G)] if G else None)  # G 必须包一层

    def snapshot(self):
        return {"name": self.name, "inner": self.inner.snapshot()}

    def restore(self, state):
        if state.get("inner") is not None:
            self.inner.restore(state["inner"])

    def restart(self, spread=0.15):
        self.inner.restart(spread)
_SCALAR_ONLY = ("random", "grid", "simplex", "global", "de", "lshade",
                "cmaes", "cmaes_ipop", "tpe")

def build_optimizer(strategy, transform, x0_values, all_names, seed=0,
                    n_obj=1, n_constr=0, budget=None, **opts):
    if strategy in _SCALAR_ONLY and n_obj > 1:
        n_obj = 1
    unit_bounds = [(0.0, 1.0)] * transform.n_free
    z0 = transform.to_unit(x0_values)
    algo = create(strategy, list(transform.free_names), unit_bounds,
                  x0=z0, n_obj=n_obj, n_constr=n_constr, seed=seed,
                  budget=budget, **opts)
    return SpaceAdapter(algo, transform, all_names)

class SimResult:
    def __init__(self, curves=None, status="ok", message="", raw_path=None,
                 timing=None, wall=0.0):
        self.curves = curves or {}
        self.status = status
        self.message = message
        self.raw_path = raw_path
        self.timing = timing or {}
        self.wall = wall

class MockRunner:
    EXAMPLE_VARS = ("L1", "L2", "k0", "cin")
    def __init__(self, config):
        self.config = config
        self.ranges = {}
        for var in config.get("variables", []):
            lo = parse_eng(str(var.get("min", 0.0)))
            hi = parse_eng(str(var.get("max", 1.0)))
            self.ranges[var["name"]] = (lo, hi)

    def run(self, values, iter_dir):
        curves = {}
        for output in self._output_names():
            curves[output] = self._synthetic_curve(values, output)
        raw = os.path.join(iter_dir, "mock_s21.txt")
        x, y = next(iter(curves.values()))
        with open(raw, "w", encoding="utf-8") as f:
            for xi, yi in zip(x, y):
                f.write(f"{xi:.6e} {yi:.6f}\n")
        if _log_mode_of(self.config) == LOG_MODE_COMPACT:
            try:
                gzip_file_in_place(raw, keep_stub=False)
            except Exception:
                pass
        with open(os.path.join(iter_dir, "result.txt"), "w",
                  encoding="utf-8") as f:
            f.write("STATUS ok\n")
            f.write("MSG mock\n")
            for name, (xs, ys) in curves.items():
                f.write("CURVE_BEGIN %s\n" % name)
                for xi, yi in zip(xs, ys):
                    f.write("%s %s\n"
                            % (repr(float(xi)), repr(float(yi))))
                f.write("CURVE_END\n")
        return SimResult(curves=curves, raw_path=raw)
    def _output_names(self):
        names = []
        for t in self.config.get("targets", []):
            name = t.get("output")
            if name and name not in names:
                names.append(name)
        return names or ["S21 dB20"]
    def _synthetic_curve(self, values, output):
        if set(values) == set(self.EXAMPLE_VARS):
            return self._example_curve(values)
        return self._generic_curve(values)
    def _example_curve(self, values):
        l1 = values["L1"] / 250e-12
        l2 = values["L2"] / 280e-12
        k0 = values["k0"]
        cin = values["cin"] / 100e-15
        gain0 = 5.0 - 5.0 * (l1 - 1.30) ** 2 - 4.0 * (l2 - 1.15) ** 2
        gain0 += -3.0 * (k0 - 0.75) ** 2 - 1.5 * (cin - 1.5) ** 2
        ripple = 40.0 * (l1 / l2 - 1.05) ** 2 + 25.0 * (k0 - 0.70) ** 2
        x = []
        y = []
        f = 16e9
        while f <= 56.0001e9:
            tilt = 0.8 * (cin - 1.5) * (f - 34e9) / 10e9
            val = gain0 + tilt + ripple * math.cos((f - 24e9) / 20e9 * math.pi)
            x.append(f)
            y.append(val)
            f += 0.1e9
        return x, y
    def _generic_curve(self, values):
        dev = 0.0
        for name, val in values.items():
            lo, hi = self.ranges.get(name, (0.0, 1.0))
            mid = (lo + hi) / 2.0
            span = (hi - lo) or 1.0
            dev += ((val - mid) / span) ** 2
        gain0 = 5.0 - 4.0 * dev
        ripple = 2.0 * dev
        lo_f, hi_f = self._freq_range()
        x = []
        y = []
        n = 200
        for i in range(n + 1):
            f = lo_f + (hi_f - lo_f) * i / n
            phase = (f - lo_f) / (hi_f - lo_f) * 2 * math.pi
            x.append(f)
            y.append(gain0 + ripple * math.cos(phase))
        return x, y
    def _freq_range(self):
        pts = []
        for t in self.config.get("targets", []):
            for val in (t.get("band") or []):
                if val not in (None, ""):
                    pts.append(parse_eng(str(val)))
            if t.get("freq") not in (None, ""):
                pts.append(parse_eng(str(t["freq"])))
        if not pts:
            return 1e9, 10e9
        lo = min(pts) * 0.9
        hi = max(pts) * 1.1
        return lo, (hi if hi > lo else lo + 1.0)

def _is_netlist_comment(line):
    return line.startswith(("//", "*", ";", "#"))

def _legacy_parameters_spans(text):
    lines = text.splitlines()
    spans = []
    depth = 0
    i = 0
    while i < len(lines):
        s = lines[i].lstrip()
        if _is_netlist_comment(s):
            i += 1
            continue
        if re.match(r"subckt(\s|$)", s):
            depth += 1
            i += 1
            continue
        if re.match(r"ends(\s|$)", s):
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and re.match(r"parameters(\s|$)", s):
            j = i
            while (j + 1 < len(lines)
                   and (lines[j].rstrip().endswith("\\")
                        or lines[j + 1].lstrip().startswith("+"))):
                j += 1
            spans.append((i, j))
            i = j + 1
            continue
        i += 1
    return spans

def _legacy_parameters_payload(lines, span):
    first, last = span
    parts = []
    for k in range(first, last + 1):
        s = lines[k].strip()
        if k > first and s.startswith("+"):
            s = s[1:].strip()
        if s.endswith("\\"):
            s = s[:-1].strip()
        if k == first:
            s = re.sub(r"^parameters\s*", "", s)
        if s:
            parts.append(s)
    return " ".join(parts)

def _legacy_parse_netlist_parameters(text):
    lines = text.splitlines()
    params = {}
    for span in _legacy_parameters_spans(text):
        for tok in _legacy_parameters_payload(lines, span).split():
            key, _eq, val = tok.partition("=")
            if key:
                params[key] = val
    return params

def _legacy_rewrite_netlist_parameters(text, line):
    spans = _legacy_parameters_spans(text)
    if not spans:
        return text, 0
    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    for n, (first, last) in enumerate(spans):
        out.extend(lines[i:first])
        if n == 0:
            out.append(line + "\n")
        i = last + 1
    out.extend(lines[i:])
    new_text = "".join(out)
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, len(spans)

def _netlist_params_module():
    try:
        import netlist_params
        return netlist_params
    except Exception:
        return None

def parse_netlist_parameters(text):
    mod = _netlist_params_module()
    if mod is not None:
        return mod.parse_netlist_parameters(text)
    return _legacy_parse_netlist_parameters(text)

def rewrite_netlist_parameters(text, line):
    mod = _netlist_params_module()
    if mod is not None:
        return mod.rewrite_netlist_parameters(text, line)
    return _legacy_rewrite_netlist_parameters(text, line)

_ANALYSIS_KEYWORDS = frozenset((
    "ac", "acmatch", "acnoise", "dc", "dcmatch", "dcop", "disto", "envlp",
    "fourier", "hb", "hbac", "hbnoise", "hbsp", "montecarlo", "mpde",
    "noise", "pac", "pacnoise", "pnoise", "pss", "pstb", "psp", "pz",
    "ql", "sens", "sp", "spnoise", "stb", "sweep", "sweephb", "tf",
    "tran", "xf",
))

_NETLIST_DIRECTIVES = frozenset((
    "simulator", "global", "include", "ahdl_include", "hdl_include",
    "parameters", "subckt", "ends", "inline", "model", "library",
    "endlibrary", "section", "endsection", "statistics", "process",
    "paramtest", "options", "save", "ic", "nodeset", "temp", "alter",
    "altergroup", "real", "check", "assert", "shell", "set", "title",
    "end", "info", "fatal", "warning", "error",
))

def netlist_analyses(text):
    found = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("*", "//", ";", "#")):
            continue
        toks = line.split()
        head = toks[0].lower()
        if head in _NETLIST_DIRECTIVES:
            continue
        kind = None
        if len(toks) > 1 and toks[1].lower() in _ANALYSIS_KEYWORDS:
            kind = toks[1].lower()
        elif head in _ANALYSIS_KEYWORDS and not (
                len(toks) > 1 and toks[1].startswith("(")):
            kind = head
        if kind:
            found.append(kind)
    return found

def resolve_netlist_path(cfg):
    cfg = cfg or {}
    raw = str(cfg.get("netlist") or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    root = os.path.expanduser(str(cfg.get("sim_root") or "~/simulation"))
    cell_dir = os.path.join(root, str(cfg.get("library") or ""),
                            str(cfg.get("cell") or ""))
    hits = glob.glob(os.path.join(cell_dir, "**", "input.scs"),
                     recursive=True)
    if not hits:
        return ""
    view = str(cfg.get("netlist_view") or cfg.get("view") or "").strip()
    if view:
        marker = os.sep + "spectre" + os.sep + view + os.sep
        preferred = [p for p in hits if marker in p]
        if preferred:
            hits = preferred
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0]

def preflight_netlist(cfg):
    cfg = cfg or {}
    explicit = bool(str(cfg.get("netlist") or "").strip())
    if not explicit and not (cfg.get("library") or cfg.get("cell")):
        return ""
    path = resolve_netlist_path(cfg)
    if not path:
        return ""
    if not os.path.isfile(path):
        raise RuntimeError(
            "网表不存在：%s。请先在 ADE 里生成网表，"
            "或在设计页把「网表」指到正确的 input.scs。" % path)
    found = netlist_analyses(_read_text(path))
    want = [p for p in str(cfg.get("analyses") or "").replace(",", " ").split()
            if p]
    missing = [w for w in want if w.lower() not in found]
    if missing:
        raise RuntimeError(
            "网表 %s 里没有 %s 分析语句（现有：%s）。ADE 增量重生成网表时"
            "可能丢掉了分析；请回 ADE 重新生成网表，或把分析行补回网表后重跑。"
            % (path, "/".join(missing), "/".join(found) or "无"))
    if not found:
        raise RuntimeError(
            "网表 %s 里没有任何分析语句（sp/dc/tran/ac…）。这样的网表 spectre "
            "能跑完但不会产生输出曲线，迭代只会一路失败。请回 ADE 重新生成"
            "网表，或把分析行补回网表后重跑。" % path)
    return "分析: %s" % "/".join(found)

class RealRunner:
    live = set()
    def __init__(self, config, skill_dir, env=None):
        self.config = config
        self.skill_dir = skill_dir
        self.env = env or os.environ.copy()
        self.proc = None
        self.sim_proc = None
        self.deck = None
        self.fixed = {}
        for item in config.get("fixed_variables", []) or []:
            name = item.get("name")
            if name:
                self.fixed[name] = str(item.get("value", ""))
        out_dir = os.path.abspath(os.path.expanduser(
            config.get("output_dir", "logs/run")))
        self.queue_dir = os.path.join(out_dir, "queue")

    @classmethod
    def stop_all(cls):
        for runner in list(cls.live):
            runner.terminate()

    def terminate(self):
        try:
            os.makedirs(self.queue_dir, exist_ok=True)
            with open(os.path.join(self.queue_dir, "STOP"), "w"):
                pass
        except OSError:
            pass
        for proc in (self.sim_proc, self.proc):
            if proc is not None and proc.poll() is None:
                self._kill_group(proc, force=False)
                import threading
                timer = threading.Timer(3.0, self._kill_group,
                                        args=(proc, True))
                timer.daemon = True
                timer.start()
    def _kill_group(self, proc, force):
        import signal
        if proc.poll() is not None:
            self._cleanup_helpers(proc.pid)
            return
        pgid = None
        try:
            if hasattr(os, "getpgid"):
                pgid = os.getpgid(proc.pid)
        except OSError:
            pass
        sig = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
        try:
            if pgid is not None:
                os.killpg(pgid, sig)
            else:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill() if force else proc.terminate()
            except OSError:
                pass
    @staticmethod
    def _cleanup_helpers(pid):
        try:
            subprocess.run(
                ["pkill", "-f", f"mpssession virtuoso{pid}($| )"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def run(self, values, iter_dir):
        t0 = time.time()
        result = self._run_fast(values, iter_dir)
        result.wall = time.time() - t0
        return result
    def _fail(self, message):
        return SimResult(status="fail", message=message)
    def _run_fast(self, values, iter_dir):
        deck = self._load_deck()
        if deck is None:
            where = self.config.get("netlist") or os.path.join(
                os.path.expanduser(self.config.get("sim_root",
                                                 "~/simulation")),
                self.config.get("library", ""),
                self.config.get("cell", ""))
            return self._fail("no spectre deck: %s 无结果"
                              "（设计页填「网表」或点「查找网表」）" % where)
        if not self._ensure_eval():
            return self._fail("cannot start eval serve")
        text, params = deck
        merged = dict(params)
        for name, val in self.fixed.items():
            merged[name] = val
        for name, val in values.items():
            merged[name] = format_eng(val)
        line = "parameters " + " ".join(f"{k}={v}" for k, v in merged.items())
        patch_report = None
        mod = _netlist_params_module()
        if mod is not None and hasattr(mod, "patch_netlist"):
            new_text, patch_report = mod.patch_netlist(
                text, dict(values), fixed=dict(self.fixed),
                options=self.config.get("netlist_patch") or {})
            try:
                with open(os.path.join(iter_dir, "netlist_patch.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(patch_report, f, indent=2, ensure_ascii=False)
            except OSError:
                pass
            if not patch_report.get("ok"):
                missing = ", ".join(patch_report.get("missing") or []) \
                    or "未知变量"
                return self._fail(
                    "网表里找不到变量 %s；已检查顶层 parameters、"
                    "子电路 parameters 与实例参数。详见 "
                    "netlist_patch.json" % missing)
        else:
            new_text, replaced = rewrite_netlist_parameters(text, line)
            if not replaced:
                return self._fail(
                    "网表顶层没有 parameters 行, 无法写入变量"
                    "（检查网表; 子电路内的 parameters 行"
                    "不会被改写）")
        try:
            _archive_aside(os.path.join(iter_dir, "result.txt"))
        except OSError:
            pass
        deck_path = os.path.join(iter_dir, "input.scs")
        with open(deck_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        spectre = resolve_prog("spectre",
                               self.config.get("spectre_bin") or "spectre")
        env = self._environment()
        timeout = self.config.get("sim_timeout", 3600)
        t0 = time.time()
        try:
            with open(os.path.join(iter_dir, "spectre.log"), "w") as slog:
                audit("exec", spectre,
                      "input.scs -raw psf  cwd=" + iter_dir)
                self.sim_proc = subprocess.Popen(
                    [spectre, "input.scs", "-raw", "psf"],
                    cwd=iter_dir, env=env, start_new_session=True,
                    stdout=slog, stderr=subprocess.STDOUT)
        except OSError as exc:
            self._compact_after_spectre(iter_dir, new_text, line, patch_report)
            return self._fail(f"cannot start spectre: {exc}")
        try:
            self.sim_proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_group(self.sim_proc, force=True)
            self.sim_proc.wait()
            self.sim_proc = None
            self._compact_after_spectre(iter_dir, new_text, line, patch_report)
            return self._fail("spectre timeout")
        sim_wall = time.time() - t0
        proc = self.sim_proc
        self.sim_proc = None
        self._cleanup_helpers(proc.pid)
        self._compact_after_spectre(iter_dir, new_text, line, patch_report)
        # 成败只看 psf 非空；spectre 退出码不可信（DC 不收敛但 sp 结果仍会写出）
        psf_dir = os.path.join(iter_dir, "psf")
        if not os.path.isdir(psf_dir) or not os.listdir(psf_dir):
            return self._fail(f"spectre exit {proc.returncode}, no psf output")
        result_path = os.path.join(iter_dir, "result.txt")
        req = os.path.join(self.queue_dir, "eval_req.txt")
        t_req = time.time()
        with open(req + ".tmp", "w", encoding="utf-8") as f:
            f.write(f"PSFDIR {os.path.join(iter_dir, 'psf')}\n")
            f.write(f"RESULT {result_path}\n")
        os.replace(req + ".tmp", req)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._result_ready(result_path):
                curves, status, message, timing = self._read_result(result_path)
                timing["sim"] = sim_wall
                timing.setdefault("eval", time.time() - t_req)
                keep_raw = _save_raw_of(self.config)
                if keep_raw == 0:
                    _archive_aside(os.path.join(iter_dir, "psf"))
                return SimResult(curves=curves, status=status,
                                 message=message, raw_path=result_path,
                                 timing=timing)
            serve = self.proc
            if serve is None or serve.poll() is not None:
                self._cleanup_helpers(serve.pid if serve else 0)
                self.proc = None
                self.live.discard(self)
                return self._fail("eval serve died")
            time.sleep(0.1)
        self.terminate()
        return self._fail("eval timeout")
    def _compact_after_spectre(self, iter_dir, full_text, params_line,
                               patch_report=None):
        if _log_mode_of(self.config) != LOG_MODE_COMPACT:
            return
        try:
            compact_netlist_file(iter_dir, full_text, params_line,
                                 patch_report)
        except Exception as exc:
            print("compact netlist skipped: %s" % exc, file=sys.stderr)
        try:
            compact_spectre_log(iter_dir)
        except Exception as exc:
            print("compact spectre.log skipped: %s" % exc, file=sys.stderr)
    def _load_deck(self):
        if self.deck is not None:
            return self.deck
        path = self.config.get("netlist") or self._find_deck()
        if not path or not os.path.exists(path):
            return None
        text = _read_text(path)
        params = parse_netlist_parameters(text)
        self.deck = (text, params)
        print("网表快照 %s" % path)
        print("运行机制: 网表一次读入; 每轮改写参数后直跑 spectre; "
              "启动前按 auto_netlist 决定是否从原理图/config 重新生成")
        return self.deck
    def _find_deck(self):
        return resolve_netlist_path(self.config) or None
    def _ensure_eval(self):
        if self.proc is not None and self.proc.poll() is None:
            return True
        state_dir = self.config.get("state_dir") or ""
        outputs_state = os.path.join(
            os.path.expanduser(str(state_dir)), "outputs.state")
        if not os.path.exists(outputs_state):
            return False
        try:
            os.makedirs(self.queue_dir, exist_ok=True)
            for name in ("eval_req.txt", "eval_req.txt.tmp", "STOP"):
                try:
                    _archive_aside(os.path.join(self.queue_dir, name))
                except OSError:
                    pass
        except OSError:
            return False
        env = self._environment()
        env["OPT_QUEUE"] = self.queue_dir
        env["OPT_OUTPUTS_STATE"] = outputs_state
        cmd = [
            resolve_prog("virtuoso",
                         self.config.get("virtuoso_bin") or "virtuoso"),
            "-nograph",
            "-log", os.path.join(self.queue_dir, "eval_serve.log"),
            "-replay", os.path.join(self.skill_dir, "librf.il"),
        ]
        cwd = self.config.get("work_dir", os.getcwd())
        try:
            audit("exec", cmd[0], " ".join(cmd[1:])[:200])
            self.proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                                         start_new_session=True)
        except OSError:
            return False
        self.live.add(self)
        return True
    @staticmethod
    def _result_ready(path):
        try:
            with open(path, errors="ignore") as f:
                return any(line.startswith("STATUS") for line in f)
        except OSError:
            return False
    def _environment(self):
        env = self.env.copy()
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        for key, val in self.config.get("env", {}).items():
            env[key] = str(val)
        extra_path = self.config.get("path_append")
        if extra_path:
            env["PATH"] = ((env.get("PATH") or "")
                               + os.pathsep + str(extra_path))
        return env
    @staticmethod
    def _read_result(path):
        curves = {}
        status = "fail"
        message = ""
        timing = {}
        name = None
        xs = []
        ys = []
        with open(path) as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "STATUS":
                    if len(parts) > 1:
                        status = parts[1]
                elif parts[0] == "MSG":
                    message = line[4:].strip()
                elif parts[0] == "TIMING":
                    if len(parts) >= 3:
                        try:
                            timing[parts[1]] = float(parts[2])
                        except ValueError:
                            pass
                elif parts[0] == "SCALAR":
                    try:
                        value = float(parts[-1])
                    except ValueError:
                        continue
                    sname = " ".join(parts[1:-1])
                    if sname:
                        curves[sname] = ([0.0], [value])
                elif parts[0] == "CURVE_BEGIN":
                    name = line[len("CURVE_BEGIN"):].strip()
                    xs = []
                    ys = []
                elif parts[0] == "CURVE_END":
                    if name:
                        curves[name] = (xs, ys)
                    name = None
                elif name is not None and len(parts) >= 2:
                    try:
                        xv = float(parts[0])
                        yv = float(parts[1])
                    except ValueError:
                        continue
                    xs.append(xv)
                    ys.append(yv)
        return curves, status, message, timing
DEFAULT_LOG_ROOT = "logs"
PLACEHOLDER_OUTPUT_DIR = "logs/run"

def _safe_segment(text):
    raw = str(text or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    cleaned = cleaned[:60] or "circuit"
    if any(ord(ch) > 127 for ch in raw):
        cleaned += "_%06x" % (zlib.crc32(raw.encode("utf-8")) & 0xffffffff)
    return cleaned

def circuit_id(cfg):
    cfg = cfg or {}
    cid = str(cfg.get("library_cell") or "").strip()
    lib = str(cfg.get("library") or "").strip()
    cell = str(cfg.get("cell") or "").strip()
    if not cid and lib and cell:
        cid = "%s_%s" % (lib, cell)
    elif not cid and cell:
        cid = cell
    if not cid:
        cid = str(cfg.get("name") or "").strip()
    return _safe_segment(cid)

def _fp_number(value):
    try:
        return round(float(parse_eng(value)), 12)
    except Exception:
        return str(value)

def _fp_target(spec):
    if not isinstance(spec, dict):
        return str(spec)
    out = dict(spec)
    for key in ("goal", "goal2", "tol", "weight", "freq"):
        if key in out:
            out[key] = _fp_number(out[key])
    if isinstance(out.get("band"), list):
        out["band"] = [_fp_number(x) for x in out["band"]]
    limit = out.get("limit")
    if isinstance(limit, dict):
        out["limit"] = {
            k: ([_fp_number(x) for x in v] if isinstance(v, list) else v)
            for k, v in limit.items()}
    return out

def config_fingerprint(cfg):
    cfg = cfg or {}
    settings = cfg.get("settings") or {}
    variables = []
    for v in cfg.get("variables") or []:
        if not isinstance(v, dict):
            continue
        variables.append({
            "name": str(v.get("name") or ""),
            "init": _fp_number(v.get("init")),
            "min": _fp_number(v.get("min")),
            "max": _fp_number(v.get("max")),
            "scale": str(v.get("scale") or ""),
            "integer": bool(v.get("integer")),
        })
    fixed = [{"name": str(v.get("name") or ""),
              "value": _fp_number(v.get("value"))}
             for v in (cfg.get("fixed_variables") or [])
             if isinstance(v, dict)]
    def _abs(key):
        value = cfg.get(key)
        return (os.path.abspath(os.path.expanduser(str(value)))
                if value else "")
    payload = {
        "circuit": circuit_id(cfg),
        "library": str(cfg.get("library") or ""),
        "cell": str(cfg.get("cell") or ""),
        "view": str(cfg.get("view") or ""),
        "state": str(cfg.get("state") or ""),
        "state_dir": _abs("state_dir"),
        "netlist": _abs("netlist"),
        "netlist_view": str(cfg.get("netlist_view") or ""),
        "auto_netlist": str(cfg.get("auto_netlist", "auto")),
        "analyses": cfg.get("analyses"),
        "variables": variables,
        "fixed_variables": fixed,
        "targets": [_fp_target(t) for t in (cfg.get("targets") or [])],
        "mode": str(settings.get("mode") or "scalar"),
        "em_bridge": cfg.get("em_bridge"),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()

def _read_json_dict(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def make_run_dir(root, circuit):
    base = os.path.abspath(os.path.expanduser(str(root or DEFAULT_LOG_ROOT)))
    folder = os.path.join(base, _safe_segment(circuit))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for n in range(10000):
        name = ("run_%s" % stamp) if n == 0 else ("run_%s_%d" % (stamp, n))
        path = os.path.join(folder, name)
        try:
            os.makedirs(path)
            return path
        except OSError:
            if not os.path.isdir(path):
                raise
    raise RuntimeError("can not create a run folder below %s" % folder)

def _looks_like_run_dir(path):
    return bool(re.match(r"^run_\d{8}_\d{6}",
                         os.path.basename(os.path.abspath(path))))

def _base_of_run_dir(path):
    abs_path = os.path.abspath(os.path.expanduser(str(path or "")))
    if _looks_like_run_dir(abs_path):
        return os.path.dirname(os.path.dirname(abs_path))
    return abs_path

def _dir_is_empty(path):
    try:
        return os.path.isdir(path) and not os.listdir(path)
    except OSError:
        return False

def resolve_run_dir(cfg, resume=False, prev=None):
    raw = os.path.expanduser(str((cfg or {}).get("output_dir") or "").strip())
    if resume and raw:
        run_dir = os.path.abspath(raw)
        base = _base_of_run_dir(run_dir)
        if prev and prev[2] and os.path.abspath(prev[2]) == run_dir:
            auto = bool(prev[1])
        else:
            auto = (os.path.abspath(base) ==
                    os.path.abspath(DEFAULT_LOG_ROOT))
        return run_dir, (base, auto, run_dir)
    prev_state = prev or None
    if raw and prev_state and prev_state[2] and \
            os.path.abspath(raw) == os.path.abspath(prev_state[2]):
        base, auto = prev_state[0], prev_state[1]
    elif not raw or raw == PLACEHOLDER_OUTPUT_DIR:
        base, auto = DEFAULT_LOG_ROOT, True
    else:
        base, auto = raw, False
    if auto:
        run_dir = make_run_dir(base, circuit_id(cfg))
    elif not os.path.isdir(base) or _dir_is_empty(base):
        run_dir = os.path.abspath(os.path.expanduser(base))
        os.makedirs(run_dir, exist_ok=True)
    else:
        run_dir = make_run_dir(_base_of_run_dir(base), circuit_id(cfg))
    return run_dir, (base, auto, run_dir)

def _archive_aside(path):  # 只改名归档，不删除
    if not os.path.exists(path):
        return None
    dst = "%s.old-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    n = 1
    while os.path.exists(dst):
        dst = "%s.old-%s.%d" % (path, time.strftime("%Y%m%d-%H%M%S"), n)
        n += 1
    os.rename(path, dst)
    audit("archive", path, "-> " + os.path.basename(dst))
    return dst
LOG_MODE_COMPACT = "compact"
LOG_MODE_FULL = "full"
SPECTRE_LOG_TAIL = 4096
NETLIST_META_NAME = "input.scs.params"
NETLIST_BASE_NAME = "deck_base.scs"
MANIFEST_NAME = "manifest.jsonl"

def _sha1_text(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()

def _sha1_file(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""

def _log_mode_of(cfg):
    raw = str((cfg or {}).get("log_mode") or LOG_MODE_COMPACT).strip().lower()
    if raw == LOG_MODE_FULL:
        return LOG_MODE_FULL
    return LOG_MODE_COMPACT

def _save_raw_of(cfg):
    try:
        return int((cfg or {}).get("save_raw", 1))
    except (TypeError, ValueError):
        return 1

def compact_netlist_file(iter_dir, full_text, params_line, patch_report=None):
    iter_dir = os.path.abspath(iter_dir)
    run_dir = os.path.dirname(iter_dir)
    base_path = os.path.join(run_dir, NETLIST_BASE_NAME)
    full_path = os.path.join(iter_dir, "input.scs")
    sha = _sha1_text(full_text)
    meta = {
        "format": "optimizer.compact.netlist/1",
        "base": NETLIST_BASE_NAME,
        "params_line": params_line,
        "sha1": sha,
        "bytes_full": len(full_text.encode("utf-8")),
    }
    counts = (patch_report or {}).get("counts") or {}
    if counts.get("subckt") or counts.get("instance"):
        meta["base"] = None
        meta["fallback"] = "full_input_kept"
        meta["patch_counts"] = dict(counts)
        with open(os.path.join(iter_dir, NETLIST_META_NAME), "w",
                  encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        return meta
    if os.path.isfile(base_path):
        try:  # 仅当 base+params_line 能逐字节重建 full_text 才归档 input.scs
            rebuilt, replaced = rewrite_netlist_parameters(
                _read_text(base_path), params_line)
            base_ok = bool(replaced) and _sha1_text(rebuilt) == sha
        except Exception:
            base_ok = False
    else:
        try:
            with open(base_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            base_ok = True
        except OSError:
            base_ok = False
    if not base_ok:
        meta["base"] = None
        meta["fallback"] = "full_input_kept"
    with open(os.path.join(iter_dir, NETLIST_META_NAME), "w",
              encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    if not base_ok:
        return meta
    try:
        if os.path.isfile(full_path):
            _archive_aside(full_path)
    except OSError:
        pass
    return meta

def reconstruct_input_scs(run_dir, index, dest=None):
    run_dir = os.path.abspath(os.path.expanduser(str(run_dir)))
    iter_dir = os.path.join(run_dir, "iter_%04d" % int(index))
    meta_path = os.path.join(iter_dir, NETLIST_META_NAME)
    full_path = os.path.join(iter_dir, "input.scs")
    if not os.path.isfile(meta_path):
        if os.path.isfile(full_path):
            text = _read_text(full_path)
            return text, (full_path if dest is None else _write_recon(
                dest, index, text))
        raise ValueError("no %s and no input.scs in %s"
                         % (NETLIST_META_NAME, iter_dir))
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("fallback") == "full_input_kept" or not meta.get("base"):
        if not os.path.isfile(full_path):
            raise ValueError(
                "iter %d kept the full input.scs but it is missing" % index)
        text = _read_text(full_path)
        return text, (full_path if dest is None else _write_recon(
            dest, index, text))
    base_path = os.path.join(run_dir, meta.get("base") or NETLIST_BASE_NAME)
    if not os.path.isfile(base_path):
        raise ValueError("missing base netlist: %s" % base_path)
    text, replaced = rewrite_netlist_parameters(
        _read_text(base_path), meta["params_line"])
    if not replaced:
        raise ValueError("base has no parameters line: %s" % base_path)
    want = meta.get("sha1")
    if want and _sha1_text(text) != want:
        raise ValueError(
            "reconstructed input.scs sha1 mismatch for iter %d "
            "(expected %s)" % (index, want))
    return text, (None if dest is None else _write_recon(dest, index, text))

def _write_recon(dest, index, text):
    dest = os.path.abspath(os.path.expanduser(str(dest)))
    if os.path.isdir(dest) or dest.endswith(os.sep) or dest.endswith("/"):
        os.makedirs(dest, exist_ok=True)
        path = os.path.join(dest, "iter_%04d.scs" % int(index))
    else:
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        path = dest
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path

def compact_spectre_log(iter_dir, tail_bytes=SPECTRE_LOG_TAIL):
    path = os.path.join(iter_dir, "spectre.log")
    if not os.path.isfile(path):
        return None
    gz_path = path + ".gz"
    try:
        raw_size = os.path.getsize(path)
        with open(path, "rb") as src, gzip.open(gz_path + ".tmp", "wb") as gz:
            while True:
                chunk = src.read(1 << 16)
                if not chunk:
                    break
                gz.write(chunk)
        os.replace(gz_path + ".tmp", gz_path)
    except OSError:
        try:
            if os.path.isfile(gz_path + ".tmp"):
                os.remove(gz_path + ".tmp")
        except OSError:
            pass
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {"bytes": raw_size, "gz": gz_path, "tail_kept": False}
    err_lines = [ln for ln in text.splitlines()
                 if re.search(r"error|fatal|fail|warning|unknown", ln, re.I)]
    tail_n = int(tail_bytes)
    if tail_n > 0:
        tail_n = min(tail_n, max(256, len(text) // 2))
    else:
        tail_n = len(text)
    tail = text[-tail_n:] if tail_n else ""
    head = ("COMPACT: full log in spectre.log.gz (%d bytes uncompressed)\n"
            "===== ERRORS/WARNINGS (last 100) =====\n"
            % raw_size)
    body = head + "\n".join(err_lines[-100:]) + "\n===== TAIL =====\n" + tail
    if not body.endswith("\n"):
        body += "\n"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError:
        return {"bytes": raw_size, "gz": gz_path, "tail_kept": False}
    return {
        "bytes": raw_size,
        "gz": os.path.basename(gz_path),
        "gz_bytes": os.path.getsize(gz_path),
        "tail_bytes": len(tail.encode("utf-8")),
    }

def gzip_file_in_place(path, keep_stub=True):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    gz_path = path + ".gz"
    raw_sha = hashlib.sha1(raw).hexdigest()
    if os.path.isfile(gz_path):
        try:
            with gzip.open(gz_path, "rb") as gz:
                prev = gz.read()
            if hashlib.sha1(prev).hexdigest() == raw_sha:
                if not keep_stub and os.path.isfile(path):
                    try:
                        _archive_aside(path)
                    except OSError:
                        pass
                return gz_path
        except (OSError, IOError, EOFError):
            pass
    tmp_path = gz_path + ".tmp"
    try:
        with gzip.open(tmp_path, "wb") as gz:
            gz.write(raw)
        os.replace(tmp_path, gz_path)
    except OSError:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return None
    if keep_stub:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("COMPACT: data in %s (%d bytes)\n"
                        % (os.path.basename(gz_path), len(raw)))
        except OSError:
            pass
    else:
        try:
            if os.path.isfile(path):
                _archive_aside(path)
        except OSError:
            pass
    return gz_path

class RunLogger:
    def __init__(self, out_dir, fresh=False, meta=None, log_mode=None,
                 save_raw=None):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.history_path = os.path.join(out_dir, "history.csv")
        self.manifest_path = os.path.join(out_dir, MANIFEST_NAME)
        self.log_mode = (log_mode if log_mode in (LOG_MODE_COMPACT,
                                                  LOG_MODE_FULL)
                         else LOG_MODE_COMPACT)
        try:
            self.save_raw = int(save_raw) if save_raw is not None else 1
        except (TypeError, ValueError):
            self.save_raw = 1
        self.iterations: list = []
        self._best_total = None
        self._pending_psf = None
        self._pending_index = None
        if fresh:
            try:
                for name in (self.history_path, self.manifest_path,
                             os.path.join(out_dir, "events.jsonl")):
                    if os.path.exists(name):
                        _archive_aside(name)
                for name in glob.glob(os.path.join(out_dir, "snapshot*.json")):
                    _archive_aside(name)
                for name in glob.glob(os.path.join(out_dir, "iter_*")):
                    _archive_aside(name)
            except OSError:
                pass
        if meta:
            try:
                with open(os.path.join(out_dir, "run_meta.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(dict(meta), f, indent=2, ensure_ascii=False)
            except Exception as exc:
                print(f"skip run_meta.json: {exc}", file=sys.stderr)

    def iter_dir(self, index):
        path = os.path.join(self.out_dir, f"iter_{index:04d}")
        os.makedirs(path, exist_ok=True)
        return path

    def seed_best(self, total):
        if isinstance(total, (int, float)):
            tval = float(total)
            if math.isfinite(tval) and (self._best_total is None
                                        or tval < self._best_total):
                self._best_total = tval

    def log_iteration(self, index, variables, objective, status, extra=None):
        path = self.iter_dir(index)
        record = {
            "index": index,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "variables": variables,
            "objective": objective,
            "status": status,
            "extra": extra or {},
        }
        with open(os.path.join(path, "record.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        self.iterations.append(record)
        self._append_history(record)
        keep_reason = self._apply_psf_policy(path, index, status, objective)
        self._write_manifest(record, path, keep_reason)
        return record

    def finalize(self):
        if self._pending_psf is None:
            return
        last = self.iterations[-1]["index"] if self.iterations else None
        try:
            if last is not None and self._pending_index != last:
                self._expire_ordinary(self._pending_psf, self._pending_index)
            else:
                self._refresh_manifest(self._pending_psf, self._pending_index,
                                       "ordinary", pending=False)
        except Exception as exc:
            print("finalize expire skipped: %s" % exc, file=sys.stderr)
        self._pending_psf = None
        self._pending_index = None
    def _expire_ordinary(self, iter_path, index=None):
        if self.save_raw == 2:
            self._archive_psf(iter_path)
        if self.log_mode == LOG_MODE_COMPACT:
            for name in ("result.txt", "mock_s21.txt"):
                path = os.path.join(iter_path, name)
                if os.path.isfile(path):
                    try:
                        gzip_file_in_place(path, keep_stub=False)
                    except Exception as exc:
                        print("gzip %s skipped: %s" % (name, exc),
                              file=sys.stderr)
        if index is not None:
            self._refresh_manifest(iter_path, index, "ordinary", pending=False)
    def _archive_psf(self, iter_path):
        psf = os.path.join(iter_path, "psf")
        if not os.path.isdir(psf):
            return
        try:
            _archive_aside(psf)
        except OSError as exc:
            print("psf archive skipped: %s" % exc, file=sys.stderr)
    def _apply_psf_policy(self, path, index, status, objective):
        total = (objective or {}).get("total")
        is_fail = (status or "") != "ok"
        is_first = (index == 0)
        is_best = False
        if not is_fail and isinstance(total, (int, float)):
            try:
                tval = float(total)
            except (TypeError, ValueError):
                tval = None
            if tval is not None and math.isfinite(tval):
                if self._best_total is None or tval < self._best_total:
                    is_best = True
                    self._best_total = tval
        if is_fail or is_first or is_best:
            return ("fail" if is_fail else
                    "first" if is_first else "best")
        if self._pending_psf and self._pending_psf != path:
            self._expire_ordinary(self._pending_psf, self._pending_index)
        self._pending_psf = path
        self._pending_index = index
        return "ordinary"
    def _file_entry(self, path):
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        return {"sha1": _sha1_file(path), "bytes": size}
    def _collect_files(self, path):
        files = {}
        for name in ("record.json", "result.txt", "result.txt.gz",
                     "input.scs", NETLIST_META_NAME, "spectre.log",
                     "spectre.log.gz", "mock_s21.txt", "mock_s21.txt.gz"):
            info = self._file_entry(os.path.join(path, name))
            if info:
                files[name] = info
        return files
    def _psf_state(self, path):
        psf = os.path.join(path, "psf")
        if os.path.isdir(psf):
            return "kept"
        try:
            names = os.listdir(path)
        except OSError:
            names = []
        if any(n.startswith("psf.old-") for n in names):
            return "archived"
        return "none"
    def _manifest_entry(self, path, index, keep_reason, when, pending,
                        extra=None):
        entry = {
            "index": int(index),
            "time": when,
            "log_mode": self.log_mode,
            "save_raw": self.save_raw,
            "psf_kept": keep_reason,
            "files": self._collect_files(path),
            "psf": self._psf_state(path),
            "provisional": bool(pending),
        }
        if extra:
            entry.update(extra)
        return entry
    def _write_manifest(self, record, path, keep_reason):
        entry = self._manifest_entry(
            path, record["index"], keep_reason, record["time"],
            keep_reason == "ordinary",
            {"status": record["status"],
             "objective_total": (record.get("objective") or {}).get("total")})
        meta_path = os.path.join(path, NETLIST_META_NAME)
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                entry["netlist"] = {
                    "sha1": meta.get("sha1"),
                    "base": meta.get("base"),
                    "params_line": meta.get("params_line"),
                    "fallback": meta.get("fallback"),
                }
            except Exception:
                pass
        self._append_manifest(entry)
        return entry
    def _refresh_manifest(self, path, index, keep_reason, pending=False):
        if index is None:
            return
        entry = self._manifest_entry(
            path, index, keep_reason, time.strftime("%Y-%m-%d %H:%M:%S"),
            pending, {"type": "retention"})
        self._append_manifest(entry)
        return entry
    def _append_manifest(self, entry):
        _append_jsonl(self.manifest_path, entry, fsync=True)
    def _append_history(self, record):
        exists = (os.path.exists(self.history_path)
                  and os.path.getsize(self.history_path) > 0)
        fields = ["index", "time", "status", "total"]
        var_names = sorted(record["variables"].keys())
        em_cols = []
        em_vals = []
        extra = record.get("extra") or {}
        em_actual = extra.get("em_actual") or {}
        if em_actual:
            for var in sorted(em_actual.keys()):
                ea = em_actual[var] or {}
                em_cols += ["delivered_%s" % var, "grid_%s" % var,
                            "distance_%s" % var]
                em_vals += [ea.get("delivered"), ea.get("grid_key"),
                            ea.get("distance")]
        with open(self.history_path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(fields + var_names + em_cols)
            row = [record["index"], record["time"], record["status"],
                   record["objective"]["total"]]
            row += [record["variables"][n] for n in var_names]
            row += em_vals
            w.writerow(row)

    def save_report(self, report):
        with open(os.path.join(self.out_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    def plot_convergence(self):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception:
            return None
        xs = [r["index"] for r in self.iterations]
        ys = [r["objective"]["total"] for r in self.iterations]
        plt.figure(figsize=(8, 5))
        plt.semilogy(xs, [max(y, 1e-12) for y in ys], marker="o")
        plt.xlabel("iteration")
        plt.ylabel("total error")
        plt.title("Optimization convergence")
        plt.grid(True)
        path = os.path.join(self.out_dir, "convergence.png")
        plt.savefig(path, dpi=120)
        plt.close()
        return path

def read_manifest(manifest_path):
    out = {}
    for entry in _read_jsonl(manifest_path, skip_bad=True):
        if isinstance(entry, dict) and "index" in entry:
            out[int(entry["index"])] = entry
    return out
SIM_FAIL_TOTAL = BIG_ERROR

class Settings:
    MODES = ("scalar", "spec", "pareto")

    @staticmethod
    def allowed_strategies():
        return available()
    def __init__(self, cfg):
        cfg = cfg or {}
        try:
            self.max_iter = int(cfg.get("max_iter", 100))
        except (TypeError, ValueError):
            raise ValueError("settings.max_iter must be an integer >= 1")
        if self.max_iter < 1:
            raise ValueError("settings.max_iter must be >= 1")
        self.patience = max(1, int(cfg.get("patience", 10)))
        self.improve_rel = float(cfg.get("improve_rel", 0.001))
        self.fail_limit = max(1, int(cfg.get("fail_limit", 5)))
        self.max_restarts = max(0, int(cfg.get("max_restarts", 2)))
        seed = cfg.get("seed", 0)
        self.seed = None if seed is None or seed == "" else int(seed)
        self.strategy = cfg.get("strategy", "simplex")
        if self.strategy is None or self.strategy == "":
            self.strategy = "simplex"
        self.strategy = str(self.strategy)
        allowed = self.allowed_strategies()
        if self.strategy not in allowed:
            raise ValueError(
                f"settings.strategy must be one of "
                f"{sorted(allowed)}, got {self.strategy!r}")
        self.mode = cfg.get("mode", "scalar")
        if self.mode is None or self.mode == "":
            self.mode = "scalar"
        self.mode = str(self.mode)
        if self.mode not in self.MODES:
            raise ValueError(
                f"settings.mode must be 'scalar', 'spec' or 'pareto', "
                f"got {self.mode!r}")
        if self.strategy == "nsga2" and self.mode == "scalar":
            self.mode = "pareto"

class Best:
    def __init__(self):
        self.total = None
        self.vars = None
        self.errors = None

    def update(self, total, values, errors, force=False):
        if not math.isfinite(total):
            return False
        if not force and self.total is not None and total >= self.total:
            return False
        self.total = total
        self.vars = dict(values)
        self.errors = errors
        return True
_EM_BRIDGE_MODES = ("once", "precover", "track")
_EM_BRIDGE_FALLBACKS = ("actual", "clip", "penalty")
_EM_DEVICE_TYPES = ("ind", "ct", "xfmr", "tl", "cpl")

def parse_em_bridge_cfg(cfg):
    raw = (cfg or {}).get("em_bridge")
    if not raw or not raw.get("enabled"):
        return None
    if not isinstance(raw, dict):
        raise ValueError("em_bridge 段必须是 JSON 对象")
    mode = (raw.get("mode") or "track").strip()
    if mode not in _EM_BRIDGE_MODES:
        raise ValueError("em_bridge.mode 须为 %s，收到 %r"
                         % ("/".join(_EM_BRIDGE_MODES), mode))
    fallback = (raw.get("fallback") or "actual").strip()
    if fallback not in _EM_BRIDGE_FALLBACKS:
        raise ValueError("em_bridge.fallback 须为 %s，收到 %r"
                         % ("/".join(_EM_BRIDGE_FALLBACKS), fallback))
    lib = (raw.get("library") or "work/em_lib").strip()
    devices = raw.get("devices") or []
    if not isinstance(devices, list):
        raise ValueError("em_bridge.devices 必须是数组")
    norm_devs = []
    for i, d in enumerate(devices):
        if not isinstance(d, dict):
            raise ValueError("em_bridge.devices[%d] 必须是对象" % i)
        dt = (d.get("device_type") or "").strip()
        if dt not in _EM_DEVICE_TYPES:
            raise ValueError("em_bridge.devices[%d].device_type 须为 %s，收到 %r"
                             % (i, "/".join(_EM_DEVICE_TYPES), dt))
        f_op = d.get("f_op")
        if f_op in (None, ""):
            raise ValueError("em_bridge.devices[%d].f_op 必填（不猜频率）" % i)
        tech = d.get("tech")
        if tech in (None, ""):
            tech = (raw.get("tech") or "").strip()
        if tech in (None, ""):
            raise ValueError("em_bridge.devices[%d].tech 必填（防跨工艺错配）" % i)
        tol = dict(d.get("tolerances") or raw.get("tolerances") or {})
        tol.setdefault("L0", 0.02)
        tol.setdefault("Q", 0.30)
        tol.setdefault("srf_margin", 1.5)
        tol.setdefault("q_mode", "warn")
        tol.setdefault("theta_tol_deg", 2.0)
        grid = dict(d.get("grid_spec") or raw.get("grid_spec") or {})
        grid.setdefault("step_rel", tol.get("L0", 0.02))
        grid.setdefault("step_abs_deg", 2.0)
        step_rel = float(grid.get("step_rel", 0.02))
        for m in ("L0", "Lp", "Ls", "Z0", "Ze", "Zo"):
            t = tol.get(m)
            if t is not None and step_rel > 2.0 * float(t):
                raise ValueError(
                    "em_bridge.devices[%d] 格栅过疏：step_rel=%.4f > 2×tol_%s=%.4f"
                    % (i, step_rel, m, 2.0 * float(t)))
        nd = {
            "var_prefix": d.get("var_prefix") or d.get("target_var") or "",
            "device_type": dt,
            "target_var": d.get("target_var") or d.get("var_prefix") or "",
            "f_op": f_op,
            "band": d.get("band"),
            "tech": tech,
            "emx_cfg_hash": d.get("emx_cfg_hash") or raw.get("emx_cfg_hash") or "",
            "tolerances": tol,
            "grid_spec": grid,
            "fixed_geom": dict(d.get("fixed_geom") or {}),
            "geom_bounds": dict(d.get("geom_bounds") or {}),
            "geom_prior": d.get("geom_prior"),
        }
        norm_devs.append(nd)
    em_opt = dict(raw.get("em_opt") or {})
    budget = dict(em_opt.get("budget") or {})
    budget.setdefault("max_em", 20)
    budget.setdefault("time_cap_s", 600)
    em_opt["budget"] = budget
    return {
        "enabled": True,
        "mode": mode,
        "fallback": fallback,
        "library": lib,
        "devices": norm_devs,
        "em_opt": em_opt,
        "prefetch": bool(raw.get("prefetch", False)),
        "time_budget_s_total": float(raw.get("time_budget_s_total", 1800.0)),
    }

class Optimization:
    def __init__(self, cfg, mock=False, max_iter=None, on_event=None,
                 stop_check=None, resume=None):
        self.cfg = cfg
        self.on_event = on_event
        self.stop_check = stop_check
        self._fresh = (resume is False)
        self.settings = Settings(cfg.get("settings", {}))
        self.max_iter = (self.settings.max_iter if max_iter is None
                         else int(max_iter))
        if self.max_iter < 1:
            raise ValueError("max_iter must be >= 1")
        self.names, x0, self.bounds = build_variables(cfg)
        self.targets = [Target(t) for t in cfg["targets"]]
        self.em_bridge_cfg = parse_em_bridge_cfg(cfg)
        out_dir = cfg.get("output_dir", "logs/run")
        self.out_dir = os.path.abspath(os.path.expanduser(out_dir))
        history = load_previous(self.out_dir) if resume is not False else []
        fingerprint = config_fingerprint(cfg)
        if history:
            old_fp = _read_json_dict(
                os.path.join(self.out_dir, "run_meta.json")).get("config_sha1")
            if old_fp and old_fp != fingerprint:
                raise ValueError(
                    "续跑目录 %s 属于另一份配置（circuit/targets/variables/"
                    "netlist 已变）；请换一个 output_dir，或设 "
                    "\"resume\": false 新开一轮。" % self.out_dir)
            if not old_fp:
                print("resume: 旧目录没有配置指纹，按旧行为继续。",
                      file=sys.stderr)
        self.logger = RunLogger(self.out_dir, fresh=(resume is False),
                                meta={"circuit": circuit_id(cfg),
                                      "config_sha1": fingerprint,
                                      "name": str(cfg.get("name") or ""),
                                      "created": time.strftime(
                                          "%Y-%m-%d %H:%M:%S"),
                                      "output_dir": self.out_dir,
                                      "resumed": bool(history)},
                                log_mode=_log_mode_of(cfg),
                                save_raw=_save_raw_of(cfg))
        self.runner = self._build_runner(mock)
        self.best = Best()
        self.best_it = None
        self.best_curves = None
        self.best_result = None
        self.restarts = 0
        self.no_improve = 0
        self.start = ((max(rec.get("index", -1) for rec in history) + 1)
                      if history else 0)
        self._restore(history)
        if self.best.total is not None:
            self.logger.seed_best(self.best.total)
        self._history_seeds = self._collect_history_seeds(history)
        best_vars = self.best.vars
        start_point = ([best_vars.get(n, x0[i])
                        for i, n in enumerate(self.names)]
                       if best_vars else x0)
        self.start_values = {}
        for i, n in enumerate(self.names):
            self.start_values[n] = start_point[i]
        self._build_algorithm()
        self._build_engine_tools()
    def _build_transform(self):
        specs = []
        for v in self.cfg.get("variables", []) or []:
            name = v["name"]
            if name not in self.names:
                continue
            i = self.names.index(name)
            lo, hi = self.bounds[i]
            spec = {"name": name, "init": self.start_values[name],
                    "min": lo, "max": hi}
            if "scale" in v:
                spec["scale"] = v["scale"]
            if v.get("integer"):
                spec["integer"] = True
            specs.append(spec)
        return Transform(specs, self.cfg.get("linking") or [])
    def _build_algorithm(self):
        strat = self.settings.strategy
        self.transform = self._build_transform()
        err_t, obj_t, con_t = classify_roles(self.targets)
        n_obj = max(1, len(obj_t)) if self.settings.mode != "scalar" else 1
        n_constr = len(con_t) + (len(err_t)
                                 if self.settings.mode != "scalar" else 0)
        opts = {}
        if strat == "weighted" and self.settings.mode != "scalar" and obj_t:
            weights = [_weight_of(t) for t in obj_t]
            if sum(weights) > 0.0:
                opts["weights"] = weights
        port = self.cfg.get("settings", {}).get("portfolio") or {}
        if port:
            opts["members"] = port.get("members")
            opts["ucb_c"] = port.get("ucb_c", 1.0)
        init_pts = self._load_init_points()
        self.algo = build_optimizer(
            strat, self.transform, self.start_values, self.names,
            seed=self.settings.seed, n_obj=n_obj, n_constr=n_constr,
            budget=self.max_iter, **opts)
        if init_pts:
            self.algo.inject(init_pts)
    def _load_init_points(self):
        path = (self.cfg.get("settings", {}) or {}).get("init_points")
        if not path:
            return []
        path = os.path.abspath(os.path.expanduser(str(path)))
        if not os.path.exists(path):
            print(f"init_points not found: {path}")
            return []
        pts = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in
                         line.replace(",", " ").split() if p.strip()]
                try:
                    vals = [parse_eng(p) for p in parts]
                except Exception:
                    continue
                if len(vals) == len(self.names):
                    pts.append(dict(zip(self.names, vals)))
        return pts
    def _build_engine_tools(self):
        self.penalty_ledger = PenaltyLedger()
        self.journal = RunJournal(self.out_dir)
    def _build_runner(self, mock):
        if mock:
            runner = MockRunner(self.cfg)
        else:
            skill_dir = self.cfg.get("skill_dir") \
                or os.path.dirname(os.path.abspath(__file__))
            runner = RealRunner(self.cfg, skill_dir)
        le_on = bool((self.cfg.get("layout_em") or {}).get("enabled"))
        em_on = bool(self.em_bridge_cfg and self.em_bridge_cfg.get("enabled"))
        if em_on and le_on:
            raise ValueError(
                "em_bridge 与 layout_em 互斥：电学参数模式与几何参数模式"
                "不得同时启用（请关掉其中一个）")
        if em_on:
            try:
                import em_bridge
            except ImportError as exc:
                raise ImportError(
                    "em_bridge.enabled=true 但无法加载 em_bridge 模块：%s"
                    "（请安装/恢复 em_bridge.py，或关闭 em_bridge）" % exc)
            try:
                runner = em_bridge.EmBridge(
                    self.cfg, runner,
                    library=None, em_optimizer=None)
            except Exception as exc:
                print("em_bridge.EmBridge 加载失败: %s" % exc)
                raise
        elif le_on:
            try:
                import librf_opt
                runner = librf_opt.GeomRunner(self.cfg, runner)
            except Exception as exc:
                print("librf_opt.GeomRunner 加载失败: %s" % exc)
                raise
        return runner
    def _restore(self, history):
        for rec in history:
            total = rec.get("objective", {}).get("total")
            if rec.get("status") == "ok" and total is not None:
                values = _values_from_record(rec, self.names, strict=True)
                if self.best.update(total, values, rec["objective"]):
                    self.best_it = rec.get("index")
        tail = (history[-self.settings.patience:]
                if self.settings.patience > 0 else [])
        for rec in tail:
            if rec.get("status") != "ok":
                continue
            total = rec.get("objective", {}).get("total")
            if total is None or self.best.total is None:
                continue
            if total >= self.best.total * (1.0 - self.settings.improve_rel):
                self.no_improve += 1
    def _collect_history_seeds(self, history):
        seeds = []
        for rec in history:
            if rec.get("status") != "ok":
                continue
            total = (rec.get("objective") or {}).get("total")
            if not isinstance(total, (int, float)):
                continue
            if not math.isfinite(float(total)):
                continue
            values = _values_from_record(rec, self.names, require_all=True)
            if values is not None:
                seeds.append((values, float(total)))
        return seeds
    def _seed_from_history(self):
        if not self._history_seeds:
            return
        try:
            self.algo.seed_history(self._history_seeds)
            print("history warm-start: %d points folded into %s"
                  % (len(self._history_seeds),
                     getattr(self.algo, "name", "algo")))
        except Exception as exc:
            print("history seed failed: %s" % exc)

    def run(self):
        if self._fresh or not self._try_restore_snapshot():
            self._seed_from_history()
        fails = 0
        for it in range(self.start, self.max_iter):
            self._last_it = it
            if self.stop_check is not None and self.stop_check():
                return "stopped"
            values = self._ask_point()
            result = self.runner.run(values, self.logger.iter_dir(it))
            self._reject_empty_curves(it, result)
            if result.status != "ok":
                kind = classify(result.status, result.message,
                                wall=result.wall,
                                timeout=self.cfg.get("sim_timeout"))
                if kind == "INFRA" and fails + 1 < self.settings.fail_limit:
                    print(f"[{it:03d}] INFRA error, retrying: "
                          f"{result.message}")
                    result = self.runner.run(values,
                                             self.logger.iter_dir(it))
                    self._reject_empty_curves(it, result)
                    if result.status == "ok":
                        fails = 0
                        total, met = self._log_success(it, values, result)
                        if met:
                            return "targets_met"
                        reason = self._stall_action(it)
                        if reason:
                            return reason
                        continue
                fails += 1
                self._log_failure(it, values, result)
                if self.stop_check is not None and self.stop_check():
                    return "stopped"
                if fails >= self.settings.fail_limit:
                    return "sim_failures"
                continue
            fails = 0
            total, met = self._log_success(it, values, result)
            if it % 10 == 9 or it == self.max_iter - 1:
                self._write_snapshot()
            if met:
                return "targets_met"
            reason = self._stall_action(it)
            if reason:
                return reason
        met = (isinstance(self.best.errors, dict)
               and bool(self.best.errors.get("met")))
        if met:
            return "targets_met"
        if self.logger.iterations:
            statuses = [str(rec.get("status") or "")
                        for rec in self.logger.iterations]
            if all(st and st != "ok" for st in statuses):
                return "sim_failures"
        return "max_iterations"
    def _reject_empty_curves(self, it, result):
        if result.status == "ok" and not result.curves:
            raise RuntimeError(
                "第 %d 轮没有解析到任何输出曲线（%s）。常见原因：网表缺少"
                "对应分析语句、或目标里的输出名与 ADE 输出不一致。已中止，"
                "避免空跑；请检查网表分析与目标输出名后重跑。"
                % (it, result.message or "无消息"))
    def _emit(self, event):
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception as exc:
            print(f"event callback failed: {exc}")
    def _ask_point(self):
        point = self.algo.ask()
        return {name: min(hi, max(lo, point[i]))
                for i, (name, (lo, hi)) in enumerate(zip(self.names, self.bounds))}
    def _try_restore_snapshot(self):
        try:
            state = self.journal.load_latest()
        except Exception as exc:
            print(f"snapshot load failed: {exc}")
            return False
        if not state or not state.get("algo"):
            return False
        try:
            self.algo.restore(state["algo"])
        except Exception as exc:
            print(f"algo restore failed: {exc}")
            return False
        self.restarts = int(state.get("restarts", self.restarts))
        self.no_improve = int(state.get("no_improve", 0))
        next_idx = state.get("index_next")
        if next_idx is None:
            return True
        try:
            events = self.journal.read_events()
        except Exception as exc:
            print(f"event replay read failed: {exc}")
            return True
        seq = state.get("events")
        if isinstance(seq, int) and seq >= 0:
            events = events[seq:]
        replayed = 0
        for ev in events:
            if ev.get("type") == "kick":
                pts = ev.get("points") or []
                if pts:
                    self.algo.inject(pts)
                continue
            if ev.get("type") == "restart":
                self.algo.restart(spread=ev.get("spread", 0.25))
                continue
            if ev.get("index", -1) < next_idx:
                continue
            status = str(ev.get("status", ""))
            if status != "ok" and not status.startswith("fail"):
                continue
            total = ev.get("total")
            if total is None:
                continue
            self.algo.ask()
            self.algo.tell(total, F=ev.get("F"), G=ev.get("G"))
            replayed += 1
        print(f"restored snapshot: evals="
              f"{getattr(self.algo, 'eval_count', '?')} "
              f"replayed={replayed}")
        return True
    def _write_snapshot(self):
        try:
            # events 计数必须与 algo 状态同一时刻；续跑从该下标回放
            self.journal.snapshot({
                "algo": self.algo.snapshot(),
                "best_total": self.best.total,
                "best_vars": self.best.vars,
                "restarts": self.restarts,
                "no_improve": self.no_improve,
                "index_next": getattr(self, "_last_it", -1) + 1,
                "events": len(self.journal.read_events()),
            })
        except Exception as exc:
            print(f"snapshot write failed: {exc}")
    def _log_failure(self, it, values, result):
        total = self.penalty_ledger.penalty("DNC", SIM_FAIL_TOTAL)
        objective = {"total": total,
                     "errors": {"simulation": total}}
        self.algo.tell(total)
        self.logger.log_iteration(it, _fmt_vars(values), objective,
                                  f"fail:{result.message}")
        self._journal_event(it, values, None, None, total,
                            f"fail:{result.message}")
        print(f"[{it:03d}] sim fail: {result.message} "
              f"耗时{result.wall:.2f}s")
        self._emit({"type": "iteration", "index": it, "status": "fail",
                    "message": result.message, "total": None,
                    "vars": values, "errors": {},
                    "wall": result.wall, "timing": dict(result.timing or {}),
                    "best_total": self.best.total,
                    "best_vars": (dict(self.best.vars)
                                  if self.best.vars else None)})
    def _journal_event(self, it, values, f_vec, g_vec, total, status):
        try:
            self.journal.log_event({
                "index": it, "status": status,
                "vars": dict(values),
                "F": f_vec, "G": g_vec, "total": total,
                "algo": getattr(self.algo, "name", ""),
            })
        except Exception as exc:
            print(f"journal write failed: {exc}")
    def _log_success(self, it, values, result):
        res = evaluate_roles(self.targets, result.curves, self.settings.mode)
        objective = {"total": res["total"], "errors": res["errors"],
                     "met": bool(res.get("met", res["total"] <= 0.0))}
        f_vec, g_vec = res["F"], res["G"]
        if self.settings.mode == "scalar":
            f_vec, g_vec = None, None
        total = objective["total"]
        self.algo.tell(total, F=f_vec, G=g_vec)
        em_actual = getattr(result, "em_actual", None) or {}
        extra = {"em_actual": em_actual} if em_actual else None
        self.logger.log_iteration(it, _fmt_vars(values), objective, "ok",
                                  extra=extra)
        self._journal_event(it, values, f_vec, g_vec, total, "ok")
        det = " ".join(f"{k}={v:.2f}s" for k, v in (result.timing or {}).items()
                       if k != "total")
        print(f"[{it:03d}] total={total:.6g} 耗时{result.wall:.2f}s "
              f"({det}) vars=" +
              " ".join(f"{n}={format_eng(values[n])}" for n in self.names))
        self._track_best(total, values, objective, objective["met"], it)
        if self.best_it == it:
            self.best_curves = result.curves
            self.best_result = result.raw_path
        self._emit({"type": "iteration", "index": it, "status": "ok",
                    "total": total, "vars": values,
                    "errors": objective["errors"],
                    "met": objective["met"],
                    "wall": result.wall, "timing": dict(result.timing or {}),
                    "best_total": self.best.total,
                    "best_vars": (dict(self.best.vars)
                                  if self.best.vars else None)})
        return total, objective["met"]
    def _track_best(self, total, values, objective, met=True, it=None):
        best = self.best.total
        if best is None:
            self.best.update(total, values, objective)
            self.best_it = it
            self.no_improve = 0
            return
        best_met = True
        if isinstance(self.best.errors, dict):
            best_met = bool(self.best.errors.get("met", True))
        if met and not best_met:
            self.best.update(total, values, objective, force=True)
            self.best_it = it
            self.no_improve = 0
            return
        if best_met and not met:
            self.no_improve += 1
            return
        if total >= best * (1.0 - self.settings.improve_rel):
            self.no_improve += 1
            return
        self.best.update(total, values, objective)
        self.best_it = it
        self.no_improve = 0
    def _stall_action(self, it):
        if self.no_improve < self.settings.patience:
            return None
        if getattr(self, "_kick_tries", 0) < 3:
            self._kick_tries = getattr(self, "_kick_tries", 0) + 1
            self.no_improve = 0
            base_vals = self.best.vars or self.start_values
            x_best = [float(base_vals.get(n, self.start_values[n]))
                      for n in self.names]
            rng = random.Random((self.settings.seed or 0)
                                + 977 * it + self._kick_tries)
            kicks = make_kick(x_best, self.bounds, rng)
            if kicks:
                self.algo.inject([dict(zip(self.names, kicks[0]))])
            try:
                self.journal.log_event({
                    "type": "kick", "index": it,
                    "points": ([dict(zip(self.names, kicks[0]))]
                               if kicks else [])})
            except Exception:
                pass
            print(f"[{it:03d}] kick {self._kick_tries}/3")
            self._emit({"type": "kick", "index": it,
                        "count": self._kick_tries})
            return None
        if self.restarts < self.settings.max_restarts:
            self.restarts += 1
            self.no_improve = 0
            self._kick_tries = 0
            self.algo.restart(spread=0.25)
            try:
                self.journal.log_event({"type": "restart", "index": it,
                                        "spread": 0.25})
            except Exception:
                pass
            print(f"[{it:03d}] restart search {self.restarts}")
            self._emit({"type": "restart", "index": it,
                        "count": self.restarts})
            return None
        if _all_at_bounds(self.best.vars, self.names, self.bounds):
            return "no_feasible_solution"
        return "no_improvement"

    def report(self, reason):
        rep = {
            "stop_reason": reason,
            "best_total_error": self.best.total,
            "best_variables": _fmt_vars(self.best.vars) if self.best.vars else None,
            "best_variables_raw": self.best.vars,
            "best_errors": self.best.errors,
            "best_met": ((self.best.errors or {}).get("met")
                         if isinstance(self.best.errors, dict) else None),
            "iterations": len(self.logger.iterations),
            "iterations_total": self.start + len(self.logger.iterations),
            "best_iter": self.best_it,
            "restarts": self.restarts,
        }
        try:
            hist_vars, hist_vals = [], []
            for rec in self.logger.iterations:
                row = _values_from_record(rec)
                hist_vars.append(row)
                total = (rec.get("objective") or {}).get("total")
                hist_vals.append(total if total is not None else 0.0)
            rep["summary"] = build_summary(
                {"vars": hist_vars, "vals": hist_vals,
                 "init_vars": self.start_values,
                 "bounds": dict(zip(self.names, self.bounds)),
                 "restarts": self.restarts,
                 "evals": len(hist_vals),
                 "failures": self._failure_counts()},
                self.best.vars or {},
                extra={"strategy": self.settings.strategy,
                       "mode": self.settings.mode,
                       "stop_reason": reason})
        except Exception as exc:
            rep["summary_error"] = str(exc)
        return rep
    def _failure_counts(self):
        counts = {}
        for rec in self.logger.iterations:
            status = str(rec.get("status") or "")
            if status and status != "ok":
                kind = status.split(":", 1)[0].strip() or "fail"
                counts[kind] = counts.get(kind, 0) + 1
        return counts

def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    # state_dir 相对路径以配置文件目录为基准
    for key in ("state_dir",):
        value = cfg.get(key)
        if not isinstance(value, str) or not value.strip():
            if key in cfg:
                cfg[key] = ""
            continue
        if not os.path.isabs(value):
            cfg[key] = os.path.join(base, value)
    return cfg

def build_variables(cfg):
    names = []
    x0 = []
    bounds = []
    for v in cfg["variables"]:
        name = v["name"]
        if name in names:
            raise ValueError(f"duplicate variable name: {name}")
        lo = parse_eng(v["min"])
        hi = parse_eng(v["max"])
        if lo > hi:
            raise ValueError(f"variable {name}: min is greater than max")
        names.append(name)
        x0.append(parse_eng(v["init"]))
        bounds.append((lo, hi))
    if not names:
        raise ValueError("config needs at least one variable")
    fixed_names = [str(v.get("name") or "")
                   for v in (cfg.get("fixed_variables") or [])
                   if isinstance(v, dict)]
    dup = [n for n in names if n in fixed_names]
    if dup:
        raise ValueError("variable is both optimized and fixed: %s"
                         % ", ".join(dup))
    return names, x0, bounds

def _iter_order(path):
    name = os.path.basename(os.path.dirname(path))
    match = re.search(r"(\d+)", name)
    return int(match.group(1)) if match else 0

def load_previous(out_dir):
    records = []
    paths = []
    for path in glob.glob(os.path.join(out_dir, "iter_*", "record.json")):
        name = os.path.basename(os.path.dirname(path))
        if name.startswith("iter_") and name[5:].isdigit():
            paths.append(path)
    paths.sort(key=_iter_order)
    for path in paths:
        try:
            with open(path) as f:
                records.append(json.load(f))
        except Exception as exc:
            print(f"skip bad record {path}: {exc}", file=sys.stderr)
    return records

def stop_all_runners():
    RealRunner.stop_all()
    try:
        import librf_opt
        stop = getattr(librf_opt, "stop_all", None)
        if stop is not None:
            stop()
    except Exception:
        pass

def _auto_netlist_mode(cfg):
    raw = (cfg or {}).get("auto_netlist", "auto")
    if raw is False or raw is None:
        return "off"
    if isinstance(raw, str):
        val = raw.strip().lower()
        if val in ("off", "false", "no", "manual", "never"):
            return "off"
        if val in ("on", "true", "yes", "always", "force"):
            return "always"
    if raw is True:
        return "always"
    return "auto"

def prepare_netlist(cfg, mock=False):
    mode = _auto_netlist_mode(cfg)
    if mock:
        return {"status": "skipped", "reason": "mock runner"}
    if mode == "off":
        return {"status": "skipped", "reason": "auto_netlist=false"}
    if cfg.get("_skip_auto_netlist"):
        return {"status": "skipped", "reason": "stage already prepared"}
    try:
        import netlist_create
    except ImportError as exc:
        report = {"status": "skipped",
                  "reason": "netlist_create.py 不可用: %s" % exc}
        if mode == "always":
            if cfg.get("netlist_strict"):
                raise RuntimeError(report["reason"])
            print("自动网表跳过: %s" % exc, file=sys.stderr)
        return report
    try:
        report = netlist_create.ensure_netlist(cfg)
    except Exception as exc:
        report = {"status": "fail", "message": str(exc), "errors": [str(exc)]}
    status = report.get("status")
    if status == "ok":
        cfg["netlist"] = report.get("path") or cfg.get("netlist")
        if report.get("design", {}).get("view"):
            cfg["netlist_view"] = report["design"]["view"]
        print("自动网表: %s (%.2fs)" %
              (cfg.get("netlist"), float(report.get("wall") or 0.0)))
    elif status in ("fail", "fallback"):
        msg = report.get("message") or "网表重新生成失败"
        if cfg.get("netlist_strict"):
            raise RuntimeError("自动网表失败: %s" % msg)
        print("自动网表失败，沿用现有网表: %s" % msg, file=sys.stderr)
    else:
        print("自动网表跳过: %s" % (report.get("reason") or status or ""),
              file=sys.stderr)
    return report

def _resume_flag(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() not in (
            "", "0", "false", "no", "off", "none", "disabled")
    return bool(value)

def run_optimization(cfg, mock=False, max_iter=None, on_event=None,
                     stop_check=None, resume=None):
    if resume is None:
        resume = _resume_flag((cfg or {}).get("resume"))
    netlist_report = prepare_netlist(cfg, mock=mock)
    if not mock:
        summary = preflight_netlist(cfg)
        if summary:
            print("网表预检: %s" % summary)
    opt = Optimization(cfg, mock=mock, max_iter=max_iter,
                       on_event=on_event, stop_check=stop_check,
                       resume=resume)
    try:
        audit_file(os.path.join(opt.out_dir, "audit.jsonl"))
        audit("write", os.path.join(opt.out_dir, "audit.jsonl"),
              "审计日志开始")
    except Exception:
        pass
    try:
        reason = opt.run()
    finally:
        stop_all_runners()
        try:
            opt.logger.finalize()
        except Exception as exc:
            print("log finalize skipped: %s" % exc, file=sys.stderr)
    report = opt.report(reason)
    report["netlist_create"] = netlist_report
    try:
        curve_path = save_best_curves(opt)
        if curve_path:
            report["best_curves"] = curve_path
    except Exception as exc:
        print("best_curves skipped: %s" % exc, file=sys.stderr)
    plot = opt.logger.plot_convergence()
    if plot:
        report["convergence_plot"] = plot
    opt.logger.save_report(report)
    return report

def save_best_curves(opt):
    curves = getattr(opt, "best_curves", None)
    if not curves:
        return None
    payload = {"iter": getattr(opt, "best_it", None),
               "variables": _fmt_vars(opt.best.vars) if opt.best.vars else None,
               "result": getattr(opt, "best_result", None),
               "curves": {}}
    for name, data in curves.items():
        try:
            xs, ys = data
        except (TypeError, ValueError):
            continue
        payload["curves"][name] = {"x": [float(v) for v in xs],
                                   "y": [float(v) for v in ys]}
    if not payload["curves"]:
        return None
    path = os.path.join(opt.out_dir, "best_curves.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path

def _load_em_align():
    try:
        import em_align
        return em_align
    except ImportError as exc:
        raise RuntimeError("EM 对齐需要 em_align.py: %s" % exc)

def _simulate_once(cfg, netlist, values, out_dir):
    import copy
    cfg2 = copy.deepcopy(cfg)
    cfg2["netlist"] = netlist
    cfg2["_skip_auto_netlist"] = True
    cfg2["auto_netlist"] = False
    os.makedirs(out_dir, exist_ok=True)
    skill_dir = cfg2.get("skill_dir") or os.path.dirname(os.path.abspath(__file__))
    runner = RealRunner(cfg2, skill_dir)
    try:
        result = runner.run(values, out_dir)
    finally:
        runner.terminate()
        RealRunner.live.discard(runner)
    if not result.curves:
        raise RuntimeError("EM 网表单次仿真失败: %s" % result.message)
    return result.curves, result

def _curves_from_best_json(run_dir):
    path = os.path.join(run_dir, "best_curves.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    curves = {}
    for name, pair in (data.get("curves") or {}).items():
        if isinstance(pair, dict) and "x" in pair and "y" in pair:
            curves[name] = (list(pair["x"]), list(pair["y"]))
    return curves or None

def _ideal_netlist_for_stage2(cfg, stage1_dir):
    path = cfg.get("netlist")
    if path and os.path.isfile(os.path.expanduser(path)):
        return os.path.expanduser(path)
    hit = os.path.join(stage1_dir, "deck_base.scs")
    if os.path.isfile(hit):
        return hit
    hit = os.path.join(stage1_dir, "iter_0000", "input.scs")
    if os.path.isfile(hit):
        return hit
    raise ValueError("找不到第一阶段的理想网表；请在任务 JSON 里保留 netlist 字段")

def run_em_align(cfg, stage1_dir, em_curve=None, em_netlist=None,
                 output_dir=None, max_iter=None, tol=None, mock=False,
                 on_event=None):
    import copy
    mod = _load_em_align()
    stage1_dir = os.path.abspath(os.path.expanduser(str(stage1_dir)))
    if not os.path.isdir(stage1_dir):
        raise ValueError("第一阶段 run 目录不存在: %s" % stage1_dir)
    stage1 = mod.load_run_best(stage1_dir)
    x_opt = stage1.get("variables") or {}
    y_opt = stage1.get("curves") or {}
    if not x_opt:
        raise ValueError("第一阶段没有可用的最优变量: %s" % stage1_dir)
    em_cfg = cfg.get("em_align") or {}
    em_curve = em_curve or em_cfg.get("em_curve")
    em_netlist = em_netlist or em_cfg.get("em_netlist")
    stage2_out = os.path.abspath(os.path.expanduser(
        output_dir or em_cfg.get("output_dir")
        or os.path.join(stage1_dir, "em_align")))
    if stage2_out == stage1_dir:
        stage2_out = os.path.join(stage1_dir, "em_align")
    if os.path.isdir(stage2_out) and os.listdir(stage2_out) \
            and not em_cfg.get("reuse_output"):
        stage2_out = "%s_%s" % (stage2_out, time.strftime("%Y%m%d-%H%M%S"))
    source = None
    if em_curve:
        em_curve = os.path.abspath(os.path.expanduser(str(em_curve)))
        y_em = mod.load_curves(
            em_curve,
            outputs_state=(os.path.join(
                os.path.expanduser(cfg.get("state_dir") or ""),
                "outputs.state")
                if cfg.get("state_dir") else None))
        source = {"kind": "file", "path": em_curve}
    elif em_netlist:
        if mock:
            raise ValueError("mock 模式不能仿真 EM 网表；请用 em_curve 或去掉 --mock")
        em_netlist = os.path.abspath(os.path.expanduser(str(em_netlist)))
        y_em, _res = _simulate_once(
            cfg, em_netlist, x_opt,
            os.path.join(stage2_out, "_em_once"))
        source = {"kind": "netlist", "path": em_netlist}
    else:
        raise ValueError("EM 对齐需要 em_curve 或 em_netlist 之一")
    names = em_cfg.get("outputs")
    if not names:
        wanted = [t.get("output") for t in (cfg.get("targets") or [])]
        names = []
        for name in wanted:
            if name in y_em and name not in names:
                names.append(name)
        names = names or None
    else:
        if isinstance(names, str):
            names = [names]
        names = [n for n in names if n in y_em]
    targets = mod.build_match_targets(
        y_em, names=names,
        max_points=int(em_cfg.get("max_points", 81)),
        tol=float(tol if tol is not None else em_cfg.get("tol", 0.1)),
        band=em_cfg.get("band"))
    if not targets:
        raise ValueError("EM 曲线里没有可匹配的曲线（至少 2 个频点）: %s"
                         % ", ".join(sorted(y_em)))
    matched = []
    for t in targets:
        if t.get("output") not in matched:
            matched.append(t.get("output"))
    stage2 = copy.deepcopy(cfg)
    stage2.pop("em_align", None)
    stage2["name"] = "%s [EM 对齐]" % (cfg.get("name") or "run")
    stage2["targets"] = targets
    stage2["auto_netlist"] = False
    stage2["_skip_auto_netlist"] = True
    stage2["resume"] = False
    stage2["output_dir"] = stage2_out
    stage2["netlist"] = _ideal_netlist_for_stage2(cfg, stage1_dir)
    settings = dict(stage2.get("settings") or {})
    settings["max_iter"] = int(max_iter or em_cfg.get("max_iter")
                               or settings.get("max_iter") or 80)
    settings["patience"] = int(em_cfg.get("patience",
                                          settings.get("patience", 15)))
    if em_cfg.get("strategy"):
        settings["strategy"] = str(em_cfg["strategy"])
    else:
        settings["strategy"] = "coordinate"
    stage2["settings"] = settings
    bounds = {}
    for v in stage2.get("variables") or []:
        name = v.get("name")
        bounds[name] = (parse_eng(v["min"]), parse_eng(v["max"]))
        if name in x_opt:
            val = float(x_opt[name])
            lo, hi = bounds[name]
            v["init"] = format_eng(min(max(val, lo), hi))
    stage2_report = run_optimization(
        stage2, mock=mock, max_iter=settings["max_iter"],
        on_event=on_event, resume=False)
    y_ideal = (_curves_from_best_json(stage2_out)
               or (mod.load_run_best(stage2_out).get("curves") if
                   os.path.isdir(stage2_out) else {}) or {})
    var_specs = {v.get("name"): v for v in (cfg.get("variables") or [])}
    def _only(curves):
        return {n: curves[n] for n in matched if n in (curves or {})}
    correction = mod.correction_report(
        x_opt, stage2_report.get("best_variables_raw") or {},
        _only(y_opt), _only(y_em), _only(y_ideal), var_specs=var_specs,
        rms_tol=float(em_cfg.get("rms_tol", 0.1)),
        max_tol=float(em_cfg.get("max_tol", 0.5)))
    payload = {
        "stage1_run": stage1_dir,
        "stage2_run": stage2_out,
        "stage1_iter": stage1.get("iter"),
        "stage1_variables": {k: format_eng(v) for k, v in x_opt.items()},
        "em_source": source,
        "targets": targets,
        "stage2_stop_reason": stage2_report.get("stop_reason"),
        "stage2_variables": stage2_report.get("best_variables"),
        "correction": correction,
        "aligned": correction.get("aligned"),
        "stage2_report_path": os.path.join(stage2_out, "report.json"),
    }
    json_path, md_path = mod.write_report(payload, stage2_out)
    payload["report_json"] = json_path
    payload["report_md"] = md_path
    stage2_report["em_align"] = payload
    return stage2_report

def _fmt_vars(values):
    if not values:
        return None
    return {k: format_eng(v) for k, v in values.items()}

def _all_at_bounds(values, names, bounds, tol=1e-6):
    if not values:
        return False
    for i, name in enumerate(names):
        lo, hi = bounds[i]
        span = hi - lo
        v = values.get(name)
        if v is None:
            return False
        if abs(v - lo) > tol * span and abs(v - hi) > tol * span:
            return False
    return True

def dump_state(cfg):
    raw = str(cfg.get("state_dir") or "").strip()
    if not raw:
        raise SystemExit("--dump-state 需要配置里的 state_dir（ADE 状态目录），"
                         "当前为空或未设置。")
    state_dir = os.path.expanduser(raw)
    info = summarize(state_dir)
    print(json.dumps(info, indent=2))
    return info
EXAMPLE_CFG = {
    'analyses': 'sp', 'cell': 'test_cs_driver', 'library': 'MyLib',
    'name': 'test_cs_driver S21 flatness optimization',
    'output_dir': 'logs/test_cs_driver', 'save_raw': 1,
    'log_mode': 'compact', 'sim_timeout': 1800, 'state': 'spectre_state1',
    'state_dir': '', 'view': 'schematic', 'virtuoso_bin': '',
    'work_dir': '.', 'use_config': False,
    'auto_netlist': True, 'netlist_view': '',
    'settings': {'fail_limit': 5, 'improve_rel': 0.001, 'max_iter': 100,
                 'patience': 10, 'seed': 0},
    'targets': [
        {'band': ['24G', '44G'], 'comment': 'in-band S21 minimum >= 3 dB',
         'goal': 3, 'metric': 'band_min', 'op': '>=', 'output': 'S21 dB20',
         'tol': 0.5, 'weight': 1.0},
        {'band': ['24G', '44G'], 'comment': 'in-band flatness <= 1 dB',
         'goal': 1, 'metric': 'band_flatness', 'op': '<=',
         'output': 'S21 dB20', 'tol': 0.2, 'weight': 1.0}],
    'variables': [
        {'init': '250p', 'max': '350p', 'min': '150p', 'name': 'L1'},
        {'init': '280p', 'max': '350p', 'min': '150p', 'name': 'L2'},
        {'init': '0.6', 'max': '0.9', 'min': '0.4', 'name': 'k0'},
        {'init': '100f', 'max': '200f', 'min': '50f', 'name': 'cin'}],
}

def run_selftests():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "selftest.py")
    if not os.path.isfile(path):
        print("本包未附带单测（%s）；完整回归请在开发仓库运行。" % path,
              file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, path])

def _warn_ascii_filesystem():
    enc = (sys.getfilesystemencoding() or "").lower()
    if enc in ("ascii", "us-ascii",
              "ansi_x3.4-1968"):
        print("警告: 文件系统编码为 %s, 含中文的路径会出错;" % enc)
        print("建议以 LANG=C.UTF-8 或 zh_CN.UTF-8 启动本程序")

def _force_utf8_stdio():
    import io as _io
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        _buf = getattr(_stream, "buffer", None)
        _enc = (getattr(_stream, "encoding", "") or "").lower()
        if _buf is not None and _enc not in ("utf-8", "utf8"):
            try:
                setattr(sys, _name, _io.TextIOWrapper(
                    _buf, encoding="utf-8", errors="replace",
                    line_buffering=True))
            except Exception:
                pass

def _cli_reconstruct(argv):
    if not argv:
        print("usage: --reconstruct RUN_DIR [INDEX [DEST]]", file=sys.stderr)
        return 1
    run_dir = argv[0]
    try:
        if len(argv) == 1:
            dest_dir = os.path.join(run_dir, "reconstructed")
            os.makedirs(dest_dir, exist_ok=True)
            hits = sorted(glob.glob(os.path.join(run_dir, "iter_*")))
            n = 0
            for hit in hits:
                name = os.path.basename(hit)
                match = re.match(r"iter_(\d+)$", name)
                if not match:
                    continue
                index = int(match.group(1))
                try:
                    _text, path = reconstruct_input_scs(run_dir, index,
                                                        dest_dir)
                    print("rebuilt %s" % path)
                    n += 1
                except ValueError as exc:
                    print("skip %s: %s" % (name, exc), file=sys.stderr)
            print("rebuilt %d input.scs into %s" % (n, dest_dir))
            return 0 if n else 1
        index = int(argv[1])
        dest = argv[2] if len(argv) > 2 else None
        text, path = reconstruct_input_scs(run_dir, index, dest)
        if dest is None:
            sys.stdout.write(text)
        else:
            print("wrote %s" % path)
        return 0
    except (ValueError, OSError, IOError) as exc:
        print("reconstruct failed: %s" % exc, file=sys.stderr)
        return 1

def main():
    _force_utf8_stdio()
    _warn_ascii_filesystem()
    ap = argparse.ArgumentParser(description="Virtuoso parameter optimizer")
    ap.add_argument("config", nargs="?", help="config JSON file")
    ap.add_argument("--mock", action="store_true", help="use mock runner")
    ap.add_argument("--max-iter", type=int, default=None)
    ap.add_argument("--dump-state", action="store_true",
                    help="parse ADE state and exit")
    ap.add_argument("--selftest", action="store_true",
                    help="run the regression tests and exit")
    ap.add_argument("--reconstruct", nargs="+", metavar="ARG",
                    help="rebuild input.scs from a compact run dir: "
                         "--reconstruct RUN_DIR [INDEX [DEST]]")
    ap.add_argument("--gui", action="store_true",
                    help="open the graphical interface")
    ap.add_argument("--gui-smoke", action="store_true",
                    help="run the GUI smoke test and exit")
    ap.add_argument("--em-align", metavar="STAGE1_DIR",
                    help="two-stage EM alignment: ideal run dir to align from")
    ap.add_argument("--em-curve", metavar="PATH",
                    help="measured EM curve (result.txt/csv/json/touchstone)")
    ap.add_argument("--em-netlist", metavar="PATH",
                    help="EM/config netlist simulated once at x_opt")
    ap.add_argument("--em-out", metavar="DIR",
                    help="stage-2 output dir (default <stage1>/em_align)")
    ap.add_argument("--em-tol", type=float, default=None,
                    help="curve match tolerance (default 0.1)")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(1 if run_selftests() else 0)
    if args.reconstruct:
        sys.exit(_cli_reconstruct(args.reconstruct))
    if args.gui or args.gui_smoke:
        from gui import run_gui
        sys.exit(run_gui(args.config, smoke=args.gui_smoke))
    if not args.config:
        ap.error("config file is required")
    cfg = load_config(args.config)
    if args.dump_state:
        dump_state(cfg)
        return
    if args.em_align:
        report = run_em_align(
            cfg, args.em_align, em_curve=args.em_curve,
            em_netlist=args.em_netlist, output_dir=args.em_out,
            max_iter=args.max_iter, tol=args.em_tol, mock=args.mock)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    report = run_optimization(cfg, mock=args.mock, max_iter=args.max_iter)
    print(json.dumps(report, indent=2))
if __name__ == "__main__":
    main()
