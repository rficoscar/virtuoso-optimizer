# -*- coding: utf-8 -*-

import json
import math
import re
import sys

__all__ = [
    "parse_netlist_parameters",
    "rewrite_netlist_parameters",
    "inspect_netlist",
    "patch_netlist",
    "parse_value",
    "format_value",
    "format_eng",
]

_ENG_SUFFIX = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
    "f": 1e-15, "a": 1e-18,
}

_NUM_RE = re.compile(
    r"^([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
    r"([A-Za-z]*)$")

def parse_value(text):
    match = _NUM_RE.match(str(text).strip())
    if not match:
        raise ValueError("bad number: %s" % text)
    suffix = match.group(2).lower()
    scale = 1.0
    if suffix:
        if suffix not in _ENG_SUFFIX:
            raise ValueError("bad suffix: %s" % text)
        scale = _ENG_SUFFIX[suffix]
    return float(match.group(1)) * scale

_NON_FINITE_TEXT = frozenset((
    "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity",
    "nan", "+nan", "-nan",
))

def _is_finite(value):
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(value)
        except (OverflowError, ValueError):
            return False
    text = "" if value is None else str(value).strip()
    if not text:
        return True
    if text.lower() in _NON_FINITE_TEXT:
        return False
    try:
        return math.isfinite(parse_value(text))
    except (ValueError, OverflowError):
        return True

