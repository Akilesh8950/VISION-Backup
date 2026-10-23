from __future__ import annotations

DARK = """
QMainWindow, QWidget { background: #17191c; color: #e8eaed; font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif; }
QFrame { background: #202329; border: 1px solid #353a42; border-radius: 8px; }
QPushButton { background: #2a2f37; border: 1px solid #454c58; border-radius: 6px; padding: 6px 10px; }
QPushButton:hover { background: #353c48; }
QPushButton:disabled { color: #7a818c; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox { background: #111317; border: 1px solid #3a404b; border-radius: 6px; padding: 5px; selection-background-color: #245a8f; }
QListWidget, QTableWidget, QTreeView { background: #111317; alternate-background-color: #191c21; border: 1px solid #353a42; }
QLabel { background: transparent; border: none; }
QToolButton { background: transparent; border: none; padding: 4px; }
QToolButton:hover { background: #303641; border-radius: 4px; }
QTabWidget::pane { border: 1px solid #353a42; }
QTabBar::tab { background: #22262d; padding: 7px 12px; }
QTabBar::tab:selected { background: #323945; }
QStatusBar { background: #111317; }
QScrollBar:vertical { width: 10px; } QScrollBar:horizontal { height: 10px; }
"""

LIGHT = """
QMainWindow, QWidget { background: #f5f6f8; color: #171a1f; font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif; }
QFrame { background: #ffffff; border: 1px solid #d4d8e0; border-radius: 8px; }
QPushButton { background: #ffffff; border: 1px solid #b9c0cc; border-radius: 6px; padding: 6px 10px; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox { background: #ffffff; border: 1px solid #b9c0cc; border-radius: 6px; padding: 5px; }
QListWidget, QTableWidget, QTreeView { background: #ffffff; alternate-background-color: #eef1f5; }
QLabel { background: transparent; border: none; }
"""


def stylesheet(theme: str) -> str:
    return LIGHT if theme.lower() == "light" else DARK
