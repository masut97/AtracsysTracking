"""Explicit hooks into the inspected installed version; original modules stay intact."""
from __future__ import annotations

import time


def install():
    from atracsys_tracker import recording
    from atracsys_tracker.ui.main_window import MainWindow
    from .layout import organize_window
    from .panel import ScriptPanel
    from .adapter import validate_tracker_contract
    from atracsys_tracker.adapters.native_bridge import NativeBridgeAdapter
    from .network import discover_camera
    from atracsys_tracker.acquisition import AcquisitionWorker
    from atracsys_tracker.adapters.base import FrameTimeoutError
    from .camera_health import CameraGuard, GuardedAdapter, StreamHealth

    Worker = recording.RecordingWorker
    if getattr(Worker, "_measurement_extension_installed", False):
        return
    validate_tracker_contract(recording, MainWindow, NativeBridgeAdapter)

    start_original = Worker.start_session
    enqueue_original = Worker.enqueue
    write_original = Worker._write_frame
    stop_original = Worker.stop
    window_init = MainWindow.__init__
    window_start = MainWindow._start_recording
    window_toggle = MainWindow._toggle_recording
    window_refresh = MainWindow._refresh_recording_status
    window_close = MainWindow.closeEvent
    bridge_connect = NativeBridgeAdapter.connect
    new_adapter = AcquisitionWorker._new_adapter
    launch_analysis = MainWindow._maybe_launch_analysis

    def guarded_adapter(self, *args, **kwargs):
        if not hasattr(self, "_camera_health"):
            self._camera_health = StreamHealth()
        return GuardedAdapter(new_adapter(self, *args, **kwargs), self._camera_health, FrameTimeoutError)

    def maybe_analyze(self, *args, **kwargs):
        if getattr(self.recorder, "_camera_abort", False):
            return
        return launch_analysis(self, *args, **kwargs)

    def connect_bridge(self, *args, **kwargs):
        discover_camera(self.sdk_root)
        return bridge_connect(self, *args, **kwargs)

    extra_columns = ["shared_sample_index", "time_since_start_s", "synchronization"]
    recording.CSV_COLUMNS.extend(c for c in extra_columns if c not in recording.CSV_COLUMNS)

    def start_session(self, *args, **kwargs):
        group = getattr(self, "_script_group", None)
        try:
            # Hold the existing re-entrant lock until auxiliary writers are initialized.
            # The acquisition and write hooks acquire it before consuming a frame.
            with self._lock:
                health = self._camera_source._camera_health.snapshot()
                if not health["stable"]:
                    raise RuntimeError("Kameradaten sind noch nicht stabil; bitte erneut starten.")
                self._camera_epoch = health["epoch"]
                self._camera_last_sample = time.monotonic()
                self._camera_abort = False
                self._sync_ticks = {}
                result = start_original(self, *args, **kwargs)
                if group is not None:
                    self._script_started_counter = time.perf_counter()
                    group.begin_recording(self._csv_path, self._session_id, self._script_started_counter)
                    self._metadata["measurement_scripts"] = {
                        "manifest": str(group.metadata_path), "hardware_synchronized": False,
                        "mode": group.synchronization, "clock": "Windows QueryPerformanceCounter"}
                return result
        except Exception:
            if self.stats().state != "Gestoppt":
                stop_original(self)
            if group is not None:
                group.stop()
            raise

    def enqueue(self, frame):
        with self._lock:
            group = getattr(self, "_script_group", None)
            if frame.computer_monotonic_s < getattr(self, "_started_monotonic", 0):
                return False
            accepted = enqueue_original(self, frame)
            if accepted:
                self._camera_last_sample = time.monotonic()
            if accepted and group is not None and group.recording:
                tick = group.frame(frame, reference_time_s=time.perf_counter())
                self._sync_ticks[(frame.frame_counter, frame.computer_monotonic_s)] = tick
            return accepted

    def write_frame(self, writer, frame):
        with self._lock:
            group = getattr(self, "_script_group", None)
            tick = getattr(self, "_sync_ticks", {}).pop((frame.frame_counter, frame.computer_monotonic_s), None)
        if tick is None:
            return write_original(self, writer, frame)
        synchronization = group.synchronization if group is not None else "not_synchronized"

        class Writer:
            def writerow(_, row):
                row.update(computer_monotonic_s=tick["computer_monotonic_s"],
                           shared_sample_index=tick["sample_index"], time_since_start_s=tick["time_s"],
                           synchronization=synchronization)
                return writer.writerow(row)

        return write_original(self, Writer(), frame)

    def stop(self, *args, **kwargs):
        result = stop_original(self, *args, **kwargs)
        group = getattr(self, "_script_group", None)
        if group is not None:
            group.stop(tracker_stats=result)
            problems = [s.snapshot() for s in group.sources if s.error or s.rows != group.sample_index]
            if problems:
                self._last_error = "Messskript-Aufnahme unvollständig. Details in der .scripts.json-Datei."
            self._script_group = None
            return self.stats()
        return result

    def init_window(self, *args, **kwargs):
        window_init(self, *args, **kwargs)
        self.recorder._camera_source = self.acquisition
        self.camera_guard = CameraGuard(self)
        self.script_panel = ScriptPanel(self, window_start)
        self.centralWidget().layout().insertWidget(3, self.script_panel)
        organize_window(self, self.script_panel)
        self.setWindowTitle(self.windowTitle() + " · Messskripte")

    def start_window(self, *args, **kwargs):
        updater = getattr(self, "update_controller", None)
        if updater is not None and updater.busy():
            self._set_recording_message("Bitte das laufende Update abwarten.", "ready")
            return
        return self.script_panel.start_recording(*args, **kwargs)

    def toggle_window(self):
        if self.script_panel.pending is not None:
            self.script_panel.cancel()
            return
        return window_toggle(self)

    def refresh_window(self, *args, **kwargs):
        result = window_refresh(self, *args, **kwargs)
        panel = getattr(self, "script_panel", None)
        if panel is not None and panel.pending is not None:
            self.record_button.setText("Vorbereitung stoppen")
            self._set_recording_message("Messgeräte werden vorbereitet …", "ready")
        return result

    def close_window(self, event):
        updater = getattr(self, "update_controller", None)
        if updater is not None and updater.busy():
            updater.status.setText("Update läuft. Bitte vor dem Schließen den Abschluss abwarten.")
            event.ignore()
            return
        try:
            window_close(self, event)
        finally:
            self.script_panel.shutdown()

    Worker.start_session = start_session
    Worker.enqueue = enqueue
    Worker._write_frame = write_frame
    Worker.stop = stop
    Worker._measurement_extension_installed = True
    MainWindow.__init__ = init_window
    MainWindow._start_recording = start_window
    MainWindow._toggle_recording = toggle_window
    MainWindow._refresh_recording_status = refresh_window
    MainWindow.closeEvent = close_window
    MainWindow._maybe_launch_analysis = maybe_analyze
    AcquisitionWorker._new_adapter = guarded_adapter
    NativeBridgeAdapter.connect = connect_bridge
