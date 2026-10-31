; auto-generated, do not edit — source tech/demo2m.json, generator tools/gen_tech.py
; overrides librf.il built-in defaults when loaded (SMIC55 values are the factory default).
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

; role table: spiral/underpass = main/aux metal, route = the layer below aux.
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
