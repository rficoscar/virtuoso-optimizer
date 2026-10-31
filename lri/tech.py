# -*- coding: utf-8 -*-
"""lri.tech — 工艺档：加载、派生、角色层与 DRC 查询、SKILL 注入。"""
from __future__ import print_function

import json
import os
import re

from .common import (
    open, _HERE, DEF_WS, LAYERMAP_NAMES, PROC_NAMES, TECH_PROFILE_NAME, _xs_parse_proc,
    _xs_parse_layermap, _xs_validate_flow_stack, DEFAULT_TECH, LibrfError, _env_path,
    _probe_data_file)

_TECH_CACHE = {}

def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load_tech_profile(work=None, path=None, use_cache=True):
    key = os.path.abspath(os.path.expanduser(path)) if path else (
        "auto|" + os.path.abspath(os.path.expanduser(work or os.getcwd())))
    if use_cache and key in _TECH_CACHE:
        return _TECH_CACHE[key]
    candidates = []
    if path:
        candidates.append(os.path.expanduser(str(path)))
    else:
        env = os.environ.get("LIBRF_TECH_PROFILE")
        if env:
            candidates.append(os.path.expanduser(env))
        if work:
            candidates.append(os.path.join(os.path.expanduser(str(work)), TECH_PROFILE_NAME))
        candidates.append(os.path.join(os.getcwd(), TECH_PROFILE_NAME))
        candidates.append(os.path.join(_HERE, TECH_PROFILE_NAME))
    raw = {}
    src = "default"
    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                with open(cand) as f:
                    raw = json.load(f) or {}
                src = cand
                break
            except (IOError, ValueError) as e:
                raise LibrfError("工艺配置 %s 解析失败: %s" % (cand, e))
    raw = dict((k, v) for k, v in raw.items() if not str(k).startswith("_doc"))
    prof = _deep_merge(DEFAULT_TECH, raw)
    prof["_src"] = src
    if use_cache:
        _TECH_CACHE[key] = prof
    return prof

TECH_REQUIRED_FILES = (
    ("cds_lib", "cds.lib（工作区库映射）"),
    ("layermap", "layermap（层名->GDS 层号）"),
    ("proc", "EMX proc（工艺叠层）"),
    ("techlib_dir", "工艺库目录（层定义）"),
)

TECH_METAL_ROLES = ("spiral", "underpass", "route", "ground")

TECH_VIA_ROLES = ("via_sp", "via_un")

TECH_MARKER_ROLES = ("marker",)

TECH_LAYER_ROLES = TECH_METAL_ROLES + TECH_VIA_ROLES + TECH_MARKER_ROLES

TECH_NEED_KEYS = ("layermap", "proc", "techlib_dir", "cds_lib")

def _tech_deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _tech_deep_merge(out[k], v)
        elif v not in (None, "", [], {}):
            out[k] = v
    return out

def build_tech(techlib=None, techlib_dir=None, layermap=None, proc=None,
               cds_lib=None, layers=None, text=None, drc=None, base=None):
    base = base if base is not None else load_tech_profile()
    prof = _tech_deep_merge(base, {})
    if techlib:
        prof["techlib"] = str(techlib).strip()
    if techlib_dir:
        prof["techlib_dir"] = os.path.abspath(
            os.path.expanduser(str(techlib_dir).strip()))
    if layermap:
        prof["layermap"] = os.path.abspath(
            os.path.expanduser(str(layermap).strip()))
    if proc:
        prof["proc"] = os.path.abspath(
            os.path.expanduser(str(proc).strip()))
    if cds_lib:
        prof["cds_lib"] = os.path.abspath(
            os.path.expanduser(str(cds_lib).strip()))
    if layers:
        prof["layers"] = _tech_deep_merge(prof.get("layers") or {}, layers)
    if text:
        prof["text"] = _tech_deep_merge(prof.get("text") or {}, text)
    if drc:
        prof["drc"] = _tech_deep_merge(prof.get("drc") or {}, drc)
    prof["_src"] = "(GUI/CLI 输入; base=%s)" % (base.get("_src") or "default")
    return prof

