"""Process lifecycle, health and independent loss-detecting CSV recording."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time

from .protocol import ScriptSpec
from .worker import receive, send

COMMON_COLUMNS = ["session_id", "sample_index", "time_s", "computer_monotonic_s",
                  "frame_counter", "tracker_device_timestamp_us", "received_monotonic_s",
                  "status", "synchronization"]


def worker_command(port, token):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--sync-worker", str(port), token]
    return [sys.executable, "-m", "atracsys_sync.bootstrap", "--sync-worker", str(port), token]


class SourceProcess:
    def __init__(self, spec: ScriptSpec, config: dict, frequency_hz: int):
        self.spec, self.config = spec, dict(config)
        self.frequency_hz = frequency_hz
        self.tasks = queue.Queue(maxsize=512)
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.process = None
        self.connection = None
        self.error = ""
        self.traceback = ""
        self.state = "Verbinden"
        self.samples = 0
        self.rows = 0
        self.drops = 0
        self.missing = 0
        self.last_values = None
        self.last_response_had_data = False
        self.first_at = 0.0
        self.last_at = 0.0
        self.started_at = time.monotonic()
        self.file = None
        self.writer = None
        self.path = None
        self.partial = None
        self.log_path = None
        self.recording = False
        self.session_id = ""

    def start(self):
        if self.thread is not None or self.stopping.is_set():
            raise RuntimeError("Dieser Messskript-Prozess wurde bereits gestartet oder beendet.")
        thread = threading.Thread(target=self._run, name=f"Messskript-{self.spec.path.stem}", daemon=True)
        thread.start()
        self.thread = thread

    def begin_recording(self, path: Path, session_id: str):
        with self.lock:
            if not self.ready.is_set() or self.error:
                raise RuntimeError(f"{self.spec.name} ist nicht bereit: {self.error}")
            self.path = Path(path)
            self.partial = Path(str(path) + ".part")
            self.log_path = path.with_suffix(".log")
            if self.path.exists() or self.partial.exists() or self.log_path.exists():
                raise FileExistsError(f"Datei existiert bereits: {path.name}")
            self.file = self.partial.open("x", encoding="utf-8", newline="")
            self.writer = csv.DictWriter(self.file, fieldnames=COMMON_COLUMNS + list(self.spec.columns))
            self.writer.writeheader()
            self.file.flush()
            self.rows = self.drops = self.missing = 0
            self.samples = 0
            self.first_at = self.last_at = 0.0
            self.recording = True
            self.session_id = session_id

    def submit(self, tick):
        if not self.ready.is_set() or self.stopping.is_set() or self.error:
            return False
        try:
            self.tasks.put_nowait(dict(tick))
            return True
        except queue.Full:
            with self.lock:
                self.drops += 1
                self.error = "Skript ist zu langsam: Messwert-Warteschlange ist voll."
            return False

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.last_at if self.last_at else None
            state = self.state
            if self.error:
                state = "Fehler"
            elif self.ready.is_set() and not self.stopping.is_set():
                if self.samples == 0 and not self.recording:
                    state = "Warte auf Daten"
                elif self.recording and self.missing and not self.last_response_had_data:
                    state = f"Datenlücke ({self.missing})"
                else:
                    state = "Läuft" if age is not None and age < 1.0 else "Keine Daten"
            rate = (self.samples - 1) / (self.last_at - self.first_at) if self.samples > 1 and self.last_at > self.first_at else 0.0
            return {"name": self.spec.name, "state": state, "error": self.error,
                    "samples": self.samples, "rows": self.rows, "dropped": self.drops,
                    "missing": self.missing, "rate_hz": rate, "values": self.last_values,
                    "age_s": age, "path": str(self.path or ""),
                    "simulated": self.spec.simulated, "hardware_synchronized": False,
                    "synchronization": self._synchronization()}

    def _synchronization(self):
        if self.spec.simulated:
            return "simulation"
        if self.spec.sync_mode == "best_effort_host_time_v1":
            return "best_effort_host_time"
        if self.spec.sync_mode == "atracsys_timestamp_correlated_v1":
            return "hardware_timestamp_correlated"
        return "not_synchronized"

    def _run(self):
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                listener.settimeout(0.2)
                token = secrets.token_hex(24)
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self.process = subprocess.Popen(worker_command(listener.getsockname()[1], token),
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, creationflags=flags)
                deadline = time.monotonic() + 10
                while True:
                    if self.stopping.is_set():
                        return
                    if self.process.poll() is not None:
                        raise RuntimeError(f"Skriptprozess beendet (Code {self.process.returncode}).")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Skriptprozess meldet sich nicht.")
                    try:
                        connection, _ = listener.accept()
                        break
                    except socket.timeout:
                        continue
            self.connection = connection
            with connection, connection.makefile("rwb") as stream:
                connection.settimeout(10)
                hello = receive(stream)
                if not hello or not secrets.compare_digest(str(hello.get("token", "")), token):
                    raise RuntimeError("Ungültige Skriptverbindung.")
                send(stream, {"type": "open", "path": str(self.spec.path), "config": self.config,
                              "context": {"frequency_hz": self.frequency_hz}})
                self._check(receive(stream), "ready")
                self.ready.set()
                with self.lock:
                    self.state = "Bereit"
                while not self.stopping.is_set() or not self.tasks.empty():
                    try:
                        tick = self.tasks.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    send(stream, {"type": "sample", "tick": tick})
                    # A blocked driver must become an error, never freeze the GUI indefinitely.
                    connection.settimeout(2.0)
                    response = self._check(receive(stream), "sample")
                    self._sample(tick, response)
                send(stream, {"type": "stop"})
                connection.settimeout(1.0)
                self._check(receive(stream), "stopped")
                with self.lock:
                    self.state = "Gestoppt"
        except Exception as exc:
            with self.lock:
                if not self.error:
                    self.error = str(exc) or type(exc).__name__
                self.state = "Fehler"
        finally:
            if self.process is not None:
                try:
                    self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=1)
            self._finalize()

    def _check(self, response, expected):
        if response is None:
            raise RuntimeError("Skriptverbindung wurde unerwartet geschlossen.")
        if response.get("type") == "error":
            self.traceback = response.get("traceback", "")
            raise RuntimeError(response.get("message", "Skriptfehler"))
        if response.get("type") != expected:
            raise ValueError(f"Skriptprotokoll: {expected} erwartet.")
        return response

    def _sample(self, tick, response):
        if response.get("tick") != tick:
            raise ValueError("Skript liefert einen falschen Zeitstempel oder eine falsche Sample-ID.")
        values = response["values"]
        with self.lock:
            if self.recording and tick.get("session_id") != self.session_id:
                return
            if values is not None:
                now = time.monotonic()
                self.first_at = self.first_at or now
                self.last_at = now
                self.samples += 1
                self.last_values = values
            self.last_response_had_data = values is not None
            if self.writer is not None and tick.get("session_id") == self.session_id:
                row = {key: tick.get(key, "") for key in COMMON_COLUMNS}
                row.update(received_monotonic_s=f"{response['received_monotonic_s']:.9f}",
                           status="ok" if values is not None else "no_data",
                           synchronization=self._synchronization())
                row.update(values or {column: "" for column in self.spec.columns})
                self.writer.writerow(row)
                self.rows += 1
                self.missing += values is None
                if self.rows % max(1, self.frequency_hz) == 0:
                    self.file.flush()

    def request_stop(self):
        self.stopping.set()

    def join(self, timeout=4.0):
        self.request_stop()
        if self.thread:
            self.thread.join(timeout)
            if self.thread.is_alive():
                with self.lock:
                    self.error = "Skript reagiert nicht auf Stop; Prozess wird beendet."
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                if self.connection:
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                self.thread.join(2)

    def _finalize(self):
        with self.lock:
            if self.file is None:
                return
            try:
                self.file.flush()
                os.fsync(self.file.fileno())
                self.file.close()
                self.file = self.writer = None
                if self.error or self.drops:
                    self.log_path.write_text(self.error + "\n" + self.traceback, encoding="utf-8")
                else:
                    # rename() on Windows refuses to overwrite an existing destination.
                    self.partial.rename(self.path)
            except Exception as exc:
                self.error = f"Speichern fehlgeschlagen: {exc}"
            finally:
                if self.file is not None:
                    try:
                        self.file.close()
                    except OSError as exc:
                        self.error = f"Datei konnte nicht geschlossen werden: {exc}"
                    self.file = self.writer = None
            self.recording = False


class ScriptGroup:
    def __init__(self, entries, frequency_hz):
        self.sources = [SourceProcess(spec, config, frequency_hz) for spec, config in entries]
        self.frequency_hz = frequency_hz
        self.recording = False
        self.metadata_path = None
        self.session = {}
        self.sample_index = 0
        self._stop_lock = threading.Lock()
        self._stopped = False
        if self.sources and all(source.spec.simulated for source in self.sources):
            self.synchronization = "simulation"
        elif self.sources and all(source.spec.sync_mode == "best_effort_host_time_v1" for source in self.sources):
            self.synchronization = "best_effort_host_time"
        else:
            self.synchronization = "not_synchronized"

    def start(self):
        if self._stopped or any(source.thread is not None for source in self.sources):
            raise RuntimeError("Diese Messskript-Sitzung wurde bereits gestartet oder beendet.")
        try:
            for source in self.sources:
                source.start()
        except Exception:
            self.stop()
            raise

    def begin_recording(self, csv_path, session_id, t0):
        if self._stopped or self.metadata_path is not None:
            raise RuntimeError("Diese Messskript-Sitzung wurde bereits gestartet oder beendet.")
        metadata_path = Path(csv_path).with_suffix(".scripts.json")
        temporary = metadata_path.with_suffix(".json.tmp")
        paths = [Path(csv_path).with_name(
            f"{Path(csv_path).stem}.script_{index:02d}_{source.spec.path.stem}.csv")
            for index, source in enumerate(self.sources, 1)]
        for path in [metadata_path, temporary] + paths + [Path(str(path) + ".part") for path in paths] + [path.with_suffix(".log") for path in paths]:
            if path.exists():
                raise FileExistsError(f"Datei existiert bereits: {path.name}")
        self.session = {"session_id": session_id, "t0_monotonic_s": t0,
                        "requested_frequency_hz": self.frequency_hz,
                        "hardware_synchronized": False, "state": "recording",
                        "synchronization": self.synchronization,
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                        "clock_description": ("Windows QueryPerformanceCounter; Kraftwert über den "
                                              "seriellen Empfangszeitpunkt softwareseitig zugeordnet.")}
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(self.session, stream, ensure_ascii=False, indent=2)
        # Publish only a fully written manifest; Windows rename never overwrites.
        temporary.rename(metadata_path)
        self.metadata_path = metadata_path
        try:
            for source, path in zip(self.sources, paths):
                source.begin_recording(path, session_id)
        except Exception as exc:
            # This session owns the manifest now. Close every prepared writer and
            # finalize immediately, even when only some sources could be opened.
            self.session["start_error"] = str(exc) or type(exc).__name__
            for source in self.sources:
                with source.lock:
                    source.error = source.error or f"Aufnahmestart fehlgeschlagen: {exc}"
            self.stop()
            raise
        self.recording = True

    def frame(self, frame, reference_time_s=None):
        if not self.recording:
            return
        reference_time_s = (frame.computer_monotonic_s if reference_time_s is None
                            else float(reference_time_s))
        tick = {"session_id": self.session["session_id"], "sample_index": self.sample_index,
                "time_s": f"{reference_time_s - self.session['t0_monotonic_s']:.9f}",
                "computer_monotonic_s": f"{reference_time_s:.9f}",
                "frame_counter": frame.frame_counter,
                "tracker_device_timestamp_us": frame.device_timestamp_us}
        self.sample_index += 1
        for source in self.sources:
            source.submit(tick)
        return tick

    def stop(self, tracker_stats=None):
        with self._stop_lock:
            if self._stopped:
                return
            try:
                self._stop(tracker_stats)
                self._stopped = True
            finally:
                self.recording = False

    def _stop(self, tracker_stats=None):
        for source in self.sources:
            source.request_stop()
        deadline = time.monotonic() + 4
        for source in self.sources:
            source.join(max(0, deadline - time.monotonic()))
        if self.metadata_path is not None:
            if tracker_stats is not None:
                self.session.update(tracker_written_frames=tracker_stats.frame_count,
                                    tracker_error=tracker_stats.last_error)
            reports = [source.snapshot() for source in self.sources]
            complete = self.sample_index > 0 and all(
                not r["error"] and not r["missing"] and not r["dropped"] and r["rows"] == self.sample_index
                for r in reports)
            complete = complete and not self.session.get("tracker_error") and self.session.get("tracker_written_frames", self.sample_index) == self.sample_index
            complete = complete and not self.session.get("start_error") and all(
                source.file is None and not (source.thread and source.thread.is_alive())
                for source in self.sources)
            self.session.update(state="complete" if complete else "incomplete",
                                expected_samples=self.sample_index, scripts=reports,
                                completed_utc=datetime.now(timezone.utc).isoformat())
            temporary = self.metadata_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.session, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.metadata_path)
        self.recording = False
