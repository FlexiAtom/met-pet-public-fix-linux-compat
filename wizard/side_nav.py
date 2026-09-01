"""设置页左侧导航栏。

视觉上是 Codex 式布局：顶部工具栏之下，左侧为深色侧边导航
（分类 + 缺失配置圆点），右侧为页面栈；右侧顶部可挂一条状态行。
对外暴露与 ``QTabWidget`` 兼容的最小接口（addTab/tabText/tabIcon/
tabToolTip/widget/count/setCurrentIndex/currentChanged/tabBar），
让既有配置收集与测试代码无需感知布局变化。
"""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


def _transparent_icon(size: int = 14) -> QIcon:
    """占位透明图标：让无提示的导航项与带圆点的项文本左缘对齐。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    return QIcon(pixmap)


class SideNavTabs(QWidget):
    """侧边导航 + 页面栈；接口对齐 QTabWidget 的常用子集。"""

    currentChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WizardNavRoot")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("WizardSidebar")
        sidebar_v = QVBoxLayout(sidebar)
        sidebar_v.setContentsMargins(12, 10, 12, 12)
        sidebar_v.setSpacing(6)

        caption = QLabel("配置分类")
        caption.setObjectName("WizardNavCaption")
        sidebar_v.addWidget(caption)

        self.list = QListWidget()
        self.list.setObjectName("WizardNav")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setIconSize(QSize(14, 14))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(True)
        self.list.setAccessibleName("配置分类")
        self.list.setAccessibleDescription(
            "带橙色圆点的分类缺少必要配置，具体原因见提示与顶部状态"
        )
        sidebar_v.addWidget(self.list, 1)

        layout.addWidget(sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self._top_widget: QWidget | None = None
        self.stack = QStackedWidget()
        self.stack.setObjectName("WizardPages")
        right.addWidget(self.stack, 1)
        layout.addLayout(right, 1)

        self._list = self.list
        self._placeholder_icon = _transparent_icon()
        self.list.currentRowChanged.connect(self._on_row_changed)

    def set_top_widget(self, widget: QWidget | None) -> None:
        """在右侧页面栈上方放置一条全宽状态行（可传 None 撤下）。"""
        if self._top_widget is widget:
            return
        old = self._top_widget
        if old is not None:
            old.setParent(None)
        self._top_widget = widget
        if widget is not None:
            # insert 的位置：右栏 VBox 的第 0 行，页面栈之前。
            self.layout().itemAt(1).layout().insertWidget(0, widget)

    # ---------------------------------------------------------------- 兼容层
    def _on_row_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        self.currentChanged.emit(row)

    def addTab(self, widget: QWidget, label: str) -> int:
        """追加一个页面与导航项，返回页面索引。"""
        self.stack.addWidget(widget)
        item = QListWidgetItem(self._placeholder_icon, label)
        self.list.addItem(item)
        if self.list.count() == 1:
            self.list.setCurrentRow(0)
        return self.stack.count() - 1

    def count(self) -> int:
        return self.stack.count()

    def widget(self, index: int) -> QWidget | None:
        return self.stack.widget(index)

    def indexOf(self, widget: QWidget) -> int:
        return self.stack.indexOf(widget)

    def tabText(self, index: int) -> str:
        item = self.list.item(index)
        return item.text() if item else ""

    def setTabText(self, index: int, text: str) -> None:
        item = self.list.item(index)
        if item:
            item.setText(text)

    def setTabIcon(self, index: int, icon: QIcon) -> None:
        item = self.list.item(index)
        if not item:
            return
        has_icon = icon is not None and not icon.isNull()
        # 用 UserRole 记录“是否真的有状态图标”，保证 tabIcon() 的
        # isNull() 语义与 QTabWidget 一致（无缺失配置 → 空图标）。
        item.setData(Qt.UserRole, has_icon)
        item.setIcon(icon if has_icon else self._placeholder_icon)

    def tabIcon(self, index: int) -> QIcon:
        item = self.list.item(index)
        if item is None or not item.data(Qt.UserRole):
            return QIcon()
        return item.icon()

    def setTabToolTip(self, index: int, tip: str) -> None:
        item = self.list.item(index)
        if item:
            item.setToolTip(tip)

    def tabToolTip(self, index: int) -> str:
        item = self.list.item(index)
        return item.toolTip() if item else ""

    def setCurrentIndex(self, index: int) -> None:
        self.list.setCurrentRow(index)

    def currentIndex(self) -> int:
        return self.list.currentRow()

    def tabBar(self):
        """兼容旧调用点（仅用于设置无障碍名称）。"""
        return self.list

    def setIconSize(self, size: QSize) -> None:
        self.list.setIconSize(size)
