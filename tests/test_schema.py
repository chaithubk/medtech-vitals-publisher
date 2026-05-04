"""Unit tests for src.schema — v2 payload and clinical scoring helpers."""

import pytest

from src.schema import (
    SCHEMA_VERSION,
    VitalsPayloadV2,
    build_payload,
    calculate_qsofa,
    calculate_sirs,
    classify_sepsis_stage,
)


# ---------------------------------------------------------------------------
# SIRS scoring
# ---------------------------------------------------------------------------


class TestCalculateSirs:
    """Tests for calculate_sirs()."""

    def test_no_criteria_met(self):
        """Normal values → SIRS score 0."""
        score = calculate_sirs(temperature=37.0, hr=75.0, respiratory_rate=16.0, wbc=7.5)
        assert score == 0

    def test_temperature_high(self):
        """Temp > 38.0 °C counts as one criterion."""
        score = calculate_sirs(temperature=38.5, hr=75.0, respiratory_rate=16.0, wbc=7.5)
        assert score == 1

    def test_temperature_low(self):
        """Temp < 36.0 °C counts as one criterion."""
        score = calculate_sirs(temperature=35.5, hr=75.0, respiratory_rate=16.0, wbc=7.5)
        assert score == 1

    def test_hr_elevated(self):
        """HR > 90 counts as one criterion."""
        score = calculate_sirs(temperature=37.0, hr=95.0, respiratory_rate=16.0, wbc=7.5)
        assert score == 1

    def test_rr_elevated(self):
        """RR > 20 counts as one criterion."""
        score = calculate_sirs(temperature=37.0, hr=75.0, respiratory_rate=22.0, wbc=7.5)
        assert score == 1

    def test_wbc_high(self):
        """WBC > 12 counts as one criterion."""
        score = calculate_sirs(temperature=37.0, hr=75.0, respiratory_rate=16.0, wbc=13.0)
        assert score == 1

    def test_wbc_low(self):
        """WBC < 4 counts as one criterion."""
        score = calculate_sirs(temperature=37.0, hr=75.0, respiratory_rate=16.0, wbc=3.0)
        assert score == 1

    def test_all_four_criteria(self):
        """All four SIRS criteria met → score 4."""
        score = calculate_sirs(temperature=39.0, hr=100.0, respiratory_rate=25.0, wbc=14.0)
        assert score == 4

    def test_boundary_temperature_exactly_38(self):
        """Temp exactly 38.0 is NOT > 38.0, so does not count."""
        score = calculate_sirs(temperature=38.0, hr=75.0, respiratory_rate=16.0, wbc=7.5)
        assert score == 0

    def test_boundary_hr_exactly_90(self):
        """HR exactly 90 is NOT > 90, so does not count."""
        score = calculate_sirs(temperature=37.0, hr=90.0, respiratory_rate=16.0, wbc=7.5)
        assert score == 0


# ---------------------------------------------------------------------------
# qSOFA scoring
# ---------------------------------------------------------------------------


class TestCalculateQsofa:
    """Tests for calculate_qsofa()."""

    def test_no_criteria_met(self):
        """Normal values → qSOFA 0."""
        score = calculate_qsofa(respiratory_rate=16.0, bp_sys=120.0)
        assert score == 0

    def test_rr_criterion(self):
        """RR ≥ 22 counts as one criterion."""
        score = calculate_qsofa(respiratory_rate=22.0, bp_sys=120.0)
        assert score == 1

    def test_bp_criterion(self):
        """Systolic BP ≤ 100 counts as one criterion."""
        score = calculate_qsofa(respiratory_rate=16.0, bp_sys=100.0)
        assert score == 1

    def test_altered_mentation_criterion(self):
        """altered_mentation=True counts as one criterion."""
        score = calculate_qsofa(respiratory_rate=16.0, bp_sys=120.0, altered_mentation=True)
        assert score == 1

    def test_all_three_criteria(self):
        """All three qSOFA criteria met → score 3."""
        score = calculate_qsofa(respiratory_rate=25.0, bp_sys=95.0, altered_mentation=True)
        assert score == 3

    def test_rr_boundary_exactly_22(self):
        """RR exactly 22 DOES meet the ≥ 22 criterion."""
        score = calculate_qsofa(respiratory_rate=22.0, bp_sys=120.0)
        assert score == 1

    def test_bp_boundary_exactly_101(self):
        """Systolic BP 101 does NOT meet the ≤ 100 criterion."""
        score = calculate_qsofa(respiratory_rate=16.0, bp_sys=101.0)
        assert score == 0


# ---------------------------------------------------------------------------
# Sepsis stage classification
# ---------------------------------------------------------------------------


