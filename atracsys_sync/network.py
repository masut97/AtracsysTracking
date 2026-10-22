"""Read-only camera address discovery for the independent Windows package."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
import time

_lock = threading.Lock()
_checked = {}
LOGGER = logging.getLogger(__name__)


def cached_addresses(path, networks, local):
    """A damaged discovery cache must not disable the native acquisition path."""
    try:
        cached = json.loads(path.read_text(encoding='utf-8-sig'))
        entries = cached['network']['interfaces']
        if not isinstance(entries, list):
            raise ValueError('interfaces must be a list')
    except FileNotFoundError:
        return set()
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        LOGGER.warning('Ignoring unreadable camera discovery cache %s: %s', path, error)
        return set()
    addresses = set()
    for entry in entries:
        try:
            address = ipaddress.ip_address(entry['address'])
        except (ValueError, TypeError, KeyError):
            continue
        if (address.version == 4 and not address.is_multicast and address not in local
                and any(address in network and address not in
                        (network.network_address, network.broadcast_address) for network in networks)):
            addresses.add(str(address))
    return addresses

ADAPTER_QUERY = r'''
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$rows = @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | Where-Object {
    $_.NetworkInterfaceType -eq 'Ethernet' -and $_.OperationalStatus -ne 'NotPresent' -and
    $_.Description -notmatch 'Virtual|VPN|Miniport|Filter|Scheduler|Bluetooth|Hyper-V'
} | ForEach-Object {
    $adapter = $_
    $_.GetIPProperties().UnicastAddresses | Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' } | ForEach-Object {
        @{ name = $adapter.Name; address = $_.Address.IPAddressToString; mask = $_.IPv4Mask.IPAddressToString }
    }
})
ConvertTo-Json -InputObject $rows -Compress
'''


def scan_addresses(adapters, limit=1024):
    local = {ipaddress.ip_address(row['address']) for row in adapters}
    targets = set()
    networks = []
    for row in adapters:
        address = ipaddress.ip_address(row['address'])
        if address.is_loopback or address.is_link_local:
            continue
        network = ipaddress.ip_network(f"{address}/{row['mask']}", strict=False)
        networks.append(network)
        block = network if network.num_addresses <= 256 else ipaddress.ip_network(f'{address}/24', strict=False)
        targets.update(str(host) for host in block.hosts() if host not in local)
    return sorted(targets, key=ipaddress.ip_address)[:limit], networks, local


def arp_addresses(text, networks, local):
    found = set()
    for candidate in re.findall(r'^\s*(\d+\.\d+\.\d+\.\d+)\s+[0-9a-f-]{17}\s', text, re.I | re.M):
        address = ipaddress.ip_address(candidate)
        if address not in local and not address.is_multicast and any(
                address in network and address not in (network.broadcast_address, network.network_address)
                for network in networks):
            found.add(candidate)
    return found


def _ping(address):
    api = ctypes.WinDLL('iphlpapi.dll')
    api.IcmpCreateFile.restype = ctypes.c_void_p
    api.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
    api.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                               ctypes.c_ushort, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_uint32, ctypes.c_uint32]
    handle = api.IcmpCreateFile()
    if handle == ctypes.c_void_p(-1).value:
        return None
    try:
        reply = ctypes.create_string_buffer(256)
        destination = int.from_bytes(ipaddress.ip_address(address).packed, 'little')
        return address if api.IcmpSendEcho(handle, destination, None, 0, None, reply, len(reply), 250) else None
    finally:
        api.IcmpCloseHandle(handle)


def discover_camera(sdk_root):
    root = Path(sdk_root).resolve()
    if not (root / 'bin/atracsys_ftk_runtime.dll').exists():
        return
    with _lock:
        now = time.monotonic()
        if now - _checked.get(str(root), -100) < 30:
            return
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        result = subprocess.run([str(windows / 'WindowsPowerShell/v1.0/powershell.exe'),
                                 '-NoProfile', '-Command', ADAPTER_QUERY], capture_output=True,
                                encoding='utf-8', errors='replace', timeout=20, creationflags=flags)
        if result.returncode:
            raise RuntimeError('Ethernetanschlüsse konnten nicht für die Kameraerkennung gelesen werden.')
        adapters = json.loads(result.stdout.strip() or '[]')
        targets, networks, local = scan_addresses(adapters)
        arp = subprocess.run([str(windows / 'arp.exe'), '-a'], capture_output=True,
                             encoding='utf-8', errors='replace', timeout=5, creationflags=flags)
        candidates = arp_addresses(arp.stdout, networks, local)
        with ThreadPoolExecutor(max_workers=24) as pool:
            candidates.update(address for address in pool.map(_ping, targets) if address)
        config_path = root / 'bin/camera_network.json'
        candidates.update(cached_addresses(config_path, networks, local))
        if not candidates:
            raise RuntimeError('Keine erreichbare Kameraadresse am Ethernetanschluss gefunden. Kamera/PoE und die bestehende Netzwerkverbindung prüfen.')
        interfaces = [{'address': address, 'port': 3509} for address in sorted(candidates, key=ipaddress.ip_address)]
        temporary = config_path.with_suffix('.json.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump({'network': {'version': 1, 'interfaces': interfaces}}, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(config_path)
        (root / 'bin/camera_discovery.json').write_text(json.dumps(
            {'method': 'ethernet_arp_icmp_then_atracsys_sdk_enumeration', 'adapters': adapters,
             'candidate_addresses': [entry['address'] for entry in interfaces],
             'network_settings_changed': False}, indent=2), encoding='utf-8')
        _checked[str(root)] = now
