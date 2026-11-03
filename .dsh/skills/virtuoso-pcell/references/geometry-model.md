# 几何模型：sym 对称差分电感的坐标是怎么算出来的

> 适用：本仓库 v1.8.1 的 `src/virtuoso_optimizer/lri/layout.py`（656 行，Python 镜像）
> 与 `src/virtuoso_optimizer/data/librf.il`（2251 行，Cadence SKILL，真落库的那份）。
> 单位一律 µm 浮点；落盘前才对齐到 1 nm。**先读懂这份模型，再动代码。**
> 机器上若还有更早的超集 `librf.il`（v10，3823 行，含 se/diff/f8），**不是**本仓库的真相源，
> 行号会对不上（差约 310 行且多出整段拓扑）。

## 0. 一句话

不画电感，画的是：**一族同轴八边形半环 + 两条交指手臂 + 每个换圈处一个严格 45° 的 X**。
主金属 TM2 负责半环与本臂的 jog，下层 TM1 负责另一条臂跨圈的 underpass，
via 只在两者重叠处把层缝起来。

## 1. 参数与派生量（layout.py:228-252 建圈，:376-431 装配）

| 量 | 公式 | 锚点 |
|---|---|---|
| `p` | `w + s` | `lri_i3_rings:241` |
| `a0` | `rx - w/2` | `:242` |
| `b0` | `ry - w/2` | `:243` |
| `c0` | `clamp(chamfer,0,1) * min(a0,b0)`，chamfer 默认 `2-√2 ≈ 0.5858` | `:244` |
| 第 k 圈 | `a=a0-k*p`、`b=b0-k*p`、`c=max(0, c0-k*p*(2-√2))` | `:246-247` |
| `N` | Python `max(1, round(n))`；SKILL `max(1, fix(n+0.5))` —— **两侧一致** | `layout.py:239` / `librf.il:1712` |
| `xj` | `p/2`（X 的横向半宽，交点恒在 `x=0`） | `layout.py:381` |
| `e` | `max(0, √2*(s+w/2) - p + w/2)` | `lri_i3_cross_gap:254-260` / `librf.il:1748-1749` |
| `xe` | `xj + e`（环端实际 abscissa） | `layout.py:304-305` |
| 圈数上限 | 第 k 圈需 `min(a,b) > w/2`，否则抛错 | `:240-245` |

- `c` **必须随 k 收缩**：不缩的话相邻圈的 45° 斜边不平行，斜边上的间距会被吃掉
  （直边间距仍是 `s`，肉眼看不出来）。
- `n` 必须是整数：两侧都报错（`layout.py:233`「n=%.3f 必须为整数（每臂圈数）」/ `librf.il:1701`）。
- 内孔放不下就**抛错**，不许 clamp 出一个假的圈。

### 1.1 默认参数的具体数字（rx=80, ry=60, n=3, w=8, s=3）

| 量 | 值 | 量 | 值 |
|---|---|---|---|
| `p` | 11.000 | `xj` | 5.500 |
| `a0` | 76.000 | `e` | 2.899495 |
| `b0` | 56.000 | `xe` | 8.399495 |
| `c0` | 32.8040 | `via_edge` | 0.450 |
| 第 1 圈 (a,b,c) | (65, 45, 26.3604) | `via_span` | 6.500（7 个 cut） |
| 第 2 圈 (a,b,c) | (54, 34, 19.9167) | `back` / `stub` | 1.657 / 0.942 |
| `bridge_y` | −34.000 | `bbox` | (−80, −60, 80, 76) |

`metal` 7 条折线、`under` 2 条、`vias` 4 个中心：(±8.8495, 45) / (±8.8495, 34) / (−8.8495, −56) / (8.8495, −45)。
端口 `p01=(5.5, 76)`、`p02=(-5.5, 76)`；`bbox` 的上边界是端口 y，不是 `ry`。

## 2. 一个半环就 6 个点（layout.py:263-278 / librf.il:1755-1767）

