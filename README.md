# Helix

### A Virtuoso Optimizer

*Parameter optimization and layout-aware EM for Cadence Virtuoso / spectre.*

`Version 1.0.0 · MIT · Python 3.6+`

## Overview

Helix: A Virtuoso Optimizer 把电路参数优化与版图电磁验证放进同一条迭代链：编辑
变量与目标 → 直连 spectre 迭代仿真 → 实时观察收敛、曲线与变量 → 输出最优组合。
版图侧按工艺角色解析层名，生成电感、传输线、变压器与耦合线，可接 EMX 做电磁提取
与几何迭代。运行由 ADE L 状态驱动，不依赖 Maestro；核心为纯 Python 3.6+ 标准库，
`matplotlib` 仅用于可选的收敛图。电路侧（optimizer / algorithm / gui /
selftest）、版图侧（librf_opt / librf.il）、EM 融合（em_bridge / em_opt）与
工艺配置（tech_profile）共用一套 run 目录与日志格式。

## Highlights

- 直连 spectre 迭代，只改写网表顶层 `parameters` 行，无 Maestro 依赖。
- 13 个注册搜索策略，覆盖局部、全局、贝叶斯与多目标。
- 标量 / 规格 / Pareto 三种目标模式，可混用曲线目标与带内指标。
- lri 电感几何内核 v2：每圈为 ring0 的精确内缩，间距恒为 `s`、bbox 恒为 `W×H`。
- 版图与电路联合迭代：几何 → 版图 → strmout → EMX → 电路仿真，同几何点复用缓存。
- 工艺剖面解析 `.proc` + layermap，生成金属/介质/VIA 横截面并做堆叠校验。

## Quick start

```sh
# 图形界面
python3 optimizer.py --gui

# 真实运行（需 Cadence / spectre license）；mock 运行无需 license
python3 optimizer.py task.json
python3 optimizer.py task.json --mock --max-iter 20
python3 optimizer.py task.json --dump-state     # 只解析 ADE 状态，不仿真

# 回归测试、GUI 无头冒烟与模块自测
python3 optimizer.py --selftest
python3 optimizer.py --gui-smoke
python3 librf_opt.py --selftest                 # 版图 / EM / 工艺配置
python3 em_opt.py --selftest                    # EM 优化器 mock 自测
python3 -m unittest test_proc_cross_section     # 工艺剖面解析与校验

# 从 compact run 目录重建完整网表（index 从 0 起）
python3 optimizer.py --reconstruct RUN_DIR [INDEX [DEST]]
```

GUI 流程：设计页导入 ADE L 变量与输出 → 变量页区分优化变量与固定变量 → 目标页
定义规格 → 设置页选策略与预算 → 菜单「运行-开始」→ 进度页看收敛、变量柱状图与
动态曲线 → 结果页读最终报告。工具栏「续跑」勾选即沿用输出目录；不勾则新建 run
目录，旧数据原地保留。「版图 EM」页负责版图生成、EMX 仿真与几何迭代，「EM 融合」
页实时展示库命中情况，工艺页负责工艺库的构建与预检。

## Configuration

任务为 UTF-8 JSON；GUI 可新建/保存，CLI 直接读取。示例：

```json
{
  "name": "S21 flatness", "library": "MyLib", "cell": "test_cs_driver",
  "view": "schematic", "state": "spectre_state1",
  "state_dir": "/path/to/spectre_state1",
  "analyses": "sp", "save_raw": 2, "log_mode": "compact",
  "sim_timeout": 1800, "output_dir": "logs/test_cs_driver/run_1",
  "variables": [
    {"name": "L1", "init": "250p", "min": "150p", "max": "350p"},
    {"name": "k0", "init": "0.6", "min": "0.4", "max": "0.9"}
  ],
  "fixed_variables": [{"name": "vdd", "value": "1.2"}],
  "targets": [{"output": "S21 dB20", "metric": "band_min",
    "band": ["24G", "44G"], "op": ">=", "goal": 3, "tol": 0.5}],
  "settings": {"max_iter": 100, "patience": 10, "improve_rel": 0.001,
    "fail_limit": 5, "max_restarts": 2, "seed": 0,
    "strategy": "simplex", "mode": "scalar"}
}
```

