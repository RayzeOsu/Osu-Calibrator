"""
OSU! CALIBRATOR by Rayze
Hall Effect keyboard calibration tool with iterative tuning recommendations.
"""

import sys
import math
import statistics
import threading
import time
import json
import os
import shutil
import tempfile
import re

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

from pynput import keyboard
from PySide6.QtCore import QTimer, Qt, QSize, QPointF, QObject, Signal, QEvent, QUrl
from PySide6.QtGui import (
    QDoubleValidator,
    QCursor,
    QPainter,
    QColor,
    QPen,
    QBrush,
    QPolygonF,
    QShortcut,
    QKeySequence,
    QIcon,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QToolTip,
    QGraphicsDropShadowEffect,
    QSlider,
    QInputDialog,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
import pyqtgraph as pg

HISTORY_SCHEMA_VERSION = 1
APP_NAME = "RayzeCalibration"

def get_resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative_path)

def _is_writable_dir(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        test_file = os.path.join(path, f".write_test_{os.getpid()}")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except OSError:
        return False

def get_persistent_dir() -> str:
    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(os.path.join(local_app_data, APP_NAME))

    home = os.path.expanduser("~")
    if home and home != "~":
        candidates.append(os.path.join(home, ".local", "share", APP_NAME))
        candidates.append(os.path.join(home, f".{APP_NAME}"))

    candidates.append(os.path.join(tempfile.gettempdir(), APP_NAME))

    for candidate in candidates:
        try:
            os.makedirs(candidate, exist_ok=True)
            if _is_writable_dir(candidate):
                return candidate
        except OSError:
            continue

    return os.path.join(tempfile.gettempdir(), APP_NAME)

def get_persistent_songs_dir() -> str:
    return os.path.join(get_persistent_dir(), "songs")

def ensure_persistent_songs() -> None:
    persistent_songs_dir = get_persistent_songs_dir()
    os.makedirs(persistent_songs_dir, exist_ok=True)

    if hasattr(sys, "_MEIPASS"):
        bundled_songs_dir = os.path.join(sys._MEIPASS, "songs")
        if os.path.exists(bundled_songs_dir):
            for file_name in os.listdir(bundled_songs_dir):
                if file_name.lower().endswith(".mp3"):
                    src = os.path.join(bundled_songs_dir, file_name)
                    dst = os.path.join(persistent_songs_dir, file_name)
                    if not os.path.exists(dst):
                        try:
                            shutil.copy2(src, dst)
                        except OSError as e:
                            print(f"Failed to copy bundled song '{file_name}': {e}")
    else:
        dev_songs_dir = get_resource_path("songs")
        if os.path.exists(dev_songs_dir):
            for file_name in os.listdir(dev_songs_dir):
                if file_name.lower().endswith(".mp3"):
                    src = os.path.join(dev_songs_dir, file_name)
                    dst = os.path.join(persistent_songs_dir, file_name)
                    if not os.path.exists(dst):
                        try:
                            shutil.copy2(src, dst)
                        except OSError as e:
                            print(f"Failed to sync dev song '{file_name}': {e}")

HISTORY_FILE = os.path.join(get_persistent_dir(), "calibration_history.json")
HISTORY_BACKUP = os.path.join(get_persistent_dir(), "calibration_history.backup.json")
MAX_HISTORY_ENTRIES = 5
MIN_PRESSES_FOR_ANALYSIS = 8
WARMUP_TAPS_TO_DISCARD = 2
INTERVAL_TO_TIMING_STDEV = math.sqrt(2)

RECOMMENDATION_THRESHOLDS = {
    "min_quality_for_any_recommendation": 40,
    "min_quality_for_keep": 78,
    "min_quality_elite": 82,
    "min_quality_high_confidence": 70,
    "ur_elite_max": 70,
    "ur_excellent_max": 95,
    "ur_good_max": 120,
    "ur_decent_max": 165,
    "ur_high_min": 165,
    "ur_very_high_min": 200,
    "keep_ur_min": 70,
    "keep_ur_max": 115,
    "keep_consistency_min": 88,
    "keep_drift_max": 4.0,
    "keep_gallop_max": 10,
    "severe_overtrigger_score": 35,
    "moderate_overtrigger_score": 12,
    "fast_repeat_severe": 2,
    "release_noise_severe": 2,
    "drift_significant_ms": 6.0,
    "gallop_significant_ms": 15,
    "gallop_concerning_ms": 12,
    "min_base_actuation": 0.10,
    "max_base_actuation": 4.00,
    "max_phase_gap_seconds": 2.0,
}

TECHNIQUE_TIPS = {
    "gallop_bias": [
        "Try tilting your keyboard slightly so the lagging finger travels a shorter distance. Many players angle their board 10-20 degrees.",
        "Check your wrist position. If one finger is always late, your wrist may be leaning toward the faster finger — try centering it between both keys.",
        "Practice slow alternation drills at 150-160 BPM focusing on equal finger pressure. Don't try to go fast until both fingers feel identical.",
    ],
    "soft_repeats": [
        "You're hovering too close to the keys. After each tap, lift your finger a few millimeters higher before pressing again — this gives the switch time to reset.",
        "Your fingers are 'mashing' rather than tapping. Try consciously relaxing your hand and using smaller, crisper finger movements.",
        "Bouncing on the same key usually means your settings are too sensitive OR your fingers aren't lifting enough. Both are fixable with practice.",
    ],
    "fast_repeats": [
        "Double-firing on the same key is almost always hardware, not you. Increase your Rapid Trigger distances to give the switch time to fully reset.",
        "If your settings are already reasonable and you're still double-firing, check if your keycap is catching on the switch housing — try removing and reseating it.",
    ],
    "release_noise": [
        "Your fingers are sitting on the keys without fully releasing. Focus on lifting each finger completely after every tap.",
        "Some players find their fingernails are brushing the keys during release — trim them if they've grown out, or try a slightly different finger angle.",
    ],
    "outliers": [
        "Your rhythm is breaking up with sudden fast or slow taps. Play burst-control maps at 20 BPM below your comfort zone to rebuild steady timing.",
        "Rhythm instability often comes from tension. Drop your shoulders, unclench your jaw, and try again — you'd be surprised how much it helps.",
    ],
    "drift_slowing": [
        "You're fatiguing partway through. Build stamina with longer streams (60+ seconds) at a comfortable speed before pushing your limits.",
        "Slowing down mid-stream can mean your fingers are tensing up. Try relaxing your ring and pinky fingers consciously — they often tense by sympathy.",
    ],
    "drift_speeding": [
        "Speeding up mid-stream usually means you're riding adrenaline past your actual control. Practice holding a steady BPM for the full duration rather than chasing speed.",
        "This can also indicate you started too cautiously. Warm up with a few short bursts before starting the test to find your comfort speed faster.",
    ],
    "key_imbalance": [
        "One finger is doing more work than the other. If you're single-tapping instead of alternating, now's the time to practice alternation at a lower BPM.",
        "Uneven key counts can mean one finger is skipping taps. Slow down, focus on clean alternation, and build speed from there.",
    ],
    "high_ur_no_issue": [
        "Your settings are fine — your timing just isn't steady yet. Play 4-5 BPM below your current comfort speed for 10 minutes to lock in a rhythm.",
        "High UR with clean mechanics usually means stamina or focus. Take a short break, warm up your hands, and try again fresh.",
        "Try playing simpler maps at a lower star rating for a while. Stream consistency comes from repetition at speeds you fully control.",
    ],
    "elite": [
        "You're dialled in. To push further, try increasing your comfort BPM by 5 at a time rather than chasing settings changes.",
        "At this level, technique refinements matter more than hardware. Record your hands tapping and look for small inefficiencies.",
    ],
    "default": [
        "Keep your hand relaxed — tension is the biggest enemy of consistent tapping.",
        "Warm up for 3-5 minutes with easy streams before attempting your comfort speed. Cold fingers are slow fingers.",
    ],
}

QUALITY_PENALTIES = {
    "press_count_low": 18,
    "press_count_med": 12,
    "consistency_low": 15,
    "consistency_med": 12,
    "spread_high": 15,
    "spread_med": 10,
    "key_imbalance_severe": 12,
    "key_imbalance_mild": 6,
    "fast_repeat_each": 10,
    "soft_repeat_each": 6,
    "short_interval_each": 8,
    "release_noise_each": 15,
    "outlier_max": 16,
    "outlier_per": 2,
    "gallop_severe": 8,
    "gallop_mild": 4,
}

@dataclass
class PhaseConfig:
    name: str
    description: str
    duration: int
    weight: float

@dataclass
class PhaseResult:
    name: str
    press_count: int
    intervals_ms: List[float]
    keys: List[str]
    avg_interval: float
    median_interval: float
    stddev_interval: float
    ur: float
    tap_rate: float
    bpm_1_4: float
    bpm_1_6: float
    bpm_1_8: float
    consistency_score: float
    key1_count: int
    key2_count: int
    avg_hold_time: float
    avg_release_gap: float
    anomaly_counts: Dict[str, int]
    gallop_bias: float
    slow_key: Optional[str]
    drift_ms: float
    drift_slope: float
    quality_score: float
    quality_label: str
    max_gap_seconds: float
    target_bpm: Optional[int] = None
    bpm_accuracy: Optional[float] = None

@dataclass
class CalibrationSession:
    settings: Dict[str, float]
    summary: Dict

class HistoryStore:
    def __init__(self, path: str = HISTORY_FILE, backup_path: str = HISTORY_BACKUP):
        self.path = path
        self.backup_path = backup_path
        self.sessions: List[CalibrationSession] = []
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and "schema_version" in data:
                file_version = data.get("schema_version", 0)
                sessions_data = data.get("sessions", [])
                if file_version > HISTORY_SCHEMA_VERSION:
                    print(f"History file is newer version ({file_version}) than this app supports ({HISTORY_SCHEMA_VERSION}). Loading read-only.")
            elif isinstance(data, list):
                sessions_data = data
            else:
                raise ValueError("History file has unknown structure")

            self.sessions = []
            for item in sessions_data:
                if not isinstance(item, dict):
                    continue
                self.sessions.append(
                    CalibrationSession(
                        settings=item.get("settings", {}),
                        summary=item.get("summary", {}),
                    )
                )
        except (json.JSONDecodeError, ValueError, OSError) as e:
            print(f"History file corrupted ({e}). Backing up.")
            try:
                shutil.copy2(self.path, self.backup_path)
            except OSError:
                pass
            self.sessions = []

    def save(self) -> None:
        try:
            recent = self.sessions[-MAX_HISTORY_ENTRIES:]
            payload = {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "app_name": APP_NAME,
                "sessions": [asdict(s) for s in recent],
            }

            dir_name = os.path.dirname(os.path.abspath(self.path)) or "."
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=dir_name,
                suffix=".tmp",
            ) as tmp:
                json.dump(payload, tmp, indent=2)
                tmp_path = tmp.name

            os.replace(tmp_path, self.path)
        except OSError as e:
            print(f"Failed to save history: {e}")

    def append(self, session: CalibrationSession) -> None:
        self.sessions.append(session)
        self.save()

    def clear(self) -> None:
        self.sessions = []
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError as e:
            print(f"Failed to delete history file: {e}")

    def latest(self) -> Optional[CalibrationSession]:
        return self.sessions[-1] if self.sessions else None

    def previous(self) -> Optional[CalibrationSession]:
        return self.sessions[-2] if len(self.sessions) >= 2 else None

