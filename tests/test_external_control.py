from pathlib import Path
import pytest

from atracsys_sync.external_control import CommandGate, RegistryControl, integer, recording_target, registry_path, reported_state


class MemoryRegistry:
    HKEY_CURRENT_USER=1; KEY_READ=1; KEY_WRITE=2
    REG_SZ=1; REG_DWORD=4; REG_QWORD=11
    def __init__(self): self.data={}
    def CreateKeyEx(self,*_args): return self
    def OpenKey(self,*_args): return self
    def __enter__(self): return self
    def __exit__(self,*_args): pass
    def QueryValueEx(self,_key,name):
        if name not in self.data: raise FileNotFoundError(name)
        return self.data[name]
    def SetValueEx(self,_key,name,_zero,kind,value): self.data[name]=(value,kind)


def test_registry_migrates_frequency_without_changing_commands_or_name():
    r=MemoryRegistry()
    r.data={'RecordingFrequencyHz':(100,4),'StartRecord':(1,4),'RecordingName':('existing',1)}
    c=RegistryControl(r'Software\Test\Control',registry=r)
    assert r.data['RecordingFrequencyHz']==('100',1)
    assert r.data['StartRecord']==(1,4)
    assert c.values()['RecordingName']=='existing'
    assert c.values()['StopRecord']==0
    c.write_frequency(80)
    assert r.data['RecordingFrequencyHz']==('80',1)
    c.write_status('Gespeichert',last_recording='example.csv')
    assert r.data['AppReady']==(1,4)
    c.write_status('Bereit')
    assert r.data['LastRecording'][0]=='example.csv'
    c.write_app_ready(False)
    assert r.data['AppReady']==(0,4)


def test_stop_wins_even_with_broken_start_and_frequency():
    gate=CommandGate()
    assert gate.action({'StopRecord':1,'StartRecord':'broken','RecordingFrequencyHz':'broken'},True)=='stop'


def test_start_is_never_replayed_after_failure_or_stop():
    gate=CommandGate()
    assert gate.action({'StartRecord':1},False)=='held'  # stale startup command
    assert gate.action({'StartRecord':0},False)=='idle'
    assert gate.action({'StartRecord':1},False)=='start'
    for busy in (False,True,False):
        assert gate.action({'StartRecord':1},busy)=='held'
    assert gate.action({'StopRecord':1,'StartRecord':1},True)=='stop'
    assert gate.action({'StartRecord':1},False)=='held'
    gate.action({'StartRecord':0},False)
    assert gate.action({'StartRecord':1},False)=='start'


def test_legacy_zero_stops_recording_and_preparation():
    for busy in (True,):
        gate=CommandGate()
        gate.action({'StartRecord':0},False)
        gate.action({'StartRecord':1},False)
        assert gate.action({'StartRecord':0},busy)=='stop'
        assert gate.action({'StartRecord':0},busy)=='idle'


@pytest.mark.parametrize('value',['0x64','100 Hz','1.5','',-1,True,336])
def test_frequency_rejects_ambiguous_or_out_of_range_values(value):
    with pytest.raises(ValueError): integer(value,'Hz',1,335)


@pytest.mark.parametrize('prefix',['HKCU\\','HKEY_CURRENT_USER\\','HKCU:\\',''])
def test_control_path_can_be_copied_or_edited(prefix):
    assert registry_path(prefix+r'Software\Example\Control')==r'Software\Example\Control'


@pytest.mark.parametrize('path',[r'HKLM\Software\Example\Control',r'Software',r'Software\Example\\Control','Software/Example/Control'])
def test_rejects_unintended_registry_locations(path):
    with pytest.raises(ValueError): registry_path(path)


@pytest.mark.parametrize('name',['','../escape','CON','aux.txt','bad:name','bad\\name','bad.'])
def test_recording_name_rejected_before_creating_files(tmp_path,name):
    with pytest.raises(ValueError): recording_target(name,tmp_path/'new')
    assert not (tmp_path/'new').exists()


def test_real_measurements_and_sidecars_are_not_overwritten_or_renamed(tmp_path):
    for suffix in ('.csv','.json','.scripts.json','.analysis.log'):
        original=tmp_path/('sample'+suffix)
        original.write_bytes(b'preserve')
        with pytest.raises(FileExistsError): recording_target('sample',tmp_path)
        assert original.read_bytes()==b'preserve'
        original.unlink()
    (tmp_path/'sample.tra').write_bytes(b'machine data')
    name,target=recording_target('sample.tra',tmp_path)
    assert name=='sample' and target==tmp_path.resolve()
    assert (tmp_path/'sample.tra').read_bytes()==b'machine data'


def test_relative_export_path_is_rejected(tmp_path):
    with pytest.raises(ValueError): recording_target('sample','relative')


def test_disabled_registry_never_touches_windows():
    c=RegistryControl(r'Software\Example\Control',False)
    c.write_frequency(50); c.write_status('Bereit'); c.write_start_record(True)
    assert not c.available and c.values()=={}


def test_registry_status_failure_cannot_interrupt_file_finalization():
    r=MemoryRegistry()
    c=RegistryControl(r'Software\Test\Control',registry=r)
    def denied(*_args): raise PermissionError('write denied')
    r.SetValueEx=denied
    # These are called by the inherited recorder after it has started or saved.
    c.write_start_record(False)
    c.write_status('Gespeichert',last_recording='completed.csv')
    assert c.last_error=='write denied'


def test_cancelled_preparation_does_not_report_previous_measurement_as_saved():
    from types import SimpleNamespace
    previous=SimpleNamespace(state='Gestoppt',csv_path='previous.csv',last_error='')
    assert reported_state(previous,False,cancelled=True)=='Gestoppt'
    assert reported_state(previous,True)=='Vorbereitung'
    assert reported_state(previous,False)=='Gespeichert'
    assert reported_state(previous,False,error='disk full')=='Fehler'
