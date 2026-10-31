# Changelog

## 1.6.2 — 2026-10-31

交付包不再携带 CHANGELOG 与开发指南；仓库与发版回归不变。`VERSION` 1.6.2。

- **打包策略**：`tools/make_kit.py` 新增 `DOCS_EXCLUDE`，默认排除 `CHANGELOG.md`、
  `DEVELOPMENT_GUIDE.md`，MANIFEST 记录 `docs: excluded`；`--include-docs` 可打回。
  包内文件 63 → 61，base64 39.9 万 → **36.5 万字符**，仍是 2 片。
- **不变**：仓库保留 CHANGELOG、DEVELOPMENT_GUIDE 和全部单测；README、LICENSE
  继续随包发布。
- **验证**：`verify_kit.py` PASS；解包后 `import librf_opt`、`--gui-smoke` 正常，
  `VERSION` 为 1.6.2。

## 1.6.1 — 2026-10-31

交付包不再携带单元测试；仓库里的测试与发版回归不变。`VERSION` 1.6.1。

- **打包策略**：`tools/make_kit.py` 默认排除 `selftest.py`、
  `test_proc_cross_section.py`、`lri/selftest_librf.py`、`fixtures/`，MANIFEST 记录
  `tests: excluded`；`--include-tests` 可打回全量。包内文件 68 → 63，xz 从 3 片降到
  **2 片**（39.8 万字符）。
- **兼容入口**：`librf_opt.py` 不再在导入时加载自测模块，`--selftest` 改为按需导入；
  包里没有测试时打印提示并以 2 退出。`optimizer.py --selftest` 同样提示“本包未附带单测”。
- **不变**：仓库保留全部单测（179 + 124）作为发版回归；`eval/` 是电感评估框架，
  属于功能，照常随包发布。
- **验证**：仓库 `--selftest` 179 OK、`librf_opt.py --selftest` 124 ok/0 fail、
  `--gui-smoke` OK；解包后的包内 `import librf_opt` 正常，两个 `--selftest` 均给出
  提示并以非零码退出（librf_opt 为 2，optimizer 为 1）；`verify_kit.py` PASS。

## 1.6.0 — 2026-10-31

源码按职责拆分，行为不变；交付仍是单一全量文本包。`VERSION` 1.6.0。

- **`lri/` 包（新增）**：`librf_opt.py` 的 176 个顶层定义按职责拆成 `common` /
  `tech` / `tools` / `skillio` / `snp` / `emx` / `device` / `runner` / `layout` /
  `ui_em` / `selftest_librf` 十一个模块，函数体逐语句原样迁移。`_HERE`/`SKILL_IL`
  改为从包位置定位仓库根；`librf.il`、`lri_pcell.il` 仍在根目录。根目录
  `librf_opt.py` 只做兼容再导出，`import librf_opt` 与 `--selftest` 用法不变。
- **`lri/ui/` 子包（新增）**：`gui.py` 拆成 `plots` / `netlist` / `results` /
  `reports` / `tech_tab` / `main`；根目录 `gui.py` 再导出 `run_gui`、
  `OptimizerGui`、`PlotCanvas`、`_TechTab`、`_MANAGED_CFG_KEYS`、`_var_extra_*`。
  `optimizer.py --gui` 分派与 `selftest.py` 对下划线接口的引用不受影响。
- **交付**：打包脚本按 `git ls-files` 收集，拆分后文件数 68、xz 3 片
  （43.6 万字符），仍只出全量包一种。新增代码按职责进子模块，根目录薄壳不再堆逻辑。
- **验证**：`optimizer.py --selftest` 179 OK、`librf_opt.py --selftest` 124 ok/0 fail、
  `em_opt.py --selftest` PASS、`--gui-smoke` OK；`verify_kit.py` PASS。

## 1.5.2 — 2026-10-31

差分对称电感 PCell、工艺档派生链与版图评估框架。已有版图器件与优化器行为不变；
`VERSION` 1.5.2。

