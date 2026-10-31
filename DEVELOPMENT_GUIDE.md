# Virtuoso Optimizer 开发导览

> 基线：1.5.1。定位用符号名，不写行号（压缩后行号随提交漂移，直接 grep 符号）。
> 内容来自 AST 大纲、逐段精读和运行时实测（--help / --mock / patch_netlist）。

## 0. 一句话架构

`config JSON → Transform（变量投影到 [0,1]）→ build_optimizer（策略实例）→ Optimization.run 主循环
→ RealRunner（改网表 → 直跑 spectre → SKILL 解析 psf 回写 result.txt）→ report.json`

没有 ADE 会话、没有 Cadence API 常驻进程：每轮就是「改一行网表参数 + 起一个 spectre 进程」。

## 1. 文件分工

| 文件 | 行数 | 职责 | 主入口 |
|---|---|---|---|
| `optimizer.py` | 3683 | CLI、配置、Target 打分、Transform、Runner、主循环、报告 | `main` |
| `algorithm.py` | 2958 | 14 种搜索策略 + 注册表 + 算法说明文案 | `create` |
| `netlist_params.py` | 1460 | 层次网表解析与参数改写 | `patch_netlist` |
| `gui.py` | 4187 | Tk 图形界面（11 个页面） | `run_gui` |
| `selftest.py` | 2276 | 回归测试套件（179 个用例） | `--selftest` 装载 |
| `librf.il` | 3320 | SKILL 侧：建版图、eval serve（解析 psf 写 result.txt） | `_ensure_eval` 启动 |
| `librf_opt.py` | 4600 | 版图器件生成 / EMX 迭代（工艺侧） | `register` |
| `em_align.py` / `em_opt.py` / `em_bridge.py` | 1937 / 974 / 971 | 两阶段 EM 对齐、EM 优化、器件桥 | `run_em_align` |
| `netlist_create.py` / `proc_cross_section.py` | 1559 / 671 | 网表重生成、工艺剖面 | `create_netlist` / `build_cross_section` |

纯标准库，零依赖；`matplotlib` 只用于可选收敛图（`plot_convergence`），导入失败静默跳过，
不要加硬依赖。

## 2. CLI 入口

`main()` 先跑两个防御动作，再看参数：

- `_force_utf8_stdio()`：保证中文不炸 CP936
- `_warn_ascii_filesystem()`：文件系统编码是 ascii 时提前警告

argparse 参数：`config`（位置参数）、`--mock`、`--max-iter`、`--dump-state`、`--selftest`、
`--reconstruct RUN_DIR [INDEX [DEST]]`、`--gui`、`--gui-smoke`、`--em-align STAGE1_DIR`、
`--em-curve PATH`、`--em-netlist PATH`、`--em-out DIR`、`--em-tol`。

分派顺序（先命中先返回）：

| 参数 | 调用 |
|---|---|
| `--selftest` | `run_selftests()` → subprocess 跑 `selftest.py` |
| `--reconstruct` | `_cli_reconstruct(argv)` |
| `--gui` / `--gui-smoke` | `from gui import run_gui` |
| 缺 `config` | `ap.error` |
| `--dump-state` | `dump_state(cfg)`（只解析 ADE 状态，不仿真） |
| `--em-align` | `run_em_align(...)` → `print(json.dumps(report, indent=2))` |
| 默认 | `run_optimization(cfg, mock, max_iter)` |

### 默认路径 run_optimization

签名：`def run_optimization(cfg, mock=False, max_iter=None, on_event=None, stop_check=None, resume=None)`

1. `resume` 为 None 时读配置里的 `resume` 标志（`_resume_flag`）
2. `prepare_netlist(cfg, mock=mock)`：按 `auto_netlist` 决定是否调 Virtuoso 重生成网表
3. 非 mock 时 `preflight_netlist(cfg)`：启动即校验，网表里没有目标需要的分析语句就当场报错
4. `Optimization(cfg, ...)` → `opt.run()`
5. `finally`：`stop_all_runners()` 杀干净 virtuoso、`opt.logger.finalize()`
6. `opt.report(reason)` → 补 `netlist_create` → `save_best_curves` → `plot_convergence` → `save_report`

