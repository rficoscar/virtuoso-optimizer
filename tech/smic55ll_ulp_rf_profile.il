; 自动生成，勿手改 —— 源 tech/smic55ll_ulp_rf.json，生成器 tools/gen_tech.py
; 加载后覆盖 librf.il 的内建缺省（SMIC55 数值即出厂默认）。
setq(librfTechName "SMIC55LL_ULP_RF_1P8M_6Ic_1TMc_1MTTc")
setq(librfTechLib "SMIC55LL_ULP_RF_1P8M_6Ic_1TMc_1MTTc")
setq(librfTechKey "smic55ll_ulp_rf")

setq(librfStackMetals
  list("M1" "M2" "M3" "M4" "M5" "M6" "TM1" "TM2"))
setq(librfStackVias
  list(
    list("V1" "M1" "M2" 0.09 0.11 0.03 0.02)
    list("V2" "M2" "M3" 0.09 0.11 0.03 0.02)
    list("V3" "M3" "M4" 0.09 0.11 0.03 0.02)
    list("V4" "M4" "M5" 0.09 0.11 0.03 0.02)
    list("V5" "M5" "M6" 0.09 0.11 0.03 0.02)
    list("TV1" "M6" "TM1" 0.36 0.34 0.05 0.02)
    list("TV2" "TM1" "TM2" 0.36 0.34 0.05 0.3)))
setq(librfMetalGds
  list(list("M1" 61 0) list("M2" 62 0) list("M3" 63 0) list("M4" 64 0) list("M5" 65 0) list("M6" 66 0) list("TM1" 120 0) list("TM2" 122 0)))
setq(librfViaGds
  list(list("V1" 70 0) list("V2" 71 0) list("V3" 72 0) list("V4" 73 0) list("V5" 74 0) list("TV1" 121 0) list("TV2" 123 0)))

; 角色表：spiral/underpass = 主/副金属，route = 副金属下方那层。
setq(librfTech
  list(
    list("spiral" "TM2") list("underpass" "TM1")
    list("route" "M6") list("ground" "M6")
    list("via_sp" "TV2") list("via_un" "TV2")
    list("via_st" "TV1")
    list("marker" "INDMY")))
setq(librfTechText
  list(
    list("M1" list("MnTXT" "metal1"))
    list("M2" list("MnTXT" "metal2"))
    list("M3" list("MnTXT" "metal3"))
    list("M4" list("MnTXT" "metal4"))
    list("M5" list("MnTXT" "metal5"))
    list("M6" list("MnTXT" "metal6"))
    list("TM1" list("MnTXT" "topmetal1"))
    list("TM2" list("MnTXT" "topmetal2"))))

setq(librfDrc
  list(
    list("min_w" 0.4) list("max_w" 30.0) list("min_s" 0.5)
    list("via" list(list("cut" 0.36) list("spacing" 0.34) list("enclosure" 0.4)))
    list("via_st" list(list("cut" 0.36) list("spacing" 0.5) list("enclosure" 0.05)))
    list("min_area"
      list(
        list("M1" 0.1)
        list("M2" 0.1)
        list("M3" 0.1)
        list("M4" 0.1)
        list("M5" 0.1)
        list("M6" 0.1)
        list("TM1" 0.4)
        list("TM2" 6.0)
      )
    )
    list("wide_space"
      list(
        list("M1" 0.5)
        list("M2" 0.5)
        list("M3" 0.5)
        list("M4" 0.5)
        list("M5" 0.5)
        list("M6" 0.5)
        list("TM1" 0.5)
        list("TM2" 2.0)
      )
    )
  )
)

t
