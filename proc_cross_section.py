# -*- coding: utf-8 -*-
"""proc_cross_section.py — EMX proc / layermap 横截面解析、叠层校验与 Tk 绘制（Python 3.6，纯 stdlib）。

模块顶层不 import tkinter；draw_cross_section 只使用调用方传入的 canvas 对象。

内置的 DEFAULT_FLOW 是一套通用示教叠层（金属 M1..M5 加顶层铝焊盘 AP），
仅供演示与自测，不对应任何真实工艺。实际工艺请用 tech_profile.json 的可选
"flow" 段覆盖（经 flow_from_profile 归一化），字段如下：
    metals   有序金属名列表
    vias     [[底层, 顶层, 通孔名], ...]
    gaps     [[底层, 顶层, 期望间距 μm], ...]
    gds      {金属名: GDS 层号}
    via_gds  {通孔名: GDS 层号}
    text     {文字层名: GDS 层号}
    via_res  {通孔名: Ω/via}
缺省（未提供 flow）时使用内置示教叠层。
"""

import ast
import io
import math
import os

__all__ = ["ProcParseError", "DEFAULT_FLOW", "flow_from_profile",
           "FLOW_METALS", "EXPECTED_VIAS", "EXPECTED_GAPS", "FLOW_GDS",
           "VIA_LAYER_GDS", "TEXT_LAYERS", "EXPECTED_VIA_RES", "METAL_COLORS",
           "parse_proc", "parse_layermap", "build_stack", "validate_flow_stack",
           "draw_cross_section", "build_cross_section_frame"]

FLOW_KEYS = ("metals", "vias", "gaps", "gds", "via_gds", "text", "via_res")

# 内置示教叠层：只用于演示，数值均为规整的虚构值。
DEFAULT_FLOW = {
    "metals": ("M1", "M2", "M3", "M4", "M5", "AP"),
    "vias": (("M1", "M2", "V1"), ("M2", "M3", "V2"), ("M3", "M4", "V3"),
             ("M4", "M5", "V4"), ("M5", "AP", "PA")),
    "gaps": (("M1", "M2", 0.35), ("M2", "M3", 0.35), ("M3", "M4", 0.35),
             ("M4", "M5", 0.35), ("M5", "AP", 0.75)),
    "gds": {"M1": 61, "M2": 62, "M3": 63, "M4": 64, "M5": 65, "AP": 5},
    "via_gds": {"V1": 70, "V2": 71, "V3": 72, "V4": 73, "PA": 80},
    "text": {"M1TXT": 61, "M2TXT": 62, "M3TXT": 63, "M4TXT": 64,
             "M5TXT": 65, "APTXT": 5},
    "via_res": {"V1": 1.1, "V2": 0.9, "V3": 0.6, "V4": 0.35, "PA": 0.1},
}

# 兼容常量：由 DEFAULT_FLOW 派生，供旧调用方/测试直接引用。
FLOW_METALS = tuple(DEFAULT_FLOW["metals"])
EXPECTED_VIAS = tuple(tuple(item) for item in DEFAULT_FLOW["vias"])
EXPECTED_GAPS = tuple(tuple(item) for item in DEFAULT_FLOW["gaps"])
FLOW_GDS = dict(DEFAULT_FLOW["gds"])
VIA_LAYER_GDS = tuple(DEFAULT_FLOW["via_gds"].items())
TEXT_LAYERS = tuple(DEFAULT_FLOW["text"].items())
EXPECTED_VIA_RES = dict(DEFAULT_FLOW["via_res"])
METAL_COLORS = {"M1": "#4C9AFF", "M2": "#36B37E", "M3": "#FFAB00",
                "M4": "#FF5630", "M5": "#6554C0", "AP": "#00B8D9"}
EXTRA_METAL_COLORS = ("#8E6CFF", "#00A3A3", "#D64545", "#7A9E1E",
                      "#B565A7", "#2C7BE5")
DIEL_FILLS = ("#F4F4F4", "#E8E8E8")


class ProcParseError(Exception):
    """proc / layermap 解析错误；消息含行号（若有）。"""

    def __init__(self, message, line=None, path=None):
        self.line, self.path = line, path
        if line is not None: message = "第 %d 行: %s" % (line, message)
        if path: message = "%s [%s]" % (message, path)
        Exception.__init__(self, message)


def _read_lines(path):
    try:
        with io.open(str(path), "r", encoding="utf-8-sig", errors="replace") as fh: return fh.read().splitlines()
    except (IOError, OSError) as exc: raise ProcParseError("无法读取文件: %s" % exc, path=str(path))


def _split_hash(line):
    pos = line.find("#")
    return (line[:pos], line[pos + 1:]) if pos >= 0 else (line, "")


