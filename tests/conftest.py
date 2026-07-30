"""pytest 的 Qt 隔离与资源回收。

测试机可能已经把 ``QT_QPA_PLATFORM`` 设为 ``xcb``。如果只用
``setdefault``，截图测试会真的显示全屏选区并抢占桌面输入。
"""

from __future__ import annotations

import os

import pytest


# 必须早于任何 PyQt5 导入；测试不应连接真实桌面或使用硬件 OpenGL。
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_OPENGL"] = "software"


def _dispose_qt_widgets(app) -> None:
    """停止窗口所属定时器并处理 DeferredDelete，避免状态泄漏到下一项测试。"""
    from PyQt5.QtCore import QCoreApplication, QEvent, QTimer

    for widget in tuple(app.topLevelWidgets()):
        try:
            for timer in widget.findChildren(QTimer):
                timer.stop()
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            # C++ 对象可能已经由测试自身销毁。
            continue
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.fixture(scope="session", autouse=True)
def _qt_application():
    """整轮测试只保留一个 QApplication，避免局部引用析构原生应用对象。"""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
    _dispose_qt_widgets(app)


@pytest.fixture(autouse=True)
def _cleanup_qt_state(_qt_application):
    """每项测试后回收未显式关闭的顶层窗口和子定时器。"""
    yield
    _dispose_qt_widgets(_qt_application)
