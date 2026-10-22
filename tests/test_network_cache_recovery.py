import ipaddress
import json
from types import SimpleNamespace

import pytest

from atracsys_sync import network


@pytest.mark.parametrize('contents', [b'\x00' * 302, b'', b'{broken', b'[]',
                                     b'{"network":null}', b'{"network":{"interfaces":{}}}', b'\xff'])
def test_corrupt_cache_does_not_prevent_fresh_discovery(tmp_path, monkeypatch, contents):
    sdk = tmp_path / 'sdk'
    (sdk / 'bin').mkdir(parents=True)
    (sdk / 'bin/atracsys_ftk_runtime.dll').touch()
    cache = sdk / 'bin/camera_network.json'
    cache.write_bytes(contents)
    adapters = [{'address': '192.168.0.1', 'mask': '255.255.255.0', 'name': 'Ethernet'}]

    def command(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(adapters) if args[-1] == network.ADAPTER_QUERY else '')

    # No sockets, ICMP, SDK, registry or live devices are used by this test.
    monkeypatch.setattr(network.subprocess, 'run', command)
    monkeypatch.setattr(network, '_ping', lambda address: address if address == '192.168.0.10' else None)
    monkeypatch.setattr(network, '_checked', {})
    network.discover_camera(sdk)
    assert json.loads(cache.read_text())['network']['interfaces'] == [{'address': '192.168.0.10', 'port': 3509}]
    assert json.loads((sdk / 'bin/camera_discovery.json').read_text())['network_settings_changed'] is False
    assert str(sdk.resolve()) in network._checked


def test_valid_bom_cache_keeps_only_usable_local_subnet_candidates(tmp_path):
    cache = tmp_path / 'camera_network.json'
    values = ['192.168.0.10', '192.168.0.1', '192.168.0.0', '192.168.0.255',
              '10.1.1.2', '224.0.0.1', '::1', 'bad']
    cache.write_text(json.dumps({'network': {'interfaces': [None, {}, *({'address': a} for a in values)]}}),
                     encoding='utf-8-sig')
    assert network.cached_addresses(cache, [ipaddress.ip_network('192.168.0.0/24')],
                                    {ipaddress.ip_address('192.168.0.1')}) == {'192.168.0.10'}


def test_missing_cache_is_normal(tmp_path):
    assert network.cached_addresses(tmp_path / 'missing.json', [], set()) == set()
