"""Measurement selection and health display embedded in the original tracker UI."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox)

from .protocol import ScriptSpec, hardware_requirement
from .runner import ScriptGroup
from .status import format_values, synchronization_status


class ScriptPanel(QGroupBox):
    def __init__(self, window, original_start):
        super().__init__("MESSSKRIPTE · GEMEINSAM STARTEN / GETRENNT SPEICHERN", window)
        self.window = window
        self.original_start = original_start
        self.root = Path(window.project_root)
        self.directory = self.root / "measurement_scripts"
        self.settings_path = self.root / "measurement_scripts.json"
        self.group = None
        self.pending = None
        self.testing = False
        self.test_index = 0
        self.test_started = 0.0
        self.ready_at = None
        self.specs = []
        self.settings = {}
        try:
            if self.settings_path.exists():
                self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        toolbar = QHBoxLayout()
        self.import_button = QPushButton("Skript importieren …")
        self.config_button = QPushButton("Einstellungen …")
        self.test_button = QPushButton("Verbindung testen")
        self.details_button = QPushButton("Details …")
        self.help_button = QPushButton("Anleitung")
        for button in (self.import_button, self.config_button, self.test_button, self.details_button, self.help_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Auswahl / Messskript", "Verbindung", "Datenrate", "Letzter Wert", "Synchronisierung"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(100)
        self.table.setMaximumHeight(220)
        layout.addWidget(self.table)
        self.message = QLabel("Messskripte auswählen. Ohne Trigger wird die gemessene Software-Zeitabweichung gespeichert.")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #edb55e; padding: 2px;")
        layout.addWidget(self.message)
        self.import_button.clicked.connect(self.import_script)
        self.config_button.clicked.connect(self.configure)
        self.test_button.clicked.connect(self.test)
        self.details_button.clicked.connect(self.details)
        self.help_button.clicked.connect(self.help)
        self.reload()
        self.table.itemChanged.connect(self.save)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def reload(self):
        self.table.blockSignals(True)
        self.specs = []
        errors = []
        self.directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.directory.glob("*.py")):
            try:
                self.specs.append(ScriptSpec.read(path))
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
        self.table.setRowCount(len(self.specs))
        self.table.setMinimumHeight(min(220, 40 + 36 * len(self.specs)))
        for row, spec in enumerate(self.specs):
            item = QTableWidgetItem(spec.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = self.settings.get(spec.path.name, {}).get("selected", False)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setToolTip(str(spec.path))
            self.table.setItem(row, 0, item)
            if spec.simulated:
                sync_text = "Simulation"
            elif spec.sync_mode == "best_effort_host_time_v1":
                sync_text = "Software-Zeitabgleich"
            elif spec.sync_mode == "atracsys_timestamp_correlated_v1":
                sync_text = "Zeitabstand messbar"
            else:
                sync_text = "Hardware-Kopplung fehlt"
            for column, text in enumerate(["Nicht getestet", "—", "—", sync_text], 1):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.blockSignals(False)
        if errors:
            self.message.setText("Nicht geladen: " + " | ".join(errors))
        refresh = getattr(self, "overview_refresh", None)
        if refresh:
            refresh()

    def entries(self):
        result = []
        for row, spec in enumerate(self.specs):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                config = dict(spec.defaults)
                config.update(self.settings.get(spec.path.name, {}).get("config", {}))
                result.append((spec, config))
        return result

    def save(self, *_):
        for row, spec in enumerate(self.specs):
            entry = self.settings.setdefault(spec.path.name, {})
            entry["selected"] = self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        try:
            temporary = self.settings_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.settings_path)
        except OSError as exc:
            self.message.setText(f"Einstellungen konnten nicht gespeichert werden: {exc}")
        refresh = getattr(self, "overview_refresh", None)
        if refresh:
            refresh()

    def import_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Python-Messskript importieren", "", "Python-Messskripte (*.py)")
        if not path:
            return
        try:
            spec = ScriptSpec.read(Path(path))
            destination = self.directory / spec.path.name
            if destination.resolve() != spec.path:
                if destination.exists():
                    raise FileExistsError("Ein Skript mit diesem Dateinamen ist bereits vorhanden.")
                with destination.open("xb") as target, spec.path.open("rb") as source:
                    shutil.copyfileobj(source, target)
            self.reload()
            self.message.setText(f"{spec.name} importiert. Zum Start links auswählen.")
        except Exception as exc:
            QMessageBox.warning(self, "Import fehlgeschlagen", str(exc))

    def configure(self):
        row = self.table.currentRow()
        if row < 0:
            self.message.setText("Zuerst eine Skriptzeile anklicken.")
            return
        spec = self.specs[row]
        config = dict(spec.defaults)
        config.update(self.settings.get(spec.path.name, {}).get("config", {}))
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{spec.name} · Einstellungen")
        dialog.resize(460, 280)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        labels = {"port": "COM-Anschluss", "baudrate": "Baudrate", "offset_N": "Nullpunktkorrektur [N]",
                  "dtr": "USB DTR aktiv", "rts": "USB RTS aktiv",
                  "amplitude_N": "Amplitude [N]", "frequency_Hz": "Signalfrequenz [Hz]",
                  "no_data_timeout_s": "Start-Zeitlimit ohne Daten [s]",
                  "stale_timeout_s": "Abbruch bei Datenstillstand [s]"}
        fields = {}
        for key, value in config.items():
            if isinstance(value, bool):
                editor = QCheckBox()
                editor.setChecked(value)
            elif isinstance(value, int):
                editor = QSpinBox()
                editor.setRange(-1_000_000_000, 1_000_000_000)
                editor.setValue(value)
            elif isinstance(value, float):
                editor = QDoubleSpinBox()
                editor.setRange(-1e9, 1e9)
                editor.setDecimals(6)
                editor.setValue(value)
            else:
                editor = QLineEdit(value if isinstance(value, str) else json.dumps(value))
            fields[key] = editor
            form.addRow(labels.get(key, key), editor)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = {}
            for key, editor in fields.items():
                if isinstance(editor, QCheckBox):
                    updated[key] = editor.isChecked()
                elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                    updated[key] = editor.value()
                else:
                    updated[key] = editor.text() if isinstance(config[key], str) else json.loads(editor.text())
            self.settings.setdefault(spec.path.name, {})["config"] = updated
            self.save()
        except ValueError as exc:
            QMessageBox.warning(self, "Einstellungen nicht gespeichert", str(exc))

    def _new_group(self, entries):
        if len(entries) > 8:
            raise ValueError("Maximal acht Messskripte gleichzeitig auswählen.")
        if self.group:
            self.group.stop()
        self.group = ScriptGroup(entries, self.window.record_frequency_spin.value())
        self.test_started = time.monotonic()
        self.ready_at = None
        self.test_index = 0
        self.group.start()

    def test(self):
        if self.testing or self.pending is not None:
            self.cancel()
            return
        entries = self.entries()
        if not entries:
            self.message.setText("Bitte mindestens ein Messskript auswählen.")
            return
        try:
            self._new_group(entries)
            self.testing = True
            self.message.setText("Verbindungstest läuft (5 s ab Bereitschaft). Es werden keine Messdateien erstellt.")
        except Exception as exc:
            self.message.setText(str(exc))

    def start_recording(self, *args, **kwargs):
        if self.pending is not None:
            return False
        entries = self.entries()
        if self.window.recorder.stats().state != "Gestoppt":
            return False
        problem = hardware_requirement([spec for spec, _ in entries], self.window.status_store.get().mode)
        if problem:
            self.message.setText(problem)
            self.window._set_recording_message("Synchronisationsmodus nicht verfügbar", "error")
            self.window._external_control.write_status("Fehler", last_error=problem)
            return False
        try:
            self.testing = False
            self._new_group(entries)
            self.pending = (args, kwargs)
            self.window.recorder._last_error = ""
            self.message.setText("Kamera und Messskripte werden geprüft. Aufnahme startet nach stabiler Datenübertragung.")
            self.window._refresh_recording_status()
        except Exception as exc:
            self.report_error(str(exc))
        return False

    def report_error(self, message):
        self.message.setText(message)
        self.window.recorder._last_error = message
        self.window._set_recording_message(message, "error")
        self.window._external_control.write_status("Fehler", last_error=message)

    def cancel(self):
        self.pending = None
        self.testing = False
        if self.group:
            self.group.stop()
        self.message.setText("Vorbereitung / Verbindungstest beendet.")
        self.window._refresh_recording_status()

    def poll(self):
        recording = self.window.recorder.stats().state != "Gestoppt"
        busy = recording or self.testing or self.pending is not None
        self.table.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.config_button.setEnabled(not busy)
        self.test_button.setEnabled(not recording)
        self.test_button.setText("Test / Vorbereitung stoppen" if self.testing or self.pending else "Verbindung testen")
        if not self.group:
            return
        reports = {source.spec.path: source.snapshot() for source in self.group.sources}
        self.table.blockSignals(True)
        for row, spec in enumerate(self.specs):
            report = reports.get(spec.path)
            if not report:
                continue
            state = report["state"]
            if not busy and not report["error"]:
                state = "Test erfolgreich" if report["samples"] else "Keine Daten"
                if report["rows"]:
                    state = "Gespeichert"
            self.table.item(row, 1).setText(state)
            self.table.item(row, 1).setToolTip(report["error"] or "")
            warning = report["state"].startswith("Datenlücke") or report["state"] == "Keine Daten"
            self.table.item(row, 1).setForeground(QColor("#ff7a7a" if report["error"] else "#edb55e" if warning else "#73d6a0" if report["samples"] else "#edb55e"))
            measured_rate = (report["values"] or {}).get("sensor_rate_Hz")
            if measured_rate is None:
                measured_rate = report["rate_hz"]
            self.table.item(row, 2).setText(f"{measured_rate:.1f} Hz")
            self.table.item(row, 3).setText(format_values(report["values"]))
            if spec.sync_mode in {"atracsys_timestamp_correlated_v1", "best_effort_host_time_v1"}:
                self.table.item(row, 4).setText(synchronization_status(spec.sync_mode, report))
        self.table.blockSignals(False)
        refresh = getattr(self, "overview_refresh", None)
        if refresh:
            refresh()
        if self.group.recording and any(r["error"] for r in reports.values()):
            self.window._stop_recording("script_error")
            self.message.setText("Aufnahme wegen Skriptfehler beendet. Teilmessungen und Fehlerdetails bleiben erhalten.")
        elif self.group.recording and not recording:
            self.group.stop(tracker_stats=self.window.recorder.stats())
        if not (self.testing or self.pending is not None):
            return
        errors = [r["error"] for r in reports.values() if r["error"]]
        if errors:
            self.cancel()
            self.report_error("Skriptfehler: " + " | ".join(errors))
            return
        all_ready = all(s.ready.is_set() for s in self.group.sources)
        now = time.monotonic()
        if all_ready:
            self.ready_at = self.ready_at or now
            reference_now = time.perf_counter()
            tick = {"session_id": "connection_test", "sample_index": self.test_index,
                    "time_s": now - self.test_started, "computer_monotonic_s": f"{reference_now:.9f}",
                    "frame_counter": -1, "tracker_device_timestamp_us": ""}
            self.test_index += 1
            for source in self.group.sources:
                source.submit(tick)
            from .camera_health import sources_stable, WARMUP_SECONDS
            if (self.pending is not None and now - self.ready_at >= WARMUP_SECONDS
                    and sources_stable(reports) and self.window.camera_guard.ready()):
                args, kwargs = self.pending
                self.pending = None
                self.window.recorder._script_group = self.group if self.group.sources else None
                if self.original_start(self.window, *args, **kwargs):
                    tabs = getattr(self.window, "workspace_tabs", None)
                    if tabs is not None:
                        tabs.setCurrentIndex(0)
                    if not self.group.sources:
                        self.message.setText("Tracker-Aufnahme läuft; Kameradaten werden überwacht.")
                    elif self.group.synchronization == "simulation":
                        self.message.setText("SIMULATION · gemeinsamer Frame-Takt, getrennte CSV-Dateien.")
                    else:
                        self.message.setText("SOFTWARE-SYNCHRONISIERUNG · gemeinsame Tracker-Zeit, getrennte CSV-Dateien; Δ wird gespeichert.")
                else:
                    self.group.stop()
                    self.window.recorder._script_group = None
                    self.message.setText("Aufnahme wurde nicht gestartet; siehe Aufnahmestatus.")
                return
        expired = now - self.test_started >= 10 if self.pending is not None or not all_ready else now - self.ready_at >= 5
        if expired:
            was_pending = self.pending is not None
            self.cancel()
            if was_pending:
                self.report_error("Aufnahme nicht gestartet: Kamera oder Messskripte liefern noch keinen stabilen Datenstrom.")
                return
            self.message.setText("Verbindungstest abgeschlossen." if all(r["samples"] for r in reports.values()) else
                                 "Keine gültigen Messwerte erhalten. Anschluss und Einstellungen prüfen.")

    def details(self):
        if self.group is None:
            QMessageBox.information(self, "Messskript-Status", "Noch kein Verbindungstest oder Skriptlauf vorhanden.")
            return
        parts = []
        for source in self.group.sources:
            status = source.snapshot()
            parts.append(f"{status['name']}\nStatus: {status['state']}\nGültige Werte: {status['samples']}\n"
                         f"Datenrate: {status['rate_hz']:.1f} Hz\nGespeicherte Zeilen: {status['rows']}\n"
                         f"Fehlende Werte: {status['missing']}\nVerlorene Samples: {status['dropped']}\n"
                         f"Datei: {status['path'] or 'Keine Aufnahme'}\nFehler: {status['error'] or 'Keiner'}\n"
                         f"Synchronisationsmodus: {source.spec.sync_mode}\n"
                         f"Letzte Werte: {status['values'] or 'Keine'}\n{source.traceback}")
        report = "\n\n".join(parts)
        dialog = QDialog(self)
        dialog.setWindowTitle("Messskript-Status und Fehlerdetails")
        dialog.resize(650, 430)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(report)
        text.setReadOnly(True)
        layout.addWidget(text)
        dialog.exec()

    def help(self):
        QMessageBox.information(self, "Messskripte", (
            "1. Skripte links auswählen (mehrere möglich).\n"
            "2. Zeile anklicken und Anschluss über Einstellungen festlegen.\n"
            "3. Verbindung testen prüft Daten für fünf Sekunden.\n"
            "4. Aufnahme starten/stoppen steuert alle ausgewählten Quellen.\n\n"
            "Ohne Trigger wird jeder Sensorwert über seinen seriellen Empfangszeitpunkt dem nächsten "
            "Kameraframe zugeordnet. Die Spalte sensor_to_tracker_ms zeigt die Abweichung; sensor_reused "
            "markiert wiederverwendete Werte. Das ist eine Software-Zuordnung und keine physische "
            "Gleichzeitigkeit.\n\n"
            "Im Mock-Kameramodus ist ein kompletter Test mit Demo-Skripten möglich. "
            "Die resultierenden Dateien sind als Simulation gekennzeichnet.\n\n"
            "Eigene Skripte benötigen die dokumentierte Messschnittstelle. Details und Vorlage stehen in MESSSKRIPTE.md."))

    def shutdown(self):
        self.timer.stop()
        self.pending = None
        self.testing = False
        if self.group:
            self.group.stop()