- `output_dir`：CLI 直接作为 run 目录，同一目录再次运行会读取已有记录续跑。
  GUI 留空或为默认 `logs/run` 时，每次新建 `logs/<circuit>/run_<时间戳>`。
- `variables[]`：`name / init / min / max`，支持工程计数法（`250p`、`1.5G`、
  `3meg`）；可选 `scale`（`auto` / `linear` / `log`，`auto` 在跨度 ≥10 倍且
  `min>0` 时取对数）与 `integer: true`。顶层 `linking` 可绑定变量：
  `[{"target": "L2", "source": "L1", "mode": "ratio", "factor": 0.8}]`。
- `fixed_variables[]`：不参与搜索，值原样写入每轮网表。
- `targets[]`：`output` 为 ADE L 输出名；`metric` 取 `scalar` / `band_min` /
  `band_max` / `band_flatness` / `band_mean` / `value_at`（需 `freq`）；`band`
  为 `[lo, hi]`；`op` 取 `>=` / `<=` / `==` / `range`（`range` 需 `goal2`）；
  `tol`、`weight`、`role`（`error` / `constraint` / `objective`，默认 `error`）。
  曲线目标用 `curve_op`（`above` / `below` / `match`）+
  `limit: {"x": [...], "y": [...]}`。
- `settings`：`max_iter`、`patience`、`improve_rel`、`fail_limit`、
  `max_restarts`、`seed`、`strategy`、`mode`；
  `portfolio: {"members": ["cmaes_ipop", "tpe"], "ucb_c": 1.0}`；
  `init_points`：CSV 路径，每行一组变量值，作为先验点先真实仿真。
- `save_raw`：`0` 提取后归档 psf；`1` 每轮保留；`2` 只保留首轮、每次新最优、
  失败与末轮。`log_mode`：`compact`（默认）或 `full`。`env` 覆盖仿真子进程
  环境变量；`path_append` 追加 `PATH`；`spectre_bin` / `virtuoso_bin` 留空即
  现场探查；`netlist` 留空则按 `sim_root` 查找网表。

## Search strategies

| 策略 | 说明 | 策略 | 说明 |
|---|---|---|---|
| `simplex` | 有界 Nelder-Mead 局部搜索（默认） | `global` | 全局差分进化 |
| `de` | 自适应差分进化（SHADE 简化） | `lshade` | L-SHADE（成功历史 DE） |
| `cmaes` | CMA-ES 协方差自适应 | `cmaes_ipop` | CMA-ES + IPOP 重启 |
| `tpe` | 树形 Parzen 估计器（贝叶斯） | `nsga2` | NSGA-II 多目标 Pareto |
| `weighted` | 加权标量化 → 内层单目标 | `worstspec` | 最差规格裕度标量化 |
| `portfolio` | 多算法线 UCB 竞跑 | `random` / `grid` | 均匀随机 / 网格扫描 |

选型速查与详细说明在设置页右侧「算法说明」，数据来自 `algorithm.DESCRIPTIONS`
与 `help_text()`；`portfolio.members` 指定成员，`ucb_c` 控制探索强度。

## Runtime model

启动时读取一次网表快照；每轮只改写顶层 `parameters` 行（固定变量 + 优化变量），
直接运行 spectre，再由 `librf.il` 的求值服务提取输出曲线。第一轮包含求值服务
冷启动。优化期间可开 Virtuoso / ADE L 查看，但不要修改原理图、变量或输出（快照
已缓存）。

