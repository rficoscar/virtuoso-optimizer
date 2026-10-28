# Changelog

## 1.3.1 — 2026-10-29

只修一处 GUI 可读性缺陷，不改引擎与配置语义。

- **中文行上下压叠**：Linux/X11 上可用的 CJK 字体（如 `song ti`）线条高度等于
  字号，`tk.Text` 的 `spacing1/2/3` 全为 0 时中文行互相压叠，设置页「搜索设置 /
  仿真选项 / 历史与续跑」摘要与「详细说明」、EM 对齐页长文本不可读。
- **修法**：`gui.py` 的 `_make_text()` 统一补 `spacing1=2 / spacing2=4 /
  spacing3=2`（调用方可覆盖）。自动换行行必须用 `spacing2`，只加 1/3 不够。
  实测 16 px 字形配 20 px 行距，留 3–4 px 间隙。
- 自测：`optimizer.py --selftest` 94 全绿、`--gui-smoke` OK、GUI 全页签旅程
  PASS（1280×1024 Xvfb，13 页签，运行→完成→结果页）。

## 1.3.0 — 2026-10-29

迭代前自动重生成网表，config / 层次网表的参数改写真正落到仿真，EM 两阶段对齐
（以实测曲线为目标反求理想参数并给出 EM 模型修正方向）；新增 `coordinate`
坐标模式搜索并作为对齐阶段默认；README 按主次精简。

### 自动网表（`netlist_create.py`）

- 首轮之前调用 Virtuoso `createNetlist(?recreateAll t)`，view 可为 `schematic` 或
  `config`，以 SKILL 返回路径为准。`auto_netlist`：`false`/`"off"` 关，
  `"auto"`（默认）有状态或 library+cell 才跑，`true`/`"always"` 强制。
- 旧网表先备份 `<名>.bak-<时间戳>`；新网表须存在、>100 字节、含
  `simulator lang=spectre`，否则恢复备份并报 `fallback`；`netlist_strict: true`
  时硬失败。错误行抽 `OSSHNL-*` / `ADE-*`，不再把 INFO 当错误。
- **纯设计网表补全**：config 视图重生成的网表常缺测试台。`merge_testbench()` 从旧
  网表合并模型 `include`（排除 `pex.netlist`）、分析与 `save`；`carry_over_parameters()`
  把旧网表里 ADE 变量的有效值回填到裸声明（如 `parameters Ls2 Lp2`），修 spectre
  `SFE-1997` 与缺模型 `SFE-23`。
- 工作目录向上回溯至含 `cds.lib` 的目录，避免 state_dir 父目录没有 `cds.lib` 时
  Virtuoso 打不开 library/config。

### config / 层次参数改写（`netlist_params.py`）

- `patch_netlist()` 解析顶层 `parameters`（含续行）、子电路形参与默认值、实例参数
  字面量，按 `scope_map` 决定改写目标；覆盖子电路默认值被实例覆盖的 `shadowed`，
  记录 `instance_overrides`。
- 变量在全网表找不到即该轮失败，`iter_XXXX/netlist_patch.json` 列出 `missing`；
  旧实现只改顶层一行，config 层次下实际仿真参数不变。
- 紧凑日志在层次改写轮次强制保留全文（`fallback=full_input_kept`）。

### EM 两阶段对齐（`em_align.py`）

- `--em-align STAGE1_DIR [--em-curve FILE | --em-netlist FILE] [--em-out DIR]
  [--em-tol T] [--max-iter N]`：第一阶段产出 `x_opt` / `y_opt_ideal`，第二阶段把
  `y_em` 当目标重跑同一理想网表，输出 `x_em_ideal` / `y_em_ideal`。
- 曲线输入支持 `result.txt(.gz)`、run 目录、CSV/TSV、JSON、Touchstone；
  `build_match_targets` 生成 `curve_op: "match"` 目标（默认容差 0.1、每曲线 81 点），
  同名输出去重。
- `correction_report` 逐变量给出偏差与 `increase`/`decrease` 方向，按 L/C/R/k/W/Lg
  族给建议；曲线侧给 rms / max_abs 与 `aligned`。输出 `em_align_report.json` / `.md`。
- `--em-netlist` 可用 EM 网表 + `x_opt` 单次仿真取 `y_em`。

### 算法 / GUI