def _role_kind(role):
    if role in TECH_METAL_ROLES:
        return "metal"
    if role in TECH_VIA_ROLES:
        return "via"
    return "marker"

def _tech_role_layers(prof):
    layers = (prof or {}).get("layers") or {}
    out, seen = [], set()
    for role in TECH_LAYER_ROLES:
        name = layers.get(role)
        if name is None:
            continue
        name = str(name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((role, name))
    return out

def _tech_file_ok(path, want_dir=False):
    if not path:
        return False
    p = os.path.expanduser(str(path))
    try:
        return os.path.isdir(p) if want_dir else os.path.isfile(p)
    except (TypeError, ValueError):
        return False

def _resolve_tech_files(prof, work=None):
    prof = dict(prof or {})
    notes = []
    ws = os.path.expanduser(_env_path("LIBRF_WS", DEF_WS))
    anchor = work or os.getcwd()
    search_dirs = [os.environ.get("LIBRF_TECHLIB_DIR", ""),
                   prof.get("techlib_dir") or "", ws, anchor, _HERE,
                   os.path.join(_HERE, "tech"), os.path.join(ws, "tech")]
    search_dirs = [d for d in search_dirs
                   if d and os.path.isdir(os.path.expanduser(str(d)))]
    def _fill(key, env_vars, patterns, want_dir=False):
        given = str(prof.get(key) or "").strip()
        if given and _tech_file_ok(given, want_dir):
            prof[key] = os.path.abspath(os.path.expanduser(given))
            return
        found, _src = _probe_data_file(key, env_vars, search_dirs,
                                       patterns=tuple(patterns))
        if found:
            if given:
                notes.append("配置的 %s 不可用（%s），已改用探查到的 %s"
                             % (key, given, found))
            prof[key] = os.path.abspath(os.path.expanduser(str(found)))
        elif given:
            prof[key] = os.path.abspath(os.path.expanduser(given))
    _fill("layermap", ("LIBRF_LAYERMAP",), LAYERMAP_NAMES)
    _fill("proc", ("LIBRF_PROC",), PROC_NAMES)
    _fill("cds_lib", ("LIBRF_CDSSETUP",), ("cds.lib",))
    given = str(prof.get("techlib_dir") or "").strip()
    if given and _tech_file_ok(given, True):
        prof["techlib_dir"] = os.path.abspath(os.path.expanduser(given))
    else:
        techlib = str(prof.get("techlib") or "").strip()
        found = None
        if techlib:
            for d in search_dirs:
                if not d:
                    continue
                cand = os.path.join(os.path.expanduser(str(d)), techlib)
                if os.path.isdir(cand):
                    found = cand
                    break
        if found:
            if given:
                notes.append("配置的 techlib_dir 不可用（%s），已改用探查到的 %s"
                             % (given, found))
            prof["techlib_dir"] = os.path.abspath(os.path.expanduser(found))
        elif given:
            prof["techlib_dir"] = os.path.abspath(os.path.expanduser(given))
    return prof, notes

def _merge_tools_into_tech(tech, tools):
    out = dict(tech or {})
    for key in ("layermap", "proc", "techlib_dir"):
        want_dir = (key == "techlib_dir")
        if _tech_file_ok(out.get(key), want_dir):
            continue
        hit = (tools or {}).get(key) or {}
        path = hit.get("path") if hit.get("found") else None
        if path and _tech_file_ok(path, want_dir):
            out[key] = os.path.abspath(os.path.expanduser(str(path)))
    return out

def _parse_layermap_generic(path):
    by_name = {}
    try:
        with open(path) as fh:
            for raw in fh:
                toks = raw.split("#", 1)[0].split()
                if len(toks) < 4:
                    continue
                try:
                    by_name.setdefault(toks[0], (int(toks[2]), int(toks[3])))
                except ValueError:
                    pass
    except OSError as e:
        raise LibrfError("layermap 读取失败：%s\n文件：%s" % (e, path))
    return {"by_name": by_name}

def _parse_proc_generic(path):
    data = {"conductors": [], "vias": [], "stack": []}
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        raise LibrfError("proc 读取失败：%s\n文件：%s" % (e, path))
    for ln, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"conductor\s+(\S+)\s+(.+?)\s+(\S+)$", line, re.I)
        if m:
            try:
                thick = float(m.group(1))
            except ValueError:
                thick = None
            name = m.group(3)
            data["conductors"].append(
                {"name": name, "thickness": thick, "line": ln})
            data["stack"].append({"kind": "conductor", "name": name,
                                  "thickness": thick, "line": ln})
            continue
        m = re.match(r"via\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", line, re.I)
        if m:
            try:
                res = float(m.group(3))
            except ValueError:
                res = None
            data["vias"].append({"bottom": m.group(1), "top": m.group(2),
                                 "resistance": res, "name": m.group(4),
                                 "line": ln})
    if not data["conductors"]:
        raise LibrfError("proc 中未识别到任何 conductor 行：%s" % path)
    return data

