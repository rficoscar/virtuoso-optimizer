# -*- coding: utf-8 -*-
"""test_proc_cross_section.py — proc 横截面模块的 headless 单元测试。

针对内置示教叠层与 fixtures/demo_stack.*（纯虚构数据）。
纯标准库；模块顶层不 import tkinter。运行：
    cd optimizer_public && python -m unittest test_proc_cross_section
或（隔离模式）：
    cd optimizer_public && python -I test_proc_cross_section.py
"""

import ast
import copy
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import proc_cross_section as pcs

PROC = os.path.join(BASE, "fixtures", "demo_stack.proc")
LAYERMAP = os.path.join(BASE, "fixtures", "demo_stack.layermap")

# 脱敏自检用：运行时拼接，避免本文件自身出现厂名/工艺代号。
BANNED_TOKENS = ("X" "MC", "55R" "FSOI", "1P" "5M", "AL" "PA",
                 "MI" "MB", "MI" "MT", "T" "M" "R1", "T" "M")


def _custom_flow():
    return {"metals": ["MA", "MB"],
            "vias": [["MA", "MB", "VA"]],
            "gaps": [["MA", "MB", 0.5]],
            "gds": {"MA": 101, "MB": 102},
            "via_gds": {"VA": 110},
            "text": {"MATXT": 101, "MBTXT": 102},
            "via_res": {"VA": 0.5}}


CUSTOM_PROC = ("layer 100 11.9 #sub_si\n"
               "conductor 0.5 0.1 MA\n"
               "layer 0.5 4.0 #ILD\n"
               "conductor 0.8 0.05 MB\n"
               "layer infinity 1\n"
               "via MA MB 0.5 VA\n")

CUSTOM_LAYERMAP = ("MA drawing 101 0\n"
                   "MB drawing 102 0\n"
                   "VA drawing 110 0\n"
                   "MATXT drawing 101 5\n"
                   "MBTXT drawing 102 5\n")


class FakeCanvas(object):
    """记录 Canvas 调用的无显示替身。"""

    def __init__(self, width=800, height=420):
        self.width, self.height = width, height
        self.items = []
        self.delete_count = 0
        self.config = {}
        self.bindings = []

    def delete(self, *args, **kwargs):
        self.delete_count += 1
        self.items = []

    def create_rectangle(self, *args, **kwargs):
        self.items.append(("rectangle", args, kwargs))
        return len(self.items)

    def create_line(self, *args, **kwargs):
        self.items.append(("line", args, kwargs))
        return len(self.items)

    def create_text(self, *args, **kwargs):
        self.items.append(("text", args, kwargs))
        return len(self.items)

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def configure(self, **kwargs):
        self.config.update(kwargs)

    def bind(self, *args, **kwargs):
        self.bindings.append((args, kwargs))


def _texts(canvas):
    return [item[2].get("text", "") for item in canvas.items
            if item[0] == "text"]


def _rects(canvas):
    return [item[2] for item in canvas.items if item[0] == "rectangle"]


def _has_label(texts, name):
    return any(text == name or text.startswith(name + " ") for text in texts)


class ProcCrossSectionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.proc = pcs.parse_proc(PROC)
        cls.layermap = pcs.parse_layermap(LAYERMAP)

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="pcs_test_")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.work, name)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return path

    # ---------------------------------------------------------------- API
    def test_api_and_constants(self):
        for name in ("parse_proc", "parse_layermap", "build_stack",
                     "validate_flow_stack", "draw_cross_section",
                     "build_cross_section_frame", "flow_from_profile"):
            self.assertTrue(callable(getattr(pcs, name, None)), name)
        self.assertEqual(tuple(pcs.DEFAULT_FLOW["metals"]),
                         ("M1", "M2", "M3", "M4", "M5", "AP"))
        self.assertEqual(pcs.FLOW_METALS,
                         ("M1", "M2", "M3", "M4", "M5", "AP"))
        self.assertEqual(tuple(pcs.EXPECTED_VIAS), (
            ("M1", "M2", "V1"), ("M2", "M3", "V2"), ("M3", "M4", "V3"),
            ("M4", "M5", "V4"), ("M5", "AP", "PA")))
        self.assertEqual(dict(((a, b), c) for a, b, c in pcs.EXPECTED_GAPS), {
            ("M1", "M2"): 0.35, ("M2", "M3"): 0.35, ("M3", "M4"): 0.35,
            ("M4", "M5"): 0.35, ("M5", "AP"): 0.75})
        self.assertEqual(pcs.FLOW_GDS["M1"], 61)
        self.assertEqual(pcs.FLOW_GDS["M5"], 65)
        self.assertEqual(pcs.FLOW_GDS["AP"], 5)
        self.assertEqual(dict(pcs.VIA_LAYER_GDS),
                         {"V1": 70, "V2": 71, "V3": 72, "V4": 73, "PA": 80})
        self.assertTrue(issubclass(pcs.ProcParseError, Exception))

    def test_flow_from_profile_defaults(self):
        base = pcs.flow_from_profile(None)
        for bad in ({}, {"flow": None}, {"layers": {"spiral": "M5"}},
                    "not-a-flow"):
            flow = pcs.flow_from_profile(bad)
            self.assertEqual(flow["metals"], pcs.FLOW_METALS)
            self.assertEqual(flow["vias"], tuple(pcs.EXPECTED_VIAS))
            self.assertEqual(flow["gds"], pcs.FLOW_GDS)
        self.assertEqual(base["via_res"], pcs.EXPECTED_VIA_RES)
        self.assertEqual(set(base["text"]),
                         {"M1TXT", "M2TXT", "M3TXT", "M4TXT", "M5TXT",
                          "APTXT"})
        # 返回的是独立副本，改它不影响模块默认值
        self.assertIsNot(base["gds"], pcs.DEFAULT_FLOW["gds"])
        base["gds"]["M1"] = 999
        self.assertEqual(pcs.DEFAULT_FLOW["gds"]["M1"], 61)

    def test_flow_from_profile_partial(self):
        flow = pcs.flow_from_profile({"flow": {"metals": ["M1", "M2"]}})
        self.assertEqual(flow["metals"], ("M1", "M2"))
        self.assertEqual(flow["vias"], ())
        self.assertEqual(flow["gaps"], ())
        self.assertEqual(flow["gds"], {})
        self.assertEqual(flow["via_gds"], {})
        self.assertEqual(flow["text"], {})
        self.assertEqual(flow["via_res"], {})

    def test_flow_from_profile_full(self):
        flow = pcs.flow_from_profile({"flow": _custom_flow()})
        self.assertEqual(flow["metals"], ("MA", "MB"))
        self.assertEqual(flow["vias"], (("MA", "MB", "VA"),))
        self.assertEqual(flow["gaps"], (("MA", "MB", 0.5),))
        self.assertEqual(flow["gds"], {"MA": 101, "MB": 102})
        self.assertEqual(flow["via_gds"], {"VA": 110})
        self.assertEqual(flow["text"], {"MATXT": 101, "MBTXT": 102})
        self.assertEqual(flow["via_res"], {"VA": 0.5})

    def test_flow_from_profile_accepts_tech_profile(self):
        profile = {"name": "demo", "layers": {"spiral": "MB"},
                   "flow": _custom_flow()}
        flow = pcs.flow_from_profile(profile)
        self.assertEqual(flow["metals"], ("MA", "MB"))
        self.assertEqual(flow["gds"]["MB"], 102)

    # -------------------------------------------------------------- parse
    def test_parse_proc_fixture(self):
        data = self.proc
        flow = [c for c in data["conductors"] if c["name"] in pcs.FLOW_METALS]
        self.assertEqual([c["name"] for c in flow],
                         ["M1", "M2", "M3", "M4", "M5", "AP"])
        values = dict((c["name"], c) for c in data["conductors"])
        self.assertAlmostEqual(values["M1"]["thickness"], 0.20, places=6)
        self.assertAlmostEqual(values["M1"]["rho"], 0.080, places=6)
        self.assertAlmostEqual(values["M2"]["thickness"], 0.30, places=6)
        self.assertAlmostEqual(values["M2"]["rho"], 0.060, places=6)
        self.assertAlmostEqual(values["M3"]["rho"], 0.060, places=6)
        self.assertAlmostEqual(values["M4"]["rho"], 0.045, places=6)
        self.assertAlmostEqual(values["M5"]["thickness"], 0.40, places=6)
        self.assertAlmostEqual(values["M5"]["rho"], 0.030, places=6)
        self.assertAlmostEqual(values["AP"]["thickness"], 1.20, places=6)
        self.assertAlmostEqual(values["AP"]["rho"], 0.020, places=6)

        found = [(v["bottom"], v["top"], v["name"]) for v in data["vias"]]
        self.assertEqual(len(data["vias"]), 5)
        for bottom, top, via_name in pcs.EXPECTED_VIAS:
            self.assertIn((bottom, top, via_name), found)
        self.assertEqual(data["capacitors"], [])
        sub = [d for d in data["dielectrics"] if d["name"] == "sub_si"][0]
        self.assertAlmostEqual(sub["thickness"], 500.0, places=6)
        self.assertAlmostEqual(sub["eps"], 11.9, places=6)
        inf = [d for d in data["dielectrics"] if d["thickness"] is None]
        self.assertEqual(len(inf), 1)
        self.assertEqual(inf[0]["name"], "infinity")
        self.assertIn("m1res", data["defines"])
        self.assertEqual(len(data["stack"]),
                         len(data["conductors"]) + len(data["dielectrics"]))
        self.assertEqual([e["order"] for e in data["stack"]],
                         list(range(len(data["stack"]))))

    def test_parse_layermap_fixture(self):
        by_name = self.layermap["by_name"]
        for name, expected in (("M1", 61), ("M2", 62), ("M3", 63),
                               ("M4", 64), ("M5", 65), ("AP", 5),
                               ("V1", 70), ("V2", 71), ("V3", 72),
                               ("V4", 73), ("PA", 80)):
            self.assertEqual(by_name[name], (expected, 0), name)
        self.assertEqual(by_name["MARKER"], (199, 0))
        self.assertIn("M1", self.layermap["by_layer"][(61, 0)])
        self.assertEqual(by_name["M1TXT"], (61, 5))
        self.assertEqual(by_name["APTXT"], (5, 5))
        self.assertEqual(self.layermap["bad_lines"], [])
        self.assertGreaterEqual(len(self.layermap["pairs"]), 20)

    # -------------------------------------------------------------- stack
    def test_build_stack(self):
        stack = pcs.build_stack(self.proc)
        flow = stack["flow"]
        self.assertEqual(set(flow), set(pcs.FLOW_METALS))
        wanted = dict(((a, b), c) for a, b, c in pcs.EXPECTED_GAPS)
        for low, high, _want in pcs.EXPECTED_GAPS:
            gap = flow[high]["z0"] - flow[low]["z1"]
            self.assertAlmostEqual(gap, wanted[(low, high)], places=6,
                                   msg="%s-%s" % (low, high))
        self.assertAlmostEqual(flow["M1"]["z1"], 501.10, places=6)
        self.assertAlmostEqual(stack["top"], 506.25, places=6)
        self.assertTrue(stack["top_is_infinite"])
        inf = [e for e in stack["entries"] if e["thickness"] is None]
        self.assertEqual(len(inf), 1)
        self.assertIsNone(inf[0]["z1"])

    def test_build_stack_custom_metals(self):
        stack = pcs.build_stack(self.proc, ("M1", "M3"))
        self.assertEqual(set(stack["flow"]), {"M1", "M3"})
        self.assertAlmostEqual(stack["flow"]["M3"]["z0"],
                               502.10, places=6)

    # ----------------------------------------------------------- validate
    def test_validate_current_proc(self):
        errors, warnings = pcs.validate_flow_stack(self.proc, self.layermap)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_validate_missing_metal(self):
        data = copy.deepcopy(self.proc)
        data["conductors"] = [c for c in data["conductors"]
                              if c["name"] != "M3"]
        data["stack"] = [e for e in data["stack"]
                         if not (e.get("kind") == "conductor" and
                                 e.get("name") == "M3")]
        errors, _warnings = pcs.validate_flow_stack(data, self.layermap)
        self.assertTrue([e for e in errors if "M3" in e and "缺少" in e],
                        errors)

    def test_validate_metal_order(self):
        data = copy.deepcopy(self.proc)
        stack = data["stack"]
        pos = {}
        for index, entry in enumerate(stack):
            if entry.get("kind") == "conductor":
                pos[entry.get("name")] = index
        i2, i3 = pos["M2"], pos["M3"]
        stack[i2], stack[i3] = stack[i3], stack[i2]
        for index, entry in enumerate(stack):
            entry["order"] = index
        errors, _warnings = pcs.validate_flow_stack(data, self.layermap)
        self.assertTrue([e for e in errors if "顺序错误" in e], errors)

    def test_validate_missing_via(self):
        data = copy.deepcopy(self.proc)
        data["vias"] = [v for v in data["vias"] if v["name"] != "V2"]
        errors, _warnings = pcs.validate_flow_stack(data, self.layermap)
        self.assertTrue([e for e in errors if "V2" in e and "通孔" in e],
                        errors)

    def test_validate_via_name_mismatch(self):
        data = copy.deepcopy(self.proc)
        for via in data["vias"]:
            if via["name"] == "V2":
                via["name"] = "VX"
        _errors, warnings = pcs.validate_flow_stack(data, self.layermap)
        self.assertTrue([w for w in warnings if "通孔名称不符" in w and
                         "V2" in w], warnings)

    def test_validate_via_resistance(self):
        data = copy.deepcopy(self.proc)
        for via in data["vias"]:
            if via["name"] == "V4":
                via["resistance"] = 2.0
        _errors, warnings = pcs.validate_flow_stack(data, self.layermap)
        self.assertTrue([w for w in warnings if "V4" in w and
                         "电阻偏差" in w], warnings)

    def test_validate_gap_override(self):
        errors, warnings = pcs.validate_flow_stack(
            self.proc, self.layermap, expected_gaps=[("M1", "M2", 0.5)])
        self.assertEqual(errors, [])
        self.assertTrue([w for w in warnings
                         if "M1→M2" in w and "0.500" in w], warnings)
        errors, warnings = pcs.validate_flow_stack(
            self.proc, self.layermap, expected_gaps={("M3", "M4"): 0.2})
        self.assertEqual(errors, [])
        self.assertTrue([w for w in warnings
                         if "M3→M4" in w and "0.200" in w], warnings)

    def test_validate_gap_from_flow(self):
        data = copy.deepcopy(self.proc)
        for entry in data["stack"]:
            if entry.get("kind") == "dielectric" and \
                    entry.get("name") == "IMD2":
                entry["thickness"] = 0.40
        errors, warnings = pcs.validate_flow_stack(data, self.layermap)
        self.assertEqual(errors, [])
        self.assertTrue([w for w in warnings if "M2→M3" in w], warnings)

    def test_validate_custom_flow(self):
        proc = pcs.parse_proc(self._write("custom.proc", CUSTOM_PROC))
        layermap = pcs.parse_layermap(self._write("custom.layermap",
                                                  CUSTOM_LAYERMAP))
        flow = pcs.flow_from_profile({"flow": _custom_flow()})
        errors, warnings = pcs.validate_flow_stack(proc, layermap, flow=flow)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        bad = copy.deepcopy(flow)
        bad["gds"]["MB"] = 199
        errors, _warnings = pcs.validate_flow_stack(proc, layermap, flow=bad)
        self.assertTrue([e for e in errors if "MB" in e and "层号" in e],
                        errors)

    def test_validate_layermap_mismatch(self):
        layermap = copy.deepcopy(self.layermap)
        layermap["by_name"]["M2"] = (99, 0)
        errors, _warnings = pcs.validate_flow_stack(self.proc, layermap)
        self.assertTrue([e for e in errors if "M2" in e and "层号" in e],
                        errors)
        layermap = copy.deepcopy(self.layermap)
        del layermap["by_name"]["V3"]
        errors, _warnings = pcs.validate_flow_stack(self.proc, layermap)
        self.assertTrue([e for e in errors if "V3" in e and "通孔层" in e],
                        errors)
        layermap = copy.deepcopy(self.layermap)
        layermap["by_name"]["M5TXT"] = (99, 5)
        _errors, warnings = pcs.validate_flow_stack(self.proc, layermap)
        self.assertTrue([w for w in warnings if "M5TXT" in w and
                         "文字层" in w], warnings)

    def test_validate_without_layermap(self):
        errors, warnings = pcs.validate_flow_stack(self.proc, None)
        self.assertEqual(errors, [])
        self.assertTrue([w for w in warnings if "layermap" in w], warnings)

    # -------------------------------------------------------- bad inputs
    def test_parse_errors(self):
        with self.assertRaises(pcs.ProcParseError):
            pcs.parse_proc(self._write("empty.proc", ""))
        with self.assertRaises(pcs.ProcParseError):
            pcs.parse_proc(self._write("garbage.proc", "not a proc\n@@@\n"))
        bad = self._write("bad.proc",
                          "assume microns\nlayer 0.1 4 #X\nconductor 0.23\n")
        with self.assertRaises(pcs.ProcParseError) as ctx:
            pcs.parse_proc(bad)
        self.assertIn("第 3 行", str(ctx.exception))
        with self.assertRaises(pcs.ProcParseError):
            pcs.parse_proc(os.path.join(self.work, "nope.proc"))
        bad_via = self._write("badvia.proc", "via M1 M2 1.1\n")
        with self.assertRaises(pcs.ProcParseError) as ctx:
            pcs.parse_proc(bad_via)
        self.assertIn("第 1 行", str(ctx.exception))
        no_cond = self._write("nocond.proc", "assume microns\nlayer 0.1 4 #X\n")
        data = pcs.parse_proc(no_cond)
        errors, _warnings = pcs.validate_flow_stack(data)
        self.assertTrue([e for e in errors if "缺少流程金属" in e], errors)

    def test_parse_layermap_bad_lines(self):
        path = self._write("bad.layermap",
                           "M1 drawing 61 0\nbadline\nM2 drawing xx 0\n"
                           "V1 drawing 70 0\n")
        result = pcs.parse_layermap(path)
        self.assertEqual(result["by_name"]["M1"], (61, 0))
        self.assertEqual(result["by_name"]["V1"], (70, 0))
        self.assertNotIn("M2", result["by_name"])
        self.assertEqual(len(result["bad_lines"]), 2)

    # -------------------------------------------------------------- draw
    def test_draw_cross_section(self):
        canvas = FakeCanvas(900, 520)
        result = pcs.draw_cross_section(canvas, self.proc, self.layermap)
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["items"], 20)
        self.assertGreaterEqual(canvas.delete_count, 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])

        rects = _rects(canvas)
        self.assertGreater(len(rects), 20)
        fills = set(r.get("fill") for r in rects)
        self.assertEqual(set(pcs.METAL_COLORS.values()) & fills,
                         set(pcs.METAL_COLORS.values()))
        self.assertFalse([r for r in rects if "dash" in r])

        texts = _texts(canvas)
        for name in pcs.FLOW_METALS:
            self.assertTrue(_has_label(texts, name), name)
        for _bottom, _top, via_name in pcs.EXPECTED_VIAS:
            self.assertIn(via_name, texts)
        self.assertIn("图例", texts)
        self.assertTrue([t for t in texts if "500.00" in t and "压缩" in t],
                        texts)
        self.assertTrue([t for t in texts if "infinity" in t], texts)
        m5 = [t for t in texts if t.startswith("M5 ")][0]
        self.assertIn("65/0", m5)
        self.assertIn("0.4", m5)
        ap = [t for t in texts if t.startswith("AP ")][0]
        self.assertIn("5/0", ap)
        self.assertIn("1.2", ap)
        self.assertIn("0.02", ap)
        self.assertGreater(
            len([i for i in canvas.items if i[0] == "line"]), 40)

    def test_draw_custom_flow(self):
        proc = pcs.parse_proc(self._write("custom.proc", CUSTOM_PROC))
        flow = pcs.flow_from_profile({"flow": _custom_flow()})
        canvas = FakeCanvas(800, 420)
        result = pcs.draw_cross_section(canvas, proc, None, flow=flow)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["errors"], [])
        self.assertTrue([w for w in result["warnings"] if "layermap" in w],
                        result["warnings"])
        texts = _texts(canvas)
        for name in ("MA", "MB"):
            self.assertTrue(_has_label(texts, name), name)
        self.assertIn("VA", texts)
        fills = set(r.get("fill") for r in _rects(canvas))
        self.assertTrue(set(pcs.EXTRA_METAL_COLORS[:2]) <= fills, fills)

    def test_draw_narrow(self):
        canvas = FakeCanvas(260, 180)
        result = pcs.draw_cross_section(canvas, self.proc, self.layermap)
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["items"], 10)
        fills = set(r.get("fill") for r in _rects(canvas))
        self.assertEqual(set(pcs.METAL_COLORS.values()) & fills,
                         set(pcs.METAL_COLORS.values()))

    def test_draw_invalid(self):
        self.assertFalse(pcs.draw_cross_section(None, self.proc)["ok"])
        canvas = FakeCanvas()
        result = pcs.draw_cross_section(canvas, {})
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])
        result = pcs.draw_cross_section(FakeCanvas(), {"stack": []})
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_draw_missing_via(self):
        data = copy.deepcopy(self.proc)
        data["vias"] = [v for v in data["vias"] if v["name"] != "PA"]
        canvas = FakeCanvas(700, 400)
        result = pcs.draw_cross_section(canvas, data, self.layermap)
        self.assertTrue(result["ok"], result)
        self.assertTrue([e for e in result["errors"] if "PA" in e])
        self.assertTrue([w for w in result["warnings"]
                         if "未找到通孔 PA" in w], result["warnings"])
        self.assertNotIn("PA", _texts(canvas))

    # ------------------------------------------------------- import/source
    def test_module_import_without_tk(self):
        code = ("import sys; sys.path.insert(0, '.'); "
                "import proc_cross_section; "
                "print('tkinter' in sys.modules)")
        out = subprocess.check_output([sys.executable, "-I", "-c", code],
                                      cwd=BASE)
        self.assertEqual(out.decode("utf-8", "replace").strip(), "False")

    def test_source_clean_and_sized(self):
        path = os.path.join(BASE, "proc_cross_section.py")
        with io.open(path, "rb") as fh:
            text = fh.read().decode("utf-8")  # 严格解码
        self.assertIn("横截面", text)
        self.assertLessEqual(len(text.splitlines()), 760)
        self.assertNotIn("dataclass", text)
        self.assertNotIn(":" "=", text)
        top_imports = [ln for ln in text.splitlines()
                       if ln.startswith("import tkinter") or
                       ln.startswith("from tkinter")]
        self.assertEqual(top_imports, [])
        for token in BANNED_TOKENS:
            self.assertNotIn(token, text, token)
        for name in ("demo_stack.proc", "demo_stack.layermap"):
            fixture = os.path.join(BASE, "fixtures", name)
            with io.open(fixture, "rb") as fh:
                content = fh.read().decode("utf-8")
            for token in BANNED_TOKENS:
                self.assertNotIn(token, content, name + ":" + token)

    def test_py36_syntax(self):
        paths = [os.path.join(BASE, "proc_cross_section.py"),
                 os.path.join(BASE, "test_proc_cross_section.py")]
        for path in paths:
            with io.open(path, "rb") as fh:
                source = fh.read().decode("utf-8")
            if sys.version_info >= (3, 8):
                tree = ast.parse(source, filename=path,
                                 feature_version=(3, 6))
            else:
                tree = ast.parse(source, filename=path)
            self.assertIsNotNone(tree)

    # -------------------------------------------------------------- frame
    def test_build_cross_section_frame_smoke(self):
        try:
            import tkinter as tk
        except Exception as exc:
            print("SKIP: tkinter unavailable (%s)" % exc)
            return
        try:
            root = tk.Tk()
        except Exception as exc:
            print("SKIP: no Tk display (%s)" % exc)
            return
        root.withdraw()
        try:
            class FakeVar(object):
                def __init__(self, value):
                    self.value = value

                def get(self):
                    return self.value

                def set(self, value):
                    self.value = value

            proc_var, map_var = FakeVar(PROC), FakeVar(LAYERMAP)
            frame = pcs.build_cross_section_frame(root, proc_var, map_var,
                                                  height=220)
            frame.pack(fill="both", expand=True)
            root.update_idletasks()
            self.assertIs(frame.proc_var, proc_var)
            self.assertIs(frame.layermap_var, map_var)
            self.assertTrue(frame.canvas.bind("<Configure>"))
            frame.redraw()
            root.update_idletasks()
            self.assertIn("检查通过", frame.status_label.cget("text"))
            self.assertEqual(
                str(frame.status_label.cget("fg")).lower(), "#2f855a")
            self.assertGreater(len(frame.canvas.find_all()), 0)

            proc_var.set(os.path.join(self.work, "missing.proc"))
            frame.redraw()
            self.assertIn("不存在", frame.status_label.cget("text"))
            self.assertEqual(
                str(frame.status_label.cget("fg")).lower(), "#c0392b")

            proc_var.set("")
            frame.redraw()
            self.assertIn("未设置", frame.status_label.cget("text"))

            proc_var.set(self._write("badframe.proc", "conductor 0.23\n"))
            frame.redraw()
            self.assertIn("解析失败", frame.status_label.cget("text"))

            frame.schedule_redraw()
            self.assertIsNotNone(frame._after_id)

            real_proc = tk.StringVar(master=root, value=PROC)
            real_map = tk.StringVar(master=root, value=LAYERMAP)
            frame2 = pcs.build_cross_section_frame(root, real_proc, real_map,
                                                   height=160)
            real_proc.set("")
            self.assertIsNotNone(frame2._after_id)
        finally:
            try:
                root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
