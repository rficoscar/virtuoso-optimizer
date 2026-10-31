;===============================================================
; lri_pcell.il — second-generation differential symmetric inductor PCell shell (IC618)
;
; Requires load("librf.il") first (the geometry kernel lriI3BuildSym lives there).
; Usage:
;   load("librf.il")
;   load("lri_pcell.il")
;   lriPcellTechSmic55()                  ; real PDK layer/DRC profile (optional; demo M5/M4 otherwise)
;   lriPcellDefine("MYLIB" "ind_sym")     ; register the cell in MYLIB
; Then instantiate ind_sym in Library Manager; the parameters are the CDF table below.
;
; Parameters (one-to-one with the user-facing fields):
;   rx/ry    horizontal/vertical radius (outer-metal half axes; footprint = 2rx x 2ry + lead_len)
;   n        turns per arm (integer; total differential turns = 2n)
;   w/s      line width / line spacing
;   chamfer  corner chamfer 0..1 (0 = rectangle, 2-sqrt(2) = regular octagon)
;   lead_len differential output lead length (centreline)
;   lead_gap differential lead spacing (edge to edge)
;   lead_w   differential lead width
;   ct       1 = centre tap (inner-bridge midpoint -> origin pad)
;
; Process layer roles: spiral/underpass/via_sp/marker come from librfTech
; (default M5/M4/V4/MARKER). For a real PDK, tools/gen_tech.py turns tech/<key>.json
; into tech/<key>_profile.il, loaded by lriPcellTechLoad.
;===============================================================

unless(boundp('lriPcellLib) (setq lriPcellLib "RF_LIB"))

; ------------------------------------------------------------ process profile loading
; Layer names, vias and DRC values all come from <key>_profile.il generated from tech/<key>.json.
defvar(lriTechDir nil)
defvar(lriTechLoaded nil)

; PCell generation context: batch sessions must loadContext before pcDefinePCell.
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


; Built-in profile for SMIC55LL ULP RF (1P8M_6Ic_1TMc_1MTTc).
; Prefer tech/smic55ll_ulp_rf_profile.il (generated from tech/*.json); fall back to
; the inline table below. TV2 rules measured against Calibre DRC V1.13:
;   UTV2_1 fixed 0.36x0.36 (MTT2_UTV2=1 branch)
;   UTV2_2a spacing 0.34
;   UTV2_5  TM2 enclosure >= 0.3 -> use 0.4 for margin
;   TM2_1 min width 0.40; TM2_2b min spacing 0.5 when length/width >= 2um; TM2_5 max width 30
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
  ; Stack table: metal_main/metal_aux may name any adjacent pair; the via chain is generated level by level
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

; Whether this cell's parameters already exist in the CDF
procedure(lriCdfHasParam(cdf pname)
  let((hit)
    hit = nil
    when(cdf
      foreach(p cdf~>parameters
        when(p~>name == pname  hit = t)))
    hit))

; Create missing CDF parameters (cdfSaveCDF may return nil in batch, but the form works in this session)
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

; Register/refresh the PCell in libName. cellName defaults to "ind_sym".
procedure(lriPcellDefine(libName cellName)
  let((lib)
    when(libName == nil || libName == ""  libName = lriPcellLib)
    when(cellName == nil || cellName == ""  cellName = "ind_sym")
    lib = ddGetObj(libName)
    unless(lib
      error(sprintf(nil "lriPcellDefine: library %s not found" libName)))
    lriPcellCxtLoad()
    unless(fboundp('lriI3BuildSym)
      error("lriPcellDefine: lriI3BuildSym missing; load librf.il first"))
    ; Load a process profile when available; otherwise keep the demo M5/M4 layers.
    when(lriTechLoaded == nil
      when(lriTechFind("smic55ll_ulp_rf")
        lriPcellTechSmic55()))
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
      let((req bootDirs bootDir bootFile pcellFile)
        ; --- Bootstrap: a new session only keeps the compiled pcell body; librf.il /
        ;     lri_pcell.il and the process profile are gone. On first evaluation, find
        ;     and load them from candidate directories so instantiating directly from
        ;     Library Manager (without loading scripts first) still builds correctly; fail loudly, never silently fall back to demo M5/M4.
        when(fboundp('lriI3BuildSym) == nil
          bootDirs = nil
          bootDir = getShellEnvVar("LRI_DATA_DIR")
          when(bootDir && bootDir != ""
            bootDirs = append(bootDirs list(bootDir)))
          bootDir = getShellEnvVar("LRI_HOME")
          when(bootDir && bootDir != ""
            bootDirs = append(bootDirs list(
              strcat(bootDir "/src/virtuoso_optimizer/data"))))
          when(boundp('lriPcellDataDir)
            when(lriPcellDataDir && lriPcellDataDir != ""
              bootDirs = append(bootDirs list(lriPcellDataDir))))
          bootDirs = append(bootDirs list("." "src/virtuoso_optimizer/data"))
          bootFile = nil
          foreach(bootDir bootDirs
            unless(bootFile
              when(isReadable(strcat(bootDir "/librf.il"))
                bootFile = strcat(bootDir "/librf.il")
                setq(lriPcellDataDir bootDir))))
          unless(bootFile
            error("inductor pcell: librf.il not found; load librf.il or set LRI_DATA_DIR"))
          load(bootFile))
        when(fboundp('lriPcellTechSmic55) == nil
          bootDirs = nil
          bootDir = getShellEnvVar("LRI_DATA_DIR")
          when(bootDir && bootDir != ""
            bootDirs = append(bootDirs list(bootDir)))
          bootDir = getShellEnvVar("LRI_HOME")
          when(bootDir && bootDir != ""
            bootDirs = append(bootDirs list(
              strcat(bootDir "/src/virtuoso_optimizer/data"))))
          when(boundp('lriPcellDataDir)
            when(lriPcellDataDir && lriPcellDataDir != ""
              bootDirs = append(bootDirs list(lriPcellDataDir))))
          bootDirs = append(bootDirs list("." "src/virtuoso_optimizer/data"))
          pcellFile = nil
          foreach(bootDir bootDirs
            unless(pcellFile
              when(isReadable(strcat(bootDir "/lri_pcell.il"))
                pcellFile = strcat(bootDir "/lri_pcell.il")
                setq(lriPcellDataDir bootDir))))
          unless(pcellFile
            error("inductor pcell: lri_pcell.il not found; load lri_pcell.il or set LRI_DATA_DIR"))
          load(pcellFile))
        when(lriTechLoaded == nil
          lriPcellTechSmic55()
          setq(lriTechLoaded "smic55ll_ulp_rf"))
        lriReport = nil
        librf__errs = nil
        req = list('kind "sym"
                   'rx rx 'ry ry 'n n 'w w 's s 'chamfer chamfer
                   'lead_len lead_len 'lead_gap lead_gap 'lead_w lead_w
                   'ct ct 'stack stack
                   'metal_main metal_main 'metal_aux metal_aux)
        unless(lriI3BuildSym(pcCellView req)
          error(sprintf(nil "inductor pcell build failed: %s"
            (if librf__errs car(librf__errs) "see lri[i3] checks"))))
        dbReplaceProp(pcCellView "instNamePrefix" "string" "L")
        t)
      )
    lriPcellCdf(libName cellName)
    sprintf(nil "lriPcellDefine: %s/%s registered" libName cellName)))
