# -*- coding: utf-8 -*-
"""lri.emx — EMX 命令行构建、sNp 校验与运行。"""
from __future__ import print_function

import math
import os

from .common import (
    open, DEF_EMX_TIMEOUT, LibrfError, _eprint, note_created, _assert_writable, parse_eng,
    run_cmd)
from .tools import (
    require_tool)
from .skillio import (
    _snp_has_explicit_port_order, _snp_append_port_order)
from .snp import (
    read_snp, ensure_snp_header)

def parse_freq_spec(spec):
    if spec is None:
        return []
    s = str(spec).strip()
    if not s:
        return []
    if ":" in s:
        parts = s.split(":")
        if len(parts) != 3:
            raise LibrfError("频点语法错误 %r（示例 1e9:30e9:30）" % spec)
        start = parse_eng(parts[0])
        stop = parse_eng(parts[1])
        ntok = parts[2].strip().lower()
        logn = ntok.startswith("log")
        n = int(float(ntok[3:] if logn else ntok))
        if n < 1:
            raise LibrfError("频点数必须 >=1: %r" % spec)
        if n == 1:
            return [start]
        if logn:
            if start <= 0 or stop <= 0:
                raise LibrfError("对数扫频要求正频率: %r" % spec)
            lo, hi = math.log10(start), math.log10(stop)
            return [10.0 ** (lo + (hi - lo) * i / (n - 1)) for i in range(n)]
        return [start + (stop - start) * i / (n - 1) for i in range(n)]
    out = []
    for tok in s.replace(";", ",").split(","):
        tok = tok.strip()
        if tok:
            out.append(parse_eng(tok))
    return out

def build_emx_cmd(emx_bin, gds, struct, proc, freqs, out_snp,
                  edge=None, three_d=None, mode=None, ports=None,
                  extra=None, sweep=False, s_impedance=None):
    extra_list = [str(x) for x in (extra or [])]
    s_imp_flags = [a for a in extra_list if a.startswith("--s-impedance")]
    extra_rest = [a for a in extra_list if not a.startswith("--s-impedance")]
    if s_impedance is not None:
        s_imp_flags.append("--s-impedance=%g" % float(s_impedance))
    cmd = [emx_bin]
    cmd += s_imp_flags
    cmd += ["--format=touchstone", "--s-file=%s" % os.path.abspath(out_snp)]
    if three_d is None:
        three_d = "*"
    if edge is None:
        edge = 0.5
    if three_d:
        cmd.append("--3d=%s" % three_d if str(three_d) not in ("1", "true", "") else "--3d")
    if edge is not None:
        cmd += ["-e", "%g" % float(edge)]
    if mode:
        cmd.append("--mode=%s" % mode)
    for p in (ports or []):
        cmd.append("-p")
        cmd.append("%s=%s" % (p, p))
    if sweep and not any(a == "--sweep" or a.startswith("--multi-sweep") for a in extra_rest):
        cmd.append("--sweep")
    cmd += extra_rest
    cmd.append(os.path.abspath(gds))
    cmd.append(str(struct))
    cmd.append(os.path.abspath(proc))
    for f in (freqs or []):
        cmd.append("%.10g" % f)
    return cmd

def validate_snp(path, n_freq_expected=None, n_ports_expected=None):
    if not os.path.isfile(path):
        raise LibrfError("EMX 未产出 sNp 文件: %s" % path)
    if os.path.getsize(path) == 0:
        raise LibrfError("sNp 文件为空: %s" % path)
    ensure_snp_header(path)
    freqs, S, z0, ports = read_snp(path)
    if not freqs:
        raise LibrfError("sNp 无频点数据: %s" % path)
    nports = len(S[0])
    if n_ports_expected is not None and nports != n_ports_expected:
        po = " ".join(str(x) for x in (ports or [])) or "(文件未标注)"
        raise LibrfError("sNp 端口数 %d != 期望 %d，结果不可信。Port order: %s"
                         % (nports, n_ports_expected, po))
    warn = None
    if n_freq_expected is not None and len(freqs) != n_freq_expected:
        warn = "频点数 %d != 期望 %d" % (len(freqs), n_freq_expected)
    return {"ok": True, "warn": warn, "nports": nports, "nfreq": len(freqs),
            "ports": ports, "z0": z0}

