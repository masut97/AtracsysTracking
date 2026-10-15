"""Inspect the installed application's Python archive without running it."""
from pathlib import Path
import argparse
import dis
import json
import types
from PyInstaller.archive.readers import CArchiveReader

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("app", type=Path, help="Existing licensed App directory")
APP = parser.parse_args().app.resolve()
OUT = Path(__file__).resolve().parents[1] / "diagnostics"
OUT.mkdir(exist_ok=True)
archive = CArchiveReader(str(APP / "AtracsysLiveTracker.exe"))
pyz = archive.open_embedded_archive("PYZ-00.pyz")

def describe(code, prefix=""):
    for child in code.co_consts:
        if isinstance(child, types.CodeType):
            name = prefix + child.co_name
            yield {"name": name, "args": child.co_varnames[:child.co_argcount],
                   "names": child.co_names}
            yield from describe(child, name + ".")

for module in ("app", "recording", "acquisition", "config", "models", "ui.main_window"):
    code = pyz.extract("atracsys_tracker." + module)
    (OUT / (module + ".json")).write_text(json.dumps(list(describe(code)), indent=2), encoding="utf-8")
    with (OUT / (module + ".dis.txt")).open("w", encoding="utf-8") as stream:
        dis.dis(code, file=stream)
    print(module, ", ".join(item["name"] for item in describe(code) if item["name"].count(".") < 2))