- **PCell 外壳（新增 `lri_pcell.il`）**：`lriPcellDefine` 注册 `ind_sym` 并建 CDF，
  参数 `rx`/`ry`/`n`/`w`/`s`/`chamfer`/`lead_len`/`lead_gap`/`lead_w`/`ct`/
  `stack`/`metal_main`/`metal_aux`。`lriPcellTechSmic55`/`lriPcellTechLoad` 从
  `tech/<key>_profile.il` 载入层角色与 DRC 数值；批处理下自动定位 `pCellGen.cxt`。
- **几何内核（`librf.il`）**：新增对称差分内核 `lriI3BuildSym`。环族
  `a_k=a0−kp`、`b_k=b0−kp`、`c_k=max(0,c0−k·p·(2−√2))`，横纵半径独立，每臂整数圈，
  `chamfer` 0..1（2−√2 为正八边形）；每圈拆两条半圈路径、underpass 逐半圈交替并
  按窗口布置通孔阵列；中心抽头走内桥中点；主/副金属可逐实例指定，`metal_main`
  在任意堆叠金属上时由 `lriViaChain` 按 `librfStackVias` 逐级补 landing pad。
  宽金属长平行间距门禁 `librfDrcWideSpace` 在构建期拒绝不可行参数（差分两臂与
  两条引线是独立多边形，`s`/`lead_gap` 必须不小于各金属的 `wide_space`）。
- **Python 侧（`librf_opt.py`）**：`lri_norm_geom` 增 sym 分支（内核的 Python 镜像），
  `lri_validate` 校验整数圈与宽金属间距；`GEOM_FORM` 增 `stack`/`metal_main`/
  `metal_aux` 三行，skill 注入支持工艺表；selftest 覆盖典型值与半圈拒绝。
- **工艺档单一事实源（`tech/`、`stream/`、`tools/gen_tech.py`）**：一个
  `tech/<key>.json` 派生 `tech/<key>_profile.il`（SKILL）、`stream/<key>.map`
  （strmout）、`eval/config_<key>.json`（评估器）。自带 `smic55ll_ulp_rf` 与
  `demo2m`（两层金属最小示例）；生成器校验堆叠顺序、通孔相邻性与默认金属链。
  换工艺只改 JSON，不改 Python/SKILL。
- **评估框架（新增 `eval/`）**：14 个模块。`skillgen.py` 用真实 SKILL PCell
  （`virtuoso -nograph` 建 cellview + `strmout` 导 GDS，输出重定向到文件，
  批处理 virtuoso 偶发静默退出时重试一次）；`geometry.py` 做 G0–G12 几何检查
  （线宽/外框/间距/自交/通孔尺寸/宿主/包络/间距/标签/连通/圈数/对称/内圈净空/
  引线一致/叠层）；`emx_runner.py` 取 L/Q/R/SRF；`calibre_runner.py` 跑 DRC/LVS；
  `matrix.py` 的 `sym` 套件 31 例，含 6 个 `expect_fail` 不可行组合。
- **文档**：README 增 PCell、工艺档、评估、单包迁移四节；DEVELOPMENT_GUIDE 增 §11
  （工艺档派生契约、SKILL 内核契约、评估器契约）与回归步骤 4；迁移只出全量包一种。
- **验证**：`--selftest` 179、`librf_opt.py --selftest` 124/0、`--gui-smoke` OK；
  SKILL 生成 GDS 与调研参考实现逐 shape/逐 text 一致（31 shapes、3 texts、bbox 相同）；
  `demo2m` 换工艺冒烟 PASS；Calibre DRC `geometry_clean`（仅 17 个密度检查，独立
  单元预期）、LVS 端口同一网络；`matrix --suite sym` 31 例全部 PASS（25 例构建并
  取 L/Q，6 例不可行组合按预期拒绝；其中 2 次批处理 virtuoso 启动段错误
  `rc=-11` 由新增重试吸收）。叠层模式 Q 低于
  单层是 M6 underpass 电阻的物理结果，不是几何缺陷。

## 1.5.1 — 2026-10-31

纯文本剪贴板迁移工具链。无运行时行为变更；`VERSION` 1.5.1。