失败分类：INFRA（license、网表、求值服务、工具找不到）原地免费重试一次，重试
成功不记失败；重试仍失败、DNC 或超时均计一次失败并累加惩罚，连续失败达
`fail_limit` 停止。单轮目标求值失败以 `BIG_ERROR=1e6` 惩罚并继续。停止原因：
`max_iterations`、`targets_met`、`sim_failures`、`no_improvement`、
`no_feasible_solution`、`stopped`。

`settings.mode`：`scalar`（默认，所有目标误差加权求和）→ 单目标搜索；
`spec`（`role=objective` 进 F，其余进 G；无 objective 时取 G 最差裕度）；
`pareto`（objective 进 F、其余进 G，供 `nsga2`；`nsga2` + `scalar` 自动提升）。

续跑：GUI 勾选「续跑」沿用当前输出目录；CLI 直接复用 `output_dir`。有快照时按
`snapshot.json` + `events.jsonl` 精确还原算法状态；没有快照时把历史成功评测热
启动进算法，不重新仿真；`settings.init_points` 的先验点会先被真实仿真。

运行审计：写入 `<output_dir>/audit.jsonl`，每条含 `kind` / `path` / `detail`，
只追加不覆盖；GUI 可注册回调实时接收。用于追溯某轮改写了哪个文件、用了什么模式。

编码：目标机可能是 C locale（`locale.getpreferredencoding()` 为
`ANSI_X3.4-1968`），Python 3.6 无 PEP 538/540。`optimizer.py` 与
`librf_opt.py` 在模块内包装 `open()`（文本默认 UTF-8、读取用
`errors="replace"`），子进程输出按字节接收再显式 UTF-8 解码。仍建议用
`LANG=C.UTF-8` 启动并避免中文路径。

## Output layout

```text
<output_dir>/
  deck_base.scs / manifest.jsonl / run_meta.json / history.csv
  events.jsonl / snapshot.json / snapshot-N.json / report.json
  convergence.png                  # 有 matplotlib 时
  iter_0000/
    record.json                    # 变量、目标、状态、耗时
    input.scs.params               # compact：parameters 行 + sha1
    input.scs.old-*                # compact：完整副本归档（只改名不删除）
    spectre.log                    # compact：错误/警告末 100 条 + 尾部 4KB
    spectre.log.gz                 # compact：完整日志
    result.txt / result.txt.gz     # 关键轮/末轮明文，普通轮 gz；psf/ 按 save_raw
```

`manifest.jsonl` 每个迭代至少一行；`type=retention` 追加归档后的最终文件集
（同 index 以后者为准），`provisional` 标记待定 ordinary。`log_mode: "full"`
回到逐轮完整 `input.scs`、明文 `spectre.log` 与明文 `result.txt`，仍写 manifest。

compact 目录没有 `input.scs` 属正常：完整字节在 `deck_base.scs` +
`input.scs.params`。重建（index 从 0 起，`7` = `iter_0007`）：

```sh
python3 optimizer.py --reconstruct RUN_DIR 7 out.scs
python3 optimizer.py --reconstruct RUN_DIR           # 全部 → reconstructed/
```

重建按 sha1 校验；模板漂移时该轮已保留完整 `input.scs`，自动回退读取。
`*.old-*` 是归档副本，工具只改名不删除；可自行清理，不影响续跑与重建。

## Layout & EM

### lri geometry kernel

lri 内核 v2 以 ring0 为基准，每一圈都是 ring0 的精确内缩，因此相邻圈金属间距
恒等于 `s`，整体 bbox 恒为 `W×H`；走线段角度只取 0°、45°、90°，可生成八角等
非曼哈顿轮廓。支持四种拓扑——`se` 单端、`diff` 差分、`f8` 八字、`sym` 对称，
并提供 `ct` 中心抽头。

v2 修复了三个实际缺陷：差分双臂圈间短路、差分中心桥/抽头形成短匝、B 臂绕向
导致互感为负。修复后同一几何 EMX 实测 L 由 0.40 nH 变为 1.70 nH。几何生成后
先做间距、线宽与 bbox 断言，再落版图。

