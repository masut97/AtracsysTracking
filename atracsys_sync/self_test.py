"""End-to-end mock-only check inside the actual packaged Qt application."""
import csv
import json
from pathlib import Path
import time
import traceback


class _MemoryRegistry:
    """No Windows registry or real external software is touched by this test."""
    HKEY_CURRENT_USER=1; KEY_READ=1; KEY_WRITE=2
    REG_SZ=1; REG_DWORD=4; REG_QWORD=11
    def __init__(self): self.data={}
    def CreateKeyEx(self,*_args): return self
    def OpenKey(self,*_args): return self
    def __enter__(self): return self
    def __exit__(self,*_args): pass
    def QueryValueEx(self,_key,name):
        if name not in self.data: raise FileNotFoundError(name)
        return self.data[name]
    def SetValueEx(self,_key,name,_zero,kind,value): self.data[name]=(value,kind)


def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QPoint, QRect, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QToolButton, QPushButton
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
        assert self.workspace_tabs.count() == 4
        tab_names = tuple(self.workspace_tabs.tabText(i).split(" (")[0] for i in range(4))
        assert tab_names == ("Live", "Messgeräte", "Auswertung", "Einstellungen"), tab_names
        assert self.findChild(QPushButton, 'stopRecordingButton') is not None
        assert self.record_frequency_spin.lineEdit().isReadOnly() is False
        self.record_frequency_spin.lineEdit().setText('20')
        self.record_frequency_spin.interpretText()
        assert self.record_frequency_spin.value() == 20
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
        assert {'auto_auswertung.py', 'rom_auswertung.py', 'trochanter_auswertung.py', 'free_auswertung.py'} <= base_choices
        assert len(base_choices) <= 5, base_choices
        profiles.setChecked(True)
        expanded = {Path(combo.itemData(i)).name for i in range(1, combo.count())}
        assert {'Auswertung_299_331_332.py', 'Auswertung_299_331_333.py', 'Auswertung_340_341_342.py'} <= expanded
        assert not any(name.endswith('_core.py') for name in expanded)
        assert combo.currentData() == selected_analysis
        profiles.setChecked(False)
        assert combo.currentData() == selected_analysis
        assert {Path(combo.itemData(i)).name for i in range(1, combo.count())} == base_choices
        from .free_analysis_ui import ProfileDialog, backend, save_profile
        from PySide6.QtGui import QCloseEvent
        assert self.findChild(QPushButton, 'freeAnalysisSetupButton') is not None
        assert self.findChild(QPushButton, 'freeAnalysisCsvButton') is not None
        sample_profile = dict(version=1, name='General profile test', reference=101, baseline_seconds=0,
            markers={'101':'Reference', '102':'Object A', '103':'Object B', '104':'Object C'},
            comparisons=[dict(name='B relative to A', anchor=102, target=103, outputs=['tx','rotation_axis'], axis=[0,0,1]),
                         dict(name='C relative to reference', anchor=101, target=104, outputs=['path'], axis=[1,0,0])])
        editor = ProfileDialog(root, sample_profile, self)
        assert editor.value() == backend(root).validate_profile(sample_profile)
        stored = output/'test_free_profile.json'
        save_profile(stored, editor.value(), backend(root).validate_profile)
        assert backend(root).load_profile(stored) == editor.value()
        editor.deleteLater()
        visible = {'299:10':'Reference instance', '301:20':'First 301', '301:21':'Second 301',
                   '302:30':'First 302', '302:31':'Second 302'}
        instances = ProfileDialog(root, parent=self, visible_choices=lambda: visible)
        assert instances.markers.rowCount() == 5
        instances.add_pair(dict(name='Two instances', anchor='301:20', target='301:21', outputs=['tx'], axis=[0,0,1]))
        instances.reference.setCurrentIndex(instances.reference.findData('299:10'))
        assert instances.value()['comparisons'][0]['target'] == '301:21'
        instances.resize(1100, 800); instances.show(); QApplication.processEvents()
        instances.grab().save(str(output/'free-instances.png'))
        visible.pop('301:21')
        instances.refresh_choices()
        assert instances.pairs.cellWidget(0, 2).findData('301:21') == -1
        try:
            instances.value()
        except ValueError:
            pass
        else:
            raise AssertionError('Invisible instance accepted')
        instances.close(); instances.deleteLater()
        controller = self.free_analysis_controller
        original_start = self.script_panel.start_recording
        calls = []
        self.script_panel.start_recording = lambda *_a, **_k: calls.append(True)
        try:
            controller.editing = True
            self._start_recording('ui')
            assert not calls, 'Recording started during profile editing'
            close = QCloseEvent(); self.closeEvent(close)
            assert not close.isAccepted(), 'Window closed during free-analysis activity'
            controller.editing = False
            self._start_recording('ui')
            assert calls == [True]
        finally:
            controller.editing = False
            self.script_panel.start_recording = original_start
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
            assert self.record_button.text() == "Aufnahme starten"
            assert not self.record_button.isEnabled()
            assert self.stop_record_button.isEnabled()
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
                    # Cancelling a new preparation must not claim that the
                    # preceding measurement is the newly saved test.
                    from .external_control import reported_state
                    cancelled_name=recording_name+'_cancelled'
                    self.record_name_edit.setText(cancelled_name)
                    self._start_recording('ui')
                    assert self.script_panel.pending is not None
                    self.zwick_controller.stop()
                    assert self.script_panel.pending is None
                    assert reported_state(self.recorder.stats(),False,self.zwick_controller.cancelled)=='Gestoppt'
                    assert not (output/(cancelled_name+'.csv')).exists()
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
                assert self.findChild(QPushButton, "softwareUpdateButton") is not None

            def capture_settings():
                self.workspace_tabs.setCurrentIndex(3)
                capture('settings.png')
                button=self.findChild(QPushButton,'softwareUpdateButton')
                assert button.isVisible()
                assert button.grab().save(str(output/'update-button.png'))

            def external_stop():
                memory=self._external_control.registry
                memory.data['RecordingFrequencyHz']=('invalid but stop must work',memory.REG_SZ)
                memory.data['StopRecord']=(1,memory.REG_DWORD)
                self.zwick_controller.poll()
                assert self.recorder.stats().state=='Gestoppt'
                assert self._external_control.values()['StopRecord']==0
                assert self._external_control.values()['StartRecord']==0
                self._external_control.write_frequency(20)

            self.workspace_tabs.setCurrentIndex(0)
            capture("live.png")
            QTimer.singleShot(250, protect(lambda: self.workspace_tabs.setCurrentIndex(1)))
            QTimer.singleShot(500, protect(lambda: capture("running.png")))
            QTimer.singleShot(750, protect(lambda: self.workspace_tabs.setCurrentIndex(2)))
            QTimer.singleShot(1000, protect(capture_analysis))
            QTimer.singleShot(1200, protect(capture_settings))
            QTimer.singleShot(1350, protect(lambda: self.workspace_tabs.setCurrentIndex(0)))
            QTimer.singleShot(1400, protect(external_stop))
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
            from .external_control import RegistryControl
            memory=_MemoryRegistry()
            self._external_control=RegistryControl(r'Software\IsolatedTest\Control',registry=memory)
            memory.data.update(RecordingName=(recording_name,memory.REG_SZ),
                RecordingDirectory=(str(output),memory.REG_SZ),RecordingFrequencyHz=('20',memory.REG_SZ))
            self.zwick_controller.poll()  # observe reset before accepting an edge
            assert self._external_control.values()['AppReady']==1
            self._external_control.write_start_record(True)
            self.zwick_controller.poll()
            assert self.script_panel.pending is not None
            assert not self.record_name_edit.isEnabled()
            assert not self.record_directory_edit.isEnabled()
            memory.data['RecordingName']=('must_not_change_active_session',memory.REG_SZ)
            memory.data['RecordingFrequencyHz']=('invalid while preparing',memory.REG_SZ)
            self.zwick_controller.poll()
            assert self.record_name_edit.text()==recording_name
            assert self.record_frequency_spin.value()==20
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
                  "free_profile_editor_save_and_activity_guards": True,
                  "zwick_start_stop_and_frozen_parameters": True,
                  "separate_start_stop_and_settings_tab": True,
                  "cancelled_preparation_not_reported_as_saved": True,
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
