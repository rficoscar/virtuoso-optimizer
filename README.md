# Helix

### A Virtuoso Optimizer

*Parameter optimization, netlist generation and EM alignment for Cadence Virtuoso / spectre.*

`Version 1.3.1 · MIT · Python 3.6+（纯标准库；matplotlib 仅用于可选收敛图）`

把 Virtuoso / spectre 的参数优化、网表生成与 EM 对齐串成一条命令链：
改原理图或变量 → 自动重生成网表（含 config / 层次设计）→ 直连 spectre 迭代 → 收敛报告。
版图侧按工艺角色生成电感 / 传输线 / 变压器 / 耦合线，可接 EMX 做几何迭代。

三条核心能力：

- **开始前自动生成网表**：迭代第 0 轮前调用 Virtuoso `createNetlist(?recreateAll t)`，
  view 可为 `schematic` 或 `config`，以返回路径为准；失败回退旧网表，不静默改设计。
- **config / 层次网表参数改写**：不只改顶层 `parameters` 行，还按作用域处理子电路显式
  默认值与被实例覆盖的参数；变量在全网表找不到时报错，不“改了没用”。
- **EM 两阶段对齐**：以实测 / EM 曲线为目标重跑同一理想网表，输出逐变量修正方向、
  曲线偏差与 `aligned` 结论。

## Quick start

```sh
python3 optimizer.py --gui                      # 图形界面
python3 optimizer.py task.json                  # 真实运行（需 Cadence / spectre）
python3 optimizer.py task.json --mock --max-iter 20   # 无 license 的流程验证
python3 optimizer.py task.json --dump-state     # 只解析 ADE 状态，不仿真
python3 optimizer.py --selftest                 # 回归测试
python3 optimizer.py --gui-smoke                # GUI 无头冒烟
python3 netlist_create.py --selftest            # 自动网表
python3 netlist_params.py --selftest            # 层次/config 参数改写
python3 em_align.py --selftest                  # EM 两阶段对齐
python3 librf_opt.py --selftest                 # 版图 / EMX / 工艺
python3 -m unittest test_proc_cross_section     # 工艺剖面解析与校验
python3 optimizer.py --reconstruct RUN_DIR [INDEX [DEST]]   # 从 compact run 重建网表
```

GUI 流程：设计页填 library / cell / view，按需勾「开始前自动生成网表」或点「立即生成网表」
→ 变量 → 目标 → 设置 → 运行 → 结果页；「版图 EM」「EM 融合」「EM 对齐」分别负责几何、EM 库与修正。
工具栏「续跑」勾选即沿用输出目录，不勾则新建 run 目录（旧数据原地保留）。

## 标准工作流

1. **自动网表**（默认 `auto`）。成功后写入 `cfg["netlist"]`，报告在 `report.json` 的
   `netlist_create` 节点。旧网表先备份 `<名>.bak-<时间戳>`；新网表须存在、>100 字节且含
   `simulator lang=spectre`，否则恢复备份报 `fallback`；错误行抽取 `OSSHNL-*` / `ADE-*`。
   config 视图重生成的常是“纯设计网表”：旧网表的模型 `include`、`sp/dcOp/tran/...`
   分析、`save` 会合并回来（`testbench_merge`）；ADE 变量在原理图里没有默认值而被写成
   裸声明（如 `Ls2 Lp2`）时，用旧网表的有效值回填（`carry_over`），避免 spectre SFE-1997。
2. **迭代**。每轮只改参数、直跑 spectre，再经 SKILL 求值服务提取输出；层次改写结果写
   `iter_XXXX/netlist_patch.json`，缺变量即该轮失败并列出变量名。
3. **EM 对齐**。第一阶段跑完后，用 `--em-align` 以 EM 结果为目标做第二阶段：

```sh
# 第一阶段 run 目录 + EM 曲线（result.txt / run 目录 / csv / json / sNp 均可）
python3 optimizer.py task.json --em-align runs/stage1 --em-curve em/ind.s2p --em-out runs/stage2
# 也可以给 EM 网表：用该网表 + x_opt 单次仿真取 y_em
python3 optimizer.py task.json --em-align runs/stage1 --em-netlist em/ind.scs
```

第二阶段默认落在 `<stage1>/em_align`（非空目录自动加时间戳，`reuse_output` 可复用），
targets 由 `curve_op: "match"` 生成（默认容差 0.1，`--em-tol` 覆盖；每曲线最多 81 点），
默认用 `coordinate` 坐标模式搜索从 `x_opt` 出发（`em_align.strategy` 可换 `global` /
`simplex` / `cmaes` 等），只对 `targets` 用到的输出判定对齐。
输出 `em_align_report.json` / `.md`，report 增加 `em_align`（含 stage2 路径与 correction）。报告含
`x_opt`、`x_em_ideal`、偏差、方向、按 L/C/R/k 族建议，以及曲线 rms / max_abs 与 `aligned`。

