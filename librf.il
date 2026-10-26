;====================================================================
; librf.il — optimizer_v09 SKILL 合并文件（两部分，两种 Cadence 进程共用）
;   A) 版图引擎 (原 librf_layout.il)：dbAccess 经 driver.il load() 后调
;      librfRun(...) / librfMakeViews(...) / librfEmxViews(...)
;   B) 求值服务 (原 eval_serve.il)：virtuoso -replay librf.il 启动，读 ADE L
;      outputs 定义，从 psf 提取曲线（约 0.5 秒/次）
; 合并依据：procedure 定义不校验未定义函数，OCEAN 与 dbAccess 函数可共处一文件，
; 各自只在被调用时解析；命名空间无冲突。工艺无关化：层名一律经 librfLyr(role)
; 解析（tech_profile.json layers 段），无 profile 时用内建通用默认映射。
;====================================================================
; eval_serve — headless ADE L output evaluator for spectre psf results.
; 环境变量：OPT_QUEUE（队列目录，必需）；OPT_OUTPUTS_STATE（outputs.state，必需）。
; 启动：errset(loadContext("adexl.cxt") nil) -> load OPT_OUTPUTS_STATE（输出表入
; gOptOuts，元素 ->name/->expression，无 expression 时用 ->signal）-> 失败写
; <queue>/error.log 并 exit()（OPT_QUEUE 未设时写 ./error.log）。
; 服务循环：有 <queue>/STOP -> exit()；有 eval_req.txt -> 处理后 rm -f；否则 sleep 0.2。
; 请求（每行 key 值）：PSFDIR <psf 目录> / RESULT <结果文件>
; 结果行序：CURVE_BEGIN <name> / "<x> <y>"(%.12g) / CURVE_END / SCALAR <name> <v> /
;   MSG SKIP <name> type <t> / MSG NO_EXPR <name> / MSG EVAL_FAIL <name> expr <e> /
;   STATUS ok|fail / MSG SKILL_ERROR_SEE_LOG（仅 fail 后）
; 求值：ADE db() 经 subst() 映射到 OCEAN db20()；结果名 sp hb ac dc tran noise pss
; 逐个尝试、上次成功者优先；波形转 CURVE、纯数转 SCALAR、单点 family 解包后同法，
; 其余 MSG SKIP；全部 errset 包裹，出错 -> STATUS fail + 错误行写 error.log。
; Globals 只用普通赋值：var() 在此环境顶层不建运行时绑定。
gOptOuts = nil
gLastResult = nil
; Dump a waveform as CURVE_BEGIN / point / CURVE_END lines.
procedure(myDumpWave(outfp name val)
  let((xvec yvec n idx)
    xvec = drGetWaveformXVec(val)
    yvec = drGetWaveformYVec(val)
    n = drVectorLength(yvec)
    fprintf(outfp "CURVE_BEGIN %s\n" name)
    for(idx 0 n-1
      fprintf(outfp "%.12g %.12g\n"
        drGetElem(xvec idx) drGetElem(yvec idx)))
    fprintf(outfp "CURVE_END\n")))
; Dump one evaluated value: waveform->curve, number->scalar, single-point family unwrapped, else skipped.
procedure(myDumpValue(outfp name val)
  if(drIsWaveform(val) == t
    then
      myDumpWave(outfp name val)
    else
      if(numberp(val)
        then
          fprintf(outfp "SCALAR %s %.12g\n" name val)
        else
          if(famIsFamily(val) == t
            then
              let((svals)
                svals = famGetSweepValues(val)
                if(length(svals) == 1
                  then
                    myDumpValue(outfp name famValue(val car(svals)))
                  else
                    fprintf(outfp "MSG SKIP %s type %L\n" name type(val))))
            else
              fprintf(outfp "MSG SKIP %s type %L\n" name type(val))))))