class AnalysisEngine:
    @staticmethod
    def extract_key_name(key) -> Optional[str]:
        try:
            if hasattr(key, "char") and key.char:
                return str(key.char).lower()
            if hasattr(key, "name") and key.name:
                return f"<{key.name.lower()}>"
        except Exception:
            pass
        return None

    @staticmethod
    def analyse_phase(
        events: List[Dict],
        cfg: PhaseConfig,
        k1: str,
        k2: str,
        target_bpm: Optional[int] = None,
    ) -> Tuple[Optional[PhaseResult], Optional[str]]:
        presses = [e for e in events if e["type"] == "press"]
        if len(presses) < MIN_PRESSES_FOR_ANALYSIS:
            return None, f"Only {len(presses)} presses captured. Make sure you are alternating evenly without pausing (need at least {MIN_PRESSES_FOR_ANALYSIS} presses)."

        if len(presses) > MIN_PRESSES_FOR_ANALYSIS + WARMUP_TAPS_TO_DISCARD:
            presses = presses[WARMUP_TAPS_TO_DISCARD:]

        if presses:
            first_valid_time = presses[0]["time"]
            valid_events = [e for e in events if e["time"] >= first_valid_time]
        else:
            valid_events = events

        ints = []
        keys = []
        for i in range(1, len(presses)):
            delta = (presses[i]["time"] - presses[i - 1]["time"]) * 1000.0
            ints.append(delta)
            keys.append(presses[i]["key"])

        if len(ints) < 4:
            return None, f"Only {len(ints)} usable intervals captured. Ensure you are tapping both keys fully and consistently."

        max_gap = max(ints) / 1000.0

        med_raw = statistics.median(ints)
        clean_ints = [x for x in ints if med_raw * 0.2 < x < med_raw * 3.0]
        if len(clean_ints) < 4:
            clean_ints = ints

        avg_i = statistics.mean(clean_ints)
        med_i = statistics.median(clean_ints)
        std = statistics.stdev(clean_ints) if len(clean_ints) > 1 else 0.0

        ur = (std / INTERVAL_TO_TIMING_STDEV) * 10.0

        bpm_4 = 15000.0 / avg_i if avg_i > 0 else 0.0
        cons = max(0.0, 100.0 - ((std / avg_i) * 100.0)) if avg_i > 0 else 0.0

        key1_presses = sum(1 for p in presses if p["key"] == k1)
        key2_presses = sum(1 for p in presses if p["key"] == k2)

        k1_k2 = []
        k2_k1 = []
        for i in range(1, len(presses)):
            d = (presses[i]["time"] - presses[i - 1]["time"]) * 1000.0
            if presses[i - 1]["key"] == k1 and presses[i]["key"] == k2:
                k1_k2.append(d)
            elif presses[i - 1]["key"] == k2 and presses[i]["key"] == k1:
                k2_k1.append(d)

        bias = abs(statistics.mean(k1_k2) - statistics.mean(k2_k1)) if k1_k2 and k2_k1 else 0.0

        slow_key: Optional[str] = None
        if k1_k2 and k2_k1 and bias >= 8:
            if statistics.mean(k1_k2) > statistics.mean(k2_k1):
                slow_key = k2
            else:
                slow_key = k1

        avg_h, avg_g = AnalysisEngine.calculate_release_stats(valid_events)
        anoms = AnalysisEngine.detect_anomalies(ints, presses, avg_h, avg_g)

        drift_slope, drift_endpoint = AnalysisEngine.calculate_drift_regression(ints)

        qual, q_lab = AnalysisEngine.calculate_phase_quality(
            len(presses), avg_i, std, cons, key1_presses, key2_presses, anoms, bias
        )

        bpm_accuracy = None
        if target_bpm and target_bpm > 0:
            diff = abs(target_bpm - bpm_4)
            bpm_accuracy = max(0.0, 100.0 - ((diff / target_bpm) * 100.0))

        result = PhaseResult(
            name=cfg.name,
            press_count=len(presses),
            intervals_ms=ints,
            keys=keys,
            avg_interval=avg_i,
            median_interval=med_i,
            stddev_interval=std,
            ur=ur,
            tap_rate=1000 / avg_i if avg_i > 0 else 0,
            bpm_1_4=bpm_4,
            bpm_1_6=bpm_4 * (2 / 3),
            bpm_1_8=bpm_4 * 0.5,
            consistency_score=cons,
            key1_count=key1_presses,
            key2_count=key2_presses,
            avg_hold_time=avg_h,
            avg_release_gap=avg_g,
            anomaly_counts=anoms,
            gallop_bias=bias,
            slow_key=slow_key,
            drift_ms=drift_endpoint,
            drift_slope=drift_slope,
            quality_score=qual,
            quality_label=q_lab,
            max_gap_seconds=max_gap,
            target_bpm=target_bpm,
            bpm_accuracy=bpm_accuracy,
        )
        return result, None

    @staticmethod
    def calculate_release_stats(events: List[Dict]) -> Tuple[float, float]:
        holds = []
        gaps = []
        last_p = {}
        last_r = {}

        for e in events:
            k = e["key"]
            t = e["time"]
            if e["type"] == "press":
                if k in last_r:
                    gaps.append((t - last_r[k]) * 1000.0)
                last_p[k] = t
            else:
                if k in last_p:
                    holds.append((t - last_p[k]) * 1000.0)
                last_r[k] = t

        return (
            statistics.mean(holds) if holds else 0.0,
            statistics.mean(gaps) if gaps else 0.0,
        )

    @staticmethod
    def detect_anomalies(
        ints: List[float],
        presses: List[Dict],
        avg_h: float,
        avg_g: float,
    ) -> Dict[str, int]:
        avg = statistics.mean(ints)
        std = statistics.stdev(ints) if len(ints) > 1 else 0.0

        fast_repeats = sum(
            1
            for i in range(1, len(presses))
            if presses[i]["key"] == presses[i - 1]["key"]
            and (presses[i]["time"] - presses[i - 1]["time"]) * 1000.0 < 35
        )

        soft_repeats = sum(
            1
            for i in range(1, len(presses))
            if presses[i]["key"] == presses[i - 1]["key"]
            and (presses[i]["time"] - presses[i - 1]["time"]) * 1000.0 < avg * 0.6
        )

        short_ints = sum(1 for x in ints if x < 35)

        if std > 0:
            outliers = sum(1 for x in ints if x < avg - (2 * std) or x > avg + (2 * std))
        else:
            outliers = 0

        release_noise = (1 if 0 < avg_h < 10 else 0) + (1 if 0 < avg_g < 10 else 0)

        return {
            "same_key_fast_repeats": fast_repeats,
            "same_key_soft_repeats": soft_repeats,
            "very_short_intervals": short_ints,
            "outliers": outliers,
            "release_noise": release_noise,
        }

    @staticmethod
    def calculate_drift_regression(ints: List[float]) -> Tuple[float, float]:
        n = len(ints)
        if n < 8:
            return 0.0, 0.0

        x_mean = (n - 1) / 2.0
        y_mean = sum(ints) / n

        num = sum((i - x_mean) * (ints[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))

        slope = num / den if den > 0 else 0.0
        endpoint_diff = slope * (n - 1)

        return slope, endpoint_diff

    @staticmethod
    def calculate_phase_quality(
        cnt: int,
        avg: float,
        std: float,
        cons: float,
        k1c: int,
        k2c: int,
        anoms: Dict[str, int],
        gallop_bias: float = 0.0,
    ) -> Tuple[float, str]:
        p = QUALITY_PENALTIES
        qual = 100.0

        norm_factor = 100.0 / max(1, cnt)

        if cnt < 20:
            qual -= p["press_count_low"]
        elif cnt < 35:
            qual -= p["press_count_med"]

        if cons < 68:
            qual -= p["consistency_low"]
        elif cons < 82:
            qual -= p["consistency_med"]

        spread = std / avg if avg > 0 else 0
        if spread > 0.18:
            qual -= p["spread_high"]
        elif spread > 0.12:
            qual -= p["spread_med"]

        total_keys = k1c + k2c
        if total_keys > 0 and max(k1c, k2c) > 0:
            balance_ratio = min(k1c, k2c) / max(k1c, k2c)
            if balance_ratio < 0.6:
                qual -= p["key_imbalance_severe"]
            elif balance_ratio < 0.75:
                qual -= p["key_imbalance_mild"]

        qual -= (anoms["same_key_fast_repeats"] * norm_factor) * p["fast_repeat_each"]
        qual -= (anoms.get("same_key_soft_repeats", 0) * norm_factor) * p["soft_repeat_each"]
        qual -= (anoms["very_short_intervals"] * norm_factor) * p["short_interval_each"]
        qual -= (anoms["release_noise"] * norm_factor) * p["release_noise_each"]
        qual -= min(p["outlier_max"], (anoms["outliers"] * norm_factor) * p["outlier_per"])

        if gallop_bias > 20:
            qual -= p["gallop_severe"]
        elif gallop_bias > 12:
            qual -= p["gallop_mild"]

        qual = max(0.0, qual)

        if qual >= 80:
            label = "Good"
        elif qual >= 55:
            label = "Usable"
        else:
            label = "Poor"

        return qual, label

class RecommendationEngine:
    @staticmethod
    def pick_technique_tip(
        w_ur: float,
        w_qual: float,
        w_drift: float,
        gallop: float,
        total_noise: int,
        total_fast: int,
        total_soft: int,
        total_short: int,
        total_outliers: int,
        key1_count: int,
        key2_count: int,
    ) -> str:
        import random as _rnd
        rng = _rnd.Random(int((w_ur + gallop * 10) * 100))

        def pick(key: str) -> str:
            tips = TECHNIQUE_TIPS.get(key, TECHNIQUE_TIPS["default"])
            return rng.choice(tips)

        if total_fast >= 1:
            return pick("fast_repeats")
        if total_noise >= 1:
            return pick("release_noise")
        if total_soft >= 2:
            return pick("soft_repeats")

        total = key1_count + key2_count
        if total > 0:
            balance = min(key1_count, key2_count) / max(key1_count, key2_count)
            if balance < 0.7:
                return pick("key_imbalance")

        if gallop >= RECOMMENDATION_THRESHOLDS["gallop_significant_ms"]:
            return pick("gallop_bias")

        if total_outliers > 4:
            return pick("outliers")

        if abs(w_drift) >= RECOMMENDATION_THRESHOLDS["drift_significant_ms"]:
            if w_drift > 0:
                return pick("drift_slowing")
            else:
                return pick("drift_speeding")

        if w_ur > RECOMMENDATION_THRESHOLDS["ur_good_max"]:
            return pick("high_ur_no_issue")

        if w_ur < RECOMMENDATION_THRESHOLDS["ur_elite_max"] and w_qual >= 80:
            return pick("elite")

        return pick("default")

    @staticmethod
    def validate_settings(
        base: float,
        press: float,
        release: float,
        force: float,
        sep_on: bool,
    ) -> List[str]:
        warnings = []

        if base <= 0:
            warnings.append("Base actuation must be greater than 0.")
        if base > RECOMMENDATION_THRESHOLDS["max_base_actuation"]:
            warnings.append(f"Base actuation {base:.2f} mm seems unusually deep.")

        if sep_on:
            if press <= 0 or release <= 0:
                warnings.append("Press and release must both be set when separate sensitivity is on.")
            if press + release > base:
                warnings.append(
                    f"Press ({press:.2f}) + Release ({release:.2f}) = {press + release:.2f} mm "
                    f"exceeds Base ({base:.2f} mm). Rapid Trigger may behave erratically — "
                    "consider raising base or lowering press/release."
                )
            if press < 0.05:
                warnings.append(f"Press activate {press:.2f} mm is extremely shallow and likely to bounce.")
            if release < 0.05:
                warnings.append(f"Release deactivate {release:.2f} mm is extremely shallow and likely to bounce.")

        if force > 0:
            if force < 20:
                warnings.append(f"Bottom-out force {force}g is extremely light. Settings will need to be very deep to avoid misfires.")
            elif force > 100:
                warnings.append(f"Bottom-out force {force}g is extremely heavy. Fatigue may severely impact your results.")

        return warnings

    @staticmethod
    def build_summary(
        results: List[PhaseResult],
        phase_configs: List[PhaseConfig],
        curr_base: float,
        curr_press: float,
        curr_rel: float,
        curr_force: float,
        sep_on: bool,
    ) -> Dict:
        t = RECOMMENDATION_THRESHOLDS

        weights = [cfg.weight for cfg in phase_configs][:len(results)]
        total_w = sum(weights) if weights else 1.0

        w_ur = sum(r.ur * w for r, w in zip(results, weights)) / total_w
        w_qual = sum(r.quality_score * w for r, w in zip(results, weights)) / total_w
        w_b14 = sum(r.bpm_1_4 * w for r, w in zip(results, weights)) / total_w
        w_drift = sum(r.drift_ms * w for r, w in zip(results, weights)) / total_w
        w_drift_slope = sum(r.drift_slope * w for r, w in zip(results, weights)) / total_w
        w_cons = sum(r.consistency_score * w for r, w in zip(results, weights)) / total_w

        total_noise = sum(r.anomaly_counts["release_noise"] for r in results)
        total_fast = sum(r.anomaly_counts["same_key_fast_repeats"] for r in results)
        total_soft = sum(r.anomaly_counts.get("same_key_soft_repeats", 0) for r in results)
        total_short = sum(r.anomaly_counts["very_short_intervals"] for r in results)
        total_outliers = sum(r.anomaly_counts["outliers"] for r in results)
        gallop = max((r.gallop_bias for r in results), default=0.0)
        max_gap = max((r.max_gap_seconds for r in results), default=0.0)

        total_presses = sum(r.press_count for r in results)
        norm_factor = 100.0 / max(1, total_presses)

        slow_key_votes: Dict[str, int] = {}
        for r in results:
            if r.slow_key:
                slow_key_votes[r.slow_key] = slow_key_votes.get(r.slow_key, 0) + 1
        dominant_slow_key: Optional[str] = None
        if slow_key_votes:
            dominant_slow_key = max(slow_key_votes.items(), key=lambda kv: kv[1])[0]

        def slow_key_label() -> str:
            if not dominant_slow_key:
                return "one finger"
            k = dominant_slow_key
            if k.startswith("<") and k.endswith(">"):
                return k.strip("<>").capitalize()
            return f"the {k.upper()} key"

        noise_weight = 14
        fast_weight = 20
        soft_weight = 8
        short_weight = 10

        if curr_force > 0:
            if curr_force >= 60:
                noise_weight = 8
            elif curr_force <= 40:
                fast_weight = 26
                soft_weight = 12
                short_weight = 14

        over_score = (
            (total_noise * norm_factor) * noise_weight
            + (total_fast * norm_factor) * fast_weight
            + (total_soft * norm_factor) * soft_weight
            + (total_short * norm_factor) * short_weight
            + min(15, (total_outliers * norm_factor) * 2)
        )
        if gallop > t["gallop_concerning_ms"]:
            over_score += 10

        def clamp_mm(v: float) -> float:
            return max(t["min_base_actuation"], round(min(t["max_base_actuation"], v), 2))

        def one_value(v: float) -> str:
            return f"Try {clamp_mm(v):.2f} mm"

        def range_value(lo: float, hi: float) -> str:
            lo, hi = clamp_mm(lo), clamp_mm(hi)
            if hi < lo:
                lo, hi = hi, lo
            return f"Try {lo:.2f} - {hi:.2f} mm"

        def na_reason() -> str:
            return "Enable Separate Sensitivity in Advanced Settings to tune this."

        urs = [r.ur for r in results]
        ur_spread = max(urs) - min(urs) if urs else 0

        if len(results) == 3 and w_qual >= t["min_quality_high_confidence"] and ur_spread < 30:
            conf = "High"
        elif len(results) >= 2 and ur_spread < 60:
            conf = "Medium"
        else:
            conf = "Low"

        rec = "Practice is the best path"
        reason = "Your settings are fine, focus on practice for cleaner taps."
        status = "coach"
        bs = "Keep as is"
        ps = "Keep as is" if sep_on else "N/A"
        rs = "Keep as is" if sep_on else "N/A"
        bs_reason = "No change needed."
        ps_reason = "No change needed." if sep_on else na_reason()
        rs_reason = "No change needed." if sep_on else na_reason()
        plain = "Your settings are configured cleanly. Focus on standard practice to improve your Unstable Rate."
        decided = False

        if w_qual < t["min_quality_for_any_recommendation"]:
            rec = "Retry with cleaner alternation"
            reason = "We need cleaner data. Try the test again focusing on even, direct taps."
            status = "warn"
            plain = (
                "We couldn't get consistent data this time. Take a breath, "
                "tap evenly between both keys, and try again."
            )
            bs = "No reliable suggestion yet"
            ps = "No reliable suggestion yet" if sep_on else "N/A"
            rs = "No reliable suggestion yet" if sep_on else "N/A"
            bs_reason = "Need cleaner data before suggesting changes."
            ps_reason = "Need cleaner data before suggesting changes." if sep_on else na_reason()
            rs_reason = "Need cleaner data before suggesting changes." if sep_on else na_reason()
            decided = True

        if not decided and (
            over_score >= t["severe_overtrigger_score"]
            or total_fast >= t["fast_repeat_severe"]
            or total_noise >= t["release_noise_severe"]
        ):
            severity = min(1.0, over_score / 60.0)
            base_raise = 0.08 + severity * 0.15
            press_raise = 0.03 + severity * 0.06
            rel_raise = 0.03 + severity * 0.06

            rec = "Increase your actuation depth"
            reason = (
                "Your keyboard is misfiring (double presses or chaotic inputs). "
                "Deeper settings are required for stability."
            )
            status = "bad"
            bs = range_value(curr_base + base_raise * 0.7, curr_base + base_raise)
            bs_reason = "Push the activation point deeper to prevent accidental touches."
            if sep_on:
                ps = one_value(curr_press + press_raise)
                ps_reason = "Increased press distance fixes double-firing keys."
                rs = one_value(curr_rel + rel_raise)
                rs_reason = "Increased release distance ensures keys reset fully."

            if curr_force > 0 and curr_force <= 40:
                plain = (
                    f"Because you are using light {curr_force:g}g switches, they are highly prone to accidental key activations. "
                    "Apply the deeper settings above to gain mechanical control over them."
                )
            elif curr_force >= 60 and total_noise > 0:
                plain = (
                    f"Your heavy {curr_force:g}g switches push back aggressively, which causes release noise. "
                    "Apply the settings above to prevent the keys from chaotic resetting."
                )
            else:
                plain = (
                    "Your keyboard is way too sensitive and is registering unwanted key activations. "
                    "Apply the settings above to get proper control back."
                )
            decided = True

        if not decided and w_ur < t["ur_excellent_max"] and w_qual >= t["min_quality_elite"] and total_noise == 0 and total_fast == 0 and total_soft == 0 and total_short == 0 and gallop < 10 and total_outliers <= 1:
            rec = "Excellent configuration found!"
            reason = "Your mechanical output is perfect and your timing is steady. Stick with this!"
            status = "good"
            bs_reason = "Activation point is dialled in perfectly."
            ps_reason = "Press timing produces clean hits." if sep_on else na_reason()
            rs_reason = "Release timing produces clean resets." if sep_on else na_reason()
            plain = (
                f"Excellent run. You tapped at roughly {w_b14:.0f} BPM with very stable timing and clean mechanical output. "
                "You are ready to practice."
            )
            decided = True

        if not decided and w_ur < t["ur_good_max"] and w_qual >= 75 and total_noise == 0 and total_fast == 0 and total_soft <= 2 and total_short == 0 and total_outliers <= 4:
            lower_amount = 0.04 + (t["ur_good_max"] - w_ur) / t["ur_good_max"] * 0.06
            rec = "You have room to increase speed"
            reason = "Your taps are controlled with no mechanical noise. You can push for more speed."
            status = "good"
            bs = range_value(curr_base - lower_amount, curr_base - lower_amount * 0.5)
            bs_reason = "You can carefully test a lighter touch to make keys fire faster."
            if sep_on:
                ps = one_value(curr_press - lower_amount * 0.4)
                ps_reason = "Shorter press distance for quicker activations."
                rs = one_value(curr_rel - lower_amount * 0.3)
                rs_reason = "Shorter release distance for quicker resets."
            plain = (
                "You are showing good control with zero mechanical errors. "
                "You have room to carefully test lighter settings and gain some speed."
            )
            decided = True

        if not decided and (
            over_score >= t["moderate_overtrigger_score"]
            or total_noise >= 1
            or total_soft >= 3
            or total_short >= 2
        ):
            severity = min(1.0, over_score / 40.0)
            base_raise = 0.04 + severity * 0.08
            press_raise = 0.01 + severity * 0.04
            rel_raise = 0.01 + severity * 0.04

            rec = "Increase your actuation depth"
            reason = "A small amount of mechanical noise crept in. A tiny adjustment should fix it."
            status = "warn"
            bs = range_value(curr_base + base_raise * 0.6, curr_base + base_raise)
            bs_reason = "A slightly deeper activation point will clean up chaotic inputs."
            if sep_on:
                ps = one_value(curr_press + press_raise)
                ps_reason = "Small increase for more deliberate press registrations."
                rs = one_value(curr_rel + rel_raise)
                rs_reason = "Small increase ensures clean key resets between taps."
            plain = (
                "A few accidental activations showed up during your run. "
                "A tiny bump deeper to the settings above will clean that up."
            )
            decided = True

        if not decided and w_ur <= t["ur_decent_max"] and w_qual >= 60 and total_noise == 0 and total_fast == 0 and abs(w_drift) < 5 and gallop < 12:
            rec = "Focus on technique, not settings"
            reason = "A solid run with no serious hardware or skill issues. Continue practicing."
            status = "good"
            plain = (
                f"Solid run around {w_b14:.0f} BPM. Timing is consistent and settings are clean. "
                "Practice is more valuable than configuration tweaks at this stage."
            )
            decided = True

        if not decided and (w_ur > t["ur_very_high_min"] or w_qual <= 50 or total_outliers > 8 or (w_cons < 70 and results[-1].consistency_score < 60)):
            rec = "Focus on even alternation and stamina"
            reason = "Your Unstable Rate is very high. Focus on clean alternation rather than setting changes."
            status = "coach"

            if total_fast >= 1 or total_short >= 1:
                rec = "Focus on alternation, settings slightly deeper"
                reason = "Your UR is high and some double-presses occur. Focus on stamina, but apply a small mechanical fix."
                status = "warn"
                bs = range_value(curr_base + 0.03, curr_base + 0.06)
                bs_reason = "A small increase to base actuation trades speed for stability."
                if sep_on:
                    ps = one_value(curr_press + 0.01)
                    ps_reason = "Tiny increase fixes random short presses."
                    rs = one_value(curr_rel + 0.02)
                    rs_reason = "Small increase improves controlled resets."
                plain = (
                    "Your tapping varies a lot, and we detected some random misfires. "
                    "Small mechanical fixes are listed above, but clean practice is the priority."
                )
            else:
                plain = (
                    "Your settings appear clean, but your Unstable Rate is very high. "
                    "Practice smooth alternation at a comfortable BPM before testing again."
                )
            decided = True

        if not decided and abs(w_drift) >= t["drift_significant_ms"] and total_fast == 0 and total_noise == 0:
            drift_dir = "slowing down" if w_drift > 0 else "speeding up"
            rec = "Settings look reliable — prioritize stamina"
            reason = (
                f"Your configuration is good, but you were {drift_dir} during the test. Focus on stamina."
            )
            status = "coach"
            plain = (
                f"Your configuration is good, but you were {drift_dir} over the course of the stream. "
                "Practice holding your comfortable rhythm before tweaking settings again."
            )
            decided = True

        if gallop > t["gallop_significant_ms"] and decided and rec in ("Focus on technique, not settings", "You have room to increase speed"):
            slow_label = slow_key_label()
            gallop_note = f" Heads up: {slow_label} is consistently lagging behind."

            if sep_on and total_noise == 0 and total_fast == 0:
                plain += (
                    f"{gallop_note} In your advanced tuning, consider a tiny reduction "
                    f"to the release distance of {slow_label} (the lagging finger)."
                )
            else:
                plain += gallop_note
            if status not in ("bad", "warn"):
                status = "coach"
            if rec in ("Keep settings", "Excellent configuration found!", "You have room to increase speed", "Perfection Achieved", "Focus on technique, not settings"):
                rec = "One finger is lagging behind"
                reason = (
                    f"Timing is okay overall, but {slow_label} is consistently lagging "
                    "the other finger. Tuning can help even this out."
                )

        if max_gap > t["max_phase_gap_seconds"]:
            plain += f" (Note: we detected a {max_gap:.1f}s pause during the test, which may have skewed the results.)"

        plain = plain.replace("settings below", "settings above").replace("listed below", "listed above")

        total_k1 = sum(r.key1_count for r in results)
        total_k2 = sum(r.key2_count for r in results)

        technique_tip = RecommendationEngine.pick_technique_tip(
            w_ur=w_ur,
            w_qual=w_qual,
            w_drift=w_drift,
            gallop=gallop,
            total_noise=total_noise,
            total_fast=total_fast,
            total_soft=total_soft,
            total_short=total_short,
            total_outliers=total_outliers,
            key1_count=total_k1,
            key2_count=total_k2,
        )

        return {
            "weighted_ur": w_ur,
            "weighted_quality": w_qual,
            "weighted_bpm_1_4": w_b14,
            "weighted_drift": w_drift,
            "weighted_drift_slope": w_drift_slope,
            "weighted_consistency": w_cons,
            "session_quality": "Good" if w_qual >= 80 else ("Usable" if w_qual >= 55 else "Poor"),
            "confidence": conf,
            "bpm_text": f"Est. UR: {w_ur:.1f}",
            "bpm_sub": f"1/4: {w_b14:.1f} BPM | Consistency: {w_cons:.0f}%",
            "recommendation_text": rec,
            "recommendation_reason": reason,
            "base_suggestion": bs,
            "base_reason": bs_reason,
            "press_suggestion": ps,
            "press_reason": ps_reason,
            "release_suggestion": rs,
            "release_reason": rs_reason,
            "recommendation_status": status,
            "plain_english": plain,
            "overtrigger_score": over_score,
            "gallop_bias": gallop,
            "slow_key": dominant_slow_key,
            "max_gap_seconds": max_gap,
            "technique_tip": technique_tip,
        }

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

STATE_IDLE = "idle"          
STATE_PHASE_READY = "ready"  
STATE_COMPLETE = "complete"  

class TapAnalyzerApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Osu!Calibrator by Rayze")
        self.resize(1550, 1050)

        icon_path = get_resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

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
        self.title_label = QLabel("OSU! CALIBRATOR")
        self.title_label.setObjectName("AppTitle")
        self.rayze_label = QLabel("BY RAYZE")
        self.rayze_label.setObjectName("AppSubtitle")
        self.rayze_explainer = QLabel(
            "Iterative Hall Effect calibration tool"
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

        self.stop_button = QPushButton("Stop  (Space)")
        self.stop_button.clicked.connect(self.stop_phase)
        self.stop_button.setEnabled(False)

        button_layout.addWidget(self.start_button, 2)
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

        form.addRow(
            self.make_help_label("Base Actuation (mm)", "Your main current actuation point right now."),
            self.base_actuation_input
        )
        form.addRow(
            self.make_help_label("Calibration Track", "Plays during phase. Extracts BPM for accurate tracking."),
            audio_row_container
        )
        form.addRow(
            self.make_help_label("Bind Keys", "Click and tap keys to bind them automatically."),
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

        self.press_label_container = self.make_help_label("Press Activate (mm)", "Downward movement needed to activate.")
        self.press_main_label = self.press_label_container.findChild(QLabel, "MainHelpLabel")

        self.release_label_container = self.make_help_label("Release Deactivate (mm)", "Upward movement needed to reset.")

        advanced_layout.addRow("", self.separate_sensitivity_checkbox)
        advanced_layout.addRow(self.press_label_container, self.press_activate_input)
        advanced_layout.addRow(self.release_label_container, self.release_deactivate_input)
        advanced_layout.addRow(
            self.make_help_label("Bottom-out Force (g)", "Heavier switches tolerate lower settings better."),
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

    def make_help_label(self, text: str, tooltip: str) -> QWidget:
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
        if not self.test_running:
            return
        with self.lock:
            if key not in self.held_keys:
                self.held_keys.add(key)
                self.events.append({"time": t, "type": "press", "key": key})
                self.cached_press_count += 1

    def on_phase_release(self, key: str, t: float):
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

        self.reset_key_detect_ui()

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

        if result.quality_score < 40:
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

        summary = RecommendationEngine.build_summary(
            self.phase_results,
            self.phase_configs,
            settings["base"],
            settings["press"],
            settings["release"],
            settings["force"],
            sep_on,
        )

        self.history.append(CalibrationSession(settings=settings, summary=summary.copy()))

        self.render_summary_cards(summary)
        self.render_graph(self.phase_results)
        self.render_detailed_text(self.phase_results, summary)
        self.evaluate_history()

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