- **tools/**：`make_kit.py` 按 `git ls-files` 打包（tar → xz/gz → base64 76 列）并切片为 heredoc 脚本；`make_onepaste.py` 把逐片 sha256、整包 sha256 与解包并入最后一片，粘贴次数等于片数；`verify_kit.py` 校验脚本纯 ASCII、单片上限、分片与整包 sha256；`make_file_kit.py` 生成按文件的 gzip+base64 落盘脚本。
- **文档**：README 增「无网络迁移」一节；DEVELOPMENT_GUIDE 增 §10 工具链契约（纯 ASCII、自校验、单片不超过 200000 字符、每次发版重新出包并验收）。
- **验证**：`--selftest` 179、`--gui-smoke` OK、`--help` 正常；核心包 9 文件单脚本一次粘贴，全量包 21 文件 xz 两片（gz 三片）；`verify_kit.py` 全部 PASS。

## 1.5.0 — 2026-10-31

代码压缩版。CLI 参数、GUI 页面与控件、报告 JSON 字段、配置键、测试断言的行为均不变；
只删注释、docstring、死代码和重复逻辑。13 个源码文件合计 32329 → 29308 行
（-3021，约 9.3%），相对 1.4.9 的 diff 为 +697/−3720。

- **注释与 docstring**：Python 侧按 AST 等价校验后删除（去注释前后语法树逐节点相同），
  SKILL 侧按 token 流等价校验后删除（字符串内容与括号结构逐字节保持），仅保留少量
  「改了会静默出错」的约束注释（如 `tell` 的 `G` 必须包一层、spectre 退出码不可信、
  `state_dir` 相对路径基准）。架构说明移入 `DEVELOPMENT_GUIDE.md`。
- **死代码**：`optimizer.py` 删 `audit_line`、`strategy_names`、`_tell_algo`、
  `Target.spec`；`librf_opt.py` 删 8 个未引用符号与一个空分支；`librf.il` 删 7 个
  未被调用的过程；`gui.py` 删已失效的 `_em_hook` 属性。`em_opt.py` 虽未接入生产
  路径，但有独立 CLI 自测且写入 1.2.x 的 CHANGELOG，保留。
- **结构去重**：`optimizer.py` 合并 JSONL 读写、manifest 记录、失败记录路径；三个
  重启半径计算收敛到 `_decay_radius`，三个 `restore` 的 RNG 解包收敛到
  `_restore_common`。`netlist_params.py` 抽出 `_mask_block_comments` / `_apply_edits`。
  `librf_opt.py` 抽出 `_prepare_write`（写入前备份）与 `_driver_text`（两处 driver.il
  生成）。`proc_cross_section.py` 合并 `_int_map`/`_float_map` 为 `_num_map`。
  `gui.py` 抽出表格增删改查辅助与变量扩展字段的改名/清理。`algorithm.py` 补
  `AlgorithmBase` 的公共辅助。
- **文档**：README 改成纯说明文，去掉修辞句；新增 `DEVELOPMENT_GUIDE.md`：架构与
  数据流、策略注册契约、网表改写四规则、8 个扩展点、回归三步，以及 SKILL 侧双模式/
  eval-serve 协议/版图几何不变量（原 `librf.il` 的 407 行注释压缩进 §9）。
- **验证**：`--selftest` 179、`netlist_create` 20/20、`netlist_params` 125/125、
  `em_align` 108、`em_opt` PASS、`librf_opt` 123、`test_proc_cross_section` 42、
  `--gui-smoke` OK、`--help` 正常。改动均为等价变换，无功能变更。

## 1.4.9 — 2026-10-30

跨模块一致性：补完 1.4.8 的续跑语义与空值配置链路，统一目标区间/频段与网表分析
类型在四个模块间的口径。接口形状不变。

- **`"resume": false` 仍会继承旧快照**：1.4.8 只归档 history/manifest/iter_*，
  `snapshot*.json` 与 `events.jsonl` 原样保留，`run()` 又无条件还原快照——新开
  一轮实际沿用旧算法状态，之后的续跑还会按新快照的 `index_next` 把上一轮更大的
  事件重新 ask/tell 灌进算法。现在 fresh 起点同时归档快照与事件，且显式新开时
  不再查询快照；快照记录已折叠的事件序号，续跑只补演其后的 kick/restart/评估
  事件（快照写在本轮停滞动作之前，按迭代号过滤会漏掉该轮 kick）。
- **`state_dir: null` 延迟崩溃**：1.4.8 的 loader 保留 null，`_ensure_eval` 与
  `--dump-state` 随后 `expanduser(None)` 抛 TypeError，GUI 载入还会显示成字符串
  `"None"` 并写回。现在 loader 把 null/空串/非字符串统一归一为 `""`，两个消费端
  按字符串处理，`--dump-state` 缺状态目录时给出可操作提示而不是 traceback，GUI
  空值回填为空行。
- **区间/频段反向与形状**：`Target` 的 band 反向（如 `["3G","1G"]`）会切空所有点、
  整轮恒为 BIG_ERROR；band 只有一个值时报 IndexError；`value_at` 的 freq 落在
  band 外同样永远无解。现在 band 接受二元列表并归一为升序（与
  `em_align._parse_band` 一致），形状错误给出 ValueError，freq 越界在配置期报错；
  `op=range` 的 goal/goal2 反向也归一为升序。
- **分析类型口径**：`dcop` 只在网表合并器 `_ANALYSES` 里，预检会判“无分析”、参数
  改写会把 `dcop1 dcop ...` 当实例；现在三张分析表一致，漂移测试由子集断言改为
  对称差断言。

## 1.4.8 — 2026-10-30

文档与配置一致性：修复 1.4.7 引入的两处 proc 解析回归（隐式 `dtemp`、负/表达式
指数），让 `"resume": false` 配置键真正生效，空 `state_dir` 不再被解析成配置
目录；回归门禁不再静默跳过横截面套件，按模板建议设置 `LIBRF_TECH_PROFILE` 的
机器上 `--selftest` 也能通过。接口形状不变。

- **proc 解析回归**：`_numeric_env` 上一版把 `dtemp` 默认值放到三轮 define 求值
  之后才注入，`define rho = r0*(1+0.003*dtemp)` 这类 define 链拿不到默认值，
  电阻率静默变 `None`（旧版 0.08）；`_safe_eval` 的幂上限只认裸字面量指数，
  `10**-3`、`2**p`、`2**(1+1)` 被误拒（旧版分别 0.001 / 8 / 4）。现在默认值在
  define 求值前注入且显式 `define dtemp` 仍可覆盖；指数先安全求值再检查 ±64，
  `9**9**9`、嵌套幂与 `2**65` 仍立即拒绝。验证：`test_proc_cross_section` 新增
  隐式 `dtemp` 与负/表达式指数 2 条已知答案用例（40→42），模块行数上限同步
  放宽到 800（原 760 已无余量）。
- **`"resume": false` 不生效**：续跑指纹冲突的报错让用户写该键，但只有函数形参
  被读取，配置文件里的键从未被消费，CLI 仍会续跑。`run_optimization` 现在在
  未显式传参时读取 `cfg["resume"]`（布尔或字符串），显式参数仍优先。验证：
  新增用例断言 5 轮历史后 `resume:false` 跑满 7 轮，而不是只补 2 轮。
- **空 `state_dir` 被当成配置目录**：`load_config` 对 `""`/`null` 也做相对路径
  拼接，配置目录因此变成真 `state_dir`，`auto_netlist:"auto"` 会尝试现场重建
  网表，配置指纹也随之改变。现在只解析非空字符串。验证：新增空串/null/相对/
  绝对四路用例。
- **帮助与文档**：`参数说明` 列出并不存在的 `settings.noise_sigma`，已删除并用
  用例保证帮助里的隐藏设置项都在引擎中有引用；`targets` 行补上 `band`/`freq`/
  `goal2`/`role`，`resume` 单独成行；`tech_profile.json` 的 `_doc_flow` 补上
  横截面绘制认识的 `gds`/`via_gds`/`text`。
- **回归门禁与工艺配置隔离**：`--selftest` 用 `except Exception` 导入横截面套件，
  语法错误或导入期异常会让 40 条用例静默消失却仍报 OK；且只按类名加载一个类，
  新增 `Test*` 不会被跑。现在只在 `ImportError`（模块确实缺失）时跳过，并按类
  自动加载套件内全部 `Test*`（`librf_opt` 的可选跳过同样收窄）。
  `TestTechProfile` 的自动加载用例未清 `LIBRF_TECH_PROFILE`，按模板建议配置
  自定义工艺的机器上 `--selftest` 会失败；现已隔离并新增环境变量正向用例，
  另用非默认 profile 断言字段确实被加载（原用例只比较与默认值相同的模板）。
  验证：`--selftest` 160→170 条。

## 1.4.7 — 2026-10-30

网表行解析与工艺横截面模块的修复：花括号块内的行注释不再吞掉块后网表
（1.4.6 自引入回归），proc 的溢出表达式与非有限数不再逃出解析器或静默
污染叠层；`--selftest` 纳入横截面套件。接口形状不变。

- **花括号块内的注释吞掉后续网表**：`_brace_opens` 在累计文本里遇到
  `//`（以及顶层解析器同样视为注释的 `#` / `;` / `*` 行首）直接结束扫描，
  而 `_logical_lines` 用空格拼接多行，物理行边界已经丢失。于是
  `sweep1 ... { // note ... }` 里的闭合 `}` 不再被计数，块之后
  的 `save`、`ac1 ac`、`include` 全被并进同一条逻辑语句；从 config view
  重建网表时 `merge_testbench` 看不到这些语句，分析/保存再次丢失。现在
  逻辑语句保留物理换行，注释只隐藏本行其余部分；`selftest.py` 的合并
  用例在块内加注释（修复前 `save PORT1:p` 丢失），`netlist_create`
  `_t_logical_lines_scope` 断言 analyses 为 `["sweep","ac"]`、saves 保留，
  并覆盖 `#`/`;`/`*` 三种注释前缀。
- **proc 非有限数与溢出**：`_safe_eval` 在 `float(10**400)` 处抛
  OverflowError（不在 except 内），`layer 10**400` / `define T=10**400`
  会让 `parse_proc` 抛出未捕获异常，而 GUI 横截面 `redraw` 只捕
  ProcParseError；`layer 1e999` / `layer nan` 被当成有限厚度，`layer inf`
  产生无限厚度，后续 z 全变 inf、绘图出现 NaN 坐标却返回 `ok=True`。
  新增 `_finite` 统一拒绝 inf/nan/溢出：`_safe_eval`、`_as_number`、
  `_int_map`、`_float_map`、`_pair_list` 都走它；`inf` 与 `infinity` 同义
  （无限半空间），其余非有限值抛 ProcParseError（含行号）。`_safe_eval`
  对幂运算设上限（常量指数 ≤64、禁止嵌套幂），`9**9**9` 不再长时间卡住
  GUI 重绘；`_numeric_env` 不再预置 `dtemp`，proc 里显式的
  `define dtemp = 25` 会真正参与温度相关 ρ（已知答案：0.08×(1+0.003×25)
  = 0.086）。验证：`test_proc_cross_section` 新增非有限输入、flow 映射、
  `_safe_eval`、`dtemp` 回归（31→40 条）。
- **测试门禁与覆盖**：部署了横截面模块时，`python optimizer.py --selftest`
  会同时运行 `test_proc_cross_section` 套件（README 承诺的回归入口不再
  漏掉横截面模块；模块缺失时与 `librf_opt` 一样降级跳过）；新增
  `TestSuiteRegistry`，未登记进 `_TEST_CLASSES` 的 `Test*` 类会直接失败；
  移除恒真的 `resolve_prog` 自比较断言、未使用的 `prepare_netlist` 导入
  与 full 模式测试里的死分支；Tk 不可用时改为 `skipTest` 而不是静默通过；
  补齐 `flow_from_profile` 列表写法、layermap 缺金属/文字层、间距容差、
  绘图异常兜底、assume/capacitor/define 等分支；移除横截面测试里恒真的
  dash 断言。验证：`--selftest` 119→160 条，`test_proc_cross_section`
  31→40 条，其余模块自测条数不变。

## 1.4.6 — 2026-10-30

网表模块（`netlist_create` / `netlist_params`）的解析与写回修复：ADE 命名的
分析语句不再在测试台合并时丢失，分析选项不再被当作设计变量改写，非有限数
不再写进网表；续行、`inline subckt` 作用域与注释中的花括号一并修正。接口
形状不变，均带回归测试。

- **命名分析在合并时丢失**：ADE 写的是 `<分析名> <类型> key=val`
  （`ac1 ac start=...`、`sp1 sp ports=...`），而 `_extract_testbench` 只把
  首 token 当分析类型，`merge_testbench` 也按首 token 去重。于是从
  config view 重建网表后，旧 deck 的 `ac1 ac` 整条消失，仿真没有分析可跑。
  新增 `_analysis_kind`：首 token 命中语句关键字（`save`/`parameters`/
  `subckt`/`include` 等）先排除，再看第二 token 是否为分析类型，首 token
  即类型的旧写法仍兼容；合并按**类型**去重，语句关键字不再误判。
  验证：`_t_merge_testbench` 断言 `["ac","sp","sweep"]` 全部合并、`save sp`
  仍按 save 处理；`_t_merge_named_write_back` 用注入 runner 跑完整
  `create_netlist`，确认 `ac1 ac`、`include`、`save` 写回且新参数保留。
- **分析类型集合漂移**：`netlist_params._ANALYSIS` 缺 `hbac`/`hbnoise`/
  `pac`/`pacnoise`/`pnoise`/`pstb`/`psp`/`ql`/`spnoise`/`sweephb`/`hbsp`/
  `mpde`/`acnoise`，`netlist_create._ANALYSES` 缺 `disto`/`fourier`/`hbsp`/
  `mpde`。缺失类型让 `pnoise1 pnoise ... dec=20` 被当成实例，一旦设计变量
  叫 `dec`，分析选项就被改写并报告成功。两处集合补齐，并在
  `selftest.py` 断言两者覆盖 `optimizer._ANALYSIS_KEYWORDS`、语句关键字
  覆盖 `_NETLIST_DIRECTIVES`，防止再次漂移。验证：新自测断言
  `pnoise1/hbnoise1/pac1/sweephb1` 不产生实例、`dec=20` 不被改、
  顶层 `parameters dec` 正常打补丁。
- **非有限数写入网表**：`format_value(float("inf"))` 产出 `infT`，
  `10**400` 在 `float()` 处 OverflowError，`"1e999"` 解析为 inf；fixed
  变量同样不校验。这些值会以 `ok=True` 写进 deck，spectre 直接报语法错。
  新增 `_is_finite`（布尔放行、数值走 `math.isfinite`、字符串先拒
  `inf`/`nan` 字面量再试解析，表达式原样放行），`format_eng` 对 inf/nan/
  溢出抛 `ValueError`，`patch_netlist` 的 values 与 fixed 入口把非有限值
  记为 missing 并告警，不改写文本。验证：`inf`/`-inf`/`nan`/`10**400`/
  `"1e999"`/`"nan"` 六种输入均 `ok=False` 且网表字节不变。
- **续行与作用域**：`_logical_lines` 只认行尾 `\` 和花括号，文档声称的
  前导 `+` 续行实际不生效，`parameters a=1` + `+ b=2` 只识别到 `a`；花括号
  计数不跳过注释，注释里一个孤立的 `{` 会把余下整个 deck 并成一条语句；
  `_top_parameters` 只匹配 `subckt`，`inline subckt` 的内部参数被当成顶层
  参数带走。现在前导 `+` 独立成为续行标记，`_brace_opens` 跳过 `//`、
  `/* */` 与引号内文本，`inline subckt` 计入作用域。验证：
  `_t_logical_lines_scope` 覆盖三种输入及 `carry_over_parameters` 回写。
- **网表校验分支**：`_validate_netlist` 的空路径/不存在/过小/缺
  `simulator lang=spectre` 四条拒绝路径此前无测试，补齐 `_t_validate_netlist`；
  `merge_testbench` 删除已不用的 `existing_first` 计算。
- 自测：`netlist_create --selftest` 16→20 组，`netlist_params --selftest`
  103→125 条，`selftest.py` 119 条全通过。

## 1.4.5 — 2026-10-30

EM 链路（`em_align` / `em_opt` / `em_bridge`）的验收语义与真实路径修复：跨 stage
选优与 `status` 同口径、`Q@f0` 按请求频点求值、sNp 与 GUI 字段贯通，均带回归
测试；搜索预算与接口形状不变。

- **跨 stage 选优口径不一致**：`solve()` 直接用各 stage 子目标的 J 跨级比较，
  而子目标键集随 stage 增大，J 单调变大，后一级几乎必然“胜出”；`status` 又取
  最后一个 stage 的子集距离，于是 `tl` 目标会出现 θ=1.2°、d=17.76 却报
  `hit_grid`。现在每级返回的指纹都用**全目标**重算 `full_distance/full_J`
  再选优，`status` 只由全目标距离决定；`stages` 行同时保留子集与全目标数值。
- **`Q@f0` 用错频点**：`distance_of`/`J_of` 只从 `target["f0"]` 取频点，请求里的
  `f_op` 没传进来；真实指纹只有 `Qcurve` 时按缺失键记 99，优化器永远认为没达标。
  两个函数新增可选 `f0=` 参数，`solve`/搜索循环/`_result` 均传 `request.f_op`。
  已知答案：`Qcurve=[[1e9,7],[2e10,9]]`、目标 `Q@f0=8`、`f_op=1e10` 的归一化
  距离为 0.032895（与 `em_bridge` 一致），旧实现为 99。
- **元数据键与 `None` 目标计分**：`target` 里混入 `kind/ports/f0/band/band_hi`
  时被当成指标（字符串比较或 `float(None)` 崩溃，`theta: None` 直接 TypeError）。
  现在 `distance_of`/`J_of` 跳过元数据键、`None`/布尔/非数值目标，与
  `em_bridge.distance_of` 同口径；没有任何可验收指标时返回 `inf` 而不是 0，
  避免空目标被当成已达标。
- **几何缓存串味**：缓存键只有几何哈希，同一几何在 2 GHz/5 GHz、或 ind/ct
  之间复用同一条指纹（mock 与真实路径也互换）。键改为
  `device_type|f_op|geom_hash|mock/real`，cell 名仍只用几何哈希保持稳定。
- **真实路径丢 sNp**：`make_layout_emx` 返回的 `out["snp"]` 被丢弃，指纹按扫描
  首点提取；`em_bridge` 又只认 `snp_path`，于是 GUI 的 sNp 列恒空、库中
  `snp_sha1` 恒空导致幂等键失效。现在按 `f_op` 从 sNp 重提取指纹并带回路径，
  `EmBridge._solve` 先内容寻址登记 sNp 再入库。
- **GUI 契约缺字段**：`EmBridge.run` 的 `em_actual[var]` 只有 requested/grid_key，
  GUI 读取的 `target/status/snp_path/record` 全缺。已补齐，并让单器件 `f_op`
  解析失败只标记该器件 `error`，不再让整次电路优化抛异常；省略
  `device_type` 的请求不再 KeyError；`error/miss` 结果不再写成 `usable` 记录，
  SQLite 读回保留 `usable` 键。
- **搜索失败记账**：`infra_streak` 成功后不清零（三次累计误判 `infra_fail`），
  持续 `dnc` 又不占预算，会空转到 `time_cap`。现在成功即清零，非 infra 的每次
  尝试都计入预算。
- 自测：`selftest.py` 新增 5 条 em_bridge 回归；`em_opt --selftest` 新增 9 组
  回归断言（全目标选优、`Q@f0`、元数据/`None`、缓存分离、省略 device_type、
  非数值/空目标、dnc 预算、infra 清零、真实路径 sNp/f_op）。全量：selftest 119、
  netlist_create 16、netlist_params 103、em_align 108、em_opt PASS、
  librf_opt 123、proc_cross_section 31 全部通过；`--gui-smoke` OK。

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
