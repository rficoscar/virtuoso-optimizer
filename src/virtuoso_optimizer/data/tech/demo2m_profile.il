; 自动生成，勿手改 —— 源 tech/demo2m.json，生成器 tools/gen_tech.py
; 加载后覆盖 librf.il 的内建缺省（SMIC55 数值即出厂默认）。
setq(librfTechName "DEMO_2M_MIN")
setq(librfTechLib "demo2m_tech")
setq(librfTechKey "demo2m")

setq(librfStackMetals
  list("M1" "M2"))
setq(librfStackVias
  list(
    list("V1" "M1" "M2" 0.2 0.2 0.05 0.05)))
setq(librfMetalGds
  list(list("M1" 10 0) list("M2" 20 0)))
setq(librfViaGds
  list(list("V1" 30 0)))

; 角色表：spiral/underpass = 主/副金属，route = 副金属下方那层。
setq(librfTech
  list(
    list("spiral" "M2") list("underpass" "M1")
    list("route" "M1") list("ground" "M1")
    list("via_sp" "V1") list("via_un" "V1")
    list("via_st" "V1")
    list("marker" "INDMY")))
setq(librfTechText
  list(
    list("M1" list("MnTXT" "metal1"))
    list("M2" list("MnTXT" "metal2"))))

setq(librfDrc
  list(
    list("min_w" 0.2) list("max_w" 10.0) list("min_s" 0.2)
    list("via" list(list("cut" 0.2) list("spacing" 0.2) list("enclosure" 0.1)))
    list("via_st" list(list("cut" 0.2) list("spacing" 0.2) list("enclosure" 0.05)))
    list("min_area"
      list(
        list("M1" 0.0)
        list("M2" 0.0)
      )
    )
    list("wide_space"
      list(
        list("M1" 0.2)
        list("M2" 0.2)
      )
    )
  )
)

t