def run_emx(gds, struct, proc, freqs, out_snp, tools,
            edge=None, three_d=None, mode=None, ports=None,
            timeout=DEF_EMX_TIMEOUT, verbose=False, extra=None,
            sweep=False, s_impedance=None, n_freq_check=None):
    emx = require_tool(tools, "emx", "EMX 电磁仿真")
    if not freqs:
        raise LibrfError("run_emx: 频点列表为空（freq 未指定）。EMX 无扫频参数会挂起，"
            "请传 freq 如 '1e9:20e9:11' 或 DEF_FREQ。")
    if not proc:
        raise LibrfError("EMX 工艺文件（proc）未指定。请用环境变量 LIBRF_PROC 指向 proc "
            "文件，或把 proc 放在工作/工艺目录中由工具自动探查。")
    if not os.path.isfile(proc):
        raise LibrfError("EMX 工艺文件不存在: %s" % proc)
    out_snp = os.path.abspath(out_snp)
    out_dir = os.path.dirname(out_snp)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    _assert_writable(out_snp, "sNp 文件")
    cmd = build_emx_cmd(emx, gds, struct, proc, freqs, out_snp,
                        edge=edge, three_d=three_d, mode=mode, ports=ports,
                        extra=extra, sweep=sweep, s_impedance=s_impedance)
    r = run_cmd(cmd, cwd=os.path.dirname(out_snp) or None, timeout=timeout, verbose=verbose)
    if os.path.isfile(out_snp):
        note_created(out_snp)
    logf = out_snp + ".emx.log"
    try:
        _assert_writable(logf, "EMX 日志")
        with open(logf, "w") as f:
            f.write("$ %s\n" % r["cmd"])
            f.write(r["stdout"])
            f.write("\n----- stderr -----\n")
            f.write(r["stderr"])
        note_created(logf)
    except (IOError, LibrfError):
        pass
    if r["timeout"]:
        raise LibrfError("EMX 超时（%ss），日志 %s。大器件请增大 timeout 后重试" % (timeout, logf))
    warns = []
    if n_freq_check is None and not sweep:
        n_freq_check = len(freqs) if freqs else None
    try:
        v = validate_snp(out_snp, n_freq_expected=n_freq_check,
                         n_ports_expected=(len(ports) if ports else None))
    except LibrfError as e:
        raise LibrfError("EMX 结果不合格: %s；日志 %s\n%s" % (e, logf, (r["stderr"] or r["stdout"])[-800:]))
    if v.get("warn"):
        warns.append(v["warn"])
    if ports and not _snp_has_explicit_port_order(out_snp):
        try:
            _snp_append_port_order(out_snp, sorted(str(p) for p in ports))
        except IOError as e:
            warns.append("补写 Port order 失败: %s" % e)
    out_txt = (r["stdout"] or "") + "\n" + (r["stderr"] or "")
    if "Results written" not in out_txt:
        raise LibrfError("EMX 未确认写出结果（输出无 'Results written'），"
                         "日志 %s\n%s" % (logf, (r["stderr"] or r["stdout"])[-800:]))
    if not r["ok"]:
        warns.append("EMX rc=%s 但产物合格，按成功收下（详见 %s）" % (r["rc"], logf))
    for w in warns:
        _eprint("WARN %s" % w)
    return {"ok": True, "cmd": r["cmd"], "out_snp": out_snp, "validate": v,
            "stdout": r["stdout"], "stderr": r["stderr"], "log": logf,
            "warn": "; ".join(warns) if warns else None}
