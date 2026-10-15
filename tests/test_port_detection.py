import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("force_port_test", Path(__file__).resolve().parents[1] / "measurement_scripts/zugkraft.py")
force = importlib.util.module_from_spec(spec)
spec.loader.exec_module(force)


def port(device, vid=0x2341, pid=0x0070, serial="EXAMPLE-USB-SERIAL"):
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial, description="USB serial")


def test_changed_com_number_is_detected():
    assert force.resolve_port({"port": "auto"}, [port("COM17")]) == "COM17"


def test_serial_number_disambiguates_multiple_boards():
    assert force.resolve_port({"port": "auto", "usb_serial": "EXAMPLE-USB-SERIAL"},
                              [port("COM8", serial="OTHER"), port("COM17")]) == "COM17"


def test_unrelated_serial_ports_are_not_opened():
    with pytest.raises(RuntimeError, match="nicht gefunden"):
        force.resolve_port({"port": "auto"}, [port("COM4", vid=None, pid=None)])


def test_ambiguous_boards_require_explicit_selection():
    with pytest.raises(RuntimeError, match="Mehrere"):
        force.resolve_port({"port": "auto"}, [port("COM8", serial="OTHER"), port("COM17")])


def test_explicit_port_remains_supported():
    assert force.resolve_port({"port": "COM4"}, []) == "COM4"
