"""Independent naming and numerical CSV compatibility regressions."""
import importlib
from pathlib import Path
import sys

import pytest


@pytest.fixture
def trochanter(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "analysis_scripts"))
    module = importlib.import_module("trochanter_auswertung")
    # The legacy core stores configuration globally; restore it after each test.
    for name in ("SPECIMEN", "INPUT_DIR", "OUTPUT_DIR", "PLOTS_DIR", "DECLARED_FAILURES", "FILE_PATTERN", "find_input_files"):
        monkeypatch.setattr(module.core, name, getattr(module.core, name))
    monkeypatch.setattr(module.core.pd, "read_csv", module.ORIGINAL_READ_CSV)
    return module


@pytest.mark.parametrize("name,expected", [
    ("EXAMPLE_R_double_450N.csv", ("EXAMPLE_R", "double", "", "450N")),
    ("EXAMPLE_R_double2_150N.csv", ("EXAMPLE_R", "double", "2", "150N")),
    ("EXAMPLE_Rdouble3_150N.csv", ("EXAMPLE_R", "double", "3", "150N")),
    ("EXAMPLE_Rdouble_300N.csv", ("EXAMPLE_R", "double", "", "300N")),
])
def test_recording_identity_includes_repeat(trochanter, name, expected):
    assert trochanter.parse_recording_name(Path(name)) == expected


@pytest.mark.parametrize("name", ["trial.csv", "S_R_double_999N.csv", "S_R_double_150N.json"])
def test_unrecognized_recording_name_is_rejected(trochanter, name):
    with pytest.raises(ValueError):
        trochanter.parse_recording_name(Path(name))


def test_long_csv_matches_independent_wide_fixture_without_filling_marker_gaps(trochanter, tmp_path):
    pd = trochanter.core.pd
    long_rows, wide_rows = [], []
    for frame in range(3):
        wide = {"sample_index": frame, "timestamp_s": frame * 0.05}
        for marker in (299, 321, 322):
            missing = frame == 1 and marker == 321
            wide[f"marker_{marker}_visible"] = int(not missing)
            for axis, offset in zip("xyz", (1, 2, 3)):
                wide[f"marker_{marker}_{axis}_mm"] = float("nan") if missing else marker + frame * offset
            for r in range(3):
                for c in range(3):
                    wide[f"marker_{marker}_R{r}{c}"] = float("nan") if missing else float(r == c)
            if missing:
                continue
            row = {"frame_counter": frame + 100, "shared_sample_index": frame,
                   "geometry_id": marker, "visible": 1, "time_since_start_s": frame * 0.05}
            row.update({f"{axis}_mm": marker + frame * offset for axis, offset in zip("xyz", (1, 2, 3))})
            row.update({f"r{r}{c}": float(r == c) for r in range(3) for c in range(3)})
            long_rows.append(row)
        wide_rows.append(wide)
    path = tmp_path / "long.csv"
    pd.DataFrame(long_rows).to_csv(path, index=False)
    expected = pd.DataFrame(wide_rows)[trochanter.core.USE_COLUMNS]
    actual = trochanter._read_current_long_csv(path)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False)
    wide_path = tmp_path / "wide.csv"
    expected.to_csv(wide_path, index=False)
    preserved = trochanter._compatible_read_csv(wide_path, usecols=trochanter.core.USE_COLUMNS)
    pd.testing.assert_frame_equal(preserved, expected, check_dtype=False)


def test_selected_repeat_has_distinct_output_and_does_not_scan_other_trials(trochanter, tmp_path):
    first = tmp_path / "EXAMPLE_R_double2_150N.csv"
    second = tmp_path / "EXAMPLE_Rdouble3_150N.csv"
    trochanter._configure(first)
    first_output = trochanter.core.OUTPUT_DIR
    assert list(trochanter.core.find_input_files()) == [first]
    trochanter._configure(second)
    assert list(trochanter.core.find_input_files()) == [second]
    assert trochanter.core.OUTPUT_DIR != first_output
    assert first.stem in str(first_output)
    assert second.stem in str(trochanter.core.OUTPUT_DIR)


def test_empty_or_stationary_data_never_becomes_invented_active_cycles(trochanter):
    pd = trochanter.core.pd
    with pytest.raises(ValueError, match="Keine auswertbaren Zyklen"):
        trochanter.core.detect_active_cycles(pd.DataFrame())
    stationary = pd.DataFrame({"cycle": range(200), "micromotion_mm": [0.01] * 200})
    with pytest.raises(ValueError, match="Keine aktiven"):
        trochanter.core.detect_active_cycles(stationary)
