# librf.il 参考：sym 差分电感的 SKILL 内核

> 读/改 Virtuoso 版图 PCell 前先过一遍；讲契约与陷阱，源码是最终真相。

## §0 版本锚定

| 项 | 值 |
|---|---|
| 仓库 / 版本 | optimizer_public v1.8.1，commit 78a6720 |
| ships 的 SKILL | src/virtuoso_optimizer/data/librf.il：2251 行 / 84911 字节 / 119 个 procedure |
| 拓扑 | 仅 sym（对称差分电感）；kind="ind" 与 "sym" 同一实现（librf.il:865-872） |
| Python 镜像 | src/virtuoso_optimizer/lri/layout.py（rings→arm→build 同公式） |
| PCell 外壳 | src/virtuoso_optimizer/data/lri_pcell.il（CDF 参数拼 req，回调 lriI3BuildSym） |

- 全文锚点都指这份 2251 行文件。旧卡锚的 3823 行超集不在此仓库：se/diff/f8、叠层变压器、spiral、lriDispatch、lriSymArm、lriRing(s)、lriOffset、lriSpiral 已无同名 procedure，相关段落已删；第一代 lriBuildSym 只剩注释（librf.il:1686）。
- 头注释定义三种入口：eval serve、librfRun、librfMakeViews / librfEmxViews（librf.il:2-5）；它引用的 DEVELOPMENT_GUIDE.md 不随仓库发布，以源码为准。

## §1 SKILL 语言陷阱

| 陷阱 | 规则与锚点 |
|---|---|
| t / nil 是保留真值 | 别把 t 绑成局部变量；nil 兼作空表与假，本文件变量表都避开 t（librf.il:2010-2015） |
| nil 不是 0 | nil 进算术报错；缺省走参数默认。req 是扁平表，member 扫描（librf.il:212-225） |
| 动态作用域 | let 不是词法闭包；跨过程只靠 defvar 全局，入口先 librfReset（librf.il:164-177） |
| errset 要双判 | 返回 list(值) 或 nil；判 res==nil 与 car(res)==nil，并留退路（librf.il:271-273、librf.il:328-341） |
| 单位与吸附 | 坐标 µm 浮点；落库前 librfSnap 取 1 nm，librfF3 只服务结果行（librf.il:193-197） |
| fix 截断 | fix(2.9)=2；最近整数用 fix(n+0.5)，圈数整数性同判据（librf.il:1701-1704、librf.il:1712） |
| 浮点不能 == | 点重合 1e-6，角度 1e-6，换圈夹缝 1e-9（librf.il:1642-1643、librf.il:1567-1569、librf.il:1833） |
| 多语句要 progn | if 的 then/else 只接一个表达式；when/unless 不成立返回 nil（librf.il:146-149、librf.il:155-159） |
| 字符串与 symbol | sprintf(nil ...) 得字符串；symbolToString / %g 互转，parseString 拆端口（librf.il:227-234、librf.il:1222） |
| dbCreate 不代表成功 | 检查返回值并退路：polygon→path→逐段矩形（librf.il:293-300、librf.il:328-341） |
| 全局状态成对清 | lriI3FillSkip 是文件级全局，Build 前清、用后清，漏清少打 via（librf.il:1952、librf.il:2137-2141、librf.il:2190） |
| 清 cellview 两条路径 | layout 清 cv->shapes，views 清 cv->figs 与 cv->instances；别照搬"figs 必空"（librf.il:789-790、librf.il:962-965） |

## §2 几何原语

| 函数 | 契约 | 锚点 |
|---|---|---|
| lriSegLen | 两点欧氏距离，不判零长 | librf.il:1522-1524 |
| lriLast | car(reverse(lst))；空表 nil | librf.il:1525 |
| lriDedup | 顺序去重，相邻 ≤1e-6 丢后点，保留首个 | librf.il:1526-1533 |
| lriPLen | 折线开链长度，不含末点回首点 | librf.il:1534-1540 |
| lriShift / lriMirrorX / lriMirrorY / lriMirrorStepsY | 平移或按轴翻转，保持点序 | librf.il:1541-1559 |
| lriAngleOK | 每段水平/垂直/45°，零长段跳过，容差 1e-6 | librf.il:1561-1571 |
| lriInterX | 线段交点；平行（det<1e-12）返回 nil | librf.il:1573-1582 |
| lriTake | 取前 min(k,len) 个点 | librf.il:1588-1593 |
| lriOnLeft | 第 k 圈左边按 y 取点；tt 越界报错 | librf.il:1597-1607 |
| lriPointAt | 按开链弧长比例取点；超界取末点 | librf.il:1614-1624 |
| lriAssert | 追加 lriReport，失败再 librfErr，总返回 t（不中断） | librf.il:1633-1636 |
| lriSamePt / lriPtTrimmed | 1e-6 判同点 / 判断点是否在点集 | librf.il:1642-1643、librf.il:1671-1675 |
| lriUnderPolyPts | 5 点 underpass 扩成 7 点等宽带、45° 斜接多边形 | librf.il:1645-1669 |

