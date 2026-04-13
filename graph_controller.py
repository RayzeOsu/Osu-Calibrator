from typing import List
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
import pyqtgraph as pg
from models import PhaseResult

class GraphController:
    def __init__(self, window):
        self.window = window
        self.graph = window.graph
        self.graph_data_points = []
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#e4e4e7", width=1, style=Qt.DashLine))
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#e4e4e7", width=1, style=Qt.DashLine))
        self.tooltip_text = pg.TextItem(anchor=(0, 1), color="#ffffff", fill="#2b2d31", border="#111214")
        self.clear()

    def hide_interaction_items(self):
        self.vLine.hide()
        self.hLine.hide()
        self.tooltip_text.hide()

    def clear(self):
        self.graph.clear()
        self.graph_data_points = []
        self.graph.addItem(self.vLine, ignoreBounds=True)
        self.graph.addItem(self.hLine, ignoreBounds=True)
        self.graph.addItem(self.tooltip_text, ignoreBounds=True)
        self.hide_interaction_items()

    def render_graph(self, results: List[PhaseResult]):
        self.clear()
        colours = ["#5865F2", "#FEE75C", "#57F287"]
        start_x = 1
        all_ints = []
        bound_k1_display = self.window.key1_display_input.text().strip().upper() or "K1"
        bound_k2_display = self.window.key2_display_input.text().strip().upper() or "K2"
        bound_k1_raw = self.window._bound_key1_raw
        bound_k2_raw = self.window._bound_key2_raw

        for idx, r in enumerate(results):
            x = list(range(start_x, start_x + len(r.intervals_ms)))
            all_ints.extend(r.intervals_ms)
            brush = QColor(colours[idx % 3])
            brush.setAlpha(40)
            self.graph.plot(
                x, r.intervals_ms,
                pen=pg.mkPen(colours[idx % 3], width=2),
                symbol="o", symbolSize=7,
                symbolBrush=colours[idx % 3],
                symbolPen=pg.mkPen("#111214", width=1),
                name=r.name, fillLevel=0, fillBrush=brush
            )
            self.graph.addItem(pg.InfiniteLine(pos=r.avg_interval, angle=0, pen=pg.mkPen(colours[idx % 3], width=1.5, style=Qt.DashLine)))
            
            for i in range(len(x)):
                raw_k = r.keys[i] if i < len(r.keys) else "?"
                display_k = bound_k1_display if raw_k == bound_k1_raw else (bound_k2_display if raw_k == bound_k2_raw else raw_k)
                self.graph_data_points.append({"x": x[i], "y": r.intervals_ms[i], "key": display_k, "phase": r.name})
            start_x += len(r.intervals_ms) + 2

        if all_ints:
            self.graph.setYRange(max(0, min(all_ints) - 10), max(all_ints) + 15, padding=0)

    def on_mouse_moved(self, pos):
        if not self.graph_data_points or not self.graph.sceneBoundingRect().contains(pos):
            self.hide_interaction_items()
            return
        mouse_point = self.graph.plotItem.vb.mapSceneToView(pos)
        x_mouse, y_mouse = mouse_point.x(), mouse_point.y()
        closest_point = None
        min_dist = float("inf")
        
        for pt in self.graph_data_points:
            dist = ((pt["x"] - x_mouse) ** 2 + ((pt["y"] - y_mouse) / 8.0) ** 2) ** 0.5
            if dist < min_dist:
                min_dist = dist
                closest_point = pt
                
        if closest_point is None or min_dist > 3.0:
            self.hide_interaction_items()
            return
            
        self.vLine.setPos(closest_point["x"])
        self.hLine.setPos(closest_point["y"])
        self.vLine.show()
        self.hLine.show()
        key_disp = closest_point["key"].upper().strip("<>")
        html = f"<div style='padding:6px;'><b>Phase:</b> {closest_point['phase']}<br><b>Tap:</b> {closest_point['x']}<br><b>Interval:</b> {closest_point['y']:.2f} ms<br><b>Key:</b> {key_disp}</div>"
        self.tooltip_text.setHtml(html)
        self.tooltip_text.setPos(closest_point["x"], closest_point["y"])
        self.tooltip_text.show()