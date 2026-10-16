"""End-to-end mock-only check inside the actual packaged Qt application."""
import csv
import json
from pathlib import Path
import time
import traceback


def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QPoint, QRect, Qt, QTimer
    from PySide6.QtWidgets import QToolButton, QPushButton
    from atracsys_tracker import app
    from atracsys_tracker.ui.main_window import MainWindow
    from .protocol import hardware_requirement
    root = app.project_root()
    root_config_path = root / "config.json"
    original_root_config = root_config_path.read_bytes()
    settings_path = root / "measurement_scripts.json"
    original_settings = settings_path.read_bytes() if settings_path.exists() else None
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    config.update(adapter_mode="mock", native_bridge_enabled=False, registry_control_enabled=False,
                  auto_analysis_enabled=False, measurement_directory=str(output), log_directory=str(output / "logs"))
    config_path = output / "test_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    original_init = MainWindow.__init__
    windows = []
    errors = []
    recording_name = "gui_test_" + str(time.time_ns())
    completed_cycles = []

    def protect(callback):
        def call():
            try:
                callback()
            except Exception:
                errors.append(traceback.format_exc())
        return call

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        windows.append(self)
        self.record_name_edit.setText(recording_name)
        self.record_directory_edit.setText(str(output))
        self.record_frequency_spin.setValue(20)
        for row, spec in enumerate(self.script_panel.specs):
            self.script_panel.table.item(row, 0).setCheckState(Qt.CheckState.Checked if spec.simulated else Qt.CheckState.Unchecked)
        assert self.workspace_tabs.count() == 3
        tab_names = tuple(self.workspace_tabs.tabText(i).split(" (")[0] for i in range(3))
        assert tab_names in {("Live", "Messgeräte", "Auswertung"), ("Live", "Devices", "Analysis")}, tab_names
        measurement_menu = self.findChild(QToolButton, "measurementMenuButton")
        workspace_menu = self.findChild(QToolButton, "workspaceMenuButton")
        assert measurement_menu is not None and measurement_menu.menu() is not None
        assert workspace_menu is not None and len(workspace_menu.menu().actions()) == 2
        assert self.findChild(QPushButton, "softwareUpdateButton") is not None
        from PySide6.QtWidgets import QCheckBox
        profiles = self.findChild(QCheckBox, "analysisProfilesToggle")
        assert profiles is not None and not profiles.isChecked()
        combo = self.analysis_combo
        selected_analysis = combo.currentData()
        base_choices = {Path(combo.itemData(i)).name for i in range(1, combo.count())}
        assert {'auto_auswertung.py', 'rom_auswertung.py', 'trochanter_auswertung.py'} <= base_choices
        assert len(base_choices) <= 4, base_choices
        profiles.setChecked(True)
        expanded = {Path(combo.itemData(i)).name for i in range(1, combo.count())}
        assert {'Auswertung_299_331_332.py', 'Auswertung_299_331_333.py', 'Auswertung_340_341_342.py'} <= expanded
        assert not any(name.endswith('_core.py') for name in expanded)
        assert combo.currentData() == selected_analysis
        profiles.setChecked(False)
        assert combo.currentData() == selected_analysis
        assert {Path(combo.itemData(i)).name for i in range(1, combo.count())} == base_choices
        assert not self.workspace_tabs.isAncestorOf(self.marker_scroll)
        assert self.marker_scroll.parentWidget() is self.marker_status_footer

        deadline = None

        def recording_diagnostics():
            panel = self.script_panel
            return {"recorder": repr(self.recorder.stats()),
                    "panel_message": panel.message.text(),
                    "pending": repr(panel.pending),
                    "preparation_elapsed_s": time.monotonic() - panel.test_started,
                    "button": self.record_button.text(),
                    "sources": [source.snapshot() for source in panel.group.sources] if panel.group else []}

        def capture(filename):
            assert self.marker_status_footer.isVisible()
            assert self.marker_scroll.isVisible()
            footer = self.marker_status_footer
            bounds = QRect(footer.mapTo(self, QPoint(0, 0)), footer.size())
            visible_child = self.childAt(bounds.center())
            geometry = {"window": self.rect().getRect(), "footer": bounds.getRect(),
                        "tabs": self.workspace_tabs.geometry().getRect(),
                        "center_child": visible_child.objectName() if visible_child else None,
                        "center_child_type": type(visible_child).__name__ if visible_child else None}
            (output / (filename + ".layout.json")).write_text(json.dumps(geometry), encoding="utf-8")
            assert self.rect().contains(bounds), geometry
            assert visible_child is footer or footer.isAncestorOf(visible_child), geometry
            self.grab().save(str(output / filename))

        def begin_second():
            first_manifest = output / (recording_name + ".scripts.json")
            assert (output / (recording_name + ".json")).exists()
            first_bytes = first_manifest.read_bytes()
            assert json.loads(first_bytes)["state"] == "complete"
            first_stamp = first_manifest.stat().st_mtime_ns
            completed_cycles.append(recording_name)
            second_name = recording_name + "_second"
            self.record_name_edit.setText(second_name)
            self._start_recording("ui")
            assert self.record_button.text() == "Vorbereitung stoppen"
            assert "Fehler" not in measurement_menu.text()
            second_deadline = time.monotonic() + 16

            def finish_second():
                stats = self.recorder.stats()
                if stats.state != "Gestoppt" and stats.frame_count >= 20:
                    assert first_manifest.read_bytes() == first_bytes
                    assert first_manifest.stat().st_mtime_ns == first_stamp
                    self._stop_recording("ui")
                    assert (output / (second_name + ".json")).exists()
                    second_manifest = json.loads((output / (second_name + ".scripts.json")).read_text(encoding="utf-8"))
                    assert second_manifest["state"] == "complete"
                    completed_cycles.append(second_name)
                    self.close()
                elif time.monotonic() >= second_deadline:
                    errors.append("Zweite Aufnahme wurde nicht rechtzeitig bereit: " + json.dumps(recording_diagnostics(), ensure_ascii=False))
                    self.close()
                else:
                    QTimer.singleShot(200, protect(finish_second))
            QTimer.singleShot(200, protect(finish_second))

        def capture_tabs_and_stop():
            def capture_analysis():
                capture("analysis.png")
                button = self.findChild(QPushButton, "softwareUpdateButton")
                assert button is not None and button.isVisible()
                assert button.grab().save(str(output / "update-button.png"))

            self.workspace_tabs.setCurrentIndex(0)
            capture("live.png")
            QTimer.singleShot(250, protect(lambda: self.workspace_tabs.setCurrentIndex(1)))
            QTimer.singleShot(500, protect(lambda: capture("running.png")))
            QTimer.singleShot(750, protect(lambda: self.workspace_tabs.setCurrentIndex(2)))
            QTimer.singleShot(1000, protect(capture_analysis))
            QTimer.singleShot(1250, protect(lambda: self.workspace_tabs.setCurrentIndex(0)))
            QTimer.singleShot(1400, protect(lambda: self._stop_recording("ui")))
            QTimer.singleShot(1750, protect(lambda: capture("stopped.png")))
            QTimer.singleShot(1950, protect(begin_second))

        def wait_for_recording():
            stats = self.recorder.stats()
            if stats.state != "Gestoppt" and stats.frame_count >= 20:
                capture_tabs_and_stop()
            elif deadline is not None and time.monotonic() >= deadline:
                self.grab().save(str(output / "failed.png"))
                errors.append("Aufnahme wurde im UI-Test nicht innerhalb von 16 Sekunden bereit: " + json.dumps(recording_diagnostics(), ensure_ascii=False))
                self.close()
            else:
                QTimer.singleShot(200, wait_for_recording)

        def begin_first():
            nonlocal deadline
            self._start_recording("ui")
            deadline = time.monotonic() + 16

        QTimer.singleShot(1000, protect(begin_first))
        QTimer.singleShot(1200, wait_for_recording)

    MainWindow.__init__ = init
    try:
        result = app.main(["--config", str(config_path), "--mode", "mock", "--auto-quit", "40"])
        if errors:
            raise AssertionError("\n".join(errors))
        assert len(completed_cycles) == 2, completed_cycles
        csv_path = output / (recording_name + ".csv")
        tracker_rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        assert tracker_rows, "Keine Tracker-Daten"
        by_index = {}
        for row in tracker_rows:
            index = row["shared_sample_index"]
            assert index, "Gemeinsame Sample-ID fehlt"
            assert row["synchronization"] == "simulation"
            by_index[index] = row
        manifest = json.loads(csv_path.with_suffix(".scripts.json").read_text(encoding="utf-8"))
        tracker_metadata = json.loads(csv_path.with_suffix(".json").read_text(encoding="utf-8"))
        assert manifest["state"] == "complete", manifest
        expected_scripts = len([spec for spec in windows[0].script_panel.specs if spec.simulated])
        assert expected_scripts >= 1, "Kein Demo-Messskript installiert"
        assert len(manifest["scripts"]) == expected_scripts, manifest
        script_rows = []
        for script in manifest["scripts"]:
            rows = list(csv.DictReader(Path(script["path"]).open(encoding="utf-8")))
            # The existing tracker omits marker rows when no marker is visible.
            # Auxiliary sources must still contain one row for EVERY recorded frame.
            assert len(rows) == tracker_metadata["frame_count"], (len(rows), tracker_metadata["frame_count"])
            script_rows.append(rows)
            for row in rows:
                if row["sample_index"] not in by_index:
                    continue
                tracker = by_index[row["sample_index"]]
                assert row["computer_monotonic_s"] == tracker["computer_monotonic_s"]
                assert row["frame_counter"] == tracker["frame_counter"]
                assert row["tracker_device_timestamp_us"] == tracker["device_timestamp_us"]
                assert row["time_s"] == tracker["time_since_start_s"]
                assert row["synchronization"] == "simulation"
        for rows in script_rows[1:]:
            assert [row["computer_monotonic_s"] for row in script_rows[0]] == [row["computer_monotonic_s"] for row in rows]
        software_sources = [spec for spec in windows[0].script_panel.specs
                            if spec.sync_mode == "best_effort_host_time_v1"]
        assert software_sources, "Kein Software-Sync-Messskript installiert"
        assert not hardware_requirement(software_sources, "native")
        report = {"passed": True, "frames": tracker_metadata["frame_count"], "marker_rows": len(tracker_rows),
                  "script_count": len(manifest["scripts"]), "frequency_hz": 20,
                  "exact_reference_timestamps": True, "hardware_synchronized": False,
                  "recording_cycles": len(completed_cycles), "json_final_immediately_after_stop": True,
                  "marker_footer_visible_in_all_tabs": True,
                  "curated_analysis_selection_and_profile_toggle": True,
                  "source_processes_stopped": all(s.process.poll() is not None for s in windows[0].script_panel.group.sources)}
        (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return result
    except Exception:
        (output / "result.json").write_text(json.dumps({"passed": False, "error": traceback.format_exc()}, indent=2), encoding="utf-8")
        return 1
    finally:
        MainWindow.__init__ = original_init
        root_config_path.write_bytes(original_root_config)
        if original_settings is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(original_settings)