```
lri_i3_half_xy(a, b, c, x_top, x_bot, sg)
  -> [(x_top, b), (a-c, b), (a, b-c), (a, c-b), (a-c, -b), (x_bot, -b)]   # sg=+1 右半
     sg=-1 时整条 x 取负（左半）
```

契约：`x_top / x_bot` 是**无符号**量，方向只由 `sg` 决定。传带符号的 x 会双重取负，
环端跑到对侧——图形仍「像个螺旋」，肉眼极难发现（失败案例 §5）。
越界立刻抛「环端 x=%.3f 超出顶边直段 (a-c=%.3f)」，**绝不 clamp**。

## 3. 一条手臂怎么拼起来（layout.py:289-374 / librf.il:1781）

```
for k in range(N):
    kside = side * (+1 if k%2==0 else -1)      # 左右交替：这就是「交指」
    x_entry = xj if k == 0    else xe          # 首圈从引线/桥进来
    x_exit  = xj if k == N-1  else xe          # 末圈去桥
    if k%2 == 0:  seg = half_xy(a,b,c, x_entry, x_exit, kside)
    else:         seg = reversed(half_xy(a,b,c, x_exit, x_entry, kside))  # 奇圈走反方向
    run = run + seg[1:]                        # 去掉重复首点，接成一条折线
```

- 偶圈把 `x_entry` 放在 `y=+b` 端、奇圈放在 `y=-b` 端，所以要 `reversed` 才能首尾相接（`:326-333`）。
- 每条 `run` 是**一条中心线折线**，不是多边形：宽度由 `w` 在落库侧扫掠。
- 换圈处（`not last`）先算 `y_end / y_next`（`:336-337`），再按 `under_here` 二选一（`:347`）：
  - **留在 TM2（jog）**：4 点 `[(kside*xe,y_end), (kside*xj,y_end), (-kside*xj,y_next), (-kside*xe,y_next)]`，
    折进 `run`（`:370-372`）。
  - **换到 TM1（underpass）**：5 点 `[ae, (kside*xj,y_end), (-kside*xj,y_next), (-kside*xe,y_next), be]`（`:364-365`），
    `run` 在此收尾另起。
  - 两条对角线都从 `(±xj, y_end)` 走到 `(∓xj, y_next)`：`dx = dy = p`，所以**交点恰在 x=0**。
    若把对角线起点挪到 via pad（`av`），X 就偏心——这正是旧版绕线不规则的来源。
- `under_here` 的判据是 `(k%2==0) if side<0 else (k%2==1)`：**两条臂必须在同一换圈处分层**，
  否则同层撞在一起。
- 可行性上界 `dmax`（`:339-342`，三选一取最小）必须 `>= e`，否则抛「45° 夹缝放不下」。
  第一项含 via 区，第二项含内圈 45° miter `w*(√2-1)/2`——这一项删掉过，导致 `w=14, rx=60`
  能构建成功但几何贴边（失败案例 §6）。`lmax` 的独立闸门在 `:308-312`。

## 4. underpass 与 via

- `under` 是中心线折线；`under_poly` 才是扫掠后的多边形：
  5 点走特例 `lri_sym_under_poly`（`layout.py:194-218`），其他形状走 `lri_sweep`（`:30`）。
  SKILL 侧同一条：`lriUnderPolyPts`（`librf.il:1645-1669`）。
- 扫掠契约：端帽垂直于中心线、内侧补 miter 顶点、**末点必须回到 `y0 ± h`**（`h=w/2`），
  即端部等于等宽带。历史 bug：末点写成 `y0 + sg*(h + back + |x1-x0|)`，
  每处多画 55.06 µm² 楔形溢到下一圈（`librf.il:1663-1668` 的注释就是这段血泪）。
- via：`n = floor((w - 2*encl + sp)/(cut + sp))`，**强制奇数**（`if n%2==0: n-=1`），
  `span = n*cut + (n-1)*sp`（`layout.py:280-287`）。SKILL `lriI3ViaSpan`（`librf.il:1734-1742`）
  同一公式，只是用 `fix()` 截断而不是 floor。**两侧口径已统一**，不再有「三档 viaW 表」。
  偶数个 cut 会让阵列偏心，等于孔偏出金属（checker E09）。