; Evaluate one ADE L output expr; the working result name is remembered in gLastResult and tried first.
procedure(myEvalOutput(outfp o)
  let((expr val order resName r)
    expr = o->expression
    unless(expr
      when(o->signal
        expr = list('getData o->signal)))
    unless(expr
      fprintf(outfp "MSG NO_EXPR %s\n" o->name)
      return(nil))
    ; Map the ADE db() function to the OCEAN db20() function.
    expr = subst('db20 'db expr)
    order = nil
    when(gLastResult
      order = list(gLastResult))
    foreach(r '(sp hb ac dc tran noise pss)
      unless(r == gLastResult
        order = append(order list(r))))
    foreach(resName order
      unless(val
        ; A missing result type is normal fallback: stay silent (real errors still log).
        when(errset(selectResult(resName) nil)
          val = errset(eval(expr) t)
          when(val gLastResult = resName))))
    if(val
      then
        myDumpValue(outfp o->name car(val))
      else
        fprintf(outfp "MSG EVAL_FAIL %s expr %L\n" o->name expr))))
; Append a message to <queue>/error.log, created if needed.
procedure(myLogError(msg)
  let((fp queueDir)
    queueDir = getShellEnvVar("OPT_QUEUE")
    unless(queueDir queueDir = ".")
    fp = outfile(strcat(queueDir "/error.log") "a")
    when(fp
      fprintf(fp "eval_serve: %s\n" msg)
      close(fp))))
; Report an unrecoverable problem and leave the process.
procedure(myFatal(msg)
  myLogError(msg)
  exit())
; Serve one request file. See the header for the file formats.
procedure(myProcessRequest(reqFile)
  let((fp line key psfDir resultFile outfp res ok)
    psfDir = nil
    resultFile = nil
    fp = infile(reqFile)
    unless(fp
      myFatal(strcat("cannot open request file " reqFile)))
    while(gets(line fp)
      key = parseString(line " \t\n")
      case(car(key)
        ("PSFDIR" psfDir = cadr(key))
        ("RESULT" resultFile = cadr(key))))
    close(fp)
    unless(resultFile
      myFatal(strcat("RESULT key missing in " reqFile)))
    outfp = outfile(resultFile "w")
    unless(outfp
      myFatal(strcat("cannot open result file " resultFile)))
    ok = errset(progn(
      res = openResults(psfDir)
      foreach(o gOptOuts
        myEvalOutput(outfp o))
      when(res closeResults(res)) ) t)
    unless(ok
      ; The block aborted early; release leftover open results.
      when(res errset(closeResults(res) nil)))
    if(ok
      then
        fprintf(outfp "STATUS ok\n")
      else
        fprintf(outfp "STATUS fail\n")
        fprintf(outfp "MSG SKILL_ERROR_SEE_LOG\n"))
    close(outfp)
    unless(ok
      myLogError(strcat("request failed, see result file " resultFile)))))
; The persistent serve loop. See the header for the queue protocol.
procedure(myServeEval()
  let((queueDir reqPath)
    queueDir = getShellEnvVar("OPT_QUEUE")
    unless(queueDir
      myFatal("OPT_QUEUE is not set"))
    reqPath = strcat(queueDir "/eval_req.txt")
    while(t
      cond(
        (isFile(strcat(queueDir "/STOP"))
          exit())
        (isFile(reqPath)
          myProcessRequest(reqPath)
          system(strcat("rm -f '" reqPath "'")))
        (t
          system("sleep 0.2"))))))
; ---- startup ----
;---------- 求值服务入口 ----------
; 仅当 OPT_QUEUE 已设置时才启动 serve 循环：
;   virtuoso -replay librf.il  (optimizer.py 的求值服务) -> 启动
;   dbAccess load(librf.il)    (driver.il 出图)           -> 跳过
; 这样两个 il 才能安全合成一个文件。
when(getShellEnvVar("OPT_QUEUE")
let((queueDir stateFile loaded)
    queueDir = getShellEnvVar("OPT_QUEUE")
    unless(queueDir
      myFatal("OPT_QUEUE is not set"))
    stateFile = getShellEnvVar("OPT_OUTPUTS_STATE")
    unless(stateFile
      myFatal("OPT_OUTPUTS_STATE is not set"))
    unless(isFile(stateFile)
      myFatal(strcat("outputs state file not found: " stateFile)))
    errset(loadContext("adexl.cxt") nil)
    loaded = errset(progn(
      load(stateFile)
      gOptOuts = car(savedComponent)
      t ) nil)
    unless(loaded
      myFatal(strcat("cannot load outputs state: " stateFile)))
    unless(listp(gOptOuts)
      myFatal(strcat("outputs state has no outputs list: " stateFile)))
    let((vok)
      vok = errset(progn(
        foreach(o gOptOuts
          unless(stringp(o->name)
            error("output without a name")))
        t ) nil)
      unless(vok
        myFatal(strcat("bad output entries in: " stateFile))))
    myServeEval()))
; librf_layout.il — LIBRF SKILL 版图引擎 (IC618 经典 SKILL / dbAccess, optimizer_v09)
; 职责: 电感/传输线/变压器/耦合线参数化绘制 + port/label + IND 标记 + via 阵列
; 入口: librfRun(req) / librfMakeViews(req) / librfEmxViews(req)；req = list(nil 'type "ind"
;   'lib "LIBRF" ... 'result "/p")
; 协议: 画完写 result 行文件 STATUS/CELL/BBOX/PORT/LAYERS/WARN/ERR, 末尾 exit()
; 约定: 几何单位 µm 浮点, 落盘对齐 0.001µm(1nm); errset 只收 1 表达式; 禁用变量名 t
;=============================================================================
;---------------------------------------------------------------- 结果收集器
defvar(librf__resultPath nil)
defvar(librf__warns nil)
defvar(librf__errs nil)
defvar(librf__ports nil)      ; ((name x y txtLayer) ...)
defvar(librf__layers nil)     ; 用过的层名字符串表
defvar(librf__ok t)
defvar(gLibrfSelfFail nil)    ; librfSelfTest 失败标志
; 清空本次运行的收集器
procedure(librfReset()
  librf__warns = nil
  librf__errs = nil
  librf__ports = nil
  librf__layers = nil
  librf__ok = t)
; 记录警告 (不致失败)
procedure(librfWarn(msg)
  librf__warns = cons(msg librf__warns))
; 记录错误并置失败
procedure(librfErr(msg)
  librf__errs = cons(msg librf__errs)
  librf__ok = nil)
; 记录用过的层名 (去重)
procedure(librfNoteLayer(layerName)
  when(layerName && !member(layerName librf__layers)
    librf__layers = cons(layerName librf__layers)))
; 记录端口: name x y txtLayerName
procedure(librfNotePort(name x y txtLayer)
  librf__ports = cons(list(name librfSnap(x) librfSnap(y) txtLayer) librf__ports))
;---------------------------------------------------------------- 浮点/格式化
; 对齐到 1nm 网格 (µm 浮点)
procedure(librfSnap(v)
  (round(v * 1000.0) / 1000.0))
; 浮点格式化为字符串 (固定 3 位小数, 便于行协议解析)
procedure(librfF3(v)
  sprintf(nil "%.3f" librfSnap(v)))
; 取点表末点 (不依赖 last, 用 car(reverse))
procedure(librfLastPt(pts)
  car(reverse(pts)))
; cellview 整体 bbox: dbComputeBBox 在此环境返回 t, 实际数据在 cv->bBox
procedure(librfCvBBox(cv)
  let((b)
    b = dbComputeBBox(cv)
    when(b == t  b = cv->bBox)
    librfBoxFlat(b)))
; bBox ((x1 y1)(x2 y2)) -> 平坦 (x1 y1 x2 y2)
procedure(librfBoxFlat(b)
  when(b
    list(caar(b) cadar(b) caadr(b) cadadr(b))))
;---------------------------------------------------------------- req 取值
; 从 list(nil 'k v 'k2 v2 ...) 平坦关键字表取值; 找不到返回 dflt
procedure(librfReqGet(req key dflt)
  let((seg)
    seg = member(key req)
    cond(
      (seg && cdr(seg)  cadr(seg))
      (t                dflt))))
; 取浮点参数并做基本数值清洗
procedure(librfReqNum(req key dflt)
  let((v)
    v = librfReqGet(req key dflt)
    cond(
      (numberp(v)  v)
      (stringp(v)  atof(v))
      (t           dflt))))
; 取字符串参数
procedure(librfReqStr(req key dflt)
  let((v)
    v = librfReqGet(req key dflt)
    cond(
      (stringp(v)  v)
      (symbolp(v)  symbolToString(v))
      (numberp(v)  sprintf(nil "%g" v))
      (t           dflt))))
;---------------------------------------------------------------- 结果文件
; 写 result 行协议 (SPEC §2.1). okFlag=t 写 STATUS ok 否则 fail
procedure(librfWriteResult(okFlag cellName bbox)
  let((port line)
    when(librf__resultPath
      port = errset(outfile(librf__resultPath) t)
      when(port && car(port)
        port = car(port)
        if(okFlag
          fprintf(port "STATUS ok\n")
          fprintf(port "STATUS fail\n"))
        when(cellName fprintf(port "CELL %s\n" cellName))
        when(bbox
          fprintf(port "BBOX %s %s %s %s\n"
            librfF3(car(bbox)) librfF3(cadr(bbox))
            librfF3(caddr(bbox)) librfF3(cadddr(bbox))))
        foreach(entry reverse(librf__ports)
          fprintf(port "PORT %s %s %s %s\n"
            car(entry) librfF3(cadr(entry))
            librfF3(caddr(entry)) cadddr(entry)))
        when(librf__layers
          fprintf(port "LAYERS")
          foreach(ln reverse(librf__layers) fprintf(port " %s" ln))
          fprintf(port "\n"))
        foreach(msg reverse(librf__warns) fprintf(port "WARN %s\n" msg))
        foreach(msg reverse(librf__errs)  fprintf(port "ERR %s\n" msg))
        close(port)))
    ; stdout 标记, 供外部驱动判定
    printf("LIBRF_EXIT=%s\n" if(okFlag then "0" else "1"))))
;---------------------------------------------------------------- 网格/图元
; 画正交矩形 (box=list(list(x1 y1) list(x2 y2))); 坐标先 snap
procedure(librfRect(cv layerSpec x1 y1 x2 y2)
  let((a b c d layName res)
    a = librfSnap(min(x1 x2))  b = librfSnap(min(y1 y2))
    c = librfSnap(max(x1 x2))  d = librfSnap(max(y1 y2))
    layName = if(listp(layerSpec) then car(layerSpec) else layerSpec)
    librfNoteLayer(layName)
    ; errset 返回值检查: 失败/空返回都报 ERR, 不静默丢图元
    res = errset(dbCreateRect(cv layerSpec list(list(a b) list(c d))) t)
    when(res == nil || car(res) == nil
      librfErr(sprintf(nil "画矩形失败 layer=%s" layName)))))
; 45° 平行四边形 (真 45° 斜边): p0->p1 中心线, 宽 w, 端头平齐
procedure(librfDiagSeg(cv layerSpec x0 y0 x1 y1 w)
  prog((dx dy len nx ny pts half)
    dx = x1 - x0   dy = y1 - y0
    half = w / 2.0
    len = sqrt(dx*dx + dy*dy)
    when(len < 0.001
      librfErr("45°段长度为 0")
      return(nil))
    ; 单位法向 = (-dy, dx)/len
    nx = (0.0 - dy) / len * half
    ny = dx / len * half
    pts = list(
      list(librfSnap(x0 + nx) librfSnap(y0 + ny))
      list(librfSnap(x0 - nx) librfSnap(y0 - ny))
      list(librfSnap(x1 - nx) librfSnap(y1 - ny))
      list(librfSnap(x1 + nx) librfSnap(y1 + ny)))
    librfNoteLayer(if(listp(layerSpec) then car(layerSpec) else layerSpec))
    ; 优先多边形(平行四边形, 真 45°); 失败退 dbCreatePath
    if(errset(dbCreatePolygon(cv layerSpec pts) t) == nil
      then errset(dbCreatePath(cv layerSpec
             list(list(librfSnap(x0) librfSnap(y0))
                  list(librfSnap(x1) librfSnap(y1))) w) t))
    ; 拐角补方块, 保证与相邻段搭接
    librfRect(cv layerSpec x0 - half y0 - half x0 + half y0 + half)
    librfRect(cv layerSpec x1 - half y1 - half x1 + half y1 + half)
    return(t)))
; 折线扫掠 -> dbCreatePath 一次画出 (中心线 pts + 宽 w)
; 顶点序列 0/45/90° 走向; GDS 导出为 PATH, 拐角由 path miter 自然外倒角,
; 根除 rect+diagseg 拼接的黑色缺口/锐角毛刺.
; 端点补 w×w 方块, 保证与 stub/underpass 搭接 (无论末段走向).
procedure(librfEndCap(cv layerSpec x y w)
  let((half)
    half = w / 2.0
    librfRect(cv layerSpec x - half y - half x + half y + half)))
procedure(librfPathSweep(cv layerSpec pts w)
  prog((clean p prev n res layName half)
    ; snap + 去重 (相邻点距离 <0.5nm 视为同点)
    clean = nil
    foreach(p pts
      when(clean == nil ||
           abs(librfSnap(car(p)) - car(librfLastPt(clean))) > 0.0005 ||
           abs(librfSnap(cadr(p)) - cadr(librfLastPt(clean))) > 0.0005
        clean = cons(list(librfSnap(car(p)) librfSnap(cadr(p))) clean)))
    clean = reverse(clean)
    n = length(clean)
    when(n < 2
      librfErr("path 点数 <2")
      return(nil))
    layName = if(listp(layerSpec) then car(layerSpec) else layerSpec)
    librfNoteLayer(layName)
    half = w / 2.0
    ; 首选: dbCreatePath 中心线折线 (4 参, pathType 创建后可设)
    res = errset(dbCreatePath(cv layerSpec clean w) t)
    when(res && car(res)
      ; pathType=0 truncate/flush: 端点止于顶点, 由 EndCap 搭接
      errset(car(res)->pathType = 0 t))
    when(res == nil || car(res) == nil
      librfErr(sprintf(nil "dbCreatePath 失败 layer=%s npts=%d, 退矩形扫掠" layName n))
      ; 回退: 旧 rect+diagseg 扫掠 (不应走到)
      prev = car(clean)
      foreach(p cdr(clean)
        librfDiagSeg(cv layerSpec car(prev) cadr(prev) car(p) cadr(p) w)
        prev = p))
    ; 端帽: 与 stub/underpass 无缝搭接
    librfEndCap(cv layerSpec car(car(clean)) cadr(car(clean)) w)
    librfEndCap(cv layerSpec
      car(librfLastPt(clean)) cadr(librfLastPt(clean)) w)
    return(t)))
;---------------------------------------------------------------- via 阵列
; 在区域满铺 cut: 见方 cutSize, 间距 cutSp, 包覆 enclX/enclY; 行列 ≥1, 网格居中, 返回 cut 个数
procedure(librfViaArray(cv cutLayer x1 y1 x2 y2 cutSize cutSp enclX enclY)
  let((xa ya xb yb wUse hUse pitch nCol nRow i j cx cy half n)
    xa = min(x1 x2)  ya = min(y1 y2)
    xb = max(x1 x2)  yb = max(y1 y2)
    wUse = (xb - xa) - 2.0 * enclX
    hUse = (yb - ya) - 2.0 * enclY
    pitch = cutSize + cutSp
    nCol = max(1 fix((wUse + cutSp) / pitch))
    nRow = max(1 fix((hUse + cutSp) / pitch))
    half = cutSize / 2.0
    n = 0
    for(i 0 nRow - 1
      for(j 0 nCol - 1
        ; 网格整体居中
        cx = (xa + xb) / 2.0 + (j - (nCol - 1) / 2.0) * pitch
        cy = (ya + yb) / 2.0 + (i - (nRow - 1) / 2.0) * pitch
        librfRect(cv cutLayer cx - half cy - half cx + half cy + half)
        n = n + 1))
    n))
; 带网格偏移的 via 阵列 (两级栈交错用): cut 网格相对区域中心错开 (offX,offY)
; 错位后行列各收缩 1 保持包覆; 单列/行无空间错位时偏移归零(同位栈)
procedure(librfViaArrayOff(cv cutLayer x1 y1 x2 y2 cutSize cutSp enclX enclY offX offY)
  let((xa ya xb yb wUse hUse pitch nCol nRow i j cx cy half n)
    xa = min(x1 x2)  ya = min(y1 y2)
    xb = max(x1 x2)  yb = max(y1 y2)
    wUse = (xb - xa) - 2.0 * enclX
    hUse = (yb - ya) - 2.0 * enclY
    pitch = cutSize + cutSp
    nCol = max(1 fix((wUse + cutSp) / pitch))
    nRow = max(1 fix((hUse + cutSp) / pitch))
    when(nCol <= 1 offX = 0.0)
    when(nRow <= 1 offY = 0.0)
    when(offX != 0.0 nCol = nCol - 1)
    when(offY != 0.0 nRow = nRow - 1)
    half = cutSize / 2.0
    n = 0
    for(i 0 nRow - 1
      for(j 0 nCol - 1
        cx = (xa + xb) / 2.0 + offX + (j - (nCol - 1) / 2.0) * pitch
        cy = (ya + yb) / 2.0 + offY + (i - (nRow - 1) / 2.0) * pitch
        librfRect(cv cutLayer cx - half cy - half cx + half cy + half)
        n = n + 1))
    n))
;---------------------------------------------------------------- label/pin
; 真 pin (layout): net + terminal + pin 对象 + TXT label
; 配方 (2026-10-16 VM 实验锁定):
;   net  = dbCreateNet(cv name)  或 dbFindNetByName 兜底
;   term = dbCreateTerm(net name "inputOutput")   ; 3 参, 第 1 参是 net
;   fig  = dbCreateRect(cv list(metal "pin") tipBox)  ; 可见 pin 标记
;   pin  = dbCreatePin(net figOrNil name)         ; fig 会挂到 pin
;   lab  = dbCreateLabel(cv list(txt "drawing") ...) ; GDS 仍走 /5, EMX 兼容
procedure(librfPortPin(cv metalName txtLayer x y portName)
  let((netObj res fig lab txtName h)
    txtName = if(listp(txtLayer) then car(txtLayer) else txtLayer)
    librfNoteLayer(txtName)
    h = 2.0
    ; net (已存在则找回)
    netObj = nil
    res = errset(dbCreateNet(cv portName) t)
    if(res && car(res)
      then netObj = car(res)
      else netObj = dbFindNetByName(cv portName))
    when(netObj
      errset(dbCreateTerm(netObj portName "inputOutput") t)
      ; pin 目的层小方块: Virtuoso Layout 肉眼可见 pin
      fig = errset(dbCreateRect(cv list(metalName "pin")
        list(list(librfSnap(x) - 1.5 librfSnap(y) - 1.5)
             list(librfSnap(x) + 1.5 librfSnap(y) + 1.5))) t)
      if(fig && car(fig)
        then res = errset(dbCreatePin(netObj car(fig) portName) t)
        else res = errset(dbCreatePin(netObj nil portName) t))
      when(res == nil
        librfWarn(sprintf(nil "dbCreatePin 失败 port=%s (label 仍保留)" portName))))
    ; TXT drawing label — strmout 后仍是 /5 文字层 (EMX 端口兼容性)
    lab = errset(dbCreateLabel(cv txtLayer
      list(librfSnap(x) librfSnap(y)) portName
      "centerCenter" "R0" "roman" h) t)
    when(lab == nil || car(lab) == nil
      librfErr(sprintf(nil "打 label 失败 layer=%s port=%s" txtName portName)))))
; 端口 pin + TXT label: 矩形 (x1,y1)-(x2,y2) 金属 + 中心真 pin
procedure(librfPinLabel(cv metalLayer txtLayer x1 y1 x2 y2 portName)
  let((cx cy metalName)
    librfRect(cv metalLayer x1 y1 x2 y2)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    metalName = if(listp(metalLayer) then car(metalLayer) else metalLayer)
    librfPortPin(cv metalName txtLayer cx cy portName)
    librfNotePort(portName cx cy
      if(listp(txtLayer) then car(txtLayer) else txtLayer))))
; 字符串->方向符号 (symbol() 函数在 dbAccess 不存在)
procedure(librfDirSym(s)
  cond(
    ((s == "W" || s == "w") 'W)
    ((s == "E" || s == "e") 'E)
    ((s == "S" || s == "s") 'S)
    ((s == "N" || s == "n") 'N)
    (t 'S)))

; 端头真 pin: 打在 stub 端头内侧 1.5um (EMX label-snap 4um, 中心会超距)
procedure(librfTipLabel(cv metalLayer txtLayer x y dirSym portName)
  let((lx ly metalName)
    lx = x  ly = y
    cond(
      (dirSym == 'W  lx = x + 1.5)
      (dirSym == 'E  lx = x - 1.5)
      (dirSym == 'S  ly = y + 1.5)
      (dirSym == 'N  ly = y - 1.5))
    metalName = if(listp(metalLayer) then car(metalLayer) else metalLayer)
    librfPortPin(cv metalName txtLayer lx ly portName)
    librfNotePort(portName lx ly
      if(listp(txtLayer) then car(txtLayer) else txtLayer))))

procedure(librfStubPort(cv metalLayer txtLayer x y dirSym w len portName)
  let((half x1 y1 x2 y2 neckW neckLen fullLen)
    half = w / 2.0
    neckW = 5.0
    neckLen = 5.0
    cond(
      (dirSym == 'W
        x1 = x - len  y1 = y - half  x2 = x      y2 = y + half)
      (dirSym == 'E
        x1 = x        y1 = y - half  x2 = x + len y2 = y + half)
      (dirSym == 'S
        x1 = x - half y1 = y - len   x2 = x + half y2 = y)
      (dirSym == 'N
        x1 = x - half y1 = y         x2 = x + half y2 = y + len)
      (t
        x1 = x        y1 = y - half  x2 = x + len y2 = y + half))
    if(w > 10.0 && len > neckLen + 1.0
      then
        ;---- w>10: 全宽段 + 5µm 窄颈(宽 5µm) 再放 label (SPEC §9.4)
        fullLen = len - neckLen
        cond(
          (dirSym == 'W
            librfRect(cv metalLayer x - fullLen y - half x y + half)
            librfRect(cv metalLayer x - len y - neckW / 2.0
              x - fullLen y + neckW / 2.0)
            librfTipLabel(cv metalLayer txtLayer x - len y dirSym portName))
          (dirSym == 'E
            librfRect(cv metalLayer x y - half x + fullLen y + half)
            librfRect(cv metalLayer x + fullLen y - neckW / 2.0
              x + len y + neckW / 2.0)
            librfTipLabel(cv metalLayer txtLayer x + len y dirSym portName))
          (dirSym == 'S
            librfRect(cv metalLayer x - half y - fullLen x + half y)
            librfRect(cv metalLayer x - neckW / 2.0 y - len
              x + neckW / 2.0 y - fullLen)
            librfTipLabel(cv metalLayer txtLayer x y - len dirSym portName))
          (dirSym == 'N
            librfRect(cv metalLayer x - half y x + half y + fullLen)
            librfRect(cv metalLayer x - neckW / 2.0 y + fullLen
              x + neckW / 2.0 y + len)
            librfTipLabel(cv metalLayer txtLayer x y + len dirSym portName))
          (t
            librfRect(cv metalLayer x y - half x + fullLen y + half)
            librfRect(cv metalLayer x + fullLen y - neckW / 2.0
              x + len y + neckW / 2.0)
            librfTipLabel(cv metalLayer txtLayer x + len y 'E portName)))
      else
        librfRect(cv metalLayer x1 y1 x2 y2)
        ; label 打在端头内侧 1.5um (贴近外缘, 满足 EMX label-snap)
        cond(
          (dirSym == 'W  librfTipLabel(cv metalLayer txtLayer x1 y dirSym portName))
          (dirSym == 'E  librfTipLabel(cv metalLayer txtLayer x2 y dirSym portName))
          (dirSym == 'S  librfTipLabel(cv metalLayer txtLayer x y1 dirSym portName))
          (dirSym == 'N  librfTipLabel(cv metalLayer txtLayer x y2 dirSym portName))
          (t             librfTipLabel(cv metalLayer txtLayer x2 y dirSym portName))))))
;---------------------------------------------------------------- 工艺配置 (tech profile)
; 层名不再写死：librfLyr(role) 把「角色」解析成该工艺的真实层名。
; librfTech 是 ( (role . layerName) ... ) 关联表，由 driver.il 经 req 的
; 'tech 键注入；缺省表即内建通用映射，所以不给 profile 时也能直接跑。
; 使用者在 tech_profile.json 里改 layers.spiral 等即可换金属绘制。
defvar(librfTech nil)
defvar(librfTechText nil)
procedure(librfTechDefault()
  list(
    list("spiral"    "M5")
    list("underpass" "M4")
    list("route"     "M3")
    list("ground"    "M3")
    list("via_sp"    "V4")
    list("via_un"    "V3")
    list("marker"    "MARKER")))
procedure(librfTechTextDefault()
  list(
    list("M5" "M5TXT")
    list("M4" "M4TXT")
    list("M3" "M3TXT")
    list("M2" "M2TXT")
    list("M1" "M1TXT")))
; role(字符串) -> 层名。查不到时按 role 原样返回（允许直接传层名）。
procedure(librfLyr(role)
  let((p tbl)
    tbl = if(librfTech then librfTech else librfTechDefault())
    p = assoc(role tbl)
    cond(
      (p && cdr(p)  cadr(p))
      (t            role))))
; 金属层名 -> 该金属的 pin/label 文字层。
procedure(librfTxtLayerOf(metalName)
  let((p tbl)
    tbl = if(librfTechText then librfTechText else librfTechTextDefault())
    p = assoc(metalName tbl)
    cond(
      (p && cdr(p)  cadr(p))
      (t            strcat(metalName "TXT")))))
; DRC 阈值查表。librfDrc = ( ("min_w" 1.0) ("min_area" ( ("M5" 4.0) ... )) )
; 由 driver.il 经 req 'drc 注入；缺项回落到内建默认值。
procedure(librfDrcNum(key dflt)
  let((p)
    when(boundp('librfDrc) && librfDrc
      p = assoc(key librfDrc))
    cond(
      (p && cdr(p) && numberp(cadr(p))  cadr(p))
      (t                                dflt))))

procedure(librfDrcMinArea(metalName)
  let((p sub)
    when(boundp('librfDrc) && librfDrc
      p = assoc("min_area" librfDrc))
    when(p && cdr(p)
      sub = assoc(metalName cadr(p))
      when(sub && cdr(sub) && numberp(cadr(sub))
        return(cadr(sub))))
    4.0)))
procedure(librfMetalPair(nm) list(nm "drawing"))
procedure(librfViaPair(nm)   list(nm "drawing"))
procedure(librfTxtPair(nm)   list(librfTxtLayerOf(nm) "drawing"))
; 工艺库名（建库时绑定 techLibName）。由 req 'techlib 或 LIBRF_TECHLIB 覆盖。
defvar(librfTechLib nil)
procedure(librfTechLibName()
  cond(
    (librfTechLib  librfTechLib)
    (t             "RFCMOS")))
;---------------------------------------------------------------- DRC 内建自检
; 输入参数级 DRC: 宽/间距/面积/最大宽. 违规写 ERR 或 WARN. 返回 t/nil
procedure(librfDrcParams(w s inInd wideSp)
  let((ok minW maxW minS)
    ok = t
    ; 防御性浮点强转：SKILL 的 %.3f 不收整数（driver 若给 'w 10 会崩 sprintf）
    w = 1.0 * w
    s = 1.0 * s
    ; 阈值优先取 tech_profile.json 的 drc 段，缺项用内建默认
    minW = librfDrcNum("min_w" 1.0)
    maxW = librfDrcNum("max_w" 30.0)
    minS = librfDrcNum("min_s" 1.0)
    when(w < minW
      librfErr(sprintf(nil "DRC: 线宽 %.3f < %.3f (minW)" w minW))
      ok = nil)
    when(w > maxW
      librfErr(sprintf(nil "DRC: 线宽 %.3f > %.3f (maxW)" w maxW))
      ok = nil)
    when(s < minS
      librfErr(sprintf(nil "DRC: 间距 %.3f < %.3f (minS)" s minS))
      ok = nil)
    ; 电感区 / 宽金属 / 大块的保守间距 = 通用倍数规则；分档细则按你的 PDK 收紧
    when(inInd && s < 2.0 * minS
      librfErr(sprintf(nil "DRC: 电感区间距 %.3f < %.3f (2*minS)" s 2.0 * minS))
      ok = nil)
    when(wideSp && s < 2.0 * minS
      librfErr(sprintf(nil "DRC: 宽金属间距 %.3f < %.3f (2*minS)" s 2.0 * minS))
      ok = nil)
    when(w >= 30.0 && s < 1.5 * minS
      librfErr(sprintf(nil "DRC: 大块(宽 %.1f>=30)间距 %.3f < %.3f (1.5*minS)" w s 1.5 * minS))
      ok = nil)
    when(w < 2.0
      librfWarn(sprintf(nil "DRC: w=%.3f<2.0, 独立小块面积可能低于最小面积" w)))
    ok))
; 两个平坦 bbox 是否搭接/重叠 (容差 0.001, 视为同一形状)
procedure(librfBoxTouch(b1 b2)
  let((ax1 ay1 ax2 ay2 bx1 by1 bx2 by2)
    ax1 = car(b1) - 0.001  ay1 = cadr(b1) - 0.001
    ax2 = caddr(b1) + 0.001  ay2 = cadddr(b1) + 0.001
    bx1 = car(b2)  by1 = cadr(b2)
    bx2 = caddr(b2)  by2 = cadddr(b2)
    not(ax2 < bx1 || bx2 < ax1 || ay2 < by1 || by2 < ay1)))
; 两平坦 bbox 的分离距离 (搭接/重叠返回 ≤0)
procedure(librfBoxGap(b1 b2)
  let((dx dy)
    dx = max(car(b1) - caddr(b2)  car(b2) - caddr(b1))
    dy = max(cadr(b1) - cadddr(b2)  cadr(b2) - cadddr(b1))
    if(dx > 0.0 && dy > 0.0
      then sqrt(dx * dx + dy * dy)
      else max(dx dy))))
; 抽查已画矩形: 短边宽、面积、大块间距. minArea 违规判 ERR(孤立小块) / 搭接块降 WARN
; 注: IC618 cv->figs 恒空, 必须用 cv->shapes; 只查 drawing 目的 (跳过 pin/label)
procedure(librfDrcFigs(cv metalName)
  let((minW minA fig b w h boxes bb g nTouch nBox i j b1 b2 gap minGap nViol big1 big2)
    minW = librfDrcNum("min_w" 2.5)
    minA = librfDrcMinArea(metalName)
    ; 收集该层全部矩形平坦 bbox (判孤立小块/大块间距用)
    boxes = nil
    foreach(fig cv->shapes
      when(equal(fig->objType 'rect) && fig->layerName == metalName
        when(fig->purposeName == nil || fig->purposeName == "drawing"
          boxes = cons(librfBoxFlat(fig->bBox) boxes))))
    foreach(fig cv->shapes
      when(equal(fig->objType 'rect) && fig->layerName == metalName
        when(fig->purposeName == nil || fig->purposeName == "drawing"
        b = fig->bBox
        w = abs(caadr(b) - caar(b))
        h = abs(cadadr(b) - cadar(b))
        when(min(w h) + 0.0005 < minW
          librfWarn(sprintf(nil "DRC: 矩形短边 %.3f < %.1f (%s)"
            min(w h) minW metalName)))
        when(w * h + 0.001 < minA
          bb = librfBoxFlat(b)
          ; 孤立判定: 与同层其他矩形搭接则视为整体形状一部分
          nTouch = 0
          foreach(g boxes
            when(librfBoxTouch(bb g) nTouch = nTouch + 1))
          if(nTouch <= 1
            then librfErr(sprintf(nil "DRC: 孤立小块面积 %.3f < %.2f (%s) 违反 minArea"
                   w * h minA metalName))
            else librfWarn(sprintf(nil "DRC: 矩形面积 %.3f < %.2f (%s) 与他块搭接, 以合并形状核算"
                   w * h minA metalName)))))))
    ; 大块(≥30µm 宽)间距 ≥3.0 (SPEC §8)
    nBox = length(boxes)
    minGap = 1.0e30
    nViol = 0
    for(i 1 nBox
      for(j (i + 1) nBox
        b1 = nth(i - 1 boxes)  b2 = nth(j - 1 boxes)
        big1 = min(caddr(b1) - car(b1)  cadddr(b1) - cadr(b1)) >= 30.0
        big2 = min(caddr(b2) - car(b2)  cadddr(b2) - cadr(b2)) >= 30.0
        when(big1 || big2
          gap = librfBoxGap(b1 b2)
          when(gap > 0.001 && gap < 2.999
            nViol = nViol + 1
            minGap = min(minGap gap)))))
    when(nViol > 0
      librfErr(sprintf(nil "DRC: 大块(≥30µm)间距 %.3f < 3.0 (%s), 违规 %d 处, 请加大间距到 ≥3.0"
        minGap metalName nViol)))))
;=============================================================================
; 纯几何计算 (不依赖 db, 可独立自测)
;=============================================================================
; 几何闭合: din = dout - 2*[n*(w+s) - s]   (res3 式 A)
procedure(librfDinFromParams(n w s dout)
  let((pitch)
    pitch = w + s
    dout - 2.0 * (n * pitch - s)))
;-----------------------------------------------------------------------------
; 方形螺旋中心线 — 嵌套正方形逐角内缩 (批评裁决 §9.2 canonical)
; 起点 (a0,-a0) 首段 -x, ccw 向内。
; 段长: ℓ0=ℓ1=ℓ2=L0; ℓ_{k>=3} = L0 - fix((k-1)/2)*pitch
; 黄金断言 (selftest): bbox=dout×dout; 平行段恒距 pitch; n=1 孔径=dout-2w;
;   n=2.5,w=10,s=2.5,dout=100 -> 段长 90 90 90 77.5 77.5 65 65 52.5 52.5 40
; 返回 ((x y) ...) 顶点表 (含起点), 长度 = 段数+1
;-----------------------------------------------------------------------------
procedure(librfSquareSpiralPts(n w s dout)
  let((pitch L0 nSeg a0 x y pts dirs k seg dx dy stopMin)
    pitch = w + s
    L0 = dout - w
    nSeg = fix(round(4.0 * n))
    a0 = L0 / 2.0
    x = a0  y = 0.0 - a0
    pts = list(list(x y))
    ; 方向序列 ccw 向内: W N E S
    dirs = list(list(-1 0) list(0 1) list(1 0) list(0 -1))
    k = 0
    stopMin = max(2.0 * w pitch)
    while(k < nSeg
      seg = if(k <= 2 then L0 else L0 - fix((k - 1) / 2) * pitch)
      when(seg < stopMin break)
      dx = car(nth(mod(k 4) dirs))
      dy = cadr(nth(mod(k 4) dirs))
      x = x + dx * seg
      y = y + dy * seg
      pts = cons(list(x y) pts)   ; 反转收集, 最后 reverse
      k = k + 1)
    ; 截断告警: 实际段数少于请求
    when(k < nSeg
      librfWarn(sprintf(nil "螺旋截断: 请求 %d 段, 实际 %d 段 (seg<stopMin=%.3f), 实际匝数 %.2f"
        nSeg k stopMin (k / 4.0))))
    reverse(pts)))
;-----------------------------------------------------------------------------
; 八角螺旋中心线 (相邻直线求交法, res3 §2.2-B)
; 与方形 turtle 同向: 起点底边右端, 首段沿底边向西 (顺时针向内)
; 线 k: 法向 phi_k = -90° - k*45°, 距 d_k = r0 - floor(k/4)*pitch
; 顶点 j = 线(j-1) ∩ 线 j ; 段 j 沿线 j 从 V_j 到 V_{j+1}
;-----------------------------------------------------------------------------
procedure(librfOctLine(k r0 pitch)
  ; 返回 (phi d): 线 k 的法向角(rad) 与 距
  let((phi d)
    phi = (-90.0 - k * 45.0) * 3.14159265358979 / 180.0
    d = if(k < 0 then r0 else r0 - fix(k / 4) * pitch)
    list(phi d)))
procedure(librfOctSpiralPts(n w s dout)
  let((pitch r0 nSeg pts j lineA lineB phiA dA phiB dB det px py)
    pitch = w + s
    r0 = (dout - w) / 2.0
    nSeg = fix(round(8.0 * n))
    pts = nil
    for(j 0 nSeg
      lineA = librfOctLine(j - 1 r0 pitch)
      lineB = librfOctLine(j     r0 pitch)
      phiA = car(lineA)  dA = cadr(lineA)
      phiB = car(lineB)  dB = cadr(lineB)
      det = cos(phiA) * sin(phiB) - sin(phiA) * cos(phiB)
      when(abs(det) < 1.0e-9
        librfErr("八角螺旋直线求交退化")
        break)
      px = (dA * sin(phiB) - dB * sin(phiA)) / det
      py = (cos(phiA) * dB - cos(phiB) * dA) / det
      pts = cons(list(px py) pts))
    reverse(pts)))
; 从参数生成螺旋顶点 (shape: "sq"/"oct"); 未知 shape 报 ERR 不静默按 sq
procedure(librfSpiralPts(shape n w s dout)
  cond(
    (shape == "oct" || shape == "octagon"
      librfOctSpiralPts(n w s dout))
    (shape == "sq" || shape == "square"
      librfSquareSpiralPts(n w s dout))
    (t
      librfErr(sprintf(nil "未知 shape: %s (仅支持 sq/oct), 已按 sq 绘制" shape))
      librfSquareSpiralPts(n w s dout))))
; (CT 用半螺旋已并入 librfHalfSpiralPts: 底边+45°跳线, 不含中心汇合)
;-----------------------------------------------------------------------------
; 半螺旋 (CT/交指共用): 南侧蛇形 —— 每圈一条底边 + 45° 收径跳线(左右交替)
; 与 rot180 副本无重叠 (本半 y<=0 区, 副本 y>=0 区), 仅在原点汇合
; n 为每半等效匝数; 返回 (pts aMin)
;-----------------------------------------------------------------------------
procedure(librfHalfSpiralPts(n w s dout)
  prog((pitch a0 nRing k aK aK1 pts)
    pitch = w + s
    a0 = (dout - w) / 2.0
    nRing = max(1 fix(round(2.0 * n)))
    pts = list(list(a0 0.0 - a0))                    ; 东端起, 首段向西
    aK = a0
    for(k 0 nRing - 1
      ; 一条底边 (k 偶: 西行; k 奇: 东行)
      if(mod(k 2) == 0
        then pts = cons(list(0.0 - aK 0.0 - aK) pts)
        else pts = cons(list(aK 0.0 - aK) pts))
      aK1 = aK - pitch
      when(aK1 < w
        return(list(reverse(pts) aK)))
      ; 45° 收径跳线 (与底边端点相接, 真 45°)
      if(mod(k 2) == 0
        then pts = cons(list(0.0 - aK1 0.0 - aK1) pts) ; 西端 -> 东北
        else pts = cons(list(aK1 0.0 - aK1) pts)     ; 东端 -> 西北
      )
      aK = aK1)
    return(list(reverse(pts) aK))))
;-----------------------------------------------------------------------------
; 自测用几何量 (纯计算, 不依赖 db)
;-----------------------------------------------------------------------------
; 点表中心线 bbox -> 平坦 (x1 y1 x2 y2)
procedure(librfPtsBBox(pts)
  let((x1 y1 x2 y2 p)
    x1 = 1.0e30  y1 = 1.0e30  x2 = -1.0e30  y2 = -1.0e30
    foreach(p pts
      x1 = min(x1 car(p))  y1 = min(y1 cadr(p))
      x2 = max(x2 car(p))  y2 = max(y2 cadr(p)))
    list(x1 y1 x2 y2)))
; 相邻平行段垂距表: 按走向(W/E 用 y, N/S 用 x)分组后取同组相邻差
; 嵌套正方形同走向段即相邻平行段, 差应恒等于 pitch
procedure(librfPitchDiffs(pts)
  let((gW gN gE gS prev cur dx dy diffs lst i)
    gW = nil  gN = nil  gE = nil  gS = nil
    prev = car(pts)
    foreach(cur cdr(pts)
      dx = car(cur) - car(prev)
      dy = cadr(cur) - cadr(prev)
      cond(
        (dx < -1.0e-9  gW = cons(cadr(prev) gW))
        (dx >  1.0e-9  gE = cons(cadr(prev) gE))
        (dy >  1.0e-9  gN = cons(car(prev) gN))
        (dy < -1.0e-9  gS = cons(car(prev) gS)))
      prev = cur)
    diffs = nil
    foreach(lst list(gW gN gE gS)
      when(length(lst) >= 2
        for(i 1 (length(lst) - 1)
          diffs = cons(abs(nth(i lst) - nth(i - 1 lst)) diffs))))
    diffs))
; 螺旋孔径: 2*min(最近水平段|y|, 最近垂直段|x|) - w (中心线在原点)
procedure(librfAperture(pts w)
  let((minH minV prev cur dx dy)
    minH = 1.0e30  minV = 1.0e30
    prev = car(pts)
    foreach(cur cdr(pts)
      dx = car(cur) - car(prev)
      dy = cadr(cur) - cadr(prev)
      when(abs(dy) < 1.0e-9 && abs(dx) > 1.0e-9
        minH = min(minH abs(cadr(prev))))
      when(abs(dx) < 1.0e-9 && abs(dy) > 1.0e-9
        minV = min(minV abs(car(prev))))
      prev = cur)
    2.0 * min(minH minV) - w))
; 相邻点距离表
procedure(librfSegLens(pts)
  let((out prev cur)
    out = nil
    prev = car(pts)
    foreach(cur cdr(pts)
      out = cons(sqrt(
        (car(cur) - car(prev)) * (car(cur) - car(prev)) +
        (cadr(cur) - cadr(prev)) * (cadr(cur) - cadr(prev))) out)
      prev = cur)
    reverse(out)))
;=============================================================================
; underpass / IND / 库与 cellview
;=============================================================================
; via 落点+走线+远端回穿+stub 端口 (res3 §underpass)
; (xIn,yIn)=螺旋内端, (xOut,yOut)=螺旋外侧回穿点(调用方按 dout 计算)
; underName: "direct"(经 underpass 层直连) 或 "route"(默认, 经 route 层过渡)
; 走线用 dbCreatePath L 折线一次画出 (规整, 无拼缝)
procedure(librfUnderpass(cv xIn yIn xOut yOut exitDir wUp spiralW underName portName stubLen)
  let((halfUp cutW routePts)
    halfUp = wUp / 2.0
    cutW = 1.5
    ; L 形中心线: 内端 -> 拐点 -> 外端 (已对齐时退化为直线)
    when(abs(xIn - xOut) < 0.001 || abs(yIn - yOut) < 0.001
      routePts = list(list(xIn yIn) list(xOut yOut)))
    when(routePts == nil
      ; 优先沿退出方向先走径向段, 再折向 (与 stub 同轴, 形状规整)
      if(exitDir == 'W || exitDir == 'E
        then routePts = list(list(xIn yIn) list(xOut yIn) list(xOut yOut))
        else routePts = list(list(xIn yIn) list(xIn yOut) list(xOut yOut))))
    if(underName == "direct" || underName == "DIRECT"
      then
        ; 方案 A: spiral -- via_sp -- underpass 走线 -- via_sp -- spiral
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW 1.5 0.5 0.5)
        librfPathSweep(cv librfMetalPair(librfLyr("underpass")) routePts wUp)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW 1.5 0.5 0.5)
      else
        ; 方案 B: spiral -- via_sp -- underpass 过渡 -- via_un -- route 走线 -- via_un -- underpass -- via_sp -- spiral
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW 1.5 0.5 0.5)
        ; via_un 与 via_sp cut 网格错开半格(两级栈交错); 单孔时退化为同位栈
        librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW 1.5 0.5 0.5 1.5 1.5)
        librfPathSweep(cv librfMetalPair(librfLyr("route")) routePts wUp)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
        ; via_un 与 via_sp cut 网格错开半格(两级栈交错); 单孔时退化为同位栈
        librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW 1.5 0.5 0.5 1.5 1.5)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW 1.5 0.5 0.5))
    ; 回 spiral 层落盘 (保证 cut 包覆) + stub 端口
    librfRect(cv librfMetalPair(librfLyr("spiral"))
      xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
    librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
      xOut yOut exitDir spiralW stubLen portName)))
; 选径向退出方向: 取 |x|,|y| 较大者的符号
procedure(librfExitDir(x y)
  cond(
    (and(abs(x) >= abs(y)  x <= 0.0)  'W)
    (abs(x) >= abs(y)                 'E)
    (y <= 0.0                         'S)
    (t                                'N)))
; underpass 外端落点 —— 必须相对「拉长后螺旋实际轮廓」定位, 禁止中心/半移参考系.
; pts=螺旋中心线(已 stretch), w=螺旋线宽, wUp=underpass 走线宽,
; x0/y0=中心(仅用于正交退出时对齐), exitDir.
; 返回 (xOut yOut): 焊盘中心在金属外缘外侧 (w/2+3.0+wUp/2), 与旧公式在 ex=0 时一致.
procedure(librfExitFromPts(pts w wUp x0 y0 exitDir)
  let((bb)
    bb = librfPtsBBox(pts)
    cond(
      (exitDir == 'W
        list(car(bb) - w - wUp / 2.0 - 3.0  y0))
      (exitDir == 'E
        list(caddr(bb) + w + wUp / 2.0 + 3.0  y0))
      (exitDir == 'S
        list(x0  cadr(bb) - w - wUp / 2.0 - 3.0))
      (exitDir == 'N
        list(x0  cadddr(bb) + w + wUp / 2.0 + 3.0))
      (t
        list(car(bb) - w - wUp / 2.0 - 3.0  y0)))))
; 辅助焊盘 vs 螺旋外缘最小间隙断言 (防 ex/ey 半移回归短路).
; gap < minGap 时报 ERR. 返回实测 gap.
procedure(librfAssertPadGap(pts w padX padY padHalf exitDir minGap)
  let((bb gap)
    bb = librfPtsBBox(pts)
    cond(
      (exitDir == 'W
        gap = (car(bb) - w / 2.0) - (padX + padHalf))
      (exitDir == 'E
        gap = (padX - padHalf) - (caddr(bb) + w / 2.0))
      (exitDir == 'S
        gap = (cadr(bb) - w / 2.0) - (padY + padHalf))
      (exitDir == 'N
        gap = (padY - padHalf) - (cadddr(bb) + w / 2.0))
      (t  gap = 999.0))
    when(gap < minGap
      librfErr(sprintf(nil
        "辅助焊盘与螺旋间隙 %.3f < %.3f (dir=%s pad=(%.3f,%.3f)) 短路风险"
        gap minGap exitDir padX padY)))
    gap))
; IND 标记: bbox 外扩 margin 的正交矩形 (132/2)
procedure(librfIndMark(cv bbox margin)
  let((x1 y1 x2 y2 w h)
    when(bbox
      x1 = car(bbox) - margin    y1 = cadr(bbox) - margin
      x2 = caddr(bbox) + margin  y2 = cadddr(bbox) + margin
      w = x2 - x1   h = y2 - y1
      ; IND.F 环宽 30~500 语义未定, 按最小边长告警
      when(min(w h) < 30.0
        librfWarn(sprintf(nil "IND 标记最小边 %.1f < 30um, 请核对 IND.F 规则" min(w h))))
      when(max(w h) > 500.0
        librfWarn(sprintf(nil "IND 标记最大边 %.1f > 500um, 请核对 IND.F 规则" max(w h))))
      librfRect(cv librfMetalPair(librfLyr("marker")) x1 y1 x2 y2))))
;---------------------------------------------------------------- 库/cellview
; 打开 (或创建) cellview, 清空旧图. 返回 cv; 失败返回 nil
; 并发安全：view 目录存在 *.cdslck* 时拒绝写入（Virtuoso 正打开编辑器）
procedure(librfOpenCell(libName cellName viewName libDir)
  prog((lib cv copyFigs)
    lib = ddGetObj(libName)
    when(lib == nil
      when(libDir == nil
        libDir = strcat(getWorkingDir() "/" libName))
      lib = errset(dbCreateLib(libName libDir) t)
      if(lib && car(lib)
        then lib = car(lib)
        else
          librfErr(sprintf(nil "建库失败: %s @ %s" libName libDir))
          return(nil))
      lib->techLibName = librfTechLibName()
      librfWarn(sprintf(nil "已建库 %s 并附着 tech %s" libName librfTechLibName())))
    when(libDir == nil
      libDir = strcat(getWorkingDir() "/" libName))
    ;---- 锁检测: layout 被 Virtuoso 打开时 figs 会空且写坏数据，整体拒绝
    when(librfViewsIsLocked(libDir cellName viewName)
      librfErr(sprintf(nil
        "view 被 Virtuoso 占用: %s/%s/%s 存在 *.cdslck* -- 请先关闭 Virtuoso 中该 cellview 的编辑器再重试"
        libName cellName viewName))
      return(nil))
    cv = errset(dbOpenCellViewByType(libName cellName viewName "maskLayout" "a") t)
    if(cv && car(cv)
      then cv = car(cv)
      else
        librfErr(sprintf(nil "打开 cellview 失败: %s/%s/%s" libName cellName viewName))
        return(nil))
    ; tech 有效性探测: 按 profile 的 spiral 层名探测，而不是写死具体层名。
    ; 库可能历史绑定了别的/默认 tech —— 此时报错并给出 profile 期望的层名。
    when(techFindLayer(techGetTechFile(cv) librfLyr("spiral")) == nil
      librfErr(sprintf(nil "库 %s 的 tech 里找不到层 %s (工艺库 %s 期望)。\n可能是库绑了默认 tech 或工艺不匹配。请检查 tech_profile.json 的 layers.spiral / techlib，或删除库目录 %s 后重建"
        libName librfLyr("spiral") librfTechLibName() libDir))
      return(nil))
    ; 清空已有图形/网络/pin (cell 重名覆盖). IC618 cv->figs 恒空, 必须用 cv->shapes
    copyFigs = copy(cv->shapes)
    foreach(fig copyFigs dbDeleteObject(fig))
    ; 旧 net/pin 一并清, 否则 dbCreateNet 同名返回 nil
    foreach(fig copy(cv->nets) errset(dbDeleteObject(fig) t))
    return(cv)))
;=============================================================================
; 器件构造器
;=============================================================================
; 点表平移 (x0,y0)
procedure(librfShiftPts(pts dx dy)
  let((out)
    out = nil
    foreach(p pts out = cons(list(car(p) + dx cadr(p) + dy) out))
    reverse(out)))
; 点表绕原点旋转 180°
procedure(librfRot180Pts(pts)
  let((out)
    out = nil
    foreach(p pts out = cons(list(0.0 - car(p) 0.0 - cadr(p)) out))
    reverse(out)))
; 非正方形拉长: 水平段 +=ex, 垂直段 +=ey, 45°斜段 dx=dy 不变; 全程保持 0/45/90°,
; 拉长后 bbox ≈ (dout+ex)×(dout+ey), 平行段间距=pitch; 累加用拉长后坐标, 段增量从原始相邻点求
procedure(librfStretchPts(pts ex ey)
  prog((out oprev ocur nprev dx dy nx ny)
    when(ex == nil ex = 0.0)
    when(ey == nil ey = 0.0)
    when(abs(ex) < 1.0e-9 && abs(ey) < 1.0e-9
      return(pts))
    oprev = car(pts)
    nprev = oprev
    out = list(nprev)
    foreach(ocur cdr(pts)
      dx = car(ocur) - car(oprev)
      dy = cadr(ocur) - cadr(oprev)
      when(abs(dy) < 1.0e-9 && abs(dx) > 1.0e-9
        dx = dx + if(dx > 0.0 then ex else (0.0 - ex)))
      when(abs(dx) < 1.0e-9 && abs(dy) > 1.0e-9
        dy = dy + if(dy > 0.0 then ey else (0.0 - ey)))
      ; 45°: dx,dy 已等值, 不改
      nx = car(nprev) + dx
      ny = cadr(nprev) + dy
      out = cons(list(nx ny) out)
      oprev = ocur
      nprev = list(nx ny))
    return(reverse(out))))
;-----------------------------------------------------------------------------
; 电感 ind: 方形/八角螺旋 + underpass + stub 端口 + IND 标记
; ct=0: p01=外端, p02=内端(underpass); ct=1: p01/p02=两半外端, ct1=中心
;-----------------------------------------------------------------------------
procedure(librfBuildInd(cv req)
  let((shape n w s dout din ct under x0 y0 stubLen wUp pitch dinCalc ex ey indMark
        pts ptsA ptsB xIn yIn xOut yOut exitDir p0 p1 a0 halfA bodyBox exitXY gap)
    shape = librfReqStr(req 'shape "sq")
    n     = librfReqNum(req 'n 2.5)
    w     = librfReqNum(req 'w 10.0)
    s     = librfReqNum(req 's 2.5)
    dout  = librfReqNum(req 'dout 150.0)
    ct    = librfReqNum(req 'ct 0)
    under = librfReqStr(req 'underpass "direct")
    x0    = librfReqNum(req 'x0 0.0)
    y0    = librfReqNum(req 'y0 0.0)
    stubLen = librfReqNum(req 'stub max(2.0 * w 5.0))
    ex = librfReqNum(req 'ex 0.0)
    ey = librfReqNum(req 'ey 0.0)
    indMark = librfReqNum(req 'ind_mark 0)
    wUp = max(w 2.5)
    ; din 闭合校验 (res3 式 A); 拉长时闭合式只作参考 (孔径同步 +ex/+ey)
    dinCalc = librfDinFromParams(n w s dout)
    din = librfReqNum(req 'din dinCalc)
    if(abs(ex) > 1.0e-9 || abs(ey) > 1.0e-9
      then
        when(abs(din - dinCalc) > 0.05
          librfWarn(sprintf(nil "拉长 ex=%.3f ey=%.3f 时 din 闭合式仅参考 (基准 %.3f), 不强制校验"
            ex ey dinCalc)))
      else
        when(abs(din - dinCalc) > 0.05
          librfWarn(sprintf(nil "din=%.3f 与闭合式 %.3f 不符, 以 n,w,s,dout 为准"
            din dinCalc))
          din = dinCalc))
    when(dinCalc < 2.0 * w
      librfWarn(sprintf(nil "din=%.3f < 2*w=%.3f, 内孔偏小" dinCalc (2.0 * w))))
    librfDrcParams(w s t (w > 15.0))
    a0 = (dout - w) / 2.0
    if(ct == 0 || ct == nil
      then
        ;---- 单螺旋 + underpass
        pts = librfSpiralPts(shape n w s dout)
        pts = librfStretchPts(pts ex ey)
        pts = librfShiftPts(pts x0 y0)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) pts w)
        bodyBox = librfCvBBox(cv)
        p0 = car(pts)  p1 = cadr(pts)
        ; 外端 stub (从起点反向伸出, 可见外伸 stubLen)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        ; 内端 underpass —— 外端落点相对拉长后螺旋轮廓 (禁止 dout/2+ex/2 半移)
        p0 = car(librfLastPt(pts))  p1 = cadr(librfLastPt(pts))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(pts w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gap = librfAssertPadGap(pts w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv p0 p1 xOut yOut exitDir wUp w under "p02" (stubLen + w / 2.0))
      else
        ;---- 中心抽头对称双半螺旋 (180° 旋转对称, 原点汇合)
        halfA = car(librfHalfSpiralPts(n w s dout))
        halfA = librfStretchPts(halfA ex ey)
        ; 无条件收到原点 (末点恒为 (±aK,-aK), 到原点连线天然 45°; 整数匝也成立)
        halfA = append(halfA list(list(0.0 0.0)))
        ; 半 B = 半 A 旋转 180°, 内端同样收到原点
        ptsB = librfRot180Pts(librfStretchPts(car(librfHalfSpiralPts(n w s dout)) ex ey))
        ptsB = append(ptsB list(list(0.0 0.0)))
        ptsA = librfShiftPts(halfA x0 y0)
        ptsB = librfShiftPts(ptsB x0 y0)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsA w)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsB w)
        bodyBox = librfCvBBox(cv)
        ; p01 = 半 A 外端
        p0 = car(ptsA)  p1 = cadr(ptsA)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        ; p02 = 半 B 外端
        p0 = car(ptsB)  p1 = cadr(ptsB)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p02")
        ; ct1 = 中心, 经 underpass 径向引出 (默认向南)
        ; 外端落点相对两半合并轮廓 (ptsA∪ptsB), 不用中心半移
        exitDir = librfReqStr(req 'ct_dir "S")
        exitDir = librfDirSym(exitDir)
        exitXY = librfExitFromPts(append(ptsA ptsB) w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gap = librfAssertPadGap(append(ptsA ptsB) w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv x0 y0 xOut yOut exitDir wUp w under "ct1" (stubLen + w / 2.0)))
    ; IND 标记默认不画 (用户明确不要识别层); ind_mark:1 才画
    when(indMark != 0 && indMark != nil
      librfIndMark(cv bodyBox 1.0))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass"))))
;-----------------------------------------------------------------------------
; 传输线 tl: 微带 (spiral 层信号条 + 可选地平面 + via_un 栅栏) 或 CPW (同层地)
;-----------------------------------------------------------------------------
procedure(librfBuildTl(cv req)
  let((w len gnd gndLayer fence cpw gap x0 y0 stubLen m gw fy i)
    w   = librfReqNum(req 'w 8.0)
    len = librfReqNum(req 'len 100.0)
    gnd = librfReqNum(req 'gnd 0)
    gndLayer = librfReqStr(req 'gnd_layer "M3")
    fence = librfReqNum(req 'fence 0)
    cpw = librfReqNum(req 'cpw 0)
    gap = librfReqNum(req 'gap 2.5)
    x0  = librfReqNum(req 'x0 0.0)
    y0  = librfReqNum(req 'y0 0.0)
    stubLen = librfReqNum(req 'stub max(2.0 * w 5.0))
    if(cpw == 0 || cpw == nil
      then librfDrcParams(w 2.5 nil (w > 15.0))
      else librfDrcParams(w gap nil (w > 15.0)))
    if(cpw == 0 || cpw == nil
      then
        ;---- 微带 (path 一次画出)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
          list(list(x0 y0 + w / 2.0) list(x0 + len y0 + w / 2.0)) w)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 y0 + w/2.0 'W w (stubLen + w / 2.0) "p01")
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 + len y0 + w/2.0 'E w (stubLen + w / 2.0) "p02")
        ; SKILL 里 0 为真值！标志位必须显式判非零（v01 的 when(gnd 会让
        ; gnd=0 也画地平面）
        when(gnd != 0 && gnd != nil
          m = 3.0 * w
          librfRect(cv librfMetalPair(gndLayer)
            x0 - m y0 - m x0 + len + m y0 + w + m)
          ; 地平面 "gnd" pin+label (EMX 公共参考; 打在平面左缘内侧 1.5um 满足 label-snap)
          librfPinLabel(cv librfMetalPair(gndLayer) librfTxtPair(gndLayer)
            x0 - m + 1.5 y0 + w / 2.0 - 1.0
            x0 - m + 3.5 y0 + w / 2.0 + 1.0 "gnd")
          when(fence != 0 && fence != nil
            if(gndLayer == librfLyr("route")
              then
                ; 两侧 via_un 栅栏: underpass 长条 + via_un 阵列缝合到 route 地
                for(i 0 1
                  fy = if(i == 0 then y0 - 2.5 else y0 + w + 2.5)
                  librfRect(cv librfMetalPair(librfLyr("underpass"))
                    x0 fy - 1.25 x0 + len fy + 1.25)
                  librfViaArray(cv librfViaPair(librfLyr("via_un"))
                    x0 fy - 1.25 x0 + len fy + 1.25 1.5 1.5 0.5 0.5))
              else
                librfWarn("fence=1 仅在 gnd_layer=route 层时画 via 栅栏, 已跳过"))))
      else
        ;---- CPW: 同层地, gap≥2.5
        gw = max(3.0 * w 5.0)
        when(gap < 2.5
          librfErr(sprintf(nil "CPW gap=%.3f < 2.5" gap)))
        librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
          list(list(x0 y0 + w / 2.0) list(x0 + len y0 + w / 2.0)) w)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
          list(list(x0 y0 - gap - gw / 2.0) list(x0 + len y0 - gap - gw / 2.0)) gw)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
          list(list(x0 y0 + w + gap + gw / 2.0)
               list(x0 + len y0 + w + gap + gw / 2.0)) gw)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 y0 + w/2.0 'W w (stubLen + w / 2.0) "p01")
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 + len y0 + w/2.0 'E w (stubLen + w / 2.0) "p02")
        ; 地标签 (EMX 同名短接为公共参考)
        librfPinLabel(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 + 1.5 y0 - gap - gw/2.0 - 1.0
          x0 + 3.5 y0 - gap - gw/2.0 + 1.0 "gnd")
        librfPinLabel(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 + 1.5 y0 + w + gap + gw/2.0 - 1.0
          x0 + 3.5 y0 + w + gap + gw/2.0 + 1.0 "gnd"))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass"))))
;-----------------------------------------------------------------------------
; 变压器 xfmr: interleaved(同层双半螺旋交错) / stacked(spiral+underpass 叠层) / balun(CT)
; 端口: p01/p02=初级, p03/p04=次级; balun 另有 ct1(次级中心抽头)
;-----------------------------------------------------------------------------
procedure(librfBuildXfmr(cv req)
  let((style np ns w s dout gap ct under x0 y0 stubLen wUp ex ey indMark
        ptsP ptsS p0 p1 xIn yIn xOut yOut exitDir halfP halfS bodyBox exitXY gapPad)
    style = librfReqStr(req 'style "interleaved")
    np = librfReqNum(req 'np 2.0)
    ns = librfReqNum(req 'ns 2.0)
    w  = librfReqNum(req 'w 5.0)
    s  = librfReqNum(req 's 2.5)
    dout = librfReqNum(req 'dout 120.0)
    gap = librfReqNum(req 'gap s)
    ct = librfReqNum(req 'ct 0)
    under = librfReqStr(req 'underpass "direct")
    x0 = librfReqNum(req 'x0 0.0)
    y0 = librfReqNum(req 'y0 0.0)
    stubLen = librfReqNum(req 'stub max(2.0 * w 5.0))
    ex = librfReqNum(req 'ex 0.0)
    ey = librfReqNum(req 'ey 0.0)
    indMark = librfReqNum(req 'ind_mark 0)
    ; stacked/balun 有 underpass 绕组, 同层跨接会短路 -> 强制 route 两级栈
    when(style == "stacked" || style == "balun" || style == "st" || style == "bal"
      under = "route")
    wUp = max(w 2.5)
    s = max(s gap)
    librfDrcParams(w s t (w > 15.0))
    cond(
      ;---- 交指: 同层双半螺旋, 不在中心汇合, 各自 underpass
      ((style == "interleaved" || style == "il")
        halfP = car(librfHalfSpiralPts(np w s dout))
        halfS = car(librfHalfSpiralPts(ns w s dout))
        ptsP = librfShiftPts(librfStretchPts(halfP ex ey) x0 y0)
        ptsS = librfShiftPts(librfRot180Pts(librfStretchPts(halfS ex ey)) x0 y0)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsP w)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsS w)
        bodyBox = librfCvBBox(cv)
        ; p01=初级外端
        p0 = car(ptsP) p1 = cadr(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        ; p03=次级外端
        p0 = car(ptsS) p1 = cadr(ptsS)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p03")
        ; p02=初级内端 underpass (向西/南)
        p0 = car(librfLastPt(ptsP)) p1 = cadr(librfLastPt(ptsP))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(ptsP w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(ptsP w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv p0 p1 xOut yOut exitDir wUp w under "p02" (stubLen + w / 2.0))
        ; p04=次级内端 underpass
        p0 = car(librfLastPt(ptsS)) p1 = cadr(librfLastPt(ptsS))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(ptsS w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(ptsS w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv p0 p1 xOut yOut exitDir wUp w under "p04" (stubLen + w / 2.0)))
      ;---- 叠层: 初级 spiral 整螺旋, 次级 underpass 整螺旋(180° 旋转让内端错开)
      ((style == "stacked" || style == "st")
        ptsP = librfShiftPts(librfStretchPts(librfSpiralPts("sq" np w s dout) ex ey) x0 y0)
        ptsS = librfShiftPts(librfRot180Pts(librfStretchPts(librfSpiralPts("sq" ns w s dout) ex ey)) x0 y0)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsP w)
        librfPathSweep(cv librfMetalPair(librfLyr("underpass"))  ptsS w)
        bodyBox = librfCvBBox(cv)
        ; p01/p02 初级 (spiral 层)
        p0 = car(ptsP) p1 = cadr(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        p0 = car(librfLastPt(ptsP)) p1 = cadr(librfLastPt(ptsP))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(ptsP w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(ptsP w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv p0 p1 xOut yOut exitDir wUp w under "p02" (stubLen + w / 2.0))
        ; p03/p04 次级 (underpass 螺旋): 外端直接 underpass stub, 内端下到 route
        p0 = car(ptsS) p1 = cadr(ptsS)
        librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p03")
        p0 = car(librfLastPt(ptsS)) p1 = cadr(librfLastPt(ptsS))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(ptsS w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(ptsS w xOut yOut (wUp / 2.0) exitDir 2.0)
        ; 次级内端: underpass 走线 + via_un 到 route + 回 underpass 做 stub (端口在 underpass 文字层)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          p0 - wUp/2.0 p1 - wUp/2.0 p0 + wUp/2.0 p1 + wUp/2.0)
        librfViaArray(cv librfViaPair(librfLyr("via_un"))
          p0 - wUp/2.0 p1 - wUp/2.0 p0 + wUp/2.0 p1 + wUp/2.0 1.5 1.5 0.5 0.5)
        librfPathSweep(cv librfMetalPair(librfLyr("route"))
          list(list(p0 p1) list(xOut p1) list(xOut yOut)) wUp)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xOut - wUp/2.0 yOut - wUp/2.0 xOut + wUp/2.0 yOut + wUp/2.0)
        librfViaArray(cv librfViaPair(librfLyr("via_un"))
          xOut - wUp/2.0 yOut - wUp/2.0 xOut + wUp/2.0 yOut + wUp/2.0 1.5 1.5 0.5 0.5)
        librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
          xOut yOut exitDir w (stubLen + w / 2.0) "p04")
        librfWarn("stacked: 次级走 underpass, 端口 p03/p04 在 underpass 文字层; 建议 EMX 端口核对"))
      ;---- balun: 次级=CT 对称双半(spiral 层), 初级=underpass 整螺旋
      ((style == "balun")
        halfP = car(librfHalfSpiralPts(ns w s dout))
        halfP = librfStretchPts(halfP ex ey)
        halfP = append(halfP list(list(0.0 0.0)))
        halfS = car(librfHalfSpiralPts(ns w s dout))
        halfS = librfStretchPts(halfS ex ey)
        halfS = append(librfRot180Pts(halfS) list(list(0.0 0.0)))
        ptsP = librfShiftPts(halfP x0 y0)     ; 次级半 A -> p03
        ptsS = librfShiftPts(halfS x0 y0)     ; 次级半 B -> p04
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsP w)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsS w)
        bodyBox = librfCvBBox(cv)
        p0 = car(ptsP) p1 = cadr(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p03")
        p0 = car(ptsS) p1 = cadr(ptsS)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p04")
        ; ct1 从中心引出 (外端相对次级双半合并轮廓)
        exitDir = librfReqStr(req 'ct_dir "S")
        exitDir = librfDirSym(exitDir)
        exitXY = librfExitFromPts(append(ptsP ptsS) w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(append(ptsP ptsS) w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv x0 y0 xOut yOut exitDir wUp w under "ct1" (stubLen + w / 2.0))
        ; 初级 underpass 整螺旋
        ptsP = librfShiftPts(librfStretchPts(librfSpiralPts("sq" np w s dout) ex ey) x0 y0)
        librfPathSweep(cv librfMetalPair(librfLyr("underpass")) ptsP w)
        bodyBox = librfCvBBox(cv)
        p0 = car(ptsP) p1 = cadr(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        p0 = car(librfLastPt(ptsP)) p1 = cadr(librfLastPt(ptsP))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(ptsP w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(ptsP w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          p0 - wUp/2.0 p1 - wUp/2.0 p0 + wUp/2.0 p1 + wUp/2.0)
        librfViaArray(cv librfViaPair(librfLyr("via_un"))
          p0 - wUp/2.0 p1 - wUp/2.0 p0 + wUp/2.0 p1 + wUp/2.0 1.5 1.5 0.5 0.5)
        librfPathSweep(cv librfMetalPair(librfLyr("route"))
          list(list(p0 p1) list(xOut p1) list(xOut yOut)) wUp)
        librfStubPort(cv librfMetalPair(librfLyr("route")) librfTxtPair(librfLyr("route"))
          xOut yOut exitDir w (stubLen + w / 2.0) "p02")
        librfWarn("balun: 次级 p03/p04/ct1 在 spiral 层, 初级 p01/p02 在 underpass/route 层"))
      (t
        librfErr(sprintf(nil "未知 xfmr style: %s" style))))
    when(indMark != 0 && indMark != nil
      librfIndMark(cv bodyBox 1.0))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass"))))
;-----------------------------------------------------------------------------
; 耦合线 cpl: 平行耦合微带 4 端口 (p01=线1左 p02=线1右 p03=线2右 p04=线2左)
;-----------------------------------------------------------------------------
procedure(librfBuildCpl(cv req)
  let((w gap len gnd x0 y0 stubLen m pitch)
    w   = librfReqNum(req 'w 5.0)
    gap = librfReqNum(req 'gap 2.5)
    len = librfReqNum(req 'len 800.0)
    gnd = librfReqNum(req 'gnd 0)
    x0  = librfReqNum(req 'x0 0.0)
    y0  = librfReqNum(req 'y0 0.0)
    stubLen = librfReqNum(req 'stub max(2.0 * w 5.0))
    librfDrcParams(w gap t (w > 15.0))
    pitch = w + gap
    ; 线 1
    librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
      list(list(x0 y0 + w / 2.0) list(x0 + len y0 + w / 2.0)) w)
    ; 线 2
    librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
      list(list(x0 y0 + pitch + w / 2.0) list(x0 + len y0 + pitch + w / 2.0)) w)
    librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
      x0 y0 + w/2.0 'W w (stubLen + w / 2.0) "p01")
    librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
      x0 + len y0 + w/2.0 'E w (stubLen + w / 2.0) "p02")
    librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
      x0 + len y0 + pitch + w/2.0 'E w (stubLen + w / 2.0) "p03")
    librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
      x0 y0 + pitch + w/2.0 'W w (stubLen + w / 2.0) "p04")
    when(gnd != 0 && gnd != nil
      m = 3.0 * w
      librfRect(cv librfMetalPair(librfLyr("ground"))
        x0 - m y0 - m x0 + len + m y0 + pitch + w + m)
      ; 地平面 "gnd" pin+label (EMX 公共参考; 平面左缘内侧 1.5um 满足 label-snap)
      librfPinLabel(cv librfMetalPair(librfLyr("ground")) librfTxtPair(librfLyr("ground"))
        x0 - m + 1.5 y0 + (pitch + w) / 2.0 - 1.0
        x0 - m + 3.5 y0 + (pitch + w) / 2.0 + 1.0 "gnd"))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass"))))
;=============================================================================
; 入口与自测
;=============================================================================
;-----------------------------------------------------------------------------
; librfRun(req) — 总入口. req=list(nil 'type "ind" ... 'result "/abs/res.txt")
; 画完写 result 行协议并 exit(); 任何错误都写 STATUS fail+ERR
;-----------------------------------------------------------------------------
procedure(librfRun(req)
  let((typ libName cellName viewName libDir resultPath cv bbox cellTag runOk)
    librfReset()
    resultPath = librfReqStr(req 'result nil)
    librf__resultPath = resultPath
    typ      = librfReqStr(req 'type nil)
    libName  = librfReqStr(req 'lib "LIBRF")
    cellName = librfReqStr(req 'cell nil)
    viewName = librfReqStr(req 'view "layout")
    libDir   = librfReqStr(req 'libdir nil)
    ;---- 参数校验: 缺 type/cell/result 立即写 result fail 并退出, 不建库不画图
    when(typ == nil     librfErr("缺少参数 type (ind/tl/xfmr/cpl)"))
    when(cellName == nil librfErr("缺少参数 cell"))
    when(resultPath == nil librfErr("缺少参数 result (结果文件路径)"))
    when(typ == nil || cellName == nil || resultPath == nil
      librfWriteResult(nil nil nil)
      exit())
    runOk = errset(progn(
      cv = librfOpenCell(libName cellName viewName libDir)
      when(cv
        cond(
          (typ == "ind"              librfBuildInd(cv req))
          (typ == "tl"               librfBuildTl(cv req))
          (typ == "xfmr"             librfBuildXfmr(cv req))
          (typ == "cpl"              librfBuildCpl(cv req))
          (t  librfErr(sprintf(nil "未知 type: %s" typ))))
        bbox = librfCvBBox(cv)
        dbSave(cv)
        dbClose(cv)) ) t)
    when(runOk == nil
      librfErr("SKILL 运行异常 (见 stdout 报错)"))
    cellTag = sprintf(nil "%s/%s/%s" libName cellName viewName)
    librfWriteResult(librf__ok cellTag
      bbox)
    exit()))
;-----------------------------------------------------------------------------
; librfSelfTest() — 纯几何自测 (不建库不画图): 从 pts 实算断言, 输出 PASS/FAIL
; 黄金断言 (SPEC §9.2): bbox=dout×dout; 相邻平行段恒距 pitch;
;   n=1 孔径=dout-2w; n=2 孔径=dout-2(2pitch-s)
; 失败置 gLibrfSelfFail=t 并打印 FAIL 计数
;-----------------------------------------------------------------------------
procedure(librfSelfTest()
  let((pts segs i p q pitch L0 nSeg bb ap diffs ok expect nPass nFail)
    gLibrfSelfFail = nil
    nPass = 0  nFail = 0
    printf("=== LIBRF layout selftest ===\n")
    ;---- 1) 黄金用例 n=2.5 w=10 s=2.5 dout=100 (golden_ind.json)
    pitch = 12.5   L0 = 90.0
    pts = librfSquareSpiralPts(2.5 10.0 2.5 100.0)
    nSeg = length(pts) - 1
    if(nSeg == 10
      then progn(nPass = nPass + 1  printf("PASS square n=2.5: nSeg=10\n"))
      else progn(nFail = nFail + 1
             printf("FAIL square n=2.5: nSeg=%d (expect 10)\n" nSeg)))
    ; 段长表 (从 pts 实算) vs 嵌套正方形裁决 §9.2
    segs = librfSegLens(pts)
    expect = list(90.0 90.0 90.0 77.5 77.5 65.0 65.0 52.5 52.5 40.0)
    ok = (length(segs) == length(expect))
    when(ok
      i = 0
      foreach(q expect
        when(abs(nth(i segs) - q) > 0.002  ok = nil)
        i = i + 1))
    if(ok
      then progn(nPass = nPass + 1  printf("PASS segs=90 90 90 77.5 77.5 65 65 52.5 52.5 40\n"))
      else progn(nFail = nFail + 1
             printf("FAIL segs:")
             foreach(v segs printf(" %.3f" v))
             printf(" (expect 90 90 90 77.5 77.5 65 65 52.5 52.5 40)\n")))
    ; bbox = dout×dout (中心线跨度 + w)
    bb = librfPtsBBox(pts)
    ok = (abs((caddr(bb) - car(bb)) + 10.0 - 100.0) < 0.002) &&
         (abs((cadddr(bb) - cadr(bb)) + 10.0 - 100.0) < 0.002)
    if(ok
      then progn(nPass = nPass + 1
             printf("PASS bbox=100x100 (centerline %.3fx%.3f + w)\n"
               caddr(bb) - car(bb)  cadddr(bb) - cadr(bb)))
      else progn(nFail = nFail + 1
             printf("FAIL bbox: centerline %.3fx%.3f + w != 100x100\n"
               caddr(bb) - car(bb)  cadddr(bb) - cadr(bb))))
    ; 相邻平行段间距恒等于 pitch (从 pts 实算)
    diffs = librfPitchDiffs(pts)
    ok = t
    foreach(p diffs
      when(abs(p - pitch) > 0.002  ok = nil))
    ok = ok && (length(diffs) >= 4)
    if(ok
      then progn(nPass = nPass + 1
             printf("PASS pitch: %d 相邻平行段间距均 = %.3f\n" length(diffs) pitch))
      else progn(nFail = nFail + 1
             printf("FAIL pitch: 间距表:")
             foreach(v diffs printf(" %.3f" v))
             printf(" (expect 全部 %.3f)\n" pitch)))
    ; 首末点
    ok = (abs(caar(pts) - 45.0) < 0.002) && (abs(cadar(pts) + 45.0) < 0.002) &&
         (abs(car(librfLastPt(pts)) + 20.0) < 0.002) &&
         (abs(cadr(librfLastPt(pts)) - 20.0) < 0.002)
    if(ok
      then progn(nPass = nPass + 1
             printf("PASS first=(45,-45) last=(-20,20)\n"))
      else progn(nFail = nFail + 1
             printf("FAIL first=(%.3f,%.3f) last=(%.3f,%.3f) (expect (45,-45) (-20,20))\n"
               caar(pts) cadar(pts) car(librfLastPt(pts)) cadr(librfLastPt(pts)))))
    ;---- 2) n=1 孔径 = dout-2w = 80
    ap = librfAperture(librfSquareSpiralPts(1.0 10.0 2.5 100.0) 10.0)
    if(abs(ap - (100.0 - 2.0 * 10.0)) < 0.002
      then progn(nPass = nPass + 1
             printf("PASS n=1 孔径=%.3f (=dout-2w=80)\n" ap))
      else progn(nFail = nFail + 1
             printf("FAIL n=1 孔径=%.3f (expect 80.000 =dout-2w)\n" ap)))
    ;---- 3) n=2 孔径 = dout-2(2pitch-s) = 55
    ap = librfAperture(librfSquareSpiralPts(2.0 10.0 2.5 100.0) 10.0)
    if(abs(ap - (100.0 - 2.0 * (2.0 * 12.5 - 2.5))) < 0.002
      then progn(nPass = nPass + 1
             printf("PASS n=2 孔径=%.3f (=dout-2(2pitch-s)=55)\n" ap))
      else progn(nFail = nFail + 1
             printf("FAIL n=2 孔径=%.3f (expect 55.000 =dout-2(2pitch-s))\n" ap)))
    ;---- 4) din 闭合式 (从参数实算)
    ok = abs(librfDinFromParams(2.5 10.0 2.5 100.0) - 42.5) < 0.002
    if(ok
      then progn(nPass = nPass + 1  printf("PASS din(2.5)=42.500\n"))
      else progn(nFail = nFail + 1
             printf("FAIL din(2.5)=%.3f (expect 42.500)\n"
               librfDinFromParams(2.5 10.0 2.5 100.0))))
    ;---- 5) 八角螺旋 (信息 + 起点断言)
    pts = librfOctSpiralPts(2.5 10.0 2.5 150.0)
    printf("oct n=2.5: nSeg=%d (expect 20) first=(%.3f,%.3f) (expect (28.995,-70.000))\n"
      length(pts) - 1 caar(pts) cadar(pts))
    ;---- 5c) 退出落点必须相对拉长后轮廓 (防 ex 半移短路)
    let((ptsX exy gapv)
      ptsX = librfStretchPts(librfOctSpiralPts(2.5 10.0 2.5 150.0) 40.0 0.0)
      exy = librfExitFromPts(ptsX 10.0 10.0 0.0 0.0 'W)
      gapv = librfAssertPadGap(ptsX 10.0 car(exy) cadr(exy) 5.0 'W 2.0)
      if(abs(gapv - 8.0) < 0.05
        then progn(nPass = nPass + 1
               printf("PASS exitFromPts ex=40 W: gap=%.3f (expect 8.0)\n" gapv))
        else progn(nFail = nFail + 1
               printf("FAIL exitFromPts ex=40 W: gap=%.3f (expect 8.0)\n" gapv)))
      ptsX = librfOctSpiralPts(2.5 10.0 2.5 150.0)
      exy = librfExitFromPts(ptsX 10.0 10.0 0.0 0.0 'W)
      if(abs(car(exy) - (0.0 - 75.0 - 10.0 - 3.0)) < 0.05
        then progn(nPass = nPass + 1
               printf("PASS exitFromPts ex=0 W: xOut=%.3f (expect -88.0)\n" car(exy)))
        else progn(nFail = nFail + 1
               printf("FAIL exitFromPts ex=0 W: xOut=%.3f (expect -88.0)\n" car(exy)))))
    ;---- 5b) 拉长: ex=40 后 bbox 宽 +40, 高不变; 段仍 0/45/90
    let((st bb0 bb dw dh dx dy okAng pp qq)
      st = librfStretchPts(librfOctSpiralPts(2.5 10.0 2.5 150.0) 40.0 0.0)
      bb0 = librfPtsBBox(librfOctSpiralPts(2.5 10.0 2.5 150.0))
      bb = librfPtsBBox(st)
      dw = (caddr(bb) - car(bb)) - (caddr(bb0) - car(bb0))
      dh = (cadddr(bb) - cadr(bb)) - (cadddr(bb0) - cadr(bb0))
      if(abs(dw - 40.0) < 0.05 && abs(dh) < 0.05
        then progn(nPass = nPass + 1
               printf("PASS stretch ex=40: centerline +w=%.3f +h=%.3f (expect 40,0)\n" dw dh))
        else progn(nFail = nFail + 1
               printf("FAIL stretch ex=40: +w=%.3f +h=%.3f (expect 40,0)\n" dw dh)))
      okAng = t
      pp = car(st)
      foreach(qq cdr(st)
        dx = car(qq) - car(pp)
        dy = cadr(qq) - cadr(pp)
        when(abs(dx) > 0.001 && abs(dy) > 0.001
          when(abs(abs(dx) - abs(dy)) > 0.05  okAng = nil))
        pp = qq)
      if(okAng
        then progn(nPass = nPass + 1  printf("PASS stretch keeps 0/45/90\n"))
        else progn(nFail = nFail + 1  printf("FAIL stretch broke 45° (dx!=dy)\n"))))
    ;---- 6) 半螺旋 (CT/交指)
    pts = car(librfHalfSpiralPts(2.5 10.0 2.5 150.0))
    printf("half n=2.5: nPts=%d (expect 10) first=(%.3f,%.3f) (expect 70,-70)\n"
      length(pts) caar(pts) cadar(pts))
    printf("  last=(%.3f,%.3f) (expect -20,-20)  A.y<=0 B.y>=0 无碰撞\n"
      car(librfLastPt(pts)) cadr(librfLastPt(pts)))
    ;---- 7) via 阵列行列数 (走线宽 2.5 → 1 列; 5.5 → 2 列)
    printf("via cols: w=2.5 -> %d (expect 1); w=5.5 -> %d (expect 2)\n"
      max(1 fix(((2.5 - 1.0) + 1.5) / 3.0))
      max(1 fix(((5.5 - 1.0) + 1.5) / 3.0)))
    gLibrfSelfFail = (nFail > 0)
    printf("=== selftest done: %d PASS, %d FAIL ===\n" nPass nFail)))
;=============================================================================
; librfMakeViews — schematic + symbol 生成
;=============================================================================
; 【锁定的 API 配方 (2026-10-16 VM scratch 验证)，参数顺序勿改】
; 1) dbCreateInst(cv master name point orient)：master 须为 cellview 对象
;    (dbOpenCellViewByType(lib cell view "schematicSymbol" "r"))；name 字符串,
;    point list(x y), orient "R0"；传 lib 字符串报 "argument #2 should be a database object"
; 2) dbCreateProp(obj name type value) 4 参；type "string"/"int"/"boolean"；
;    例 dbCreateProp(nport "p" "int" 2) / dbCreateProp(nport "nmode" "boolean" "TRUE")
; 3) dbCreateInstTerm(net inst term)：net 在前；term 取自 inst->master
;    (dbFindTermByName(inst->master "p1"))；iopin 的 term 名 "iopin", gnd 为 "gnd!"
; 4) dbAddFigToNet(fig net)：fig 在前
; 5) dbCreatePin(net figOrNil name)：fig=nil 只建 pin 不抢 net 的 fig；fig 非 nil 会从
;    net->figs 移到 pin->fig；symbol 的 pin 存于 cv->nets (net + ("pin" "drawing") 图形)
; 6) dbCreateLabel(cv layer pt text just orient font height) 8 参；文本属性 ->theLabel
; 7) symbol 合法层 ("border"/"annotate"/"pin" "drawing")；schematic wire "wire" "drawing"
;    (purpose 读出 nil 与金标准一致)；symbol 上 ("drawing" "drawing") 非法
; 8) nport 终端 p1..pN + m1..mN (m* 接 GND net, 等价 GUI 的 common)；p 设 int 后
;    inst->master 自动参数化；属性顺序无影响
; 9) dbCreateNet(cv name)：net 已存在返回 nil
; 10) 覆盖重跑：删除 cv->figs / cv->instances / cv->nets 后重建；dbDeleteObject 可删三者
; 11) errset 单表达式；禁用变量名 t；裸圆括号=函数调用；makeBox/makePoint 不可用
;=============================================================================

;---------------------------------------------------------------- 结果收集器 (views)
defvar(librf__vresultPath nil)
defvar(librf__vwarns nil)
defvar(librf__verrs nil)
defvar(librf__vcells nil)
defvar(librf__vpins nil)
defvar(librf__vok t)

procedure(librfViewsReset()
  librf__vwarns = nil
  librf__verrs = nil
  librf__vcells = nil
  librf__vpins = nil
  librf__vok = t)

procedure(librfViewsWarn(msg)
  librf__vwarns = cons(msg librf__vwarns))

procedure(librfViewsErr(msg)
  librf__verrs = cons(msg librf__verrs)
  librf__vok = nil)

procedure(librfViewsNoteCell(tag)
  librf__vcells = cons(tag librf__vcells))

procedure(librfViewsNotePin(name dirStr)
  librf__vpins = cons(list(name dirStr) librf__vpins))

; 写 views 结果行协议 (STATUS/CELL/PIN/WARN/ERR)
procedure(librfWriteViewsResult(okFlag)
  let((port)
    when(librf__vresultPath
      port = errset(outfile(librf__vresultPath) t)
      when(port && car(port)
        port = car(port)
        if(okFlag
          then fprintf(port "STATUS ok\n")
          else fprintf(port "STATUS fail\n"))
        foreach(tag reverse(librf__vcells)
          fprintf(port "CELL %s\n" tag))
        foreach(entry reverse(librf__vpins)
          fprintf(port "PIN %s %s\n" car(entry) cadr(entry)))
        foreach(msg reverse(librf__vwarns) fprintf(port "WARN %s\n" msg))
        foreach(msg reverse(librf__verrs)  fprintf(port "ERR %s\n" msg))
        close(port)))
    printf("LIBRF_EXIT=%s\n" if(okFlag then "0" else "1"))))

;---------------------------------------------------------------- 锁检测
; 查 libdir/cell/view/*.cdslck*  返回 t=被锁 / nil=可写
procedure(librfViewsIsLocked(libDir cellName viewName)
  prog((dir shcmd shres)
    when(libDir == nil || cellName == nil || viewName == nil
      return(nil))
    dir = sprintf(nil "%s/%s/%s" libDir cellName viewName)
    ; system() 返回退出码: ls 有匹配=0, 无匹配=非0
    shcmd = sprintf(nil "ls %s/*.cdslck* >/dev/null 2>&1" dir)
    shres = errset(system(shcmd) t)
    if(shres && car(shres) == 0
      then return(t)
      else return(nil))))

;---------------------------------------------------------------- 打开/清空 cellview
procedure(librfViewsOpenCv(libName cellName viewName viewType libDir mode)
  prog((lib cv old)
    lib = ddGetObj(libName)
    when(lib == nil
      when(libDir == nil libDir = strcat(getWorkingDir() "/" libName))
      lib = errset(dbCreateLib(libName libDir) t)
      if(lib && car(lib)
        then lib = car(lib)
        else
          librfViewsErr(sprintf(nil "建库失败: %s @ %s" libName libDir))
          return(nil))
      lib->techLibName = librfTechLibName()
      librfViewsWarn(sprintf(nil "已建库 %s 并附着 tech %s" libName librfTechLibName())))
    cv = errset(dbOpenCellViewByType(libName cellName viewName viewType mode) t)
    if(cv && car(cv)
      then cv = car(cv)
      else
        librfViewsErr(sprintf(nil "打开 cellview 失败: %s/%s/%s (%s)"
          libName cellName viewName viewType))
        return(nil))
    ; 清空旧内容 (覆盖重跑)
    old = copy(cv->figs)
    foreach(fig old errset(dbDeleteObject(fig) t))
    old = copy(cv->instances)
    foreach(inst old errset(dbDeleteObject(inst) t))
    old = copy(cv->nets)
    foreach(n old errset(dbDeleteObject(n) t))
    old = copy(cv->pins)
    foreach(p old errset(dbDeleteObject(p) t))
    return(cv)))

; 取 master cellview 对象 (供 dbCreateInst)
procedure(librfViewsMaster(libName cellName viewName)
  let((m)
    m = errset(dbOpenCellViewByType(libName cellName viewName "schematicSymbol" "r") t)
    if(m && car(m) then car(m) else nil)))

; 找 term 对象: 优先 inst->master
procedure(librfViewsFindTerm(inst master termName)
  prog((src tm)
    src = if(inst && inst->master then inst->master else master)
    when(src == nil return(nil))
    tm = errset(dbFindTermByName(src termName) t)
    if(tm && car(tm) then return(car(tm)) else return(nil))))

; 连接 inst 终端到 net; 成功返回 t
procedure(librfViewsConnect(net inst master termName)
  prog((tm res)
    tm = librfViewsFindTerm(inst master termName)
    when(tm == nil
      librfViewsWarn(sprintf(nil "找不到终端 %s" termName))
      return(nil))
    res = errset(dbCreateInstTerm(net inst tm) t)
    if(res && car(res)
      then return(t)
      else
        progn(
          librfViewsWarn(sprintf(nil "连接终端 %s 失败" termName))
          return(nil)))))

; 取已有 net 或建新 net
procedure(librfViewsGetNet(cv name)
  prog((res)
    res = errset(dbCreateNet(cv name) t)
    if(res && car(res)
      then return(car(res))
      else
        progn(
          res = errset(dbFindNetByName(cv name) t)
          if(res && car(res) then return(car(res)) else return(nil))))))

;---------------------------------------------------------------- schematic 生成
procedure(librfViewsBuildSchematic(libName cellName snpPath portNames libDir)
  prog((cv mIopin mNport mGnd nport gnd net fig npins x0 y0
        i instName tmName wireY res)
    cv = librfViewsOpenCv(libName cellName "schematic" "schematic" libDir "w")
    when(cv == nil return(nil))
    mIopin = librfViewsMaster("basic" "iopin" "symbolr")
    mNport = librfViewsMaster("analogLib" "nport" "symbol")
    mGnd   = librfViewsMaster("analogLib" "gnd" "symbol")
    when(mIopin == nil || mNport == nil || mGnd == nil
      librfViewsErr("打开 master 失败 (basic/iopin 或 analogLib/nport 或 analogLib/gnd)")
      errset(dbClose(cv) t)
      return(nil))
    npins = length(portNames)
    x0 = 8.0
    y0 = npins * 1.5 / 2.0
    ;---- nport 实例 + 属性 (金标准属性)
    res = errset(dbCreateInst(cv mNport "NPORT0" list(x0 y0) "R0") t)
    if(res && car(res)
      then nport = car(res)
      else
        progn(
          librfViewsErr("创建 nport 实例失败")
          errset(dbClose(cv) t)
          return(nil)))
    errset(dbCreateProp(nport "dataFile" "string" snpPath) t)
    errset(dbCreateProp(nport "acModel" "string" "freqdomain") t)
    errset(dbCreateProp(nport "p" "int" npins) t)
    errset(dbCreateProp(nport "nmode" "boolean" "TRUE") t)
    errset(dbCreateProp(nport "thermalnoise" "string" "yes") t)
    ;---- 每端口: net + wire + iopin + pin + 连接 nport/pi
    i = 0
    foreach(pName portNames
      progn(
        wireY = (npins - 1 - i) * 1.5
        net = librfViewsGetNet(cv pName)
        when(net == nil
          librfViewsErr(sprintf(nil "创建 net 失败: %s" pName)))
        when(net
          ; 短 wire (与金标准 net fig 一致)
          let((res)
            res = errset(dbCreateLine(cv list("wire" "drawing")
              list(list(2.0 wireY) list(x0 - 1.0 wireY))) t)
            when(res && car(res) errset(dbAddFigToNet(car(res) net) t)))
          ; iopin 实例
          instName = sprintf(nil "PIN%d" i)
          let((res iopin)
            res = errset(dbCreateInst(cv mIopin instName list(0.5 wireY) "R0") t)
            if(res && car(res)
              then progn(
                iopin = car(res)
                librfViewsConnect(net iopin mIopin "iopin"))
              else librfViewsErr(sprintf(nil "创建 iopin 失败: %s" instName))))
          ; pin 对象 (nil fig 不抢 net figs)
          errset(dbCreatePin(net nil pName) t)
          net->isPin = t
          ; 连接 nport 终端 pi
          tmName = sprintf(nil "p%d" i + 1)
          librfViewsConnect(net nport mNport tmName))
        i = i + 1))
    ;---- GND net + gnd 实例 + nport 回流端
    net = librfViewsGetNet(cv "GND")
    when(net
      let((res)
        res = errset(dbCreateLine(cv list("wire" "drawing")
          list(list(x0 - 1.0 y0 - 1.5) list(x0 - 1.0 y0 - 0.5))) t)
        when(res && car(res) errset(dbAddFigToNet(car(res) net) t)))
      let((res gnd)
        res = errset(dbCreateInst(cv mGnd "GND0" list(x0 - 1.0 y0 - 2.5) "R0") t)
        when(res && car(res)
          librfViewsConnect(net car(res) mGnd "gnd!")))
      ; nport 回流: 优先 common, 否则全部 m*
      if(librfViewsConnect(net nport mNport "common")
        then librfViewsWarn("nport common 终端已接 GND")
        else
          for(i 1 npins
            progn(
              tmName = sprintf(nil "m%d" i)
              librfViewsConnect(net nport mNport tmName)))))
    ;---- 落盘
    errset(dbSave(cv) t)
    errset(dbClose(cv) t)
    errset(dbClose(mIopin) t)
    errset(dbClose(mNport) t)
    errset(dbClose(mGnd) t)
    librfViewsNoteCell(sprintf(nil "%s/%s/schematic" libName cellName))
    t))

;---------------------------------------------------------------- symbol 生成
procedure(librfViewsBuildSymbol(libName cellName portNames kind libDir)
  prog((cv npins boxW boxH margin pinLen i cx cy)
    cv = librfViewsOpenCv(libName cellName "symbol" "schematicSymbol" libDir "w")
    when(cv == nil return(nil))
    npins = length(portNames)
    margin = 2.0
    pinLen = 2.0
    boxH = max(8.0 npins * 2.0 + 4.0)
    boxW = 16.0
    cx = boxW / 2.0
    cy = boxH / 2.0
    ; 边框
    errset(dbCreateRect(cv list("border" "drawing")
      list(list(0.0 0.0) list(boxW boxH))) t)
    ; 中心 cell 名
    errset(dbCreateLabel(cv list("annotate" "drawing")
      list(cx cy) cellName "centerCenter" "R0" "roman" 0.0) t)
    ;---- 端口 pin 排布
    i = 0
    foreach(pName portNames
      progn(
        let((px py dirX)
          px = 0.0  py = 0.0  dirX = -1.0
          cond(
            (npins <= 2
              if(i == 0
                then progn(px = 0.0  py = cy  dirX = -1.0)
                else progn(px = boxW  py = cy  dirX = 1.0)))
            (npins <= 8
              let((half nL nR)
                half = fix((npins + 1) / 2)
                nL = half
                nR = npins - half
                if(i < nL
                  then progn(
                    px = 0.0
                    dirX = -1.0
                    py = if(nL <= 1
                      then cy
                      else boxH - margin - i * (boxH - 2.0 * margin) / (nL - 1.0)))
                  else progn(
                    px = boxW
                    dirX = 1.0
                    py = if(nR <= 1
                      then cy
                      else boxH - margin - (i - nL) * (boxH - 2.0 * margin) / (nR - 1.0))))))
            (t
              ; 均匀分布: 左右两列 + 顶底补充
              let((nSide nTop nBot)
                nSide = fix(npins / 4) + 1
                nTop = fix((npins + 1) / 2) - nSide
                nBot = npins - nSide - nTop
                cond(
                  (i < nSide
                    px = 0.0
                    dirX = -1.0
                    py = if(nSide <= 1
                      then cy
                      else boxH - margin - i * (boxH - 2.0 * margin) / (nSide - 1.0)))
                  ((i < nSide + nTop) && nTop > 0
                    px = if(nTop <= 1
                      then cx
                      else margin + (i - nSide) * (boxW - 2.0 * margin) / (nTop - 1.0))
                    py = boxH
                    dirX = 0.0)
                  ((i < nSide + nTop + nBot) && nBot > 0
                    px = if(nBot <= 1
                      then cx
                      else margin + (i - nSide - nTop) * (boxW - 2.0 * margin) / (nBot - 1.0))
                    py = 0.0
                    dirX = 0.0)
                  (t
                    px = boxW
                    dirX = 1.0
                    py = if(nSide <= 1
                      then cy
                      else boxH - margin - (i - nSide - nTop - nBot) * (boxH - 2.0 * margin) / (nSide - 1.0)))))))
          ; pin 引线
          let((x1 y1 x2 y2 net res)
            x1 = px + dirX * pinLen
            y1 = py
            x2 = px
            y2 = py
            when(dirX == 0.0
              x1 = px  x2 = px
              y1 = py + if(py > cy then -pinLen else pinLen)
              y2 = py)
            res = errset(dbCreateLine(cv list("pin" "drawing")
              list(list(x1 y1) list(x2 y2))) t)
            net = librfViewsGetNet(cv pName)
            when(net && res && car(res)
              errset(dbAddFigToNet(car(res) net) t)
              ; pin 名标注 (挂 net)
              let((lbl)
                lbl = errset(dbCreateLabel(cv list("pin" "drawing")
                  list(x1 + dirX * 0.5 y1 + 0.4) pName
                  "centerLeft" "R0" "roman" 0.0) t)
                when(lbl && car(lbl) errset(dbAddFigToNet(car(lbl) net) t)))
              errset(dbCreatePin(net nil pName) t)
              net->isPin = t)))
        i = i + 1))
    ;---- 落盘
    errset(dbSave(cv) t)
    errset(dbClose(cv) t)
    librfViewsNoteCell(sprintf(nil "%s/%s/symbol" libName cellName))
    t))

;---------------------------------------------------------------- 主入口
; librfMakeViews(req)
;   req 键: lib(默认"LIBRF") cell snp ports kind result libdir
procedure(librfMakeViews(req)
  let((libName cellName snpPath portsStr portNames kind resultPath libDir runOk)
    librfViewsReset()
    libName    = librfReqStr(req 'lib "LIBRF")
    cellName   = librfReqStr(req 'cell nil)
    snpPath    = librfReqStr(req 'snp nil)
    portsStr   = librfReqStr(req 'ports nil)
    kind       = librfReqStr(req 'kind "ind")
    resultPath = librfReqStr(req 'result nil)
    libDir     = librfReqStr(req 'libdir nil)
    librf__vresultPath = resultPath
    ;---- 参数校验
    when(cellName == nil   librfViewsErr("缺少参数 cell"))
    when(snpPath == nil    librfViewsErr("缺少参数 snp"))
    when(portsStr == nil   librfViewsErr("缺少参数 ports"))
    when(resultPath == nil librfViewsErr("缺少参数 result"))
    when(cellName == nil || snpPath == nil || portsStr == nil || resultPath == nil
      librfWriteViewsResult(nil)
      exit())
    ; 解析端口名 (空格分隔)
    portNames = parseString(portsStr " \t")
    when(length(portNames) < 1
      librfViewsErr("ports 为空")
      librfWriteViewsResult(nil)
      exit())
    ;---- 锁检测 (整体 fail, 不写任何数据)
    when(librfViewsIsLocked(libDir cellName "schematic")
      librfViewsErr(sprintf(nil
        "view 被 Virtuoso 占用: %s/%s/schematic 存在 *.cdslck* -- 请先关闭 Virtuoso 中该 cellview 的编辑器再重试"
        libName cellName)))
    when(librfViewsIsLocked(libDir cellName "symbol")
      librfViewsErr(sprintf(nil
        "view 被 Virtuoso 占用: %s/%s/symbol 存在 *.cdslck* -- 请先关闭 Virtuoso 中该 cellview 的编辑器再重试"
        libName cellName)))
    when(librf__vok == nil
      librfWriteViewsResult(nil)
      exit())
    ;---- 生成
    runOk = errset(progn(
      librfViewsBuildSchematic(libName cellName snpPath portNames libDir)
      librfViewsBuildSymbol(libName cellName portNames kind libDir) ) t)
    when(runOk == nil
      librfViewsErr("SKILL 运行异常 (见 stdout 报错)"))
    ; PIN 行 (每端口一行, 汇总两 view)
    librf__vpins = nil
    foreach(pName portNames
      librfViewsNotePin(pName "inputOutput"))
    librfWriteViewsResult(librf__vok)
    exit()))
; ==== librf_viewgen.il merged：EMX create-view 批处理 (v03) ====
; 唯一入口: librfEmxViews(list(nil 'lib "LIBRF" 'cell "xxx" 'snp "/abs/x.s2p"
;   'ports "p01 p02" 'kind "ind" 'result "/abs/res.txt" 'libdir "/abs/LIBRF"))
; 行为 (对齐 EMX GUI "Create view" 的 N-port 路径):
;   1. load EMX interface (emxconfig.il -> emxform.ils -> makeschsym.ils) + batch
;      SKILL++ stubs (屏蔽 hiDisplayAppDBox / schCheck 等)
;   2. EMX_make_n_port_symbol -> symbol_nport; EMX_make_n_port_schematic_no_dev ->
;      schematic_nport (nport 实例 dataFile = snp 绝对路径, datafmt=touchstone)
;   3. dbCopyCellView 改名/复制为 symbol / schematic
;   4. kind 可选: makeschsym 的 EMX_make_<type>_symbol 替换 symbol ("ind" -> 电感
;      线圈图形; 仅当端口数匹配时启用, 否则 WARN 回退 nport box)
;   5. 覆盖重跑: 写前删旧 view; *.cdslck* 被占则 ERR 中文拒写
;   6. 结果行协议 STATUS/CELL/PIN/WARN/ERR + stdout LIBRF_EXIT=0|1
; terminals 顺序 = sNp 列序 (ports 键) + 末尾 GND; terminal_dirs 全 inputOutput
; (金标准 testdir/balun 与 testdir/symind/schematic_nport 一致)
; 约束: 经典 SKILL (procedure/let/prog); EMX 内部函数为 SKILL++, 经 load 调用;
;       禁变量名 t; errset 单表达式; 路径正斜杠
; =============================================================================
; 结果协议状态 / 全局旗标
;-----------------------------------------------------------------------------
librf__emx_ready = nil
procedure(librfEmxReset()
  librf__eok     = t
  librf__ecells  = nil
  librf__epins   = nil
  librf__ewarns  = nil
  librf__eerrs   = nil
  librf__eresultPath = nil)
procedure(librfEmxWarn(msg)
  librf__ewarns = cons(msg librf__ewarns))
procedure(librfEmxErr(msg)
  librf__eok = nil
  librf__eerrs = cons(msg librf__eerrs))
procedure(librfEmxNoteCell(tag)
  librf__ecells = cons(tag librf__ecells))
procedure(librfEmxNotePin(name dirStr)
  librf__epins = cons(list(name dirStr) librf__epins))
procedure(librfEmxWriteResult(okFlag)
  let((port)
    when(librf__eresultPath
      port = errset(outfile(librf__eresultPath) t)
      when(port && car(port)
        port = car(port)
        if(okFlag
          then fprintf(port "STATUS ok\n")
          else fprintf(port "STATUS fail\n"))
        foreach(tag reverse(librf__ecells)
          fprintf(port "CELL %s\n" tag))
        foreach(entry reverse(librf__epins)
          fprintf(port "PIN %s %s\n" car(entry) cadr(entry)))
        foreach(msg reverse(librf__ewarns) fprintf(port "WARN %s\n" msg))
        foreach(msg reverse(librf__eerrs)  fprintf(port "ERR %s\n" msg))
        close(port)))
    printf("LIBRF_EXIT=%s\n" if(okFlag then "0" else "1"))))
;-----------------------------------------------------------------------------
; 请求解析 (与 librf_layout.il 的 librfReq* 同风格)
;-----------------------------------------------------------------------------
procedure(librfEmxReqGet(req key dflt)
  let((seg)
    seg = member(key req)
    cond(
      (seg && cdr(seg)  cadr(seg))
      (t                dflt))))
procedure(librfEmxReqStr(req key dflt)
  let((v)
    v = librfEmxReqGet(req key dflt)
    if(v == nil then nil else sprintf(nil "%s" v))))
;-----------------------------------------------------------------------------
; 锁检测: libdir/cell/view/*.cdslck*  t=被占 / nil=可写
;-----------------------------------------------------------------------------
procedure(librfEmxIsLocked(libDir cellName viewName)
  prog((dir shcmd shres)
    when(libDir == nil || cellName == nil || viewName == nil
      return(nil))
    dir = sprintf(nil "%s/%s/%s" libDir cellName viewName)
    shcmd = sprintf(nil "ls %s/*.cdslck* >/dev/null 2>&1" dir)
    shres = errset(system(shcmd) t)
    if(shres && car(shres) == 0
      then return(t)
      else return(nil))))
;-----------------------------------------------------------------------------
; 删除已有 cellview (覆盖重跑前置; 否则 EMX_make_* 会追加/拷贝加锁失败)
;-----------------------------------------------------------------------------
procedure(librfEmxDelView(libName cellName viewName)
  let((obj)
    obj = ddGetObj(libName cellName viewName)
    when(obj
      errset(ddDeleteObj(obj) t))))
;-----------------------------------------------------------------------------
; 找 EMX interface 目录
;-----------------------------------------------------------------------------
procedure(librfEmxIfaceDir()
  prog((cands envp d)
    envp = getShellEnvVar("EMX_INTERFACE_PATH")
    cands = list(
      "/opt/eda/cadence/INTEGRAND60/virtuoso_ui/emxinterface"
      "/opt/eda/cadence/INTEGRAND58/virtuoso_ui/emxinterface")
    when(envp && envp != ""
      cands = cons(envp cands))
    d = nil
    foreach(c cands
      when(d == nil
        when(c && isFile(strcat(c "/emxskill/emxconfig.il"))
          d = c)))
    return(d)))
;-----------------------------------------------------------------------------
; EMX 初始化 (幂等): 加载 emxconfig.il (会 load emxform/makeschsym)
; emxform.ils 尾部 hiCreatePulldownMenu 在 dbAccess 下不存在 -> load 报错,
; 但所需函数 (EMX_make_n_port_* / EMX_make_*_symbol) 在报错点之前已定义。
; 随后用 SKILL++ define 打 batch 补丁, 避免 hiDisplayAppDBox/schCheck 崩溃。
;-----------------------------------------------------------------------------
procedure(librfEmxInit()
  prog((iface loadRes stub)
    when(boundp('librf__emx_ready) && librf__emx_ready
      return(t))
    iface = librfEmxIfaceDir()
    when(iface == nil
      librfEmxErr("找不到 EMX interface 目录 (emxconfig.il). 请设置 EMX_INTERFACE_PATH 或安装 EMX")
      return(nil))
    EMX_interface_path = iface
    ; load emxconfig.il: 尾部 GUI 报错可容忍, 用 errset 吞掉
    loadRes = errset(load(strcat(iface "/emxskill/emxconfig.il")) t)
    unless(fboundp('EMX_make_n_port_symbol)
      librfEmxErr(sprintf(nil
        "EMX 函数未加载 (EMX_make_n_port_symbol). emxconfig 路径=%s load=%L" iface loadRes))
      return(nil))
    ; batch stubs (SKILL++ define, 必须 evalstring 而非 procedure)
    stub = "
(define (schCheck cv) t)
(define (EMX_error @rest args) (error (apply sprintf (cons nil args))))
(define (EMX_unexpected_error @rest args) (error (apply sprintf (cons nil args))))
(define (EMX_warning @rest args) (printf \"WARN %s\\n\" (apply sprintf (cons nil args))))
(define (EMX_informational @rest args) (printf \"INFO %s\\n\" (apply sprintf (cons nil args))))
(define (EMX_warning_okcancel @rest args) t)
"
    errset(evalstring(stub) t)
    ; 强制覆盖已有 view (我们自己先删旧); 不弹确认框
    EMX_force_overwrite = t
    ; 不把 sNp 拷成 text cellview, dataFile 直接用绝对路径
    EMX_nport_sparam_view_name = nil
    librf__emx_ready = t
    return(t)))
;-----------------------------------------------------------------------------
; kind -> makeschsym 符号函数名 (仅 symbol; schematic 始终 nport 挂 sNp)
; 返回 nil 表示用 nport box
;-----------------------------------------------------------------------------
procedure(librfEmxSymFnForKind(kind nTotal)
  prog((fn)
    fn = nil
    cond(
      ((kind == "ind" || kind == "inductor") && nTotal == 3
        fn = 'EMX_make_inductor_symbol)
      ((kind == "mim" || kind == "mimcap") && nTotal == 3
        fn = 'EMX_make_mim_capacitor_symbol)
      ((kind == "mom" || kind == "momcap") && nTotal == 3
        fn = 'EMX_make_mom_capacitor_symbol)
      (t fn = nil))
    when(fn && not(fboundp(fn))
      fn = nil)
    return(fn)))
;-----------------------------------------------------------------------------
; 生成 nport symbol + schematic, 再复制为 symbol/schematic
;-----------------------------------------------------------------------------
procedure(librfEmxBuild(libName cellName snpPath portNames kind libDir)
  prog((terminals dirs signals pinDirs symFn res src nTotal tag)
    nTotal = length(portNames) + 1   ; + GND
    ;---- terminals: sNp 列序 + GND (金标准 balun/symind 末位 GND)
    terminals = append(copy(portNames) list("GND"))
    dirs = nil
    foreach(tn terminals
      dirs = cons("inputOutput" dirs))
    dirs = reverse(dirs)
    signals = nil
    pinDirs = nil
    foreach(pn portNames
      signals  = cons(list(pn "GND") signals)
      pinDirs  = cons(list(pn "inputOutput") pinDirs))
    signals = reverse(signals)
    pinDirs = append(reverse(pinDirs) list(list("GND" "inputOutput")))

    ;---- 清旧 (覆盖重跑)
    foreach(v list("symbol_nport" "schematic_nport" "symbol" "schematic")
      librfEmxDelView(libName cellName v))

    ;---- symbol_nport (EMX_make_n_port_symbol)
    res = errset(EMX_make_n_port_symbol(libName cellName "symbol_nport"
      terminals dirs) t)
    if(res && car(res)
      then librfEmxNoteCell(sprintf(nil "%s/%s/symbol_nport" libName cellName))
      else progn(
        librfEmxErr("EMX_make_n_port_symbol 失败 (symbol_nport)")
        return(nil)))

    ;---- schematic_nport (nport + dataFile=snp)
    res = errset(EMX_make_n_port_schematic_no_dev(nil libName cellName
      "schematic_nport" signals pinDirs snpPath) t)
    if(res && car(res)
      then librfEmxNoteCell(sprintf(nil "%s/%s/schematic_nport" libName cellName))
      else progn(
        librfEmxErr("EMX_make_n_port_schematic_no_dev 失败 (schematic_nport)")
        return(nil)))

    ;---- 复制改名 -> symbol / schematic
    src = errset(dbOpenCellViewByType(libName cellName "symbol_nport" nil "r") t)
    when(src && car(src)
      src = car(src)
      res = errset(dbCopyCellView(src libName cellName "symbol" nil nil t) t)
      errset(dbClose(src) t)
      if(res && car(res)
        then librfEmxNoteCell(sprintf(nil "%s/%s/symbol" libName cellName))
        else librfEmxErr("复制 symbol_nport -> symbol 失败")))

    src = errset(dbOpenCellViewByType(libName cellName "schematic_nport" nil "r") t)
    when(src && car(src)
      src = car(src)
      res = errset(dbCopyCellView(src libName cellName "schematic" nil nil t) t)
      errset(dbClose(src) t)
      if(res && car(res)
        then librfEmxNoteCell(sprintf(nil "%s/%s/schematic" libName cellName))
        else librfEmxErr("复制 schematic_nport -> schematic 失败")))

    ;---- 可选: makeschsym 美化 symbol (kind 匹配且端口数吻合)
    ; 失败时从 symbol_nport 补拷回 symbol, 保证 symbol 始终存在
    symFn = librfEmxSymFnForKind(kind nTotal)
    when(symFn
      librfEmxDelView(libName cellName "symbol")
      res = errset(apply(symFn list(libName cellName "symbol" terminals dirs)) t)
      if(res && car(res)
        then librfEmxWarn(sprintf(nil
            "symbol 已用 makeschsym %s 生成 (电感图形)" symFn))
        else progn(
          librfEmxWarn(sprintf(nil
            "makeschsym %s 生成 symbol 失败, 回退 nport box" symFn))
          src = errset(dbOpenCellViewByType(libName cellName "symbol_nport" nil "r") t)
          when(src && car(src)
            src = car(src)
            errset(dbCopyCellView(src libName cellName "symbol" nil nil t) t)
            errset(dbClose(src) t)))))

    ;---- PIN 行 (端口 + GND)
    foreach(pn portNames
      librfEmxNotePin(pn "inputOutput"))
    librfEmxNotePin("GND" "inputOutput")
    return(t)))
;-----------------------------------------------------------------------------
; 主入口
;-----------------------------------------------------------------------------
procedure(librfEmxViews(req)
  let((libName cellName snpPath portsStr portNames kind resultPath libDir runOk)
    librfEmxReset()
    libName    = librfEmxReqStr(req 'lib "LIBRF")
    cellName   = librfEmxReqStr(req 'cell nil)
    snpPath    = librfEmxReqStr(req 'snp nil)
    portsStr   = librfEmxReqStr(req 'ports nil)
    kind       = librfEmxReqStr(req 'kind "ind")
    resultPath = librfEmxReqStr(req 'result nil)
    libDir     = librfEmxReqStr(req 'libdir nil)
    librf__eresultPath = resultPath
    ;---- 参数校验
    when(cellName == nil   librfEmxErr("缺少参数 cell"))
    when(snpPath == nil    librfEmxErr("缺少参数 snp"))
    when(portsStr == nil   librfEmxErr("缺少参数 ports"))
    when(resultPath == nil librfEmxErr("缺少参数 result"))
    when(cellName == nil || snpPath == nil || portsStr == nil || resultPath == nil
      librfEmxWriteResult(nil)
      exit())
    portNames = parseString(portsStr " \t")
    when(length(portNames) < 1
      librfEmxErr("ports 为空")
      librfEmxWriteResult(nil)
      exit())
    ; snp 必须存在
    unless(isFile(snpPath)
      librfEmxErr(sprintf(nil "sNp 文件不存在: %s" snpPath)))
    ; snp 列数 vs ports 数 (粗检: 文件头 ! Port order 或 .sNp 扩展名)
    when(librf__eok
      let((ext npExt)
        ; .s2p -> 2 端口 .s4p -> 4 ...
        ext = nil
        foreach(suffix list(".s1p" ".s2p" ".s3p" ".s4p" ".s5p" ".s6p" ".s7p" ".s8p"
                           ".s9p" ".s10p" ".s11p" ".s12p")
          when(ext == nil && length(snpPath) > length(suffix)
            when(substring(snpPath length(snpPath)-length(suffix)+1) == suffix
              ext = suffix)))
        when(ext
          npExt = atoi(substring(ext 3 length(ext)-1))
          when(npExt != length(portNames)
            librfEmxWarn(sprintf(nil
              "ports 数(%d) 与 sNp 端口数(%d, 据扩展名 %s) 不一致 -- 以 ports 为准"
              length(portNames) npExt ext))))))
    ;---- 锁检测 (整体 fail, 不写任何数据)
    when(librf__eok
      foreach(v list("schematic" "symbol" "schematic_nport" "symbol_nport")
        when(librfEmxIsLocked(libDir cellName v)
          librfEmxErr(sprintf(nil
            "view 被 Virtuoso 占用: %s/%s/%s 存在 *.cdslck* -- 请先关闭 Virtuoso 中该 cellview 的编辑器再重试"
            libName cellName v)))))
    when(librf__eok == nil
      librfEmxWriteResult(nil)
      exit())
    ;---- EMX 初始化
    runOk = errset(librfEmxInit() t)
    unless(runOk && car(runOk) && boundp('librf__emx_ready) && librf__emx_ready
      when(librf__eok
        librfEmxErr("EMX 初始化失败 (详见 stdout)"))
      librfEmxWriteResult(nil)
      exit())
    ;---- 生成
    runOk = errset(librfEmxBuild(libName cellName snpPath portNames kind libDir) t)
    when(runOk == nil
      librfEmxErr("SKILL 运行异常 (见 stdout 报错)"))
    librfEmxWriteResult(librf__eok)
    exit()))
