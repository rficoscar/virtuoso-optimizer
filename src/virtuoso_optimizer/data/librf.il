
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

procedure(librfDinFromParams(n w s dout)
  let((pitch m)
    pitch = w + s
    m = fix(ceiling(n - 1.0e-6))
    dout - 2.0 * (m * pitch - s)))

procedure(librfSquareSpiralPts(n w s dout)
  let((pitch L0 nSeg a0 x y pts dirs k seg dx dy stopMin)
    pitch = w + s
    L0 = dout - w
    nSeg = fix(round(4.0 * n))
    a0 = L0 / 2.0
    x = a0  y = 0.0 - a0
    pts = list(list(x y))

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
      pts = cons(list(x y) pts)
      k = k + 1)

    when(k < nSeg
      librfWarn(sprintf(nil "螺旋截断: 请求 %d 段, 实际 %d 段 (seg<stopMin=%.3f), 实际匝数 %.2f"
        nSeg k stopMin (k / 4.0))))
    reverse(pts)))

procedure(librfOctLine(k r0 pitch)

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

procedure(librfSpiralPts(shape n w s dout)
  cond(
    (shape == "oct" || shape == "octagon"
      librfOctSpiralPts(n w s dout))
    (shape == "sq" || shape == "square"
      librfSquareSpiralPts(n w s dout))
    (t
      librfErr(sprintf(nil "未知 shape: %s (仅支持 sq/oct), 已按 sq 绘制" shape))
      librfSquareSpiralPts(n w s dout))))

procedure(librfPtsBBox(pts)
  let((x1 y1 x2 y2 p)
    x1 = 1.0e30  y1 = 1.0e30  x2 = -1.0e30  y2 = -1.0e30
    foreach(p pts
      x1 = min(x1 car(p))  y1 = min(y1 cadr(p))
      x2 = max(x2 car(p))  y2 = max(y2 cadr(p)))
    list(x1 y1 x2 y2)))

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

    when(abs(ex) < 1.0e-9 && abs(ey) < 1.0e-9
      when(abs(cx - x0) > 0.02 || abs(cy - y0) > 0.02
        librfErr(sprintf(nil
          "%s 螺旋中心偏移: (%.3f,%.3f), 期望 (%.3f,%.3f)"
          tag cx cy x0 y0))))))

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

procedure(librfUnderpass(cv xIn yIn xOut yOut exitDir wUp spiralW underName portName stubLen)
  let((halfUp cutW viaS viaE viaOff routePts)
    halfUp = wUp / 2.0

    cutW = librfDrcViaNum("cut" 0.5)
    viaS = librfDrcViaNum("spacing" 0.5)
    viaE = librfDrcViaNum("enclosure" 0.2)
    viaOff = (cutW + viaS) / 2.0

    routePts = librfRoutePts(xIn yIn xOut yOut exitDir)
    if(underName == "direct" || underName == "DIRECT"
      then

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

        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW viaS viaE viaE)

        librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
          xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW viaS viaE viaE viaOff viaOff)
        librfPathSweep(cv librfMetalPair(librfLyr("route")) routePts wUp)
        librfRect(cv librfMetalPair(librfLyr("underpass"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)

        librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW viaS viaE viaE viaOff viaOff)
        librfViaArray(cv librfViaPair(librfLyr("via_sp"))
          xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW viaS viaE viaE))

    librfRect(cv librfMetalPair(librfLyr("spiral"))
      xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
    librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
      xOut yOut exitDir spiralW stubLen portName)))

procedure(librfUnderTm(cv xIn yIn xOut yOut exitDir wUp w portName stubLen)
  let((halfUp cutW viaS viaE viaOff routePts)
    halfUp = wUp / 2.0
    cutW = librfDrcViaNum("cut" 0.5)
    viaS = librfDrcViaNum("spacing" 0.5)
    viaE = librfDrcViaNum("enclosure" 0.2)
    viaOff = (cutW + viaS) / 2.0
    routePts = librfRoutePts(xIn yIn xOut yOut exitDir)

    librfRect(cv librfMetalPair(librfLyr("underpass"))
      xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp)
    librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
      xIn - halfUp yIn - halfUp xIn + halfUp yIn + halfUp cutW viaS viaE viaE viaOff viaOff)
    librfPathSweep(cv librfMetalPair(librfLyr("route")) routePts wUp)

    librfRect(cv librfMetalPair(librfLyr("underpass"))
      xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp)
    librfViaArrayOff(cv librfViaPair(librfLyr("via_un"))
      xOut - halfUp yOut - halfUp xOut + halfUp yOut + halfUp cutW viaS viaE viaE viaOff viaOff)
    librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
      xOut yOut exitDir w stubLen portName)))

