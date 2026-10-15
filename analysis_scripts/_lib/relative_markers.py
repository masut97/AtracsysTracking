from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QFileDialog
from scipy.spatial.transform import Rotation


MARKER_IDS = (299, 331, 332)
SCREW_ID = 332

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


from _lib.local_settings import load_transform

NEW_KOS_299_FROM_RECORDED, NEW_KOS_299_ORIGIN_IN_RECORDED_MM = load_transform(299)


@dataclass(frozen=True)
class Pose:
    time_s: float
    position_mm: np.ndarray
    rotation: np.ndarray
    registration_error_mm: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auswertung der manuellen X-/Y-/Z- und Rotationstests"
    )

    # nargs="?" macht die Eingabedatei optional.
    # Wenn keine Datei angegeben wird, öffnet sich ein Auswahlfenster.
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=None,
        help="CSV-Datei mit den aufgezeichneten Markerpositionen",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optionaler Ausgabeordner",
    )

    parser.add_argument(
        "--baseline-seconds",
        type=float,
        default=1.0,
        help="Dauer der Baseline in Sekunden",
    )

    parser.add_argument(
        "--transform-299-to-step-kos",
        action="store_true",
        help=(
            "Transformiert die aufgezeichneten Posen von Marker 299 "
            "in das Koordinatensystem aus 299_ref_marker.STEP"
        ),
    )

    return parser.parse_args()


def select_input_file() -> Path | None:
    filename, _ = QFileDialog.getOpenFileName(
        None,
        "CSV-Datei für die Auswertung auswählen",
        "",
        "CSV-Dateien (*.csv);;Alle Dateien (*.*)",
    )

    if not filename:
        return None

    return Path(filename)


def load_frames(
    path: Path,
) -> tuple[np.ndarray, dict[int, tuple[Pose, ...]], int]:
    frames: dict[int, dict[int, Pose]] = {}
    total_rows = 0

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        reader = csv.DictReader(stream)

        if reader.fieldnames is None:
            raise ValueError("Die CSV-Datei enthält keine Spaltenüberschriften.")

        required_columns = {
            "geometry_id",
            "visible",
            "computer_monotonic_s",
            "frame_counter",
            "x_mm",
            "y_mm",
            "z_mm",
            "registration_error_mm",
            *ROTATION_COLUMNS,
        }

        missing_columns = required_columns.difference(reader.fieldnames)

        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise ValueError(
                f"Folgende erforderliche CSV-Spalten fehlen: {missing_text}"
            )

        for row in reader:
            total_rows += 1

            geometry_id = int(row["geometry_id"])

            if geometry_id not in MARKER_IDS:
                continue

            if row["visible"] != "1":
                continue

            rotation = np.array(
                [float(row[name]) for name in ROTATION_COLUMNS],
                dtype=float,
            ).reshape(3, 3)

            position = np.array(
                [
                    float(row["x_mm"]),
                    float(row["y_mm"]),
                    float(row["z_mm"]),
                ],
                dtype=float,
            )

            pose = Pose(
                time_s=float(row["computer_monotonic_s"]),
                position_mm=position,
                rotation=rotation,
                registration_error_mm=float(
                    row["registration_error_mm"]
                ),
            )

            frame_counter = int(row["frame_counter"])

            frames.setdefault(frame_counter, {})[geometry_id] = pose

    complete = [
        (counter, rows)
        for counter, rows in frames.items()
        if all(marker_id in rows for marker_id in MARKER_IDS)
    ]

    complete.sort(key=lambda item: item[0])

    if not complete:
        raise ValueError(
            f"Keine vollständigen Frames mit Marker 299, 331 und {SCREW_ID} gefunden."
        )

    times = np.array(
        [rows[299].time_s for _, rows in complete],
        dtype=float,
    )

    times -= times[0]

    poses = {
        geometry_id: tuple(
            rows[geometry_id]
            for _, rows in complete
        )
        for geometry_id in MARKER_IDS
    }

    return times, poses, total_rows


