# Native Atracsys bridge

`bridge_main.cpp` and `shared_protocol.hpp` are the recovered original bridge
source, unchanged. Creation was recorded on 2026-08-03; the latest recovered
bridge change was recorded on 2026-08-04. A fresh local build on 2026-10-20
reproduced the currently deployed bridge **byte for byte except for the Microsoft
Rich build header and the PE/linker debug timestamps**. This is stronger evidence
than matching strings or protocol versions. See [verification.json](verification.json)
for source hashes, compiler details and binary comparison results.

The reference binary was checked against the bridge in the current desktop
installation, application revision `10b31753ab36`. Its SHA256 is
`4d4f7affa69e46072e57124ff11008704876c79ce460e457747a8a31e8777f39`.
Older extension installation `b4c691e19684` has the same bridge hash.
The Python extension revision and native bridge version are separate things.

## Scope and provenance

The source was reconstructed from completed file changes in locally retained
development chat history. Each patch was applied chronologically with exact
line/context and hunk-length checks. The full private recovery audit stays local
under the ignored `diagnostics/` directory; no private chat exports or personal
directory paths are published here.

Only our bridge and protocol definitions are integrated. The recovered launcher
and its old build/install script are not used: the current application uses a
different packaging route, and the old script installed into its source tree.
The CMake file here builds only the bridge and a synthetic protocol test.

The SDK, vendor headers/libraries and executables are excluded from Git. Building
requires a separately licensed local Windows x64 SDK, CMake 3.20 or later and
Visual Studio 2022 C++ Build Tools. The verified toolchain was MSVC 19.44.35229.0,
Windows SDK 10.0.26100.0, C++17, Release, static MSVC runtime.
Use the same SDK/toolchain when reproducing the recorded comparison; another
compiler or SDK can legitimately produce different machine code.

## Build and verify without devices

From the repository root, using the development Python environment:

```powershell
.\.venv\Scripts\python.exe tools/build_bridge.py --output build/bridge-check-NEW
.\.venv\Scripts\python.exe tools/verify_native_bridge.py "C:\Path\App\native\bin\atracsys_bridge.exe" build/bridge-check-NEW/Release/atracsys_bridge.exe
.\.venv\Scripts\python.exe -m pytest tests -q
```

Choose a new output directory every time; existing directories are refused.
For a nonstandard SDK location add `--sdk-root "C:\Path\LicensedSDK"`.
The builder compiles but **never executes the bridge**. CTest executes only a
standalone synthetic memory-layout/serialization test, which does not link to
the SDK. It checks packed sizes, offsets, image regions and the two distinct
timestamp fields. There is no install/deploy step.

The comparison tool uses `pefile`, supplied by the Windows PyInstaller development
dependency. It compares the entire file after masking only explicitly identified
build metadata. A code, data, section, other header or trailing-byte difference
fails verification. It never runs the binaries.

## Runtime integration

Normal extension builds and in-app updates continue to preserve the local bridge
from the existing runtime. They do not compile or substitute this source.
`tools/create_update.py` still packages only the Python update payload; native
source is available through a Git checkout. SDK binaries must never be added to
the public update archive or repository.

This recovery changes no session lifecycle, camera recovery behavior, timestamps
or synchronization semantics. Matching the existing binary does not establish
that it has no bugs or replace a separately authorized hardware acceptance test.
Physical synchronization still requires a hardware trigger. Measurements,
calibration and deployed installations remain untouched.
