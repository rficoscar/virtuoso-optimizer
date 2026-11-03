
; librf.il - Virtuoso Optimizer SKILL (dual mode)
; eval serve: virtuoso -replay librf.il; needs OPT_QUEUE + OPT_OUTPUTS_STATE
; layout: dbAccess load -> librfRun / librfMakeViews / librfEmxViews
; protocol and invariants: DEVELOPMENT_GUIDE.md section 9
gOptOuts = nil
gLastResult = nil

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

procedure(myEvalOutput(outfp o)
  let((expr val order resName r)
    expr = o->expression
    unless(expr
      when(o->signal
        expr = list('getData o->signal)))
    unless(expr
      fprintf(outfp "MSG NO_EXPR %s\n" o->name)
      return(nil))

    expr = subst('db20 'db expr)
    order = nil
    when(gLastResult
      order = list(gLastResult))
    foreach(r '(sp hb ac dc tran noise pss)
      unless(r == gLastResult
        order = append(order list(r))))
    foreach(resName order
      unless(val

        when(errset(selectResult(resName) nil)
          val = errset(eval(expr) t)
          when(val gLastResult = resName))))
    if(val
      then
        myDumpValue(outfp o->name car(val))
      else
        fprintf(outfp "MSG EVAL_FAIL %s expr %L\n" o->name expr))))

procedure(myLogError(msg)
  let((fp queueDir)
    queueDir = getShellEnvVar("OPT_QUEUE")
    unless(queueDir queueDir = ".")
    fp = outfile(strcat(queueDir "/error.log") "a")
    when(fp
      fprintf(fp "eval_serve: %s\n" msg)
      close(fp))))

procedure(myFatal(msg)
  myLogError(msg)
  exit())

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

defvar(librf__resultPath nil)
defvar(librf__warns nil)
defvar(librf__errs nil)
defvar(librf__ports nil)
defvar(librf__layers nil)
defvar(librf__ok t)
defvar(gLibrfSelfFail nil)

procedure(librfReset()
  librf__warns = nil
  librf__errs = nil
  librf__ports = nil
  librf__layers = nil
  librf__ok = t)

procedure(librfWarn(msg)
  librf__warns = cons(msg librf__warns))

procedure(librfErr(msg)
  librf__errs = cons(msg librf__errs)
  librf__ok = nil)

procedure(librfNoteLayer(layerName)
  when(layerName && !member(layerName librf__layers)
    librf__layers = cons(layerName librf__layers)))

procedure(librfNotePort(name x y txtLayer)
  librf__ports = cons(list(name librfSnap(x) librfSnap(y) txtLayer) librf__ports))

procedure(librfSnap(v)
  (round(v * 1000.0) / 1000.0))

procedure(librfF3(v)
  sprintf(nil "%.3f" librfSnap(v)))

procedure(librfLastPt(pts)
  car(reverse(pts)))

procedure(librfCvBBox(cv)
  let((b)
    b = dbComputeBBox(cv)
    when(b == t  b = cv->bBox)
    librfBoxFlat(b)))

procedure(librfBoxFlat(b)
  when(b
    list(caar(b) cadar(b) caadr(b) cadadr(b))))

procedure(librfReqGet(req key dflt)
  let((seg)
    seg = member(key req)
    cond(
      (seg && cdr(seg)  cadr(seg))
      (t                dflt))))

procedure(librfReqNum(req key dflt)
  let((v)
    v = librfReqGet(req key dflt)
    cond(
      (numberp(v)  v)
      (stringp(v)  atof(v))
      (t           dflt))))

procedure(librfReqStr(req key dflt)
  let((v)
    v = librfReqGet(req key dflt)
    cond(
      (stringp(v)  v)
      (symbolp(v)  symbolToString(v))
      (numberp(v)  sprintf(nil "%g" v))
      (t           dflt))))

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

    printf("LIBRF_EXIT=%s\n" if(okFlag then "0" else "1"))))

procedure(librfRect(cv layerSpec x1 y1 x2 y2)
  let((a b c d layName res)
    a = librfSnap(min(x1 x2))  b = librfSnap(min(y1 y2))
    c = librfSnap(max(x1 x2))  d = librfSnap(max(y1 y2))
    layName = if(listp(layerSpec) then car(layerSpec) else layerSpec)
    librfNoteLayer(layName)

    res = errset(dbCreateRect(cv layerSpec list(list(a b) list(c d))) t)
    when(res == nil || car(res) == nil
      librfErr(sprintf(nil "画矩形失败 layer=%s" layName)))))

procedure(librfDiagSeg(cv layerSpec x0 y0 x1 y1 w)
  prog((dx dy len nx ny pts half)
    dx = x1 - x0   dy = y1 - y0
    half = w / 2.0
    len = sqrt(dx*dx + dy*dy)
    when(len < 0.001
      librfErr("45°段长度为 0")
      return(nil))

    nx = (0.0 - dy) / len * half
    ny = dx / len * half
    pts = list(
      list(librfSnap(x0 + nx) librfSnap(y0 + ny))
      list(librfSnap(x0 - nx) librfSnap(y0 - ny))
      list(librfSnap(x1 - nx) librfSnap(y1 - ny))
      list(librfSnap(x1 + nx) librfSnap(y1 + ny)))
    librfNoteLayer(if(listp(layerSpec) then car(layerSpec) else layerSpec))

    if(errset(dbCreatePolygon(cv layerSpec pts) t) == nil
      then errset(dbCreatePath(cv layerSpec
             list(list(librfSnap(x0) librfSnap(y0))
                  list(librfSnap(x1) librfSnap(y1))) w) t))

    librfRect(cv layerSpec x0 - half y0 - half x0 + half y0 + half)
    librfRect(cv layerSpec x1 - half y1 - half x1 + half y1 + half)
    return(t)))

procedure(librfEndCap(cv layerSpec x y w)
  let((half)
    half = w / 2.0
    librfRect(cv layerSpec x - half y - half x + half y + half)))

procedure(librfPathSweepEx(cv layerSpec pts w capStart capEnd)
  prog((clean p prev n res layName half)

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

    res = errset(dbCreatePath(cv layerSpec clean w) t)
    when(res && car(res)

      errset(car(res)->pathType = 0 t)
    )
    when(res == nil || car(res) == nil
      librfErr(sprintf(nil "dbCreatePath 失败 layer=%s npts=%d, 退矩形扫掠" layName n))

      prev = car(clean)
      foreach(p cdr(clean)
        librfDiagSeg(cv layerSpec car(prev) cadr(prev) car(p) cadr(p) w)
        prev = p
      )
    )

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

        cx = (xa + xb) / 2.0 + (j - (nCol - 1) / 2.0) * pitch
        cy = (ya + yb) / 2.0 + (i - (nRow - 1) / 2.0) * pitch
        librfRect(cv cutLayer cx - half cy - half cx + half cy + half)
        n = n + 1))
    n))

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

