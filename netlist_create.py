#!/usr/bin/env python3
'netlist_create: 优化开始前从 Virtuoso 原理图/config 重新生成网表（需求 A）。'
# ===== 设计要点 =====
# 1) 只依赖标准库，Python 3.6 可运行；所有文本 I/O 显式 UTF-8。
# 2) 真正的 createNetlist 由 virtuoso -nograph -replay 执行；本模块生成 .il、
#    解析 OPT_NETLIST_* 文本协议、备份/校验/恢复网表。
# 3) runner 可注入，自测不依赖 Cadence。
# 4) 铁律：不删除用户文件；旧网表备份为 <path>.bak-<ts>。
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time


class NetlistError(Exception):
    """设计信息缺失/ADE_state.info 解析失败。"""
    pass


# ===== 文本与路径 =====

def _read_text(path):
    'UTF-8 读取（坏字节替换，绝不因 locale 失败）。'
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_text(path, text):
    'UTF-8 写入；自动建父目录；固定 LF。'
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _abs_path(path, base=None):
    '展开 ~；相对路径拼到 base（默认 cwd）；返回 normpath。'
    text = os.path.expanduser(_clean(path))
    if not text:
        return ""
    if not os.path.isabs(text):
        text = os.path.join(base or os.getcwd(), text)
    return os.path.normpath(text)


def _audit(kind, path=None, detail=""):
    """把操作并入 optimizer 的审计流；optimizer 不可用时静默跳过。"""
    try:
        import optimizer as _optimizer
        _optimizer.audit(kind, path, detail)
    except Exception:
        pass


# ===== ADE_state.info 解析 =====
# 典型内容：
#   designInfo = '("LIB" "CELL" "config" "spectre")
#   projectDir = "/home/user/simulation"
# 也兼容 OCEAN 风格：design( "LIB" "CELL" "view" ) / projectDir( "/path" )
_DESIGN_INFO_RE = re.compile(r"designInfo\s*=\s*'?\s*\(([^)]*)\)", re.S)
_DESIGN_CALL_RE = re.compile(
    r"\bdesign\s*\(\s*\"([^\"]*)\"\s+\"([^\"]*)\"\s+\"([^\"]*)\"", re.S)
_PROJECT_RE = re.compile(
    r"\bprojectDir\s*(?:=\s*|\(\s*)\"([^\"]*)\"", re.I | re.S)
_PROJECT_BARE_RE = re.compile(r"\bprojectDir\s*=\s*([^\s;)']+)", re.I)
_SIMULATOR_RE = re.compile(r"simulator\s*\(\s*'?\"?([A-Za-z0-9_]+)", re.I)


def _strip_comment_lines(text):
    '去掉整行注释（; // #），避免注释掉的历史 designInfo 抢先命中。'
    out = []
    for line in (text or "").splitlines():
        stripped = line.lstrip()
        if (stripped.startswith(";") or stripped.startswith("//")
                or stripped.startswith("#")):
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def _tokens(body):
    '引号里的 token；否则按空白/逗号切分。'
    quoted = re.findall(r'"([^"]*)"' + r"|'([^']*)'", body or "")
    out = [a if a else b for a, b in quoted]
    if not out:
        out = [t for t in re.split(r"[\s,()]+", (body or "").strip()) if t]
    return out


def parse_ade_state(state_dir):
    """解析 <state_dir>/ADE_state.info -> design 字段。解析失败抛 NetlistError。"""
    state_dir = _abs_path(state_dir)
    path = os.path.join(state_dir, "ADE_state.info")
    try:
        text = _strip_comment_lines(_read_text(path))
    except OSError as exc:
        raise NetlistError("无法读取 ADE_state.info：%s（%s）" % (path, exc))
    info = {"library": "", "cell": "", "view": "", "simulator": "",
            "project_dir": "", "path": path}
    names = []
    match = _DESIGN_INFO_RE.search(text)
    if match:
        names = _tokens(match.group(1))
    if len(names) < 3:
        call = _DESIGN_CALL_RE.search(text)
        if call:
            names = [call.group(1), call.group(2), call.group(3)]
    if len(names) < 3:
        raise NetlistError(
            "ADE_state.info 中未找到 designInfo/design(...)：%s" % path)
    info["library"], info["cell"], info["view"] = names[0], names[1], names[2]
    if len(names) > 3:
        info["simulator"] = _clean(names[3])
    sim = _SIMULATOR_RE.search(text)
    if sim and not info["simulator"]:
        info["simulator"] = sim.group(1)
    if not info["simulator"]:
        info["simulator"] = "spectre"
    proj = _PROJECT_RE.search(text)
    if proj:
        info["project_dir"] = os.path.expanduser(proj.group(1).strip())
    else:
        bare = _PROJECT_BARE_RE.search(text)
        if bare:
            info["project_dir"] = os.path.expanduser(
                bare.group(1).strip("\"' "))
    return info


# ===== 设计信息 =====

def _cfg_has_design(cfg):
    view = (_clean(cfg.get("netlist_view")) or _clean(cfg.get("view")))
    return bool(_clean(cfg.get("library")) and _clean(cfg.get("cell")) and view)


def read_design(cfg):
    """合并 cfg 与 ADE_state.info -> design dict。

    优先级：cfg["library"/"cell"/"netlist_view"/"view"] 高于 state 文件。
    缺项且无法从 state_dir 补齐时抛 NetlistError（中文原因）。
    """
    cfg = cfg or {}
    state_dir = _clean(cfg.get("state_dir"))
    info = {}
    if state_dir:
        try:
            info = parse_ade_state(state_dir)
        except NetlistError:
            # cfg 自身完整时，state 文件缺失不致命
            if not _cfg_has_design(cfg):
                raise
            info = {}

    sources = []

    def pick(keys, name):
        for key in keys:
            val = _clean(cfg.get(key))
            if val:
                sources.append("cfg")
                return val
        val = _clean(info.get(name))
        if val:
            sources.append("ade_state")
            return val
        return ""

    library = pick(("library",), "library")
    cell = pick(("cell",), "cell")
    view = pick(("netlist_view", "view"), "view")
    simulator = (_clean(cfg.get("simulator"))
                 or _clean(info.get("simulator")) or "spectre")
    project_dir = (_clean(cfg.get("project_dir"))
                   or _clean(info.get("project_dir")))
    missing = [name for name, val in (("library", library), ("cell", cell),
                                      ("view", view)) if not val]
    if missing:
        raise NetlistError(
            "设计信息缺少 %s；请在配置里给出，或提供包含 ADE_state.info 的 "
            "state_dir" % "/".join(missing))
    source = "cfg"
    if sources:
        if all(item == "ade_state" for item in sources):
            source = "ade_state"
        elif any(item == "ade_state" for item in sources):
            source = "mixed"
    return {
        "library": library,
        "cell": cell,
        "view": view,
        "simulator": simulator,
        "project_dir": os.path.expanduser(project_dir),
        "state_dir": _abs_path(state_dir) if state_dir else None,
        "source": source,
    }


def expected_netlist_path(design, cfg=None):
    """预期网表路径：<sim_root|projectDir|~/simulation>/<cell>/<sim>/<view>/netlist/input.scs。"""
    design = design or {}
    cfg = cfg or {}
    cell = _clean(design.get("cell"))
    view = _clean(design.get("view")) or "schematic"
    simulator = _clean(design.get("simulator")) or "spectre"
    sim_root = _clean(cfg.get("sim_root"))
    project_dir = _clean(design.get("project_dir")) or _clean(
        cfg.get("project_dir"))
    root = sim_root or project_dir or "~/simulation"
    root = os.path.expanduser(root)
    # projectDir 已是 <...>/<cell>/<sim>/<view> 时直接用，避免重复拼层次
    if project_dir and not sim_root:
        pd = os.path.normpath(os.path.expanduser(project_dir))
        parent = os.path.dirname(pd)
        grand = os.path.dirname(parent)
        if (cell and view and simulator and
                os.path.basename(pd) == view and
                os.path.basename(parent) == simulator and
                os.path.basename(grand) == cell):
            return os.path.normpath(os.path.join(pd, "netlist", "input.scs"))
    return os.path.normpath(os.path.join(root, cell, simulator, view,
                                         "netlist", "input.scs"))


