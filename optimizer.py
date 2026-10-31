#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Virtuoso Optimizer 图形界面入口。

用法：`python3 optimizer.py`（直接打开界面）。实现位于
src/virtuoso_optimizer/。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from virtuoso_optimizer.cli import *  # noqa: F401,F403,E402
from virtuoso_optimizer.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
