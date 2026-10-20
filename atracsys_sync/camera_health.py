"""Detect a stopped stream independently of marker visibility and connection flags."""
from __future__ import annotations

import threading
import time

STALL_SECONDS = 3.0
STARTUP_SECONDS = 30.0
FRESH_SECONDS = 0.5
WARMUP_SECONDS = 0.5
OVERFLOW_FLAG = 0x5347000d
OVERFLOW_LIVENESS_SECONDS = 1.5


class StreamHealth:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.epoch = 0
        self.reset()

    def reset(self):
        with self.lock:
            self.epoch += 1
            self.started = self.clock()
            self.last = None
            self.first = None
            self.identity = None
            self.count = 0
            self.overflow_heartbeat = None
            self.overflow_last = None
            self.overflow_since = None

    def observe_overflow(self, heartbeat):
        with self.lock:
            if heartbeat > 0 and (self.overflow_heartbeat is None or heartbeat > self.overflow_heartbeat):
                self.overflow_heartbeat = heartbeat
                self.overflow_last = self.clock()
                if self.overflow_since is None:
                    self.overflow_since = self.overflow_last

    def clear_overflow(self):
        with self.lock:
            self.overflow_last = None
            self.overflow_since = None

    def observe(self, frame):
        identity = (frame.frame_counter, frame.device_timestamp_us)
        now = self.clock()
        with self.lock:
            if self.identity is not None:
                counter, timestamp = self.identity
                if identity == self.identity or (frame.device_timestamp_us <= timestamp
                                                 and frame.frame_counter <= counter):
                    return False
            if self.last is None or now - self.last > FRESH_SECONDS:
                self.first, self.count = now, 0
            self.identity, self.last = identity, now
            self.count += 1
            self.overflow_last = None
            self.overflow_since = None
            return True

    def snapshot(self):
        now = self.clock()
        with self.lock:
            age = now - (self.last if self.last is not None else self.started)
            stable = (self.last is not None and age <= FRESH_SECONDS and self.count >= 3
                      and self.last - self.first >= WARMUP_SECONDS)
            limit = STARTUP_SECONDS if self.last is None else STALL_SECONDS
            overflow = self.overflow_last is not None and now-self.overflow_last <= OVERFLOW_LIVENESS_SECONDS
            return {"epoch": self.epoch, "age_s": age, "stable": stable and not overflow,
                    "stalled": age >= limit and not overflow, "frames": self.count,
                    "segmentation_overflow": overflow, "overflow_since": self.overflow_since}


class GuardedAdapter:
    """Escalate endless bounded SDK/bridge timeouts to the existing reconnect loop."""
    def __init__(self, adapter, health, timeout_error):
        self.adapter, self.health, self.timeout_error = adapter, health, timeout_error

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def connect(self, *args, **kwargs):
        result = self.adapter.connect(*args, **kwargs)
        self.health.reset()
        return result

    def read_frame(self, *args, **kwargs):
        try:
            frame = self.adapter.read_frame(*args, **kwargs)
        except self.timeout_error:
            self._observe_native_error()
            if self.health.snapshot()["stalled"]:
                raise RuntimeError("Kamera liefert keine neuen Frames; Verbindung wird neu aufgebaut.") from None
            raise
        if not self.health.observe(frame):
            self._observe_native_error()
            if self.health.snapshot()["stalled"]:
                raise RuntimeError("Kameraframe bleibt stehen; Verbindung wird neu aufgebaut.")
            raise self.timeout_error("Warte auf einen neuen Kameraframe")
        return frame

    def _observe_native_error(self):
        # Only the new bridge's explicit status plus an advancing heartbeat may
        # suppress a reconnect. Error text alone and old/frozen mappings cannot.
        try:
            header = self.adapter._header()
            process = self.adapter._process
            if (process is not None and process.poll() is None and header.state == 2
                    and header.reserved0 == OVERFLOW_FLAG):
                self.health.observe_overflow(int(header.heartbeat_ns))
                return
        except (AttributeError, OSError, RuntimeError, ValueError):
            pass
        self.health.clear_overflow()


def sources_stable(reports):
    return all(not report["error"] and report["samples"] >= 3
               and report["age_s"] is not None and report["age_s"] <= FRESH_SECONDS
               for report in reports.values())


class CameraGuard:
    def __init__(self, window):
        from PySide6.QtCore import QTimer
        self.window = window
        self.timer = QTimer(window)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def snapshot(self):
        health = getattr(self.window.acquisition, "_camera_health", None)
        return health.snapshot() if health else {"stable": False, "stalled": True, "epoch": -1}

    def ready(self):
        return self.window.status_store.get().connected and self.snapshot()["stable"]

    def poll(self):
        window = self.window
        recorder = window.recorder
        if recorder.stats().state == "Gestoppt":
            return
        health = self.snapshot()
        now = time.monotonic()
        if health.get('segmentation_overflow') and health['epoch'] == getattr(recorder, '_camera_epoch', -1):
            with recorder._lock:
                gaps = recorder._metadata.setdefault('camera_segmentation_overflows', [])
                if not getattr(recorder, '_overflow_interval_open', False):
                    gaps.append({'start_computer_monotonic_s': health['overflow_since'],
                                 'last_observed_computer_monotonic_s': now,
                                 'status': 'open', 'reason': 'SDK segmentation overflow; no poses synthesized'})
                    recorder._overflow_interval_open = True
                gaps[-1]['last_observed_computer_monotonic_s'] = now
                recorder._metadata['camera_data_incomplete'] = True
            window._set_recording_message('Bildsegmentierungs-Overflow: Aufnahme bleibt offen, Messdaten fehlen.', 'error')
            return
        if getattr(recorder, '_overflow_interval_open', False):
            with recorder._lock:
                event = recorder._metadata['camera_segmentation_overflows'][-1]
                # Give the independent pose ring a bounded chance to deliver the
                # first genuinely new recording sample after image recovery.
                if (health['epoch'] == getattr(recorder, '_camera_epoch', -1)
                        and health['age_s'] < FRESH_SECONDS
                        and getattr(recorder, '_camera_last_sample', 0) < event['last_observed_computer_monotonic_s']):
                    event.setdefault('recovery_wait_started_s', now)
                    if now-event['recovery_wait_started_s'] < OVERFLOW_LIVENESS_SECONDS:
                        return
                event.update(status='ended', end_computer_monotonic_s=now)
                recorder._overflow_interval_open = False
        last_sample = getattr(recorder, "_camera_last_sample", time.monotonic())
        failed = (health["stalled"] or health["epoch"] != getattr(recorder, "_camera_epoch", -1)
                  or time.monotonic() - last_sample >= STALL_SECONDS)
        if not failed:
            return
        message = "Kameradaten unterbrochen. Teilaufnahme gespeichert; bitte nach Wiederverbindung neu starten."
        recorder._camera_abort = True
        with recorder._lock:
            recorder._metadata["camera_stream_error"] = {"message": message, "health": health,
                                                        "computer_monotonic_s": time.monotonic()}
            recorder._last_error = message
        window._stop_recording("camera_stream_error")
        window.script_panel.report_error(message)
