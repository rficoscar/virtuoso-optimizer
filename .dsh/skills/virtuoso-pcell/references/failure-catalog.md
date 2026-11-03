# 电感 PCell 几何开发：失败案例库（sym 对称差分电感，v1.8.1）

> 服务对象：写/改电感 PCell 几何代码的 AI。案例全部来自本仓库的真实修复记录
> （CHANGELOG 1.1.0–1.4.4、`lri/layout.py` 注释、`librf.il` 注释）与 2026-11-03 解剖会话。
> 排序：按「出现频率 × 危害」。**前 4 条都是静默故障**——不报错、图形看起来对，只有量尺寸或对账才暴露。
> 约定：`librf.il` = `src/virtuoso_optimizer/data/librf.il`（2251 行），
> `layout.py` = `src/virtuoso_optimizer/lri/layout.py`（656 行）；坐标/面积都用 µm。
> v1.8.0 起只剩 sym 一种拓扑（se/diff/f8/变压器/balun/cpl 已删），旧案例里涉及这些拓扑的段落一并作废。

**基线自证（改完必跑，缺一条就别声称改对了）**

```bash
cd <repo>; export PYTHONPATH=src          # Windows: $env:PYTHONPATH='src'
python -m virtuoso_optimizer.librf_opt --selftest     # 应为 selftest: 113 ok, 0 fail
python -m virtuoso_optimizer.netlist.create           # 20/20 tests passed
python -m virtuoso_optimizer.netlist.params           # selftest: 125/125 passed
python -m virtuoso_optimizer.em.align --selftest      # selftest: 108 passed, 0 failed
python .dsh/skills/virtuoso-pcell/scripts/dump_geo.py -o /tmp/geo.json
python .dsh/skills/virtuoso-pcell/scripts/check_geometry.py /tmp/geo.json   # exit 0；默认参数允许 1 条 E01 WARN
```

几何断言的公共夹具就是 `check_geometry.py`（`polys_overlap` / `self_intersections` /
`min_nonadjacent_segdist` / `signed_area` / `symmetry_residual` 都在里面）。
本仓库**没有** `work/pcell_research/`、`compare_skill_py.py`、`connectivity.py`、`geomlib.py`
——网络上/旧文档里出现这些路径的都是别的 checkout，别照抄命令。

---

## 1. 等宽带多边形的收尾点算错：每处换圈多画一块楔形（+55 µm² TM1 外溢）
- **症状**：跨线（underpass）端部「多出一面旗子」，TM1 红块溢到下一圈 TM2 底下。
  n=2/w=8/s=3 实测面积 248.193 µm²，同一条中心线的等宽扫掠只有 193.133 µm²，
  **每处 +55.06**（n=3 两处 110）。用户看图一眼指出。
- **根因**：改写 miter 收尾时把两个不同量纲的项加到端点 y 上：`y0 + sg*(h + back + abs(x1-x0))`。
  `back = h*(√2−1)` 只是 45° 接缝外扩量，`abs(x1-x0)` 是水平跨距，末端 y 落到 −46.05 而不是 −52。
  更早写 `h+back-(x1-x0)`（−54.63）是欠切，通孔柱顶 −52.75 露出 → 悬空孔；改成 `+abs` 又过冲。
- **正确做法**：端部必须等于「等宽带」——补内侧 miter 顶点后让末点回到 `y0+sg*h`
  （`layout.py:194-218`、`librf.il:1645-1669`，后者注释就是这段血泪）。miter 符号必须跟
  `(x1-x0)` 的符号。
- **验证**：`signed_area(poly) == signed_area(sweep_poly(pts,w))`，顶点逐点 <1e-6；
  n=2/w=8/s=3 必须 193.133 µm²，出现 248.19 / 55.06 差值即为错（checker E06）。

## 2. Python 与 SKILL 双份内核漂移，只改一边
- **症状**：Python 预览放行、Virtuoso 生成结果不同；或一边修好另一边还是旧公式。
- **根因**：几何事实只有一份，代码有两份（`layout.py:194-218` ↔ `librf.il:1645-1669`）。
  移植靠人眼抄，没有机器对账。
- **正确做法**：SKILL 是唯一事实源，改动成对提交并在提交信息写两端 file:line；
  Python 只作「镜像 + 断言」，不允许先改 Python 看效果再回头补 SKILL。