procedure(librfExitDir(x y)
  cond(
    (and(abs(x) >= abs(y)  x <= 0.0)  'W)
    (abs(x) >= abs(y)                 'E)
    (y <= 0.0                         'S)
    (t                                'N)))

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

procedure(librfShiftPts(pts dx dy)
  let((out)
    out = nil
    foreach(p pts out = cons(list(car(p) + dx cadr(p) + dy) out))
    reverse(out)))

procedure(librfRot180Pts(pts)
  let((out)
    out = nil
    foreach(p pts out = cons(list(0.0 - car(p) 0.0 - cadr(p)) out))
    reverse(out)))

procedure(librfTapPoint(pts frac)
  let((total acc prev cur d ux uy tb x y done out)
    total = 0.0
    prev = car(pts)
    foreach(cur cdr(pts)
      total = total + sqrt((car(cur) - car(prev)) * (car(cur) - car(prev)) +
                           (cadr(cur) - cadr(prev)) * (cadr(cur) - cadr(prev)))
      prev = cur)

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

      nx = car(nprev) + dx
      ny = cadr(nprev) + dy
      out = cons(list(nx ny) out)
      oprev = ocur
      nprev = list(nx ny))
    return(reverse(out))))

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

        pts = librfSpiralPts(shape n w s dout)
        pts = librfStretchPts(pts ex ey)
        pts = librfShiftPts(pts x0 y0)

        librfAssertSpiralBox(pts dout w ex ey x0 y0 "电感")
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) pts w)
        bodyBox = librfCvBBox(cv)
        p0 = car(pts)  p1 = cadr(pts)

        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")

        p0 = car(librfLastPt(pts))  p1 = cadr(librfLastPt(pts))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(pts w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gap = librfAssertPadGap(pts w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv p0 p1 xOut yOut exitDir wUp w under "p02" (stubLen + w / 2.0))
      else

        pts = librfSpiralPts(shape n w s dout)
        pts = librfStretchPts(pts ex ey)
        pts = librfShiftPts(pts x0 y0)

        librfAssertSpiralBox(pts dout w ex ey x0 y0 "电感")
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) pts w)
        bodyBox = librfCvBBox(cv)

        p0 = car(pts)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")

        p0 = car(librfLastPt(pts))  p1 = cadr(librfLastPt(pts))
        exitDir = librfExitDir(p0 - x0 p1 - y0)
        exitXY = librfExitFromPts(pts w wUp x0 y0 exitDir)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gap = librfAssertPadGap(pts w xOut yOut (wUp / 2.0) exitDir 2.0)
        librfUnderpass(cv p0 p1 xOut yOut exitDir wUp w under "p02" (stubLen + w / 2.0))

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

    when(indMark != 0 && indMark != nil
      librfIndMark(cv bodyBox 1.0))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass")))))

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
      then librfDrcParams(w 1.0e30 nil (w > 15.0))
      else librfDrcParams(w gap nil (w > 15.0)))
    if(cpw == 0 || cpw == nil
      then

        librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
          list(list(x0 y0 + w / 2.0) list(x0 + len y0 + w / 2.0)) w)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 y0 + w/2.0 'W w (stubLen + w / 2.0) "p01")
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 + len y0 + w/2.0 'E w (stubLen + w / 2.0) "p02")

        when(gnd != 0 && gnd != nil
          m = 3.0 * w
          librfRect(cv librfMetalPair(gndLayer)
            x0 - m y0 - m x0 + len + m y0 + w + m)

          librfPinLabel(cv librfMetalPair(gndLayer) librfTxtPair(gndLayer)
            x0 - m + 1.5 y0 + w / 2.0 - 1.0
            x0 - m + 3.5 y0 + w / 2.0 + 1.0 "gnd")
          when(fence != 0 && fence != nil
            if(gndLayer == "M3"
              then

                for(i 0 1
                  fy = if(i == 0 then y0 - 2.5 else y0 + w + 2.5)
                  librfRect(cv librfMetalPair(librfLyr("underpass"))
                    x0 fy - vhalf x0 + len fy + vhalf)
                  librfViaArray(cv librfViaPair(librfLyr("via_un"))
                    x0 fy - vhalf x0 + len fy + vhalf vc vs ve ve))
              else
                librfWarn("fence=1 仅在 gnd_layer=M3 时画 V3 栅栏, 已跳过"))))
      else

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

        librfPinLabel(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 + 1.5 y0 - gap - gw/2.0 - 1.0
          x0 + 3.5 y0 - gap - gw/2.0 + 1.0 "gnd")
        librfPinLabel(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          x0 + 1.5 y0 + w + gap + gw/2.0 - 1.0
          x0 + 3.5 y0 + w + gap + gw/2.0 + 1.0 "gnd"))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass"))))

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

        librfAssertSpiralBox(ptsP dout  w ex ey x0 y0 "变压器初级")
        librfAssertSpiralBox(ptsS doutS w ex ey x0 y0 "变压器次级")

        apS = librfAperture(librfStretchPts(librfSpiralPts("sq" ns w s doutS) ex ey) w)
        when(apS / 2.0 < wUp / 2.0 + 1.5
          librfErr(sprintf(nil
            "次级内孔 %.2f 太小: 初级中心下穿孔 (%.2f) 会压到次级金属, 请减 ns 或加大 dout"
            apS wUp)))
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsP w)
        librfPathSweep(cv librfMetalPair(librfLyr("underpass")) ptsS w)
        bodyBox = librfCvBBox(cv)
        allPts = append(ptsP ptsS)

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

        p0 = car(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")

        prevP = car(reverse(cdr(reverse(ptsP))))
        p0 = car(librfLastPt(ptsP))  p1 = cadr(librfLastPt(ptsP))
        if(abs(p0 - car(prevP)) > abs(p1 - cadr(prevP))
          then leadPts = list(list(p0 p1) list(p0 y0) list(x0 y0))
          else leadPts = list(list(p0 p1) list(x0 p1) list(x0 y0)))
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) leadPts w)
        xOut = car(e02)  yOut = cadr(e02)
        gapPad = librfAssertPadGap(allPts w xOut yOut (wUp / 2.0) 'W 2.0)
        librfUnderpass(cv x0 y0 xOut yOut 'W wUp w "route" "p02" (stubLen + w / 2.0))

        p0 = car(ptsS)
        librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p03")

        p0 = car(librfLastPt(ptsS))  p1 = cadr(librfLastPt(ptsS))
        xOut = car(e04)  yOut = cadr(e04)
        gapPad = librfAssertPadGap(allPts w xOut yOut (wUp / 2.0) 'E 2.0)
        librfUnderTm(cv p0 p1 xOut yOut 'E wUp w "p04" (stubLen + w / 2.0))
        librfWarn("stacked/interleaved: 初级 M5 / 次级 M4; 内端分别从中心(W)与东侧经 M3 引出"))

      ((style == "balun" || style == "bal")
        ptsS = librfShiftPts(librfRot180Pts(librfStretchPts(librfSpiralPts("sq" ns w s dout) ex ey)) x0 y0)
        ptsP = librfShiftPts(librfStretchPts(librfSpiralPts("sq" np w s dout) ex ey) x0 y0)

        librfAssertSpiralBox(ptsP dout w ex ey x0 y0 "balun 初级")
        librfAssertSpiralBox(ptsS dout w ex ey x0 y0 "balun 次级")
        apS = librfAperture(librfStretchPts(librfSpiralPts("sq" ns w s dout) ex ey) w)
        when(apS / 2.0 < wUp / 2.0 + 1.5
          librfErr(sprintf(nil
            "balun 次级内孔 %.2f 太小: 初级中心下穿孔会压到次级金属, 请减 ns 或加大 dout" apS)))
        librfPathSweep(cv librfMetalPair(librfLyr("underpass")) ptsS w)
        librfPathSweep(cv librfMetalPair(librfLyr("spiral")) ptsP w)
        bodyBox = librfCvBBox(cv)

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

        p0 = car(ptsS)
        librfStubPort(cv librfMetalPair(librfLyr("underpass")) librfTxtPair(librfLyr("underpass"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p03")

        exitXY = librfExitFromPts(append(ptsP ptsS) w wUp x0 y0 dirP04)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(append(ptsP ptsS) w xOut yOut (wUp / 2.0) dirP04 2.0)
        librfUnderTm(cv car(librfLastPt(ptsS)) cadr(librfLastPt(ptsS))
          xOut yOut dirP04 wUp w "p04" (stubLen + w / 2.0))

        exitXY = librfExitFromPts(append(ptsP ptsS) w wUp x0 y0 dirCT)
        xOut = car(exitXY)  yOut = cadr(exitXY)
        gapPad = librfAssertPadGap(append(ptsP ptsS) w xOut yOut (wUp / 2.0) dirCT 2.0)
        librfUnderTm(cv tx ty xOut yOut dirCT wUp w "ct1" (stubLen + w / 2.0))

        p0 = car(ptsP)
        librfStubPort(cv librfMetalPair(librfLyr("spiral")) librfTxtPair(librfLyr("spiral"))
          car(p0) cadr(p0) librfExitDir(car(p0) - x0 cadr(p0) - y0) w (stubLen + w / 2.0) "p01")

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

    librfPathSweep(cv librfMetalPair(librfLyr("spiral"))
      list(list(x0 y0 + w / 2.0) list(x0 + len y0 + w / 2.0)) w)

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

      librfPinLabel(cv librfMetalPair(librfLyr("ground")) librfTxtPair(librfLyr("ground"))
        x0 - m + 1.5 y0 + (pitch + w) / 2.0 - 1.0
        x0 - m + 3.5 y0 + (pitch + w) / 2.0 + 1.0 "gnd"))
    librfDrcFigs(cv librfLyr("spiral"))
    librfDrcFigs(cv librfLyr("underpass"))))

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

procedure(librfSelfTest()
  let((pts segs i p q pitch L0 nSeg bb ap diffs ok expect nPass nFail)
    gLibrfSelfFail = nil
    nPass = 0  nFail = 0
    printf("=== LIBRF layout selftest ===\n")

    pitch = 12.5   L0 = 90.0
    pts = librfSquareSpiralPts(2.5 10.0 2.5 100.0)
    nSeg = length(pts) - 1
    if(nSeg == 10
      then progn(nPass = nPass + 1  printf("PASS square n=2.5: nSeg=10\n"))
      else progn(nFail = nFail + 1
             printf("FAIL square n=2.5: nSeg=%d (expect 10)\n" nSeg)))

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

    ok = (abs(caar(pts) - 45.0) < 0.002) && (abs(cadar(pts) + 45.0) < 0.002) &&
         (abs(car(librfLastPt(pts)) + 20.0) < 0.002) &&
         (abs(cadr(librfLastPt(pts)) - 20.0) < 0.002)
    if(ok
      then progn(nPass = nPass + 1
             printf("PASS first=(45,-45) last=(-20,20)\n"))
      else progn(nFail = nFail + 1
             printf("FAIL first=(%.3f,%.3f) last=(%.3f,%.3f) (expect (45,-45) (-20,20))\n"
               caar(pts) cadar(pts) car(librfLastPt(pts)) cadr(librfLastPt(pts)))))

    ap = librfAperture(librfSquareSpiralPts(1.0 10.0 2.5 100.0) 10.0)
    if(abs(ap - (100.0 - 2.0 * 10.0)) < 0.002
      then progn(nPass = nPass + 1
             printf("PASS n=1 孔径=%.3f (=dout-2w=80)\n" ap))
      else progn(nFail = nFail + 1
             printf("FAIL n=1 孔径=%.3f (expect 80.000 =dout-2w)\n" ap)))

    ap = librfAperture(librfSquareSpiralPts(2.0 10.0 2.5 100.0) 10.0)
    if(abs(ap - (100.0 - 2.0 * (2.0 * 12.5 - 2.5))) < 0.002
      then progn(nPass = nPass + 1
             printf("PASS n=2 孔径=%.3f (=dout-2(2pitch-s)=55)\n" ap))
      else progn(nFail = nFail + 1
             printf("FAIL n=2 孔径=%.3f (expect 55.000 =dout-2(2pitch-s))\n" ap)))

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

    let((tp sum dd prev cur)
      pts = librfSquareSpiralPts(2.5 10.0 2.5 100.0)
      tp = librfTapPoint(pts 0.5)
      sum = 0.0
      prev = car(pts)
      foreach(cur cdr(pts)
        sum = sum + sqrt((car(cur) - car(prev)) * (car(cur) - car(prev)) +
                         (cadr(cur) - cadr(prev)) * (cadr(cur) - cadr(prev)))
        prev = cur)

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

    printf("via cols: w=2.5 -> %d (expect 1); w=5.5 -> %d (expect 2)\n"
      max(1 fix(((2.5 - 1.0) + 1.5) / 3.0))
      max(1 fix(((5.5 - 1.0) + 1.5) / 3.0)))

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

    let((save sErr sOk r1 r2 r3 r4)
      save = librfDrc
      sErr = librf__errs  sOk = librf__ok

      librfDrc = list(list("min_w" 4.0) list("max_w" 20.0) list("min_s" 2.0))
      r1 = librfDrcParams(3.0 2.5 nil nil)
      r2 = librfDrcParams(5.0 2.5 nil nil)
      r3 = librfDrcParams(21.0 2.5 nil nil)

      librfDrc = nil
      librf__errs = sErr  librf__ok = sOk
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

procedure(lriRing(a b c seam)
  let((r)

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

procedure(lriRings(W H w s n corner seam pitch)
  let((p a0 b0 ratio nr rs k a b stop)

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

                  when(length(rs) < fix(n + 1.0e-7) + 1
                    librfWarn(sprintf(nil "lri: 内孔已闭合, 最多 %.2f 圈 < 请求 %.2f 圈" (length(rs)-1)*1.0 (1.0 * n)))))
          else rs = cons(lriRing(a b ratio*min(a b) seam) rs))))
    list(reverse(rs) p)))

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

      when(L >= 1.0e-9
        if(acc + L >= want - 1.0e-9
          then return(append(out list(list(
                 car(a)+(car(b)-car(a))*(want-acc)/L
                 cadr(a)+(cadr(b)-cadr(a))*(want-acc)/L))))
          else progn(
            out = append(out list(b))
            acc = acc + L))))
    return(out)))

procedure(lriRotList(l i)
  let((n k out j)
    n = length(l)
    k = mod(mod(i n) + n n)
    out = nil
    for(j 0 n-1
      out = append(out list(nth(mod(k+j n) l))))
    out))

procedure(lriTake(l k)
  let((out j)
    out = nil
    for(j 0 min(k length(l))-1
      out = append(out list(nth(j l))))
    out))

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

procedure(lriSpiralBottom(W H w s n corner pitchOv)
  prog((rp rs p r0 i m bIdx nfull frac L1 D0 Lk pts r rot dk nxt
        closed part k)

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

procedure(lriDraw(cv pts w)
  when(length(pts) >= 2
    librfPathSweep(cv librfMetalPair(librfLyr("spiral")) pts w)))

procedure(lriDrawEx(cv pts w capStart capEnd)
  when(length(pts) >= 2
    librfPathSweepEx(cv librfMetalPair(librfLyr("spiral"))
      pts w capStart capEnd)))

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

procedure(lriPortDir(cv x y dir w len name)
  let((m tl)
    m  = librfMetalPair(librfLyr("spiral"))
    tl = librfTxtPair(librfLyr("spiral"))
    librfStubPort(cv m tl x y dir w len name)))

procedure(lriPortVec(cv x y dx dy w len name)
  let((L nx ny ex ey m tl lx ly off)
    L = sqrt(dx*dx + dy*dy)
    when(L < 1.0e-9  L = 1.0)
    nx = dx/L  ny = dy/L
    ex = x + nx*len  ey = y + ny*len
    m  = librfMetalPair(librfLyr("spiral"))
    tl = librfTxtPair(librfLyr("spiral"))
    librfPathSweep(cv m list(list(x y) list(ex ey)) w)

    off = max(0.0 w/2.0 - 1.5)
    lx = ex + nx*off  ly = ey + ny*off
    librfPortPin(cv car(m) car(tl) lx ly name)
    librfNotePort(name lx ly car(tl))))

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

        ctDir = if(dir == 'S then 'N else 'S)
        if(ctDir == 'N
          then vx2 = 0.0  vy2 = 1.0
               ex2 = tx  ey2 = H/2.0 + w + 40.0
          else vx2 = 0.0  vy2 = -1.0
               ex2 = tx  ey2 = 0.0 - H/2.0 - w - 40.0)
        lriBridge(cv list(cut list(ex2 ey2)) w)
        lriPortVec(cv ex2 ey2 vx2 vy2 w stubLen "ct1")))
    body))

procedure(lriBuildDiff(cv req W H w s n corner seam stubLen ct)
  let((p A B stepsA stepsB tangle dirs d1 d2)
    p = w + s
    A = lriSpiral(W H w s n corner "bottom" 2.0*p)
    stepsA = lriStepList

    B = lriMirrorY(lriSpiral(W-2.0*p H-2.0*p w s n corner "bottom" 2.0*p))
    lriStepList = lriMirrorStepsY(lriStepList)
    stepsB = lriStepList
    lriCheck(A w W H s "diff.A")
    lriCheck(B w (W-2.0*p) (H-2.0*p) s "diff.B")

    lriDrawSteps(cv A w stepsA)
    lriDrawSteps(cv B w stepsB)

    d1 = 'S
    tangle = librfReqNum(req 'tangle -1)
    when(tangle >= 0 && tangle != 180
      librfWarn(sprintf(nil "lri[diff]: tangle=%g 会使 B 端口同层穿过 A 外圈, 已改用自然 180 度出线 (p01=S, p02=N)" tangle)))
    lriPortDir(cv car(car(A)) cadr(car(A)) d1 w stubLen "p01")

    let((pB ex ey)
      pB = car(B)
      ex = car(pB)
      ey = H/2.0 + w + 40.0 + stubLen
      lriBridge(cv list(pB list(ex ey)) w)
      lriPortVec(cv ex ey 0.0 1.0 w stubLen "p02"))

    lriBridge(cv list(lriLast(A) list(car(lriLast(B)) cadr(lriLast(A)))
                      lriLast(B)) w)
    when(ct != 0 && ct != nil
      let((ctEnd)

        ctEnd = list(car(lriLast(B)) 0.0-H/2.0-w-40.0-stubLen)
        lriBridgeTap(cv list(lriLast(B) ctEnd) w)
        lriPortVec(cv car(ctEnd) cadr(ctEnd) 0.0 -1.0 w stubLen "ct1")))
    list(A B)))

procedure(lriBuildF8(cv req W H w s n corner gap stubLen)
  let((d nA Lft Rgt dx tangle dirs d1 d2 cf8)
    when(gap == nil || gap < 1.0  gap = 8.0)

    when(gap < w + s
      librfWarn(sprintf(nil "lri[f8]: gap=%g 小于 w+s=%g, 已放大到 w+s" gap (w+s)))
      gap = w + s)
    d = (W - gap)/2.0
    nA = n/2.0
    when(abs(nA - round(nA)) < 1.0e-6  nA = nA + 0.5)
    dx = d/2.0 + gap/2.0

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

    lriBridge(cv list(lriLast(Lft)
                      list(car(lriLast(Lft)) 0.0)
                      list(0.0 0.0)
                      list(car(lriLast(Rgt)) 0.0)
                      lriLast(Rgt)) w)
    list(Lft Rgt)))

procedure(lriHalfOctR(R xj)
  let((c)
    c = (2.0 - sqrt(2.0)) * R
    list(list(xj R) list(R-c R) list(R R-c)
         list(R 0.0-(R-c)) list(R-c 0.0-R) list(xj 0.0-R))))

procedure(lriHalfOctL(R xj)
  lriMirrorX(reverse(lriHalfOctR(R xj))))

procedure(lriLastCell(lst)
  if(null(cdr(lst)) lst lriLastCell(cdr(lst))))

procedure(lriPtTrimmed(pt pts)
  let((hit)
    hit = nil
    foreach(q pts  when(lriSamePt(pt q)  hit = t))
    hit))

procedure(lriSymWindow(w s R p)
  let((a)
    a = sqrt(2.0) * (w / 2.0 + 2.5)
    list(a - p + w / 2.0
         (sqrt(2.0) - 1.0) * R + p / 2.0 - w / 2.0 - a)))

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

    viaW = if(w >= 8.5 then 7.5 else if(w >= 5.5 then 4.5 else 1.5))

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

        chordMax = (sqrt(2.0) - 1.0) * (Rin - w / 2.0) - xj

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

        av = list(car(at) + sa * viaEdge cadr(at))
        bv = list(car(bt) + sb * viaEdge cadr(bt))
        ae = list(car(av) + sa * stub cadr(av))
        be = list(car(bv) + sb * stub cadr(bv))

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

    topCaps = nil
    foreach(run tops
      topCaps = append(topCaps list(list(
        if(lriPtTrimmed(car(run) trimPts) then nil else t)
        if(lriPtTrimmed(lriLast(run) trimPts) then nil else t)))))
    list(tops unders vias topCaps N R0 Rlast xj)))

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

procedure(lriBridgeVia(cv pts w)
  when(length(pts) == 5
    librfNoteLayer(librfLyr("underpass"))
    dbCreatePolygon(cv librfMetalPair(librfLyr("underpass"))
      lriUnderPolyPts(pts w))))

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

    ta = append(reverse(cdr(reverse(ta)))
                list(append(lriLast(ta)
                            list(list(0.0-xj 0.0-Rlast)))))

    for(i 0 length(ta)-1
      lriDrawEx(cv nth(i ta) w car(nth(i ca)) cadr(nth(i ca))))
    for(i 0 length(tb)-1
      lriDrawEx(cv nth(i tb) w car(nth(i cb)) cadr(nth(i cb))))

    foreach(p append(ua ub)  lriBridgeVia(cv p w))

    foreach(v append(va vb)
      lriViaAt(cv car(v) cadr(v) caddr(v)))

    lriPortDir(cv xj R0 'N w lead "p01")
    lriPortDir(cv 0.0-xj R0 'N w lead "p02")

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

;=============================================================== 二代对称电感
; lriI3：横向/纵向半径、任意整数圈、可调切角、可调差分引线。
; 几何规格见 DEVELOPMENT_GUIDE §11.2；本段是该规格的 SKILL 实现。
; 与一代 lriBuildSym 的差别：
;   1) 环族 a_k=a0-kp, b_k=b0-kp, c_k=max(0,c0-kp(2-sqrt2)) 是精确内偏移，
;      切角不再随半径等比缩小，圈间净距恒为 s；
;   2) rx/ry 独立；chamfer 为 0..1 比例（2-sqrt2 = 正八边形）；
;   3) 每臂圈数可为任意正整数；偶数圈时桥/中心抽头自动移到上侧；
;   4) 引线长度/间距/线宽可调；端口在 (+/-(gap+lw)/2, b0+len)。
defvar(lriI3WedgeMin 2.6)
defvar(lriI3DeltaFrac 0.5)

