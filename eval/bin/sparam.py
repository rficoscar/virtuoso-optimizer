# -*- coding: utf-8 -*-
"""sparam.py - Touchstone 读写 + 电感/差分/共模指标提取（纯标准库）。

口径（与 librf_opt.fingerprint_from_snp 对齐，但独立实现，便于交叉校验）：
  - 单端 2 端口：Y = (I-S)(I+S)^-1 / Z0，Zin = 1/Y11，
      L(f) = Im(Zin)/(2*pi*f)，Q(f) = Im(Zin)/Re(Zin)，R = Re(Zin)。
  - SRF：L(f) 由正变负（过零）处线性插值；无过零 -> None。
  - 中心抽头 3 端口：信号端 p01/p02 与 ct；
      L_diff = Im(Z_diff)/(2*pi*f)，Z_diff 由 3 端口 Z 参数做奇模驱动求得。
  - 混合模（2 端口差分对 p01/p02）：
      Sdd = 0.5*(S11 - S12 - S21 + S22)
      Scc = 0.5*(S11 + S12 + S21 + S22)
      Scd = 0.5*(S11 + S12 - S21 - S22)
      Sdc = 0.5*(S11 - S12 + S21 - S22)
      L_diff = Im(Z0*(1+Sdd)/(1-Sdd))/(2*pi*f)
  - 引线/焊盘寄生：用"去嵌参考"比较（同一量测设置下同长度 lead 的直线结构），
    或直接用 Z 参数的串联分量 Rs/Ls。

全部函数不依赖 numpy，便于在只有 stdlib 的 EDA 主机上跑。
"""

from __future__ import print_function

import cmath
import math
import re


# ------------------------------------------------------------------ Touchstone
def parse_touchstone(path):
    """返回 dict: freqs(Hz), S(list of matrices), z0, nports, fmt. 支持 RI/MA/DB。

    Touchstone 允许一条记录跨多行（3 端口 9 个复数会折行），因此先收集全部
    数值，再按 "1 + 2*n^2" 的整除关系与频率单调性反推端口数。
    """
    freqs_tok, data_tok = [], []
    z0 = 50.0
    fmt = "MA"
    unit = 1.0
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("!"):
                continue
            if line.startswith("#"):
                toks = line[1:].split()
                for i, t in enumerate(toks):
                    tl = t.lower()
                    if tl in ("hz", "khz", "mhz", "ghz"):
                        unit = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}[tl]
                    elif tl in ("s", "y", "z", "h", "g"):
                        pass
                    elif tl in ("ri", "ma", "db"):
                        fmt = tl.upper()
                    elif tl == "r" and i + 1 < len(toks):
                        try:
                            z0 = float(toks[i + 1])
                        except ValueError:
                            pass
                continue
            for t in line.replace(",", " ").split():
                try:
                    data_tok.append(float(t))
                except ValueError:
                    raise ValueError("Touchstone 非数值 token: %r (%s)" % (t, path))
    freqs, mats, nports = _split_records(data_tok, unit, fmt)
    if nports is None:
        raise ValueError("空 Touchstone 文件: %s" % path)
    return {"freqs": freqs, "S": mats, "z0": z0, "nports": nports, "format": fmt}


