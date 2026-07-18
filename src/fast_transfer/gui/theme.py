"""Dark theme: one palette, one stylesheet, applied application-wide.

Colours are defined once here and reused by the widgets and the icon so the
product reads as a single system rather than a pile of styled controls.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


class Colors:
    """The dark palette. Accent matches the CLI's accent so both look related."""

    BASE = "#0f1115"        # window background
    SURFACE = "#161922"     # panels, trees
    SURFACE_ALT = "#1c2029" # inputs, headers
    SURFACE_HI = "#232834"  # hover
    BORDER = "#262b36"
    BORDER_HI = "#38414f"

    TEXT = "#e6e9ef"
    TEXT_MUTED = "#8b93a7"
    TEXT_DIM = "#5d6579"

    ACCENT = "#4cc2ff"
    ACCENT_HOVER = "#6ecfff"
    ACCENT_PRESSED = "#2fa8e8"
    ACCENT_SOFT = "#1b3a4d"

    SUCCESS = "#3ddc84"
    WARNING = "#ffb454"
    ERROR = "#ff5f6b"

    SELECTION = "#26445c"


RADIUS = 8
SPACING = 12


def apply_theme(app: QApplication) -> None:
    """Install the dark palette and stylesheet. Call once at startup."""
    app.setStyle("Fusion")  # Fusion respects the palette on every platform
    app.setPalette(_palette())
    app.setFont(_font())
    app.setStyleSheet(STYLESHEET)


def _font() -> QFont:
    font = QFont("Segoe UI", 9)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


def _palette() -> QPalette:
    """A real palette, so native-drawn bits (tooltips, menus) match the sheet."""
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(Colors.BASE))
    palette.setColor(role.WindowText, QColor(Colors.TEXT))
    palette.setColor(role.Base, QColor(Colors.SURFACE))
    palette.setColor(role.AlternateBase, QColor(Colors.SURFACE_ALT))
    palette.setColor(role.ToolTipBase, QColor(Colors.SURFACE_ALT))
    palette.setColor(role.ToolTipText, QColor(Colors.TEXT))
    palette.setColor(role.Text, QColor(Colors.TEXT))
    palette.setColor(role.Button, QColor(Colors.SURFACE_ALT))
    palette.setColor(role.ButtonText, QColor(Colors.TEXT))
    palette.setColor(role.BrightText, QColor(Colors.ERROR))
    palette.setColor(role.Link, QColor(Colors.ACCENT))
    palette.setColor(role.Highlight, QColor(Colors.SELECTION))
    palette.setColor(role.HighlightedText, QColor(Colors.TEXT))
    palette.setColor(role.PlaceholderText, QColor(Colors.TEXT_DIM))

    for disabled in (group.Disabled,):
        palette.setColor(disabled, role.Text, QColor(Colors.TEXT_DIM))
        palette.setColor(disabled, role.ButtonText, QColor(Colors.TEXT_DIM))
        palette.setColor(disabled, role.WindowText, QColor(Colors.TEXT_DIM))
    return palette


