"""Light/dark palettes and the application stylesheet, following the Windows theme unless overridden."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

UI_FONTS = ["Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Arial"]
MONO_FONTS = ["Cascadia Mono", "Consolas", "Menlo", "Courier New"]


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    border: str
    divider: str
    text: str
    text2: str
    text3: str
    chip: str
    hover: str
    field: str
    accent: str
    on_accent: str = "#FFFFFF"


DARK = Palette(True, "#1F2025", "rgba(255,255,255,0.12)", "rgba(255,255,255,0.08)", "#F2F2F5", "#A4A7AF", "#6F737C",
               "rgba(255,255,255,0.08)", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.07)", "#0A84FF")
LIGHT = Palette(False, "#F8F8FA", "rgba(0,0,0,0.12)", "rgba(0,0,0,0.08)", "#1C1C1E", "#5F6368", "#9AA0A6",
                "rgba(0,0,0,0.06)", "rgba(0,0,0,0.05)", "rgba(0,0,0,0.05)", "#0A6CFF")


def ui_font(size: float = 13, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont()
    f.setFamilies(UI_FONTS)
    f.setPointSizeF(size * 0.75)         # sizes below are in px like the V1 design
    f.setWeight(weight)
    return f


def mono_font(size: float = 12) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FONTS)
    f.setStyleHint(QFont.Monospace)
    f.setPointSizeF(size * 0.75)
    return f


class Theme(QObject):
    changed = Signal()

    def __init__(self, mode: str = "system"):
        super().__init__()
        self.mode = mode
        hints = QGuiApplication.styleHints()
        try:
            hints.colorSchemeChanged.connect(lambda *_: self.changed.emit())
        except AttributeError:
            pass

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.changed.emit()

    @property
    def palette(self) -> Palette:
        dark = self.mode == "dark"
        if self.mode == "system":
            try:
                dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
            except AttributeError:
                dark = False
        base = DARK if dark else LIGHT
        accent = self._system_accent()
        if accent:
            from dataclasses import replace
            base = replace(base, accent=accent)
        return base

    @staticmethod
    def _system_accent() -> str | None:
        try:
            c = QGuiApplication.palette().color(QPalette.Accent)
            return c.name() if c.isValid() and c.alpha() > 0 else None
        except AttributeError:
            return None


def stylesheet(p: Palette) -> str:
    return f"""
    QWidget {{ color: {p.text}; font-size: 13px; }}
    QFrame#card {{ background: {p.bg}; border: 1px solid {p.border}; border-radius: 12px; }}
    QFrame[divider="true"] {{ background: {p.divider}; border: none; max-height: 1px; min-height: 1px; }}
    QFrame[vdivider="true"] {{ background: {p.divider}; border: none; max-width: 1px; min-width: 1px; }}
    QLineEdit {{ background: transparent; border: none; selection-background-color: {p.accent}; selection-color: white; }}
    QLineEdit#search {{ font-size: 17px; }}
    QPlainTextEdit {{ background: transparent; border: none; selection-background-color: {p.accent}; selection-color: white; }}
    QLabel#muted {{ color: {p.text2}; font-size: 11px; }}
    QLabel#faint {{ color: {p.text3}; font-size: 11px; }}
    QPushButton[chip="true"] {{ background: {p.chip}; border: none; border-radius: 12px; padding: 0px 12px;
                               color: {p.text}; font-size: 11.5px; }}
    QPushButton[chip="true"]:hover {{ background: {p.hover}; }}
    QPushButton[chip="true"]:checked {{ background: {p.accent}; color: white; font-weight: 600; }}
    QPushButton#primary {{ background: {p.accent}; color: white; border: none; border-radius: 6px; padding: 5px 14px; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {p.accent}; }}
    QPushButton[tool="true"], QToolButton[tool="true"] {{ background: {p.chip}; border: none; border-radius: 6px; padding: 5px 7px; color: {p.text}; }}
    QPushButton[tool="true"]:hover, QToolButton[tool="true"]:hover {{ background: {p.hover}; }}
    QToolButton::menu-indicator {{ image: none; }}
    QMenu {{ background: {p.bg}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {p.accent}; color: white; }}
    QMenu::separator {{ height: 1px; background: {p.divider}; margin: 4px 6px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.chip}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.text3}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {p.bg}; color: {p.text}; border: 1px solid {p.border}; padding: 3px 6px; }}
    QLabel#toast {{ background: {p.bg}; border: 1px solid {p.border}; border-radius: 13px; padding: 5px 14px;
                   font-size: 12px; font-weight: 600; }}
    QDialog, QMessageBox {{ background: {p.bg}; }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ padding: 6px 14px; margin-right: 2px; border-radius: 6px; color: {p.text2}; }}
    QTabBar::tab:selected {{ background: {p.chip}; color: {p.text}; }}
    QCheckBox {{ spacing: 8px; }}
    QComboBox, QSpinBox, QListWidget, QLineEdit[field="true"] {{ background: {p.field}; border: 1px solid {p.divider};
                   border-radius: 6px; padding: 4px 8px; }}
    QComboBox QAbstractItemView {{ background: {p.bg}; border: 1px solid {p.border}; selection-background-color: {p.accent}; }}
    QPushButton[plain="true"] {{ background: {p.chip}; border: none; border-radius: 6px; padding: 5px 12px; }}
    QPushButton[plain="true"]:hover {{ background: {p.hover}; }}
    """
