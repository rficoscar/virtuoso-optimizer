# -*- coding: utf-8 -*-
"""librf_opt.py — 版图/EM 自动化模块的兼容入口。

1.6.0 起实现拆分到 lri/ 包；`import librf_opt` 与本文件的历史接口保持不变。
"""
from __future__ import print_function

from lri.common import *  # noqa: F401,F403
from lri.tech import *  # noqa: F401,F403
from lri.tools import *  # noqa: F401,F403
from lri.skillio import *  # noqa: F401,F403
from lri.emx import *  # noqa: F401,F403
from lri.snp import *  # noqa: F401,F403
from lri.device import *  # noqa: F401,F403
from lri.runner import *  # noqa: F401,F403
from lri.layout import *  # noqa: F401,F403
from lri.ui_em import *  # noqa: F401,F403

from lri.selftest_librf import _selftest

if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "--selftest":
        _sys.exit(0 if _selftest() else 1)
    print("librf_opt.py — optimizer_v06 版图/EM 自动化模块")
    print("用法: python librf_opt.py --selftest")
