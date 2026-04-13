import time
from typing import List, Optional
from pynput import keyboard
from PySide6.QtCore import QObject, Signal, QTimer

from engine import AnalysisEngine

class KeyListenerManager(QObject):
    key_state_changed = Signal(bool)
    keys_detected = Signal(str, str, str, str)
    phase_press = Signal(str, float)
    phase_release = Signal(str, float)
    bind_cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.bg_listener: Optional[keyboard.Listener] = None
        self.phase_listener: Optional[keyboard.Listener] = None
        self.key_detect_mode = False
        self.detected_keys_display_temp: List[str] = []
        self.detected_keys_raw_temp: List[str] = []
        self.tracked_keys: set = set()
        self.bg_held_keys: set = set()
        self.app_has_focus = True

        self.bind_timer = QTimer(self)
        self.bind_timer.setSingleShot(True)
        self.bind_timer.timeout.connect(self.timeout_bind_mode)

    def start_background(self):
        if self.bg_listener is None:
            self.bg_listener = keyboard.Listener(
                on_press=self._bg_press,
                on_release=self._bg_release,
            )
            self.bg_listener.start()

    def stop_background(self):
        if self.bg_listener:
            try:
                self.bg_listener.stop()
            except Exception:
                pass
            self.bg_listener = None

    def start_phase(self, k1_raw: str, k2_raw: str):
        self.tracked_keys = {k1_raw, k2_raw}
        self.phase_listener = keyboard.Listener(
            on_press=self._phase_press,
            on_release=self._phase_release,
        )
        self.phase_listener.start()

    def stop_phase(self):
        if self.phase_listener:
            try:
                self.phase_listener.stop()
            except Exception:
                pass
            self.phase_listener = None

    def begin_key_detect(self):
        self.key_detect_mode = True
        self.detected_keys_display_temp = []
        self.detected_keys_raw_temp = []
        self.bind_timer.start(5000)

    def cancel_key_detect(self):
        if self.key_detect_mode:
            self.key_detect_mode = False
            self.bind_timer.stop()
            self.detected_keys_display_temp = []
            self.detected_keys_raw_temp = []
            self.bind_cancelled.emit()

    def timeout_bind_mode(self):
        if self.key_detect_mode:
            self.key_detect_mode = False
            self.bind_cancelled.emit()

    def set_focus(self, has_focus: bool):
        self.app_has_focus = has_focus

    def _bg_press(self, key):
        if not self.app_has_focus and not self.key_detect_mode:
            return

        k = AnalysisEngine.extract_key_name(key)
        if not k:
            return

        if self.key_detect_mode:
            if k == "<esc>":
                self.key_detect_mode = False
                self.bind_timer.stop()
                self.bind_cancelled.emit()
                return

            raw_key_name = k
            display_key_name = k

            if raw_key_name.startswith("<") and raw_key_name.endswith(">"):
                display_key_name = raw_key_name.strip("<>").capitalize()
            elif len(raw_key_name) == 1:
                display_key_name = raw_key_name.upper()

            if raw_key_name not in self.detected_keys_raw_temp:
                self.detected_keys_raw_temp.append(raw_key_name)
                self.detected_keys_display_temp.append(display_key_name)

            if len(self.detected_keys_raw_temp) >= 2:
                self.key_detect_mode = False
                self.bind_timer.stop()
                self.keys_detected.emit(
                    self.detected_keys_display_temp[0], self.detected_keys_raw_temp[0],
                    self.detected_keys_display_temp[1], self.detected_keys_raw_temp[1]
                )
            return

        if k in self.tracked_keys:
            self.bg_held_keys.add(k)
            self.key_state_changed.emit(True)

    def _bg_release(self, key):
        k = AnalysisEngine.extract_key_name(key)
        if k and k in self.tracked_keys:
            self.bg_held_keys.discard(k)
            if not self.bg_held_keys:
                self.key_state_changed.emit(False)

    def _phase_press(self, key):
        k = AnalysisEngine.extract_key_name(key)
        if k and k in self.tracked_keys:
            self.phase_press.emit(k, time.perf_counter())

    def _phase_release(self, key):
        k = AnalysisEngine.extract_key_name(key)
        if k and k in self.tracked_keys:
            self.phase_release.emit(k, time.perf_counter())