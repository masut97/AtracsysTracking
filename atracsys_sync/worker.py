"""One isolated process per measurement source, with bounded JSON messages."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import sys
import time
import traceback

from .protocol import ScriptSpec, validate_values

MAX_MESSAGE = 65536


def send(stream, message):
    data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE:
        raise ValueError("Skript-Nachricht ist zu groß.")
    stream.write(data)
    stream.flush()


def receive(stream):
    raw = stream.readline(MAX_MESSAGE + 1)
    if not raw:
        return None
    if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
        raise ValueError("Ungültige oder übergroße Skript-Nachricht.")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("JSON-Objekt erwartet.")
    return result


def main(port: int, token: str) -> int:
    source = None
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.settimeout(None)
        with connection.makefile("rwb") as stream:
            send(stream, {"type": "hello", "token": token})
            try:
                request = receive(stream)
                if request is None or request.get("type") != "open":
                    raise ValueError("Initialisierung fehlt.")
                spec = ScriptSpec.read(Path(request["path"]))
                sys.path.insert(0, str(spec.path.parent))
                module_spec = importlib.util.spec_from_file_location("measurement_source", spec.path)
                module = importlib.util.module_from_spec(module_spec)
                module_spec.loader.exec_module(module)
                source = module.open_source(request["config"], request["context"])
                if not callable(getattr(source, "sample", None)) or not callable(getattr(source, "close", None)):
                    raise ValueError("open_source muss ein Objekt mit sample(tick) und close() liefern.")
                send(stream, {"type": "ready"})
                while True:
                    request = receive(stream)
                    if request is None or request.get("type") == "stop":
                        break
                    if request.get("type") != "sample":
                        raise ValueError("Unbekannte Anforderung.")
                    tick = request["tick"]
                    values = source.sample(tick)
                    received = time.perf_counter()
                    if values is not None:
                        values = validate_values(values, spec.columns, spec.nullable_columns)
                    send(stream, {"type": "sample", "tick": tick, "values": values,
                                  "received_monotonic_s": received})
            except Exception as exc:
                send(stream, {"type": "error", "message": str(exc), "traceback": traceback.format_exc()})
                return 1
            finally:
                if source is not None:
                    try:
                        source.close()
                    except Exception as exc:
                        send(stream, {"type": "error", "message": f"Schließen fehlgeschlagen: {exc}"})
            send(stream, {"type": "stopped"})
    return 0
