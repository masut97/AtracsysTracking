from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROTATION_COLUMNS = (
    "r00",
    "r01",
    "r02",
    "r10",
    "r11",
    "r12",
    "r20",
    "r21",
    "r22",
)

DEFAULT_CONFIG = {
    "markers": {
        "reference": 299,
        "cranial": 301,
        "caudal": 302,
    },
    "baseline_seconds": 1.0,
    "default_movement": "FE",
    "movement_axes": {
        "FE": [0.0, 1.0, 0.0],
        "LB": [1.0, 0.0, 0.0],
    },
    "angle_sign": {
        "FE": 1.0,
        "LB": 1.0,
    },
    "registry_path": r"Motion Tracking",
    "zwick_file": "",
    "zwick_search_directories": [],
    "zwick_filename_patterns": ["*.tra", "*.csv", "*.txt", "*.tsv", "*.asc"],
    "zwick_max_age_seconds": 300.0,
    "zwick_wait_seconds": 30.0,
    "zwick_columns": {
        "angle": "",
        "moment": "",
    },
    "open_figure": True,
}


Matrix = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]
Quaternion = tuple[float, float, float, float]


@dataclass(frozen=True)
class Pose:
    rotation: Matrix
    registration_error_mm: float


@dataclass(frozen=True)
class AtracsysData:
    times_s: tuple[float, ...]
    poses: dict[int, tuple[Pose, ...]]
    total_rows: int
    incomplete_samples: int


@dataclass(frozen=True)
class ZwickData:
    angles_deg: tuple[float, ...]
    moments_nm: tuple[float, ...]
    angle_column: str
    moment_column: str
    angle_unit: str
    moment_unit: str
    source: Path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Projizierte Segmentrotation (301 relativ zu 302) im KOS 299 "
            "und Zwick-Moment-Winkel-Kurve"
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Atracsys-CSV der gerade beendeten Aufnahme",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optionale JSON-Konfiguration",
    )
    parser.add_argument(
        "--bewegung",
        choices=("FE", "LB", "fe", "lb"),
        default=None,
        help="Bewegung und Projektionsachse: FE oder LB",
    )
    parser.add_argument(
        "--zwick",
        type=Path,
        default=None,
        help="Zwick-Export (CSV/TXT/TSV/ASC)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Ausgabeordner; Standard: <Atracsys-Dateiname>_auswertung",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        dest="force_open",
        help="Erzeugte SVG-Figure nach der Auswertung öffnen",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Erzeugte SVG-Figure nicht automatisch öffnen",
    )
    return parser.parse_args(argv)


def _deep_merge(base: dict, update: dict) -> dict:
    result = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None) -> tuple[dict, Path]:
    config_path = path or Path(__file__).with_name("auswertung_config.json")
    user_config: dict = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8-sig") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, dict):
            raise ValueError("Die Auswertungskonfiguration muss ein JSON-Objekt sein.")
        user_config = loaded
    local_path = config_path.with_suffix(".local.json")
    if path is None and local_path.is_file():
        local = json.loads(local_path.read_text(encoding="utf-8-sig"))
        if not isinstance(local, dict):
            raise ValueError("Lokale Auswertungskonfiguration muss ein JSON-Objekt sein.")
        user_config = _deep_merge(user_config, local)
    return _deep_merge(DEFAULT_CONFIG, user_config), config_path


def _matrix(values: Sequence[float]) -> Matrix:
    if len(values) != 9:
        raise ValueError("Eine Rotationsmatrix benötigt neun Werte.")
    return (
        (float(values[0]), float(values[1]), float(values[2])),
        (float(values[3]), float(values[4]), float(values[5])),
        (float(values[6]), float(values[7]), float(values[8])),
    )


def transpose(matrix: Matrix) -> Matrix:
    return (
        (matrix[0][0], matrix[1][0], matrix[2][0]),
        (matrix[0][1], matrix[1][1], matrix[2][1]),
        (matrix[0][2], matrix[1][2], matrix[2][2]),
    )


def multiply(left: Matrix, right: Matrix) -> Matrix:
    right_t = transpose(right)
    return tuple(
        tuple(sum(a * b for a, b in zip(row, column)) for column in right_t)
        for row in left
    )  # type: ignore[return-value]


def _normalize_quaternion(quaternion: Sequence[float]) -> Quaternion:
    length = math.sqrt(sum(value * value for value in quaternion))
    if length <= 1e-15:
        raise ValueError("Ungültige Rotation mit Quaternionlänge null.")
    return tuple(float(value / length) for value in quaternion)  # type: ignore[return-value]