procedure(librfPortPin(cv metalName txtLayer x y portName)
  let((netObj res fig lab txtName h)
    txtName = if(listp(txtLayer) then car(txtLayer) else txtLayer)
    librfNoteLayer(txtName)
    h = 2.0

    netObj = nil
    res = errset(dbCreateNet(cv portName) t)
    if(res && car(res)
      then netObj = car(res)
      else netObj = dbFindNetByName(cv portName))
    when(netObj
      errset(dbCreateTerm(netObj portName "inputOutput") t)

      fig = errset(dbCreateRect(cv list(metalName "pin")
        list(list(librfSnap(x) - 1.5 librfSnap(y) - 1.5)
             list(librfSnap(x) + 1.5 librfSnap(y) + 1.5))) t)
      if(fig && car(fig)
        then res = errset(dbCreatePin(netObj car(fig) portName) t)
        else res = errset(dbCreatePin(netObj nil portName) t))
      when(res == nil
        librfWarn(sprintf(nil "dbCreatePin 失败 port=%s (label 仍保留)" portName))))

    lab = errset(dbCreateLabel(cv txtLayer
      list(librfSnap(x) librfSnap(y)) portName
      "centerCenter" "R0" "roman" h) t)
    when(lab == nil || car(lab) == nil
      librfErr(sprintf(nil "打 label 失败 layer=%s port=%s" txtName portName)))))

procedure(librfPinLabel(cv metalLayer txtLayer x1 y1 x2 y2 portName)
  let((cx cy metalName)
    librfRect(cv metalLayer x1 y1 x2 y2)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    metalName = if(listp(metalLayer) then car(metalLayer) else metalLayer)
    librfPortPin(cv metalName txtLayer cx cy portName)
    librfNotePort(portName cx cy
      if(listp(txtLayer) then car(txtLayer) else txtLayer))))

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

        cond(
          (dirSym == 'W  librfTipLabel(cv metalLayer txtLayer x1 y dirSym portName))
          (dirSym == 'E  librfTipLabel(cv metalLayer txtLayer x2 y dirSym portName))
          (dirSym == 'S  librfTipLabel(cv metalLayer txtLayer x y1 dirSym portName))
          (dirSym == 'N  librfTipLabel(cv metalLayer txtLayer x y2 dirSym portName))
          (t             librfTipLabel(cv metalLayer txtLayer x2 y dirSym portName))))))

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

procedure(librfLyr(role)
  let((p tbl)
    tbl = if(librfTech then librfTech else librfTechDefault())
    p = assoc(role tbl)
    cond(
      (p && cdr(p)  cadr(p))
      (t            role))))

procedure(librfTxtLayerOf(metalName)
  let((p tbl)
    tbl = if(librfTechText then librfTechText else librfTechTextDefault())
    p = assoc(metalName tbl)
    cond(
      (p && cdr(p)  cadr(p))
      (t            strcat(metalName "TXT")))))

procedure(librfDrcNum(key dflt)
  let((p)
    when(boundp('librfDrc) && librfDrc
      p = assoc(key librfDrc))
    cond(
      (p && cdr(p) && numberp(cadr(p))  1.0 * cadr(p))
      (t                                dflt))))

procedure(librfDrcViaNum(key dflt)
  let((p q)
    when(boundp('librfDrc) && librfDrc
      p = assoc("via" librfDrc)
      when(p && cdr(p)
        q = assoc(key cadr(p))
        when(q && cdr(q) && numberp(cadr(q))
          dflt = 1.0 * cadr(q))))
    dflt))

procedure(librfDrcWideSpace(metalName)
  let((p sub ans)
    ans = librfDrcNum("min_s" 1.0)
    when(boundp('librfDrc) && librfDrc
      p = assoc("wide_space" librfDrc))
    when(p && cdr(p)
      sub = assoc(metalName cadr(p))
      when(sub && cdr(sub) && numberp(cadr(sub))
        ans = 1.0 * cadr(sub)))
    ans))
procedure(librfDrcMinArea(metalName)
  let((p sub ans)
    ans = 4.0
    when(boundp('librfDrc) && librfDrc
      p = assoc("min_area" librfDrc))
    when(p && cdr(p)
      sub = assoc(metalName cadr(p))
      when(sub && cdr(sub) && numberp(cadr(sub))
        ans = 1.0 * cadr(sub)))
    ans))
procedure(librfMetalPair(nm) list(nm "drawing"))
procedure(librfViaPair(nm)   list(nm "drawing"))
procedure(librfTxtPair(nm)
  let((p)
    p = librfTxtLayerOf(nm)
    if(listp(p) p list(p "drawing"))))

defvar(librfTechLib nil)
procedure(librfTechLibName()
  cond(
    (librfTechLib  librfTechLib)
    (t             "RFCMOS")))

procedure(librfDrcParams(w s inInd wideSp)
  let((ok minW maxW minS)
    ok = t

    w = 1.0 * w
    s = 1.0 * s

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

procedure(librfBoxTouch(b1 b2)
  let((ax1 ay1 ax2 ay2 bx1 by1 bx2 by2)
    ax1 = car(b1) - 0.001  ay1 = cadr(b1) - 0.001
    ax2 = caddr(b1) + 0.001  ay2 = cadddr(b1) + 0.001
    bx1 = car(b2)  by1 = cadr(b2)
    bx2 = caddr(b2)  by2 = cadddr(b2)
    not(ax2 < bx1 || bx2 < ax1 || ay2 < by1 || by2 < ay1)))

procedure(librfBoxGap(b1 b2)
  let((dx dy)
    dx = max(car(b1) - caddr(b2)  car(b2) - caddr(b1))
    dy = max(cadr(b1) - cadddr(b2)  cadr(b2) - cadddr(b1))
    if(dx > 0.0 && dy > 0.0
      then sqrt(dx * dx + dy * dy)
      else max(dx dy))))

procedure(librfDrcFigs(cv metalName)
  let((minW minA fig b w h boxes bb g nTouch nBox i j b1 b2 gap minGap nViol big1 big2 pw)
    minW = librfDrcNum("min_w" 1.0)
    minA = librfDrcMinArea(metalName)

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

          nTouch = 0
          foreach(g boxes
            when(librfBoxTouch(bb g) nTouch = nTouch + 1))
          if(nTouch <= 1
            then librfErr(sprintf(nil "DRC: 孤立小块面积 %.3f < %.2f (%s) 违反 minArea"
                   w * h minA metalName))
            else librfWarn(sprintf(nil "DRC: 矩形面积 %.3f < %.2f (%s) 与他块搭接, 以合并形状核算"
                   w * h minA metalName)))))))

    foreach(fig cv->shapes
      when(equal(fig->objType 'path) && fig->layerName == metalName
        when(fig->purposeName == nil || fig->purposeName == "drawing"
          pw = car(errset(fig->width))

          when(numberp(pw) pw = 1.0 * pw)
          when(numberp(pw) && pw + 0.0005 < minW
            librfWarn(sprintf(nil "DRC: 走线宽度 %.3f < %.1f (%s)"
              pw minW metalName))))))

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















procedure(librfIndMark(cv bbox margin)
  let((x1 y1 x2 y2 w h)
    when(bbox
      x1 = car(bbox) - margin    y1 = cadr(bbox) - margin
      x2 = caddr(bbox) + margin  y2 = cadddr(bbox) + margin
      w = x2 - x1   h = y2 - y1

      when(min(w h) < 30.0
        librfWarn(sprintf(nil "MARKER 标记最小边 %.1f < 30um, 请核对标记层规则" min(w h))))
      when(max(w h) > 500.0
        librfWarn(sprintf(nil "MARKER 标记最大边 %.1f > 500um, 请核对标记层规则" max(w h))))
      librfRect(cv librfMetalPair(librfLyr("marker")) x1 y1 x2 y2))))

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

    when(techFindLayer(techGetTechFile(cv) librfLyr("spiral")) == nil
      librfErr(sprintf(nil "库 %s 的 tech 里找不到层 %s (工艺库 %s 期望)。\n可能是库绑了默认 tech 或工艺不匹配。请检查 tech_profile.json 的 layers.spiral / techlib，或删除库目录 %s 后重建"
        libName librfLyr("spiral") librfTechLibName() libDir))
      return(nil))

    copyFigs = copy(cv->shapes)
    foreach(fig copyFigs dbDeleteObject(fig))

    foreach(fig copy(cv->nets) errset(dbDeleteObject(fig) t))
    return(cv)))









procedure(librfRoutePts(xIn yIn xOut yOut exitDir)
  let((r)
    when(abs(xIn - xOut) < 0.001 || abs(yIn - yOut) < 0.001
      r = list(list(xIn yIn) list(xOut yOut)))
    when(r == nil
      if(exitDir == 'W || exitDir == 'E
        then r = list(list(xIn yIn) list(xOut yIn) list(xOut yOut))
        else r = list(list(xIn yIn) list(xIn yOut) list(xOut yOut))))
    r))

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
              m = cadddr(r)
              sc = min(m (needGap - 1.0)) + if(dc == dirCT then 0.5 else 0.0)
              when(sc > bestSc
                bestSc = sc  bestM = m
                bestCT = dc  best02 = d2  best04 = d4))))))
    list(bestCT best02 best04 bestM)))

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

    when(typ == nil     librfErr("缺少参数 type (ind)"))
    when(cellName == nil librfErr("缺少参数 cell"))
    when(resultPath == nil librfErr("缺少参数 result (结果文件路径)"))
    when(typ == nil || cellName == nil || resultPath == nil
      librfWriteResult(nil nil nil)
      exit())
    runOk = errset(progn(
      cv = librfOpenCell(libName cellName viewName libDir)
      when(cv
        cond(
          ((typ == "ind" || typ == "sym")
            ; 只剩 sym 对称差分电感
            prog((ok)
              ok = lriI3BuildSym(cv req)
              when(ok && librfReqNum(req 'ind_mark 0) != 0
                librfIndMark(cv librfCvBBox(cv) 1.0))
              return(ok)))
          (t  librfErr(sprintf(nil "未知/已删除 type: %s（只剩 ind/sym）" typ))))
        bbox = librfCvBBox(cv)
        dbSave(cv)
        dbClose(cv)) ) t)
    when(runOk == nil
      librfErr("SKILL 运行异常 (见 stdout 报错)"))
    cellTag = sprintf(nil "%s/%s/%s" libName cellName viewName)
    librfWriteResult(librf__ok cellTag
      bbox)
    exit()))


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

