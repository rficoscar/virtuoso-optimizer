# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import gzip
import json
import math
import os
import re
import shutil
import sys
import tempfile

__all__ = [
    "load_curves", "load_run_best", "build_match_targets", "curve_metrics",
    "correction_report", "format_markdown", "write_report",
    "infer_family",
]

_ENG_SUFFIX = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
    "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "a": 1e-18,
}
_ENG_RE = re.compile(
    r"^\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*([A-Za-z]*)\s*$")

def parse_eng(text):
    if isinstance(text, bool):
        raise ValueError("bad number: %r" % (text,))
    if isinstance(text, (int, float)):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("bad number: %r" % (text,))
        return value
    m = _ENG_RE.match(str(text))
    if not m:
        raise ValueError("bad number: %s" % text)
    value = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix:
        if suffix not in _ENG_SUFFIX:
            raise ValueError("bad suffix: %s" % text)
        value *= _ENG_SUFFIX[suffix]
    if not math.isfinite(value):
        raise ValueError("bad number: %s" % text)
    return value

def format_eng(value):
    if value is None:
        return ""
    value = float(value)
    if value == 0:
        return "0"
    if not math.isfinite(value):
        return str(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    for scale, suffix in ((1e12, "T"), (1e9, "G"), (1e6, "meg"), (1e3, "k"),
                          (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
                          (1e-12, "p"), (1e-15, "f"), (1e-18, "a")):
        if value >= scale:
            return "%s%g%s" % (sign, value / scale, suffix)
    return "%s%g" % (sign, value)

def _try_num(text):
    try:
        return parse_eng(text)
    except (TypeError, ValueError):
        return None

def _open_text(path):
    low = str(path).lower()
    if low.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")

def _read_text(path):
    with _open_text(path) as f:
        return f.read()

def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def _load_json(path):
    with _open_text(path) as f:
        return json.load(f)

def _sorted_curve(xs, ys):
    pairs = []
    for xi, yi in zip(xs, ys):
        xv = _try_num(xi)
        yv = _try_num(yi)
        if xv is None or yv is None:
            continue
        pairs.append((xv, yv))
    if not pairs:
        return [], []
    pairs.sort(key=lambda p: p[0])
    out = [pairs[0]]
    for p in pairs[1:]:
        if p[0] == out[-1][0]:
            out[-1] = p
        else:
            out.append(p)
    return [p[0] for p in out], [p[1] for p in out]

def _normalize_curve(data, name="curve"):
    xs = ys = None
    if isinstance(data, dict):
        if "x" in data and "y" in data:
            xs, ys = data.get("x"), data.get("y")
        elif "freq" in data and "value" in data:
            xs, ys = data.get("freq"), data.get("value")
    elif isinstance(data, (list, tuple)):
        if isinstance(data, tuple) and len(data) == 2 \
                and isinstance(data[0], (list, tuple)) \
                and isinstance(data[1], (list, tuple)):
            xs, ys = data[0], data[1]
        elif isinstance(data, list) and len(data) == 2 \
                and all(isinstance(part, (list, tuple)) and part
                        for part in data) \
                and (isinstance(data[0][0], (list, tuple))
                     or isinstance(data[1][0], (list, tuple))
                     or len(data[0]) != 2 or len(data[1]) != 2):
            xs, ys = data[0], data[1]
        else:
            px, py = [], []
            for item in data:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    px.append(item[0])
                    py.append(item[1])
                elif isinstance(item, dict) and "x" in item and "y" in item:
                    px.append(item["x"])
                    py.append(item["y"])
            xs, ys = px, py
    if xs is None or ys is None:
        raise ValueError("曲线 %s 的数据格式无法识别" % name)
    if not isinstance(xs, (list, tuple)) or not isinstance(ys, (list, tuple)):
        raise ValueError("曲线 %s 的 x/y 必须是列表" % name)
    n = min(len(xs), len(ys))
    xs, ys = xs[:n], ys[:n]
    ox, oy = _sorted_curve(xs, ys)
    if not ox:
        raise ValueError("曲线 %s 没有有效数值点" % name)
    return ox, oy

def parse_result_text(text):
    curves = {}
    name = None
    xs = []
    ys = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == "CURVE_BEGIN":
            name = line[len("CURVE_BEGIN"):].strip()
            xs = []
            ys = []
        elif tag == "CURVE_END":
            if name and xs:
                curves[name] = _sorted_curve(xs, ys)
            name = None
            xs = []
            ys = []
        elif tag == "SCALAR":
            if len(parts) >= 3:
                value = _try_num(parts[-1])
                sname = " ".join(parts[1:-1])
                if sname and value is not None:
                    curves[sname] = ([0.0], [value])
        elif name is not None and len(parts) >= 2:
            xv = _try_num(parts[0])
            yv = _try_num(parts[1])
            if xv is not None and yv is not None:
                xs.append(xv)
                ys.append(yv)
    curves = dict((k, v) for k, v in curves.items() if v[0])
    if not curves:
        raise ValueError("result 文本里没有 SCALAR/CURVE 数据")
    return curves

def _result_status(text):
    status = ""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "STATUS" and len(parts) > 1:
            status = parts[1]
    return status

def _split_line(line):
    if "\t" in line:
        parts = line.split("\t")
    elif "," in line:
        parts = line.split(",")
    elif ";" in line and " " not in line.strip():
        parts = line.split(";")
    else:
        parts = line.split()
    return [p.strip().strip('"').strip("'") for p in parts if p.strip() != ""]

_CSV_GENERIC_HEADERS = {
    "value", "values", "val", "y", "v", "mag", "magnitude", "db", "db20",
    "amplitude", "data",
}

def parse_csv_text(text, default_name="curve"):
    header = None
    rows = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith(("#", "!", "%", "//")):
            continue
        parts = _split_line(line)
        if not parts:
            continue
        nums = [_try_num(p) for p in parts]
        if all(v is not None for v in nums):
            rows.append(nums)
        elif header is None and not rows:
            header = parts
    if not rows:
        raise ValueError("CSV 文本里没有数值数据行")
    ncols = min(len(r) for r in rows)
    if ncols < 2:
        raise ValueError("CSV 至少需要两列（freq,value）")
    rows = [r[:ncols] for r in rows]
    names = []
    for i in range(1, ncols):
        nm = ""
        if header is not None and len(header) >= ncols and header[i]:
            nm = header[i]
        if not nm or nm.lower() in _CSV_GENERIC_HEADERS:
            nm = default_name if ncols == 2 else \
                "%s_%d" % (default_name, i + 1)
        if nm in names:
            nm = "%s_%d" % (nm, i + 1)
        names.append(nm)
    curves = {}
    for j, nm in enumerate(names):
        xs = [r[0] for r in rows]
        ys = [r[j + 1] for r in rows]
        ox, oy = _sorted_curve(xs, ys)
        if ox:
            curves[nm] = (ox, oy)
    if not curves:
        raise ValueError("CSV 文本里没有可用曲线")
    return curves

def _curves_from_json(data, default_name="curve"):
    if isinstance(data, dict) and isinstance(data.get("curves"), dict):
        data = data["curves"]
    if isinstance(data, dict):
        if "x" in data and "y" in data:
            return {default_name: _normalize_curve(data, default_name)}
        curves = {}
        for name, val in data.items():
            if str(name).startswith("_"):
                continue
            curves[str(name)] = _normalize_curve(val, str(name))
        if not curves:
            raise ValueError("JSON 对象里没有曲线")
        return curves
    if isinstance(data, list):
        if data and all(isinstance(it, dict) and ("name" in it or "output" in it)
                        for it in data):
            curves = {}
            for item in data:
                nm = str(item.get("name") or item.get("output"))
                curves[nm] = _normalize_curve(item, nm)
            return curves
        return {default_name: _normalize_curve(data, default_name)}
    raise ValueError("JSON 顶层必须是对象或数组")

_TS_UNITS = {
    "hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9, "thz": 1e12,
}

def _ts_ports(path):
    m = re.search(r"\.s(\d+)p$", str(path).lower())
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None

def _ts_option(line):
    unit = 1e9
    param = "s"
    fmt = "ma"
    toks = line[1:].split()
    i = 0
    while i < len(toks):
        low = toks[i].lower()
        if low in _TS_UNITS:
            unit = _TS_UNITS[low]
        elif low in ("s", "y", "z"):
            param = low
        elif low in ("ri", "ma", "db"):
            fmt = low
        elif low == "r" and i + 1 < len(toks):
            i += 1
        i += 1
    return unit, param, fmt

def _ts_complex(re_v, im_v, fmt):
    if fmt == "ri":
        return complex(re_v, im_v)
    ph = math.radians(im_v)
    if fmt == "ma":
        return complex(re_v * math.cos(ph), re_v * math.sin(ph))
    mag = 10.0 ** (re_v / 20.0)
    return complex(mag * math.cos(ph), mag * math.sin(ph))

def parse_touchstone_text(text, ports=None):
    unit = 1e9
    fmt = "ma"
    param = "s"
    n = int(ports) if ports and int(ports) > 0 else None
    rows = []
    for raw in text.splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            unit, param, fmt = _ts_option(line)
            continue
        if line.startswith("!"):
            continue
        toks = line.split()
        nums = []
        for tok in toks:
            v = _try_num(tok)
            if v is None:
                break
            nums.append(v)
        if not nums:
            continue
        rows.append(nums)
    if param != "s":
        raise ValueError("只支持 Touchstone 的 S 参数（当前 %s）" % param.upper())
    if not rows:
        raise ValueError("Touchstone 文本里没有数据行")
    if n is None:
        rest = len(rows[0]) - 1
        if rest <= 0:
            raise ValueError("Touchstone 首行缺少 S 参数数据")
        n = int(round(math.sqrt(rest / 2.0)))
        if n <= 0 or 2 * n * n != rest:
            raise ValueError("Touchstone 端口数无法从 %d 个数据推断" % rest)
    expect = 2 * n * n
    freqs = []
    smat = []
    for row in rows:
        if len(row) < expect + 1:
            continue
        f = row[0] * unit
        vals = row[1:expect + 1]
        mat = [[0j] * n for _ in range(n)]
        for pos in range(n * n):
            j = pos // n
            i = pos % n
            mat[i][j] = _ts_complex(vals[2 * pos], vals[2 * pos + 1], fmt)
        freqs.append(f)
        smat.append(mat)
    if not freqs:
        raise ValueError("Touchstone 数据行不完整（需要每行 %d 个数）"
                         % (expect + 1))
    order = sorted(range(len(freqs)), key=lambda k: freqs[k])
    freqs = [freqs[k] for k in order]
    smat = [smat[k] for k in order]
    curves = {}
    for i in range(n):
        for j in range(n):
            db = []
            ang = []
            for k in range(len(freqs)):
                z = smat[k][i][j]
                mag = abs(z)
                db.append(20.0 * math.log10(max(mag, 1e-15)))
                ang.append(math.degrees(math.atan2(z.imag, z.real)))
            nm = "S%d%d" % (i + 1, j + 1)
            curves[nm + " dB20"] = (list(freqs), db)
            curves[nm + " ang"] = (list(freqs), ang)
    return curves

def _looks_like_result(text):
    for line in text.splitlines()[:400]:
        s = line.strip()
        if s.startswith(("CURVE_BEGIN", "CURVE_END", "SCALAR ")) \
                or s.startswith("STATUS "):
            return True
    return False

def _looks_like_touchstone(text):
    for raw in text.splitlines()[:80]:
        s = raw.strip()
        if not s or s.startswith("!"):
            continue
        if s.startswith("#"):
            toks = [t.lower() for t in s[1:].split()]
            return (any(t in ("s", "y", "z") for t in toks)
                    or any(t in _TS_UNITS for t in toks)
                    or any(t in ("ri", "ma", "db") for t in toks))
        return False
    return False

def _default_curve_name(path):
    base = os.path.basename(str(path))
    if base.lower().endswith(".gz"):
        base = base[:-3]
    root, _ext = os.path.splitext(base)
    return root or "curve"

def _load_curve_file(path):
    low = path.lower()
    base = low[:-3] if low.endswith(".gz") else low
    stem = _default_curve_name(path)
    if base.endswith(".json"):
        try:
            return _curves_from_json(_load_json(path), stem)
        except (OSError, ValueError):
            raise
        except Exception as exc:
            raise ValueError("JSON 曲线解析失败 %s: %s" % (path, exc))
    if base.endswith((".csv", ".tsv")):
        return parse_csv_text(_read_text(path), stem)
    ports = _ts_ports(base)
    if ports is not None:
        return parse_touchstone_text(_read_text(path), ports)
    text = _read_text(path)
    if not text.strip():
        raise ValueError("曲线文件为空: %s" % path)
    if _looks_like_result(text):
        return parse_result_text(text)
    if _looks_like_touchstone(text):
        return parse_touchstone_text(text, None)
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return _curves_from_json(json.loads(text), stem)
        except ValueError:
            pass
    return parse_csv_text(text, stem)

def _curves_from_runner(ret, result_path, psf_dir):
    if ret is None:
        if os.path.isfile(result_path):
            return parse_result_text(_read_text(result_path))
        raise ValueError("runner 没有生成结果文件: %s" % result_path)
    if isinstance(ret, dict):
        if isinstance(ret.get("curves"), dict):
            data = ret["curves"]
        elif ret and all(not isinstance(v, (str, bytes))
                         for v in ret.values()):
            data = ret
        else:
            if os.path.isfile(result_path):
                return parse_result_text(_read_text(result_path))
            msg = ret.get("message") or ret.get("status") or "无 curves"
            raise ValueError("runner 未返回曲线: %s" % msg)
        out = {}
        for name, val in data.items():
            out[str(name)] = _normalize_curve(val, str(name))
        if not out:
            raise ValueError("runner 返回空曲线")
        return out
    if isinstance(ret, str):
        if os.path.isfile(ret):
            return parse_result_text(_read_text(ret))
        return parse_result_text(ret)
    if isinstance(ret, (tuple, list)) and len(ret) >= 1:
        if os.path.isfile(result_path):
            return parse_result_text(_read_text(result_path))
        for item in ret[1:]:
            if isinstance(item, str) and _looks_like_result(item):
                return parse_result_text(item)
        rc = ret[0] if ret else "?"
        err = ret[2] if len(ret) > 2 else ""
        raise ValueError("eval serve 未产出曲线（rc=%s）: %s" % (rc, err))
    if hasattr(ret, "curves"):
        curves = getattr(ret, "curves") or {}
        if curves:
            return dict((str(k), _normalize_curve(v, str(k)))
                        for k, v in curves.items())
    raise ValueError("runner 返回值无法识别（psf=%s）" % psf_dir)

def _load_psf_dir(psf_dir, outputs_state, runner):
    if runner is None:
        raise ValueError(
            "目录 %s 只有 psf/ 输出，没有 result.txt：请注入 runner 调用 "
            "eval serve，或先把曲线导出为 result.txt/CSV/JSON/.sNp" % psf_dir)
    tmp = tempfile.mkdtemp(prefix="em_align_psf_")
    result_path = os.path.join(tmp, "result.txt")
    try:
        try:
            ret = runner(psf_dir, result_path, outputs_state)
        except Exception as exc:
            raise ValueError("eval serve runner 失败（psf=%s）: %s"
                             % (psf_dir, exc))
        return _curves_from_runner(ret, result_path, psf_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _iter_dirs(run_dir):
    out = []
    for name in os.listdir(run_dir):
        m = re.match(r"^iter_(\d+)$", name)
        if not m:
            continue
        path = os.path.join(run_dir, name)
        if os.path.isdir(path):
            out.append((int(m.group(1)), path))
    out.sort(key=lambda p: p[0])
    return out

def _iter_result_path(iter_path):
    for name in ("result.txt", "result.txt.gz"):
        hit = os.path.join(iter_path, name)
        if os.path.isfile(hit):
            return hit
    return None

def _is_curve_file(name):
    low = name.lower()
    if low.endswith(".gz"):
        low = low[:-3]
    return (low.endswith((".txt", ".csv", ".tsv", ".dat", ".json"))
            or re.search(r"\.s\d+p$", low) is not None)

def _load_curve_dir(path, outputs_state=None, runner=None):
    result = _iter_result_path(path)
    if result:
        return _load_curve_file(result)
    psf = os.path.join(path, "psf")
    if os.path.isdir(psf):
        return _load_psf_dir(psf, outputs_state, runner)
    if os.path.basename(path.rstrip("\\/")).lower() == "psf":
        return _load_psf_dir(path, outputs_state, runner)
    if _iter_dirs(path):
        best = load_run_best(path)
        if best.get("curves"):
            return best["curves"]
        raise ValueError("run 目录 %s 里找不到可用的 best 曲线" % path)
    files = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if not os.path.isfile(full) or name.startswith("."):
            continue
        if name in ("report.json", "manifest.jsonl", "record.json"):
            continue
        if _is_curve_file(name):
            files.append(full)
    if len(files) == 1:
        return _load_curve_file(files[0])
    if not files:
        raise ValueError("目录 %s 里没有 result.txt、psf/、run 目录或曲线文件"
                         % path)
    raise ValueError("目录 %s 里有多个曲线文件，无法确定读哪一个：%s"
                     % (path, ", ".join(sorted(os.path.basename(f)
                                               for f in files))))

def load_curves(path, outputs_state=None, runner=None):
    if path is None:
        raise ValueError("曲线路径为空")
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.exists(path):
        raise ValueError("曲线路径不存在: %s" % path)
    try:
        if os.path.isdir(path):
            return _load_curve_dir(path, outputs_state, runner)
        return _load_curve_file(path)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("曲线读取失败 %s: %s" % (path, exc))

def _load_best_payload(run_dir, report):
    path = None
    if isinstance(report.get("best_curves"), str):
        cand = os.path.expanduser(report["best_curves"])
        if not os.path.isabs(cand):
            cand = os.path.join(run_dir, cand)
        if os.path.isfile(cand):
            path = cand
    if path is None:
        cand = os.path.join(run_dir, "best_curves.json")
        if os.path.isfile(cand):
            path = cand
    if path is None:
        return {}
    try:
        data = _load_json(path)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {"variables": {}, "curves": {}, "iter": None}
    variables = data.get("variables")
    if isinstance(variables, dict):
        out["variables"] = dict((str(k), v) for k, v in variables.items())
    iter_val = data.get("iter")
    if isinstance(iter_val, bool):
        iter_val = None
    if isinstance(iter_val, int):
        out["iter"] = iter_val
    elif isinstance(iter_val, str) and re.match(r"^\s*\d+\s*$", iter_val):
        out["iter"] = int(iter_val)
    curves = data.get("curves")
    if isinstance(curves, dict):
        for name, val in curves.items():
            try:
                out["curves"][str(name)] = _normalize_curve(val, str(name))
            except ValueError:
                pass
    return out

def _vars_to_float(values):
    out = {}
    raw = {}
    if not isinstance(values, dict):
        return out, raw
    for name, val in values.items():
        if isinstance(val, bool):
            continue
        try:
            num = parse_eng(val)
        except (TypeError, ValueError):
            continue
        out[str(name)] = num
        raw[str(name)] = val if isinstance(val, str) else format_eng(num)
    return out, raw

def _vars_match(target, candidate, rel=2e-3):
    if not target or not candidate:
        return float("inf")
    if set(target) - set(candidate):
        return float("inf")
    worst = 0.0
    for name, val in target.items():
        other = candidate.get(name)
        if other is None:
            return float("inf")
        scale = max(abs(val), abs(other), 1e-30)
        diff = abs(val - other) / scale
        if diff > rel:
            return float("inf")
        worst = max(worst, diff)
    return worst

def _load_jsonl_map(path):
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                idx = rec.get("index")
                if isinstance(idx, int):
                    out[idx] = rec
    except OSError:
        pass
    return out

def _iter_status(index, iter_path, records, manifest):
    rec = records.get(index)
    if rec and rec.get("status"):
        return str(rec.get("status"))
    man = manifest.get(index)
    if man and man.get("status"):
        return str(man.get("status"))
    result = _iter_result_path(iter_path)
    if result:
        try:
            return _result_status(_read_text(result))
        except OSError:
            pass
    return ""

def _load_iter_curves(iter_path):
    result = _iter_result_path(iter_path)
    if not result:
        return {}
    try:
        return parse_result_text(_read_text(result))
    except (OSError, ValueError):
        return {}

def load_run_best(run_dir):
    if run_dir is None:
        raise ValueError("run 目录为空")
    run_dir = os.path.abspath(os.path.expanduser(str(run_dir)))
    if not os.path.isdir(run_dir):
        raise ValueError("run 目录不存在: %s" % run_dir)
    report = {}
    path = os.path.join(run_dir, "report.json")
    if os.path.isfile(path):
        try:
            data = _load_json(path)
            if isinstance(data, dict):
                report = data
        except Exception:
            report = {}
    best_payload = _load_best_payload(run_dir, report)
    iters = _iter_dirs(run_dir)
    iter_map = dict(iters)
    records = {}
    for idx, iter_path in iters:
        rec_path = os.path.join(iter_path, "record.json")
        if os.path.isfile(rec_path):
            try:
                rec = _load_json(rec_path)
                if isinstance(rec, dict):
                    records[idx] = rec
            except Exception:
                pass
    manifest = _load_jsonl_map(os.path.join(run_dir, "manifest.jsonl"))

    chosen = None
    best_iter = report.get("best_iter")
    if isinstance(best_iter, dict):
        best_iter = best_iter.get("index", best_iter.get("iter"))
    if isinstance(best_iter, bool):
        best_iter = None
    if isinstance(best_iter, str):
        m = re.search(r"\d+", best_iter)
        best_iter = int(m.group(0)) if m else None
    if isinstance(best_iter, int) and best_iter in iter_map:
        chosen = best_iter
    target_vars = None
    for key in ("best_variables_raw", "best_variables"):
        raw = report.get(key)
        if isinstance(raw, dict) and raw:
            target_vars, _raw = _vars_to_float(raw)
            if target_vars:
                break
    if chosen is None and target_vars:
        best_diff = float("inf")
        for idx, _iter_path in iters:
            rec = records.get(idx) or {}
            cand, _raw = _vars_to_float(rec.get("variables") or {})
            diff = _vars_match(target_vars, cand)
            if diff < best_diff:
                best_diff = diff
                chosen = idx
        if best_diff == float("inf"):
            chosen = None
    if chosen is None and isinstance(best_payload.get("iter"), int) \
            and best_payload["iter"] in iter_map:
        chosen = best_payload["iter"]
    if chosen is None:
        for idx, iter_path in reversed(iters):
            if _iter_status(idx, iter_path, records, manifest) == "ok" \
                    and _iter_result_path(iter_path):
                chosen = idx
                break
    if chosen is None:
        for idx, iter_path in reversed(iters):
            if _iter_result_path(iter_path):
                chosen = idx
                break
    if chosen is None and iters:
        chosen = iters[-1][0]

    variables, variables_raw = {}, {}
    for key in ("best_variables_raw", "best_variables"):
        raw = report.get(key)
        if isinstance(raw, dict) and raw:
            variables, variables_raw = _vars_to_float(raw)
            if variables:
                break
    if not variables and best_payload.get("variables"):
        variables, variables_raw = _vars_to_float(best_payload["variables"])
    if not variables and chosen is not None:
        rec = records.get(chosen) or {}
        variables, variables_raw = _vars_to_float(rec.get("variables") or {})
    if not variables and chosen is not None:
        man = manifest.get(chosen) or {}
        line = ((man.get("netlist") or {}).get("params_line") or "")
        variables, variables_raw = _params_line_vars(line)

    curves = dict(best_payload.get("curves") or {})
    if not curves and chosen is not None and chosen in iter_map:
        curves = _load_iter_curves(iter_map[chosen])
    if not curves:
        ok_first = [idx for idx, _p in iters
                    if _iter_status(idx, _p, records, manifest) == "ok"]
        rest = [idx for idx, _p in iters if idx not in ok_first]
        for idx in list(reversed(ok_first)) + list(reversed(rest)):
            cand = _load_iter_curves(iter_map[idx])
            if cand:
                curves = cand
                if chosen is None:
                    chosen = idx
                break

    iter_out = chosen
    if iter_out is None and isinstance(best_payload.get("iter"), int):
        iter_out = best_payload["iter"]
    return {
        "variables": variables,
        "variables_raw": variables_raw,
        "curves": curves,
        "iter": iter_out if iter_out is not None else -1,
        "report": report,
        "path": run_dir,
    }

def _params_line_vars(line):
    text = str(line or "").strip()
    if not text:
        return {}, {}
    toks = text.replace("=", " = ").split()
    out, raw = {}, {}
    for i, tok in enumerate(toks):
        if tok == "=" and 0 < i < len(toks) - 1:
            name = toks[i - 1]
            value = toks[i + 1]
            num = _try_num(value)
            if num is not None:
                out[name] = num
                raw[name] = value
    return out, raw

def _parse_band(band):
    if band is None:
        return None
    if isinstance(band, (list, tuple)) and len(band) >= 2:
        lo = _try_num(band[0])
        hi = _try_num(band[1])
        if lo is None or hi is None:
            raise ValueError("band 必须是两个数值: %r" % (band,))
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi
    raise ValueError("band 必须是 [lo, hi]: %r" % (band,))

def _decimate(pts, max_points):
    n = len(pts)
    if n <= max_points:
        return list(pts)
    if max_points <= 1:
        return [pts[0]]
    idx = []
    last = -1
    for i in range(max_points):
        k = int(round(i * (n - 1) / float(max_points - 1)))
        if k != last:
            idx.append(k)
            last = k
    return [pts[k] for k in idx]

def build_match_targets(curves, names=None, max_points=81, tol=0.1,
                        band=None, weight=1.0):
    max_points = int(max_points)
    if max_points < 2:
        raise ValueError("max_points 必须 >= 2")
    band_lo_hi = _parse_band(band)
    if names is None:
        wanted = sorted(curves)
    elif isinstance(names, str):
        wanted = [names]
    else:
        wanted = [str(n) for n in names]
    targets = []
    for name in wanted:
        key = _resolve_curve_name(curves, name)
        if key is None:
            continue
        try:
            xs, ys = _normalize_curve(curves[key], key)
        except ValueError:
            continue
        pts = list(zip(xs, ys))
        if band_lo_hi is not None:
            lo, hi = band_lo_hi
            pts = [p for p in pts if lo <= p[0] <= hi]
        if len(pts) < 2:
            continue
        pts = _decimate(pts, max_points)
        targets.append({
            "output": key,
            "curve_op": "match",
            "limit": {"x": [float(p[0]) for p in pts],
                      "y": [float(p[1]) for p in pts]},
            "tol": float(tol),
            "weight": float(weight),
            "role": "error",
        })
    return targets

def _resolve_curve_name(curves, name):
    if name in curves:
        return name
    hits = [k for k in curves if k.startswith(name)]
    if len(hits) == 1:
        return hits[0]
    return None

def _interp(xs, ys, x):
    if not xs:
        return None
    if x < xs[0] or x > xs[-1]:
        return None
    lo = 0
    hi = len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    x0, y0 = xs[lo], ys[lo]
    x1, y1 = xs[hi], ys[hi]
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def _empty_metrics():
    return {"rms": float("inf"), "max_abs": float("inf"),
            "mean_signed": 0.0, "p95_abs": float("inf"),
            "n": 0, "worst_freq": None}

def curve_metrics(ref, got, band=None):
    if ref is None or got is None:
        return _empty_metrics()
    try:
        rx, ry = _normalize_curve(ref, "ref")
        gx, gy = _normalize_curve(got, "got")
    except ValueError:
        return _empty_metrics()
    band_lo_hi = _parse_band(band)
    if band_lo_hi is not None:
        lo, hi = band_lo_hi
        rp = [(x, y) for x, y in zip(rx, ry) if lo <= x <= hi]
        gp = [(x, y) for x, y in zip(gx, gy) if lo <= x <= hi]
        rx = [p[0] for p in rp]
        ry = [p[1] for p in rp]
        gx = [p[0] for p in gp]
        gy = [p[1] for p in gp]
    if not rx or not gx:
        return _empty_metrics()
    lo = max(rx[0], gx[0])
    hi = min(rx[-1], gx[-1])
    if lo > hi:
        return _empty_metrics()
    samples = [x for x in rx if lo <= x <= hi]
    if not samples:
        samples = [x for x in gx if lo <= x <= hi]
    if not samples:
        samples = [lo] if lo == hi else []
    if not samples:
        return _empty_metrics()
    diffs = []
    worst = None
    worst_abs = -1.0
    for x in samples:
        rv = _interp(rx, ry, x)
        gv = _interp(gx, gy, x)
        if rv is None or gv is None:
            continue
        d = gv - rv
        diffs.append(d)
        if abs(d) > worst_abs:
            worst_abs = abs(d)
            worst = x
    if not diffs:
        return _empty_metrics()
    n = len(diffs)
    rms = math.sqrt(sum(d * d for d in diffs) / float(n))
    mean_signed = sum(diffs) / float(n)
    p95 = sorted(abs(d) for d in diffs)
    k = int(math.ceil(0.95 * n)) - 1
    if k < 0:
        k = 0
    if k >= n:
        k = n - 1
    return {
        "rms": rms,
        "max_abs": max(abs(d) for d in diffs),
        "mean_signed": mean_signed,
        "p95_abs": p95[k],
        "n": n,
        "worst_freq": worst,
    }

_FAMILY_ALIASES = {
    "l": "L", "ind": "L", "inductor": "L", "inductance": "L",
    "c": "C", "cap": "C", "capacitor": "C", "capacitance": "C",
    "r": "R", "res": "R", "resistor": "R", "resistance": "R",
    "k": "k", "coupling": "k", "couple": "k", "km": "k",
    "w": "W", "width": "W", "mos_w": "W", "transistor_w": "W",
    "lg": "Lg", "lch": "Lg", "length": "Lg", "mos_l": "Lg",
    "gate_length": "Lg", "transistor_l": "Lg",
}

_FAMILY_LABEL = {
    "L": "电感 L", "C": "电容 C", "R": "电阻 R", "k": "耦合 k",
    "W": "MOS 宽度 W", "Lg": "MOS 栅长 L", "other": "其他",
}

def _map_family(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[text]
    for key, fam in _FAMILY_ALIASES.items():
        if len(key) > 2 and key in text:
            return fam
    return None

def infer_family(name):
    raw = str(name or "").strip()
    low = raw.lower()
    if not low:
        return "other"
    if re.match(r"^(lg|lch|lmos|len|length)[\d_]*$", low):
        if low.startswith(("lg", "lch", "lmos", "len", "length")):
            return "Lg"
    if re.match(r"^(w|wn|wp|wmos|width)[\d_]*$", low):
        return "W"
    if re.match(r"^k([\d_].*|[a-z]*)$", low) or low in ("kc", "km"):
        return "k"
    if low.startswith("cpl"):
        return "k"
    if re.match(r"^l[psmdt]?[\d_]*$", low):
        return "L"
    if re.match(r"^c[a-z_]*[\d_]*$", low):
        return "C"
    if re.match(r"^r[a-z_]*[\d_]*$", low):
        return "R"
    if low.startswith("l"):
        return "L"
    if low.startswith("c"):
        return "C"
    if low.startswith("r"):
        return "R"
    if low.startswith("w"):
        return "W"
    return "other"

def _family_of(name, spec):
    if isinstance(spec, dict):
        for key in ("family", "kind", "type", "device", "device_type"):
            fam = _map_family(spec.get(key))
            if fam:
                return fam
    return infer_family(name)

def _pct_text(rel):
    if rel is None:
        return ""
    return "%.1f%%" % (abs(rel) * 100.0)

def _advice(name, family, x0, x1, rel, direction):
    if x0 is None:
        return ("%s 在第一阶段变量表中缺失：先确认第一阶段配置里有该变量，"
                "否则无法判定修正方向。" % name)
    if x1 is None:
        return ("%s 在第二阶段没有结果：检查第二阶段变量名与边界设置。" % name)
    if direction == "unchanged" or (rel is not None and abs(rel) < 1e-3):
        return ("%s 基本不变（%s → %s）：保持现有几何与版图参数；"
                "若残差仍集中在该变量敏感频段，再做 1%% 量级微调。"
                % (name, format_eng(x0), format_eng(x1)))
    word = "增大" if direction == "increase" else "减小"
    pct = _pct_text(rel)
    head = "%s 需%s%s（%s → %s）：" % (
        name, word, (" " + pct) if pct else "", format_eng(x0),
        format_eng(x1))
    if family == "L":
        if direction == "increase":
            geo = ("增加螺旋圈数（L∝N²，圈数约 +%.1f%%）或加大线宽/直径"
                   % (abs(rel) * 50.0)) if rel is not None else \
                  "增加螺旋圈数或加大线宽/直径"
        else:
            geo = ("减少螺旋圈数（约 %.1f%%）或缩小直径"
                   % (abs(rel) * 50.0)) if rel is not None else \
                  "减少螺旋圈数或缩小直径"
        tail = "；改后复查 SRF、Q 与最高工作频率余量。"
    elif family == "C":
        if direction == "increase":
            geo = "增加指条数/指长或增大 MIM 面积"
        else:
            geo = "减少指条数/指长或缩小 MIM 面积"
        tail = "；注意寄生电容与 SRF。"
    elif family == "R":
        if direction == "increase":
            geo = "增加方块数（加长或减窄）"
        else:
            geo = "减少方块数（缩短或加宽）"
        tail = "；注意电流密度与 EM 规则。"
    elif family == "k":
        if direction == "increase":
            geo = "减小初次级间距或增加重叠（k 对间距高度敏感）"
        else:
            geo = "增大初次级间距或减小重叠"
        tail = "；避免 k→1 造成耦合电容与 SRF 恶化。"
    elif family == "W":
        if direction == "increase":
            geo = "增加 finger 数（保持单指宽度）或增大 unit width"
        else:
            geo = "减少 finger 数或减小 unit width"
        if direction == "increase":
            tail = ("；W 增大会提高 gm/Id 与 Cgs，注意输入匹配、"
                    "电流密度与 EM 规则。")
        else:
            tail = ("；W 减小会降低 gm/Id 与 Cgs，注意增益/线性度余量、"
                    "电流密度与 EM 规则。")
    elif family == "Lg":
        if direction == "increase":
            geo = "加长栅长（提高 ro/增益，降低 fT）"
        else:
            geo = "缩短栅长（提高 fT/增益，注意匹配与可靠性）"
        tail = "；若受最小栅长限制，优先改 W 或多指并联。"
    else:
        geo = "按该变量在网表中的作用调整"
        tail = "；族信息缺失，建议先确认变量语义再改版图。"
    return head + geo + tail + "随后复跑第二阶段，确认 RMS/最大偏差落到阈值内。"

def _float_vars(values):
    out = {}
    if not isinstance(values, dict):
        return out
    for name, val in values.items():
        if isinstance(val, bool):
            continue
        try:
            out[str(name)] = parse_eng(val)
        except (TypeError, ValueError):
            continue
    return out

def _var_specs_map(var_specs):
    out = {}
    if isinstance(var_specs, dict):
        for name, spec in var_specs.items():
            out[str(name)] = spec
    elif isinstance(var_specs, (list, tuple)):
        for item in var_specs:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = item
    return out

def correction_report(stage1_vars, stage2_vars, y_opt, y_em, y_em_ideal,
                      var_specs=None, rms_tol=0.1, max_tol=0.5):
    x_opt = _float_vars(stage1_vars)
    x_new = _float_vars(stage2_vars)
    specs = _var_specs_map(var_specs)
    rms_tol = float(rms_tol)
    max_tol = float(max_tol)
    names = list(x_new.keys())
    for name in x_opt:
        if name not in x_new:
            names.append(name)
    variables = {}
    warnings = []
    for name in names:
        x0 = x_opt.get(name)
        x1 = x_new.get(name)
        if x0 is None:
            warnings.append("变量 %s 只在第二阶段出现（第一阶段缺失）" % name)
        elif x1 is None:
            warnings.append("变量 %s 只在第一阶段出现（第二阶段缺失）" % name)
        if x0 is not None and x1 is not None:
            abs_delta = abs(x1 - x0)
            if x0 != 0:
                rel_delta = (x1 - x0) / abs(x0)
            else:
                rel_delta = None
            if x1 > x0:
                direction = "increase"
            elif x1 < x0:
                direction = "decrease"
            else:
                direction = "unchanged"
        else:
            abs_delta = None
            rel_delta = None
            direction = "unknown"
        family = _family_of(name, specs.get(name))
        variables[name] = {
            "x_opt": x0,
            "x_em_ideal": x1,
            "abs_delta": abs_delta,
            "rel_delta": rel_delta,
            "direction": direction,
            "family": family,
            "advice": _advice(name, family, x0, x1, rel_delta, direction),
        }

    y_opt = y_opt if isinstance(y_opt, dict) else {}
    y_em = y_em if isinstance(y_em, dict) else {}
    y_em_ideal = y_em_ideal if isinstance(y_em_ideal, dict) else {}
    if not y_em:
        warnings.append("EM 曲线为空：无法判定对齐")
    curve_keys = list(y_em.keys())
    curves = {}
    aligned_curves = []
    all_ok = True
    resolved_opt = set()
    resolved_ideal = set()
    for name in curve_keys:
        opt_key = _resolve_curve_name(y_opt, name)
        ideal_key = _resolve_curve_name(y_em_ideal, name)
        if opt_key is not None:
            resolved_opt.add(opt_key)
        if ideal_key is not None:
            resolved_ideal.add(ideal_key)
        m_opt = curve_metrics(y_em[name], y_opt.get(opt_key)
                              if opt_key is not None else None)
        m_ideal = curve_metrics(y_em[name], y_em_ideal.get(ideal_key)
                                if ideal_key is not None else None)
        curves[name] = {"y_opt_vs_em": m_opt, "y_em_vs_em_ideal": m_ideal}
        if opt_key is None:
            warnings.append("第一阶段缺少曲线 %s：y_opt_vs_em 无法计算" % name)
        if ideal_key is None:
            warnings.append("第二阶段缺少曲线 %s：y_em_vs_em_ideal 无法计算"
                            % name)
        if m_ideal["n"] <= 0:
            warnings.append("曲线 %s 与 EM 曲线没有公共频点" % name)
            all_ok = False
        elif m_ideal["rms"] > rms_tol or m_ideal["max_abs"] > max_tol:
            all_ok = False
        else:
            aligned_curves.append(name)
    for key in y_opt:
        if key not in resolved_opt:
            warnings.append("第一阶段曲线 %s 不在 EM 曲线中（未参与统计）"
                            % key)
    for key in y_em_ideal:
        if key not in resolved_ideal:
            warnings.append("第二阶段曲线 %s 不在 EM 曲线中（未参与统计）"
                            % key)
    aligned = bool(curve_keys) and all_ok and len(aligned_curves) == len(curve_keys)

    changed = []
    for name, item in variables.items():
        if item["abs_delta"] is None:
            continue
        if item["x_opt"]:
            is_changed = (item["rel_delta"] is not None
                          and abs(item["rel_delta"]) >= 1e-3)
        else:
            is_changed = item["abs_delta"] > 1e-12
        if is_changed:
            changed.append((name, item))
    changed.sort(key=lambda pair: (
        abs(pair[1]["rel_delta"]) if pair[1]["rel_delta"] is not None else -1.0,
        pair[1]["abs_delta"] or 0.0), reverse=True)
    largest = []
    for name, item in changed[:3]:
        largest.append({
            "name": name,
            "family": item["family"],
            "direction": item["direction"],
            "rel_delta": item["rel_delta"],
            "abs_delta": item["abs_delta"],
        })
    summary = {
        "n_vars_changed": len(changed),
        "largest": largest,
        "warnings": warnings,
        "n_curves": len(curve_keys),
        "aligned_curves": aligned_curves,
    }
    return {
        "aligned": aligned,
        "rms_tol": rms_tol,
        "max_tol": max_tol,
        "variables": variables,
        "curves": curves,
        "summary": summary,
    }

def _fmt_num(value, digits=4):
    if value is None:
        return "—"
    if isinstance(value, float):
        if math.isinf(value):
            return "inf"
        if math.isnan(value):
            return "nan"
        return ("%%.%dg" % digits) % value
    return str(value)

def _fmt_hz(value):
    if value is None:
        return "—"
    value = float(value)
    for scale, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, "")):
        if abs(value) >= scale:
            return "%.4g%sHz" % (value / scale, suffix)
    return "%.4gHz" % value

def _md_cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ").strip()

def _metrics_row(name, m):
    if m.get("n", 0) <= 0:
        return "| %s | 0 | — | — | — | — | — |" % _md_cell(name)
    return "| %s | %d | %s | %s | %s | %s | %s |" % (
        _md_cell(name), m["n"], _fmt_num(m["rms"]), _fmt_num(m["max_abs"]),
        _fmt_num(m["mean_signed"]), _fmt_num(m["p95_abs"]),
        _fmt_hz(m["worst_freq"]))

def format_markdown(report):
    if not isinstance(report, dict):
        raise ValueError("report 必须是 dict")
    outer = None
    if isinstance(report.get("correction"), dict):
        outer = report
        corr = report["correction"]
    else:
        corr = report
    lines = ["# EM 两阶段对齐报告", ""]
    aligned = corr.get("aligned")
    if outer is not None and outer.get("aligned") is not None:
        aligned = outer.get("aligned")
    lines.append("- 判定：%s" % (
        "**已对齐 (aligned=True)**" if aligned
        else "**未对齐 (aligned=False)**"))
    lines.append("- 判定阈值：RMS ≤ %s，最大偏差 ≤ %s" % (
        _fmt_num(corr.get("rms_tol", 0.1)), _fmt_num(corr.get("max_tol", 0.5))))
    if outer is not None:
        if outer.get("stage1_run"):
            lines.append("- 第一阶段 run：`%s`" % outer.get("stage1_run"))
        if outer.get("stage2_run"):
            lines.append("- 第二阶段 run：`%s`" % outer.get("stage2_run"))
        if outer.get("stage1_iter") is not None:
            lines.append("- 第一阶段 best iter：%s" % outer.get("stage1_iter"))
        src = outer.get("em_source")
        if isinstance(src, dict):
            lines.append("- EM 来源：%s `%s`" % (
                src.get("kind") or "", src.get("path") or ""))
        if outer.get("stage2_stop_reason"):
            lines.append("- 第二阶段停止原因：%s"
                         % outer.get("stage2_stop_reason"))
        targets = outer.get("targets")
        if isinstance(targets, list):
            names = [str(t.get("output")) for t in targets
                     if isinstance(t, dict)]
            lines.append("- 匹配目标：%d 条（%s）" % (len(names),
                                                ", ".join(names)))
    summary = corr.get("summary") or {}
    lines.append("- 曲线数：%s；参与对齐：%s" % (
        summary.get("n_curves", len(corr.get("curves") or {})),
        ", ".join(summary.get("aligned_curves") or []) or "无"))
    lines.extend(["", "## 变量修正方向", ""])
    variables = corr.get("variables") or {}
    if variables:
        lines.append("| 变量 | 族 | x_opt | x_em_ideal | 绝对变化 | 相对变化 "
                     "| 方向 | 建议 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        dir_label = {"increase": "增大", "decrease": "减小",
                     "unchanged": "保持", "unknown": "未知"}
        for name, item in variables.items():
            rel = item.get("rel_delta")
            rel_text = "—" if rel is None else "%+.2f%%" % (rel * 100.0)
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                _md_cell(name),
                _md_cell(_FAMILY_LABEL.get(item.get("family"), "其他")),
                _md_cell(format_eng(item.get("x_opt")) or "—"),
                _md_cell(format_eng(item.get("x_em_ideal")) or "—"),
                _md_cell(format_eng(item.get("abs_delta")) or "—"),
                rel_text,
                dir_label.get(item.get("direction"), "未知"),
                _md_cell(item.get("advice"))))
    else:
        lines.append("（无变量数据）")
    curves = corr.get("curves") or {}
    for title, key in (
            ("y_opt vs y_em（第一阶段理想 − EM）", "y_opt_vs_em"),
            ("y_em_ideal vs y_em（第二阶段理想 − EM，判定依据）",
             "y_em_vs_em_ideal")):
        lines.extend(["", "## 曲线偏差：%s" % title, ""])
        if not curves:
            lines.append("（无曲线数据）")
            continue
        lines.append("| 曲线 | n | RMS | 最大 | 平均(带符号) | p95 | 最差频点 |")
        lines.append("|---|---|---|---|---|---|---|")
        for name, item in curves.items():
            lines.append(_metrics_row(name, item.get(key) or {}))
    lines.extend(["", "## 摘要", ""])
    lines.append("- 变化变量数：%s" % summary.get("n_vars_changed", 0))
    largest = summary.get("largest") or []
    if largest:
        parts = []
        for item in largest:
            rel = item.get("rel_delta")
            rel_text = "—" if rel is None else "%+.2f%%" % (rel * 100.0)
            parts.append("%s %s（%s，%s）" % (
                item.get("name"), rel_text, item.get("family"),
                item.get("direction")))
        lines.append("- 最大变化：%s" % "；".join(parts))
    warns = summary.get("warnings") or []
    if warns:
        lines.append("- 警告：")
        for text in warns:
            lines.append("  - %s" % text)
    else:
        lines.append("- 警告：无")
    lines.append("")
    return "\n".join(lines)