def matrix_to_quaternion(matrix: Matrix) -> Quaternion:
    m00, m01, m02 = matrix[0]
    m10, m11, m12 = matrix[1]
    m20, m21, m22 = matrix[2]
    trace = m00 + m11 + m22

    if trace > 0.0:
        scale = math.sqrt(max(trace + 1.0, 0.0)) * 2.0
        quaternion = (
            0.25 * scale,
            (m21 - m12) / scale,
            (m02 - m20) / scale,
            (m10 - m01) / scale,
        )
    elif m00 > m11 and m00 > m22:
        scale = math.sqrt(max(1.0 + m00 - m11 - m22, 0.0)) * 2.0
        quaternion = (
            (m21 - m12) / scale,
            0.25 * scale,
            (m01 + m10) / scale,
            (m02 + m20) / scale,
        )
    elif m11 > m22:
        scale = math.sqrt(max(1.0 + m11 - m00 - m22, 0.0)) * 2.0
        quaternion = (
            (m02 - m20) / scale,
            (m01 + m10) / scale,
            0.25 * scale,
            (m12 + m21) / scale,
        )
    else:
        scale = math.sqrt(max(1.0 + m22 - m00 - m11, 0.0)) * 2.0
        quaternion = (
            (m10 - m01) / scale,
            (m02 + m20) / scale,
            (m12 + m21) / scale,
            0.25 * scale,
        )

    normalized = _normalize_quaternion(quaternion)
    if normalized[0] < 0.0:
        return tuple(-value for value in normalized)  # type: ignore[return-value]
    return normalized


def quaternion_to_matrix(quaternion: Quaternion) -> Matrix:
    w, x, y, z = _normalize_quaternion(quaternion)
    return (
        (
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
        ),
        (
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
        ),
        (
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ),
    )


def average_rotation(rotations: Sequence[Matrix]) -> Matrix:
    if not rotations:
        raise ValueError("Für die Baseline ist keine Rotation vorhanden.")
    reference = matrix_to_quaternion(rotations[0])
    total = [0.0, 0.0, 0.0, 0.0]
    for rotation in rotations:
        quaternion = matrix_to_quaternion(rotation)
        if sum(a * b for a, b in zip(reference, quaternion)) < 0.0:
            quaternion = tuple(-value for value in quaternion)  # type: ignore[assignment]
        for index, value in enumerate(quaternion):
            total[index] += value
    return quaternion_to_matrix(_normalize_quaternion(total))


def quaternion_to_rotvec(quaternion: Quaternion) -> tuple[float, float, float]:
    w, x, y, z = _normalize_quaternion(quaternion)
    vector_length = math.sqrt(x * x + y * y + z * z)
    if vector_length <= 1e-14:
        return (0.0, 0.0, 0.0)
    angle = 2.0 * math.atan2(vector_length, w)
    if angle > math.pi:
        angle -= 2.0 * math.pi
    scale = angle / vector_length
    return (x * scale, y * scale, z * scale)


def _normalize_axis(axis: Sequence[float]) -> tuple[float, float, float]:
    if len(axis) != 3:
        raise ValueError("Eine Projektionsachse muss genau drei Komponenten haben.")
    length = math.sqrt(sum(float(value) ** 2 for value in axis))
    if length <= 1e-15:
        raise ValueError("Die Projektionsachse darf nicht null sein.")
    return tuple(float(value) / length for value in axis)  # type: ignore[return-value]


def load_atracsys(path: Path, marker_ids: Sequence[int]) -> AtracsysData:
    frames: dict[str, dict[int, Pose]] = {}
    frame_times: dict[str, float] = {}
    total_rows = 0

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError("Die Atracsys-CSV enthält keine Spaltenüberschriften.")
        required = {"geometry_id", "visible", *ROTATION_COLUMNS}
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(
                "In der Atracsys-CSV fehlen Spalten: " + ", ".join(sorted(missing))
            )
        if not ({"shared_sample_index", "frame_counter"} & set(reader.fieldnames)):
            raise ValueError(
                "In der Atracsys-CSV fehlt shared_sample_index bzw. frame_counter."
            )
        if not ({"time_since_start_s", "computer_monotonic_s"} & set(reader.fieldnames)):
            raise ValueError(
                "In der Atracsys-CSV fehlt time_since_start_s bzw. computer_monotonic_s."
            )

        wanted = set(marker_ids)
        for row in reader:
            total_rows += 1
            try:
                geometry_id = int(row["geometry_id"])
            except (TypeError, ValueError):
                continue
            if geometry_id not in wanted or row.get("visible", "") != "1":
                continue
            # Bei Registry-Aufnahmen sind die gemeinsamen Skriptfelder als
            # Spalten vorhanden, aber ohne Zusatzmessgerät leer. Dann gelten
            # frame_counter und computer_monotonic_s als Kamera-Referenz.
            sample = (
                row.get("shared_sample_index", "").strip()
                or row.get("frame_counter", "").strip()
            )
            time_text = (
                row.get("time_since_start_s", "").strip()
                or row.get("computer_monotonic_s", "").strip()
            )
            if not sample or not time_text:
                continue
            rotation = _matrix([float(row[name]) for name in ROTATION_COLUMNS])
            error_text = row.get("registration_error_mm", "")
            registration_error = float(error_text) if error_text else math.nan
            frames.setdefault(sample, {})[geometry_id] = Pose(
                rotation=rotation,
                registration_error_mm=registration_error,
            )
            frame_times[sample] = float(time_text)

    complete = [
        (frame_times[sample], poses)
        for sample, poses in frames.items()
        if all(marker_id in poses for marker_id in marker_ids)
    ]
    complete.sort(key=lambda item: item[0])
    if not complete:
        joined = ", ".join(str(marker_id) for marker_id in marker_ids)
        raise ValueError(f"Keine vollständigen Samples mit Marker {joined} gefunden.")

    start_time = complete[0][0]
    times = tuple(item[0] - start_time for item in complete)
    poses_by_marker = {
        marker_id: tuple(item[1][marker_id] for item in complete)
        for marker_id in marker_ids
    }
    return AtracsysData(
        times_s=times,
        poses=poses_by_marker,
        total_rows=total_rows,
        incomplete_samples=len(frames) - len(complete),
    )