procedure(librfViewsIsLocked(libDir cellName viewName)
  prog((dir shcmd shres)
    when(libDir == nil || cellName == nil || viewName == nil
      return(nil))
    dir = sprintf(nil "%s/%s/%s" libDir cellName viewName)

    shcmd = sprintf(nil "ls %s/*.cdslck* >/dev/null 2>&1" dir)
    shres = errset(system(shcmd) t)
    if(shres && car(shres) == 0
      then return(t)
      else return(nil))))

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

    old = copy(cv->figs)
    foreach(fig old errset(dbDeleteObject(fig) t))
    old = copy(cv->instances)
    foreach(inst old errset(dbDeleteObject(inst) t))
    old = copy(cv->nets)
    foreach(n old errset(dbDeleteObject(n) t))
    old = copy(cv->pins)
    foreach(p old errset(dbDeleteObject(p) t))
    return(cv)))

procedure(librfViewsMaster(libName cellName viewName)
  let((m)
    m = errset(dbOpenCellViewByType(libName cellName viewName "schematicSymbol" "r") t)
    if(m && car(m) then car(m) else nil)))

procedure(librfViewsFindTerm(inst master termName)
  prog((src tm)
    src = if(inst && inst->master then inst->master else master)
    when(src == nil return(nil))
    tm = errset(dbFindTermByName(src termName) t)
    if(tm && car(tm) then return(car(tm)) else return(nil))))

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

procedure(librfViewsGetNet(cv name)
  prog((res)
    res = errset(dbCreateNet(cv name) t)
    if(res && car(res)
      then return(car(res))
      else
        progn(
          res = errset(dbFindNetByName(cv name) t)
          if(res && car(res) then return(car(res)) else return(nil))))))

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

    i = 0
    foreach(pName portNames
      progn(
        wireY = (npins - 1 - i) * 1.5
        net = librfViewsGetNet(cv pName)
        when(net == nil
          librfViewsErr(sprintf(nil "创建 net 失败: %s" pName)))
        when(net

          let((res)
            res = errset(dbCreateLine(cv list("wire" "drawing")
              list(list(2.0 wireY) list(x0 - 1.0 wireY))) t)
            when(res && car(res) errset(dbAddFigToNet(car(res) net) t)))

          instName = sprintf(nil "PIN%d" i)
          let((res iopin)
            res = errset(dbCreateInst(cv mIopin instName list(0.5 wireY) "R0") t)
            if(res && car(res)
              then progn(
                iopin = car(res)
                librfViewsConnect(net iopin mIopin "iopin"))
              else librfViewsErr(sprintf(nil "创建 iopin 失败: %s" instName))))

          errset(dbCreatePin(net nil pName) t)
          net->isPin = t

          tmName = sprintf(nil "p%d" i + 1)
          librfViewsConnect(net nport mNport tmName))
        i = i + 1))

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

      if(librfViewsConnect(net nport mNport "common")
        then librfViewsWarn("nport common 终端已接 GND")
        else
          for(i 1 npins
            progn(
              tmName = sprintf(nil "m%d" i)
              librfViewsConnect(net nport mNport tmName)))))

    errset(dbSave(cv) t)
    errset(dbClose(cv) t)
    errset(dbClose(mIopin) t)
    errset(dbClose(mNport) t)
    errset(dbClose(mGnd) t)
    librfViewsNoteCell(sprintf(nil "%s/%s/schematic" libName cellName))
    t))

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

    errset(dbCreateRect(cv list("border" "drawing")
      list(list(0.0 0.0) list(boxW boxH))) t)

    errset(dbCreateLabel(cv list("annotate" "drawing")
      list(cx cy) cellName "centerCenter" "R0" "roman" 0.0) t)

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

              let((lbl)
                lbl = errset(dbCreateLabel(cv list("pin" "drawing")
                  list(x1 + dirX * 0.5 y1 + 0.4) pName
                  "centerLeft" "R0" "roman" 0.0) t)
                when(lbl && car(lbl) errset(dbAddFigToNet(car(lbl) net) t)))
              errset(dbCreatePin(net nil pName) t)
              net->isPin = t)))
        i = i + 1))

    errset(dbSave(cv) t)
    errset(dbClose(cv) t)
    librfViewsNoteCell(sprintf(nil "%s/%s/symbol" libName cellName))
    t))

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

    when(cellName == nil   librfViewsErr("缺少参数 cell"))
    when(snpPath == nil    librfViewsErr("缺少参数 snp"))
    when(portsStr == nil   librfViewsErr("缺少参数 ports"))
    when(resultPath == nil librfViewsErr("缺少参数 result"))
    when(cellName == nil || snpPath == nil || portsStr == nil || resultPath == nil
      librfWriteViewsResult(nil)
      exit())

    portNames = parseString(portsStr " \t")
    when(length(portNames) < 1
      librfViewsErr("ports 为空")
      librfWriteViewsResult(nil)
      exit())

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

    runOk = errset(progn(
      librfViewsBuildSchematic(libName cellName snpPath portNames libDir)
      librfViewsBuildSymbol(libName cellName portNames kind libDir) ) t)
    when(runOk == nil
      librfViewsErr("SKILL 运行异常 (见 stdout 报错)"))

    librf__vpins = nil
    foreach(pName portNames
      librfViewsNotePin(pName "inputOutput"))
    librfWriteViewsResult(librf__vok)
    exit()))

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