def _comment_name(comment, fallback):
    text = (comment or "").strip()
    if not text:
        return fallback, ""
    bits = text.split(None, 1)
    return bits[0], (bits[1].strip() if len(bits) > 1 else "")


def _finite(value):
    """Finite float for a number-like value; None for inf/nan/overflow/text."""
    try: value = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return value if math.isfinite(value) else None


def _safe_eval(expr, env):
    """只允许数字/名字/四则运算的安全求值；失败或非有限数返回 None。"""
    try: tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError, TypeError): return None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.Load, ast.Num, ast.Name)
    const = getattr(ast, "Constant", None)
    for node in ast.walk(tree):
        if isinstance(node, allowed): continue
        if const is not None and isinstance(node, const) and isinstance(node.value, (int, float)): continue
        return None
    # Cap powers: 9**9**9 or nested powers would be computed in full first.
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Pow): continue
        raw = getattr(node.right, "n", getattr(node.right, "value", None))
        if not isinstance(raw, (int, float)) or abs(raw) > 64: return None
        if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Pow): return None
    try: value = eval(compile(tree, "<proc>", "eval"), {"__builtins__": {}}, env)
    except Exception: return None
    if isinstance(value, bool) or not isinstance(value, (int, float)): return None
    return _finite(value)


def _numeric_env(defines):
    env = {}
    for _round in range(3):
        changed = False
        for name, expr in (defines or {}).items():
            value = _safe_eval(expr, env)
            if value is not None and env.get(name) != value:
                env[name] = value; changed = True
        if not changed: break
    # Default only when the proc did not define it; an explicit
    # `define dtemp = 25` must drive temperature-dependent rho.
    env.setdefault("dtemp", 0.0)
    return env


def _as_number(text, defines, lineno, what, path=None):
    value = _finite(text)
    if value is None: value = _safe_eval(text, _numeric_env(defines))
    if value is None: raise ProcParseError("%s 不是有效数字: %r" % (what, text), line=lineno, path=path)
    return value


def _stack_entry(entry, kind, order):
    item = dict(entry); item["kind"], item["order"] = kind, order
    return item


def parse_proc(path):
    """解析 EMX proc 文件；失败抛 ProcParseError（含行号）。"""
    path = str(path)
    data = {"path": path,
            "assumes": {"units": "microns", "res": "ohms/sq", "via": "ohms/via"},
            "defines": {}, "conductors": [], "dielectrics": [], "vias": [],
            "capacitors": [], "stack": [], "ignored": []}
    recognized = 0
    for lineno, raw in enumerate(_read_lines(path), 1):
        line = raw.strip()
        if not line or line.startswith("#"): continue
        head, comment = _split_hash(line)
        tokens = head.split()
        if not tokens: continue
        key = tokens[0].lower()
        if key == "assume":
            value = " ".join(tokens[1:]).lower()
            if value in ("microns", "micron", "um", "µm"): data["assumes"]["units"] = "microns"
            elif value in ("ohms/sq", "ohm/sq", "ohms/square"): data["assumes"]["res"] = "ohms/sq"
            elif value in ("ohms/via", "ohm/via"): data["assumes"]["via"] = "ohms/via"
            else: data["ignored"].append((lineno, line))
            recognized += 1
        elif key == "define":
            body = head[len(tokens[0]):].strip()
            if "=" not in body: raise ProcParseError("define 行缺少 '='", line=lineno, path=path)
            name, expr = [part.strip() for part in body.split("=", 1)]
            if not name or not expr: raise ProcParseError("define 缺少名称或表达式", line=lineno, path=path)
            data["defines"][name] = expr; recognized += 1
        elif key == "layer":
            if len(tokens) < 3: raise ProcParseError("layer 行至少需要厚度和介电常数", line=lineno, path=path)
            is_inf = tokens[1].lower() in ("infinity", "inf")
            if is_inf: thickness = None
            else:
                thickness = _as_number(tokens[1], data["defines"], lineno, "layer 厚度", path)
                if thickness <= 0: raise ProcParseError("layer 厚度必须 > 0", line=lineno, path=path)
            eps = _as_number(tokens[2], data["defines"], lineno, "layer 介电常数", path)
            name, note = _comment_name(comment, "infinity" if is_inf else "layer_%d" % lineno)
            entry = {"name": name, "thickness": thickness, "eps": eps, "line": lineno,
                     "note": note, "extra": tokens[3:]}
            data["dielectrics"].append(entry)
            data["stack"].append(_stack_entry(entry, "dielectric", len(data["stack"])))
            recognized += 1
        elif key == "conductor":
            if len(tokens) < 4:
                raise ProcParseError("conductor 行应为: conductor <厚度> <表达式> <名称>", line=lineno, path=path)
            thickness = _as_number(tokens[1], data["defines"], lineno, "conductor 厚度", path)
            if thickness <= 0: raise ProcParseError("conductor 厚度必须 > 0", line=lineno, path=path)
            name, expr = tokens[-1], " ".join(tokens[2:-1])
            entry = {"name": name, "thickness": thickness, "expr": expr,
                     "rho": _safe_eval(expr, _numeric_env(data["defines"])),
                     "line": lineno, "note": comment.strip()}
            data["conductors"].append(entry)
            data["stack"].append(_stack_entry(entry, "conductor", len(data["stack"])))
            recognized += 1
        elif key == "via":
            if len(tokens) < 5:
                raise ProcParseError("via 行应为: via <bottom> <top> <R> <名称>", line=lineno, path=path)
            resistance = _as_number(tokens[3], data["defines"], lineno, "via 电阻", path)
            if resistance < 0: raise ProcParseError("via 电阻不能为负", line=lineno, path=path)
            entry = {"name": tokens[4], "bottom": tokens[1], "top": tokens[2],
                     "resistance": resistance, "line": lineno}
            if len(tokens) > 5: entry["extra"] = tokens[5:]
            data["vias"].append(entry); recognized += 1
        elif key == "capacitor":
            if len(tokens) < 4:
                raise ProcParseError("capacitor 行应为: capacitor <bottom> <top> <表达式>", line=lineno, path=path)
            data["capacitors"].append({"bottom": tokens[1], "top": tokens[2],
                                       "expr": " ".join(tokens[3:]), "line": lineno})
            recognized += 1
        else: data["ignored"].append((lineno, line))
    if recognized == 0:
        raise ProcParseError("未识别到任何 proc 指令（空文件或格式错误）", line=1, path=path)
    return data


