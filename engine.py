import math
import statistics
import re
from typing import Dict, List, Optional, Tuple

from models import PhaseConfig, PhaseResult
from config import (
    MIN_PRESSES_FOR_ANALYSIS,
    WARMUP_TAPS_TO_DISCARD,
    INTERVAL_TO_TIMING_STDEV,
    RECOMMENDATION_THRESHOLDS,
    TECHNIQUE_TIPS,
    QUALITY_PENALTIES,
)

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
        prev_summary: Optional[Dict] = None
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

        change_log = "Run another test to see comparison reasoning here."
        if prev_summary:
            prev_ur = prev_summary.get("weighted_ur", 0)
            prev_noise = prev_summary.get("raw_noise", 0)
            prev_fast = prev_summary.get("raw_fast", 0)

            ur_diff = w_ur - prev_ur
            noise_diff = total_noise - prev_noise
            fast_diff = total_fast - prev_fast

            reasons = []
            if abs(ur_diff) >= 8:
                dir_str = "worsened" if ur_diff > 0 else "improved"
                reasons.append(f"Your UR {dir_str} by {abs(ur_diff):.0f} points.")
            else:
                reasons.append("Your UR remained relatively stable.")

            if noise_diff > 0:
                reasons.append(f"We detected {noise_diff} new release noise misfires.")
            elif noise_diff < 0:
                reasons.append(f"You cleaned up {abs(noise_diff)} release noise misfires.")

            if fast_diff > 0:
                reasons.append(f"We detected {fast_diff} new double-taps.")
            elif fast_diff < 0:
                reasons.append(f"You cleaned up {abs(fast_diff)} double-taps.")

            change_log = " ".join(reasons) + " The engine's recommendation above is a direct response to these specific mechanical changes."

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

        ceiling_fired = False

        def _wants_deeper(suggestion: str, current: float) -> bool:
            if not isinstance(suggestion, str) or "Try" not in suggestion:
                return False
            m = re.search(r"(\d+\.\d+)", suggestion)
            if not m:
                return False
            return float(m.group(1)) > current

        wants_deeper_base = _wants_deeper(bs, curr_base)
        wants_deeper_press = sep_on and _wants_deeper(ps, curr_press)
        wants_deeper_rel = sep_on and _wants_deeper(rs, curr_rel)

        at_base_ceiling = curr_base >= t["depth_ceiling_hard_base"]
        at_press_ceiling = sep_on and curr_press >= t["depth_ceiling_hard_press"]
        at_rel_ceiling = sep_on and curr_rel >= t["depth_ceiling_hard_release"]

        ceiling_blocks = (
            (wants_deeper_base and at_base_ceiling)
            or (wants_deeper_press and at_press_ceiling)
            or (wants_deeper_rel and at_rel_ceiling)
        )

        if ceiling_blocks:
            ceiling_fired = True
            bs = "Keep as is"
            bs_reason = "You've hit the depth ceiling — going deeper won't fix technique issues."
            if sep_on:
                ps = "Keep as is"
                ps_reason = "Already at the safe RT ceiling. Further increase won't help."
                rs = "Keep as is"
                rs_reason = "Already at the safe RT ceiling. Further increase won't help."

            rec = "Your settings are deep enough — focus on technique"
            reason = (
                "Your hardware is in a safe zone. The misfires we're seeing are now coming "
                "from your hand technique, not the keyboard."
            )
            status = "coach"
            plain = (
                f"You've reached the depth ceiling (Base {curr_base:.2f} mm). For 99% of "
                "players, Hall Effect switches don't need to be deeper than this. The chaotic "
                "inputs we're spotting at this depth are almost always from finger technique — "
                "mashing the keys, hovering too close, or not fully lifting between taps. "
                "Lock your settings here and focus on the Mechanics Tip card."
            )
        elif (
            curr_base >= t["depth_ceiling_warn_base"]
            and (wants_deeper_base or wants_deeper_press or wants_deeper_rel)
        ):
            plain += (
                f" Heads up: at {curr_base:.2f} mm you're approaching the depth ceiling "
                f"({t['depth_ceiling_hard_base']:.2f} mm). If 'go deeper' advice keeps "
                "appearing after this, the issue is technique not hardware — focus on "
                "lifting your fingers cleanly between taps."
            )

        def _extract_first_mm(s: str) -> Optional[float]:
            if not isinstance(s, str):
                return None
            m = re.search(r"(\d+\.\d+)", s)
            return float(m.group(1)) if m else None

        rt_override_applied = False
        rt_override_explanation = ""

        if sep_on:
            sug_base = _extract_first_mm(bs) if "Try" in bs else curr_base
            sug_press = _extract_first_mm(ps) if isinstance(ps, str) and "Try" in ps else curr_press
            sug_rel = _extract_first_mm(rs) if isinstance(rs, str) and "Try" in rs else curr_rel

            eff_base = sug_base if sug_base else curr_base
            eff_press = sug_press if sug_press else curr_press
            eff_rel = sug_rel if sug_rel else curr_rel

            current_paradox = (curr_press + curr_rel) > (curr_base * 0.7)
            suggested_paradox = (eff_press + eff_rel) > (eff_base * 0.7)

            if current_paradox or suggested_paradox:
                safe_base = max(eff_base, curr_base)
                safe_each = round(safe_base * 0.35, 2)
                safe_each = max(0.10, safe_each)

                bs = f"Try {clamp_mm(safe_base):.2f} mm"
                ps = f"Try {safe_each:.2f} mm"
                rs = f"Try {safe_each:.2f} mm"

                bs_reason = "Locked to a safe depth to give your Rapid Trigger room to work."
                ps_reason = "Auto-capped to fit safely inside your base actuation."
                rs_reason = "Auto-capped to fit safely inside your base actuation."

                rt_override_applied = True
                rt_override_explanation = (
                    "⚠️ We adjusted your Rapid Trigger values. Think of Base Actuation as the "
                    "physical 'room' your key has to move inside. Your Press + Release distances "
                    f"({curr_press:.2f} + {curr_rel:.2f} = {curr_press + curr_rel:.2f} mm) were "
                    f"taking up more than 70% of your Base ({curr_base:.2f} mm), leaving no room "
                    "for the key to fully reset between taps. We recalculated them to fit safely "
                    f"({safe_each:.2f} mm each) so the switch can actually breathe. "
                )
                rec = "Rapid Trigger values recalculated"
                reason = "Your previous RT settings left no room for the key to reset. See explanation."
                status = "warn"
                plain = rt_override_explanation + plain

        if sep_on and not rt_override_applied:
            eff_p = _extract_first_mm(ps) if isinstance(ps, str) and "Try" in ps else curr_press
            eff_r = _extract_first_mm(rs) if isinstance(rs, str) and "Try" in rs else curr_rel
            eff_p = eff_p if eff_p else curr_press
            eff_r = eff_r if eff_r else curr_rel

            if eff_r > eff_p:
                rs = f"Try {eff_p:.2f} mm"
                rs_reason = "Capped to match Press distance (prevents the key from feeling 'sticky')."
                
                anti_sticky_note = (
                    " ⚠️ We noticed your Release distance was higher than your Press distance. "
                    "This forces your finger to lift further to reset the key than it pushed to fire it, "
                    "causing dropped slider ends. We matched your Release to your Press to fix this."
                )
                plain += anti_sticky_note
                
                status = "warn"
                
                if rec in ("Practice is the best path", "Focus on technique, not settings", "Excellent configuration found!"):
                    rec = "Rapid Trigger values optimized"
                    reason = "Your tapping is fine, but we tweaked your Release distance to prevent sticky keys."

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

        if ceiling_fired:
            import random as _rnd
            _ceiling_rng = _rnd.Random(int(curr_base * 1000))
            technique_tip = _ceiling_rng.choice(TECHNIQUE_TIPS["depth_ceiling"])

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
            "raw_noise": total_noise,
            "raw_fast": total_fast,
            "change_log_text": change_log,
        }