### Layout generation

「版图 EM」页的版图生成子页支持类型 `ind` / `tl` / `xfmr` / `cpl`，按类型
展开动态几何表单与预设；按钮「仅出图」/「出图 + EM + views」/「渲染截图」；
views 模式 `emx`（推荐）/ `skill`（回退）/ `none`。层名不写死在代码里，全部
按 `tech_profile.json` 的角色解析。

### EMX simulation

EM 仿真子页提供 cell、GDS、频率与 EMX 精度设置（edge-width、3d、sidewalls、
via-separation、label-snap、差分 mode、温度、并行）；可开始 EM、查看指纹、
命令行预览、保存/载入设置。结果含 L(f)/Q(f)、SRF 标线与健康判读。

### Geometry iteration

勾选「启用版图 EM 优化」后，每轮几何 → 版图 → strmout → EMX → 刷新固定 sNp
→ 电路 spectre → 合并输出；同几何点命中 `work/.geom_cache.json`。

```json
"layout_em": {
  "enabled": true, "type": "ind", "cell": "opt_ind", "work": "work",
  "geom_vars": ["n", "w", "s", "dout"], "fixed_geom": {"shape": "octagon"},
  "snp": "work/snps/opt_ind.s2p", "emx": {"freq": "1e9:30e9:21"},
  "geom_outputs": true, "views_mode": "emx"
}
```

几何变量必须同时出现在 `variables` 中（GUI 勾选后自动并入）；`geom_var_specs`
是 GUI 生成的初始值与边界表。`geom_outputs: true` 把 `L0`、`SRF`、`Q@f0`、
`L(f)`、`Q(f)` 并入优化输出。几何失败、锁冲突、超时计罚并计入 `fail_limit`；
工具缺失等 INFRA 免费重试一次；写 cellview 前检测 `*.cdslck*`，被占用时拒绝
写入。`em_bridge` 与 `layout_em` 互斥，不得同时启用。

### EM fusion

默认关闭；`em_bridge.enabled=false` 或缺段时对原流程零影响。启用后每轮在电路
仿真前把电学目标（默认 `L0`）格栅化：先查 EM 库，命中则取库中的实测指纹；未
命中时仅当桥持有 `EmOptimizer` 才现做几何搜索 → EMX → 指纹 → 入库。桥把库
返回的 `delivered`（实测标量）写回对应电路变量后再跑电路仿真，并把 requested /
grid / delivered / delta / distance / sNp 路径记入 record 与 GUI；history 只记
delivered / grid / distance。`grid_key` 只是库索引，交付以 `delivered` 为准；
库保存 sNp 副本但不改写网表 nport 指向。

```json
"em_bridge": {
  "enabled": true, "mode": "track", "fallback": "actual",
  "library": "work/em_lib", "tech": "RFCMOS",
  "devices": [{"device_type": "ind", "target_var": "L0", "metric": "L0",
    "f_op": "28G", "tolerances": {"L0": 0.02},
    "grid_spec": {"step_rel": 0.02}}],
  "em_opt": {"budget": {"max_em": 20, "time_cap_s": 600}}
}
```

- `mode`（默认 `track`）：上一次求解的几何作为下一轮 EM 的几何先验。
- `fallback`：`actual` / `clip` / `penalty`，默认 `actual`。
- `library`：库根，默认 `work/em_lib`；`LIBRF_EM_LIB` 可覆盖。
- `device_type`：`ind` / `ct` / `tl` / `cpl` / `xfmr`；`target_var` 绑定电路
  变量（也接受 `var_prefix`），`metric` 为指纹键（默认 `L0`）。
- `f_op` 与 `tech` 必填（`tech` 可提到全局）；`tolerances` 可按指标覆盖
  `L0` / `Q@f0` / `srf_margin` / `theta_tol_deg`；`step_rel` 不得大于
  `2 × tol`，否则报错。`em_opt.budget` 的 `max_em` / `time_cap_s` 不写时按
  器件默认（如 `ind` 25 次、`tl` 10 次；时间上限 1800–2700 秒）。