; 平行内偏移环族。返回 (rings p N)，ring=(a b c)。
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
      ; 45 度切角吃满整条直边后环退化成菱形；并内偏移的正确解就是 c = min(a b)
      when(c > min(a b)  c = min(a b))
      out = append(out list(list(a b c))))
    return(list(out p N))))

; 右半圈：上 (xj,b) -> 下 (xj,-b)
procedure(lriI3HalfR(a b c xj)
  prog((ac nb bc)
    when(xj > a - c + 1.0e-9
      librfErr(sprintf(nil
        "lri[i3]: xj=%.3f 超出顶边直段 (a-c=%.3f)；减小 w/s、增大 rx/ry 或减小 chamfer"
        xj (a - c)))
      return(nil))
    ac = a - c
    nb = 0.0 - b
    bc = b - c
    return(lriDedup(list(list(xj b) list(ac b) list(a bc)
                         list(a 0.0 - bc) list(ac nb) list(xj nb))))))

; 左半圈：下 (-xj,-b) -> 上 (-xj,b)
procedure(lriI3HalfL(a b c xj)
  lriMirrorX(reverse(lriI3HalfR(a b c xj))))

; 换圈 flush 端外移窗口 (dmin dmax)。
procedure(lriI3Window(w s ringK ringK1 xj viaEdge stub)
  let((a b c a1 b1 c1 dmin dm1 dm2 dm3)
    a = car(ringK)   b = cadr(ringK)   c = caddr(ringK)
    a1 = car(ringK1) b1 = cadr(ringK1) c1 = caddr(ringK1)
    dmin = (sqrt(2.0) - 1.0) * (s + w / 2.0)
    dm1 = (a - c) - xj - (viaEdge + stub)
    dm2 = (a1 - c1) - w * (sqrt(2.0) - 1.0) / 2.0 - xj
    dm3 = (a - c) + s / 2.0 - sqrt(2.0) * (w / 2.0 + s)
    list(dmin min(dm1 min(dm2 dm3)))))

