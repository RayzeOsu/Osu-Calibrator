import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush, QPolygonF
from PySide6.QtCore import Qt, QPointF

app = QApplication(sys.argv)

# Create a transparent blank canvas
pixmap = QPixmap(65, 65)
pixmap.fill(Qt.transparent)

painter = QPainter(pixmap)
painter.setRenderHint(QPainter.Antialiasing)

# Your exact keycap colors and math
top_color = QColor("#e4e4e7")
front_color = QColor("#a1a1aa")
side_color = QColor("#71717a")
outline = QPen(QColor("#09090b"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

p1 = QPointF(28, 8)
p2 = QPointF(58, 20)
p3 = QPointF(40, 36)
p4 = QPointF(10, 24)
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
painter.end()

# Save it as an icon file!
pixmap.save("app_icon.ico", "ICO")
print("Successfully generated app_icon.ico!")