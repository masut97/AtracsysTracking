"""Create a source-only release; no SDK, credentials, calibration or measurements."""
import argparse
import hashlib
import json
import io
from pathlib import Path
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def create_update(output):
    output = Path(output)
    if output.exists() or output.with_suffix(output.suffix + ".sha256").exists():
        raise FileExistsError(output)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    config = json.loads(subprocess.check_output(["git", "show", "HEAD:update-release-config.json"], cwd=ROOT))
    snapshot = subprocess.check_output(["git", "archive", "HEAD", "atracsys_sync", "analysis_scripts", "measurement_scripts"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as source:
        payload = {entry.name: source.extractfile(entry).read() for entry in source.getmembers()
                   if entry.isfile() and entry.name.endswith(".py")}
    files = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}
    manifest = {"format_version": 1, "revision": revision,
                "compatible_base_sha256": config["compatible_base_sha256"], "files": files}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("update-manifest.json", json.dumps(manifest, indent=2))
        for name in files:
            archive.writestr(name, payload[name])
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n", encoding="utf-8")
    print(json.dumps({"revision": revision, "files": len(files), "sha256": digest}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_update(args.output)
