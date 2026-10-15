import csv
import importlib.util
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from atracsys_sync.protocol import ScriptSpec, hardware_requirement, validate_values
from atracsys_sync.runner import ScriptGroup, SourceProcess

ROOT = Path(__file__).resolve().parents[1]
DEMO = ScriptSpec.read(ROOT / "measurement_scripts/demo.py")
FORCE = ScriptSpec.read(ROOT / "measurement_scripts/zugkraft.py")


def wait_until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Zeitlimit beim Warten auf Skriptprozess")
        time.sleep(0.02)


def custom(tmp_path, body):
    path = tmp_path / "source.py"
    path.write_text('MEASUREMENT_SCRIPT = {"api_version": 1, "name": "Test", "columns": ["value"]}\n' + body, encoding="utf-8")
    return ScriptSpec.read(path)


def test_software_synchronized_source_requires_real_tracker():
    assert FORCE.defaults["port"] == "COM6"
    assert FORCE.defaults["dtr"] is True
    assert not hardware_requirement([FORCE], "native")
    assert hardware_requirement([FORCE], "mock")
    assert hardware_requirement([DEMO], "native")
    assert not hardware_requirement([DEMO], "mock")
    assert not hardware_requirement([], "native")

def test_metadata_inspection_does_not_execute_code(tmp_path):
    marker = tmp_path / "executed"
    spec = custom(tmp_path, f"raise RuntimeError({str(marker)!r})\n")
    assert spec.name == "Test"
    assert not marker.exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "2"])
def test_invalid_numbers_rejected(value):
    with pytest.raises(ValueError):
        validate_values({"value": value}, ("value",))


def test_declared_nullable_value_is_preserved():
    assert validate_values({"force_N": None, "raw": 4}, ("force_N", "raw"), ("force_N",)) == {
        "force_N": None, "raw": 4.0,
    }
    with pytest.raises(ValueError):
        validate_values({"force_N": None}, ("force_N",))


def test_two_scripts_keep_exact_shared_timestamps_in_separate_files(tmp_path):
    group = ScriptGroup([(DEMO, DEMO.defaults), (DEMO, {**DEMO.defaults, "amplitude_N": 5})], 50)
    group.start()
    try:
        wait_until(lambda: all(s.ready.is_set() or s.error for s in group.sources))
        assert not any(s.error for s in group.sources)
        t0 = time.monotonic()
        group.begin_recording(tmp_path / "trial.csv", "session-test", t0)
        for index in range(40):
            group.frame(SimpleNamespace(computer_monotonic_s=t0 + index / 50,
                                        frame_counter=index + 10, device_timestamp_us=9000 + 20000 * index))
    finally:
        group.stop()
    files = sorted(tmp_path.glob("*.script_*.csv"))
    assert len(files) == 2
    a, b = [list(csv.DictReader(p.open(encoding="utf-8"))) for p in files]
    assert len(a) == len(b) == 40
    for first, second in zip(a, b):
        for field in ("session_id", "sample_index", "time_s", "computer_monotonic_s", "frame_counter", "tracker_device_timestamp_us"):
            assert first[field] == second[field]
        assert first["synchronization"] == "simulation"
        assert float(first["force_N"]) == pytest.approx(2 * float(second["force_N"]))
    metadata = json.loads((tmp_path / "trial.scripts.json").read_text(encoding="utf-8"))
    assert metadata["state"] == "complete"
    assert metadata["hardware_synchronized"] is False
    assert all(s.process.poll() is not None for s in group.sources)


def test_best_effort_source_is_allowed_and_labeled(tmp_path):
    path = tmp_path / "software_source.py"
    path.write_text(
        'MEASUREMENT_SCRIPT = {"api_version": 1, "name": "Software", "columns": ["value"], '
        '"sync_mode": "best_effort_host_time_v1"}\n'
        'class Source:\n'
        '    def sample(self, tick): return {"value": float(tick["sample_index"])}\n'
        '    def close(self): pass\n'
        'def open_source(config, context): return Source()\n', encoding="utf-8")
    spec = ScriptSpec.read(path)
    assert not hardware_requirement([spec], "native")
    group = ScriptGroup([(spec, {})], 25)
    group.start()
    try:
        wait_until(group.sources[0].ready.is_set)
        t0 = time.monotonic()
        group.begin_recording(tmp_path / "software.csv", "best-effort", t0)
        for index in range(5):
            group.frame(SimpleNamespace(computer_monotonic_s=t0 + index / 25,
                                        frame_counter=index, device_timestamp_us=1000 + index * 40000))
    finally:
        group.stop()
    rows = list(csv.DictReader(next(tmp_path.glob("*.script_*.csv")).open(encoding="utf-8")))
    assert len(rows) == 5
    assert all(row["synchronization"] == "best_effort_host_time" for row in rows)
    manifest = json.loads((tmp_path / "software.scripts.json").read_text(encoding="utf-8"))
    assert manifest["synchronization"] == "best_effort_host_time"


