---
name: virtuoso-pcell
description: Cadence Virtuoso 版图 PCell 的几何守门与讲解（本仓库 virtuoso-optimizer 只剩 sym 对称差分电感）。当用户要读/改 PCell 的 SKILL(.il) 或镜像它的 Python 几何内核、问电感/八边形环/underpass/via/中心抽头/中心桥/X 交叉到底怎么画、抱怨"版图图形看着对但面积/DRC/连通性不对"、或要求把 PCell 的绘制逻辑讲清楚并出图时使用。强制先判定真相源，再用可执行的不变量检查器挡错，最后按真实层序出图。
---

# Virtuoso PCell 几何守门

PCell 的几何 bug 几乎都是**静默**的：不报错、图形看着还像个螺旋，只有量面积、
对账顶点、查连通性才暴露。这个 skill 的用途就是把"看着对"变成"量过对"。

## 何时用

- 读/改 PCell 几何代码：`librf.il` 里的 `lri*` / `librf*` 过程，或 `lri/layout.py` 这类 Python 镜像
- 用户描述版图问题：块溢到下一圈、孔悬空、两臂不对称、换圈处短路、via 偏出、端口悬空
- 用户要"讲清楚这个 PCell 是怎么画的"、要解剖图/分层图
- 改 `w/s/n/lead_gap/ct` 等参数前后做回归

**不做**：不管工艺库安装、不管 DRC deck 本身、不替你跑 Virtuoso GUI。
需要真实落版图时给命令，不假装执行过。

## 0. 仓库锚点（v1.8.1 / commit 78a6720，行号漂移时用 grep 重定位）

| 角色 | 文件 |
|---|---|
| SKILL 真相源 | `src/virtuoso_optimizer/data/librf.il`（2251 行，只剩 sym 拓扑） |
| Python 镜像 | `src/virtuoso_optimizer/lri/layout.py`（656 行） |
| PCell 壳 / CDF | `src/virtuoso_optimizer/data/lri_pcell.il` |
| 实机验收 | `tools/pcell_check.py`（要 Virtuoso，本机跑不了） |
| 几何自测 | `src/virtuoso_optimizer/lri/selftest_librf.py`，入口 `librf_opt --selftest` |

Python 内核的关键入口（layout.py）：

```
lri_norm_geom:63      lri_validate:156      lri_sym_under_poly:194
lri_i3_rings:228      lri_i3_cross_gap:254  lri_i3_half_xy:263
lri_i3_via_span:280   lri_i3_arm:289        lri_i3_build:376
lri_build_ind:433     lri_draw_sketch:464   PRESETS:600  GEOM_FORM:615
```

SKILL 侧：`lriI3Rings:1696`、`lriI3ViaSpan:1734`、`lriI3CrossGap:1748`、
`lriI3HalfXY:1755`、`lriI3Arm:1781`、`lriI3BuildSym:2009`、`librfSelfTest:2214`；
落库调用点在 `librf.il:868`（`ok = lriI3BuildSym(cv req)`）。

受支持参数（`GEOM_FORM`／`lri_build_ind` 唯一真口径）：`kind`(只有 sym)、`n`、`w`、`s`、
`dout`、`rx`、`ry`、`chamfer`、`lead_len`、`lead_gap`、`lead_w`、`ct`、`stack`、
`metal_main`、`metal_aux`、`ind_mark`。**没有** `shape`/`seam` 这类旧参数。

## 工作流（五步，每步都有完成判据）

### 第 1 步 判定真相源（不跳过）

同一个几何有**两份实现**，改错边等于没改：

| 现象 | 真相源 |
|---|---|
| 用户给的是本仓库内 `.il`、或要改 Virtuoso 里的行为 | `data/librf.il` |
| 只动 Python 预览/优化器 | `lri/layout.py`，但仍要回头看 `.il` 是否同律 |
| 两边都动 | 一次提交，提交信息写两端 `file:line` |

判据：说得出"这次改动落在哪个文件哪一行，另一边是否需要同步"。

> 陷阱：机器上可能还有更早的**超集** `librf.il`（v10，3823 行，含 se/diff/f8/叠层变压器）。
> 那不是本仓库的真相源——本仓库 v1.8.0 起裁掉了那些拓扑，超集的行号与本仓库差几百行，
> 拿它的 `librf.il:NNNN` 去读 `.il` 会读错过程。**以 `wc -l` 为 2251 行、开头注释写
> "dual mode" 的这份为准。**