- 新增 `CoordinateSearch`（`strategy: "coordinate"`）：首问返回 `x0` 基线，逐轴
  ±步长，成对失败减半步长；适合“起点已不错、只需小幅修正”的场景。注册策略 14 个。
- GUI 新增「开始前自动生成网表」、`netlist_view` 与「立即生成网表」；新增「EM 对齐」
  页（第一阶段目录、曲线/网表、容差、策略），配置往返不丢。

### 自测

`optimizer.py --selftest` 94；`netlist_create.py` 16；`netlist_params.py` 103；
`em_align.py` 108；`librf_opt.py --selftest` 106 ok / 0 fail；
`test_proc_cross_section` 31；`em_opt.py --selftest` PASS；`--gui-smoke` OK。
实机（IC618 + Spectre 18.1）：config 视图网表重生成后 spectre 0 错误，参数改写
使 S21 rms 差 9.0463 dB（证明改动进入仿真）；EM 对齐 20 轮 `aligned=true`、
S21 rms 0.0461，反解参数与设计真值 2.5% 以内。

## 1.2.0 — 2026-10-28

版图引擎与优化引擎的审查修复：SKILL 侧让 `tech_profile.json` 的 DRC/via 真正
生效，修掉 balun 三路引出的必崩点；Python 侧修正 spec/pareto 的达标语义、续跑
配置校验与 Windows 停止路径；GUI 修掉三处数据/显示缺陷。

### 版图 / SKILL

- **via / min_area 注入是死代码（1.1.0 起）**：`tech_to_skill` 的嵌套子表用
  `_alist` 渲染，把 `drc.via` / `drc.min_area` 的数值也加了引号（`list("cut" "1.5")`）。
  SKILL 侧 `numberp` 判假，`librfDrcViaNum` / `librfDrcMinArea` 永远走回落值。
  改用 `_skill_val`：字符串才加引号，数值/布尔按字面量输出。via 缺省回落改为
  示教模板 `0.5/0.5/0.2`，与 Python 侧 `SYM_VIA_*` 一致。
- **via 描画尺寸**：underpass、传输线栅栏、lri 桥接/landing 共 7 个函数 21 个
  调用点改由 `librfDrcViaNum` 读 `drc.via`；两级栈错位量随 `(cut+spacing)/2`
  推导。
- **`librfPickRoutes` 必崩**：`librfRoutes3` 返回 `(rCT rP02 rP04 minGap)`，
  旧代码 `m = cadr(r)` 取到 p02 走线点表，`min(点表, 浮点)` 直接抛错——balun
  变压器与 `librfSelfTest` 第 7 组都无法执行。改取 `cadddr(r)`。
- **微带被自己的 DRC 判死**：`librfBuildTl` 对单根微带传假 `s=2.5`，`w>15` 时
  `wideSp` 分支报间距错；单根线无邻线，改为不参与间距判定。
- **DRC 补强**：`librfDrcNum` / `librfDrcMinArea` / `librfDrcViaNum` 返回值补
  `1.0 *` 强转；`librfDrcFigs` 增加 path 线宽检查（`fig->width`，errset 保护）；
  新增 `librfAssertSpiralBox`，电感与变压器生成后断言中心线 bbox =
  `(dout-w+ex) × (dout-w+ey)`。
- **整数进 `%.2f` 崩溃**：匝数 `n` 与 `fig->width` 进 `sprintf` 前统一强转。
- `librfSelfTest()` 新增三组断言（八角/方形孔径 80/55/80、via 规则注入与缺省、
  DRC 阈值注入与缺省）；IC618 实机 24 PASS / 0 FAIL。

### 引擎

- **spec/pareto 假达标**：`evaluate_roles` 新增逐目标 `met`。这两个模式的
  `total` 只汇总 objective，约束违反不进 total；pareto 无 objective 时 total
  恒 0，旧代码第 0 轮必报 `targets_met`。`run()` 改按 `met` 停止，`_track_best`
  让可行点优先，报告新增 `best_met`。
- **续跑配置校验**：`run_meta.json` 写入 `config_sha1`（circuit / variables /
  targets / mode / netlist / state / em_bridge）。续跑指纹不符直接报错，不再
  把别的 run 的最优解当本轮结果；旧目录无指纹时告警后按旧行为继续。
- **归档目录**：`load_previous` 只收 `iter_<digits>/record.json`，不再把
  `iter_*.old-*` 计入历史。
