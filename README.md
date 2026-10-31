# Virtuoso Optimizer

Cadence Virtuoso / spectre 电路参数优化工具。修改原理图变量后自动重生成网表，
调用 spectre 迭代，输出收敛报告；版图部分按工艺角色生成电感、传输线、变压器
和耦合线，可接 EMX 做几何迭代。界面用 Tk 编写，直接运行 `optimizer.py` 即可。

## 功能

- 直接调用 spectre，不需要常驻 ADE 会话。网表只读一次，每轮改写 parameters 行
  后重新仿真；首轮约 7 秒（求值服务冷启动），之后约 0.5 秒/轮。
- 启动前可自动调用 Virtuoso `createNetlist(?recreateAll t)` 重生成网表，支持
  schematic 和 config 视图；失败时保留旧网表并在日志中说明。
- 支持 config 与层次网表：除顶层 parameters 外，也会修改子电路默认值和实例覆盖
  的参数；变量在网表中找不到时报错。
- 14 种搜索策略：simplex、coordinate、global、de、lshade、cmaes、cmaes_ipop、
  tpe、nsga2、weighted、worstspec、portfolio、random、grid，支持多目标
  （scalar、spec、pareto）。
- 支持断点续跑：snapshot 和 events 用于还原算法状态；没有快照时把历史成功评测
  注入算法，避免重复仿真。
- 版图：按工艺角色生成电感、传输线、变压器与耦合线，出图后可直接导出 GDS、
  渲染 PNG 截图，并把 cell / GDS / 工作目录自动带入 EM 页。
- EM：两阶段对齐（实测或 EM 曲线 → 逐变量修正方向、曲线偏差、aligned 结论）、
  EM 优化迭代与 EM 融合看板。
- 工艺检查：开始版图/EM 任务前核对工艺库路径、layermap/proc 解析与叠层/层号，
  硬错误直接中止任务。

## 目录结构

```
optimizer.py                唯一入口：python3 optimizer.py 打开图形界面
src/virtuoso_optimizer/     主包（GUI、算法、CLI、网表、EM、版图 lri/）
  data/                     随包数据：librf.il / lri_pcell.il / 工艺档与层映射
src/librf_opt.py            版图/EM 兼容模块（供脚本 import 与开发自测）
src/em_opt.py               EM 融合兼容模块（同上）
release/                    剪贴板传输脚本（仅仓库，不进交付包）
tests/                      回归测试与 fixtures（不进交付包）
docs/                       开发指南与界面评估（不进交付包）
eval/                       PCell 质量评估（GDS 几何、EMX、Calibre）
tools/                      打包、工艺档生成、GDS 渲染等开发工具
LICENSE  README.md          许可与说明
```

## 快速开始

```sh
python3 optimizer.py
```

需要 Python 3.6+ 与图形桌面；没有显示时程序打印 `GUI_SMOKE_FAIL: no display`
后退出。收敛图需要 matplotlib（可选），版图截图需要 Pillow（可选）。真实仿真
需要本机可用的 Virtuoso / spectre；没有 license 时可在界面里用 mock 走流程。

开发自检：`python3 optimizer.py --selftest`、`python3 optimizer.py --gui-smoke`。

## 界面

页面顺序：环境、设计、变量、目标、迭代、进度、结果、工艺、版图、EM、EM 对齐、
EM 融合。

- 环境：Cadence 和 spectre 环境变量，留空使用系统默认值，支持自动探测与自检。
- 设计：库、单元、视图、状态名/状态目录、工作目录、网表与输出目录、分析；
  可勾选「开始前自动生成网表」，次要项（任务名称、网表视图、网表根目录）在
  「高级…」里。
- 变量与目标：参与优化的变量表、不参与优化的固定变量表；目标表支持算子、容差、
  频段与限值曲线。
- 迭代：搜索设置（迭代数、停滞耐心、改进阈值、失败上限、重启次数、随机种子、
  搜索策略）与仿真选项（保存原始数据、仿真超时）。
- 进度和结果：收敛图、变量柱状条、动态曲线、迭代日志与最终报告。
- 工艺：工艺库与 layermap/proc 路径、工艺检查、横截面。
- 版图：器件类型与几何参数、示意图、仅出图 / 出图+EM+views / 渲染截图。
- EM：EM 仿真参数、曲线、EMX 命令行预览；EM 优化迭代在子标签里。
- EM 对齐与 EM 融合：两阶段对齐报告与 EM 库状态看板。

## 版图 PCell（差分对称电感）