class TestClassifySepsisStage:
    """Tests for classify_sepsis_stage()."""

    def test_none_stage(self):
        """Low SIRS + low qSOFA → 'none'."""
        stage = classify_sepsis_stage(sirs_score=1, qsofa_score=1, bp_sys=120.0, lactate=1.0)
        assert stage == "none"

    def test_sirs_stage(self):
        """SIRS ≥ 2, qSOFA < 2 → 'sirs'."""
        stage = classify_sepsis_stage(sirs_score=2, qsofa_score=1, bp_sys=120.0, lactate=1.0)
        assert stage == "sirs"

    def test_sepsis_stage(self):
        """qSOFA ≥ 2, BP normal, lactate normal → 'sepsis'."""
        stage = classify_sepsis_stage(sirs_score=3, qsofa_score=2, bp_sys=120.0, lactate=1.5)
        assert stage == "sepsis"

    def test_septic_shock_low_bp(self):
        """qSOFA ≥ 2 + BP < 65 → 'septic_shock'."""
        stage = classify_sepsis_stage(sirs_score=3, qsofa_score=2, bp_sys=60.0, lactate=1.5)
        assert stage == "septic_shock"

    def test_septic_shock_high_lactate(self):
        """qSOFA ≥ 2 + lactate > 2 → 'septic_shock'."""
        stage = classify_sepsis_stage(sirs_score=3, qsofa_score=2, bp_sys=120.0, lactate=2.5)
        assert stage == "septic_shock"

    def test_boundary_lactate_exactly_2(self):
        """Lactate exactly 2.0 is NOT > 2.0 → 'sepsis' not 'septic_shock'."""
        stage = classify_sepsis_stage(sirs_score=3, qsofa_score=2, bp_sys=120.0, lactate=2.0)
        assert stage == "sepsis"


# ---------------------------------------------------------------------------
# VitalsPayloadV2 dataclass
# ---------------------------------------------------------------------------


class TestVitalsPayloadV2:
    """Tests for VitalsPayloadV2 dataclass."""

    def _make_payload(self, **overrides) -> VitalsPayloadV2:
        defaults = dict(
            patient_id="P001",
            scenario="sepsis",
            scenario_stage="sepsis_onset",
            timestamp=1_700_000_000_000,
            hr=115.0,
            bp_sys=100.0,
            bp_dia=65.0,
            o2_sat=92.0,
            temperature=38.8,
            respiratory_rate=22.0,
            wbc=14.0,
            lactate=2.2,
            sirs_score=3,
            qsofa_score=2,
            sepsis_stage="septic_shock",
            sepsis_onset_ts=1_700_000_001_000,
            quality="degraded",
            source="simulator",
        )
        defaults.update(overrides)
        return VitalsPayloadV2(**defaults)

    def test_default_version(self):
        """VitalsPayloadV2 defaults version to SCHEMA_VERSION."""
        p = self._make_payload()
        assert p.version == SCHEMA_VERSION

    def test_to_dict_contains_all_fields(self):
        """to_dict() returns all payload fields."""
        p = self._make_payload()
        d = p.to_dict()
        expected_keys = {
            "version", "patient_id", "scenario", "scenario_stage", "timestamp",
            "hr", "bp_sys", "bp_dia", "o2_sat", "temperature", "respiratory_rate",
            "wbc", "lactate", "sirs_score", "qsofa_score", "sepsis_stage",
            "sepsis_onset_ts", "quality", "source",
        }
        assert expected_keys == set(d.keys())

    def test_to_dict_sepsis_onset_ts_can_be_none(self):
        """to_dict() serialises sepsis_onset_ts=None correctly."""
        p = self._make_payload(sepsis_onset_ts=None)
        d = p.to_dict()
        assert d["sepsis_onset_ts"] is None


# ---------------------------------------------------------------------------
# build_payload helper
# ---------------------------------------------------------------------------


class TestBuildPayload:
    """Tests for build_payload()."""

    def test_healthy_payload_scores(self):
        """Healthy vitals → sirs=0, qsofa=0, sepsis_stage='none'."""
        p = build_payload(
            patient_id="P001",
            scenario="healthy",
            scenario_stage="healthy",
            timestamp=1_700_000_000_000,
            hr=72.0,
            bp_sys=118.0,
            bp_dia=76.0,
            o2_sat=98.0,
            temperature=36.8,
            respiratory_rate=15.0,
            wbc=7.0,
            lactate=1.0,
            quality="good",
            source="simulator",
        )
        assert p.sirs_score == 0
        assert p.qsofa_score == 0
        assert p.sepsis_stage == "none"
        assert p.version == "2.0"

    def test_sepsis_payload_scores(self):
        """Sepsis-range vitals → positive scores and correct stage."""
        p = build_payload(
            patient_id="P002",
            scenario="sepsis",
            scenario_stage="sepsis",
            timestamp=1_700_000_000_000,
            hr=120.0,
            bp_sys=88.0,
            bp_dia=55.0,
            o2_sat=91.0,
            temperature=39.2,
            respiratory_rate=25.0,
            wbc=16.0,
            lactate=3.0,
            quality="degraded",
            source="simulator",
        )
        assert p.sirs_score >= 2
        assert p.qsofa_score >= 2
        assert p.sepsis_stage == "septic_shock"

    def test_altered_mentation_flag(self):
        """altered_mentation=True adds 1 to qSOFA."""
        p_normal = build_payload(
            patient_id="P001", scenario="sepsis", scenario_stage="sepsis",
            timestamp=0, hr=110.0, bp_sys=110.0, bp_dia=70.0, o2_sat=93.0,
            temperature=38.8, respiratory_rate=21.0, wbc=13.0, lactate=1.8,
            quality="degraded", source="simulator", altered_mentation=False,
        )
        p_altered = build_payload(
            patient_id="P001", scenario="sepsis", scenario_stage="sepsis",
            timestamp=0, hr=110.0, bp_sys=110.0, bp_dia=70.0, o2_sat=93.0,
            temperature=38.8, respiratory_rate=21.0, wbc=13.0, lactate=1.8,
            quality="degraded", source="simulator", altered_mentation=True,
        )
        assert p_altered.qsofa_score == p_normal.qsofa_score + 1
