# Changelog

## 1.4.4 — 2026-10-30

版图几何生成（`librf`）的正确性与预检修复：耦合线奇偶模端口对齐、器件元数据
贯通、DRC 预检与 SKILL 同口径，均带回归测试；配置与搜索语义不变。

- **耦合线奇偶模取错端**：`_cpl_port_indices()` 在端口名不是 a1/a2/b1/b2 时按
  字母序取前四个，b1 落到 `p03`（线 2 东端）、b2 落到 `p04`（西端），与
  `p01`（西）/`p02`（东）取向相反；带 gnd 的 5 端口预设排序后甚至会把地当 a1。
  现在按 `librfBuildCpl` 的 p01..p04 名字定位（b1=p04、b2=p03），并支持 sNp
  Port order 重排。已知答案：Ze/Zo=60/35Ω、θe/θo=60°/80° 现在精确还原，
  旧映射实测 55.2/38.0Ω、56.0°/84.7°。
- **器件元数据只读不写**：「EM 仿真」页按 `<cell>.meta.json` 判断端口与器件
  类型，但全仓库没有写入方：4 端口耦合线/变压器一律按 2 端口电感处理，
  输出 `.s2p` 且结束后报「kind=ind 需要 2 端口」；2 端口传输线被当电感输出
  L0/SRF。「查看 sNp」还漏传 `kind`，必然 TypeError。现在生成版图时落盘
  type/kind/ports/bbox/geom，EM 页按它定 sNp 端口数、指纹口径与曲线类型，
  无元数据的 4 端口器件给出明确提示。
- **DRC 预检比 SKILL 宽松**：Python 预检不查 `drc.min_w/max_w`，且只在
  `w>=8` 时要求 `s>=2*min_s`；SKILL 的 `librfDrcParams(w s t ...)` 对电感区
  无条件要求 `s>=2*min_s`，同一几何预览放行、生成时才报 DRC；阈值还写死在
  常量里，不随工艺 profile 变化。现在 `_drc_veto()` 与 SKILL 同口径、阈值取
  `tech.drc`，版图页预检与 `make_layout_emx` 共用同一份工艺。
- **默认 cell 名非法且重名**：未填 cell 时取 `geom_key(...)[:8]`，得到
  `ind|{"H` 这类含引号/花括号的固定前缀（Virtuoso cell 名不合法），且不同
  几何同名、后一次覆盖前一个 cell。现在用整串 geom 键的 sha1 前 8 位。
- 自测 123 用例（新增 17 条：cpl 已知答案与端口重排、器件元数据往返/缺失、
  kind 推断、指纹摘要、DRC 拒绝/边界/随工艺变化、默认 cell 名）；optimizer
  114、netlist_create 16、netlist_params 103、em_align 108、em_opt PASS、
  proc_cross_section 31 全部通过；`--gui-smoke` OK。

## 1.4.3 — 2026-10-30

图形界面的配置往返与仿真设置展示三处缺陷修复，均带回归测试；引擎与配置语义不变。

- **重命名变量会丢 scale/integer**：变量表只显示四列，`scale`/`integer` 等元数据
  存在 `var_extra` 里按变量名索引；改名后仍按旧名取值，下一次保存/运行会把对数
  或整数变量静默变成连续/线性搜索。现在改名时把元数据迁到新名，删除或「移至
  固定」时清除，撤销/重做快照连元数据一起恢复。
- **工艺页配置串味**：`techlib/layermap/proc/...` 不在配置载入的接管列表里，旧
  配置的值滞留在 `self.extra`，清空输入框后仍会写回 JSON；打开另一份不含工艺段
  的配置或点「新建」时，`fill_from_cfg` 又只覆盖存在的键，上一份配置的工艺文件
  继续参与工艺门禁。现在这些键由表单接管，缺省键回到字段默认值。
- **仿真设置页可能与实际网表不一致**：GUI 自己按「最新 input.scs」找网表，运行器
  却优先 `netlist_view`；`view=schematic` 而 config 网表更新时，页面展示的是另一份
  网表的分析/频率/parameters。现在复用 `resolve_netlist_path()`，并把目标定义内容
  纳入 2 秒刷新签名——此前只比较目标条数，改频段/目标值后覆盖判断一直不更新。
- 自测 114 用例全绿（新增 5 条）；netlist_create 16、netlist_params 103、
  em_align 108、em_opt PASS、librf_opt 106、proc_cross_section 31 全部通过；
  `--gui-smoke` OK。

## 1.4.2 — 2026-10-30

搜索策略与引擎接口的若干缺陷修复（续跑崩溃、约束向量形状、多目标策略标量化
失效、CMA-ES 步长路径），均带回归测试；CLI / 配置语义不变。

- **grid 续跑越界崩溃**：快照恢复了 `levels` 与网格遍历顺序，`_grid` 却仍按本次
  构造参数（新的 `max_iter`）重算；续跑把预算改小时旧顺序下标落到新网格之外，
  `ask()` 抛 `IndexError`（100 → 10 实测必现）。现在 `restore()` 用快照里的 `levels`
  重建网格，并对越界顺序回退为顺序遍历。
- **worstspec / weighted 没有做自己的标量化**：`optimizer._SCALAR_ONLY` 把这两个
  包装器也强制成单目标，`SpaceAdapter` 先把目标向量求和再下发，`worstspec` 的
  `max()` 与 `weighted` 的目标权重从未执行（F=[0.2, 0.9] 时两者都按 1.1 记分）。
  现在二者接收原始目标向量；`weighted` 还接收目标页配置的权重。已知答案验证：
  `worstspec` 得 0.9，权重 [1, 3] 的加权和为 0.725。
