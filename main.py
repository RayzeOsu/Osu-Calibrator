import sys
import time
import threading
import statistics
from typing import Dict, List

from PySide6.QtCore import QTimer, Qt, QEvent
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
import pyqtgraph as pg

from config import ensure_persistent_songs, INTERVAL_TO_TIMING_STDEV, RECOMMENDATION_THRESHOLDS
from models import PhaseConfig, PhaseResult, CalibrationSession
from history import HistoryStore
from engine import AnalysisEngine, RecommendationEngine
from listener import KeyListenerManager
from ui_layout import build_main_ui, apply_app_styles
from audio_manager import AudioManager
from graph_controller import GraphController

STATE_IDLE = "idle"
STATE_PHASE_READY = "ready"
STATE_COMPLETE = "complete"

class TapAnalyzerApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Osu! Calibrator by Rayze")
        self.resize(1550, 1050)

        ensure_persistent_songs()

        self.history = HistoryStore()
        self.listener_mgr = KeyListenerManager()
        self.listener_mgr.key_state_changed.connect(self.update_logo_state)
        self.listener_mgr.keys_detected.connect(self.apply_detected_keys)
        self.listener_mgr.phase_press.connect(self.on_phase_press)
        self.listener_mgr.phase_release.connect(self.on_phase_release)
        self.listener_mgr.bind_cancelled.connect(self.restore_key_detect_ui)

        self._bound_key1_raw: str = ""
        self._bound_key2_raw: str = ""
        self._bound_key1_display: str = ""
        self._bound_key2_display: str = ""

        self.phase_configs = [
            PhaseConfig("Comfort Phase", "Tap at your natural, comfortable stream speed with the music.", 8, 0.35),
            PhaseConfig("Push Phase", "Tap your max speed — the music is just background, don't try to match it.", 6, 0.35),
            PhaseConfig("Stability Phase", "Tap cleanly again at comfort speed to check control under fatigue.", 8, 0.30),
        ]

        self.button_state: str = STATE_IDLE
        self.test_running = False
        self.waiting_for_first_tap = False
        self.start_time = 0.0
        self.end_time = 0.0
        self.current_phase_index = 0
        self.phase_results: List[PhaseResult] = []
        self.in_zen_mode = False
        self.zen_events = []
        self.lock = threading.Lock()
        self.events: List[Dict] = []
        self.held_keys = set()
        self.cached_press_count = 0
        self.last_status_update = 0.0

        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update_countdown)

        build_main_ui(self)
        apply_app_styles(self)
        
        self.audio_mgr = AudioManager(self)
        self.audio_output = self.audio_mgr.audio_output
        self.graph_ctrl = GraphController(self)
        
        self.install_shortcuts()
        self.audio_mgr.refresh_song_dropdown()
        self.evaluate_history()
        self.update_phase_ui()
        self.listener_mgr.start_background()
        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.WindowActivate:
            self.listener_mgr.set_focus(True)
        elif event.type() == QEvent.WindowDeactivate:
            self.listener_mgr.set_focus(False)
        return super().eventFilter(obj, event)

    def install_shortcuts(self):
        self.space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.space_shortcut.activated.connect(self.toggle_phase)

    def toggle_phase(self):
        if self.listener_mgr.key_detect_mode: return
        if self.zen_button.isChecked():
            self.zen_button.setChecked(False)
            self.on_zen_toggled(False)
            return
        if self.test_running and self.stop_button.isEnabled(): self.stop_button.click()
        elif self.start_button.isEnabled(): self.start_button.click()

    def _on_start_button_clicked(self):
        if self.button_state == STATE_COMPLETE: self.reset_calibration()
        else: self.start_phase()

    def _set_button_state(self, state: str):
        self.button_state = state
        if state in (STATE_IDLE, STATE_PHASE_READY): self.start_button.setText("Start Phase  (Space)")
        elif state == STATE_COMPLETE: self.start_button.setText("Reset for New Calibration  (Space)")
        self.start_button.setEnabled(True)

    def get_float(self, line_edit, default: float) -> float:
        text = line_edit.text().strip()
        if not text: return default
        try: return float(text.replace(",", "."))
        except ValueError: return default

    def get_current_settings(self) -> Dict[str, float]:
        press_val = self.get_float(self.press_activate_input, 0.0)
        release_val = self.get_float(self.release_deactivate_input, 0.0) if self.separate_sensitivity_checkbox.isChecked() else press_val
        return {
            "base": self.get_float(self.base_actuation_input, 0.0),
            "press": press_val,
            "release": release_val,
            "force": self.get_float(self.bottom_out_force_input, 0.0),
        }

    def format_setting_value(self, val: float) -> str:
        return f"{val:.2f}" if val != 0.0 else ""

    def toggle_separate_sensitivity(self, checked: bool):
        self.release_label_container.setVisible(checked)
        self.release_deactivate_input.setVisible(checked)
        self.press_main_label.setText("Press Activate (mm)" if checked else "Rapid Trigger (mm)")

    def import_custom_song(self):
        self.audio_mgr.import_custom_song()

    def setup_graph_interaction_items(self):
        pass

    def on_mouse_moved(self, pos):
        if hasattr(self, 'graph_ctrl'):
            self.graph_ctrl.on_mouse_moved(pos)

    def update_logo_state(self, is_pressed: bool):
        self.logo.set_pressed(is_pressed)

    def start_key_detect(self):
        self.listener_mgr.begin_key_detect()
        self.key1_display_input.clear()
        self.key2_display_input.clear()
        self.key1_display_input.setPlaceholderText("...")
        self.key2_display_input.setPlaceholderText("...")
        self.detect_btn.setText("Press 2 keys (Esc to cancel)")
        self.detect_btn.setStyleSheet("background-color: #FEE75C; border-color: #FEE75C; color: #09090b;")
        self.detect_cancel_btn.setVisible(True)

    def cancel_key_detect_from_ui(self):
        self.listener_mgr.cancel_key_detect()

    def apply_detected_keys(self, display_k1: str, raw_k1: str, display_k2: str, raw_k2: str):
        self.listener_mgr.bg_held_keys.clear()
        self.listener_mgr.key_state_changed.emit(False)
        self.key1_display_input.setText(display_k1)
        self.key2_display_input.setText(display_k2)
        self.key1_display_input.setPlaceholderText("eg Z")
        self.key2_display_input.setPlaceholderText("eg X")
        self._bound_key1_raw = raw_k1
        self._bound_key2_raw = raw_k2
        self._bound_key1_display = display_k1
        self._bound_key2_display = display_k2
        self.detect_btn.setText("Record Keys")
        self.detect_btn.setStyleSheet("")
        self.detect_cancel_btn.setVisible(False)
        self.listener_mgr.tracked_keys = {raw_k1, raw_k2}

    def restore_key_detect_ui(self):
        self.key1_display_input.setText(self._bound_key1_display)
        self.key2_display_input.setText(self._bound_key2_display)
        self.key1_display_input.setPlaceholderText("eg Z")
        self.key2_display_input.setPlaceholderText("eg X")
        self.detect_btn.setText("Record Keys")
        self.detect_btn.setStyleSheet("")
        self.detect_cancel_btn.setVisible(False)

    def reset_key_detect_ui(self):
        self.listener_mgr.key_detect_mode = False
        self.listener_mgr.detected_keys_raw_temp.clear()
        self.listener_mgr.detected_keys_display_temp.clear()
        self.listener_mgr.bind_timer.stop()
        self.restore_key_detect_ui()

    def on_phase_press(self, key: str, t: float):
        if self.in_zen_mode:
            self.zen_events.append({"time": t, "type": "press", "key": key})
            return
        if not self.test_running: return
        with self.lock:
            if key not in self.held_keys:
                self.held_keys.add(key)
                self.events.append({"time": t, "type": "press", "key": key})
                self.cached_press_count += 1

    def on_phase_release(self, key: str, t: float):
        if self.in_zen_mode:
            self.zen_events.append({"time": t, "type": "release", "key": key})
            return
        if not self.test_running: return
        with self.lock:
            self.held_keys.discard(key)
            self.events.append({"time": t, "type": "release", "key": key})

    def export_to_clipboard(self):
        QApplication.clipboard().setText(self.analysis_box.toPlainText())
        self.export_button.setText("Copied!")
        QTimer.singleShot(2000, lambda: self.export_button.setText("Copy Report"))

    def confirm_clear_history(self):
        if not self.history.sessions:
            QMessageBox.information(self, "No History", "There is no calibration history to clear.")
            return
        if QMessageBox.question(self, "Clear History", f"Delete all {len(self.history.sessions)} saved calibration sessions? This cannot be undone.", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.history.clear()
            self.evaluate_history()
            QMessageBox.information(self, "History Cleared", "Calibration history has been deleted.")

    def clear_summary_cards(self):
        self.summary_bpm_card.set_data("-", "Run a calibration to populate this.", "neutral")
        self.summary_quality_card.set_data("-", "Waiting for session data.", "neutral")
        self.summary_confidence_card.set_data("-", "Waiting for session data.", "neutral")
        self.summary_recommendation_card.set_data("-", "No recommendation yet.", "neutral")
        self.summary_press_card.set_data("-", "N/A", "neutral")
        self.summary_release_card.set_data("-", "N/A", "neutral")
        self.summary_base_card.set_data("-", "N/A", "neutral")
        self.summary_phase_note_card.set_data("-", "Complete all phases for analysis.", "neutral")
        self.summary_tip_card.set_data("-", "Tips will appear after your calibration.", "neutral")

    def update_phase_ui(self):
        cfg = self.phase_configs[self.current_phase_index]
        self.phase_label.setText(cfg.name)
        self.phase_description_label.setText(cfg.description)
        self.phase_progress_label.setText(f"Phase {self.current_phase_index + 1} of {len(self.phase_configs)}")

    def cancel_phase(self):
        if not self.test_running: return
        self.test_running = False
        self.countdown_timer.stop()
        self.listener_mgr.stop_phase()
        self.audio_mgr.stop()
        self.current_phase_index = 0
        self.phase_results = []
        with self.lock:
            self.events = []
            self.held_keys = set()
            self.cached_press_count = 0
        self.status_label.setText("Phase Cancelled — restart from Phase 1")
        self.status_label.setStyleSheet("color: #FEE75C; border-color: #FEE75C;")
        self.update_phase_ui()
        self._set_button_state(STATE_IDLE)
        self.stop_button.setEnabled(False)

    def reset_calibration(self):
        if self.zen_button.isChecked():
            self.zen_button.setChecked(False)
            self.on_zen_toggled(False)
        if self.test_running: self.cancel_phase()
        self.listener_mgr.bg_held_keys.clear()
        self.listener_mgr.key_state_changed.emit(False)
        self.audio_mgr.stop()
        self.song_combo.setEnabled(True)
        self.import_song_button.setEnabled(True)
        self.base_actuation_input.setEnabled(True)
        self.key1_display_input.setEnabled(True)
        self.key2_display_input.setEnabled(True)
        self.detect_btn.setEnabled(True)
        self.bottom_out_force_input.setEnabled(True)
        self.separate_sensitivity_checkbox.setEnabled(True)
        self.press_activate_input.setEnabled(True)
        self.release_deactivate_input.setEnabled(True)
        self.current_phase_index = 0
        self.phase_results = []
        with self.lock:
            self.events = []
            self.held_keys = set()
            self.cached_press_count = 0
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet("")
        self.countdown_label.setText("Time left: -")
        self.tap_count_label.setText("Detected presses: 0")
        self._set_button_state(STATE_IDLE)
        self.stop_button.setEnabled(False)
        self.update_phase_ui()
        self.clear_summary_cards()
        self.evaluate_history()
        self.analysis_box.clear()
        self.graph_ctrl.clear()
        self.change_log_label.setText("Complete two runs to see comparison reasoning.")
        self.reset_key_detect_ui()

    def on_zen_toggled(self, checked: bool):
        if checked:
            if not self._bound_key1_raw or not self._bound_key2_raw:
                self.zen_button.setChecked(False)
                QMessageBox.warning(self, "Missing Keys", "Bind your stream keys first.")
                return
            self.listener_mgr.bg_held_keys.clear()
            self.listener_mgr.key_state_changed.emit(False)
            self.in_zen_mode = True
            self.zen_events = []
            self.start_button.setEnabled(False)
            self.reset_button.setEnabled(False)
            selected_file = self.audio_mgr.get_selected_song_file()
            if selected_file:
                self.audio_mgr.play(selected_file)
                if self.audio_mgr.current_target_bpm:
                    self.metronome_widget.start(self.audio_mgr.current_target_bpm)
            self.status_label.setText("Zen Mode: Warming up... (Tap to start)")
            self.status_label.setStyleSheet("color: #5865F2; border-color: #5865F2;")
            self.listener_mgr.start_phase(self._bound_key1_raw, self._bound_key2_raw)
            self.countdown_timer.start(50)
        else:
            self.in_zen_mode = False
            self.listener_mgr.stop_phase()
            self.audio_mgr.stop()
            self.countdown_timer.stop()
            self.start_button.setEnabled(True)
            self.reset_button.setEnabled(True)
            self.status_label.setText("Ready")
            self.status_label.setStyleSheet("")
            self.countdown_label.setText("Time left: -")
            self.tap_count_label.setText("Detected presses: 0")

    def start_phase(self):
        if self.listener_mgr.key_detect_mode:
            QMessageBox.information(self, "Key Binding In Progress", "Please finish binding your keys or cancel before starting a phase.")
            return
        if self.test_running: return
        
        base_val = self.base_actuation_input.text().strip()
        if not base_val:
            QMessageBox.warning(self, "Missing Settings", "You must enter your current Base Actuation before testing.")
            self.base_actuation_input.setFocus()
            return
        if not self._bound_key1_raw or not self._bound_key2_raw:
            QMessageBox.warning(self, "Missing Keys", "You must bind both stream keys. Use the 'Record Keys' button.")
            return
        if self._bound_key1_raw == self._bound_key2_raw:
            QMessageBox.warning(self, "Invalid Keys", "Key 1 and Key 2 must be different physical keys.")
            return

        sep_on = self.separate_sensitivity_checkbox.isChecked()
        press_val_str = self.press_activate_input.text().strip()
        if sep_on:
            if not press_val_str or not self.release_deactivate_input.text().strip():
                QMessageBox.warning(self, "Missing Settings", "Separate Sensitivity is enabled. Fill out Press/Release settings, or disable the option.")
                return
        elif not press_val_str:
            QMessageBox.warning(self, "Missing Settings", "Please fill out your Rapid Trigger setting.")
            return

        settings = self.get_current_settings()
        warnings = RecommendationEngine.validate_settings(settings["base"], settings["press"], settings["release"], settings["force"], sep_on)
        if warnings:
            msg = "Configuration warnings:\n\n" + "\n\n".join(f"• {w}" for w in warnings) + "\n\nProceed anyway?"
            if QMessageBox.question(self, "Settings Sanity Check", msg, QMessageBox.Yes | QMessageBox.No) == QMessageBox.No: return

        prev = self.history.latest()
        if prev and self.current_phase_index == 0 and not self.phase_results and settings != prev.settings:
            if QMessageBox.question(self, "Settings Changed", "Configuration changed since last test. Treat these new settings as the 'Baseline' for this run?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
                self.base_actuation_input.setText(self.format_setting_value(prev.settings.get("base", 0.0)))
                self.press_activate_input.setText(self.format_setting_value(prev.settings.get("press", 0.0)))
                self.release_deactivate_input.setText(self.format_setting_value(prev.settings.get("release", 0.0)))
                self.bottom_out_force_input.setText(self.format_setting_value(prev.settings.get("force", 0.0)))
                return

        with self.lock:
            self.events = []
            self.held_keys = set()
            self.cached_press_count = 0

        cfg = self.phase_configs[self.current_phase_index]
        self.test_running = True
        self.waiting_for_first_tap = True

        if self.current_phase_index == 0:
            self.song_combo.setEnabled(False)
            self.import_song_button.setEnabled(False)
            self.base_actuation_input.setEnabled(False)
            self.key1_display_input.setEnabled(False)
            self.key2_display_input.setEnabled(False)
            self.detect_btn.setEnabled(False)
            self.bottom_out_force_input.setEnabled(False)
            self.separate_sensitivity_checkbox.setEnabled(False)
            self.press_activate_input.setEnabled(False)
            self.release_deactivate_input.setEnabled(False)

        selected_file = self.audio_mgr.get_selected_song_file()
        if selected_file:
            if self.current_phase_index == 0 and not self.audio_mgr.song_is_paused:
                self.audio_mgr.play(selected_file)
            elif self.audio_mgr.song_is_paused:
                self.audio_mgr.resume()

            if self.audio_mgr.current_target_bpm:
                self.metronome_widget.start(self.audio_mgr.current_target_bpm)
                self.metronome_widget.set_faded(self.current_phase_index == 1)
            else:
                self.metronome_widget.stop()
        else:
            self.audio_mgr.stop()

        self.last_status_update = time.perf_counter()
        self.status_label.setText("Waiting for first tap...")
        self.status_label.setStyleSheet("color: #FEE75C; border-color: #FEE75C;")
        self.countdown_label.setText(f"Time left: {cfg.duration}s")
        self.tap_count_label.setText("Detected presses: 0")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.listener_mgr.start_phase(self._bound_key1_raw, self._bound_key2_raw)
        self.countdown_timer.start(50)

    def stop_phase(self):
        if not self.test_running: return
        self.test_running = False
        self.countdown_timer.stop()
        self.listener_mgr.stop_phase()
        self.audio_mgr.pause()
        if hasattr(self, "metronome_widget"): self.metronome_widget.stop()
        
        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet("color: #949ba4; border-color: #313338;")

        cfg = self.phase_configs[self.current_phase_index]
        with self.lock: evs = list(self.events)
        result, error = AnalysisEngine.analyse_phase(evs, cfg, self._bound_key1_raw, self._bound_key2_raw, self.audio_mgr.current_target_bpm)

        if not result:
            self.status_label.setText("Phase failed - retry")
            self.status_label.setStyleSheet("color: #FEE75C; border-color: #FEE75C;")
            self._set_button_state(STATE_PHASE_READY)
            self.stop_button.setEnabled(False)
            self.countdown_label.setText("Time left: 0.0s")
            QMessageBox.warning(self, "Not Enough Usable Data", f"Phase retry needed:\n\n{error}\n\nPlease retry the same phase.")
            return

        self.phase_results.append(result)

        if self.current_phase_index == 0 and result.quality_score < 55:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Messy Comfort Phase")
            msg.setText("This phase didn't produce reliable data. This can happen if you're not warmed up yet, if your hands are cold, OR if your current settings are fighting you.\n\nYou can stop here and adjust your settings, or push through to the end.")
            if msg.exec() == msg.addButton("Stop & Fix Settings", QMessageBox.ActionRole):
                self.reset_calibration()
                return
        elif result.quality_score < 40:
            QMessageBox.warning(self, "Poor Test Data", f"{result.name} was very messy ({result.quality_label}).\nAnomalies: Release Noise={result.anomaly_counts['release_noise']}, Fast Repeats={result.anomaly_counts['same_key_fast_repeats']}.\nThe final recommendation may be unreliable.")

        if result.max_gap_seconds > RECOMMENDATION_THRESHOLDS["max_phase_gap_seconds"]:
            QMessageBox.warning(self, "Pause Detected", f"A pause of {result.max_gap_seconds:.1f}s was detected during {result.name}. This may skew the analysis.")

        if self.current_phase_index < len(self.phase_configs) - 1:
            self.current_phase_index += 1
            self.update_phase_ui()
            self._set_button_state(STATE_PHASE_READY)
            self.stop_button.setEnabled(False)
            self.status_label.setText("Ready for next phase")
            self.status_label.setStyleSheet("")
        else:
            self.status_label.setText("Calibration Complete")
            self.status_label.setStyleSheet("color: #57F287; border-color: #57F287;")
            self._set_button_state(STATE_COMPLETE)
            self.stop_button.setEnabled(False)
            self.render_final_results()

        self.countdown_label.setText("Time left: 0.0s")

    def update_countdown(self):
        if self.in_zen_mode:
            now = time.perf_counter()
            self.zen_events = [e for e in self.zen_events if now - e["time"] <= 30.0]
            presses = [e for e in self.zen_events if e["type"] == "press"]
            self.tap_count_label.setText(f"Taps in last 30s: {len(presses)}")
            if len(presses) >= 10:
                ints = [(presses[i]["time"] - presses[i - 1]["time"]) * 1000 for i in range(1, len(presses))]
                avg_int = statistics.mean(ints)
                ur = (statistics.stdev(ints) / INTERVAL_TO_TIMING_STDEV) * 10.0 if len(ints) > 1 else 0.0
                bpm = 15000 / avg_int if avg_int > 0 else 0
                if len(presses) > 40 and ur < 120:
                    self.status_label.setText(f"Zen Mode: {bpm:.0f} BPM | {ur:.0f} UR — Looking ready! 🟢")
                    self.status_label.setStyleSheet("color: #57F287; border-color: #57F287;")
                else:
                    self.status_label.setText(f"Zen Mode: {bpm:.0f} BPM | {ur:.0f} UR — Warming up...")
                    self.status_label.setStyleSheet("color: #5865F2; border-color: #5865F2;")
            return

        if not self.test_running: return
        with self.lock:
            press_count = self.cached_press_count
            if self.waiting_for_first_tap:
                if press_count > 0:
                    self.waiting_for_first_tap = False
                    self.start_time = self.events[0]["time"]
                    self.end_time = self.start_time + self.phase_configs[self.current_phase_index].duration
                    self.status_label.setText("Recording Live")
                    self.status_label.setStyleSheet("color: #ED4245; border-color: #ED4245;")
                else: return

        rem = max(0.0, self.end_time - time.perf_counter())
        self.countdown_label.setText(f"Time left: {rem:.1f}s")
        self.tap_count_label.setText(f"Detected presses: {press_count}")

        now = time.perf_counter()
        if press_count > 8 and not self.waiting_for_first_tap and (now - self.last_status_update > 0.25):
            self.last_status_update = now
            with self.lock:
                recent_presses = [e["time"] for e in self.events if e["type"] == "press"][-12:]
                if len(recent_presses) >= 4:
                    ints = [(recent_presses[i] - recent_presses[i - 1]) * 1000 for i in range(1, len(recent_presses))]
                    avg_int = statistics.mean(ints)
                    ur = (statistics.stdev(ints) / INTERVAL_TO_TIMING_STDEV) * 10.0 if len(ints) > 1 else 0.0
                    bpm = 15000 / avg_int if avg_int > 0 else 0
                    self.status_label.setText(f"Recording Live ({bpm:.0f} BPM | {ur:.0f} UR)")

        if rem <= 0: self.stop_phase()

    def render_final_results(self):
        if not self.phase_results:
            self.analysis_box.setPlainText("No usable phase data was captured.")
            return

        settings = self.get_current_settings()
        sep_on = self.separate_sensitivity_checkbox.isChecked()
        prev_summary = self.history.latest().summary if self.history.latest() else None

        summary = RecommendationEngine.build_summary(self.phase_results, self.phase_configs, settings["base"], settings["press"], settings["release"], settings["force"], sep_on, prev_summary)

        if summary.get("weighted_quality", 100) < 55 and len(self.history.sessions) >= 2 and all(s.summary.get("weighted_quality", 100) < 55 for s in self.history.sessions[-2:]):
            safe_base = settings["base"] if 0.6 <= settings["base"] <= 2.0 else 1.0
            safe_each = round(safe_base * 0.35, 2)
            summary.update({
                "recommendation_text": "Switch to a safe baseline preset",
                "recommendation_reason": "We've seen a few messy runs in a row — micro-adjustments aren't helping right now.",
                "recommendation_status": "coach",
                "base_suggestion": f"Try {safe_base:.2f} mm",
                "base_reason": "A forgiving depth that should 'just work' while you build consistency.",
                "press_suggestion": f"Try {safe_each:.2f} mm" if sep_on else summary.get("press_suggestion"),
                "press_reason": "Safe RT distance — fits comfortably inside the base depth." if sep_on else summary.get("press_reason"),
                "release_suggestion": f"Try {safe_each:.2f} mm" if sep_on else summary.get("release_suggestion"),
                "release_reason": "Safe RT distance — fits comfortably inside the base depth." if sep_on else summary.get("release_reason"),
                "plain_english": f"After a few messy runs, here's the deal: rather than chasing tiny tweaks, we recommend a safe 'training wheels' preset (Base {safe_base:.2f} mm, RT {safe_each:.2f} mm each side). These settings should just work while you focus on building tapping consistency.",
                "technique_tip": "Build a daily warm-up routine: 5 minutes of slow alternation drills before any serious tapping."
            })

        self.history.append(CalibrationSession(settings=settings, summary=summary.copy()))
        self.render_summary_cards(summary)
        self.graph_ctrl.render_graph(self.phase_results)
        self.render_detailed_text(self.phase_results, summary)
        self.evaluate_history()
        self.change_log_label.setText(summary.get("change_log_text", ""))

    def evaluate_history(self):
        if len(self.history.sessions) < 2:
            self.summary_coaching_card.set_data("Waiting for next run", "Run another calibration with changes so we can compare progress.", "info")
            return

        curr = self.history.latest().summary
        prev = self.history.previous().summary
        ur_delta = curr.get("weighted_ur", 0.0) - prev.get("weighted_ur", 0.0)
        qual_delta = curr.get("weighted_quality", 0.0) - prev.get("weighted_quality", 0.0)
        
        last_three = [s.summary.get("weighted_ur", 0.0) for s in self.history.sessions[-3:]]
        spread = max(last_three) - min(last_three) if len(last_three) == 3 else 0

        if self.history.latest().settings == self.history.previous().settings and abs(ur_delta) >= 40:
            self.summary_coaching_card.set_data("Your tapping is volatile today", f"Your timing swung by {abs(ur_delta):.0f} UR between runs even though your settings didn't change.", "warn")
            return

        if len(last_three) == 3 and spread >= 60:
            self.summary_coaching_card.set_data("Runs are all over the place", f"Your last 3 runs varied by {spread:.0f} UR. Take a break and come back when your fingers feel locked in.", "warn")
            return

        if curr.get("weighted_ur", 0.0) < 100 and prev.get("weighted_ur", 0.0) < 110 and abs(ur_delta) < 8 and qual_delta > -5:
            self.summary_coaching_card.set_data("Optimal range reached!", "Your timing is excellent and highly stable. These settings are perfect for practice.", "good")
        elif ur_delta < -15.0:
            self.summary_coaching_card.set_data("Big improvement!", "Your timing is noticeably steadier than last time. Keep these settings.", "good")
        elif ur_delta < -5.0:
            self.summary_coaching_card.set_data("Getting steadier", "Your timing improved compared to your last run." + (" The run was also cleaner." if qual_delta > 5 else ""), "good")
        elif ur_delta > 15.0:
            self.summary_coaching_card.set_data("Timing worsened", "Your timing was noticeably less stable. Consider reverting to your previous settings." + (" The run was also messier." if qual_delta < -10 else ""), "bad")
        elif ur_delta > 5.0:
            self.summary_coaching_card.set_data("Tiny step back", "Your timing was slightly less stable than last time. This could be fatigue or over-sensitive settings.", "warn")
        else:
            self.summary_coaching_card.set_data("Consistent progress", "Your timing is about as steady as your previous run." + (" The run was cleaner, though." if qual_delta > 8 else (" The run was slightly messier, though." if qual_delta < -8 else "")), "info")

    def render_summary_cards(self, summary: Dict):
        ur_val = summary["weighted_ur"]
        ur_meaning = "Elite steadiness — taps are perfectly timed." if ur_val < 95 else ("Controlled steadiness — good timing." if ur_val < 135 else ("Decent timing — room to dial in." if ur_val < 170 else ("Uneven timing — difficult to judge settings." if ur_val < 220 else "Very uneven timing — test results may be unreliable.")))
        bpm_sub = f"Around {summary['weighted_bpm_1_4']:.0f} BPM. {ur_meaning}"
        accs = [r.bpm_accuracy for r in self.phase_results if r.bpm_accuracy is not None]
        if accs: bpm_sub += f"<br>BPM Match: {(sum(accs) / len(accs)):.1f}%"
        
        self.summary_bpm_card.set_data(f"{ur_val:.1f} UR", bpm_sub, "info")
        self.summary_quality_card.set_data(summary["session_quality"], "Clean output, settings are dialled in." if summary["weighted_quality"] >= 80 else ("Controlled output, reliable advice possible." if summary["weighted_quality"] >= 55 else "Misfires detected — data is messy."), "good" if summary["session_quality"] == "Good" else ("warn" if summary["session_quality"] == "Usable" else "bad"))
        self.summary_confidence_card.set_data(summary["confidence"], "Judgment is based on your mechanical consistency.", "good" if summary["confidence"] == "High" else ("warn" if summary["confidence"] == "Medium" else "bad"))
        self.summary_recommendation_card.set_data(summary["recommendation_text"], summary["recommendation_reason"], summary["recommendation_status"])
        self.summary_press_card.set_data(summary["press_suggestion"], summary.get("press_reason", ""), summary["recommendation_status"])
        self.summary_release_card.set_data(summary["release_suggestion"], summary.get("release_reason", ""), summary["recommendation_status"])
        self.summary_base_card.set_data(summary["base_suggestion"], summary.get("base_reason", ""), summary["recommendation_status"])

        meta = []
        if abs(summary["weighted_drift"]) >= 4: meta.append(f"You were {'slowing down' if summary['weighted_drift'] > 0 else 'speeding up'} as the test went on")
        if summary["gallop_bias"] >= 12: meta.append(f"{summary.get('slow_key', 'One finger').upper().strip('<>')} is consistently lagging behind")
        if summary["overtrigger_score"] >= 12: meta.append("Hardware misfires detected (settings too sensitive)")
        self.summary_phase_note_card.set_data(summary["plain_english"], " • ".join(meta) if meta else "No mechanical issues spotted in the details.", "info")
        self.summary_tip_card.set_data("💡 Try this", summary["technique_tip"], "coach") if summary.get("technique_tip") else self.summary_tip_card.set_data("-", "No specific tip for this run.", "neutral")

    def render_detailed_text(self, results: List[PhaseResult], summary: Dict):
        k1d, k2d = self.key1_display_input.text().strip().upper() or "K1", self.key2_display_input.text().strip().upper() or "K2"
        lines = []
        for r in results:
            lines.extend([f"[{r.name.upper()}]", f"• UR (Unstable Rate): {r.ur:.2f}"])
            if r.target_bpm: lines.append(f"• Target BPM: {r.target_bpm} (BPM Match: {r.bpm_accuracy:.1f}%)")
            lines.extend([
                f"• Gallop Bias Peak: {r.gallop_bias:.2f} ms", f"• Total Hits: {r.press_count} ({k1d}: {r.key1_count} | {k2d}: {r.key2_count})",
                f"• Avg Interval: {r.avg_interval:.2f} ms", f"• Median Interval: {r.median_interval:.2f} ms", f"• Std Dev: {r.stddev_interval:.2f}",
                f"• Consistency: {r.consistency_score:.1f}%", f"• Drift (regression): {r.drift_ms:.2f} ms total ({r.drift_slope:+.3f} ms/tap)",
                f"• Avg Hold Time: {r.avg_hold_time:.2f} ms", f"• Avg Release Gap: {r.avg_release_gap:.2f} ms",
                f"• Mechanical Quality Score: {r.quality_score:.1f} ({r.quality_label})", f"• Max Pause/Gap: {r.max_gap_seconds:.2f}s",
                f"• Anomalies: Fast Repeats={r.anomaly_counts['same_key_fast_repeats']}, Soft Repeats={r.anomaly_counts.get('same_key_soft_repeats', 0)}, Short Intervals={r.anomaly_counts['very_short_intervals']}, Release Noise={r.anomaly_counts['release_noise']}, Outliers={r.anomaly_counts['outliers']}\n"
            ])
        lines.extend([
            "[COMBINED SESSION SUMMARY]", f"• Weighted UR: {summary['weighted_ur']:.1f}", f"• Weighted Mechanical Quality: {summary['weighted_quality']:.1f}",
            f"• Weighted Consistency: {summary.get('weighted_consistency', 0):.1f}%", f"• Estimated 1/4 BPM: {summary['weighted_bpm_1_4']:.1f}",
            f"• Weighted Drift: {summary['weighted_drift']:.1f} ms (slope: {summary.get('weighted_drift_slope', 0):+.3f} ms/tap)",
            f"• Overtrigger Score: {summary['overtrigger_score']:.1f}", f"• Gallop Bias Peak: {summary['gallop_bias']:.1f} ms",
            f"• Primary Advice: {summary['recommendation_text']}", f"  -> {summary['recommendation_reason']}",
            f"• Base Actuation Suggestion (mm): {summary['base_suggestion']}", f"  -> {summary.get('base_reason', '')}",
            f"• RT Press Suggestion (mm): {summary['press_suggestion']}", f"  -> {summary.get('press_reason', '')}",
            f"• RT Release Suggestion (mm): {summary['release_suggestion']}", f"  -> {summary.get('release_reason', '')}",
            f"• Analysis Summary: {summary['plain_english']}",
        ])
        self.analysis_box.setPlainText("\n".join(lines))

    def closeEvent(self, event):
        try:
            self.countdown_timer.stop()
            self.listener_mgr.stop_phase()
            self.listener_mgr.stop_background()
            self.audio_mgr.stop()
        except Exception: pass
        super().closeEvent(event)

def main() -> None:
    ensure_persistent_songs()
    app = QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    window = TapAnalyzerApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
