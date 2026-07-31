"""Qt 测试进程不得连接真实桌面。"""

from __future__ import annotations


def test_qt_uses_offscreen_platform():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    assert app is not None
    assert app.platformName() == "offscreen"
    assert app.quitOnLastWindowClosed() is False
    assert app.topLevelWidgets() == []
