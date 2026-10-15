import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("build_extension", Path(__file__).resolve().parents[1] / "tools/build_extension.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.mark.parametrize("selection", [None, ""])
def test_disabled_analysis_remains_disabled(tmp_path, selection):
    assert builder.select_analysis(selection, tmp_path) == ""


@pytest.mark.parametrize("selection,expected", [
    ("analysis_scripts/Auswertung_299_331_332.py", "Auswertung_299_331_332.py"),
    (r"C:\another-machine\Trochanter_Auswertung_299_321_322.py", "trochanter_auswertung.py"),
    ("removed_script.py", "auto_auswertung.py"),
])
def test_analysis_selection_relocates_or_falls_back_without_foreign_paths(tmp_path, selection, expected):
    for name in ("auto_auswertung.py", "trochanter_auswertung.py", "Auswertung_299_331_332.py"):
        (tmp_path / name).touch()
    assert builder.select_analysis(selection, tmp_path) == "analysis_scripts/" + expected
