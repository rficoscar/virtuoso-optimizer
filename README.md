# Virtuoso 参数优化器 v09

对 Cadence Virtuoso / spectre 电路做参数优化的独立工具：编辑变量与目标 → 直连
spectre 迭代仿真 → 实时观察收敛、曲线与变量 → 输出最优组合。ADE L 状态驱动，
无 Maestro 依赖。纯 Python 3.6+ 标准库（`matplotlib` 仅用于可选收敛图）。

能力分四部分：电路参数优化（optimizer / algorithm / gui / selftest）、版图自动化
与版图 EM（librf_opt / librf.il）、EM 融合（em_bridge / em_opt）、工艺配置。

> 本仓库是脱敏发布版：`tech_profile.json` 只是通用模板，不含任何具体工艺/PDK
> 数据；内建默认层名与 DRC 阈值同样是占位示例，请按你的 PDK 改写后再用。

## 部署文件（当前行数）

| 文件 | 行数 | 作用 |
|---|---:|---|
| `optimizer.py` | 3,353 | 引擎与 CLI：仿真调度、日志/快照、变量变换、工具探查 |
| `selftest.py` | 1,264 | unittest 回归套件（69 个用例） |
| `gui.py` | 3,585 | Tkinter 图形界面与无头冒烟 |
| `algorithm.py` | 2,987 | 13 个搜索策略、注册表、算法说明 |
| `librf_opt.py` | 3,146 | 版图/EMX 编排、sNp 指纹、GeomRunner、GUI「版图 EM」页 |
| `em_bridge.py` | 990 | EM 融合桥：电学目标 → 库查询 / 现做 EM |
| `em_opt.py` | 774 | 电磁优化器：几何搜索 → EMX → 指纹 → 入库 |
| `librf.il` | 2,336 | SKILL 合并文件：版图引擎 + 求值服务 |
| `tech_profile.json` | 43 | 工艺配置模板：工艺库、层角色、文字层、DRC 阈值 |
| `README.md` | 317 | 本手册 |
| **合计** | **18,795** | 十个部署文件 |

## 快速开始

```sh
# 图形界面
python3 optimizer.py --gui

# 真实运行（需 Cadence / spectre license）；mock 运行无需 license
python3 optimizer.py task.json
python3 optimizer.py task.json --mock --max-iter 20
python3 optimizer.py task.json --dump-state     # 只解析 ADE 状态

# 回归测试、GUI 无头冒烟与模块自测
python3 optimizer.py --selftest
python3 optimizer.py --gui-smoke
python3 librf_opt.py --selftest                 # 版图/EM/工艺配置
python3 em_opt.py --selftest                    # EM 优化器 mock 自测

# 从 compact run 目录重建完整网表（index 从 0 起）
python3 optimizer.py --reconstruct RUN_DIR [INDEX [DEST]]
```

GUI 流程：设计页导入 ADE L 变量/输出 → 变量页区分优化变量与固定变量 → 目标页
定义规格 → 设置页选策略与预算 → 菜单「运行-开始」→ 进度页看收敛、变量柱状图、
动态曲线 → 结果页读最终报告。工具栏「续跑」勾选=沿用输出目录；不勾=新建 run
目录，旧数据原地保留。

## 配置文件

JSON，UTF-8。GUI 可新建/保存；CLI 直接读取。示例：

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
  GUI 留空或默认 `logs/run` 时，每次新建 `logs/<circuit>/run_<时间戳>`。
- `variables[]`：`name / init / min / max`，支持工程计数法（250p、1.5G、3meg）；
  可选 `scale`（`auto` / `linear` / `log`，auto：跨度≥10 倍且 min>0 时对数）
  与 `integer: true`。顶层 `linking` 可绑定变量：
  `[{"target": "L2", "source": "L1", "mode": "ratio", "factor": 0.8}]`。
- `fixed_variables[]`：不参与搜索，值原样写入每轮网表。
- `targets[]`：`output` 为 ADE L 输出名；`metric` 为 `scalar` / `band_min` /
  `band_max` / `band_flatness` / `band_mean` / `value_at`（需 `freq`）；`band`
  为 `[lo, hi]`；`op` 为 `>=` / `<=` / `==` / `range`（`range` 需 `goal2`）；
  `tol`、`weight`、`role`（`error` / `constraint` / `objective`，默认
  `error`）。曲线目标用 `curve_op`（`above` / `below` / `match`）+
  `limit: {"x": [...], "y": [...]}`。
- `settings`：`max_iter`、`patience`、`improve_rel`、`fail_limit`、
  `max_restarts`、`seed`、`strategy`、`mode`；
  `portfolio: {"members": ["cmaes_ipop", "tpe"], "ucb_c": 1.0}`；
  `init_points`：CSV 路径，每行一组变量值，作为先验点先真实仿真。
