# Virtuoso Optimizer

Cadence Virtuoso / spectre 电路参数优化工具。修改原理图变量后自动重生成网表，
调用 spectre 迭代，输出收敛报告。版图部分按工艺角色生成电感、传输线、变压器
和耦合线，可接 EMX 做几何迭代。

`Version 1.6.1 · MIT · Python 3.6+（仅标准库；收敛图需要 matplotlib，可选）`

## 功能

- 直接调用 spectre，不需要常驻 ADE 会话。网表只读一次，每轮改写 parameters 行
  后重新仿真；首轮约 7 秒（求值服务冷启动），之后约 0.5 秒/轮。
- 启动前可自动调用 Virtuoso `createNetlist(?recreateAll t)` 重生成网表，支持
  schematic 和 config 视图。失败时保留旧网表并在日志中说明；网表缺少分析语句
  时在启动阶段报错。
- 支持 config 和层次网表。除顶层 parameters 外，也会修改子电路默认值和实例覆盖
  的参数；变量在网表中找不到时报错。
- 14 种搜索策略：simplex、coordinate、global、de、lshade、cmaes、cmaes_ipop、
  tpe、nsga2、weighted、worstspec、portfolio、random、grid，支持多目标
  （scalar、spec、pareto）。
- 支持断点续跑。snapshot 和 events 用于还原算法状态；没有快照时把历史成功评测
  注入算法，避免重复仿真。
- EM 两阶段对齐：以实测或 EM 曲线为目标重跑同一理想网表，输出逐变量修正方向、
  曲线偏差和 aligned 结论。

## 快速开始

```sh
python3 optimizer.py --gui                        # 图形界面
python3 optimizer.py task.json                    # 真实运行（需 Cadence / spectre）
python3 optimizer.py task.json --mock --max-iter 20   # 无 license 走流程
python3 optimizer.py task.json --dump-state       # 只解析 ADE 状态，不仿真
python3 optimizer.py --selftest                   # 回归测试
python3 optimizer.py --gui-smoke                  # GUI 无头冒烟
```

界面需要在图形桌面中运行，或设置 `DISPLAY` 指向可用的 X 显示。没有显示时程序
打印 `GUI_SMOKE_FAIL: no display` 后退出。

## 版图 PCell（差分对称电感）

`lri_pcell.il` 是 Virtuoso PCell 外壳，几何内核 `lriI3BuildSym` 在 `librf.il`。
参数：横向/纵向半径 `rx`/`ry`、每臂圈数 `n`、线宽 `w`、线间隔 `s`、拐角切角
`chamfer`（0..1，2−√2 为正八边形）、差分线长 `lead_len`、差分线间距 `lead_gap`、
差分线宽 `lead_w`、中心抽头 `ct`、叠层 `stack`、主金属 `metal_main`、副金属
`metal_aux`。

```skill
load("librf.il")
load("lri_pcell.il")
lriPcellTechSmic55()               ; 读 tech/smic55ll_ulp_rf_profile.il
lriPcellDefine("MYLIB" "ind_sym") ; 注册 PCell，随后在 Library Manager 实例化
```

主/副金属可逐实例指定（如 TM2/TM1、TM1/M6），跨线层与走线层之间按工艺档的
通孔链逐级自动补 landing pad；叠层模式只接受相邻金属。

## 工艺档（换工艺不改代码）

一个 `tech/<key>.json` 是唯一事实源，同时派生 SKILL 工艺档、GDS 层映射和评估
配置：

```sh
python3 tools/gen_tech.py tech/smic55ll_ulp_rf.json --eval-config eval/config_smic55.json
```

产物为 `tech/<key>_profile.il`（层角色、通孔链、`wide_space`、文字层）、
`stream/<key>.map`、`eval/config_<key>.json`。换工艺时复制一份 JSON 改层号与
DRC 数值即可；`tech/demo2m.json` 是两层金属的最小示例。

## PCell 质量评估（eval/）

评估器只依赖真实 GDS：几何规则 G0–G12（线宽、外框、间距、自交、通孔尺寸/
宿主/包络/间距、标签归属、连通性、圈数、对称性、内圈净空、引线与参数一致、
叠层），再按需接 EMX（L/Q/R/SRF）和 Calibre DRC/LVS。