def parse_layermap(path):
    """解析 layermap；坏行跳过并记录到 bad_lines。"""
    result = {"by_name": {}, "by_layer": {}, "pairs": [], "bad_lines": []}
    for lineno, raw in enumerate(_read_lines(str(path)), 1):
        text = raw.split("#", 1)[0].strip()
        if not text: continue
        tokens = text.split()
        try:
            if len(tokens) < 4: raise ValueError("short")
            layer, datatype = int(tokens[2]), int(tokens[3])
        except (TypeError, ValueError):
            result["bad_lines"].append((lineno, text)); continue
        name, purpose = tokens[0], tokens[1]
        result["pairs"].append((name, purpose, layer, datatype))
        result["by_name"][name] = (layer, datatype)
        result["by_layer"].setdefault((layer, datatype), []).append(name)
    return result


def _name_list(items):
    out = []
    for item in items or ():
        name = str(item).strip()
        if name and name not in out: out.append(name)
    return tuple(out)


def _int_map(raw):
    out = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            name = str(key).strip()
            if not name: continue
            number = _finite(value)
            if number is None: continue
            out[name] = int(number)
    return out


def _float_map(raw):
    out = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            name = str(key).strip()
            if not name: continue
            number = _finite(value)
            if number is None: continue
            out[name] = number
    return out


def _via_list(raw):
    out = []
    for item in raw or ():
        if not isinstance(item, (list, tuple)) or len(item) < 2: continue
        bottom, top = str(item[0]).strip(), str(item[1]).strip()
        name = str(item[2]).strip() if len(item) > 2 else ""
        if bottom and top: out.append((bottom, top, name))
    return tuple(out)


def _pair_list(raw):
    out = []
    for item in raw or ():
        if not isinstance(item, (list, tuple)) or len(item) < 3: continue
        low, high = str(item[0]).strip(), str(item[1]).strip()
        value = _finite(item[2])
        if value is None: continue
        if low and high: out.append((low, high, value))
    return tuple(out)


def _normalize_flow(raw):
    """把一份 flow 规格归一化为固定形状；缺项留空，不回落默认。"""
    raw = raw if isinstance(raw, dict) else {}
    metals = raw.get("metals")
    if metals is None: metals = raw.get("stack")
    return {
        "metals": _name_list(metals),
        "vias": _via_list(raw.get("vias")),
        "gaps": _pair_list(raw.get("gaps")),
        "gds": _int_map(raw.get("gds")),
        "via_gds": _int_map(raw.get("via_gds")),
        "text": _int_map(raw.get("text")),
        "via_res": _float_map(raw.get("via_res")),
    }


def _empty_flow(flow):
    return not any(flow.get(key) for key in FLOW_KEYS)


