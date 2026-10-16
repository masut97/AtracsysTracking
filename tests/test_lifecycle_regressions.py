import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from atracsys_sync.runner import ScriptGroup
from atracsys_sync.protocol import ScriptSpec
from atracsys_sync.status import format_values, synchronization_status
from atracsys_sync.adapter import validate_tracker_contract


DEMO = ScriptSpec.read(Path(__file__).resolve().parents[1] / "measurement_scripts/demo.py")


def test_partial_start_failure_closes_prepared_writer_and_finalizes_now(tmp_path, monkeypatch):
    group = ScriptGroup([(DEMO, DEMO.defaults), (DEMO, DEMO.defaults)], 20)
    # Use real writers but no devices or subprocesses; finalize synchronously.
    for source in group.sources:
        source.ready.set()
        monkeypatch.setattr(source, "join", lambda timeout=4, source=source: source._finalize())
    def deny_open(*args):
        raise PermissionError("simulated disk permission failure")
    monkeypatch.setattr(group.sources[1], "begin_recording", deny_open)
    with pytest.raises(PermissionError):
        group.begin_recording(tmp_path / "trial.csv", "test-session", time.monotonic())
    manifest = tmp_path / "trial.scripts.json"
    before = manifest.read_bytes()
    report = json.loads(before)
    assert report["state"] == "incomplete"
    assert "disk permission" in report["start_error"]
    assert all(source.file is None for source in group.sources)
    assert not group.recording
    assert not list(tmp_path.glob("*.script_*.csv"))
    group.stop()
    assert manifest.read_bytes() == before
    with pytest.raises(RuntimeError):
        group.begin_recording(tmp_path / "another.csv", "other-session", time.monotonic())
    assert not (tmp_path / "another.scripts.json").exists()


def test_double_start_cannot_replace_owned_metadata(tmp_path):
    group = ScriptGroup([], 20)
    first = tmp_path / "first.csv"
    group.begin_recording(first, "one", time.monotonic())
    try:
        with pytest.raises(RuntimeError):
            group.begin_recording(tmp_path / "second.csv", "two", time.monotonic())
        assert group.session["session_id"] == "one"
        assert not (tmp_path / "second.scripts.json").exists()
    finally:
        group.stop()


def test_existing_manifest_temporary_file_is_preserved(tmp_path):
    temporary = tmp_path / "trial.scripts.json.tmp"
    temporary.write_bytes(b"previous interrupted save")
    group = ScriptGroup([], 20)
    with pytest.raises(FileExistsError):
        group.begin_recording(tmp_path / "trial.csv", "new", time.monotonic())
    group.stop()
    assert temporary.read_bytes() == b"previous interrupted save"
    assert not (tmp_path / "trial.scripts.json").exists()


def test_initial_metadata_write_error_never_publishes_partial_final_json(tmp_path, monkeypatch):
    def fail_dump(value, stream, **kwargs):
        stream.write('{"partial":')
        raise OSError("simulated disk full")
    monkeypatch.setattr("atracsys_sync.runner.json.dump", fail_dump)
    group = ScriptGroup([], 20)
    with pytest.raises(OSError, match="disk full"):
        group.begin_recording(tmp_path / "trial.csv", "new", time.monotonic())
    group.stop()
    assert not (tmp_path / "trial.scripts.json").exists()


def test_generic_nullable_measurement_status_does_not_assume_force_sensor():
    assert "temperature_C: 21.500" in format_values({"temperature_C": 21.5, "optional": None})
    assert "optional: —" in format_values({"optional": None})
    assert synchronization_status("best_effort_host_time_v1", {"values": {"temperature_C": 21.5}}) == "Software"
    assert synchronization_status("best_effort_host_time_v1", {"values": {"sensor_to_tracker_ms": None}}) == "Software"
    assert "—" in synchronization_status("atracsys_timestamp_correlated_v1", {"values": {"adc_age_at_exposure_us": None}})
    assert "Fehler" in synchronization_status("simulation", {"error": "unavailable"})


def test_incompatible_tracker_contract_fails_before_any_hook_is_applied():
    class Worker:
        def start_session(self, name, target, metadata, sample_frequency_hz): pass
        def enqueue(self, frame): pass
        def _write_frame(self, writer, frame): pass
        def stop(self): pass
        def stats(self): pass
    class Window:
        def __init__(self): pass
        def _start_recording(self): pass
        def _toggle_recording(self): pass
        def _refresh_recording_status(self): pass
        def _populate_analysis_scripts(self, selected=None): pass
        def _configured_analysis_script(self): pass
        def closeEvent(self): pass
    recording = SimpleNamespace(RecordingWorker=Worker, CSV_COLUMNS=[])
    original = Worker.enqueue
    validate_tracker_contract(recording, Window)
    assert Worker.enqueue is original
    class IncompatibleWorker(Worker):
        def enqueue(self, renamed_parameter): pass
    with pytest.raises(RuntimeError, match="enqueue"):
        validate_tracker_contract(SimpleNamespace(RecordingWorker=IncompatibleWorker, CSV_COLUMNS=[]), Window)
    assert Worker.enqueue is original
    with pytest.raises(RuntimeError, match="CSV"):
        validate_tracker_contract(SimpleNamespace(RecordingWorker=Worker, CSV_COLUMNS=()), Window)
