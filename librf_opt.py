#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""librf_opt 兼容入口：python librf_opt.py --selftest"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from virtuoso_optimizer.librf_opt import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        try:
            from virtuoso_optimizer.lri.selftest_librf import _selftest
        except ImportError:
            print("本包未附带自测（lri/selftest_librf.py）；完整回归请在开发仓库运行。",
                  file=sys.stderr)
            sys.exit(2)
        sys.exit(0 if _selftest() else 1)
    print("librf_opt.py — Virtuoso Optimizer 版图/EM 自动化模块")
    print("用法: python librf_opt.py --selftest")
