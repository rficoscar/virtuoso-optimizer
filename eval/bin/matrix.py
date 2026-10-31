# -*- coding: utf-8 -*-
"""matrix.py - 测试矩阵：单端/差分/中心抽头 × 参数边界与典型值 × foundry 基准。

矩阵条目：
  {'id','kind':'ind'|'ct'|'ext','params':{...},'tags':[...]}
kind='ind' 由 gdsgen.make_spiral 生成，'ct' 由 make_ct_spiral 生成，
'ext' 是外部 GDS（foundry pcell 基准），只给 gds/cell。
"""
from __future__ import print_function

import copy

# 典型值 + 单因子边界（OFAT）。数值单位 um，圈数无量纲。
BASE = {"n": 2.5, "w": 8.0, "s": 3.0, "dout": 120.0, "shape": "oct",
        "chamfer": 0.0, "lead": 12.0}


def _mk(cid, kind, params, tags):
    p = copy.deepcopy(BASE)
    p.update(params)
    return {"id": cid, "kind": kind, "params": p, "tags": list(tags)}


def build(limits=None):
    lim = limits or {}
    wmin = float(lim.get("min_w", 8.0))
    smin = float(lim.get("min_s", 3.0))
    rows = [
        _mk("ind_base", "ind", {}, ["typical", "single_ended"]),
        _mk("ind_w_min", "ind", {"w": wmin}, ["boundary", "width"]),
        _mk("ind_w_max", "ind", {"w": 16.0}, ["boundary", "width"]),
        _mk("ind_s_min", "ind", {"s": smin}, ["boundary", "spacing"]),
        _mk("ind_s_large", "ind", {"s": 3.0 * smin}, ["boundary", "spacing"]),
        _mk("ind_n_1p5", "ind", {"n": 1.5}, ["boundary", "turns"]),
        _mk("ind_n_4p5", "ind", {"n": 4.5}, ["boundary", "turns"]),
        _mk("ind_dout_small", "ind", {"dout": 80.0}, ["boundary", "outer_size"]),
        _mk("ind_dout_large", "ind", {"dout": 200.0}, ["boundary", "outer_size"]),
        _mk("ind_chamfer_0", "ind", {"chamfer": 0.0}, ["shape"]),
        _mk("ind_chamfer_half", "ind", {"chamfer": 4.0}, ["shape"]),
        _mk("ind_chamfer_w", "ind", {"chamfer": 8.0}, ["shape"]),
        _mk("ind_lead_short", "ind", {"lead": 6.0}, ["lead"]),
        _mk("ind_lead_long", "ind", {"lead": 24.0}, ["lead"]),
        _mk("ct_base", "ct", {}, ["typical", "center_tap"]),
        _mk("ct_n_1p5", "ct", {"n": 1.5}, ["boundary", "center_tap"]),
        _mk("ct_lead_long", "ct", {"lead": 24.0}, ["boundary", "center_tap"]),
    ]
    return rows


def add_baseline(rows, gds, cell, params=None, tags=("foundry_pcell",)):
    """把 foundry pcell 导出的 GDS 作为同尺寸基准加入矩阵。"""
    r = {"id": "base_foundry", "kind": "ext", "gds": gds, "cell": cell,
         "params": dict(params or {}), "tags": list(tags)}
    rows.append(r)
    return rows


