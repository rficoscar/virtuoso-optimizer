# -*- coding: utf-8 -*-
"""gui.py — 图形界面兼容入口。

1.6.0 起实现拆分到 lri/ui/；`from gui import ...` 的历史用法保持不变。
"""
from __future__ import print_function

from lri.ui.plots import *  # noqa: F401,F403
from lri.ui.netlist import *  # noqa: F401,F403
from lri.ui.results import *  # noqa: F401,F403
from lri.ui.reports import *  # noqa: F401,F403
from lri.ui.tech_tab import *  # noqa: F401,F403
from lri.ui.main import *  # noqa: F401,F403

from lri.ui.main import (  # noqa: F401 - 下划线接口的兼容再导出
    OptimizerGui, PlotCanvas, _MANAGED_CFG_KEYS, _SET_DEFAULTS, _TechTab, run_gui)
from lri.ui.reports import _var_extra_drop, _var_extra_rename  # noqa: F401
