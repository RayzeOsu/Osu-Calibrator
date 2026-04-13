import sys
import os
import time
import threading
import statistics
import re
import shutil
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, Qt, QEvent, QUrl
from PySide6.QtGui import QDoubleValidator, QShortcut, QKeySequence, QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget, QSlider,
    QInputDialog
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
import pyqtgraph as pg

from config import (
    ensure_persistent_songs, get_persistent_songs_dir,
    RECOMMENDATION_THRESHOLDS, INTERVAL_TO_TIMING_STDEV
)
from models import PhaseConfig, PhaseResult, CalibrationSession
from history import HistoryStore
from engine import AnalysisEngine, RecommendationEngine
from listener import KeyListenerManager
from ui_components import (
    apply_shadow, MetricCard, CollapsibleSection,
    TiltedKeycapLogo, MetronomeWidget
)

STATE_IDLE = "idle"         
STATE_PHASE_READY = "ready"  
STATE_COMPLETE = "complete"  


class TapAnalyzerApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Osu! Calibrator by Rayze")
        self.resize(1550, 1050)

        ensure_persistent_songs()

        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.3)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setLoops(-1)
        self.player.errorOccurred.connect(self._on_player_error)

        self.current_target_bpm: Optional[int] = None
        self.song_is_paused = False
        self.current_song_file: Optional[str] = None

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
        self.graph_data_points: List[Dict] = []

        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update_countdown)

        self.last_status_update = 0.0

        self.build_ui()
        self.apply_styles()
        self.install_shortcuts()

        self.refresh_song_dropdown()

        self.evaluate_history()
        self.update_phase_ui()

        self.listener_mgr.start_background()
        self.installEventFilter(self)

    def _on_player_error(self, error, error_string):
        if error == QMediaPlayer.NoError:
            return
        friendly = {
            QMediaPlayer.ResourceError: "The audio file could not be opened. It may be missing, locked by another program, or corrupted.",
            QMediaPlayer.FormatError: "The audio format is not supported. Try re-encoding the song to MP3.",
            QMediaPlayer.NetworkError: "A network error occurred loading the song.",
            QMediaPlayer.AccessDeniedError: "Permission denied when reading the song file.",
        }.get(error, f"An audio error occurred: {error_string}")

        self.player.stop()
        self.song_is_paused = False
        self.metronome_widget.stop()

        QMessageBox.warning(
            self,
            "Audio Playback Error",
            f"{friendly}\n\nPhases will continue to work normally without audio.",
        )

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
        if self.listener_mgr.key_detect_mode:
            return
        if self.zen_button.isChecked():
            self.zen_button.setChecked(False)
            self.on_zen_toggled(False)
            return
        if self.test_running and self.stop_button.isEnabled():
            self.stop_button.click()
        elif self.start_button.isEnabled():
            self.start_button.click()

    def _on_start_button_clicked(self):
        if self.button_state == STATE_COMPLETE:
            self.reset_calibration()
        else:
            self.start_phase()

    def _set_button_state(self, state: str):
        self.button_state = state
        if state == STATE_IDLE:
            self.start_button.setText("Start Phase  (Space)")
            self.start_button.setEnabled(True)
        elif state == STATE_PHASE_READY:
            self.start_button.setText("Start Phase  (Space)")
            self.start_button.setEnabled(True)
        elif state == STATE_COMPLETE:
            self.start_button.setText("Reset for New Calibration  (Space)")
            self.start_button.setEnabled(True)

    def get_float(self, line_edit: QLineEdit, default: float) -> float:
        text = line_edit.text().strip()
        if not text:
            return default
        try:
            return float(text.replace(",", "."))
        except ValueError:
            return default

    def get_current_settings(self) -> Dict[str, float]:
        press_val = self.get_float(self.press_activate_input, 0.0)
        if self.separate_sensitivity_checkbox.isChecked():
            release_val = self.get_float(self.release_deactivate_input, 0.0)
        else:
            release_val = press_val

        return {
            "base": self.get_float(self.base_actuation_input, 0.0),
            "press": press_val,
            "release": release_val,
            "force": self.get_float(self.bottom_out_force_input, 0.0),
        }

    def format_setting_value(self, val: float) -> str:
        if val == 0.0:
            return ""
        return f"{val:.2f}"

    def toggle_separate_sensitivity(self, checked: bool):
        self.release_label_container.setVisible(checked)
        self.release_deactivate_input.setVisible(checked)
        if checked:
            self.press_main_label.setText("Press Activate (mm)")
        else:
            self.press_main_label.setText("Rapid Trigger (mm)")

    def extract_bpm_from_filename(self, file_name: str) -> Optional[int]:
        match = re.search(r"\[(\d+)\s*BPM\]", file_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def get_song_display_name(self, file_name: str) -> str:
        if file_name.lower().endswith(".mp3"):
            return file_name[:-4]
        return file_name

    def get_selected_song_file(self) -> Optional[str]:
        data = self.song_combo.currentData()
        if isinstance(data, str):
            return data
        return None

    def refresh_song_dropdown(self, preserve_selection: bool = True, preferred_file: Optional[str] = None):
        previous_file = self.get_selected_song_file() if preserve_selection else None

        self.song_combo.blockSignals(True)
        self.song_combo.clear()
        self.song_combo.addItem("None (No Audio)", None)

        songs_dir = get_persistent_songs_dir()
        os.makedirs(songs_dir, exist_ok=True)

        song_files = [f for f in os.listdir(songs_dir) if f.lower().endswith(".mp3")]

        songs_with_bpm = []
        for file_name in song_files:
            bpm = self.extract_bpm_from_filename(file_name)
            sort_bpm = bpm if bpm is not None else 9999
            display_name = self.get_song_display_name(file_name)
            songs_with_bpm.append((sort_bpm, display_name.lower(), display_name, file_name))

        songs_with_bpm.sort(key=lambda item: (item[0], item[1]))

        for _, _, display_name, file_name in songs_with_bpm:
            self.song_combo.addItem(display_name, file_name)

        target_file = preferred_file if preferred_file is not None else previous_file
        if target_file is not None:
            index = self.song_combo.findData(target_file)
            if index >= 0:
                self.song_combo.setCurrentIndex(index)
            else:
                self.song_combo.setCurrentIndex(0)
        else:
            self.song_combo.setCurrentIndex(0)

        self.song_combo.blockSignals(False)

    def import_custom_song(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Custom Song",
            "",
            "MP3 Files (*.mp3)",
        )

        if not file_path:
            return

        songs_dir = get_persistent_songs_dir()
        os.makedirs(songs_dir, exist_ok=True)

        file_name = os.path.basename(file_path)
        destination_path = os.path.join(songs_dir, file_name)

        if os.path.abspath(file_path) == os.path.abspath(destination_path):
            self.refresh_song_dropdown(preferred_file=file_name)
            return

        if os.path.exists(destination_path):
            reply = QMessageBox.question(
                self,
                "Song Already Exists",
                f"'{file_name}' already exists in your songs folder.\n\nDo you want to replace it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self.refresh_song_dropdown(preferred_file=file_name)
                return

        try:
            shutil.copy2(file_path, destination_path)
        except OSError as e:
            QMessageBox.critical(
                self,
                "Import Failed",
                f"Could not import the selected song.\n\n{e}",
            )
            return

        if self.extract_bpm_from_filename(file_name) is None:
            bpm_input, ok = QInputDialog.getInt(
                self,
                "BPM Missing",
                f"No BPM found in '{file_name}'.\n\nEnter the song's BPM to enable the visual metronome (or cancel to skip):",
                150, 1, 500, 1
            )
            if ok:
                new_file_name = f"{file_name[:-4]} [{bpm_input} BPM].mp3"
                new_destination = os.path.join(songs_dir, new_file_name)
                try:
                    os.rename(destination_path, new_destination)
                    file_name = new_file_name
                except OSError as e:
                    QMessageBox.warning(self, "Rename Failed", f"Could not rename file to include BPM:\n{e}\n\nThe track was imported but the BPM tag was not saved.")

        self.refresh_song_dropdown(preferred_file=file_name)

    def build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)

        container = QWidget()
        container.setObjectName("MainContainer")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(24)

        self.header_panel = QFrame()
        self.header_panel.setObjectName("HeaderPanel")
        apply_shadow(self.header_panel, blur_radius=30, y_offset=8, alpha=70)

        header_layout = QHBoxLayout(self.header_panel)
        header_layout.setContentsMargins(20, 20, 20, 20)
        header_layout.setSpacing(20)

        self.logo = TiltedKeycapLogo()
        header_layout.addWidget(self.logo)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(2)
        self.title_label = QLabel("Osu! Calibrator")
        self.title_label.setObjectName("AppTitle")
        self.rayze_label = QLabel("BY RAYZE")
        self.rayze_label.setObjectName("AppSubtitle")
        self.rayze_explainer = QLabel(
            "Iterative Hall Effect calibration. Press SPACE to start/stop a phase."
        )
        self.rayze_explainer.setObjectName("ExplainerSubtitle")
        self.rayze_explainer.setWordWrap(True)
        header_text_layout.addWidget(self.title_label)
        header_text_layout.addWidget(self.rayze_label)
        header_text_layout.addWidget(self.rayze_explainer)
        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()
        root.addWidget(self.header_panel)

        top_row = QHBoxLayout()
        top_row.setSpacing(24)
        root.addLayout(top_row)

        main_content = QVBoxLayout()
        main_content.setSpacing(20)

        workflow_panel = QFrame()
        workflow_panel.setObjectName("Panel")
        apply_shadow(workflow_panel)
        workflow_layout = QVBoxLayout(workflow_panel)
        workflow_layout.setContentsMargins(28, 28, 28, 28)
        workflow_layout.setSpacing(14)

        workflow_title = QLabel("Calibration Flow")
        workflow_title.setObjectName("SectionTitle")
        workflow_layout.addWidget(workflow_title)

        phase_header_row = QHBoxLayout()
        phase_header_row.setContentsMargins(0, 0, 0, 0)
        phase_header_row.setSpacing(12)

        self.phase_label = QLabel("")
        self.phase_label.setObjectName("BigStatus")

        self.metronome_widget = MetronomeWidget()

        phase_header_row.addWidget(self.phase_label)
        phase_header_row.addWidget(self.metronome_widget, 0, Qt.AlignVCenter)
        phase_header_row.addStretch()

        workflow_layout.addLayout(phase_header_row)

        self.phase_description_label = QLabel("")
        self.phase_description_label.setObjectName("MutedText")
        workflow_layout.addWidget(self.phase_description_label)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusBadge")
        workflow_layout.addWidget(self.status_label)

        self.countdown_label = QLabel("Time left: -")
        self.countdown_label.setObjectName("MutedText")
        workflow_layout.addWidget(self.countdown_label)

        self.tap_count_label = QLabel("Detected presses: 0")
        self.tap_count_label.setObjectName("MutedText")
        workflow_layout.addWidget(self.tap_count_label)

        self.phase_progress_label = QLabel("Phase 1 of 3")
        self.phase_progress_label.setObjectName("MutedText")
        workflow_layout.addWidget(self.phase_progress_label)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.start_button = QPushButton("Start Phase  (Space)")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._on_start_button_clicked)

        self.zen_button = QPushButton("🧘 Zen Warm-up")
        self.zen_button.setCheckable(True)
        self.zen_button.clicked.connect(self.on_zen_toggled)

        self.stop_button = QPushButton("Stop  (Space)")
        self.stop_button.clicked.connect(self.stop_phase)
        self.stop_button.setEnabled(False)

        button_layout.addWidget(self.start_button, 2)
        button_layout.addWidget(self.zen_button, 1)
        button_layout.addWidget(self.stop_button, 1)

        bottom_actions = QHBoxLayout()

        self.reset_button = QPushButton("Start New Calibration")
        self.reset_button.setObjectName("SecondaryAction")
        self.reset_button.clicked.connect(self.reset_calibration)

        self.export_button = QPushButton("Copy Report")
        self.export_button.clicked.connect(self.export_to_clipboard)

        self.clear_history_button = QPushButton("Clear History")
        self.clear_history_button.clicked.connect(self.confirm_clear_history)

        bottom_actions.addWidget(self.reset_button)
        bottom_actions.addWidget(self.export_button)
        bottom_actions.addWidget(self.clear_history_button)

        workflow_layout.addLayout(button_layout)
        workflow_layout.addLayout(bottom_actions)
        main_content.addWidget(workflow_panel)

        self.summary_coaching_card = MetricCard(
            "Coaching Partner",
            "Compares runs for prescriptive tuning loops."
        )
        main_content.addWidget(self.summary_coaching_card)

        top_row.addLayout(main_content, 1)

        side_column = QVBoxLayout()
        side_column.setSpacing(20)

        how_to_use = QFrame()
        how_to_use.setObjectName("InstructionBanner")
        apply_shadow(how_to_use)
        how_layout = QVBoxLayout(how_to_use)
        how_layout.setContentsMargins(20, 20, 20, 20)

        how_title = QLabel("USER INSTRUCTIONS")
        how_title.setObjectName("SectionTitle")
        how_text = QLabel(
            "1. Enter <b>current</b> settings and bind keys.<br>"
            "2. Set RT settings if tuned.<br><br>"
            "<b>DURING THE TEST:</b><br>"
            "<span style='color: #5865F2;'>■ Comfort:</span> Tap natural stream speed.<br>"
            "<span style='color: #FEE75C;'>■ Push:</span> Tap near speed limit.<br>"
            "<span style='color: #57F287;'>■ Stability:</span> Tap through fatigue.<br><br>"
            "Press <b>SPACE</b> to start/stop phases.<br><br>"
            "3. Review UR and apply coaching advice."
        )
        how_text.setObjectName("RichText")
        how_text.setWordWrap(True)
        how_layout.addWidget(how_title)
        how_layout.addWidget(how_text)
        side_column.addWidget(how_to_use)

        settings_panel = QFrame()
        settings_panel.setObjectName("Panel")
        apply_shadow(settings_panel)
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(24, 24, 24, 24)
        settings_layout.setSpacing(16)

        settings_title = QLabel("Device Settings")
        settings_title.setObjectName("SectionTitle")
        settings_layout.addWidget(settings_title)

        form = QFormLayout()
        form.setVerticalSpacing(16)
        form.setHorizontalSpacing(18)

        double_val = QDoubleValidator(0.00, 150.00, 2)
        double_val.setNotation(QDoubleValidator.StandardNotation)

        def polish_input(le: QLineEdit):
            le.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self.base_actuation_input = QLineEdit("")
        self.base_actuation_input.setPlaceholderText("eg 0.70")
        self.base_actuation_input.setValidator(double_val)
        polish_input(self.base_actuation_input)

        key_layout = QHBoxLayout()
        self.key1_display_input = QLineEdit("")
        self.key1_display_input.setPlaceholderText("eg Z")
        self.key1_display_input.setReadOnly(True)
        self.key2_display_input = QLineEdit("")
        self.key2_display_input.setPlaceholderText("eg X")
        self.key2_display_input.setReadOnly(True)

        self.detect_btn = QPushButton("Record Keys")
        self.detect_btn.setMinimumWidth(100)
        self.detect_btn.clicked.connect(self.start_key_detect)

        self.detect_cancel_btn = QPushButton("Cancel")
        self.detect_cancel_btn.setMinimumWidth(80)
        self.detect_cancel_btn.clicked.connect(self.cancel_key_detect_from_ui)
        self.detect_cancel_btn.setVisible(False)

        key_layout.addWidget(self.key1_display_input)
        key_layout.addWidget(self.key2_display_input)
        key_layout.addWidget(self.detect_btn)
        key_layout.addWidget(self.detect_cancel_btn)

        self.song_combo = QComboBox()
        self.song_combo.addItem("None (No Audio)", None)

        self.import_song_button = QPushButton("Import MP3")
        self.import_song_button.clicked.connect(self.import_custom_song)

        self.volume_title_label = QLabel("Volume")
        self.volume_title_label.setObjectName("MiniLabel")

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(30)
        self.volume_slider.setToolTip("Audio Volume")
        self.volume_slider.setFixedWidth(130)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100.0))

        volume_layout = QVBoxLayout()
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(4)
        volume_layout.addWidget(self.volume_title_label)
        volume_layout.addWidget(self.volume_slider)

        audio_row_container = QWidget()
        audio_row_layout = QHBoxLayout(audio_row_container)
        audio_row_layout.setContentsMargins(0, 0, 0, 0)
        audio_row_layout.setSpacing(10)
        audio_row_layout.addWidget(self.song_combo, 1)
        audio_row_layout.addWidget(self.import_song_button)
        audio_row_layout.addLayout(volume_layout)

        def make_help_label(text: str, tooltip: str) -> QWidget:
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            label = QLabel(text)
            label.setObjectName("MainHelpLabel")
            from ui_components import HelpIconLabel
            icon = HelpIconLabel("ⓘ", tooltip)
            layout.addWidget(label)
            layout.addWidget(icon)
            layout.addStretch()
            return container

        form.addRow(
            make_help_label("Base Actuation (mm)", "Your main current actuation point right now."),
            self.base_actuation_input
        )
        form.addRow(
            make_help_label("Calibration Track", "Plays during phase. Extracts BPM for accurate tracking."),
            audio_row_container
        )
        form.addRow(
            make_help_label("Bind Keys", "Click and tap keys to bind them automatically."),
            key_layout
        )
        settings_layout.addLayout(form)

        self.advanced_section = CollapsibleSection("Advanced RT Settings")
        advanced_layout = QFormLayout(self.advanced_section.content)
        advanced_layout.setContentsMargins(16, 16, 16, 16)

        self.separate_sensitivity_checkbox = QCheckBox("Separate press/release sensitivity enabled")
        self.separate_sensitivity_checkbox.setChecked(True)
        self.separate_sensitivity_checkbox.toggled.connect(self.toggle_separate_sensitivity)

        self.press_activate_input = QLineEdit("")
        self.press_activate_input.setPlaceholderText("eg 0.15")
        self.press_activate_input.setValidator(double_val)
        polish_input(self.press_activate_input)

        self.release_deactivate_input = QLineEdit("")
        self.release_deactivate_input.setPlaceholderText("eg 0.15")
        self.release_deactivate_input.setValidator(double_val)
        polish_input(self.release_deactivate_input)

        self.bottom_out_force_input = QLineEdit("")
        self.bottom_out_force_input.setValidator(double_val)
        self.bottom_out_force_input.setPlaceholderText("eg 45 (Optional)")
        polish_input(self.bottom_out_force_input)

        self.press_label_container = make_help_label("Press Activate (mm)", "Downward movement needed to activate.")
        self.press_main_label = self.press_label_container.findChild(QLabel, "MainHelpLabel")

        self.release_label_container = make_help_label("Release Deactivate (mm)", "Upward movement needed to reset.")

        advanced_layout.addRow("", self.separate_sensitivity_checkbox)
        advanced_layout.addRow(self.press_label_container, self.press_activate_input)
        advanced_layout.addRow(self.release_label_container, self.release_deactivate_input)
        advanced_layout.addRow(
            make_help_label("Bottom-out Force (g)", "Heavier switches tolerate lower settings better."),
            self.bottom_out_force_input
        )

        settings_layout.addWidget(self.advanced_section)
        side_column.addWidget(settings_panel)
        side_column.addStretch()

        top_row.addLayout(side_column, 2)

        self.toggle_separate_sensitivity(self.separate_sensitivity_checkbox.isChecked())

        self.advanced_section.toggle_button.setChecked(True)
        self.advanced_section.on_toggled()

        cards = QGridLayout()
        cards.setSpacing(16)
        root.addLayout(cards)

        self.summary_bpm_card = MetricCard(
            "Tap Steadiness (UR)",
            "Lower Unstable Rate is steadier timing."
        )
        self.summary_quality_card = MetricCard(
            "Mechanical Quality",
            "How clean this run was mechanically (misfires vs clean hits)."
        )
        self.summary_confidence_card = MetricCard(
            "Analysis Confidence",
            "How sure we are about the suggestions below based on the run details."
        )
        self.summary_recommendation_card = MetricCard(
            "Calibration Advice",
            "Separates settings sensitivity advice from general skill coaching."
        )
        self.summary_press_card = MetricCard("Rapid Trigger (Press)", "Activation distance tuning — how far down the key travels before firing.")
        self.summary_release_card = MetricCard("Rapid Trigger (Release)", "Reset distance tuning — how far up the key travels before resetting.")
        self.summary_base_card = MetricCard("Base Actuation", "Global actuation point tuning suggestion.")

        self.summary_phase_note_card = MetricCard(
            "Analysis Summary",
            "Plain English summary of findings."
        )

        self.summary_tip_card = MetricCard(
            "Mechanics Tip",
            "Actionable technique advice based on what we spotted in your tapping."
        )

        cards.addWidget(self.summary_bpm_card, 0, 0)
        cards.addWidget(self.summary_quality_card, 0, 1)
        cards.addWidget(self.summary_confidence_card, 0, 2)
        cards.addWidget(self.summary_recommendation_card, 0, 3, 2, 1)

        cards.addWidget(self.summary_press_card, 1, 0)
        cards.addWidget(self.summary_release_card, 1, 1)
        cards.addWidget(self.summary_base_card, 1, 2)

        cards.addWidget(self.summary_phase_note_card, 2, 0, 1, 3)
        cards.addWidget(self.summary_tip_card, 2, 3)

        self.change_log_section = CollapsibleSection("What Changed? (Engine Reasoning)")
        cl_layout = QVBoxLayout(self.change_log_section.content)
        cl_layout.setContentsMargins(16, 16, 16, 16)
        self.change_log_label = QLabel("Complete two runs to see comparison reasoning.")
        self.change_log_label.setObjectName("RichText")
        self.change_log_label.setWordWrap(True)
        cl_layout.addWidget(self.change_log_label)
        root.addWidget(self.change_log_section)

        graph_panel = QFrame()
        graph_panel.setObjectName("Panel")
        apply_shadow(graph_panel)
        graph_layout = QVBoxLayout(graph_panel)
        graph_layout.setContentsMargins(16, 16, 16, 16)
        graph_layout.setSpacing(8)

        graph_title = QLabel("Tap Interval Analysis")
        graph_title.setObjectName("SectionTitle")
        graph_layout.addWidget(graph_title)

        graph_sub = QLabel("Colours match the phase instructions. Dashed lines represent phase averages.")
        graph_sub.setObjectName("MutedText")
        graph_layout.addWidget(graph_sub)

        self.graph = pg.PlotWidget()
        self.graph.setMinimumHeight(300)
        self.graph.setBackground("#1a1b1e")
        self.graph.showGrid(x=True, y=True, alpha=0.15)
        self.graph.setLabel("left", "Interval (ms)")
        self.graph.setLabel("bottom", "Tap Sequence")

        axis_pen = pg.mkPen("#80848e")
        self.graph.getAxis("left").setTextPen(axis_pen)
        self.graph.getAxis("bottom").setTextPen(axis_pen)
        self.graph.getAxis("left").setPen(axis_pen)
        self.graph.getAxis("bottom").setPen(axis_pen)

        graph_layout.addWidget(self.graph)
        root.addWidget(graph_panel)

        self.graph.scene().sigMouseMoved.connect(self.on_mouse_moved)
        self.setup_graph_interaction_items()

        details_section = CollapsibleSection("Raw Analysis Data")
        details_layout = QVBoxLayout(details_section.content)
        details_layout.setContentsMargins(16, 16, 16, 16)

        self.analysis_box = QPlainTextEdit()
        self.analysis_box.setReadOnly(True)
        self.analysis_box.setMinimumHeight(200)
        details_layout.addWidget(self.analysis_box)

        root.addWidget(details_section)

        self.clear_summary_cards()

    def apply_styles(self) -> None:
        self.setStyleSheet("""
            QWidget {
                color: #f2f3f5;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                font-size: 14px;
            }
            #MainContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1625, stop:0.4 #111214, stop:1 #09090b);
            }
            QMainWindow { background-color: #09090b; }
            QToolTip {
                background-color: #2b2d31;
                color: #f2f3f5;
                border: 1px solid #111214;
                padding: 6px 10px;
                border-radius: 4px;
            }
            QScrollArea { border: none; background: transparent; }
            #HeaderPanel {
                background-color: #0c0c0e;
                border: 1px solid #111214;
                border-radius: 12px;
            }
            #AppTitle {
                font-size: 32px;
                font-weight: 900;
                color: #ffffff;
                letter-spacing: -1px;
            }
            #AppSubtitle {
                color: #5865F2;
                font-size: 14px;
                font-weight: 800;
                letter-spacing: 1.5px;
            }
            #ExplainerSubtitle { color: #80848e; font-size: 13px; }
            #SectionTitle {
                font-size: 15px;
                font-weight: 800;
                color: #ffffff;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            #BigStatus { font-size: 24px; font-weight: 800; color: #ffffff; }
            #MutedText { color: #a1a1aa; line-height: 1.5; }
            #RichText { color: #dbdee1; font-size: 14px; line-height: 1.4; }
            #HelpIcon { color: #80848e; font-weight: bold; font-size: 14px; }
            #HelpIcon:hover { color: #ffffff; }
            #MiniLabel {
                color: #a1a1aa;
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            #InstructionBanner {
                background-color: #1e1f22;
                border: 1px solid #111214;
                border-radius: 12px;
                border-left: 4px solid #5865F2;
            }
            #StatusBadge {
                background-color: #1e1f22;
                border: 1px solid #111214;
                color: #dbdee1;
                border-radius: 8px;
                padding: 10px 14px;
                font-weight: 700;
                font-size: 13px;
            }
            #Panel, #MetricCard, QPlainTextEdit {
                background-color: #1e1f22;
                border: 1px solid #111214;
                border-radius: 12px;
            }
            #PanelSub {
                background-color: #0c0c0e;
                border: 1px solid #111214;
                border-radius: 8px;
            }
            #MetricCard:hover, QPlainTextEdit:hover, QLineEdit:hover {
                border-color: #313338;
                background-color: #2b2d31;
            }
            #MetricCard[status="good"]  { border-left: 4px solid #57F287; }
            #MetricCard[status="warn"]  { border-left: 4px solid #FEE75C; }
            #MetricCard[status="bad"]   { border-left: 4px solid #ED4245; }
            #MetricCard[status="info"]  { border-left: 4px solid #5865F2; }
            #MetricCard[status="coach"] { border-left: 4px solid #EB459E; }
            #MetricTitle {
                color: #949ba4;
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            #MetricValue { color: #ffffff; font-size: 18px; font-weight: 700; }
            #MetricSub { color: #a1a1aa; font-size: 13px; }
            QLineEdit, QComboBox {
                background-color: #0c0c0e;
                border: 1px solid #111214;
                border-radius: 6px;
                padding: 10px 12px;
                color: #ffffff;
                font-size: 15px;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                border: 1px solid #5865F2;
                background-color: #111214;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #111214;
                color: #ffffff;
                selection-background-color: #5865F2;
            }
            QPushButton {
                background-color: #313338;
                border: 1px solid #111214;
                border-radius: 8px;
                padding: 14px 16px;
                font-weight: 600;
                color: #ffffff;
                font-size: 15px;
            }
            QPushButton:hover { background-color: #4e5058; border-color: #313338; color: #ffffff; }
            QPushButton:pressed { background-color: #2b2d31; }
            QPushButton:disabled { color: #80848e; background-color: #1e1f22; border-color: #111214; }
            #PrimaryButton {
                background-color: #5865F2;
                border: 1px solid #5865F2;
                color: white;
            }
            #PrimaryButton:hover { background-color: #4752C4; border-color: #4752C4; }
            #PrimaryButton:pressed { background-color: #3C45A5; }
            #PrimaryButton:disabled { background-color: #1e3a8a; color: #60a5fa; border-color: #1e3a8a; }
            #SecondaryAction {
                background-color: #248046;
                border: 1px solid #248046;
                color: #ffffff;
            }
            #SecondaryAction:hover { background-color: #1a6334; }
            QCheckBox { spacing: 10px; }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border-radius: 4px;
                border: 1px solid #313338;
                background-color: #0c0c0e;
            }
            QCheckBox::indicator:checked {
                background-color: #5865F2;
                border: 1px solid #5865F2;
            }
            QSlider::groove:horizontal {
                border: 1px solid #111214;
                height: 6px;
                background: #0c0c0e;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #5865F2;
                border: 1px solid #5865F2;
                width: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }
            QSlider::sub-page:horizontal {
                background: #5865F2;
                border-radius: 3px;
            }
        """)

    def setup_graph_interaction_items(self):
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#e4e4e7", width=1, style=Qt.DashLine))
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#e4e4e7", width=1, style=Qt.DashLine))
        self.tooltip_text = pg.TextItem(anchor=(0, 1), color="#ffffff", fill="#2b2d31", border="#111214")

        self.graph.addItem(self.vLine, ignoreBounds=True)
        self.graph.addItem(self.hLine, ignoreBounds=True)
        self.graph.addItem(self.tooltip_text, ignoreBounds=True)

        self.vLine.hide()
        self.hLine.hide()
        self.tooltip_text.hide()

    def hide_graph_interaction_items(self):
        if hasattr(self, "vLine"):
            self.vLine.hide()
        if hasattr(self, "hLine"):
            self.hLine.hide()
        if hasattr(self, "tooltip_text"):
            self.tooltip_text.hide()

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
        if not self.test_running:
            return
        with self.lock:
            if key not in self.held_keys:
                self.held_keys.add(key)
                self.events.append({"time": t, "type": "press", "key": key})
                self.cached_press_count += 1

    def on_phase_release(self, key: str, t: float):
        if self.in_zen_mode:
            self.zen_events.append({"time": t, "type": "release", "key": key})
            return
        if not self.test_running:
            return
        with self.lock:
            self.held_keys.discard(key)
            self.events.append({"time": t, "type": "release", "key": key})

    def export_to_clipboard(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.analysis_box.toPlainText())
        self.export_button.setText("Copied!")
        QTimer.singleShot(2000, lambda: self.export_button.setText("Copy Report"))

    def confirm_clear_history(self):
        if not self.history.sessions:
            QMessageBox.information(self, "No History", "There is no calibration history to clear.")
            return
        reply = QMessageBox.question(
            self,
            "Clear History",
            f"Delete all {len(self.history.sessions)} saved calibration sessions? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.history.clear()
            self.evaluate_history()
            QMessageBox.information(self, "History Cleared", "Calibration history has been deleted.")

    def clear_summary_cards(self) -> None:
        self.summary_bpm_card.set_data("-", "Run a calibration to populate this.", "neutral")
        self.summary_quality_card.set_data("-", "Waiting for session data.", "neutral")
        self.summary_confidence_card.set_data("-", "Waiting for session data.", "neutral")
        self.summary_recommendation_card.set_data("-", "No recommendation yet.", "neutral")
        self.summary_press_card.set_data("-", "N/A", "neutral")
        self.summary_release_card.set_data("-", "N/A", "neutral")
        self.summary_base_card.set_data("-", "N/A", "neutral")
        self.summary_phase_note_card.set_data("-", "Complete all phases for analysis.", "neutral")
        self.summary_tip_card.set_data("-", "Tips will appear after your calibration.", "neutral")

    def update_phase_ui(self) -> None:
        cfg = self.phase_configs[self.current_phase_index]
        self.phase_label.setText(cfg.name)
        self.phase_description_label.setText(cfg.description)
        self.phase_progress_label.setText(f"Phase {self.current_phase_index + 1} of {len(self.phase_configs)}")

    def cancel_phase(self) -> None:
        if not self.test_running:
            return
        self.test_running = False
        self.countdown_timer.stop()
        self.listener_mgr.stop_phase()
        self.metronome_widget.stop()

        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.stop()
        self.song_is_paused = False

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

    def reset_calibration(self) -> None:
        if self.zen_button.isChecked():
            self.zen_button.setChecked(False)
            self.on_zen_toggled(False)

        if self.test_running:
            self.cancel_phase()

        self.listener_mgr.bg_held_keys.clear()
        self.listener_mgr.key_state_changed.emit(False)

        self.player.stop()
        self.song_is_paused = False
        self.current_target_bpm = None
        self.current_song_file = None
        self.metronome_widget.stop()

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

        self.graph.clear()
        self.graph_data_points = []
        self.setup_graph_interaction_items()
        self.change_log_label.setText("Complete two runs to see comparison reasoning.")

        self.reset_key_detect_ui()

    def on_zen_toggled(self, checked: bool):
        if checked:
            k1_raw = self._bound_key1_raw
            k2_raw = self._bound_key2_raw
            if not k1_raw or not k2_raw:
                self.zen_button.setChecked(False)
                QMessageBox.warning(self, "Missing Keys", "Bind your stream keys first.")
                return

            self.listener_mgr.bg_held_keys.clear()
            self.listener_mgr.key_state_changed.emit(False)

            self.in_zen_mode = True
            self.zen_events = []
            self.start_button.setEnabled(False)
            self.reset_button.setEnabled(False)

            selected_song_file = self.get_selected_song_file()
            if selected_song_file:
                song_path = os.path.join(get_persistent_songs_dir(), selected_song_file)
                if os.path.exists(song_path):
                    self.player.setSource(QUrl.fromLocalFile(song_path))
                    self.player.play()
                    self.current_target_bpm = self.extract_bpm_from_filename(selected_song_file)
                    if self.current_target_bpm:
                        self.metronome_widget.start(self.current_target_bpm)

            self.status_label.setText("Zen Mode: Warming up... (Tap to start)")
            self.status_label.setStyleSheet("color: #5865F2; border-color: #5865F2;")

            self.listener_mgr.start_phase(k1_raw, k2_raw)
            self.countdown_timer.start(50)
        else:
            self.in_zen_mode = False
            self.listener_mgr.stop_phase()
            self.metronome_widget.stop()
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.stop() 
            
            self.current_song_file = None
            self.current_target_bpm = None
            self.countdown_timer.stop()

            self.start_button.setEnabled(True)
            self.reset_button.setEnabled(True)
            self.status_label.setText("Ready")
            self.status_label.setStyleSheet("")
            self.countdown_label.setText("Time left: -")
            self.tap_count_label.setText("Detected presses: 0")

    def start_phase(self) -> None:
        if self.listener_mgr.key_detect_mode:
            QMessageBox.information(
                self,
                "Key Binding In Progress",
                "Please finish binding your keys or cancel before starting a phase."
            )
            return

        if self.test_running:
            return

        base_val = self.base_actuation_input.text().strip()
        k1_raw = self._bound_key1_raw
        k2_raw = self._bound_key2_raw

        if not base_val:
            QMessageBox.warning(self, "Missing Settings", "You must enter your current Base Actuation before testing.")
            self.base_actuation_input.setFocus()
            return

        if not k1_raw or not k2_raw:
            QMessageBox.warning(self, "Missing Keys", "You must bind both stream keys. Use the 'Record Keys' button.")
            return

        if k1_raw == k2_raw:
            QMessageBox.warning(self, "Invalid Keys", "Key 1 and Key 2 must be different physical keys.")
            return

        sep_on = self.separate_sensitivity_checkbox.isChecked()
        press_val_str = self.press_activate_input.text().strip()
        if sep_on:
            rel_val_str = self.release_deactivate_input.text().strip()
            if not press_val_str or not rel_val_str:
                QMessageBox.warning(
                    self,
                    "Missing Settings",
                    "Separate Sensitivity is enabled. Fill out Press/Release settings, or disable the option."
                )
                return
        else:
            if not press_val_str:
                QMessageBox.warning(
                    self,
                    "Missing Settings",
                    "Please fill out your Rapid Trigger setting."
                )
                return

        settings = self.get_current_settings()
        warnings = RecommendationEngine.validate_settings(
            settings["base"], settings["press"], settings["release"], settings["force"], sep_on
        )
        if warnings:
            msg = "Configuration warnings:\n\n" + "\n\n".join(f"• {w}" for w in warnings) + "\n\nProceed anyway?"
            reply = QMessageBox.question(self, "Settings Sanity Check", msg, QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                return

        prev = self.history.latest()
        if prev and self.current_phase_index == 0 and not self.phase_results:
            if settings != prev.settings:
                reply = QMessageBox.question(
                    self,
                    "Settings Changed",
                    "Configuration changed since last test. Treat these new settings as the 'Baseline' for this run?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.No:
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

        selected_song_file = self.get_selected_song_file()
        is_push_phase = (self.current_phase_index == 1)

        if selected_song_file:
            self.current_song_file = selected_song_file

            if self.current_phase_index == 0 and not self.song_is_paused:
                song_path = os.path.join(get_persistent_songs_dir(), selected_song_file)
                if os.path.exists(song_path):
                    self.player.setSource(QUrl.fromLocalFile(song_path))
                    self.player.play()
                    self.current_target_bpm = self.extract_bpm_from_filename(selected_song_file)
                else:
                    QMessageBox.warning(
                        self,
                        "Song Missing",
                        f"Selected track not found:\n\n{song_path}\n\nAudio disabled."
                    )
                    self.player.stop()
                    self.current_song_file = None
                    self.current_target_bpm = None

            elif self.song_is_paused:
                self.player.play()
                self.song_is_paused = False

            if self.current_target_bpm:
                self.metronome_widget.start(self.current_target_bpm)
                self.metronome_widget.set_faded(is_push_phase)
            else:
                self.metronome_widget.stop()
        else:
            self.current_song_file = None
            self.current_target_bpm = None
            self.player.stop()
            self.song_is_paused = False
            self.metronome_widget.stop()

        self.last_status_update = time.perf_counter()

        self.status_label.setText("Waiting for first tap...")
        self.status_label.setStyleSheet("color: #FEE75C; border-color: #FEE75C;")
        self.countdown_label.setText(f"Time left: {cfg.duration}s")
        self.tap_count_label.setText("Detected presses: 0")

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        self.listener_mgr.start_phase(k1_raw, k2_raw)
        self.countdown_timer.start(50)

    def stop_phase(self) -> None:
        if not self.test_running:
            return

        self.test_running = False
        self.countdown_timer.stop()
        self.listener_mgr.stop_phase()
        self.metronome_widget.stop()

        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.song_is_paused = True

        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet("color: #949ba4; border-color: #313338;")

        cfg = self.phase_configs[self.current_phase_index]
        k1 = self._bound_key1_raw
        k2 = self._bound_key2_raw

        with self.lock:
            evs = list(self.events)

        result, error = AnalysisEngine.analyse_phase(evs, cfg, k1, k2, self.current_target_bpm)

        if not result:
            self.status_label.setText("Phase failed - retry")
            self.status_label.setStyleSheet("color: #FEE75C; border-color: #FEE75C;")
            self._set_button_state(STATE_PHASE_READY)
            self.stop_button.setEnabled(False)
            self.countdown_label.setText("Time left: 0.0s")
            QMessageBox.warning(
                self,
                "Not Enough Usable Data",
                f"Phase retry needed:\n\n{error}\n\nPlease retry the same phase."
            )
            return

        self.phase_results.append(result)

        if self.current_phase_index == 0 and result.quality_score < 55:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Messy Comfort Phase")
            msg.setText(
                "This phase didn't produce reliable data. This can happen if you're not warmed up yet, "
                "if your hands are cold, OR if your current settings are fighting you.\n\n"
                "You can stop here and adjust your settings, or push through to the end."
            )
            stop_btn = msg.addButton("Stop & Fix Settings", QMessageBox.ActionRole)
            cont_btn = msg.addButton("Continue Anyway", QMessageBox.AcceptRole)
            msg.exec()

            if msg.clickedButton() == stop_btn:
                self.reset_calibration()
                return
        elif result.quality_score < 40:
            QMessageBox.warning(
                self,
                "Poor Test Data",
                f"{result.name} was very messy ({result.quality_label}).\n"
                f"Anomalies: Release Noise={result.anomaly_counts['release_noise']}, "
                f"Fast Repeats={result.anomaly_counts['same_key_fast_repeats']}.\n"
                "The final recommendation may be unreliable."
            )

        if result.max_gap_seconds > RECOMMENDATION_THRESHOLDS["max_phase_gap_seconds"]:
            QMessageBox.warning(
                self,
                "Pause Detected",
                f"A pause of {result.max_gap_seconds:.1f}s was detected during {result.name}. "
                "This may skew the analysis."
            )

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

    def update_countdown(self) -> None:
        if self.in_zen_mode:
            now = time.perf_counter()
            self.zen_events = [e for e in self.zen_events if now - e["time"] <= 30.0]
            presses = [e for e in self.zen_events if e["type"] == "press"]

            self.tap_count_label.setText(f"Taps in last 30s: {len(presses)}")

            if len(presses) >= 10:
                ints = [(presses[i]["time"] - presses[i - 1]["time"]) * 1000 for i in range(1, len(presses))]
                avg_int = statistics.mean(ints)
                std_dev = statistics.stdev(ints) if len(ints) > 1 else 0.0
                ur = (std_dev / INTERVAL_TO_TIMING_STDEV) * 10.0
                bpm = 15000 / avg_int if avg_int > 0 else 0

                if len(presses) > 40 and ur < 120:
                    self.status_label.setText(f"Zen Mode: {bpm:.0f} BPM | {ur:.0f} UR — Looking ready! 🟢")
                    self.status_label.setStyleSheet("color: #57F287; border-color: #57F287;")
                else:
                    self.status_label.setText(f"Zen Mode: {bpm:.0f} BPM | {ur:.0f} UR — Warming up...")
                    self.status_label.setStyleSheet("color: #5865F2; border-color: #5865F2;")
            return

        if not self.test_running:
            return

        with self.lock:
            press_count = self.cached_press_count

            if self.waiting_for_first_tap:
                if press_count > 0:
                    self.waiting_for_first_tap = False
                    cfg = self.phase_configs[self.current_phase_index]
                    self.start_time = self.events[0]["time"]
                    self.end_time = self.start_time + cfg.duration

                    self.status_label.setText("Recording Live")
                    self.status_label.setStyleSheet("color: #ED4245; border-color: #ED4245;")
                else:
                    return

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
                    avg_interval = statistics.mean(ints)
                    std_dev = statistics.stdev(ints) if len(ints) > 1 else 0.0

                    live_ur = (std_dev / INTERVAL_TO_TIMING_STDEV) * 10.0
                    live_bpm = 15000 / avg_interval if avg_interval > 0 else 0

                    self.status_label.setText(f"Recording Live ({live_bpm:.0f} BPM | {live_ur:.0f} UR)")

        if rem <= 0:
            self.stop_phase()

    def render_final_results(self):
        if not self.phase_results:
            self.analysis_box.setPlainText("No usable phase data was captured.")
            return

        settings = self.get_current_settings()
        sep_on = self.separate_sensitivity_checkbox.isChecked()

        prev_session = self.history.latest()
        prev_summary = prev_session.summary if prev_session else None

        summary = RecommendationEngine.build_summary(
            self.phase_results,
            self.phase_configs,
            settings["base"],
            settings["press"],
            settings["release"],
            settings["force"],
            sep_on,
            prev_summary
        )

        MESSY_QUAL = 55
        curr_messy = summary.get("weighted_quality", 100) < MESSY_QUAL
        prev_two = self.history.sessions[-2:]
        prev_two_messy = (
            len(prev_two) >= 2
            and all(s.summary.get("weighted_quality", 100) < MESSY_QUAL for s in prev_two)
        )

        if curr_messy and prev_two_messy:
            curr_base = settings["base"]
            safe_base = curr_base if 0.6 <= curr_base <= 2.0 else 1.0
            safe_each = round(safe_base * 0.35, 2)

            summary["recommendation_text"] = "Switch to a safe baseline preset"
            summary["recommendation_reason"] = (
                "We've seen a few messy runs in a row — micro-adjustments aren't helping right now."
            )
            summary["recommendation_status"] = "coach"
            summary["base_suggestion"] = f"Try {safe_base:.2f} mm"
            summary["base_reason"] = "A forgiving depth that should 'just work' while you build consistency."
            if sep_on:
                summary["press_suggestion"] = f"Try {safe_each:.2f} mm"
                summary["press_reason"] = "Safe RT distance — fits comfortably inside the base depth."
                summary["release_suggestion"] = f"Try {safe_each:.2f} mm"
                summary["release_reason"] = "Safe RT distance — fits comfortably inside the base depth."
            summary["plain_english"] = (
                "After a few messy runs, here's the deal: rather than chasing tiny tweaks, "
                f"we recommend a safe 'training wheels' preset (Base {safe_base:.2f} mm, "
                f"RT {safe_each:.2f} mm each side). These settings should just work while "
                "you focus on building tapping consistency. Come back when your runs feel "
                "steadier and we'll dial things in properly."
            )
            summary["technique_tip"] = (
                "Build a daily warm-up routine: 5 minutes of slow alternation drills before "
                "any serious tapping. Consistency comes from repetition, not from settings."
            )

        self.history.append(CalibrationSession(settings=settings, summary=summary.copy()))

        self.render_summary_cards(summary)
        self.render_graph(self.phase_results)
        self.render_detailed_text(self.phase_results, summary)
        self.evaluate_history()

        self.change_log_label.setText(summary.get("change_log_text", ""))

    def evaluate_history(self):
        if len(self.history.sessions) < 2:
            self.summary_coaching_card.set_data(
                "Waiting for next run",
                "Run another calibration with changes so we can compare progress.",
                "info"
            )
            return

        curr = self.history.latest()
        prev = self.history.previous()

        curr_ur = curr.summary.get("weighted_ur", 0.0)
        prev_ur = prev.summary.get("weighted_ur", 0.0)
        ur_delta = curr_ur - prev_ur

        curr_qual = curr.summary.get("weighted_quality", 0.0)
        prev_qual = prev.summary.get("weighted_quality", 0.0)
        qual_delta = curr_qual - prev_qual

        settings_unchanged = (curr.settings == prev.settings)
        huge_swing = abs(ur_delta) >= 40

        last_three_urs = [s.summary.get("weighted_ur", 0.0) for s in self.history.sessions[-3:]]
        three_run_spread = max(last_three_urs) - min(last_three_urs) if len(last_three_urs) == 3 else 0

        if settings_unchanged and huge_swing:
            value = "Your tapping is volatile today"
            sub = (
                f"Your timing swung by {abs(ur_delta):.0f} UR between runs even though "
                "your settings didn't change. That's you, not the app. "
                "Warm up for 5 minutes, keep your hands warm, and retest — "
                "inconsistent tapping will keep producing inconsistent advice."
            )
            status = "warn"
            self.summary_coaching_card.set_data(value, sub, status)
            return

        if len(last_three_urs) == 3 and three_run_spread >= 60:
            value = "Runs are all over the place"
            sub = (
                f"Your last 3 runs varied by {three_run_spread:.0f} UR. "
                "This usually means you aren't warmed up, you're tired, or your hands are cold. "
                "The recommendations will keep shifting until your tapping stabilises — "
                "take a break and come back when your fingers feel locked in."
            )
            status = "warn"
            self.summary_coaching_card.set_data(value, sub, status)
            return

        if curr_ur < 100 and prev_ur < 110 and abs(ur_delta) < 8 and qual_delta > -5:
            value = "Optimal range reached!"
            sub = "Your timing is excellent and highly stable. These settings are perfect for practice."
            status = "good"
        elif ur_delta < -15.0:
            value = "Big improvement!"
            sub = "Your timing is noticeably steadier than last time. Keep these settings."
            status = "good"
        elif ur_delta < -5.0:
            value = "Getting steadier"
            sub = "Your timing improved compared to your last run."
            if qual_delta > 5:
                sub += " The run was also cleaner."
            status = "good"
        elif ur_delta > 15.0:
            value = "Timing worsened"
            sub = "Your timing was noticeably less stable. Consider reverting to your previous settings."
            if qual_delta < -10:
                sub += " The run was also messier."
            status = "bad"
        elif ur_delta > 5.0:
            value = "Tiny step back"
            sub = "Your timing was slightly less stable than last time. This could be fatigue or over-sensitive settings."
            status = "warn"
        else:
            value = "Consistent progress"
            sub = "Your timing is about as steady as your previous run."
            if abs(qual_delta) > 8:
                if qual_delta > 0:
                    sub += " The run was cleaner, though."
                else:
                    sub += " The run was slightly messier, though."
            status = "info"

        self.summary_coaching_card.set_data(value, sub, status)

    def render_summary_cards(self, summary: Dict):
        q_stat = "good" if summary["session_quality"] == "Good" else ("warn" if summary["session_quality"] == "Usable" else "bad")

        ur_val = summary["weighted_ur"]
        if ur_val < 95:
            ur_meaning = "Elite steadiness — taps are perfectly timed."
        elif ur_val < 135:
            ur_meaning = "Controlled steadiness — good timing."
        elif ur_val < 170:
            ur_meaning = "Decent timing — room to dial in."
        elif ur_val < 220:
            ur_meaning = "Uneven timing — difficult to judge settings."
        else:
            ur_meaning = "Very uneven timing — test results may be unreliable."

        bpm_sub_parts = [f"Around {summary['weighted_bpm_1_4']:.0f} BPM. {ur_meaning}"]

        accuracies = [r.bpm_accuracy for r in self.phase_results if r.bpm_accuracy is not None]
        if accuracies:
            avg_acc = sum(accuracies) / len(accuracies)
            bpm_sub_parts.append(f"BPM Match: {avg_acc:.1f}%")

        bpm_sub = "<br>".join(bpm_sub_parts)

        self.summary_bpm_card.set_data(
            f"{ur_val:.1f} UR",
            bpm_sub,
            "info"
        )

        qual_val = summary["weighted_quality"]
        if qual_val >= 80:
            qual_meaning = "Clean output, settings are dialled in."
        elif qual_val >= 55:
            qual_meaning = "Controlled output, reliable advice possible."
        else:
            qual_meaning = "Misfires detected — data is messy. (SETTINGS LIKELY TOO SENSITIVE)"
        self.summary_quality_card.set_data(
            summary["session_quality"],
            qual_meaning,
            q_stat
        )

        conf = summary["confidence"]
        self.summary_confidence_card.set_data(
            conf,
            "Judgment is based on your mechanical consistency.",
            "good" if conf == "High" else ("warn" if conf == "Medium" else "bad")
        )

        self.summary_recommendation_card.set_data(
            summary["recommendation_text"],
            summary["recommendation_reason"],
            summary["recommendation_status"]
        )
        self.summary_press_card.set_data(
            summary["press_suggestion"],
            summary.get("press_reason", ""),
            summary["recommendation_status"]
        )
        self.summary_release_card.set_data(
            summary["release_suggestion"],
            summary.get("release_reason", ""),
            summary["recommendation_status"]
        )
        self.summary_base_card.set_data(
            summary["base_suggestion"],
            summary.get("base_reason", ""),
            summary["recommendation_status"]
        )

        meta_parts = []
        if abs(summary["weighted_drift"]) >= 4:
            direction = "slowing down" if summary["weighted_drift"] > 0 else "speeding up"
            meta_parts.append(f"You were {direction} as the test went on")
        if summary["gallop_bias"] >= 12:
            slow = summary.get("slow_key")
            label = slow.upper().strip("<>") if slow else "One finger"
            meta_parts.append(f"{label} is consistently lagging behind")
        if summary["overtrigger_score"] >= 12:
            meta_parts.append("Hardware misfires detected (settings too sensitive)")
        meta_text = " • ".join(meta_parts) if meta_parts else "No mechanical issues spotted in the details."

        self.summary_phase_note_card.set_data(
            summary["plain_english"],
            meta_text,
            "info"
        )

        tip_text = summary.get("technique_tip", "")
        if tip_text:
            self.summary_tip_card.set_data(
                "💡 Try this",
                tip_text,
                "coach"
            )
        else:
            self.summary_tip_card.set_data(
                "-",
                "No specific tip for this run.",
                "neutral"
            )

    def render_graph(self, results: List[PhaseResult]):
        self.graph.clear()
        self.graph_data_points = []

        colours = ["#5865F2", "#FEE75C", "#57F287"]
        start_x = 1
        all_ints = []

        bound_k1_display = self.key1_display_input.text().strip().upper() or "K1"
        bound_k2_display = self.key2_display_input.text().strip().upper() or "K2"
        bound_k1_raw = self._bound_key1_raw
        bound_k2_raw = self._bound_key2_raw

        for idx, r in enumerate(results):
            x = list(range(start_x, start_x + len(r.intervals_ms)))
            all_ints.extend(r.intervals_ms)

            brush = QColor(colours[idx % 3])
            brush.setAlpha(40)

            self.graph.plot(
                x,
                r.intervals_ms,
                pen=pg.mkPen(colours[idx % 3], width=2),
                symbol="o",
                symbolSize=7,
                symbolBrush=colours[idx % 3],
                symbolPen=pg.mkPen("#111214", width=1),
                name=r.name,
                fillLevel=0,
                fillBrush=brush
            )

            self.graph.addItem(
                pg.InfiniteLine(
                    pos=r.avg_interval,
                    angle=0,
                    pen=pg.mkPen(colours[idx % 3], width=1.5, style=Qt.DashLine)
                )
            )

            for i in range(len(x)):
                raw_k = r.keys[i] if i < len(r.keys) else "?"
                display_k = raw_k
                if raw_k == bound_k1_raw:
                    display_k = bound_k1_display
                elif raw_k == bound_k2_raw:
                    display_k = bound_k2_display

                self.graph_data_points.append({
                    "x": x[i],
                    "y": r.intervals_ms[i],
                    "key": display_k,
                    "phase": r.name,
                })

            start_x += len(r.intervals_ms) + 2

        if all_ints:
            y_min = max(0, min(all_ints) - 10)
            y_max = max(all_ints) + 15
            self.graph.setYRange(y_min, y_max, padding=0)

        self.setup_graph_interaction_items()

    def on_mouse_moved(self, pos):
        if not self.graph_data_points:
            self.hide_graph_interaction_items()
            return

        if not self.graph.sceneBoundingRect().contains(pos):
            self.hide_graph_interaction_items()
            return

        mouse_point = self.graph.plotItem.vb.mapSceneToView(pos)
        x_mouse = mouse_point.x()
        y_mouse = mouse_point.y()

        closest_point = None
        min_dist = float("inf")

        for pt in self.graph_data_points:
            dist = ((pt["x"] - x_mouse) ** 2 + ((pt["y"] - y_mouse) / 8.0) ** 2) ** 0.5
            if dist < min_dist:
                min_dist = dist
                closest_point = pt

        if closest_point is None or min_dist > 3.0:
            self.hide_graph_interaction_items()
            return

        self.vLine.setPos(closest_point["x"])
        self.hLine.setPos(closest_point["y"])
        self.vLine.show()
        self.hLine.show()

        key_disp = closest_point["key"].upper().strip("<>")
        html = (
            "<div style='padding:6px;'>"
            f"<b>Phase:</b> {closest_point['phase']}<br>"
            f"<b>Tap:</b> {closest_point['x']}<br>"
            f"<b>Interval:</b> {closest_point['y']:.2f} ms<br>"
            f"<b>Key:</b> {key_disp}"
            "</div>"
        )
        self.tooltip_text.setHtml(html)
        self.tooltip_text.setPos(closest_point["x"], closest_point["y"])
        self.tooltip_text.show()

    def render_detailed_text(self, results: List[PhaseResult], summary: Dict):
        bound_k1_display = self.key1_display_input.text().strip().upper() or "K1"
        bound_k2_display = self.key2_display_input.text().strip().upper() or "K2"

        lines = []
        for r in results:
            lines.extend([
                f"[{r.name.upper()}]",
                f"• UR (Unstable Rate): {r.ur:.2f}",
            ])

            if r.target_bpm:
                lines.append(f"• Target BPM: {r.target_bpm} (BPM Match: {r.bpm_accuracy:.1f}%)")

            lines.extend([
                f"• Gallop Bias Peak: {r.gallop_bias:.2f} ms",
                f"• Total Hits: {r.press_count} ({bound_k1_display}: {r.key1_count} | {bound_k2_display}: {r.key2_count})",
                f"• Avg Interval: {r.avg_interval:.2f} ms",
                f"• Median Interval: {r.median_interval:.2f} ms",
                f"• Std Dev: {r.stddev_interval:.2f}",
                f"• Consistency: {r.consistency_score:.1f}%",
                f"• Drift (regression): {r.drift_ms:.2f} ms total ({r.drift_slope:+.3f} ms/tap)",
                f"• Avg Hold Time: {r.avg_hold_time:.2f} ms",
                f"• Avg Release Gap: {r.avg_release_gap:.2f} ms",
                f"• Mechanical Quality Score: {r.quality_score:.1f} ({r.quality_label})",
                f"• Max Pause/Gap: {r.max_gap_seconds:.2f}s",
                f"• Anomalies: Fast Repeats={r.anomaly_counts['same_key_fast_repeats']}, "
                f"Soft Repeats={r.anomaly_counts.get('same_key_soft_repeats', 0)}, "
                f"Short Intervals={r.anomaly_counts['very_short_intervals']}, "
                f"Release Noise={r.anomaly_counts['release_noise']}, "
                f"Outliers={r.anomaly_counts['outliers']}",
                ""
            ])

        lines.extend([
            "[COMBINED SESSION SUMMARY]",
            f"• Weighted UR: {summary['weighted_ur']:.1f}",
            f"• Weighted Mechanical Quality: {summary['weighted_quality']:.1f}",
            f"• Weighted Consistency: {summary.get('weighted_consistency', 0):.1f}%",
            f"• Estimated 1/4 BPM: {summary['weighted_bpm_1_4']:.1f}",
            f"• Weighted Drift: {summary['weighted_drift']:.1f} ms (slope: {summary.get('weighted_drift_slope', 0):+.3f} ms/tap)",
            f"• Overtrigger Score: {summary['overtrigger_score']:.1f}",
            f"• Gallop Bias Peak: {summary['gallop_bias']:.1f} ms",
            f"• Primary Advice: {summary['recommendation_text']}",
            f"  -> {summary['recommendation_reason']}",
            f"• Base Actuation Suggestion (mm): {summary['base_suggestion']}",
            f"  -> {summary.get('base_reason', '')}",
            f"• RT Press Suggestion (mm): {summary['press_suggestion']}",
            f"  -> {summary.get('press_reason', '')}",
            f"• RT Release Suggestion (mm): {summary['release_suggestion']}",
            f"  -> {summary.get('release_reason', '')}",
            f"• Analysis Summary: {summary['plain_english']}",
        ])

        self.analysis_box.setPlainText("\n".join(lines))

    def closeEvent(self, event):
        try:
            self.countdown_timer.stop()
            self.listener_mgr.stop_phase()
            self.listener_mgr.stop_background()
            self.player.stop()
            self.metronome_widget.stop()
        except Exception:
            pass
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
