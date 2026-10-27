;====================================================================
; librf.il — Helix 1.2.0 SKILL 合并文件（两部分，两种 Cadence 进程共用）
;   A) 版图引擎 (原 librf_layout.il)：dbAccess 经 driver.il load() 后调
;      librfRun(...) / librfMakeViews(...) / librfEmxViews(...)
;   B) 求值服务 (原 eval_serve.il)：virtuoso -replay librf.il 启动，读 ADE L
;      outputs 定义，从 psf 提取曲线（约 0.5 秒/次）
; 合并依据：procedure 定义不校验未定义函数，OCEAN 与 dbAccess 函数可共处一文件，
; 各自只在被调用时解析；命名空间无冲突。工艺无关化：层名一律经 librfLyr(role)
; 解析（tech_profile.json layers 段），无 profile 时用内建 RFCMOS 默认映射。
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
; librf_layout.il — LIBRF SKILL 版图引擎 (IC618 经典 SKILL / dbAccess, Helix 1.2.0)
; 职责: 电感/传输线/变压器/耦合线参数化绘制 + port/label + MARKER 标记 + via 阵列
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
; 【移植 v08c】librfPathSweepEx：折线扫掠（中心线 pts + 宽 w），几何与下方
; librfPathSweep 的原实现逐位相同（snap 去重 -> dbCreatePath -> 端点补块），
; 只多 capStart/capEnd 两个端帽开关。sym 换圈 flush 端必须两者都为 nil ——
; 满宽 w×w 方端帽会同时撞上另一臂 45° 斜线与外圈切角（夹缝放不下方角）。
procedure(librfPathSweepEx(cv layerSpec pts w capStart capEnd)
  prog((clean p prev n res layName half)
    ; snap + 去重 (相邻点距离 <0.5nm 视为同点)
    clean = nil
    foreach(p pts
      when(clean == nil ||
           abs(librfSnap(car(p)) - car(librfLastPt(clean))) > 0.0005 ||
           abs(librfSnap(cadr(p)) - cadr(librfLastPt(clean))) > 0.0005
        clean = cons(list(librfSnap(car(p)) librfSnap(cadr(p))) clean)
      )
    )
    clean = reverse(clean)
    n = length(clean)
    when(n < 2
      librfErr("path 点数 <2")
      return(nil)
    )
    layName = if(listp(layerSpec) then car(layerSpec) else layerSpec)
    librfNoteLayer(layName)
    half = w / 2.0
    ; 首选: dbCreatePath 中心线折线 (4 参, pathType 创建后可设)
    res = errset(dbCreatePath(cv layerSpec clean w) t)
    when(res && car(res)
      ; pathType=0 truncate/flush: 端点止于顶点, 由 EndCap 搭接
      errset(car(res)->pathType = 0 t)
    )
    when(res == nil || car(res) == nil
      librfErr(sprintf(nil "dbCreatePath 失败 layer=%s npts=%d, 退矩形扫掠" layName n))
      ; 回退: 旧 rect+diagseg 扫掠 (不应走到)
      prev = car(clean)
      foreach(p cdr(clean)
        librfDiagSeg(cv layerSpec car(prev) cadr(prev) car(p) cadr(p) w)
        prev = p
      )
    )
    ; 端帽: 与 stub/underpass 无缝搭接。sym 换圈 flush 端必须
    ; capStart/capEnd=nil：满宽方端帽会同时撞上另一臂 45° 斜线
    ; 与外圈切角（45° 夹缝宽度不足以容纳 w x w 方角）。
    when(capStart
      librfEndCap(cv layerSpec car(car(clean)) cadr(car(clean)) w))
    when(capEnd
      librfEndCap(cv layerSpec
        car(librfLastPt(clean)) cadr(librfLastPt(clean)) w))
    return(t)
  )
)
procedure(librfPathSweep(cv layerSpec pts w)
  librfPathSweepEx(cv layerSpec pts w t t))

;---------------------------------------------------------------- 端子夹角 (tangle)
; 【移植 v08c】librfDirAngle / librfTangleDirs：给定两个端口的自然出线方向，
; 选出夹角等于 tangle(0/90/180) 的 (dir1 dir2)；代价小者优先——改端口 2
; 的代价(1)低于改端口 1 的代价(10)，即尽量保第一个端口自然向。
; lri 引擎的 se / f8 拓扑用它们实现 tangle 参数。
procedure(librfDirAngle(k1 k2)
  if(k1 == k2
    then 0
    else if((k1 == 'N && k2 == 'S) || (k1 == 'S && k2 == 'N) ||
            (k1 == 'E && k2 == 'W) || (k1 == 'W && k2 == 'E)
      then 180
      else 90
    )
  )
)
procedure(librfTangleDirs(tangle nat1 nat2)
  let((best bc k1 k2 cost)
    best = nil  bc = 999
    foreach(k1 '(N S E W)
      foreach(k2 '(N S E W)
        when(librfDirAngle(k1 k2) == tangle
          cost = 0
          when(k1 != nat1  cost = cost + 10)
          when(k2 != nat2  cost = cost + 1)
          when(cost < bc  bc = cost  best = list(k1 k2))
        )
      )
    )
    unless(best  best = list(nat1 nat2))
    best
  )
)
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
; 'tech 键注入；缺省表即内建 RFCMOS 映射，所以不给 profile 时行为与旧版
; 逐位一致。使用者在 tech_profile.json 里改 layers.spiral 等即可换金属绘制。
defvar(librfTech nil)
defvar(librfTechText nil)
defvar(librfDrc nil)
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
    list("M4"  "M4TXT")
    list("M3"  "M3TXT")
    list("M2"  "M2TXT")
    list("M1"  "M1TXT")
    list("MARKER" "MARKERTXT")))
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
      (p && cdr(p) && numberp(cadr(p))  1.0 * cadr(p))
      (t                                dflt))))

