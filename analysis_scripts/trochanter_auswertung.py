"""Portable integration of Trochanter's relative trochanter analysis.

The original analysis was written for wide CSV files in a fixed specimen
folder. This adapter also accepts the tracker's current long-form CSV and
derives the specimen and protocol from the file name.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
VENDOR_DIRECTORY = SCRIPT_DIRECTORY.parent / "analysis_vendor"
for directory in (VENDOR_DIRECTORY, SCRIPT_DIRECTORY):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import trochanter_analysis_core as core


PROTOCOL_PATTERN = re.compile(
    r"^(?P<specimen>.+?)_?(?P<cerclage>8er|double|screw)(?P<repeat>\d*)_"
    r"(?P<load>150N|300N|450N|RTF)\.csv$",
    re.IGNORECASE,
)
LONG_COLUMNS = {
    "frame_counter", "geometry_id", "visible", "x_mm", "y_mm", "z_mm",
    "r00", "r01", "r02", "r10", "r11", "r12", "r20", "r21", "r22",
}
ORIGINAL_READ_CSV = core.pd.read_csv


def parse_recording_name(path: Path) -> tuple[str, str, str, str]:
    """Accept existing recording names without renaming or merging repetitions."""
    match = PROTOCOL_PATTERN.fullmatch(Path(path).name)
    if match is None:
        raise ValueError(
            "Dateiname: <Probe>_<8er|double|screw>[Wiederholung]_"
            "<150N|300N|450N|RTF>.csv, z.B. EXAMPLE_R_double2_150N.csv."
        )
    return (match['specimen'], match['cerclage'].lower(),
            match['repeat'], match['load'].upper())


def _input_path(arguments: Sequence[str]) -> Path:
    parser = argparse.ArgumentParser(
        description="Trochanter-Auswertung fuer Marker 299, 321 und 322",
        add_help=False,
    )
    parser.add_argument("input", nargs="?", type=Path)
    options, remaining = parser.parse_known_args(list(arguments))
    candidates = []
    if options.input is not None:
        candidates.append(options.input)
    candidates.extend(Path(value) for value in remaining if not value.startswith("-"))
    for candidate in candidates:
        if candidate.suffix.lower() == ".csv" or candidate.is_file():
            return candidate.resolve()
    raise ValueError("Pfad zur Atracsys-CSV fehlt.")


def _read_current_long_csv(path: Path):
    headers = set(ORIGINAL_READ_CSV(path, nrows=0).columns)
    if not LONG_COLUMNS.issubset(headers):
        missing = ", ".join(sorted(LONG_COLUMNS - headers))
        raise ValueError(f"Unbekanntes Atracsys-CSV-Format; Spalten fehlen: {missing}")

    optional = [
        name for name in ("shared_sample_index", "time_since_start_s", "computer_monotonic_s")
        if name in headers
    ]
    source = ORIGINAL_READ_CSV(path, usecols=sorted(LONG_COLUMNS | set(optional)))
    source = source[source["geometry_id"].isin((core.REFERENCE_ID, core.TROCHANTER_ID, core.FEMUR_ID))].copy()
    if source.empty:
        raise ValueError("Die Trochanter-Marker 299, 321 und 322 wurden nicht gefunden.")

    shared = source.get("shared_sample_index")
    frame_key = "shared_sample_index" if shared is not None and shared.notna().any() else "frame_counter"
    first_rows = source.drop_duplicates(frame_key, keep="first")
    frame_values = first_rows[frame_key].tolist()
    result = core.pd.DataFrame(index=frame_values)
    result["sample_index"] = range(len(result))

    relative_time = source.get("time_since_start_s")
    if relative_time is not None and relative_time.notna().any():
        times = first_rows.set_index(frame_key)["time_since_start_s"].reindex(frame_values).astype(float)
    else:
        times = first_rows.set_index(frame_key)["computer_monotonic_s"].reindex(frame_values).astype(float)
        times = times - float(times.iloc[0])
    result["timestamp_s"] = times.to_numpy()

    rotations = [f"r{row}{column}" for row in range(3) for column in range(3)]
    for marker_id in (core.REFERENCE_ID, core.TROCHANTER_ID, core.FEMUR_ID):
        marker = source[source["geometry_id"] == marker_id].drop_duplicates(frame_key, keep="last").set_index(frame_key)
        prefix = f"marker_{marker_id}_"
        result[prefix + "visible"] = marker["visible"].reindex(frame_values).fillna(0).astype(int).to_numpy()
        for axis in ("x", "y", "z"):
            result[prefix + f"{axis}_mm"] = marker[f"{axis}_mm"].reindex(frame_values).to_numpy()
        for rotation in rotations:
            result[prefix + rotation.upper()] = marker[rotation].reindex(frame_values).to_numpy()
    return result.reset_index(drop=True)[core.USE_COLUMNS]


def _compatible_read_csv(path, *args, **kwargs):
    requested = kwargs.get("usecols")
    if requested is not None and set(requested) == set(core.USE_COLUMNS):
        headers = set(ORIGINAL_READ_CSV(path, nrows=0).columns)
        if "geometry_id" in headers:
            return _read_current_long_csv(Path(path))
    return ORIGINAL_READ_CSV(path, *args, **kwargs)


def _configure(input_path: Path) -> None:
    specimen, protocol, repeat, load = parse_recording_name(input_path)
    core.SPECIMEN = specimen
    core.INPUT_DIR = input_path.parent
    core.OUTPUT_DIR = input_path.parent / f"{specimen}_analysis" / "recordings" / input_path.stem
    core.PLOTS_DIR = core.OUTPUT_DIR / "plots"
    core.DECLARED_FAILURES = core.DECLARED_FAILURES_BY_SPECIMEN.get(specimen, {})
    core.FILE_PATTERN = re.compile(
        rf"^{re.escape(specimen)}_?(8er|double|screw)\d*_(150N|300N|450N|RTF)\.csv$",
        re.IGNORECASE,
    )
    core.pd.read_csv = _compatible_read_csv
    # Automatic analysis is a single recording, never an implicit folder batch.
    core.find_input_files = lambda: [input_path]


def main(argv: Sequence[str] | None = None) -> int:
    input_path = _input_path(sys.argv[1:] if argv is None else argv)
    if not input_path.is_file():
        raise FileNotFoundError(f"Atracsys-CSV nicht gefunden: {input_path}")
    import tempfile
    from datetime import datetime

    names = ("SPECIMEN", "INPUT_DIR", "OUTPUT_DIR", "PLOTS_DIR",
             "DECLARED_FAILURES", "FILE_PATTERN", "find_input_files")
    saved = {name: getattr(core, name) for name in names}
    saved_reader = core.pd.read_csv
    try:
        _configure(input_path)
        destination = core.OUTPUT_DIR
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination = destination.with_name(destination.name + "_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        # Publish a complete directory, keeping previous analyses on repeat runs.
        with tempfile.TemporaryDirectory(prefix=".trochanter-", dir=destination.parent) as temporary:
            core.OUTPUT_DIR = Path(temporary) / "results"
            core.PLOTS_DIR = core.OUTPUT_DIR / "plots"
            core.main()
            core.OUTPUT_DIR.rename(destination)
        print(f"Trochanter-Ergebnisse: {destination}")
    finally:
        core.pd.read_csv = saved_reader
        for name, value in saved.items():
            setattr(core, name, value)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"TROCHANTER-AUSWERTUNG FEHLGESCHLAGEN: {error}", file=sys.stderr)
        raise