def _tech_flow(prof):
    raw = (prof or {}).get("flow")
    if not raw:
        return None
    strict = bool((prof or {}).get("strict_flow"))
    if isinstance(raw, dict):
        metals = raw.get("metals") or raw.get("stack") or []
        vias = raw.get("vias") or []
        gaps = raw.get("gaps") or []
        via_res = raw.get("via_res") or {}
        strict = strict or bool(raw.get("strict"))
    elif isinstance(raw, (list, tuple)):
        metals, vias, gaps, via_res = list(raw), [], [], {}
    else:
        return None
    metals = [str(x).strip() for x in metals if str(x).strip()]
    vias_n = []
    for item in vias:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            vias_n.append((str(item[0]).strip(), str(item[1]).strip(),
                           str(item[2]).strip() if len(item) > 2 else ""))
    gaps_n = []
    for item in gaps:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            try:
                gaps_n.append((str(item[0]).strip(), str(item[1]).strip(),
                               float(item[2])))
            except (TypeError, ValueError):
                pass
    via_res_n = {}
    if isinstance(via_res, dict):
        for k, v in via_res.items():
            try:
                via_res_n[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
    if not metals and not vias_n:
        return None
    return {"metals": metals, "vias": vias_n, "gaps": gaps_n,
            "via_res": via_res_n, "strict": strict}

def _proc_conductor_z(proc_data):
    z, out = 0.0, {}
    for item in (proc_data or {}).get("stack") or []:
        thick = item.get("thickness")
        if thick is None:
            break
        try:
            thick = float(thick)
        except (TypeError, ValueError):
            break
        z0, z = z, z + thick
        if item.get("kind") == "conductor" and item.get("name"):
            out.setdefault(item["name"], (z0, z))
    return out

def _flow_warnings(flow, cond_names, proc_data, cond_z):
    warnings = []
    metals = flow.get("metals") or []
    pos = dict((n, i) for i, n in enumerate(cond_names))
    missing = [n for n in metals if n not in pos]
    if missing:
        warnings.append("flow 金属 %s 未在 proc 中找到（跳过对应顺序校验）"
                        % ", ".join(missing))
    expected = [n for n in metals if n in pos]
    actual = [n for _p, n in sorted((pos[n], n) for n in expected)]
    if expected and actual != expected:
        warnings.append("proc 流程金属顺序与 flow 不一致：flow=%s，实际=%s"
                        "（当前工艺，仅告警）"
                        % (" → ".join(expected), " → ".join(actual)))
    vias = proc_data.get("vias") or []
    for bottom, top, name in flow.get("vias") or []:
        hit = [v for v in vias
               if v.get("bottom") == bottom and v.get("top") == top
               and (not name or v.get("name") == name)]
        if not hit:
            warnings.append("flow 通孔 %s（%s→%s）未在 proc 中找到（当前工艺）"
                            % (name or "?", bottom, top))
    for low, high, want in flow.get("gaps") or []:
        lo, hi = cond_z.get(low), cond_z.get(high)
        if not lo or not hi:
            continue
        gap = hi[0] - lo[1]
        if abs(gap - want) > 0.02:
            warnings.append("间距偏差 %s→%s：实测 %.3f μm，期望 %.3f μm"
                            "（当前工艺，仅告警）" % (low, high, gap, want))
    want_res = flow.get("via_res") or {}
    for v in vias:
        want = want_res.get(v.get("name"))
        got = v.get("resistance")
        if want is None or got is None:
            continue
        if abs(got - want) > max(0.02, 0.1 * abs(want)):
            warnings.append("通孔电阻偏差 %s：实测 %s Ω/via，期望 %g Ω/via"
                            "（当前工艺，仅告警）" % (v.get("name"), got, want))
    return warnings

def _generic_stack_check(prof, lmap_by_name, proc_data=None):
    errors, warnings = [], []
    roles = _tech_role_layers(prof)
    if not roles:
        errors.append("工艺配置 layers 为空：请至少定义 spiral/underpass/route/"
                      "via_sp 等角色（当前工艺）")
    if lmap_by_name is not None:
        errors.extend(_tech_lm_errors({"by_name": lmap_by_name}, prof))
    if proc_data is not None:
        cond_names = [c.get("name") for c in (proc_data.get("conductors") or [])
                      if c.get("name")]
        for role, name in roles:
            if _role_kind(role) == "metal" and name not in cond_names:
                errors.append("proc 中缺少角色 %s 的导体层 %s（当前工艺）"
                              % (role, name))
        flow = _tech_flow(prof)
        if flow:
            warnings.extend(_flow_warnings(
                flow, cond_names, proc_data, _proc_conductor_z(proc_data)))
    elif _tech_flow(prof):
        warnings.append("工艺配置提供了 flow，但本次未提供 proc，"
                        "已跳过叠层顺序/通孔校验")
    return errors, warnings

def _tech_lm_errors(lmap_data, prof=None):
    if prof is None:
        return []
    by_name = {}
    if isinstance(lmap_data, dict):
        by_name = lmap_data.get("by_name") or {}
        if not isinstance(by_name, dict):
            by_name = {}
    errs = []
    for role, name in _tech_role_layers(prof):
        if name not in by_name:
            errs.append("layermap 缺少层 %s（角色 %s，当前工艺）"
                        % (name, role))
    return errs

def _tech_error_text(errors, prof):
    lm = os.path.expanduser(str((prof or {}).get("layermap") or "")) or "(未填)"
    pr = os.path.expanduser(str((prof or {}).get("proc") or "")) or "(未填)"
    return ("工艺检查未通过，已停止本次任务：\n  " + "\n  ".join(errors)
            + "\n文件：layermap=%s\n      proc=%s\n"
              "建议：核对 tech_profile.json 的 layers 角色映射与对应 PDK "
              "文件；换工艺只需改 layers（及可选 flow），无需改代码。"
            % (lm, pr))

def _missing_tech_error(missing):
    lines = ["  %s = (未填写/未探查到)" % label for _key, label in missing]
    return ("工艺文件缺失或不可读，已停止本次任务：\n" + "\n".join(lines)
            + "\n建议：在「工艺」页填写，或用环境变量指定 "
              "LIBRF_LAYERMAP / LIBRF_PROC / LIBRF_TECHLIB_DIR / "
              "LIBRF_CDSSETUP；也可把文件放到工作目录或工作空间中由工具"
              "自动探查。")

def _normalize_need(need):
    if need is None:
        return ("layermap",)
    if isinstance(need, str):
        need = (need,)
    keys = []
    for k in need:
        k = str(k).strip()
        if k in TECH_NEED_KEYS and k not in keys:
            keys.append(k)
    return tuple(keys)

def require_tech_ready(prof, need=None, work=None, probe=True):
    prof = dict(prof or {})
    notes = []
    if probe:
        prof, notes = _resolve_tech_files(prof, work=work)
    need = _normalize_need(need)
    warnings = list(notes)
    missing = []
    for key, label in TECH_REQUIRED_FILES:
        ok = _tech_file_ok(prof.get(key), key == "techlib_dir")
        if key in need:
            if not ok:
                missing.append((key, label))
        elif not ok and key in ("techlib_dir", "cds_lib"):
            warnings.append("%s 未配置/不可用（%s）；将回退默认探查或自动生成"
                            % (label, "未填"))
    if missing:
        raise LibrfError(_missing_tech_error(missing))
    lmap_path = os.path.expanduser(str(prof.get("layermap") or ""))
    lmap_data = None
    lmap_by_name = None
    if lmap_path and _tech_file_ok(lmap_path):
        if _xs_parse_layermap is not None:
            try:
                lmap_data = _xs_parse_layermap(lmap_path)
            except Exception as e:
                raise LibrfError(
                    "layermap 解析失败：%s\n文件：%s\n建议：确认每行格式为 "
                    "name purpose layer datatype（空格/制表符分隔）。"
                    % (e, lmap_path))
        else:
            lmap_data = _parse_layermap_generic(lmap_path)
        lmap_by_name = lmap_data.get("by_name") or {}
    proc_path = os.path.expanduser(str(prof.get("proc") or ""))
    proc_data = None
    if "proc" in need and _tech_file_ok(proc_path):
        if _xs_parse_proc is not None:
            try:
                proc_data = _xs_parse_proc(proc_path)
            except Exception as e:
                raise LibrfError(
                    "proc 解析失败：%s\n文件：%s\n建议：检查报错行号处的 "
                    "conductor/layer/via 语法，或改用 PDK 原始 proc。"
                    % (e, proc_path))
        else:
            proc_data = _parse_proc_generic(proc_path)
    errors, gen_warnings = _generic_stack_check(prof, lmap_by_name, proc_data)
    warnings.extend(gen_warnings)
    flow = _tech_flow(prof)
    strict = bool(prof.get("strict_flow") or (flow and flow.get("strict")))
    if strict:
        if flow is None:
            warnings.append("已请求 strict_flow，但 profile 未提供 flow 段，"
                            "已跳过叠层严格校验")
        elif proc_data is None:
            warnings.append("已请求 strict_flow，但本次未提供/未需要 proc，"
                            "已跳过 proc_cross_section 严格校验")
        elif _xs_validate_flow_stack is None:
            warnings.append("已请求 strict_flow，但 proc_cross_section 不可用，"
                            "已降级为通用检查")
        else:
            try:
                strict_errs, strict_warns = _xs_validate_flow_stack(
                    proc_data, lmap_data, flow=flow)
            except Exception as e:
                raise LibrfError("工艺叠层严格校验异常：%s\n文件：%s"
                                 % (e, proc_path))
            errors.extend(str(x) for x in (strict_errs or []))
            warnings.extend(str(x) for x in (strict_warns or []))
    if errors:
        raise LibrfError(_tech_error_text(errors, prof))
    out = []
    for w in warnings:
        s = str(w)
        if s and s not in out:
            out.append(s)
    return out

def tech_layer(prof, role):
    lay = (prof or {}).get("layers") or {}
    return lay.get(role, role)

def tech_text(prof, metal):
    txt = (prof or {}).get("text") or {}
    return txt.get(metal, "%sTXT" % metal)

def tech_drc(prof):
    return (prof or {}).get("drc") or DEFAULT_TECH["drc"]

def _skill_val(v):
    if isinstance(v, bool):
        return "t" if v else "nil"
    if v is None:
        return "nil"
    if isinstance(v, str):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    return str(v)

def _stack_il(prof):
    """工艺档可选 stack 表 -> SKILL 的叠层全局量。

    prof["stack"] 形如::

        {"metals": ["M1", "M2", ...],            # 自下而上
         "vias": {"V1": {"lower": "M1", "upper": "M2",
                         "cut": 0.09, "space": 0.11,
                         "enc_lo": 0.03, "enc_hi": 0.02}, ...}}

    也接受顶层 stack_order + metals/vias。缺任意一段就返回空串：
    SKILL 会退回 via_sp 角色单跳连接，不会因缺表而画错通孔。
    """
    prof = prof or {}
    st = prof.get("stack") or {}
    metals = st.get("metals") or prof.get("metals") or {}
    order = list(st.get("order") or prof.get("stack_order") or [])
    if not order and isinstance(metals, (list, tuple)):
        order = list(metals)
    vias = st.get("vias") or prof.get("vias") or {}
    if not order or not metals:
        return ""
    if any(m not in metals for m in order):
        return ""
    vd = {}
    if isinstance(vias, dict):
        for name, v in vias.items():
            if isinstance(v, dict):
                vv = dict(v)
                vv.setdefault("name", name)
                vd[name] = vv
    elif isinstance(vias, (list, tuple)):
        for v in vias:
            if isinstance(v, dict) and v.get("name"):
                vd[v["name"]] = v

    def _enc(v, key, alt):
        val = v.get(key, v.get(alt, 0.0))
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    rows = []
    for lo, hi in zip(order, order[1:]):
        hit = None
        for name, v in vd.items():
            if v.get("lower") == lo and v.get("upper") == hi:
                hit = (name, v)
                break
        if not hit:
            return ""
        name, v = hit
        try:
            cut = float(v.get("cut", 0.0))
            space = float(v.get("space", 0.0))
        except (TypeError, ValueError):
            return ""
        rows.append('    list("%s" "%s" "%s" %s %s %s %s)' % (
            name, lo, hi, _skill_val(cut), _skill_val(space),
            _skill_val(_enc(v, "enc_lo", "enclosure")),
            _skill_val(_enc(v, "enc_hi", "enclosure"))))
    return ("; 叠层表（主/副金属与逐级通孔）\n"
            "librfStackMetals = list(%s)\n"
            "librfStackVias = list(\n%s)\n"
            % (" ".join('"%s"' % str(m) for m in order), "\n".join(rows)))

def tech_to_skill(prof):
    lay = (prof or {}).get("layers") or {}
    txt = (prof or {}).get("text") or {}
    drc = (prof or {}).get("drc") or {}
    def _alist(d):
        return "list(" + " ".join( 'list("%s" %s)' % (k, _skill_val(v)) for k, v in sorted(d.items())) + ")"
    def _drc_alist(d):
        parts = []
        for k in sorted(d.keys()):
            v = d[k]
            if isinstance(v, dict):
                parts.append('list("%s" %s)' % (k, _alist(v)))
            else:
                parts.append('list("%s" %s)' % (k, _skill_val(v)))
        return "list(" + " ".join(parts) + ")"
    techlib = (prof or {}).get("techlib") or ""
    return ("; librf_opt 注入的工艺配置（勿手改）\n"
        "librfTech = %s\n" "librfTechText = %s\n"
        "librfDrc = %s\n"
        "librfTechLib = \"%s\"\n" % (_alist(lay), _alist(txt), _drc_alist(drc), techlib)
        + _stack_il(prof))

def _techlib_name(prof=None):
    if prof and prof.get("techlib"):
        return prof["techlib"]
    return _env_path("LIBRF_TECHLIB", (DEFAULT_TECH.get("techlib") or "RFCMOS"))
