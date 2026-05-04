"""Unit tests for src.progression — deterministic multi-stage sepsis progression engine."""

import time

import pytest

from src.progression import ProgressionEngine, _STAGE_PARAMS, _VALID_STAGES


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestProgressionEngineConstruction:
    """Tests for ProgressionEngine constructor validation."""

    def test_invalid_scenario_raises(self):
        """Unknown scenario string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid scenario"):
            ProgressionEngine(scenario="unknown")

    def test_invalid_stage_raises(self):
        """Unknown stage string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid stage"):
            ProgressionEngine(scenario="sepsis", stage="nonexistent_stage")

    def test_valid_healthy_construction(self):
        """Healthy scenario constructs without error."""
        eng = ProgressionEngine(scenario="healthy", seed=1)
        assert eng.current_stage == "healthy"

    def test_valid_sepsis_construction(self):
        """Sepsis scenario starts at pre_sepsis."""
        eng = ProgressionEngine(scenario="sepsis", seed=1)
        assert eng.current_stage == "pre_sepsis"

    def test_valid_critical_construction(self):
        """Critical scenario starts at septic_shock."""
        eng = ProgressionEngine(scenario="critical", seed=1)
        assert eng.current_stage == "septic_shock"


# ---------------------------------------------------------------------------
# Stage progression
# ---------------------------------------------------------------------------


class TestStageProgression:
    """Tests for stage advancement logic."""

    def test_healthy_stays_indefinitely(self):
        """Healthy scenario never advances to another stage."""
        eng = ProgressionEngine(scenario="healthy", seed=42)
        for _ in range(50):
            eng.next_reading()
        assert eng.current_stage == "healthy"

    def test_sepsis_advances_through_stages(self):
        """Sepsis scenario eventually advances through pre_sepsis → sepsis_onset → sepsis → septic_shock."""
        eng = ProgressionEngine(scenario="sepsis", seed=42)
        stages_seen = set()
        # Generate enough readings to traverse all stages (6+8+10 = 24 ticks minimum)
        for _ in range(60):
            stages_seen.add(eng.current_stage)
            eng.next_reading()
        assert "pre_sepsis" in stages_seen
        assert "sepsis_onset" in stages_seen
        assert "sepsis" in stages_seen
        assert "septic_shock" in stages_seen

    def test_critical_stays_at_septic_shock(self):
        """Critical scenario remains at septic_shock indefinitely."""
        eng = ProgressionEngine(scenario="critical", seed=42)
        for _ in range(20):
            eng.next_reading()
        assert eng.current_stage == "septic_shock"

    def test_explicit_stage_start(self):
        """Engine starts at the explicitly requested stage."""
        eng = ProgressionEngine(scenario="sepsis", stage="sepsis", seed=42)
        assert eng.current_stage == "sepsis"

    def test_ticks_per_stage_override(self):
        """Custom ticks_per_stage shortens stage duration."""
        eng = ProgressionEngine(
            scenario="sepsis",
            seed=42,
            ticks_per_stage={"pre_sepsis": 1, "sepsis_onset": 1, "sepsis": 1},
        )
        stages_seen = set()
        for _ in range(10):
            stages_seen.add(eng.current_stage)
            eng.next_reading()
        # With 1-tick budgets we should see all stages within 10 ticks
        assert "septic_shock" in stages_seen


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


class TestDeterministicReplay:
    """Verify that same seed always produces same sequence."""

    def test_same_seed_same_sequence(self):
        """Two engines with the same seed produce identical reading sequences."""
        eng1 = ProgressionEngine(scenario="sepsis", seed=99)
        eng2 = ProgressionEngine(scenario="sepsis", seed=99)
        ts = 1_700_000_000_000
        for _ in range(30):
            r1 = eng1.next_reading(ts=ts)
            r2 = eng2.next_reading(ts=ts)
            assert r1["hr"] == r2["hr"], "hr differs for same seed"
            assert r1["temperature"] == r2["temperature"], "temperature differs for same seed"
            ts += 10_000

    def test_different_seeds_different_sequences(self):
        """Two engines with different seeds produce different values."""
        eng1 = ProgressionEngine(scenario="healthy", seed=1)
        eng2 = ProgressionEngine(scenario="healthy", seed=2)
        readings1 = [eng1.next_reading()["hr"] for _ in range(5)]
        readings2 = [eng2.next_reading()["hr"] for _ in range(5)]
        # At least one reading should differ
        assert readings1 != readings2


# ---------------------------------------------------------------------------
# Reading structure
# ---------------------------------------------------------------------------