def write_report(report, out_dir):
    if not isinstance(report, dict):
        raise ValueError("report 必须是 dict")
    out_dir = os.path.abspath(os.path.expanduser(str(out_dir)))
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "em_align_report.json")
    md_path = os.path.join(out_dir, "em_align_report.md")
    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, allow_nan=True)
        f.write("\n")
    _write_text(md_path, format_markdown(report))
    return json_path, md_path

class _ST(object):
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.msgs = []

    def check(self, cond, label, detail=""):
        if cond:
            self.passed += 1
            print("  PASS %s" % label)
        else:
            self.failed += 1
            detail = (": " + detail) if detail else ""
            print("  FAIL %s%s" % (label, detail))
            self.msgs.append(label)

    def case(self, label, fn):
        print("[case] %s" % label)
        try:
            fn(self)
        except Exception as exc:
            self.failed += 1
            print("  FAIL %s 异常: %r" % (label, exc))
            self.msgs.append("%s: %r" % (label, exc))

def _st_result(st, tmp):
    text = ("STATUS ok\nMSG mock\nTIMING sim 0.5\nSCALAR Gain 12.5\n"
            "CURVE_BEGIN S21 dB20\n1e9 -1.0\n2e9 -1.5\n3e9 -2.0\n"
            "CURVE_END\n")
    path = os.path.join(tmp, "result.txt")
    _write_text(path, text)
    curves = load_curves(path)
    st.check(set(curves) == {"S21 dB20", "Gain"},
             "result.txt 曲线名", repr(sorted(curves)))
    st.check(len(curves["S21 dB20"][0]) == 3, "result.txt 点数")
    st.check(curves["S21 dB20"][1][1] == -1.5, "result.txt 数值")
    st.check(curves["Gain"] == ([0.0], [12.5]), "SCALAR 解析")
    gz = os.path.join(tmp, "result.txt.gz")
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        f.write(text)
    st.check(load_curves(gz)["S21 dB20"] == curves["S21 dB20"],
             "result.txt.gz 解析")
    sub = os.path.join(tmp, "result_dir")
    os.makedirs(sub)
    _write_text(os.path.join(sub, "result.txt"), text)
    st.check(load_curves(sub)["S21 dB20"][1][0] == -1.0, "目录内 result.txt")
    empty = os.path.join(tmp, "empty.txt")
    _write_text(empty, "")
    st.check(_raises(lambda: load_curves(empty), ValueError), "空文件报错")
    st.check(_raises(lambda: load_curves(os.path.join(tmp, "nope.txt")),
                     ValueError), "缺失文件报错")
    st.check(_raises(lambda: load_curves(os.path.join(tmp, "bad.txt")),
                     ValueError), "目录无曲线报错")

