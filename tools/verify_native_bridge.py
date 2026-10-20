"""Compare PE files, ignoring ONLY Rich build metadata and linker timestamps.

This reads files; it never executes either bridge or accesses a device.
Requires pefile, supplied by the Windows PyInstaller development dependency.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pefile


def inspect(path):
    data = Path(path).read_bytes()
    pe = pefile.PE(data=data)
    if pe.FILE_HEADER.Machine != 0x8664:
        raise ValueError("Expected a Windows x64 PE image")
    ranges = [(pe.FILE_HEADER.get_field_absolute_offset("TimeDateStamp"), 4)]
    for entry in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []):
        ranges.append((entry.struct.get_field_absolute_offset("TimeDateStamp"), 4))
    rich = getattr(pe, "RICH_HEADER", None)
    if rich:
        offset = data.find(rich.raw_data, 64, pe.DOS_HEADER.e_lfanew)
        if offset < 0 or data[offset + len(rich.raw_data):offset + len(rich.raw_data) + 4] != b"Rich":
            raise ValueError("Cannot locate Rich build metadata safely")
        ranges.append((offset, len(rich.raw_data) + 8))
    normalized = bytearray(data)
    for offset, size in ranges:
        normalized[offset:offset + size] = bytes(size)
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "normalized_sha256": hashlib.sha256(normalized).hexdigest(),
        "ignored_metadata_ranges": sorted(ranges),
        "sections": {s.Name.rstrip(b"\0").decode("ascii"): hashlib.sha256(s.get_data()).hexdigest()
                     for s in pe.sections},
    }


def compare(reference, candidate):
    original, rebuilt = inspect(reference), inspect(candidate)
    matches = (original["ignored_metadata_ranges"] == rebuilt["ignored_metadata_ranges"] and
               original["normalized_sha256"] == rebuilt["normalized_sha256"])
    return {"equivalent_except_build_metadata": matches,
            "reference": original, "candidate": rebuilt}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    report = compare(args.reference, args.candidate)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["equivalent_except_build_metadata"] else 1)