## 配置参考

任务为 UTF-8 JSON；相对路径的 `state_dir` 以配置文件所在目录为基准。常用键：

| 键 | 作用 |
|---|---|
| `auto_netlist` | `false`/`"off"` 关闭；`"auto"`（默认）有 `state_dir` 或 library+cell 才执行；`true`/`"always"` 强制执行 |
| `netlist_view` | 生成网表用的 view（`schematic` / `config`）；缺省按 `view` 或 `ADE_state.info` 的 designInfo |
| `netlist_strict` | `true` 时失败即中止；默认有旧网表则继续，无旧网表仍报错 |
| `netlist_timeout` | `createNetlist` 超时秒数，默认 600 |
| `netlist_patch` | 层次改写开关：`patch_subckts`、`patch_instances`、`scope_map`（默认全开） |
| `library` / `cell` / `view` / `state` / `state_dir` | 设计与 ADE L 状态 |
| `variables` / `fixed_variables` / `linking` | 搜索变量（`init`/`min`/`max`，可选 `scale`、`integer`）、固定值、变量绑定 |
| `targets` | `output` + `metric`（`scalar`/`band_min`/`band_max`/`band_flatness`/`band_mean`/`value_at`）+ `op`/`goal`/`tol`/`weight`/`role`；曲线目标用 `curve_op` + `limit` |
| `settings` | `max_iter`、`patience`、`improve_rel`、`fail_limit`、`max_restarts`、`seed`、`strategy`、`mode`；可加 `portfolio`、`init_points` |
| `output_dir` | run 目录；CLI 同一目录自动续跑，GUI 用「续跑」勾选控制，不勾则新建 run 目录 |
| `netlist` / `sim_root` / `sim_timeout` | 网表路径、仿真树根、单轮超时 |
| `save_raw` / `log_mode` | psf 留存级别（0/1/2）、日志模式（`compact`/`full`） |
| `env` / `path_append` / `spectre_bin` / `virtuoso_bin` | 子进程环境与工具覆盖（留空现场探查） |
| `layout_em` / `em_bridge` | 版图几何迭代、EM 融合；两者互斥 |
| `em_align` | 第二阶段参数：`em_curve` / `em_netlist`（二选一）、`output_dir`、`tol`、`max_points`、`band`、`max_iter`、`patience`、`strategy`、`rms_tol`、`max_tol`、`outputs`、`reuse_output` |

完整示例：

```json
{
  "name": "S21 flatness",
  "library": "MyLib",
  "cell": "test_cs_driver",
  "view": "schematic",
  "netlist_view": "config",
  "auto_netlist": "auto",
  "netlist_strict": false,
  "netlist_timeout": 600,
  "netlist_patch": {"patch_subckts": true, "patch_instances": true},
  "state": "spectre_state1",
  "state_dir": "state/spectre_state1",
  "save_raw": 2,
  "log_mode": "compact",
  "sim_timeout": 1800,
  "output_dir": "runs/pa_1",
  "variables": [
    {"name": "L1", "init": "250p", "min": "150p", "max": "350p"},
    {"name": "k0", "init": "0.6", "min": "0.4", "max": "0.9"}
  ],
  "fixed_variables": [{"name": "vdd", "value": "1.2"}],
  "targets": [
    {"output": "S21 dB20", "metric": "band_min", "band": ["24G", "44G"],
     "op": ">=", "goal": 3, "tol": 0.5}
  ],
  "settings": {"max_iter": 100, "patience": 10, "improve_rel": 0.001,
               "fail_limit": 5, "max_restarts": 2, "seed": 0,
               "strategy": "simplex", "mode": "scalar"}
}
```

补充：`variables[].scale`（`auto`/`linear`/`log`，`auto` 在跨度 ≥10 倍且 `min>0` 时取对数），
`linking` 形如 `[{"target": "L2", "source": "L1", "mode": "ratio", "factor": 0.8}]`；
`settings.portfolio` 形如 `{"members": ["cmaes_ipop", "tpe"], "ucb_c": 1.0}`；
`settings.init_points` 为 CSV 先验点。`settings.mode`：`scalar`（默认）/ `spec` / `pareto`。

## 策略速查