改动约定：`print(out)` 的输出就是 CLI 结果，报告 JSON 只由 `report()` 生成；GUI 走同一函数
（`gui.py` 调 `run_optimization`，用 `on_event` / `stop_check` 回调驱动进度条）。

## 3. 核心数据流与三个契约

### 3.1 Transform（optimizer.py）

`Transform.__init__(variables, linking)`：变量按 `linking` 关系解析依赖顺序（`_resolve_order`）；
`to_real(z)` / `to_unit(values)` 做物理量与 [0,1] 的归一。

所有算法只在单位立方体里搜索，物理边界由这一层负责。新增 `scale` / `integer` 约束就改这里。

### 3.2 策略契约（optimizer.py SpaceAdapter）

`build_optimizer(strategy, transform, x0_values, all_names, seed=0, n_obj=1, n_constr=0, budget=None, **opts)`：

```python
unit_bounds = [(0.0, 1.0)] * transform.n_free
z0 = transform.to_unit(x0_values)
return SpaceAdapter(create(strategy, all_names, unit_bounds, x0=z0, ...), transform, all_names)
```

`SpaceAdapter` 把「批量 ask/tell」降级成一次一个点：
`ask()` 返回物理量列表（顺序即 `all_names`，顺序错一次就全错）；
`tell(value, F, G)` 固定写成 `self.inner.tell([z], [f], [list(G)])`。
历史坑：旧版把 `G` 直传会让约束引擎按元组解包崩掉。

### 3.3 打分（optimizer.py evaluate_roles）

- `mode="scalar"`：`total = Σ max(e, 0) * weight`，`met = all(e <= 0)`
- `mode="spec"` / `"pareto"`：`role="objective"` → F，`constraint` 与 `error` 都进 G
- `met` 是对全部 target 的判定，不是对 total 的判定

单个 target 的误差在 `Target.error`：有 `curve_op` 走 `_curve_error`（曲线逐点最差违背），
否则走 `_point_error`（`raw / tol`，`op` 决定 raw 取哪个方向）。

## 4. 策略注册机制

### 4.1 注册表本体（algorithm.py）

```python
_REGISTRY = {}

def register(name, cls):
    _REGISTRY[name] = cls
    return cls                    # 返回 cls，所以可以当装饰器用

def available():
    return sorted(_REGISTRY)

def create(name, names, bounds, x0=None, n_obj=1, n_constr=0,
           seed=0, budget=None, **opts):
    if name not in _REGISTRY:
        raise ValueError("unknown strategy %r (have: %s)" % (name, ", ".join(available())))
    return _REGISTRY[name](names, bounds, x0=x0, ...)
```

注册即被发现：没有硬编码策略列表。GUI 下拉、`Settings.allowed_strategies()` 都跟着
`available()` 走。类的 `name` 属性（如 `RandomSearch.name = "random"`）只是文档，
真正生效的是 `register()` 的第一个参数。

### 4.2 十四个策略

| 名字 | 类 | 类别 |
|---|---|---|
| `random` | `RandomSearch` | 基线 |
| `grid` | `GridSearch` | 基线 |
| `simplex` | `NelderMead` | 局部 |
| `coordinate` | `CoordinateSearch` | 局部 |
| `global` | `DifferentialEvolution` | 全局 |
| `de` | `AdaptiveDe` | 全局 |
| `lshade` | `Lshade` | 全局 |
| `cmaes` | `CmaEs` | 全局 |
| `cmaes_ipop` | `CmaEsIpop` | 全局 |
| `tpe` | `TpeOptimizer` | 贝叶斯 |
| `nsga2` | `Nsga2` | 多目标 |
| `weighted` | `WeightedScalar` | 包装器 |
| `worstspec` | `WorstspecScalar` | 包装器 |
| `portfolio` | `Portfolio` | 元算法（UCB 调度） |

运行时 `available()` 返回这 14 个名字。

### 4.3 基类契约（algorithm.py AlgorithmBase）