# ===== .il 脚本 =====

def _skill_quote(value):
    'SKILL 字符串字面量（转义反斜杠和双引号）。'
    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + text + '"'


def build_script(design, status_path=None):
    """生成 createNetlist 的 .il；协议同时写 stdout 与 status_path（可为 None）。"""
    design = design or {}
    lib = _skill_quote(design.get("library", ""))
    cell = _skill_quote(design.get("cell", ""))
    view = _skill_quote(design.get("view", "schematic"))
    port_init = ("outfile(%s)" % _skill_quote(status_path)
                 if status_path else "nil")
    fmt_design = '"OPT_NETLIST_DESIGN %s %s %s\\n"'
    fmt_ok = '"OPT_NETLIST_OK %L\\n"'
    fmt_fail = '"OPT_NETLIST_FAIL createNetlist returned nil (OSSHNL/ADE)\\n"'
    fmt_fail_path = ('"OPT_NETLIST_FAIL createNetlist returned no path '
                     '(OSSHNL/ADE)\\n"')
    lines = [
        "; Virtuoso Optimizer: createNetlist before optimization. Generated; do not edit.",
        "simulator('spectre)",
        "design(%s %s %s)" % (lib, cell, view),
        "let((port p q)",
        "  port = %s" % port_init,
        "  printf(%s %s %s %s)" % (fmt_design, lib, cell, view),
        "  when(port fprintf(port %s %s %s %s))" % (
            fmt_design, lib, cell, view),
        "  p = errset(createNetlist(?recreateAll t) t)",
        '  printf("OPT_NETLIST_RAW %L\\n" p)',
        "  when(port fprintf(port \"OPT_NETLIST_RAW %L\\n\" p))",
        "  if(p then",
        "    q = car(p)",
        "    when(listp(q) q = car(q))",
        # 以返回路径为准：errset 成功但 createNetlist 返回 nil 时 p=(nil)，
        # 不能当成功，否则会用旧网表蒙混过关。
        "    if(stringp(q) then",
        "      printf(%s q)" % fmt_ok,
        "      when(port fprintf(port %s q))" % fmt_ok,
        "    else",
        "      printf(%s)" % fmt_fail_path,
        "      when(port fprintf(port %s))" % fmt_fail_path,
        "    )",
        "  else",
        "    printf(%s)" % fmt_fail,
        "    when(port fprintf(port %s))" % fmt_fail,
        "  )",
        "  when(port close(port))",
        ")",
        "exit",
    ]
    return "\n".join(lines) + "\n"


def _unquote(raw):
    return re.sub(r"\\(.)", r"\1", raw or "")


def _extract_path(rest):
    '从 OK 行剩余部分提取路径；兼容 "p"、("p")、(\'p\')、裸词。'
    rest = (rest or "").strip()
    if not rest:
        return ""
    match = re.search(r'"((?:[^"\\]|\\.)*)"', rest)
    if match:
        return _unquote(match.group(1)).strip()
    match = re.search(r"'([^']*)'", rest)
    if match:
        return match.group(1).strip()
    for token in rest.split():
        token = token.strip("()[],'\"")
        if token and ("/" in token or token.endswith(".scs")):
            return token
    return ""


def _parse_status(text):
    '解析 OPT_NETLIST_* 协议；ok/fail 为 None 表示没收到该行。'
    out = {"design": [], "ok": None, "fail": None}
    for raw in _as_text(text).splitlines():
        line = raw.strip()
        if line.startswith("OPT_NETLIST_DESIGN"):
            out["design"] = _tokens(line[len("OPT_NETLIST_DESIGN"):])
        elif line.startswith("OPT_NETLIST_FAIL"):
            out["fail"] = (line[len("OPT_NETLIST_FAIL"):].strip()
                           or "createNetlist 返回 nil")
        elif line.startswith("OPT_NETLIST_OK"):
            out["ok"] = _extract_path(line[len("OPT_NETLIST_OK"):])
    return out


# ===== 运行环境 =====

def _resolve_virtuoso(cfg):
    'cfg["virtuoso_bin"] -> VIRTUOSO_BIN -> PATH -> 常见 CDS 根。'
    cand = _clean(cfg.get("virtuoso_bin")) or _clean(
        os.environ.get("VIRTUOSO_BIN"))
    if cand:
        cand = os.path.expanduser(cand)
        if os.path.isfile(cand):
            return cand
        hit = shutil.which(cand)
        if hit:
            return hit
    hit = shutil.which("virtuoso")
    if hit:
        return hit
    for var in ("CDSHOME", "CDSROOT", "CDS_INST_DIR", "CADHOME"):
        root = _clean(os.environ.get(var))
        if not root:
            continue
        for sub in ("tools/dfII/bin", "bin"):
            path = os.path.join(root, sub, "virtuoso")
            if os.path.isfile(path):
                return path
    return cand or "virtuoso"


def _environment(cfg):
    '子进程环境：继承 + cfg["env"] + path_append；去掉 DISPLAY。'
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    extra_env = cfg.get("env") or {}
    if isinstance(extra_env, dict):
        for key, val in extra_env.items():
            env[str(key)] = str(val)
    extra_path = _clean(cfg.get("path_append"))
    if extra_path:
        env["PATH"] = (env.get("PATH") or "") + os.pathsep + extra_path
    return env


def _has_cds_lib(path):
    return bool(path) and os.path.isfile(os.path.join(path, "cds.lib"))


