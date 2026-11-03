# -*- coding: utf-8 -*-
"""lri.snp — Touchstone I/O, S/Y/Z conversions and device fingerprints."""
import math
import os
import re

from .common import (
    open, LibrfError, note_created, backup_file, _assert_writable, fmt_eng)

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

def extract_fingerprint(kind, ports, freqs, S, freq_of_interest=None):
    if not freqs or not S:
        raise LibrfError("extract_fingerprint: 空数据（kind=%s n_freq=%d）" % (kind, len(freqs or [])))
    n = len(S[0])
    if len(freqs) != len(S):
        raise LibrfError( "extract_fingerprint: freqs/S 长度不一致 (%d vs %d, kind=%s)"
            % (len(freqs), len(S), kind))
    canon = {"inductor": "ind", "ind2": "ind", "ind_ct": "ct",
             "center_tap": "ct", "sym": "ind", "symmetric": "ind"}
    raw_kind = (kind or "ind").lower()
    kind_l = canon.get(raw_kind, raw_kind)
    if kind_l not in ("ind", "ct"):
        raise LibrfError(
            "extract_fingerprint: 未知/已删除 kind %r（只剩 ind/sym/ct）" % kind)
    req = {"ind": (2, 2, "kind=ind 需要 2 端口，收到 %d 个——3 端口（含中心抽头）请用 kind=ct"),
           "ct": (3, 99, "kind=ct 需要 >=3 端口，收到 %d 个")}
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
    raise LibrfError("extract_fingerprint: 未知 kind %r" % kind)

def fingerprint_from_snp(snp_path, kind, ports=None, f0=None):
    freqs, S, _z0, file_ports = read_snp(snp_path)
    use_ports = ports if ports else file_ports
    return extract_fingerprint(kind, use_ports, freqs, S, freq_of_interest=f0)

def fprint_summary(fpr):
    fpr = fpr or {}
    return "L0=%sH  SRF=%s" % (fmt_eng(fpr.get("L0")), fmt_eng(fpr.get("SRF")))