```python
class AlgorithmBase(object):
    name = "base"
    def __init__(self, names, bounds, x0=None, n_obj=1, n_constr=0,
                 seed=0, budget=None, **opts)
    def ask(self, n=1)             # 返回 [[x...], ...]，最多 n 个点
    def tell(self, X, F, G=None)   # 按 ask 的顺序回报目标
    def best(self)                 # (best_x, best_y)
    def snapshot(self)             # JSON 化状态（续跑用）
    def restore(self, state)
    def restart(self, spread)      # 耐心耗尽时的重启
    def simplex_size(self)         # 随机重启半径试探用
    def seed_history(self, points) # 历史热启动
```

辅助：`constraint_violation(g)`、`deb_better(fa, ga, fb, gb)`、`scalar_of(f)`。
RNG 状态用 `_rng_pack` / `_rng_unpack` JSON 化，续跑必需。

### 4.4 标量 vs 多目标的接线（optimizer.py）

```python
_SCALAR_ONLY = ("random", "grid", "simplex", "global", "de",
                "lshade", "cmaes", "cmaes_ipop", "tpe")
```

`build_optimizer` 里：策略落在 `_SCALAR_ONLY` 且 `n_obj > 1` → 强制 `n_obj = 1`。
`weighted` / `worstspec` / `nsga2` / `portfolio` 不在里面，因为它们自己处理多目标
（前两个在自己的 `tell` 里标量化，后两个原生多目标）。新写的策略如果自己会标量化，
不要加进这个元组，否则它拿不到 F 向量。

## 5. 网表改写流程

### 5.1 为什么需要

`config` 视图的网表是层次的：变量可能出现在三处——顶层 `parameters` 行、`subckt` 头部的
默认值、实例上的覆盖参数。只改顶层会出现「改了没用」的静默错误，这是本模块存在的唯一理由。

### 5.2 解析管线（netlist_params.py）

```
文本
 └─ _parse(text)        → (masked, lines, starts, stmts)   逐语句切分 + 记录每个 token 的字符偏移
     └─ _analyze(text)  → {masked, lines, starts, stmts, scopes, top_stmts, instances, includes}
         └─ patch_netlist(text, values, fixed=None, options=None) → (new_text, report)
             └─ _apply_edits   按偏移从后往前替换（所以不会互相错位）
```

关键内部件：`_mask_block_comments`（注释掩码，保证偏移不变）、`_tokenize`、
`_param_entries(stmt, start, allow_bare)`、`_instance_info`、`_include_info`、
`_balance`（括号配平，判断语句是否写完）。

兼容老 API（只动顶层，新代码不要用）：`parse_netlist_parameters`、`rewrite_netlist_parameters`、
`inspect_netlist`（调试用：直接看 scopes/instances/top_params）。

### 5.3 patch_netlist 的四条规则

1. 顶层 `parameters`：找不到就补一条 `name=value`；裸声明（只有名字）补成 `name=value`
2. 子电路 `subckt` 头部默认值：裸声明跳过，让顶层值穿透；改了会记进 `shadowed`
3. 字面量实例覆盖：`X1 out 0 ind wd=11u` 这种；`old_value == key` 的自引用跳过，非数值跳过并告警
4. 够不到：进 `missing`，同时 `ok=False`，上游据此直接判该轮失败

缺的顶层名字追加到已有 `parameters` 行尾；没有 `parameters` 语句就新建一条。

### 5.4 report 与 options

```python
report = {
  "ok": bool,                        # 没有 missing 才为 True
  "applied":   {name: {"value": str, "locations": ["top"|"sub:ind"|"inst:X1", ...]}},
  "missing":   [name, ...],
  "shadowed":  [{"name","scope","old"}],              # 子电路默认值被改
  "instance_overrides": [{"name","instance","old","new","line"}],
  "warnings":  [str, ...],
  "counts":    {"top": int, "subckt": int, "instance": int},
}
```

`options`：`patch_subckts`（默认 True）、`patch_instances`（默认 True）、
`scope_map`（`{name: ["top"|"sub:ind"|"inst:X1", ...]}`，白名单限定位置）。

实测（网表含 `parameters w=10u` + `subckt ind p n wd=10u` + `X1 out 0 ind wd=11u`）：