def _cds_root(start):
    """Nearest ancestor with cds.lib, else the original directory.

    Virtuoso resolves library names through cds.lib in its cwd.  A state
    directory such as <sim>/Cell/spectre_state1 has no cds.lib of its own,
    so use the project root that does.
    """
    cur = _abs_path(start)
    for _ in range(10):
        if _has_cds_lib(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return _abs_path(start)


def _work_dir(cfg):
    'cfg["work_dir"]，否则 state_dir 上一级，否则 cwd；尽量落在有 cds.lib 的目录。'
    work = _clean(cfg.get("work_dir"))
    if work:
        return _cds_root(work)
    state_dir = _clean(cfg.get("state_dir"))
    if state_dir:
        return _cds_root(os.path.dirname(_abs_path(state_dir)))
    return _cds_root(os.getcwd())


def _timeout(cfg, timeout):
    if timeout is not None:
        try:
            return float(timeout)
        except (TypeError, ValueError):
            pass
    try:
        return float(cfg.get("netlist_timeout", 600))
    except (TypeError, ValueError):
        return 600.0


def _default_runner(cmd, cwd, env, timeout):
    'subprocess 实现；超时杀进程并返回 rc=124。'
    proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
        err_text = _as_text(err)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        out, err = proc.communicate()
        rc = 124
        err_text = _as_text(err) + "\nTimeout after %ss: %s" % (
            timeout, " ".join(str(x) for x in cmd))
    return rc, _as_text(out), err_text


def _unique_stem(directory, prefix):
    ts = time.strftime("%Y%m%d-%H%M%S")
    stem = os.path.join(directory, "%s_%s" % (prefix, ts))
    n = 1
    while (os.path.exists(stem + ".il") or os.path.exists(stem + ".log")
           or os.path.exists(stem + ".status")):
        stem = os.path.join(directory, "%s_%s.%d" % (prefix, ts, n))
        n += 1
    return stem


def _backup_path(path):
    '不覆盖已有备份：<path>.bak-<ts>，冲突再加 .N。'
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-%s" % (path, ts)
    n = 1
    while os.path.exists(dst):
        dst = "%s.bak-%s.%d" % (path, ts, n)
        n += 1
    return dst


def _validate_netlist(path):
    '返回空串表示有效；否则中文原因。'
    if not path:
        return "路径为空"
    if not os.path.isfile(path):
        return "文件不存在：%s" % path
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "无法读取文件大小：%s（%s）" % (path, exc)
    if size <= 100:
        return "文件过小（%d 字节）：%s" % (size, path)
    try:
        text = _read_text(path)
    except OSError as exc:
        return "无法读取文件：%s（%s）" % (path, exc)
    if "simulator lang=spectre" not in text:
        return "缺少 'simulator lang=spectre'：%s" % path
    return ""


def _scan_lines(text):
    '抽 OSSHNL/ADE/ERROR 行与 WARNING 行；各最多 20 条，去重保序。'
    errors, warnings = [], []
    for raw in _as_text(text).splitlines():
        line = raw.strip()
        if not line or line.startswith("OPT_NETLIST_"):
            continue
        # Virtuoso echoes the replay script into its log with a \i prefix.
        # Those lines contain our own printf("... FAIL ...") literals and
        # must never be counted as real tool errors.
        if line.startswith("\\i"):
            continue
        body = line[2:].strip() if line.startswith(("\\o", "\\w")) else line
        if re.match(r"Errors:\s*0\s+Warnings:\s*0", body):
            continue
        if re.match(r"INFO\b", body, re.IGNORECASE):
            continue
        low = body.lower()
        # WARNING first: OSSHNL warnings also match the OSSHNL pattern.
        if "warning" in low:
            if len(warnings) < 20 and body not in warnings:
                warnings.append(body)
        elif re.search(r"osshnl|\bade-|\berror\b", low):
            if len(errors) < 20 and body not in errors:
                errors.append(body)
    return errors, warnings


def _brace_opens(text):
    'Unclosed { } count, ignoring comments and quoted text.'
    depth = 0
    quote = None
    at_line_start = True
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\r\n":
            at_line_start = True
        elif quote:
            if ch == quote:
                quote = None
        elif at_line_start and ch in " \t":
            pass
        elif at_line_start and ch in "#;*":
            # Comment lines use the same prefixes the deck parsers skip;
            # a "{" in prose must not swallow the rest of the deck.
            end = text.find("\n", i + 1)
            if end < 0:
                break
            i = end
            at_line_start = True
        else:
            at_line_start = False
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch in "\"'":
                quote = ch
            elif ch == "/" and i + 1 < n and text[i + 1] == "/":
                # A line comment hides only the rest of its own physical line;
                # the "}" closing a brace block may be on a later line.
                end = text.find("\n", i + 2)
                if end < 0:
                    break
                i = end
                at_line_start = True
            elif ch == "/" and i + 1 < n and text[i + 1] == "*":
                end = text.find("*/", i + 2)
                if end < 0:
                    break
                i = end + 1
        i += 1
    return depth


def _logical_lines(lines):
    'Yield (start, end, text) for statements joined by \\, + or { } blocks.'
    i = 0
    while i < len(lines):
        start = i
        text = lines[i]
        while i + 1 < len(lines):
            cont = text.rstrip().endswith("\\")
            # Braces inside comments/quotes are not block syntax; counting
            # them would swallow the rest of the deck into one statement.
            opens = _brace_opens(text)
            nxt = lines[i + 1].lstrip()
            # A leading "+" is a continuation marker too (SPICE heritage),
            # independent of a trailing backslash on the previous line.
            plus = nxt.startswith("+")
            if not cont and opens <= 0 and not plus:
                break
            i += 1
            if plus:
                nxt = nxt[1:]
            # Keep physical line breaks: _brace_opens scans the accumulated
            # text, and "//" must not hide the rest of later lines.
            text = text.rstrip().rstrip("\\") + "\n" + nxt
        yield start, i, text
        i += 1


def _top_parameters(text):
    """Parse top level `parameters` statements, continuation aware.

    Returns (ordered name -> raw value, list of (start, end) line spans).
    Bare declarations map to "" (no value).
    """
    lines = text.splitlines()
    depth = 0
    values = {}
    blocks = []
    for start, end, raw in _logical_lines(lines):
        code = raw.strip()
        if not code or code.startswith(("//", "*", ";", "#")):
            continue
        low = code.lower()
        if re.match(r"(?:inline\s+)?subckt\b", low):
            depth += 1
            continue
        if re.match(r"ends\b", low):
            depth = max(0, depth - 1)
            continue
        if depth or not re.match(r"parameters\b", low):
            continue
        blocks.append((start, end))
        body = re.sub(r"^parameters\b", "", code, count=1,
                      flags=re.IGNORECASE)
        for token in body.split():
            if "=" in token:
                name, val = token.split("=", 1)
                if name:
                    values[name] = val
            elif token:
                values[token] = ""
    return values, blocks


def _fill_parameters(text, values):
    'Rewrite top level parameters statements into one line with values.'
    lines = text.splitlines()
    _, blocks = _top_parameters(text)
    line = "parameters " + " ".join(
        "%s=%s" % (k, v) if v else k for k, v in values.items())
    if blocks:
        first_start, first_end = blocks[0]
        out = lines[:first_start] + [line]
        drop = set()
        for start, end in blocks[1:]:
            drop.update(range(start, end + 1))
        out.extend(l for idx, l in enumerate(lines[first_end + 1:],
                                           first_end + 1)
                   if idx not in drop)
        return "\n".join(out) + ("\n" if text.endswith("\n") else "")
    insert = 0
    for idx, raw in enumerate(lines):
        if re.match(r"\s*(simulator|global)\b", raw):
            insert = idx + 1
    return "\n".join(lines[:insert] + [line] + lines[insert:]) + (
        "\n" if text.endswith("\n") else "")


def carry_over_parameters(new_text, old_text):
    """Keep old top level values for parameters the regenerated deck left bare.

    ADE createNetlist can emit declarations such as `Ls2 Lp2` without
    values when those variables live in the ADE state rather than in the
    schematic.  The previous deck has the effective values, so carry them
    over instead of failing at spectre with SFE-1997.
    """
    old_values, _ = _top_parameters(old_text)
    new_values, _ = _top_parameters(new_text)
    if not old_values:
        return new_text, []
    changes = []
    for name, value in old_values.items():
        if not value:
            continue
        if name in new_values:
            if new_values[name]:
                continue
            new_values[name] = value
            changes.append({"name": name, "value": value,
                            "action": "fill_bare"})
        elif re.search(r"(?<![\w.])%s(?![\w.])" % re.escape(name),
                       new_text):
            new_values[name] = value
            changes.append({"name": name, "value": value,
                            "action": "append_used"})
    if not changes:
        return new_text, []
    return _fill_parameters(new_text, new_values), changes


# 分析类型集合必须覆盖 optimizer._ANALYSIS_KEYWORDS（预检用同一口径），
# selftest.py 有子集断言防漂移。
_ANALYSES = {
    "ac", "acmatch", "acnoise", "dc", "dcOp".lower(), "dcmatch", "disto",
    "envlp", "fourier", "hb", "hbac", "hbnoise", "hbsp", "montecarlo",
    "mpde", "noise", "pac", "pacnoise", "pnoise", "pss", "pstb", "psp",
    "pz", "ql", "sens", "sp", "spnoise", "stb", "sweep", "sweephb",
    "tran", "xf", "tf",
}

# 语句首 token：这些行永远不是分析（`save sp`、`parameters sp`、
# `subckt sp` 等），必须先于第二 token 的类型判断排除。`inc` 是 spectre
# 合法的 include 写法，optimizer 的指令集没有列它，这里有意保留。
_NON_ANALYSIS_HEADS = frozenset((
    "simulator", "global", "include", "ahdl_include", "hdl_include", "inc",
    "parameters", "subckt", "ends", "inline", "model", "library",
    "endlibrary", "section", "endsection", "statistics", "process",
    "paramtest", "options", "save", "ic", "nodeset", "temp", "alter",
    "altergroup", "real", "check", "assert", "shell", "set", "title",
    "end", "info", "fatal", "warning", "error",
))


def _normalize_stmt(text):
    return " ".join(str(text or "").split())


def _analysis_kind(code):
    """一行逻辑语句的分析类型；不是分析则返回 None。

    spectre/ADE 写成 ``<分析名> <类型> key=val``（``ac1 ac start=...``、
    ``sp1 sp ports=...``），类型取第二 token；手写的首 token 即类型
    （``sp sp ports=...``）仍兼容。实例的第二 token 是节点列表
    （以 ``(`` 开头），所以不会把 ``sp (a b) capacitor`` 当成分析。
    """
    toks = str(code or "").lower().split()
    if not toks or toks[0] in _NON_ANALYSIS_HEADS:
        return None
    if len(toks) > 1 and toks[1] in _ANALYSES:
        return toks[1]
    if toks[0] in _ANALYSES and not (
            len(toks) > 1 and toks[1].startswith("(")):
        return toks[0]
    return None


def _extract_testbench(text):
    """Pull model includes, analyses and save statements from a full deck."""
    lines = text.splitlines()
    depth = 0
    includes = []
    analyses = []
    saves = []
    for _start, _end, raw in _logical_lines(lines):
        code = raw.strip()
        if not code or code.startswith(("//", "*", ";", "#")):
            continue
        low = code.lower()
        if re.match(r"(?:inline\s+)?subckt\b", low):
            depth += 1
            continue
        if re.match(r"ends\b", low):
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        first = low.split(None, 1)[0]
        if first in ("include", "ahdl_include", "inc"):
            if "pex.netlist" not in low:
                includes.append(code)
        elif first == "save":
            saves.append(code)
        else:
            kind = _analysis_kind(code)
            if kind:
                analyses.append((kind, code))
    return {"includes": includes, "analyses": analyses, "saves": saves}


def _insert_before(lines, index, new_lines):
    return lines[:index] + list(new_lines) + lines[index:]


def merge_testbench(new_text, old_text):
    """Keep model includes / analyses / saves that createNetlist drops.

    A config-view createNetlist run yields the design deck only: no model
    library includes and no ADE analyses.  The previous full deck has both,
    so merge the pieces the new deck lacks.
    """
    old = _extract_testbench(old_text)
    new_lines = new_text.splitlines()
    existing = set()
    existing_types = set()
    for _s, _e, raw in _logical_lines(new_lines):
        existing.add(_normalize_stmt(raw))
        code = raw.strip()
        if code and not code.startswith(("//", "*", ";", "#")):
            kind = _analysis_kind(code)
            if kind:
                existing_types.add(kind)

    add_includes = []
    for code in old["includes"]:
        if _normalize_stmt(code) not in existing:
            add_includes.append(code)
    add_analyses = []
    for name, code in old["analyses"]:
        if name not in existing_types and _normalize_stmt(code) not in existing:
            add_analyses.append(code)
    add_saves = [code for code in old["saves"]
                 if _normalize_stmt(code) not in existing]

    if not (add_includes or add_analyses or add_saves):
        return new_text, {"includes": 0, "analyses": [], "saves": 0}

    # Includes go right before the first subcircuit / library block.
    if add_includes:
        idx = 0
        for i, raw in enumerate(new_lines):
            if re.match(r"\s*(subckt\b|//\s*Library name:)", raw):
                idx = i
                break
        new_lines = _insert_before(new_lines, idx, add_includes)

    # Analyses and saves go before the reporting options, else at the end.
    tail = add_analyses + add_saves
    if tail:
        idx = len(new_lines)
        for i, raw in enumerate(new_lines):
            if re.match(r"\s*(modelParameter info|saveOptions\b)", raw):
                idx = i
                break
        new_lines = _insert_before(new_lines, idx, tail)

    return ("\n".join(new_lines) + ("\n" if new_text.endswith("\n") else ""),
            {"includes": len(add_includes),
             "analyses": [name for name, code in old["analyses"]
                          if code in add_analyses],
             "saves": len(add_saves)})


def _empty_report(status="fail", message=""):
    return {"status": status, "path": "", "expected": "", "previous": "",
            "backup": "", "design": {}, "wall": 0.0, "log": "",
            "errors": [], "warnings": [], "message": message,
            "restored": False, "carry_over": [],
            "testbench_merge": {"includes": 0, "analyses": [], "saves": 0}}


# ===== 主流程 =====

def create_netlist(cfg, timeout=None, log_dir=None, runner=None):
    """执行一次 createNetlist：备份 -> 运行 -> 校验 -> 失败恢复。

    runner(cmd, cwd, env, timeout) -> (rc, stdout, stderr)；默认 subprocess。
    """
    cfg = cfg or {}
    started = time.time()
    report = _empty_report("fail", "")
    try:
        design = read_design(cfg)
    except NetlistError as exc:
        report["message"] = "读取设计信息失败：%s" % exc
        report["wall"] = round(time.time() - started, 3)
        return report
    report["design"] = dict(design)
    expected = expected_netlist_path(design, cfg)
    report["expected"] = expected
    previous = expected if os.path.isfile(expected) else ""
    report["previous"] = previous

    # 1) 先备份旧网表（存在才备份；备份失败不阻断，只记 warning）
    backup = ""
    if previous:
        try:
            backup = _backup_path(expected)
            shutil.copy2(previous, backup)
            _audit("backup", expected, "-> " + os.path.basename(backup))
        except OSError as exc:
            backup = ""
            report["warnings"].append("备份旧网表失败：%s" % exc)
    report["backup"] = backup

    # 2) 写脚本 / 准备命令
    work_dir = _work_dir(cfg)
    run_dir = (_abs_path(log_dir) if log_dir
               else os.path.join(work_dir, "netlist_create"))
    try:
        os.makedirs(run_dir, exist_ok=True)
        stem = _unique_stem(run_dir, "netlist_create")
        script_path = stem + ".il"
        status_path = stem + ".status"
        log_path = stem + ".log"
        _write_text(script_path, build_script(design, status_path))
    except OSError as exc:
        report["message"] = "准备 createNetlist 脚本失败：%s" % exc
        report["wall"] = round(time.time() - started, 3)
        return report
    report["log"] = log_path
    report["script"] = script_path

    cmd = [_resolve_virtuoso(cfg), "-nograph", "-replay", script_path,
           "-log", log_path]
    env = _environment(cfg)
    wait = _timeout(cfg, timeout)
    report["cmd"] = cmd
    _audit("exec", cmd[0], " ".join(cmd[1:])[:200])

    # 3) 运行
    run = runner or _default_runner
    try:
        rc, out, err = run(cmd, work_dir, env, wait)
    except Exception as exc:  # 注入 runner / Popen 抛错都按失败处理
        rc, out, err = -1, "", "runner 异常：%s" % exc
    stdout = _as_text(out)
    stderr = _as_text(err)
    status_text = _read_text(status_path) if os.path.isfile(status_path) else ""
    log_text = _read_text(log_path) if os.path.isfile(log_path) else ""
    report["rc"] = rc
    combined = "\n".join([stdout, stderr, status_text, log_text])
    parsed = _parse_status(combined)
    errors, warnings = _scan_lines(
        "\n".join([stderr, status_text, log_text, stdout]))
    report["errors"] = errors
    for warn in warnings:
        if warn not in report["warnings"]:
            report["warnings"].append(warn)

    # 4) 判定 + 校验
    #    OK 行必须带路径：否则可能是 errset 包住了 nil，旧网表还在原地，
    #    不能把“文件存在”误判为“刚生成成功”。
    if parsed["ok"]:
        got = _abs_path(parsed["ok"], work_dir)
        report["path"] = got
        problem = _validate_netlist(got)
        if not problem and previous and backup and os.path.isfile(backup):
            # createNetlist on a config view emits the design deck only.
            # Keep the old model includes / analyses / saves, and carry
            # over ADE-state parameters it left as bare declarations.
            try:
                old_text = _read_text(backup)
                merged, tb = merge_testbench(_read_text(got), old_text)
                merged, changes = carry_over_parameters(merged, old_text)
                if tb["includes"] or tb["analyses"] or tb["saves"] or changes:
                    _write_text(got, merged)
                    problem = _validate_netlist(got)
                    report["testbench_merge"] = tb
                    report["carry_over"] = changes
                    _audit("write", got, "merge testbench=%s carry=%d" % (
                        tb, len(changes)))
            except (OSError, ValueError) as exc:
                report["warnings"].append("测试台合并失败：%s" % exc)
        if not problem:
            report["status"] = "ok"
            report["message"] = "已重新生成网表：%s" % got
        else:
            report["message"] = "网表校验失败：%s" % problem
    else:
        message = parsed["fail"] or ""
        if not message and parsed["ok"] == "":
            message = "OPT_NETLIST_OK 行缺少网表路径"
        if not message and report["errors"]:
            message = report["errors"][0]
        if not message:
            message = "virtuoso 返回码 %s，未收到 OPT_NETLIST_OK" % rc
        report["message"] = "createNetlist 失败：%s" % message

    # 5) 失败且原来有网表 -> 用备份恢复
    if report["status"] != "ok" and previous and backup and os.path.isfile(
            backup):
        try:
            shutil.copy2(backup, expected)
            report["restored"] = True
            report["message"] += "；已恢复原网表"
            _audit("write", expected, "restored from " + os.path.basename(
                backup))
        except OSError as exc:
            report["message"] += "；恢复备份失败：%s" % exc
    report["wall"] = round(time.time() - started, 3)
    return report


def _auto_mode(value):
    'auto_netlist 取值归一化：off / auto / always。'
    if value is None:
        return "auto"
    if isinstance(value, bool):
        return "always" if value else "off"
    if isinstance(value, (int, float)):
        return "always" if value else "off"
    text = str(value).strip().lower()
    if text in ("", "auto"):
        return "auto"
    if text in ("false", "off", "no", "0", "none", "disabled"):
        return "off"
    if text in ("true", "always", "on", "1", "yes", "force"):
        return "always"
    return "auto"


def _readable_netlist(path, base=None):
    text = _clean(path)
    if not text:
        return ""
    full = _abs_path(text, base)
    try:
        if os.path.isfile(full) and os.path.getsize(full) > 0:
            return full
    except OSError:
        pass
    return ""


def ensure_netlist(cfg):
    """入口：按 cfg["auto_netlist"] 决定是否生成；失败可回退旧网表，不抛异常。"""
    cfg = cfg or {}
    mode = _auto_mode(cfg.get("auto_netlist", "auto"))
    if mode == "off":
        report = _empty_report("skipped", "auto_netlist=false，跳过自动生成网表")
        report["reason"] = "auto_netlist=false"
        return report
    if mode == "auto":
        state_dir = _clean(cfg.get("state_dir"))
        has_cfg = bool(_clean(cfg.get("library")) and _clean(cfg.get("cell")))
        if not (state_dir or has_cfg):
            report = _empty_report(
                "skipped", "auto_netlist=auto 但未提供 state_dir 或 "
                "library+cell，跳过")
            report["reason"] = "no state_dir/library+cell"
            return report
    try:
        report = create_netlist(cfg)
    except Exception as exc:
        report = _empty_report("fail", "自动生成网表异常：%s" % exc)
    if report.get("status") == "ok":
        cfg["netlist"] = report.get("path", "")
        cfg["netlist_view"] = (report.get("design") or {}).get("view", "")
        return report
    old = _readable_netlist(cfg.get("netlist"), _work_dir(cfg))
    if old:
        report["status"] = "fallback"
        report["fallback_path"] = old
        report["path"] = old
        report["message"] = "%s；沿用旧网表：%s" % (
            report.get("message") or "自动生成网表失败", old)
        return report
    report["status"] = "fail"
    return report


# ===== 自测（纯 Python，不依赖 Cadence） =====

_FAKE_OLD = "simulator lang=spectre\nparameters L1=1n\n" + "// old\n" * 20
_FAKE_NEW = "simulator lang=spectre\nparameters L1=2n\n" + "// new\n" * 20


def _fake_ade(path, lib="LibA", cell="CellA", view="schematic",
              sim="spectre", project=None):
    lines = [";; ADE state", "designInfo = '(\"%s\" \"%s\" \"%s\" \"%s\")"
             % (lib, cell, view, sim)]
    if project:
        lines.append('projectDir = "%s"' % project)
    _write_text(os.path.join(path, "ADE_state.info"), "\n".join(lines) + "\n")


def _t_ade_state():
    tmp = tempfile.mkdtemp(prefix="nc_ade_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        _fake_ade(state, "MyLib", "test_cs_driver", "config",
                  project="/home/user/simulation")
        # 注释掉的历史 designInfo 不能抢先命中
        info_path = os.path.join(state, "ADE_state.info")
        _write_text(info_path,
                    ";; designInfo = '(\"Wrong\" \"Wrong\" \"wrong\" "
                    "\"spectre\")\n" + _read_text(info_path))
        design = read_design({"state_dir": state})
        assert design["library"] == "MyLib", design
        assert design["cell"] == "test_cs_driver", design
        assert design["view"] == "config", design
        assert design["simulator"] == "spectre", design
        assert design["project_dir"] == "/home/user/simulation", design
        assert design["source"] == "ade_state", design
        assert design["state_dir"] == os.path.normpath(state), design

        mixed = read_design({"state_dir": state, "library": "Lib2",
                             "netlist_view": "schematic"})
        assert mixed["library"] == "Lib2", mixed
        assert mixed["cell"] == "test_cs_driver", mixed
        assert mixed["view"] == "schematic", mixed
        assert mixed["source"] == "mixed", mixed

        state2 = os.path.join(tmp, "ade2")
        os.makedirs(state2)
        _write_text(os.path.join(state2, "ADE_state.info"),
                    "design( \"LibX\" \"CellX\" \"config\" )\n"
                    "simulator( 'spectre )\n"
                    "projectDir( \"/p/sim\" )\n")
        design2 = read_design({"state_dir": state2})
        assert design2["library"] == "LibX", design2
        assert design2["cell"] == "CellX", design2
        assert design2["view"] == "config", design2
        assert design2["simulator"] == "spectre", design2
        assert design2["project_dir"] == "/p/sim", design2

        try:
            read_design({"state_dir": os.path.join(tmp, "missing")})
        except NetlistError as exc:
            assert "ADE_state.info" in str(exc), exc
        else:
            raise AssertionError("缺失 state 文件应抛 NetlistError")

        # cfg 完整时 state 文件缺失不致命
        fallback = read_design({"state_dir": os.path.join(tmp, "missing"),
                                "library": "L", "cell": "C",
                                "view": "schematic"})
        assert fallback["source"] == "cfg", fallback

        try:
            read_design({"library": "L", "cell": "C"})
        except NetlistError as exc:
            assert "view" in str(exc) or "设计信息缺少" in str(exc), exc
        else:
            raise AssertionError("缺 view 应抛 NetlistError")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_expected_path():
    design = {"library": "L", "cell": "C", "view": "config",
              "simulator": "spectre", "project_dir": "/home/u/simulation"}
    path = expected_netlist_path(design, {})
    assert path.replace("\\", "/") == (
        "/home/u/simulation/C/spectre/config/netlist/input.scs"), path
    path2 = expected_netlist_path(design, {"sim_root": "/scratch/sim"})
    assert path2.replace("\\", "/") == (
        "/scratch/sim/C/spectre/config/netlist/input.scs"), path2
    design3 = {"cell": "C", "view": "schematic", "simulator": "spectre",
               "project_dir": "/home/u/simulation/C/spectre/schematic"}
    path3 = expected_netlist_path(design3, {})
    assert path3.replace("\\", "/").endswith(
        "/home/u/simulation/C/spectre/schematic/netlist/input.scs"), path3
    design4 = {"cell": "C", "view": "schematic", "simulator": "spectre"}
    path4 = expected_netlist_path(design4, {})
    assert path4.replace("\\", "/").endswith(
        "/simulation/C/spectre/schematic/netlist/input.scs"), path4


def _t_build_script():
    design = {"library": "MyLib", "cell": "Cell1", "view": "config",
              "simulator": "spectre"}
    text = build_script(design, "/tmp/status.opt")
    assert "simulator('spectre)" in text
    assert 'design("MyLib" "Cell1" "config")' in text
    assert "OPT_NETLIST_DESIGN" in text
    assert "OPT_NETLIST_OK" in text
    assert "OPT_NETLIST_FAIL" in text
    assert "createNetlist(?recreateAll t)" in text
    assert 'outfile("/tmp/status.opt")' in text
    # 绝不输出无路径的 OK：那会把旧网表误判成新生成
    assert '"OPT_NETLIST_OK\\n"' not in text, text
    assert text.rstrip().endswith("exit"), text
    assert _skill_quote('a"b\\c') == '"a\\"b\\\\c"'
    no_status = build_script(design, None)
    assert "port = nil" in no_status, no_status


def _t_parse_status():
    parsed = _parse_status(
        "OPT_NETLIST_DESIGN Lib Cell schematic\n"
        'OPT_NETLIST_OK "/tmp/input.scs"\n')
    assert parsed["design"] == ["Lib", "Cell", "schematic"], parsed
    assert parsed["ok"] == "/tmp/input.scs", parsed
    parsed = _parse_status('OPT_NETLIST_OK ("/tmp/input.scs")\n')
    assert parsed["ok"] == "/tmp/input.scs", parsed
    parsed = _parse_status("OPT_NETLIST_FAIL OSSHNL-514: boom\n")
    assert "OSSHNL-514" in parsed["fail"], parsed
    parsed = _parse_status("OPT_NETLIST_OK\n")
    assert parsed["ok"] == "", parsed
    parsed = _parse_status("nothing here")
    assert parsed["ok"] is None and parsed["fail"] is None, parsed


def _runner_success(expected, old_written=None):
    def runner(cmd, cwd, env, timeout):
        assert "-nograph" in cmd and "-replay" in cmd and "-log" in cmd
        assert os.path.isdir(cwd), cwd
        _write_text(expected, _FAKE_NEW)
        show = expected.replace("\\", "/")
        return 0, ('OPT_NETLIST_DESIGN LibA CellA schematic\n'
                   'OPT_NETLIST_OK "%s"\n' % show), ""
    return runner


def _t_runner_success():
    tmp = tempfile.mkdtemp(prefix="nc_ok_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(state, "LibA", "CellA", "schematic", project=sim_root)
        expected = os.path.join(sim_root, "CellA", "spectre", "schematic",
                                "netlist", "input.scs")
        os.makedirs(os.path.dirname(expected))
        _write_text(expected, _FAKE_OLD)
        cfg = {"state_dir": state, "work_dir": tmp,
               "virtuoso_bin": "virtuoso"}
        report = create_netlist(cfg, runner=_runner_success(expected))
        assert report["status"] == "ok", report
        assert os.path.normpath(report["path"]) == os.path.normpath(
            expected), report["path"]
        assert report["previous"] == expected, report
        assert report["backup"] and os.path.isfile(report["backup"]), report
        assert _read_text(report["backup"]) == _FAKE_OLD
        assert "simulator lang=spectre" in _read_text(expected)
        assert report["expected"] == expected, report["expected"]
        assert report["log"], report
        assert os.path.isdir(os.path.dirname(report["script"])), report
        assert report["wall"] >= 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_status_file():
    tmp = tempfile.mkdtemp(prefix="nc_status_")
    try:
        rate = os.path.join(tmp, "ade")
        os.makedirs(rate)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(rate, "LibA", "CellA", "schematic", project=sim_root)
        expected = os.path.join(sim_root, "CellA", "spectre", "schematic",
                                "netlist", "input.scs")
        os.makedirs(os.path.dirname(expected))
        cfg = {"state_dir": rate, "work_dir": tmp}

        def runner(cmd, cwd, env, timeout):
            script = _read_text(cmd[cmd.index("-replay") + 1])
            match = re.search(r'outfile\("((?:[^"\\]|\\.)*)"\)', script)
            assert match, script
            status = re.sub(r"\\(.)", r"\1", match.group(1))
            _write_text(expected, _FAKE_NEW)
            _write_text(status, 'OPT_NETLIST_OK "%s"\n'
                        % expected.replace("\\", "/"))
            return 0, "", ""

        report = create_netlist(cfg, runner=runner)
        assert report["status"] == "ok", report
        assert os.path.normpath(report["path"]) == os.path.normpath(
            expected), report["path"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_runner_failure_restore():
    tmp = tempfile.mkdtemp(prefix="nc_fail_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(state, "LibA", "CellA", "schematic", project=sim_root)
        expected = os.path.join(sim_root, "CellA", "spectre", "schematic",
                                "netlist", "input.scs")
        os.makedirs(os.path.dirname(expected))
        _write_text(expected, _FAKE_OLD)
        cfg = {"state_dir": state, "work_dir": tmp}

        def runner(cmd, cwd, env, timeout):
            # 模拟 createNetlist 写坏网表后失败
            _write_text(expected, "simulator lang=spectre\n// partial\n")
            return 1, "", ("OSSHNL-514: cannot open lib\n"
                           "ADE-1000: createNetlist failed\n")

        report = create_netlist(cfg, runner=runner)
        assert report["status"] == "fail", report
        assert report["restored"] is True, report
        assert _read_text(expected) == _FAKE_OLD
        assert report["backup"] and os.path.isfile(report["backup"])
        assert any("OSSHNL" in line for line in report["errors"]), report
        assert "createNetlist 失败" in report["message"], report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_runner_failure_no_previous():
    tmp = tempfile.mkdtemp(prefix="nc_fail2_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(state, "LibA", "CellA", "schematic", project=sim_root)
        cfg = {"state_dir": state, "work_dir": tmp}

        def runner(cmd, cwd, env, timeout):
            return 0, "", "OPT_NETLIST_FAIL boom\nWARNING: something odd\n"

        report = create_netlist(cfg, runner=runner)
        assert report["status"] == "fail", report
        assert report["restored"] is False, report
        assert report["previous"] == "" and report["backup"] == "", report
        assert "boom" in report["message"], report
        assert report["warnings"], report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_ok_without_path():
    'errset 包住 nil 时不能拿旧网表冒充新网表。'
    tmp = tempfile.mkdtemp(prefix="nc_nopath_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(state, "LibA", "CellA", "schematic", project=sim_root)
        expected = os.path.join(sim_root, "CellA", "spectre", "schematic",
                                "netlist", "input.scs")
        os.makedirs(os.path.dirname(expected))
        _write_text(expected, _FAKE_OLD)
        cfg = {"state_dir": state, "work_dir": tmp}

        def runner(cmd, cwd, env, timeout):
            return 0, "OPT_NETLIST_OK\n", ""

        report = create_netlist(cfg, runner=runner)
        assert report["status"] == "fail", report
        assert report["restored"] is True, report
        assert _read_text(expected) == _FAKE_OLD
        assert "缺少网表路径" in report["message"], report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_log_errors_cap():
    tmp = tempfile.mkdtemp(prefix="nc_log_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(state, "LibA", "CellA", "schematic", project=sim_root)
        cfg = {"state_dir": state, "work_dir": tmp}

        def runner(cmd, cwd, env, timeout):
            lines = ["OSSHNL-514: fatal entry"]
            lines += ["ERROR: failure number %d" % i for i in range(25)]
            lines.append("WARNING: fallback used")
            return 1, "", "\n".join(lines)

        report = create_netlist(cfg, runner=runner)
        assert report["status"] == "fail"
        assert len(report["errors"]) == 20, len(report["errors"])
        assert report["errors"][0].startswith("OSSHNL"), report["errors"][0]
        assert any("WARNING" in w for w in report["warnings"]), report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_default_runner():
    '真实 subprocess 分支：正常退出与超时（不涉及 Cadence）。'
    env = dict(os.environ)
    rc, out, err = _default_runner(
        [sys.executable, "-c", "print('hello')"], None, env, 30)
    assert rc == 0 and "hello" in out, (rc, out, err)
    rc, out, err = _default_runner(
        [sys.executable, "-c", "import time; time.sleep(2)"], None, env, 0.3)
    assert rc == 124, (rc, out, err)
    assert "Timeout" in err
    assert _resolve_virtuoso({"virtuoso_bin": sys.executable}) == sys.executable


def _t_timeout_runner():
    tmp = tempfile.mkdtemp(prefix="nc_timeout_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(state, "LibA", "CellA", "schematic", project=sim_root)
        cfg = {"state_dir": state, "work_dir": tmp, "netlist_timeout": 1}

        def runner(cmd, cwd, env, timeout):
            assert timeout == 1.0, timeout
            return 124, "", "Timeout after 1.0s"

        report = create_netlist(cfg, runner=runner)
        assert report["status"] == "fail", report
        assert "124" in report["message"], report["message"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_ensure_ok_injected():
    tmp = tempfile.mkdtemp(prefix="nc_ensure2_")
    old_runner = globals()["_default_runner"]
    try:
        sim_root = os.path.join(tmp, "sim")
        cfg = {"auto_netlist": "always", "library": "LibZ", "cell": "CellZ",
               "netlist_view": "config", "sim_root": sim_root,
               "work_dir": tmp}
        expected = expected_netlist_path(read_design(cfg), cfg)
        os.makedirs(os.path.dirname(expected))

        def fake(cmd, cwd, env, timeout):
            _write_text(expected, _FAKE_NEW)
            return 0, 'OPT_NETLIST_OK "%s"\n' % expected.replace("\\", "/"), ""

        # ensure_netlist 无 runner 参数；替换模块级默认 runner，绝不真起 virtuoso
        globals()["_default_runner"] = fake
        report = ensure_netlist(cfg)
        assert report["status"] == "ok", report
        assert os.path.normpath(cfg["netlist"]) == os.path.normpath(
            expected), cfg
        assert cfg["netlist_view"] == "config", cfg
        assert os.path.isfile(cfg["netlist"])
    finally:
        globals()["_default_runner"] = old_runner
        shutil.rmtree(tmp, ignore_errors=True)


def _t_ensure_fallback():
    tmp = tempfile.mkdtemp(prefix="nc_fb_")
    try:
        old = os.path.join(tmp, "old_input.scs")
        _write_text(old, _FAKE_OLD)
        cfg = {"auto_netlist": "always",
               "state_dir": os.path.join(tmp, "missing_state"),
               "netlist": old, "work_dir": tmp}
        report = ensure_netlist(cfg)
        assert report["status"] == "fallback", report
        assert os.path.normpath(report["path"]) == os.path.normpath(old), report
        assert "旧网表" in report["message"], report
        assert cfg["netlist"] == old, cfg
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_ensure_fail_and_skip():
    tmp = tempfile.mkdtemp(prefix="nc_skip_")
    try:
        cfg = {"auto_netlist": "always",
               "state_dir": os.path.join(tmp, "missing_state"),
               "work_dir": tmp}
        report = ensure_netlist(cfg)
        assert report["status"] == "fail", report

        report = ensure_netlist({"auto_netlist": False})
        assert report["status"] == "skipped", report
        assert report.get("reason") == "auto_netlist=false", report

        report = ensure_netlist({"auto_netlist": "off"})
        assert report["status"] == "skipped", report

        report = ensure_netlist({})
        assert report["status"] == "skipped", report
        assert report.get("reason"), report

        # auto 模式下有 library+cell 就执行（注入失败 runner，绝不真起 virtuoso）
        old_runner = globals()["_default_runner"]
        globals()["_default_runner"] = (
            lambda cmd, cwd, env, timeout: (1, "", "OPT_NETLIST_FAIL no tool"))
        try:
            report = ensure_netlist({"library": "L", "cell": "C",
                                     "view": "schematic", "work_dir": tmp})
        finally:
            globals()["_default_runner"] = old_runner
        assert report["status"] == "fail", report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_auto_mode():
    assert _auto_mode(None) == "auto"
    assert _auto_mode("") == "auto"
    assert _auto_mode("auto") == "auto"
    assert _auto_mode(False) == "off"
    assert _auto_mode("false") == "off"
    assert _auto_mode("off") == "off"
    assert _auto_mode(True) == "always"
    assert _auto_mode("true") == "always"
    assert _auto_mode("always") == "always"
    assert _auto_mode(1) == "always"
    assert _auto_mode(0) == "off"
    assert _auto_mode("weird") == "auto"


def _t_merge_testbench():
    old = (
        "simulator lang=spectre\n"
        "parameters fin=36G\n"
        "include \"/pdk/models.lib\" section=tt\n"
        "subckt s a b\nends s\n"
        "sp1 sp ports=[PORT0 PORT1] start=16G stop=56G lin=81\n"
        "ac1 ac start=1G stop=56G dec=10\n"
        "sweep1 sweep param=pin start=-20 stop=10 step=1 {\n"
        "  hb hb fundfreqs=[(fin)]\n"
        "}\n"
        "save PORT1:p\n"
        "include \"/proj/cell.pex.netlist\"\n")
    new = (
        "simulator lang=spectre\n"
        "global 0\n"
        "parameters fin=36G\n"
        "subckt s a b\nends s\n"
        "I0 (a b) s\n"
        "include \"/proj/cell.pex.netlist\"\n")
    merged, tb = merge_testbench(new, old)
    assert sorted(tb["analyses"]) == ["ac", "sp", "sweep"], tb
    assert tb["includes"] == 1, tb
    assert "sp1 sp ports=[PORT0 PORT1]" in merged
    assert "ac1 ac start=1G" in merged
    assert "sweep1 sweep param=pin" in merged
    assert merged.count('include "/proj/cell.pex.netlist"') == 1, merged

    # 语句关键字和名为 sp 的实例都不能算分析；save sp 仍按 save 合并。
    tb2 = _extract_testbench(
        "save sp\nparameters sp\nsubckt sp (a b)\nends sp\n"
        "sp (a b) capacitor c=1p\n")
    assert tb2["analyses"] == [], tb2
    assert tb2["saves"] == ["save sp"], tb2
    # 首 token 即类型的手写形式（hb hb ...）仍然可用。
    assert _analysis_kind("hb hb fundfreqs=[(fin)]") == "hb"

    # 新 deck 已含 ac 时按类型去重，旧 ac 不再插入；其它分析照常合并。
    new_ac = new + "ac ac start=2G stop=3G dec=5\n"
    merged2, tb3 = merge_testbench(new_ac, old)
    assert "ac" not in tb3["analyses"], tb3
    assert "ac1 ac start=1G" not in merged2, merged2
    assert "sp1 sp ports=[PORT0 PORT1]" in merged2
    assert "save PORT1:p" in merged2


def _t_logical_lines_scope():
    'Leading "+" continues; inline subckt is a scope; commented braces ignored.'
    values, _blocks = _top_parameters(
        "simulator lang=spectre\nparameters a=1\n    + b=2\n")
    assert values == {"a": "1", "b": "2"}, values
    merged, changes = carry_over_parameters(
        "simulator lang=spectre\nparameters a b\n",
        "simulator lang=spectre\nparameters a=1\n    + b=2\n")
    assert "parameters a=1 b=2" in merged, merged
    assert sorted(item["name"] for item in changes) == ["a", "b"], changes
    inline = ("simulator lang=spectre\n"
              "parameters w=1u\n"
              "inline subckt foo (a b)\n"
              "parameters Ls=1n\n"
              "ends foo\n")
    values, _blocks = _top_parameters(inline)
    assert values == {"w": "1u"}, values
    # 注释里的未配对 "{" 不能把余下全文并成一条注释语句。
    braces = ("simulator lang=spectre\n"
              "// note { unpaired\n"
              "parameters a=1\n"
              "ac1 ac start=1G stop=2G\n")
    assert _top_parameters(braces)[0] == {"a": "1"}
    tb = _extract_testbench(braces)
    assert [kind for kind, _code in tb["analyses"]] == ["ac"], tb
    # 花括号块内的行注释不能吞掉闭合 "}" 以及块之后的 save/分析。
    commented = ("simulator lang=spectre\n"
                 "sweep1 sweep param=pin {\n"
                 "  // bias\n"
                 "  hb hb fundfreqs=[(fin)]\n"
                 "}\n"
                 "save V(out)\n"
                 "ac1 ac start=1G\n")
    assert _top_parameters(commented)[0] == {}
    tb = _extract_testbench(commented)
    assert [kind for kind, _code in tb["analyses"]] == ["sweep", "ac"], tb
    assert tb["saves"] == ["save V(out)"], tb
    # 引号与块注释里的花括号同样不算块语法。
    quoted = ("simulator lang=spectre\n"
              'parameters p="a{b"\n'
              "ac1 ac start=1G\n")
    assert _top_parameters(quoted)[0] == {"p": '"a{b"'}, _top_parameters(quoted)
    assert [kind for kind, _code in _extract_testbench(quoted)["analyses"]] == ["ac"]
    blockcmt = ("simulator lang=spectre\n"
                "/* { note */\n"
                "parameters a=1\n"
                "ac1 ac start=1G\n")
    assert _top_parameters(blockcmt)[0] == {"a": "1"}
    # inline subckt 里的分析/save 不是顶层测试台语句。
    inline_tb = ("simulator lang=spectre\n"
                 "inline subckt foo (a b)\n"
                 "sp1 sp ports=[a b]\n"
                 "save V(x)\n"
                 "ends foo\n"
                 "ac2 ac start=2G\n")
    tb = _extract_testbench(inline_tb)
    assert [kind for kind, _code in tb["analyses"]] == ["ac"], tb
    assert tb["saves"] == [], tb
    # #/;/* 注释行里的 "{" 同样不能吞掉后续网表（与顶层解析器的注释前缀一致）。
    for prefix in ("#", ";", "*"):
        comment = ("simulator lang=spectre\n"
                   "%s old sweep block { unpaired\n" % prefix +
                   "parameters a=1\n"
                   "save V(out)\n"
                   "ac1 ac start=1G\n")
        assert _top_parameters(comment)[0] == {"a": "1"}, prefix
        tb = _extract_testbench(comment)
        assert [kind for kind, _code in tb["analyses"]] == ["ac"], (prefix, tb)
        assert tb["saves"] == ["save V(out)"], (prefix, tb)


def _t_validate_netlist():
    'Missing, too small and simulator-less decks are rejected with reasons.'
    tmp = tempfile.mkdtemp(prefix="nc_val_")
    try:
        assert "路径为空" in _validate_netlist("")
        assert "文件不存在" in _validate_netlist(os.path.join(tmp, "nope.scs"))
        small = os.path.join(tmp, "small.scs")
        _write_text(small, "x\n")
        assert "文件过小" in _validate_netlist(small)
        bad = os.path.join(tmp, "bad.scs")
        _write_text(bad, "global 0\n" + "// pad\n" * 30)
        assert "simulator lang=spectre" in _validate_netlist(bad)
        good = os.path.join(tmp, "good.scs")
        _write_text(good, "simulator lang=spectre\n" + "// pad\n" * 30)
        assert _validate_netlist(good) == ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_merge_named_write_back():
    'A config-view createNetlist drops analyses; ADE-named ones merge back.'
    tmp = tempfile.mkdtemp(prefix="nc_merge_")
    try:
        state = os.path.join(tmp, "ade")
        os.makedirs(state)
        sim_root = os.path.join(tmp, "sim")
        _fake_ade(state, "LibA", "CellA", "schematic", project=sim_root)
        expected = os.path.join(sim_root, "CellA", "spectre", "schematic",
                                "netlist", "input.scs")
        os.makedirs(os.path.dirname(expected))
        old = "\n".join([
            "simulator lang=spectre",
            'include "/pdk/models.lib" section=tt',
            "parameters L1=1n",
            "ac1 ac start=1G stop=2G dec=10",
            "save V(out)",
            "saveOptions options save=selected",
            "// pad\n" * 20,
            "",
        ])
        _write_text(expected, old)
        new = "\n".join([
            "simulator lang=spectre",
            "global 0",
            "parameters L1=2n",
            "subckt cell (a b)",
            "ends cell",
            "X1 (a b) cell",
            "saveOptions options save=selected",
            "// new pad\n" * 10,
            "",
        ])

        def runner(cmd, cwd, env, timeout):
            _write_text(expected, new)
            return 0, 'OPT_NETLIST_OK "%s"\n' % (
                expected.replace("\\", "/")), ""

        report = create_netlist({"state_dir": state, "work_dir": tmp},
                                runner=runner)
        assert report["status"] == "ok", report
        assert report["testbench_merge"]["analyses"] == ["ac"], report
        text = _read_text(expected)
        assert "ac1 ac start=1G stop=2G dec=10" in text, text
        assert 'include "/pdk/models.lib" section=tt' in text, text
        assert "save V(out)" in text, text
        assert "parameters L1=2n" in text, text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _force_utf8_stdio():
    'locale 把 stdio 留成 ASCII 时重包成 UTF-8（与 optimizer 一致）。'
    try:
        import io as _io
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            buf = getattr(stream, "buffer", None)
            enc = (getattr(stream, "encoding", "") or "").lower()
            if buf is not None and enc not in ("utf-8", "utf8"):
                setattr(sys, name, _io.TextIOWrapper(
                    buf, encoding="utf-8", errors="replace",
                    line_buffering=True))
    except Exception:
        pass


def _selftest():
    _force_utf8_stdio()
    tests = [
        ("ADE_state.info 解析", _t_ade_state),
        ("expected path", _t_expected_path),
        ("IL 脚本协议", _t_build_script),
        ("状态行解析", _t_parse_status),
        ("runner 成功 + 备份", _t_runner_success),
        ("status 文件协议", _t_status_file),
        ("runner 失败 + 恢复", _t_runner_failure_restore),
        ("runner 失败无旧网表", _t_runner_failure_no_previous),
        ("OK 无路径不算成功", _t_ok_without_path),
        ("日志错误抽取上限", _t_log_errors_cap),
        ("默认 runner + 超时", _t_default_runner),
        ("timeout 分支", _t_timeout_runner),
        ("ensure 写回 cfg", _t_ensure_ok_injected),
        ("ensure fallback", _t_ensure_fallback),
        ("ensure fail/skip", _t_ensure_fail_and_skip),
        ("auto_netlist 取值", _t_auto_mode),
        ("测试台合并", _t_merge_testbench),
        ("续行与作用域", _t_logical_lines_scope),
        ("网表校验分支", _t_validate_netlist),
        ("合并命名分析写回", _t_merge_named_write_back),
    ]
    failed = 0
    for name, func in tests:
        try:
            func()
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("ok   %s" % name)
    print("%d/%d tests passed" % (len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


# ===== CLI =====

def _load_cfg(path):
    import json
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise NetlistError("配置文件必须是 JSON 对象：%s" % path)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Virtuoso Optimizer: 优化前重新生成 Virtuoso 网表（需求 A）")
    parser.add_argument("--selftest", action="store_true",
                        help="运行纯 Python 自测（不依赖 Cadence）")
    parser.add_argument("--config", help="optimizer 配置 JSON")
    parser.add_argument("--state-dir", help="覆盖 cfg['state_dir']")
    parser.add_argument("--work-dir", help="覆盖 cfg['work_dir']")
    parser.add_argument("--log-dir", help="脚本/日志/状态文件目录")
    parser.add_argument("--auto", choices=("auto", "always", "off"),
                        help="覆盖 cfg['auto_netlist']")
    parser.add_argument("--json", action="store_true", help="打印完整 report")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.config:
        parser.print_help()
        return 2
    try:
        cfg = _load_cfg(args.config)
    except Exception as exc:
        print("读取配置失败：%s" % exc, file=sys.stderr)
        return 2
    if args.state_dir:
        cfg["state_dir"] = args.state_dir
    if args.work_dir:
        cfg["work_dir"] = args.work_dir
    if args.auto:
        cfg["auto_netlist"] = args.auto
    if args.log_dir:
        report = create_netlist(cfg, log_dir=args.log_dir)
        if report.get("status") == "ok":
            cfg["netlist"] = report.get("path", "")
            cfg["netlist_view"] = (report.get("design") or {}).get(
                "view", "")
    else:
        report = ensure_netlist(cfg)
    import json
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("netlist_create: %s" % report.get("status"))
        if report.get("message"):
            print(report["message"])
        if report.get("path"):
            print("path: %s" % report["path"])
        if report.get("expected"):
            print("expected: %s" % report["expected"])
        if report.get("log"):
            print("log: %s" % report["log"])
        for line in report.get("errors", []):
            print("ERROR %s" % line)
        for line in report.get("warnings", []):
            print("WARN  %s" % line)
    return 0 if report.get("status") in ("ok", "skipped", "fallback") else 1


if __name__ == "__main__":
    sys.exit(main())