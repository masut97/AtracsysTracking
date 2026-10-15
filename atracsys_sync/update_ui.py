"""User-facing update controller; all installation work runs off the GUI thread."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from .github_updates import FLAGS, download_release, latest_release
from .updater import install_update, load_update_settings


def verify_mock(app):
    """Verify a staged runtime by running its real EXE without touching devices."""
    with tempfile.TemporaryDirectory(prefix="atracsys-update-test-") as output:
        process = subprocess.run([str(Path(app) / "AtracsysLiveTracker_Messskripte.exe"),
                                  "--sync-self-test", output], cwd=app, capture_output=True,
                                 timeout=100, creationflags=FLAGS)
        result = Path(output) / "result.json"
        if process.returncode or not result.is_file() or not json.loads(result.read_text())["passed"]:
            raise RuntimeError("Programm-Selbsttest fehlgeschlagen. Die bisherige Version bleibt aktiv.")
    return {"passed": True}


def switch_shortcut(app):
    """Replace only our desktop shortcut, keeping its previous target as backup."""
    app = Path(app).resolve()
    script = '''param([string]$Target,[string]$Backup)
$ErrorActionPreference='Stop'
function Get-Sha256([string]$Path){
    $algorithm=[Security.Cryptography.SHA256]::Create()
    $stream=$null
    try{
        $stream=[IO.File]::OpenRead($Path)
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant()
    }finally{
        if($null -ne $stream){$stream.Dispose()}
        $algorithm.Dispose()
    }
}
$desktop=[Environment]::GetFolderPath('Desktop')
$link=Join-Path $desktop 'Atracsys Tracking.lnk'
$temporary=Join-Path $desktop ('Atracsys Tracking.'+[Guid]::NewGuid().ToString()+'.lnk')
$shell=New-Object -ComObject WScript.Shell
$s=$shell.CreateShortcut($temporary)
$s.TargetPath=$Target
$s.WorkingDirectory=Split-Path $Target
$s.Arguments='--mode hardware'
$s.Save()
if(Test-Path -LiteralPath $link){
    $localBackup=Join-Path $desktop ('AtracsysTracking-backup-'+[Guid]::NewGuid().ToString()+'.lnk')
    [IO.File]::Replace($temporary,$link,$localBackup)
    try {
        Copy-Item -LiteralPath $localBackup -Destination $Backup
    if((Get-Sha256 $localBackup) -ne (Get-Sha256 $Backup)){throw 'Shortcut backup verification failed'}
        Remove-Item -LiteralPath $localBackup
    } catch {
        $discard=Join-Path $desktop ('AtracsysTracking-rollback-'+[Guid]::NewGuid().ToString()+'.lnk')
        [IO.File]::Replace($localBackup,$link,$discard)
        Remove-Item -LiteralPath $discard
        throw
    }
}else{[IO.File]::Move($temporary,$link)}
'''
    with tempfile.TemporaryDirectory(prefix="atracsys-shortcut-") as temporary:
        path = Path(temporary) / "shortcut.ps1"
        path.write_text(script, encoding="utf-8-sig")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(path), "-Target", str(app / "AtracsysLiveTracker_Messskripte.exe"),
                                 "-Backup", str(app.parent / "previous-desktop-shortcut.lnk")],
                                capture_output=True, timeout=20, creationflags=FLAGS)
        if result.returncode:
            raise RuntimeError(f"Neue Version ist geprüft, aber die Verknüpfung konnte nicht umgestellt werden: {app}")


def launch_after_exit(pid, app):
    """Wait for the previous process to release the camera, then launch its successor."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x00100000, False, int(pid))
    if not handle and ctypes.get_last_error() != 87:
        return 1
    if handle:
        try:
            if api.WaitForSingleObject(handle, 120000) != 0:
                return 1
        finally:
            api.CloseHandle(handle)
    app = Path(app).resolve(strict=True)
    subprocess.Popen([str(app / "AtracsysLiveTracker_Messskripte.exe"), "--mode", "hardware"], cwd=app)
    return 0


class UpdateWorker(QThread):
    progress = Signal(str)
    finished_update = Signal(object)
    failed = Signal(str)

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = Path(app)

    def run(self):
        try:
            self.progress.emit("GitHub-Version wird geprüft …")
            release = latest_release(self.app)
            build = json.loads((self.app / "sync_extension/build.json").read_text())
            if build.get("source_revision") == release["revision"]:
                self.finished_update.emit(None)
                return
            destination = Path(load_update_settings(self.app)["update_directory"])
            destination = destination / time.strftime("%Y%m%d-%H%M%S")
            with tempfile.TemporaryDirectory(prefix="atracsys-download-") as directory:
                self.progress.emit("Geprüftes Code-Paket wird heruntergeladen …")
                archive, checksum = download_release(self.app, release, directory)
                self.progress.emit("Neue Version wird separat installiert und getestet. Das kann einige Minuten dauern …")
                updated = install_update(self.app, archive, destination, checksum, release["revision"],
                                         verify_callback=verify_mock)
            switch_shortcut(updated)
            self.finished_update.emit(str(updated))
        except Exception as error:
            self.failed.emit(str(error))


class UpdateController:
    def __init__(self, window, layout):
        self.window, self.worker = window, None
        self.completed = False
        self.button = QPushButton("Nach Updates suchen …", window)
        self.button.setObjectName("softwareUpdateButton")
        self.status = QLabel("Updates ohne Anmeldung über GitHub; Messdaten und Kalibrierungen bleiben lokal.", window)
        self.status.setWordWrap(True)
        layout.addWidget(self.button)
        layout.addWidget(self.status)
        self.button.clicked.connect(self.start)

    def busy(self):
        return not self.completed and self.worker is not None and self.worker.isRunning()

    def start(self):
        panel = self.window.script_panel
        if self.busy():
            return
        if panel.testing or panel.pending is not None or (panel.group is not None and panel.group.recording) or self.window.recorder.stats().state != "Gestoppt":
            QMessageBox.information(self.window, "Update", "Bitte zuerst die laufende Aufnahme oder Vorbereitung beenden.")
            return
        self.worker = UpdateWorker(Path(sys.executable).resolve().parent, self.window)
        self.completed = False
        self.worker.progress.connect(self.status.setText)
        self.worker.finished_update.connect(self.done)
        self.worker.failed.connect(self.error)
        self.window.setEnabled(False)
        self.worker.start()

    def restore(self):
        if self.worker is not None:
            self.worker.wait()
        self.completed = True
        self.window.setEnabled(True)
        self.button.setEnabled(True)
        self.window.record_button.setEnabled(True)

    def error(self, message):
        self.restore()
        self.status.setText("Update fehlgeschlagen: " + message)
        QMessageBox.warning(self.window, "Update", message)

    def done(self, updated):
        self.restore()
        if updated is None:
            self.status.setText("Die neueste geprüfte Version ist bereits installiert.")
            return
        self.status.setText("Update installiert; Desktop-Verknüpfung aktualisiert.")
        answer = QMessageBox.question(self.window, "Update installiert",
                                      "Die neue Version ist geprüft und die Desktop-Verknüpfung aktualisiert. Jetzt neu starten?")
        if answer == QMessageBox.StandardButton.Yes:
            subprocess.Popen([sys.executable, "--launch-after-exit", str(os.getpid()), updated], creationflags=FLAGS)
            self.window.close()
