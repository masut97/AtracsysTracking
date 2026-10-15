from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import auswertung as common


ROTATION_MODES = {"FE", "LB", "AR"}
TRANSLATION_MODES = {"AS", "LS", "AC"}
ALL_MODES = ROTATION_MODES | TRANSLATION_MODES

DEFAULT_CONFIG = {
    "markers": {
        "reference": 299,
        "clamp_up": 301,
        "clamp_down": 302,
        "vertebra_cranial": 311,
        "vertebra_caudal": 314,
    },
    "baseline_seconds": 1.0,
    "default_movement": "FE",
    "rotation_axes": {
        "FE": [0.0, 1.0, 0.0],
        "LB": [0.0, 0.0, 1.0],
        "AR": [0.0, 0.0, 1.0],
    },
    "legacy_rotation_axes": {
        "FE": [0.0, 0.0, 1.0],
        "LB": [0.0, 0.0, 1.0],
        "AR": [0.0, 0.0, 1.0],
    },
    "translation_axes": {
        "AS": [0.0, 0.0, 1.0],
        "LS": [0.0, 0.0, 1.0],
        "AC": [0.0, 0.0, 1.0],
    },
    "legacy_translation_axes": {
        "AS": [0.0, 0.0, 1.0],
        "LS": [0.0, 0.0, 1.0],
        "AC": [0.0, 0.0, 1.0],
    },
    "sign": {mode: 1.0 for mode in ALL_MODES},
    "registry_path": r"Motion Tracking",
    "zwick_file": "",
    "zwick_search_directories": [],
    "zwick_filename_patterns": ["*.tra"],
    "zwick_max_age_seconds": 300.0,
    "zwick_wait_seconds": 30.0,
    "zwick_columns": {
        "time": "Prüfzeit",
        "rotation_load": "Standarddrehmoment Torsion",
        "rotation_displacement": "Winkel Workaround",
        "translation_load": "Standardkraft 1",
        "translation_displacement": "Axial Workaround",
    },
    "rotation_legacy_offset_deg": 180000.0,
    "legacy_output_directory": "",
    "write_testxpert_rom_files": True,
    "load_limits": {
        "FE": [-7.5, 7.5],
        "LB": [-7.5, 7.5],
        "AR": [-7.5, 7.5],
        "AS": [-150.0, 150.0],
        "LS": [-150.0, 150.0],
        "AC": [-150.0, 400.0],
    },
    "quality": {
        "vertebra_over_clamp_critical_ratio": 1.10,
        "vertebra_under_clamp_warning_ratio": 0.70,
        "vertebra_under_clamp_critical_ratio": 0.50,
        "minimum_clamp_rom_rotation_deg": 0.50,
        "minimum_clamp_rom_translation_mm": 0.10,
        "edge_peak_fraction": 0.03,
        "duration_warning_fraction": 0.05,
        "duration_critical_fraction": 0.10,
        "incomplete_warning_fraction": 0.01,
        "incomplete_critical_fraction": 0.05,
        "peak_gap_factor": 1.75,
        "peak_zone_fraction": 0.20,
        "peak_gap_min_rom_fraction": 0.03,
        "peak_gap_min_missing_samples": 8,
        "peak_noise_zone_fraction": 0.25,
        "peak_noise_ratio_critical": 3.0,
        "peak_noise_rom_fraction_critical": 0.015,
        "time_gap_factor": 5.0,
        "time_gap_absolute_s": 0.10,
        "registration_warning_mm": 1.0,
        "registration_critical_mm": 2.0,
    },
    "open_figure": True,
}


@dataclass(frozen=True)
class RomPose:
    rotation: common.Matrix
    translation_mm: tuple[float, float, float]
    registration_error_mm: float = math.nan


@dataclass(frozen=True)
class RomTrackingData:
    times_s: tuple[float, ...]
    poses: dict[int, tuple[RomPose, ...]]
    total_rows: int
    incomplete_samples: int
    source_format: str


@dataclass(frozen=True)
class RomZwickData:
    times_s: tuple[float, ...]
    loads: tuple[float, ...]
    displacements: tuple[float, ...]
    vertical_translation: tuple[float, ...]
    load_column: str
    displacement_column: str
    load_unit: str
    displacement_unit: str
    source: Path


@dataclass(frozen=True)
class RomMetrics:
    rom_range: float
    positive_load: float
    negative_load: float
    up_rom: float | None
    down_rom: float | None
    zero_point: float | None
    zero_indices: tuple[int, int] | None
    stored_offset: float


@dataclass(frozen=True)
class QualityFinding:
    severity: str
    code: str
    title: str
    detail: str


def quality_summary(findings: Sequence[QualityFinding]) -> dict:
    """Expose the existing finding severity as a user-facing quality rating.

    This is an automatic screening result; the thresholds and underlying ROM
    calculations remain in evaluate_quality.
    """
    status = ("critical" if any(item.severity == "critical" for item in findings)
              else "warning" if findings else "ok")
    rating, label = {"ok": ("good", "Gut"), "warning": ("medium", "Mittel"),
                     "critical": ("bad", "Schlecht")}[status]
    return {"status": status, "rating": rating, "label": label,
            "hints": [item.detail for item in findings]}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Five-marker ROM analysis replacing the MATLAB functions"
    )
    parser.add_argument("input", type=Path, help="Atracsys CSV")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--bewegung",
        choices=tuple(sorted(ALL_MODES | {mode.lower() for mode in ALL_MODES})),
        default=None,
        help="FE, LB, AR, AS, LS oder AC",
    )
    parser.add_argument("--zwick", type=Path, default=None, help="testXpert .tra file")
    parser.add_argument(
        "--ohne-zwick",
        action="store_true",
        help="Retrospective camera analysis without searching for a .tra file",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--open", action="store_true", dest="force_open")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument(
        "--no-rom-files",
        action="store_true",
        help="Do not write UpROM/DnROM/ZeroPoint files for testXpert",
    )
    return parser.parse_args(argv)


def load_config(path: Path | None) -> tuple[dict, Path]:
    config_path = path or Path(__file__).with_name("rom_auswertung_config.json")
    user_config: dict = {}
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8-sig") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, dict):
            raise ValueError("The ROM configuration must be a JSON object.")
        user_config = loaded
    local_path = config_path.with_suffix(".local.json")
    if path is None and local_path.is_file():
        local = json.loads(local_path.read_text(encoding="utf-8-sig"))
        if not isinstance(local, dict):
            raise ValueError("Local ROM configuration must be a JSON object.")
        user_config = common._deep_merge(user_config, local)
    return common._deep_merge(DEFAULT_CONFIG, user_config), config_path


def _frame_key_and_time(row: dict[str, str]) -> tuple[str, float] | None:
    sample = (
        row.get("shared_sample_index", "").strip()
        or row.get("frame_counter", "").strip()
    )
    time_text = (
        row.get("time_since_start_s", "").strip()
        or row.get("computer_monotonic_s", "").strip()
    )
    if not sample or not time_text:
        return None
    return sample, float(time_text)


