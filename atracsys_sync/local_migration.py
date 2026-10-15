"""Preserve legacy private analysis settings without executing legacy source."""
import ast
import hashlib
import json
from pathlib import Path
import shutil


def _assignments(path):
    if not path.is_file():
        return {}
    return {node.targets[0].id: node.value for node in ast.parse(path.read_text(encoding="utf-8-sig")).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}


def _write_new(path, value):
    if not value or path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)


def preserve_analysis_settings(source, target):
    source, target = Path(source), Path(target)
    old_scripts, new_scripts = source / "analysis_scripts", target / "analysis_scripts"
    for path in old_scripts.glob("*_config.json"):
        _write_new(new_scripts / path.with_suffix(".local.json").name,
                   json.loads(path.read_text(encoding="utf-8-sig")))
    transforms = {}
    candidates = [old_scripts / "_lib/relative_markers.py", old_scripts / "Auswertung_340_341_342.py"]
    for path in candidates:
        for name, node in _assignments(path).items():
            if not name.startswith("NEW_KOS_") or not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "array":
                continue
            marker = name.split("_")[2]
            field = "origin_mm" if "ORIGIN" in name else "rotation"
            transforms.setdefault(marker, {})[field] = ast.literal_eval(node.args[0])
    _write_new(new_scripts / "marker_transforms.local.json", transforms)
    for path in (source / "analysis_vendor").glob("*analysis_core.py"):
        value = _assignments(path).get("DECLARED_FAILURES_BY_SPECIMEN")
        if isinstance(value, ast.Dict):
            raw = ast.literal_eval(value)
            values = {sample: {f"{protocol}:{force}": item for (protocol, force), item in annotations.items()}
                      for sample, annotations in raw.items()}
            _write_new(target / "analysis_vendor/failure_annotations.local.json", values)


def initialize_updated_runtime(root):
    """Bridge the first update from an older installer without losing local values."""
    root = Path(root)
    report_path = root / "sync_extension/update.json"
    if not report_path.is_file():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    previous = Path(report["previous_app"])
    if previous.resolve() != root.resolve() and previous.is_dir():
        preserve_analysis_settings(previous, root)
    # Older installers put this new library beside selectable entry points.
    core = root / "analysis_scripts/trochanter_analysis_core.py"
    key = "analysis_scripts/trochanter_analysis_core.py"
    expected = report.get("source_files", {}).get(key)
    if core.is_file() and expected:
        if hashlib.sha256(core.read_bytes()).hexdigest() != expected:
            raise ValueError("Updated analysis library failed its integrity check")
        destination = root / "analysis_vendor" / core.name
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(core, destination)
        build_path = root / "sync_extension/build.json"
        build = json.loads(build_path.read_text(encoding="utf-8"))
        build["files"].pop(key, None)
        build["files"]["analysis_vendor/" + core.name] = expected
        build_path.write_text(json.dumps(build, indent=2), encoding="utf-8")
        core.unlink()  # Own verified release file in the new version only.