def projected_segment_angle(
    data: AtracsysData,
    reference_id: int,
    cranial_id: int,
    caudal_id: int,
    axis: Sequence[float],
    baseline_seconds: float,
    sign: float = 1.0,
) -> tuple[tuple[float, ...], int]:
    """Return 301-vs-302 rotation projected onto an axis of KOS 299.

    Each marker is first expressed in the instantaneous reference frame 299.
    Caudal motion is then removed and the caudal marker is placed back into its
    mean baseline orientation.  The logarithmic rotation vector of the remaining
    cranial motion is projected onto the requested reference axis.  This avoids
    Euler cross-talk between FE and LB.
    """

    normalized_axis = _normalize_axis(axis)
    baseline_count = sum(time_s <= baseline_seconds for time_s in data.times_s)
    baseline_count = min(len(data.times_s), max(5, baseline_count))

    reference = data.poses[reference_id]
    cranial = data.poses[cranial_id]
    caudal = data.poses[caudal_id]
    caudal_in_reference: list[Matrix] = []
    cranial_in_reference: list[Matrix] = []

    for ref_pose, cranial_pose, caudal_pose in zip(reference, cranial, caudal):
        reference_t = transpose(ref_pose.rotation)
        caudal_in_reference.append(multiply(reference_t, caudal_pose.rotation))
        cranial_in_reference.append(multiply(reference_t, cranial_pose.rotation))

    caudal_baseline = average_rotation(caudal_in_reference[:baseline_count])
    compensated_cranial = [
        multiply(
            multiply(caudal_baseline, transpose(caudal_rotation)),
            cranial_rotation,
        )
        for caudal_rotation, cranial_rotation in zip(
            caudal_in_reference, cranial_in_reference
        )
    ]
    cranial_baseline = average_rotation(compensated_cranial[:baseline_count])

    projected: list[float] = []
    previous_quaternion: Quaternion | None = None
    for rotation in compensated_cranial:
        delta = multiply(rotation, transpose(cranial_baseline))
        quaternion = matrix_to_quaternion(delta)
        if previous_quaternion is not None and sum(
            a * b for a, b in zip(previous_quaternion, quaternion)
        ) < 0.0:
            quaternion = tuple(-value for value in quaternion)  # type: ignore[assignment]
        previous_quaternion = quaternion
        rotvec = quaternion_to_rotvec(quaternion)
        component_rad = sum(a * b for a, b in zip(rotvec, normalized_axis))
        projected.append(math.degrees(component_rad) * float(sign))

    return tuple(projected), baseline_count


def _registry_values(path: str) -> dict[str, str]:
    if os.name != "nt":
        return {}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            result: dict[str, str] = {}
            for name in ("Movement", "ZwickFile", "StartedAtUnixMs"):
                try:
                    result[name] = str(winreg.QueryValueEx(key, name)[0])
                except FileNotFoundError:
                    pass
            return result
    except (FileNotFoundError, OSError):
        return {}