def _st_csv(st, tmp):
    p1 = os.path.join(tmp, "s21.csv")
    _write_text(p1, "freq,S21 dB20\n1e9,-1.0\n2e9,-1.5\n3e9,-2.0\n")
    c1 = load_curves(p1)
    st.check(list(c1) == ["S21 dB20"], "CSV 表头命名", repr(list(c1)))
    st.check(c1["S21 dB20"][1] == [-1.0, -1.5, -2.0], "CSV 数值")
    p2 = os.path.join(tmp, "sp.txt")
    _write_text(p2, "# comment\n1e9 -1.0\n2e9 -1.2\n3e9 -1.4\n")
    c2 = load_curves(p2)
    st.check(list(c2) == ["sp"], "空白分隔无表头命名", repr(list(c2)))
    p3 = os.path.join(tmp, "two.tsv")
    _write_text(p3, "freq\tS11 dB20\tS21 dB20\n"
                    "1e9\t-1\t-11\n2e9\t-2\t-12\n")
    c3 = load_curves(p3)
    st.check(set(c3) == {"S11 dB20", "S21 dB20"}, "制表符三列", repr(sorted(c3)))
    p4 = os.path.join(tmp, "eng.csv")
    _write_text(p4, "freq,val\n6G,-1\n8G,-1.2\n")
    c4 = load_curves(p4)
    st.check(c4["eng"][0] == [6e9, 8e9], "CSV 工程频率 + 通用表头用文件名")
    p5 = os.path.join(tmp, "bad.csv")
    _write_text(p5, "hello,world\nfoo,bar\n")
    st.check(_raises(lambda: load_curves(p5), ValueError), "CSV 无数据报错")