def flow_from_profile(profile=None):
    """把 profile（或 tech_profile.json 的整份内容）归一化为 flow 字典。

    支持三种形态：
      - profile 为 None / 空 / 非 dict：返回内置示教叠层；
      - profile 是 tech_profile 字典且含 "flow" 段：只用该段；
      - profile 直接就是 flow 段字典（或有序金属名列表）。
    未提供的字段留空（不回落示教值），便于只覆盖部分校验项。
    """
    raw = profile
    if isinstance(profile, dict) and "flow" in profile:
        raw = profile.get("flow")
    if raw is None:
        return _normalize_flow(DEFAULT_FLOW)
    if isinstance(raw, (list, tuple)):
        raw = {"metals": list(raw)}
    if not isinstance(raw, dict):
        return _normalize_flow(DEFAULT_FLOW)
    flow = _normalize_flow(raw)
    if _empty_flow(flow):
        return _normalize_flow(DEFAULT_FLOW)
    return flow


def build_stack(proc_data, metals=None):
    """按 proc 顺序累加 z（μm）；返回 entries / top / flow。

    metals 指定要收集的流程金属名（缺省用内置示教叠层的金属链）。
    """
    wanted = tuple(metals) if metals else FLOW_METALS
    entries, flow, z, top, top_inf = [], {}, 0.0, None, False
    for raw in (proc_data or {}).get("stack", []):
        item = dict(raw); item["z0"] = z
        thickness = item.get("thickness")
        if thickness is None: item["z1"], top_inf = None, True
        else:
            try: z = z + float(thickness)
            except (TypeError, ValueError):
                raise ProcParseError("叠层厚度无效: %r" % (thickness,), line=item.get("line"))
            item["z1"], top = z, z
        entries.append(item)
        if item.get("kind") == "conductor" and item.get("name") in wanted:
            flow[item["name"]] = item
    return {"entries": entries, "top": top, "flow": flow, "top_is_infinite": top_inf}


def _gap_items(expected_gaps, flow_gaps):
    if expected_gaps:
        if isinstance(expected_gaps, dict):
            out = []
            for key, value in expected_gaps.items():
                if isinstance(key, (tuple, list)) and len(key) == 2:
                    number = _finite(value)
                    if number is not None: out.append((str(key[0]), str(key[1]), number))
            return out
        out = []
        for item in expected_gaps:
            item = tuple(item)
            if len(item) >= 3: out.append(item)
        return out
    return [tuple(item) for item in (flow_gaps or ())]


def validate_flow_stack(proc_data, layermap_data=None, expected_gaps=None, tol=0.02, flow=None):
    """返回 (errors, warnings)；硬错仅限缺金属/顺序/缺通孔/GDS 对不上，其余为 warning。

    flow 为 None 或空时使用内置示教叠层；实际工艺请传 flow_from_profile(profile)。
    默认按 flow 的 gaps 校验金属间距，调用方可用 expected_gaps 覆盖（列表或
    {(低, 高): 期望μm} 字典）。可解析的电阻率缺失、文字层缺失等只记为 warning。
    """
    spec = flow_from_profile(flow)
    metals = tuple(spec["metals"])
    if not isinstance(proc_data, dict) or not proc_data.get("stack"):
        return ["proc 中没有 layer/conductor 叠层定义"], []
    try: stack_info = build_stack(proc_data, metals)
    except ProcParseError as exc: return ["无法构建叠层: %s" % exc], []
    errors, warnings = [], []
    flow_map = stack_info["flow"]
    cond_map = dict((c.get("name"), c) for c in (proc_data.get("conductors") or []))
    missing = [n for n in metals if n not in flow_map]
    if missing:
        errors.append("缺少流程金属: %s（proc 导体层: %s）" % (", ".join(missing), ", ".join(sorted(n for n in cond_map if n)) or "无"))
    present = [n for n in metals if n in flow_map]
    actual = sorted(present, key=lambda n: flow_map[n].get("order", 0))
    if actual != present:
        errors.append("流程金属顺序错误: 实际 %s，应为 %s" % (" -> ".join(actual), " -> ".join(metals)))
    for pair in _gap_items(expected_gaps, spec["gaps"]):
        want = _finite(pair[2])
        if want is None: continue
        low, high = pair[0], pair[1]
        ent_low, ent_high = flow_map.get(low), flow_map.get(high)
        if not ent_low or not ent_high or ent_low.get("z1") is None or ent_high.get("z0") is None: continue
        gap = ent_high["z0"] - ent_low["z1"]
        if abs(gap - want) > tol:
            warnings.append("间距偏差 %s→%s: 实测 %.3f μm，期望 %.3f μm，偏差 %+.3f μm（第 %d/%d 行）" % (low, high, gap, want, gap - want, ent_low.get("line", 0), ent_high.get("line", 0)))
    vias = list(proc_data.get("vias") or [])
    for bottom, top, via_name in spec["vias"]:
        candidates = [v for v in vias if v.get("bottom") == bottom and v.get("top") == top]
        if not candidates:
            errors.append("缺少通孔 %s（%s→%s）" % (via_name, bottom, top)); continue
        hit = [v for v in candidates if v.get("name") == via_name]
        used = hit[0] if hit else candidates[0]
        if via_name and not hit:
            warnings.append("通孔名称不符: %s→%s 应为 %s，实际为 %s（第 %d 行）" % (bottom, top, via_name, used.get("name"), used.get("line", 0)))
        want_res = spec["via_res"].get(via_name)
        if want_res is not None and (used.get("resistance") is None or abs(used["resistance"] - want_res) > max(0.02, 0.1 * abs(want_res))):
            warnings.append("通孔电阻偏差 %s: 实测 %s Ω/via，期望 %g Ω/via（第 %d 行）" % (via_name, used.get("resistance"), want_res, used.get("line", 0)))
    for name in metals:
        ent = flow_map.get(name)
        if ent is not None and ent.get("rho") is None:
            warnings.append("无法解析 %s 电阻率表达式: %r（第 %d 行）" % (name, ent.get("expr"), ent.get("line", 0)))
    if not layermap_data: warnings.append("未提供 layermap，跳过 GDS 层号核对")
    else:
        by_name = layermap_data.get("by_name") or {}
        for name in metals:
            want = spec["gds"].get(name)
            if want is None: continue
            got = by_name.get(name)
            if got is None: errors.append("layermap 中缺少流程金属 %s（期望 %d/0）" % (name, want))
            elif tuple(got) != (want, 0): errors.append("layermap 层号不符: %s 为 %d/%d，期望 %d/0" % (name, got[0], got[1], want))
        for cut, want in spec["via_gds"].items():
            got = by_name.get(cut)
            if got is None: errors.append("layermap 中缺少通孔层 %s（期望 %d/0）" % (cut, want))
            elif tuple(got) != (want, 0): errors.append("layermap 通孔层层号不符: %s 为 %d/%d，期望 %d/0" % (cut, got[0], got[1], want))
        for text_name, want in spec["text"].items():
            got = by_name.get(text_name)
            if got is None: warnings.append("layermap 缺少文字层 %s（%d/5）" % (text_name, want))
            elif tuple(got) != (want, 5): warnings.append("layermap 文字层层号不符: %s 为 %d/%d，期望 %d/5" % (text_name, got[0], got[1], want))
    return errors, warnings


