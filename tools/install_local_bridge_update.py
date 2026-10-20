"""Install a verified local native build into a NEW staged source-update runtime.

Never modifies the running application or distributes the licensed SDK.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from atracsys_sync.updater import install_update


def install(args):
    binary = args.bridge.resolve(strict=True)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != args.bridge_sha256:
        raise ValueError('Local bridge checksum mismatch')

    def verify(stage):
        target = stage/'native/bin/atracsys_bridge.exe'
        shutil.copy2(binary, target)
        build_path = stage/'sync_extension/build.json'
        build = json.loads(build_path.read_text(encoding='utf-8'))
        build['files']['native/bin/atracsys_bridge.exe'] = digest
        build['native_bridge_revision'] = args.revision
        build['native_bridge_features'] = ['segmentation-overflow-recovery-v1']
        build_path.write_text(json.dumps(build,indent=2),encoding='utf-8')
        result = subprocess.run([str(stage/'AtracsysLiveTracker_Messskripte.exe'),
                                 '--sync-self-test',str(args.test_output.resolve())],timeout=120)
        report=json.loads((args.test_output/'result.json').read_text())
        print(json.dumps(report),flush=True)
        return result.returncode==0 and report.get('passed') is True

    return install_update(args.current,args.archive,args.target_parent,args.sha256,
                          args.revision,verify_callback=verify)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('current','archive','target-parent','bridge','test-output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('sha256','revision','bridge-sha256'):
        parser.add_argument('--'+name,required=True)
    print(install(parser.parse_args()))