def _st_json(st, tmp):
    p1 = os.path.join(tmp, "c.json")
    _write_text(p1, json.dumps({"S21 dB20": {"x": [1e9, 2e9],
                                            "y": [-1.0, -2.0]}}))
    c1 = load_curves(p1)
    st.check(c1["S21 dB20"][0] == [1e9, 2e9], "JSON x/y 形式")
    p2 = os.path.join(tmp, "p.json")
    _write_text(p2, json.dumps({"S11 dB20": [[1e9, -1], [2e9, -2]]}))
    c2 = load_curves(p2)
    st.check(c2["S11 dB20"][1] == [-1.0, -2.0], "JSON 点列形式")
    p3 = os.path.join(tmp, "best_curves.json")
    _write_text(p3, json.dumps({"iter": 3, "variables": {"L1": "1n"},
                                "curves": {"S21 dB20": {
                                    "x": [1e9, 2e9], "y": [-1, -2]}}}))
    c3 = load_curves(p3)
    st.check(list(c3) == ["S21 dB20"], "JSON best_curves 形式")
    p4 = os.path.join(tmp, "bad.json")
    _write_text(p4, json.dumps({"X": "nope"}))
    st.check(_raises(lambda: load_curves(p4), ValueError), "JSON 坏数据报错")

def _st_touchstone(st, tmp):
    p1 = os.path.join(tmp, "one.s1p")
    _write_text(p1, "! test\n# Hz S MA R 50\n1e9 0.5 0\n2e9 0.1 90\n")
    c1 = load_curves(p1)
    st.check(abs(c1["S11 dB20"][1][0] - (-6.0206)) < 1e-3, "s1p MA->dB20")
    st.check(abs(c1["S11 ang"][1][1] - 90.0) < 1e-9, "s1p 相位")
    p2 = os.path.join(tmp, "two.s2p")
    _write_text(p2, "# Hz S DB R 50\n1e9 -6 0 -1 0 -20 0 -30 0\n")
    c2 = load_curves(p2)
    st.check(abs(c2["S21 dB20"][1][0] - (-1.0)) < 1e-9, "s2p 列优先 S21")
    st.check(abs(c2["S12 dB20"][1][0] - (-20.0)) < 1e-9, "s2p 列优先 S12")
    p3 = os.path.join(tmp, "ri.s2p")
    _write_text(p3, "# MHz S RI R 50\n"
                    "1000 0.5 0 0.1 0.2 0.1 -0.2 0.25 0\n")
    c3 = load_curves(p3)
    expect = 20.0 * math.log10(math.sqrt(0.1 ** 2 + 0.2 ** 2))
    st.check(abs(c3["S21 dB20"][1][0] - expect) < 1e-9, "s2p RI 复数")
    st.check(c3["S21 dB20"][0][0] == 1e9, "s2p 频率单位 MHz")
    p4 = os.path.join(tmp, "bad.s1p")
    _write_text(p4, "# Hz S MA R 50\n")
    st.check(_raises(lambda: load_curves(p4), ValueError), "sNp 无数据报错")