- `save_raw`：`0` 提取后归档 psf；`1` 每轮保留；`2` 只保留首轮、每次新最优、
  失败与末轮。`log_mode`：`compact`（默认）或 `full`。`env` 覆盖仿真子进程
  环境变量；`path_append` 追加 PATH；`spectre_bin` / `virtuoso_bin` 留空即
  现场探查；`netlist` 留空则按 `sim_root` 查找网表。

## 搜索策略（13 个注册名）

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

## 运行机制、目标模式与续跑

启动时读取一次网表快照；每轮只改写顶层 `parameters` 行（固定变量 + 优化变量），
直接运行 spectre，再由 `librf.il` 的求值服务提取输出曲线。第一轮包含求值服务
冷启动。优化期间可开 Virtuoso / ADE L 查看，但不要修改原理图、变量或输出（快照
已缓存）。

失败分类：INFRA（license、网表、求值服务、工具找不到）原地免费重试一次，重试成功
不记失败；重试仍失败、DNC 或超时均计一次失败并累加惩罚，连续失败达 `fail_limit`
停止。单轮目标求值失败以 `BIG_ERROR=1e6` 惩罚并继续。停止原因：`max_iterations`、
`targets_met`、`sim_failures`、`no_improvement`、`no_feasible_solution`、`stopped`。

`settings.mode`：`scalar`（默认，所有目标误差加权求和）→ 单目标搜索；
`spec`（`role=objective` 进 F，其余进 G；无 objective 时取 G 最差裕度）；
`pareto`（objective 进 F、其余进 G，供 `nsga2`；`nsga2` + `scalar` 自动提升）。

续跑：GUI 勾选「续跑」沿用当前输出目录；CLI 直接复用 `output_dir`。有快照时按
`snapshot.json` + `events.jsonl` 精确还原算法状态；没有快照时把历史成功评测热
启动进算法，不重新仿真；`settings.init_points` 的先验点会先被真实仿真。

## 输出目录与紧凑日志

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
（同 index 以后者为准），`provisional` 标记待定 ordinary。`log_mode: "full"` 回到
逐轮完整 `input.scs`、明文 `spectre.log` 与明文 `result.txt`，仍写 manifest。

compact 目录没有 `input.scs` 属正常：完整字节在 `deck_base.scs` +
`input.scs.params`。重建（index 从 0 起，`7` = `iter_0007`）：

```sh
python3 optimizer.py --reconstruct RUN_DIR 7 out.scs
python3 optimizer.py --reconstruct RUN_DIR           # 全部 → reconstructed/
```

重建按 sha1 校验；模板漂移时该轮已保留完整 `input.scs`，自动回退读取。
`*.old-*` 是归档副本，工具只改名不删除；可自行清理，不影响续跑与重建。

## 版图 EM（librf_opt.py + librf.il）

GUI 第 11 个页签「版图 EM」含三个子页：
- 版图生成：类型 `ind` / `tl` / `xfmr` / `cpl`，动态几何表单与预设；按钮
  「仅出图」/「出图 + EM + views」/「渲染截图」；views 模式 `emx`（推荐）/
  `skill`（回退）/ `none`。
- EM 仿真：cell、GDS、频率与 EMX 精度设置（edge-width、3d、sidewalls、
  via-separation、label-snap、差分 mode、温度、并行）；开始 EM、查看指纹、
  命令行预览、保存/载入设置；结果含 L(f)/Q(f)、SRF 标线与健康判读。
- EM 优化迭代：几何变量表 + 固定几何 JSON；勾选「启用版图 EM 优化」后每轮
  几何 → 版图 → strmout → EMX → 刷新固定 sNp → 电路 spectre → 合并输出；
  同几何点命中 `work/.geom_cache.json`。

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
工具缺失等 INFRA 免费重试一次；写 cellview 前检测 `*.cdslck*`，被占用时拒绝写入。
`em_bridge` 与 `layout_em` 互斥，不得同时启用。

## EM 融合（em_bridge.py + em_opt.py）

默认关闭；`em_bridge.enabled=false` 或缺段时对原流程零影响。启用后每轮在电路
仿真前把电学目标（默认 L0）格栅化：先查 EM 库，命中则取库中的实测指纹；未命中
时仅当桥持有 `EmOptimizer` 才现做几何搜索 → EMX → 指纹 → 入库；当前 CLI/GUI
未注入，按未命中继续。桥把库返回的 `delivered`（实测标量）写回对应电路变量
后再跑电路仿真，并把 requested / grid / delivered / delta / distance / sNp 路径
记入 record 与 GUI；history 只记 delivered/grid/distance。`grid_key` 只是库索引，
交付以 `delivered` 为准；库保存 sNp 副本但不改写网表 nport 指向。

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

## 工艺配置与工具路径