14 个注册策略，选型说明见设置页右侧「算法说明」（来自 `algorithm.DESCRIPTIONS`）：

| 策略 | 一行说明 | 策略 | 一行说明 |
|---|---|---|---|
| `simplex` | 有界 Nelder-Mead 局部搜索（默认） | `coordinate` | 逐轴 ±步长模式搜索（EM 对齐默认） |
| `global` | 全局差分进化 | `de` | 自适应差分进化 |
| `lshade` | L-SHADE | `cmaes` | CMA-ES |
| `cmaes_ipop` | CMA-ES + IPOP 重启 | `tpe` | 贝叶斯 TPE |
| `nsga2` | NSGA-II 多目标 | `weighted` | 加权标量化 |
| `worstspec` | 最差规格裕度标量化 | `portfolio` | 多算法 UCB 竞跑 |
| `random` | 均匀随机 | `grid` | 网格扫描 |

## 运行模型与输出

启动时读一次网表快照；每轮按作用域改写参数后直接运行 spectre，再由 `librf.il` 求值服务
提取曲线。失败分 INFRA（license / 工具，免费重试一次）、DNC 与超时；连续失败达
`fail_limit` 停止。停止原因：`max_iterations`、`targets_met`、`sim_failures`、
`no_improvement`、`no_feasible_solution`、`stopped`。续跑按快照或历史记录还原（配置指纹
`config_sha1` 变化则拒绝续跑）。写文件同时记 `<output_dir>/audit.jsonl`。

```text
<output_dir>/
  deck_base.scs / manifest.jsonl / run_meta.json / history.csv
  events.jsonl / snapshot.json / report.json / audit.jsonl
  iter_0000/
    record.json / input.scs.params / netlist_patch.json
    spectre.log(.gz) / result.txt(.gz) / psf/（按 save_raw）
  em_align/                # 第二阶段：em_align_report.json / .md
```

`report.json` 的 `best_iter` 指向最优迭代，`best_met` 给出达标判定。compact 目录没有
完整 `input.scs` 属正常，用 `--reconstruct RUN_DIR [INDEX [DEST]]` 按 sha1 重建；层次改写
（子电路或实例级）的轮次会保留全文，`fallback=full_input_kept`。`*.old-*` 是归档副本，
工具只改名不删除。

## 版图 / EM

- 「版图 EM」页：类型 `ind` / `tl` / `xfmr` / `cpl`，出图、EMX 与几何迭代；开
  `layout_em.enabled` 后每轮几何 → 版图 → strmout → EMX → 电路，同几何点复用缓存
  （`work/.geom_cache.json`）。几何变量须同时在 `variables` 中。
- lri 几何内核：每圈为 ring0 的精确内缩，圈间距恒为 `s`、bbox 恒为 `W×H`；线段角度
  0°/45°/90°；支持 `se` / `diff` / `f8` / `sym` 与中心抽头 `ct`。生成后先做间距、线宽与
  bbox 断言再落版图。EMX 子页可设精度、频率与并行，结果含 L(f)/Q(f)与 SRF 判读。
- 「EM 融合」页：只读展示 requested / grid / delivered，由 `em_bridge` 驱动，库只追加
  （`em_lib.sqlite3` + 内容寻址 sNp），`grid_key` 只是索引、交付以 `delivered` 为准。
- 入口与约束：层名按 `tech_profile.json` 角色解析（默认 `spiral=M5`、`underpass=M4`、
  `route/ground=M3`、`via_sp=V4`、`via_un=V3`、`marker=MARKER`），出图前 `require_tech_ready`
  预检。**`em_bridge` 与 `layout_em` 互斥**。

## 工艺剖面与工具查找

版图生成依赖四类文件：工艺库 techlib（层定义）、layermap（层名 → GDS 层号）、EMX `.proc`
（层号 → 厚度/电导率）、`cds.lib`（库名 → 目录）。`tech_profile.json` 字段：`techlib`、
`techlib_dir`、`layermap`、`proc`、`layers`、`text`、`drc`（`min_w`/`max_w`/`min_s`/
`min_area`/`via`，参数级自检阈值，非 sign-off）、可选 `flow`（叠层校验，默认告警，
`strict_flow` 升级为阻断）。门禁工艺无关：只按角色核对层是否存在，不写死层名或 GDS 层号。

`proc_cross_section.py` 解析 `.proc` 与 layermap，按声明顺序累加 z 生成金属/介质/VIA
横截面并校验堆叠；内置示教叠层（M1–M5 + AP、V1–V4 + PA）只用于演示，真实工艺由
`fixtures/` 与 `tech_profile.json` 覆盖。profile 查找顺序：`$LIBRF_TECH_PROFILE` →
工作目录 → 当前目录 → 模块目录 → 内建默认；工作空间 `LIBRF_WS`，默认 `~/virtuoso_work`。