库布局：`<library>/em_lib.sqlite3`（只 INSERT，不覆盖旧行）+
`snp/<sha1 前 16 位>.<原扩展名>`（内容寻址，只增不改）。状态：`hit_grid`、
`hit_nearest`、`miss`、`error`、`best_effort`、`infra_fail`；`infra_fail` 不占
优化预算。GUI「EM 融合」页实时展示 requested / grid / delivered 与告警。

## Process profile & tool discovery

版图生成依赖四类工艺文件，层名必须一致：工艺库 techlib（层定义）、layermap
（层名 → GDS 层号）、EMX `.proc`（层号 → 金属厚度/电导率）、`cds.lib`
（库名 → 目录）。`tech_profile.json` 字段：`techlib`、`techlib_dir`、`layermap`、
`proc`、`layers`（角色 → 层名，默认 `spiral=M5`、`underpass=M4`、
`route/ground=M3`、`via_sp=V4`、`via_un=V3`、`marker=MARKER`）、`text`（金属 →
文字层，缺省 `<层名>TXT`）、`drc`（`min_w` / `max_w` / `min_s` / `min_area` /
`via` 参数级自检阈值，不是 sign-off）。

工艺页用 `build_tech` 构建工艺对象、用 `require_tech_ready` 在出图与 EM 前预检。
门禁是**工艺无关**的：只按 `layers` 的角色映射核对 layermap 中是否存在对应层、
proc 中是否存在对应导体层，不写死任何层名或 GDS 层号；文件缺失与角色层缺失为
硬错，`techlib_dir` 与 `cds.lib` 缺失只告警并回退探查或自动生成。可选 `flow`
段显式给出叠层流程后，额外校验顺序、通孔、间距与通孔电阻（默认只告警，
`strict_flow` 升级为阻断）。

`proc_cross_section.py` 解析 `.proc`（`DEFINE` 常量与四则运算）与 layermap，按
声明顺序累加 z 坐标生成金属 / 介质 / VIA 横截面并做堆叠校验：缺流程金属、金属
顺序错误、缺通孔、layermap 层号不符为硬错；间距偏差、通孔电阻与叠层顺序异常
为告警。内置示教叠层（M1–M5 + AP、V1–V4 + PA）只用于演示与测试，实际工艺用
`tech_profile.json` 的 `flow` 段或自己的 `.proc` 覆盖。模块顶层不 import
tkinter，绘图函数接收调用方传入的 canvas，可在 GUI 工艺页直接渲染。

仓库自带 `fixtures/demo_stack.proc` 与 `fixtures/demo_stack.layermap` 一对示教
文件，`librf_opt.py --selftest` 与 `test_proc_cross_section.py` 都基于它们。

查找顺序：`$LIBRF_TECH_PROFILE` → 工作目录 → 当前目录 → 模块目录 → 内建默认；
缺项逐级回落。版图 cell 落在 `<WS>/LIBRF`，工作空间由 `LIBRF_WS` 指定（默认
`~/virtuoso_work`）。

工具路径现场探查，命中即停：显式环境变量（`SPECTRE_BIN`、`VIRTUOSO_BIN`、
`LIBRF_EMX`、`LIBRF_DBACCESS`、`LIBRF_STRMOUT`、`LIBRF_LAYERMAP`、`LIBRF_PROC`、
`LIBRF_CDSSETUP`）→ `$CADHOME` / `$CDS` / `$SPECTRE_HOME` 等环境目录 → `PATH`
→ `/opt/eda`、`/eda`、`/usr/eda`、`/opt/cadence` 常见安装树。GUI「环境」页的
「自动探测」填入空栏，「自检报告」显示每个工具的解析结果与来源。

## Safety rules

**版图/EM 写入绝不删除或覆盖目标机中已存在的非本工具文件。**

