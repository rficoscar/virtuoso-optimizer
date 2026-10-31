#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Virtuoso Optimizer 命令行入口。

保留 `python optimizer.py <task.json>` 的调用方式；实现位于
src/virtuoso_optimizer/cli.py。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from virtuoso_optimizer.cli import *  # noqa: F401,F403,E402
from virtuoso_optimizer.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