def format_eng(value):
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("bad number: %s" % (value,))
    if not math.isfinite(value):
        raise ValueError("bad number: %s" % (value,))
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    value = abs(value)
    for scale, suffix in ((1e12, "T"), (1e9, "G"), (1e6, "meg"), (1e3, "k"),
                          (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
                          (1e-12, "p"), (1e-15, "f"), (1e-18, "a")):
        if value >= scale:
            return "%s%g%s" % (sign, value / scale, suffix)
    return "%s%g" % (sign, value)

def format_value(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        try:
            return format_eng(value)
        except (OverflowError, ValueError):
            return ""
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    try:
        number = parse_value(text)
    except ValueError:
        return text
    try:
        return format_eng(number)
    except (OverflowError, ValueError):
        return ""

def _format_fixed(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        try:
            return format_eng(value)
        except (OverflowError, ValueError):
            return ""
    if value is None:
        return ""
    return str(value).strip()

_KEYWORDS = frozenset("""
simulator global include ahdl_include hdl_include parameters subckt ends
inline model library endlibrary section endsection statistics process
paramtest montecarlo options save ic nodeset temp alter altergroup real
check assert shell set title end info fatal warning error
tran ac dc noise sp pss hb envlp xf sens pz stb dcmatch acmatch sweep
load pull correlate mismatch montecarlo
""".split())

_ANALYSIS = frozenset("""
ac acmatch acnoise dc dcmatch dcop disto envlp fourier hb hbac hbnoise hbsp
montecarlo mpde noise pac pacnoise pnoise pss pstb psp pz ql sens sp
spnoise stb sweep sweephb tf tran xf
""".split())

def _split_lines(text):
    lines = text.splitlines(keepends=True)
    starts = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line)
    return lines, starts

def _body(line):
    body = line
    if body.endswith("\n"):
        body = body[:-1]
    if body.endswith("\r"):
        body = body[:-1]
    return body

def _mask_block_comments(text):  # 掩码必须等长替换，偏移才不漂移
    out = list(text)
    in_block = False
    quote = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_block:
            if ch == "*" and i + 1 < n and text[i + 1] == "/":
                out[i] = " "
                out[i + 1] = " "
                in_block = False
                i += 2
                continue
            if ch not in "\r\n":
                out[i] = " "
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = " "
            out[i + 1] = " "
            in_block = True
            i += 2
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        i += 1
    return "".join(out)

def _comment_index(body):
    quote = None
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "/" and i + 1 < n and body[i + 1] == "/":
            return i
        i += 1
    return n

def _code_of(body):
    stripped = body.lstrip()
    if not stripped:
        return ""
    if (stripped.startswith("//") or stripped.startswith("*")
            or stripped.startswith(";") or stripped.startswith("#")):
        return ""
    return body[:_comment_index(body)]

def _tokenize(code, base):
    tokens = []
    n = len(code)
    i = 0
    while i < n:
        while i < n and code[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        quote = None
        depth = 0
        while i < n:
            ch = code[i]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch in "({[":
                depth += 1
            elif ch in ")}]":
                if depth > 0:
                    depth -= 1
            elif ch.isspace() and depth == 0:
                break
            i += 1
        tokens.append((code[start:i], base + start, base + i))
    return tokens

def _balance(code):
    depth = 0
    quote = None
    for ch in code:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    return depth

def _parse(text):
    masked = _mask_block_comments(text)
    lines, starts = _split_lines(masked)
    stmts = []
    n = len(lines)
    i = 0
    while i < n:
        j = i
        depth = 0
        while True:
            depth += _balance(_code_of(_body(lines[j])))
            if j + 1 >= n:
                break
            current = lines[j].rstrip()
            nxt = lines[j + 1].lstrip()
            if (current.endswith("\\") or nxt.startswith("+")
                    or depth > 0):
                j += 1
                continue
            break
        tokens = []
        for k in range(i, j + 1):
            body = _body(lines[k])
            code = _code_of(body)
            toks = _tokenize(code, starts[k])
            if toks and toks[0][0] == "+":
                toks = toks[1:]
            if toks and toks[-1][0].endswith("\\"):
                trimmed = toks[-1][0][:-1]
                if trimmed:
                    toks[-1] = (trimmed, toks[-1][1], toks[-1][2] - 1)
                else:
                    toks.pop()
            tokens.extend(toks)
        stmts.append({
            "first": i,
            "last": j,
            "tokens": tokens,
            "kind": "blank",
            "name": None,
            "scope": None,
            "depth": 0,
            "header_start": 1,
        })
        i = j + 1
    return masked, lines, starts, stmts

def _assign_parts(tok):
    text, start, end = tok
    quote = None
    depth = 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch in "({[":
            depth += 1
            continue
        if ch in ")}]":
            if depth > 0:
                depth -= 1
            continue
        if ch == "=" and depth == 0:
            return {
                "name": text[:i],
                "value": text[i + 1:],
                "name_start": start,
                "name_end": start + i,
                "value_start": start + i + 1,
                "value_end": end,
            }
    return None

def _param_entries(stmt, start=1, allow_bare=True):
    out = []
    for tok in stmt["tokens"][start:]:
        parts = _assign_parts(tok)
        if parts is not None:
            if parts["name"]:
                out.append(parts)
            continue
        if not allow_bare:
            continue
        if not tok[0] or tok[0] in ("+", "\\"):
            continue
        out.append({
            "name": tok[0],
            "value": None,
            "name_start": tok[1],
            "name_end": tok[2],
            "value_start": None,
            "value_end": None,
        })
    return out

def _classify(stmt):
    toks = stmt["tokens"]
    if not toks:
        stmt["kind"] = "blank"
        return
    head = toks[0][0].lower()
    if head == "inline" and len(toks) > 1 and toks[1][0].lower() == "subckt":
        stmt["kind"] = "subckt"
        stmt["name"] = toks[2][0] if len(toks) > 2 else ""
        stmt["header_start"] = 3
        return
    if head == "subckt":
        stmt["kind"] = "subckt"
        stmt["name"] = toks[1][0] if len(toks) > 1 else ""
        stmt["header_start"] = 2
        return
    if head == "ends":
        stmt["kind"] = "ends"
        return
    if head == "parameters":
        stmt["kind"] = "parameters"
        return
    if head in ("include", "ahdl_include", "hdl_include"):
        stmt["kind"] = "include"
        return
    if head in _KEYWORDS:
        stmt["kind"] = "directive"
        return
    if "=" in toks[0][0] or len(toks) < 2:
        stmt["kind"] = "directive"
        return
    if toks[1][0].lower() in _ANALYSIS:
        stmt["kind"] = "directive"
        return
    stmt["kind"] = "instance"
    stmt["name"] = toks[0][0]

def _instance_info(stmt):
    toks = stmt["tokens"]
    name = toks[0][0]
    rest = toks[1:]
    master = None
    index = 0
    if rest and rest[0][0].startswith("("):
        depth = rest[0][0].count("(") - rest[0][0].count(")")
        index = 1
        while index < len(rest) and depth > 0:
            depth += rest[index][0].count("(") - rest[index][0].count(")")
            index += 1
    if index < len(rest):
        master = rest[index][0]
    params = {}
    for tok in rest:
        parts = _assign_parts(tok)
        if parts is None or not parts["name"]:
            continue
        params[parts["name"]] = (
            parts["value"], (parts["value_start"], parts["value_end"]))
    return {
        "line": stmt["first"],
        "name": name,
        "master": master,
        "params": params,
        "scope": None,
    }

def _include_info(stmt):
    toks = stmt["tokens"]
    if len(toks) < 2:
        return None
    path = toks[1][0]
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "\"'":
        path = path[1:-1]
    return {"path": path, "line": stmt["first"], "kind": toks[0][0].lower()}

def _analyze(text):
    masked, lines, starts, stmts = _parse(text)
    scopes = []
    top_stmts = []
    instances = []
    includes = []
    stack = []
    last_line = max(0, len(lines) - 1)
    for idx, stmt in enumerate(stmts):
        _classify(stmt)
        kind = stmt["kind"]
        if kind == "subckt":
            scope = {
                "name": stmt["name"] or "",
                "kind": "subckt",
                "start": stmt["first"],
                "end": last_line,
                "header": idx,
                "stmts": [idx],
            }
            stmt["scope"] = scope
            stmt["depth"] = len(stack)
            stack.append(scope)
            scopes.append(scope)
        elif kind == "ends":
            stmt["depth"] = max(0, len(stack) - 1)
            if stack:
                stack.pop()["end"] = stmt["last"]
        elif kind == "parameters":
            stmt["depth"] = len(stack)
            if stack:
                scope = stack[-1]
                stmt["scope"] = scope
                scope["stmts"].append(idx)
            else:
                top_stmts.append(idx)
        else:
            stmt["depth"] = len(stack)
            if stack:
                stmt["scope"] = stack[-1]
            if kind == "instance":
                info = _instance_info(stmt)
                info["scope"] = stack[-1]["name"] if stack else None
                instances.append(info)
            elif kind == "include":
                info = _include_info(stmt)
                if info is not None:
                    includes.append(info)
    return {
        "masked": masked,
        "lines": lines,
        "starts": starts,
        "stmts": stmts,
        "scopes": scopes,
        "top_stmts": top_stmts,
        "instances": instances,
        "includes": includes,
    }

def parse_netlist_parameters(text):
    info = _analyze("" if text is None else str(text))
    out = {}
    for idx in info["top_stmts"]:
        for entry in _param_entries(info["stmts"][idx], 1, True):
            out[entry["name"]] = "" if entry["value"] is None else entry["value"]
    return out

def rewrite_netlist_parameters(text, line):
    text = "" if text is None else str(text)
    info = _analyze(text)
    spans = []
    for idx in info["top_stmts"]:
        stmt = info["stmts"][idx]
        spans.append((stmt["first"], stmt["last"]))
    if not spans:
        return text, 0
    lines = text.splitlines(keepends=True)
    out = []
    cursor = 0
    for number, (first, last) in enumerate(spans):
        out.extend(lines[cursor:first])
        if number == 0:
            out.append(line + "\n")
        cursor = last + 1
    out.extend(lines[cursor:])
    new_text = "".join(out)
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, len(spans)

def inspect_netlist(text):
    text = "" if text is None else str(text)
    info = _analyze(text)
    stmts = info["stmts"]

    top_params = {}
    for idx in info["top_stmts"]:
        for entry in _param_entries(stmts[idx], 1, True):
            top_params[entry["name"]] = entry["value"]

    scopes_out = [{
        "name": "top",
        "kind": "top",
        "start": 0,
        "end": max(0, len(info["lines"]) - 1),
        "params": dict(top_params),
    }]
    for scope in info["scopes"]:
        params = {}
        for idx in scope["stmts"]:
            stmt = stmts[idx]
            if idx == scope["header"]:
                entries = _param_entries(stmt, stmt["header_start"], False)
            else:
                entries = _param_entries(stmt, 1, True)
            for entry in entries:
                params[entry["name"]] = entry["value"]
        scopes_out.append({
            "name": scope["name"],
            "kind": scope["kind"],
            "start": scope["start"],
            "end": scope["end"],
            "params": params,
        })

    instances_out = []
    for instance in info["instances"]:
        params = {}
        for key, (value, span) in instance["params"].items():
            params[key] = (value, list(span) if span else None)
        instances_out.append({
            "line": instance["line"],
            "name": instance["name"],
            "master": instance["master"],
            "params": params,
        })

    return {
        "top_params": top_params,
        "scopes": scopes_out,
        "instances": instances_out,
        "includes": [inc["path"] for inc in info["includes"]],
    }

def _option_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "y"):
        return True
    if text in ("0", "false", "no", "off", "n", ""):
        return False
    return default

def _normalize_scope_map(raw):
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = [str(item) for item in value]
        else:
            values = []
        cleaned = [item.strip() for item in values if str(item).strip()]
        out[str(key)] = cleaned
    return out

def _valid_location(loc):
    if loc in ("top", "sub", "sub:*", "inst", "instance", "inst:*"):
        return True
    if loc.startswith("sub:") and len(loc) > 4:
        return True
    if loc.startswith("inst:") and len(loc) > 5:
        return True
    return False

def _allows(scope_map, name, kind, scope_name):
    if name not in scope_map:
        return True
    entries = scope_map.get(name) or []
    if kind == "top":
        return "top" in entries
    if kind == "sub":
        target = "sub:%s" % scope_name
        return ("sub" in entries or "sub:*" in entries or target in entries)
    if kind == "inst":
        for entry in entries:
            if entry in ("inst", "instance", "inst:*"):
                return True
            if entry.startswith("inst:") and entry[5:].split(".")[0] == str(scope_name):
                return True
        return False
    return True

def _unique(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

def _eol_of(text):
    return "\r\n" if "\r\n" in text else "\n"

def _append_offset(stmt, lines, starts):
    index = stmt["last"]
    body = _body(lines[index])
    code = _code_of(body)
    stripped = code.rstrip()
    rel = len(stripped)
    if stripped.endswith("\\"):
        rel = len(stripped) - 1
        while rel > 0 and stripped[rel - 1].isspace():
            rel -= 1
    return starts[index] + rel

def _apply_edits(text, edits):  # 从后往前替换，前部偏移不受影响
    for start, end, replacement in sorted(
            edits, key=lambda item: (item[0], item[1]), reverse=True):
        text = text[:start] + replacement + text[end:]
    return text

def patch_netlist(text, values, fixed=None, options=None):
    text = "" if text is None else str(text)
    values = dict(values or {})
    fixed = dict(fixed or {})
    options = dict(options or {})
    patch_subckts = _option_bool(options.get("patch_subckts"), True)
    patch_instances = _option_bool(options.get("patch_instances"), True)
    scope_map = _normalize_scope_map(options.get("scope_map"))

    warnings = []
    invalid = []
    targets = {}
    target_kind = {}

    for raw_name, raw_value in fixed.items():
        name = str(raw_name)
        if not _is_finite(raw_value):
            invalid.append(name)
            warnings.append(
                "fixed 变量 %s 的值不是有限数，已跳过" % name)
            continue
        formatted = _format_fixed(raw_value)
        if not formatted:
            invalid.append(name)
            warnings.append("fixed 变量 %s 的值为空，已跳过" % name)
            continue
        if re.search(r"\s", formatted):
            invalid.append(name)
            warnings.append(
                "fixed 变量 %s 的值 '%s' 含空白，无法写入网表"
                % (name, formatted))
            continue
        targets[name] = formatted
        target_kind[name] = "fixed"

    for raw_name, raw_value in values.items():
        name = str(raw_name)
        if not _is_finite(raw_value):
            invalid.append(name)
            warnings.append(
                "变量 %s 的值不是有限数，已跳过" % name)
            continue
        formatted = format_value(raw_value)
        if not formatted:
            invalid.append(name)
            warnings.append("变量 %s 的值为空，已跳过" % name)
            continue
        if re.search(r"\s", formatted):
            invalid.append(name)
            warnings.append(
                "变量 %s 的值 '%s' 含空白，无法写入网表" % (name, formatted))
            continue
        if name in targets and target_kind.get(name) == "fixed":
            warnings.append("变量 %s 同时出现在 fixed 与 values 中，采用 values"
                            % name)
        targets[name] = formatted
        target_kind[name] = "value"

    invalid = _unique(invalid)
    for name in list(invalid):
        if name in targets and target_kind.get(name) != "fixed":
            invalid.remove(name)

    for name in scope_map:
        if name not in targets:
            warnings.append("scope_map 中的 %s 不在 values/fixed，已忽略" % name)
        for location in scope_map[name]:
            if not _valid_location(location):
                warnings.append(
                    "scope_map[%s] 含未识别的位置 '%s'" % (name, location))

    info = _analyze(text)
    stmts = info["stmts"]
    scopes = info["scopes"]
    top_ids = info["top_stmts"]
    lines = info["lines"]
    starts = info["starts"]

    found = set()
    top_present = set()
    for idx in top_ids:
        for entry in _param_entries(stmts[idx], 1, True):
            found.add(entry["name"])
            top_present.add(entry["name"])
    for scope in scopes:
        for idx in scope["stmts"]:
            stmt = stmts[idx]
            bare_ok = idx != scope["header"]
            start = stmt["header_start"] if idx == scope["header"] else 1
            for entry in _param_entries(stmt, start, bare_ok):
                found.add(entry["name"])
    for instance in info["instances"]:
        for key in instance["params"]:
            found.add(key)

    missing = []
    for name in values:
        if str(name) in invalid or str(name) not in found:
            missing.append(str(name))

    edits = []
    applied = {}
    shadowed = []
    seen_shadow = set()
    instance_overrides = []
    counts = {"top": 0, "subckt": 0, "instance": 0}

    def note(name, location):
        entry = applied.get(name)
        if entry is None:
            entry = {"value": targets[name], "locations": []}
            applied[name] = entry
        if location not in entry["locations"]:
            entry["locations"].append(location)

    for idx in top_ids:
        stmt = stmts[idx]
        for entry in _param_entries(stmt, 1, True):
            name = entry["name"]
            if name not in targets:
                continue
            if not _allows(scope_map, name, "top", None):
                continue
            new_value = targets[name]
            if entry["value"] is None:
                edits.append((entry["name_start"], entry["name_end"],
                              "%s=%s" % (name, new_value)))
                counts["top"] += 1
            elif entry["value"] != new_value:
                edits.append((entry["value_start"], entry["value_end"],
                              new_value))
                counts["top"] += 1
            note(name, "top")

    skip_append = set()
    for name in values:
        name = str(name)
        if name not in found:
            skip_append.add(name)
    skip_append.update(invalid)

    append_names = []
    for name in targets:
        if not _allows(scope_map, name, "top", None):
            continue
        if name in top_present or name in skip_append:
            continue
        append_names.append(name)

    for name in append_names:
        if target_kind.get(name) == "fixed" and name not in found:
            warnings.append(
                "fixed 变量 %s 在全网表未出现，已写入顶层" % name)

    if append_names:
        assignments = ["%s=%s" % (name, targets[name])
                       for name in append_names]
        joined = " ".join(assignments)
        if top_ids:
            first = stmts[top_ids[0]]
            position = _append_offset(first, lines, starts)
            edits.append((position, position, " " + joined))
            for name in append_names:
                note(name, "top")
                counts["top"] += 1
        else:
            position = _new_statement_offset(stmts, lines, starts, text)
            eol = _eol_of(text)
            prefix = ""
            if position > 0 and text[position - 1] not in "\r\n":
                prefix = eol
            edits.append((position, position,
                          prefix + "parameters " + joined + eol))
            for name in append_names:
                note(name, "top")
                counts["top"] += 1

    for scope in scopes:
        scope_name = scope["name"]
        for idx in scope["stmts"]:
            stmt = stmts[idx]
            is_header = idx == scope["header"]
            start = stmt["header_start"] if is_header else 1
            for entry in _param_entries(stmt, start, not is_header):
                name = entry["name"]
                if name not in targets:
                    continue
                if entry["value"] is None:
                    continue
                key = (name, scope_name)
                if key not in seen_shadow:
                    seen_shadow.add(key)
                    shadowed.append({
                        "name": name,
                        "scope": scope_name,
                        "old": entry["value"],
                    })
                if not _allows(scope_map, name, "sub", scope_name):
                    continue
                if not patch_subckts:
                    warnings.append(
                        "子电路 %s 的默认值 %s=%s 未被改写"
                        "（patch_subckts=False）"
                        % (scope_name, name, entry["value"]))
                    continue
                if entry["value"] != targets[name]:
                    edits.append((entry["value_start"], entry["value_end"],
                                  targets[name]))
                    counts["subckt"] += 1
                note(name, "sub:%s" % scope_name)

    for instance in info["instances"]:
        for key, (old_value, span) in instance["params"].items():
            if key not in targets:
                continue
            if not _allows(scope_map, key, "inst", instance["name"]):
                continue
            if not patch_instances:
                warnings.append(
                    "实例 %s.%s=%s 未被改写（patch_instances=False）"
                    % (instance["name"], key, old_value))
                continue
            if old_value is None:
                continue
            if old_value == key:
                continue
            try:
                parse_value(old_value)
            except ValueError:
                warnings.append(
                    "实例 %s.%s=%s 不是数值字面量，跳过"
                    % (instance["name"], key, old_value))
                continue
            new_value = targets[key]
            if old_value != new_value:
                edits.append((span[0], span[1], new_value))
                counts["instance"] += 1
                instance_overrides.append({
                    "name": key,
                    "instance": instance["name"],
                    "old": old_value,
                    "new": new_value,
                    "line": instance["line"],
                })
            note(key, "inst:%s.%s" % (instance["name"], key))

    for name in targets:
        if name in applied or name in missing:
            continue
        warnings.append("变量 %s 在允许的位置没有可改写的参数" % name)
        missing.append(name)
    for name in invalid:
        if name not in missing:
            missing.append(name)
    missing = _unique(missing)

    new_text = _apply_edits(text, edits)
    report = {
        "ok": not missing,
        "applied": applied,
        "missing": missing,
        "shadowed": shadowed,
        "instance_overrides": instance_overrides,
        "warnings": _unique(warnings),
        "counts": counts,
    }
    return new_text, report

def _new_statement_offset(stmts, lines, starts, text):
    anchor = None
    for stmt in stmts:
        if stmt["kind"] != "directive" or stmt["depth"] != 0:
            continue
        if not stmt["tokens"]:
            continue
        head = stmt["tokens"][0][0].lower()
        if head in ("simulator", "global"):
            anchor = stmt
    if anchor is not None:
        return starts[anchor["last"]] + len(lines[anchor["last"]])
    for stmt in stmts:
        if stmt["kind"] != "blank":
            return starts[stmt["first"]]
    return len(text)

_DECK_LINES = [
    "// top of deck, comment mentions w=9u l=9u",
    "simulator lang=spectre",
    "global 0",
    "",
    'include "/pdk/models.scs"',
    "",
    "parameters w=1u l=0.1u \\",
    "    nf=2",
    "",
    "subckt xfmr_tap (a b c) w=2u",
    "parameters Ls=1n Rs=0.5 \\",
    "    + k=0.9",
    "parameters Ctap",
    "I1 (a b) inductor l=Ls",
    "I2 (a c) inductor l=2u",
    "ends xfmr_tap",
    "",
    "X1 (n1 n2) xfmr_tap w=4u Ls=2n",
    "X3 (a b) xfmr_tap w=w",
    "R1 (n1 0) resistor r=1k // w=9u",
    "/* block comment w=9u */",
    "",
    "subckt plain (a b)",
    "parameters Ls Rs",
    "ends plain",
    "",
    "X2 (n3 n4) plain Ls=1n",
    "",
]
DECK = "\n".join(_DECK_LINES) + "\n"

_NO_TOP = "\n".join([
    "simulator lang=spectre",
    "global 0",
    "subckt foo (a b)",
    "parameters Ls=1n",
    "ends foo",
    "X1 (a b) foo Ls=1n",
    "",
])

_BARE = "\n".join([
    "simulator lang=spectre",
    "parameters w Ls=1n",
    "subckt c (a b)",
    "parameters Ls",
    "ends c",
    "X5 (a b) c Ls=Ls",
    "",
])

_INST_ONLY = "\n".join([
    "simulator lang=spectre",
    "subckt c (a b)",
    "parameters Ls",
    "ends c",
    "X1 (a b) c Ls=1n",
    "",
])

_ANALYSIS_DECK = "\n".join([
    "simulator lang=spectre",
    "global 0",
    "parameters dec=20 step=0.01",
    "ac1 ac start=1G stop=10G dec=20",
    "dc1 dc start=0 stop=1.2 step=0.01",
    "X1 (a b) cell dec=20",
    "",
])

_MULTILINE = "\n".join([
    "simulator lang=spectre",
    "global 0",
    "parameters w=1u",
    "subckt cell (a b c)",
    "parameters Ls=1n",
    "ends cell",
    "X1 (a b",
    "    c) cell w=4u",
    "",
])

def _selftest():
    results = []

    def check(name, condition, detail=""):
        ok = bool(condition)
        results.append((name, ok))
        if ok:
            print("[PASS] %s" % name)
        else:
            print("[FAIL] %s%s" % (name, (": " + detail) if detail else ""))

    check("format 4e-6 -> 4u", format_value(4e-6) == "4u",
          format_value(4e-6))
    check("format 0", format_value(0) == "0")
    check("format 1e-12 string -> 1p", format_value("1e-12") == "1p",
          format_value("1e-12"))
    check("format expression kept", format_value("2*pi") == "2*pi")
    check("format 1e6 -> 1meg", format_value(1e6) == "1meg",
          format_value(1e6))
    check("parse 2.5meg", abs(parse_value("2.5meg") - 2.5e6) < 1.0)
    try:
        parse_value("2*w")
        check("parse rejects identifier", False)
    except ValueError:
        check("parse rejects identifier", True)
    try:
        format_eng(float("inf"))
        check("format inf rejected", False)
    except ValueError:
        check("format inf rejected", True)
    try:
        format_eng(10 ** 400)
        check("format huge int rejected", False)
    except ValueError:
        check("format huge int rejected", True)

    for bad in (float("inf"), float("-inf"), float("nan"), 10 ** 400,
                "1e999", "nan"):
        bad_text, bad_report = patch_netlist("parameters w=1u\n", {"w": bad})
        check("non-finite value rejected",
              bad_report["ok"] is False
              and bad_report["missing"] == ["w"],
              "%r %s" % (bad, bad_report["missing"]))
        check("non-finite value leaves text",
              bad_text == "parameters w=1u\n", bad_text)
    fixed_bad_text, fixed_bad = patch_netlist(
        "parameters nf=2\n", {}, fixed={"nf": float("nan")})
    check("non-finite fixed rejected",
          fixed_bad["ok"] is False and fixed_bad["missing"] == ["nf"],
          str(fixed_bad["missing"]))
    check("non-finite fixed leaves text",
          fixed_bad_text == "parameters nf=2\n", fixed_bad_text)

    info = inspect_netlist(DECK)
    top = info["top_params"]
    check("inspect top w", top.get("w") == "1u", str(top))
    check("inspect top continuation nf", top.get("nf") == "2", str(top))
    names = [scope["name"] for scope in info["scopes"]]
    check("inspect scopes", names == ["top", "xfmr_tap", "plain"], str(names))
    xf = [s for s in info["scopes"] if s["name"] == "xfmr_tap"][0]
    check("inspect header param w", xf["params"].get("w") == "2u",
          str(xf["params"]))
    check("inspect plus continuation k", xf["params"].get("k") == "0.9",
          str(xf["params"]))
    check("inspect bare declaration None", xf["params"].get("Ctap") is None,
          str(xf["params"]))
    plain = [s for s in info["scopes"] if s["name"] == "plain"][0]
    check("inspect pure declaration Ls",
          plain["params"].get("Ls") is None
          and plain["params"].get("Rs") is None, str(plain["params"]))
    check("inspect scope range",
          DECK.splitlines()[xf["start"]].lstrip().startswith("subckt")
          and DECK.splitlines()[xf["end"]].lstrip().startswith("ends"),
          "%s..%s" % (xf["start"], xf["end"]))
    by_name = dict((inst["name"], inst) for inst in info["instances"])
    check("inspect instance X1 master",
          by_name["X1"]["master"] == "xfmr_tap")
    value, span = by_name["X1"]["params"]["w"]
    check("inspect instance span", DECK[span[0]:span[1]] == "4u",
          DECK[span[0]:span[1]])
    check("inspect self reference", by_name["X3"]["params"]["w"][0] == "w")
    check("inspect includes",
          info["includes"] == ["/pdk/models.scs"], str(info["includes"]))

    parsed = parse_netlist_parameters(DECK)
    check("parse top params", parsed == {"w": "1u", "l": "0.1u", "nf": "2"},
          str(parsed))
    check("parse top ignores subckt",
          "Ls" not in parsed and "Rs" not in parsed, str(parsed))

    rewritten, replaced = rewrite_netlist_parameters(
        DECK, "parameters w=2u l=0.2u nf=3")
    check("rewrite count", replaced == 1, str(replaced))
    check("rewrite line", "parameters w=2u l=0.2u nf=3" in rewritten)
    check("rewrite dropped old values",
          "parameters w=1u" not in rewritten and "nf=2" not in rewritten)
    check("rewrite parse back",
          parse_netlist_parameters(rewritten)
          == {"w": "2u", "l": "0.2u", "nf": "3"})
    check("rewrite keeps subckt",
          "parameters Ls=1n Rs=0.5" in rewritten)
    check("rewrite keeps include", 'include "/pdk/models.scs"' in rewritten)
    no_top_text, no_top_count = rewrite_netlist_parameters(_NO_TOP, "parameters x=1")
    check("rewrite without top is a no-op",
          no_top_count == 0 and no_top_text == _NO_TOP)

    new_text, report = patch_netlist(
        DECK, {"w": 4e-6, "l": 2e-7, "nf": 3, "Ls": 3e-9})
    check("patch ok", report["ok"] is True, str(report["missing"]))
    check("patch top w", "parameters w=4u l=200n \\" in new_text,
          _line_with(new_text, "parameters w="))
    check("patch top nf and append Ls", "    nf=3 Ls=3n" in new_text,
          _line_with(new_text, "nf="))
    check("patch subckt header w", "subckt xfmr_tap (a b c) w=4u" in new_text)
    check("patch subckt Ls", "parameters Ls=3n Rs=0.5 \\" in new_text)
    check("patch instance X1 Ls", "X1 (n1 n2) xfmr_tap w=4u Ls=3n" in new_text)
    check("patch instance I2 l", "I2 (a c) inductor l=200n" in new_text)
    check("patch instance X2 Ls", "X2 (n3 n4) plain Ls=3n" in new_text)
    check("patch self reference untouched", "w=w" in new_text)
    check("patch include untouched", 'include "/pdk/models.scs"' in new_text)
    check("patch line comment untouched", "// w=9u" in new_text)
    check("patch block comment untouched", "/* block comment w=9u */" in new_text)
    check("patch continuation kept",
          "parameters Ls=3n Rs=0.5 \\" in new_text and "    + k=0.9" in new_text)
    check("patch counts",
          report["counts"] == {"top": 4, "subckt": 2, "instance": 3},
          str(report["counts"]))
    shadow_pairs = sorted((item["name"], item["scope"])
                          for item in report["shadowed"])
    check("patch shadowed",
          shadow_pairs == [("Ls", "xfmr_tap"), ("w", "xfmr_tap")],
          str(shadow_pairs))
    check("patch applied locations",
          report["applied"]["w"]["locations"]
          == ["top", "sub:xfmr_tap", "inst:X1.w"],
          str(report["applied"]["w"]["locations"]))
    overrides = sorted((item["name"], item["instance"], item["old"], item["new"])
                       for item in report["instance_overrides"])
    check("patch instance overrides",
          overrides == [("Ls", "X1", "2n", "3n"),
                        ("Ls", "X2", "1n", "3n"),
                        ("l", "I2", "2u", "200n")], str(overrides))
    check("patch warns non literal",
          any("I1.l=Ls" in text for text in report["warnings"]),
          str(report["warnings"]))

    again, report2 = patch_netlist(
        new_text, {"w": 4e-6, "l": 2e-7, "nf": 3, "Ls": 3e-9})
    check("patch idempotent", again == new_text)
    check("patch idempotent counts",
          report2["counts"] == {"top": 0, "subckt": 0, "instance": 0},
          str(report2["counts"]))

    bare_text, bare_report = patch_netlist(_BARE, {"w": 4e-6, "Ls": 2e-9})
    check("bare top declaration", "parameters w=4u Ls=2n" in bare_text,
          _line_with(bare_text, "parameters "))
    check("bare subckt declaration kept", "parameters Ls\n" in bare_text)
    check("bare self reference kept", "X5 (a b) c Ls=Ls" in bare_text)
    check("bare counts",
          bare_report["counts"] == {"top": 2, "subckt": 0, "instance": 0},
          str(bare_report["counts"]))

    inserted, insert_report = patch_netlist(_NO_TOP, {"Ls": 2e-9})
    lines = inserted.splitlines()
    check("insert after global", len(lines) > 2
          and lines[2].strip() == "parameters Ls=2n",
          str(lines[:4]))
    check("insert patches subckt", len(lines) > 4
          and lines[4].strip() == "parameters Ls=2n", str(lines[:6]))
    check("insert counts",
          insert_report["counts"] == {"top": 1, "subckt": 1, "instance": 1},
          str(insert_report["counts"]))
    check("insert applied", insert_report["applied"]["Ls"]["locations"]
          == ["top", "sub:foo", "inst:X1.Ls"],
          str(insert_report["applied"]["Ls"]))

    inst_text, inst_report = patch_netlist(_INST_ONLY, {"Ls": 2e-9})
    inst_lines = inst_text.splitlines()
    check("instance only inserts top",
          inst_lines[1].strip() == "parameters Ls=2n", str(inst_lines[:3]))
    check("instance only patches literal",
          "X1 (a b) c Ls=2n" in inst_text)
    check("instance only locations",
          inst_report["applied"]["Ls"]["locations"]
          == ["top", "inst:X1.Ls"],
          str(inst_report["applied"]["Ls"]))

    analysis_info = inspect_netlist(_ANALYSIS_DECK)
    inst_names = [inst["name"] for inst in analysis_info["instances"]]
    check("analysis not an instance", inst_names == ["X1"], str(inst_names))
    analysis_text, analysis_report = patch_netlist(
        _ANALYSIS_DECK, {"dec": 10})
    check("analysis ac line untouched",
          "ac1 ac start=1G stop=10G dec=20" in analysis_text)
    check("analysis dc line untouched",
          "dc1 dc start=0 stop=1.2 step=0.01" in analysis_text)
    check("analysis top dec patched",
          "parameters dec=10 step=0.01" in analysis_text)
    check("analysis instance dec patched",
          "X1 (a b) cell dec=10" in analysis_text)
    check("analysis counts",
          analysis_report["counts"] == {"top": 1, "subckt": 0, "instance": 1},
          str(analysis_report["counts"]))

    named_analyses = "\n".join([
        "simulator lang=spectre",
        "parameters dec=20",
        "pnoise1 pnoise start=1G stop=10G dec=20",
        "hbnoise1 hbnoise start=1G stop=10G dec=20",
        "pac1 pac start=1G stop=10G dec=20",
        "sweephb1 sweephb param=pin start=-20 stop=10",
        "",
    ])
    named_info = inspect_netlist(named_analyses)
    named_instances = [inst["name"] for inst in named_info["instances"]]
    check("named analyses not instances", named_instances == [],
          str(named_instances))
    named_text, named_report = patch_netlist(named_analyses, {"dec": 10})
    check("named analysis options untouched",
          "pnoise1 pnoise start=1G stop=10G dec=20" in named_text
          and "hbnoise1 hbnoise start=1G stop=10G dec=20" in named_text
          and "pac1 pac start=1G stop=10G dec=20" in named_text,
          _line_with(named_text, "pnoise1"))
    check("named analysis top dec patched",
          "parameters dec=10" in named_text)
    check("named analysis counts",
          named_report["counts"] == {"top": 1, "subckt": 0, "instance": 0},
          str(named_report["counts"]))
    _, option_report = patch_netlist(named_analyses, {"start": 5.0})
    check("analysis option is not a design variable",
          option_report["ok"] is False
          and option_report["missing"] == ["start"],
          str(option_report["missing"]))

    multi_info = inspect_netlist(_MULTILINE)
    multi_inst = multi_info["instances"][0]
    check("multiline instance name", multi_inst["name"] == "X1",
          str(multi_inst))
    check("multiline instance master", multi_inst["master"] == "cell",
          str(multi_inst))
    check("multiline instance param", multi_inst["params"]["w"][0] == "4u",
          str(multi_inst["params"]))
    multi_text, multi_report = patch_netlist(
        _MULTILINE, {"w": 5e-6, "Ls": 3e-9})
    check("multiline top patched", "parameters w=5u" in multi_text)
    check("multiline subckt patched", "parameters Ls=3n" in multi_text)
    check("multiline instance patched", "    c) cell w=5u" in multi_text,
          _line_with(multi_text, "cell"))
    check("multiline counts",
          multi_report["counts"] == {"top": 2, "subckt": 1, "instance": 1},
          str(multi_report["counts"]))

    same_text, missing_report = patch_netlist(DECK, {"nope": 1.0})
    check("missing ok false", missing_report["ok"] is False)
    check("missing list", missing_report["missing"] == ["nope"],
          str(missing_report["missing"]))
    check("missing leaves text", same_text == DECK)

    scoped, scoped_report = patch_netlist(
        DECK, {"w": 6e-6},
        options={"scope_map": {"w": ["sub:xfmr_tap"]}})
    check("scope_map keeps top", "parameters w=1u l=0.1u \\" in scoped)
    check("scope_map patches subckt",
          "subckt xfmr_tap (a b c) w=6u" in scoped)
    check("scope_map keeps instance w=4u", "X1 (n1 n2) xfmr_tap w=4u" in scoped)
    check("scope_map counts",
          scoped_report["counts"] == {"top": 0, "subckt": 1, "instance": 0},
          str(scoped_report["counts"]))
    check("scope_map applied only subckt",
          scoped_report["applied"]["w"]["locations"] == ["sub:xfmr_tap"],
          str(scoped_report["applied"]["w"]))

    bad_scope, bad_report = patch_netlist(
        DECK, {"w": 4e-6}, options={"scope_map": {"w": ["bogus"]}})
    check("scope_map bogus fails", bad_report["ok"] is False)
    check("scope_map bogus warns",
          any("bogus" in warning for warning in bad_report["warnings"]),
          str(bad_report["warnings"]))

    no_inst, no_inst_report = patch_netlist(
        DECK, {"w": 5e-6}, options={"patch_instances": False})
    check("patch_instances false keeps literal",
          "X1 (n1 n2) xfmr_tap w=4u" in no_inst)
    check("patch_instances false warns",
          any("patch_instances=False" in warning
              for warning in no_inst_report["warnings"]),
          str(no_inst_report["warnings"]))
    no_sub, no_sub_report = patch_netlist(
        DECK, {"w": 4e-6}, options={"patch_subckts": False})
    check("patch_subckts false keeps default",
          "subckt xfmr_tap (a b c) w=2u" in no_sub)
    check("patch_subckts false warns",
          any("patch_subckts=False" in warning
              for warning in no_sub_report["warnings"]),
          str(no_sub_report["warnings"]))

    fixed_text, fixed_report = patch_netlist(DECK, {}, fixed={"nf": "2*pi"})
    check("fixed expression written", "nf=2*pi" in fixed_text,
          _line_with(fixed_text, "nf="))
    check("fixed applied top", fixed_report["applied"]["nf"]["locations"]
          == ["top"], str(fixed_report["applied"].get("nf")))
    unknown_fixed, unknown_report = patch_netlist(
        DECK, {}, fixed={"zzz": "1n"})
    check("fixed unknown appended", "parameters w=1u" in unknown_fixed
          and "zzz=1n" in unknown_fixed)
    check("fixed unknown ok", unknown_report["ok"] is True)
    check("fixed unknown warns",
          any("zzz" in warning for warning in unknown_report["warnings"]),
          str(unknown_report["warnings"]))
    check("fixed unknown appended once",
          unknown_fixed.count("zzz=1n") == 1,
          str(unknown_fixed.count("zzz=1n")))

    invalid_text, invalid_report = patch_netlist(DECK, {"w": "2 * pi"})
    check("space value fails", invalid_report["ok"] is False)
    check("space value listed", "w" in invalid_report["missing"],
          str(invalid_report["missing"]))
    check("space value leaves text", invalid_text == DECK)
    empty_fixed_value, empty_fixed_value_report = patch_netlist(
        DECK, {}, fixed={"nf": ""})
    check("empty fixed fails", empty_fixed_value_report["ok"] is False)
    check("empty fixed missing",
          empty_fixed_value_report["missing"] == ["nf"],
          str(empty_fixed_value_report["missing"]))
    check("empty fixed leaves text", empty_fixed_value == DECK)
    space_fixed, space_fixed_report = patch_netlist(
        DECK, {}, fixed={"nf": "2 * pi"})
    check("space fixed fails", space_fixed_report["ok"] is False)

    crlf = "\r\n".join(["simulator lang=spectre", "global 0",
                        "subckt c (a b)", "parameters Ls=1n",
                        "ends c", ""])
    crlf_text, crlf_report = patch_netlist(crlf, {"Ls": 2e-9})
    check("crlf insert", crlf_text.count("\r\n") == crlf_text.count("\n"),
          "CRLF count mismatch")
    check("crlf subckt patched", "parameters Ls=2n" in crlf_text)
    check("crlf top inserted",
          "global 0\r\nparameters Ls=2n\r\n" in crlf_text,
          crlf_text.replace("\r\n", "\\n"))

    empty_text, empty_report = patch_netlist("", {"w": 1e-6})
    check("empty deck missing", empty_report["ok"] is False
          and empty_text == "")
    empty_fixed, empty_fixed_report = patch_netlist(
        "", {}, fixed={"x": 1e-9})
    check("empty deck fixed writes", "parameters x=1n" in empty_fixed,
          empty_fixed)

    failed = [name for name, ok in results if not ok]
    print("selftest: %d/%d passed" % (len(results) - len(failed),
                                      len(results)))
    if failed:
        print("failures: %s" % ", ".join(failed))
        return 1
    return 0

def _line_with(text, needle):
    for line in text.splitlines():
        if needle in line:
            return line
    return "<not found: %s>" % needle

def _read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()

def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)

def _parse_assignments(items):
    out = {}
    for item in items:
        name, sep, value = str(item).partition("=")
        if not sep or not name:
            raise SystemExit("bad assignment: %s" % item)
        try:
            out[name] = parse_value(value)
        except ValueError:
            out[name] = value
    return out

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or "--selftest" in argv:
        return _selftest()
    if argv[0] == "--inspect" and len(argv) > 1:
        data = inspect_netlist(_read_text(argv[1]))
        print(json.dumps(data, indent=2, ensure_ascii=True))
        return 0
    if argv[0] == "--patch" and len(argv) > 1:
        path = argv[1]
        sets = []
        fixed = []
        out_path = None
        options = {}
        i = 2
        while i < len(argv):
            arg = argv[i]
            if arg == "--set" and i + 1 < len(argv):
                sets.append(argv[i + 1])
                i += 2
            elif arg == "--fixed" and i + 1 < len(argv):
                fixed.append(argv[i + 1])
                i += 2
            elif arg == "--out" and i + 1 < len(argv):
                out_path = argv[i + 1]
                i += 2
            elif arg == "--no-subckts":
                options["patch_subckts"] = False
                i += 1
            elif arg == "--no-instances":
                options["patch_instances"] = False
                i += 1
            else:
                i += 1
        new_text, report = patch_netlist(
            _read_text(path), _parse_assignments(sets),
            fixed=_parse_assignments(fixed), options=options)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        if out_path:
            _write_text(out_path, new_text)
        return 0 if report.get("ok") else 2
    print("usage: netlist_params.py --selftest")
    print("       netlist_params.py --inspect NETLIST")
    print("       netlist_params.py --patch NETLIST --set NAME=VALUE "
          "[--fixed NAME=VALUE] [--out PATH] [--no-subckts] "
          "[--no-instances]")
    return 2

if __name__ == "__main__":
    sys.exit(main())
