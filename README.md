# Virtuoso Optimizer

Cadence Virtuoso / spectre 电路参数优化工具。修改原理图变量后自动重生成网表，
调用 spectre 迭代，输出收敛报告。版图部分按工艺角色生成电感、传输线、变压器
和耦合线，可接 EMX 做几何迭代。

`Version 1.5.0 · MIT · Python 3.6+（仅标准库；收敛图需要 matplotlib，可选）`

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