def _st_run_dir(st, tmp):
    run = os.path.join(tmp, "run1")
    for idx, (l1, c1, y0) in ((0, ("2n", "2p", -10.0)),
                              (1, ("1n", "1p", -1.0))):
        d = os.path.join(run, "iter_%04d" % idx)
        os.makedirs(d)
        _write_text(os.path.join(d, "record.json"),
                    json.dumps({"index": idx, "status": "ok",
                                "variables": {"L1": l1, "C1": c1}}))
        _write_text(os.path.join(d, "result.txt"),
                    "STATUS ok\nCURVE_BEGIN S21 dB20\n1e9 %.3f\n2e9 %.3f\n"
                    "CURVE_END\n" % (y0, y0 - 1.0))
    _write_text(os.path.join(run, "report.json"),
                json.dumps({"best_variables": {"L1": "1n", "C1": "1p"},
                            "best_variables_raw": {"L1": 1e-9, "C1": 1e-12},
                            "best_total_error": 0.2}))
    best = load_run_best(run)
    st.check(best["iter"] == 1, "run best 由 best_variables 匹配", str(best["iter"]))
    st.check(abs(best["variables"]["L1"] - 1e-9) < 1e-20, "run best 变量")
    st.check(best["curves"]["S21 dB20"][1][0] == -1.0, "run best 曲线")
    st.check(best["report"].get("best_total_error") == 0.2, "run report 返回")
    _write_text(os.path.join(run, "report.json"),
                json.dumps({"best_iter": "iter_0000",
                            "best_variables": {"L1": "1n", "C1": "1p"}}))
    best0 = load_run_best(run)
    st.check(best0["iter"] == 0, "report best_iter 优先", str(best0["iter"]))
    st.check(best0["curves"]["S21 dB20"][1][0] == -10.0, "best_iter 曲线")

    run2 = os.path.join(tmp, "run2")
    for idx, status in ((0, "ok"), (1, "ok"), (2, "fail")):
        d = os.path.join(run2, "iter_%04d" % idx)
        os.makedirs(d)
        _write_text(os.path.join(d, "result.txt"),
                    "CURVE_BEGIN S21 dB20\n1e9 %.1f\n2e9 %.1f\nCURVE_END\n"
                    "STATUS %s\n" % (idx * 1.0, idx * 1.0 + 0.5, status))
    best2 = load_run_best(run2)
    st.check(best2["iter"] == 1, "无 report 时取最后一个 ok", str(best2["iter"]))
    st.check(best2["curves"]["S21 dB20"][1][0] == 1.0, "last ok 曲线")

    run3 = os.path.join(tmp, "run3")
    os.makedirs(run3)
    _write_text(os.path.join(run3, "best_curves.json"),
                json.dumps({"iter": 7, "variables": {"L1": "3n"},
                            "curves": {"S21 dB20": {"x": [1e9, 2e9],
                                                    "y": [-3, -4]}}}))
    best3 = load_run_best(run3)
    st.check(best3["iter"] == 7, "best_curves.json iter")
    st.check(abs(best3["variables"]["L1"] - 3e-9) < 1e-20,
             "best_curves.json 变量")
    st.check(best3["curves"]["S21 dB20"][1][1] == -4.0,
             "best_curves.json 曲线")
    st.check(_raises(lambda: load_run_best(os.path.join(tmp, "no_run")),
                     ValueError), "run 目录缺失报错")

