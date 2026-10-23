from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout


class CardsWidget(QFrame):
    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.payload = payload
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{payload.get('title', 'Cards')}</b>"))
        grid = QGridLayout()
        self.value_labels: dict[str, QLabel] = {}
        for index, card in enumerate(payload.get("cards", [])):
            box = QFrame(self)
            box.setFrameShape(QFrame.Shape.StyledPanel)
            box_layout = QVBoxLayout(box)
            title = QLabel(f"<b>{card.get('title', '')}</b>")
            box_layout.addWidget(title)
            if card.get("value") is not None:
                value = QLabel(f"<span style='font-size:18pt'>{card.get('value')}</span>")
                self.value_labels[str(card.get('title', ''))] = value
                box_layout.addWidget(value)
            if card.get("body"):
                body = QLabel(str(card["body"]))
                body.setWordWrap(True)
                box_layout.addWidget(body)
            if card.get("footer"):
                footer = QLabel(f"<i>{card['footer']}</i>")
                footer.setWordWrap(True)
                box_layout.addWidget(footer)
            grid.addWidget(box, index // 3, index % 3)
        layout.addLayout(grid)

    def update_values(self, values: dict[str, str]) -> None:
        for key, value in values.items():
            if key in self.value_labels:
                self.value_labels[key].setText(f"<span style='font-size:18pt'>{value}</span>")