def _is_major_dielectric(entry):
    label = ("%s %s" % (entry.get("name", ""), entry.get("note", ""))).upper()
    return any(k in label for k in ("STI", "ILD", "IMD", "ESL", "PASS"))


def _metal_color_map(metals):
    """按 flow 金属顺序给出颜色；示教金属有固定色，其余用备用色循环。"""
    colors = {}
    for index, name in enumerate(metals or ()):
        colors[name] = METAL_COLORS.get(name) or EXTRA_METAL_COLORS[index % len(EXTRA_METAL_COLORS)]
    return colors


def _text_color(color):
    try: red, green, blue = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    except (ValueError, TypeError): return "#102A43"
    return "#FFFFFF" if (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0 < 0.62 else "#102A43"


def _draw_impl(canvas, proc_data, layermap_data, width, height, warnings, spec):
    canvas.delete("all")
    try: canvas.configure(background="white")
    except Exception: pass
    try: W = int(width) if width and int(width) > 10 else int(canvas.winfo_width())
    except Exception: W = 0
    if W <= 1: W = 680
    try: H = int(height) if height and int(height) > 10 else int(canvas.winfo_height())
    except Exception: H = 0
    if H <= 1: H = 420
    counter = [0]
    metals = tuple(spec.get("metals") or ())
    colors = _metal_color_map(metals)
    gds_map = spec.get("gds") or {}

    def rect(x0, y0, x1, y1, **kw):
        canvas.create_rectangle(x0, y0, x1, y1, **kw); counter[0] += 1

    def line(coords, **kw):
        canvas.create_line(*coords, **kw); counter[0] += 1

    def text(x, y, value, **kw):
        canvas.create_text(x, y, text=value, **kw); counter[0] += 1

    stack = build_stack(proc_data)["entries"]
    substrate, mids, infinity = None, [], None
    for entry in stack:
        name = (entry.get("name") or "").lower()
        if entry.get("thickness") is None: infinity = entry
        elif substrate is None and (name.startswith("sub") or (entry.get("thickness") or 0) >= 100):
            substrate = entry
        else: mids.append(entry)
    plot_left, plot_top, plot_bottom = 8, 24, H - 8
    sub_h = 34 if substrate is not None else 0
    inf_h = 14 if infinity is not None else 0
    legend_w = 0
    if plot_bottom - plot_top >= 260:
        if W >= 1100: legend_w = 330
        elif W >= 880: legend_w = 280
        elif W >= 760: legend_w = 220
    plot_right = W - 8 - (legend_w + 10 if legend_w else 0)
    if plot_right - plot_left < 260:
        legend_w, plot_right = 0, W - 8
    plot_right = max(plot_left + 80, plot_right)
    plot_w = plot_right - plot_left
    avail = max(30, plot_bottom - plot_top - sub_h - inf_h)

    def min_height(entry):
        if entry.get("kind") == "conductor":
            return 12.0 if entry.get("name") in colors else 6.0
        return 1.3

    base = [min_height(e) for e in mids]
    thick = [max(0.0, (e.get("z1") or e.get("z0")) - e.get("z0")) for e in mids]
    base_sum = sum(base) or 1.0
    if avail >= base_sum:
        total_t = sum(thick)
        scale = (avail - base_sum) / total_t if total_t > 0 else 0.0
        heights = [base[i] + thick[i] * scale for i in range(len(mids))]
    else: heights = [value * avail / base_sum for value in base]
    cursor = plot_bottom - sub_h
    for entry, value in zip(mids, heights):
        entry["_py1"], entry["_py0"] = cursor, cursor - value; cursor -= value
    if substrate is not None: substrate["_py1"], substrate["_py0"] = plot_bottom, plot_bottom - sub_h
    if infinity is not None: infinity["_py1"], infinity["_py0"] = cursor, cursor - inf_h
    draw_entries = [e for e in ([substrate] if substrate is not None else []) + mids
                    if e.get("z1") is not None]

    def z_to_y(z):
        for entry in draw_entries:
            z0, z1 = entry["z0"], entry["z1"]
            if z0 - 1e-9 <= z <= z1 + 1e-9:
                span = z1 - z0
                return entry["_py0"] if span <= 0 else entry["_py1"] + (z - z0) / span * (entry["_py0"] - entry["_py1"])
        if draw_entries: return draw_entries[0]["_py1"] if z <= draw_entries[0]["z0"] else draw_entries[-1]["_py0"]
        return plot_bottom

    title = "工艺叠层横截面（示意，z 非等比例）" if W >= 380 else "叠层横截面"
    text(plot_left, 4, title, anchor="nw", fill="#243B53")
    if substrate is not None:
        y0, y1 = substrate["_py0"], substrate["_py1"]
        rect(plot_left, y0, plot_right, y1, fill="#D8CFC4", outline="#8D7B68")
        text((plot_left + plot_right) / 2.0, y0 + 12,
             "%s %.2f μm（压缩显示，z 非等比例）" % (substrate.get("name", "sub_si"),
                (substrate.get("z1") or 0) - (substrate.get("z0") or 0)), fill="#3B2E2A")
        coords, xpos, toggle = [], plot_left + 2, 0
        while xpos <= plot_right - 2:
            coords.extend([xpos, y1 - 5 + (3 if toggle else -3)]); xpos += 10; toggle ^= 1
        if len(coords) >= 4: line(coords, fill="#8D7B68", width=1)
    diel_index = 0
    for entry in mids:
        y0, y1, name = entry["_py0"], entry["_py1"], entry.get("name", "")
        if entry.get("kind") == "dielectric":
            rect(plot_left, y0, plot_right, y1, fill=DIEL_FILLS[diel_index % 2],
                 outline="#D9D9D9"); diel_index += 1
        elif name in colors:
            rect(plot_left, y0, plot_right, y1, fill=colors[name], outline="#243B53")
        else:
            rect(plot_left, y0, plot_right, y1, fill="#C7CDD4", outline="#8A94A6")
    if infinity is not None:
        y0, y1 = infinity["_py0"], infinity["_py1"]
        rect(plot_left, y0, plot_right, y1, fill="#FFFFFF", outline="#A0AEC0")
        xpos = plot_left + 6
        while xpos < plot_right:
            line([xpos, y1 - 1, min(xpos + inf_h - 2, plot_right - 1), y0 + 1],
                 fill="#A0AEC0"); xpos += 9
        rect(plot_left + 4, y0 + 1, min(plot_right - 2, plot_left + 190), y1 - 1, fill="white", outline="")
        text(plot_left + 8, (y0 + y1) / 2.0, "∞ 空气（layer infinity）",
             anchor="w", fill="#4A5568")
    conduct = {}
    for entry in draw_entries:
        if entry.get("kind") == "conductor" and entry.get("name"):
            conduct.setdefault(entry["name"], entry)
    via_defs = {}
    for via in proc_data.get("vias") or []: via_defs.setdefault(via.get("name"), via)
    shown = []
    for bottom_name, top_name, via_name in (spec.get("vias") or ()):
        via = via_defs.get(via_name)
        if via is None:
            warnings.append("绘图: 未找到通孔 %s" % via_name); continue
        bottom = conduct.get(via.get("bottom") or bottom_name)
        top = conduct.get(via.get("top") or top_name)
        if bottom is None or top is None:
            warnings.append("绘图: 通孔 %s 的端点层缺失" % via_name); continue
        shown.append((via_name, bottom, top))
    n_via = len(shown)

    def via_y(bottom, top):
        y_top, y_bot = z_to_y(top["z0"]), z_to_y(bottom["z1"]); return y_top, max(y_bot, y_top + 3)

    via_left, via_right = plot_left + 10, plot_left + plot_w * 0.78

    def via_cx(index): return via_left + (via_right - via_left) * (index + 1) / float(n_via + 1)

    for index, (via_name, bottom, top) in enumerate(shown):
        y_top, y_bot = via_y(bottom, top)
        rect(via_cx(index) - 5, y_top, via_cx(index) + 5, y_bot,
             fill="#42526E", outline="#243B53")
    for index, (via_name, bottom, top) in enumerate(shown):
        y_top, y_bot = via_y(bottom, top)
        if y_bot - y_top < 16 or plot_w < 420: continue
        cx = via_cx(index)
        ly, lx, anchor = (y_top + y_bot) / 2.0, cx + 8, "w"
        if lx > via_right - 22: lx, anchor = cx - 8, "e"
        box = len(via_name) * 7 + 4
        rect(lx - 2 if anchor == "w" else lx - box, ly - 6,
             lx + box if anchor == "w" else lx + 2, ly + 6, fill="white", outline="")
        text(lx, ly, via_name, anchor=anchor, fill="#243B53")
    by_name = (layermap_data or {}).get("by_name") or {}
    for entry in mids:
        name = entry.get("name", "")
        if entry.get("kind") != "conductor" or name not in colors: continue
        band = entry["_py1"] - entry["_py0"]
        if band < 16: continue
        if band >= 26:
            gds = gds_map.get(name)
            if name in by_name: gds = by_name[name][0]
            gds_text = "%d/0" % gds if gds is not None else "?/0"
            rho = entry.get("rho")
            label = "%s %s  t=%g μm  %s" % (
                name, gds_text, entry["thickness"],
                ("ρ=%.4g Ω/□" % rho) if rho is not None else "ρ=?")
        else:
            label = name
        text(plot_left + 8, (entry["_py0"] + entry["_py1"]) / 2.0, label,
             anchor="w", fill=_text_color(colors[name]))
    last_y = None
    for entry in mids:
        if entry.get("kind") != "dielectric" or plot_w < 330 or not _is_major_dielectric(entry): continue
        yc = (entry["_py0"] + entry["_py1"]) / 2.0
        if last_y is not None and abs(yc - last_y) < 20: continue
        last_y = yc
        text(plot_right - 6, yc, entry.get("name", ""), anchor="e", fill="#6B7280")
    if legend_w:
        lx0, lx1 = W - 8 - legend_w, W - 8
        rect(lx0, plot_top, lx1, plot_bottom, fill="#FAFBFC", outline="#CBD5E0")
        text(lx0 + 10, plot_top + 4, "图例", anchor="nw", fill="#243B53")
        row, full = plot_top + 34, legend_w >= 260
        for name in metals:
            ent = conduct.get(name)
            if ent is None: continue
            rect(lx0 + 10, row - 5, lx0 + 22, row + 5, fill=colors[name], outline="#243B53")
            gds = by_name.get(name, (gds_map.get(name), 0))[0]
            gds_text = "%d/0" % gds if gds is not None else "?/0"
            label = "%s %s t=%g μm" % (name, gds_text, ent["thickness"])
            rho = ent.get("rho")
            if full and rho is not None: label += " ρ=%.4g Ω/□" % rho
            text(lx0 + 28, row, label, anchor="w", fill="#243B53")
            row += 17
        row += 3
        text(lx0 + 10, row, "通孔", anchor="w", fill="#243B53")
        row += 18
        via_names = [item[2] for item in (spec.get("vias") or ()) if item[2]]
        for index, via_name in enumerate(via_names):
            text(lx0 + 10 + (index % 4) * 62, row + (index // 4) * 20,
                 via_name, anchor="w", fill="#42526E")
    return counter[0]


def draw_cross_section(canvas, proc_data, layermap_data=None, width=None, height=None, flow=None):
    """在调用方提供的 Canvas 上绘制横截面；不创建 Tk root。

    flow 缺省时使用内置示教叠层（见 flow_from_profile）。
    """
    if canvas is None:
        return {"ok": False, "items": 0, "warnings": [], "errors": ["canvas 为空"]}
    spec = flow_from_profile(flow)
    errors, warnings = validate_flow_stack(proc_data, layermap_data, flow=spec)
    if not isinstance(proc_data, dict) or not proc_data.get("stack"):
        try: canvas.delete("all"); canvas.create_text(16, 16, text="proc 中没有可绘制的叠层", anchor="nw")
        except Exception: pass
        return {"ok": False, "items": 0, "warnings": warnings, "errors": errors}
    try: items = _draw_impl(canvas, proc_data, layermap_data, width, height, warnings, spec)
    except Exception as exc:
        return {"ok": False, "items": 0, "warnings": warnings,
                "errors": errors + ["绘图异常: %s" % exc]}
    return {"ok": True, "items": items, "warnings": warnings, "errors": errors}


def build_cross_section_frame(parent, proc_var, layermap_var=None, height=420):
    """返回 ttk.Frame（Canvas + 状态行）；250ms debounce 重绘。"""
    import tkinter as tk
    from tkinter import ttk
    frame = ttk.Frame(parent)
    frame.proc_var, frame.layermap_var = proc_var, layermap_var
    frame.canvas = tk.Canvas(frame, height=height, background="white", highlightthickness=0)
    frame.canvas.pack(side="top", fill="both", expand=True)
    frame.status_label = tk.Label(frame, text="", anchor="w", justify="left", fg="#6B7280")
    frame.status_label.pack(side="bottom", fill="x", padx=4, pady=(2, 2))
    frame._after_id, frame._last_result = None, None
    frame._last_errors, frame._last_warnings = [], []
    colors = {"error": "#C0392B", "warn": "#B7791F", "ok": "#2F855A", "info": "#6B7280"}

    def set_status(message, level="info"):
        try: frame.status_label.configure(text=message, fg=colors.get(level, "#333333"))
        except Exception: pass

    def placeholder(message, color="#6B7280"):
        try:
            frame.canvas.delete("all"); frame.canvas.create_text(14, 14, text=message, anchor="nw", fill=color)
        except Exception: pass

    def redraw(*_args):
        frame._after_id = None
        proc_path = map_path = ""
        try: proc_path = proc_var.get().strip() if proc_var is not None else ""
        except Exception: proc_path = ""
        try: map_path = layermap_var.get().strip() if layermap_var is not None else ""
        except Exception: map_path = ""
        if not proc_path:
            set_status("未设置 proc 路径", "error"); placeholder("请在上方填写 proc 文件路径"); return
        if not os.path.isfile(proc_path):
            set_status("proc 文件不存在: %s" % proc_path, "error"); placeholder("proc 文件不存在", "#C0392B"); return
        try: proc_data = parse_proc(proc_path)
        except ProcParseError as exc:
            set_status("proc 解析失败: %s" % exc, "error"); placeholder("proc 解析失败", "#C0392B")
            frame._last_result, frame._last_errors, frame._last_warnings = None, [str(exc)], []
            return
        layermap_data, map_note = None, ""
        if map_path:
            if not os.path.isfile(map_path): map_note = "layermap 文件不存在: %s" % map_path
            else:
                try: layermap_data = parse_layermap(map_path)
                except ProcParseError as exc: map_note = "layermap 解析失败: %s" % exc
        result = draw_cross_section(frame.canvas, proc_data, layermap_data)
        errors, warnings = list(result.get("errors") or []), list(result.get("warnings") or [])
        if map_note: warnings.insert(0, map_note)
        frame._last_result, frame._last_errors, frame._last_warnings = result, errors, warnings
        try: frame.status_label.configure(wraplength=max(200, frame.canvas.winfo_width() - 12))
        except Exception: pass
        if errors:
            message = "错误 %d / 警告 %d: %s" % (len(errors), len(warnings), "；".join(errors[:2]))
            if warnings: message += " | 警告: " + "；".join(warnings[:1])
            set_status(message, "error")
        elif warnings:
            set_status("检查通过（警告 %d）: %s" % (len(warnings), "；".join(warnings[:2])), "warn")
        else: set_status("工艺检查通过：%d 金属 / %d 通孔" % (len(FLOW_METALS), len(EXPECTED_VIAS)), "ok")

    def schedule(*_args):
        try:
            if frame._after_id is not None: frame.after_cancel(frame._after_id)
        except Exception: frame._after_id = None
        try: frame._after_id = frame.after(250, redraw)
        except Exception: frame._after_id = None

    frame.redraw, frame.schedule_redraw = redraw, schedule
    for var in (proc_var, layermap_var):
        if var is None: continue
        try: var.trace_add("write", schedule)
        except AttributeError:
            try: var.trace("w", schedule)
            except Exception: pass
        except Exception: pass
    try:
        frame.canvas.bind("<Configure>", schedule); frame.after(20, redraw)
    except Exception: pass
    return frame