def _st_psf(st, tmp):
    base = os.path.join(tmp, "psf_run")
    psf = os.path.join(base, "psf")
    os.makedirs(psf)
    _write_text(os.path.join(psf, "dummy.psf"), "x")
    st.check(_raises(lambda: load_curves(base), ValueError),
             "psf 目录无 runner 报错")

    def runner(psf_dir, result_path, outputs_state):
        assert os.path.basename(psf_dir) == "psf"
        _write_text(result_path,
                    "STATUS ok\nCURVE_BEGIN S21 dB20\n1e9 -1\n2e9 -2\n"
                    "CURVE_END\n")
        return (0, "", "")

    c = load_curves(base, outputs_state="outputs.state", runner=runner)
    st.check(c["S21 dB20"][1] == [-1.0, -2.0], "psf runner 写文件")

    def runner_dict(psf_dir, result_path, outputs_state):
        return {"curves": {"S21 dB20": ([1e9, 2e9], [-5.0, -6.0])}}

    c2 = load_curves(base, runner=runner_dict)
    st.check(c2["S21 dB20"][1] == [-5.0, -6.0], "psf runner 返回 dict")

    def runner_text(psf_dir, result_path, outputs_state):
        return "STATUS ok\nCURVE_BEGIN X\n1 2\n2 3\nCURVE_END\n"

    c3 = load_curves(base, runner=runner_text)
    st.check(c3["X"][1] == [2.0, 3.0], "psf runner 返回文本")

    def runner_none(psf_dir, result_path, outputs_state):
        return None

    st.check(_raises(lambda: load_curves(base, runner=runner_none), ValueError),
             "psf runner 空返回报错")