STYLESHEET = f"""
QWidget {{
    background-color: {Colors.BASE};
    color: {Colors.TEXT};
    font-family: "Segoe UI", "Malgun Gothic", sans-serif;
    font-size: 12px;
}}

/* Labels must not paint the window colour on top of a card's surface --
   otherwise every caption sits in a darker rectangle of its own. */
QLabel, QCheckBox, QGroupBox {{
    background: transparent;
}}

QToolTip {{
    background-color: {Colors.SURFACE_ALT};
    color: {Colors.TEXT};
    border: 1px solid {Colors.BORDER_HI};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* -- panels ----------------------------------------------------------- */

QFrame#Card {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS}px;
}}

QFrame#Divider {{
    background-color: {Colors.BORDER};
    max-height: 1px;
    border: none;
}}

QLabel#PaneTitle {{
    color: {Colors.TEXT};
    font-size: 13px;
    font-weight: 600;
    padding: 2px 0;
}}

QLabel#PaneHint {{
    color: {Colors.TEXT_DIM};
    font-size: 11px;
}}

QLabel#Muted {{ color: {Colors.TEXT_MUTED}; }}
QLabel#StatValue {{ color: {Colors.TEXT}; font-size: 15px; font-weight: 600; }}
QLabel#StatLabel {{ color: {Colors.TEXT_DIM}; font-size: 10px; text-transform: uppercase; }}
QLabel#CurrentFile {{ color: {Colors.ACCENT}; font-size: 11px; }}
QLabel#AppTitle {{ font-size: 16px; font-weight: 700; letter-spacing: 0.3px; }}
QLabel#AppSubtitle {{ color: {Colors.TEXT_DIM}; font-size: 11px; }}

/* -- inputs ------------------------------------------------------------ */

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background-color: {Colors.SURFACE_ALT};
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {Colors.SELECTION};
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {Colors.ACCENT};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {Colors.TEXT_DIM};
    background-color: {Colors.SURFACE};
}}

QComboBox::drop-down {{ border: none; width: 20px; }}
/* Zero width/height keeps this a pure CSS triangle instead of a box. */
QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {Colors.TEXT_MUTED};
    margin-right: 8px;
}}
QComboBox::down-arrow:hover {{ border-top-color: {Colors.TEXT}; }}
QComboBox QAbstractItemView {{
    background-color: {Colors.SURFACE_ALT};
    border: 1px solid {Colors.BORDER_HI};
    border-radius: 6px;
    selection-background-color: {Colors.SELECTION};
    outline: none;
    padding: 4px;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {Colors.SURFACE_HI};
    border: none;
    width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background-color: {Colors.BORDER_HI}; }}

/* -- buttons ----------------------------------------------------------- */

QPushButton {{
    background-color: {Colors.SURFACE_ALT};
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    padding: 7px 14px;
    color: {Colors.TEXT};
}}
QPushButton:hover {{ background-color: {Colors.SURFACE_HI}; border-color: {Colors.BORDER_HI}; }}
QPushButton:pressed {{ background-color: {Colors.BORDER}; }}
QPushButton:disabled {{ color: {Colors.TEXT_DIM}; background-color: {Colors.SURFACE}; border-color: {Colors.BORDER}; }}

QPushButton#Primary {{
    background-color: {Colors.ACCENT};
    border: none;
    color: #06121a;
    font-weight: 600;
    padding: 9px 20px;
}}
QPushButton#Primary:hover {{ background-color: {Colors.ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background-color: {Colors.ACCENT_PRESSED}; }}
QPushButton#Primary:disabled {{ background-color: {Colors.SURFACE_HI}; color: {Colors.TEXT_DIM}; }}

QPushButton#Danger {{ color: {Colors.ERROR}; }}
QPushButton#Danger:hover {{ background-color: #2a1a1e; border-color: {Colors.ERROR}; }}

QPushButton#Icon {{
    padding: 6px;
    border-radius: 6px;
    min-width: 28px;
}}

/* Segmented copy/move selector */
QRadioButton#Segment {{
    background-color: {Colors.SURFACE_ALT};
    border: 1px solid {Colors.BORDER};
    padding: 7px 18px;
    color: {Colors.TEXT_MUTED};
    font-weight: 600;
}}
QRadioButton#Segment::indicator {{ width: 0; height: 0; }}
QRadioButton#Segment:hover {{ background-color: {Colors.SURFACE_HI}; color: {Colors.TEXT}; }}
QRadioButton#Segment:checked {{
    background-color: {Colors.ACCENT_SOFT};
    border-color: {Colors.ACCENT};
    color: {Colors.ACCENT};
}}
QRadioButton#SegmentLeft {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
QRadioButton#SegmentRight {{
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    border-left: none;
}}

/* -- trees ------------------------------------------------------------- */

QTreeView {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    outline: none;
    alternate-background-color: {Colors.SURFACE_ALT};
    show-decoration-selected: 1;
}}
QTreeView::item {{ padding: 4px 2px; border-radius: 4px; }}
QTreeView::item:hover {{ background-color: {Colors.SURFACE_HI}; }}
QTreeView::item:selected {{ background-color: {Colors.SELECTION}; color: {Colors.TEXT}; }}
QTreeView:focus {{ border-color: {Colors.BORDER_HI}; }}

/* Highlight the pane a drag is hovering over */
QTreeView[dropActive="true"] {{
    border: 1px dashed {Colors.ACCENT};
    background-color: {Colors.ACCENT_SOFT};
}}

QHeaderView::section {{
    background-color: {Colors.SURFACE_ALT};
    color: {Colors.TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
    padding: 6px;
    font-weight: 600;
}}

/* -- progress ---------------------------------------------------------- */

QProgressBar {{
    background-color: {Colors.SURFACE_ALT};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background-color: {Colors.ACCENT}; border-radius: 5px; }}

QProgressBar#Secondary {{ height: 4px; }}
QProgressBar#Secondary::chunk {{ background-color: {Colors.TEXT_DIM}; }}

/* -- tabs / lists ------------------------------------------------------ */

QTabWidget::pane {{
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {Colors.TEXT_DIM};
    padding: 7px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {Colors.TEXT}; }}
QTabBar::tab:selected {{ color: {Colors.ACCENT}; border-bottom: 2px solid {Colors.ACCENT}; }}

QPlainTextEdit#Log {{
    background-color: {Colors.SURFACE};
    border: none;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
}}

QTableView {{
    background-color: {Colors.SURFACE};
    border: none;
    gridline-color: {Colors.BORDER};
    outline: none;
}}
QTableView::item:selected {{ background-color: {Colors.SELECTION}; }}

/* -- misc -------------------------------------------------------------- */

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {Colors.BORDER_HI};
    border-radius: 4px;
    background-color: {Colors.SURFACE_ALT};
}}
QCheckBox::indicator:hover {{ border-color: {Colors.ACCENT}; }}
QCheckBox::indicator:checked {{
    background-color: {Colors.ACCENT};
    border-color: {Colors.ACCENT};
}}
QCheckBox:disabled {{ color: {Colors.TEXT_DIM}; }}

QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:horizontal {{ width: {SPACING}px; }}
QSplitter::handle:hover {{ background-color: {Colors.ACCENT_SOFT}; }}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {Colors.BORDER_HI}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {Colors.TEXT_DIM}; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {Colors.BORDER_HI}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QGroupBox {{
    border: 1px solid {Colors.BORDER};
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {Colors.TEXT_MUTED};
}}

QMenu {{
    background-color: {Colors.SURFACE_ALT};
    border: 1px solid {Colors.BORDER_HI};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {Colors.SELECTION}; }}

QStatusBar {{ color: {Colors.TEXT_MUTED}; border-top: 1px solid {Colors.BORDER}; }}
QStatusBar::item {{ border: none; }}
"""


def status_color(status: str) -> str:
    """Map a JobStatus value onto a palette colour."""
    return {
        "completed": Colors.SUCCESS,
        "completed_with_errors": Colors.WARNING,
        "failed": Colors.ERROR,
        "cancelled": Colors.WARNING,
        "paused": Colors.WARNING,
        "running": Colors.ACCENT,
        "scanning": Colors.ACCENT,
    }.get(status, Colors.TEXT_MUTED)


def level_color(level: str) -> str:
    return {
        "ERROR": Colors.ERROR,
        "WARNING": Colors.WARNING,
        "INFO": Colors.TEXT_MUTED,
        "DEBUG": Colors.TEXT_DIM,
    }.get(level.upper(), Colors.TEXT_MUTED)