def _load_current_tracking(path: Path, marker_ids: Sequence[int]) -> RomTrackingData:
    frames: dict[str, dict[int, RomPose]] = {}
    frame_times: dict[str, float] = {}
    total_rows = 0
    wanted = set(marker_ids)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = set(reader.fieldnames or ())
        required = {
            "geometry_id",
            "visible",
            "x_mm",
            "y_mm",
            "z_mm",
            *common.ROTATION_COLUMNS,
        }
        missing = required.difference(headers)
        if missing:
            raise ValueError(
                "The Atracsys ROM file is missing columns: "
                + ", ".join(sorted(missing))
            )
        for row in reader:
            total_rows += 1
            try:
                marker_id = int(row["geometry_id"])
            except (TypeError, ValueError):
                continue
            if marker_id not in wanted or row.get("visible", "") != "1":
                continue
            key_and_time = _frame_key_and_time(row)
            if key_and_time is None:
                continue
            sample, sample_time = key_and_time
            rotation = common._matrix(
                [float(row[name]) for name in common.ROTATION_COLUMNS]
            )
            error_text = row.get("registration_error_mm", "").strip()
            pose = RomPose(
                rotation=rotation,
                translation_mm=(
                    float(row["x_mm"]),
                    float(row["y_mm"]),
                    float(row["z_mm"]),
                ),
                registration_error_mm=float(error_text) if error_text else math.nan,
            )
            frames.setdefault(sample, {})[marker_id] = pose
            frame_times[sample] = sample_time

    complete = [
        (frame_times[key], poses)
        for key, poses in frames.items()
        if all(marker_id in poses for marker_id in marker_ids)
    ]
    complete.sort(key=lambda item: item[0])
    if not complete:
        raise ValueError(
            "No complete five-marker samples with 299, 301, 302, 311 and 314 were found."
        )
    start = complete[0][0]
    return RomTrackingData(
        times_s=tuple(sample_time - start for sample_time, _ in complete),
        poses={
            marker_id: tuple(poses[marker_id] for _, poses in complete)
            for marker_id in marker_ids
        },
        total_rows=total_rows,
        incomplete_samples=len(frames) - len(complete),
        source_format="atracsys_long_csv",
    )