def _split_records(vals, unit, fmt):
    """把扁平数值流切成 [f, 2*n^2 个实数] 记录，反推端口数。"""
    if not vals:
        return [], [], None
    best = None
    for n in range(1, 21):
        rec = 1 + 2 * n * n
        if len(vals) % rec:
            continue
        nrec = len(vals) // rec
        fr = [vals[k * rec] * unit for k in range(nrec)]
        if any(fr[i] <= fr[i - 1] for i in range(1, nrec)):
            continue
        best = n
        break
    if best is None:
        raise ValueError("无法推断端口数（%d 个数值）" % len(vals))
    n, rec = best, 1 + 2 * best * best
    freqs, mats = [], []
    for k in range(len(vals) // rec):
        base = k * rec
        freqs.append(vals[base] * unit)
        data = vals[base + 1: base + rec]
        mat = [[0j] * n for _ in range(n)]
        p = 0
        for i in range(n):
            for j in range(n):
                mat[i][j] = _to_complex(data[p], data[p + 1], fmt)
                p += 2
        mats.append(mat)
    return freqs, mats, n


def _to_complex(a, b, fmt):
    if fmt == "RI":
        return complex(a, b)
    if fmt == "MA":
        return cmath.rect(a, math.radians(b))
    if fmt == "DB":
        return cmath.rect(10 ** (a / 20.0), math.radians(b))
    raise ValueError("未知 Touchstone 格式 %s" % fmt)


def write_touchstone(path, freqs, mats, z0=50.0, fmt="RI", comment=None):
    n = len(mats[0])
    with open(path, "w", encoding="utf-8") as f:
        if comment:
            for ln in str(comment).splitlines():
                f.write("! %s\n" % ln)
        f.write("# Hz S %s R %g\n" % (fmt, z0))
        for k, fr in enumerate(freqs):
            vals = []
            for i in range(n):
                for j in range(n):
                    z = mats[k][i][j]
                    if fmt == "RI":
                        vals += ["%.10e" % z.real, "%.10e" % z.imag]
                    elif fmt == "MA":
                        vals += ["%.10e" % abs(z), "%.10e" % math.degrees(cmath.phase(z))]
                    else:
                        vals += ["%.10e" % (20 * math.log10(max(abs(z), 1e-30))),
                                 "%.10e" % math.degrees(cmath.phase(z))]
            f.write("%.10e %s\n" % (fr, " ".join(vals)))
    return path


# --------------------------------------------------------------- matrix ops
def eye(n):
    return [[1.0 + 0j if i == j else 0j for j in range(n)] for i in range(n)]


def mat_add(a, b, sa=1.0, sb=1.0):
    n = len(a)
    return [[sa * a[i][j] + sb * b[i][j] for j in range(n)] for i in range(n)]


def mat_mul(a, b):
    n, k, m = len(a), len(b), len(b[0])
    out = [[0j] * m for _ in range(n)]
    for i in range(n):
        for p in range(k):
            x = a[i][p]
            if x == 0:
                continue
            for j in range(m):
                out[i][j] += x * b[p][j]
    return out


def mat_inv(a):
    n = len(a)
    aug = [list(a[i]) + [1.0 + 0j if i == j else 0j for j in range(n)] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-300:
            raise ValueError("矩阵奇异")
        if piv != col:
            aug[col], aug[piv] = aug[piv], aug[col]
        d = 1.0 / aug[col][col]
        aug[col] = [x * d for x in aug[col]]
        for r in range(n):
            if r == col:
                continue
            f = aug[r][col]
            if f:
                aug[r] = [aug[r][j] - f * aug[col][j] for j in range(2 * n)]
    return [row[n:] for row in aug]


def s_to_y(s, z0=50.0):
    n = len(s)
    num = mat_add(eye(n), s, 1.0, -1.0)
    den = mat_add(eye(n), s, 1.0, 1.0)
    return [[x / z0 for x in row] for row in mat_mul(num, mat_inv(den))]


def s_to_z(s, z0=50.0):
    n = len(s)
    num = mat_add(eye(n), s, 1.0, 1.0)
    den = mat_add(eye(n), s, 1.0, -1.0)
    return [[x * z0 for x in row] for row in mat_mul(num, mat_inv(den))]


# ------------------------------------------------------------------- metrics
def _interp(freqs, values, f0):
    if not freqs:
        return None
    if f0 <= freqs[0]:
        return values[0]
    if f0 >= freqs[-1]:
        return values[-1]
    for i in range(1, len(freqs)):
        if freqs[i] >= f0:
            f1, f2 = freqs[i - 1], freqs[i]
            if f2 == f1:
                return values[i]
            t = (f0 - f1) / (f2 - f1)
            return values[i - 1] + t * (values[i] - values[i - 1])
    return values[-1]


def _srf_from_curve(freqs, ind):
    """L 曲线过零（正->负）线性插值。"""
    for i in range(1, len(freqs)):
        if ind[i - 1] > 0 and ind[i] <= 0:
            f1, f2 = freqs[i - 1], freqs[i]
            l1, l2 = ind[i - 1], ind[i]
            if l1 == l2:
                return f1
            return f1 + (f2 - f1) * l1 / (l1 - l2)
    return None


def inductor_metrics(freqs, S, port_i=0, port_j=1, z0=50.0, f0=None):
    """二端口串联电感：L/Q/R/SRF。返回 dict（含曲线，便于画图/收敛比较）。"""
    L, Q, R, Zin = [], [], [], []
    for k, f in enumerate(freqs):
        if f <= 0:
            continue
        y = s_to_y(S[k], z0)
        y11 = y[port_i][port_i]
        if y11 == 0:
            continue
        zin = 1.0 / y11
        w = 2.0 * math.pi * f
        l = zin.imag / w
        L.append(l)
        R.append(zin.real)
        Q.append(zin.imag / zin.real if zin.real != 0 else float("nan"))
        Zin.append(zin)
    srf = _srf_from_curve(freqs, L) if len(freqs) == len(L) else _srf_from_curve(
        [f for f in freqs if f > 0], L)
    out = {"Lcurve": list(zip(freqs, L)), "Qcurve": list(zip(freqs, Q)),
           "Rcurve": list(zip(freqs, R)), "SRF": srf}
    if f0 is not None:
        fx = [f for f in freqs if f > 0]
        out["L@f0"] = _interp(fx, L, f0)
        out["Q@f0"] = _interp(fx, Q, f0)
        out["R@f0"] = _interp(fx, R, f0)
    if L:
        out["Lmin"] = min(L)
        out["Qmax"] = max([q for q in Q if not math.isnan(q)]) if Q else None
    return out


def diff_metrics(freqs, S, z0=50.0, f0=None):
    """2 端口差分对（p01/p02）混合模 Sdd/Scc/Scd + 差分 L/Q。"""
    n = len(S[0])
    if n != 2:
        raise ValueError("diff_metrics 需要 2 端口 sNp，收到 %d 端口" % n)
    curves = {"freqs": list(freqs), "Sdd": [], "Scc": [], "Scd": [], "Sdc": [],
              "Ldiff": [], "Qdiff": [], "Rdiff": []}
    for k, f in enumerate(freqs):
        s11, s12 = S[k][0][0], S[k][0][1]
        s21, s22 = S[k][1][0], S[k][1][1]
        sdd = 0.5 * (s11 - s12 - s21 + s22)
        scc = 0.5 * (s11 + s12 + s21 + s22)
        scd = 0.5 * (s11 + s12 - s21 - s22)
        sdc = 0.5 * (s11 - s12 + s21 - s22)
        curves["Sdd"].append(sdd)
        curves["Scc"].append(scc)
        curves["Scd"].append(scd)
        curves["Sdc"].append(sdc)
        if f > 0 and abs(1.0 - sdd) > 1e-12:
            # 混合模 Sdd 的参考阻抗是差分 2*Z0（不是单端 Z0）。
            # 用 2*Z0 换算才得到两端之间的真实差模阻抗。
            zd = 2.0 * z0 * (1.0 + sdd) / (1.0 - sdd)
            w = 2.0 * math.pi * f
            curves["Ldiff"].append(zd.imag / w)
            curves["Rdiff"].append(zd.real)
            curves["Qdiff"].append(zd.imag / zd.real if zd.real else float("nan"))
    fx = [f for f in freqs if f > 0]
    out = dict(curves)
    out["SRF_diff"] = _srf_from_curve(fx, curves["Ldiff"]) if curves["Ldiff"] else None
    if f0 is not None and curves["Ldiff"]:
        out["Ldiff@f0"] = _interp(fx, curves["Ldiff"], f0)
        out["Qdiff@f0"] = _interp(fx, curves["Qdiff"], f0)
    return out


def ct_metrics(freqs, S, z0=50.0, sig=(0, 1), ct_idx=2, f0=None):
    """中心抽头 3 端口：Z 参数奇模驱动。

    返回：
      Ldiff/LQdiff  —— p01-p02 两端之间的总差模电感（ct 交流接地），
                       Ldiff = Im(Z11+Z22-Z12-Z21)/w；
      Lhalf         —— 总电感/4（对称、k≈1 时的单边等效，标注为派生量）；
      Zct           —— 中心抽头对地阻抗模值 |Z33|（判断 ct 是否有效接地/悬空）。
    """
    out = {"freqs": list(freqs), "Ldiff": [], "Lhalf": [], "Qdiff": [], "Zct": []}
    for k, f in enumerate(freqs):
        if f <= 0:
            continue
        z = s_to_z(S[k], z0)
        i1, i2 = sig
        # 差分驱动：I1 = +1, I2 = -1, Ict = 0（ct 端口短路到地）
        zdd = z[i1][i1] + z[i2][i2] - z[i1][i2] - z[i2][i1]
        w = 2.0 * math.pi * f
        out["Ldiff"].append(zdd.imag / w)
        out["Lhalf"].append(zdd.imag / w / 4.0)
        out["Qdiff"].append(zdd.imag / zdd.real if zdd.real else float("nan"))
        out["Zct"].append(abs(z[ct_idx][ct_idx]))
    fx = [f for f in freqs if f > 0]
    if f0 is not None and out["Ldiff"]:
        out["Ldiff@f0"] = _interp(fx, out["Ldiff"], f0)
        out["Qdiff@f0"] = _interp(fx, out["Qdiff"], f0)
    out["SRF_diff"] = _srf_from_curve(fx, out["Ldiff"]) if out["Ldiff"] else None
    return out


def analyze(parsed, kind, f0=None, port_order=None):
    """按端口名（EMX 输出顺序）计算指标；port_order 形如 ['ct','p01','p02']。"""
    freqs, S, n = parsed["freqs"], parsed["S"], parsed["nports"]
    order = list(port_order) if port_order else ["p%02d" % (i + 1) for i in range(n)]
    idx = {name: i for i, name in enumerate(order)}
    if kind in ("ind", "se"):
        i0 = idx.get("p01", 0)
        i1 = idx.get("p02", 1 if n > 1 else 0)
        return inductor_metrics(freqs, S, port_i=i0, port_j=i1, f0=f0)
    if kind == "diff":
        return diff_metrics(freqs, S, f0=f0)
    if kind == "ct":
        ct_name = "ct" if "ct" in idx else ("ct1" if "ct1" in idx else None)
        if ct_name is None or "p01" not in idx or "p02" not in idx:
            raise ValueError("ct 分析需要 p01/p02/ct 三个端口，实际 %s" % order)
        out = ct_metrics(freqs, S, sig=(idx["p01"], idx["p02"]),
                         ct_idx=idx[ct_name], f0=f0)
        out["port_order"] = order
        return out
    raise ValueError("未知 kind=%r" % kind)


def summary(snp_path, kind="ind", f0=2.4e9):
    """一站式：读 sNp -> 指标 dict（JSON 友好）。"""
    d = parse_touchstone(snp_path)
    n = d["nports"]
    res = {"file": snp_path, "nports": n, "nfreq": len(d["freqs"]),
           "freq_range": [d["freqs"][0], d["freqs"][-1]], "z0": d["z0"]}
    if kind in ("ind", "se") and n >= 2:
        res.update(inductor_metrics(d["freqs"], d["S"], f0=f0))
    if kind == "diff" and n == 2:
        res.update(diff_metrics(d["freqs"], d["S"], f0=f0))
    if kind == "ct" and n >= 3:
        res.update(ct_metrics(d["freqs"], d["S"], f0=f0))
    if kind == "diff" and n >= 3:
        # 3 端口：取信号端 (0,1) 与 ct(2)
        res.update(ct_metrics(d["freqs"], d["S"], f0=f0))
    return res


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="sNp -> L/Q/R/SRF/混合模指标")
    ap.add_argument("snp")
    ap.add_argument("--kind", default="ind", choices=["ind", "se", "diff", "ct"])
    ap.add_argument("--f0", type=float, default=2.4e9)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = summary(a.snp, a.kind, a.f0)
    def _clean(o):
        if isinstance(o, complex):
            return [o.real, o.imag]
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        return o
    print(json.dumps(_clean(r), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