procedure(librfEmxDelView(libName cellName viewName)
  let((obj)
    obj = ddGetObj(libName cellName viewName)
    when(obj
      errset(ddDeleteObj(obj) t))))

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

procedure(librfEmxInit()
  prog((iface loadRes stub)
    when(boundp('librf__emx_ready) && librf__emx_ready
      return(t))
    iface = librfEmxIfaceDir()
    when(iface == nil
      librfEmxErr("找不到 EMX interface 目录 (emxconfig.il). 请设置 EMX_INTERFACE_PATH 或安装 EMX")
      return(nil))
    EMX_interface_path = iface

    loadRes = errset(load(strcat(iface "/emxskill/emxconfig.il")) t)
    unless(fboundp('EMX_make_n_port_symbol)
      librfEmxErr(sprintf(nil
        "EMX 函数未加载 (EMX_make_n_port_symbol). emxconfig 路径=%s load=%L" iface loadRes))
      return(nil))

    stub = "
(define (schCheck cv) t)
(define (EMX_error @rest args) (error (apply sprintf (cons nil args))))
(define (EMX_unexpected_error @rest args) (error (apply sprintf (cons nil args))))
(define (EMX_warning @rest args) (printf \"WARN %s\\n\" (apply sprintf (cons nil args))))
(define (EMX_informational @rest args) (printf \"INFO %s\\n\" (apply sprintf (cons nil args))))
(define (EMX_warning_okcancel @rest args) t)
"
    errset(evalstring(stub) t)

    EMX_force_overwrite = t

    EMX_nport_sparam_view_name = nil
    librf__emx_ready = t
    return(t)))

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

procedure(librfEmxBuild(libName cellName snpPath portNames kind libDir)
  prog((terminals dirs signals pinDirs symFn res src nTotal tag)
    nTotal = length(portNames) + 1

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

    foreach(v list("symbol_nport" "schematic_nport" "symbol" "schematic")
      librfEmxDelView(libName cellName v))

    res = errset(EMX_make_n_port_symbol(libName cellName "symbol_nport"
      terminals dirs) t)
    if(res && car(res)
      then librfEmxNoteCell(sprintf(nil "%s/%s/symbol_nport" libName cellName))
      else progn(
        librfEmxErr("EMX_make_n_port_symbol 失败 (symbol_nport)")
        return(nil)))

    res = errset(EMX_make_n_port_schematic_no_dev(nil libName cellName
      "schematic_nport" signals pinDirs snpPath) t)
    if(res && car(res)
      then librfEmxNoteCell(sprintf(nil "%s/%s/schematic_nport" libName cellName))
      else progn(
        librfEmxErr("EMX_make_n_port_schematic_no_dev 失败 (schematic_nport)")
        return(nil)))

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

    foreach(pn portNames
      librfEmxNotePin(pn "inputOutput"))
    librfEmxNotePin("GND" "inputOutput")
    return(t)))

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

    unless(isFile(snpPath)
      librfEmxErr(sprintf(nil "sNp 文件不存在: %s" snpPath)))

    when(librf__eok
      let((ext npExt)

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

    when(librf__eok
      foreach(v list("schematic" "symbol" "schematic_nport" "symbol_nport")
        when(librfEmxIsLocked(libDir cellName v)
          librfEmxErr(sprintf(nil
            "view 被 Virtuoso 占用: %s/%s/%s 存在 *.cdslck* -- 请先关闭 Virtuoso 中该 cellview 的编辑器再重试"
            libName cellName v)))))
    when(librf__eok == nil
      librfEmxWriteResult(nil)
      exit())

    runOk = errset(librfEmxInit() t)
    unless(runOk && car(runOk) && boundp('librf__emx_ready) && librf__emx_ready
      when(librf__eok
        librfEmxErr("EMX 初始化失败 (详见 stdout)"))
      librfEmxWriteResult(nil)
      exit())

    runOk = errset(librfEmxBuild(libName cellName snpPath portNames kind libDir) t)
    when(runOk == nil
      librfEmxErr("SKILL 运行异常 (见 stdout 报错)"))
    librfEmxWriteResult(librf__eok)
    exit()))

defvar(lriReport nil)
defvar(lriStepList nil)

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





procedure(lriTake(l k)
  let((out j)
    out = nil
    for(j 0 min(k length(l))-1
      out = append(out list(nth(j l))))
    out))



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








procedure(lriAssert(tag ok msg)
  lriReport = cons(list(tag ok msg) lriReport)
  unless(ok  librfErr(sprintf(nil "lri[%s] %s" tag msg)))
  t)





procedure(lriSamePt(a b)
  (abs(car(a)-car(b)) < 1.0e-6 && abs(cadr(a)-cadr(b)) < 1.0e-6))

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
      ; Inner miter of the end flare, then close the band at y0 +/- h: the end
      ; has to be the equal-width band (= lriSweep of this polyline).  The old
      ; tail y0 + sg*(h + back + abs(x1-x0)) added |x1-x0| - back too much and
      ; painted an extra wedge of TM1 under the next ring's TM2.  The miter
      ; sign follows (x1-x0) so the band end never depends on it.
      list((x1 - (if(x1 >= x0 then back else -back))) (y0 + sg * h))
      list(x0 (y0 + sg * h)))))

procedure(lriPtTrimmed(pt pts)
  let((hit)
    hit = nil
    foreach(q pts  when(lriSamePt(pt q)  hit = t))
    hit))







; =============================================================== second-generation symmetric inductor
; lriI3: horizontal/vertical radius, any integer turns, adjustable chamfer and differential leads.
; This section is the reference SKILL implementation of the geometry spec.
; Differences from the first-generation lriBuildSym:
;   1) ring family a_k=a0-kp, b_k=b0-kp, c_k=max(0,c0-kp(2-sqrt2)) is an exact inward offset;
;      the chamfer no longer shrinks proportionally, and the inter-turn gap stays s;
;   2) rx/ry are independent; chamfer is a 0..1 fraction (2-sqrt2 = regular octagon);
;   3) turns per arm may be any positive integer; with an even count the bridge/centre tap moves to the top;
;   4) lead length/spacing/width are adjustable; ports at (+/-(gap+lw)/2, b0+len).
defvar(lriI3WedgeMin 2.6)
defvar(lriI3DeltaFrac 0.5)

