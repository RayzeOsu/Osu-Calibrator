import time
from typing import Optional
from PySide6.QtCore import Qt, QTimer, QSize, QPointF
from PySide6.QtGui import (
    QCursor, QPainter, QColor, QPen, QBrush, QPolygonF
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QFrame, QVBoxLayout, QHBoxLayout,
    QToolButton, QToolTip, QGraphicsDropShadowEffect
)

def apply_shadow(widget: QWidget, blur_radius=20, y_offset=5, alpha=60):
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(blur_radius)
    shadow.setXOffset(0)
    shadow.setYOffset(y_offset)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)

class HelpIconLabel(QLabel):
    def __init__(self, text: str, tooltip: str = "") -> None:
        super().__init__(text)
        self.setObjectName("HelpIcon")
        self.setFixedSize(QSize(24, 24))
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._tooltip_text = tooltip
        self.setToolTip(tooltip)

    def enterEvent(self, event) -> None:
        if self._tooltip_text:
            QToolTip.showText(event.globalPosition().toPoint(), self._tooltip_text, self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

class MetricCard(QFrame):
    def __init__(self, title: str, tooltip: str = "") -> None:
        super().__init__()
        self.setObjectName("MetricCard")
        self.setProperty("status", "neutral")
        apply_shadow(self, blur_radius=15, y_offset=4, alpha=40)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("MetricTitle")
        self.help_icon = HelpIconLabel("ⓘ", tooltip)

        top_row.addWidget(self.title_label)
        top_row.addWidget(self.help_icon)
        top_row.addStretch()

        self.value_label = QLabel("-")
        self.value_label.setObjectName("MetricValue")
        self.value_label.setWordWrap(True)

        self.sub_label = QLabel("")
        self.sub_label.setObjectName("MetricSub")
        self.sub_label.setWordWrap(True)
        self.sub_label.setTextFormat(Qt.RichText)

        layout.addLayout(top_row)
        layout.addWidget(self.value_label)
        layout.addWidget(self.sub_label)
        layout.addStretch()

    def set_data(self, value: str, sub: str = "", status: str = "neutral") -> None:
        self.value_label.setText(value)
        self.sub_label.setText(sub)
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

class CollapsibleSection(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.RightArrow)
        self.toggle_button.clicked.connect(self.on_toggled)

        self.content = QFrame()
        self.content.setObjectName("PanelSub")
        self.content.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

    def on_toggled(self) -> None:
        expanded = self.toggle_button.isChecked()
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.content.setVisible(expanded)

class TiltedKeycapLogo(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(65, 65)
        self.is_pressed = False

    def set_pressed(self, state: bool):
        if self.is_pressed != state:
            self.is_pressed = state
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        top_color = QColor("#5865F2") if self.is_pressed else QColor("#e4e4e7")
        front_color = QColor("#4752C4") if self.is_pressed else QColor("#a1a1aa")
        side_color = QColor("#3C45A5") if self.is_pressed else QColor("#71717a")
        outline = QPen(QColor("#09090b"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

        offset = 6 if self.is_pressed else 0
        p1 = QPointF(28, 8 + offset)
        p2 = QPointF(58, 20 + offset)
        p3 = QPointF(40, 36 + offset)
        p4 = QPointF(10, 24 + offset)
        top_poly = QPolygonF([p1, p2, p3, p4])
        p5 = QPointF(10, 44)
        p6 = QPointF(40, 56)
        front_poly = QPolygonF([p4, p3, p6, p5])
        p7 = QPointF(58, 40)
        side_poly = QPolygonF([p3, p2, p7, p6])

        painter.setPen(outline)
        painter.setBrush(QBrush(front_color))
        painter.drawPolygon(front_poly)
        painter.setBrush(QBrush(side_color))
        painter.drawPolygon(side_poly)
        painter.setBrush(QBrush(top_color))
        painter.drawPolygon(top_poly)

class MetronomeWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(50, 50)
        self.bpm = 0
        self.faded = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.start_time = 0.0

    def set_faded(self, faded: bool):
        if self.faded != faded:
            self.faded = faded
            self.update()

    def start(self, bpm: Optional[int]):
        if bpm and bpm > 0:
            self.bpm = bpm
            self.start_time = time.perf_counter()
            self.timer.start(16)
        else:
            self.stop()

    def stop(self):
        self.timer.stop()
        self.bpm = 0
        self.faded = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.bpm == 0:
            painter.setBrush(QColor("#313338"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(25, 25), 10.0, 10.0)
            return

        beat_duration = 60.0 / self.bpm
        elapsed = time.perf_counter() - self.start_time
        phase = (elapsed % beat_duration) / beat_duration

        intensity = max(0.0, 1.0 - (phase * 4.0))
        size = 20.0 + (20.0 * intensity)

        glow_color = QColor("#5865F2")
        base_alpha = int(60 + 195 * intensity)
        if self.faded:
            base_alpha = int(base_alpha * 0.25)
        glow_color.setAlpha(base_alpha)

        painter.setBrush(glow_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(25, 25), size / 2.0, size / 2.0)