"""配置向导的设计令牌、QSS 与无障碍辅助函数（墨樱夜）。"""

from __future__ import annotations

import re

from meapet.ui_theme import (
    BUNDLED_CHEVRON_DOWN_PATH,
    BUNDLED_CHEVRON_UP_PATH,
    BUTTON_SECONDARY_BG,
    BUTTON_SECONDARY_BG_HOVER,
    BUTTON_SECONDARY_BG_PRESSED,
    DISPLAY_FONT_FAMILY,
    FONT_FAMILY,
    GRADIENT_PAPER,
    GRADIENT_PRIMARY,
    GRADIENT_PRIMARY_HOVER,
    GRADIENT_PROGRESS,
    MIN_TARGET_SIZE,
    MONO_FONT_FAMILY,
    PALETTE,
    RADIUS_LARGE,
    RADIUS_MEDIUM,
    RADIUS_SMALL,
    rgba,
    seam_highlight,
)


COLOR_BG = PALETTE["canvas"]
COLOR_CARD = PALETTE["surface"]
COLOR_ELEVATED = PALETTE["surface_elevated"]
COLOR_INPUT = PALETTE["surface_input"]
COLOR_ACCENT = PALETTE["primary"]
COLOR_ACCENT_2 = PALETTE["secondary"]
COLOR_FOCUS = PALETTE["focus"]
COLOR_TEXT = PALETTE["text_primary"]
COLOR_TEXT_SECONDARY = PALETTE["text_secondary"]
COLOR_MUTED = PALETTE["text_muted"]
COLOR_BORDER = PALETTE["border"]
COLOR_BORDER_STRONG = PALETTE["border_strong"]
COLOR_OK = PALETTE["success"]
COLOR_WARN = PALETTE["warning"]
COLOR_ERR = PALETTE["danger"]


STYLE_PAGE_CARD = f"""
    QFrame#PageCard {{
        background: {GRADIENT_PAPER};
        border: 1px solid {COLOR_BORDER};
        border-top-color: {seam_highlight(75)};
        border-radius: {RADIUS_MEDIUM}px;
    }}
"""

STYLE_INPUT = f"""
    QLineEdit {{
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 12px;
        font-size: 14px;
        selection-background-color: {rgba(COLOR_ACCENT, 200)};
        selection-color: {PALETTE['on_primary']};
    }}
    QLineEdit:hover {{
        border-color: {COLOR_FOCUS};
    }}
    QLineEdit:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 11px;
    }}
    QLineEdit:disabled {{
        background: {rgba(COLOR_INPUT, 150)};
        color: {rgba(COLOR_MUTED, 120)};
        border-color: {rgba(COLOR_BORDER, 150)};
    }}
"""

STYLE_BTN_PRIMARY = f"""
    QPushButton {{
        background: {GRADIENT_PRIMARY};
        color: {PALETTE['on_primary']};
        border: 1px solid {COLOR_ACCENT};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 22px;
        font-size: 14px;
        font-weight: 700;
    }}
    QPushButton:hover {{
        background: {GRADIENT_PRIMARY_HOVER};
        border-color: {PALETTE['primary_hover']};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 21px;
    }}
    QPushButton:pressed {{
        background: {COLOR_ACCENT};
    }}
    QPushButton:disabled {{
        background: {rgba(COLOR_ELEVATED, 150)};
        color: {rgba(COLOR_MUTED, 120)};
        border-color: {rgba(COLOR_BORDER, 150)};
    }}
"""

STYLE_BTN_SECONDARY = f"""
    QPushButton {{
        background: {BUTTON_SECONDARY_BG};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 18px;
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {BUTTON_SECONDARY_BG_HOVER};
        border-color: {COLOR_FOCUS};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 17px;
    }}
    QPushButton:pressed {{
        background: {BUTTON_SECONDARY_BG_PRESSED};
    }}
    QPushButton:disabled {{
        background: {rgba(COLOR_ELEVATED, 150)};
        color: {rgba(COLOR_MUTED, 120)};
        border-color: {rgba(COLOR_BORDER, 150)};
    }}
"""