; Parallel inward-offset ring family. Returns (rings p N), ring=(a b c).
procedure(lriI3Rings(rx ry w s n chamfer)
  prog((p a0 b0 c0 N out a b c)
    when(rx <= 0.0 || ry <= 0.0 || w <= 0.0 || s <= 0.0
      librfErr("lri[i3]: rx/ry/w/s 必须 > 0")
      return(nil))
    when(abs(n - fix(n + 0.5)) > 1.0e-6
      librfErr(sprintf(nil
        "lri[i3]: n=%.3f 必须为整数（每臂圈数）" n))
      return(nil))
    p = w + s
    a0 = rx - w / 2.0
    b0 = ry - w / 2.0
    when(min(a0 b0) <= 0.0
      librfErr(sprintf(nil "lri[i3]: rx/ry 太小（w=%.3f）" w))
      return(nil))
    c0 = max(0.0 min(1.0 chamfer)) * min(a0 b0)
    N = max(1 fix(n + 0.5))
    out = nil
    for(k 0 N-1
      a = a0 - k * p
      b = b0 - k * p
      when(min(a b) <= w / 2.0 + 1.0e-9
        librfErr(sprintf(nil
          "lri[i3]: 第 %d 圈放不下 (a=%.2f b=%.2f)；请增大 rx/ry 或减小 n/w/s"
          k a b))
        return(nil))
      c = max(0.0 c0 - k * p * (2.0 - sqrt(2.0)))
      ; Once the 45-degree chamfer consumes the whole straight edge the ring degenerates into a rhombus; the correct inward-offset solution is c = min(a b)
      when(c > min(a b)  c = min(a b))
      out = append(out list(list(a b c))))
    return(list(out p N))))

; (the fixed-xj half-turn constructors lriI3HalfR/L were replaced by
;  lriI3HalfXY, which takes independent end abscissae)

; Turn-flush outer-window shift (dmin dmax).

; Largest odd cut count that fits the wide end -> (span ncut)
procedure(lriI3ViaSpan(w cut sp encl)
  let((sm n span)
    sm = w - 2.0 * encl
    n = fix((sm + sp) / (cut + sp) + 1.0e-9)
    when(n < 1  n = 1)
    when(mod(n 2) == 0  n = n - 1)
    when(n < 1  n = 1)
    span = n * cut + (n - 1) * sp
    list(span n)))

; ---- rfgeom law: concentric octagons joined by symmetric 45 deg X crossings
; Closed-form outward pull-back of a ring end next to a crossing, so the
; edge-to-edge gap at the crossing is exactly the coil spacing s.  Mirrors
; rfgeom.cross_gap; replaces the old lriI3Window/delta heuristic.
procedure(lriI3CrossGap(w s p)
  max(0.0 sqrt(2.0) * (s + w / 2.0) - p + w / 2.0))

; Half ring with independent end abscissae, mirroring rfgeom.half_top_to_bot.
; xTop/xBot are UNSIGNED and sg picks the side: +1 right half, -1 mirrored
; left half.  Passing a pre-signed x here negates the endpoints twice and
; drags the ring end onto the opposite side (shorts the lead row).
procedure(lriI3HalfXY(a b c xTop xBot sg)
  prog((ac nb bc pts)
    when(max(xTop xBot) > a - c + 1.0e-9
      librfErr(sprintf(nil
        "lri[i3]: 环端 x=%.3f 超出顶边直段 (a-c=%.3f)；减小 w/s、增大 rx/ry 或减小 chamfer"
        max(xTop xBot) (a - c)))
      return(nil))
    ac = a - c
    nb = 0.0 - b
    bc = b - c
    pts = list(list(xTop b) list(ac b) list(a bc)
               list(a 0.0 - bc) list(ac nb) list(xBot nb))
    return(lriDedup(if(sg < 0 then lriMirrorX(pts) else pts)))))

; Single arm. Returns (tops unders vias).
;   tops   = main-metal continuous polyline table
;   unders = 5-point turn-change underpass path (ae av cc bv be)
;   vias   = via pad centres (x y)
; Turn changes follow the rfgeom law: each ring end is pulled out by the
; closed-form cross gap e, and consecutive rings are joined by a symmetric
; 45-degree X crossing on the coil axis (dx = dy = p) -- one branch on the
; main metal, the other on the under layer, with a via pad at both ring ends.
; The old lriI3Window/deltaFrac sliding heuristic is gone: it picked a
; fit-dependent offset out of a window, which is what made the windings
; irregular.  The viaEdge/stub enclosure relation is unchanged -- only where
; the ring ends sit moved, so the DRC-proven via coverage is preserved.
procedure(lriI3Arm(rings p N xj w s side viaEdge stub deltaFrac)
  prog((e xe lmax a0 c0 aN cN unders vias tops run k last r1 dmax
        a b c kside xEnd xLead seg yEnd yNext jog uh av bv ae be)
    e = lriI3CrossGap(w s p)
    xe = xj + e
    a0 = car(car(rings))     c0 = caddr(car(rings))
    aN = car(lriLast(rings)) cN = caddr(lriLast(rings))
    lmax = min(a0 - c0  aN - cN) - xe
    ; The via pad sits on the ring's straight edge past the crossing, so the
    ; edge must be at least viaEdge+stub long.  (Do not reuse rfgeom's fixed
    ; 2.0 um floor: there the pad extends a full vlen outward, whereas here
    ; it is viaEdge+stub and lands on the edge itself.)
    when(lmax < viaEdge + stub
      librfErr(sprintf(nil
        "lri[i3]: 45 度跨线放不下通孔区 (可用 %.3f < 需要 %.3f)；请增大 rx/ry 或减小 w/s/n"
        lmax (viaEdge + stub)))
      return(nil))
    unders = nil   vias = nil   tops = nil   run = nil
    for(k 0 N-1
      a = car(nth(k rings))  b = cadr(nth(k rings))  c = caddr(nth(k rings))
      kside = side * if(mod(k 2) == 0 then 1 else -1)
      last = (k == N-1)
      ; both abscissae UNSIGNED -- lriI3HalfXY mirrors by kside.  Passing a
      ; pre-signed x negates the endpoints twice and drags the ring end onto
      ; the opposite side (shorts the lead row).  The free end of the
      ; innermost ring lands on xj to meet the bridge; only ring changes use
      ; the pulled-out xe -- using xe there leaves the bridge hanging e away.
      ; entry = lead (k == 0) or a crossing (xe); exit = next crossing (xe)
      ; or, on the innermost ring, the bridge (xj).  Odd rings run bottom ->
      ; top, so their entry is the BOTTOM end of lriI3HalfXY.  Giving the
      ; innermost ring's entry xj left 0.95 um to the crossing (s = 3).
      xEnd  = if(last then xj else xe)
      xLead = if(k == 0 then xj else xe)
      seg = if(mod(k 2) == 0
              then lriI3HalfXY(a b c xLead xEnd kside)
              else lriI3HalfXY(a b c xEnd xLead kside))
      when(seg == nil  return(nil))
      when(mod(k 2) == 1  seg = reverse(seg))
      run = if(run then append(run cdr(seg)) else seg)
      unless(last
        yEnd  = if(mod(k 2) == 0 then 0.0 - b else b)
        yNext = if(mod(k 2) == 0 then 0.0 - (b - p) else (b - p))
        ; Feasibility window at this ring change.  e == (sqrt(2)-1)*(s+w/2) is
        ; the tightest legal pull-back (gap at the crossing == s), so it plays
        ; the role of the old dmin; dmax is kept because it also accounts for
        ; the 45-degree chamfer miter w*(sqrt(2)-1)/2 at the inner ring end,
        ; which lmax alone does not -- dropping it let w=14/rx=60 build, a
        ; case the suite expects to be rejected.
        r1 = nth(k + 1 rings)
        dmax = min(((a - c) - xj - (viaEdge + stub))
                   ((car(r1) - caddr(r1)) - w * (sqrt(2.0) - 1.0) / 2.0 - xj)
                   ((a - c) + s / 2.0 - sqrt(2.0) * (w / 2.0 + s)))
        when(dmax < e - 1.0e-9
          librfErr(sprintf(nil
            "lri[i3]: 换圈 k=%d 的 45 度夹缝放不下（需 %.3f > 可用 %.3f）；请增大 rx/ry 或减小 w/s"
            k e dmax))
          return(nil))
        uh = if(side < 0 then mod(k 2) == 0 else mod(k 2) == 1)
        av = list(kside * (xe + viaEdge) yEnd)
        bv = list(0.0 - kside * (xe + viaEdge) yNext)
        ae = list(kside * (xe + viaEdge + stub) yEnd)
        be = list(0.0 - kside * (xe + viaEdge + stub) yNext)
        cond(
          (uh
            tops = append(tops list(run))
            run = nil
            ; rfgeom law: the X crossing is centred on the coil axis.  The
            ; diagonal runs (kside*xj,yEnd) -> (-kside*xj,yNext), so dx = dy
            ; = p and the crossing point is exactly x = 0.  The old (av,cc)
            ; form started the diagonal at the via pad instead, putting it
            ; off-axis -- that asymmetry is the irregularity this rewrite
            ; removes.  Both horizontal runs still sweep through the via pads
            ; (xj .. xe+viaEdge+stub covers xe+viaEdge on each side).
            unders = append(unders
                     list(list(ae
                               list(kside * xj yEnd)
                               list(0.0 - kside * xj yNext)
                               list(0.0 - kside * xe yNext)
                               be)))
            vias = append(vias list(av bv)))
          (t
            ; the X crossing stays on the main metal this turn: same 4-point
            ; jog, folded into the run (av/bv are the via-column centres)
            jog = list(list(kside * xe yEnd) list(kside * xj yEnd)
                       list(0.0 - kside * xj yNext)
                       list(0.0 - kside * xe yNext))
            run = append(run cdr(jog))))))
    tops = append(tops list(run))
    return(list(tops unders vias))))

