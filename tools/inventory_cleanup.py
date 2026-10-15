"""Inventory installations and quarantine explicitly selected, reproducible caches.

No permanent deletion is supported. Inventory never follows directory links.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from datetime import datetime, timezone

CACHE_NAMES = {"__pycache__", ".pytest_cache"}
EXCLUDED = {".git", ".venv", ".arduino", "build", "diagnostics", "measurements",
            "geometries", "back_up_geom", "firmware", "sdk", "native", "analysis_vendor", "_internal"}


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inventory(root: Path, hashes: bool = False) -> dict:
    report = {"root": str(root), "files": [], "links_skipped": [], "errors": [], "resolved_root": str(root.resolve())}
    if not root.exists():
        report["errors"].append("Root does not exist")
        return report
    if is_link(root):
        report["links_skipped"].append(str(root))
        return report
    def walk_error(exc):
        report["errors"].append({"path": str(exc.filename), "error": str(exc)})
    for current, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(current)
        for name in directories[:]:
            path = base / name
            try:
                linked = is_link(path)
            except OSError as exc:
                report["errors"].append({"path": str(path), "error": str(exc)})
                directories.remove(name)
                continue
            if linked:
                report["links_skipped"].append(str(path))
                directories.remove(name)
        for name in files:
            path = base / name
            try:
                if is_link(path):
                    report["links_skipped"].append(str(path))
                    continue
                info = path.stat()
                item = {"path": str(path.relative_to(root)), "bytes": info.st_size,
                        "mtime_ns": info.st_mtime_ns}
                if hashes:
                    item["sha256"] = digest(path)
                report["files"].append(item)
            except OSError as exc:
                report["errors"].append({"path": str(path), "error": str(exc)})
    report["files"].sort(key=lambda item: item["path"])
    return report


def safe_cache(root: Path, requested: str) -> Path:
    """Allow only caches in owned source folders, with no reparse ancestors."""
    relative = Path(requested)
    if relative.is_absolute() or ".." in relative.parts or relative.name not in CACHE_NAMES:
        raise ValueError("Select a relative __pycache__ or .pytest_cache directory")
    if any(part.casefold() in EXCLUDED for part in relative.parts[:-1]):
        raise ValueError("Cache is inside a protected or generated tree")
    path = root / relative
    for ancestor in [*root.parents, root, *[root.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1)]]:
        if is_link(ancestor):
            raise ValueError(f"Reparse point is not allowed: {ancestor}")
    if not path.is_dir():
        raise ValueError(f"Not a directory: {path}")
    # Reject linked contents, too: quarantine must not move a reference to user data.
    contents = inventory(path, hashes=True)
    if contents["links_skipped"] or contents["errors"]:
        raise ValueError("Cache has links or unreadable contents")
    allowed_pytest = {"README.md", ".gitignore", "CACHEDIR.TAG", "v/cache/nodeids",
                      "v/cache/lastfailed", "v/cache/stepwise", "v/cache/durations"}
    for item in contents["files"]:
        name = Path(item["path"])
        allowed = name.suffix == ".pyc" if relative.name == "__pycache__" else name.as_posix() in allowed_pytest
        if not allowed:
            raise ValueError(f"Unrecognized cache content: {name}")
    return path


def write_manifest(path: Path, report: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(path)


def quarantine(root: Path, requested: list[str]) -> dict:
    paths = [safe_cache(root, name) for name in requested]
    if len(set(paths)) != len(paths) or any(a in b.parents for a in paths for b in paths if a != b):
        raise ValueError("Duplicate or overlapping cache selections")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / (".cleanup-quarantine-" + stamp + "-" + uuid.uuid4().hex[:8])
    target.mkdir()
    report = {"created_utc": stamp, "source_root": str(root), "quarantine": str(target), "entries": []}
    manifest = target / "manifest.json"
    for path in paths:
        relative = path.relative_to(root)
        destination = target / relative
        entry = {"original": str(path), "destination": str(destination),
                 "inventory": inventory(path, hashes=True), "status": "planned"}
        report["entries"].append(entry)
    # Write recovery information before moving anything; update after each move.
    write_manifest(manifest, report)
    for entry in report["entries"]:
        source, destination = Path(entry["original"]), Path(entry["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        entry["status"] = "moved"
        write_manifest(manifest, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--hash", action="store_true", help="SHA256 all regular files")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quarantine-cache", action="append", default=[], metavar="RELATIVE_PATH",
                        help="Explicit owned cache only; never delete permanently")
    args = parser.parse_args()
    root = args.root.absolute()
    if args.output.exists():
        parser.error("Output already exists; choose a new report path")
    result = quarantine(root, args.quarantine_cache) if args.quarantine_cache else inventory(root, args.hash)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(args.output)


if __name__ == "__main__":
    main()