- 把 `wd` 改成 `13u` → `counts={'top':1,'subckt':1,'instance':1}`，三处同步；
  `shadowed=[{"name":"wd","scope":"ind","old":"10u"}]`；
  `instance_overrides=[{"name":"wd","instance":"X1","old":"11u","new":"13u","line":5}]`
- `patch_subckts=False` + `patch_instances=False` → 只改顶层，两条 warning 说明哪处没改
- `scope_map={"wd": ["inst:X1"]}` → `counts={'top':0,'subckt':0,'instance':1}`

### 5.5 optimizer.py 侧的胶水与降级

`_netlist_params_module()` 导入失败的兜底：返回 None 时走
`_legacy_parse_netlist_parameters` / `_legacy_rewrite_netlist_parameters`（只改顶层）。也就是说 netlist_params.py 可以独立演化，但一旦改崩，上层会静默退化成
「只改顶层」——排查「改了变量没生效」时先看这里有没有退化。

其它网表工具：`netlist_analyses(text)`（静态找出网表有哪些分析语句，`_ANALYSIS_KEYWORDS`
36 种，`_NETLIST_DIRECTIVES` 排除 `save/options` 这类指令）、`resolve_netlist_path(cfg)`
（显式 `netlist` 优先，否则 `sim_root/library/cell` 下按 mtime 取最新 `input.scs`）、
`preflight_netlist(cfg)`。

### 5.6 每轮仿真闭环（RealRunner._run_fast）

1. `_load_deck`：整份网表只读一次，`self.deck = (text, params)`
2. `_ensure_eval`：起 `virtuoso -nograph -log <queue>/eval_serve.log -replay <skill_dir>/librf.il`，
   env 注入 `OPT_QUEUE` 与 `OPT_OUTPUTS_STATE`；`outputs.state` 缺失直接返回 False（这轮算失败）
3. `merged = 模板参数 + fixed + 本轮 values` → `mod.patch_netlist(...)`，report 写入 `iter_dir/netlist_patch.json`
4. 写 `iter_dir/input.scs`（覆盖前先 `_archive_aside` 把旧 `result.txt` 存档）
5. `subprocess.Popen([spectre, "input.scs", "-raw", "psf"], cwd=iter_dir, start_new_session=True)`，
   超时 `sim_timeout`（默认 3600）
6. `_compact_after_spectre` 压日志；只看 psf 目录是否为空判成败（退出码不可信：
   理想变压器网表故意 DC 不收敛，但 sp 结果照样写出来了）
7. 往 `queue_dir/eval_req.txt` 原子写 `PSFDIR <iter>/psf` + `RESULT <iter>/result.txt`，
   SKILL 侧（`librf.il` 的 eval serve）解析 psf 后写出带 `STATUS` 行的 `result.txt`；
   `_result_ready` 判这行是否出现，`_read_result` 解析曲线

## 6. 扩展点清单

### E1 新增一种搜索策略（最常见）

1. 在 `algorithm.py` 写类，继承 `AlgorithmBase`，实现 `ask/tell/best`，
   概率性算法再实现 `snapshot/restore`（否则续跑会退化成随机重启）
2. 类定义之后紧跟一行 `register("myname", MyClass)`
3. 若要出现在 GUI 的「算法说明」里：`DESCRIPTIONS` 加条目（`title/short/how/when/pro/con/hist`，中文），
   `help_text()` 会自动拼进文档
4. 若它自己会标量化多目标：不要加进 `_SCALAR_ONLY`
5. 跑 `python optimizer.py --selftest`：有测试会遍历 `available()`（179 个用例里覆盖了策略工厂）

### E2 新增一个 metric

1. `Target.__init__` 的 `metric` 白名单加上名字（不加就直接 ValueError）
2. `Target.metric_value` 加分支（`curves` 结构：`{output: {"x": [...], "y": [...]}}`）
3. 若需要频点参数，仿照 `value_at` 的 `freq` 校验
4. 补测试到 `selftest.py`；GUI 目标页的下拉在 `gui.py` 的目标页里另有一份硬编码列表

### E3 新增 op / role

