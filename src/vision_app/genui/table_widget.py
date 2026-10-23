from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout


class TableWidget(QFrame):
    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.payload = payload
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{payload.get('title', 'Table')}</b>"))
        header.addStretch()
        csv_button = QPushButton("Export CSV")
        png_button = QPushButton("Export PNG")
        csv_button.clicked.connect(self.export_csv)
        png_button.clicked.connect(self.export_png)
        header.addWidget(csv_button)
        header.addWidget(png_button)
        layout.addLayout(header)

        columns = [str(value) for value in payload.get("columns", [])]
        rows = payload.get("rows", [])
        self.table = QTableWidget(len(rows), len(columns), self)
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setSortingEnabled(True)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    item.setData(0, value)
                self.table.setItem(row_index, column_index, item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

    def export_csv(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export table", "vision-table.csv", "CSV (*.csv)")
        if not filename:
            return
        with Path(filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(self.payload.get("columns", []))
            writer.writerows(self.payload.get("rows", []))

    def export_png(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export table image", "vision-table.png", "PNG (*.png)")
        if filename:
            self.grab().save(filename)
