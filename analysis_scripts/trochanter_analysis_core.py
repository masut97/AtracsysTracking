"""Relative Bewegungsanalyse fuer das konfigurierte Specimen.

Marker:
    299 = externe Referenz
    321 = refixierter Trochanter
    322 = Femur

Die zentrale Groesse ist die volle starre Pose des Trochantermarkers relativ
zum Femurmarker. Dadurch werden Translation und Rotation des Femurs gemeinsam
beruecksichtigt; eine reine Subtraktion der Kamerakoordinaten ist nicht noetig.

Das Skript benoetigt NumPy, pandas und Pillow. Es erzeugt:
    - summary.csv: kompakte Kennwerte je Versuch
    - cycle_metrics.csv: Kennwerte fuer jeden erkannten Belastungszyklus
    - timeseries_1s.csv: auf eine Sekunde reduzierte Zeitverlaeufe
    - analysis_data.json: reduzierte Daten fuer die Ergebnisvisualisierung
    - plots/*.png: jeder Translations- und Rotationsplot als eigenes Bild
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ModuleNotFoundError as error:
    requirements = Path(__file__).resolve().with_name("requirements-analysis.txt")
    raise SystemExit(
        f"Missing analysis dependency: {error.name}.\n"
        "Install the dependencies into the interpreter used to run this file:\n"
        f'"{sys.executable}" -m pip install -r "{requirements}"'
    ) from error


SPECIMEN = "EXAMPLE_DEFAULT_R"
REFERENCE_ID = 299
TROCHANTER_ID = 321
FEMUR_ID = 322

INPUT_DIR = Path(__file__).resolve().parent / SPECIMEN
OUTPUT_DIR = Path(__file__).resolve().parent / f"{SPECIMEN}_analysis"
PLOTS_DIR = OUTPUT_DIR / "plots"

BASELINE_SECONDS = 1.0
EXPECTED_CYCLE_FREQUENCY_HZ = 2.0
MAX_PROTOCOL_CYCLES = 5000
ACTIVITY_SMOOTHING_CYCLES = 21
MAX_ACTIVITY_GAP_CYCLES = 20
WINDOW_CYCLES = 200
EDGE_EXCLUSION_CYCLES = 100

# Failure declarations belong to a specific specimen; never transfer an old
# specimen's fracture annotation to a newly selected specimen.
def load_failure_annotations():
    path = Path(__file__).with_name("failure_annotations.local.json")
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return {sample: {(key.split(":")[0], int(key.split(":")[1])): value
                     for key, value in items.items()} for sample, items in raw.items()}


DECLARED_FAILURES_BY_SPECIMEN = load_failure_annotations()
DECLARED_FAILURES = DECLARED_FAILURES_BY_SPECIMEN.get(SPECIMEN, {})

FILE_PATTERN = re.compile(
    rf"^{re.escape(SPECIMEN)}_(8er|double|screw)_(150N|300N|450N|RTF)\.csv$",
    re.IGNORECASE,
)


def marker_columns(marker_id: int) -> list[str]:
    columns = [
        f"marker_{marker_id}_visible",
        f"marker_{marker_id}_x_mm",
        f"marker_{marker_id}_y_mm",
        f"marker_{marker_id}_z_mm",
    ]
    columns.extend(
        f"marker_{marker_id}_R{row}{column}"
        for row in range(3)
        for column in range(3)
    )
    return columns


USE_COLUMNS = ["sample_index", "timestamp_s"]
for _marker_id in (REFERENCE_ID, TROCHANTER_ID, FEMUR_ID):
    USE_COLUMNS.extend(marker_columns(_marker_id))


def position(data: pd.DataFrame, marker_id: int) -> np.ndarray:
    return data[
        [f"marker_{marker_id}_{axis}_mm" for axis in ("x", "y", "z")]
    ].to_numpy(dtype=float)


def rotation(data: pd.DataFrame, marker_id: int) -> np.ndarray:
    columns = [
        f"marker_{marker_id}_R{row}{column}"
        for row in range(3)
        for column in range(3)
    ]
    return data[columns].to_numpy(dtype=float).reshape(-1, 3, 3)


def project_to_rotation(matrix: np.ndarray) -> np.ndarray:
    """Projiziert eine gemittelte 3x3-Matrix auf die naechste Rotation."""
    left, _, right = np.linalg.svd(matrix)
    result = left @ right
    if np.linalg.det(result) < 0:
        left[:, -1] *= -1
        result = left @ right
    return result


def mean_rotation(rotations: np.ndarray) -> np.ndarray:
    return project_to_rotation(np.nanmean(rotations, axis=0))


def rotation_angle_deg(rotations: np.ndarray) -> np.ndarray:
    """Rotationswinkel fuer eine einzelne Matrix oder einen Matrixstapel."""
    traces = np.trace(rotations, axis1=-2, axis2=-1)
    cosine = np.clip((traces - 1.0) / 2.0, -1.0, 1.0)
    return np.degrees(np.arccos(cosine))


def pairwise_rotation_excursion_deg(rotations: np.ndarray) -> float:
    """Groesste relative Rotationsdifferenz innerhalb eines Zyklus."""
    relative = np.einsum("aij,bik->abjk", rotations, rotations)
    return float(np.nanmax(rotation_angle_deg(relative)))


def longest_activity_segment(
    cycle_numbers: np.ndarray,
    max_gap: int = MAX_ACTIVITY_GAP_CYCLES,
) -> tuple[int, int] | None:
    if len(cycle_numbers) == 0:
        return None

    cuts = np.where(np.diff(cycle_numbers) > max_gap)[0]
    starts = np.r_[0, cuts + 1]
    ends = np.r_[cuts, len(cycle_numbers) - 1]
    segment_index = int(
        np.argmax(cycle_numbers[ends] - cycle_numbers[starts])
    )
    return (
        int(cycle_numbers[starts[segment_index]]),
        int(cycle_numbers[ends[segment_index]]),
    )


def parse_condition(path: Path) -> tuple[str, int | None, str]:
    match = FILE_PATTERN.match(path.name)
    if match is None:
        raise ValueError(f"Unerwarteter Dateiname: {path.name}")
    cerclage = match.group(1).lower()
    load_text = match.group(2).upper()
    force = None if load_text == "RTF" else int(load_text[:-1])
    return cerclage, force, load_text


def find_input_files() -> list[Path]:
    result = []
    for path in sorted(INPUT_DIR.glob("*.csv")):
        if "ignore" in path.name.lower():
            continue
        if FILE_PATTERN.match(path.name):
            result.append(path)
    return result


def calculate_cycle_metrics(
    time_s: np.ndarray,
    valid: np.ndarray,
    relative_position: np.ndarray,
    relative_rotation: np.ndarray,
    baseline_position: np.ndarray,
    baseline_rotation: np.ndarray,
    frequency_hz: float,
) -> pd.DataFrame:
    cycle_number = np.floor((time_s - time_s[0]) * frequency_hz).astype(int)
    records: list[dict[str, float | int]] = []

    for current_cycle in np.unique(cycle_number):
        indices = np.where((cycle_number == current_cycle) & valid)[0]
        if len(indices) < 5:
            continue

        cycle_positions = relative_position[indices]
        cycle_rotations = relative_rotation[indices]
        pairwise_distance = np.linalg.norm(
            cycle_positions[:, None, :] - cycle_positions[None, :, :],
            axis=2,
        )
        center = np.nanmedian(cycle_positions, axis=0)
        center_rotation = mean_rotation(cycle_rotations)

        records.append(
            {
                "cycle": int(current_cycle),
                "time_s": float(np.nanmedian(time_s[indices])),
                "center_x_mm": float(center[0]),
                "center_y_mm": float(center[1]),
                "center_z_mm": float(center[2]),
                "center_displacement_mm": float(
                    np.linalg.norm(center - baseline_position)
                ),
                "micromotion_mm": float(np.nanmax(pairwise_distance)),
                "center_rotation_deg": float(
                    rotation_angle_deg(
                        baseline_rotation.T @ center_rotation
                    )
                ),
                "rotation_micromotion_deg": (
                    pairwise_rotation_excursion_deg(cycle_rotations)
                ),
                "valid_samples": int(len(indices)),
            }
        )

    return pd.DataFrame.from_records(records)


def detect_active_cycles(cycles: pd.DataFrame) -> tuple[int, int, float]:
    """Erkennt den laengsten zusammenhaengenden zyklischen Belastungsblock."""
    if cycles.empty:
        raise ValueError("Keine auswertbaren Zyklen: Aufnahme zu kurz oder Marker-Daten fehlen.")
    tail = cycles["micromotion_mm"].iloc[-100:]
    tail_median = float(tail.median())
    tail_mad = float(np.nanmedian(np.abs(tail - tail_median)))

    # Der Dateischluss ist bei den vorliegenden Messungen entlastet. Die
    # Schwelle liegt oberhalb dieses Rauschens, bleibt aber niedrig genug fuer
    # die kleinen 150-N-Bewegungen.
    threshold = max(
        0.08,
        tail_median + max(0.025, 2.0 * tail_mad),
    )
    smoothed = cycles["micromotion_mm"].rolling(
        ACTIVITY_SMOOTHING_CYCLES,
        center=True,
        min_periods=8,
    ).median()
    active_numbers = cycles.loc[smoothed > threshold, "cycle"].to_numpy()
    segment = longest_activity_segment(active_numbers)

    if segment is None:
        raise ValueError("Keine aktiven Belastungszyklen oberhalb der Rauschschwelle erkannt.")
    return segment[0], segment[1], threshold


def select_window(
    active: pd.DataFrame,
    *,
    early: bool,
) -> pd.DataFrame:
    count = len(active)
    if count <= 2 * EDGE_EXCLUSION_CYCLES:
        width = max(20, count // 3)
        return active.iloc[:width] if early else active.iloc[-width:]

    width = min(WINDOW_CYCLES, max(50, (count - 2 * EDGE_EXCLUSION_CYCLES) // 3))
    if early:
        return active.iloc[
            EDGE_EXCLUSION_CYCLES : EDGE_EXCLUSION_CYCLES + width
        ]
    return active.iloc[
        -(EDGE_EXCLUSION_CYCLES + width) : -EDGE_EXCLUSION_CYCLES
    ]


def vector_center(frame: pd.DataFrame) -> np.ndarray:
    return frame[["center_x_mm", "center_y_mm", "center_z_mm"]].median().to_numpy()


def one_second_timeseries(
    time_s: np.ndarray,
    valid: np.ndarray,
    delta_position: np.ndarray,
    displacement_mm: np.ndarray,
    rotation_deg: np.ndarray,
) -> pd.DataFrame:
    second = np.floor(time_s).astype(int)
    frame = pd.DataFrame(
        {
            "time_s": second[valid],
            "dx_mm": delta_position[valid, 0],
            "dy_mm": delta_position[valid, 1],
            "dz_mm": delta_position[valid, 2],
            "displacement_mm": displacement_mm[valid],
            "rotation_deg": rotation_deg[valid],
        }
    )
    return frame.groupby("time_s", as_index=False).median(numeric_only=True)


def analyze_cyclic_file(path: Path) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    cerclage, force, load_text = parse_condition(path)
    data = pd.read_csv(path, usecols=USE_COLUMNS)
    time_s = data["timestamp_s"].to_numpy(dtype=float)
    if len(time_s) < 2 or not np.isfinite(time_s).all() or np.any(np.diff(time_s) <= 0):
        raise ValueError("Mindestens zwei streng aufsteigende, endliche Zeitstempel erforderlich.")

    positions = {
        marker_id: position(data, marker_id)
        for marker_id in (REFERENCE_ID, TROCHANTER_ID, FEMUR_ID)
    }
    rotations = {
        marker_id: rotation(data, marker_id)
        for marker_id in (REFERENCE_ID, TROCHANTER_ID, FEMUR_ID)
    }

    valid = np.ones(len(data), dtype=bool)
    for marker_id in (REFERENCE_ID, TROCHANTER_ID, FEMUR_ID):
        valid &= data[f"marker_{marker_id}_visible"].eq(1).to_numpy()
        valid &= np.isfinite(positions[marker_id]).all(axis=1)
        valid &= np.isfinite(rotations[marker_id]).all(axis=(1, 2))

    # Referenzkoordinaten: T_RM = inv(T_CR) @ T_CM.
    position_ref_femur = np.einsum(
        "nji,nj->ni",
        rotations[REFERENCE_ID],
        positions[FEMUR_ID] - positions[REFERENCE_ID],
    )
    position_ref_trochanter = np.einsum(
        "nji,nj->ni",
        rotations[REFERENCE_ID],
        positions[TROCHANTER_ID] - positions[REFERENCE_ID],
    )
    rotation_ref_femur = np.einsum(
        "nji,njk->nik",
        rotations[REFERENCE_ID],
        rotations[FEMUR_ID],
    )

    # Volle Relativpose T_FT = inv(T_RF) @ T_RT.
    relative_position = np.einsum(
        "nji,nj->ni",
        rotation_ref_femur,
        position_ref_trochanter - position_ref_femur,
    )
    relative_rotation = np.einsum(
        "nji,njk->nik",
        rotations[FEMUR_ID],
        rotations[TROCHANTER_ID],
    )

    if not np.any(valid):
        raise ValueError("Keine gemeinsamen gültigen Messpunkte der Marker 299, 321 und 322.")
    first_valid_time = float(time_s[valid][0])
    baseline_mask = valid & (time_s <= first_valid_time + BASELINE_SECONDS)
    baseline_position = np.nanmedian(relative_position[baseline_mask], axis=0)
    baseline_rotation = mean_rotation(relative_rotation[baseline_mask])

    delta_position = relative_position - baseline_position
    displacement_mm = np.linalg.norm(delta_position, axis=1)
    rotation_change = np.einsum(
        "ij,njk->nik",
        baseline_rotation.T,
        relative_rotation,
    )
    rotation_deg = rotation_angle_deg(rotation_change)
    valid_indices = np.flatnonzero(valid)
    first_valid_index = int(valid_indices[0])
    last_valid_index = int(valid_indices[-1])
    first_to_last_translation_mm = float(
        np.linalg.norm(
            relative_position[last_valid_index]
            - relative_position[first_valid_index]
        )
    )
    first_to_last_rotation_deg = float(
        rotation_angle_deg(
            relative_rotation[first_valid_index].T
            @ relative_rotation[last_valid_index]
        )
    )

    sample_rate_hz = (len(data) - 1) / (time_s[-1] - time_s[0])
    cycles = calculate_cycle_metrics(
        time_s,
        valid,
        relative_position,
        relative_rotation,
        baseline_position,
        baseline_rotation,
        EXPECTED_CYCLE_FREQUENCY_HZ,
    )
    active_start, active_end, activity_threshold = detect_active_cycles(cycles)
    declared_failure = DECLARED_FAILURES.get((cerclage, force))
    cycle_limit = (
        int(declared_failure["failure_cycle"])
        if declared_failure
        else MAX_PROTOCOL_CYCLES
    )
    active_end = min(active_end, active_start + cycle_limit - 1)
    active = cycles[
        cycles["cycle"].between(active_start, active_end)
    ].copy()
    active["active_cycle"] = np.arange(1, len(active) + 1)

    early = select_window(active, early=True)
    late = select_window(active, early=False)
    early_center = vector_center(early)
    late_center = vector_center(late)

    post_start = float(active["time_s"].iloc[-1] + 10.0)
    post_end = post_start + 30.0
    post_mask = valid & (time_s >= post_start) & (time_s <= post_end)
    post_test_displacement = (
        float(
            np.linalg.norm(
                np.nanmedian(relative_position[post_mask], axis=0)
                - baseline_position
            )
        )
        if np.count_nonzero(post_mask) >= 20
        else math.nan
    )

    early_motion = float(early["micromotion_mm"].median())
    late_motion = float(late["micromotion_mm"].median())
    change_percent = (
        100.0 * (late_motion - early_motion) / early_motion
        if early_motion > 0
        else math.nan
    )

    summary: dict[str, object] = {
        "specimen": SPECIMEN,
        "cerclage": cerclage,
        "force_N": force,
        "condition": load_text,
        "file": path.name,
        "rows": int(len(data)),
        "duration_s": float(time_s[-1] - time_s[0]),
        "sample_rate_hz": float(sample_rate_hz),
        "all_markers_valid_percent": float(100.0 * np.mean(valid)),
        "cycle_frequency_hz": EXPECTED_CYCLE_FREQUENCY_HZ,
        "activity_threshold_mm": activity_threshold,
        "active_start_s": float(active["time_s"].iloc[0]),
        "active_end_s": float(active["time_s"].iloc[-1]),
        "active_cycles": int(len(active)),
        "completed_approximately_5000_cycles": bool(
            len(active) == MAX_PROTOCOL_CYCLES
        ),
        "failure_mode": DECLARED_FAILURES.get(
            (cerclage, force), {}
        ).get("failure_mode", ""),
        "failure_cycle": DECLARED_FAILURES.get(
            (cerclage, force), {}
        ).get("failure_cycle", math.nan),
        "micromotion_early_mm": early_motion,
        "micromotion_late_mm": late_motion,
        "micromotion_median_mm": float(active["micromotion_mm"].median()),
        "micromotion_change_percent": change_percent,
        "migration_during_test_mm": float(
            np.linalg.norm(late_center - early_center)
        ),
        "loaded_displacement_early_mm": float(
            np.linalg.norm(early_center - baseline_position)
        ),
        "loaded_displacement_late_mm": float(
            np.linalg.norm(late_center - baseline_position)
        ),
        "post_test_displacement_mm": post_test_displacement,
        "translation_first_to_last_recording_mm": (
            first_to_last_translation_mm
        ),
        "rotation_first_to_last_recording_deg": (
            first_to_last_rotation_deg
        ),
        "rotation_micromotion_early_deg": float(
            early["rotation_micromotion_deg"].median()
        ),
        "rotation_micromotion_late_deg": float(
            late["rotation_micromotion_deg"].median()
        ),
        "rotation_change_late_deg": float(
            late["center_rotation_deg"].median()
        ),
    }

    cycles.insert(0, "condition", load_text)
    cycles.insert(0, "force_N", force)
    cycles.insert(0, "cerclage", cerclage)
    cycles.insert(0, "specimen", SPECIMEN)
    cycles["is_active"] = cycles["cycle"].between(active_start, active_end)
    cycles["active_cycle"] = np.where(
        cycles["is_active"],
        cycles["cycle"] - active_start + 1,
        np.nan,
    )
    cycles["event"] = ""
    if declared_failure:
        cycles.loc[
            cycles["active_cycle"] == declared_failure["failure_cycle"],
            "event",
        ] = declared_failure["failure_mode"]

    time_series = one_second_timeseries(
        time_s,
        valid,
        delta_position,
        displacement_mm,
        rotation_deg,
    )
    time_series.insert(0, "condition", load_text)
    time_series.insert(0, "force_N", force)
    time_series.insert(0, "cerclage", cerclage)
    time_series.insert(0, "specimen", SPECIMEN)

    return summary, cycles, time_series


def analyze_rtf_file(path: Path) -> tuple[dict[str, object], pd.DataFrame]:
    cerclage, force, load_text = parse_condition(path)
    data = pd.read_csv(path, usecols=USE_COLUMNS)
    time_s = data["timestamp_s"].to_numpy(dtype=float)
    positions = {
        marker_id: position(data, marker_id)
        for marker_id in (REFERENCE_ID, TROCHANTER_ID, FEMUR_ID)
    }
    rotations = {
        marker_id: rotation(data, marker_id)
        for marker_id in (REFERENCE_ID, TROCHANTER_ID, FEMUR_ID)
    }

    valid = np.ones(len(data), dtype=bool)
    for marker_id in (REFERENCE_ID, TROCHANTER_ID, FEMUR_ID):
        valid &= data[f"marker_{marker_id}_visible"].eq(1).to_numpy()
        valid &= np.isfinite(positions[marker_id]).all(axis=1)
        valid &= np.isfinite(rotations[marker_id]).all(axis=(1, 2))

    relative_position = np.einsum(
        "nji,nj->ni",
        rotations[FEMUR_ID],
        positions[TROCHANTER_ID] - positions[FEMUR_ID],
    )
    relative_rotation = np.einsum(
        "nji,njk->nik",
        rotations[FEMUR_ID],
        rotations[TROCHANTER_ID],
    )

    first_valid_time = float(time_s[valid][0])
    baseline_mask = valid & (time_s <= first_valid_time + BASELINE_SECONDS)
    baseline_position = np.nanmedian(relative_position[baseline_mask], axis=0)
    baseline_rotation = mean_rotation(relative_rotation[baseline_mask])
    delta_position = relative_position - baseline_position
    displacement_mm = np.linalg.norm(delta_position, axis=1)
    rotation_deg = rotation_angle_deg(
        np.einsum("ij,njk->nik", baseline_rotation.T, relative_rotation)
    )

    summary: dict[str, object] = {
        "specimen": SPECIMEN,
        "cerclage": cerclage,
        "force_N": force,
        "condition": load_text,
        "file": path.name,
        "rows": int(len(data)),
        "duration_s": float(time_s[-1] - time_s[0]),
        "sample_rate_hz": float((len(data) - 1) / (time_s[-1] - time_s[0])),
        "all_markers_valid_percent": float(100.0 * np.mean(valid)),
        "max_displacement_mm": float(np.nanmax(displacement_mm[valid])),
        "final_displacement_mm": float(
            np.nanmedian(displacement_mm[valid][-20:])
        ),
        "max_rotation_deg": float(np.nanmax(rotation_deg[valid])),
    }

    time_series = one_second_timeseries(
        time_s,
        valid,
        delta_position,
        displacement_mm,
        rotation_deg,
    )
    time_series.insert(0, "condition", load_text)
    time_series.insert(0, "force_N", force)
    time_series.insert(0, "cerclage", cerclage)
    time_series.insert(0, "specimen", SPECIMEN)
    return summary, time_series


def rounded_records(frame: pd.DataFrame, decimals: int = 4) -> list[dict[str, object]]:
    rounded = frame.copy()
    numeric = rounded.select_dtypes(include=[np.number]).columns
    rounded[numeric] = rounded[numeric].round(decimals)
    return rounded.replace({np.nan: None}).to_dict(orient="records")


PLOT_BACKGROUND = "#f8fafc"
PLOT_FOREGROUND = "#172033"
PLOT_MUTED = "#596579"
PLOT_GRID = "#dbe2ea"
PLOT_FAILURE = "#c2413b"
PLOT_COLORS = {
    "8er": "#3366e8",
    "double": "#e07a15",
    "screw": "#009b77",
}
PLOT_LABELS = {"8er": "Figure-8", "double": "Double", "screw": "Screw"}


def pillow_modules():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise RuntimeError(
            "Zum Speichern der PNG-Dateien wird Pillow benoetigt "
            "(Installation: python -m pip install pillow)."
        ) from error
    return Image, ImageDraw, ImageFont


def plot_font(image_font, size: int, bold: bool = False):
    candidates = (
        ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf"]
        if bold
        else ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return image_font.truetype(candidate, size=size)
    return image_font.load_default()


def nice_scale(value: float) -> tuple[float, float]:
    value = max(float(value), 1e-6)
    raw_step = value / 4.0
    magnitude = 10.0 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    if normalized <= 1.0:
        step = 1.0 * magnitude
    elif normalized <= 2.0:
        step = 2.0 * magnitude
    elif normalized <= 2.5:
        step = 2.5 * magnitude
    elif normalized <= 5.0:
        step = 5.0 * magnitude
    else:
        step = 10.0 * magnitude
    return math.ceil(value / step) * step, step


def binned_active_cycles(
    cycle_frame: pd.DataFrame,
    metric: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    active = cycle_frame[cycle_frame["is_active"]].copy()
    active["plot_bin"] = (
        np.floor((active["active_cycle"] - 1) / 100) * 100 + 1
    )
    binned = (
        active.groupby(
            ["force_N", "cerclage", "plot_bin"],
            as_index=False,
        )
        .agg(
            value=(metric, "median"),
            cycles_in_bin=(metric, "size"),
        )
    )
    return active, binned[binned["cycles_in_bin"] >= 50]


def save_cycle_metric_plot_png(
    cycle_frame: pd.DataFrame,
    force: int,
    metric: str,
    title: str,
    y_label: str,
    unit: str,
    destination: Path,
) -> None:
    """Speichert einen einzelnen Translations- oder Rotations-Zyklusplot."""
    Image, ImageDraw, ImageFont = pillow_modules()
    width, height = 2100, 1120
    left, right, top, bottom = 170, 1900, 250, 900
    plot_width = right - left
    plot_height = bottom - top
    image = Image.new("RGB", (width, height), PLOT_BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = plot_font(ImageFont, 52, bold=True)
    subtitle_font = plot_font(ImageFont, 27)
    axis_font = plot_font(ImageFont, 27)
    label_font = plot_font(ImageFont, 25, bold=True)
    note_font = plot_font(ImageFont, 23)

    active = cycle_frame[cycle_frame["is_active"]].copy()
    force_data = active[active["force_N"] == force].sort_values(
        ["cerclage", "active_cycle"]
    )
    y_max, y_step = nice_scale(float(force_data[metric].max()) * 1.05)

    draw.text((left, 45), title, fill=PLOT_FOREGROUND, font=title_font)
    draw.text(
        (left, 120),
        (
            f"Trochanter relative to femur · {SPECIMEN} · "
            "every detected loading cycle"
        ),
        fill=PLOT_MUTED,
        font=subtitle_font,
    )
    draw.text((left, 215), y_label, fill=PLOT_FOREGROUND, font=axis_font)

    y_tick = 0.0
    while y_tick <= y_max + y_step / 10:
        y = bottom - (y_tick / y_max) * plot_height
        draw.line([(left, y), (right, y)], fill=PLOT_GRID, width=2)
        tick_text = f"{y_tick:.2f}" if y_step < 0.1 else f"{y_tick:.1f}"
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (left - 18 - (bbox[2] - bbox[0]), y - 14),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )
        y_tick += y_step

    for cycle_tick in (0, 1000, 2000, 3000, 4000, 5000):
        x = left + (cycle_tick / 5000.0) * plot_width
        draw.line([(x, bottom), (x, bottom + 10)], fill=PLOT_FOREGROUND, width=2)
        tick_text = f"{cycle_tick:,}".replace(",", "'")
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (x - (bbox[2] - bbox[0]) / 2, bottom + 15),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )

    draw.line(
        [(left, top), (left, bottom), (right, bottom)],
        fill=PLOT_FOREGROUND,
        width=3,
    )

    legend_x = left
    for cerclage in ("8er", "double", "screw"):
        series = force_data[force_data["cerclage"] == cerclage]
        if series.empty:
            continue

        points = [
            (
                left
                + (min(float(row.active_cycle), 5000.0) / 5000.0)
                * plot_width,
                bottom
                - (float(getattr(row, metric)) / y_max) * plot_height,
            )
            for row in series.itertuples()
        ]

        if len(points) >= 2:
            draw.line(
                points,
                fill=PLOT_COLORS[cerclage],
                width=3,
                joint="curve",
            )
        draw.ellipse(
            (
                points[-1][0] - 6,
                points[-1][1] - 6,
                points[-1][0] + 6,
                points[-1][1] + 6,
            ),
            fill=PLOT_COLORS[cerclage],
            outline=PLOT_BACKGROUND,
            width=2,
        )
        active_count = int(len(series))
        declared_failure = DECLARED_FAILURES.get((cerclage, force))
        if declared_failure:
            legend_text = (
                f"{PLOT_LABELS[cerclage]} · fracture at cycle "
                f"{declared_failure['failure_cycle']}"
            )
        elif active_count < MAX_PROTOCOL_CYCLES:
            legend_text = (
                f"{PLOT_LABELS[cerclage]} · "
                f"{active_count:,} cycles".replace(",", "'")
            )
        else:
            legend_text = PLOT_LABELS[cerclage]
        draw.line(
            [(legend_x, 185), (legend_x + 45, 185)],
            fill=PLOT_COLORS[cerclage],
            width=6,
        )
        draw.text(
            (legend_x + 58, 169),
            legend_text,
            fill=PLOT_FOREGROUND,
            font=label_font,
        )
        legend_x += 360

    screw_failure = DECLARED_FAILURES.get(("screw", force))
    has_screw_data = bool((force_data["cerclage"] == "screw").any())
    if screw_failure and has_screw_data:
        failure_cycle = screw_failure["failure_cycle"]
        failure_x = left + (failure_cycle / 5000.0) * plot_width
        draw.line(
            [(failure_x, top), (failure_x, bottom)],
            fill=PLOT_FAILURE,
            width=4,
        )
        draw.multiline_text(
            (failure_x + 16, top + 18),
            f"Trochanter fracture\napprox. cycle {failure_cycle}",
            fill=PLOT_FAILURE,
            font=label_font,
            spacing=5,
        )
    if force == 450 and not (force_data["cerclage"] == "double").any():
        draw.text(
            (1400, 169),
            "Double · no data file",
            fill=PLOT_MUTED,
            font=label_font,
        )

    x_label = "Loading cycle"
    bbox = draw.textbbox((0, 0), x_label, font=axis_font)
    draw.text(
        (left + (plot_width - (bbox[2] - bbox[0])) / 2, bottom + 65),
        x_label,
        fill=PLOT_FOREGROUND,
        font=axis_font,
    )
    draw.text(
        (left, height - 60),
        (
            "No smoothing or aggregation: every detected cycle is shown"
            f" · Unit: {unit}"
            + (
                f" · Screw: fracture at cycle {screw_failure['failure_cycle']}"
                if screw_failure and has_screw_data
                else ""
            )
        ),
        fill=PLOT_MUTED,
        font=note_font,
    )
    image.save(destination, format="PNG", optimize=True)


def save_early_late_plot_png(
    summary_frame: pd.DataFrame,
    early_column: str,
    late_column: str,
    title: str,
    y_label: str,
    unit: str,
    destination: Path,
) -> None:
    """Speichert den Frueh-Spaet-Vergleich als eigenen Plot."""
    Image, ImageDraw, ImageFont = pillow_modules()
    width, height = 1800, 1050
    left, right, top, bottom = 155, 1650, 220, 850
    plot_height = bottom - top
    image = Image.new("RGB", (width, height), PLOT_BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = plot_font(ImageFont, 46, bold=True)
    axis_font = plot_font(ImageFont, 25)
    label_font = plot_font(ImageFont, 23, bold=True)
    note_font = plot_font(ImageFont, 22)

    maximum_value = float(
        summary_frame[[early_column, late_column]].max().max()
    )
    y_max, y_step = nice_scale(maximum_value * 1.12)
    draw.text((left, 48), title, fill=PLOT_FOREGROUND, font=title_font)
    draw.text((left, 145), y_label, fill=PLOT_FOREGROUND, font=axis_font)

    y_tick = 0.0
    while y_tick <= y_max + y_step / 10:
        y = bottom - (y_tick / y_max) * plot_height
        draw.line([(left, y), (right, y)], fill=PLOT_GRID, width=2)
        tick_text = f"{y_tick:.2f}" if y_step < 0.1 else f"{y_tick:.1f}"
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (left - 18 - (bbox[2] - bbox[0]), y - 14),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )
        y_tick += y_step

    draw.line(
        [(left, top), (left, bottom), (right, bottom)],
        fill=PLOT_FOREGROUND,
        width=3,
    )
    group_centers = {150: 400, 300: 900, 450: 1400}
    offsets = {"8er": -115, "double": 0, "screw": 115}

    for force in (150, 300, 450):
        center = group_centers[force]
        draw.text(
            (center - 38, bottom + 80),
            f"{force} N",
            fill=PLOT_FOREGROUND,
            font=axis_font,
        )
        for cerclage in ("8er", "double", "screw"):
            row = summary_frame[
                (summary_frame["force_N"] == force)
                & (summary_frame["cerclage"] == cerclage)
            ]
            x = center + offsets[cerclage]
            draw.text(
                (x - 35, bottom + 25),
                PLOT_LABELS[cerclage],
                fill=PLOT_MUTED,
                font=note_font,
            )
            if row.empty:
                draw.text((x - 5, bottom - 28), "—", fill=PLOT_MUTED, font=axis_font)
                continue

            early_value = float(row[early_column].iloc[0])
            late_value = float(row[late_column].iloc[0])
            y_early = bottom - (early_value / y_max) * plot_height
            y_late = bottom - (late_value / y_max) * plot_height
            draw.line([(x, y_early), (x, y_late)], fill=PLOT_MUTED, width=3)
            draw.ellipse(
                (x - 8, y_early - 8, x + 8, y_early + 8),
                fill=PLOT_BACKGROUND,
                outline=PLOT_FOREGROUND,
                width=3,
            )
            draw.rectangle(
                (x - 8, y_late - 8, x + 8, y_late + 8),
                fill=PLOT_COLORS[cerclage],
                outline=PLOT_BACKGROUND,
                width=2,
            )
            draw.text(
                (x - 32, max(top + 4, y_late - 38)),
                f"{late_value:.2f}",
                fill=PLOT_COLORS[cerclage],
                font=label_font,
            )

    draw.ellipse(
        (right - 260, 115, right - 244, 131),
        fill=PLOT_BACKGROUND,
        outline=PLOT_FOREGROUND,
        width=3,
    )
    draw.text((right - 232, 108), "early", fill=PLOT_MUTED, font=note_font)
    draw.rectangle(
        (right - 135, 115, right - 119, 131),
        fill=PLOT_FOREGROUND,
    )
    draw.text((right - 107, 108), "late", fill=PLOT_MUTED, font=note_font)
    draw.text(
        (left, height - 48),
        f"Open circle: early · filled square: late · Unit: {unit}",
        fill=PLOT_MUTED,
        font=note_font,
    )
    image.save(destination, format="PNG", optimize=True)


def save_grouped_bar_plot_png(
    summary_frame: pd.DataFrame,
    value_column: str,
    title: str,
    y_label: str,
    unit: str,
    destination: Path,
) -> None:
    """Speichert einen gruppierten Cerclagenvergleich als eigenen Plot."""
    Image, ImageDraw, ImageFont = pillow_modules()
    width, height = 1800, 1000
    left, right, top, bottom = 155, 1650, 190, 800
    plot_height = bottom - top
    image = Image.new("RGB", (width, height), PLOT_BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = plot_font(ImageFont, 46, bold=True)
    axis_font = plot_font(ImageFont, 25)
    label_font = plot_font(ImageFont, 23, bold=True)
    note_font = plot_font(ImageFont, 22)
    maximum_value = float(summary_frame[value_column].max())
    y_max, y_step = nice_scale(maximum_value * 1.12)

    draw.text((left, 48), title, fill=PLOT_FOREGROUND, font=title_font)
    draw.text((left, 135), y_label, fill=PLOT_FOREGROUND, font=axis_font)
    y_tick = 0.0
    while y_tick <= y_max + y_step / 10:
        y = bottom - (y_tick / y_max) * plot_height
        draw.line([(left, y), (right, y)], fill=PLOT_GRID, width=2)
        tick_text = f"{y_tick:.2f}" if y_step < 0.1 else f"{y_tick:.1f}"
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (left - 18 - (bbox[2] - bbox[0]), y - 14),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )
        y_tick += y_step
    draw.line(
        [(left, top), (left, bottom), (right, bottom)],
        fill=PLOT_FOREGROUND,
        width=3,
    )

    group_centers = {150: 400, 300: 900, 450: 1400}
    offsets = {"8er": -115, "double": 0, "screw": 115}
    bar_width = 70
    for force in (150, 300, 450):
        center = group_centers[force]
        draw.text(
            (center - 38, bottom + 80),
            f"{force} N",
            fill=PLOT_FOREGROUND,
            font=axis_font,
        )
        for cerclage in ("8er", "double", "screw"):
            row = summary_frame[
                (summary_frame["force_N"] == force)
                & (summary_frame["cerclage"] == cerclage)
            ]
            x = center + offsets[cerclage]
            draw.text(
                (x - 35, bottom + 25),
                PLOT_LABELS[cerclage],
                fill=PLOT_MUTED,
                font=note_font,
            )
            if row.empty:
                continue
            value = float(row[value_column].iloc[0])
            y = bottom - (value / y_max) * plot_height
            draw.rectangle(
                (x - bar_width / 2, y, x + bar_width / 2, bottom),
                fill=PLOT_COLORS[cerclage],
            )
            draw.text(
                (x - 32, max(top + 4, y - 38)),
                f"{value:.2f}",
                fill=PLOT_COLORS[cerclage],
                font=label_font,
            )

    draw.text(
        (left, height - 48),
        (
            f"Unit: {unit} · Only available test conditions are shown"
            + (
                f" · Screw 450 N: fracture at cycle {DECLARED_FAILURES[('screw', 450)]['failure_cycle']}"
                if ("screw", 450) in DECLARED_FAILURES
                and (
                    (summary_frame["cerclage"] == "screw")
                    & (summary_frame["force_N"] == 450)
                ).any()
                else ""
            )
        ),
        fill=PLOT_MUTED,
        font=note_font,
    )
    image.save(destination, format="PNG", optimize=True)


def save_rtf_plot_png(
    time_frame: pd.DataFrame,
    metric: str,
    title: str,
    y_label: str,
    unit: str,
    destination: Path,
) -> None:
    """Speichert einen einzelnen Zeitverlauf fuer die 8er-RTF-Messung."""
    Image, ImageDraw, ImageFont = pillow_modules()
    width, height = 1800, 950
    left, right, top, bottom = 155, 1650, 190, 760
    plot_width = right - left
    plot_height = bottom - top
    image = Image.new("RGB", (width, height), PLOT_BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = plot_font(ImageFont, 46, bold=True)
    axis_font = plot_font(ImageFont, 26)
    note_font = plot_font(ImageFont, 23)

    data = time_frame[time_frame["condition"] == "RTF"].dropna(subset=[metric])
    max_time = float(data["time_s"].max())
    y_max, y_step = nice_scale(float(data[metric].max()) * 1.08)
    draw.text((left, 48), title, fill=PLOT_FOREGROUND, font=title_font)
    draw.text((left, 135), y_label, fill=PLOT_FOREGROUND, font=axis_font)

    y_tick = 0.0
    while y_tick <= y_max + y_step / 10:
        y = bottom - (y_tick / y_max) * plot_height
        draw.line([(left, y), (right, y)], fill=PLOT_GRID, width=2)
        tick_text = f"{y_tick:.1f}"
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (left - 18 - (bbox[2] - bbox[0]), y - 14),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )
        y_tick += y_step

    for time_tick in np.linspace(0, max_time, 5):
        x = left + (time_tick / max_time) * plot_width
        draw.line([(x, bottom), (x, bottom + 10)], fill=PLOT_FOREGROUND, width=2)
        tick_text = f"{time_tick:.0f}"
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (x - (bbox[2] - bbox[0]) / 2, bottom + 15),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )
    draw.line(
        [(left, top), (left, bottom), (right, bottom)],
        fill=PLOT_FOREGROUND,
        width=3,
    )

    points = [
        (
            left + (float(row.time_s) / max_time) * plot_width,
            bottom - (float(getattr(row, metric)) / y_max) * plot_height,
        )
        for row in data.itertuples()
    ]
    draw.line(points, fill=PLOT_COLORS["8er"], width=6, joint="curve")
    draw.text(
        (left + plot_width / 2 - 25, bottom + 60),
        "Time [s]",
        fill=PLOT_FOREGROUND,
        font=axis_font,
    )
    draw.text(
        (left, height - 48),
        f"Figure-8 ramp to failure · Unit: {unit}",
        fill=PLOT_MUTED,
        font=note_font,
    )
    image.save(destination, format="PNG", optimize=True)


def save_screw_450_diagnostic_png(
    cycle_frame: pd.DataFrame,
    destination: Path,
) -> None:
    """Zeigt den Trochanterbruch und den statischen Rest der Screw-450-N-Datei."""
    Image, ImageDraw, ImageFont = pillow_modules()
    width, height = 2100, 1050
    left, right, top, bottom = 170, 1900, 240, 830
    plot_width = right - left
    plot_height = bottom - top
    image = Image.new("RGB", (width, height), PLOT_BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = plot_font(ImageFont, 52, bold=True)
    subtitle_font = plot_font(ImageFont, 27)
    axis_font = plot_font(ImageFont, 27)
    note_font = plot_font(ImageFont, 24)
    annotation_font = plot_font(ImageFont, 26, bold=True)

    data = cycle_frame[
        (cycle_frame["force_N"] == 450)
        & (cycle_frame["cerclage"] == "screw")
    ].sort_values("time_s")
    active = data[data["is_active"]]
    max_time = float(data["time_s"].max())
    y_max, y_step = nice_scale(float(data["micromotion_mm"].max()) * 1.05)
    active_start = float(active["time_s"].iloc[0])
    active_end = float(active["time_s"].iloc[-1])

    draw.text(
        (left, 45),
        f"Screw at 450 N: fracture at cycle {DECLARED_FAILURES.get(('screw', 450), {}).get('failure_cycle', 'unknown')}",
        fill=PLOT_FOREGROUND,
        font=title_font,
    )
    draw.text(
        (left, 120),
        (
            f"{SPECIMEN} · recording duration {max_time:.0f} s · "
            "relative motion for every detected 2 Hz cycle"
        ),
        fill=PLOT_MUTED,
        font=subtitle_font,
    )
    draw.text(
        (left, 195),
        "Relative trochanter–femur translation per cycle [mm]",
        fill=PLOT_FOREGROUND,
        font=axis_font,
    )

    active_x_start = left + (active_start / max_time) * plot_width
    active_x_end = left + (active_end / max_time) * plot_width
    draw.rectangle(
        (active_x_start, top, active_x_end, bottom),
        fill="#e5f5ef",
    )

    y_tick = 0.0
    while y_tick <= y_max + y_step / 10:
        y = bottom - (y_tick / y_max) * plot_height
        draw.line([(left, y), (right, y)], fill=PLOT_GRID, width=2)
        tick_text = f"{y_tick:.1f}"
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (left - 18 - (bbox[2] - bbox[0]), y - 14),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )
        y_tick += y_step

    for time_tick in np.linspace(0, max_time, 6):
        x = left + (time_tick / max_time) * plot_width
        draw.line([(x, bottom), (x, bottom + 10)], fill=PLOT_FOREGROUND, width=2)
        tick_text = f"{time_tick:.0f}"
        bbox = draw.textbbox((0, 0), tick_text, font=axis_font)
        draw.text(
            (x - (bbox[2] - bbox[0]) / 2, bottom + 15),
            tick_text,
            fill=PLOT_MUTED,
            font=axis_font,
        )
    draw.line(
        [(left, top), (left, bottom), (right, bottom)],
        fill=PLOT_FOREGROUND,
        width=3,
    )

    all_points = [
        (
            left + (float(row.time_s) / max_time) * plot_width,
            bottom - (float(row.micromotion_mm) / y_max) * plot_height,
        )
        for row in data.itertuples()
    ]
    active_points = [
        (
            left + (float(row.time_s) / max_time) * plot_width,
            bottom - (float(row.micromotion_mm) / y_max) * plot_height,
        )
        for row in active.itertuples()
    ]
    draw.line(all_points, fill="#9aa6b5", width=2, joint="curve")
    draw.line(
        active_points,
        fill=PLOT_COLORS["screw"],
        width=4,
        joint="curve",
    )
    draw.line(
        [(active_x_end, top), (active_x_end, bottom)],
        fill=PLOT_FAILURE,
        width=4,
    )

    active_duration = active_end - active_start
    after_duration = max_time - active_end
    annotation = (
        f"Cyclic loading: {active_start:.0f}–{active_end:.0f} s\n"
        f"Trochanter fracture at cycle {DECLARED_FAILURES.get(('screw', 450), {}).get('failure_cycle', 'unknown')}\n"
        f"Tracker continued recording for approx. {after_duration:.0f} s"
    )
    draw.multiline_text(
        (780, 290),
        annotation,
        fill=PLOT_FOREGROUND,
        font=annotation_font,
        spacing=10,
    )
    draw.text(
        (left + plot_width / 2 - 35, bottom + 65),
        "Recording time [s]",
        fill=PLOT_FOREGROUND,
        font=axis_font,
    )
    draw.text(
        (left, height - 55),
        (
            "Green: loading until trochanter fracture · "
            "Red: fracture/test stop · Grey: continued tracker recording"
        ),
        fill=PLOT_MUTED,
        font=note_font,
    )
    image.save(destination, format="PNG", optimize=True)


def save_all_plot_pngs(
    summary_frame: pd.DataFrame,
    cycle_frame: pd.DataFrame,
    time_frame: pd.DataFrame,
) -> None:
    """Erzeugt jeden Plot als separate PNG-Datei."""
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    available_forces = sorted(cycle_frame.loc[
        cycle_frame["is_active"], "force_N"
    ].unique())
    for force_value in available_forces:
        force = int(force_value)
        save_cycle_metric_plot_png(
            cycle_frame,
            force,
            "micromotion_mm",
            f"Relative translation per loading cycle · {force} N",
            "Trochanter–femur translation excursion per cycle [mm]",
            "mm",
            PLOTS_DIR / f"translation_zyklen_{force}N.png",
        )
        save_cycle_metric_plot_png(
            cycle_frame,
            force,
            "rotation_micromotion_deg",
            f"Relative rotation per loading cycle · {force} N",
            "Trochanter–femur rotation excursion per cycle [deg]",
            "°",
            PLOTS_DIR / f"rotation_zyklen_{force}N.png",
        )

    save_grouped_bar_plot_png(
        summary_frame,
        "translation_first_to_last_recording_mm",
        "Net displacement: first to last valid position",
        "Relative trochanter–femur translation [mm]",
        "mm",
        PLOTS_DIR / "translation_erste_bis_letzte_position.png",
    )
    save_grouped_bar_plot_png(
        summary_frame,
        "rotation_first_to_last_recording_deg",
        "Net rotation: first to last valid position",
        "Relative trochanter–femur rotation [deg]",
        "°",
        PLOTS_DIR / "rotation_erste_bis_letzte_position.png",
    )
    has_rtf_data = bool((time_frame["condition"] == "RTF").any())
    if has_rtf_data:
        save_rtf_plot_png(
            time_frame,
            "displacement_mm",
            "Ramp to failure · Figure-8 · relative translation",
            "Relative 3D translation [mm]",
            "mm",
            PLOTS_DIR / "rtf_8er_translation.png",
        )
        save_rtf_plot_png(
            time_frame,
            "rotation_deg",
            "Ramp to failure · Figure-8 · relative rotation",
            "Relative rotation [deg]",
            "°",
            PLOTS_DIR / "rtf_8er_rotation.png",
        )
    has_screw_450 = bool((
        (cycle_frame["cerclage"] == "screw")
        & (cycle_frame["force_N"] == 450)
        & cycle_frame["is_active"]
    ).any())
    if has_screw_450 and ("screw", 450) in DECLARED_FAILURES:
        save_screw_450_diagnostic_png(
            cycle_frame,
            PLOTS_DIR / "screw_450N_gesamte_aufzeichnung.png",
        )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    rtf_summaries: list[dict[str, object]] = []
    cycle_frames: list[pd.DataFrame] = []
    time_frames: list[pd.DataFrame] = []

    input_files = find_input_files()
    if not input_files:
        raise SystemExit(f"No matching CSV files in {INPUT_DIR}")

    for path in input_files:
        _, force, _ = parse_condition(path)
        print(f"Analyzing {path.name} ...")
        if force is None:
            summary, time_series = analyze_rtf_file(path)
            rtf_summaries.append(summary)
            time_frames.append(time_series)
        else:
            summary, cycles, time_series = analyze_cyclic_file(path)
            summaries.append(summary)
            cycle_frames.append(cycles)
            time_frames.append(time_series)

    summary_frame = pd.DataFrame(summaries).sort_values(
        ["force_N", "cerclage"]
    )
    cycle_frame = pd.concat(cycle_frames, ignore_index=True)
    time_frame = pd.concat(time_frames, ignore_index=True)
    rtf_frame = pd.DataFrame(rtf_summaries)

    summary_frame.to_csv(OUTPUT_DIR / "summary.csv", index=False)
    cycle_frame.to_csv(OUTPUT_DIR / "cycle_metrics.csv", index=False)
    time_frame.to_csv(OUTPUT_DIR / "timeseries_1s.csv", index=False)
    rtf_frame.to_csv(OUTPUT_DIR / "rtf_summary.csv", index=False)
    save_all_plot_pngs(
        summary_frame,
        cycle_frame,
        time_frame,
    )

    # Fuer die Darstellung werden Zyklen in 50er-Gruppen zusammengefasst.
    active_cycles = cycle_frame[cycle_frame["is_active"]].copy()
    active_cycles["cycle_bin"] = (
        np.floor((active_cycles["active_cycle"] - 1) / 50) * 50 + 1
    )
    cycle_binned = (
        active_cycles.groupby(
            ["cerclage", "force_N", "condition", "cycle_bin"],
            as_index=False,
        )
        .agg(
            micromotion_mm=("micromotion_mm", "median"),
            displacement_mm=("center_displacement_mm", "median"),
            rotation_micromotion_deg=("rotation_micromotion_deg", "median"),
            cycles_in_bin=("micromotion_mm", "size"),
        )
    )
    cycle_binned = cycle_binned[cycle_binned["cycles_in_bin"] >= 25]

    visualization_data = {
        "summary": rounded_records(summary_frame),
        "cycle_binned": rounded_records(cycle_binned),
        "rtf_summary": rounded_records(rtf_frame),
    }
    (OUTPUT_DIR / "analysis_data.json").write_text(
        json.dumps(visualization_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print(summary_frame.to_string(index=False))
    print(f"\nErgebnisse gespeichert in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