; 宽端能放下的最大奇数 cut 数 -> (span ncut)
procedure(lriI3ViaSpan(w cut sp encl)
  let((sm n span)
    sm = w - 2.0 * encl
    n = fix((sm + sp) / (cut + sp) + 1.0e-9)
    when(n < 1  n = 1)
    when(mod(n 2) == 0  n = n - 1)
    when(n < 1  n = 1)
    span = n * cut + (n - 1) * sp
    list(span n)))

; 单臂。返回 (tops unders vias)。
; tops   = 顶层连续折线表；unders = 5 点下穿路径表；vias = 孔中心点表。
procedure(lriI3Arm(rings p N xj w s side viaEdge stub deltaFrac)
  prog((halfs unders vias tops run nxt k uh q dmin dmax delta ring ring1
        a b sa sb at bt av bv ae be cc seg)
    halfs = nil
    for(k 0 N-1
      ring = nth(k rings)
      seg = if(mod(k 2) == 0
              then lriI3HalfR(car(ring) cadr(ring) caddr(ring) xj)
              else lriI3HalfL(car(ring) cadr(ring) caddr(ring) xj))
      when(seg == nil  return(nil))
      halfs = append(halfs list(seg)))
    when(side < 0
      let((m h)
        m = nil
        foreach(h halfs  m = append(m list(lriMirrorX(h))))
        halfs = m))
    unders = nil
    vias = nil
    for(k 0 N-2
      uh = if(side < 0 then mod(k 2) == 0 else mod(k 2) == 1)
      when(uh
        q = lriI3Window(w s nth(k rings) nth(k+1 rings) xj viaEdge stub)
        dmin = car(q)  dmax = cadr(q)
        when(dmax < dmin - 1.0e-9
          librfErr(sprintf(nil
            "lri[i3]: 换圈 k=%d 的 45 度夹缝放不下 w=%.2f 端头（窗口 %.3f..%.3f）；请增大 rx/ry 或减小 w/s"
            k w dmin dmax))
          return(nil))
        delta = dmin + deltaFrac * (dmax - dmin)
        a = lriLast(nth(k halfs))
        b = car(nth(k+1 halfs))
        sa = if(car(a) >= 0.0 then 1.0 else -1.0)
        sb = if(car(b) >= 0.0 then 1.0 else -1.0)
        at = list(car(a) + sa * delta cadr(a))
        bt = list(car(b) + sb * delta cadr(b))
        rplaca(lriLastCell(nth(k halfs)) at)
        rplaca(nth(k+1 halfs) bt)
        av = list(car(at) + sa * viaEdge cadr(at))
        bv = list(car(bt) + sb * viaEdge cadr(bt))
        ae = list(car(av) + sa * stub cadr(av))
        be = list(car(bv) + sb * stub cadr(bv))
        cc = list(car(av) - sa * p cadr(bv))
        unders = append(unders list(list(ae av cc bv be)))
        vias = append(vias list(av bv))))
    tops = nil
    run = car(halfs)
    for(k 0 N-2
      uh = if(side < 0 then mod(k 2) == 0 else mod(k 2) == 1)
      nxt = car(nth(k+1 halfs))
      cond(
        (uh
          tops = append(tops list(run))
          run = nth(k+1 halfs))
        (t
          run = append(run cons(nxt cdr(nth(k+1 halfs)))))))
    tops = append(tops list(run))
    return(list(tops unders vias))))