### 第 2 步 把几何倒成 geo.json

判几何对错不需要 Virtuoso。用仓库自带脚本：

```bash
# 任意参数组合；输出带 w/s/n/ct 的 geo.json
python .dsh/skills/virtuoso-pcell/scripts/dump_geo.py --w 8 --s 3 --n 3 --rx 80 --ry 60 -o geo.json
```

要自己写片段的话，注意 `lri_build_ind` 的返回值**只有几何**，检查器还要设计参数：

```python
import json, sys
sys.path.insert(0, "src")
from virtuoso_optimizer.lri import layout as L
p = dict(kind="sym", w=8.0, s=3.0, n=3, rx=80.0, ry=60.0, ct=0)
g = L.lri_build_ind(dict(p))                 # w、s 是必填键
g.update({k: p[k] for k in ("w", "s", "n", "ct")})   # 补检查器要的设计参数
json.dump(g, open("geo.json", "w"))
```

判据：`geo.json` 里 `metal` 是**中心线折线**列表、`under` 是下层 TM1 中心线、
`under_poly` 才是扫掠多边形、`vias=[(x,y,span)]`。分不清这三者，后面全会判错。
（`rx`/`ry` 都留空时二者都取 `dout/2`；要椭圆线圈必须显式给 `ry`。）

### 第 3 步 跑不变量检查器（硬门槛）

```bash
python .dsh/skills/virtuoso-pcell/scripts/check_geometry.py geo.json          # 人读
python .dsh/skills/virtuoso-pcell/scripts/check_geometry.py geo.json --json    # 机读
python .dsh/skills/virtuoso-pcell/scripts/check_geometry.py --selftest        # 改过检查器才需要
```

判据：`exit 0`。FAIL 一律先解释清楚是几何错还是参数本就不可行，**不许改检查器阈值把它变绿**。

| code | 判据 | 典型根因 |
|---|---|---|
| E01 | 零长度段/重复点 | v1.8.2 起引线改 2 点折线、起点取外圈内沿 `b0-w/2`，默认参数不再触发；再出现就是真有重复点 |
| E02 | 中心线自交 | 环端 `xe` 外推量算错，斜段穿过本体 |
| E03 | 段角不是 45° 整数倍 | 切角 `c` 没随圈号收缩；underpass y 偏移抄错 |
| E04 | under_poly 自交/零面积 | 收尾 miter 顶点补错 |
| E05 | 两中心线**平行**走线、中心距 < w 且沿走向重叠 > w/2（金属粘连） | 节距 `p=w+s` 被写死或漏改；`ct` 柱压到内圈 |
| E06 | under_poly 面积 vs 等宽扫掠差 >2 µm² 且 >1% | **端头多画楔形**（历史 55 µm² 事故） |
| E07 | underpass 侵入主金属 w/2 带 | 跨圈 y 位置错、端帽方向错 |
| E08 | via 没同时落在两层中心线上 | 环端/via 位置各自算，没对齐 |
| E09 | via cut 数为偶数 | 漏 `if n%2==0: n-=1` |
| E10 | 端口不在金属上 | 端口写成理想点，没跟引线联动 |
| E11 | 几何跑出 bbox | bbox 上边界应是端口 y，不是 ry |
| E12 | N 与 n 不一致（两侧同为 `round`/`fix(n+0.5)`，偶数 n 合法，只判自洽） | `N` 传参错、或改了圈数规则没同步两边 |
| E13 | 两臂顶点不镜像 | 镜像用了旋转拷贝，或 `sg` 乘了两次 |

### 第 4 步 改代码

- 先写断言（面积/bbox/不重叠/连通/对称/端口已知答案），再动代码。没有断言的改动算没做完。
- 成对改：`data/librf.il` + `lri/layout.py` 同一次提交，提交信息写两端 `file:line`。
- 硬约束先过一遍：`lead_gap>=s`、`lead_w>=w/2`、`lead_len>=w/2`、`x_port>=xj`、
  `x_port<=a0`、`lmax >= via_edge+stub`、`dmax >= e`、桥两端等高；
  `ct` 另需 `b_last >= 1.5w+s`。
- 参数耦合：改 `w/s` 要跟着看 `lead_gap/lead_len`；改圈数要重算 `lmax` 是否还放得下 via 区。

