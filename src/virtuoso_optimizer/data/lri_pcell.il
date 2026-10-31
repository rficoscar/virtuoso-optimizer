;===============================================================
; lri_pcell.il — 二代差分对称电感 PCell 外壳（IC618）
;
; 依赖：先 load("librf.il")（几何内核 lriI3BuildSym 在那边）。
; 用法：
;   load("librf.il")
;   load("lri_pcell.il")
;   lriPcellTechSmic55()                  ; 真实 PDK 层/DRC 档（可跳过=示教 M5/M4）
;   lriPcellDefine("MYLIB" "ind_sym")     ; 在 MYLIB 里注册 cell
; 之后在 Library Manager 里实例化 ind_sym，参数即下面 CDF 表。
;
; 参数（与用户需求一一对应）：
;   rx/ry    横向/纵向半径（外金属半轴，footprint = 2rx x 2ry + lead_len）
;   n        每臂圈数（整数；差分总圈数 = 2n）
;   w/s      线宽 / 线间隔
;   chamfer  拐角切角 0..1（0=矩形，2-sqrt(2)=正八边形）
;   lead_len 差分输出线长度（中心线）
;   lead_gap 差分线间距（边到边）
;   lead_w   差分线宽
;   ct       1 = 中心抽头（内桥中点 -> 原点焊盘）
;
; 工艺层角色：spiral/underpass/via_sp/marker 由 librfTech 决定
; （缺省 M5/M4/V4/MARKER）。真实 PDK 由 tools/gen_tech.py 从 tech/<key>.json
; 生成 tech/<key>_profile.il，lriPcellTechLoad 加载。
;===============================================================

