"""Independent protocol fixtures: attachment, lever arms and loss provenance."""
import csv
import importlib
import math
from pathlib import Path

import pytest


@pytest.fixture
def rom(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'analysis_scripts'))
    return importlib.import_module('rom_auswertung')


def curves(upper_gain=1.,lower_gain=0.,extra_upper=0.):
    times=tuple(i*.025 for i in range(801))
    upper=tuple(5*math.sin(2*math.pi*t/10) for t in times)
    bone=tuple((upper_gain+extra_upper/5)*v for v in upper)
    lower=tuple(lower_gain*v for v in upper)
    zero=tuple(0. for _ in times)
    return times,dict(clamp_up=upper,clamp_down=zero,vertebra_cranial=bone,
                      vertebra_caudal=lower,segmental=tuple(a-b for a,b in zip(bone,lower)),clamp_segmental=upper)


def test_matching_same_bone_markers_pass(rom):
    times,signals=curves()
    data=rom.RomTrackingData(times,{},len(times),0,'synthetic')
    findings,metrics=rom.evaluate_quality(data,signals,'FE',None,True,rom.DEFAULT_CONFIG,False)
    assert rom.quality_summary(findings)['label']=='Approved'
    assert metrics['cranial_fixture_comparison']['tolerance']>=.02


@pytest.mark.parametrize('kwargs,code',[
    ({'upper_gain':.8},'cranial_fixture_motion_loss'),
    ({'upper_gain':1.1},'cranial_exceeds_fixture'),
    ({'lower_gain':.03},'caudal_exceeds_fixture'),
])
def test_each_fixation_problem_is_identified_separately(rom,kwargs,code):
    times,signals=curves(**kwargs)
    findings,_=rom.evaluate_quality(rom.RomTrackingData(times,{},len(times),0,'synthetic'),signals,'FE',None,True,rom.DEFAULT_CONFIG,False)
    assert any(f.code==code and f.solution for f in findings)
    assert rom.quality_summary(findings)['action']=='REPEAT TEST'


@pytest.mark.parametrize('extra,expected',[(.0095,'Approved'),(.0125,'Not approved')])
def test_absolute_rotation_tolerance_does_not_use_unstable_near_zero_ratios(rom,extra,expected):
    times,signals=curves(extra_upper=extra)
    findings,_=rom.evaluate_quality(rom.RomTrackingData(times,{},len(times),0,'synthetic'),signals,'FE',None,True,rom.DEFAULT_CONFIG,False)
    assert rom.quality_summary(findings)['label']==expected


def test_translation_prediction_respects_a_rotating_marker_lever_arm(rom):
    times=tuple(i*.02 for i in range(301))
    identity=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))
    pose=lambda rotation,translation:rom.RomPose(rotation,translation)
    poses={299:[],301:[],302:[],311:[],314:[]}
    for t in times:
        angle=.08*math.sin(t)
        rotation=((math.cos(angle),0.,math.sin(angle)),(0.,1.,0.),(-math.sin(angle),0.,math.cos(angle)))
        origin=(0.,0.,math.sin(t))
        arm=rom._matrix_vector(rotation,(30.,0.,0.))
        for marker in poses:
            poses[marker].append(pose(rotation,tuple(a+b for a,b in zip(origin,arm))) if marker==311 else
                                 pose(rotation,origin) if marker==301 else pose(identity,(0.,0.,0.)))
    data=rom.RomTrackingData(times,{k:tuple(v) for k,v in poses.items()},len(times),0,'synthetic')
    signals,_=rom.calculate_motion(data,'AS',[0,0,1],rom.DEFAULT_CONFIG['markers'],1.,1.)
    assert max(abs(a-b) for a,b in zip(signals['vertebra_cranial'],signals['cranial_fixture_expected']))<1e-9
    assert max(abs(a-b) for a,b in zip(signals['vertebra_cranial'],signals['clamp_up']))>.5
    checks,_=rom.curve_quality.fixture_checks(times,signals,'AS',{})
    assert not checks


def write_long(rom,path,missing_frame=False,unseen=False):
    columns=['frame_counter','time_since_start_s','geometry_id','visible','x_mm','y_mm','z_mm',*rom.common.ROTATION_COLUMNS]
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for frame in range(101):
            if missing_frame and frame in range(45,55):continue
            for marker in (299,301,302,311,314):
                row=dict(frame_counter=frame,time_since_start_s=frame*.05,geometry_id=marker,
                         visible=0 if unseen and marker==311 and frame in range(45,55) else 1,x_mm=0,y_mm=0,z_mm=0)
                row.update(zip(rom.common.ROTATION_COLUMNS,[1,0,0,0,1,0,0,0,1]));writer.writerow(row)


@pytest.mark.parametrize('kind',['marker','frame','legacy'])
def test_gap_diagnoses_do_not_conflate_visibility_and_recorded_camera_frames(rom,tmp_path,kind):
    path=tmp_path/'synthetic.csv';write_long(rom,path,kind=='frame',kind=='marker')
    data=rom.load_tracking(path,[299,301,302,311,314])
    if kind=='legacy':
        from dataclasses import replace
        data=replace(data,source_format='legacy_matlab_wide_csv',received_times_s=(),unseen_marker_events=())
        from dataclasses import replace
        data=replace(data,times_s=tuple(t for t in data.times_s if not 2.2<t<2.8))
    y=tuple(math.sin(t) for t in data.times_s)
    findings,_=rom.evaluate_quality(data,{'segmental':y,'clamp_segmental':y},'FE',None,False,rom.DEFAULT_CONFIG,False)
    codes={f.code for f in findings}
    if kind=='marker':assert 'marker_not_recognized' in codes and 'recorded_camera_frame_gap' not in codes
    if kind=='frame':assert 'recorded_camera_frame_gap' in codes and 'marker_not_recognized' not in codes
    if kind=='legacy':assert 'unknown_tracking_gap' in codes and 'marker_not_recognized' not in codes


def test_linear_slope_kink_is_detected_but_a_normal_turning_point_is_not(rom):
    times=tuple(i*.02 for i in range(501))
    kink=tuple(t if t<5 else 5+5*(t-5) for t in times)
    assert rom.curve_quality.slope_changes(times,{'clamp_up':kink})
    smooth=tuple(math.sin(t*math.pi/5) for t in times)
    assert not rom.curve_quality.slope_changes(times,{'clamp_up':smooth})


def test_missing_visibility_flag_is_invalid_input_not_proof_of_occlusion(rom,tmp_path):
    path=tmp_path/'synthetic.csv'
    write_long(rom,path,unseen=True)
    path.write_text(path.read_text().replace(',311,0,',',311,,'))
    data=rom.load_tracking(path,[299,301,302,311,314])
    assert not data.unseen_marker_events
    assert data.invalid_samples==10
