# -*- coding: utf-8 -*-
"""gui.py — graphical interface facade.

The implementation lives in lri/ui/. In-package imports
`from virtuoso_optimizer.gui import ...` and `python optimizer.py --gui`
keep working unchanged.
"""
from .lri.ui.plots import *  # noqa: F401,F403
from .lri.ui.netlist import *  # noqa: F401,F403
from .lri.ui.results import *  # noqa: F401,F403
from .lri.ui.reports import *  # noqa: F401,F403
from .lri.ui.tech_tab import *  # noqa: F401,F403
from .lri.ui.main import *  # noqa: F401,F403

from .lri.ui.main import (  # noqa: F401 - compatibility re-export of underscore-prefixed names
    OptimizerGui, PlotCanvas, _MANAGED_CFG_KEYS, _SET_DEFAULTS, _TechTab, run_gui)
from .lri.ui.reports import _var_extra_drop, _var_extra_rename  # noqa: F401