class TestReadingStructure:
    """Tests for the dict returned by next_reading()."""

    _REQUIRED_KEYS = {
        "scenario_stage", "timestamp", "hr", "bp_sys", "bp_dia", "o2_sat",
        "temperature", "respiratory_rate", "wbc", "lactate", "quality", "sepsis_onset_ts",
    }

    def test_required_keys_present(self):
        """next_reading() returns a dict with all required keys."""
        eng = ProgressionEngine(scenario="sepsis", seed=42)
        reading = eng.next_reading()
        missing = self._REQUIRED_KEYS - reading.keys()
        assert not missing, f"Missing keys: {missing}"

    def test_timestamp_is_ms_epoch(self):
        """timestamp is a positive integer in a reasonable ms-epoch range."""
        before = int(time.time() * 1000)
        eng = ProgressionEngine(scenario="healthy", seed=42)
        reading = eng.next_reading()
        after = int(time.time() * 1000) + 1000
        assert before <= reading["timestamp"] <= after

    def test_explicit_timestamp(self):
        """Explicit ts parameter is used as-is."""
        eng = ProgressionEngine(scenario="healthy", seed=42)
        reading = eng.next_reading(ts=999_999)
        assert reading["timestamp"] == 999_999

    def test_vitals_are_numeric(self):
        """All vital fields are numeric."""
        eng = ProgressionEngine(scenario="sepsis", seed=42)
        reading = eng.next_reading()
        for key in ("hr", "bp_sys", "bp_dia", "o2_sat", "temperature", "respiratory_rate", "wbc", "lactate"):
            assert isinstance(reading[key], (int, float)), f"{key} is not numeric"

    def test_quality_is_str(self):
        """quality field is a string."""
        eng = ProgressionEngine(scenario="healthy", seed=42)
        reading = eng.next_reading()
        assert isinstance(reading["quality"], str)


# ---------------------------------------------------------------------------
# Sepsis onset tracking
# ---------------------------------------------------------------------------


class TestSepsisOnset:
    """Tests for sepsis_onset_ts tracking."""

    def test_healthy_onset_never_set(self):
        """Healthy scenario never sets sepsis_onset_ts."""
        eng = ProgressionEngine(scenario="healthy", seed=42)
        for _ in range(20):
            reading = eng.next_reading()
            assert reading["sepsis_onset_ts"] is None

    def test_sepsis_onset_ts_set_when_sepsis_stage_reached(self):
        """sepsis_onset_ts is set once the engine enters 'sepsis' stage."""
        eng = ProgressionEngine(scenario="sepsis", seed=42)
        onset_recorded = None
        for _ in range(60):
            reading = eng.next_reading()
            if reading["scenario_stage"] in {"sepsis", "septic_shock"}:
                onset_recorded = reading["sepsis_onset_ts"]
                break
        assert onset_recorded is not None

    def test_onset_ts_does_not_change_once_set(self):
        """sepsis_onset_ts is immutable once first set."""
        eng = ProgressionEngine(scenario="sepsis", seed=42)
        first_onset = None
        for _ in range(100):
            reading = eng.next_reading()
            if reading["sepsis_onset_ts"] is not None:
                if first_onset is None:
                    first_onset = reading["sepsis_onset_ts"]
                else:
                    assert reading["sepsis_onset_ts"] == first_onset


# ---------------------------------------------------------------------------
# generate_sequence
# ---------------------------------------------------------------------------


class TestGenerateSequence:
    """Tests for the batch generate_sequence() helper."""

    def test_returns_correct_count(self):
        """generate_sequence(n) returns exactly n readings."""
        eng = ProgressionEngine(scenario="healthy", seed=42)
        seq = eng.generate_sequence(10)
        assert len(seq) == 10

    def test_timestamps_evenly_spaced(self):
        """Timestamps in the sequence are separated by interval_ms."""
        eng = ProgressionEngine(scenario="healthy", seed=42)
        seq = eng.generate_sequence(5, start_ts=0, interval_ms=10_000)
        for i, reading in enumerate(seq):
            assert reading["timestamp"] == i * 10_000

    def test_sequence_is_deterministic(self):
        """Same engine state → same sequence."""
        eng1 = ProgressionEngine(scenario="sepsis", seed=7)
        eng2 = ProgressionEngine(scenario="sepsis", seed=7)
        seq1 = eng1.generate_sequence(5, start_ts=0)
        seq2 = eng2.generate_sequence(5, start_ts=0)
        for r1, r2 in zip(seq1, seq2):
            assert r1["hr"] == r2["hr"]