; ------------------------------------------------- 任意金属对的通孔链
; 叠层表由工艺档注入（tech/<pdk>_profile.il 或 tech_to_skill 生成）；
; 缺省为空表，sym 会退回 via_sp 角色单跳连接（通用模板也能出图）。
; 金属自下而上；via = (名字 下层 上层 cut space 下层包络 上层包络)。
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

; lo 逐级升到 hi 的通孔表；不同层或方向反了 -> nil。
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

; 一条通孔列（x 固定，y 向 ncut 个 cut）；跨越多层时在中间金属补 landing pad。
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
        ; 中间金属要同时包住上、下两排孔：各取所需包络的最大值
        halfX = max((cut / 2.0 + encLo) (prevCut / 2.0 + prevEnc))
        halfY = max((span / 2.0 + encLo) (prevSpan / 2.0 + prevEnc))
        midName = nth(1 v)
        librfNoteLayer(midName)
        librfRect(cv librfMetalPair(midName)
          x - halfX  y - halfY  x + halfX  y + halfY))
      prevSpan = span  prevEnc = encHi  prevCut = cut)))

; ---------------------------------------------------------------- 叠层
; 单颗通孔（正方形 cut，中心 x/y）。
procedure(lriViaSq(cv vpair x y cut)
  librfRect(cv vpair (x - cut / 2.0) (y - cut / 2.0)
                     (x + cut / 2.0) (y + cut / 2.0)))

