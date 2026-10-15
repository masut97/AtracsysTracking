import importlib
import math
from pathlib import Path

import pytest


@pytest.fixture
def rom(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "analysis_scripts"))
    return importlib.import_module("rom_auswertung")


def fixture(rom, ratio=0.9, missing=0, registration=0.2):
    times = tuple(i * 0.05 for i in range(401))
    curve = tuple(5 * math.sin(2 * math.pi * time / 10) for time in times)
    poses = {299: (rom.RomPose(((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0, 0, 0), registration),)}
    tracking = rom.RomTrackingData(times, poses, len(times) * 5, missing, "synthetic")
    curves = {"segmental": tuple(ratio * value for value in curve), "clamp_segmental": curve}
    machine = rom.RomZwickData(times, curve, curve, curve, "moment", "angle", "Nm", "deg", Path("synthetic.tra"))
    return tracking, curves, machine


@pytest.mark.parametrize("ratio,rating,code", [
    (0.9, "good", None), (0.6, "good", None),
    (0.4, "good", None), (1.2, "good", None), (1.5,"medium","excess_motion_transfer"),
])
def test_quality_rates_smooth_measurements_and_explains_slip(rom, ratio, rating, code):
    tracking, curves, machine = fixture(rom, ratio)
    findings, metrics = rom.evaluate_quality(tracking, curves, "FE", machine, False, rom.DEFAULT_CONFIG)
    summary = rom.quality_summary(findings)
    assert summary["rating"] == rating
    assert metrics["vertebra_to_clamp_ratio"] == pytest.approx(ratio)
    if code:
        assert code in {finding.code for finding in findings}
        assert summary["hints"]
    else:
        assert not findings


@pytest.mark.parametrize("missing,registration,code", [
    (40, 0.2, "incomplete_marker_samples"), (0, 2.5, "registration_error"),
])
def test_missing_markers_or_bad_registration_cannot_be_rated_good(rom, missing, registration, code):
    tracking, curves, machine = fixture(rom, missing=missing, registration=registration)
    findings, _ = rom.evaluate_quality(tracking, curves, "FE", machine, False, rom.DEFAULT_CONFIG)
    assert code in {finding.code for finding in findings}
    assert rom.quality_summary(findings)["rating"] == "bad"


def test_camera_trace_approval_is_scoped_and_missing_required_load_data_fails(rom):
    tracking, curves, _ = fixture(rom)
    findings, _ = rom.evaluate_quality(tracking, curves, "FE", None, False, rom.DEFAULT_CONFIG, zwick_expected=False)
    assert rom.quality_summary(findings)["label"] == "Approved"
    assert "camera_only_analysis" in {finding.code for finding in findings}
    findings, _ = rom.evaluate_quality(tracking, curves, "FE", None, False, rom.DEFAULT_CONFIG)
    assert rom.quality_summary(findings)["rating"] == "bad"
    assert "missing_zwick_data" in {finding.code for finding in findings}


@pytest.mark.parametrize("frequency",[11,64,333])
def test_smooth_multiple_cycles_approved_at_different_sample_rates(rom,frequency):
    times=tuple(i/frequency for i in range(20*frequency+1))
    clamp=tuple(5*math.sin(2*math.pi*t/10) for t in times)
    curves={"clamp_segmental":clamp,"segmental":tuple(.3*v for v in clamp)}
    data=rom.RomTrackingData(times,{},len(times),0,"synthetic")
    findings,_=rom.evaluate_quality(data,curves,"FE",None,True,rom.DEFAULT_CONFIG,False)
    assert rom.quality_summary(findings)['label']=='Approved'


@pytest.mark.parametrize("kind,code",[("clock","nonmonotonic_time"),("spike","rom_spike"),("gap","peak_data_gap"),("noise","unresolved_signal_to_noise")])
def test_definite_signal_faults_reject_with_actions(rom,kind,code):
    data,curves,_=fixture(rom)
    times=list(data.times_s);segment=list(curves['segmental'])
    if kind=='clock':times[90]=times[89]
    elif kind=='spike':segment[50]+=4
    elif kind=='noise':segment=[.02*v+.02*math.sin(i*2.3) for i,v in enumerate(segment)]
    else:
        times=times[:20]+times[50:]
        segment=segment[:20]+segment[50:]
        curves['clamp_segmental']=curves['clamp_segmental'][:20]+curves['clamp_segmental'][50:]
    curves['segmental']=tuple(segment)
    tracking=rom.RomTrackingData(tuple(times),data.poses,len(times),0,'synthetic')
    findings,_=rom.evaluate_quality(tracking,curves,'FE',None,True,rom.DEFAULT_CONFIG,False)
    summary=rom.quality_summary(findings)
    assert summary['label']=='Not approved' and summary['action']=='REPEAT TEST'
    assert any(f.code==code and f.solution for f in findings)


def test_short_unimportant_high_rate_gap_does_not_reject(rom):
    times=tuple(i/333 for i in range(6661) if i not in range(200,208))
    clamp=tuple(5*math.sin(2*math.pi*t/10) for t in times)
    data=rom.RomTrackingData(times,{},len(times),0,'synthetic')
    findings,_=rom.evaluate_quality(data,{'segmental':clamp,'clamp_segmental':clamp},'FE',None,True,rom.DEFAULT_CONFIG,False)
    assert rom.quality_summary(findings)['label']=='Approved'


def test_wrong_load_limits_reject_even_when_camera_curve_is_smooth(rom):
    data,curves,machine=fixture(rom)
    findings,_=rom.evaluate_quality(data,curves,'FE',machine,True,rom.DEFAULT_CONFIG)
    assert any(f.code=='load_limit_not_reached' for f in findings)
    assert rom.quality_summary(findings)['repeat_required']


def test_expected_load_limits_pass_and_scope_is_not_downgraded(rom):
    from dataclasses import replace
    data,curves,machine=fixture(rom)
    machine=replace(machine,loads=tuple(1.5*v for v in machine.loads))
    findings,_=rom.evaluate_quality(data,curves,'FE',machine,True,rom.DEFAULT_CONFIG)
    assert rom.quality_summary(findings)['label']=='Approved'


def test_plot_contains_all_english_problems_and_solutions_without_clipping(rom,tmp_path):
    data,curves,_=fixture(rom)
    for key in ('clamp_up','clamp_down','vertebra_cranial','vertebra_caudal'):curves[key]=curves['segmental']
    findings=[rom.QualityFinding('critical',f'problem{i}',f'Problem {i}','A '*140,'Check fixation and repeat the test.') for i in range(7)]
    output=tmp_path/'quality.svg'
    rom.render_figure(output,'synthetic.csv',data,curves,'FE',[0,0,1],rom.DEFAULT_CONFIG['markers'],None,None,findings,{},False,rom.DEFAULT_CONFIG,[])
    import xml.etree.ElementTree as ET
    svg=ET.fromstring(output.read_text(encoding='utf-8'))
    content=output.read_text(encoding='utf-8')
    assert 'Not approved — REPEAT TEST' in content and 'Problem 6' in content
    assert content.count('SOLUTION:')==7 and 'QUALITÄT' not in content
    assert float(svg.attrib['height'])>1000


def test_invalid_pose_rows_are_reported_instead_of_silently_approved(rom):
    from dataclasses import replace
    data,curves,machine=fixture(rom)
    findings,_=rom.evaluate_quality(replace(data,invalid_samples=1),curves,'FE',machine,False,rom.DEFAULT_CONFIG)
    assert any(f.code=='invalid_input_data' for f in findings)
    assert rom.quality_summary(findings)['label']=='Not approved'


def test_no_usable_input_writes_a_visible_repeat_result(rom,tmp_path):
    import json
    source=tmp_path/'synthetic_a_FE.csv'
    source.write_text('not,a,valid,tracking,file\n')
    output=tmp_path/'output'
    assert rom.main([str(source),'--ohne-zwick','--no-open','--output-dir',str(output)])==0
    report=json.loads(next(output.glob('*_report.json')).read_text(encoding='utf-8'))
    assert report['segmental_rom'] is None
    assert report['quality']['label']=='Not approved'
    assert 'REPEAT TEST' in next(output.glob('*.svg')).read_text(encoding='utf-8')


@pytest.mark.parametrize('invisible',[False,True])
def test_current_loader_preserves_clock_faults_and_counts_fully_invisible_frames(rom,tmp_path,invisible):
    import csv
    source=tmp_path/'synthetic.csv'
    fields=['shared_sample_index','time_since_start_s','geometry_id','visible','x_mm','y_mm','z_mm',*rom.common.ROTATION_COLUMNS]
    with source.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for frame,time in enumerate([0.,.1,.05]):
            for marker in (299,301,302,311,314):
                row=dict(shared_sample_index=frame,time_since_start_s=time,geometry_id=marker,
                         visible=0 if invisible and frame==2 else 1,x_mm=0,y_mm=0,z_mm=0)
                row.update(zip(rom.common.ROTATION_COLUMNS,[1,0,0,0,1,0,0,0,1]));writer.writerow(row)
    data=rom.load_tracking(source,[299,301,302,311,314])
    assert data.times_s==((0.,.1) if invisible else (0.,.1,.05))
    assert data.incomplete_samples==(1 if invisible else 0)