def relative_pose(
    reference: tuple[Pose, ...],
    target: tuple[Pose, ...],
) -> tuple[np.ndarray, np.ndarray]:
    ref_r = np.stack(
        [pose.rotation for pose in reference]
    )

    target_r = np.stack(
        [pose.rotation for pose in target]
    )

    ref_p = np.stack(
        [pose.position_mm for pose in reference]
    )

    target_p = np.stack(
        [pose.position_mm for pose in target]
    )

    # R_relativ = R_ref^T × R_target
    relative_r = np.einsum(
        "nji,njk->nik",
        ref_r,
        target_r,
    )

    # p_relativ = R_ref^T × (p_target - p_ref)
    relative_p = np.einsum(
        "nji,nj->ni",
        ref_r,
        target_p - ref_p,
    )

    return relative_p, relative_r


def transform_299_to_step_kos(
    reference: tuple[Pose, ...],
) -> tuple[Pose, ...]:
    if NEW_KOS_299_FROM_RECORDED is None:
        raise ValueError("Lokale Marker-Kalibrierung fehlt; marker_transforms.local.json bereitstellen.")
    recorded_from_new = NEW_KOS_299_FROM_RECORDED.T

    transformed_poses: list[Pose] = []

    for pose in reference:
        transformed_pose = Pose(
            time_s=pose.time_s,
            position_mm=(
                pose.position_mm
                - pose.rotation
                @ NEW_KOS_299_ORIGIN_IN_RECORDED_MM
            ),
            rotation=(
                pose.rotation
                @ recorded_from_new
            ),
            registration_error_mm=pose.registration_error_mm,
        )

        transformed_poses.append(transformed_pose)

    return tuple(transformed_poses)