def _legacy_timestamp_seconds(value: float) -> float:
    integer = int(round(value))
    milliseconds = integer % 1000
    seconds = (integer // 1000) % 100
    minutes = (integer // 100000) % 100
    hours = integer // 10000000
    return hours * 3600.0 + minutes * 60.0 + seconds + milliseconds / 1000.0


def _legacy_matrix(values: Sequence[float]) -> common.Matrix:
    # MATLAB schrieb matrix(1:9) spaltenweise: R11,R21,R31,R12,...
    if len(values) != 9:
        raise ValueError("Invalid rotation matrix in the MATLAB CSV.")
    return (
        (values[0], values[3], values[6]),
        (values[1], values[4], values[7]),
        (values[2], values[5], values[8]),
    )


def _load_legacy_tracking(path: Path, marker_ids: Sequence[int]) -> RomTrackingData:
    marker_order = tuple(marker_ids)
    poses: dict[int, list[RomPose]] = {marker_id: [] for marker_id in marker_order}
    times: list[float] = []
    total_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        headers = next(reader, None)
        if headers is None or len(headers) < 61:
            raise ValueError("The historical MATLAB CSV does not have the expected 61 columns.")
        day_offset = 0.0
        previous_time: float | None = None
        for row in reader:
            total_rows += 1
            if len(row) < 61:
                continue
            try:
                values = [float(value.strip()) for value in row[:61]]
            except ValueError:
                continue
            sample_time = _legacy_timestamp_seconds(values[0])
            if previous_time is not None and sample_time + day_offset < previous_time - 43200:
                day_offset += 86400.0
            sample_time += day_offset
            previous_time = sample_time
            times.append(sample_time)
            for index, marker_id in enumerate(marker_order):
                start = 1 + index * 12
                poses[marker_id].append(
                    RomPose(
                        rotation=_legacy_matrix(values[start + 3 : start + 12]),
                        translation_mm=tuple(values[start : start + 3]),  # type: ignore[arg-type]
                    )
                )
    if not times:
        raise ValueError("The historical MATLAB CSV contains no valid measurement rows.")
    first = times[0]
    return RomTrackingData(
        times_s=tuple(value - first for value in times),
        poses={marker_id: tuple(values) for marker_id, values in poses.items()},
        total_rows=total_rows,
        incomplete_samples=0,
        source_format="legacy_matlab_wide_csv",
    )


def load_tracking(path: Path, marker_ids: Sequence[int]) -> RomTrackingData:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        headers = [value.strip() for value in next(csv.reader(stream), [])]
    if "geometry_id" in headers:
        return _load_current_tracking(path, marker_ids)
    return _load_legacy_tracking(path, marker_ids)


def movement_axis(config: dict, mode: str, source_format: str) -> Sequence[float]:
    legacy = source_format == "legacy_matlab_wide_csv"
    if mode in ROTATION_MODES:
        key = "legacy_rotation_axes" if legacy else "rotation_axes"
    else:
        key = "legacy_translation_axes" if legacy else "translation_axes"
    configured = config.get(key)
    if not isinstance(configured, dict) or mode not in configured:
        fallback_key = "rotation_axes" if mode in ROTATION_MODES else "translation_axes"
        configured = config[fallback_key]
    return configured[mode]


def _normalized_stem(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")


def determine_movement(
    explicit: str | None,
    input_path: Path,
    zwick_path: Path | None,
    registry: dict[str, str],
    config: dict,
) -> tuple[str, str]:
    if explicit:
        return explicit.upper(), "command_line"
    paths = []
    if zwick_path is not None:
        paths.append((zwick_path, "zwick_filename"))
    paths.append((input_path, "atracsys_filename"))
    for path, source in paths:
        stem = _normalized_stem(path)
        for mode in ("FE", "LB", "AR", "AS", "LS", "AC"):
            if re.search(rf"(^|_){mode.lower()}($|_)", stem):
                return mode, source
    registry_mode = registry.get("Movement", "").strip().upper()
    if registry_mode in ALL_MODES:
        return registry_mode, "registry_fallback"
    fallback = str(config.get("default_movement", "FE")).strip().upper()
    if fallback not in ALL_MODES:
        raise ValueError("default_movement is invalid.")
    return fallback, "config_default"


def _resolve_zwick_path(
    input_path: Path,
    command_line: Path | None,
    config: dict,
    registry: dict[str, str],
) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    explicit = common._explicit_zwick_path(command_line, config, registry)
    deadline = time.monotonic() + max(0.0, float(config.get("zwick_wait_seconds", 30.0)))
    while True:
        candidates: list[Path] = []
        if explicit is not None:
            if explicit.is_dir():
                for pattern in config.get("zwick_filename_patterns", ["*.tra"]):
                    candidates.extend(explicit.glob(str(pattern)))
                candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            elif explicit.is_file():
                candidates = [explicit]
        else:
            candidates = common._scan_zwick_candidates(input_path, config, registry)
        for candidate in candidates:
            if common._file_is_stable(candidate, 0.1):
                return candidate.resolve(), warnings
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if explicit is not None:
        warnings.append(f"The specified Zwick file was not found: {explicit}")
    else:
        warnings.append("No time-matched testXpert .tra file was found.")
    return None, warnings


def _column_index(headers: Sequence[str], configured: str) -> int:
    wanted = common._normalized_text(configured)
    for index, header in enumerate(headers):
        if common._normalized_text(header) == wanted:
            return index
    raise ValueError(f"Zwick column '{configured}' was not found.")


def load_zwick(path: Path, mode: str, config: dict) -> RomZwickData:
    lines = common._decode_table(path).splitlines()
    delimiter = common._detect_delimiter(lines)
    rows = [
        [cell.strip().strip('"') for cell in row]
        for row in csv.reader(lines, delimiter=delimiter)
    ]
    columns = dict(config.get("zwick_columns", {}))
    load_key = "rotation_load" if mode in ROTATION_MODES else "translation_load"
    displacement_key = (
        "rotation_displacement" if mode in ROTATION_MODES else "translation_displacement"
    )
    names = {
        "time": str(columns.get("time", "Prüfzeit")),
        "load": str(columns.get(load_key, "")),
        "displacement": str(columns.get(displacement_key, "")),
    }
    header_index: int | None = None
    indices: dict[str, int] = {}
    for index, headers in enumerate(rows[:50]):
        try:
            indices = {key: _column_index(headers, value) for key, value in names.items()}
        except ValueError:
            continue
        header_index = index
        break
    if header_index is None:
        raise ValueError(
            "Required testXpert columns are missing: " + ", ".join(names.values())
        )
    headers = rows[header_index]
    units = rows[header_index + 1] if header_index + 1 < len(rows) else []
    time_values: list[float] = []
    loads: list[float] = []
    displacements: list[float] = []
    required = max(indices.values())
    for row in rows[header_index + 1 :]:
        if len(row) <= required:
            continue
        try:
            sample_time = common._parse_number(row[indices["time"]])
            load = common._parse_number(row[indices["load"]])
            displacement = common._parse_number(row[indices["displacement"]])
        except ValueError:
            continue
        if all(math.isfinite(value) for value in (sample_time, load, displacement)):
            time_values.append(sample_time)
            loads.append(load)
            displacements.append(displacement)
    if len(loads) < 2:
        raise ValueError("The testXpert file contains fewer than two valid data rows.")

    load_unit = units[indices["load"]] if indices["load"] < len(units) else ""
    displacement_unit = (
        units[indices["displacement"]]
        if indices["displacement"] < len(units)
        else ""
    )
    normalized_load_unit = common._normalized_text(load_unit)
    normalized_displacement_unit = common._normalized_text(displacement_unit)
    if mode in ROTATION_MODES and "nmm" in normalized_load_unit:
        loads = [value / 1000.0 for value in loads]
        load_unit = "Nm"
    if mode in ROTATION_MODES and normalized_displacement_unit == "rad":
        displacements = [math.degrees(value) for value in displacements]
        displacement_unit = "°"
    if mode in TRANSLATION_MODES and normalized_displacement_unit == "m":
        displacements = [value * 1000.0 for value in displacements]
        displacement_unit = "mm"
    start = time_values[0]
    return RomZwickData(
        times_s=tuple(value - start for value in time_values),
        loads=tuple(loads),
        displacements=tuple(displacements),
        vertical_translation=tuple(displacements),
        load_column=headers[indices["load"]],
        displacement_column=headers[indices["displacement"]],
        load_unit=load_unit,
        displacement_unit=displacement_unit,
        source=path,
    )


def _matrix_vector(
    matrix: common.Matrix, vector: Sequence[float]
) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[row][column] * float(vector[column]) for column in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def _baseline_count(times_s: Sequence[float], seconds: float) -> int:
    count = sum(value <= seconds for value in times_s)
    return min(len(times_s), max(5, count))


def _marker_rotation(
    data: RomTrackingData,
    reference_id: int,
    marker_id: int,
    axis: Sequence[float],
    baseline_count: int,
    sign: float,
) -> tuple[float, ...]:
    normalized_axis = common._normalize_axis(axis)
    rotations = [
        common.multiply(common.transpose(reference.rotation), marker.rotation)
        for reference, marker in zip(data.poses[reference_id], data.poses[marker_id])
    ]
    baseline = common.average_rotation(rotations[:baseline_count])
    result: list[float] = []
    previous: common.Quaternion | None = None
    for rotation in rotations:
        delta = common.multiply(rotation, common.transpose(baseline))
        quaternion = common.matrix_to_quaternion(delta)
        if previous is not None and sum(a * b for a, b in zip(previous, quaternion)) < 0:
            quaternion = tuple(-value for value in quaternion)  # type: ignore[assignment]
        previous = quaternion
        rotvec = common.quaternion_to_rotvec(quaternion)
        result.append(
            math.degrees(sum(a * b for a, b in zip(rotvec, normalized_axis))) * sign
        )
    return tuple(result)


def _positions_in_reference(
    data: RomTrackingData, reference_id: int, marker_id: int
) -> tuple[tuple[float, float, float], ...]:
    result = []
    for reference, marker in zip(data.poses[reference_id], data.poses[marker_id]):
        difference = tuple(
            marker.translation_mm[index] - reference.translation_mm[index]
            for index in range(3)
        )
        result.append(_matrix_vector(common.transpose(reference.rotation), difference))
    return tuple(result)


def _marker_translation(
    data: RomTrackingData,
    reference_id: int,
    marker_id: int,
    axis: Sequence[float],
    baseline_count: int,
    sign: float,
) -> tuple[float, ...]:
    normalized_axis = common._normalize_axis(axis)
    raw = [
        sum(a * b for a, b in zip(position, normalized_axis)) * sign
        for position in _positions_in_reference(data, reference_id, marker_id)
    ]
    baseline = statistics.mean(raw[:baseline_count])
    return tuple(value - baseline for value in raw)


def calculate_motion(
    data: RomTrackingData,
    mode: str,
    axis: Sequence[float],
    markers: dict,
    baseline_seconds: float,
    sign: float,
) -> tuple[dict[str, tuple[float, ...]], int]:
    reference_id = int(markers["reference"])
    baseline_count = _baseline_count(data.times_s, baseline_seconds)
    named_markers = {
        "clamp_up": int(markers["clamp_up"]),
        "clamp_down": int(markers["clamp_down"]),
        "vertebra_cranial": int(markers["vertebra_cranial"]),
        "vertebra_caudal": int(markers["vertebra_caudal"]),
    }
    if mode in ROTATION_MODES:
        curves = {
            name: _marker_rotation(
                data, reference_id, marker_id, axis, baseline_count, sign
            )
            for name, marker_id in named_markers.items()
        }
        segmental, _ = common.projected_segment_angle(
            data,  # type: ignore[arg-type]
            reference_id,
            named_markers["vertebra_cranial"],
            named_markers["vertebra_caudal"],
            axis,
            baseline_seconds,
            sign,
        )
        clamp_segmental, _ = common.projected_segment_angle(
            data,  # type: ignore[arg-type]
            reference_id,
            named_markers["clamp_up"],
            named_markers["clamp_down"],
            axis,
            baseline_seconds,
            sign,
        )
    else:
        curves = {
            name: _marker_translation(
                data, reference_id, marker_id, axis, baseline_count, sign
            )
            for name, marker_id in named_markers.items()
        }
        segmental = tuple(
            cranial - caudal
            for cranial, caudal in zip(
                curves["vertebra_cranial"], curves["vertebra_caudal"]
            )
        )
        baseline = statistics.mean(segmental[:baseline_count])
        segmental = tuple(value - baseline for value in segmental)
        clamp_segmental = tuple(
            clamp_up - clamp_down
            for clamp_up, clamp_down in zip(
                curves["clamp_up"], curves["clamp_down"]
            )
        )
        clamp_baseline = statistics.mean(clamp_segmental[:baseline_count])
        clamp_segmental = tuple(
            value - clamp_baseline for value in clamp_segmental
        )
    curves["segmental"] = tuple(segmental)
    curves["clamp_segmental"] = tuple(clamp_segmental)
    return curves, baseline_count


def _has_token(path: Path, token: str) -> bool:
    return bool(re.search(rf"(^|_){re.escape(token.lower())}($|_)", _normalized_stem(path)))


def find_zero_and_rom(
    zwick: RomZwickData,
    mode: str,
    is_active_rom: bool,
    rotation_offset: float,
) -> RomMetrics:
    loads = zwick.loads
    displacements = zwick.displacements
    maximum_index = max(range(len(loads)), key=loads.__getitem__)
    minimum_index = min(range(len(loads)), key=loads.__getitem__)
    offset = rotation_offset if mode in ROTATION_MODES else 0.0
    stored = tuple(value + offset for value in displacements)
    zero_indices: tuple[int, int] | None = None
    zero_point: float | None = None
    up_rom: float | None = None
    down_rom: float | None = None
    if is_active_rom:
        first_range = range(0, maximum_index + 1)
        first_zero = min(first_range, key=lambda index: abs(loads[index]))
        lower, upper = sorted((maximum_index, minimum_index))
        second_range = range(lower, upper + 1)
        second_zero = min(second_range, key=lambda index: abs(loads[index]))
        zero_indices = (first_zero, second_zero)
        zero_point = (stored[first_zero] + stored[second_zero]) / 2.0
        up_rom = stored[maximum_index]
        down_rom = stored[minimum_index]
    return RomMetrics(
        rom_range=max(displacements) - min(displacements),
        positive_load=max(loads),
        negative_load=min(loads),
        up_rom=up_rom,
        down_rom=down_rom,
        zero_point=zero_point,
        zero_indices=zero_indices,
        stored_offset=offset,
    )


def _span(values: Sequence[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(value)]
    return max(finite) - min(finite) if finite else 0.0


def _edge_extrema(values: Sequence[float], edge_fraction: float) -> list[str]:
    if len(values) < 5 or _span(values) <= 1e-12:
        return []
    edge_count = max(1, math.ceil((len(values) - 1) * edge_fraction))
    maximum_index = max(range(len(values)), key=values.__getitem__)
    minimum_index = min(range(len(values)), key=values.__getitem__)
    result = []
    if maximum_index <= edge_count:
        result.append("positive peak at the beginning")
    elif maximum_index >= len(values) - 1 - edge_count:
        result.append("positive peak at the end")
    if minimum_index <= edge_count:
        result.append("negative peak at the beginning")
    elif minimum_index >= len(values) - 1 - edge_count:
        result.append("negative peak at the end")
    return result


def _peak_data_gaps(
    times_s: Sequence[float],
    curves: dict[str, tuple[float, ...]],
    settings: dict,
    strict: bool = False,
) -> list[tuple[float, float, int, tuple[str, ...]]]:
    """Find dropped camera samples adjacent to the measured ROM extrema."""
    positive_steps = [
        right - left
        for left, right in zip(times_s, times_s[1:])
        if right > left
    ]
    if not positive_steps:
        return []
    median_step = statistics.median(positive_steps)
    if median_step <= 0.0:
        return []
    gap_factor = float(settings.get("peak_gap_factor", 1.75))
    peak_zone = min(0.49, max(0.01, float(settings.get("peak_zone_fraction", 0.20))))
    minimum_impact = float(settings.get("peak_gap_min_rom_fraction", 0.03))
    minimum_missing = int(settings.get("peak_gap_min_missing_samples", 8))
    labels = {
        "segmental": "vertebra",
        "clamp_segmental": "Clamp",
    }
    result = []
    for index, (left_time, right_time) in enumerate(zip(times_s, times_s[1:])):
        step = right_time - left_time
        if step <= median_step * gap_factor:
            continue
        affected = []
        maximum_impact = 0.0
        for curve_name, display_name in labels.items():
            values = curves.get(curve_name, ())
            if index + 1 >= len(values):
                continue
            finite = [float(value) for value in values if math.isfinite(value)]
            if not finite:
                continue
            minimum, maximum = min(finite), max(finite)
            span = maximum - minimum
            if span <= 1e-12:
                continue
            left_value = float(values[index])
            right_value = float(values[index + 1])
            maximum_impact = max(
                maximum_impact, abs(right_value - left_value) / span
            )
            low_limit = minimum + span * peak_zone
            high_limit = maximum - span * peak_zone
            near_low = min(left_value, right_value) <= low_limit
            near_high = max(left_value, right_value) >= high_limit
            if near_low:
                affected.append(f"negative {display_name} peak")
            if near_high:
                affected.append(f"positive {display_name} peak")
        estimated_missing = max(1, round(step / median_step) - 1)
        if affected and (
            strict
            or maximum_impact >= minimum_impact
            or estimated_missing >= minimum_missing
        ):
            result.append(
                (left_time, right_time, estimated_missing, tuple(dict.fromkeys(affected)))
            )
    return result


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(value))
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * min(1.0, max(0.0, fraction))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _peak_noise_findings(
    times_s: Sequence[float],
    curves: dict[str, tuple[float, ...]],
    settings: dict,
) -> list[tuple[str, str, float, float, float, float, float, float]]:
    """Detect high-frequency spikes localized around positive or negative ROM peaks."""
    labels = {"segmental": "vertebra", "clamp_segmental": "clamp"}
    zone_fraction = min(
        0.49,
        max(0.02, float(settings.get("peak_noise_zone_fraction", 0.25))),
    )
    ratio_limit = float(settings.get("peak_noise_ratio_critical", 3.0))
    rom_fraction_limit = float(
        settings.get("peak_noise_rom_fraction_critical", 0.015)
    )
    result = []
    for curve_name, display_name in labels.items():
        values = curves.get(curve_name, ())
        if len(values) < 9 or len(values) != len(times_s):
            continue
        minimum, maximum = min(values), max(values)
        span = maximum - minimum
        if span <= 1e-12:
            continue
        roughness = [0.0] * len(values)
        for index in range(1, len(values) - 1):
            roughness[index] = abs(
                float(values[index])
                - (float(values[index - 1]) + float(values[index + 1])) / 2.0
            )
        low_limit = minimum + span * zone_fraction
        high_limit = maximum - span * zone_fraction
        zones = {
            "negative": {index for index, value in enumerate(values) if value <= low_limit},
            "positive": {index for index, value in enumerate(values) if value >= high_limit},
        }
        all_peak_indices = set().union(*zones.values())
        rest_noise = _percentile(
            [
                roughness[index]
                for index in range(1, len(values) - 1)
                if index not in all_peak_indices
            ],
            0.90,
        )
        noise_floor = max(rest_noise, span * 1e-5, 1e-12)
        for polarity, zone in zones.items():
            usable = sorted(index for index in zone if 0 < index < len(values) - 1)
            if len(usable) < 5:
                continue
            peak_noise = _percentile([roughness[index] for index in usable], 0.90)
            noise_ratio = peak_noise / noise_floor
            rom_fraction = peak_noise / span
            if noise_ratio >= ratio_limit and rom_fraction >= rom_fraction_limit:
                result.append(
                    (
                        display_name,
                        polarity,
                        float(times_s[usable[0]]),
                        float(times_s[usable[-1]]),
                        peak_noise,
                        rest_noise,
                        noise_ratio,
                        rom_fraction,
                    )
                )
    return result


def evaluate_quality(
    tracking: RomTrackingData,
    curves: dict[str, tuple[float, ...]],
    mode: str,
    zwick: RomZwickData | None,
    is_active_rom: bool,
    config: dict,
    zwick_expected: bool = True,
) -> tuple[list[QualityFinding], dict[str, float | None]]:
    settings = dict(config.get("quality", {}))
    findings: list[QualityFinding] = []
    vertebra_rom = _span(curves["segmental"])
    clamp_rom = _span(curves["clamp_segmental"])
    ratio = vertebra_rom / clamp_rom if clamp_rom > 1e-12 else None
    minimum_clamp = float(
        settings.get(
            "minimum_clamp_rom_rotation_deg"
            if mode in ROTATION_MODES
            else "minimum_clamp_rom_translation_mm",
            0.5 if mode in ROTATION_MODES else 0.1,
        )
    )
    unit = "°" if mode in ROTATION_MODES else "mm"

    peak_gaps = _peak_data_gaps(
        tracking.times_s,
        curves,
        settings,
        strict=tracking.incomplete_samples > 0,
    )
    if peak_gaps:
        affected = tuple(
            dict.fromkeys(label for _start, _end, _missing, labels in peak_gaps for label in labels)
        )
        missing_total = sum(missing for _start, _end, missing, _labels in peak_gaps)
        intervals = ", ".join(
            f"{start:.3f}–{end:.3f} s" for start, end, _missing, _labels in peak_gaps[:3]
        )
        if len(peak_gaps) > 3:
            intervals += f" and {len(peak_gaps) - 3} more"
        findings.append(
            QualityFinding(
                "critical",
                "peak_data_gap",
                "MISSING DATA AT THE PEAK",
                f"Data gap at {', '.join(affected)} ({intervals}); approximately "
                f"{missing_total} samples missing. ROM peak unreliable – inspect or repeat.",
            )
        )

    noisy_peaks = _peak_noise_findings(tracking.times_s, curves, settings)
    for (
        curve_label,
        polarity,
        start_time,
        end_time,
        peak_noise,
        rest_noise,
        noise_ratio,
        rom_fraction,
    ) in noisy_peaks:
        findings.append(
            QualityFinding(
                "critical",
                "peak_noise",
                "EXCESSIVE NOISE AT THE PEAK",
                f"{polarity.capitalize()} {curve_label} peak at {start_time:.3f}–"
                f"{end_time:.3f} s: roughness {noise_ratio:.1f}× the rest of the curve "
                f"({rom_fraction * 100:.1f}% of ROM). Marker unstable/slipped; peak "
                "unreliable – inspect or repeat.",
            )
        )

    if clamp_rom < minimum_clamp:
        findings.append(
            QualityFinding(
                "critical",
                "clamp_rom_too_small",
                "Clamp motion too small",
                f"Clamp ROM {clamp_rom:.2f} {unit}; minimum {minimum_clamp:.2f} {unit}. The ratio cannot be assessed reliably.",
            )
        )
    elif ratio is not None:
        over_limit = float(settings.get("vertebra_over_clamp_critical_ratio", 1.10))
        under_warning = float(settings.get("vertebra_under_clamp_warning_ratio", 0.70))
        under_critical = float(settings.get("vertebra_under_clamp_critical_ratio", 0.50))
        if ratio > over_limit:
            findings.append(
                QualityFinding(
                    "critical",
                    "vertebra_exceeds_clamp",
                    "Vertebral ROM exceeds clamp ROM",
                    f"Vertebra {vertebra_rom:.2f} {unit}, clamp {clamp_rom:.2f} {unit} ({ratio * 100:.0f}%). Biomechanically implausible; inspect markers, coordinate system and tracking.",
                )
            )
        elif ratio < under_critical:
            findings.append(
                QualityFinding(
                    "critical",
                    "severe_clamp_slip",
                    "Severe motion loss at the vertebra",
                    f"The vertebra reaches only {ratio * 100:.0f}% of clamp ROM. The clamp is probably holding poorly or slipping on the vertebra.",
                )
            )
        elif ratio < under_warning:
            findings.append(
                QualityFinding(
                    "warning",
                    "possible_clamp_slip",
                    "Possible poor clamp fixation",
                    f"The vertebra reaches only {ratio * 100:.0f}% of clamp ROM. Inspect clamp seating and relative motion.",
                )
            )

    if is_active_rom:
        edge_fraction = float(settings.get("edge_peak_fraction", 0.03))
        camera_edges = _edge_extrema(curves["segmental"], edge_fraction)
        if camera_edges:
            findings.append(
                QualityFinding(
                    "critical",
                    "camera_peak_at_edge",
                    "Atracsys peak may be truncated",
                    ", ".join(camera_edges) + ". Inspect recording timing and the complete loading cycle.",
                )
            )
        if zwick is not None:
            zwick_edges = _edge_extrema(zwick.loads, edge_fraction)
            if zwick_edges:
                findings.append(
                    QualityFinding(
                        "critical",
                        "zwick_peak_at_edge",
                        "testXpert peak may be truncated",
                        ", ".join(zwick_edges) + ". The load trace may not contain the complete peak.",
                    )
                )

    if zwick is None and zwick_expected:
        findings.append(
            QualityFinding(
                "critical",
                "missing_zwick_data",
                "testXpert data missing",
                "The ROM analysis is incomplete without full load-displacement or moment-angle data.",
            )
        )
    elif zwick is None:
        findings.append(
            QualityFinding(
                "warning",
                "camera_only_analysis",
                "Retrospective Atracsys-only analysis",
                "No .tra file was used; machine ROM and the moment/force curve cannot be compared.",
            )
        )

    if zwick is not None and tracking.times_s and zwick.times_s:
        camera_duration = tracking.times_s[-1]
        zwick_duration = zwick.times_s[-1]
        denominator = max(camera_duration, zwick_duration, 1e-9)
        difference_fraction = abs(camera_duration - zwick_duration) / denominator
        warning_limit = float(settings.get("duration_warning_fraction", 0.05))
        critical_limit = float(settings.get("duration_critical_fraction", 0.10))
        if difference_fraction > warning_limit:
            severity = "critical" if difference_fraction > critical_limit else "warning"
            findings.append(
                QualityFinding(
                    severity,
                    "duration_mismatch",
                    "Recording durations do not match",
                    f"Atracsys {camera_duration:.2f} s, testXpert {zwick_duration:.2f} s; difference {difference_fraction * 100:.1f}%. Inspect start/stop timing and missing peaks.",
                )
            )

    sample_total = len(tracking.times_s) + tracking.incomplete_samples
    incomplete_fraction = (
        tracking.incomplete_samples / sample_total if sample_total else 0.0
    )
    incomplete_warning = float(settings.get("incomplete_warning_fraction", 0.01))
    incomplete_critical = float(settings.get("incomplete_critical_fraction", 0.05))
    if incomplete_fraction > incomplete_warning:
        severity = "critical" if incomplete_fraction > incomplete_critical else "warning"
        findings.append(
            QualityFinding(
                severity,
                "incomplete_marker_samples",
                "Incomplete marker samples",
                f"{tracking.incomplete_samples} samples ({incomplete_fraction * 100:.1f}%) do not contain all five markers.",
            )
        )

    positive_steps = [
        right - left
        for left, right in zip(tracking.times_s, tracking.times_s[1:])
        if right > left
    ]
    if positive_steps:
        median_step = statistics.median(positive_steps)
        maximum_step = max(positive_steps)
        gap_limit = max(
            float(settings.get("time_gap_absolute_s", 0.10)),
            median_step * float(settings.get("time_gap_factor", 5.0)),
        )
        if maximum_step > gap_limit:
            findings.append(
                QualityFinding(
                    "warning",
                    "tracking_time_gap",
                    "Time gap in Atracsys data",
                    f"Largest gap {maximum_step:.3f} s with a typical interval of {median_step:.3f} s. Peaks may be incomplete.",
                )
            )

    registration_values = [
        pose.registration_error_mm
        for poses in tracking.poses.values()
        for pose in poses
        if math.isfinite(pose.registration_error_mm)
    ]
    maximum_registration = max(registration_values) if registration_values else None
    if maximum_registration is not None:
        registration_warning = float(settings.get("registration_warning_mm", 1.0))
        registration_critical = float(settings.get("registration_critical_mm", 2.0))
        if maximum_registration > registration_warning:
            severity = "critical" if maximum_registration > registration_critical else "warning"
            findings.append(
                QualityFinding(
                    severity,
                    "registration_error",
                    "High marker registration error",
                    f"Maximum registration error {maximum_registration:.2f} mm. Inspect marker recognition and visibility.",
                )
            )

    metrics: dict[str, float | None] = {
        "vertebra_rom": vertebra_rom,
        "clamp_rom": clamp_rom,
        "vertebra_to_clamp_ratio": ratio,
        "incomplete_sample_fraction": incomplete_fraction,
        "peak_data_gap_count": float(len(peak_gaps)),
        "peak_noise_finding_count": float(len(noisy_peaks)),
        "peak_noise_max_ratio": max(
            (finding[6] for finding in noisy_peaks), default=None
        ),
        "peak_noise_max_rom_fraction": max(
            (finding[7] for finding in noisy_peaks), default=None
        ),
        "maximum_registration_error_mm": maximum_registration,
    }
    return findings, metrics


def write_testxpert_rom_files(
    zwick: RomZwickData,
    metrics: RomMetrics,
    mode: str,
    config: dict,
) -> list[Path]:
    if metrics.up_rom is None or metrics.down_rom is None or metrics.zero_point is None:
        return []
    configured = str(config.get("legacy_output_directory", "")).strip()
    output = common._expand_path(configured) if configured else zwick.source.parent
    output.mkdir(parents=True, exist_ok=True)
    pressure = "wp" if _has_token(zwick.source, "wp") else "p"
    values = {
        output / f"{mode}_{pressure}_UpROM.txt": metrics.up_rom,
        output / f"{mode}_{pressure}_DnROM.txt": metrics.down_rom,
        output / f"{mode}_{pressure}_ZeroPoint.txt": metrics.zero_point,
    }
    if mode == "AR" and zwick.vertical_translation:
        values[output / f"{pressure}_Vert_TransSP.txt"] = statistics.mean(
            zwick.vertical_translation
        )
    for path, value in values.items():
        path.write_text(f"{value:.12g}", encoding="ascii")
    return list(values)


def _svg_multi_plot(
    series: Sequence[tuple[str, Sequence[float], Sequence[float], str]],
    box: tuple[float, float, float, float],
    x_label: str,
    y_label: str,
    title: str,
    horizontal_lines: Sequence[float] = (),
    alert_level: str | None = None,
    vertical_bands: Sequence[tuple[float, float, str]] = (),
    split_gap_factor: float | None = None,
) -> str:
    left, top, width, height = box
    x_values = [value for _, xs, _, _ in series for value in xs if math.isfinite(value)]
    y_values = [value for _, _, ys, _ in series for value in ys if math.isfinite(value)]
    y_values.extend(float(value) for value in horizontal_lines)
    x_min, x_max, x_ticks = common._nice_ticks(x_values)
    y_min, y_max, y_ticks = common._nice_ticks(y_values)

    def sx(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * width

    def sy(value: float) -> float:
        return top + height - (value - y_min) / (y_max - y_min) * height

    parts = [
        f'<text x="{left:.1f}" y="{top - 30:.1f}" class="panel-title">{html.escape(title)}</text>',
        f'<rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" class="plot-bg"/>',
    ]
    for tick in x_ticks:
        x = sx(tick)
        parts.append(f'<line x1="{x:.2f}" y1="{top:.2f}" x2="{x:.2f}" y2="{top + height:.2f}" class="grid" vector-effect="non-scaling-stroke"/>')
        parts.append(f'<text x="{x:.2f}" y="{top + height + 24:.2f}" class="tick" text-anchor="middle">{html.escape(common._format_tick(tick))}</text>')
    for tick in y_ticks:
        y = sy(tick)
        parts.append(f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{left + width:.2f}" y2="{y:.2f}" class="grid" vector-effect="non-scaling-stroke"/>')
        parts.append(f'<text x="{left - 10:.2f}" y="{y + 4:.2f}" class="tick" text-anchor="end">{html.escape(common._format_tick(tick))}</text>')
    for value in horizontal_lines:
        y = sy(float(value))
        parts.append(f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{left + width:.2f}" y2="{y:.2f}" class="limit" vector-effect="non-scaling-stroke"/>')
    for start, end, label in vertical_bands:
        clipped_start = max(x_min, min(x_max, float(start)))
        clipped_end = max(x_min, min(x_max, float(end)))
        band_x = sx(min(clipped_start, clipped_end))
        band_width = max(4.0, abs(sx(clipped_end) - sx(clipped_start)))
        parts.append(
            f'<rect x="{band_x:.2f}" y="{top:.2f}" width="{band_width:.2f}" '
            f'height="{height:.2f}" class="peak-gap"><title>{html.escape(label)}</title></rect>'
        )
    frame_class = "frame" if alert_level is None else f"frame alert-{alert_level}"
    parts.append(f'<rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" class="{frame_class}" vector-effect="non-scaling-stroke"/>')

    for label, xs, ys, color in series:
        points = [
            (index, x, y)
            for index, (x, y) in enumerate(zip(xs, ys))
            if math.isfinite(x) and math.isfinite(y)
        ]
        gap_after = set()
        if split_gap_factor is not None:
            steps = [
                xs[index + 1] - xs[index]
                for index in range(len(xs) - 1)
                if math.isfinite(xs[index])
                and math.isfinite(xs[index + 1])
                and xs[index + 1] > xs[index]
            ]
            if steps:
                typical_step = statistics.median(steps)
                gap_after = {
                    index
                    for index in range(len(xs) - 1)
                    if math.isfinite(xs[index])
                    and math.isfinite(xs[index + 1])
                    and xs[index + 1] - xs[index] > typical_step * split_gap_factor
                }
        if len(points) > 2500:
            stride = math.ceil(len(points) / 2500)
            selected = set(range(0, len(points), stride))
            selected.add(len(points) - 1)
            original_to_point = {original_index: point_index for point_index, (original_index, _x, _y) in enumerate(points)}
            for gap_index in gap_after:
                if gap_index in original_to_point:
                    selected.add(original_to_point[gap_index])
                if gap_index + 1 in original_to_point:
                    selected.add(original_to_point[gap_index + 1])
            points = [points[index] for index in sorted(selected)]
        if len(points) >= 2:
            first_index, first_x, first_y = points[0]
            commands = [f"M {sx(first_x):.2f} {sy(first_y):.2f}"]
            previous_index = first_index
            for original_index, x, y in points[1:]:
                command = "M" if any(
                    gap_index in gap_after
                    for gap_index in range(previous_index, original_index)
                ) else "L"
                commands.append(f"{command} {sx(x):.2f} {sy(y):.2f}")
                previous_index = original_index
            dash = ' stroke-dasharray="10 6"' if label.lower().startswith("clamp") else ""
            parts.append(f'<path class="data-line" d="{" ".join(commands)}" fill="none" stroke="{color}" stroke-width="1.15" vector-effect="non-scaling-stroke"{dash}/>')
    legend_x = left + 10
    legend_y = top + 20
    for index, (label, _, _, color) in enumerate(series):
        row = index // 2
        column = index % 2
        x = legend_x + column * (width / 2)
        y = legend_y + row * 22
        dash = ' stroke-dasharray="8 4"' if label.lower().startswith("clamp") else ""
        parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + 24:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="3" vector-effect="non-scaling-stroke"{dash}/>')
        parts.append(f'<text x="{x + 31:.1f}" y="{y + 4:.1f}" class="legend">{html.escape(label)}</text>')
    parts.append(f'<text x="{left + width / 2:.1f}" y="{top + height + 55:.1f}" class="axis-label" text-anchor="middle">{html.escape(x_label)}</text>')
    parts.append(f'<text x="{left - 64:.1f}" y="{top + height / 2:.1f}" class="axis-label" text-anchor="middle" transform="rotate(-90 {left - 64:.1f} {top + height / 2:.1f})">{html.escape(y_label)}</text>')
    return "\n".join(parts)


def render_figure(
    output: Path,
    source_name: str,
    data: RomTrackingData,
    curves: dict[str, tuple[float, ...]],
    mode: str,
    axis: Sequence[float],
    markers: dict,
    zwick: RomZwickData | None,
    metrics: RomMetrics | None,
    quality_findings: Sequence[QualityFinding],
    quality_metrics: dict[str, float | None],
    zwick_expected: bool,
    config: dict,
    warnings: Sequence[str],
) -> None:
    unit = "°" if mode in ROTATION_MODES else "mm"
    colors = {
        "clamp_up": "#0057b8",
        "clamp_down": "#56b4e9",
        "vertebra_cranial": "#d55e00",
        "vertebra_caudal": "#e69f00",
        "segmental": "#d55e00",
    }
    labels = {
        "clamp_up": f"Upper clamp ({markers['clamp_up']})",
        "clamp_down": f"Lower clamp ({markers['clamp_down']})",
        "vertebra_cranial": f"Cranial vertebra ({markers['vertebra_cranial']})",
        "vertebra_caudal": f"Caudal vertebra ({markers['vertebra_caudal']})",
    }
    quality_settings = dict(config.get("quality", {}))
    peak_gap_bands = [
        (start, end, "CRITICAL: measurement data missing at the peak")
        for start, end, _missing, _labels in _peak_data_gaps(
            data.times_s,
            curves,
            quality_settings,
            strict=data.incomplete_samples > 0,
        )
    ]
    peak_noise_bands = [
        (start, end, f"CRITICAL: excessive noise at the {polarity} {curve_label} peak")
        for curve_label, polarity, start, end, _peak, _rest, _ratio, _fraction
        in _peak_noise_findings(data.times_s, curves, quality_settings)
    ]
    camera_quality_bands = peak_gap_bands + peak_noise_bands
    first_series = [
        (labels[name], data.times_s, curves[name], colors[name])
        for name in ("clamp_up", "vertebra_cranial", "clamp_down", "vertebra_caudal")
    ]
    segment_rom = max(curves["segmental"]) - min(curves["segmental"])
    clamp_rom = max(curves["clamp_segmental"]) - min(curves["clamp_segmental"])
    ratio = quality_metrics.get("vertebra_to_clamp_ratio")
    severity_rank = {"warning": 1, "critical": 2}
    quality = quality_summary(quality_findings)
    overall_severity = quality["status"]
    first_alert = max(
        (
            finding.severity
            for finding in quality_findings
            if finding.code
            in {"peak_data_gap", "peak_noise", "incomplete_marker_samples", "tracking_time_gap", "registration_error"}
        ),
        key=lambda value: severity_rank.get(value, 0),
        default=None,
    )
    comparison_findings = {
        "clamp_rom_too_small",
        "vertebra_exceeds_clamp",
        "severe_clamp_slip",
        "possible_clamp_slip",
        "camera_peak_at_edge",
        "peak_data_gap",
        "peak_noise",
    }
    comparison_alert = max(
        (
            finding.severity
            for finding in quality_findings
            if finding.code in comparison_findings
        ),
        key=lambda value: severity_rank.get(value, 0),
        default=None,
    )
    zwick_alert = max(
        (
            finding.severity
            for finding in quality_findings
            if finding.code in {"zwick_peak_at_edge", "duration_mismatch"}
        ),
        key=lambda value: severity_rank.get(value, 0),
        default=None,
    )
    first_plot = _svg_multi_plot(
        first_series,
        (95.0, 210.0, 500.0, 390.0),
        "Time [s]",
        f"Motion [{unit}]",
        "Individual motions in reference coordinate system 299",
        alert_level=first_alert,
        vertical_bands=camera_quality_bands,
        split_gap_factor=float(quality_settings.get("peak_gap_factor", 1.75)),
    )
    ratio_text = "n/a" if ratio is None else f"{ratio * 100:.0f} %"
    second_plot = _svg_multi_plot(
        [
            ("Clamp 301 relative to 302", data.times_s, curves["clamp_segmental"], "#0057b8"),
            ("Vertebra 311 relative to 314", data.times_s, curves["segmental"], colors["segmental"]),
        ],
        (710.0, 210.0, 500.0, 390.0),
        "Time [s]",
        f"Segment motion [{unit}]",
        f"Clamp {clamp_rom:.2f} {unit} / Vertebra {segment_rom:.2f} {unit} ({ratio_text})",
        alert_level=comparison_alert,
        vertical_bands=camera_quality_bands,
        split_gap_factor=float(quality_settings.get("peak_gap_factor", 1.75)),
    )
    if zwick is not None and metrics is not None:
        limits = [float(value) for value in config.get("load_limits", {}).get(mode, [])]
        third_plot = _svg_multi_plot(
            [("testXpert", zwick.displacements, zwick.loads, "#d95f02")],
            (1325.0, 210.0, 500.0, 390.0),
            f"{zwick.displacement_column} [{unit}]",
            f"{zwick.load_column} [{zwick.load_unit}]",
            f"testXpert – ROM {metrics.rom_range:.2f} {unit}",
            limits,
            zwick_alert,
        )
        zwick_note = f"Source: {zwick.source.name}"
    else:
        missing_class = "alert-critical" if zwick_expected else "alert-warning"
        missing_text = (
            "No matching .tra file"
            if zwick_expected
            else "Retrospective without .tra – Atracsys only"
        )
        third_plot = (
            '<text x="1325" y="180" class="panel-title">testXpert</text>'
            '<rect x="1325" y="210" width="500" height="390" class="plot-bg"/>'
            f'<rect x="1325" y="210" width="500" height="390" class="frame {missing_class}"/>'
            f'<text x="1575" y="405" class="missing" text-anchor="middle">{missing_text}</text>'
        )
        zwick_note = ""
    axis_text = ", ".join(f"{float(value):g}" for value in axis)
    warning_text = " | ".join(warnings[-2:]) if warnings else ""
    if overall_severity == "critical":
        banner_class = "quality-critical"
        banner_text = "QUALITÄT: SCHLECHT / BAD – Messung und Hinweise vor der Auswertung prüfen"
    elif overall_severity == "warning":
        banner_class = "quality-warning"
        banner_text = "QUALITÄT: MITTEL / MEDIUM – Klemmung, Messkurve und Hinweise prüfen"
    else:
        banner_class = "quality-ok"
        banner_text = "QUALITÄT: GUT / GOOD – kein automatischer Qualitätsmangel erkannt"
    quality_rows = []
    for row_index, finding in enumerate(quality_findings[:5]):
        row_class = "finding-critical" if finding.severity == "critical" else "finding-warning"
        quality_rows.append(
            f'<text x="95" y="{722 + row_index * 27}" class="{row_class}">'
            f'{html.escape(finding.severity.upper())}: {html.escape(finding.title)} – {html.escape(finding.detail)}</text>'
        )
    if len(quality_findings) > 5:
        quality_rows.append(
            f'<text x="95" y="857" class="finding-warning">+ {len(quality_findings) - 5} additional findings in the JSON report</text>'
        )
    quality_details = "\n".join(quality_rows)
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg id="rom-figure" xmlns="http://www.w3.org/2000/svg" width="1920" height="880" viewBox="0 0 1920 880">
  <style>
    .background {{ fill:#fff; }} .plot-bg {{ fill:#f8fafc; }}
    .frame {{ fill:none;stroke:#334155;stroke-width:1.3; }}
    .frame.alert-warning {{ stroke:#f59e0b;stroke-width:4; }}
    .frame.alert-critical {{ stroke:#dc2626;stroke-width:5; }}
    .grid {{ stroke:#cbd5e1;stroke-width:1;opacity:.65; }}
    .limit {{ stroke:#2563eb;stroke-width:1.5;stroke-dasharray:8 6; }}
    .peak-gap {{ fill:#ef4444;fill-opacity:.20;stroke:#dc2626;stroke-width:2;stroke-dasharray:6 4;vector-effect:non-scaling-stroke; }}
    .title {{ font:700 28px Arial,sans-serif;fill:#0f172a; }}
    .subtitle {{ font:16px Arial,sans-serif;fill:#475569; }}
    .panel-title {{ font:700 17px Arial,sans-serif;fill:#0f172a; }}
    .axis-label {{ font:15px Arial,sans-serif;fill:#1e293b; }}
    .tick,.legend {{ font:12px Arial,sans-serif;fill:#475569; }}
    .note {{ font:14px Arial,sans-serif;fill:#64748b; }}
    .warning {{ font:13px Arial,sans-serif;fill:#9a3412; }}
    .missing {{ font:700 18px Arial,sans-serif;fill:#64748b; }}
    .quality-ok {{ fill:#dcfce7;stroke:#16a34a;stroke-width:2; }}
    .quality-warning {{ fill:#fef3c7;stroke:#f59e0b;stroke-width:3; }}
    .quality-critical {{ fill:#fee2e2;stroke:#dc2626;stroke-width:4; }}
    .quality-text {{ font:700 19px Arial,sans-serif;fill:#0f172a; }}
    .finding-warning {{ font:700 14px Arial,sans-serif;fill:#92400e; }}
    .finding-critical {{ font:700 14px Arial,sans-serif;fill:#b91c1c; }}
    .zoom-help {{ font:13px Arial,sans-serif;fill:#475569; }}
  </style>
  <rect width="1920" height="880" class="background"/>
  <text x="70" y="48" class="title">5-Marker ROM Analysis – {html.escape(mode)} – {html.escape(source_name)}</text>
  <text x="70" y="80" class="subtitle">Ref {markers['reference']}; Clamp {markers['clamp_up']}/{markers['clamp_down']}; Vertebra {markers['vertebra_cranial']}/{markers['vertebra_caudal']}; axis [{html.escape(axis_text)}]</text>
  <rect x="70" y="101" width="1780" height="55" rx="8" class="{banner_class}"/>
  <text x="95" y="136" class="quality-text">{html.escape(banner_text)}</text>
  {first_plot}
  {second_plot}
  {third_plot}
  <text x="95" y="676" class="note">{html.escape(zwick_note)}</text>
  <text x="95" y="699" class="warning">{html.escape(warning_text)}</text>
  {quality_details}
  <text x="1825" y="857" class="zoom-help" text-anchor="end">Zoom: mouse wheel · Pan: drag · Reset: double-click</text>
  <script type="application/ecmascript"><![CDATA[
    (function () {{
      const svg = document.getElementById("rom-figure");
      const original = {{x: 0, y: 0, width: 1920, height: 880}};
      let view = {{...original}};
      let drag = null;
      function apply() {{
        svg.setAttribute("viewBox", `${{view.x}} ${{view.y}} ${{view.width}} ${{view.height}}`);
      }}
      function pointer(event) {{
        const rect = svg.getBoundingClientRect();
        return {{
          x: view.x + (event.clientX - rect.left) / rect.width * view.width,
          y: view.y + (event.clientY - rect.top) / rect.height * view.height
        }};
      }}
      svg.style.cursor = "grab";
      svg.style.touchAction = "none";
      svg.addEventListener("wheel", function (event) {{
        event.preventDefault();
        const anchor = pointer(event);
        const factor = event.deltaY < 0 ? 0.80 : 1.25;
        const nextWidth = Math.max(180, Math.min(7680, view.width * factor));
        const nextHeight = nextWidth * original.height / original.width;
        const scaleX = nextWidth / view.width;
        const scaleY = nextHeight / view.height;
        view.x = anchor.x - (anchor.x - view.x) * scaleX;
        view.y = anchor.y - (anchor.y - view.y) * scaleY;
        view.width = nextWidth;
        view.height = nextHeight;
        apply();
      }}, {{passive: false}});
      svg.addEventListener("pointerdown", function (event) {{
        if (event.button !== 0) return;
        drag = {{clientX: event.clientX, clientY: event.clientY, viewX: view.x, viewY: view.y}};
        svg.setPointerCapture(event.pointerId);
        svg.style.cursor = "grabbing";
      }});
      svg.addEventListener("pointermove", function (event) {{
        if (!drag) return;
        const rect = svg.getBoundingClientRect();
        view.x = drag.viewX - (event.clientX - drag.clientX) / rect.width * view.width;
        view.y = drag.viewY - (event.clientY - drag.clientY) / rect.height * view.height;
        apply();
      }});
      function stopDrag() {{ drag = null; svg.style.cursor = "grab"; }}
      svg.addEventListener("pointerup", stopDrag);
      svg.addEventListener("pointercancel", stopDrag);
      svg.addEventListener("dblclick", function (event) {{
        event.preventDefault();
        view = {{...original}};
        apply();
      }});
    }})();
  ]]></script>
</svg>
'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    embedded = document.split("?>", 1)[-1].lstrip()
    output.with_suffix(".html").write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>5-Marker ROM Analysis</title>"
        "<style>html,body{margin:0;background:#fff}svg{display:block;width:100%;height:auto}</style>"
        "</head><body>" + embedded + "</body></html>",
        encoding="utf-8",
    )


def write_motion_csv(
    output: Path,
    times_s: Sequence[float],
    curves: dict[str, tuple[float, ...]],
    mode: str,
) -> None:
    unit = "deg" if mode in ROTATION_MODES else "mm"
    names = (
        "clamp_up",
        "clamp_down",
        "vertebra_cranial",
        "vertebra_caudal",
        "clamp_segmental",
        "segmental",
    )
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", *[f"{name}_{mode}_{unit}" for name in names]])
        writer.writerows(
            zip(times_s, *(curves[name] for name in names))
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config, config_path = load_config(args.config)
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Atracsys file was not found: {input_path}")
    markers = dict(config["markers"])
    marker_ids = tuple(int(markers[name]) for name in (
        "reference", "clamp_up", "clamp_down", "vertebra_cranial", "vertebra_caudal"
    ))
    registry = common._registry_values(str(config.get("registry_path", "Motion Tracking")))
    if args.ohne_zwick:
        zwick_path = None
        warnings = ["Retrospective camera analysis without a testXpert .tra file."]
    else:
        zwick_path, warnings = _resolve_zwick_path(
            input_path, args.zwick, config, registry
        )
    mode, mode_source = determine_movement(
        args.bewegung, input_path, zwick_path, registry, config
    )
    sign = float(config.get("sign", {}).get(mode, 1.0))
    baseline_seconds = float(config.get("baseline_seconds", 1.0))
    tracking = load_tracking(input_path, marker_ids)
    axis = movement_axis(config, mode, tracking.source_format)
    curves, baseline_count = calculate_motion(
        tracking, mode, axis, markers, baseline_seconds, sign
    )

    zwick: RomZwickData | None = None
    metrics: RomMetrics | None = None
    is_active_rom = zwick_path is not None and _has_token(zwick_path, "a")
    if zwick_path is not None:
        try:
            zwick = load_zwick(zwick_path, mode, config)
            metrics = find_zero_and_rom(
                zwick,
                mode,
                is_active_rom,
                float(config.get("rotation_legacy_offset_deg", 180000.0)),
            )
        except (OSError, ValueError) as error:
            warnings.append(f"The testXpert file could not be evaluated: {error}")

    written_rom_files: list[Path] = []
    if (
        zwick is not None
        and metrics is not None
        and bool(config.get("write_testxpert_rom_files", True))
        and not args.no_rom_files
    ):
        written_rom_files = write_testxpert_rom_files(zwick, metrics, mode, config)

    quality_findings, quality_metrics = evaluate_quality(
        tracking,
        curves,
        mode,
        zwick,
        is_active_rom,
        config,
        not args.ohne_zwick,
    )

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else input_path.parent / f"{input_path.stem}_ROM_auswertung"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path = output_dir / f"{input_path.stem}_{mode}_ROM_figure.svg"
    html_path = figure_path.with_suffix(".html")
    motion_path = output_dir / f"{input_path.stem}_{mode}_ROM_bewegung.csv"
    report_path = output_dir / f"{input_path.stem}_{mode}_ROM_report.json"
    render_figure(
        figure_path,
        input_path.name,
        tracking,
        curves,
        mode,
        axis,
        markers,
        zwick,
        metrics,
        quality_findings,
        quality_metrics,
        not args.ohne_zwick,
        config,
        warnings,
    )
    write_motion_csv(motion_path, tracking.times_s, curves, mode)

    report = {
        "status": "ok",
        "analysis": "five_marker_rom",
        "input": str(input_path),
        "input_format": tracking.source_format,
        "config": str(config_path.resolve()),
        "markers": markers,
        "movement": mode,
        "movement_source": mode_source,
        "camera_only": bool(args.ohne_zwick),
        "projection_axis_in_reference_kos": list(common._normalize_axis(axis)),
        "sign": sign,
        "complete_samples": len(tracking.times_s),
        "incomplete_samples": tracking.incomplete_samples,
        "duration_s": tracking.times_s[-1],
        "baseline_seconds": baseline_seconds,
        "baseline_samples": baseline_count,
        "segmental_rom": max(curves["segmental"]) - min(curves["segmental"]),
        "clamp_segmental_rom": max(curves["clamp_segmental"]) - min(curves["clamp_segmental"]),
        "segmental_unit": "deg" if mode in ROTATION_MODES else "mm",
        "zwick": None if zwick is None else {
            "source": str(zwick.source.resolve()),
            "samples": len(zwick.loads),
            "load_column": zwick.load_column,
            "displacement_column": zwick.displacement_column,
            "load_unit": zwick.load_unit,
            "displacement_unit": zwick.displacement_unit,
        },
        "rom_metrics": None if metrics is None else {
            "range": metrics.rom_range,
            "positive_load": metrics.positive_load,
            "negative_load": metrics.negative_load,
            "up_rom": metrics.up_rom,
            "down_rom": metrics.down_rom,
            "zero_point": metrics.zero_point,
            "stored_rotation_offset": metrics.stored_offset,
        },
        "testxpert_rom_files": [str(path.resolve()) for path in written_rom_files],
        "quality": {
            **quality_summary(quality_findings),
            "metrics": quality_metrics,
            "findings": [
                {
                    "severity": finding.severity,
                    "code": finding.code,
                    "title": finding.title,
                    "detail": finding.detail,
                }
                for finding in quality_findings
            ],
        },
        "warnings": warnings,
        "outputs": {
            "figure_svg": str(figure_path.resolve()),
            "figure_html": str(html_path.resolve()),
            "motion_csv": str(motion_path.resolve()),
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
            common.open_figure_in_browser(html_path)
        except OSError as error:
            print(f"Warning: the ROM figure could not be opened: {error}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ROM ANALYSIS ERROR: {error}", file=sys.stderr)
        raise
