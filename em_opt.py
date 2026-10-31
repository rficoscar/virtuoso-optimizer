#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""em_opt 兼容入口：python em_opt.py --selftest"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from virtuoso_optimizer.em.opt import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        from virtuoso_optimizer.em.opt import _selftest
        sys.exit(1 if _selftest() else 0)
    print("em_opt.py — Virtuoso Optimizer EM 融合优化模块")
    print("用法: python em_opt.py --selftest")
