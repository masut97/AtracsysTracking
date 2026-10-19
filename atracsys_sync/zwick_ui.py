"""Small recording bar and editable, documented Zwick control settings."""
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .external_control import CommandGate, RegistryControl, integer, recording_target, registry_path, reported_state


class ZwickController(QObject):
    def __init__(self, window, settings_layout):
        super().__init__(window)
        self.window=window
        self.gate=CommandGate()
        self.error=''
        self.last_status=0.
        self.preparing=False
        self.cancelled=False
        self._settings(settings_layout)
        self._recording_bar()
        self.timer=QTimer(self); self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh); self.timer.start()

    def busy(self):
        w=self.window
        return w.script_panel.pending is not None or w.recorder.stats().state!='Gestoppt'

    def _settings(self, layout):
        w=self.window
        save_group=QGroupBox('Speichern bei manueller Aufnahme',w)
        form=QFormLayout(save_group)
        form.addRow('Dateiname',w.record_name_edit)
        folder=QWidget(save_group); row=QHBoxLayout(folder); row.setContentsMargins(0,0,0,0)
        row.addWidget(w.record_directory_edit,1); row.addWidget(w.record_browse_button)
        form.addRow('Exportordner',folder)
        layout.addWidget(save_group)
        group=QGroupBox('Zwick / testXpert – direkte Steuerung',w)
        group.setObjectName('zwickSettings')
        box=QVBoxLayout(group)
        self.enabled=QCheckBox('Externe Steuerung aktiv',group)
        self.enabled.setChecked(w.config.registry_control_enabled)
        box.addWidget(self.enabled)
        hint=QLabel('Diesen Registry-Pfad in Zwick verwenden. Kein ExperimentName.txt erforderlich. '
                    'Zwick und Tracker müssen unter demselben Windows-Benutzer laufen.',group)
        hint.setWordWrap(True); box.addWidget(hint)
        keyrow=QHBoxLayout()
        self.key=QLineEdit(w._external_control.display_path,group)
        self.key.setObjectName('zwickRegistryPath')
        keyrow.addWidget(self.key,1)
        copy=QPushButton('Pfad kopieren',group)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.key.text()))
        keyrow.addWidget(copy)
        self.save=QPushButton('Übernehmen',group); self.save.clicked.connect(self.save_settings)
        keyrow.addWidget(self.save); box.addLayout(keyrow)
        table=QTableWidget(8,3,group); table.setObjectName('zwickProtocolTable')
        table.setHorizontalHeaderLabels(['Wert','Typ / Beispiel','Bedeutung'])
        rows=[('RecordingName','Text: Probe01_FE','Gemeinsamer Basisname für CSV, TRA und Plots'),
              ('RecordingDirectory',r'Text: D:\Messungen','Absoluter Exportordner; leer = manueller Ordner'),
              ('RecordingFrequencyHz','Text: 100','Ganze Dezimalzahl in Hz, ohne Einheit'),
              ('StartRecord','DWORD: 1','Nach den Einstellungen zuletzt auf 1 setzen'),
              ('StopRecord','DWORD: 1','Beenden / Vorbereitung abbrechen; wird quittiert'),
              ('AppReady','DWORD: 1','Steuerung ansprechbar; Kamera muss zusätzlich bereit sein'),
              ('RecordingState','Text','Bereit / Vorbereitung / Aufnahme / Gespeichert / Fehler'),
              ('LastError / LastRecording','Text','Fehler bzw. zuletzt gespeicherte CSV')]
        for i,values in enumerate(rows):
            for j,value in enumerate(values):
                item=QTableWidgetItem(value); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(i,j,item)
        table.verticalHeader().hide(); table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(270); box.addWidget(table)
        foot=QLabel('Ablauf: AppReady prüfen → StartRecord=0 → Name, Ordner und Frequenz schreiben '
                    '→ StartRecord=1 → auf Aufnahme warten → StopRecord=1 → auf Gespeichert warten. '
                    'StartRecord=0 beendet weiterhin eine laufende oder vorbereitete Aufnahme. '
                    'Bei Fehler: StartRecord erst auf 0, korrigieren, danach erneut 1. '
                    'Vorhandene Messdateien werden nicht überschrieben oder automatisch umbenannt.',group)
        foot.setWordWrap(True); box.addWidget(foot)
        self.feedback=QLabel('',group); self.feedback.setWordWrap(True); box.addWidget(self.feedback)
        layout.addWidget(group)

    def _recording_bar(self):
        w=self.window
        bar=w.findChild(QWidget,'recordingBar')
        row=bar.layout().itemAt(0).layout()
        devices=w.findChild(QWidget,'measurementMenuButton')
        while row.count():
            item=row.takeAt(0)
            if item.widget(): item.widget().hide()
        w.record_button.clicked.disconnect()
        w.record_button.clicked.connect(lambda: w._start_recording('ui'))
        w.record_button.setText('Aufnahme starten')
        w.stop_record_button=QPushButton('Aufnahme stoppen',bar)
        w.stop_record_button.setObjectName('stopRecordingButton')
        w.stop_record_button.clicked.connect(self.stop)
        disabled='QPushButton:disabled { background:#182232; color:#74859a; border:1px solid #27364a; }'
        w.record_button.setStyleSheet('QPushButton { background:#245b93; color:white; border:1px solid #4481ba; padding:8px 14px; border-radius:7px; } '+disabled)
        w.stop_record_button.setStyleSheet('QPushButton { background:#94283d; color:white; border:1px solid #c04d62; padding:8px 14px; border-radius:7px; } '+disabled)
        spin=w.record_frequency_spin
        spin.setKeyboardTracking(False); spin.setSuffix(' Hz'); spin.setMinimumWidth(95)
        spin.lineEdit().setReadOnly(False)
        spin.setToolTip('Frequenz direkt als ganze Dezimalzahl eingeben und Enter drücken.')
        row.addWidget(QLabel('AUFNAHME',bar))
        for widget in (w.record_button,w.stop_record_button):
            row.addWidget(widget); widget.show()
        row.addWidget(QLabel('Frequenz',bar)); row.addWidget(spin); spin.show()
        if devices: row.addWidget(devices); devices.show()
        row.addWidget(w.record_status_label,1); w.record_status_label.show()
        self.summary=QLabel('',bar); self.summary.setWordWrap(True)
        bar.layout().addWidget(self.summary)
        self.refresh()

    def save_settings(self):
        w=self.window
        if self.busy():
            self.feedback.setText('Bitte Aufnahme / Vorbereitung zuerst beenden.'); return
        try:
            path=registry_path(self.key.text())
            control=RegistryControl(path,self.enabled.isChecked(),w.record_frequency_spin.value())
            if control.last_error: raise OSError(control.last_error)
            # Preserve old key values and do not transfer outstanding commands.
            old=w._external_control
            old.write_app_ready(False)
            w._external_control=control
            w.config.registry_path=path; w.config.registry_control_enabled=control.enabled
            w._save_preferences()
            self.gate=CommandGate(); self.error=''
            control.write_app_ready(True)
            self.key.setText(control.display_path)
            self.feedback.setText('Gespeichert. Den angezeigten Pfad in Zwick übernehmen.')
        except (OSError,ValueError) as error:
            self.feedback.setText(str(error))

    def stop(self):
        w=self.window
        if w.script_panel.pending is not None:
            w.script_panel.cancel()
            w._external_control.write_start_record(False)
            self.error=''; self.preparing=False
            self.cancelled=True
            w._external_control.write_status('Gestoppt')
        else:
            w._stop_recording('zwick')
        self.refresh()

    def refresh(self):
        w=self.window
        busy=self.busy()
        w.record_button.setText('Aufnahme starten'); w.record_button.setEnabled(not busy)
        w.stop_record_button.setEnabled(busy)
        for widget in (w.record_name_edit,w.record_directory_edit,w.record_browse_button,w.record_frequency_spin):
            widget.setEnabled(not busy)
        self.key.setEnabled(not busy); self.enabled.setEnabled(not busy); self.save.setEnabled(not busy)
        name=w.record_name_edit.text(); directory=w.record_directory_edit.text()
        self.summary.setText(f'{name}  ·  {directory}')
        self.summary.setToolTip(self.summary.text())

    def poll(self, now=None):
        w=self.window; control=w._external_control
        if not control.available: return
        try:
            values=control.values()
            action=self.gate.action(values,self.busy())
            if action=='stop':
                self.stop(); control.write_start_record(False); control.clear_stop()
            elif action=='start':
                frequency=integer(values['RecordingFrequencyHz'],'RecordingFrequencyHz',1,w.record_frequency_spin.maximum())
                name,target=recording_target(values['RecordingName'],values['RecordingDirectory'] or w.record_directory_edit.text())
                w.record_frequency_spin.setValue(frequency)
                w.record_name_edit.setText(name); w.record_directory_edit.setText(str(target))
                self.error=''
                self.cancelled=False
                # Zwick requests an exact name. The legacy 'registry' start silently
                # adds suffixes, which would break matching with machine exports.
                w._start_recording('zwick', name_override=name)
                self.preparing=w.script_panel.pending is not None
                if not self.preparing and w.recorder.stats().state=='Gestoppt':
                    raise ValueError('Start nicht möglich: '+w.record_status_label.text())
            elif action=='idle' and not self.busy():
                frequency=integer(values['RecordingFrequencyHz'],'RecordingFrequencyHz',1,w.record_frequency_spin.maximum())
                if frequency!=w.record_frequency_spin.value():
                    w.record_frequency_spin.setValue(frequency)
            stats=w.recorder.stats()
            pending=w.script_panel.pending is not None
            if self.preparing and not pending:
                self.preparing=False
                if stats.state=='Gestoppt':
                    self.error=stats.last_error or 'Vorbereitung fehlgeschlagen oder beendet.'
            if stats.state!='Gestoppt':
                self.cancelled=False
            state=reported_state(stats,pending,self.cancelled,self.error)
            error=self.error or stats.last_error
            if action in {'start','stop'} or time.monotonic()-self.last_status>=1:
                control.write_status(state,last_recording=stats.csv_path if not self.busy() else '',last_error=error)
                guard = getattr(w, 'camera_guard', None)
                control._write('CameraReady', int(bool(guard and guard.ready())), control.registry.REG_DWORD)
                if control.last_error:
                    raise OSError(control.last_error)
                self.last_status=time.monotonic()
            if error:
                self.feedback.setText(error)
                w._set_recording_message(error,'error')
            elif action=='held' and not self.busy() and not self.gate.armed:
                self.feedback.setText('StartRecord zuerst auf 0 setzen, danach für einen neuen Start auf 1.')
        except (OSError,ValueError) as error:
            self.error=str(error); self.feedback.setText(self.error)
            w._set_recording_message(self.error,'error')
            try: control.write_status('Fehler',last_error=self.error)
            except OSError: pass
        self.refresh()


def add_settings(window, tabs):
    page=QWidget(tabs); outer=QVBoxLayout(page)
    scroll=QScrollArea(page); scroll.setWidgetResizable(True)
    content=QWidget(); layout=QVBoxLayout(content)
    controller=ZwickController(window,layout)
    updater=window.update_controller
    group=QGroupBox('Software-Updates',content); box=QVBoxLayout(group)
    box.addWidget(updater.button); box.addWidget(updater.status); layout.addWidget(group)
    layout.addStretch(1); scroll.setWidget(content); outer.addWidget(scroll)
    tabs.addTab(page,'Einstellungen')
    window.zwick_controller=controller
