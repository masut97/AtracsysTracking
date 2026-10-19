import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows registry bridge')
SCRIPT = Path(__file__).resolve().parents[1] / 'tools/zwick_bridge.ps1'


def invoke(root, action, *extra):
    return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass',
        '-File',str(SCRIPT),'-Action',action,'-Root',str(root),*extra],
        capture_output=True,text=True,timeout=15)


def test_shared_name_skips_existing_csv_tra_and_sidecars(tmp_path):
    exports=tmp_path/'Exports'; exports.mkdir()
    (tmp_path/'ExperimentName.txt').write_text('Sample_a_FE_\n')
    (exports/'Sample_a_FE.tra').write_text('old machine data')
    (exports/'Sample_a_FE_rep_001.json').write_text('old camera sidecar')
    result=invoke(tmp_path,'Plan')
    assert result.returncode == 0, result.stderr
    plan=json.loads(result.stdout)
    assert plan['Name']=='Sample_a_FE_rep_002'
    assert plan['Mode']=='FE'
    assert (tmp_path/'ExperimentName.txt').read_text()=='Sample_a_FE_\n'
    assert (exports/'Sample_a_FE.tra').read_text()=='old machine data'


def test_rejected_start_does_not_stop_an_existing_recording(tmp_path):
    import time
    import winreg
    name='Software\\AtracsysTracking\\BridgeTest_'+uuid.uuid4().hex
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,name) as key:
        winreg.SetValueEx(key,'AppReady',0,winreg.REG_DWORD,1)
        winreg.SetValueEx(key,'HeartbeatUnixMs',0,winreg.REG_QWORD,int(time.time()*1000))
        winreg.SetValueEx(key,'RecordingState',0,winreg.REG_SZ,'Aufnahme')
        winreg.SetValueEx(key,'StopRecord',0,winreg.REG_DWORD,0)
    try:
        result=invoke(tmp_path,'Start','-ControlPath','HKCU:\\'+name)
        assert result.returncode != 0
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,name) as key:
            assert winreg.QueryValueEx(key,'StopRecord')[0]==0
        assert not (tmp_path/'ZwickCoupling/session.json').exists()
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,name)


def test_repetition_suffix_does_not_accumulate(tmp_path):
    exports=tmp_path/'Exports';exports.mkdir()
    (tmp_path/'ExperimentName.txt').write_text('Sample_a_FE_rep_001_rep_001\n')
    (exports/'Sample_a_FE.csv').write_text('original')
    (exports/'Sample_a_FE_rep_001.csv').write_text('repeat1')
    result=invoke(tmp_path,'Plan')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['Name']=='Sample_a_FE_rep_002'


def test_finalize_validates_matching_analysis_and_fresh_feedback(tmp_path):
    import winreg
    key_name='Software\\AtracsysTracking\\BridgeTest_'+uuid.uuid4().hex
    key=winreg.CreateKey(winreg.HKEY_CURRENT_USER,key_name);key.Close()
    try:
        exports=tmp_path/'Exports';exports.mkdir()
        coupling=tmp_path/'ZwickCoupling';coupling.mkdir()
        name='Sample_a_FE_'
        session=dict(Name=name,Mode='FE',Pressure='p',Active=True,
            StartedUtc='2026-01-01T00:00:00Z',TRA=str(exports/(name+'.tra')),CSV=str(exports/(name+'.csv')))
        (coupling/'session.json').write_text(json.dumps(session))
        output=exports/(name+'_ROM_auswertung');output.mkdir()
        report_path=output/(name+'_FE_ROM_report.json')
        report=dict(input=session['CSV'],zwick=dict(source=session['TRA']),camera_only=False,quality=dict(status='ok'))
        report_path.write_text(json.dumps(report))
        for suffix in ('UpROM','DnROM','ZeroPoint'):
            (exports/f'FE_p_{suffix}.txt').write_text('180001.25')
        args=('-ControlPath','HKCU:\\'+key_name)
        result=invoke(tmp_path,'Finalize',*args)
        assert result.returncode==0,result.stderr
        report['quality']['status']='critical';report_path.write_text(json.dumps(report))
        assert invoke(tmp_path,'Finalize',*args).returncode != 0
        report['quality']['status']='ok';report['zwick']['source']='wrong.tra';report_path.write_text(json.dumps(report))
        assert invoke(tmp_path,'Finalize',*args).returncode != 0
        report['zwick']['source']=session['TRA'];report_path.write_text(json.dumps(report))
        (exports/'FE_p_UpROM.txt').unlink()
        assert invoke(tmp_path,'Finalize',*args).returncode != 0
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,key_name)


def test_rom_feedback_preserves_zwick_offset_and_output_names(tmp_path, monkeypatch):
    import importlib
    monkeypatch.syspath_prepend(str(SCRIPT.parents[1]/'analysis_scripts'))
    rom=importlib.import_module('rom_auswertung')
    tra=tmp_path/'Example_a_FE_.tra'
    tra.write_bytes(('"Prüfzeit";"Standarddrehmoment Torsion";"Winkel Workaround"\n'
        '"s";"Nm";"°"\n0;-0.1;0\n1;7.5;2\n2;0.1;4\n3;-7.5;6\n4;-0.1;8\n').encode('cp1252'))
    data=rom.load_zwick(tra,'FE',rom.DEFAULT_CONFIG)
    metrics=rom.find_zero_and_rom(data,'FE',True,180000.0)
    paths=rom.write_testxpert_rom_files(data,metrics,'FE',{'legacy_output_directory':str(tmp_path)})
    assert {p.name for p in paths}=={'FE_p_UpROM.txt','FE_p_DnROM.txt','FE_p_ZeroPoint.txt'}
    assert float((tmp_path/'FE_p_UpROM.txt').read_text())==180002.0
    assert float((tmp_path/'FE_p_DnROM.txt').read_text())==180006.0
    assert float((tmp_path/'FE_p_ZeroPoint.txt').read_text())==180002.0