`Target.__init__` 的 op 元组与 role 元组各加一个值，然后在 `Target.error` / `evaluate_roles`
里处理语义——两处都要改，漏一处就是静默错判。

### E4 新增一个配置键

配置是裸 dict，没有 schema 类：`load_config` 只做路径归一化（`state_dir` 相对路径转绝对）。
新键的消费点直接用 `cfg.get("newkey", default)`。要同时改：

- `Settings.__init__`（带校验的走这里，非法值当场抛）
- 文档：`README.md` 的「任务配置」表
- GUI：对应页面的表单控件（`gui.py` 里各页 `build_xxx`）
- 若是路径类，考虑也加进 `load_config` 的归一化循环

### E5 新增 CLI 子命令

`main()` 里加一个 `ap.add_argument`，在分派链里选位置插入。约定：子命令函数打印结果或
返回退出码，不要在这里做业务逻辑；能复用 `run_optimization` 就复用。

### E6 新增网表改写规则

改 `patch_netlist` 的四段之一，并同步 `report` 的 `counts`（新增 `counts` 键会让老断言失败）。
两条铁律：只按 `_parse` 记录的偏移改（不要正则重排文本）、改完 `_apply_edits` 从后往前
（从前往后会让后面的偏移全错位）。

### E7 新增报告字段

`Optimization.report()` 里加，`run_optimization` 的 finally 之后会原样写进 `report.json`。
GUI 结果页读的是同一份 dict（`gui.py` 的 report 展示处），字段名两边都要对。

### E8 新增 GUI 页面

`gui.py` 的页面是 `(标题, 构造函数)` 列表驱动，`run_gui` 装配。长任务不准阻塞主线程：
走 `on_event` / `stop_check` 回调 + `queue.Queue`（参考 `Writer` 那段）。

## 7. 回归与验证（改代码后必须做的三件事）

1. 单元回归：`python optimizer.py --selftest` → `Ran 179 tests ... OK`
2. 流程回归：`python optimizer.py <task.json> --mock --max-iter 3`
   没有 Cadence 也能跑完主循环，看 `report.json` 里 `stop_reason`、`best_errors`、`summary` 是否合理
3. 改网表逻辑时：拿 `inspect_netlist` 直接看解析结果，再用 `patch_netlist` 跑三组
   （默认 / 关掉子电路 / `scope_map`）对照 `counts` 与 `warnings`

`selftest.py` 是 `unittest` 套件，由 `run_selftests` 用 `subprocess` 起子进程跑
（隔离 `sys.path` 与 stdout），所以新测试加在 `selftest.py`，不要塞进 `optimizer.py`。

## 8. 符号速查

| 你要找的 | 符号 |
|---|---|
| CLI 总入口 | `optimizer.main` |
| 默认跑一次的完整流程 | `run_optimization` |
| 主循环 | `Optimization.run` |
| 单轮仿真 | `RealRunner._run_fast` |
| 打分 | `evaluate_roles` / `Target.error` |
| 变量归一化 | `Transform` |
| 策略工厂 | `build_optimizer` |
| 策略注册表 | `algorithm.register` / `available` / `create` |
| 算法说明文案 | `DESCRIPTIONS` / `help_text` |
| 网表改写核心 | `netlist_params.patch_netlist` |
| 网表解析树 | `_parse` / `_analyze` |
| GUI 入口 | `gui.run_gui` |
| 回归测试 | `run_selftests` → `selftest.py` |

## 9. SKILL 侧契约（librf.il，注释已压缩进本节）

### 9.1 一个文件、两种进程

- `virtuoso -replay librf.il`：只有 `OPT_QUEUE` 已设置时才进入 eval-serve 循环。
- `dbAccess load(librf.il)`：driver 调 `librfRun` / `librfMakeViews` / `librfEmxViews`，不进入循环。
- 顶层只允许普通赋值（`var()` 在此环境不建运行时绑定）；`errset` 只收一个表达式；禁用变量名 `t`。

### 9.2 eval-serve 协议

