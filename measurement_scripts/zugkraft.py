"""Best-effort software synchronization for the existing numeric serial stream."""
from collections import deque
import math
import threading
import time

MEASUREMENT_SCRIPT = {
    "api_version": 1,
    "name": "Zugkraft (Software-Sync)",
    "columns": ["force_N", "sensor_raw", "sensor_sequence",
                "sensor_received_monotonic_s", "sensor_to_tracker_ms",
                "sensor_reused", "sensor_rate_Hz"],
    "simulated": False,
    "sync_mode": "best_effort_host_time_v1",
    "defaults": {"port": "COM6", "baudrate": 230400, "dtr": True, "rts": True,
                 "offset_N": 87.5,
                 "no_data_timeout_s": 2.0, "stale_timeout_s": 1.0},
}


def resolve_port(config, ports=None):
    """Find the Nano after Windows assigns a different COM number."""
    requested = str(config["port"]).strip()
    if requested.lower() not in ("auto", "automatisch"):
        return requested
    if ports is None:
        from serial.tools.list_ports import comports
        ports = list(comports())
    candidates = [port for port in ports if
                  (port.vid, port.pid) in ((0x2341, 0x0070), (0x303A, 0x1001)) or
                  "nano esp32" in (port.description or "").lower()]
    serial_number = str(config.get("usb_serial", "")).strip().upper()
    preferred = [port for port in candidates if serial_number and
                 (port.serial_number or "").upper() == serial_number]
    choices = preferred or candidates
    if len(choices) == 1:
        return choices[0].device
    if not choices:
        raise RuntimeError("Nano ESP32 nicht gefunden. USB-Datenkabel anschließen und das Board im normalen Messbetrieb starten.")
    raise RuntimeError("Mehrere Nano-/ESP32-Anschlüsse erkannt: " +
                       ", ".join(port.device for port in choices) +
                       ". Im Tab Messgeräte den richtigen COM-Anschluss auswählen.")


class Source:
    def __init__(self, config):
        import serial
        self.offset = float(config["offset_N"])
        if not math.isfinite(self.offset):
            raise ValueError("offset_N muss endlich sein.")
        self.no_data_timeout = float(config.get("no_data_timeout_s", 2.0))
        self.stale_timeout = float(config.get("stale_timeout_s", 1.0))
        if not 0.1 <= self.no_data_timeout <= 60 or not 0.1 <= self.stale_timeout <= 60:
            raise ValueError("Zeitlimits müssen zwischen 0.1 und 60 Sekunden liegen.")
        self.serial = serial.Serial(port=None, baudrate=int(config["baudrate"]), timeout=0.02,
                                    write_timeout=0.2)
        self.serial.dtr = bool(config.get("dtr", True))
        self.serial.rts = bool(config.get("rts", True))
        self.serial.port = resolve_port(config)
        self.serial.open()
        self.buffer = bytearray()
        self.records = deque(maxlen=4096)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.reader_error = ""
        self.opened_at = time.perf_counter()
        self.first_received_at = 0.0
        self.last_received_at = 0.0
        self.received_count = 0
        self.last_returned_sequence = None
        self.reader = threading.Thread(target=self._read_loop, name="Zugkraft-SerialReader", daemon=True)
        self.reader.start()

    def _read_loop(self):
        try:
            while not self.stopping.is_set():
                waiting = getattr(self.serial, "in_waiting", 0)
                chunk = self.serial.read(max(1, min(waiting, 8192)))
                if not chunk:
                    continue
                with self.lock:
                    self.buffer.extend(chunk)
                    if len(self.buffer) > 65536:
                        raise ValueError("Mehr als 64 KB ohne gültigen Zeilenabschluss empfangen.")
                    while b"\n" in self.buffer:
                        line, _, remainder = self.buffer.partition(b"\n")
                        self.buffer = bytearray(remainder)
                        try:
                            raw = float(line.decode("ascii").strip())
                        except (ValueError, UnicodeDecodeError) as exc:
                            raise ValueError("Ungültige Kraftdaten: Zahlen mit LF-Zeilenabschluss erwartet.") from exc
                        if not math.isfinite(raw):
                            raise ValueError("Ungültige Kraftdaten: endliche Zahl erwartet.")
                        received = time.perf_counter()
                        self.received_count += 1
                        self.first_received_at = self.first_received_at or received
                        self.last_received_at = received
                        self.records.append((self.received_count, received, raw))
        except Exception as exc:
            if not self.stopping.is_set():
                with self.lock:
                    self.reader_error = str(exc) or type(exc).__name__

    def sample(self, tick):
        target = float(tick.get("computer_monotonic_s", time.perf_counter()))
        with self.lock:
            if self.reader_error:
                raise RuntimeError(self.reader_error)
            if not self.records:
                if time.perf_counter() - self.opened_at >= self.no_data_timeout:
                    raise RuntimeError("Keine Kraftdaten empfangen. COM-Port, Nano ESP32 und Sensorversorgung prüfen.")
                return None
            sequence, received, raw = min(self.records, key=lambda item: abs(item[1] - target))
            if time.perf_counter() - self.last_received_at > self.stale_timeout:
                raise RuntimeError("Kraftdatenstrom ist abgebrochen.")
            rate = ((self.received_count - 1) / (self.last_received_at - self.first_received_at)
                    if self.received_count > 1 and self.last_received_at > self.first_received_at else 0.0)
            reused = sequence == self.last_returned_sequence
            self.last_returned_sequence = sequence
        return {"force_N": -raw + self.offset, "sensor_raw": raw,
                "sensor_sequence": sequence, "sensor_received_monotonic_s": received,
                "sensor_to_tracker_ms": (received - target) * 1000.0,
                "sensor_reused": int(reused), "sensor_rate_Hz": rate}

    def close(self):
        self.stopping.set()
        self.serial.close()
        self.reader.join(timeout=1.0)


def open_source(config, context):
    return Source(config)