判据：改完重跑第 3 步 exit 0，且新参数组合的检查结果与旧参数在"该变的地方变了、不该变的没变"。

### 第 5 步 讲解 / 出图

- 先讲**派生量**再讲图形。默认参数 `rx=80, ry=60, n=3, w=8, s=3` 的实数：

  | 量 | 值 | 算法 |
  |---|---|---|
  | `p` | 11 | `w+s` |
  | `a0 / b0` | 76 / 56 | `rx−w/2`、`ry−w/2` |
  | `c0` | 32.804 | `chamfer·min(a0,b0)`，`chamfer=2−√2` |
  | `xj / xe` | 5.5 / 8.3995 | `xj=p/2`，`xe=xj+e` |
  | `e` | 2.8995 | `(√2−1)(s+w/2)` |
  | `via_edge / span` | 0.45 / 6.5 | `cut/2+encl`（cut=0.5, encl=0.2）；span 含 7 个 cut |

- 讲绘制顺序：参数 → 单圈 6 点 → 半环 → 左右交替（`kside`）→ 必须换层 + X 交叉
  → 整条臂 → 两臂+桥+引线 → via 定位 → 层叠。
- 出图**按真实层序**（TM1 → TM2 → via），不要用整圈闭合多边形当上下文：

  ```bash
  python .dsh/skills/virtuoso-pcell/scripts/draw_anatomy.py geo.json anatomy.png
  ```

- 要在 GUI 里给用户看（或导出 PNG），走 `lri/ui/layout_view.py`——它调的是同一个内核，
  `LayoutView.show(geo, params)` 上屏、`render_image(...)` 导出，两者共用一个
  `LayoutTransform`，屏上与导出逐点一致。别另写一套坐标系（`references/visualization.md` §5）。

- 判图先判数：图与数矛盾时信数。

判据：用户能指着图说出"这个 X 的交点为什么在 x=0"、"这个 via 为什么落在这儿"。

## 反模式（见到就停）

- 只跑 Python selftest 就声称版图对了 —— 那只证明镜像没坏。
- 拿外部 v10 超集（3823 行）当真相源 —— 行号对不上，会读错过程。
- 为了让参数画得出来而挪几何/放宽可行性上界 —— 可行域虚胖，贴边绕线。
- 用 `clamp` 处理越界的环端 x —— 越界必须抛错。
- 看图判断层间关系 —— TM2 会盖住 X 交叉，看起来像 underpass 全错。
- 改 DRC 阈值只改一边、或继续用硬编码 `w>=30.0`。

## references

- `references/geometry-model.md` —— 完整几何模型与公式（先读这个再动代码）
- `references/skill-il.md` —— SKILL 语言陷阱、几何原语、sym 引擎、落库调用链
- `references/failure-catalog.md` —— 真实事故：症状/根因/正确做法/验证方式
- `references/visualization.md` —— 出图配方、层序、像素反算、`draw_anatomy.py` 三面板讲解图、GUI 预览模块
- `scripts/dump_geo.py` —— 从仓库内核倒 geo.json（带设计参数）
- `scripts/check_geometry.py` —— 13 条不可变量检查器（零依赖，可直接跑）
- `scripts/draw_anatomy.py` —— 按层序出解剖图

## 基线自证（汇报前必须跑）

```bash
cd <repo>
PYTHONPATH=src python -m virtuoso_optimizer.librf_opt --selftest        # 几何/DRC 套件
PYTHONPATH=src python -m virtuoso_optimizer.netlist.create --selftest   # 网表生成
PYTHONPATH=src python -m virtuoso_optimizer.netlist.params              # 网表参数
PYTHONPATH=src python -m virtuoso_optimizer.em.align --selftest         # EM 曲线对齐
PYTHONPATH=src python -m virtuoso_optimizer.lri.layout                  # 内核可导入
python .dsh/skills/virtuoso-pcell/scripts/check_geometry.py geo.json    # 本例几何 exit 0
```

Windows PowerShell 里等价：`$env:PYTHONPATH='src'; python -m ...`。
改过 `.il` 还想验"SKILL 真解释器 vs Python 逐点一致"，本机跑不了（要 Virtuoso + spectre），
用 `tools/pcell_check.py` 在实机上跑；跑不了就明说没跑，不要说"应该没问题"。