- 必需环境变量：`OPT_QUEUE`、`OPT_OUTPUTS_STATE`；缺失时写 `error.log` 并 `exit()`。
- 循环：`<queue>/STOP` → 退出；`eval_req.txt` → 处理并 `rm -f`；否则 `sleep 0.2`。
- 请求行：`PSFDIR <psf 目录>` / `RESULT <结果文件>`。
- 结果行序：`CURVE_BEGIN name` → `"x y"`（`%.12g`）→ `CURVE_END`；`SCALAR name v`；
  `MSG SKIP name type t` / `MSG NO_EXPR name` / `MSG EVAL_FAIL name expr e`；
  `STATUS ok|fail`；仅 fail 后追加 `MSG SKILL_ERROR_SEE_LOG`。
- 求值结果名 `sp/hb/ac/dc/tran/noise/pss` 逐个尝试，上次成功者优先；`db()` 经 `subst()`
  映射到 OCEAN `db20()`；波形→CURVE、纯数→SCALAR、单点 family 解包，其余 SKIP。
  缺结果类型是正常回退，保持静默。

### 9.3 版图入口与几何约定

- `librfRun(req)`：`req = list(nil 'type "ind" 'lib "LIBRF" 'cell ... 'result "/abs/res.txt")`；
  缺 `type/cell/result` 立即写 fail，不建库不画图。
- 结果行：`STATUS/CELL/BBOX/PORT/LAYERS/WARN/ERR`，末尾 `exit()`；stdout `LIBRF_EXIT=0|1`。
- 单位 µm 浮点，落盘对齐 1 nm（`librfSnap`）；段角只允许 0/45/90。
- 层名一律经 `librfLyr(role)` 从 `tech_profile.json` 的 `layers` 解析；
  `librfDrc` 的 `drc.via.{cut,spacing,enclosure}` 与 `drc.{min_w,max_w,min_s}` 由 driver
  经 req 注入，缺项回落内建 RFCMOS 默认（cut/space/encl = 0.5/0.5/0.2，min_w 1.0，max_w 30，min_s 1.0）。
- `IC618 cv->figs` 恒空，必须用 `cv->shapes`；只查 `drawing` 目的，跳过 pin/label。

### 9.4 真 pin / label 配方（参数顺序勿改，2026-10-16 VM 锁定）

```
net  = dbCreateNet(cv name) 或 dbFindNetByName 兜底   ; 已存在返回 nil
term = dbCreateTerm(net name "inputOutput")           ; 3 参，第 1 参是 net
fig  = dbCreateRect(cv list(metal "pin") tipBox)      ; 可见 pin 标记
pin  = dbCreatePin(net figOrNil name)                 ; fig 会从 net 移到 pin
lab  = dbCreateLabel(cv list(txt "drawing") pt text just orient font height)  ; 8 参
```

- TXT 用 `drawing` 目的，strmout 后仍是 `/5` 文字层（EMX 兼容）。
- 端头 label 打在金属尖端内侧 1.5 µm：EMX label-snap 4 µm，放端帽中心会距边 5 µm。
- `makeBox/makePoint` 不可用；裸圆括号是函数调用。

### 9.5 views 生成 API 配方（锁定）

1. `dbCreateInst(cv master name point orient)`：`master` 必须是 cellview 对象
   （`dbOpenCellViewByType(lib cell view "schematicSymbol" "r")`），传 lib 字符串会报错。
2. `dbCreateProp(obj name type value)` 4 参，`type` 为 `"string"/"int"/"boolean"`。
3. `dbCreateInstTerm(net inst term)`：net 在前，term 取自 `inst->master`；iopin term 名 `"iopin"`，gnd 为 `"gnd!"`。
4. `dbAddFigToNet(fig net)`：fig 在前。
5. symbol 合法层：`("border"/"annotate"/"pin" "drawing")`；schematic wire 用 `"wire" "drawing"`。
6. `nport` 终端 `p1..pN` + `m1..mN`（`m*` 接 GND）；`p` 设 int 后 master 自动参数化。
7. 覆盖重跑：删 `cv->figs / instances / nets` 后重建；`dbDeleteObject` 三者都可删。

### 9.6 EMX create-view（`librfEmxViews`）