if __name__ == "__main__":
    import json
    print(json.dumps(build(), indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- 真 SKILL PCell
# 差分对称电感（lriI3BuildSym, kind="sym"）的单因子扫掠。
# 每个条目都由 skillgen 真正跑 virtuoso + strmout 生成 GDS，不是 Python 镜像。
SYM_BASE = {"rx": 60.0, "ry": 60.0, "n": 2, "w": 8.0, "s": 3.0,
            "chamfer": 0.5857864376, "lead_len": 20.0, "lead_gap": 3.0,
            "lead_w": 8.0, "ct": 1}


def _sym(cid, params, tags, **kw):
    p = dict(SYM_BASE)
    p.update(params)
    d = {"id": cid, "kind": "sym", "source": "skill", "params": p,
         "tags": list(tags)}
    d.update(kw)
    return d


def build_sym():
    """差分对称 PCell 的自动化/鲁棒性扫掠（真正的 SKILL 构建）。"""
    return [
        _sym("sym_base", {}, ["typical"]),
        _sym("sym_rx_40", {"rx": 40.0}, ["radius_x"]),
        _sym("sym_rx_90", {"rx": 90.0}, ["radius_x", "large"]),
        _sym("sym_ry_35", {"ry": 35.0}, ["radius_y"]),
        _sym("sym_ry_85", {"ry": 85.0}, ["radius_y", "large"]),
        _sym("sym_ell_60x40", {"rx": 60.0, "ry": 40.0}, ["radius", "elliptic"]),
        _sym("sym_n_1", {"n": 1}, ["turns"]),
        _sym("sym_n_3", {"n": 3}, ["turns"]),
        _sym("sym_w_4", {"w": 4.0, "lead_w": 4.0}, ["width"]),
        _sym("sym_w_14", {"rx": 85.0, "ry": 85.0, "w": 14.0, "lead_w": 14.0},
             ["width", "large"]),
        # TM2 有 MTT2_2b：宽/长>=16 时两块独立金属的长平行间距要 2.0；
        # 差分两臂在缝隙处是独立多边形，s<2 必须拒绝。
        _sym("sym_s_1p5", {"s": 1.5}, ["spacing", "invalid"], expect_fail=True),
        _sym("sym_s_2", {"s": 2.0}, ["spacing", "boundary"]),
        _sym("sym_s_6", {"s": 6.0, "lead_gap": 6.0}, ["spacing", "large", "lead_follow"]),
        # 下层金属没有 MTT2_2b：同样的 1.5 间距在 M6/M5 上合法
        _sym("sym_metal_m6_m5_s1p5",
             {"metal_main": "M6", "metal_aux": "M5", "s": 1.5,
              "lead_gap": 1.5}, ["metal_pair", "spacing", "min"]),
        _sym("sym_ch_0", {"chamfer": 0.0}, ["chamfer", "rect"]),
        _sym("sym_ch_1", {"chamfer": 1.0}, ["chamfer", "max"], expect_fail=True),
        _sym("sym_ch_0p7", {"chamfer": 0.7}, ["chamfer", "large"]),
        _sym("sym_lead_6", {"lead_len": 6.0}, ["lead_len"]),
        _sym("sym_lead_60", {"lead_len": 60.0}, ["lead_len", "large"]),
        _sym("sym_leadgap_6", {"lead_gap": 6.0}, ["lead_gap"]),
        _sym("sym_leadgap_24", {"lead_gap": 24.0}, ["lead_gap", "large"]),
        _sym("sym_leadgap_1p5", {"lead_gap": 1.5}, ["lead_gap", "invalid"],
             expect_fail=True),
        _sym("sym_leadw_4", {"lead_w": 4.0}, ["lead_w"]),
        _sym("sym_ct_off", {"ct": 0}, ["no_center_tap"], emx_kind="diff"),
        _sym("sym_w_14_r60", {"w": 14.0, "lead_w": 14.0}, ["width", "invalid"],
             expect_fail=True),
        _sym("sym_stack_base", {"stack": 1}, ["stack", "typical"]),
        _sym("sym_stack_ct_off", {"stack": 1, "ct": 0}, ["stack", "no_center_tap"],
             emx_kind="diff"),
        # 主/副金属可换：TV1 单级、TV1+TV2 链（TM1 上自动补 landing pad）
        _sym("sym_metal_tm1_m6", {"metal_main": "TM1", "metal_aux": "M6"},
             ["metal_pair", "via_single"]),
        _sym("sym_metal_tm2_m6", {"metal_main": "TM2", "metal_aux": "M6"},
             ["metal_pair", "via_chain"]),
        # 叠层语义只对相邻金属成立：TM2/M6 跨 TM1，缝合孔没有整圈宿主，
        # PCell 必须明确拒绝（见 librf.il lriI3BuildSym 的相邻性检查）。
        _sym("sym_stack_tm2_m6", {"metal_main": "TM2", "metal_aux": "M6",
                                  "stack": 1},
             ["metal_pair", "via_chain", "stack", "invalid"], expect_fail=True),
        _sym("sym_metal_bad_order", {"metal_main": "M6", "metal_aux": "TM2"},
             ["metal_pair", "invalid"], expect_fail=True),
    ]