| 情形 | 行为 |
|---|---|
| 文件不存在 | 正常创建 |
| 已存在且非本工具产物 | 拒绝写入并报错（输出领地内也不豁免） |
| 本工具产物且属用户数据 | 更新前留 `<名>.bak-<时间戳>` |
| 工具内部状态 | 免 `.bak`（每轮重生成） |
| `cds.lib` | 只追加缺失 `DEFINE`/`SOFTINCLUDE`，改动前留 `.bak`，绝不截断 |
| 需淘汰的旧产物 | 改名 `<名>.old-<时间戳>`，不删除 |

用户数据示例：sNp、settings JSON、`cds.lib`、`result.txt`。内部状态：精确名
`.librf_owned.json`、`.geom_cache.json`；前缀 `driver_`、`res_`、
`dbaccess.log`、`strmout_`；后缀/包含 `.emx.log`、`.tmp`、`.old-`、`.bak-`。
`optimizer.py` 直接落盘的 `record.json`、`history.csv`、`manifest.jsonl`、
`deck_base.scs`、`input.scs.params`、快照与报告同属内部状态，免 `.bak`；run
目录应专用。写 cellview 前检查 `*.cdslck*` 与 `.librf_owned` 归属，非本工具
cell 拒绝重画。

## Testing

当前测试规模：`optimizer.py --selftest` 73 个用例；`librf_opt.py --selftest`
102 ok / 1 fail；`em_opt.py --selftest` 3 组 mock 场景；`--gui-smoke` 跑 3 轮
mock 并检查进度、仿真设置与 EM 融合页；`proc_cross_section` 自带 31 个 unittest。

已知 Windows 失败两项（其余用例必须全绿）：`TestToolDiscovery.test_find_prog_env_override`
（selftest，rc=1 属预期）与 `run_cmd 中文输出不崩`（librf_opt selftest）。

## Troubleshooting

| 现象 | 处理 |
|---|---|
| `config file is required` | CLI 必须给出配置路径 |
| `no spectre deck` | 设计页填「网表」或「网表根目录」，检查 library/cell |
| `网表顶层没有 parameters 行` | 顶层必须有 `parameters` 行；子电路内的不会被改写 |
| 找不到 spectre / virtuoso / emx | 设对应 `*_BIN` / `LIBRF_*` 环境变量，或用 GUI「自动探测」「自检报告」 |
| 中文乱码 / `UnicodeDecodeError` | 用 `LANG=C.UTF-8` 启动，避免中文路径，配置存为 UTF-8 |
| 工具报路径不存在 | 清空配置中的绝对路径，让现场探查接管；必要时设 `LIBRF_PROC` / `LIBRF_LAYERMAP` |
| compact 目录没有 `input.scs` | 正常；用 `--reconstruct` 重建并看 sha1 校验 |
| EM 结果层错或厚度不对 | 核对 `tech_profile.json` 的 `layers`/`text` 与 layermap、`.proc` 层名一致 |
| 横截面校验报缺金属、顺序错误或缺通孔 | 检查 `.proc` 的层定义与 VIA 链，确认 layermap 层号对应 |
| 电感几何断言失败（间距、线宽或 bbox） | 检查 `s`、`w` 与 `W×H` 是否冲突；差分拓扑注意中心抽头与臂间距 |
| EM 库记录不更新 | 库只 INSERT；换空库目录重建（工具不删/不覆盖用户文件） |
| EMX 卡住或报锁 | 关闭 Virtuoso 中打开的 cellview；检查 `*.cdslck*` |
| `em_bridge` 与 `layout_em` 同时开 | 两者互斥，关掉其中一个 |
| EM 设备配置报错 | `f_op`、`tech` 必填；`step_rel` 不得大于 2×tol；缺 `em_bridge.py` 时不静默降级 |
| selftest 返回码 1 | 只允许上述已知 Windows 失败；其他按 traceback 修复 |

## License

MIT，见 `LICENSE`。
