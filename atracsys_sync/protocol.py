"""Validated measurement-script metadata and messages. No script execution on import."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import math
from pathlib import Path
import re


@dataclass(frozen=True)
class ScriptSpec:
    path: Path
    name: str
    columns: tuple[str, ...]
    defaults: dict
    simulated: bool = False
    nullable_columns: tuple[str, ...] = ()
    sync_mode: str = "none"

    @classmethod
    def read(cls, path: Path) -> "ScriptSpec":
        path = Path(path).resolve()
        if path.suffix.lower() != ".py":
            raise ValueError("Bitte ein Python-Messskript (.py) importieren.")
        if path.stat().st_size > 1_000_000:
            raise ValueError("Das Messskript ist zu groß (maximal 1 MB).")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        metadata = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "MEASUREMENT_SCRIPT"
                for target in node.targets
            ):
                metadata = ast.literal_eval(node.value)
        if not isinstance(metadata, dict) or metadata.get("api_version") != 1:
            raise ValueError("Messskript-Schnittstelle fehlt: MEASUREMENT_SCRIPT mit api_version=1 erforderlich. Siehe Vorlage.")
        name = metadata.get("name")
        columns = metadata.get("columns")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Ungültiger Skriptname.")
        if not isinstance(columns, list) or not 1 <= len(columns) <= 32:
            raise ValueError("Ein Messskript benötigt 1 bis 32 Messwertspalten.")
        if any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", c) for c in columns):
            raise ValueError("Messwertspalten dürfen nur Buchstaben, Zahlen und Unterstriche enthalten.")
        if len(set(columns)) != len(columns):
            raise ValueError("Doppelte Messwertspalten.")
        reserved = {"session_id", "sample_index", "frame_counter", "time_s", "computer_monotonic_s",
                    "tracker_device_timestamp_us", "received_monotonic_s", "status", "synchronization"}
        if reserved.intersection(columns):
            raise ValueError("Ein Messwert verwendet einen reservierten Spaltennamen.")
        defaults = metadata.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ValueError("defaults muss ein JSON-Objekt sein.")
        nullable = metadata.get("nullable_columns", [])
        if not isinstance(nullable, list) or any(c not in columns for c in nullable):
            raise ValueError("nullable_columns muss eine Liste deklarierter Messwertspalten sein.")
        sync_mode = metadata.get("sync_mode", "simulation" if metadata.get("simulated") is True else "none")
        if sync_mode not in {"none", "simulation", "best_effort_host_time_v1",
                            "atracsys_timestamp_correlated_v1"}:
            raise ValueError("Unbekannter sync_mode im Messskript.")
        return cls(path, name.strip(), tuple(columns), defaults, metadata.get("simulated") is True,
                   tuple(nullable), sync_mode)


def validate_values(values: object, columns: tuple[str, ...], nullable_columns=()) -> dict[str, float | None]:
    if not isinstance(values, dict) or set(values) != set(columns):
        raise ValueError("Messwerte stimmen nicht mit den deklarierten Spalten überein.")
    result = {}
    for column in columns:
        value = values[column]
        if value is None and column in nullable_columns:
            result[column] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Ungültiger Messwert für {column}: endliche Zahl erwartet.")
        result[column] = float(value)
    return result


def hardware_requirement(specs: list[ScriptSpec], tracker_mode: str) -> str:
    """Allow simulations only with mock data and software-aligned sources only with real tracking."""
    if not specs:
        return ""
    if tracker_mode.lower() == "mock" and all(s.simulated for s in specs):
        return ""
    if tracker_mode.lower() != "mock" and all(s.sync_mode == "best_effort_host_time_v1" for s in specs):
        return ""
    if any(s.sync_mode == "atracsys_timestamp_correlated_v1" for s in specs):
        return ("Exakte Gleichzeitigkeit ist mit fusionTrack500 und NAU7802 nicht möglich: "
                "Die Kamera misst mit 335 Hz, der frei laufende NAU7802 mit maximal 320 SPS und besitzt "
                "keinen externen Wandlungs-Trigger. Der vorbereitete Treiber kann den Abstand jeder "
                "ADC-Wandlung zur Kamera-Belichtung messen, erfüllt aber die gewählte Anforderung "
                "'exakt gleichzeitig' nicht. Die Aufnahme bleibt deshalb gesperrt.")
    return ("Mindestens ein ausgewähltes Skript unterstützt den gemeinsamen Software-Zeitabgleich nicht. "
            "Das Skript muss sync_mode='best_effort_host_time_v1' deklarieren und seine Messwerte "
            "anhand des übergebenen computer_monotonic_s-Zeitpunkts zuordnen. Mit 'Verbindung testen' "
            "kann die Quelle bereits geprüft werden.")