```sh
cd eval
python3 bin/cli.py run --config config_smic55.json --params '{"rx":60,"ry":60,"n":2}' --work runs/one
python3 bin/tech_smoke.py demo2m --config config_demo2m.json --work runs/tech_demo2m
python3 bin/cli.py matrix --suite sym --config config_smic55.json --work runs/sym
```

`bin/cli.py matrix --suite sym` 用真实 SKILL PCell 跑参数扫掠（含 `expect_fail`
的不可行组合），输出每个用例的状态与 L/Q。`bin/skillgen.py` 通过
`virtuoso -nograph` 建 cellview 再用 `strmout` 导出 GDS，路径可用
`LIBRF_VIRTUOSO` / `LIBRF_STRMOUT` / `LIBRF_PDK_MAP` 覆盖。

## 无网络迁移（纯文本剪贴板）

目标机不能传文件、只能通过 VNC 复制文本时，用 `tools/` 把项目打成纯文本安装
脚本：本地复制整段脚本，在目标机文本编辑器中粘贴保存，`sh` 执行即可，脚本自带
sha256 校验与解包。只出全量包一种（全部功能，不裁剪）；单元测试不进包
（`--include-tests` 可打回），包内 `--selftest` 会提示未附带测试：

```sh
python3 tools/make_kit.py --repo . --out dist/kit --limit 200000 --format xz
python3 tools/make_onepaste.py --kit dist/kit --ext xz
python3 tools/verify_kit.py dist/kit
```

- RealVNC 剪贴板上限 256 kB，超限会整段粘贴失败并粘出上一次的内容；脚本逐片
  校验 sha256，失败时把剪贴板换成任意小文本后重抄该片。
- 目标机需要 `python3`、`base64`、`tar`，以及对应的 `xz` 或 `gzip`。

## GUI 页面

页面顺序：环境、设计、变量、目标、仿真设置、进度、结果、工艺、版图 EM、
EM 对齐、EM 融合。

- 环境：Cadence 和 spectre 环境变量。留空使用系统默认值，支持自动探测和自检。
- 设计：library、cell、view、ADE 状态、网表路径和输出目录。可以勾选“开始前
  自动生成网表”，也可以点“立即生成网表”。
- 变量和目标：搜索变量（init/min/max）与目标（指标、算子、容差、频段）。目标页
  底部是搜索设置，包括迭代数、耐心、改进阈值、失败上限、重启次数、种子和策略。
- 仿真设置：保存原始数据、仿真超时，以及 ADE 设置的只读实时展示。
- 进度和结果：收敛图、变量柱状条、动态曲线、迭代日志与最终报告。
- 工艺和版图 EM：按工艺角色生成版图器件，接 EMX 做几何迭代。
- EM 对齐和 EM 融合：两阶段对齐与 EM 库融合状态。

帮助菜单包含使用说明、算法说明、参数说明和版本信息。

## 任务配置

任务文件为 UTF-8 JSON。相对路径的 `state_dir` 以配置文件所在目录为基准。

| 键 | 作用 |
|---|---|
| `auto_netlist` | `"off"` 关闭；`"auto"`（默认）有 `state_dir` 或 library+cell 才执行；`"always"` 强制执行 |
| `library` / `cell` / `view` / `state` / `state_dir` | 设计与 ADE L 状态 |
| `netlist` / `sim_root` / `sim_timeout` | 网表路径、仿真树根、单轮超时 |
| `variables` / `fixed_variables` | 搜索变量（`init`/`min`/`max`，可选 `scale`、`integer`）与固定值 |
| `targets` | `output` + `metric`（scalar / band_min / band_max / band_flatness / band_mean / value_at）+ `band`（频段）/ `freq`（value_at）+ `op`（`>=`/`<=`/`==`/`range`）/`goal`/`goal2`/`tol`/`weight`；spec/pareto 模式用 `role`（error / constraint / objective）区分子目标 |
| `settings` | `max_iter`、`patience`、`improve_rel`、`fail_limit`、`max_restarts`、`seed`、`strategy`、`mode` |
| `output_dir` | run 目录；CLI 在同目录自动续跑，GUI 用「续跑」勾选控制 |
| `resume` | `false` 时忽略同目录历史，旧数据归档后从迭代 0 新开；缺省自动续跑 |

## 运行产物

`history.csv`（每轮变量与误差）、`iter_XXXX/`（record.json、result.txt、psf/）、
`report.json`（最优解、达标判定、灵敏度）、`convergence.png`、`audit.jsonl`（操作审计）、
`snapshot*.json` + `events.jsonl`（精确续跑）。
