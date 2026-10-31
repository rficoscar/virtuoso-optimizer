#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Virtuoso Optimizer graphical entry point.

Usage: `python3 optimizer.py` (opens the GUI directly). The implementation
lives in src/virtuoso_optimizer/.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from virtuoso_optimizer.cli import *  # noqa: F401,F403,E402
from virtuoso_optimizer.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
