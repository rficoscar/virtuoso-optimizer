# -*- coding: utf-8 -*-
"""lri.ui.netlist — 网表分析解析、频率范围与 simset 文本生成。"""
from __future__ import print_function

import os
import re
import time

from ...cli import (
    format_eng, parse_eng)

_FREQ_ANALYSES = ("ac", "sp", "noise", "snoise", "pac", "psp", "xf")

_FREQ_LO_KEYS = ("start", "fstart", "f1", "fmin", "begin", "from")

_FREQ_HI_KEYS = ("stop", "fstop", "f2", "fmax", "end", "to")

_FREQ_ONE_KEYS = ("freq", "frequency", "f")

_NETLIST_ANALYSES = ("ac", "dc", "tran", "sp", "noise", "pss", "pac",
                     "hb", "xf", "snoise", "noise1")

_UNIT_HINT = ("提示: 数值无工程后缀时按 SI Hz 解读 (start=1 即 1 Hz, 不是 1 GHz);"
              " ADE/网表请写全后缀 (1G/10G)。若你的流程约定裸数字=GHz,"
              " 覆盖判断可能偏严, 请以网表后缀为准。")

def _eng_or_none(text):
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        return parse_eng(s)
    except Exception:
        return None

def _eng_bare(text):
    if text is None:
        return None, False
    s = str(text).strip()
    if not s:
        return None, False
    return _eng_or_none(s), bool(re.search(r"[A-Za-z]+$", s))

def _lower_fields(fields):
    return {str(k).strip().lower(): v for k, v in (fields or {}).items()}

def _freq_from_fields(fields, bare_flag):
    lo = hi = None
    for keys, is_hi in ((_FREQ_LO_KEYS, False), (_FREQ_HI_KEYS, True)):
        for k in keys:
            if k in fields:
                v, bare = _eng_bare(fields[k])
                if v is not None:
                    if bare:
                        bare_flag[0] = True
                    if is_hi:
                        hi = v
                    else:
                        lo = v
                    break
    if lo is None and hi is None:
        for k in _FREQ_ONE_KEYS:
            if k in fields:
                v, bare = _eng_bare(fields[k])
                if v is not None:
                    if bare:
                        bare_flag[0] = True
                    lo = hi = v
                    break
    if lo is None or hi is None:
        return None, None
    return (hi, lo) if lo > hi else (lo, hi)

def _freq_union(entries, missing, tag_prefix, tag_src):
    los, his, labels, bare_flag = [], [], [], [False]
    for a in entries or []:
        atype = str(a.get("type", "")).strip().lower()
        if atype not in _FREQ_ANALYSES:
            continue
        lo, hi = _freq_from_fields(_lower_fields(a.get("fields")), bare_flag)
        if lo is None or hi is None:
            continue
        los.append(lo)
        his.append(hi)
        labels.append(atype)
    if not los:
        return None, None, missing
    tag = tag_prefix + (",".join(labels) if labels else tag_src)
    if bare_flag[0]:
        tag += " [含无后缀数值,按Hz]"
    return min(los), max(his), tag

def freq_range_from_analyses(analyses):
    return _freq_union(analyses, "分析字段中未找到频率范围", "analyses.state:", "analyses")

def _strip_inline_comment(s):
    while "/*" in s and "*/" in s:
        i = s.find("/*")
        j = s.find("*/", i + 2)
        if j < 0:
            break
        s = s[:i] + " " + s[j + 2:]
    for mark in ("//", "#", ";"):
        if mark in s:
            s = s.split(mark, 1)[0]
    return s.strip()

def _join_continuations(text):
    out, buf = [], ""
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if buf:
            piece = line.lstrip()
            if piece.startswith("+"):
                piece = piece[1:].lstrip()
            buf = buf + " " + piece
        else:
            buf = line
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out

def _split_tokens(s):
    toks, cur, in_q = [], [], None
    for ch in s:
        if in_q:
            cur.append(ch)
            if ch == in_q:
                in_q = None
        elif ch in ('"', "'"):
            in_q = ch
            cur.append(ch)
        elif ch.isspace():
            if cur:
                toks.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        toks.append("".join(cur))
    return toks

def parse_netlist_analyses(text):
    out = []
    for raw in _join_continuations(text):
        s = _strip_inline_comment(raw)
        if not s or s.startswith(("//", "*", ";", "#")):
            continue
        toks = _split_tokens(s)
        if len(toks) < 2 or toks[1].lower() not in _NETLIST_ANALYSES:
            continue
        fields = {}
        for tok in toks[2:]:
            if "=" in tok:
                key, _eq, val = tok.partition("=")
                fields[key.strip().lower()] = val.strip().strip('"').strip("'")
        out.append({"type": toks[1].lower(), "fields": fields, "name": toks[0]})
    return out

def freq_range_from_netlist(text):
    return _freq_union(parse_netlist_analyses(text), "网表中未找到频率扫描语句", "netlist:", "netlist")

def coverage_label(sim_lo, sim_hi, band):
    if band is None:
        return "目标无band"
    try:
        if len(band) < 2:
            return "非法band"
        blo, bhi = _eng_or_none(band[0]), _eng_or_none(band[1])
    except Exception:
        return "非法band"
    if blo is None or bhi is None:
        return "非法band"
    if blo > bhi:
        blo, bhi = bhi, blo
    if sim_lo is None or sim_hi is None:
        return "仿真频率未知"
    if sim_lo > sim_hi:
        sim_lo, sim_hi = sim_hi, sim_lo
    if sim_lo <= blo and sim_hi >= bhi:
        return "完全覆盖"
    if sim_hi < blo or sim_lo > bhi:
        return "不覆盖"
    return "部分重叠"

