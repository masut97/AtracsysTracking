import hashlib
import json

from atracsys_sync.local_migration import initialize_updated_runtime, preserve_analysis_settings


def test_legacy_calibration_and_analysis_paths_stay_local_without_executing_code(tmp_path):
    source, target = tmp_path / "old", tmp_path / "new"
    library = source / "analysis_scripts/_lib"
    library.mkdir(parents=True)
    (library / "relative_markers.py").write_text(
        "raise RuntimeError('must not run')\nNEW_KOS_299_FROM_RECORDED=np.array(((1,0,0),(0,1,0),(0,0,1)))\n"
        "NEW_KOS_299_ORIGIN_IN_RECORDED_MM=np.array((1,2,3))\n")
    (source / "analysis_scripts/rom_auswertung_config.json").write_text(json.dumps({"zwick_search_directories": ["local-export"]}))
    vendor = source / "analysis_vendor"
    vendor.mkdir()
    (vendor / "legacy_analysis_core.py").write_text("DECLARED_FAILURES_BY_SPECIMEN={'EXAMPLE_R':{('screw',100):{'failure_cycle':12}}}")
    preserve_analysis_settings(source, target)
    calibration = json.loads((target / "analysis_scripts/marker_transforms.local.json").read_text())
    assert calibration["299"]["origin_mm"] == [1, 2, 3]
    annotations = json.loads((target / "analysis_vendor/failure_annotations.local.json").read_text())
    assert annotations["EXAMPLE_R"]["screw:100"]["failure_cycle"] == 12
    local_config = target / "analysis_scripts/rom_auswertung_config.local.json"
    assert json.loads(local_config.read_text())["zwick_search_directories"] == ["local-export"]
    local_config.write_text('{"existing":true}')
    preserve_analysis_settings(source, target)
    assert json.loads(local_config.read_text()) == {"existing": True}


def test_first_update_from_old_installer_preserves_and_relocates_new_library(tmp_path):
    app = tmp_path / "App"
    core = app / "analysis_scripts/trochanter_analysis_core.py"
    core.parent.mkdir(parents=True)
    code = b"VALUE=1\n"
    core.write_bytes(code)
    digest = hashlib.sha256(code).hexdigest()
    extension = app / "sync_extension"
    extension.mkdir()
    key = "analysis_scripts/" + core.name
    (extension / "update.json").write_text(json.dumps({"previous_app": str(tmp_path / "old"), "source_files": {key: digest}}))
    (extension / "build.json").write_text(json.dumps({"files": {key: digest}}))
    initialize_updated_runtime(app)
    assert not core.exists()
    assert (app / "analysis_vendor" / core.name).read_bytes() == code
    files = json.loads((extension / "build.json").read_text())["files"]
    assert key not in files
    assert files["analysis_vendor/" + core.name] == digest
    initialize_updated_runtime(app)