- **已知仍在的真实分歧**（照着改就会分歧，先确认要哪一侧语义）：
  - 切角超界：Python **抛错**（`layout.py:248-250`「第 k 圈切角 %.3f 超过半轴」），
    SKILL **夹到** `c=min(a,b)`（`librf.il:1724`）。实测 `rx=80,ry=60,chamfer=1.0` →
    Python 报「第 1 圈切角 49.556 超过半轴」。
  - 圈数 `N` 两边**已一致**（都是 `round`/`fix(n+0.5)`，都拒非整数 `n`）；
    旧文档说「SKILL 对偶数 n 强制补奇数」是上一代引擎的事，本仓库不再成立。
- **验证**：`dump_geo.py` 导出的顶点与 SKILL 侧 dump 逐点 ≤1e-6；`check_geometry.py` exit 0。

## 3. DRC 阈值两份口径 + 写死的魔法数
- **症状**：同一几何 Python 预览放行、落版图时才报 DRC；换工艺 profile 阈值不跟着变。
- **根因**：DRC 规则复写两遍，阈值一半取 `tech.drc` 一半写常量。**至今有残留**：
  `_drc_veto` 里 `w >= 30.0` 仍是硬编码（`layout.py:126`），不随 `tech.drc.max_w` 变；
  `min_w/max_w` 本身已经改成取 tech（`:106-114`）。
- **正确做法**：阈值只从 `tech.drc` 读；`w>=30` 分支要么删，要么由 `drc.wide_space` 决定
  （参考 `_wide_space_veto`，`:131-152`）。
- **验证**：selftest 五个 DRC 例同时成立——`s:1.5`→False、`s:2.0`→True（边界放行）、
  `w:0.5`→min_w 拒、`w:31`→max_w 拒、`build_tech(drc={min_s:0.5})` 下同一几何放行。
  再 `grep -n "30\.0\|[^0-9]8\.0" layout.py` 清点剩余魔法数。

## 4. 数值注入被加引号，SKILL 静默走回落值
- **症状**：工艺文件写了 `drc.via={cut:0.5,spacing:0.5}`，版图里 via 仍是默认尺寸；
  不报错不告警，量尺寸才发现。
- **根因**：`tech_to_skill` 用 `_alist` 渲染嵌套子表，把数值也加引号 → SKILL 侧
  `numberp("0.5")` 判假 → `librfDrcViaNum`（`librf.il:578`）落进 else 默认分支。
  死代码能活两个版本没人发现，因为回落值本身是合法 via 尺寸。
- **正确做法**：注入统一走只给字符串加引号的那条路径（`tech.py:586-592`）；
  「注入 → 解释 → 回落」三段每段都断言，回落分支必须吼一声。
- **验证**：`tech_to_skill` 产物里 `grep -c 'list("cut"' ` 必须为 0（正确形态是 `list("cut" 0.5)`）；
  `librfDrcViaNum(tech,"cut")` 必须等于 tech 里的值，缺省才等于 DEFAULT。

## 5. 带符号 / 无符号 x 混用 → 双重取负
- **症状**：环端跑到对侧（该在右端的点出现在左端），几何整体翻转，但代码不报错、
  图形仍「像个螺旋」。
- **根因**：`lri_i3_half_xy` 的契约是 x 为**无符号**量、方向由 `sg`(`kside`) 决定；
  外部传了已带符号的 x，内部再乘一次就双重取负（`layout.py:263-278` docstring、
  `librf.il:1755-1767` 原话）。
- **正确做法**：函数边界只收无符号量，`sg`/`kside` 只在内部乘一次；越界立刻抛
  「环端 x=%.3f 超出顶边直段 (a-c=%.3f)」，绝不 clamp。
- **验证**：镜像对称断言 `symmetry_residual(mirror(A), build(镜像参数)) < 1e-6`；
  越界用例必须 raise。别用「所有 under 都 x0<x1」当全局断言——左半 x 本来就是负的。

## 6. 删掉可行性上界（dmax 的 miter 项）
- **症状**：`w=14, rx=60` 构建成功，而测试套期望它被拒；可行域变窄时几何贴边、绕线不齐。
- **根因**：`dmax` 里 `w*(√2−1)/2` 的 miter 修正（内圈 45° 倒角切掉的半宽）被当成多余项删掉，
  可行域虚胖；旧的滑窗启发式 `lri_i3_window / delta_frac` 会挑一个跟拟合有关的偏移，
  那正是绕线不规则的来源（`layout.py:289-303` docstring）。