; via 规则查表 (cut/spacing/enclosure)。librfDrc 的 "via" 子表由 driver.il 注入,
; 缺项回落内建默认 (cut 0.5 / spacing 0.5 / enclosure 0.2, 与示教 tech_profile 一致)。
; 换工艺时只改 tech_profile.json 的 drc.via, 不再改 SKILL。
procedure(librfDrcViaNum(key dflt)
  let((p q)
    when(boundp('librfDrc) && librfDrc
      p = assoc("via" librfDrc)
      when(p && cdr(p)
        q = assoc(key cadr(p))
        when(q && cdr(q) && numberp(cadr(q))
          dflt = 1.0 * cadr(q))))
    dflt))

procedure(librfDrcMinArea(metalName)
  let((p sub)
    when(boundp('librfDrc) && librfDrc
      p = assoc("min_area" librfDrc))
    when(p && cdr(p)
      sub = assoc(metalName cadr(p))
      when(sub && cdr(sub) && numberp(cadr(sub))
        return(1.0 * cadr(sub))))
    4.0))
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
  let((minW minA fig b w h boxes bb g nTouch nBox i j b1 b2 gap minGap nViol big1 big2 pw)
    minW = librfDrcNum("min_w" 1.0)
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
    ; path 线宽: 螺旋/underpass 主体都由 dbCreatePath 画出, 原实现只查 rect,
    ; 线宽违规完全静默。宽度取 fig->width (取不到时跳过该项)。
    foreach(fig cv->shapes
      when(equal(fig->objType 'path) && fig->layerName == metalName
        when(fig->purposeName == nil || fig->purposeName == "drawing"
          pw = car(errset(fig->width))
          ; fig->width 若回整数, %.3f 会崩; 取到数就强转浮点
          when(numberp(pw) pw = 1.0 * pw)
          when(numberp(pw) && pw + 0.0005 < minW
            librfWarn(sprintf(nil "DRC: 走线宽度 %.3f < %.1f (%s)"
              pw minW metalName))))))
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
; 几何闭合: din = dout - 2*[ceil(n)*(w+s) - s]   (res3 式 A, 匝数向上取整)
; 段数 nSeg=round(4n) 时内孔由「最后一条整圈底边」决定, 半整数匝实际孔径比 n 本体
; 小一个 pitch: n=2.5,w=10,s=2.5,dout=100 实画孔径 30, 原式给 42.5 (虚大)。
; 八角同式 (规范 /8 构造实测孔径与该式一致)。
procedure(librfDinFromParams(n w s dout)
  let((pitch m)
    pitch = w + s
    m = fix(ceiling(n - 1.0e-6))
    dout - 2.0 * (m * pitch - s)))
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
; 线 k: 法向 phi_k = -90° - k*45°, 距 d_k = r0 - floor(k/8)*pitch
; 【v09 修正】原实现按 floor(k/4) 内缩, 即 4 条边就掉一个 pitch, 而一周是 8 条边,
;   于是相邻两圈只隔 pitch/2: n=2.5,w=10,s=2.6 实测非相邻段中心距 8.86um < w=10
;   (金属自重叠), 且金属外框恒 137.4 而非 dout=150、中心偏 (-6.3,-6.3)。
;   改为 floor(k/8) 后 bbox=dout,d 恒为中心,pitch 恒 = w+s (与参考实现一致)。
; 顶点 j = 线(j-1) ∩ 线 j ; 段 j 沿线 j 从 V_j 到 V_{j+1}
;-----------------------------------------------------------------------------
procedure(librfOctLine(k r0 pitch)
  ; 返回 (phi d): 线 k 的法向角(rad) 与 距
  let((phi d)
    phi = (-90.0 - k * 45.0) * 3.14159265358979 / 180.0
    d = if(k < 0 then r0 else r0 - fix(k / 8) * pitch)
    list(phi d)))
procedure(librfOctSpiralPts(n w s dout)
  let((pitch r0 nSeg pts j lineA lineB phiA dA phiB dB det px py stopMin)
    pitch = w + s
    r0 = (dout - w) / 2.0
    nSeg = fix(round(8.0 * n))
    stopMin = max(1.5 * w pitch)
    pts = nil
    for(j 0 nSeg
      lineA = librfOctLine(j - 1 r0 pitch)
      lineB = librfOctLine(j     r0 pitch)
      phiA = car(lineA)  dA = cadr(lineA)
      phiB = car(lineB)  dB = cadr(lineB)
      ; 内圈半径不足: 截断 (再往里画会让相邻段中心距 < w 而自重叠)
      when(dB + 0.0005 < stopMin
        librfWarn(sprintf(nil
          "八角螺旋截断: 请求 %d 段, 实际 %d 段 (内圈半径 %.3f < %.3f), 实际匝数 %.2f"
          nSeg j dB stopMin (j / 8.0)))
        break)
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
;-----------------------------------------------------------------------------
; 【已删除】librfHalfSpiralPts —— 原"半螺旋"实为半平面蛇形 (底边 + 45° 收径跳线
; 左右往返), 电流在相邻底边上反向抵消: 实测以绕线中点收尾时
;   shoelace(A) = shoelace(B) = -2962.50um^2, 且 B 反走 -> A-B = 0.00um^2,
; 同尺寸方形螺旋为 -36718.75um^2。即该结构包围面积恒为 0, 不是电感。
; 单层金属也无法拼出左右对称的半螺旋(两半会在每个环的角点重合短路)。
; CT/balun/变压器已改用整螺旋 + 中点抽头 / 中心下穿方案, 本函数无调用者。
;-----------------------------------------------------------------------------
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
; 螺旋外形自检 (生成期兜底, 防 P0-1 类"内缩步长/段长算错"):
;   中心线 bbox 必须 = (dout-w+ex) x (dout-w+ey), 且中心 = (x0,y0)。
;   方形/八角共用 该闭合关系(八角规范构造实测 bbox=dout-w; 方形 L0=dout-w)。
;   不用"非相邻段最小中心距 >= w": 八角在换圈处的短 45° 过渡边会被误判为自重叠。
procedure(librfAssertSpiralBox(pts dout w ex ey x0 y0 tag)
  let((bb bw bh cx cy ew eh)
    bb = librfPtsBBox(pts)
    bw = caddr(bb) - car(bb)
    bh = cadddr(bb) - cadr(bb)
    cx = (car(bb) + caddr(bb)) / 2.0
    cy = (cadr(bb) + cadddr(bb)) / 2.0
    ew = dout - w + ex
    eh = dout - w + ey
    when(abs(bw - ew) > 0.02 || abs(bh - eh) > 0.02
      librfErr(sprintf(nil
        "%s 螺旋外形异常: 中心线 bbox %.3f x %.3f, 期望 %.3f x %.3f (dout=%.2f w=%.2f ex=%.2f ey=%.2f)"
        tag bw bh ew eh dout w ex ey)))
    ; 中心只在未拉长时硬断言: 拉长按段方向加长, 会带来 (-ex/2, +ey/2) 的既有偏移
    when(abs(ex) < 1.0e-9 && abs(ey) < 1.0e-9
      when(abs(cx - x0) > 0.02 || abs(cy - y0) > 0.02
        librfErr(sprintf(nil
          "%s 螺旋中心偏移: (%.3f,%.3f), 期望 (%.3f,%.3f)"
          tag cx cy x0 y0))))))
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
; underpass / marker / 库与 cellview
;=============================================================================
; via 落点+走线+远端回穿+stub 端口 (res3 §underpass)
; (xIn,yIn)=螺旋内端, (xOut,yOut)=螺旋外侧回穿点(调用方按 dout 计算)
; underName: "direct"(V4 直连) 或 "route"(默认, V3+V4 经 M4 过渡)
; 走线用 dbCreatePath L 折线一次画出 (规整, 无拼缝)
procedure(librfUnderpass(cv xIn yIn xOut yOut exitDir wUp spiralW underName portName stubLen)
  let((halfUp cutW viaS viaE viaOff routePts)
    halfUp = wUp / 2.0
    ; via 规则从 tech_profile.json 的 drc.via 读 (cut/spacing/enclosure), 缺项回落内建
    cutW = librfDrcViaNum("cut" 0.5)
    viaS = librfDrcViaNum("spacing" 0.5)
    viaE = librfDrcViaNum("enclosure" 0.2)
    viaOff = (cutW + viaS) / 2.0
    ; L 形中心线: 内端 -> 拐点 -> 外端 (已对齐时退化为直线)
    routePts = librfRoutePts(xIn yIn xOut yOut exitDir)
    if(underName == "direct" || underName == "DIRECT"
      then
        ; 方案 A: M5 --V4-- M4 走线 --V4-- M5
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW viaS viaE viaE)
        librfPathSweep(cv librfMetalPair(librfLyr("underpass")) routePts wUp)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW viaS viaE viaE)
      else
        ; 方案 B: M5 --V4-- M4 过渡 --V3-- M3 走线 --V3-- M4 --V4-- M5
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW viaS viaE viaE)
        ; V3 与 V4 cut 网格错开半格(两级栈交错); 单孔时退化为同位栈(PDK 允许)
        librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW viaS viaE viaE viaOff viaOff)
        librfPathSweep(cv librfMetalPair(librfLyr("route")) routePts wUp)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
        ; V3 与 V4 cut 网格错开半格(两级栈交错); 单孔时退化为同位栈(PDK 允许)
        librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW viaS viaE viaE viaOff viaOff)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW viaS viaE viaE))
    ; 回 M5 落盘 (保证 cut 包覆) + stub 端口
    librfRect(cv librfMetalPair(librfLyr("spiral"))
      xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
    librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
      xOut yOut exitDir spiralW stubLen portName)))

;-----------------------------------------------------------------------------
; 下层绕组 (underpass 层 M4) 的内端/抽头引出:
;   M4 焊盘 --V3-- M3 走线 --V3-- M4 焊盘 -- stub 端口 (M4TXT)
; 与 librfUnderpass 的区别: 起点在 M4 上且终点也回 M4, 全程不碰 M5
; (M5 上还有另一层绕组, 若在 M4 点画 M5 焊盘会短路)。
;-----------------------------------------------------------------------------
procedure(librfUnderTm(cv xIn yIn xOut yOut exitDir wUp w portName stubLen)
  let((halfUp cutW viaS viaE viaOff routePts)
    halfUp = wUp / 2.0
    cutW = librfDrcViaNum("cut" 0.5)
    viaS = librfDrcViaNum("spacing" 0.5)
    viaE = librfDrcViaNum("enclosure" 0.2)
    viaOff = (cutW + viaS) / 2.0
    routePts = librfRoutePts(xIn yIn xOut yOut exitDir)
    ; 起点焊盘 + V3 下到 M3
    librfRect(cv librfMetalPair(librfLyr("underpass"))
      xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp)
    librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
      xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW viaS viaE viaE viaOff viaOff)
    librfPathSweep(cv librfMetalPair(librfLyr("route")) routePts wUp)
    ; 终点: 回 M4 焊盘 + V3 + stub 端口
    librfRect(cv librfMetalPair(librfLyr("underpass"))
      xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
    librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
      xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW viaS viaE viaE viaOff viaOff)
    librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
      xOut yOut exitDir w stubLen portName)))
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
; MARKER 标记: bbox 外扩 margin 的正交矩形
procedure(librfIndMark(cv bbox margin)
  let((x1 y1 x2 y2 w h)
    when(bbox
      x1 = car(bbox) - margin    y1 = cadr(bbox) - margin
      x2 = caddr(bbox) + margin  y2 = cadddr(bbox) + margin
      w = x2 - x1   h = y2 - y1
      ; 标记环宽 30~500 语义未定, 按最小边长告警
      when(min(w h) < 30.0
        librfWarn(sprintf(nil "MARKER 标记最小边 %.1f < 30um, 请核对标记层规则" min(w h))))
      when(max(w h) > 500.0
        librfWarn(sprintf(nil "MARKER 标记最大边 %.1f > 500um, 请核对标记层规则" max(w h))))
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
    ; tech 有效性探测: 按 profile 的 spiral 层名探测，而不是写死 M5。
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
;-----------------------------------------------------------------------------
; 沿中心线按弧长比例取点 (中心抽头用): frac=0.5 即绕线中点
; 返回 (x y ux uy): 点坐标 + 该段走向单位向量
;-----------------------------------------------------------------------------
procedure(librfTapPoint(pts frac)
  let((total acc prev cur d ux uy tb x y done out)
    total = 0.0
    prev = car(pts)
    foreach(cur cdr(pts)
      total = total + sqrt((car(cur) - car(prev)) * (car(cur) - car(prev)) +
                           (cadr(cur) - cadr(prev)) * (cadr(cur) - cadr(prev)))
      prev = cur)
    ; 注意: 本函数用 let 而非 prog, 不能 return, 用 if 取返回值
    if(total < 1.0e-6
      then out = list(car(car(pts)) cadr(car(pts)) 1.0 0.0)
      else
        progn(
          acc = 0.0  done = nil
          x = car(car(pts))  y = cadr(car(pts))  ux = 1.0  uy = 0.0
          prev = car(pts)
          foreach(cur cdr(pts)
            unless(done
              d = sqrt((car(cur) - car(prev)) * (car(cur) - car(prev)) +
                       (cadr(cur) - cadr(prev)) * (cadr(cur) - cadr(prev)))
              when(acc + d >= frac * total
                tb = if(d < 1.0e-9 then 0.0 else (frac * total - acc) / d)
                x = car(prev) + (car(cur) - car(prev)) * tb
                y = cadr(prev) + (cadr(cur) - cadr(prev)) * tb
                ux = if(d < 1.0e-9 then 1.0 else (car(cur) - car(prev)) / d)
                uy = if(d < 1.0e-9 then 0.0 else (cadr(cur) - cadr(prev)) / d)
                done = t)
              acc = acc + d
              prev = cur))
          out = list(x y ux uy)))
    out))
;-----------------------------------------------------------------------------
; 两折线中心线最小距离 (采样近似): 用于同层走线互查 (path 不进 librfDrcFigs)
;-----------------------------------------------------------------------------
procedure(librfPtsMinDist(ptsA ptsB)
  let((best prevA curA prevB curB i n s pa pb dx dy l2 u q dd)
    best = 1.0e30
    n = 24
    prevA = car(ptsA)
    foreach(curA cdr(ptsA)
      for(i 0 n
        s = i / 1.0 / n
        pa = list(car(prevA) + (car(curA) - car(prevA)) * s
                  cadr(prevA) + (cadr(curA) - cadr(prevA)) * s)
        prevB = car(ptsB)
        foreach(curB cdr(ptsB)
          dx = car(curB) - car(prevB)
          dy = cadr(curB) - cadr(prevB)
          l2 = dx * dx + dy * dy
          if(l2 < 1.0e-12
            then pb = list(car(prevB) cadr(prevB))
            else
              u = ((car(pa) - car(prevB)) * dx + (cadr(pa) - cadr(prevB)) * dy) / l2
              u = max(0.0 min(1.0 u))
              pb = list(car(prevB) + dx * u cadr(prevB) + dy * u))
          dd = sqrt((car(pa) - car(pb)) * (car(pa) - car(pb)) +
                    (cadr(pa) - cadr(pb)) * (cadr(pa) - cadr(pb)))
          best = min(best dd)
          prevB = curB))
      prevA = curA)
    best))
;-----------------------------------------------------------------------------
; 两线段中心线最短距离 (采样近似) / 同一折线内非相邻段最小距 (自重叠检查)
;-----------------------------------------------------------------------------
procedure(librfSegSegDist(p1 p2 p3 p4)
  let((best i n s pa dx dy l2 u q dd)
    best = 1.0e30
    n = 24
    dx = car(p4) - car(p3)
    dy = cadr(p4) - cadr(p3)
    l2 = dx * dx + dy * dy
    for(i 0 n
      s = i / 1.0 / n
      pa = list(car(p1) + (car(p2) - car(p1)) * s
                cadr(p1) + (cadr(p2) - cadr(p1)) * s)
      if(l2 < 1.0e-12
        then q = list(car(p3) cadr(p3))
        else
          u = ((car(pa) - car(p3)) * dx + (cadr(pa) - cadr(p3)) * dy) / l2
          u = max(0.0 min(1.0 u))
          q = list(car(p3) + dx * u cadr(p3) + dy * u))
      dd = sqrt((car(pa) - car(q)) * (car(pa) - car(q)) +
                (cadr(pa) - cadr(q)) * (cadr(pa) - cadr(q)))
      best = min(best dd))
    best))
procedure(librfPtsSelfGap(pts)
  let((n i j best dd)
    n = length(pts)
    best = 1.0e30
    for(i 0 n - 2
      for(j (i + 2) n - 2
        dd = librfSegSegDist(nth(i pts) nth(i + 1 pts) nth(j pts) nth(j + 1 pts))
        best = min(best dd)))
    best))
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
; 电感 ind: 方形/八角螺旋 + underpass + stub 端口 + MARKER 标记
; ct=0: p01=外端, p02=内端(underpass); ct=1: p01=外端, p02=内端(underpass), ct1=绕线中点抽头
;-----------------------------------------------------------------------------
procedure(librfBuildInd(cv req)
  let((shape n w s dout din ct under x0 y0 stubLen wUp pitch dinCalc ex ey indMark
        pts xIn yIn xOut yOut exitDir p0 p1 bodyBox exitXY gap
        tap tx ty tapDir tapUnder tapXY)
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
    if(ct == 0 || ct == nil
      then
        ;---- 单螺旋 + underpass
        pts = librfSpiralPts(shape n w s dout)
        pts = librfStretchPts(pts ex ey)
        pts = librfShiftPts(pts x0 y0)
        ; 外形自检 (生成期兜底): 中心线 bbox 必须与 dout/w/ex/ey 闭合, 中心在 (x0,y0)
        librfAssertSpiralBox(pts dout w ex ey x0 y0 "电感")
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
        ;---- 中心抽头 = 单螺旋 + 绕线中点抽头 (v09 修正)
        ; 【旧实现的问题】"半螺旋对"每半只在 y<=0 / y>=0 半平面内蛇形往返
        ;   (底边 + 45° 收径跳线), 不是螺旋: 电流在相邻底边上反向抵消,
        ;   实测包围面积 A - B = 0.00um^2 (同尺寸方形螺旋 -36719um^2),
        ;   画出来是曲折线而不是电感。单层金属在几何上也无法做出左右对称的
        ;   半螺旋(两半会在每个环的角点重合短路)。
        ; 现改为工程上可行的抽头螺旋: 整螺旋 + 绕线中点(弧长 50%)下穿抽头,
        ; 三端 p01(外端)/p02(内端)/ct1(中点), 抽头与内端分走 M4/M3 两层。
        pts = librfSpiralPts(shape n w s dout)
        pts = librfStretchPts(pts ex ey)
        pts = librfShiftPts(pts x0 y0)
        ; 外形自检 (生成期兜底): 中心线 bbox 必须与 dout/w/ex/ey 闭合, 中心在 (x0,y0)
        librfAssertSpiralBox(pts dout w ex ey x0 y0 "电感")
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) pts w)
        bodyBox = librfCvBBox(cv)
        ; p01 = 外端
        p0 = car(pts)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        ; p02 = 内端 underpass
        p0 = car(librfLastPt(pts))  p1 = cadr(librfLastPt(pts))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(pts w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gap = librfAssertPadGap(pts w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv p0 p1 xOut yOut exitDir wUp w under "p02" (stubLen + w / 2.0))
        ; ct1 = 中点抽头; 与 p02 强制分层 (under=route 时抽头走 direct, 反之走 route)
        tap = librfTapPoint(pts 0.5)
        tx = car(tap)  ty = cadr(tap)
        if(exitDir == 'S then tapDir = 'N
          else if(exitDir == 'N then tapDir = 'S
            else if(exitDir == 'W then tapDir = 'E
              else tapDir = 'W))
        tapUnder = if(under == "route" || under == "ROUTE" then "direct" else "route")
        tapXY = librfExitFromPts(pts w wUp x0 y0 tapDir)
        xOut = car(tapXY)  yOut = cadr(tapXY)
        gap = librfAssertPadGap(pts w xOut yOut (wUp / 2.0) tapDir 2.0)
        librfUnderpass(cv tx ty xOut yOut tapDir wUp w tapUnder "ct1" (stubLen + w / 2.0))
        librfWarn(sprintf(nil
          "中心抽头: ct1 = 绕线中点(弧长 50%%)抽头 @ (%.2f,%.2f), 走 %s 层; 属非对称抽头 (单层金属无法做左右对称半螺旋)"
          tx ty tapUnder)))
    ; MARKER 标记默认不画 (用户明确不要识别层); ind_mark:1 才画
    when(indMark != 0 && indMark != nil
      librfIndMark(cv bodyBox 1.0))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass")))))
;-----------------------------------------------------------------------------
; 传输线 tl: 微带 (M5 信号条 + 可选地平面 + V3 栅栏) 或 CPW (同层地)
;-----------------------------------------------------------------------------
procedure(librfBuildTl(cv req)
  let((w len gnd gndLayer fence cpw gap x0 y0 stubLen m gw fy i
      vc vs ve vhalf)
    vc = librfDrcViaNum("cut" 0.5)
    vs = librfDrcViaNum("spacing" 0.5)
    ve = librfDrcViaNum("enclosure" 0.2)
    vhalf = vc / 2.0 + ve
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
      then librfDrcParams(w 1.0e30 nil (w > 15.0))  ; 单根微带无邻线, 不参与间距判定
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
            if(gndLayer == "M3"
              then
                ; 两侧 V3 栅栏: M4 长条 + V3 阵列缝合到 M3 地
                for(i 0 1
                  fy = if(i == 0 then y0 - 2.5 else y0 + w + 2.5)
                  librfRect(cv librfMetalPair(librfLyr("underpass"))
                    x0 fy - vhalf x0 + len fy + vhalf)
                  librfViaArray(cv librfViaPair(librfLyr("via_un"))
                    x0 fy - vhalf x0 + len fy + vhalf vc vs ve ve))
              else
                librfWarn("fence=1 仅在 gnd_layer=M3 时画 V3 栅栏, 已跳过"))))
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
; 变压器 xfmr: stacked(上下叠层) / interleaved(径向交指) / balun(次级中点抽头)
; 端口: p01/p02=初级, p03/p04=次级; balun 另有 ct1(次级绕线中点抽头)
; 【v09 修正】旧实现三处硬伤:
;  1) stacked 初级内端直接在螺旋内端打 M4 过孔, 而次级 M4 整螺旋恰好压在该点上
;     (实测中心线间距 0.000um) -> 必然短路; 且 p02 走 M4 也无法穿出次级。
;  2) interleaved / balun 用 librfHalfSpiralPts 的"半螺旋"实为半平面蛇形
;     (底边+45°跳线往返), 正负包围面积完全抵消 (A-B=0.00um^2), 不是电感。
;  3) balun 的 p01(M4) 与 p03(M5) 落在同一角点, 端口/标签重叠。
; 现统一为: 双整螺旋分居 M5(初级) / M4(次级);
;   上层绕组内端先在自身层沿内孔引到中心, 再从中心 M5->M4->M3 下穿
;   (中心恒为下层绕组的内孔, 无金属, 不会短路);
;   下层绕组内端/抽头在自身层 M4->M3 引出;
;   三路 M3 走线取互不相同的方位并做同层间距断言。
;-----------------------------------------------------------------------------
; 折线路径 (与 librfUnderpass/librfUnderTm 内部规则一致, 供同层互查复用)
procedure(librfRoutePts(xIn yIn xOut yOut exitDir)
  let((r)
    when(abs(xIn - xOut) < 0.001 || abs(yIn - yOut) < 0.001
      r = list(list(xIn yIn) list(xOut yOut)))
    when(r == nil
      if(exitDir == 'W || exitDir == 'E
        then r = list(list(xIn yIn) list(xOut yIn) list(xOut yOut))
        else r = list(list(xIn yIn) list(xIn yOut) list(xOut yOut))))
    r))
;-----------------------------------------------------------------------------
; 三路 M3 引出方位选择 (纯几何, 可自测, 不依赖 db)
; 起点: ct1=抽头 (tx,ty) 绕组内部, p02=中心 (x0,y0), p04=次级内端 (pSx,pSy)
; 三路各自都是 L 形折线, 固定方位在 np>ns 等组合下会互相穿越 -> 同层 M3 短路,
; 故穷举 4x3x2 组互异方位取同层最小间距最大者; 达标(>= wUp+1.5)者一律合格,
; 同级时优先 ct1 走径向 (dirCT 给定).
; 返回 (dirCT dirP02 dirP04 minGap)
;-----------------------------------------------------------------------------
procedure(librfPickRoutes(allPts tx ty pSx pSy w wUp x0 y0 dirCT)
  let((needGap bestSc bestM bestCT best02 best04 dc d2 d4 m sc e r)
    needGap = wUp + 1.5
    bestSc = -1.0e30  bestM = 0.0
    bestCT = 'E  best02 = 'N  best04 = 'W
    foreach(dc list('E 'W 'S 'N)
      foreach(d2 list('E 'W 'S 'N)
        when(d2 != dc
          foreach(d4 list('E 'W 'S 'N)
            when(d4 != dc && d4 != d2
              r = librfRoutes3(allPts tx ty pSx pSy w wUp x0 y0 dc d2 d4)
              m = cadddr(r)   ; 第 4 项才是同层最小间距 (前 3 项是走线点表)
              sc = min(m (needGap - 1.0)) + if(dc == dirCT then 0.5 else 0.0)
              when(sc > bestSc
                bestSc = sc  bestM = m
                bestCT = dc  best02 = d2  best04 = d4))))))
    list(bestCT best02 best04 bestM)))
; 给定三方位, 返回 (rCT rP02 rP04 minGap)
procedure(librfRoutes3(allPts tx ty pSx pSy w wUp x0 y0 dc d2 d4)
  let((eCT e02 e04 rCT rP02 rP04)
    eCT = librfExitFromPts(allPts w wUp x0 y0 dc)
    e02 = librfExitFromPts(allPts w wUp x0 y0 d2)
    e04 = librfExitFromPts(allPts w wUp x0 y0 d4)
    rCT = librfRoutePts(tx ty car(eCT) cadr(eCT) dc)
    rP02 = librfRoutePts(x0 y0 car(e02) cadr(e02) d2)
    rP04 = librfRoutePts(pSx pSy car(e04) cadr(e04) d4)
    list(rCT rP02 rP04
         min(librfPtsMinDist(rCT rP02)
             min(librfPtsMinDist(rCT rP04) librfPtsMinDist(rP02 rP04))))))
procedure(librfBuildXfmr(cv req)
  let((style np ns w s dout gap ct under x0 y0 stubLen wUp ex ey indMark doutS apS
        ptsP ptsS p0 p1 xIn yIn xOut yOut exitDir bodyBox exitXY gapPad prevP
        leadPts tap tx ty dirCT dirP02 dirP04 rCT rP02 rP04 allPts pSx pSy
        needGap bestM pick eCT e02 e04 m)
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
    wUp = max(w 2.5)
    s = max(s gap)
    when(ct != 0 && ct != nil && style != "balun"
      librfWarn("xfmr: 仅 balun 有 ct1, 其余风格忽略 ct"))
    librfDrcParams(w s t (w > 15.0))
    cond(
      ;=======================================================================
      ; 叠层 / 交指: 初级 M5 整螺旋 + 次级 M4 整螺旋
      ; 交指 = 次级外径缩一个 pitch -> 两绕组径向错开半个 pitch, 交错排布
      ;=======================================================================
      ((style == "stacked" || style == "st" || style == "interleaved" || style == "il")
        doutS = dout
        when((style == "interleaved" || style == "il") && dout > (w + s) + 4.0 * w
          doutS = dout - (w + s))
        when((style == "interleaved" || style == "il") && doutS == dout
          librfWarn(sprintf(nil
            "交指: dout=%.1f 不足以缩一个 pitch %.1f, 退化为叠层 (两绕组不交错)"
            dout (w + s))))
        ptsP = librfShiftPts(librfStretchPts(librfSpiralPts("sq" np w s dout) ex ey) x0 y0)
        ptsS = librfShiftPts(librfRot180Pts(librfStretchPts(librfSpiralPts("sq" ns w s doutS) ex ey)) x0 y0)
        ; 外形自检: 初/次级各自 bbox=dout-w / doutS-w (rot180 不改 bbox), 中心在 (x0,y0)
        librfAssertSpiralBox(ptsP dout  w ex ey x0 y0 "变压器初级")
        librfAssertSpiralBox(ptsS doutS w ex ey x0 y0 "变压器次级")
        ; 中心下穿孔必须落在次级内孔里
        apS = librfAperture(librfStretchPts(librfSpiralPts("sq" ns w s doutS) ex ey) w)
        when(apS / 2.0 < wUp / 2.0 + 1.5
          librfErr(sprintf(nil
            "次级内孔 %.2f 太小: 初级中心下穿孔 (%.2f) 会压到次级金属, 请减 ns 或加大 dout"
            apS wUp)))
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsP w)
        librfPathSweep(cv librfMetalPair(librfLyr("underpass")) ptsS w)
        bodyBox = librfCvBBox(cv)
        allPts = append(ptsP ptsS)
        ; p02(W) 与 p04(E) 都在 M3: 先算两路中心线互查 (次级内端 y 接近 0 时会并线)
        e02 = librfExitFromPts(allPts w wUp x0 y0 'W)
        e04 = librfExitFromPts(allPts w wUp x0 y0 'E)
        rP02 = librfRoutePts(x0 y0 car(e02) cadr(e02) 'W)
        rP04 = librfRoutePts(car(librfLastPt(ptsS)) cadr(librfLastPt(ptsS))
                car(e04) cadr(e04) 'E)
        m = librfPtsMinDist(rP02 rP04)
        when(m < wUp + 1.5
          librfErr(sprintf(nil
            "stacked/interleaved: M3 两路引出线过近 (%.2f < %.2f), 请加大 dout 或错开 ns"
            m (wUp + 1.5))))
        ;---- p01 初级外端 (M5)
        p0 = car(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        ;---- p02 初级内端: M5 沿内孔引到中心 -> 中心下穿 -> 西出
        ;   引导线先走与末段垂直的方向(末段竖直->先横后竖), 才不会贴到内圈金属
        prevP = car(reverse(cdr(reverse(ptsP))))
        p0 = car(librfLastPt(ptsP))  p1 = cadr(librfLastPt(ptsP))
        if(abs(p0 - car(prevP)) > abs(p1 - cadr(prevP))
          then leadPts = list(list(p0 p1) list(p0 y0) list(x0 y0))
          else leadPts = list(list(p0 p1) list(x0 p1) list(x0 y0)))
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) leadPts w)
        xOut = car(e02)  yOut = cadr(e02)
        gapPad = librfAssertPadGap(allPts w xOut yOut (wUp / 2.0) 'W 2.0)
        librfUnderpass(cv x0 y0 xOut yOut 'W wUp w "route" "p02" (stubLen + w / 2.0))
        ;---- p03 次级外端 (M4)
        p0 = car(ptsS)
        librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p03")
        ;---- p04 次级内端: M4->M3 东出 -> 回 M4 端口
        p0 = car(librfLastPt(ptsS))  p1 = cadr(librfLastPt(ptsS))
        xOut = car(e04)  yOut = cadr(e04)
        gapPad = librfAssertPadGap(allPts w xOut yOut (wUp / 2.0) 'E 2.0)
        librfUnderTm(cv p0 p1 xOut yOut 'E wUp w "p04" (stubLen + w / 2.0))
        librfWarn("stacked/interleaved: 初级 M5 / 次级 M4; 内端分别从中心(W)与东侧经 M3 引出"))
      ;=======================================================================
      ; balun: 次级 M4 整螺旋(中点抽头) + 初级 M5 整螺旋
      ;=======================================================================
      ((style == "balun" || style == "bal")
        ptsS = librfShiftPts(librfRot180Pts(librfStretchPts(librfSpiralPts("sq" ns w s dout) ex ey)) x0 y0)
        ptsP = librfShiftPts(librfStretchPts(librfSpiralPts("sq" np w s dout) ex ey) x0 y0)
        ; 外形自检: 初/次级各自 bbox=dout-w, 中心在 (x0,y0)
        librfAssertSpiralBox(ptsP dout w ex ey x0 y0 "balun 初级")
        librfAssertSpiralBox(ptsS dout w ex ey x0 y0 "balun 次级")
        apS = librfAperture(librfStretchPts(librfSpiralPts("sq" ns w s dout) ex ey) w)
        when(apS / 2.0 < wUp / 2.0 + 1.5
          librfErr(sprintf(nil
            "balun 次级内孔 %.2f 太小: 初级中心下穿孔会压到次级金属, 请减 ns 或加大 dout" apS)))
        librfPathSweep(cv librfMetalPair(librfLyr("underpass")) ptsS w)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsP w)
        bodyBox = librfCvBBox(cv)
        ;---- 三路 M3 引出方位: 穷举互异方位, 取同层间距最大者 (见 librfPickRoutes)
        tap = librfTapPoint(ptsS 0.5)
        tx = car(tap)  ty = cadr(tap)
        allPts = append(ptsP ptsS)
        pSx = car(librfLastPt(ptsS))  pSy = cadr(librfLastPt(ptsS))
        dirCT = librfExitDir(tx - x0 ty - y0)
        needGap = wUp + 1.5
        pick = librfPickRoutes(allPts tx ty pSx pSy w wUp x0 y0 dirCT)
        dirCT = car(pick)  dirP02 = cadr(pick)  dirP04 = caddr(pick)
        bestM = cadddr(pick)
        when(bestM < needGap
          librfErr(sprintf(nil
            "balun: M3 三路引出线过近 (最佳 %.2f < %.2f, 方位 %L/%L/%L), 请调 ns/np 或 dout"
            bestM needGap dirCT dirP02 dirP04)))
        ;---- p03 次级外端 (M4)
        p0 = car(ptsS)
        librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p03")
        ;---- p04 次级内端 (M4 -> M3)
        exitXY = librfExitFromPts(append(ptsP ptsS) w wUp x0 y0 dirP04)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(append(ptsP ptsS) w xOut yOut (wUp / 2.0) dirP04 2.0)
        librfUnderTm(cv car(librfLastPt(ptsS)) cadr(librfLastPt(ptsS))
          xOut yOut dirP04 wUp w "p04" (stubLen + w / 2.0))
        ;---- ct1 次级绕线中点抽头 (M4 -> M3)
        exitXY = librfExitFromPts(append(ptsP ptsS) w wUp x0 y0 dirCT)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(append(ptsP ptsS) w xOut yOut (wUp / 2.0) dirCT 2.0)
        librfUnderTm(cv tx ty xOut yOut dirCT wUp w "ct1" (stubLen + w / 2.0))
        ;---- p01 初级外端 (M5)
        p0 = car(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")
        ;---- p02 初级内端: M5 沿内孔引到中心 -> 中心下穿 -> 出 dirP02
        prevP = car(reverse(cdr(reverse(ptsP))))
        p0 = car(librfLastPt(ptsP))  p1 = cadr(librfLastPt(ptsP))
        if(abs(p0 - car(prevP)) > abs(p1 - cadr(prevP))
          then leadPts = list(list(p0 p1) list(p0 y0) list(x0 y0))
          else leadPts = list(list(p0 p1) list(x0 p1) list(x0 y0)))
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) leadPts w)
        exitXY = librfExitFromPts(append(ptsP ptsS) w wUp x0 y0 dirP02)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(append(ptsP ptsS) w xOut yOut (wUp / 2.0) dirP02 2.0)
        librfUnderpass(cv x0 y0 xOut yOut dirP02 wUp w "route" "p02" (stubLen + w / 2.0))
        librfWarn(sprintf(nil
          "balun: 次级 M4 整螺旋 (p03/p04/ct1 在 M4TXT, ct1=绕线中点), 初级 M5 整螺旋 (p01/p02)")))
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
          (typ == "ind"              lriDispatch(cv req))
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
    ;---- 4) din 闭合式 (从参数实算): 匝数向上取整 -> n=2.5 按 3 圈, 孔径 30
    ;   (原断言 42.5 是按 n=2.5 直接代入, 与实画孔径不符, v09 修正为 30)
    ok = abs(librfDinFromParams(2.5 10.0 2.5 100.0) - 30.0) < 0.002
    if(ok
      then progn(nPass = nPass + 1  printf("PASS din(2.5)=30.000 (ceil(n)=3)\n"))
      else progn(nFail = nFail + 1
             printf("FAIL din(2.5)=%.3f (expect 30.000 = dout-2(3*pitch-s))\n"
               librfDinFromParams(2.5 10.0 2.5 100.0))))
    ok = abs(librfDinFromParams(2.0 10.0 2.5 100.0) - 55.0) < 0.002
    if(ok
      then progn(nPass = nPass + 1  printf("PASS din(2.0)=55.000 (整数匝不变)\n"))
      else progn(nFail = nFail + 1
             printf("FAIL din(2.0)=%.3f (expect 55.000)\n"
               librfDinFromParams(2.0 10.0 2.5 100.0))))
    ;---- 5) 八角螺旋: 黄金断言 (bbox=dout, 中心在原点, 平行段恒距 pitch, 无自重叠)
    pts = librfOctSpiralPts(2.5 10.0 2.5 150.0)
    nSeg = length(pts) - 1
    if(nSeg == 20
      then progn(nPass = nPass + 1  printf("PASS oct n=2.5: nSeg=20\n"))
      else progn(nFail = nFail + 1
             printf("FAIL oct n=2.5: nSeg=%d (expect 20)\n" nSeg)))
    bb = librfPtsBBox(pts)
    ok = abs((caddr(bb) - car(bb)) + 10.0 - 150.0) < 0.002 &&
         abs((cadddr(bb) - cadr(bb)) + 10.0 - 150.0) < 0.002 &&
         abs((car(bb) + caddr(bb)) / 2.0) < 0.002 &&
         abs((cadr(bb) + cadddr(bb)) / 2.0) < 0.002
    if(ok
      then progn(nPass = nPass + 1
             printf("PASS oct bbox=150x150 且中心在原点 (metal %.3fx%.3f)\n"
               (caddr(bb) - car(bb)) + 10.0 (cadddr(bb) - cadr(bb)) + 10.0))
      else progn(nFail = nFail + 1
             printf("FAIL oct bbox: centerline %.3fx%.3f 中心 (%.3f,%.3f) (expect 150x150 @0,0)\n"
               caddr(bb) - car(bb) cadddr(bb) - cadr(bb)
               (car(bb) + caddr(bb)) / 2.0 (cadr(bb) + cadddr(bb)) / 2.0)))
    ; 自重叠: 非相邻段中心距必须 >= w (原 fix(k/4) 时 n=2.5 只有 8.86 < 10)
    let((sg)
      sg = librfPtsSelfGap(librfOctSpiralPts(2.5 10.0 2.6 150.0))
      if(sg + 0.002 >= 10.0
        then progn(nPass = nPass + 1
               printf("PASS oct 无自重叠: 非相邻段最小中心距 %.3f >= w=10\n" sg))
        else progn(nFail = nFail + 1
               printf("FAIL oct 自重叠: 非相邻段最小中心距 %.3f < w=10\n" sg)))
      sg = librfPtsSelfGap(librfOctSpiralPts(4.0 10.0 2.6 200.0))
      if(sg + 0.002 >= 10.0
        then progn(nPass = nPass + 1
               printf("PASS oct n=4 无自重叠: %.3f >= w=10\n" sg))
        else progn(nFail = nFail + 1
               printf("FAIL oct n=4 自重叠: %.3f < w=10\n" sg))))
    ;---- 5b) 八角与方形同参数孔径一致 (钉住 P0-1 内步长: 多缩一个 pitch 即露馅)
    let((a1 a2 a3 s1 s2 s3)
      a1 = librfAperture(librfOctSpiralPts(1.0 10.0 2.5 100.0) 10.0)
      s1 = librfAperture(librfSquareSpiralPts(1.0 10.0 2.5 100.0) 10.0)
      a2 = librfAperture(librfOctSpiralPts(2.0 10.0 2.5 100.0) 10.0)
      s2 = librfAperture(librfSquareSpiralPts(2.0 10.0 2.5 100.0) 10.0)
      a3 = librfAperture(librfOctSpiralPts(2.5 10.0 2.5 150.0) 10.0)
      s3 = librfAperture(librfSquareSpiralPts(2.5 10.0 2.5 150.0) 10.0)
      if(abs(a1 - s1) < 0.002 && abs(a2 - s2) < 0.002 && abs(a3 - s3) < 0.002
          && abs(a1 - 80.0) < 0.002 && abs(a2 - 55.0) < 0.002
          && abs(a3 - 80.0) < 0.002
        then progn(nPass = nPass + 1
               printf("PASS oct/sq 孔径一致且为 80/55/80: %.3f/%.3f %.3f/%.3f %.3f/%.3f\n"
                 a1 s1 a2 s2 a3 s3))
        else progn(nFail = nFail + 1
               printf("FAIL oct/sq 孔径: n=1 %.3f/%.3f n=2 %.3f/%.3f n=2.5 %.3f/%.3f (expect 80/55/80)\n"
                 a1 s1 a2 s2 a3 s3))))
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
    ;---- 6) 抽头点 (CT/balun 中点抽头): 弧长 50% 必落在中心线上
    let((tp sum dd prev cur)
      pts = librfSquareSpiralPts(2.5 10.0 2.5 100.0)
      tp = librfTapPoint(pts 0.5)
      sum = 0.0
      prev = car(pts)
      foreach(cur cdr(pts)
        sum = sum + sqrt((car(cur) - car(prev)) * (car(cur) - car(prev)) +
                         (cadr(cur) - cadr(prev)) * (cadr(cur) - cadr(prev)))
        prev = cur)
      ; 抽头点到中心线的最短距离必须为 0 (点在线上)
      dd = 1.0e30  prev = car(pts)
      foreach(cur cdr(pts)
        dd = min(dd librfSegSegDist(tp tp prev cur))
        prev = cur)
      if(dd < 0.01 && sum > 0.0
        then progn(nPass = nPass + 1
               printf("PASS tapPoint 50%%: (%.3f,%.3f) 落在线中心, 总长 %.3f\n"
                 car(tp) cadr(tp) sum))
        else progn(nFail = nFail + 1
               printf("FAIL tapPoint 50%%: (%.3f,%.3f) 距中心线 %.3f (expect 0)\n"
                 car(tp) cadr(tp) dd))))
    ;---- 7) 变压器三路 M3 方位选择 (balun 预设参数), 纯几何可自测
    ;   np=2 ns=2 w=10 s=2.6 dout=180: 抽头/中心/次级内端三路 L 形走线互异且不打架
    let((ptsP ptsS allp tp pick mgap)
      ptsP = librfSquareSpiralPts(2.0 10.0 2.6 180.0)
      ptsS = librfRot180Pts(librfSquareSpiralPts(2.0 10.0 2.6 180.0))
      allp = append(ptsP ptsS)
      tp = librfTapPoint(ptsS 0.5)
      pick = librfPickRoutes(allp car(tp) cadr(tp)
               car(librfLastPt(ptsS)) cadr(librfLastPt(ptsS))
               10.0 10.0 0.0 0.0 librfExitDir(car(tp) cadr(tp)))
      mgap = cadddr(pick)
      if(mgap + 0.001 >= 11.5 && car(pick) != cadr(pick) &&
         cadr(pick) != caddr(pick) && car(pick) != caddr(pick)
        then progn(nPass = nPass + 1
               printf("PASS balun 三路方位互异, 最小同层间距 %.2f >= 11.50\n" mgap))
        else progn(nFail = nFail + 1
               printf("FAIL balun 三路方位: 最小同层间距 %.2f (expect >= 11.50 且三向互异)\n"
                 mgap))))
    ;---- 8) via 阵列行列数 (走线宽 2.5 → 1 列; 5.5 → 2 列)
    printf("via cols: w=2.5 -> %d (expect 1); w=5.5 -> %d (expect 2)\n"
      max(1 fix(((2.5 - 1.0) + 1.5) / 3.0))
      max(1 fix(((5.5 - 1.0) + 1.5) / 3.0)))
    ;---- 9) via 规则查表: drc.via.{cut,spacing,enclosure} 由 driver 注入, 缺项回落
    let((save got)
      save = librfDrc
      librfDrc = list(list("min_w" 1.0)
                      list("via" list(list("cut" 0.8)
                                      list("enclosure" 0.3)
                                      list("spacing" 0.9))))
      got = list(librfDrcViaNum("cut" 99.0)
                 librfDrcViaNum("spacing" 99.0)
                 librfDrcViaNum("enclosure" 99.0)
                 librfDrcViaNum("missing" 7.5))
      if(got == list(0.8 0.9 0.3 7.5)
        then progn(nPass = nPass + 1
               printf("PASS via 规则注入: cut/spacing/enclosure=%.2f/%.2f/%.2f, 缺项回落 7.5\n"
                 car(got) cadr(got) caddr(got)))
        else progn(nFail = nFail + 1
               printf("FAIL via 规则注入: got %L (expect (0.8 0.9 0.3 7.5))\n" got)))
      librfDrc = save
      got = librfDrcViaNum("cut" 0.5)
      if(boundp('librfDrc) && librfDrc
        then progn(nPass = nPass + 1 printf("PASS via 规则恢复: 外部 librfDrc 未被测试污染\n"))
        else
          if(abs(got - 0.5) < 1.0e-6
            then progn(nPass = nPass + 1 printf("PASS via 规则缺省: 无 librfDrc 时 cut=0.5\n"))
            else progn(nFail = nFail + 1
                   printf("FAIL via 规则缺省: %.3f (expect 0.5)\n" got)))))
    ;---- 10) DRC 基础阈值: drc.{min_w,max_w,min_s} 由 driver 注入, 缺项回落 1.0/30/1.0
    let((save sErr sOk r1 r2 r3 r4)
      save = librfDrc
      sErr = librf__errs  sOk = librf__ok
      ; (a) 注入阈值必须生效
      librfDrc = list(list("min_w" 4.0) list("max_w" 20.0) list("min_s" 2.0))
      r1 = librfDrcParams(3.0 2.5 nil nil)   ; 3.0 < min_w=4.0 -> nil
      r2 = librfDrcParams(5.0 2.5 nil nil)   ; 4.0<=5.0<=20, 2.5>=2.0 -> t
      r3 = librfDrcParams(21.0 2.5 nil nil)  ; 21.0 > max_w=20 -> nil
      ; (b) 缺项回落: 无 librfDrc 时 3.0/2.0 按 1.0/30/1.0 通过
      librfDrc = nil
      librf__errs = sErr  librf__ok = sOk   ; 丢掉 (a) 故意触发的前两条 ERR
      r4 = librfDrcParams(3.0 2.0 nil nil)
      librfDrc = save
      librf__errs = sErr  librf__ok = sOk
      if(r1 == nil && r2 && r3 == nil
        then progn(nPass = nPass + 1
               printf("PASS DRC 阈值注入: min_w=4/max_w=20 生效 (3.0->nil, 5.0->t, 21.0->nil)\n"))
        else progn(nFail = nFail + 1
               printf("FAIL DRC 阈值注入: %L/%L/%L (expect nil/t/nil)\n" r1 r2 r3)))
      if(r4
        then progn(nPass = nPass + 1
               printf("PASS DRC 缺省阈值: 无 librfDrc 时 3.0/2.0 通过 (1.0/30/1.0)\n"))
        else progn(nFail = nFail + 1
               printf("FAIL DRC 缺省阈值: 3.0/2.0 被拒\n"))))
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

;===== lri engine (inductor v2) =====
;=============================================================================
; librf_ind2.il  --  电感版图引擎 (替换 librfBuildInd / 变压器电感部分)
;
; 内核：ring0 = 带 45° 切角的矩形；ring k = ring0 的精确内缩 offset(k*pitch)
;       => 间距恒 = s，bbox 恒 = W x H，段角恒 ∈ {0,45,90}
;
; 拓扑：kind="se"   单端
;       kind="diff" 差分（两臂嵌套，原点下层桥接）
;       kind="f8"   八字形（两镜像环，中心桥接）
;       ct=1        加中心抽头 ct1
;       shape="rect" 矩形（W≠H，拐角仍 45°）
;       shape="oct"  正八角（corner 自动 (2-√2)·min(a,b)）
;
; 依赖（沿用原工程）：librfRect librfPathSweep librfStubPort librfViaArray
;   librfLyr librfMetalPair librfTxtPair librfNotePort librfWarn librfErr
;   librfReqNum librfReqStr librfDrcParams librfCvBBox
;=============================================================================

defvar(lriReport nil)
defvar(lriStepList nil)      ; 跨过程状态：lriSpiralBottom 产出的圈间步进段

;------------------------------------------------------------------ 基础
procedure(lriSegLen(p q)
  sqrt((car(q)-car(p))*(car(q)-car(p)) + (cadr(q)-cadr(p))*(cadr(q)-cadr(p)))
)
procedure(lriLast(lst) car(reverse(lst)))
procedure(lriDedup(pts)
  let((out)
    out = nil
    foreach(p pts
      when(out == nil || lriSegLen(lriLast(out) p) > 1.0e-6
        out = append(out list(p))))
    out)
)
procedure(lriPLen(pts)
  let((s)
    s = 0.0
    for(i 1 length(pts)-1  s = s + lriSegLen(nth(i-1 pts) nth(i pts)))
    s
  )
)
procedure(lriShift(pts dx dy)
  let((out) out=nil
    foreach(p pts out = cons(list(car(p)+dx cadr(p)+dy) out))
    reverse(out)))
procedure(lriRot180(pts)
  let((out) out=nil
    foreach(p pts out = cons(list(0.0-car(p) 0.0-cadr(p)) out))
    reverse(out)))
procedure(lriMirrorX(pts)
  let((out) out=nil
    foreach(p pts out = cons(list(0.0-car(p) cadr(p)) out))
    reverse(out)))
procedure(lriMirrorY(pts)
  let((out) out=nil
    foreach(p pts out = cons(list(car(p) 0.0-cadr(p)) out))
    reverse(out)))
procedure(lriMirrorStepsY(steps)
  let((out s)
    out = nil
    foreach(s steps
      out = cons(list(list(car(car(s)) 0.0-cadr(car(s)))
                      list(car(cadr(s)) 0.0-cadr(cadr(s)))) out))
    reverse(out)))

; 所有段必须是 0/45/90
procedure(lriAngleOK(pts)
  let((dx dy ok)
    ok = t
    for(i 1 length(pts)-1
      dx = car(nth(i pts)) - car(nth(i-1 pts))
      dy = cadr(nth(i pts)) - cadr(nth(i-1 pts))
      when(lriSegLen(nth(i-1 pts) nth(i pts)) > 1.0e-9
        unless(abs(dx) < 1.0e-6 || abs(dy) < 1.0e-6
               || abs(abs(dx) - abs(dy)) < 1.0e-6
          ok = nil)))
    ok))

procedure(lriInterX(p1 p2 q1 q2)
  let((d1x d1y d2x d2y det rx ry tt)
    d1x = car(p2)-car(p1)  d1y = cadr(p2)-cadr(p1)
    d2x = car(q2)-car(q1)  d2y = cadr(q2)-cadr(q1)
    det = d1x*(0.0-d2y) - d1y*(0.0-d2x)
    if(abs(det) < 1.0e-12  then nil
      else
        rx = car(q1)-car(p1)  ry = cadr(q1)-cadr(p1)
        tt = (rx*(0.0-d2y) - ry*(0.0-d2x))/det
        list(car(p1)+tt*d1x cadr(p1)+tt*d1y))))

;------------------------------------------------------------------ ring0
; a,b = 中心线半宽/半高, c = 45° 切角腿长, seam = "bottom"/"top"/"left"/"right"
procedure(lriRing(a b c seam)
  let((r)
    ; 旧版 0.49*min 会把 (2-sqrt(2))=0.586 的标准 45° 切角截短，
    ; 使各圈切角边不再平行等距（连接点会跑出边外）。这里只防退化。
    c = max(0.0 min(c 0.999*min(a b)))
    cond(
      ((seam == "top")
        r = list(list(0.0 b) list(c-a b) list(-a b-c) list(-a c-b)
                 list(c-a -b) list(a-c -b) list(a c-b) list(a b-c)
                 list(a-c b)))
      ((seam == "left")
        r = list(list(-a 0.0) list(-a c-b) list(c-a -b) list(a-c -b)
                 list(a c-b) list(a b-c) list(a-c b) list(c-a b)
                 list(-a b-c)))
      ((seam == "right")
        r = list(list(a 0.0) list(a b-c) list(a-c b) list(c-a b)
                 list(-a b-c) list(-a c-b) list(c-a -b) list(a-c -b)
                 list(a c-b)))
      (t
        r = list(list(0.0 -b) list(a-c -b) list(a c-b) list(a b-c)
                 list(a-c b) list(c-a b) list(-a b-c) list(-a c-b)
                 list(c-a -b))))
    lriDedup(r)))

;------------------------------------------------------------------ offset
procedure(lriOffset(poly d)
  let((n out p0 p1 p2 d1x d1y d2x d2y L1 L2 n1x n1y n2x n2y q)
    n = length(poly)
    out = nil
    for(i 0 n-1
      p0 = nth(mod(i+n-1 n) poly)  p1 = nth(i poly)
      p2 = nth(mod(i+1 n) poly)
      d1x = car(p1)-car(p0)  d1y = cadr(p1)-cadr(p0)
      L1 = sqrt(d1x*d1x + d1y*d1y)
      d2x = car(p2)-car(p1)  d2y = cadr(p2)-cadr(p1)
      L2 = sqrt(d2x*d2x + d2y*d2y)
      n1x = (0.0-d1y)/L1  n1y = d1x/L1
      n2x = (0.0-d2y)/L2  n2y = d2x/L2
      q = lriInterX(
            list(car(p0)+d*n1x cadr(p0)+d*n1y)
            list(car(p1)+d*n1x cadr(p1)+d*n1y)
            list(car(p1)+d*n2x cadr(p1)+d*n2y)
            list(car(p2)+d*n2x cadr(p2)+d*n2y))
      unless(q  q = list(car(p1)+d*n1x cadr(p1)+d*n1y))
      out = cons(q out))
    reverse(out)))

;------------------------------------------------------------------ ring 族
; 自相似切角 => 每圈都是 ring0 的精确 offset => 间距恒 = pitch
procedure(lriRings(W H w s n corner seam pitch)
  let((p a0 b0 ratio nr rs k a b stop)
    ; pitch 覆盖: diff 交错双臂需要 2*(w+s) 的环间距;
    ; 旧实现忽略 pitchOv, 导致 B 臂与 A 臂落在相同半径上 (短路/重叠).
    p = if(pitch then pitch else w + s)
    a0 = (W - w)/2.0  b0 = (H - w)/2.0
    ratio = if(min(a0 b0) > 0.0 then corner/min(a0 b0) else 0.0)
    nr = fix(round(n)) + 2
    rs = list(lriRing(a0 b0 ratio*min(a0 b0) seam))
    stop = nil
    for(k 1 nr-1
      unless(stop
        a = a0 - k*p  b = b0 - k*p
        if(min(a b) <= w/2.0
          then
            progn(stop = t
                  ; 只有确实容纳不下请求匝数时才告警；nr 本就多预留 2 圈，
                  ; 多预留的那几圈无法生成是正常现象。
                  when(length(rs) < fix(n + 1.0e-7) + 1
                    librfWarn(sprintf(nil "lri: 内孔已闭合, 最多 %.2f 圈 < 请求 %.2f 圈" (length(rs)-1)*1.0 (1.0 * n)))))
          else rs = cons(lriRing(a b ratio*min(a b) seam) rs))))
    list(reverse(rs) p)))

procedure(lriCutRing(r frac)
  prog((tot want acc out a b L)
    when(frac >= 0.9999  return(append(r list(car(r)))))
    tot = lriPLen(append(r list(car(r))))
    want = tot*frac
    acc = 0.0
    out = list(car(r))
    for(i 0 length(r)-1
      a = nth(i r)
      b = if(i+1 < length(r) then nth(i+1 r) else car(r))
      L = lriSegLen(a b)
      when(L < 1.0e-9  return(out))
      if(acc + L >= want - 1.0e-9
        then return(append(out list(list(
               car(a)+(car(b)-car(a))*(want-acc)/L
               cadr(a)+(cadr(b)-cadr(a))*(want-acc)/L))))
        else out = append(out list(b)))
      acc = acc + L)
    return(out)))

;------------------------------------------------------------------ 螺旋
; 单位方向
procedure(lriUnit(a b)
  let((dx dy L)
    dx = car(b)-car(a)  dy = cadr(b)-cadr(a)
    L = sqrt(dx*dx + dy*dy)
    if(L < 1.0e-12 then list(0.0 0.0) else list(dx/L dy/L))))

; 开放折线按弧长比例截断（不做闭合，不回起点）
procedure(lriCutOpen(pts frac)
  prog((tot want acc out a b L)
    when(frac >= 0.9999  return(pts))
    tot = lriPLen(pts)
    when(tot < 1.0e-9  return(pts))
    want = tot*frac
    acc = 0.0
    out = list(car(pts))
    for(i 0 length(pts)-2
      a = nth(i pts)  b = nth(i+1 pts)
      L = lriSegLen(a b)
      ; 重合点直接跳过，不能因此丢掉整段剩余路径
      when(L >= 1.0e-9
        if(acc + L >= want - 1.0e-9
          then return(append(out list(list(
                 car(a)+(car(b)-car(a))*(want-acc)/L
                 cadr(a)+(cadr(b)-cadr(a))*(want-acc)/L))))
          else progn(
            out = append(out list(b))
            acc = acc + L))))
    return(out)))

; 列表循环移位：从索引 i 起取一周（nth 0 基）
procedure(lriRotList(l i)
  let((n k out j)
    n = length(l)
    k = mod(mod(i n) + n n)
    out = nil
    for(j 0 n-1
      out = append(out list(nth(mod(k+j n) l))))
    out))

; 取列表前 k 个元素
procedure(lriTake(l k)
  let((out j)
    out = nil
    for(j 0 min(k length(l))-1
      out = append(out list(nth(j l))))
    out))

; 精确 90/180/270 旋转（避免 cos/sin 误差破坏 0/45/90 角度）
procedure(lriRotExact(pts ang)
  let((out p x y)
    out = nil
    foreach(p pts
      x = car(p)  y = cadr(p)
      cond(
        ((ang == 90)  out = cons(list(0.0-y x) out))
        ((ang == 180) out = cons(list(0.0-x 0.0-y) out))
        ((ang == 270) out = cons(list(y 0.0-x) out))
        (t out = cons(p out))))
    reverse(out)))

; seam=bottom 环的左竖直边（CCW 向下）索引
procedure(lriStepEdge(r)
  prog((m a b)
    m = length(r)
    for(i 0 m-1
      a = nth(i r)
      b = nth(mod(i+1 m) r)
      when(abs(car(b)-car(a)) < 1.0e-9 && abs(cadr(b)-cadr(a)) > 1.0e-9
           && car(a) < 0.0 && cadr(b) < cadr(a)
        return(i)))
    librfErr("lri: 环上没有可用的左侧竖直边，无法构造螺旋")))

; 第 k 圈左竖直边上高度 y 处的点；越界直接报错（提示减小 n）
procedure(lriOnLeft(rs i k y)
  prog((r a b tt)
    r = nth(k rs)
    a = nth(i r)
    b = nth(mod(i+1 length(r)) r)
    tt = (y - cadr(a))/(cadr(b) - cadr(a))
    when(tt < -1.0e-9 || tt > 1.0 + 1.0e-9
      librfErr(sprintf(nil
        "lri: 第 %d 圈左侧边长度不足，无法容纳螺旋台阶 (y=%.3f, 边范围 %.3f..%.3f)；请减小 n 或 s/w"
        k y min(cadr(a) cadr(b)) max(cadr(a) cadr(b)))))
    return(list(car(a) y))))

; seam=bottom 的标准螺旋：单一连续路径，无自交。
; 每圈从左侧竖直边上的 L 出发，向下绕整圈后回到该边上的 D 结束，
; D 比 L 高一个 pitch；再沿内法向(+x)平移 pitch 到下一圈的 L。
; 同圈起止端缺口 = pitch => 金属净距 s；相邻过渡段相距 pitch，不重叠。
procedure(lriSpiralBottom(W H w s n corner pitchOv)
  prog((rp rs p r0 i m bIdx nfull frac L1 D0 Lk pts r rot dk nxt
        closed part k)
    ; 圈间“径向步进”段 (dk->nxt)。单臂螺旋里它与下一圈边共线，无影响；
    ; 交错双臂 (diff) 时这段会横穿另一臂 -> 必须能识别出来改走下穿层。
    lriStepList = nil
    rp = lriRings(W H w s n corner "bottom" pitchOv)
    rs = car(rp)
    p = cadr(rp)
    r0 = car(rs)
    i = lriStepEdge(r0)
    m = length(r0)
    bIdx = mod(i+1 m)
    nfull = fix(n + 1.0e-7)
    frac = n - nfull
    if(nfull == 0
      then
        closed = append(lriRotList(r0 0) list(car(r0)))
        return(list(lriDedup(lriCutOpen(closed frac)) p)))
    unless(length(rs) >= 2
      librfErr("lri: 内孔已闭合，无法构造螺旋；请减小 n 或 s/w"))
    when(nfull > length(rs)-1
      librfErr(sprintf(nil "lri: 内孔已闭合，只能容纳 %.2f 圈 < 请求 %.2f 圈；请减小 n 或 s/w" (length(rs)-1)*1.0 (1.0 * n))))
    L1 = nth(bIdx nth(1 rs))
    D0 = lriOnLeft(rs i 0 cadr(L1))
    pts = append(lriTake(lriRotList(r0 0) i+1) list(D0))
    ; 首圈->第二圈的径向步进：先补上“第二圈左边缘”的中间点，
    ; 才能把径向步进单独切出来（否则它与第二圈底边共线、混成一段）。
    let((mid0)
      mid0 = list(car(nth(i nth(1 rs))) cadr(D0))
      when(lriSegLen(D0 mid0) > 1.0e-6
        lriStepList = cons(list(D0 mid0) lriStepList)
        pts = append(pts list(mid0))))
    Lk = L1
    pts = append(pts list(Lk))
    for(k 1 nfull-1
      r = nth(k rs)
      rot = lriRotList(r bIdx)
      pts = append(pts list(nth(bIdx r)))
      pts = append(pts cdr(rot))
      when(k+1 < length(rs)
        dk = lriOnLeft(rs i k cadr(Lk)+p)
        pts = append(pts list(dk))
        when(k < nfull-1 || frac > 1.0e-9
          nxt = list(car(nth(i nth(k+1 rs))) cadr(dk))
          lriStepList = cons(list(dk nxt) lriStepList)
          pts = append(pts list(nxt))
          Lk = nxt)))
    when(frac > 1.0e-9 && nfull < length(rs)
      r = nth(nfull rs)
      closed = lriDedup(append(cons(Lk lriRotList(r bIdx)) list(Lk)))
      part = lriCutOpen(closed frac)
      pts = append(pts cdr(part)))
    lriStepList = reverse(lriStepList)
    return(list(lriDedup(pts) p))))

; 点集/步进段按角度精确旋转
procedure(lriSamePt(a b)
  (abs(car(a)-car(b)) < 1.0e-6 && abs(cadr(a)-cadr(b)) < 1.0e-6))

procedure(lriRotPt(p ang)
  car(lriRotExact(list(p) ang)))

procedure(lriRotSteps(steps ang)
  let((out s)
    out = nil
    foreach(s steps
      out = cons(list(lriRotPt(car(s) ang) lriRotPt(cadr(s) ang)) out))
    reverse(out)))

; 连续螺旋（无自交），seam 只决定端口边：统一用 bottom 版本生成再精确旋转。
; 同时把圈间步进段随点集一起旋转，供 diff 的下穿处理使用。
procedure(lriSpiral(W H w s n corner seam pitchOv)
  let((q pts st)
    cond(
      ((seam == "top")
        q = lriSpiralBottom(W H w s n corner pitchOv)
        pts = lriRotExact(car(q) 180)
        st = lriRotSteps(lriStepList 180))
      ((seam == "right")
        q = lriSpiralBottom(H W w s n corner pitchOv)
        pts = lriRotExact(car(q) 90)
        st = lriRotSteps(lriStepList 90))
      ((seam == "left")
        q = lriSpiralBottom(H W w s n corner pitchOv)
        pts = lriRotExact(car(q) 270)
        st = lriRotSteps(lriStepList 270))
      (t
        q = lriSpiralBottom(W H w s n corner pitchOv)
        pts = car(q)
        st = lriStepList))
    lriStepList = st
    pts))

; 弧长比例取点
procedure(lriPointAt(pts frac)
  prog((tot want acc L fr)
    tot = lriPLen(pts)  want = tot*frac  acc = 0.0
    for(i 1 length(pts)-1
      L = lriSegLen(nth(i-1 pts) nth(i pts))
      when(acc + L >= want - 1.0e-9
        fr = if(L > 0.0 then (want-acc)/L else 0.0)
        return(list(car(nth(i-1 pts))+(car(nth(i pts))-car(nth(i-1 pts)))*fr
                    cadr(nth(i-1 pts))+(cadr(nth(i pts))-cadr(nth(i-1 pts)))*fr)))
      acc = acc + L)
    return(lriLast(pts))))

;------------------------------------------------------------------ 绘制
procedure(lriDraw(cv pts w)
  when(length(pts) >= 2
    librfPathSweep(cv librfMetalPair(librfLyr("spiral")) pts w)))

procedure(lriDrawEx(cv pts w capStart capEnd)
  when(length(pts) >= 2
    librfPathSweepEx(cv librfMetalPair(librfLyr("spiral"))
      pts w capStart capEnd)))

; 带下穿步进的画法：steps 里的段改走 M4（underpass）并两端打孔。
; 单臂螺旋里圈间步进与下一圈边共线，放哪层都一样；但 diff 的两臂
; 交错时，径向步进会横穿另一臂，同层画就是短路。
procedure(lriDrawSteps(cv pts w steps)
  let((run a b isStep i s)
    run = list(car(pts))
    for(i 1 length(pts)-1
      a = nth(i-1 pts)
      b = nth(i pts)
      isStep = nil
      foreach(s steps
        when(lriSamePt(a car(s)) && lriSamePt(b cadr(s))
          isStep = t))
      if(isStep
        then
          when(length(run) >= 2  lriDraw(cv run w))
          lriBridge(cv list(a b) w)
          run = list(b)
        else
          run = append(run list(b))))
    when(length(run) >= 2  lriDraw(cv run w))))

; 四个正交方向的端口
procedure(lriPortDir(cv x y dir w len name)
  let((m tl)
    m  = librfMetalPair(librfLyr("spiral"))
    tl = librfTxtPair(librfLyr("spiral"))
    librfStubPort(cv m tl x y dir w len name)))

; 任意方向端口（中心抽头用）：沿 (dx,dy) 画短走线 + label
procedure(lriPortVec(cv x y dx dy w len name)
  let((L nx ny ex ey m tl lx ly off)
    L = sqrt(dx*dx + dy*dy)
    when(L < 1.0e-9  L = 1.0)
    nx = dx/L  ny = dy/L
    ex = x + nx*len  ey = y + ny*len
    m  = librfMetalPair(librfLyr("spiral"))
    tl = librfTxtPair(librfLyr("spiral"))
    librfPathSweep(cv m list(list(x y) list(ex ey)) w)
    ; librfPathSweep 在端点加 w x w 端帽, 金属真实尖端还要再外伸 w/2.
    ; EMX label-snap 要求标签贴近金属外缘: 放在尖端内侧 1.5um,
    ; 放在端帽中心会距边 5um, EMX 报 label not close to any edge.
    off = max(0.0 w/2.0 - 1.5)
    lx = ex + nx*off  ly = ey + ny*off
    librfPortPin(cv car(m) car(tl) lx ly name)
    librfNotePort(name lx ly car(tl))))

; 下穿/桥接：underpass 层走线 + 两端 landing + via
procedure(lriBridge(cv pts w)
  let((mU mS cut x1 y1 x2 y2 p vc vs ve)
    mU  = librfMetalPair(librfLyr("underpass"))
    mS  = librfMetalPair(librfLyr("spiral"))
    cut = librfViaPair(librfLyr("via_sp"))
    vc = librfDrcViaNum("cut" 0.5)
    vs = librfDrcViaNum("spacing" 0.5)
    ve = librfDrcViaNum("enclosure" 0.2)
    librfPathSweep(cv mU pts w)
    foreach(p list(car(pts) lriLast(pts))
      x1 = car(p)-w/2.0  y1 = cadr(p)-w/2.0
      x2 = car(p)+w/2.0  y2 = cadr(p)+w/2.0
      librfRect(cv mU x1 y1 x2 y2)
      librfRect(cv mS x1 y1 x2 y2)
      librfViaArray(cv cut x1 y1 x2 y2 vc vs ve ve))))

; 仅末端 landing/via 的桥：起点已经在下层网络中心 (diff ct),
; 起点再放 metal landing 会与最内圈短路.
procedure(lriBridgeTap(cv pts w)
  let((mU mS cut p x1 y1 x2 y2 vc vs ve)
    mU  = librfMetalPair(librfLyr("underpass"))
    mS  = librfMetalPair(librfLyr("spiral"))
    cut = librfViaPair(librfLyr("via_sp"))
    vc = librfDrcViaNum("cut" 0.5)
    vs = librfDrcViaNum("spacing" 0.5)
    ve = librfDrcViaNum("enclosure" 0.2)
    librfPathSweep(cv mU pts w)
    p = lriLast(pts)
    x1 = car(p)-w/2.0  y1 = cadr(p)-w/2.0
    x2 = car(p)+w/2.0  y2 = cadr(p)+w/2.0
    librfRect(cv mU x1 y1 x2 y2)
    librfRect(cv mS x1 y1 x2 y2)
    librfViaArray(cv cut x1 y1 x2 y2 vc vs ve ve)))

;------------------------------------------------------------------ 断言
procedure(lriAssert(tag ok msg)
  lriReport = cons(list(tag ok msg) lriReport)
  unless(ok  librfErr(sprintf(nil "lri[%s] %s" tag msg)))
  t)

procedure(lriCheck(pts w W H s tag)
  let((x1 y1 x2 y2 bw bh)
    x1 = 1.0e30 y1 = 1.0e30 x2 = -1.0e30 y2 = -1.0e30
    foreach(p pts
      x1 = min(x1 car(p))  y1 = min(y1 cadr(p))
      x2 = max(x2 car(p))  y2 = max(y2 cadr(p)))
    bw = x2-x1+w  bh = y2-y1+w
    lriAssert(sprintf(nil "%s.bbox" tag) (abs(bw-W) < 0.02)
      sprintf(nil "bbox %.3f != %.3f" bw W))
    lriAssert(sprintf(nil "%s.bboxY" tag) (abs(bh-H) < 0.02)
      sprintf(nil "bboxY %.3f != %.3f" bh H))
    lriAssert(sprintf(nil "%s.angle" tag) (lriAngleOK(pts) != nil)
      "存在非 0/45/90 段")
    t))

;------------------------------------------------------------------ SE
procedure(lriBuildSE(cv req W H w s n corner seam stubLen ct)
  let((body p0 p1 ex ey L dx dy dir tangle dirs nat2 vx vy bb)
    body = lriSpiral(W H w s n corner seam nil)
    lriCheck(body w W H s "se")
    lriDraw(cv body w)
    p0 = car(body)
    dir = cond(
            ((seam == "top")    'N)
            ((seam == "left")   'W)
            ((seam == "right")  'E)
            (t                  'S))
    p1 = lriLast(body)
    dx = car(p1)  dy = cadr(p1)
    L = sqrt(dx*dx + dy*dy)
    if(L < 1.0e-6  then progn(dx = 0.0  dy = 1.0  L = 1.0))
    nat2 = librfExitDir(dx dy)
    ; 端子夹角 tangle: 0/90/180 (-1=自然向, 旧行为)
    tangle = librfReqNum(req 'tangle -1)
    when(tangle >= 0
      dirs = librfTangleDirs(tangle dir nat2)
      dir = car(dirs)  nat2 = cadr(dirs)
      cond(
        ((nat2 == 'N)  vx = 0.0   vy = 1.0)
        ((nat2 == 'S)  vx = 0.0   vy = -1.0)
        ((nat2 == 'E)  vx = 1.0   vy = 0.0)
        (t             vx = -1.0  vy = 0.0)
      )
      ex = car(p1) + vx*40.0
      ey = cadr(p1) + vy*40.0
    )
    when(tangle < 0
      vx = dx/L  vy = dy/L
      ex = car(p1) + vx*40.0
      ey = cadr(p1) + vy*40.0
    )
    ; 无论 tangle 与否, 出口点都必须在包围盒外:
    ; 否则 p02 的同层 stub 会从内部穿过绕组, 造成短路.
    when(vy > 0.5 && ey < H/2.0 + w + 20.0  ey = H/2.0 + w + 40.0)
    when(vy < -0.5 && ey > 0.0 - H/2.0 - w - 20.0  ey = 0.0 - H/2.0 - w - 40.0)
    when(vx > 0.5 && ex < W/2.0 + w + 20.0  ex = W/2.0 + w + 40.0)
    when(vx < -0.5 && ex > 0.0 - W/2.0 - w - 20.0  ex = 0.0 - W/2.0 - w - 40.0)
    lriPortDir(cv car(p0) cadr(p0) dir w stubLen "p01")
    lriBridge(cv list(p1 list(ex ey)) w)
    lriPortVec(cv ex ey vx vy w stubLen "p02")
    when(ct != 0 && ct != nil
      let((cut tx ty ctDir vx2 vy2 ex2 ey2)
        cut = lriPointAt(body 0.5)
        tx = car(cut)  ty = cadr(cut)
        ; 抽头出线必须保持正交（0/90），径向出线会产生任意角度；
        ; p01 为 S 时向北引出，否则向南；出口在包围盒外。
        ctDir = if(dir == 'S then 'N else 'S)
        if(ctDir == 'N
          then vx2 = 0.0  vy2 = 1.0
               ex2 = tx  ey2 = H/2.0 + w + 40.0
          else vx2 = 0.0  vy2 = -1.0
               ex2 = tx  ey2 = 0.0 - H/2.0 - w - 40.0)
        lriBridge(cv list(cut list(ex2 ey2)) w)
        lriPortVec(cv ex2 ey2 vx2 vy2 w stubLen "ct1")))
    body))

;------------------------------------------------------------------ DIFF
; 对称交错双臂：A/B 交错填充 (union 间距恒 s)，180° 旋转对称；
; 中心桥 A内端->原点->B内端；tangle 控两端出线向
procedure(lriBuildDiff(cv req W H w s n corner seam stubLen ct)
  let((p A B stepsA stepsB tangle dirs d1 d2)
    p = w + s
    A = lriSpiral(W H w s n corner "bottom" 2.0*p)
    stepsA = lriStepList
    ; B 必须是 A 的镜像，不能是 180° 旋转拷贝。
    ; 两臂内端对接后：旋转拷贝会让 B 臂电流绕中心的旋向与 A 相反，
    ; 同心交错结构的互感为负，EMX 实测 L 只有 ~0.5nH（单臂 ~1.2nH）；
    ; 镜像后两臂电流同向，磁场相加（正确差分电感）。
    B = lriMirrorY(lriSpiral(W-2.0*p H-2.0*p w s n corner "bottom" 2.0*p))
    lriStepList = lriMirrorStepsY(lriStepList)
    stepsB = lriStepList
    lriCheck(A w W H s "diff.A")
    lriCheck(B w (W-2.0*p) (H-2.0*p) s "diff.B")
    ; 两臂交错：A/B 的圈间步进都会横穿另一臂，必须走 underpass，
    ; 否则同层 M5 直接短路（对差分电感等于失效）。
    lriDrawSteps(cv A w stepsA)
    lriDrawSteps(cv B w stepsB)
    ; A 外端在下方外缘, 直接 S 出线安全.
    d1 = 'S
    tangle = librfReqNum(req 'tangle -1)
    when(tangle >= 0 && tangle != 180
      librfWarn(sprintf(nil "lri[diff]: tangle=%g 会使 B 端口同层穿过 A 外圈, 已改用自然 180 度出线 (p01=S, p02=N)" tangle)))
    lriPortDir(cv car(car(A)) cadr(car(A)) d1 w stubLen "p01")
    ; B 外端在上方, 但 B 外圈位于 A 外圈之内; 必须先用 underpass
    ; 逃到 A 包围盒外, 再在 spiral 层做 p02 端口. 旧版直接 N 出线
    ; 会横穿 A 外圈 (同层短路).
    let((pB ex ey)
      pB = car(B)
      ex = car(pB)
      ey = H/2.0 + w + 40.0 + stubLen
      lriBridge(cv list(pB list(ex ey)) w)
      lriPortVec(cv ex ey 0.0 1.0 w stubLen "p02"))
    ; 中心桥：A内端 -> (B内端x, A内端y) -> B内端。
    ; 不能走直线对角线：两臂内端对称时该线恰好穿过另一臂“部分圈起点”
    ; 的 via landing（圈间步进已改 M4），等于把中心节点接到绕组中间，
    ; 会短路掉半匝。改走中心空区：先沿 A 内端所在水平线到 B 内端 x，
    ; 再垂直落到 B 内端；该走廊距 B 步进/landing、A 步进均 >= s。
    lriBridge(cv list(lriLast(A) list(car(lriLast(B)) cadr(lriLast(A)))
                      lriLast(B)) w)
    when(ct != 0 && ct != nil
      let((ctEnd)
        ; 抽头直接从中心节点 (B 内端, 已有桥 landing+via) 垂直向下引出。
        ; 旧版从 (0,0) 引出，起点 10x10 端帽会压到 B 步进 (7,7) 的
        ; via landing（相距 7 < w），同样短路半匝。
        ctEnd = list(car(lriLast(B)) 0.0-H/2.0-w-40.0-stubLen)
        lriBridgeTap(cv list(lriLast(B) ctEnd) w)
        lriPortVec(cv car(ctEnd) cadr(ctEnd) 0.0 -1.0 w stubLen "ct1")))
    list(A B)))

;------------------------------------------------------------------ F8
; 8 字双环：左右两环、内端经原点(腰)交叉 underpass，外端东西出线；tangle 可调
procedure(lriBuildF8(cv req W H w s n corner gap stubLen)
  let((d nA Lft Rgt dx tangle dirs d1 d2 cf8)
    when(gap == nil || gap < 1.0  gap = 8.0)
    ; 内端 landing 是 w x w; 两环内端间距若 < w+s, landing 会直接短路.
    when(gap < w + s
      librfWarn(sprintf(nil "lri[f8]: gap=%g 小于 w+s=%g, 已放大到 w+s" gap (w+s)))
      gap = w + s)
    d = (W - gap)/2.0
    nA = n/2.0
    when(abs(nA - round(nA)) < 1.0e-6  nA = nA + 0.5)
    dx = d/2.0 + gap/2.0
    ; 子环外径 d < W：45° 切角按比例缩小，避免 oct 子环切角过大退化自交
    cf8 = corner
    when(min((W-w)/2.0 (H-w)/2.0) > 0.0
      cf8 = corner*min((d-w)/2.0 (H-w)/2.0)/min((W-w)/2.0 (H-w)/2.0))
    Lft = lriShift(lriSpiral(d H w s nA cf8 "left" nil) (0.0-dx) 0.0)
    Rgt = lriShift(lriMirrorX(lriSpiral(d H w s nA cf8 "left" nil)) dx 0.0)
    lriCheck(Lft w d H s "f8.L")
    lriDraw(cv Lft w)
    lriDraw(cv Rgt w)
    d1 = 'W  d2 = 'E
    tangle = librfReqNum(req 'tangle -1)
    when(tangle >= 0
      dirs = librfTangleDirs(tangle d1 d2)
      d1 = car(dirs)  d2 = cadr(dirs)
    )
    lriPortDir(cv car(car(Lft)) cadr(car(Lft)) d1 w stubLen "p01")
    lriPortDir(cv car(car(Rgt)) cadr(car(Rgt)) d2 w stubLen "p02")
    ; 腰：两内端经原点交叉（8 字的中心），下层桥。
    ; 先垂直走到 y=0 再横穿，保证 underpass 全为 0/90 段。
    lriBridge(cv list(lriLast(Lft)
                      list(car(lriLast(Lft)) 0.0)
                      list(0.0 0.0)
                      list(car(lriLast(Rgt)) 0.0)
                      lriLast(Rgt)) w)
    list(Lft Rgt)))

;------------------------------------------------------------------ SYM
; RFIC-GPT 对称型（4 参数：dout/n/w/s）
; 每臂 = 交替左右半圈：偶数 k 右半圈、奇数 k 左半圈；
; 换圈在上下对称轴处跨过轴线（x=+-xj），两臂在同一轴心交叉，
; 层互补：一臂走顶层、另一臂走 underpass，避免同层短路。
; N = 2*fix(n/2)+1（就近奇数）：总圈数必须为奇数，两内端才能
; 同时落在下方对称轴并用一段顶层短桥相连。

; 右半圈：从 (xj,R) 顺时针经右侧到 (xj,-R)
procedure(lriHalfOctR(R xj)
  let((c)
    c = (2.0 - sqrt(2.0)) * R
    list(list(xj R) list(R-c R) list(R R-c)
         list(R 0.0-(R-c)) list(R-c 0.0-R) list(xj 0.0-R))))

; 左半圈：从 (-xj,-R) 顺时针经左侧到 (-xj,R)
procedure(lriHalfOctL(R xj)
  lriMirrorX(reverse(lriHalfOctR(R xj))))

; 最后一个 cons cell（rplaca 用）
procedure(lriLastCell(lst)
  if(null(cdr(lst)) lst lriLastCell(cdr(lst))))

; pt 是否在点表中（坐标容差 1nm）
procedure(lriPtTrimmed(pt pts)
  let((hit)
    hit = nil
    foreach(q pts  when(lriSamePt(pt q)  hit = t))
    hit))

; 换圈 flush 端的外移窗口 (dmin dmax)：端点 (xj,R-p) 外移 delta 后，
; 危险角点到两条 45 度斜线的净距都 >= 2.5um：
;   1) 另一臂换圈斜线 u-v=xj-R  (u=|x|, v=|y|)
;   2) 半径 R 外圈切角   u+v=sqrt(2)*R
; dmax < dmin 表示该半径的夹缝放不下 w 宽端头。
procedure(lriSymWindow(w s R p)
  let((a)
    a = sqrt(2.0) * (w / 2.0 + 2.5)
    list(a - p + w / 2.0
         (sqrt(2.0) - 1.0) * R + p / 2.0 - w / 2.0 - a)))

; 返回 (tops unders vias topCaps N R0 Rlast xj)
;   tops    = 顶层金属连续折线列表（半圈 + 本臂顶层换圈段）
;   unders  = 下层 M4 折线列表；两端水平包住通孔，再斜穿到另一臂
;   vias    = 通孔阵列中心与 y 向宽度（(x y viaW) 列表）
;   topCaps = 每条 tops 的 (起点端帽 终点端帽)，nil 表示 flush 平头
; 换圈 flush 端不得加 w x w 满宽方端帽：该方角在 45 度夹缝里既撞
; 另一臂换圈斜线又撞外圈切角。外移量取夹缝窗口中点；通孔直接放在
; 外移后的宽端下方，M4 从通孔继续向中心延伸，M5 不再向中心伸窄颈。
; PDK 规则：V4 cut 1.5um、space 1.5um、
; M4/M5 enclosure 0.5um；通孔放在宽端下方后 y 向可放 1/2/3 个 cut。
procedure(lriSymArm(dout n w s side)
  let((p xj R0 N Rlast k R seg halfs tops unders vias trimPts topCaps
      run nxt underHere dmin dmax delta Rin tmMax viaW viaEdge stub back
      a b sa sb at bt av bv ae be cc)
    when(dout <= 0.0 || w <= 0.0 || s <= 0.0
      librfErr("lri[sym]: dout/w/s 必须 > 0"))
    p = w + s
    xj = p / 2.0
    R0 = dout / 2.0 - w / 2.0
    when(R0 <= 0.0
      librfErr(sprintf(nil "lri[sym]: dout=%.3f 太小（需 > w=%.3f）" dout w)))
    N = 2 * fix(n / 2.0) + 1
    when(N < 1  N = 1)
    Rlast = R0 - (N - 1) * p
    when(Rlast <= 0.0
      librfErr(sprintf(nil
        "lri[sym]: dout=%.3f 放不下 %d 圈；请增大 dout 或减小 n/w/s"
        dout N)))
    when(Rlast * (sqrt(2.0) - 1.0) < xj
      librfErr(sprintf(nil
        "lri[sym]: 最内半圈 R=%.3f 直边太短，放不下 xj=%.3f；请增大 dout"
        Rlast xj)))
    halfs = nil
    for(k 0 N-1
      R = R0 - k * p
      seg = if(mod(k 2) == 0 then lriHalfOctR(R xj) else lriHalfOctL(R xj))
      when(side < 0  seg = lriMirrorX(seg))
      halfs = append(halfs list(seg)))
    unders = nil
    vias = nil
    trimPts = nil
    viaEdge = 1.5 / 2.0 + 0.5
    ; 宽端下方 y 向可放 1/2/3 个 cut（两侧各留 0.5 包覆）
    viaW = if(w >= 8.5 then 7.5 else if(w >= 5.5 then 4.5 else 1.5))
    ; 端头水平短段长度：端头外侧顶边 = stub + h(sqrt2-1)，
    ; PDK 开口楔规则要求 >= 2.5，取 2.6 留余量。
    back = w / 2.0 * (sqrt(2.0) - 1.0)
    stub = max(viaEdge 2.6 - back)
    for(k 0 N-2
      underHere = if(side < 0
                     then mod(k 2) == 0
                     else mod(k 2) == 1)
      when(underHere
        R = R0 - k * p
        dmin = car(lriSymWindow(w s R p))
        dmax = cadr(lriSymWindow(w s R p))
        when(dmax < dmin - 1.0e-9
          librfErr(sprintf(nil
            "lri[sym]: 换圈 R=%.3f 的 45 度夹缝放不下 w=%.2f 端头（窗口 %.2f..%.2f）；请增大 dout 或减小 w/s"
            R w dmin dmax)))
        Rin = R - p
        ; flush 面必须落在内圈水平边内边线以内（否则切角内边在
        ; flush 面处收成斜楔，宽端外角掉出本圈金属 -> 顶层金属缺口）。
        chordMax = (sqrt(2.0) - 1.0) * (Rin - w / 2.0) - xj
        ; M4 从宽端下方再向外包住通孔，端点不能越过对应 M5 的直边
        ; R-c；否则通孔外侧包覆不足，M4 也会伸出这一圈。
        tmMax = (sqrt(2.0) - 1.0) * R - xj - (viaEdge + stub)
        upper = min(dmax chordMax tmMax)
        when(upper < dmin - 1.0e-9
          librfErr(sprintf(nil
            "lri[sym]: 换圈 R=%.3f（内圈 %.3f）的 45 度夹缝放不下 w=%.2f 端头（下界 %.2f，上界 min(%.2f 直边 %.2f M4端 %.2f)=%.2f）；请增大 dout 或减小 w/s"
            R Rin w dmin dmax chordMax tmMax upper)))
        delta = max(0.0 (dmin + upper) / 2.0)
        a = lriLast(nth(k halfs))
        b = car(nth(k+1 halfs))
        sa = if(car(a) >= 0.0 then 1.0 else -1.0)
        sb = if(car(b) >= 0.0 then 1.0 else -1.0)
        at = list(car(a) + sa * delta cadr(a))
        bt = list(car(b) + sb * delta cadr(b))
        rplaca(lriLastCell(nth(k halfs)) at)
        rplaca(nth(k+1 halfs) bt)
        ; 通孔放在外移后的宽端下方；M4 两端水平包住通孔，
        ; 再沿两孔连线斜穿到另一臂（比原 45 度直线更长）。
        av = list(car(at) + sa * viaEdge cadr(at))
        bv = list(car(bt) + sb * viaEdge cadr(bt))
        ae = list(car(av) + sa * stub cadr(av))
        be = list(car(bv) + sb * stub cadr(bv))
        ; 45° 斜段 + 水平段：避免水平包覆段与细长斜线之间出现
        ; 锐角 miter（w=10 时会甩出很长的尖角）。
        cc = list(car(av) - sa * p cadr(bv))
        unders = append(unders list(list(ae av cc bv be)))
        vias = append(vias list(
          list(car(av) cadr(av) viaW)
          list(car(bv) cadr(bv) viaW)))
        trimPts = append(trimPts list(at bt))))
    tops = nil
    run = car(halfs)
    for(k 0 N-2
      underHere = if(side < 0
                     then mod(k 2) == 0
                     else mod(k 2) == 1)
      nxt = car(nth(k+1 halfs))
      if(underHere
        then
          tops = append(tops list(run))
          run = nth(k+1 halfs)
        else
          run = append(run cons(nxt cdr(nth(k+1 halfs))))))
    tops = append(tops list(run))
    ; flush 端点（等于任一 trimPt）不加满宽端帽
    topCaps = nil
    foreach(run tops
      topCaps = append(topCaps list(list(
        if(lriPtTrimmed(car(run) trimPts) then nil else t)
        if(lriPtTrimmed(lriLast(run) trimPts) then nil else t)))))
    list(tops unders vias topCaps N R0 Rlast xj)))

; 换圈 underpass 多边形。端头 1.25um 水平短段后紧跟 45° 弯；若按
; miter 扫掠，弯折侧端头竖边与 45° 边之间会留下 1.25-h(√2-1) 的尖楔
; （w=4/6 时 <2.5um，PDK 开口楔 INT 规则报错）。这里把端头竖边延长到
; 与 45° 边直接相交，两段边共点，尖楔消失；另一端两段共线，仍为整宽平头。
procedure(lriUnderPolyPts(pts w)
  let((x0 y0 x1 x2 y2 x4 h back sg ya yb)
    x0 = car(car(pts))   y0 = cadr(car(pts))
    x1 = car(nth(1 pts))
    x2 = car(nth(2 pts)) y2 = cadr(nth(2 pts))
    x4 = car(nth(4 pts))
    h = w / 2.0
    back = h * (sqrt(2.0) - 1.0)
    sg = if(y2 >= cadr(nth(1 pts)) then 1.0 else -1.0)
    ya = y0 - sg * h
    yb = y2 + sg * h
    list(
      list(x0 ya)
      list((x1 + back) ya)
      list((x2 + back) (y2 - sg * h))
      list(x4 (y2 - sg * h))
      list(x4 yb)
      list((x2 - back) yb)
      list(x0 (y0 + sg * (h + back - (x1 - x0)))))))

; 换圈 underpass：只画下层 M4 多边形（端点已含通孔包覆）。
; 通孔阵列由 lriViaAt 单独放置，宽度随 w 取 1/2/3 个 cut。
procedure(lriBridgeVia(cv pts w)
  when(length(pts) == 5
    librfNoteLayer(librfLyr("underpass"))
    dbCreatePolygon(cv librfMetalPair(librfLyr("underpass"))
      lriUnderPolyPts(pts w))))

; 宽端下方的通孔阵列：x 向 1 个 cut，y 向 1/2/3 个 cut。
procedure(lriViaAt(cv x y vw)
  let((cut vc vs ve vhalf)
    cut = librfViaPair(librfLyr("via_sp"))
    vc = librfDrcViaNum("cut" 0.5)
    vs = librfDrcViaNum("spacing" 0.5)
    ve = librfDrcViaNum("enclosure" 0.2)
    vhalf = vc / 2.0 + ve
    librfViaArray(cv cut
      x - vhalf y - (vw / 2.0 + ve)
      x + vhalf y + (vw / 2.0 + ve)
      vc vs ve ve)))

procedure(lriBuildSym(cv req dout n w s)
  let((qa qb ta ua va ca tb ub vb cb N R0 Rlast xj i
      lead angOK x1 y1 x2 y2)
    qa = lriSymArm(dout n w s 1)
    qb = lriSymArm(dout n w s -1)
    ta = nth(0 qa)  ua = nth(1 qa)  va = nth(2 qa)  ca = nth(3 qa)
    N = nth(4 qa)  R0 = nth(5 qa)  Rlast = nth(6 qa)  xj = nth(7 qa)
    tb = nth(0 qb)  ub = nth(1 qb)  vb = nth(2 qb)  cb = nth(3 qb)
    lead = 20.0
    ; 两臂内端同在下方对称轴：短桥并入 A 臂最后一条顶层路径，
    ; 避免把同一块连续金属当成两段独立金属做间距检查。
    ta = append(reverse(cdr(reverse(ta)))
                list(append(lriLast(ta)
                            list(list(0.0-xj 0.0-Rlast)))))
    ; 主线圈：两臂全部顶层金属段（含本臂的顶层换圈段）；
    ; 换圈 flush 端用 capStart/capEnd=nil 画平头，避免满宽方端帽
    ; 同时撞另一臂换圈斜线和外圈切角。
    for(i 0 length(ta)-1
      lriDrawEx(cv nth(i ta) w car(nth(i ca)) cadr(nth(i ca))))
    for(i 0 length(tb)-1
      lriDrawEx(cv nth(i tb) w car(nth(i cb)) cadr(nth(i cb))))
    ; 换圈 underpass（层与另一臂互补，交叉处不会同层短路）
    foreach(p append(ua ub)  lriBridgeVia(cv p w))
    ; 通孔阵列放在外移后的宽端下方，y 向 1/2/3 个 cut
    foreach(v append(va vb)
      lriViaAt(cv car(v) cadr(v) caddr(v)))
    ; 两端口同在顶部，垂直向上引出
    lriPortDir(cv xj R0 'N w lead "p01")
    lriPortDir(cv 0.0-xj R0 'N w lead "p02")
    ; N=1 没有换圈 via，M5.P（顶层金属必须接触 V4 与焊盘层）会报错；
    ; 在两条端口引线下方各加一块 M4 landing + 一颗 V4。
    when(N == 1
      let((mU cut sx vy vc vs ve vhalf)
        mU = librfMetalPair(librfLyr("underpass"))
        cut = librfViaPair(librfLyr("via_sp"))
        vc = librfDrcViaNum("cut" 0.5)
        vs = librfDrcViaNum("spacing" 0.5)
        ve = librfDrcViaNum("enclosure" 0.2)
        vhalf = vc / 2.0 + ve
        foreach(sx list(xj 0.0-xj)
          librfPathSweep(cv mU list(list(sx R0) list(sx R0 + lead)) w)
          vy = R0 + lead / 2.0
          librfViaArray(cv cut
            sx - vhalf  vy - vhalf  sx + vhalf  vy + vhalf
            vc vs ve ve))))
    ; 底部中心结点已并入 A 臂最后一段，不再单独画桥
    ; 断言
    lriAssert("sym.N" (mod(N 2) == 1) "每臂半圈数必须为奇数")
    angOK = t
    foreach(p append(ta tb)
      unless(lriAngleOK(p)  angOK = nil))
    lriAssert("sym.angle" angOK "存在非 0/45/90 段")
    x1 = 1.0e30  y1 = 1.0e30  x2 = -1.0e30  y2 = -1.0e30
    foreach(pl append(ta tb)
      foreach(p pl
        x1 = min(x1 car(p))  y1 = min(y1 cadr(p))
        x2 = max(x2 car(p))  y2 = max(y2 cadr(p))))
    lriAssert("sym.bbox" (abs((x2-x1+w) - dout) < 0.02)
      sprintf(nil "bbox %.3f != %.3f" (x2-x1+w) dout))
    lriAssert("sym.bboxY" (abs((y2-y1+w) - dout) < 0.02)
      sprintf(nil "bboxY %.3f != %.3f" (y2-y1+w) dout))
    lriReport))

;------------------------------------------------------------------ 入口
procedure(lriBuildInd(cv req)
  let((kind ct shape W H w s n corner seam stubLen gap doutDef)
    kind = librfReqStr(req 'kind "se")
    ct   = librfReqNum(req 'ct 0)
    shape = librfReqStr(req 'shape "rect")
    doutDef = librfReqNum(req 'dout 160.0)
    W = librfReqNum(req 'W doutDef)
    H = librfReqNum(req 'H doutDef)
    w = librfReqNum(req 'w 10.0)
    s = librfReqNum(req 's 3.2)
    n = librfReqNum(req 'n 3.0)
    corner = librfReqNum(req 'corner 0.0)
    when(shape == "oct" || shape == "octagon"
      corner = (2.0 - sqrt(2.0)) * min((W-w)/2.0 (H-w)/2.0))
    seam = librfReqStr(req 'seam "bottom")
    ; 表单用 S/N/E/W，内核用 bottom/top/left/right；两种写法都接受，
    ; 否则直接调 API 传 "N"/"E"/"W" 会静默退化成 bottom。
    cond(
      ((seam == "S" || seam == "s") seam = "bottom")
      ((seam == "N" || seam == "n") seam = "top")
      ((seam == "E" || seam == "e") seam = "right")
      ((seam == "W" || seam == "w") seam = "left"))
    stubLen = librfReqNum(req 'stub max(2.0*w 5.0))
    gap = librfReqNum(req 'gap 8.0)
    librfDrcParams(w s t (w > 15.0))
    lriReport = nil
    cond(
      ((kind == "sym" || kind == "symmetric" || kind == "wiki")
        lriBuildSym(cv req doutDef n w s))
      ((kind == "se")
        lriBuildSE(cv req W H w s n corner seam stubLen ct))
      ((kind == "diff")
        lriBuildDiff(cv req W H w s n corner seam stubLen ct))
      ((kind == "f8" || kind == "fig8" || kind == "figure8")
        lriBuildF8(cv req W H w s n corner gap stubLen))
      (t
        librfErr(sprintf(nil "lri: 未知 kind=%s (sym/se/diff/f8)" kind))))
    ; 统一落 marker 标记 + DRC
    when(librfReqNum(req 'ind_mark 0) != 0
      librfIndMark(cv librfCvBBox(cv) 1.0))
    lriReport))

;------------------------------------------------------------------ 自测
procedure(lriSelfTest2()
  let((pts bb w s W H n corner ok nf)
    w = 10.0  s = 2.6  W = 150.0  H = 150.0  n = 2.5
    nf = 0
    foreach(corner list(0.0 10.0 25.0 41.0 60.0)
      pts = lriSpiral(W H w s n corner "bottom" nil)
      bb = list(1.0e30 1.0e30 -1.0e30 -1.0e30)
      foreach(p pts
        bb = list(min(car(bb) car(p)) min(cadr(bb) cadr(p))
                  max(caddr(bb) car(p)) max(cadddr(bb) cadr(p))))
      ok = (abs((caddr(bb)-car(bb)+w) - W) < 0.01) &&
           (abs((cadddr(bb)-cadr(bb)+w) - H) < 0.01) &&
           lriAngleOK(pts)
      if(ok then nf = nf + 1
        else printf("FAIL se c=%.1f bbox=%.3fx%.3f angOK=%L\n"
               corner (caddr(bb)-car(bb)+w) (cadddr(bb)-cadr(bb)+w)
               lriAngleOK(pts)))
    )
    ; 矩形
    pts = lriSpiral(300.0 150.0 w s 2.5 20.0 "bottom" nil)
    bb = list(1.0e30 1.0e30 -1.0e30 -1.0e30)
    foreach(p pts
      bb = list(min(car(bb) car(p)) min(cadr(bb) cadr(p))
                max(caddr(bb) car(p)) max(cadddr(bb) cadr(p))))
    if(abs((caddr(bb)-car(bb)+w)-300.0) < 0.01 && lriAngleOK(pts)
      then nf = nf + 1
      else printf("FAIL rect bbox=%.3f\n" (caddr(bb)-car(bb)+w)))
    printf("=== lri selftest: %d/6 pass ===\n" nf)
    nf == 6))

;=============================================================================
; lriDispatch -- 电感入口：按 kind 选拓扑，未知则回退旧实现
;=============================================================================
procedure(lriDispatch(cv req)
  let((kind)
    kind = librfReqStr(req 'kind "se")
    if(kind == "se" || kind == "diff" || kind == "f8"
       || kind == "fig8" || kind == "figure8" || kind == "sym"
       || kind == "symmetric" || kind == "wiki"
      then
        lriBuildInd(cv req)
      else
        librfWarn(sprintf(nil "lri: 未知 kind=%s, 回退旧 librfBuildInd" kind))
        librfBuildInd(cv req)
    )
  )
)

;------------------------------------------------------------------ 量测工具
procedure(lriPtsBBoxRaw(pts)
  let((x1 y1 x2 y2)
    x1 = 1.0e30  y1 = 1.0e30  x2 = -1.0e30  y2 = -1.0e30
    foreach(p pts
      x1 = min(x1 car(p))  y1 = min(y1 cadr(p))
      x2 = max(x2 car(p))  y2 = max(y2 cadr(p)))
    list(x1 y1 x2 y2)))

; 平行段最小间距（同走向分组后取相邻差）——用于 DRC 自检
procedure(lriPitchOf(pts)
  let((gW gN gE gS best prev cur dx dy lst)
    gW = nil  gN = nil  gE = nil  gS = nil
    when(length(pts) >= 2
      prev = car(pts)
      foreach(cur cdr(pts)
        dx = car(cur) - car(prev)
        dy = cadr(cur) - cadr(prev)
        cond(((dx < -1.0e-9)  gW = cons(cadr(prev) gW))
             ((dx > 1.0e-9)   gE = cons(cadr(prev) gE))
             ((dy > 1.0e-9)   gN = cons(car(prev) gN))
             ((dy < -1.0e-9)  gS = cons(car(prev) gS)))
        prev = cur))
    best = 1.0e30
    foreach(lst list(gW gN gE gS)
      when(length(lst) >= 2
        lst = sort(lst 'lessp)
        for(i 1 length(lst)-1
          when(abs(nth(i lst) - nth(i-1 lst)) > 1.0e-6   ; 跳过共线点
            best = min(best abs(nth(i lst) - nth(i-1 lst)))))))
    list(t best)))