def determine_movement(
    explicit: str | None,
    input_path: Path,
    config: dict,
    registry: dict[str, str],
    zwick_path: Path | None = None,
) -> tuple[str, str]:
    if explicit:
        return explicit.upper(), "command_line"

    # Der echte testXpert-Export trägt die Bewegungsart im Dateinamen,
    # beispielsweise "monotest_a_FE_.tra". Dieser Name hat Vorrang vor
    # Registrywerten, damit keine alte Auswahl in den nächsten Test gelangt.
    named_paths = []
    if zwick_path is not None:
        named_paths.append((zwick_path, "zwick_filename"))
    named_paths.append((input_path, "atracsys_filename"))
    for named_path, source in named_paths:
        normalized_name = re.sub(r"[^a-z0-9]+", "_", named_path.stem.lower())
        if re.search(r"(^|_)(fe|flexion|extension|flexext)($|_)", normalized_name):
            return "FE", source
        if re.search(
            r"(^|_)(lb|lateral|lateralflexion|latbend|seitenneigung)($|_)",
            normalized_name,
        ):
            return "LB", source

    registry_movement = registry.get("Movement", "").strip().upper()
    if registry_movement in {"FE", "LB"}:
        return registry_movement, "registry_fallback"

    fallback = str(config.get("default_movement", "FE")).strip().upper()
    if fallback not in {"FE", "LB"}:
        raise ValueError("default_movement muss FE oder LB sein.")
    return fallback, "config_default"


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_like = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", ascii_like.lower())


