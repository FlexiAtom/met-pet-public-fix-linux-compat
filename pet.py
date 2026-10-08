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

    from meapet.doctor import doctor_requested

    # 拦在 ensure_pet_dependencies 之前：来取证的那台机器往往就是依赖坏的那台，
    # 而装依赖是写侧动作——doctor 承诺只读，就不能先动它再问它。
    if doctor_requested(sys.argv):
        from meapet.doctor import main as doctor_main

        return doctor_main(sys.argv[1:])

    from meapet.bootstrap import ensure_pet_dependencies

    if not ensure_pet_dependencies(_ROOT):
        return 2

    from meapet.desktop.app import main as desktop_main

    return desktop_main()


def _run() -> int:
    """把 GUI 出现之前的任何失败都变成可见的错误，而不是静默退出。

    窗口化打包下 stdout/stderr 均为 None，未捕获异常不会留下任何痕迹，
    表现就是「双击 exe 没反应」。这里统一兜到 emit_startup_error。
    """

    try:
        return main() or 0
    except SystemExit:
        raise
    except BaseException:
        import traceback

        detail = traceback.format_exc()
        try:
            from meapet.bootstrap import emit_startup_error

            emit_startup_error(f"[MeaPet] 启动失败：\n\n{detail}")
        except BaseException:
            try:
                print(detail, file=sys.stderr)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(_run())
