import importlib
import math
from pathlib import Path

import pytest


@pytest.fixture
def rom(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'analysis_scripts'))
    return importlib.import_module('rom_auswertung')


def test_explicit_export_profile_excludes_only_identified_result_row(rom, tmp_path):
    path=tmp_path/'Sample_a_FE.tra'
    path.write_bytes(('"Prüfzeit";"Standarddrehmoment Torsion";"Winkel Workaround";"Zyklus"\n'
        '"s";"Nm";"°";""\n0;99;0;2\n0;0;3;0\n0.02;1;4;0\n'
        '0.02;2;5;0\n0.04;-3;6;1\n').encode('cp1252'))
    raw=path.read_bytes()
    cfg=dict(rom.DEFAULT_CONFIG,zwick_export_leading_result_row=True)
    data=rom.load_zwick(path,'FE',cfg)
    assert data.times_s==(0.0,0.02,0.02,0.04)
    assert data.loads==(0.0,1.0,2.0,-3.0)
    assert data.removed_leading_result_rows==1
    assert data.repeated_time_samples==1
    assert path.read_bytes()==raw
    default_data=rom.load_zwick(path,'FE',rom.DEFAULT_CONFIG)
    assert default_data.loads[0]==99


def test_backward_clock_is_never_sorted_or_repaired(rom,tmp_path):
    path=tmp_path/'Sample_a_FE.tra'
    path.write_bytes(('"Prüfzeit";"Standarddrehmoment Torsion";"Winkel Workaround"\n'
        '"s";"Nm";"°"\n0;0;0\n0.02;1;1\n0.01;2;2\n').encode('cp1252'))
    data=rom.load_zwick(path,'FE',rom.DEFAULT_CONFIG)
    assert data.times_s==(0.0,0.02,0.01)


@pytest.mark.parametrize('backward',[False,True])
def test_repeated_timestamp_is_distinguished_from_clock_reversal(rom,backward):
    times=tuple(i*.05 for i in range(401))
    values=tuple(5*math.sin(2*math.pi*t/10) for t in times)
    machine_times=list(times)
    machine_times[200]=machine_times[199]-(.01 if backward else 0)
    machine=rom.RomZwickData(tuple(machine_times),values,values,values,'load','angle','Nm','deg',Path('sample.tra'),0,0 if backward else 1)
    tracking=rom.RomTrackingData(times,{},len(times)*5,0,'synthetic')
    findings,_=rom.evaluate_quality(tracking,{'segmental':values,'clamp_segmental':values},'FE',machine,False,rom.DEFAULT_CONFIG)
    assert ('invalid_load_time' in {f.code for f in findings}) is backward
