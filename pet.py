#!/usr/bin/env python3
"""MeaPet desktop entry (compat). Prefer: python -m meapet"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main():
    """先用纯标准库检查依赖，再导入 PyQt5 桌面应用。"""

    from meapet.bootstrap import ensure_pet_dependencies

    if not ensure_pet_dependencies(_ROOT):
        return 2

    from meapet.desktop.app import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    raise SystemExit(main() or 0)