def _decode_table(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Zeichencodierung konnte nicht gelesen werden: {path}")


def _detect_delimiter(lines: Sequence[str]) -> str:
    candidates = ("\t", ";", ",")
    sample = [line for line in lines[:30] if line.strip()]
    scores = {
        candidate: statistics.median(line.count(candidate) for line in sample)
        if sample
        else 0
        for candidate in candidates
    }
    delimiter = max(candidates, key=lambda candidate: scores[candidate])
    if scores[delimiter] <= 0:
        raise ValueError("Trennzeichen der Zwick-Datei konnte nicht erkannt werden.")
    return delimiter


def _parse_number(value: str) -> float:
    text = value.strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise ValueError("leerer Zahlenwert")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def _column_score(name: str, unit: str, kind: str) -> int:
    normalized_name = _normalized_text(name)
    normalized_unit = _normalized_text(unit)
    if kind == "angle":
        keywords = ("winkel", "angle", "rotation", "drehwinkel", "torsion")
        unit_match = normalized_unit in {"deg", "degree", "grad", "rad"} or "°" in unit
    else:
        keywords = ("moment", "torque", "drehmoment")
        unit_match = "nm" in normalized_unit or "nmm" in normalized_unit
    score = max((20 if keyword in normalized_name else 0) for keyword in keywords)
    if "standard" in normalized_name:
        score += 3
    if "workaround" in normalized_name:
        score -= 2
    if unit_match:
        score += 10
    return score


def _find_column(
    headers: Sequence[str],
    units: Sequence[str],
    kind: str,
    configured: str,
) -> int | None:
    if configured:
        wanted = _normalized_text(configured)
        for index, header in enumerate(headers):
            if _normalized_text(header) == wanted:
                return index
        raise ValueError(
            f"Konfigurierte Zwick-Spalte '{configured}' wurde nicht gefunden."
        )
    scored = [
        (_column_score(header, units[index] if index < len(units) else "", kind), index)
        for index, header in enumerate(headers)
    ]
    best_score, best_index = max(scored, default=(0, -1))
    return best_index if best_score > 0 else None


def load_zwick(path: Path, configured_columns: dict) -> ZwickData:
    text = _decode_table(path)
    lines = text.splitlines()
    delimiter = _detect_delimiter(lines)
    rows = [
        [cell.strip().strip('"') for cell in row]
        for row in csv.reader(lines, delimiter=delimiter)
    ]

    header_index: int | None = None
    angle_index: int | None = None
    moment_index: int | None = None
    selected_units: list[str] = []
    for index, headers in enumerate(rows[:50]):
        if len(headers) < 2:
            continue
        units = rows[index + 1] if index + 1 < len(rows) else []
        try:
            candidate_angle = _find_column(
                headers, units, "angle", str(configured_columns.get("angle", ""))
            )
            candidate_moment = _find_column(
                headers, units, "moment", str(configured_columns.get("moment", ""))
            )
        except ValueError:
            continue
        if candidate_angle is not None and candidate_moment is not None:
            header_index = index
            angle_index = candidate_angle
            moment_index = candidate_moment
            selected_units = units
            break

    if header_index is None or angle_index is None or moment_index is None:
        raise ValueError(
            "Keine Zwick-Spalten für Winkel und Moment erkannt. "
            "Bitte zwick_columns in auswertung_config.json setzen."
        )

    headers = rows[header_index]
    angle_unit = selected_units[angle_index] if angle_index < len(selected_units) else ""
    moment_unit = selected_units[moment_index] if moment_index < len(selected_units) else ""
    normalized_angle_unit = _normalized_text(angle_unit)
    normalized_moment_unit = _normalized_text(moment_unit)
    angle_factor = 180.0 / math.pi if "rad" in normalized_angle_unit else 1.0
    if "knm" in normalized_moment_unit:
        moment_factor = 1000.0
    elif "nmm" in normalized_moment_unit:
        moment_factor = 0.001
    else:
        moment_factor = 1.0

    angles: list[float] = []
    moments: list[float] = []
    required_index = max(angle_index, moment_index)
    for row in rows[header_index + 1 :]:
        if len(row) <= required_index:
            continue
        try:
            angle = _parse_number(row[angle_index]) * angle_factor
            moment = _parse_number(row[moment_index]) * moment_factor
        except ValueError:
            continue
        if math.isfinite(angle) and math.isfinite(moment):
            angles.append(angle)
            moments.append(moment)

    if len(angles) < 2:
        raise ValueError("Die Zwick-Datei enthält weniger als zwei gültige Datenzeilen.")
    return ZwickData(
        angles_deg=tuple(angles),
        moments_nm=tuple(moments),
        angle_column=headers[angle_index],
        moment_column=headers[moment_index],
        angle_unit=angle_unit,
        moment_unit=moment_unit,
        source=path,
    )


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def _explicit_zwick_path(
    command_line: Path | None,
    config: dict,
    registry: dict[str, str],
) -> Path | None:
    if command_line is not None:
        return command_line.resolve()
    registry_path = registry.get("ZwickFile", "").strip()
    if registry_path:
        return _expand_path(registry_path)
    configured_path = str(config.get("zwick_file", "")).strip()
    if configured_path:
        return _expand_path(configured_path)
    environment_path = os.environ.get("ZWICK_EXPORT_FILE", "").strip()
    if environment_path:
        return _expand_path(environment_path)
    return None


def _scan_zwick_candidates(
    input_path: Path,
    config: dict,
    registry: dict[str, str],
) -> list[Path]:
    directories = [input_path.parent]
    directories.extend(
        _expand_path(str(value))
        for value in config.get("zwick_search_directories", [])
        if str(value).strip()
    )
    patterns = [str(value) for value in config.get("zwick_filename_patterns", [])]
    max_age = float(config.get("zwick_max_age_seconds", 300.0))
    input_mtime = input_path.stat().st_mtime
    started_at_text = registry.get("StartedAtUnixMs", "").strip()
    try:
        start_cutoff = int(started_at_text) / 1000.0 - 5.0
    except (TypeError, ValueError):
        # Ohne explizite Startzeit nur Exporte desselben Prüfendes zulassen.
        # Dadurch wird während der Wartezeit nicht die vorherige Messung gewählt.
        start_cutoff = input_mtime - 10.0
    candidates: dict[Path, float] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for pattern in patterns:
            for candidate in directory.glob(pattern):
                if not candidate.is_file() or candidate.resolve() == input_path.resolve():
                    continue
                lowered = candidate.name.lower()
                if ".script_" in lowered or lowered.endswith(".scripts.json"):
                    continue
                if candidate.stat().st_mtime < start_cutoff:
                    continue
                age = abs(candidate.stat().st_mtime - input_mtime)
                if age <= max_age:
                    name_bonus = 30.0 if "zwick" in lowered else 0.0
                    stem_bonus = 20.0 if input_path.stem.lower() in lowered else 0.0
                    candidates[candidate.resolve()] = name_bonus + stem_bonus - age
    return [item[0] for item in sorted(candidates.items(), key=lambda item: item[1], reverse=True)]


def _file_is_stable(path: Path, stable_for_seconds: float = 0.75) -> bool:
    try:
        first = path.stat()
        time.sleep(stable_for_seconds)
        second = path.stat()
    except OSError:
        return False
    return (
        first.st_size > 0
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )


def resolve_zwick(
    input_path: Path,
    command_line: Path | None,
    config: dict,
    registry: dict[str, str],
) -> tuple[ZwickData | None, list[str]]:
    warnings: list[str] = []
    explicit = _explicit_zwick_path(command_line, config, registry)
    configured_columns = dict(config.get("zwick_columns", {}))
    candidates: list[Path] = []

    if explicit is not None:
        wait_seconds = max(0.0, float(config.get("zwick_wait_seconds", 30.0)))
        deadline = time.monotonic() + wait_seconds
        while not explicit.exists() and time.monotonic() < deadline:
            time.sleep(0.25)
        if explicit.is_dir():
            for pattern in config.get("zwick_filename_patterns", []):
                candidates.extend(explicit.glob(str(pattern)))
            candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        elif explicit.exists():
            candidates = [explicit]
        else:
            warnings.append(f"Angegebene Zwick-Datei wurde nicht gefunden: {explicit}")

    deadline = time.monotonic() + max(
        0.0, float(config.get("zwick_wait_seconds", 30.0))
    )
    tried_errors: dict[Path, str] = {}
    while True:
        if not candidates:
            candidates = _scan_zwick_candidates(input_path, config, registry)

        for candidate in candidates:
            if not _file_is_stable(candidate):
                continue
            try:
                return load_zwick(candidate, configured_columns), warnings
            except (OSError, ValueError) as error:
                tried_errors[candidate] = str(error)

        if time.monotonic() >= deadline:
            break
        candidates = []
        time.sleep(0.25)

    for candidate, error in tried_errors.items():
        warnings.append(f"Zwick-Datei übersprungen ({candidate.name}): {error}")

    warnings.append(
        "Keine passende Zwick-Datei gefunden; die untere Figure bleibt als Hinweisfeld erhalten."
    )
    return None, warnings


def _nice_ticks(values: Sequence[float], count: int = 6) -> tuple[float, float, list[float]]:
    finite = [float(value) for value in values if math.isfinite(value)]
    if not finite:
        return 0.0, 1.0, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    minimum = min(finite)
    maximum = max(finite)
    if math.isclose(minimum, maximum, rel_tol=0.0, abs_tol=1e-12):
        padding = max(abs(minimum) * 0.1, 1.0)
        minimum -= padding
        maximum += padding
    raw_step = (maximum - minimum) / max(count - 1, 1)
    exponent = math.floor(math.log10(raw_step))
    fraction = raw_step / (10**exponent)
    if fraction <= 1.0:
        nice_fraction = 1.0
    elif fraction <= 2.0:
        nice_fraction = 2.0
    elif fraction <= 5.0:
        nice_fraction = 5.0
    else:
        nice_fraction = 10.0
    step = nice_fraction * (10**exponent)
    lower = math.floor(minimum / step) * step
    upper = math.ceil(maximum / step) * step
    ticks: list[float] = []
    current = lower
    while current <= upper + step * 0.25 and len(ticks) < 100:
        ticks.append(current)
        current += step
    return lower, upper, ticks


def _format_tick(value: float) -> str:
    if abs(value) >= 1000 or (0 < abs(value) < 0.001):
        return f"{value:.2e}"
    return f"{value:.6g}".replace("-0", "0")


def _svg_plot(
    x_values: Sequence[float],
    y_values: Sequence[float],
    box: tuple[float, float, float, float],
    x_label: str,
    y_label: str,
    color: str,
    title: str,
    gap_limit: float | None = None,
) -> str:
    left, top, width, height = box
    x_min, x_max, x_ticks = _nice_ticks(x_values)
    y_min, y_max, y_ticks = _nice_ticks(y_values)

    def sx(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * width

    def sy(value: float) -> float:
        return top + height - (value - y_min) / (y_max - y_min) * height

    parts = [
        f'<text x="{left:.1f}" y="{top - 22:.1f}" class="panel-title">{html.escape(title)}</text>',
        f'<rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" class="plot-bg"/>',
    ]
    for tick in x_ticks:
        x = sx(tick)
        parts.append(f'<line x1="{x:.2f}" y1="{top:.2f}" x2="{x:.2f}" y2="{top + height:.2f}" class="grid"/>')
        parts.append(f'<text x="{x:.2f}" y="{top + height + 28:.2f}" class="tick" text-anchor="middle">{html.escape(_format_tick(tick))}</text>')
    for tick in y_ticks:
        y = sy(tick)
        parts.append(f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{left + width:.2f}" y2="{y:.2f}" class="grid"/>')
        parts.append(f'<text x="{left - 15:.2f}" y="{y + 5:.2f}" class="tick" text-anchor="end">{html.escape(_format_tick(tick))}</text>')
    parts.append(f'<rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" class="frame"/>')

    points = list(zip(x_values, y_values))
    if len(points) > 5000:
        stride = math.ceil(len(points) / 5000)
        points = points[::stride]
        if points[-1] != (x_values[-1], y_values[-1]):
            points.append((x_values[-1], y_values[-1]))
    segments: list[list[tuple[float, float]]] = [[]]
    previous_x: float | None = None
    for x_value, y_value in points:
        if not (math.isfinite(x_value) and math.isfinite(y_value)):
            if segments[-1]:
                segments.append([])
            previous_x = None
            continue
        if (
            gap_limit is not None
            and previous_x is not None
            and x_value - previous_x > gap_limit
            and segments[-1]
        ):
            segments.append([])
        segments[-1].append((x_value, y_value))
        previous_x = x_value
    for segment in segments:
        if len(segment) < 2:
            continue
        commands = [f"M {sx(segment[0][0]):.2f} {sy(segment[0][1]):.2f}"]
        commands.extend(f"L {sx(x):.2f} {sy(y):.2f}" for x, y in segment[1:])
        parts.append(f'<path d="{" ".join(commands)}" fill="none" stroke="{color}" stroke-width="2.5"/>')

    parts.append(f'<text x="{left + width / 2:.1f}" y="{top + height + 62:.1f}" class="axis-label" text-anchor="middle">{html.escape(x_label)}</text>')
    parts.append(f'<text x="{left - 82:.1f}" y="{top + height / 2:.1f}" class="axis-label" text-anchor="middle" transform="rotate(-90 {left - 82:.1f} {top + height / 2:.1f})">{html.escape(y_label)}</text>')
    return "\n".join(parts)


def render_figure(
    output: Path,
    times_s: Sequence[float],
    angles_deg: Sequence[float],
    movement: str,
    axis: Sequence[float],
    marker_ids: tuple[int, int, int],
    zwick: ZwickData | None,
    warnings: Sequence[str],
) -> None:
    reference_id, cranial_id, caudal_id = marker_ids
    if len(times_s) > 2:
        positive_steps = [
            right - left
            for left, right in zip(times_s, times_s[1:])
            if right > left
        ]
        median_step = statistics.median(positive_steps) if positive_steps else 0.02
        gap_limit = max(0.1, median_step * 5.0)
    else:
        gap_limit = None

    top_plot = _svg_plot(
        times_s,
        angles_deg,
        (145.0, 155.0, 1385.0, 310.0),
        "Zeit [s]",
        f"projizierter {movement}-Winkel [°]",
        "#1769aa",
        f"Atracsys: Marker {cranial_id} relativ zu {caudal_id}, projiziert im KOS {reference_id}",
        gap_limit,
    )
    if zwick is not None:
        bottom_plot = _svg_plot(
            zwick.angles_deg,
            zwick.moments_nm,
            (145.0, 625.0, 1385.0, 310.0),
            "Zwick-Winkel [°]",
            "Zwick-Moment [Nm]",
            "#d95f02",
            f"Zwick: Moment–Winkel ({zwick.source.name})",
        )
        zwick_note = (
            f"Spalten: {html.escape(zwick.angle_column)} / "
            f"{html.escape(zwick.moment_column)}"
        )
    else:
        bottom_plot = "\n".join(
            (
                '<text x="145" y="603" class="panel-title">Zwick: Moment–Winkel</text>',
                '<rect x="145" y="625" width="1385" height="310" class="plot-bg"/>',
                '<rect x="145" y="625" width="1385" height="310" class="frame"/>',
                '<text x="837.5" y="775" class="missing" text-anchor="middle">Keine passende Zwick-Exportdatei gefunden</text>',
                '<text x="837.5" y="810" class="note" text-anchor="middle">Dateipfad beim testXpert-Aufruf übergeben oder in auswertung_config.json konfigurieren.</text>',
            )
        )
        zwick_note = ""

    warning_text = " | ".join(warnings[-2:]) if warnings else ""
    axis_text = ", ".join(f"{float(value):g}" for value in axis)
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1680" height="1050" viewBox="0 0 1680 1050">
  <style>
    .background {{ fill: #ffffff; }}
    .plot-bg {{ fill: #f8fafc; }}
    .frame {{ fill: none; stroke: #334155; stroke-width: 1.4; }}
    .grid {{ stroke: #cbd5e1; stroke-width: 1; opacity: 0.65; }}
    .title {{ font: 700 29px Arial, sans-serif; fill: #0f172a; }}
    .subtitle {{ font: 16px Arial, sans-serif; fill: #475569; }}
    .panel-title {{ font: 700 18px Arial, sans-serif; fill: #0f172a; }}
    .axis-label {{ font: 16px Arial, sans-serif; fill: #1e293b; }}
    .tick {{ font: 14px Arial, sans-serif; fill: #475569; }}
    .note {{ font: 14px Arial, sans-serif; fill: #64748b; }}
    .warning {{ font: 13px Arial, sans-serif; fill: #9a3412; }}
    .missing {{ font: 700 20px Arial, sans-serif; fill: #64748b; }}
  </style>
  <rect width="1680" height="1050" class="background"/>
  <text x="80" y="58" class="title">Wirbelsäulen-Auswertung – {html.escape(movement)}</text>
  <text x="80" y="91" class="subtitle">Referenzmarker {reference_id}; kaudal {caudal_id}; kranial {cranial_id}; Projektionsachse [{html.escape(axis_text)}]</text>
  {top_plot}
  {bottom_plot}
  <text x="145" y="982" class="note">{zwick_note}</text>
  <text x="145" y="1012" class="warning">{html.escape(warning_text)}</text>
</svg>
'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    html_output = output.with_suffix(".html")
    embedded_svg = document.split("?>", 1)[-1].lstrip()
    html_output.write_text(
        "<!doctype html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        "<title>Atracsys-Zwick-Auswertung</title>"
        '<style>html,body{margin:0;background:#fff}svg{display:block;width:100%;height:auto}</style>'
        "</head><body>\n"
        + embedded_svg
        + "\n</body></html>\n",
        encoding="utf-8",
    )


def write_angle_csv(
    output: Path,
    times_s: Sequence[float],
    angles_deg: Sequence[float],
    movement: str,
) -> None:
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time_s", f"projected_{movement}_angle_deg"))
        writer.writerows(zip(times_s, angles_deg))


def open_figure_in_browser(path: Path) -> None:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", ""))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("ProgramFiles", ""))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("ProgramFiles", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    ]
    uri = path.resolve().as_uri()
    for browser in candidates:
        if browser.is_file():
            subprocess.Popen(
                [str(browser), uri],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif not webbrowser.open(uri):
        raise OSError("Kein Browser zum Öffnen der Figure gefunden.")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config, config_path = load_config(args.config)
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Atracsys-Datei wurde nicht gefunden: {input_path}")

    marker_config = dict(config["markers"])
    reference_id = int(marker_config["reference"])
    cranial_id = int(marker_config["cranial"])
    caudal_id = int(marker_config["caudal"])
    marker_ids = (reference_id, cranial_id, caudal_id)
    registry = _registry_values(str(config["registry_path"]))
    atracsys = load_atracsys(input_path, marker_ids)
    zwick, warnings = resolve_zwick(input_path, args.zwick, config, registry)
    movement, movement_source = determine_movement(
        args.bewegung,
        input_path,
        config,
        registry,
        zwick.source if zwick is not None else None,
    )
    axis = config["movement_axes"][movement]
    sign = float(config.get("angle_sign", {}).get(movement, 1.0))
    baseline_seconds = float(config.get("baseline_seconds", 1.0))

    angles_deg, baseline_count = projected_segment_angle(
        atracsys,
        reference_id,
        cranial_id,
        caudal_id,
        axis,
        baseline_seconds,
        sign,
    )
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else input_path.parent / f"{input_path.stem}_auswertung"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path = output_dir / f"{input_path.stem}_{movement}_figure.svg"
    figure_html_path = figure_path.with_suffix(".html")
    angle_csv_path = output_dir / f"{input_path.stem}_{movement}_winkel.csv"
    report_path = output_dir / f"{input_path.stem}_{movement}_report.json"

    render_figure(
        figure_path,
        atracsys.times_s,
        angles_deg,
        movement,
        axis,
        marker_ids,
        zwick,
        warnings,
    )
    write_angle_csv(angle_csv_path, atracsys.times_s, angles_deg, movement)

    finite_errors = {
        str(marker_id): [
            pose.registration_error_mm
            for pose in atracsys.poses[marker_id]
            if math.isfinite(pose.registration_error_mm)
        ]
        for marker_id in marker_ids
    }
    report = {
        "status": "ok",
        "input": str(input_path),
        "config": str(config_path.resolve()),
        "markers": marker_config,
        "movement": movement,
        "movement_source": movement_source,
        "projection_axis_in_reference_kos": list(_normalize_axis(axis)),
        "angle_sign": sign,
        "method": (
            "reference compensation + caudal compensation + "
            "rotation-vector projection"
        ),
        "raw_rows": atracsys.total_rows,
        "complete_samples": len(atracsys.times_s),
        "incomplete_samples": atracsys.incomplete_samples,
        "duration_s": atracsys.times_s[-1],
        "baseline_seconds": baseline_seconds,
        "baseline_samples": baseline_count,
        "angle_deg": {
            "minimum": min(angles_deg),
            "maximum": max(angles_deg),
            "peak_to_peak": max(angles_deg) - min(angles_deg),
        },
        "registration_error_mm": {
            marker_id: {
                "median": statistics.median(values) if values else None,
                "maximum": max(values) if values else None,
            }
            for marker_id, values in finite_errors.items()
        },
        "zwick": None
        if zwick is None
        else {
            "source": str(zwick.source.resolve()),
            "samples": len(zwick.angles_deg),
            "angle_column": zwick.angle_column,
            "moment_column": zwick.moment_column,
            "source_angle_unit": zwick.angle_unit,
            "source_moment_unit": zwick.moment_unit,
            "output_angle_unit": "deg",
            "output_moment_unit": "Nm",
        },
        "warnings": warnings,
        "outputs": {
            "figure": str(figure_path.resolve()),
            "figure_html": str(figure_html_path.resolve()),
            "angle_csv": str(angle_csv_path.resolve()),
            "report": str(report_path.resolve()),
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    should_open = bool(config.get("open_figure", True))
    if args.force_open:
        should_open = True
    if args.no_open:
        should_open = False
    if should_open:
        try:
            open_figure_in_browser(figure_html_path)
        except OSError as error:
            print(f"Warnung: Figure konnte nicht automatisch geöffnet werden: {error}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"AUSWERTUNGSFEHLER: {error}", file=sys.stderr)
        raise