procedure(lriStackFill(cv vlyr pts w cut sp enc)
  prog((vpair r dpitch nrow x1 y1 x2 y2 dx dy L ux uy nx ny s j off px py)
    r = w / 2.0 - enc - cut / 2.0
    when(r < 0.0
      librfErr(sprintf(nil "lri[i3]: 叠层线宽 %.2f 放不下通孔" w))
      return(nil))
    ; 45 度段上相邻方孔的角-角间距 = 沿向距 - cut*sqrt(2)，
    ; 所以沿向与行距都按 (cut+sp)*sqrt(2) 起步，0/90 段同样满足。
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
            lriViaSq(cv vpair px py cut))
          s = s + dpitch)))
    t))


; 换圈下穿画在指定金属上（叠层时是 aux 下方那层，否则就是 aux 本身）。
; 本 PDK 的 1P8M_6Ic_1TMc_1MTTc 叠层里 M6/TM1/TM2 才是 metal6/7/8，
; M7/M8(67/68) 不在这套叠层内（见 LVS deck TOPMETAL 8 分支与 EMX proc）。
procedure(lriBridgeOn(cv metalName pts w)
  when(length(pts) == 5
    librfNoteLayer(metalName)
    dbCreatePolygon(cv librfMetalPair(metalName)
      lriUnderPolyPts(pts w))))

