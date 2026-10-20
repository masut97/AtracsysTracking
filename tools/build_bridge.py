"""Build the native bridge into a NEW directory; never install or run it."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(output, sdk_root=None):
    output = Path(output).resolve()
    source = ROOT / "native"
    if output.exists() or output == source or source in output.parents or output in source.parents:
        raise ValueError("Use a new build directory outside native/; existing paths are refused.")
    command = ["cmake", "-S", str(source), "-B", str(output),
               "-G", "Visual Studio 17 2022", "-A", "x64"]
    if sdk_root is not None:
        command.append("-DATRACSYS_SDK_ROOT=" + str(Path(sdk_root).resolve()))
    subprocess.run(command, check=True)
    subprocess.run(["cmake", "--build", str(output), "--config", "Release"], check=True)
    subprocess.run(["ctest", "--test-dir", str(output), "-C", "Release",
                    "--output-on-failure", "--no-tests=error"], check=True)
    return output / "Release" / "atracsys_bridge.exe"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sdk-root", type=Path)
    args = parser.parse_args()
    print(build(args.output, args.sdk_root))
