import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile

import pytest


spec = importlib.util.spec_from_file_location("create_update", Path(__file__).resolve().parents[1] / "tools/create_update.py")
creator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(creator)


def test_release_uses_only_committed_source_snapshot_and_preserves_existing_sidecar(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()
    git("init", "--quiet")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "test@example.invalid")
    for name, data in {
        "atracsys_sync/bootstrap.py": "VALUE = 'committed'\n",
        "analysis_scripts/analysis.py": "VALUE = 2\n",
        "measurement_scripts/sensor.py": "VALUE = 3\n",
        "measurement_scripts/private-data.csv": "must not ship",
        "sdk/license.txt": "licensed private data",
        "update-release-config.json": json.dumps({"compatible_base_sha256": ["a" * 64]}),
    }.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    git("add", ".")
    git("commit", "--quiet", "-m", "baseline")
    revision = git("rev-parse", "HEAD")
    (repo / "atracsys_sync/bootstrap.py").write_text("VALUE='dirty'\n")
    (repo / "analysis_scripts/untracked.py").write_text("SECRET='untracked'\n")
    monkeypatch.setattr(creator, "ROOT", repo)
    output = tmp_path / "update.zip"
    creator.create_update(output)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("update-manifest.json"))
        assert manifest["revision"] == revision
        assert archive.read("atracsys_sync/bootstrap.py").decode().splitlines() == ["VALUE = 'committed'"]
        assert set(archive.namelist()) == set(manifest["files"]) | {"update-manifest.json"}
        assert all(name.endswith(".py") for name in manifest["files"])
        assert "analysis_scripts/untracked.py" not in archive.namelist()
        assert "sdk/license.txt" not in archive.namelist()
    sidecar = output.with_suffix(".zip.sha256")
    assert sidecar.read_text().startswith(hashlib.sha256(output.read_bytes()).hexdigest())
    blocked = tmp_path / "blocked.zip"
    blocked_sidecar = blocked.with_suffix(".zip.sha256")
    blocked_sidecar.write_text("original checksum")
    with pytest.raises(FileExistsError):
        creator.create_update(blocked)
    assert blocked_sidecar.read_text() == "original checksum"
    assert not blocked.exists()