版图生成依赖四类工艺文件，层名必须一致：工艺库 techlib（层定义）、layermap
（层名 → GDS 层号）、EMX `.proc`（层号 → 金属厚度/电导率）、`cds.lib`
（库名 → 目录）。`tech_profile.json` 字段：`techlib`、`techlib_dir`、`layermap`、
`proc`、`layers`（角色 → 层名，内建默认 `spiral=M5`、`underpass=M4`、
`route/ground=M3`、`via_sp=V4`、`via_un=V3`、`marker=MARKER`，按你的 PDK
改写）、`text`（金属 → 文字层，缺省 `<层名>TXT`）、`drc`（`min_w`/`max_w`/
`min_s`/`min_area`/`via` 参数级自检阈值，不是 sign-off）。

查找顺序：`$LIBRF_TECH_PROFILE` → 工作目录 → 当前目录 → 模块目录 → 内建默认；
缺项逐级回落。版图 cell 落在 `<WS>/LIBRF`，工作空间由 `LIBRF_WS` 指定（默认
`~/virtuoso_work`）。`tech_profile.json` 随仓库的是通用模板，没有工艺细节。

工具路径现场探查，命中即停：显式环境变量（`SPECTRE_BIN`、`VIRTUOSO_BIN`、
`LIBRF_EMX`、`LIBRF_DBACCESS`、`LIBRF_STRMOUT`、`LIBRF_LAYERMAP`、`LIBRF_PROC`、
`LIBRF_CDSSETUP`）→ `$CADHOME` / `$CDS` / `$SPECTRE_HOME` 等环境目录 → `PATH`
→ `/opt/eda`、`/eda`、`/usr/eda`、`/opt/cadence` 常见安装树。GUI「环境」页的
「自动探测」填入空栏，「自检报告」显示每个工具的解析结果与来源。

## 文件安全铁律

**版图/EM 写入（librf_opt）绝不删除或覆盖目标机中已存在的非本工具文件。**

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
`deck_base.scs`、`input.scs.params`、快照与报告同属内部状态，免 `.bak`；run 目录应专用。
写 cellview 前检查 `*.cdslck*` 与 `.librf_owned` 归属，非本工具 cell 拒绝重画。

## UTF-8 与 C locale

目标机可能是 C locale（`locale.getpreferredencoding()` 为
`ANSI_X3.4-1968`），Python 3.6 无 PEP 538/540，默认 `open()` 与子进程解码按
ASCII，遇到中文会 `UnicodeDecodeError`。本工具已处理：`optimizer.py` /
`librf_opt.py` 在模块内包装 `open()`：文本默认 `encoding="utf-8"`、读取用
`errors="replace"`；子进程输出按字节接收再显式 UTF-8 解码，不使用
`universal_newlines`；`stdout` / `stderr` 在 ASCII locale 下重包为 UTF-8；文件
系统编码为 ASCII 时打印警告。仍建议用 `LANG=C.UTF-8` 启动，并避免中文路径。

## 测试与验收

当前测试规模：`optimizer.py --selftest` 69 个用例；`librf_opt.py --selftest`
87 条源码检查（本机实跑 80 ok / 1 fail）；`em_opt.py --selftest` 3 组 mock
场景；`--gui-smoke` 跑 3 轮 mock 并检查进度、仿真设置与 EM 融合页。

已知 Windows 失败仅两项，门禁按 `known_failures` 放行：
`TestToolDiscovery.test_find_prog_env_override`（selftest，rc=1 属预期）与
`run_cmd 中文输出不崩`（librf_opt selftest）。

本地开发另有一套严格门禁（不随本仓库发布）：依次检查 import、optimizer
selftest、GUI smoke、librf selftest、em_opt selftest、mock CLI 语义（6 轮、
stop_reason、最优变量与数值）与部署行数。自行使用时建议同样把
「基线测试名 + 语义哈希」冻结成清单，删除/改名/改写测试都要登记理由，
而不是靠补测试数量糊弄覆盖率。

## 故障排查

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
| EM 库记录不更新 | 库只 INSERT；换空库目录重建（工具不删/不覆盖用户文件） |
| EMX 卡住或报锁 | 关闭 Virtuoso 中打开的 cellview；检查 `*.cdslck*` |
| `em_bridge` 与 `layout_em` 同时开 | 两者互斥，关掉其中一个 |
| EM 设备配置报错 | `f_op`、`tech` 必填；`step_rel` 不得大于 2×tol；缺 `em_bridge.py` 时不静默降级 |
| selftest 返回码 1 | 只允许上述已知 Windows 失败；其他按 traceback 修复 |
| 回归测试数量变少 | 别删测试凑覆盖率；确需删除/改名就在自己的基线清单里登记理由 |

## 许可证

MIT License，见 [LICENSE](LICENSE)。