- `via_edge = cut/2 + encl`（`layout.py:397`，默认 0.5/2+0.2 = **0.45**）；
  `stub = max(via_edge, SYM_I3_WEDGE_MIN - back)`（`:399`，默认 `max(0.45, 0.942) = 0.942`）。
  环端直段 `lmax` 必须盖住 `via_edge + stub`，否则 via 落在 45° 斜面上（`:308-312`）。
- via 中心必须**同时**落在 TM2 与 TM1 的中心线上（实测距离 0），这是「换圈连接是否成立」
  最强的单点判据（checker E08）。

## 5. 桥 / 引线 / 端口 / bbox（layout.py:409-438）

- `bridge = [tops_a[-1][-1], tops_b[-1][-1]]`（`:409`）：两臂末点之间的水平短段，
  两端必须等高（`:411-412`），默认 `bridge_y = -34`。
- 引线 `lead_a/lead_b`（`:417-418`）：右侧 `[(x_port, b0-w/2), (x_port, y_port)]`，
  左侧 `[(-x_port, b0-w/2), (-x_port, y_port)]`。
  `x_port=(lead_gap+lead_w)/2`（`:390`）、`y_port=b0+lead_len`（`:391`）。
  **起点是外圈金属带的内沿 `b0-w/2`，不是外圈中心线 `b0`**：从中心线起笔时引线是平头，
  与外圈平头端面围出一个 `w/2 × (x_port−xj)` 的缺口（默认 `lead_gap=s, lead_w=w` 时
  `x_port==xj`，每侧 4×4 µm，共 32 µm²；v1.8.2 修复，失败案例 §11）。下探到内沿后，
  引线内壁 `x=lead_gap/2` 与外圈内沿 `y=b0−w/2` 连成一条直角 L，缺口消失，
  checker E01 由 WARN 变 PASS。
  守卫 `lead_w >= w/2`（`:386-389`）：引线比半圈窄时下探露出的残台补不上，直接抛 ValueError。
- 端口：`p01=(x_port,y_port)`、`p02=(-x_port,y_port)`（`:420`）；`ct` 时追加 `ct1`（`:429`）。
  端口必须落在金属上（距中心线 ≤ w/2），理想情况恰在中心线端点。
- `bbox = (-rx, -ry, rx, y_port)`（`:438`）：**上边界是端口 y，不是 ry**。
- `ct`（中心抽头）：`stem=[(0,bridge_y),(0,0)]` + `pad=(0,0,w)`（`:426-427`），
  约束 `b_last >= 1.5*w + s`（`:424-425`）。实测 `ct=1` 时 stem 中心线到内圈的平行水平段
  只有 5.5 < w=8：**这是 T 接拐角、同一网络的合法连接，不是短路**——那块角域本来就被桥覆盖。
  教训：只看中心线距离会把「拐角」误判成「贴线」；checker 的 E05 因此改成
  「平行且沿走向重叠 > w/2 才算间距不足」，垂直/斜交的近距降为 E05d INFO。

## 6. 必须成立的硬约束（改参数时先过这几条）

```
lead_gap >= s                    layout.py:384-385
lead_w   >= w/2                  :386-389
lead_len >= w/2                  :392-393
x_port   >= xj                   :394-395
x_port   <= a0                   :396-398
lmax      = min(a0-c0, aN-cN) - xe  >= via_edge + stub    :308-312
dmax      >= e                   :343-346
桥两端等高                        :411-412
ct 时 b_last >= 1.5*w + s         :424-425
```

任何一条破了就抛 ValueError，**不许为了让参数画得出来而挪几何**（失败案例 §6）。

## 7. 段角只允许 45° 的整数倍

