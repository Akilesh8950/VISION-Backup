from __future__ import annotations

import difflib

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout


class DiffWidget(QFrame):
    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{payload.get('filename', 'Diff')}</b>"))
        before = str(payload.get("before", "")).splitlines()
        after = str(payload.get("after", "")).splitlines()
        matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
        rows: list[tuple[str, str, str, str, str]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    rows.append(("equal", str(i1 + offset + 1), before[i1 + offset], str(j1 + offset + 1), after[j1 + offset]))
            else:
                max_count = max(i2 - i1, j2 - j1)
                for offset in range(max_count):
                    left_no = str(i1 + offset + 1) if i1 + offset < i2 else ""
                    left = before[i1 + offset] if i1 + offset < i2 else ""
                    right_no = str(j1 + offset + 1) if j1 + offset < j2 else ""
                    right = after[j1 + offset] if j1 + offset < j2 else ""
                    rows.append((tag, left_no, left, right_no, right))

        self.table = QTableWidget(len(rows), 4, self)
        self.table.setHorizontalHeaderLabels(["#", "Before", "#", "After"])
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.table.setFont(font)
        for row_index, (row_tag, left_no, left, right_no, right) in enumerate(rows):
            values = [left_no, left, right_no, right]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if row_tag != "equal":
                    if column in (0, 1) and left:
                        item.setBackground(QColor("#5a2020"))
                    if column in (2, 3) and right:
                        item.setBackground(QColor("#1f4d2a"))
                self.table.setItem(row_index, column, item)
        self.table.setColumnWidth(0, 45)
        self.table.setColumnWidth(2, 45)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
