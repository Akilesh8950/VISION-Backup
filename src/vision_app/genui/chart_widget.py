from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

try:
    from PySide6.QtCharts import (
        QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QLineSeries,
        QPieSeries, QScatterSeries, QValueAxis,
    )
    HAS_QTCHARTS = True
except Exception:  # pragma: no cover
    HAS_QTCHARTS = False


class ChartWidget(QFrame):
    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.payload = payload
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(320)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{payload.get('title', 'Chart')}</b>"))
        header.addStretch()
        export = QPushButton("Export PNG")
        export.clicked.connect(self.export_png)
        header.addWidget(export)
        layout.addLayout(header)
        if HAS_QTCHARTS:
            self.view = QChartView(self._make_chart(payload), self)
            self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
            layout.addWidget(self.view)
        else:  # pragma: no cover - fallback used only when Addons is absent
            summary = ", ".join(f"{label}: {value}" for label, value in zip(payload.get("labels", []), payload.get("values", [])))
            label = QLabel(summary)
            label.setWordWrap(True)
            layout.addWidget(label)
            self.view = label

    @staticmethod
    def _make_chart(payload: dict):
        labels = [str(value) for value in payload.get("labels", [])]
        values = [float(value) for value in payload.get("values", [])]
        chart_type = str(payload.get("chart_type", "bar"))
        chart = QChart()
        chart.setTitle(str(payload.get("title", "Chart")))
        chart.legend().setVisible(chart_type == "pie")
        if chart_type == "bar":
            bar_set = QBarSet("Values")
            bar_set.append(values)
            series = QBarSeries()
            series.append(bar_set)
            chart.addSeries(series)
            axis_x = QBarCategoryAxis()
            axis_x.append(labels)
            axis_y = QValueAxis()
            chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
            chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
            series.attachAxis(axis_x)
            series.attachAxis(axis_y)
        elif chart_type == "pie":
            series = QPieSeries()
            for label, value in zip(labels, values):
                series.append(label, value)
            chart.addSeries(series)
        else:
            series = QScatterSeries() if chart_type == "scatter" else QLineSeries()
            series.setName("Values")
            for index, (label, value) in enumerate(zip(labels, values)):
                try:
                    x = float(label)
                except ValueError:
                    x = float(index)
                series.append(QPointF(x, value))
            chart.addSeries(series)
            chart.createDefaultAxes()
        chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        return chart

    def export_png(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export chart", "vision-chart.png", "PNG (*.png)")
        if filename:
            self.view.grab().save(filename)
