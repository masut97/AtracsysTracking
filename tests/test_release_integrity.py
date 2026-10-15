import hashlib
import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location("verify_release", Path(__file__).resolve().parents[1] / "tools/verify_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_integrity_detects_modified_core_but_allows_local_runtime_settings(tmp_path):
    for name in release.REQUIRED:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"baseline")
    (tmp_path / "sdk").mkdir()
    config = {"sdk_root": "sdk", "analysis_script": "analysis_scripts/auto_auswertung.py"}
    (tmp_path / "config.json").write_text(json.dumps(config))
    (tmp_path / "update_settings.json").write_text(json.dumps({"update_directory": "original"}))
    files = {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in tmp_path.rglob("*") if p.is_file()}
    (tmp_path / "sync_extension/build.json").write_text(json.dumps({"files": files}))
    assert release.verify(tmp_path)["passed"]
    config["measurement_directory"] = "some-user-measurements"
    (tmp_path / "config.json").write_text(json.dumps(config))
    (tmp_path / "update_settings.json").write_text(json.dumps({"update_directory": "rebased-installation"}))
    assert release.verify(tmp_path)["passed"]
    (tmp_path / "analysis_vendor/trochanter_analysis_core.py").write_bytes(b"modified")
    assert "Hash mismatch: analysis_vendor/trochanter_analysis_core.py" in release.verify(tmp_path)["errors"]
    config["sdk_root"] = str(tmp_path.parent)
    (tmp_path / "config.json").write_text(json.dumps(config))
    assert "sdk_root must resolve inside this release" in release.verify(tmp_path)["errors"]
    files["../outside.py"] = "not-an-allowed-entry"
    (tmp_path / "sync_extension/build.json").write_text(json.dumps({"files": files}))
    assert "Invalid manifest path: ../outside.py" in release.verify(tmp_path)["errors"]
