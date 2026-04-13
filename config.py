import os
import sys
import shutil
import tempfile
import math

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
    "depth_ceiling_warn_base": 0.90,   
    "depth_ceiling_hard_base": 1.20,   
    "depth_ceiling_hard_press": 0.35,  
    "depth_ceiling_hard_release": 0.35, 
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
    "depth_ceiling": [
        "Your settings are already deep enough that the keyboard isn't the problem anymore. Focus on lifting each finger fully off the key after every tap — most 'misfires' at this depth are fingers brushing the keys.",
        "At this depth, going deeper won't help. The chaotic inputs are coming from your hand technique. Try playing at 80% effort for a few minutes to find a cleaner alternation pattern before going hard again.",
        "You've hit the ceiling for hardware fixes. From here on, every UR improvement comes from technique: keep your hand relaxed, lift fingers cleanly between taps, and don't mash through the bottom of the switch.",
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