; 差分对称电感主入口（kind=sym）。参数：
;   rx ry 外金属半轴、n 每臂圈数、w s 线宽/间距、chamfer 0..1、
;   lead_len 引线中心线长、lead_gap 引线边距、lead_w 引线宽、ct 中心抽头。
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
    ; 引线从窗口边 xj 向内/向外都合法：只要中心 > 0 且不出外圈
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
    ; 空串 = 用当前工艺档的默认角色，换工艺时 CDF 不必改默认值
    when(mainName == ""  mainName = librfLyr("spiral"))
    when(auxName == ""   auxName = librfLyr("underpass"))
    when(equal(mainName auxName)
      librfErr("lri[i3]: metal_main 与 metal_aux 不能同层")
      return(nil))
    ; 宽金属长平行间距：差分两臂在缝隙处是两块独立多边形（间距 = s），
    ; 两条输出引线之间也是（间距 = lead_gap），都要满足 *_2b/*_2c/MTT2_2b。
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
      ; 工艺档没有叠层表：按当前工艺的 via_sp 角色单跳连接（旧版行为）
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
    ; 叠层语义是「同一圈电流分两層并联」：只有相邻金属才成立，
    ; 跨级叠层没有中间层承载整圈电流，缝合孔会悬空（DRC UTV2）。
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
      ; 非出厂金属对：用该通孔自己的规则，不能拿 TV2 的 0.36/0.34 去画 V5
      vcut = 1.0 * nth(3 vTop)
      vsp  = 1.0 * nth(4 vTop)
      ven  = max(1.0 * nth(5 vTop) 1.0 * nth(6 vTop)))
    viaEdge = vcut / 2.0 + ven
    back = w / 2.0 * (sqrt(2.0) - 1.0)
    stub = max(viaEdge max(0.0 lriI3WedgeMin - back))
    spanInfo = lriI3ViaSpan(w vcut vsp ven)
    span = car(spanInfo)  ncut = cadr(spanInfo)
    ; 叠层：换圈下穿走 aux 下方那层，再用逐级通孔链打回主金属
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
    ; 主线圈：全部顶层连续段（flush 端不加方帽，避免撞 45 度夹缝）
    foreach(run append(ta tb)
      librfPathSweepEx(cv M run w nil nil)
      when(stack > 0.5
        librfPathSweepEx(cv ML run w nil nil)
        lriStackFill(cv car(vTop) run w vcut vsp ven)))
    ; 串联桥（两臂内端）
    librfPathSweepEx(cv M br w nil nil)
    when(stack > 0.5
      librfPathSweepEx(cv ML br w nil nil)
      lriStackFill(cv car(vTop) br w vcut vsp ven))
    ; 换圈下穿（与另一臂互补，交叉处不同层）；叠层时改走 M8，用 TV1 接回 TM1
    foreach(path append(ua ub)
      lriBridgeOn(cv (cond ((stack > 0.5) crossName) (t auxName)) path w))
    ; 宽端通孔列
    foreach(v append(va vb)
      lriViaColStack(cv car(v) cadr(v) w (cond ((stack > 0.5) chainUse) (t chain))))
    ; 差分引线：径向短段 + 垂直引出
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
    ; 中心抽头：内桥中点 -> 原点焊盘
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
    ; 断言
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
        lriI3BuildSym(cv req))
      ((kind == "sym_legacy")
        lriBuildSym(cv req doutDef n w s))
      ((kind == "se")
        lriBuildSE(cv req W H w s n corner seam stubLen ct))
      ((kind == "diff")
        lriBuildDiff(cv req W H w s n corner seam stubLen ct))
      ((kind == "f8" || kind == "fig8" || kind == "figure8")
        lriBuildF8(cv req W H w s n corner gap stubLen))
      (t
        librfErr(sprintf(nil "lri: 未知 kind=%s (sym/se/diff/f8)" kind))))

    when(librfReqNum(req 'ind_mark 0) != 0
      librfIndMark(cv librfCvBBox(cv) 1.0))
    ; 断言结果已通过 librfErr 汇报；返回值只表示构建完成（调用方按 t 判成功）
    t))

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