; ------------------------------------------------- via chain for any metal pair
; The stack table is injected by the process profile (tech/<pdk>_profile.il or tech_to_skill);
; it defaults to empty, and sym falls back to a single via_sp hop (works with generic templates).
; Metals bottom to top; via = (name lower upper cut space lower-enclosure upper-enclosure).
defvar(librfStackMetals nil)
defvar(librfStackVias nil)
defvar(librfMetalGds nil)
defvar(librfViaGds nil)

procedure(lriStackIndex(nm)
  let((i)
    i = -1
    for(k 0 (sub1 length(librfStackMetals))
      when(equal(nth(k librfStackMetals) nm)  (setq i k)))
    i))

procedure(lriViaForStep(lo hi)
  let((out)
    foreach(v librfStackVias
      when(and(equal(nth(1 v) lo) equal(nth(2 v) hi))  (setq out v)))
    out))

; Via table stepping from lo up to hi; different layers or reversed direction -> nil.
procedure(lriViaChain(lo hi)
  let((i0 i1 v out)
    i0 = lriStackIndex(lo)
    i1 = lriStackIndex(hi)
    cond(
      ((or (i0 < 0) (i1 < 0) (i0 >= i1))  nil)
      (t
        out = nil
        for(k i0 (sub1 i1)
          v = lriViaForStep(nth(k librfStackMetals)
                            nth((add1 k) librfStackMetals))
          when(v  (setq out (append out list(v)))))
        out))))

procedure(lriStackBelow(nm)
  let((i)
    i = lriStackIndex(nm)
    if(i > 0 then nth((sub1 i) librfStackMetals) else nil)))

; One via column (x fixed, ncut cuts along y); add landing pads on intermediate metals when spanning layers.
procedure(lriViaColStack(cv x y w chain)
  prog((v nm cut sp encLo encHi enc si span ncut h i
        prevSpan prevEnc prevCut midName halfX halfY)
    prevSpan = 0.0  prevEnc = 0.0  prevCut = 0.0
    foreach(v chain
      nm = car(v)
      cut = 1.0 * nth(3 v)
      sp = 1.0 * nth(4 v)
      encLo = 1.0 * nth(5 v)
      encHi = 1.0 * nth(6 v)
      enc = max(encLo encHi)
      si = lriI3ViaSpan(w cut sp enc)
      span = car(si)  ncut = cadr(si)
      h = (ncut - 1) * (cut + sp) / 2.0
      librfNoteLayer(nm)
      for(i 0 (sub1 ncut)
        librfRect(cv librfViaPair(nm)
          x - cut / 2.0  (y - h) + i * (cut + sp) - cut / 2.0
          x + cut / 2.0  (y - h) + i * (cut + sp) + cut / 2.0))
      when(prevSpan > 0.0
        ; Intermediate metal must enclose both the upper and lower via rows: take the max of the required enclosures
        halfX = max((cut / 2.0 + encLo) (prevCut / 2.0 + prevEnc))
        halfY = max((span / 2.0 + encLo) (prevSpan / 2.0 + prevEnc))
        midName = nth(1 v)
        librfNoteLayer(midName)
        librfRect(cv librfMetalPair(midName)
          x - halfX  y - halfY  x + halfX  y + halfY))
      prevSpan = span  prevEnc = encHi  prevCut = cut)))

; ---------------------------------------------------------------- stack
; Single via (square cut, centre x/y).
procedure(lriViaSq(cv vpair x y cut)
  librfRect(cv vpair (x - cut / 2.0) (y - cut / 2.0)
                     (x + cut / 2.0) (y + cut / 2.0)))

; Turn-change via columns occupy the ring edge they sit on; stack stitching
; fills that same edge and would collide with them (G5b via spacing).  Build
; sets this list to (x y halfX halfY) boxes before filling and clears it after.
defvar(lriI3FillSkip nil)
procedure(lriI3FillHit(x y)
  let((hit z)
    hit = nil
    foreach(z lriI3FillSkip
      when(and(abs(x - nth(0 z)) < nth(2 z)  abs(y - nth(1 z)) < nth(3 z))
        hit = t))
    hit))

procedure(lriStackFill(cv vlyr pts w cut sp enc)
  prog((vpair r dpitch nrow x1 y1 x2 y2 dx dy L ux uy nx ny s j off px py)
    r = w / 2.0 - enc - cut / 2.0
    when(r < 0.0
      librfErr(sprintf(nil "lri[i3]: 叠层线宽 %.2f 放不下通孔" w))
      return(nil))
    ; On 45-degree segments the corner-to-corner spacing between adjacent square cuts is along-distance - cut*sqrt(2),
    ; so both along-distance and row pitch start from (cut+sp)*sqrt(2); 0/90 segments satisfy it as well.
    dpitch = (cut + sp) * sqrt(2.0)
    nrow = fix(2.0 * r / dpitch + 1.0e-9) + 1
    when(nrow < 1  nrow = 1)
    when(nrow > 9  nrow = 9)
    vpair = librfViaPair(librfLyr(vlyr))
    librfNoteLayer(librfLyr(vlyr))
    for(k 0 length(pts) - 2
      x1 = car(nth(k pts))      y1 = cadr(nth(k pts))
      x2 = car(nth(k + 1 pts))  y2 = cadr(nth(k + 1 pts))
      dx = x2 - x1  dy = y2 - y1
      L = sqrt(dx * dx + dy * dy)
      when(L > 1.0e-9
        ux = dx / L  uy = dy / L
        nx = 0.0 - uy  ny = ux
        s = r
        while(s <= L - r + 1.0e-9
          for(j 0 nrow - 1
            off = if(nrow > 1
                     then (0.0 - r) + j * (2.0 * r) / (nrow - 1.0)
                     else 0.0)
            px = x1 + ux * s + nx * off
            py = y1 + uy * s + ny * off
            unless(lriI3FillHit(px py)
              lriViaSq(cv vpair px py cut)))
          s = s + dpitch)))
    t))


