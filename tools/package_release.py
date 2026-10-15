"""Add offline installer and a full checksum manifest to a tested App package."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package(root, archive=None):
    root = Path(root).resolve()
    if not (root / "App" / "sync_extension" / "build.json").is_file():
        raise ValueError("A complete built App is required")
    (root / "Installer").mkdir(exist_ok=True)
    for name in ("Installieren.cmd", "Starten.cmd"):
        shutil.copy2(ROOT / "packaging" / name, root / name)
    shutil.copy2(ROOT / "packaging" / "Installieren.ps1", root / "Installer" / "Installieren.ps1")
    shutil.copy2(ROOT / "README.md", root / "README.md")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "PAKET.json" or "__pycache__" in path.parts:
            continue
        if any(part in {"measurements", "logs", "diagnostics", "screenshots"} for part in path.relative_to(root).parts[:-1]):
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files.append({"path": path.relative_to(root).as_posix(), "sha256": digest})
    manifest = {"version": revision, "required_files": files}
    (root / "PAKET.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if archive:
        archive = Path(archive).absolute()
        if archive.exists() or archive.resolve().is_relative_to(root):
            raise ValueError("Archive must be a new path outside the package")
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
            for item in files + [{"path": "PAKET.json"}]:
                output.write(root / item["path"], root.name + "/" + item["path"])
    print(json.dumps({"root": str(root), "revision": revision, "files": len(files), "archive": str(archive or "")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--zip", type=Path)
    args = parser.parse_args()
    package(args.root, args.zip)