WIZARD_STYLESHEET = f"""
    QDialog {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
    }}
    QWidget#WizardRoot {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
        font-size: 14px;
    }}
    QFrame#WizardShell {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #1D1729, stop:0.32 {COLOR_BG}, stop:1 #120E1A);
        border: 1px solid {COLOR_BORDER};
        border-top-color: {seam_highlight(85)};
        border-radius: {RADIUS_LARGE}px;
    }}
    QFrame#WizardHeader {{
        background: qradialgradient(cx:0.08, cy:0.0, radius:0.9, fx:0.08, fy:0.0,
            stop:0 rgba(255, 157, 190, 30),
            stop:0.5 rgba(183, 166, 255, 14),
            stop:1 rgba(22, 17, 31, 0));
        border: none;
    }}
    QFrame#WizardFooter {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 rgba(22, 17, 31, 0), stop:1 rgba(16, 12, 24, 150));
        border: none;
    }}
    QFrame#WizardDivider {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {rgba(COLOR_ACCENT, 130)},
            stop:0.26 {rgba(PALETTE['accent'], 75)},
            stop:1 {rgba(COLOR_BORDER, 0)});
        border: none;
        min-height: 1px;
        max-height: 1px;
    }}
    QFrame#PageCard {{
        background: {GRADIENT_PAPER};
        border: 1px solid {COLOR_BORDER};
        border-top-color: {seam_highlight(75)};
        border-radius: {RADIUS_MEDIUM}px;
    }}
    QFrame#SectionCard {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #352A4A, stop:1 #2A213A);
        border: 1px solid {COLOR_BORDER};
        border-left: 3px solid {rgba(COLOR_ACCENT, 110)};
        border-radius: 12px;
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: {COLOR_TEXT};
    }}
    QLabel#BrandMark {{
        background: {GRADIENT_PRIMARY};
        color: {PALETTE['on_primary']};
        border-radius: 14px;
        font-size: 14px;
        font-weight: 700;
    }}
    QLabel#BrandName {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 16px;
        font-weight: 700;
    }}
    QLabel#StepLabel {{
        color: {COLOR_TEXT_SECONDARY};
        font-size: 12px;
        font-weight: 600;
        padding: 5px 12px;
        background: {rgba(COLOR_ELEVATED, 200)};
        border: 1px solid {COLOR_BORDER};
        border-radius: 11px;
    }}
    QLabel#ConfigStatus {{
        min-height: 22px;
        color: {COLOR_TEXT_SECONDARY};
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel#ConfigStatus[status="error"] {{
        color: {COLOR_ERR};
    }}
    QLabel#ConfigStatus[status="success"] {{
        color: {COLOR_OK};
    }}
    QLabel#PageEyebrow {{
        color: {PALETTE['accent']};
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#PageTitle {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 22px;
        font-weight: 700;
    }}
    QLabel#PageDescription {{
        color: {COLOR_TEXT_SECONDARY};
        font-size: 13px;
    }}
    QLabel#FieldLabel {{
        color: {COLOR_TEXT_SECONDARY};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel#HelperText {{
        color: {COLOR_MUTED};
        font-size: 12px;
    }}
    QLabel#SectionTitle {{
        color: {COLOR_TEXT};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 16px;
        font-weight: 700;
        padding-top: 4px;
    }}
    QLabel#InlineFieldLabel {{
        color: {COLOR_TEXT_SECONDARY};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel#FontScaleValue,
    QLabel#PetSizeValue {{
        color: {COLOR_ACCENT};
        background: transparent;
        border: none;
        padding: 0;
        font-size: 14px;
        font-weight: 700;
    }}
    QLabel[status="success"] {{
        color: {COLOR_OK};
    }}
    QLabel[status="warning"] {{
        color: {COLOR_WARN};
    }}
    QLabel[status="error"] {{
        color: {COLOR_ERR};
    }}
    QLabel[status="muted"] {{
        color: {COLOR_MUTED};
    }}
    QTextBrowser,
    QTextBrowser#SummaryOutput {{
        background: {COLOR_INPUT};
        color: {COLOR_TEXT_SECONDARY};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_MEDIUM}px;
        padding: 12px 14px;
        font-size: 13px;
        selection-background-color: {rgba(COLOR_ACCENT, 200)};
        selection-color: {PALETTE['on_primary']};
    }}
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox {{
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 12px;
        font-size: 14px;
        selection-background-color: {rgba(COLOR_ACCENT, 200)};
        selection-color: {PALETTE['on_primary']};
    }}
    QLineEdit:hover,
    QTextEdit:hover,
    QPlainTextEdit:hover,
    QComboBox:hover,
    QSpinBox:hover,
    QDoubleSpinBox:hover {{
        border-color: {COLOR_FOCUS};
    }}
    QLineEdit:focus,
    QTextEdit:focus,
    QPlainTextEdit:focus,
    QComboBox:focus,
    QSpinBox:focus,
    QDoubleSpinBox:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 11px;
    }}
    QLineEdit:disabled,
    QTextEdit:disabled,
    QPlainTextEdit:disabled,
    QComboBox:disabled,
    QSpinBox:disabled,
    QDoubleSpinBox:disabled {{
        background: {rgba(COLOR_INPUT, 150)};
        color: {rgba(COLOR_MUTED, 120)};
        border-color: {rgba(COLOR_BORDER, 150)};
    }}
    QSpinBox,
    QDoubleSpinBox {{
        padding-right: 34px;
    }}
    QSpinBox::up-button,
    QDoubleSpinBox::up-button,
    QSpinBox::down-button,
    QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        width: 28px;
        color: {COLOR_TEXT_SECONDARY};
        background: {COLOR_ELEVATED};
        border-left: 1px solid {COLOR_BORDER_STRONG};
    }}
    QSpinBox::up-button,
    QDoubleSpinBox::up-button {{
        subcontrol-position: top right;
        border-bottom: 1px solid {COLOR_BORDER};
        border-top-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QSpinBox::down-button,
    QDoubleSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QSpinBox::up-button:hover,
    QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover,
    QDoubleSpinBox::down-button:hover {{
        background: {rgba(COLOR_FOCUS, 45)};
        border-left-color: {COLOR_FOCUS};
    }}
    QSpinBox::up-arrow,
    QDoubleSpinBox::up-arrow {{
        image: url("{BUNDLED_CHEVRON_UP_PATH}");
        width: 10px;
        height: 7px;
    }}
    QSpinBox::down-arrow,
    QDoubleSpinBox::down-arrow,
    QComboBox::down-arrow {{
        image: url("{BUNDLED_CHEVRON_DOWN_PATH}");
        width: 10px;
        height: 7px;
    }}
    QTextEdit#LogOutput {{
        color: {COLOR_TEXT_SECONDARY};
        font-family: {MONO_FONT_FAMILY};
        font-size: 12px;
    }}
    QComboBox::drop-down {{
        border: none;
        border-left: 1px solid {COLOR_BORDER};
        background: {rgba(COLOR_ELEVATED, 170)};
        width: 34px;
        border-top-right-radius: {RADIUS_SMALL - 1}px;
        border-bottom-right-radius: {RADIUS_SMALL - 1}px;
    }}
    QComboBox QAbstractItemView {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        selection-background-color: {rgba(COLOR_ACCENT, 70)};
        selection-color: {COLOR_TEXT};
        padding: 4px;
        outline: 0;
    }}
    QCheckBox,
    QRadioButton {{
        color: {COLOR_TEXT};
        spacing: 10px;
        font-size: 14px;
        border: 2px solid transparent;
        border-radius: 8px;
        padding: 0px 4px;
    }}
    QCheckBox:focus,
    QRadioButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        color: {COLOR_TEXT};
    }}
    QCheckBox::indicator,
    QRadioButton::indicator {{
        width: 20px;
        height: 20px;
        border: 2px solid {COLOR_BORDER_STRONG};
        background: {COLOR_INPUT};
    }}
    QCheckBox::indicator {{
        border-radius: 6px;
    }}
    QRadioButton::indicator {{
        border-radius: 11px;
    }}
    QCheckBox::indicator:hover,
    QRadioButton::indicator:hover {{
        border-color: {COLOR_FOCUS};
    }}
    QCheckBox::indicator:checked,
    QRadioButton::indicator:checked {{
        background: {GRADIENT_PRIMARY};
        border-color: {COLOR_ACCENT};
    }}
    QCheckBox::indicator:checked:disabled,
    QRadioButton::indicator:checked:disabled {{
        background: {rgba(COLOR_ACCENT, 90)};
        border-color: {rgba(COLOR_BORDER_STRONG, 140)};
    }}
    QCheckBox:disabled,
    QRadioButton:disabled {{
        color: {rgba(COLOR_MUTED, 120)};
    }}
    QPushButton {{
        background: {BUTTON_SECONDARY_BG};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 9px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {BUTTON_SECONDARY_BG_HOVER};
        border-color: {COLOR_FOCUS};
    }}
    QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 8px 15px;
    }}
    QPushButton:pressed {{
        background: {BUTTON_SECONDARY_BG_PRESSED};
    }}
    QPushButton:disabled {{
        color: {rgba(COLOR_MUTED, 120)};
        background: {rgba(COLOR_ELEVATED, 150)};
        border-color: {rgba(COLOR_BORDER, 150)};
    }}
    QPushButton#AdvancedToggle {{
        min-height: 32px;
        background: transparent;
        color: {COLOR_MUTED};
        border: 1px solid transparent;
        text-align: left;
        padding: 4px 0px;
    }}
    QPushButton#AdvancedToggle:hover {{
        background: {rgba(PALETTE['accent'], 26)};
        color: {COLOR_TEXT_SECONDARY};
        border-color: transparent;
    }}
    QPushButton#AdvancedToggle:focus {{
        border: 2px solid {COLOR_FOCUS};
        padding: 3px 0px;
    }}
    QPushButton#PrimaryButton {{
        background: {GRADIENT_PRIMARY};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 15px;
        color: {PALETTE['on_primary']};
        border-color: {COLOR_ACCENT};
        font-weight: 700;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {GRADIENT_PRIMARY_HOVER};
        border-color: {PALETTE['primary_hover']};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {COLOR_ACCENT};
    }}
    QPushButton#CloseButton {{
        background: transparent;
        color: {COLOR_MUTED};
        border-color: transparent;
        border-radius: 12px;
        font-size: 17px;
        padding: 0;
    }}
    QPushButton#CloseButton:hover {{
        background: {rgba(COLOR_ERR, 40)};
        color: {COLOR_ERR};
        border-color: {rgba(COLOR_ERR, 110)};
    }}
    QPushButton#CloseButton:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QPushButton#ShapeAddTool:checked {{
        background: {rgba(COLOR_OK, 48)};
        color: {COLOR_TEXT};
        border: 2px solid {COLOR_OK};
        padding: 8px 15px;
    }}
    QPushButton#ShapeSubtractTool:checked {{
        background: {rgba(COLOR_WARN, 48)};
        color: {COLOR_TEXT};
        border: 2px solid {COLOR_WARN};
        padding: 8px 15px;
    }}
    QProgressBar {{
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {GRADIENT_PROGRESS};
        border-radius: 7px;
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {COLOR_INPUT};
        border: 1px solid {COLOR_BORDER};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {GRADIENT_PROGRESS};
        border-radius: 3px;
    }}
    QSlider::add-page:horizontal {{
        background: {COLOR_INPUT};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        width: 20px;
        margin: -8px 0;
        background: {COLOR_TEXT};
        border: 3px solid {COLOR_ACCENT};
        border-radius: 10px;
    }}
    QSlider::handle:horizontal:hover {{
        border-color: {COLOR_FOCUS};
    }}
    QSlider::handle:horizontal:pressed {{
        background: {PALETTE['primary_hover']};
    }}
    QTabWidget#ConfigurationTabs {{
        background: transparent;
        border: none;
    }}
    QTabWidget#ConfigurationTabs::pane {{
        background: {GRADIENT_PAPER};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_MEDIUM}px;
        top: -1px;
        margin: 0 18px 10px 18px;
    }}
    QTabBar {{
        qproperty-drawBase: 0;
    }}
    QTabBar::tab {{
        min-width: 112px;
        min-height: 32px;
        padding: 9px 18px;
        margin: 0 6px 0 0;
        color: {COLOR_MUTED};
        background: transparent;
        border: 1px solid transparent;
        border-top-left-radius: 12px;
        border-top-right-radius: 12px;
        font-size: 14px;
        font-weight: 600;
    }}
    QTabBar::tab:hover {{
        color: {COLOR_TEXT};
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 rgba(255, 157, 190, 34),
            stop:1 rgba(183, 166, 255, 22));
        border-color: {rgba(PALETTE['accent'], 55)};
    }}
    QTabBar::tab:selected {{
        color: {COLOR_TEXT};
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {COLOR_ACCENT}, stop:0.06 {COLOR_ACCENT},
            stop:0.061 {COLOR_CARD}, stop:1 {COLOR_CARD});
        border-color: {COLOR_BORDER};
        border-bottom-color: {COLOR_CARD};
        font-weight: 700;
    }}
    QTabBar::tab:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QWidget#ConfigurationTabContent {{
        background: transparent;
    }}
    QScrollArea#ConfigurationTabScroll {{
        background: transparent;
        border: none;
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {rgba(COLOR_BORDER_STRONG, 170)};
        border-radius: 4px;
        min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {COLOR_MUTED};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QToolTip {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: 8px;
        padding: 6px 10px;
    }}
    QMessageBox {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
        font-size: 14px;
    }}
    QMessageBox QLabel {{
        background: transparent;
        color: {COLOR_TEXT};
    }}
    QMessageBox QLabel#qt_msgbox_label {{
        min-width: 280px;
    }}
    QMessageBox QPushButton {{
        background: {BUTTON_SECONDARY_BG};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 8px 18px;
        min-width: 88px;
        min-height: 36px;
        font-weight: 600;
    }}
    QMessageBox QPushButton:hover {{
        background: {BUTTON_SECONDARY_BG_HOVER};
        border-color: {COLOR_FOCUS};
    }}
    QMessageBox QPushButton:focus {{
        border: 2px solid {COLOR_FOCUS};
    }}
    QMessageBox QPushButton:default,
    QMessageBox QPushButton[default="true"] {{
        background: {GRADIENT_PRIMARY};
        color: {PALETTE['on_primary']};
        border-color: {COLOR_ACCENT};
        font-weight: 700;
    }}
    QFileDialog {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
        font-family: {FONT_FAMILY};
    }}
    QFileDialog QWidget {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
    }}
    QFileDialog QLineEdit,
    QFileDialog QComboBox {{
        background: {COLOR_INPUT};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 8px 10px;
    }}
    QFileDialog QPushButton {{
        background: {BUTTON_SECONDARY_BG};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px;
        padding: 8px 14px;
        min-height: 36px;
        font-weight: 600;
    }}
    QFileDialog QPushButton:hover {{
        background: {BUTTON_SECONDARY_BG_HOVER};
        border-color: {COLOR_FOCUS};
    }}
    QFileDialog QTreeView,
    QFileDialog QListView {{
        background: {COLOR_CARD};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        border-radius: {RADIUS_SMALL}px;
        selection-background-color: {rgba(COLOR_ACCENT, 70)};
        selection-color: {COLOR_TEXT};
    }}
    QFileDialog QHeaderView::section {{
        background: {COLOR_ELEVATED};
        color: {COLOR_TEXT_SECONDARY};
        border: none;
        border-right: 1px solid {COLOR_BORDER};
        border-bottom: 1px solid {COLOR_BORDER};
        padding: 6px 8px;
    }}
"""


