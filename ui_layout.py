from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QFrame, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QLineEdit, QComboBox, QSlider, QFormLayout,
    QGridLayout, QPlainTextEdit
)
import pyqtgraph as pg

from ui_components import (
    apply_shadow, MetricCard, CollapsibleSection,
    TiltedKeycapLogo, MetronomeWidget, HelpIconLabel
)

def build_main_ui(window):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    window.setCentralWidget(scroll)

    container = QWidget()
    container.setObjectName("MainContainer")
    scroll.setWidget(container)

    root = QVBoxLayout(container)
    root.setContentsMargins(30, 30, 30, 30)
    root.setSpacing(24)

    window.header_panel = QFrame()
    window.header_panel.setObjectName("HeaderPanel")
    apply_shadow(window.header_panel, blur_radius=30, y_offset=8, alpha=70)

    header_layout = QHBoxLayout(window.header_panel)
    header_layout.setContentsMargins(20, 20, 20, 20)
    header_layout.setSpacing(20)

    window.logo = TiltedKeycapLogo()
    header_layout.addWidget(window.logo)

    header_text_layout = QVBoxLayout()
    header_text_layout.setSpacing(2)
    window.title_label = QLabel("Osu! Calibrator")
    window.title_label.setObjectName("AppTitle")
    window.rayze_label = QLabel("BY RAYZE")
    window.rayze_label.setObjectName("AppSubtitle")
    window.rayze_explainer = QLabel(
        "Iterative Hall Effect calibration. Press SPACE to start/stop a phase."
    )
    window.rayze_explainer.setObjectName("ExplainerSubtitle")
    window.rayze_explainer.setWordWrap(True)
    header_text_layout.addWidget(window.title_label)
    header_text_layout.addWidget(window.rayze_label)
    header_text_layout.addWidget(window.rayze_explainer)
    header_layout.addLayout(header_text_layout)
    header_layout.addStretch()
    root.addWidget(window.header_panel)

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

    window.phase_label = QLabel("")
    window.phase_label.setObjectName("BigStatus")

    window.metronome_widget = MetronomeWidget()

    phase_header_row.addWidget(window.phase_label)
    phase_header_row.addWidget(window.metronome_widget, 0, Qt.AlignVCenter)
    phase_header_row.addStretch()

    workflow_layout.addLayout(phase_header_row)

    window.phase_description_label = QLabel("")
    window.phase_description_label.setObjectName("MutedText")
    workflow_layout.addWidget(window.phase_description_label)

    window.status_label = QLabel("Ready")
    window.status_label.setObjectName("StatusBadge")
    workflow_layout.addWidget(window.status_label)

    window.countdown_label = QLabel("Time left: -")
    window.countdown_label.setObjectName("MutedText")
    workflow_layout.addWidget(window.countdown_label)

    window.tap_count_label = QLabel("Detected presses: 0")
    window.tap_count_label.setObjectName("MutedText")
    workflow_layout.addWidget(window.tap_count_label)

    window.phase_progress_label = QLabel("Phase 1 of 3")
    window.phase_progress_label.setObjectName("MutedText")
    workflow_layout.addWidget(window.phase_progress_label)

    button_layout = QHBoxLayout()
    button_layout.setSpacing(12)

    window.start_button = QPushButton("Start Phase  (Space)")
    window.start_button.setObjectName("PrimaryButton")
    window.start_button.clicked.connect(window._on_start_button_clicked)

    window.zen_button = QPushButton("🧘 Zen Warm-up")
    window.zen_button.setCheckable(True)
    window.zen_button.clicked.connect(window.on_zen_toggled)

    window.stop_button = QPushButton("Stop  (Space)")
    window.stop_button.clicked.connect(window.stop_phase)
    window.stop_button.setEnabled(False)

    button_layout.addWidget(window.start_button, 2)
    button_layout.addWidget(window.zen_button, 1)
    button_layout.addWidget(window.stop_button, 1)

    bottom_actions = QHBoxLayout()

    window.reset_button = QPushButton("Start New Calibration")
    window.reset_button.setObjectName("SecondaryAction")
    window.reset_button.clicked.connect(window.reset_calibration)

    window.export_button = QPushButton("Copy Report")
    window.export_button.clicked.connect(window.export_to_clipboard)

    window.clear_history_button = QPushButton("Clear History")
    window.clear_history_button.clicked.connect(window.confirm_clear_history)

    bottom_actions.addWidget(window.reset_button)
    bottom_actions.addWidget(window.export_button)
    bottom_actions.addWidget(window.clear_history_button)

    workflow_layout.addLayout(button_layout)
    workflow_layout.addLayout(bottom_actions)
    main_content.addWidget(workflow_panel)

    window.summary_coaching_card = MetricCard(
        "Coaching Partner",
        "Compares runs for prescriptive tuning loops."
    )
    main_content.addWidget(window.summary_coaching_card)

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

    window.base_actuation_input = QLineEdit("")
    window.base_actuation_input.setPlaceholderText("eg 0.70")
    window.base_actuation_input.setValidator(double_val)
    polish_input(window.base_actuation_input)

    key_layout = QHBoxLayout()
    window.key1_display_input = QLineEdit("")
    window.key1_display_input.setPlaceholderText("eg Z")
    window.key1_display_input.setReadOnly(True)
    window.key2_display_input = QLineEdit("")
    window.key2_display_input.setPlaceholderText("eg X")
    window.key2_display_input.setReadOnly(True)

    window.detect_btn = QPushButton("Record Keys")
    window.detect_btn.setMinimumWidth(100)
    window.detect_btn.clicked.connect(window.start_key_detect)

    window.detect_cancel_btn = QPushButton("Cancel")
    window.detect_cancel_btn.setMinimumWidth(80)
    window.detect_cancel_btn.clicked.connect(window.cancel_key_detect_from_ui)
    window.detect_cancel_btn.setVisible(False)

    key_layout.addWidget(window.key1_display_input)
    key_layout.addWidget(window.key2_display_input)
    key_layout.addWidget(window.detect_btn)
    key_layout.addWidget(window.detect_cancel_btn)

    window.song_combo = QComboBox()
    window.song_combo.addItem("None (No Audio)", None)

    window.import_song_button = QPushButton("Import MP3")
    window.import_song_button.clicked.connect(window.import_custom_song)

    window.volume_title_label = QLabel("Volume")
    window.volume_title_label.setObjectName("MiniLabel")

    window.volume_slider = QSlider(Qt.Horizontal)
    window.volume_slider.setRange(0, 100)
    window.volume_slider.setValue(30)
    window.volume_slider.setToolTip("Audio Volume")
    window.volume_slider.setFixedWidth(130)
    window.volume_slider.valueChanged.connect(lambda v: window.audio_output.setVolume(v / 100.0))

    volume_layout = QVBoxLayout()
    volume_layout.setContentsMargins(0, 0, 0, 0)
    volume_layout.setSpacing(4)
    volume_layout.addWidget(window.volume_title_label)
    volume_layout.addWidget(window.volume_slider)

    audio_row_container = QWidget()
    audio_row_layout = QHBoxLayout(audio_row_container)
    audio_row_layout.setContentsMargins(0, 0, 0, 0)
    audio_row_layout.setSpacing(10)
    audio_row_layout.addWidget(window.song_combo, 1)
    audio_row_layout.addWidget(window.import_song_button)
    audio_row_layout.addLayout(volume_layout)

    def make_help_label(text: str, tooltip: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(text)
        label.setObjectName("MainHelpLabel")
        icon = HelpIconLabel("ⓘ", tooltip)
        layout.addWidget(label)
        layout.addWidget(icon)
        layout.addStretch()
        return container

    form.addRow(
        make_help_label("Base Actuation (mm)", "Your main current actuation point right now."),
        window.base_actuation_input
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

    window.advanced_section = CollapsibleSection("Advanced RT Settings")
    advanced_layout = QFormLayout(window.advanced_section.content)
    advanced_layout.setContentsMargins(16, 16, 16, 16)

    window.separate_sensitivity_checkbox = QCheckBox("Separate press/release sensitivity enabled")
    window.separate_sensitivity_checkbox.setChecked(True)
    window.separate_sensitivity_checkbox.toggled.connect(window.toggle_separate_sensitivity)

    window.press_activate_input = QLineEdit("")
    window.press_activate_input.setPlaceholderText("eg 0.15")
    window.press_activate_input.setValidator(double_val)
    polish_input(window.press_activate_input)

    window.release_deactivate_input = QLineEdit("")
    window.release_deactivate_input.setPlaceholderText("eg 0.15")
    window.release_deactivate_input.setValidator(double_val)
    polish_input(window.release_deactivate_input)

    window.bottom_out_force_input = QLineEdit("")
    window.bottom_out_force_input.setValidator(double_val)
    window.bottom_out_force_input.setPlaceholderText("eg 45 (Optional)")
    polish_input(window.bottom_out_force_input)

    window.press_label_container = make_help_label("Press Activate (mm)", "Downward movement needed to activate.")
    window.press_main_label = window.press_label_container.findChild(QLabel, "MainHelpLabel")

    window.release_label_container = make_help_label("Release Deactivate (mm)", "Upward movement needed to reset.")

    advanced_layout.addRow("", window.separate_sensitivity_checkbox)
    advanced_layout.addRow(window.press_label_container, window.press_activate_input)
    advanced_layout.addRow(window.release_label_container, window.release_deactivate_input)
    advanced_layout.addRow(
        make_help_label("Bottom-out Force (g)", "Heavier switches tolerate lower settings better."),
        window.bottom_out_force_input
    )

    settings_layout.addWidget(window.advanced_section)
    side_column.addWidget(settings_panel)
    side_column.addStretch()

    top_row.addLayout(side_column, 2)

    window.toggle_separate_sensitivity(window.separate_sensitivity_checkbox.isChecked())

    window.advanced_section.toggle_button.setChecked(True)
    window.advanced_section.on_toggled()

    cards = QGridLayout()
    cards.setSpacing(16)
    root.addLayout(cards)

    window.summary_bpm_card = MetricCard(
        "Tap Steadiness (UR)",
        "Lower Unstable Rate is steadier timing."
    )
    window.summary_quality_card = MetricCard(
        "Mechanical Quality",
        "How clean this run was mechanically (misfires vs clean hits)."
    )
    window.summary_confidence_card = MetricCard(
        "Analysis Confidence",
        "How sure we are about the suggestions below based on the run details."
    )
    window.summary_recommendation_card = MetricCard(
        "Calibration Advice",
        "Separates settings sensitivity advice from general skill coaching."
    )
    window.summary_press_card = MetricCard("Rapid Trigger (Press)", "Activation distance tuning — how far down the key travels before firing.")
    window.summary_release_card = MetricCard("Rapid Trigger (Release)", "Reset distance tuning — how far up the key travels before resetting.")
    window.summary_base_card = MetricCard("Base Actuation", "Global actuation point tuning suggestion.")

    window.summary_phase_note_card = MetricCard(
        "Analysis Summary",
        "Plain English summary of findings."
    )

    window.summary_tip_card = MetricCard(
        "Mechanics Tip",
        "Actionable technique advice based on what we spotted in your tapping."
    )

    cards.addWidget(window.summary_bpm_card, 0, 0)
    cards.addWidget(window.summary_quality_card, 0, 1)
    cards.addWidget(window.summary_confidence_card, 0, 2)
    cards.addWidget(window.summary_recommendation_card, 0, 3, 2, 1)

    cards.addWidget(window.summary_press_card, 1, 0)
    cards.addWidget(window.summary_release_card, 1, 1)
    cards.addWidget(window.summary_base_card, 1, 2)

    cards.addWidget(window.summary_phase_note_card, 2, 0, 1, 3)
    cards.addWidget(window.summary_tip_card, 2, 3)

    window.change_log_section = CollapsibleSection("What Changed? (Engine Reasoning)")
    cl_layout = QVBoxLayout(window.change_log_section.content)
    cl_layout.setContentsMargins(16, 16, 16, 16)
    window.change_log_label = QLabel("Complete two runs to see comparison reasoning.")
    window.change_log_label.setObjectName("RichText")
    window.change_log_label.setWordWrap(True)
    cl_layout.addWidget(window.change_log_label)
    root.addWidget(window.change_log_section)

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

    window.graph = pg.PlotWidget()
    window.graph.setMinimumHeight(300)
    window.graph.setBackground("#1a1b1e")
    window.graph.showGrid(x=True, y=True, alpha=0.15)
    window.graph.setLabel("left", "Interval (ms)")
    window.graph.setLabel("bottom", "Tap Sequence")

    axis_pen = pg.mkPen("#80848e")
    window.graph.getAxis("left").setTextPen(axis_pen)
    window.graph.getAxis("bottom").setTextPen(axis_pen)
    window.graph.getAxis("left").setPen(axis_pen)
    window.graph.getAxis("bottom").setPen(axis_pen)

    graph_layout.addWidget(window.graph)
    root.addWidget(graph_panel)

    window.graph.scene().sigMouseMoved.connect(window.on_mouse_moved)
    window.setup_graph_interaction_items()

    details_section = CollapsibleSection("Raw Analysis Data")
    details_layout = QVBoxLayout(details_section.content)
    details_layout.setContentsMargins(16, 16, 16, 16)

    window.analysis_box = QPlainTextEdit()
    window.analysis_box.setReadOnly(True)
    window.analysis_box.setMinimumHeight(200)
    details_layout.addWidget(window.analysis_box)

    root.addWidget(details_section)
    window.clear_summary_cards()

def apply_app_styles(window):
    window.setStyleSheet("""
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