def _fmt_hz(v):
    if v is None:
        return "?"
    try:
        return format_eng(v)
    except Exception:
        return str(v)

def build_simset_text(state_dir, info, netlist_path, netlist_params,
                      netlist_text, targets, extra_notes=None):
    nl = chr(10)
    lines = [
        "仿真设置（只读 · 实时展示 · 优化器不修改 ADE）", "=" * 52,
        "说明: 优化时 spectre 直跑网表快照; 每轮只改写顶层",
        "      parameters 行(优化+固定变量)。分析/模型/温度等",
        "      继承自网表快照, 运行中改 ADE 不影响本轮优化。", "",
        "【分析 analyses】 来源: analyses.state"]
    if info is None:
        lines.append("  (状态目录不可用或解析失败)")
    else:
        analyses = info.get("analyses") or []
        if not analyses:
            lines.append("  (analyses.state 中没有分析定义)")
        for i, a in enumerate(analyses, 1):
            atype = a.get("type", "?")
            fields = a.get("fields") or {}
            lines.append(f"  [{i}] 类型: {atype}   字段数: {len(fields)}")
            if not fields:
                lines.append("      (无 fields)")
            for key in sorted(fields.keys()):
                lines.append(f"      {key} = {fields[key]}")
    lines.append("")
    st_lo, st_hi, st_src = freq_range_from_analyses((info or {}).get("analyses"))
    nl_lo, nl_hi, nl_src = freq_range_from_netlist(netlist_text)
    if nl_lo is not None or nl_hi is not None:
        sim_lo, sim_hi = nl_lo, nl_hi
    else:
        sim_lo, sim_hi = st_lo, st_hi
    lines += [
        "【仿真频率范围】",
        f"  analyses.state : {_fmt_hz(st_lo)} ~ {_fmt_hz(st_hi)}   ({st_src})",
        f"  网表快照       : {_fmt_hz(nl_lo)} ~ {_fmt_hz(nl_hi)}   ({nl_src})",
        "  " + _UNIT_HINT,
        f"  用于覆盖判断   : {_fmt_hz(sim_lo)} ~ {_fmt_hz(sim_hi)}", ""]
    lines.append("【网表快照】")
    if netlist_path:
        lines.append(f"  路径: {netlist_path}")
        try:
            mtime = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(os.path.getmtime(netlist_path)))
            lines.append(f"  修改时间: {mtime}")
        except OSError:
            lines.append("  修改时间: (读取失败)")
        lines += ["  机制: 启动时读取一次; 每轮仅改写顶层 parameters 行", "        后直跑 spectre; 中途改 ADE/网表不影响本轮。"]
    else:
        lines.append("  路径: (未找到; 设计页填「网表」或点「查找网表」)")
    lines += ["", "【parameters 行当前值】"]
    if netlist_params is None:
        lines.append("  (网表不可用或解析失败)")
    elif not netlist_params:
        lines.append("  (顶层没有 parameters 行, 优化器将无法写入变量)")
    else:
        lines.append(f"  共 {len(netlist_params)} 项:")
        for key in sorted(netlist_params.keys()):
            lines.append(f"    {key} = {netlist_params[key]}")
    lines += ["", "【模型文件】 来源: modelSetup.state"]
    models = (info or {}).get("model_files") or []
    if info is None:
        lines.append("  (状态目录不可用)")
    elif not models:
        lines.append("  (modelSetup.state 未列出模型文件; 网表内 include 仍生效)")
    else:
        lines.extend("  %s" % p for p in models)
    lines += ["", "【目标频段 vs 仿真频率范围】"]
    if not targets:
        lines.append("  (目标页还没有目标)")
    for i, spec in enumerate(targets or [], 1):
        out = spec.get("output", "?")
        metric = spec.get("metric", "scalar")
        band = spec.get("band")
        band_txt = "无"
        if band:
            try:
                band_txt = ("[%s, %s]" % (band[0], band[1]) if len(band) >= 2
                            else "非法(%r)" % (band,))
            except Exception:
                band_txt = "非法(%r)" % (band,)
        cov = coverage_label(sim_lo, sim_hi, band if band else None)
        extra = ""
        freq = spec.get("freq")
        if freq:
            fv = _eng_or_none(freq)
            if fv is not None and sim_lo is not None and sim_hi is not None:
                lo2, hi2 = ((sim_lo, sim_hi) if sim_lo <= sim_hi else (sim_hi, sim_lo))
                extra = ("; 频率 %s 在仿真范围内" % freq if lo2 <= fv <= hi2 else "; 频率 %s 不在仿真范围内" % freq)
            else:
                extra = f"; 频率 {freq}"
        if spec.get("curve_op"):
            extra += f"; 限值线 curve_op={spec.get('curve_op')}"
        lines.append(f"  [{i}] {out}  metric={metric}  band={band_txt}" f"  -> {cov}{extra}")
    lines.append("")
    if extra_notes:
        lines.append("【提示】")
        lines.extend("  %s" % note for note in extra_notes)
        lines.append("")
    lines.append(f"状态目录: {state_dir or '(未填写)'}")
    return nl.join(lines)
