from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import rom_auswertung


MODES = ("FE", "LB", "AR", "AS", "LS", "AC")


def _mode_from_filename(path: Path) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
    for mode in MODES:
        if re.search(rf"(^|_){mode.lower()}($|_)", normalized):
            return mode
    return None


def _select_file() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askopenfilename(
        parent=root,
        title="Select historical Atracsys ROM CSV",
        filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
    )
    root.destroy()
    return Path(selected) if selected else None


def _select_mode(default: str = "FE") -> str | None:
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    while True:
        selected = simpledialog.askstring(
            "Motion type",
            "Enter motion type: FE, LB, AR, AS, LS or AC",
            initialvalue=default,
            parent=root,
        )
        if selected is None:
            root.destroy()
            return None
        mode = selected.strip().upper()
        if mode in MODES:
            root.destroy()
            return mode


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a historical five-marker Atracsys CSV without a testXpert .tra file"
    )
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--bewegung", choices=MODES, default=None)
    parser.add_argument("--no-open", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = args.input.resolve() if args.input is not None else _select_file()
    if input_path is None:
        return 0
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"CSV file was not found: {input_path}")
    mode = args.bewegung or _mode_from_filename(input_path) or _select_mode()
    if mode is None:
        return 0
    arguments = [
        str(input_path),
        "--bewegung",
        mode,
        "--ohne-zwick",
    ]
    arguments.append("--no-open" if args.no_open else "--open")
    return rom_auswertung.main(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"RETROSPECTIVE ANALYSIS ERROR: {error}", file=sys.stderr)
        raise
