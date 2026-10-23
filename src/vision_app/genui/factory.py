from __future__ import annotations

from PySide6.QtWidgets import QWidget

from .cards_widget import CardsWidget
from .chart_widget import ChartWidget
from .diff_widget import DiffWidget
from .table_widget import TableWidget
from .timeline_widget import TimelineWidget


def build_genui_widget(payload: dict, parent=None) -> QWidget:
    kind = str(payload.get("kind") or "")
    if kind == "table":
        return TableWidget(payload, parent)
    if kind == "chart":
        return ChartWidget(payload, parent)
    if kind == "cards":
        return CardsWidget(payload, parent)
    if kind == "timeline":
        return TimelineWidget(payload, parent)
    if kind == "diff":
        return DiffWidget(payload, parent)
    raise ValueError(f"Unsupported generative UI kind: {kind}")