def test_open_error_reported_and_process_exits(tmp_path):
    spec = custom(tmp_path, 'def open_source(config, context):\n    raise RuntimeError("device unavailable")\n')
    source = SourceProcess(spec, {}, 20)
    source.start()
    try:
        wait_until(lambda: bool(source.error))
        assert "device unavailable" in source.error
        assert source.snapshot()["state"] == "Fehler"
    finally:
        source.join()
    assert source.process.poll() is not None


def test_blocked_script_does_not_block_shutdown(tmp_path):
    spec = custom(tmp_path, 'import time\nclass Source:\n    def sample(self, tick):\n        time.sleep(60)\n    def close(self):\n        pass\ndef open_source(config, context):\n    return Source()\n')
    source = SourceProcess(spec, {}, 20)
    source.start()
    wait_until(source.ready.is_set)
    source.submit({"sample_index": 1, "time_s": 0})
    time.sleep(0.05)
    started = time.monotonic()
    source.join(timeout=0.1)
    assert time.monotonic() - started < 4
    assert source.error
    assert source.process.poll() is not None


def test_no_data_is_not_a_successful_sample(tmp_path):
    spec = custom(tmp_path, 'class Source:\n    def sample(self, tick):\n        return None\n    def close(self):\n        pass\ndef open_source(config, context):\n    return Source()\n')
    source = SourceProcess(spec, {}, 20)
    source.start()
    try:
        wait_until(source.ready.is_set)
        source.submit({"sample_index": 1, "time_s": 0})
        time.sleep(0.15)
        assert source.snapshot()["state"] == "Warte auf Daten"
        assert source.samples == 0
    finally:
        source.join()


def test_existing_output_is_never_overwritten(tmp_path):
    source = SourceProcess(DEMO, DEMO.defaults, 20)
    source.start()
    target = tmp_path / "existing.csv"
    target.write_text("preserve", encoding="utf-8")
    try:
        wait_until(source.ready.is_set)
        with pytest.raises(FileExistsError):
            source.begin_recording(target, "session")
        assert target.read_text() == "preserve"
    finally:
        source.join()


def test_force_parser_keeps_fragmented_lines_and_calibration(monkeypatch):
    import serial
    chunks = [b"80", b".5\r\n81\n"]
    class FakeSerial:
        def __init__(self, *args, **kwargs):
            self.closed = False
            self.dtr = self.rts = True
            self.port = None
        @property
        def in_waiting(self):
            return len(chunks[0]) if chunks else 0
        def open(self):
            pass
        def read(self, count):
            return chunks.pop(0) if chunks else b""
        def close(self):
            self.closed = True
    monkeypatch.setattr(serial, "Serial", FakeSerial)
    module_spec = importlib.util.spec_from_file_location("force_source", FORCE.path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    source = module.open_source(FORCE.defaults, {})
    assert source.serial.dtr is True
    assert source.serial.rts is True
    wait_until(lambda: len(source.records) == 2)
    with source.lock:
        first_received = source.records[0][1]
    values = source.sample({"computer_monotonic_s": first_received})
    assert values["force_N"] == pytest.approx(7.0)
    assert values["sensor_raw"] == pytest.approx(80.5)
    assert values["sensor_to_tracker_ms"] == pytest.approx(0)
    assert values["sensor_reused"] == 0
    again = source.sample({"computer_monotonic_s": first_received})
    assert again["sensor_reused"] == 1
    source.close()
    assert source.serial.closed


def test_manifest_is_final_at_stop_and_never_rewritten_or_recreated(tmp_path):
    group = ScriptGroup([(DEMO, DEMO.defaults)], 50)
    group.start()
    try:
        wait_until(group.sources[0].ready.is_set)
        t0 = time.perf_counter()
        group.begin_recording(tmp_path / "once.csv", "once", t0)
        group.frame(SimpleNamespace(computer_monotonic_s=t0, frame_counter=1, device_timestamp_us=1000))
    finally:
        group.stop(SimpleNamespace(frame_count=1, last_error=""))
    manifest = tmp_path / "once.scripts.json"
    saved = manifest.read_bytes()
    stamp = manifest.stat().st_mtime_ns
    assert json.loads(saved)["state"] == "complete"
    group.stop()
    assert manifest.read_bytes() == saved
    assert manifest.stat().st_mtime_ns == stamp
    manifest.rename(tmp_path / "moved.scripts.json")
    group.stop()
    assert not manifest.exists()


def test_existing_manifest_is_not_owned_or_changed_after_start_collision(tmp_path):
    manifest = tmp_path / "existing.scripts.json"
    manifest.write_text('{"state":"complete","previous":true}', encoding="utf-8")
    original = manifest.read_bytes()
    group = ScriptGroup([(DEMO, DEMO.defaults)], 50)
    with pytest.raises(FileExistsError):
        group.begin_recording(tmp_path / "existing.csv", "new", time.perf_counter())
    group.stop()
    assert manifest.read_bytes() == original