注意：lriAssert 只记录不抛错，故存在"返回 t 但带 ERR"（librf.il:1633-1636、librf.il:182-184）；lriReport 有写无读（librf.il:1634），lriStepList 定义后无引用（librf.il:1520）。

## §3 第二代 sym 引擎

设计说明见 librf.il:1683-1691：等距内偏置环族、rx/ry 独立、圈数可为偶数、引线可调。入口返回三张表：tops、unders（换圈下穿 5 点）、vias（librf.il:1770-1772）。

### 3.1 环族 lriI3Rings(rx ry w s n chamfer) → (rings p N)

p=w+s；a0=rx-w/2；b0=ry-w/2；第 k 圈 a=a0-k·p、b=b0-k·p、c=max(0,c0-k·p(2-√2))；c0=clamp(chamfer)·min(a0,b0)；N=max(1,fix(n+0.5))，允许偶数（librf.il:1705-1712、librf.il:1689-1691）。必须报错：rx/ry/w/s≤0（librf.il:1698-1700）；n 非整数（librf.il:1701-1704）；min(a0,b0)≤0（librf.il:1708-1710）；任一圈 min(a,b)≤w/2+1e-9（librf.il:1717-1721）。c>min(a,b) 时钳位（librf.il:1724），勿改回按比例缩切角：

    c = max(0.0 c0 - k * p * (2.0 - sqrt(2.0)))

### 3.2 通孔跨度 lriI3ViaSpan(w cut sp encl) → (span ncut)

sm=w-2·encl；n=fix((sm+sp)/(cut+sp)+1e-9)，至少 1，强制奇数；span=n·cut+(n-1)·sp（librf.il:1734-1742）。宽端 via 列与叠层填充都消费它（librf.il:1925-1932、librf.il:2105-2106）。

### 3.3 跨线间隙 lriI3CrossGap(w s p)

max(0,√2(s+w/2)-p+w/2)（librf.il:1748-1749），换圈 45° 交叉的闭式外拉量，取代旧窗口启发式（librf.il:1744-1747）；arm 由此得 xe=xj+e（librf.il:1784-1785）。环端拉到该处时交叉净距恰为 s。

### 3.4 半环 lriI3HalfXY(a b c xTop xBot sg)

6 点带两条 45° 斜边，末尾 dedup；xTop/xBot 必须无符号，sg=±1 选左右半（内部 mirrorX）（librf.il:1755-1767）。报错：max(xTop,xBot)>a-c+1e-9（librf.il:1757-1761）。传带符号 x 会二次取反、环端跑到另一侧（librf.il:1752-1754）。

### 3.5 单臂 lriI3Arm(rings p N xj w s side viaEdge stub deltaFrac) → (tops unders vias)

e=CrossGap(w,s,p)；xe=xj+e；直边余量 lmax=min(a0-c0,aN-cN)-xe 必须 ≥viaEdge+stub（librf.il:1784-1797）。逐圈 kside=side·±1：偶数圈 xj→xe、最内圈出口改 xj，奇数圈反向，相邻圈 cdr 拼接避免重复点（librf.il:1801-1819）。换圈可行性 dmax=min((a-c)-xj-(viaEdge+stub),(a1-c1)-w(√2-1)/2-xj,(a-c)+s/2-√2(w/2+s))，dmax<e-1e-9 报错（librf.il:1829-1837）。uh 决定换圈走 underpass 还是折进顶层：underpass 5 点为 (ae, kside·xj/yEnd, -kside·xj/yNext, -kside·xe/yNext, be)，via 在 kside(xe+viaEdge)（librf.il:1838-1860）。deltaFrac 保留但未用（librf.il:1781、librf.il:1823-1828）。viaEdge=vcut/2+ven，stub=max(viaEdge, WedgeMin-back)（librf.il:2102-2104）。

### 3.6 叠层 via 链（librf.il:1875-1947）