所有拐点来自 0°/45°/90°：半环斜边（45°）、X 斜段（45°）、桥/引线/underpass 的直段（0°/90°）。
**任何其他角度的段都是 bug**（checker E03 判 FAIL），常见根因是切角 `c` 没随圈号收缩、
或 underpass 的 y 偏移抄错。45° jog 的折点内夹角是 **135°**，扫掠时外侧 miter 只外扩
`h/sin(67.5°) ≈ 1.31h`，不会触发 `lri_sweep` 无 miter limit 的失控。

## 8. 层与线宽

| 概念 | 层 | 说明 |
|---|---|---|
| 主金属 / 半环 / 本臂 jog / 桥 / 引线 | TM2 = `metal8` | 宽 `w` |
| 另一条臂的跨圈 underpass | TM1 = `metal7` | 宽 `w`（**不是更窄**） |
| via 阵列 | TV1（`librfViaArray:382` 决定层名） | cut 方形，`via_edge=0.45` |

层号来自 `tech.drc.via = {cut:0.5, spacing:0.5, enclosure:0.2}`（`layout.py:188-192`）；
1P8M 栈里只有 M6/TM1/TM2 是 metal6/7/8（`librf.il:1998` 注释）。

出图时必须**由下往上**画（TM1 → TM2 → via），否则 TM2 会把 X 交叉盖死，
看起来像「underpass 全错」（失败案例 §7）。

## 9. Python 内核 与 SKILL 源码 的已知差异（改一边必须核对另一边）

| 项 | Python `layout.py` | SKILL `librf.il` |
|---|---|---|
| 圈数 `N` | `max(1, round(n))`（:239） | `max(1, fix(n+0.5))`（:1712）——**一致** |
| 非整数 `n` | 抛错（:233） | 抛错（:1701） |
| 切角超界 | **抛错**「第 k 圈切角 %.3f 超过半轴」（:248-250） | **夹到** `c = min(a,b)`（:1724） |
| 扫掠收尾 | `lri_sym_under_poly:194-218` | `lriUnderPolyPts:1645-1669` |
| via span | `lri_i3_via_span:280-287`（floor） | `lriI3ViaSpan:1734-1742`（fix）——同式 |
| DRC 阈值 | `_drc_limits:106-114` 取 tech，但 `w>=30.0` 仍硬编码（`_drc_veto:126`） | `librfDrcParams:621` 一套口径 |
| 职责 | 只做几何与 DRC 预检 | 真正落版图，还管名字/端口/层次 |

**推论**：任何几何改动都要在两边同时验证；只跑 Python 的 selftest 不能证明版图正确，
只跑 Virtuoso 也不能证明参数域没退化。已知潜在不一致：`PRESETS:600-613` 里 `n=2.5/3.5`
现在会被整数校验拒掉，属仓库内待修项。

## 10. 与 checker 的对应关系

`scripts/check_geometry.py` 把上面每一条变成可执行判据：

| code | 判据 | 对应章节 |
|---|---|---|
| E01 | 零长度段 / 重复点（默认参数下 WARN 而非 FAIL） | §5 引线 |
| E02 | 中心线自交 | §3 拼装 |
| E03 | 段角必须是 45° 整数倍 | §7 |
| E04 | under_poly 自交 / 零面积 | §4 |
| E05 | 两条中心线距离 < w → 金属粘连（垂直/斜交降为 E05d INFO） | §1、§3 |
| E06 | under_poly 面积 vs 等宽扫掠（>2 µm² 且 >1%） | §4 楔形 |
| E07 | underpass 侵入主金属 w/2 带（via 附近豁免 1.5w） | §4 |
| E08 | via 没同时落在两层中心线上 | §4 |
| E09 | via cut 数为偶数 | §4 |
| E10 | 端口没落在金属上 | §5 |
| E11 | 几何跑出 bbox | §5 |
| E12 | `N` 与 `n` 不一致（两侧同为 round，偶数 `n` 合法，只判自洽） | §1、§9 |
| E13 | 两臂顶点 x 镜像对应（jog 处允许不齐） | §3 |
