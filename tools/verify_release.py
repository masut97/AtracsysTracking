"""Read-only integrity check for a freshly built or installed App directory."""
import argparse
import hashlib
import json
from pathlib import Path


MUTABLE_FILES = {"config.json", "measurement_scripts.json", "measurement_startup_error.log", "update_settings.json"}
MUTABLE_DIRECTORIES = {"logs", "diagnostics", "screenshots", "__pycache__"}
REQUIRED = {
    "AtracsysLiveTracker_Messskripte.exe", "analysis_scripts/_lib/relative_markers.py",
    "analysis_scripts/trochanter_auswertung.py", "analysis_scripts/auto_auswertung.py",
    "analysis_vendor/trochanter_analysis_core.py", "sync_extension/atracsys_sync/adapter.py",
    "sync_extension/atracsys_sync/status.py", "native/bin/atracsys_bridge.exe",
}


def verify(app):
    app = Path(app).resolve(strict=True)
    report = json.loads((app / "sync_extension/build.json").read_text(encoding="utf-8"))
    files = report.get("files", {})
    errors, checked = [], 0
    for required in sorted(REQUIRED):
        if required not in files or not (app / required).is_file():
            errors.append("Missing required release file: " + required)
    for name, expected in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("Invalid manifest path: " + name)
            continue
        if name in MUTABLE_FILES or any(part in MUTABLE_DIRECTORIES for part in relative.parts[:-1]):
            continue
        path = app / relative
        if not path.is_file() or not path.resolve().is_relative_to(app):
            errors.append("Missing or external release file: " + name)
            continue
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            errors.append("Hash mismatch: " + name)
        checked += 1
    config = json.loads((app / "config.json").read_text(encoding="utf-8-sig"))
    sdk = Path(config.get("sdk_root", ""))
    if not sdk.is_absolute():
        sdk = app / sdk
    if not sdk.is_dir() or not sdk.resolve().is_relative_to(app):
        errors.append("sdk_root must resolve inside this release")
    if config.get("analysis_script"):
        selected = Path(config["analysis_script"])
        if not selected.is_absolute():
            selected = app / selected
        if not selected.is_file() or not selected.resolve().is_relative_to(app):
            errors.append("Selected analysis must exist inside this release")
    return {"passed": not errors, "app": str(app), "checked_files": checked, "errors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    result = verify(args.app)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