librfStackMetals 自下而上，librfStackVias 为 (name lower upper cut space encLo encHi)（librf.il:1874-1878）。lriStackIndex（librf.il:1880-1885）；lriViaForStep（librf.il:1887-1891）；lriViaChain(lo,hi) 层未知/同层/反向返回 nil（librf.il:1894-1906）；lriStackBelow 取下一层（librf.il:1908-1911）。lriViaColStack(cv x y w chain)：每级沿 y 排 ncut 颗 cut，h=(ncut-1)(cut+sp)/2（librf.il:1927-1932）；跨级时中间金属补矩形，halfX/halfY 取上下级包覆 max（librf.il:1933-1940）；lriViaSq 画单颗（librf.il:1945-1947）。

### 3.7 叠层填充 lriStackFill

r=w/2-enc-cut/2，r<0 报错（librf.il:1963-1966）；dpitch=(cut+sp)√2（librf.il:1967-1969）；nrow=fix(2r/dpitch+1e-9)+1 钳 1..9（librf.il:1970-1972）；沿段步进，行偏移 -r..r，命中 lriI3FillHit 的 lriI3FillSkip 跳过（librf.il:1984-1992）。FillSkip 由 Build 填入并清空（librf.il:2137-2141、librf.il:2190）。

### 3.8 下穿桥 lriBridgeOn

只接受 5 点，直接 dbCreatePolygon；没有 errset 包裹，改这里要补错误处理（librf.il:2000-2004）。

### 3.9 入口 lriI3BuildSym(req) → t/nil

req 是扁平 key/value 表。默认 dout=160、rx=ry=dout/2、n=3、w=10、s=3.2、chamfer=2-√2、lead_len=20、lead_gap=s、lead_w=w、ct=0、stack=0；metal_main/aux 默认角色 spiral/underpass，空串回落、同名报错；ind_mark 归 librfRun（librf.il:2016-2027、librf.il:2053-2060、librf.il:869-870）。

校验：lead_gap≥s、lead_w≥w/2、lead_len≥w/2；xPort=(lead_gap+lead_w)/2 满足 1e-9<xPort≤a0；s 与 lead_gap≥ws=max(wide(main),stack?wide(aux):0)；via 链 lriViaChain(aux,main)，无表非 stack 降级 via_sp 单跳，无链要 stack 报错；stack 要求相邻层（差 1）（librf.il:2028-2052、librf.il:2063-2092）。

绘制：两臂 ±1，桥取两臂末点并查等高；顶层/桥用 PathSweepEx(...,nil,nil)，stack 时同扫 aux 并填 via；underpass 在 crossName/aux；宽端 via 列；2 点引线（起点取外圈内沿 b0−w/2，避免端口根部缺口，librf.il:2167-2168）；p01/p02 由 TipLabel 从 (±xPort,yPort) 向内偏 1.5 打 pin/label（librf.il:464-475、librf.il:2167-2168）。ct>0.5 需 b_last≥1.5w+s，stem 到 origin、origin 放 w×w pad，再打 ct1 pin。收尾断言 bboxX/bboxY/角度，返回 t（librf.il:2120-2208）。

## §4 落库调用链

librfRun（librf.il:844-881）：librfReset→读 result/type/lib/cell/view/libdir（librf.il:846-853）→缺参先写 fail 并 exit（librf.il:855-860）→errset 内 librfOpenCell（建库/附 tech/查锁/清 shapes 与 nets，librf.il:755-793）→type 只认 ind/sym（librf.il:865-872）→lriI3BuildSym(cv req)（librf.il:868），ind_mark 画 marker（librf.il:869-870）→bbox（librf.il:873）→dbSave/dbClose（librf.il:874-875）→librfWriteResult(librf__ok,...,bbox) 后 exit（librf.il:878-880）。

librfWriteResult 协议（librf.il:236-262）：STATUS ok/fail、CELL、BBOX 四个 F3、PORT name x y textLayer、LAYERS 一行、WARN/ERR 各一行，stdout 末行 LIBRF_EXIT=0/1（librf.il:242-262）。层/端口登记只影响结果行：librfNoteLayer 去重（librf.il:186-188），librfNotePort 存 snap 坐标与 text layer（librf.il:190-191）。librfErr 置 librf__ok=nil 并追加（librf.il:182-184），librfWarn 不置失败（librf.il:179-180）。

## §5 eval-serve 与 views/emx

### 5.1 eval-serve（librf.il:41-133）

