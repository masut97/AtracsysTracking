from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))


MONOTEST_MARKERS = {299, 301, 302}
ROM_MARKERS = {299, 301, 302, 311, 314}
TROCHANTER_MARKERS = {299, 321, 322}
TOGGLING_MARKERS = {340, 341, 342}


def available_marker_ids(path: Path) -> set[int]:
    """Read marker IDs from a current long-form or legacy wide-form CSV."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        try:
            headers = [value.strip() for value in next(reader)]
        except StopIteration as error:
            raise ValueError("The Atracsys file is empty.") from error

    if "geometry_id" in headers:
        geometry_index = headers.index("geometry_id")
        visible_index = headers.index("visible") if "visible" in headers else None
        result: set[int] = set()
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            next(reader, None)
            for row in reader:
                if geometry_index >= len(row):
                    continue
                if (
                    visible_index is not None
                    and visible_index < len(row)
                    and row[visible_index].strip() not in {"", "1"}
                ):
                    continue
                try:
                    result.add(int(row[geometry_index]))
                except ValueError:
                    continue
        return result

    normalized = {header.replace(" ", "").lower() for header in headers}
    wide_marker_ids: set[int] = set()
    for header in normalized:
        match = re.fullmatch(r"marker_(\d+)_visible", header)
        if match:
            wide_marker_ids.add(int(match.group(1)))
    if wide_marker_ids:
        return wide_marker_ids
    if {
        "ref_tx",
        "clampup_tx",
        "clampdn_tx",
        "vertcran_tx",
        "vertcaud_tx",
    }.issubset(normalized):
        return set(ROM_MARKERS)
    return set()


def _extract_dispatch_option(argv: Sequence[str]) -> tuple[str, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--auswertung",
        choices=("auto", "mono", "rom", "trochanter", "toggling"),
        default="auto",
    )
    options, remaining = parser.parse_known_args(list(argv))
    return options.auswertung, remaining


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    selection, remaining = _extract_dispatch_option(arguments)
    positional = [value for value in remaining if not value.startswith("-")]
    if not positional:
        raise ValueError("Pfad zur Atracsys-CSV fehlt.")
    input_path = Path(positional[0]).resolve()

    marker_ids = available_marker_ids(input_path)
    if selection == "toggling" or (selection == "auto" and TOGGLING_MARKERS.issubset(marker_ids)):
        import toggling_auswertung
        print("Automatic selection: toggling (reference 340, bone 341, screw 342).")
        return toggling_auswertung.main(remaining)
    if selection == "rom" or (selection == "auto" and ROM_MARKERS.issubset(marker_ids)):
        import rom_auswertung

        print(
            "Automatic selection: five-marker ROM analysis "
            "(299, 301, 302, 311, 314)."
        )
        return rom_auswertung.main(remaining)

    if selection == "trochanter" or (
        selection == "auto" and TROCHANTER_MARKERS.issubset(marker_ids)
    ):
        import trochanter_auswertung

        print(
            "Automatic selection: Trochanter trochanter analysis "
            "(299, 321, 322)."
        )
        return trochanter_auswertung.main(remaining)

    if selection == "mono" or (
        selection == "auto" and MONOTEST_MARKERS.issubset(marker_ids)
    ):
        import auswertung

        print("Automatic selection: three-marker monotest analysis (299, 301, 302).")
        return auswertung.main(remaining)

    found = ", ".join(str(value) for value in sorted(marker_ids)) or "keine"
    raise ValueError(
        "The analysis type could not be identified. Visible markers: " + found
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"AUTOMATISCHE AUSWERTUNG FEHLGESCHLAGEN: {error}", file=sys.stderr)
        raise