def _st_targets(st, tmp):
    xs = [1e9 + i * 1e8 for i in range(1000)]
    ys = [-1.0 + 0.001 * i for i in range(1000)]
    curves = {
        "S21 dB20": (xs, ys),
        "S11 dB20": ([1e9, 2e9], [-1.0, -2.0]),
        "tiny": ([1e9], [1.0]),
    }
    targets = build_match_targets(curves, max_points=81)
    st.check(len(targets) == 2, "少于 2 点的曲线跳过", str(len(targets)))
    t = [x for x in targets if x["output"] == "S21 dB20"][0]
    st.check(len(t["limit"]["x"]) == 81, "抽点到 81")
    st.check(t["limit"]["x"][0] == xs[0] and t["limit"]["x"][-1] == xs[-1],
             "抽点保留端点")
    st.check(t["curve_op"] == "match" and t["role"] == "error"
             and t["tol"] == 0.1 and t["weight"] == 1.0, "target 字段")
    st.check(t["limit"]["x"] == sorted(t["limit"]["x"]), "抽点单调")
    t2 = build_match_targets(curves, names=["S21 dB20"], max_points=2)[0]
    st.check(t2["limit"]["x"] == [xs[0], xs[-1]], "max_points=2 保端点")
    t3 = build_match_targets(curves, names=["S21 dB20"], band=["1.2G", "2.0G"],
                             max_points=5)[0]
    st.check(t3["limit"]["x"][0] == 1.2e9 and t3["limit"]["x"][-1] == 2.0e9
             and len(t3["limit"]["x"]) == 5, "band 过滤 + 端点")
    st.check(len(build_match_targets(curves, names=["nope"])) == 0,
             "缺失曲线名跳过")
    st.check(_raises(lambda: build_match_targets(curves, max_points=1),
                     ValueError), "max_points<2 报错")

def _st_metrics(st, tmp):
    xs = [float(i) for i in range(11)]
    ref = (xs, [0.0] * 11)
    got = (xs, [2.0] * 11)
    m = curve_metrics(ref, got)
    st.check(abs(m["rms"] - 2.0) < 1e-12 and abs(m["max_abs"] - 2.0) < 1e-12,
             "常数偏差 rms/max")
    st.check(abs(m["mean_signed"] - 2.0) < 1e-12, "平均带符号偏差")
    st.check(m["n"] == 11 and m["worst_freq"] == 0.0, "n / worst_freq")
    ref2 = (xs, list(xs))
    got2 = ([x + 0.5 for x in xs], list(xs))
    m2 = curve_metrics(ref2, got2)
    st.check(abs(m2["mean_signed"] + 0.5) < 1e-12, "公共频点插值")
    st.check(m2["n"] == 10, "插值采样数", str(m2["n"]))
    spike = [0.0] * 11
    spike[5] = 10.0
    m3 = curve_metrics(ref, (xs, spike))
    st.check(m3["max_abs"] == 10.0 and m3["worst_freq"] == 5.0, "worst_freq")
    st.check(abs(m3["rms"] - 10.0 / math.sqrt(11.0)) < 1e-12, "spike rms")
    m4 = curve_metrics(ref, got, band=(2.0, 4.0))
    st.check(m4["n"] == 3, "band 过滤点数", str(m4["n"]))
    m5 = curve_metrics(([0.0, 1.0], [0.0, 1.0]),
                       ([10.0, 11.0], [0.0, 1.0]))
    st.check(m5["n"] == 0 and math.isinf(m5["rms"]), "无公共频点")
    m6 = curve_metrics({"x": xs, "y": [0.0] * 11},
                       [[x, 2.0] for x in xs])
    st.check(abs(m6["mean_signed"] - 2.0) < 1e-12, "dict/list 输入")
    m7 = curve_metrics((xs, [0.0] * 11), [xs, [2.0] * 11])
    st.check(abs(m7["mean_signed"] - 2.0) < 1e-12, "列表 (xs, ys) 输入")

def _st_families(st, tmp):
    cases = [("L1", "L"), ("Lp", "L"), ("Ls", "L"), ("Lm", "L"),
             ("C1", "C"), ("Cout", "C"), ("R1", "R"), ("k0", "k"),
             ("km", "k"), ("W1", "W"), ("Wg", "W"), ("Lg", "Lg"),
             ("lch", "Lg"), ("tail", "other")]
    for name, fam in cases:
        st.check(infer_family(name) == fam,
                 "族推断 %s -> %s" % (name, fam), infer_family(name))

def _corr_fixture():
    xs = [1e9 + i * 2.5e8 for i in range(41)]
    y = [-1.0 + 0.2 * math.sin(i / 4.0) for i in range(len(xs))]
    y_opt = [v - 1.5 for v in y]
    s1 = {"L1": 1e-9, "C1": 1e-12, "R1": 10.0, "k0": 0.5,
          "W1": 10e-6, "Lg1": 0.1e-6, "tail": 1.0}
    s2 = {"L1": 1.05e-9, "C1": 0.95e-12, "R1": 10.0, "k0": 0.6,
          "W1": 12e-6, "Lg1": 0.09e-6, "tail": 2.0}
    return xs, y, y_opt, s1, s2