; Turn-change underpass drawn on the given metal (in stack mode it is the layer below aux, otherwise aux itself).
; In this PDK's 1P8M_6Ic_1TMc_1MTTc stack only M6/TM1/TM2 are metal6/7/8;
; M7/M8 (67/68) are outside this stack (see the LVS deck TOPMETAL 8 branch and the EMX proc).
procedure(lriBridgeOn(cv metalName pts w)
  when(length(pts) == 5
    librfNoteLayer(metalName)
    dbCreatePolygon(cv librfMetalPair(metalName)
      lriUnderPolyPts(pts w))))

; Differential symmetric inductor main entry (kind=sym). Parameters:
;   rx ry outer-metal half axes, n turns per arm, w s line width/spacing, chamfer 0..1,
;   lead_len lead centreline length, lead_gap lead edge spacing, lead_w lead width, ct centre tap.
procedure(lriI3BuildSym(cv req)
  prog((dout rx ry n w s chamfer leadLen leadGap leadW ct stack
        q rings p N xj a0 b0 qa qb ta ua va tb ub vb
        br bridgeY bl M ML T vcut vsp ven viaEdge back stub spanInfo ncut span
        xPort yPort leadA leadB mName tName
        mainName auxName chain vTop crossName chainUse
        wsMain wsAux ws)
    dout = librfReqNum(req 'dout 160.0)
    rx = librfReqNum(req 'rx dout / 2.0)
    ry = librfReqNum(req 'ry dout / 2.0)
    n = librfReqNum(req 'n 3.0)
    w = librfReqNum(req 'w 10.0)
    s = librfReqNum(req 's 3.2)
    chamfer = librfReqNum(req 'chamfer (2.0 - sqrt(2.0)))
    leadLen = librfReqNum(req 'lead_len 20.0)
    leadGap = librfReqNum(req 'lead_gap s)
    leadW = librfReqNum(req 'lead_w w)
    ct = librfReqNum(req 'ct 0)
    stack = librfReqNum(req 'stack 0)
    when(leadGap < s - 1.0e-9
      librfErr(sprintf(nil "lri[i3]: lead_gap=%.3f < s=%.3f"
        (1.0 * leadGap) (1.0 * s)))
      return(nil))
    q = lriI3Rings(rx ry w s n chamfer)
    when(q == nil  return(nil))
    rings = car(q)  p = cadr(q)  N = caddr(q)
    xj = p / 2.0
    a0 = car(car(rings))
    b0 = cadr(car(rings))
    xPort = (leadGap + leadW) / 2.0
    yPort = b0 + leadLen
    when(leadLen < w / 2.0 - 1.0e-9
      librfErr("lri[i3]: lead_len < w/2，端口会落在外圈金属内")
      return(nil))
    ; Leads may go inward or outward from the window edge xj: only require centre > 0 and stay inside the outer ring
    when(xPort < 1.0e-9
      librfErr(sprintf(nil "lri[i3]: lead_gap 太小（xPort=%.3f）"
        (1.0 * xPort)))
      return(nil))
    when(xPort > a0 + 1.0e-9
      librfErr(sprintf(nil
        "lri[i3]: lead_gap=%.3f 把引线推到外圈之外（xPort=%.3f > a0=%.3f）"
        leadGap xPort a0))
      return(nil))
    mainName = librfReqStr(req 'metal_main librfLyr("spiral"))
    auxName  = librfReqStr(req 'metal_aux  librfLyr("underpass"))
    ; Empty string = use the current profile's default role; the CDF defaults need no change when the process changes
    when(mainName == ""  mainName = librfLyr("spiral"))
    when(auxName == ""   auxName = librfLyr("underpass"))
    when(equal(mainName auxName)
      librfErr("lri[i3]: metal_main 与 metal_aux 不能同层")
      return(nil))
    ; Wide-metal long parallel spacing: the differential arms are two separate polygons at the gap (spacing = s),
    ; and so are the two output leads (spacing = lead_gap); both must satisfy *_2b/*_2c/MTT2_2b.
    wsMain = librfDrcWideSpace(mainName)
    wsAux  = if(stack > 0.5  librfDrcWideSpace(auxName)  0.0)
    ws = max(wsMain wsAux)
    when((s < ws - 1.0e-9) || (leadGap < ws - 1.0e-9)
      librfErr(sprintf(nil
        "lri[i3]: s=%.3f / lead_gap=%.3f < %s 宽金属长平行间距 %.3f；差分两臂与两条引线都是独立多边形，间距不能更小"
        (1.0 * s) (1.0 * leadGap) mainName (1.0 * ws)))
      return(nil))
    chain = lriViaChain(auxName mainName)
    when(null(chain) && stack < 0.5
      ; The profile has no stack table: use a single via_sp hop for the current process (legacy behavior)
      librfWarn(sprintf(nil
        "lri[i3]: 工艺档无 %s -> %s 叠层表，按 via_sp=%s 单跳连接"
        auxName mainName librfLyr("via_sp")))
      chain = list(list(librfLyr("via_sp") auxName mainName
                        librfDrcViaNum("cut" 0.5)
                        librfDrcViaNum("spacing" 0.5)
                        librfDrcViaNum("enclosure" 0.2)
                        librfDrcViaNum("enclosure" 0.2))))
    when(null(chain)
      librfErr(sprintf(nil "lri[i3]: %s -> %s 没有直连通孔链（叠层表顺序不对？）"
        auxName mainName))
      return(nil))
    ; Stack semantics mean "one turn's current splits between two layers in parallel": only adjacent metals qualify;
    ; cross-level stacks have no intermediate layer carrying the full turn current, so stitch vias would float (DRC UTV2).
    when((stack > 0.5) && ((lriStackIndex(mainName) - lriStackIndex(auxName)) != 1)
      librfErr(sprintf(nil
        "lri[i3]: 叠层要求主/副金属相邻；%s -> %s 跨 %d 级，请换相邻层或 stack=0"
        auxName mainName (lriStackIndex(mainName) - lriStackIndex(auxName))))
      return(nil))
    vTop = lriLast(chain)
    vcut = librfDrcViaNum("cut" 0.5)
    vsp = librfDrcViaNum("spacing" 0.5)
    ven = librfDrcViaNum("enclosure" 0.2)
    unless(equal(car(vTop) librfLyr("via_sp"))
      ; For a non-default metal pair, use that via's own rules; do not draw V5 with TV2's 0.36/0.34.
      vcut = 1.0 * nth(3 vTop)
      vsp  = 1.0 * nth(4 vTop)
      ven  = max(1.0 * nth(5 vTop) 1.0 * nth(6 vTop)))
    viaEdge = vcut / 2.0 + ven
    back = w / 2.0 * (sqrt(2.0) - 1.0)
    stub = max(viaEdge max(0.0 lriI3WedgeMin - back))
    spanInfo = lriI3ViaSpan(w vcut vsp ven)
    span = car(spanInfo)  ncut = cadr(spanInfo)
    ; Stacked: the turn-change underpass uses the layer below aux, then a level-by-level via chain returns to the main metal
    crossName = nil
    chainUse = chain
    when(stack > 0.5
      crossName = lriStackBelow(auxName)
      when(null(crossName)
        librfErr(sprintf(nil "lri[i3]: 叠层需要 %s 下方还有金属层" auxName))
        return(nil))
      chainUse = lriViaChain(crossName mainName)
      when(null(chainUse)
        librfErr(sprintf(nil "lri[i3]: %s -> %s 通孔链不完整"
          crossName mainName))
        return(nil)))
    qa = lriI3Arm(rings p N xj w s 1 viaEdge stub lriI3DeltaFrac)
    qb = lriI3Arm(rings p N xj w s -1 viaEdge stub lriI3DeltaFrac)
    when(qa == nil || qb == nil  return(nil))
    ta = nth(0 qa)  ua = nth(1 qa)  va = nth(2 qa)
    tb = nth(0 qb)  ub = nth(1 qb)  vb = nth(2 qb)
    br = list(lriLast(lriLast(ta)) lriLast(lriLast(tb)))
    bridgeY = cadr(car(br))
    when(abs(cadr(cadr(br)) - bridgeY) > 1.0e-9
      librfErr("lri[i3]: 桥两端不等高")
      return(nil))
    M = librfMetalPair(mainName)
    ML = librfMetalPair(auxName)
    T = librfTxtPair(mainName)
    mName = car(M)  tName = car(T)
    ; Main winding: all top-layer continuous segments (no square caps at flush ends, avoiding 45-degree gaps)
    ; Keep stack stitching clear of the turn-change via columns -- they sit on
    ; the same ring edge that lriStackFill walks, and collided there (G5b).
    when(stack > 0.5
      lriI3FillSkip = nil
      foreach(z append(va vb)
        lriI3FillSkip = append(lriI3FillSkip
          list(list(car(z) cadr(z) (vcut + vsp) (span / 2.0 + ven + vcut))))))
    foreach(run append(ta tb)
      librfPathSweepEx(cv M run w nil nil)
      when(stack > 0.5
        librfPathSweepEx(cv ML run w nil nil)
        lriStackFill(cv car(vTop) run w vcut vsp ven)))
    ; Series bridge (inner ends of the two arms)
    librfPathSweepEx(cv M br w nil nil)
    when(stack > 0.5
      librfPathSweepEx(cv ML br w nil nil)
      lriStackFill(cv car(vTop) br w vcut vsp ven))
    ; Turn-change underpass (complementary to the other arm; different layers at the crossing); in stack mode use M8 and return to TM1 via TV1
    foreach(path append(ua ub)
      lriBridgeOn(cv (cond ((stack > 0.5) crossName) (t auxName)) path w))
    ; Wide-end via columns
    foreach(v append(va vb)
      lriViaColStack(cv car(v) cadr(v) w (cond ((stack > 0.5) chainUse) (t chain))))
    ; Differential leads: radial stub + vertical escape
    leadA = list(list(xj b0) list(xPort b0) list(xPort yPort))
    leadB = list(list(0.0 - xj b0) list(0.0 - xPort b0)
                 list(0.0 - xPort yPort))
    foreach(ld list(leadA leadB)
      librfPathSweepEx(cv M ld leadW nil nil)
      when(stack > 0.5
        librfPathSweepEx(cv ML ld leadW nil nil)
        lriStackFill(cv car(vTop) ld leadW vcut vsp ven)))
    librfTipLabel(cv M T xPort yPort 'N "p01")
    librfTipLabel(cv M T 0.0 - xPort yPort 'N "p02")
    ; Centre tap: inner-bridge midpoint -> origin pad
    when(ct > 0.5
      bl = cadr(lriLast(rings))
      when(bl < 1.5 * w + s
        librfErr(sprintf(nil
          "lri[i3]: 内孔太小放不下中心抽头（b_last=%.2f）" bl))
        return(nil))
      let((ctp)
        ctp = list(list(0.0 bridgeY) list(0.0 0.0))
        librfPathSweepEx(cv M ctp w nil nil)
        librfRect(cv M 0.0 - w / 2.0 0.0 - w / 2.0 w / 2.0 w / 2.0)
        when(stack > 0.5
          librfPathSweepEx(cv ML ctp w nil nil)
          lriStackFill(cv car(vTop)
            list(list(0.0 bridgeY - w / 2.0) list(0.0 0.0))
            w vcut vsp ven)
          librfRect(cv ML 0.0 - w / 2.0 0.0 - w / 2.0 w / 2.0 w / 2.0)))
      let((ly)
        ly = bridgeY - if(bridgeY >= 0.0 then 1.5 else -1.5)
        librfPortPin(cv mName T 0.0 ly "ct1")
        librfNotePort("ct1" 0.0 ly tName)))
    lriI3FillSkip = nil
    ; Assertions
    let((x1 y1 x2 y2)
      x1 = 1.0e30  y1 = 1.0e30  x2 = -1.0e30  y2 = -1.0e30
      foreach(pl append(append(ta tb) list(leadA leadB))
        foreach(pt pl
          x1 = min(x1 car(pt))  y1 = min(y1 cadr(pt))
          x2 = max(x2 car(pt))  y2 = max(y2 cadr(pt))))
      lriAssert("i3.bboxX" (abs((x2 - x1 + w) - 2.0 * rx) < 0.02)
        sprintf(nil "bboxX %.3f != %.3f" (x2 - x1 + w) (2.0 * rx)))
      lriAssert("i3.bboxY" (abs((y2 - y1 + w / 2.0) - (yPort + ry)) < 0.02)
        sprintf(nil "bboxY %.3f != %.3f" (y2 - y1 + w / 2.0)
          (yPort + ry)))
    let((angOK)
      angOK = t
      foreach(run append(ta tb)
        unless(lriAngleOK(run)  angOK = nil))
      lriAssert("i3.angle" angOK "存在非 0/45/90 段"))
    return(t))))


; ---------------------------------------------------------------- selftest
; Real-machine smoke: exercise the sym geometry kernel.  The legacy
; librfSquareSpiralPts-based selftest went away with the old kernel.
procedure(librfSelfTest()
  let((ok nPass nFail q rings armA armB a w s chamfer)
    gLibrfSelfFail = nil
    nPass = 0  nFail = 0
    printf("=== LIBRF layout selftest (sym only) ===
")
    w = 10.0  s = 3.2  chamfer = 2.0 - sqrt(2.0)
    q = lriI3Rings(80.0 80.0 w s 2 chamfer)
    ok = (q != nil && length(car(q)) == 2)
    if(ok then nPass = nPass + 1  printf("PASS lriI3Rings n=2 -> 2 rings
")
      else nFail = nFail + 1
           printf("FAIL lriI3Rings -> %L
" q))
    when(ok
      rings = car(q)
      armA = lriI3Arm(rings cadr(q) 2 cadr(q) / 2.0 w s 1 1.0 2.0 0.5)
      armB = lriI3Arm(rings cadr(q) 2 cadr(q) / 2.0 w s -1 1.0 2.0 0.5)
      ok = (armA != nil && armB != nil &&
            length(car(armA)) == length(car(armB)))
      if(ok then nPass = nPass + 1
             printf("PASS lriI3Arm: arms mirror (tops %d vs %d)
"
               length(car(armA)) length(car(armB)))
        else nFail = nFail + 1
             printf("FAIL lriI3Arm arms unequal
"))
      a = car(car(armA))
      ok = lriAngleOK(a)
      if(ok then nPass = nPass + 1
             printf("PASS lriAngleOK arm pts only 0/45/90/135
")
        else nFail = nFail + 1
             printf("FAIL lriAngleOK arm pts
")))
    printf("=== %d PASS / %d FAIL ===
" nPass nFail)
    nFail == 0))