- 入口参数：`lib cell snp ports kind result libdir`；terminals = sNp 列序 + 末位 `GND`，方向全 `inputOutput`。
- 流程：加载 EMX interface（`emxconfig.il` → `emxform.ils`/`makeschsym.ils`，尾部 GUI 报错可吞）
  → SKILL++ `define` 补丁屏蔽 `hiDisplayAppDBox`/`schCheck` → `EMX_make_n_port_symbol`、
  `EMX_make_n_port_schematic_no_dev` → `dbCopyCellView` 为 `symbol`/`schematic`。
- `kind` 命中 `makeschsym` 时用 `EMX_make_<type>_symbol` 美化 symbol；端口数不匹配则 WARN 回退 nport box。
- `dataFile` 直接用 sNp 绝对路径（不拷 text cellview）；`*.cdslck*` 被占时整体 fail，不写任何数据。

### 9.7 lri 电感引擎不变量

- `ring0` = 45° 切角矩形；第 k 圈 = ring0 的精确内缩 `k*pitch` ⇒ 间距恒 `s`、bbox 恒 `W×H`、角度 ∈ {0,45,90}。
- `diff`：B 臂必须是 A 臂的镜像而非 180° 旋转拷贝；旋转会让两臂电流反向，互感为负。
- 两臂圈间径向步进必须走下层 underpass，否则同层短路；中心桥走 L 形走廊，不能走对角线。
- `f8`：两内端经原点交叉，先把 y 走到 0 再横穿，保证桥全为 0/90 段。
- `sym`：总圈数 `N = 2*fix(n/2)+1`（就近奇数）；换圈 flush 端必须 `capStart/capEnd=nil`，
  满宽方端帽会同时撞另一臂斜线与外圈切角。
- `seam` 表单用 `S/N/E/W`，内核用 `bottom/top/left/right`；两种写法都必须接受，否则 API 调用会静默退化为 bottom。
- `tangle` 出口点必须在包围盒外，否则 p02 同层 stub 会从内部穿过绕组短路。
- 拉长 `ex/ey` 时，underpass 外端落点相对「拉长后实际轮廓」定位，禁止 `dout/2+ex/2` 半移参考系。
- 内孔闭合告警只在确实容纳不下请求匝数时发出（`nr` 本就多预留 2 圈）。

## 10. 传输工具链（tools/）

四个脚本只用于交付，不进入运行时：

| 脚本 | 作用 |
|---|---|
| `make_kit.py` | `git ls-files` 打 tar，xz/gz 压缩后 base64（76 列）切片，每片一个 heredoc 脚本；`--exclude` 接受 glob |
| `make_onepaste.py` | 把逐片 sha256、整包 sha256 与解包命令追加到最后一片，粘贴次数等于片数 |
| `verify_kit.py` | 校验安装脚本纯 ASCII、单片不超过上限、分片与整包 sha256 一致 |
| `make_file_kit.py` | 按文件生成 gzip+base64 落盘脚本，需要单文件复制时用 |

契约（改工具时必须保持）：

- 安装脚本纯 ASCII；正文可容忍 CRLF（解码链 `tr -d '\r\n '`），在文本编辑器里粘贴保存后可直接 `sh` 执行。
- 每片自带该片 sha256，最后一片额外校验整包 sha256 并解包到 `optimizer-<版本>/`。
- 单片不超过 200000 字符（RealVNC 剪贴板上限 256 kB）。全量包 xz 2 片、gz 3 片；核心包 gz 1 片。
- 每次发版重新出包并跑 `verify_kit.py`。

全量包与核心包出包命令：

```sh
python3 tools/make_kit.py --repo . --out dist/kit --limit 200000 --format xz
python3 tools/make_onepaste.py --kit dist/kit --ext xz
python3 tools/verify_kit.py dist/kit

python3 tools/make_kit.py --repo . --out dist/core --limit 200000 --format gz \
  --exclude "tools/*,README.md,CHANGELOG.md,DEVELOPMENT_GUIDE.md,.gitignore,selftest.py,test_proc_cross_section.py,em_align.py,em_opt.py,em_bridge.py,librf.il,librf_opt.py,proc_cross_section.py"
python3 tools/make_onepaste.py --kit dist/core --ext gz
python3 tools/verify_kit.py dist/core
```