工具路径现场探查，命中即停：显式环境变量（`SPECTRE_BIN`、`VIRTUOSO_BIN`、`LIBRF_EMX`、
`LIBRF_DBACCESS`、`LIBRF_STRMOUT`、`LIBRF_LAYERMAP`、`LIBRF_PROC`、`LIBRF_CDSSETUP`）→
`$CADHOME` / `$CDS` / `$SPECTRE_HOME` → `PATH` → `/opt/eda`、`/eda`、`/usr/eda`、
`/opt/cadence`。GUI「环境」页「自动探测」「自检报告」。

## 安全规则

**写入绝不删除或覆盖目标机中已存在的非本工具文件。**

| 情形 | 行为 |
|---|---|
| 文件不存在 | 正常创建 |
| 已存在且非本工具产物 | 拒绝写入并报错（输出领地内也不豁免） |
| 本工具产物且属用户数据 | 更新前留 `<名>.bak-<时间戳>` |
| 工具内部状态 | 免 `.bak`（每轮重生成） |
| `cds.lib` | 只追加缺失 `DEFINE`/`SOFTINCLUDE`，改动前留 `.bak`，绝不截断 |
| 需淘汰的旧产物 | 改名 `<名>.old-<时间戳>`，不删除 |

写 cellview 前检查 `*.cdslck*` 与 `.librf_owned` 归属，非本工具 cell 拒绝重画。

## 测试

`optimizer.py --selftest` 94 用例；`netlist_create.py` 16；`netlist_params.py` 103；
`em_align.py` 108；`librf_opt.py --selftest` 106 ok / 0 fail；`test_proc_cross_section` 31；
`em_opt.py --selftest` PASS；`--gui-smoke` 跑 3 轮 mock 并检查进度、仿真设置、EM 与对齐页。
`librf.il` 另带 `librfSelfTest()`（SKILL 侧黄金断言，需在 Virtuoso 内执行，24 PASS / 0 FAIL），
不计入上述 Python 用例数。

## 故障排查

| 现象 | 处理 |
|---|---|
| `config file is required` | CLI 必须给配置路径；仅 `--selftest` / `--reconstruct` 等除外 |
| 自动网表失败 / `fallback` | `OSSHNL-*` / `ADE-*`：查 `cds.lib`、library / cell / view 与 `state_dir`；旧网表会备份恢复，需硬失败设 `netlist_strict: true` |
| 重生成后 `SFE-1997` 参数无值 | ADE 变量在原理图里没有默认值；看 `netlist_create.carry_over`，旧网表有值会自动回填 |
| 重生成后模型/分析缺失 | `netlist_create.testbench_merge` 会把旧网表的 `include`、`sp/dcOp/...`、`save` 合并回来；若旧网表本身没有，需在 config/ADE 里补 |
| `网表里找不到变量 X` | 看 `iter_*/netlist_patch.json` 的 `missing`；确认变量在顶层、子电路默认值或实例参数中出现 |
| `no spectre deck` | 填 `netlist` 或 `sim_root`，或用自动网表生成后重跑 |
| 找不到 spectre / virtuoso / emx | 设对应 `*_BIN` / `LIBRF_*` 环境变量，或用 GUI 环境页自动探测 |
| EM 对齐无结果 | 第一阶段 run 目录须有可定位的最优轮次（`best_iter` / `best_variables`）；曲线文件需解析出至少一条曲线 |
| EM 结果层错或厚度不对 | 核对 `tech_profile.json` 的 `layers`/`text` 与 layermap、`.proc` 层名一致 |
| 横截面校验报缺金属/顺序错/缺通孔 | 检查 `.proc` 层定义与 VIA 链，确认 layermap 层号对应 |
| 电感几何断言失败 | 检查 `s`、`w` 与 `W×H` 是否冲突；差分拓扑注意中心抽头与臂间距 |
| EMX 卡住或报锁 | 关闭 Virtuoso 中打开的 cellview；检查 `*.cdslck*` |
| `em_bridge` 与 `layout_em` 同时开 | 两者互斥，关掉其中一个 |
| 中文乱码 / `UnicodeDecodeError` | 用 `LANG=C.UTF-8` 启动，避免中文路径，配置存为 UTF-8 |

## License

MIT，见 `LICENSE`。逐版变更见 [CHANGELOG](CHANGELOG.md)。