def set_status(widget, status: str, text: str | None = None) -> None:
    """给状态标签设置语义属性，并触发 QSS 重绘。"""
    if text is not None:
        widget.setText(text)
    if not hasattr(widget, "setProperty"):
        fallback_colors = {
            "success": COLOR_OK,
            "warning": COLOR_WARN,
            "error": COLOR_ERR,
            "muted": COLOR_MUTED,
        }
        if hasattr(widget, "setStyleSheet"):
            widget.setStyleSheet(f"color: {fallback_colors.get(status, COLOR_TEXT)};")
        return

    widget.setProperty("status", status)
    if hasattr(widget, "style"):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
    if hasattr(widget, "update"):
        widget.update()


def apply_wizard_dialog_style(dialog) -> None:
    """给 QFileDialog / 临时说明窗统一套配置中心主题。"""
    from meapet.ui_theme import set_scaled_stylesheet

    if dialog is None:
        return
    set_scaled_stylesheet(dialog, WIZARD_STYLESHEET)
    if (
        hasattr(dialog, "setObjectName")
        and hasattr(dialog, "objectName")
        and not dialog.objectName()
    ):
        dialog.setObjectName("WizardDialog")


def field_label(text: str, *, inline: bool = False):
    """创建带语义样式的字段标签，避免行内 QLabel 落到系统默认外观。"""
    from PyQt5.QtWidgets import QLabel

    label = QLabel(text)
    label.setObjectName("InlineFieldLabel" if inline else "FieldLabel")
    return label


