"""Safety and binary comparison checks using synthetic PE images, no devices."""
import importlib.util
from pathlib import Path
import struct

import pytest


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_tool("build_bridge")
verifier = load_tool("verify_native_bridge")


def synthetic_pe(timestamp=100, rich_key=0x12345678):
    data = bytearray(1536)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 256)
    words = [0x536E6144, 0, 0, 0, 0x01050001, 1]
    struct.pack_into("<6I", data, 128, *(x ^ rich_key for x in words))
    data[152:156] = b"Rich"
    struct.pack_into("<I", data, 156, rich_key)
    data[256:260] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 260, 0x8664, 1, timestamp, 0, 0, 240, 0x22)
    opt = 280
    struct.pack_into("<H", data, opt, 0x20B)
    struct.pack_into("<I", data, opt + 16, 0x1000)
    struct.pack_into("<Q", data, opt + 24, 0x140000000)
    struct.pack_into("<II", data, opt + 32, 4096, 512)
    struct.pack_into("<II", data, opt + 56, 8192, 1024)
    struct.pack_into("<H", data, opt + 68, 3)
    struct.pack_into("<I", data, opt + 108, 16)
    struct.pack_into("<II", data, opt + 112 + 6 * 8, 0x1080, 28)
    struct.pack_into("<8sIIIIIIHHI", data, 520, b".text\0\0\0", 512, 4096, 512, 1024, 0, 0, 0, 0, 0x60000020)
    struct.pack_into("<IIHHIIII", data, 1152, 0, timestamp, 0, 0, 13, 0, 0, 0)
    return data


def test_only_documented_build_metadata_is_ignored(tmp_path):
    a, b = tmp_path / "original.exe", tmp_path / "rebuilt.exe"
    a.write_bytes(synthetic_pe())
    b.write_bytes(synthetic_pe(timestamp=200, rich_key=0x87654321))
    report = verifier.compare(a, b)
    assert report["reference"]["sha256"] != report["candidate"]["sha256"]
    assert report["equivalent_except_build_metadata"]


@pytest.mark.parametrize("offset", [1100, 530, 350, 1024])
def test_code_section_and_other_header_changes_are_rejected(tmp_path, offset):
    a, b = tmp_path / "a.exe", tmp_path / "b.exe"
    data = synthetic_pe()
    a.write_bytes(data)
    data[offset] ^= 1
    b.write_bytes(data)
    assert not verifier.compare(a, b)["equivalent_except_build_metadata"]


def test_build_refuses_existing_directory_before_running_commands(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An existing directory must not trigger any build command")
    monkeypatch.setattr(builder.subprocess, "run", forbidden)
    sentinel = tmp_path / "preserve.txt"
    sentinel.write_text("keep")
    with pytest.raises(ValueError):
        builder.build(tmp_path)
    assert sentinel.read_text() == "keep"


def test_build_refuses_output_inside_native_sources(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A source directory must not become a build target")
    monkeypatch.setattr(builder.subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        builder.build(builder.ROOT / "native" / "must-not-be-created")