`lri_pcell.il` 是 Virtuoso PCell 外壳，几何内核 `lriI3BuildSym` 在 `librf.il`。
参数：横向/纵向半径 `rx`/`ry`、每臂圈数 `n`、线宽 `w`、线间隔 `s`、拐角切角
`chamfer`（0..1，2−√2 为正八边形）、差分线长 `lead_len`、差分线间距 `lead_gap`、
差分线宽 `lead_w`、中心抽头 `ct`、叠层 `stack`、主金属 `metal_main`、副金属
`metal_aux`。

```skill
load("src/virtuoso_optimizer/data/librf.il")
load("src/virtuoso_optimizer/data/lri_pcell.il")
lriPcellTechSmic55()               ; 读 data/tech/smic55ll_ulp_rf_profile.il
lriPcellDefine("MYLIB" "ind_sym") ; 注册 PCell，随后在 Library Manager 实例化
```

主/副金属可逐实例指定（如 TM2/TM1、TM1/M6），跨线层与走线层之间按工艺档的
通孔链逐级自动补 landing pad；叠层模式只接受相邻金属。

## 工艺档（换工艺不改代码）

一个 `data/tech/<key>.json` 是唯一事实源，同时派生 SKILL 工艺档、GDS 层映射和评估
配置：

```sh
python3 tools/gen_tech.py src/virtuoso_optimizer/data/tech/smic55ll_ulp_rf.json \
    --eval-config eval/config_smic55.json
```

产物为 `data/tech/<key>_profile.il`（层角色、通孔链、`wide_space`、文字层）、
`data/stream/<key>.map`、`eval/config_<key>.json`。换工艺时复制一份 JSON 改层号与
DRC 数值即可；`data/tech/demo2m.json` 是两层金属的最小示例。

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

目标机不能传文件、只能通过 VNC 复制文本时，`release/` 里是现成的传输脚本：

1. 依次把 `release/paste_001.sh`、`release/paste_002.sh` 的内容粘贴到目标机终端；
2. 执行 `sh check.sh && sh unpack.sh`，校验并解包。

两个 `paste_*.sh` 各自内含一段 base64 数据（`p001.b64` / `p002.b64`），
`check.sh` 校验分片，`unpack.sh` 校验整包 sha256 后解出安装目录。RealVNC 剪贴板
上限 256 kB，超限会整段粘贴失败并粘出上一次的内容；若 `check.sh` 报某片不符，
把剪贴板换成任意小文本后重抄该片。目标机需要 `python3`、`base64`、`tar` 与
`xz`。

需要重新生成传输包时：

```sh
python3 tools/make_kit.py --repo . --out dist/kit --limit 200000 --format xz
python3 tools/make_onepaste.py --kit dist/kit --ext xz
python3 tools/verify_kit.py dist/kit
```

## 配置文件

界面里的设置可保存为 UTF-8 JSON 任务文件（相对路径的 `state_dir` 以配置文件所在
目录为基准）。主要键：

| 键 | 作用 |
|---|---|
| `auto_netlist` | `"off"` 关闭；`"auto"`（默认）有 `state_dir` 或 library+cell 才执行；`"always"` 强制执行 |
| `library` / `cell` / `view` / `state` / `state_dir` | 设计与 ADE L 状态 |
| `netlist` / `sim_root` / `sim_timeout` | 网表路径、仿真树根、单轮超时 |
| `variables` / `fixed_variables` | 搜索变量（`init`/`min`/`max`，可选 `scale`、`integer`）与固定值 |
| `targets` | `output` + `metric`（scalar / band_min / band_max / band_flatness / band_mean / value_at）+ `band`（频段）/ `freq`（value_at）+ `op`（`>=`/`<=`/`==`/`range`）/`goal`/`goal2`/`tol`/`weight`；spec/pareto 模式用 `role`（error / constraint / objective）区分子目标 |
| `settings` | `max_iter`、`patience`、`improve_rel`、`fail_limit`、`max_restarts`、`seed`、`strategy`、`mode` |
| `output_dir` | run 目录；界面用「续跑」勾选控制是否沿用历史 |
| `resume` | `false` 时忽略同目录历史，旧数据归档后从迭代 0 新开；缺省自动续跑 |

## 运行产物

`history.csv`（每轮变量与误差）、`iter_XXXX/`（record.json、result.txt、psf/）、
`report.json`（最优解、达标判定、灵敏度）、`convergence.png`、`audit.jsonl`（操作审计）、
`snapshot*.json` + `events.jsonl`（精确续跑）。

## 许可

MIT，见 `LICENSE`。