- myEvalOutput：表达式做 db20 替换，按 sp/hb/ac/dc/tran/noise/pss 顺序 selectResult+eval，缓存 gLastResult；波形/标量/家族分别输出 CURVE/SCALAR/MSG（librf.il:41-68）。
- myProcessRequest：请求是 "KEY value" 行，认 PSFDIR/RESULT（librf.il:83-97）；openResults 后逐个 output 求值，成功 STATUS ok，失败 STATUS fail + MSG SKILL_ERROR_SEE_LOG（librf.il:101-117）；错误写 $OPT_QUEUE/error.log（librf.il:70-77）。
- myServeEval：轮询 $OPT_QUEUE/eval_req.txt；见 STOP 退出；处理完 rm -f；空闲 sleep 0.2（librf.il:119-133）。
- 启动守卫（librf.il:135-162）：必须有 OPT_QUEUE 与 OPT_OUTPUTS_STATE；loadContext 与 load(stateFile) 后取 gOptOuts 并校验 output 有 name（librf.il:143-161）；缺失走 myFatal（librf.il:79-81）。

### 5.2 views / emx（librf.il:1202-1517）

- librfMakeViews 键：lib（默认 LIBRF）、cell、snp、ports、kind（默认 ind）、result、libdir（librf.il:1205-1211）；cell/snp/ports/result 必填（librf.il:1214-1220）；端口 parseString 拆分（librf.il:1222）；先查 schematic/symbol 的 *.cdslck*（librf.il:1228-1235）；errset 内建 schematic+symbol（librf.il:1240-1242），逐端口记 pin（librf.il:1246-1248），结果由 librfWriteViewsResult 写（librf.il:911-927）。
- librfEmxViews 键同上（librf.il:1455-1461、librf.il:1464-1470）；snp 必须是已存在文件（librf.il:1477），扩展名按 sNp 处理（librf.il:1484-1494）；四个目标 view 先查锁（librf.il:1497-1501）；librfEmxBuild 由它拆键后按位置参数调用（librf.il:1377-1380）。
- EMX：librfEmxInit 载 emxconfig.il 并检查 EMX_* 接口（librf.il:1330-1360），接口目录由 EMX_INTERFACE_PATH 决定（librf.il:1315-1328）；Build 先删目标 view（librf.il:1394-1395），调 EMX_make_n_port_symbol / EMX_make_n_port_schematic_no_dev（librf.il:1397-1405），拷贝并补 schsym（librf.il:1413-1445），结果由 librfEmxWriteResult 写（librf.il:1269-1285）。

## §6 七条不变量与自测

| # | 不变量 | 证据 | 含义 |
|---|---|---|---|
| 1 | bbox 闭合 | librf.il:2192-2202、librf.il:873 | 改引线/桥长度先炸 |
| 2 | 角度 ∈{0,45,90} | librf.il:1561-1571、librf.il:2203-2207 | 新折线点必须落 8 方向 |
| 3 | 同层不自交 | 构造保证；FillSkip 防撞（librf.il:2137-2141） | 无全局扫描器 |
| 4 | 端口是真 pin | librf.il:425-452（3×3 µm 于 librf.il:439-441）；p01/p02 经 TipLabel 到 PortPin（librf.il:464-475）；ct 于 librf.il:2186-2189 | 不是画 label 就算 |
| 5 | DRC 阈值只从 tech 读 | librf.il:570-607、librf.il:621-652；注入见 librf.il:1872 | 例外：librfDrcFigs 的 30/3 硬编码（librf.il:721、librf.il:725） |
| 6 | 结果协议不可改形 | librf.il:236-262、librf.il:911-927、librf.il:1269-1285 | 只加键，不改前缀 |
| 7 | 双端对称 | librf.il:2120-2121、librf.il:2125-2129、librf.il:2232-2237 | 每臂独立参数会破坏 |

- SKILL 自测：librfSelfTest（librf.il:2214-2251），仅 sym；检查 rings n=2→2 圈、±1 两臂 tops 等长、arm 首段角度；返回 nFail==0。
- Python 自测：PYTHONPATH=src python -m virtuoso_optimizer.librf_opt --selftest；约 90 项协议/DRC/tech 注入/driver/cds.lib 检查，不是几何 oracle。
- 盲点：lriReport 只写不读（librf.il:1634），lriStepList 无引用（librf.il:1520）。

## 附：已核实的差异

1. Python 镜像门禁不同：SKILL 只要 xPort>1e-9（librf.il:2044），layout.py 要 xPort≥xj；SKILL 对 c 钳位（librf.il:1724），layout.py 报错。
2. 旧卡"cv->figs 恒空"不成立：layout 用 shapes（librf.il:789-790），views 用 figs（librf.il:962-965）。
3. DEVELOPMENT_GUIDE.md 缺失（librf.il:5 引用）。
