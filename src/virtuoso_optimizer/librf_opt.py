# -*- coding: utf-8 -*-
"""librf_opt.py — layout/EM automation facade.

The implementation lives in the lri/ package. In-package imports
`from virtuoso_optimizer.librf_opt import ...` are unchanged; run the
self-test with `python3 -m virtuoso_optimizer.librf_opt --selftest`.
"""
from .lri.common import *  # noqa: F401,F403
from .lri.tech import *  # noqa: F401,F403
from .lri.tools import *  # noqa: F401,F403
from .lri.skillio import *  # noqa: F401,F403
from .lri.emx import *  # noqa: F401,F403
from .lri.snp import *  # noqa: F401,F403
from .lri.device import *  # noqa: F401,F403
from .lri.runner import *  # noqa: F401,F403
from .lri.layout import *  # noqa: F401,F403
from .lri.ui_em import *  # noqa: F401,F403

if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "--selftest":
        try:
            from .lri.selftest_librf import _selftest
        except ImportError:
            print("本包未附带自测（lri/selftest_librf.py）；完整回归请在开发仓库运行。",
                  file=_sys.stderr)
            _sys.exit(2)
        _sys.exit(0 if _selftest() else 1)
    print("librf_opt.py — Virtuoso Optimizer layout/EM automation module")
    print("Usage: PYTHONPATH=src python3 -m virtuoso_optimizer.librf_opt --selftest")
