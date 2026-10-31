#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""em_opt 兼容模块：python3 src/em_opt.py --selftest

程序入口只有根目录的 optimizer.py（图形界面）；本文件保留给脚本
`import em_opt` 与开发自测使用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from virtuoso_optimizer.em.opt import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        from virtuoso_optimizer.em.opt import _selftest
        sys.exit(1 if _selftest() else 0)
    print("em_opt.py — Virtuoso Optimizer EM 融合优化模块")
    print("用法: python3 src/em_opt.py --selftest")
