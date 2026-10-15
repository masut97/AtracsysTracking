"""Fault injection into the real acquisition/UI pipeline, exclusively in mock mode."""
import hashlib
import json
from pathlib import Path
import time
import traceback
from dataclasses import replace


def run(output):
    from PySide6.QtCore import QTimer, Qt
    from atracsys_tracker import app
    from atracsys_tracker.adapters.mock import MockAdapter
    from atracsys_tracker.adapters.base import FrameTimeoutError
    from atracsys_tracker.ui.main_window import MainWindow

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = app.project_root()
    config_path, settings_path = root / "config.json", root / "measurement_scripts.json"
    saved = {p: p.read_bytes() if p.exists() else None for p in (config_path, settings_path)}
    config = json.loads(saved[config_path])
    config.update(adapter_mode="mock", native_bridge_enabled=False, registry_control_enabled=False,
                  auto_analysis_enabled=True, measurement_directory=str(output), log_directory=str(output / "logs"))
    test_config = output / "test_config.json"
    test_config.write_text(json.dumps(config))
    original_init, original_read, original_connect = MainWindow.__init__, MockAdapter.read_frame, MockAdapter.connect
    state = {"connections": 0, "fault": False, "phase": "starting", "empty_frames": 0}
    errors = []
    name = "camera_fault_" + str(time.time_ns())
    first_json, first_csv = output / (name + ".json"), output / (name + ".csv")

    def connect(self, *args, **kwargs):
        result = original_connect(self, *args, **kwargs)
        state["connections"] += 1
        state["fault"] = False
        return result

    def read(self, *args, **kwargs):
        if state["fault"]:
            time.sleep(0.02)
            raise FrameTimeoutError("injected camera freeze")
        packet = original_read(self, *args, **kwargs)
        state["empty_frames"] += 1
        return replace(packet, markers=())

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.record_directory_edit.setText(str(output))
        self.record_name_edit.setText(name)
        self.record_frequency_spin.setValue(20)
        for row in range(self.script_panel.table.rowCount()):
            self.script_panel.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        state["started"] = time.monotonic()
        self._start_recording("ui")
        assert self.recorder.stats().state == "Gestoppt", "Recording started without camera warmup"
        assert not first_csv.exists()
        deadline = time.monotonic() + 25

        def tick():
            try:
                now = time.monotonic()
                assert now < deadline, state
                stats = self.recorder.stats()
                if state["phase"] == "starting" and stats.frame_count >= 15 and stats.state != "Gestoppt":
                    assert now - state["started"] >= 0.5
                    assert self.camera_guard.ready()
                    state.update(phase="frozen", fault=True, fault_at=now)
                elif state["phase"] == "frozen" and stats.state == "Gestoppt":
                    assert now - state["fault_at"] < 6
                    report = json.loads(first_json.read_text())
                    assert "camera_stream_error" in report
                    assert self._analysis_process is None
                    assert not first_csv.with_suffix(".scripts.json").exists()
                    state.update(phase="reconnecting", saved_hash=hashlib.sha256(first_csv.read_bytes()).hexdigest())
                elif state["phase"] == "reconnecting" and state["connections"] >= 2 and self.camera_guard.ready():
                    self.auto_analysis_check.setChecked(False)
                    self.record_name_edit.setText(name + "_recovered")
                    self._start_recording("ui")
                    state["phase"] = "recovered"
                elif state["phase"] == "recovered" and stats.state != "Gestoppt" and stats.frame_count >= 10:
                    self._stop_recording("ui")
                    assert hashlib.sha256(first_csv.read_bytes()).hexdigest() == state["saved_hash"]
                    report = json.loads((output / (name + "_recovered.json")).read_text())
                    assert "camera_stream_error" not in report
                    state["phase"] = "done"
                    self.close()
                    return
                QTimer.singleShot(100, tick)
            except Exception:
                errors.append(traceback.format_exc())
                self.close()
        QTimer.singleShot(100, tick)

    MockAdapter.read_frame, MockAdapter.connect, MainWindow.__init__ = read, connect, init
    try:
        code = app.main(["--config", str(test_config), "--mode", "mock", "--auto-quit", "30"])
        assert not errors, errors
        assert state["phase"] == "done", state
        result = {"passed": code == 0, "empty_marker_frames_continue": True,
                  "start_waits_for_stable_stream": True, "freeze_finalizes_partial_recording": True,
                  "automatic_reconnect": True, "recording_after_reconnect": True,
                  "hardware_used": False}
    except Exception:
        result = {"passed": False, "error": traceback.format_exc()}
    finally:
        MockAdapter.read_frame, MockAdapter.connect, MainWindow.__init__ = original_read, original_connect, original_init
        for path, content in saved.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
    (output / "result.json").write_text(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1