def styled_message_box(
    parent,
    *,
    title: str,
    text: str,
    icon=None,
    buttons=None,
    default_button=None,
) -> int:
    """显示无原生标题栏的主题消息框，返回 QMessageBox 标准按钮结果。"""
    from meapet.message_dialog import show_message_dialog

    return show_message_dialog(
        parent,
        title=title,
        text=text,
        icon=icon,
        buttons=buttons,
        default_button=default_button,
    )


def styled_open_file(
    parent,
    title: str,
    directory: str = "",
    file_filter: str = "All (*.*)",
) -> str:
    """打开带配置中心主题的文件选择对话框，返回所选路径或空串。"""
    from PyQt5.QtWidgets import QFileDialog

    dialog = QFileDialog(parent, title, directory, file_filter)
    dialog.setFileMode(QFileDialog.ExistingFile)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    apply_wizard_dialog_style(dialog)
    if dialog.exec_():
        selected = dialog.selectedFiles()
        return selected[0] if selected else ""
    return ""


def styled_open_directory(
    parent,
    title: str,
    directory: str = "",
) -> str:
    """打开带配置中心主题的目录选择对话框，返回所选路径或空串。"""
    from PyQt5.QtWidgets import QFileDialog

    dialog = QFileDialog(parent, title, directory)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    apply_wizard_dialog_style(dialog)
    if dialog.exec_():
        selected = dialog.selectedFiles()
        return selected[0] if selected else ""
    return ""


