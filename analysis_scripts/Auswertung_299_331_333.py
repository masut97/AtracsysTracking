"""Relative-marker analysis; shared implementation for marker variants."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from relative_markers import main

if __name__ == "__main__":
    raise SystemExit(main(333))
