import importlib
import math
from pathlib import Path

import pytest


@pytest.fixture
def rom(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "analysis_scripts"))
    return importlib.import_module("rom_auswertung")


def fixture(rom, ratio=0.9, missing=0, registration=0.2):
    times = tuple(i * 0.05 for i in range(401))
    curve = tuple(5 * math.sin(2 * math.pi * time / 10) for time in times)
    poses = {299: (rom.RomPose(((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0, 0, 0), registration),)}
    tracking = rom.RomTrackingData(times, poses, len(times) * 5, missing, "synthetic")
    curves = {"segmental": tuple(ratio * value for value in curve), "clamp_segmental": curve}
    machine = rom.RomZwickData(times, curve, curve, curve, "moment", "angle", "Nm", "deg", Path("synthetic.tra"))
    return tracking, curves, machine


@pytest.mark.parametrize("ratio,rating,code", [
    (0.9, "good", None), (0.6, "medium", "possible_clamp_slip"),
    (0.4, "bad", "severe_clamp_slip"), (1.2, "bad", "vertebra_exceeds_clamp"),
])
def test_quality_rates_smooth_measurements_and_explains_slip(rom, ratio, rating, code):
    tracking, curves, machine = fixture(rom, ratio)
    findings, metrics = rom.evaluate_quality(tracking, curves, "FE", machine, False, rom.DEFAULT_CONFIG)
    summary = rom.quality_summary(findings)
    assert summary["rating"] == rating
    assert metrics["vertebra_to_clamp_ratio"] == pytest.approx(ratio)
    if code:
        assert code in {finding.code for finding in findings}
        assert summary["hints"]
    else:
        assert not findings


@pytest.mark.parametrize("missing,registration,code", [
    (40, 0.2, "incomplete_marker_samples"), (0, 2.5, "registration_error"),
])
def test_missing_markers_or_bad_registration_cannot_be_rated_good(rom, missing, registration, code):
    tracking, curves, machine = fixture(rom, missing=missing, registration=registration)
    findings, _ = rom.evaluate_quality(tracking, curves, "FE", machine, False, rom.DEFAULT_CONFIG)
    assert code in {finding.code for finding in findings}
    assert rom.quality_summary(findings)["rating"] == "bad"


def test_camera_only_warns_and_missing_expected_machine_data_fails_quality(rom):
    tracking, curves, _ = fixture(rom)
    findings, _ = rom.evaluate_quality(tracking, curves, "FE", None, False, rom.DEFAULT_CONFIG, zwick_expected=False)
    assert rom.quality_summary(findings)["rating"] == "medium"
    assert "camera_only_analysis" in {finding.code for finding in findings}
    findings, _ = rom.evaluate_quality(tracking, curves, "FE", None, False, rom.DEFAULT_CONFIG)
    assert rom.quality_summary(findings)["rating"] == "bad"
    assert "missing_zwick_data" in {finding.code for finding in findings}