def _st_correction(st, tmp):
    xs, y, y_opt, s1, s2 = _corr_fixture()
    y_em = {"S21 dB20": (xs, y)}
    y_opt_d = {"S21 dB20": (xs, y_opt)}
    y_ideal = {"S21 dB20": (xs, list(y))}
    rep = correction_report(s1, s2, y_opt_d, y_em, y_ideal)
    st.check(rep["aligned"] is True, "aligned=True 分支")
    v = rep["variables"]
    st.check(v["L1"]["family"] == "L" and v["L1"]["direction"] == "increase",
             "L1 方向/族")
    st.check(abs(v["L1"]["rel_delta"] - 0.05) < 1e-12, "L1 rel_delta")
    st.check("增大" in v["L1"]["advice"] and "圈数" in v["L1"]["advice"]
             and "SRF" in v["L1"]["advice"], "L 建议具体")
    st.check(v["C1"]["direction"] == "decrease"
             and "指" in v["C1"]["advice"], "C 建议具体")
    st.check(v["R1"]["direction"] == "unchanged"
             and "保持" in v["R1"]["advice"], "R 保持不变建议")
    st.check(v["k0"]["family"] == "k" and "间距" in v["k0"]["advice"],
             "k 建议具体")
    st.check(v["W1"]["family"] == "W" and "finger" in v["W1"]["advice"],
             "W 建议具体")
    st.check(v["Lg1"]["family"] == "Lg"
             and "栅长" in v["Lg1"]["advice"], "Lg 建议具体")
    st.check(v["tail"]["family"] == "other", "other 族")
    st.check("增大" in v["Lg1"]["advice"] or "缩短" in v["Lg1"]["advice"],
             "Lg 方向词")
    st.check(rep["summary"]["n_vars_changed"] == 6,
             "变化变量计数", str(rep["summary"]["n_vars_changed"]))
    st.check(len(rep["summary"]["largest"]) == 3, "largest 前 3")
    m_opt = rep["curves"]["S21 dB20"]["y_opt_vs_em"]
    st.check(abs(m_opt["mean_signed"] + 1.5) < 1e-12, "y_opt_vs_em 符号")
    st.check(rep["curves"]["S21 dB20"]["y_em_vs_em_ideal"]["rms"] == 0.0,
             "aligned 曲线 rms=0")

    y_shift = [v + 2.0 for v in y]
    rep2 = correction_report(s1, s2, y_opt_d, y_em,
                             {"S21 dB20": (xs, y_shift)})
    st.check(rep2["aligned"] is False, "not_aligned 分支")
    st.check(abs(rep2["curves"]["S21 dB20"]["y_em_vs_em_ideal"]["rms"] - 2.0)
             < 1e-12, "not_aligned rms")

    y_spike = list(y)
    y_spike[20] += 2.0
    rep3 = correction_report(s1, s2, y_opt_d, y_em,
                             {"S21 dB20": (xs, y_spike)},
                             rms_tol=1.0, max_tol=1.5)
    st.check(rep3["aligned"] is False, "max 阈值单独否决")
    rep4 = correction_report(s1, s2, y_opt_d, y_em,
                             {"S21 dB20": (xs, y_spike)},
                             rms_tol=1.0, max_tol=3.0)
    st.check(rep4["aligned"] is True, "双阈值同时满足")
    rep5 = correction_report(s1, s2, y_opt_d, y_em,
                             {"S21 dB20": (xs, y_shift)},
                             rms_tol=3.0, max_tol=3.0)
    st.check(rep5["aligned"] is True, "rms 阈值放宽")

    rep6 = correction_report(s1, s2, {}, y_em, y_ideal)
    st.check(rep6["curves"]["S21 dB20"]["y_opt_vs_em"]["n"] == 0,
             "缺 y_opt 指标为 0")
    st.check(any("第一阶段缺少曲线" in w
                 for w in rep6["summary"]["warnings"]), "缺曲线警告")

    rep7 = correction_report(s1, s2, y_opt_d, y_em, y_ideal,
                             var_specs={"L1": {"kind": "capacitor"}})
    st.check(rep7["variables"]["L1"]["family"] == "C",
             "var_specs 覆盖族")

    rep8 = correction_report(s1, None, y_opt_d, y_em, y_ideal)
    st.check(rep8["variables"]["L1"]["x_em_ideal"] is None
             and rep8["variables"]["L1"]["direction"] == "unknown",
             "缺第二阶段变量")

def _st_markdown(st, tmp):
    xs, y, y_opt, s1, s2 = _corr_fixture()
    rep = correction_report(s1, s2, {"S21 dB20": (xs, y_opt)},
                            {"S21 dB20": (xs, y)},
                            {"S21 dB20": (xs, list(y))})
    md = format_markdown(rep)
    st.check("EM 两阶段对齐报告" in md and "已对齐" in md, "Markdown 标题/判定")
    st.check("L1" in md and "变量修正方向" in md and "曲线偏差" in md,
             "Markdown 表体")
    payload = {"stage1_run": "runs/stage1", "stage2_run": "runs/stage2",
               "stage1_iter": 3, "aligned": True,
               "em_source": {"kind": "file", "path": "em.s2p"},
               "stage2_stop_reason": "targets_met",
               "targets": [{"output": "S21 dB20"}],
               "correction": rep}
    md2 = format_markdown(payload)
    st.check("runs/stage1" in md2 and "em.s2p" in md2
             and "targets_met" in md2, "Markdown 完整 payload")
    out = os.path.join(tmp, "report_out")
    jp, mp = write_report(payload, out)
    st.check(os.path.isfile(jp) and os.path.isfile(mp), "write_report 文件")
    st.check(os.path.basename(jp) == "em_align_report.json"
             and os.path.basename(mp) == "em_align_report.md",
             "write_report 文件名")
    with open(jp, "r", encoding="utf-8") as f:
        data = json.load(f)
    st.check(data["aligned"] is True and data["correction"]["aligned"] is True,
             "write_report JSON 可回读")
    st.check(_raises(lambda: format_markdown("x"), ValueError),
             "format_markdown 非 dict 报错")

def run_selftest():
    tmp = tempfile.mkdtemp(prefix="em_align_selftest_")
    st = _ST()
    cases = [
        ("result.txt / 目录 / gz", _st_result),
        ("CSV / 空白 / 制表符", _st_csv),
        ("JSON", _st_json),
        ("Touchstone .s1p/.s2p", _st_touchstone),
        ("run 目录 best 选择", _st_run_dir),
        ("psf 目录 + runner", _st_psf),
        ("build_match_targets 抽点", _st_targets),
        ("curve_metrics 插值指标", _st_metrics),
        ("变量族推断", _st_families),
        ("correction_report 两分支", _st_correction),
        ("Markdown / write_report", _st_markdown),
    ]
    try:
        for label, fn in cases:
            st.case(label, lambda s, f=fn: f(s, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("")
    print("selftest: %d passed, %d failed" % (st.passed, st.failed))
    if st.msgs:
        print("failed cases: %s" % ", ".join(st.msgs))
    return 0 if st.failed == 0 else 1

def _raises(fn, exc_type):
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False

def _curve_summary(curves):
    out = {}
    for name, (xs, ys) in curves.items():
        out[name] = {
            "n": len(xs),
            "x_min": min(xs) if xs else None,
            "x_max": max(xs) if xs else None,
            "y_min": min(ys) if ys else None,
            "y_max": max(ys) if ys else None,
        }
    return out

def _cli_curve(path):
    curves = load_curves(path)
    print(json.dumps({"path": os.path.abspath(path),
                      "curves": _curve_summary(curves)},
                     ensure_ascii=False, indent=2))
    return 0

def _cli_align(args):
    stage1 = load_run_best(args.stage1)
    stage2 = load_run_best(args.stage2)
    y_em = load_curves(args.em_curve)
    corr = correction_report(
        stage1["variables"], stage2["variables"], stage1["curves"], y_em,
        stage2["curves"], var_specs=None, rms_tol=args.rms_tol,
        max_tol=args.max_tol)
    payload = {
        "stage1_run": stage1["path"],
        "stage2_run": stage2["path"],
        "stage1_iter": stage1["iter"],
        "stage2_iter": stage2["iter"],
        "em_source": {"kind": "file", "path": os.path.abspath(args.em_curve)},
        "aligned": corr["aligned"],
        "correction": corr,
    }
    json_path, md_path = write_report(payload, args.out)
    print(format_markdown(payload))
    print("report: %s" % json_path)
    print("report: %s" % md_path)
    return 0

def _force_utf8_stdio():
    import io
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        buf = getattr(stream, "buffer", None)
        enc = (getattr(stream, "encoding", "") or "").lower()
        if buf is not None and enc not in ("utf-8", "utf8"):
            try:
                setattr(sys, name, io.TextIOWrapper(
                    buf, encoding="utf-8", errors="replace",
                    line_buffering=True))
            except Exception:
                pass

def main(argv=None):
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(
        description="EM 两阶段对齐：曲线读取 / 匹配目标 / 修正报告")
    ap.add_argument("--selftest", action="store_true", help="运行内建自测")
    ap.add_argument("--curve", help="读取一个曲线文件/目录并打印摘要")
    ap.add_argument("--stage1", help="第一阶段 run 目录")
    ap.add_argument("--stage2", help="第二阶段 run 目录")
    ap.add_argument("--em-curve", help="EM 曲线文件")
    ap.add_argument("--out", default=".", help="报告输出目录")
    ap.add_argument("--rms-tol", type=float, default=0.1)
    ap.add_argument("--max-tol", type=float, default=0.5)
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if args.curve:
        return _cli_curve(args.curve)
    if args.stage1 and args.stage2 and args.em_curve:
        return _cli_align(args)
    ap.print_help()
    return 2

if __name__ == "__main__":
    sys.exit(main())