- **CMA-ES 的 h_sigma 分母用了 g/λ**：标准式是
  `sqrt(1-(1-c_sigma)^(2(g+1)))`，代码写成 `2g/λ`，把代际尺度缩小 √λ 倍，使
  前若干代几乎恒判 `hsig=0`、协方差 rank-one 路径不更新（5 维凸问题上 20 代里
  5 代被误判）。改为 2g，并抽出 `_hsig()` 做已知答案断言。
- **约束向量形状错位**：`SpaceAdapter` 把单点的扁平 `G` 直接下发给按行取 `G[i]`
  的引擎，`G[0]` 成了 float；spec/pareto 模式下只要存在 constraint 或 error 角色
  目标，lshade / cmaes / tpe / nsga2 在第一次成功迭代就抛
  `TypeError: 'float' object is not iterable`。现在按点包一层；不可行点仍按 Deb
  规则排除在最优之外。
- **标量化包装器的 restart 不进内层**：停滞重启对 weighted/worstspec 原本是空
  操作；现在转发给内层引擎并回报其 `simplex_size()`。
- README 策略计数 13 → 14（实际注册 14 个）。
- 自测 109 用例全绿（新增 6 条）；netlist_create 16、netlist_params 103、
  em_align 108、em_opt PASS、librf_opt 106、proc_cross_section 31 全部通过。

## 1.4.1 — 2026-10-30

网表预检与零曲线守卫的三处缺陷修复，均带回归测试；引擎算法与配置语义不变。

- **预检误杀具名分析网表**：`netlist_analyses()` 按行首 token 判断分析类型，
  而 spectre/ADE 的语句是 `<分析名> <类型> …`（`ac1 ac start=…`、
  `sp1 sp ports=…`），真实网表会被判成"没有任何分析语句"、启动即中止。
  改为按第二个 token 取类型，保留 `sp sp …` 首 token 兼容写法；分析类型集合
  补齐到与 `netlist_create._ANALYSES` 一致（`tf` / `hbnoise` / `pstb` /
  `acmatch` 等），并跳过 `save sp` 这类指令行。
- **零曲线守卫漏掉 INFRA 重试**：首轮被判 INFRA 后重试成功、但解析不到任何
  曲线时，守卫被绕过，仍按 BIG_ERROR 记一轮成功继续空跑。现在首次结果与重试
  结果都检查；中止消息改用实际轮次，不再写死"首轮"。
- **预检不覆盖自动发现的网表**：未填 `netlist` 时运行器会按
  `sim_root/library/cell` 自己找 `input.scs`（GUI 默认就是"未指定，自动查找"），
  预检却直接跳过。抽出 `resolve_netlist_path()` 供运行器与预检共用，预检检查的
  正是实际要仿真的文件；补 `run_optimization` 入口的拒绝路径测试。
- 自测 103 用例全绿（新增 4 条）；`--gui-smoke` OK；netlist_create 16、
  netlist_params 103、em_align 108、em_opt PASS、librf_opt 106、
  proc_cross_section 31 全部通过。

## 1.4.0 — 2026-10-30

界面结构与命名调整 + 一处"跑了但没结果"的实机缺陷修复；引擎与配置语义不变。

- **更名**：Helix → Virtuoso Optimizer（窗口标题、关于、`librf.il` 文件头、
  自测/工具说明），`VERSION = "1.4.0"`。
- **页签顺序**：`环境 设计 变量 目标 仿真设置 进度 结果 工艺 版图 EM EM 对齐
  EM 融合`。EM 融合移到版图之后，EM 对齐在 EM 融合之前（先对齐再融合）；
  重排由 `_reorder_tabs()` 统一执行，`视图` 菜单同序。
- **删除「设置」「JSON」页**：搜索设置并入目标页底部，仿真选项并入仿真设置页
  顶部；长说明迁到菜单「帮助 → 参数说明」，信息不丢。配置仍以 JSON 文件为准。
- **网表预检**：真实运行前检查网表存在且含分析语句（`sp`/`dc`/`tran`…），
  缺失直接报错并给出处理办法，不再跑满预算拿到全是 1e6 惩罚的"假收敛"；
  单轮成功但解析不到任何曲线时也立即中止，GUI 弹「运行中止」。
- **README 精简**：只留特点 / 快速开始 / GUI 页面 / 任务配置键 / 运行产物，
  并写明启动前提（需要 `DISPLAY`，SSH 里无显示会打印 `GUI_SMOKE_FAIL` 退出）。
- 自测 99 用例全绿（新增网表分析识别与预检两条），`--gui-smoke` OK；
  VM 实机 12 轮 `targets_met`、误差 0.0。

## 1.3.2 — 2026-10-29

三处状态/校验语义修复，均带回归测试。

- **续跑已达标 run 被改判**：预算已耗尽时续跑，循环一次都不进，直接把
  `targets_met` 改写成 `max_iterations`、`iterations=0`。现在循环结束后先看恢复出的
  `best.errors.met`，已达标即返回 `targets_met`；报告新增 `iterations_total`。
- **`max_iter <= 0` 被静默改成 1**：`Settings` 与 `Optimization` 改为报
  `ValueError`（`settings.max_iter must be >= 1`）。
- **固定变量与优化变量重名**：`build_variables` 报
  `variable is both optimized and fixed: <名>`，不再让固定值静默覆盖搜索变量。
- 自测 97 用例全绿；实机验证成功 run 续跑保持 `targets_met`、`iterations_total=6`。

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