def baseline_change(
    times: np.ndarray,
    positions: np.ndarray,
    rotations: np.ndarray,
    baseline_seconds: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    baseline_count = max(
        5,
        int(
            np.searchsorted(
                times,
                baseline_seconds,
                side="right",
            )
        ),
    )

    baseline_count = min(
        baseline_count,
        len(times),
    )

    baseline_position = np.median(
        positions[:baseline_count],
        axis=0,
    )

    baseline_rotation = (
        Rotation.from_matrix(
            rotations[:baseline_count]
        )
        .mean()
        .as_matrix()
    )

    delta_positions = (
        positions
        - baseline_position
    )

    delta_rotations = np.einsum(
        "nij,jk->nik",
        rotations,
        baseline_rotation.T,
    )

    euler = (
        Rotation.from_matrix(delta_rotations)
        .as_euler(
            "xyz",
            degrees=False,
        )
    )

    euler = np.rad2deg(
        np.unwrap(
            euler,
            axis=0,
        )
    )

    return delta_positions, euler, baseline_count


def bone_compensated_screw_pose_in_reference(
    bone_positions_in_reference: np.ndarray,
    bone_rotations_in_reference: np.ndarray,
    screw_positions_in_bone: np.ndarray,
    screw_rotations_in_bone: np.ndarray,
    baseline_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Entfernt die Knochenbewegung und verankert die Schraubenpose
    im Baseline-Koordinatensystem von Marker 299.
    """

    baseline_bone_position = np.median(
        bone_positions_in_reference[:baseline_count],
        axis=0,
    )

    baseline_bone_rotation = (
        Rotation.from_matrix(
            bone_rotations_in_reference[:baseline_count]
        )
        .mean()
        .as_matrix()
    )

    compensated_position = (
        baseline_bone_position
        + np.einsum(
            "ij,nj->ni",
            baseline_bone_rotation,
            screw_positions_in_bone,
        )
    )

    compensated_rotation = np.einsum(
        "ij,njk->nik",
        baseline_bone_rotation,
        screw_rotations_in_bone,
    )

    return compensated_position, compensated_rotation


def add_curve(
    plot: pg.PlotItem,
    times: np.ndarray,
    values: np.ndarray,
    axis: int,
) -> None:
    colors = (
        "#1769aa",
        "#e65100",
        "#2e7d32",
    )

    names = (
        "X",
        "Y",
        "Z",
    )

    if len(times) > 1:
        median_dt = float(
            np.median(
                np.diff(times)
            )
        )

        gap_limit_s = max(
            0.05,
            5.0 * median_dt,
        )

        gap_indices = (
            np.flatnonzero(
                np.diff(times) > gap_limit_s
            )
            + 1
        )
    else:
        gap_indices = np.array(
            [],
            dtype=int,
        )

    curve_times = np.insert(
        times,
        gap_indices,
        np.nan,
    )

    curve_values = np.insert(
        values[:, axis],
        gap_indices,
        np.nan,
    )

    plot.plot(
        curve_times,
        curve_values,
        pen=pg.mkPen(
            colors[axis],
            width=2,
        ),
        name=names[axis],
        connect="finite",
    )


def make_plot(
    times: np.ndarray,
    translations: np.ndarray,
    rotations_deg: np.ndarray,
    title: str,
    output: Path,
) -> None:
    pg.setConfigOptions(
        antialias=True,
        background="w",
        foreground="#202124",
    )

    widget = pg.GraphicsLayoutWidget(
        show=False
    )

    widget.resize(
        1800,
        1050,
    )

    heading = pg.LabelItem(
        title,
        size="17pt",
        bold=True,
        color="#202124",
    )

    widget.addItem(
        heading,
        row=0,
        col=0,
    )

    translation_plot = widget.addPlot(
        row=1,
        col=0,
    )

    translation_plot.setLabel(
        "left",
        "Translation Δ",
        units="mm",
    )

    translation_plot.showGrid(
        x=True,
        y=True,
        alpha=0.18,
    )

    translation_plot.addLegend(
        offset=(12, 8),
        labelTextSize="11pt",
    )

    translation_plot.getAxis(
        "left"
    ).setWidth(78)

    translation_plot.getAxis(
        "left"
    ).enableAutoSIPrefix(False)

    for axis in range(3):
        add_curve(
            translation_plot,
            times,
            translations,
            axis,
        )

    rotation_plot = widget.addPlot(
        row=2,
        col=0,
    )

    rotation_plot.setLabel(
        "left",
        "Rotation Δ",
        units="°",
    )

    rotation_plot.setLabel(
        "bottom",
        "Zeit",
        units="s",
    )

    rotation_plot.showGrid(
        x=True,
        y=True,
        alpha=0.18,
    )

    rotation_plot.addLegend(
        offset=(12, 8),
        labelTextSize="11pt",
    )

    rotation_plot.getAxis(
        "left"
    ).setWidth(78)

    rotation_plot.getAxis(
        "left"
    ).enableAutoSIPrefix(False)

    rotation_plot.setXLink(
        translation_plot
    )

    for axis in range(3):
        add_curve(
            rotation_plot,
            times,
            rotations_deg,
            axis,
        )

    if len(times) > 1:
        translation_plot.setXRange(
            float(times[0]),
            float(times[-1]),
            padding=0,
        )

        rotation_plot.setXRange(
            float(times[0]),
            float(times[-1]),
            padding=0,
        )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    widget.show()

    QApplication.processEvents()

    pixmap = widget.grab()

    if not pixmap.save(
        str(output),
        "PNG",
    ):
        raise RuntimeError(
            f"PNG konnte nicht gespeichert werden: {output}"
        )

    widget.close()


def component_summary(
    values: np.ndarray,
) -> dict[str, dict[str, float]]:
    names = (
        "x",
        "y",
        "z",
    )

    return {
        name: {
            "min": round(
                float(
                    np.min(
                        values[:, axis]
                    )
                ),
                6,
            ),
            "max": round(
                float(
                    np.max(
                        values[:, axis]
                    )
                ),
                6,
            ),
            "peak_to_peak": round(
                float(
                    np.ptp(
                        values[:, axis]
                    )
                ),
                6,
            ),
        }
        for axis, name in enumerate(names)
    }


def main(screw_id: int = 332) -> int:
    global MARKER_IDS, SCREW_ID
    if screw_id not in (332, 333):
        raise ValueError("Unsupported screw marker")
    SCREW_ID = screw_id
    MARKER_IDS = (299, 331, screw_id)
    args = parse_args()

    app = (
        QApplication.instance()
        or QApplication([])
    )

    # Arial unter Windows laden.
    arial_path = Path(
        r"C:\Windows\Fonts\arial.ttf"
    )

    if arial_path.exists():
        font_id = QFontDatabase.addApplicationFont(
            str(arial_path)
        )

        if font_id >= 0:
            app.setFont(
                QFont(
                    "Arial",
                    10,
                )
            )
    else:
        print(
            "Warnung: Arial wurde nicht gefunden. "
            "Es wird die Standardschrift verwendet."
        )

    # Falls beim Start keine CSV-Datei angegeben wurde,
    # öffnet sich jetzt das Dateiauswahlfenster.
    if args.input is None:
        args.input = select_input_file()

    if args.input is None:
        print(
            "Keine Datei ausgewählt. "
            "Die Auswertung wurde abgebrochen."
        )
        return 1

    if not args.input.exists():
        raise FileNotFoundError(
            f"Die Eingabedatei wurde nicht gefunden: {args.input}"
        )

    if not args.input.is_file():
        raise ValueError(
            f"Der ausgewählte Pfad ist keine Datei: {args.input}"
        )

    output_dir = (
        args.output_dir
        or args.input.parent
        / f"{args.input.stem}_plots"
    )

    times, poses, total_rows = load_frames(
        args.input
    )

    if args.transform_299_to_step_kos:
        poses[299] = transform_299_to_step_kos(
            poses[299]
        )

    kos_label = (
        "neuen KOS 299"
        if args.transform_299_to_step_kos
        else "KOS 299"
    )

    # Marker 331 relativ zu Marker 299
    bone_in_ref_p, bone_in_ref_r = relative_pose(
        poses[299],
        poses[331],
    )

    # Marker 332 relativ zu Marker 331
    screw_in_bone_p, screw_in_bone_r = relative_pose(
        poses[331],
        poses[SCREW_ID],
    )

    bone_translation, bone_rotation, baseline_count = baseline_change(
        times,
        bone_in_ref_p,
        bone_in_ref_r,
        args.baseline_seconds,
    )

    screw_translation, screw_rotation, _ = baseline_change(
        times,
        screw_in_bone_p,
        screw_in_bone_r,
        args.baseline_seconds,
    )

    compensated_screw_p, compensated_screw_r = (
        bone_compensated_screw_pose_in_reference(
            bone_in_ref_p,
            bone_in_ref_r,
            screw_in_bone_p,
            screw_in_bone_r,
            baseline_count,
        )
    )

    compensated_translation, compensated_rotation, _ = baseline_change(
        times,
        compensated_screw_p,
        compensated_screw_r,
        args.baseline_seconds,
    )

    screw_plot = (
        output_dir
        / f"{SCREW_ID}_relativ_zu_331.png"
    )

    compensated_screw_plot = (
        output_dir
        / f"{SCREW_ID}_relativ_zu_331_im_kos299.png"
    )

    stability_plot = (
        output_dir
        / "331_relativ_zu_299_stabilitaet.png"
    )

    make_plot(
        times,
        screw_translation,
        screw_rotation,
        f"{SCREW_ID} relativ zu 331",
        screw_plot,
    )

    make_plot(
        times,
        compensated_translation,
        compensated_rotation,
        "Schraubenbewegung relativ zum Knochen im KOS 299",
        compensated_screw_plot,
    )

    make_plot(
        times,
        bone_translation,
        bone_rotation,
        "331 relativ zu 299 – Stabilität",
        stability_plot,
    )

    marker_plots: list[Path] = []

    markers_in_299: dict[
        str,
        dict[str, object],
    ] = {}

    for geometry_id in MARKER_IDS:
        if geometry_id == 299:
            marker_position = np.zeros(
                (len(times), 3),
                dtype=float,
            )

            marker_rotation = np.repeat(
                np.eye(3)[None, :, :],
                len(times),
                axis=0,
            )
        else:
            marker_position, marker_rotation = relative_pose(
                poses[299],
                poses[geometry_id],
            )

        marker_translation, marker_euler, _ = baseline_change(
            times,
            marker_position,
            marker_rotation,
            args.baseline_seconds,
        )

        marker_plot = (
            output_dir
            / f"{geometry_id}_im_kos299.png"
        )

        title_suffix = (
            " (Referenz)"
            if geometry_id == 299
            else ""
        )

        make_plot(
            times,
            marker_translation,
            marker_euler,
            (
                f"Marker {geometry_id} "
                f"im {kos_label}"
                f"{title_suffix}"
            ),
            marker_plot,
        )

        marker_plots.append(
            marker_plot
        )

        markers_in_299[str(geometry_id)] = {
            "translation_mm": component_summary(
                marker_translation
            ),
            "rotation_deg": component_summary(
                marker_euler
            ),
        }

    dt = np.diff(times)

    if len(dt) > 0:
        median_frequency_hz = float(
            1.0
            / np.median(dt)
        )
    else:
        median_frequency_hz = 0.0

    report = {
        "input": str(
            args.input.resolve()
        ),
        "raw_rows": total_rows,
        "complete_frames": len(times),
        "duration_s": round(
            float(times[-1]),
            6,
        ),
        "median_frequency_hz": round(
            median_frequency_hz,
            3,
        ),
        "baseline_seconds": args.baseline_seconds,
        "baseline_frames": baseline_count,
        "reference_299_kos": {
            "name": (
                "299_ref_marker.STEP"
                if args.transform_299_to_step_kos
                else "recorded"
            ),
            "post_transformed": (
                args.transform_299_to_step_kos
            ),
            "new_from_recorded_rotation": (
                NEW_KOS_299_FROM_RECORDED.tolist()
                if args.transform_299_to_step_kos
                else np.eye(3).tolist()
            ),
            "new_origin_in_recorded_mm": (
                NEW_KOS_299_ORIGIN_IN_RECORDED_MM.tolist()
                if args.transform_299_to_step_kos
                else [0.0, 0.0, 0.0]
            ),
        },
        f"screw_{SCREW_ID}_relative_to_bone_331": {
            "translation_mm": component_summary(
                screw_translation
            ),
            "rotation_deg": component_summary(
                screw_rotation
            ),
        },
        f"screw_{SCREW_ID}_relative_to_bone_331_in_kos_299": {
            "method": (
                "T_299_331_baseline "
                "@ inverse(T_299_331_t) "
                f"@ T_299_{SCREW_ID}_t"
            ),
            "translation_mm": component_summary(
                compensated_translation
            ),
            "rotation_deg": component_summary(
                compensated_rotation
            ),
        },
        "bone_331_relative_to_reference_299": {
            "translation_mm": component_summary(
                bone_translation
            ),
            "rotation_deg": component_summary(
                bone_rotation
            ),
        },
        "markers_in_kos_299": markers_in_299,
        "registration_error_mm": {
            str(geometry_id): {
                "median": round(
                    float(
                        np.median(
                            [
                                pose.registration_error_mm
                                for pose in marker
                            ]
                        )
                    ),
                    6,
                ),
                "p95": round(
                    float(
                        np.percentile(
                            [
                                pose.registration_error_mm
                                for pose in marker
                            ],
                            95,
                        )
                    ),
                    6,
                ),
            }
            for geometry_id, marker in poses.items()
        },
        "plots": [
            str(
                screw_plot.resolve()
            ),
            str(
                compensated_screw_plot.resolve()
            ),
            str(
                stability_plot.resolve()
            ),
            *[
                str(path.resolve())
                for path in marker_plots
            ],
        ],
    }

    report_path = (
        output_dir
        / "auswertung.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
    )

    print()
    print("Auswertung erfolgreich abgeschlossen.")
    print(f"Ausgabeordner: {output_dir.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
