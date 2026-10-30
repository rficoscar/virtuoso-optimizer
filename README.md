# Virtuoso Optimizer

把 Cadence Virtuoso / spectre 的电路参数优化串成一条命令：改原理图或变量 →
自动重生成网表 → 直连 spectre 迭代 → 收敛报告。版图侧可按工艺角色生成电感 /
传输线 / 变压器 / 耦合线，接 EMX 做几何迭代。

`Version 1.4.9 · MIT · Python 3.6+（纯标准库；matplotlib 仅用于可选收敛图）`

## 特点

- **直连 spectre，不开 ADE 会话**：网表读入一次，每轮只改写 parameters 行再跑
  spectre，首轮约 7 秒（求值服务冷启动），之后约 0.5 秒/轮。
- **启动前自动生成网表**：调用 Virtuoso `createNetlist(?recreateAll t)`，schematic /
  config 视图都支持；失败回退旧网表，不静默改设计。网表缺分析语句时启动即报错，
  不会跑满预算才发觉得分全是惩罚。
- **config / 层次网表改写**：不只改顶层 `parameters`，还处理子电路默认值与被实例
  覆盖的参数；变量在网表里找不到就报错，不“改了没用”。
- **14 种搜索策略**：simplex / coordinate / global / de / lshade / cmaes /
  cmaes_ipop / tpe / nsga2 / weighted / worstspec / portfolio / random / grid，
  支持多目标（scalar / spec / pareto）。
- **断点续跑与历史热启动**：snapshot + events 精确还原算法状态；没有快照时把历史
  成功评测注入算法，不重复仿真。
- **EM 两阶段对齐**：以实测 / EM 曲线为目标重跑同一理想网表，输出逐变量修正方向、
  曲线偏差与 `aligned` 结论。

## 快速开始

```sh
python3 optimizer.py --gui                        # 图形界面
python3 optimizer.py task.json                    # 真实运行（需 Cadence / spectre）
python3 optimizer.py task.json --mock --max-iter 20   # 无 license 走流程
python3 optimizer.py task.json --dump-state       # 只解析 ADE 状态，不仿真
python3 optimizer.py --selftest                   # 回归测试
python3 optimizer.py --gui-smoke                  # GUI 无头冒烟
```

界面启动前提：在图形桌面里运行（或 `DISPLAY` 指向可用的 X 显示）。
SSH 里没有 `DISPLAY` 时程序只会打印 `GUI_SMOKE_FAIL: no display` 后退出。

## GUI 页面

`环境` → `设计` → `变量` → `目标` → `仿真设置` → `进度` → `结果` → `工艺` →
`版图 EM` → `EM 对齐` → `EM 融合`

- **环境**：Cadence / spectre 环境变量，留空=系统默认，可自动探测与自检。
- **设计**：library / cell / view、ADE 状态、网表路径、输出目录，可勾「开始前自动
  生成网表」或点「立即生成网表」。
- **变量 / 目标**：搜索变量（init/min/max）与目标（指标、算子、容差、频段）；
  目标页底部是搜索设置（迭代数、耐心、改进阈值、失败上限、重启、种子、策略）。
- **仿真设置**：运行选项（保存原始数据、仿真超时）+ ADE 设置只读实时展示。
- **进度 / 结果**：收敛图、变量柱状条、动态曲线、迭代日志与最终报告。
- **工艺 / 版图 EM**：按工艺角色生成版图器件，接 EMX 做几何迭代。
- **EM 对齐 / EM 融合**：两阶段对齐与 EM 库融合状态。

菜单「帮助」有使用说明、算法说明（策略选型与原理）、参数说明（每个设置项的
取值与影响）、关于。

## 任务配置

任务为 UTF-8 JSON；相对路径的 `state_dir` 以配置文件所在目录为基准。

| 键 | 作用 |
|---|---|
| `auto_netlist` | `"off"` 关闭；`"auto"`（默认）有 `state_dir` 或 library+cell 才执行；`"always"` 强制执行 |
| `library` / `cell` / `view` / `state` / `state_dir` | 设计与 ADE L 状态 |
| `netlist` / `sim_root` / `sim_timeout` | 网表路径、仿真树根、单轮超时 |
| `variables` / `fixed_variables` | 搜索变量（`init`/`min`/`max`，可选 `scale`、`integer`）与固定值 |
| `targets` | `output` + `metric`（scalar / band_min / band_max / band_flatness / band_mean / value_at）+ `band`（频段）/ `freq`（value_at）+ `op`（`>=`/`<=`/`==`/`range`）/`goal`/`goal2`/`tol`/`weight`；spec/pareto 模式用 `role`（error / constraint / objective）区分子目标 |
| `settings` | `max_iter`、`patience`、`improve_rel`、`fail_limit`、`max_restarts`、`seed`、`strategy`、`mode` |
| `output_dir` | run 目录；CLI 同目录自动续跑，GUI 用「续跑」勾选控制 |
| `resume` | `false` 时忽略同目录历史，旧数据归档后从迭代 0 新开；缺省自动续跑 |

## 运行产物

`history.csv`（每轮变量与误差）、`iter_XXXX/`（record.json、result.txt、psf/）、
`report.json`（最优解、达标判定、灵敏度）、`convergence.png`、`audit.jsonl`（操作审计）、
`snapshot*.json` + `events.jsonl`（精确续跑）。