def prepare_accessible_page(root) -> None:
    """统一表单交互尺寸、焦点策略与可访问名称。"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import (
        QAbstractButton,
        QAbstractSpinBox,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QSlider,
        QTextEdit,
    )

    for button in root.findChildren(QAbstractButton):
        if button.maximumWidth() < MIN_TARGET_SIZE:
            button.setMaximumWidth(MIN_TARGET_SIZE)
        button.setMinimumSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        button.setFocusPolicy(Qt.StrongFocus)
        if not button.accessibleName():
            label = _plain_accessible_text(button.text())
            button.setAccessibleName(label or button.toolTip() or "操作按钮")

    text_controls = (
        root.findChildren(QLineEdit)
        + root.findChildren(QTextEdit)
        + root.findChildren(QPlainTextEdit)
    )
    for control in text_controls:
        control.setMinimumHeight(MIN_TARGET_SIZE)
        control.setFocusPolicy(Qt.StrongFocus)
        if not control.accessibleName():
            label = control.placeholderText() if hasattr(control, "placeholderText") else ""
            control.setAccessibleName(label or control.objectName() or "配置输入")
        if not control.accessibleDescription() and hasattr(control, "placeholderText"):
            control.setAccessibleDescription(control.placeholderText())

    for combo in root.findChildren(QComboBox):
        combo.setMinimumHeight(MIN_TARGET_SIZE)
        combo.setFocusPolicy(Qt.StrongFocus)
        if not combo.accessibleName():
            combo.setAccessibleName(combo.objectName() or "配置选项")

    for spin_box in root.findChildren(QAbstractSpinBox):
        spin_box.setMinimumHeight(MIN_TARGET_SIZE)
        spin_box.setFocusPolicy(Qt.StrongFocus)
        if not spin_box.accessibleName():
            spin_box.setAccessibleName(spin_box.objectName() or "数值设置")

    for slider in root.findChildren(QSlider):
        slider.setMinimumHeight(MIN_TARGET_SIZE)
        slider.setFocusPolicy(Qt.StrongFocus)
        if not slider.accessibleName():
            slider.setAccessibleName(slider.objectName() or "数值调节")


def _plain_accessible_text(text: str) -> str:
    """去掉按钮文字首尾的装饰符号，同时保留中文和英文标签。"""
    value = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or ""))
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff）)]+$", "", value)
    return value.strip()