- **正确做法**：保留 miter 项并判 `dmax >= e`（`:339-346`）；删掉「为了让参数能画出来而挪一挪」的自由度。
- **验证**：`build(w=14, rx=60)` 必须抛错；凡构建成功的参数都满足 §6 那 8 条硬约束。

## 7. 出图图层顺序反了，把假象当成真 bug
- **症状**：每张图都是「红块盖住另一臂的 45° jog」，X 交叉完全看不见，只剩一面旗子；
  第一反应是「underpass 全错」。
- **根因**：先画 TM2 再画 TM1，后画的压先画的；真实叠层是 TM2=metal8 在上、TM1=metal7 在下。
  图上遮挡顺序反了，把真实存在的交叉盖死了。
- **正确做法**：按真实层序由下往上画（under → tops → via），见 `scripts/draw_anatomy.py`
  （`draw_coil()` 的调用顺序就是层序）；判断几何对错先看坐标/面积/连通性，别看像素。
- **验证**：同一几何两套证据对照——图上「看不见」的交叉在 `polys_overlap` 里必须仍成立；
  先跑基线，再谈图。

## 8. 调试图自身失真（单位 / 上下文 / alpha）
- **症状**：① via 只有 1.7 px 看不见，误判「没画 via」；② 用完整闭合 ring 当上下文，
  本该断开的极线处被填上金属，交叉区彻底读不出来；③ 0.45 alpha 叠层糊成一团紫色。
- **根因**：① `side=max(cut, min_px/fig.k)` 单位错，应为 `min_px*fig.S/fig.k`；
  ② 拿「整圈闭合多边形」当「该换圈处真实存在的半环」，画出来的比版图多；③ 半透明不适合判层间关系。
- **正确做法**：像素尺寸先换算到 µm 再乘比例；上下文只用真实存在的半环折线并描边；
  层间用颜色+描边区分，不用 alpha。
- **验证**：反算像素尺寸（`cut/µm_per_px` 应在 3–6 px）；出图前后断言图上面积之和 ≈ 内核面积之和。

## 9. 改参数忘了伴随约束；默认命名非法且重名
- **症状**：① 变体 `w=4,s=4` 时 `ValueError: lead_gap=3.000 < s=4.000`；
  ② 不填 cell 得到 `ind|{"H…` 这种非法名，且不同几何同名互相覆盖。
- **根因**：几何参数互相耦合（`lead_gap>=s`、`lead_len>=w/2`、`ct` 时 `b_last>=1.5w+s`、
  `dmax>=e`），改一个必须同步改另一个；`geom_key(...)[:8]` 当名字既含非法字符又会碰撞。
- **正确做法**：参数组合先过约束检查再画；默认名取整串 geom 键的 sha1 前 8 位，
  并断言「不同几何 → 不同名字」（`lri/device.py:36-43`）。
- **验证**：`re.match(r"^[A-Za-z0-9_]+$", default_cell_name(geom))`；
  `default_cell_name(g1) != default_cell_name(g2)`；8 条硬约束各写一个负例。

## 10. 参数域里的「合法输入」被内核拒掉（PRESETS 里 n=2.5/3.5）
- **症状**：GUI 下拉选到某个预设，直接报「n=2.500 必须为整数（每臂圈数）」。
- **根因**：`layout.py:600-613` 的 `PRESETS` 保留了上一代引擎的半整数匝（`n=2.5/3.5`），
  而现内核 `lri_i3_rings:232-233` 与 SKILL `librf.il:1701` 都要求整数 —— 预设表没跟着更新。
- **正确做法**：预设值先过 `lri_validate`；要么把半整数改成整数，要么在内核里显式支持。
- **验证**：遍历 `PRESETS` 每个条目 `lri_validate(form_to_geom(...))` 全部 True（当前会红一条）。

---

## 怎么用（改代码时的顺序）
1. 先写「应该成立的性质」断言（面积 / bbox / 不重叠 / 连通 / 对称 / 端口已知答案），再动代码；
   没有断言的改动算没做完。
2. 改动成对提交（SKILL + Python），提交信息写两端 file:line。
3. 图看着不对时先分流：内核错 or 出图错——用坐标/面积/连通性判，不用像素判（案 7、8）。
4. 收尾跑基线五条 + 本条验证方式，两个都过再汇报；只说「应该没问题」不算。