unless(boundp('lriPcellLib) (setq lriPcellLib "RF_LIB"))

; ------------------------------------------------------------ 工艺档加载
; 层名/通孔/DRC 数值全部来自 tech/<key>.json 生成的 <key>_profile.il。
defvar(lriTechDir nil)
defvar(lriTechLoaded nil)

; PCell 生成上下文：批处理会话要先 loadContext 才能 pcDefinePCell。
procedure(lriPcellCxtLoad()
  let((d p)
    p = getShellEnvVar("LIBRF_PCELLGEN_CXT")
    unless(p && p != ""
      d = getShellEnvVar("CDS_INST_DIR")
      when(d && d != ""
        p = strcat(d "/tools/dfII/etc/context/64bit/pCellGen.cxt")))
    when(p && p != ""
      errset(loadContext(p) nil))
    t))
procedure(lriTechFind(name)
  let((cands d p)
    cands = nil
    when(lriTechDir  cands = append(cands list(lriTechDir)))
    d = getShellEnvVar("LRI_TECH_DIR")
    when(d && d != ""  cands = append(cands list(d)))
    cands = append(cands list("./tech" "../tech"))
    p = nil
    foreach(c cands
      unless(p
        when(isReadable(strcat(c "/" name "_profile.il"))
          p = strcat(c "/" name "_profile.il"))))
    p))

procedure(lriPcellTechLoad(name)
  let((p)
    unless(name && name != ""  name = "smic55ll_ulp_rf")
    p = lriTechFind(name)
    unless(p
      error(sprintf(nil
        "lriPcellTechLoad: 找不到 %s_profile.il（设 LRI_TECH_DIR 或放 ./tech）"
        name)))
    unless(equal(lriTechLoaded name)
      load(p)
      setq(lriTechLoaded name))
    sprintf(nil "lriPcellTechLoad: %s -> %s" name librfTechName)))


; SMIC55LL ULP RF（1P8M_6Ic_1TMc_1MTTc）内建档。
; 优先读 tech/smic55ll_ulp_rf_profile.il（由 tech/*.json 生成）；找不到才用
; 下面的内联表。TV2 规则按 Calibre DRC V1.13 实测：
;   UTV2_1 固定 0.36x0.36（MTT2_UTV2=1 分支）
;   UTV2_2a 间距 0.34
;   UTV2_5  TM2 包络 >= 0.3  -> 取 0.4 留裕量
;   TM2_1 最小线宽 0.40；TM2_2b 长/宽>=2um 时最小间距 0.5；TM2_5 最大宽 30
procedure(lriPcellTechSmic55()
  if(lriTechFind("smic55ll_ulp_rf")
    then lriPcellTechLoad("smic55ll_ulp_rf")
    else lriPcellTechSmic55Inline()))

procedure(lriPcellTechSmic55Inline()
  librfTechName = "SMIC55LL_ULP_RF (inline)"
  librfTech = list(
    list("spiral" "TM2") list("underpass" "TM1") list("route" "M6")
    list("ground" "M6") list("via_sp" "TV2") list("via_un" "TV2")
    list("via_st" "TV1")
    list("marker" "INDMY"))
  librfTechText = list(
    list("TM2" list("MnTXT" "topmetal2"))
    list("TM1" list("MnTXT" "topmetal1"))
    list("M6"  list("MnTXT" "metal6")))
  ; 叠层表：metal_main/metal_aux 可指任意相邻两层，通孔链逐级生成
  librfStackMetals = list("M1" "M2" "M3" "M4" "M5" "M6" "TM1" "TM2")
  librfStackVias = list(
    list("V1" "M1" "M2" 0.09 0.11 0.03 0.02)
    list("V2" "M2" "M3" 0.09 0.11 0.03 0.02)
    list("V3" "M3" "M4" 0.09 0.11 0.03 0.02)
    list("V4" "M4" "M5" 0.09 0.11 0.03 0.02)
    list("V5" "M5" "M6" 0.09 0.11 0.03 0.02)
    list("TV1" "M6" "TM1" 0.36 0.34 0.05 0.02)
    list("TV2" "TM1" "TM2" 0.36 0.34 0.05 0.30))
  librfDrc = list(list("min_w" 0.40) list("max_w" 30.0) list("min_s" 0.50)
    list("wide_space" list(list("M1" 0.5) list("M2" 0.5) list("M3" 0.5)
                           list("M4" 0.5) list("M5" 0.5) list("M6" 0.5)
                           list("TM1" 0.5) list("TM2" 2.0)))
    list("via" list(list("cut" 0.36) list("spacing" 0.34)
                    list("enclosure" 0.40)))
    list("via_st" list(list("cut" 0.36) list("spacing" 0.50)
                       list("enclosure" 0.05))))
  t)

; 该 cell 的参数是否已在 CDF 里
procedure(lriCdfHasParam(cdf pname)
  let((hit)
    hit = nil
    when(cdf
      foreach(p cdf~>parameters
        when(p~>name == pname  hit = t)))
    hit))

; 补建 CDF 参数（batch 下 cdfSaveCDF 可能返回 nil，但本会话内表单可用）
procedure(lriPcellCdf(libName cellName)
  let((master cdf)
    master = ddGetObj(libName cellName)
    when(master
      cdf = cdfGetBaseCellCDF(master)
      unless(cdf  cdf = cdfCreateBaseCellCDF(master))
      when(cdf
        unless(lriCdfHasParam(cdf "rx")
          cdfCreateParam(cdf ?name "rx" ?type "float" ?defValue 80.0
            ?prompt "Horizontal radius (um)"))
        unless(lriCdfHasParam(cdf "ry")
          cdfCreateParam(cdf ?name "ry" ?type "float" ?defValue 60.0
            ?prompt "Vertical radius (um)"))
        unless(lriCdfHasParam(cdf "n")
          cdfCreateParam(cdf ?name "n" ?type "int" ?defValue 3
            ?prompt "Turns per arm"))
        unless(lriCdfHasParam(cdf "w")
          cdfCreateParam(cdf ?name "w" ?type "float" ?defValue 8.0
            ?prompt "Line width (um)"))
        unless(lriCdfHasParam(cdf "s")
          cdfCreateParam(cdf ?name "s" ?type "float" ?defValue 3.0
            ?prompt "Line spacing (um)"))
        unless(lriCdfHasParam(cdf "chamfer")
          cdfCreateParam(cdf ?name "chamfer" ?type "float"
            ?defValue 0.5857864376 ?prompt "Corner chamfer 0..1"))
        unless(lriCdfHasParam(cdf "lead_len")
          cdfCreateParam(cdf ?name "lead_len" ?type "float" ?defValue 20.0
            ?prompt "Differential lead length (um)"))
        unless(lriCdfHasParam(cdf "lead_gap")
          cdfCreateParam(cdf ?name "lead_gap" ?type "float" ?defValue 3.0
            ?prompt "Differential lead spacing (um)"))
        unless(lriCdfHasParam(cdf "lead_w")
          cdfCreateParam(cdf ?name "lead_w" ?type "float" ?defValue 8.0
            ?prompt "Differential lead width (um)"))
        unless(lriCdfHasParam(cdf "ct")
          cdfCreateParam(cdf ?name "ct" ?type "int" ?defValue 0
            ?prompt "Centre tap (0/1)"))
        unless(lriCdfHasParam(cdf "stack")
          cdfCreateParam(cdf ?name "stack" ?type "int" ?defValue 0
            ?prompt "Stacked TM1+TM2 winding (0/1)"))
        unless(lriCdfHasParam(cdf "metal_main")
          cdfCreateParam(cdf ?name "metal_main" ?type "string" ?defValue ""
            ?prompt "Main metal: spiral + leads (empty = tech default)"))
        unless(lriCdfHasParam(cdf "metal_aux")
          cdfCreateParam(cdf ?name "metal_aux" ?type "string" ?defValue ""
            ?prompt "Aux metal: cross-under (empty = tech default)"))
        errset(cdfSaveCDF(cdf) t)))
    cdf))

; 在 libName 中注册/刷新 PCell。cellName 缺省 "ind_sym"。
procedure(lriPcellDefine(libName cellName)
  let((lib)
    when(libName == nil || libName == ""  libName = lriPcellLib)
    when(cellName == nil || cellName == ""  cellName = "ind_sym")
    lib = ddGetObj(libName)
    unless(lib
      error(sprintf(nil "lriPcellDefine: library %s not found" libName)))
    lriPcellCxtLoad()
    pcDefinePCell(
      list(lib cellName "layout")
      (
        (rx       float 80.0  "Horizontal radius (um, outer metal)")
        (ry       float 60.0  "Vertical radius (um, outer metal)")
        (n        int   3     "Turns per arm (integer)")
        (w        float 8.0   "Line width (um)")
        (s        float 3.0   "Line spacing (um)")
        (chamfer  float 0.5857864376 "Corner chamfer 0..1 (2-sqrt2 = octagon)")
        (lead_len float 20.0  "Differential lead length (um, centreline)")
        (lead_gap float 3.0   "Differential lead spacing (um, edge-edge)")
        (lead_w   float 8.0   "Differential lead width (um)")
        (ct       int   0     "Centre tap (0/1)")
        (stack    int   0     "Stacked TM1+TM2 winding (0/1)")
        (metal_main string "" "Main metal: spiral + leads (empty = tech default)")
        (metal_aux  string "" "Aux metal: cross-under (empty = tech default)")
      )
      let((req)
        lriReport = nil
        librf__errs = nil
        req = list('kind "sym"
                   'rx rx 'ry ry 'n n 'w w 's s 'chamfer chamfer
                   'lead_len lead_len 'lead_gap lead_gap 'lead_w lead_w
                   'ct ct 'stack stack
                   'metal_main metal_main 'metal_aux metal_aux)
        unless(lriI3BuildSym(pcCellView req)
          error(sprintf(nil "inductor pcell build failed: %s"
            (if(librf__errs car(librf__errs) "see lri[i3] checks")))))
        dbReplaceProp(pcCellView "instNamePrefix" "string" "L")
        t)
      )
    lriPcellCdf(libName cellName)
    sprintf(nil "lriPcellDefine: %s/%s registered" libName cellName)))