- **Windows 停止路径**：`_kill_group` 在无 `os.getpgid` / 无 `SIGKILL` 的平台
  回落到 `terminate()`/`kill()`。
- `summary.failures` 改为真实失败计数；整轮全失败返回 `sim_failures`。

### GUI

- 「帮助 → 关于」引用未定义的 `_opt`，点击必 `NameError`；改为导入 `VERSION`。
- EM 融合页读 `record["em_actual"]`，实际写入在 `record["extra"]["em_actual"]`，
  「当前查询」恒为空；补 `extra` 回退。
- 变量表的 `scale` / `integer` 在打开→开始/另存时丢失；载入时保存额外键，
  回写时合并，CLI 与 GUI 搜索空间一致。

### 自测

`optimizer.py --selftest` 82（新增 spec/pareto 停止、续跑指纹、归档过滤）；
`librf_opt.py --selftest` 106 ok / 0 fail（新增 via/min_area 数值注入断言）；
`proc_cross_section` 31；`em_opt.py --selftest` PASS；`--gui-smoke` OK；mock CLI
6 轮。Windows 两项历史失败改为跨平台写法，不再计入已知失败。

### 脱敏

仍为通用示教工艺：层角色 M1–M5/AP、V1–V4/PA、MARKER 199/0；仓库不含真实
工艺名、层号或绝对路径。测试用 techlib 名改为 `DEMO65`。

## 1.1.0 — 2026-10-27

版图 PCell 几何修正，全部落在 `librf.il`。共同特征是"画出来的版图与参数/注释
不符"，其中八角自重叠、内径闭合式、变压器上下绕组重叠三条会直接导致短路或
尺寸失真。所有修正都补了自测断言。

### 修复

- **八角螺旋同圈自重叠**：每圈半径的内缩量原按 `fix(k/4)` 递减，但一圈有 8 条
  边，应为 `fix(k/8)`。原实现每圈多缩一次，`dout=150` 的八角电感外接 bbox 只有
  137.4、几何中心偏移 (−6.3, −6.3)，`n=2.5` 时非相邻段最小中心距 8.86 < 线宽
  10。修正后 bbox 恒等于 `dout`、中心回到原点、平行段间距等于 `pitch`。
- **内径闭合式 `din` 与实画绕线不符**：`din` 原按非整数匝直接代入参数，实画却
  按向上取整（`n=2.5` 实际画 3 圈）。改为 `dout − 2(⌈n⌉·pitch − s)`；
  `librfDinFromParams(2.5, 10, 2.5, 100)` 由 42.5 改为 30。
- **中心抽头电感 / balun 的"半螺旋"实为蛇形**：单层对称半螺旋在几何上不存在
  ——两个半边的拐角逐点重合、包围面积为 0。改为**整螺旋 + 绕线中点抽头**
  （按弧长 50% 求落点），抽头经下层引出，并与另一端口强制分层。
- **叠层 / 交指变压器上下绕组重叠**：上绕组内端原来直接在上层折回，与上绕组
  自身金属重叠。改为上绕组内端先沿内孔引到中心，再从中心穿孔下到下层、由下层
  引出——中心恒为下绕组内孔，必然空闲。
- **交指变压器两绕组不交错**：次级 `dout` 未做半间距偏移，实际与叠层同构。
  改为 `doutS = dout − (w + s)`，`dout` 不足以让开一个 `pitch` 时告警并退化为叠层。
- **balun 三路下层引出线相撞**：原来用固定方位（W/S/E），`ns > np` 时次级内端
  与中心孔引出线常交叉。改为穷举三路互异方位、按最小同层间距选优；不足阈值时
  报错并给出方位，而非画出一张会短路的版图。
- 下穿孔与抽头焊盘对相邻绕组的净空、引出落点相对**拉长后**轮廓的计算，一并
  补上断言（此前拉长/非方形参数可能把落点算到金属上）。

### 自测

`librfSelfTest` 增加黄金断言：`din` 闭合式（整数匝与非整数匝）、八角螺旋
nSeg/bbox/中心/无自重叠、抽头点落在线中心、balun 三路引出方位互异且间距达标。
原「半螺旋点数」断言随该函数一并移除。

## 1.0.0 — 2026-10-27

首个公开版本：合入 v08c 几何内核与工艺门禁，脱敏发布。
