from dataclasses import dataclass
from typing import Dict, List, Optional

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