"""Typed HKCU control protocol and one-shot command decisions, without Qt/devices."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile
import time

DEFAULT_PATH = r'Software\AtracsysTracking\Control'


def registry_path(value):
    value = str(value).strip().strip('\\')
    for prefix in ('HKEY_CURRENT_USER\\', 'HKCU\\', 'HKCU:\\'):
        if value.upper().startswith(prefix):
            value = value[len(prefix):]
            break
    if (not value.lower().startswith('software\\') or len(value.split('\\')) < 3
            or any(not part.strip() for part in value.split('\\'))
            or any(ord(c) < 32 for c in value) or '/' in value or ':' in value):
        raise ValueError('Bitte einen eigenen HKCU\\Software\\…\\Control-Pfad angeben.')
    return value


def integer(value, name, minimum=0, maximum=1):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value).strip()):
        raise ValueError(f'{name}: bitte eine ganze Dezimalzahl eingeben.')
    number = int(str(value).strip(), 10)
    if not minimum <= number <= maximum:
        raise ValueError(f'{name}: erlaubt sind {minimum} bis {maximum}.')
    return number


def recording_target(name, directory):
    name = str(name).strip()
    if name.lower().endswith(('.csv', '.tra')):
        name = name[:-4]
    if (not name or name in {'.','..'} or name.endswith(('.', ' '))
            or any(c in name for c in '<>:"/\\|?*') or any(ord(c)<32 for c in name)
            or name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',
                *[f'COM{i}' for i in range(1,10)], *[f'LPT{i}' for i in range(1,10)]}):
        raise ValueError('RecordingName: gültigen Dateinamen ohne Ordner angeben.')
    target = Path(str(directory).strip()).expanduser()
    if not str(directory).strip() or not target.is_absolute():
        raise ValueError('RecordingDirectory: vollständigen absoluten Exportordner angeben.')
    target = target.resolve()
    # Exact base name is essential for matching machine exports; never add suffixes.
    if any(p.suffix.lower() != '.tra' for p in target.glob(name+'.*')):
        raise FileExistsError('Messname bereits vorhanden. Nichts überschrieben: '+name)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=target):
        pass
    return name, target


class RegistryControl:
    """Compatible with the inherited recorder; all frequencies are decimal REG_SZ."""
    def __init__(self, registry_path, enabled=True, default_frequency_hz=100, *, registry=None):
        self.registry_path = globals()['registry_path'](registry_path)
        self.enabled = bool(enabled and (registry is not None or os.name == 'nt'))
        self.default_frequency_hz = integer(default_frequency_hz, 'RecordingFrequencyHz', 1, 10000)
        self._last_error = ''
        if registry is None and self.enabled:
            import winreg
            registry = winreg
        self.registry = registry
        self.ensure_defaults()

    @property
    def available(self):
        return self.enabled

    @property
    def display_path(self):
        return 'HKEY_CURRENT_USER\\'+self.registry_path

    @property
    def last_error(self):
        return self._last_error

    def _write(self, name, value, kind):
        if not self.enabled:
            return
        try:
            r = self.registry
            with r.CreateKeyEx(r.HKEY_CURRENT_USER, self.registry_path, 0, r.KEY_READ | r.KEY_WRITE) as key:
                r.SetValueEx(key, name, 0, kind, value)
            return True
        except OSError as error:
            self._last_error = str(error)
            # Recorder callbacks must still finalize files if status publication
            # loses permission. The controller surfaces this diagnostic separately.
            return False

    def values(self):
        if not self.enabled:
            return {}
        r = self.registry
        with r.OpenKey(r.HKEY_CURRENT_USER, self.registry_path, 0, r.KEY_READ) as key:
            def read(name, default):
                try:
                    return r.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    return default
            return {k:read(k,v) for k,v in self.defaults().items()}

    def defaults(self):
        return dict(StartRecord=0, StopRecord=0, RecordingFrequencyHz=str(self.default_frequency_hz),
            RecordingName='', RecordingDirectory='', AppReady=0, RecordingState='Gestoppt',
            LastRecording='', LastError='', HeartbeatUnixMs=0,
            StartResetToken='', StartResetAck='', StartResetSupported=0)

    def ensure_defaults(self):
        if not self.enabled:
            return
        r = self.registry
        try:
            with r.CreateKeyEx(r.HKEY_CURRENT_USER, self.registry_path, 0, r.KEY_READ | r.KEY_WRITE) as key:
                for name,value in self.defaults().items():
                    try:
                        old,kind = r.QueryValueEx(key,name)
                    except FileNotFoundError:
                        kind = r.REG_SZ if isinstance(value,str) else r.REG_QWORD if name=='HeartbeatUnixMs' else r.REG_DWORD
                        r.SetValueEx(key,name,0,kind,value)
                    else:
                        if name=='RecordingFrequencyHz' and kind==r.REG_DWORD:
                            # Lossless migration; malformed existing values are not silently repaired.
                            r.SetValueEx(key,name,0,r.REG_SZ,str(old))
        except OSError as error:
            self._last_error = str(error)

    def write_start_record(self, enabled):
        if self.enabled:
            self._write('StartRecord', int(bool(enabled)), self.registry.REG_DWORD)

    def write_frequency(self, frequency_hz):
        value = integer(frequency_hz, 'RecordingFrequencyHz', 1, 10000)
        if self.enabled:
            self._write('RecordingFrequencyHz', str(value), self.registry.REG_SZ)

    def write_app_ready(self, ready):
        if self.enabled:
            self._write('AppReady', int(bool(ready)), self.registry.REG_DWORD)

    def clear_stop(self):
        if self.enabled:
            self._write('StopRecord', 0, self.registry.REG_DWORD)

    def write_status(self, state, last_recording='', last_error=''):
        if not self.enabled:
            return
        self._last_error = ''
        self.write_app_ready(True)
        r=self.registry
        for name,value,kind in (('RecordingState',state,r.REG_SZ),('LastError',last_error,r.REG_SZ),
                ('HeartbeatUnixMs',int(time.time()*1000),r.REG_QWORD)):
            self._write(name,value,kind)
        if last_recording:
            self._write('LastRecording',str(last_recording),r.REG_SZ)


@dataclass
class CommandGate:
    """Never replay a held start (including after failure); stop always wins."""
    armed: bool = False
    seen_start: bool = False

    def action(self, values, busy):
        # Even an invalid frequency/name must not prevent stopping a session.
        stop = integer(values.get('StopRecord',0), 'StopRecord')
        if stop:
            self.armed=False; self.seen_start=False
            return 'stop'
        start = integer(values.get('StartRecord',0), 'StartRecord')
        if not start:
            self.armed=True
            falling=self.seen_start; self.seen_start=False
            return 'stop' if busy and falling else 'idle'
        self.seen_start=True
        if self.armed and not busy:
            self.armed=False
            return 'start'
        self.armed=False
        return 'held'


def reported_state(stats, pending, cancelled=False, error=''):
    if error or stats.last_error:
        return 'Fehler'
    if pending:
        return 'Vorbereitung'
    if stats.state!='Gestoppt':
        return stats.state
    if cancelled:
        return 'Gestoppt'
    return 'Gespeichert' if stats.csv_path else 'Bereit'
