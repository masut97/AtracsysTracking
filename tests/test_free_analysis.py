import copy
import csv
import importlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from atracsys_sync.profile_store import save_profile


@pytest.fixture
def free(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'analysis_scripts'))
    return importlib.import_module('free_auswertung')


def profile(free):
    return dict(version=1, name='Arbitrary objects', reference=11, baseline_seconds=0,
                markers={'11':'Reference', '27':'Fixture A', '53':'Part B', '81':'Part C'},
                comparisons=[dict(name='B relative to A', anchor=27, target=53,
                                  outputs=list(free.OUTPUTS), axis=[0,0,1])])


def rz(angle):
    c,s=math.cos(angle),math.sin(angle)
    return ((c,-s,0.),(s,c,0.),(0.,0.,1.))


def dataset(free,kind='translation'):
    rom=free.tracking
    identity=rz(0.)
    poses={11:[],27:[],53:[]}
    times=tuple(i*.1 for i in range(31))
    for t in times:
        if kind=='rigid':
            rotation=rz(t*.2);a=(t,2*t,-t)
            arm=free.relative_motion.__globals__['vector'](rotation,(25.,-10.,0.))
            b=tuple(x+y for x,y in zip(a,arm))
            values={11:(identity,(0,0,0)),27:(rotation,a),53:(rotation,b)}
        elif kind=='rotation':
            values={11:(identity,(0,0,0)),27:(identity,(0,0,0)),53:(rz(t*.2),(20,0,0))}
        elif kind=='rotated_reference':
            values={11:(rz(math.pi/2),(100,100,0)),27:(identity,(0,0,0)),53:(identity,(t,0,0))}
        else:
            values={11:(identity,(0,0,0)),27:(identity,(0,0,0)),53:(identity,(t,0,0))}
        for key,(rotation,point) in values.items():
            poses[key].append(rom.RomPose(rotation,point))
    return rom.RomTrackingData(times,{key:tuple(value) for key,value in poses.items()},len(times),0,'synthetic')


def test_relative_motion_cancels_common_rigid_rotation_and_translation(free):
    curves,_,_=free.relative_motion(dataset(free,'rigid'),11,27,53,0,[0,0,1])
    assert all(max(abs(v) for v in values)<1e-10 for values in curves.values())


def test_reference_axes_rotate_components_without_changing_distance(free):
    curves,_,_=free.relative_motion(dataset(free,'rotated_reference'),11,27,53,0,[0,0,1])
    assert curves['tx'][-1]==pytest.approx(0,abs=1e-12)
    assert curves['ty'][-1]==pytest.approx(-3)
    assert curves['displacement'][-1]==pytest.approx(3)
    assert curves['path'][-1]==pytest.approx(3)


def test_rotation_has_correct_units_axis_sign_and_shortest_magnitude(free):
    data=dataset(free,'rotation')
    curves,_,_=free.relative_motion(data,11,27,53,0,[0,0,-1])
    assert curves['rotation_axis'][-1]==pytest.approx(-math.degrees(.6))
    assert curves['rotation_total'][-1]==pytest.approx(math.degrees(.6))
    assert curves['displacement'][-1]==pytest.approx(0)


def test_common_world_movement_does_not_change_relative_quantities(free):
    from dataclasses import replace
    data=dataset(free,'rotation');maths=free.tracking.common
    world=rz(.7);shift=(50.,-13.,6.)
    vector=free.relative_motion.__globals__['vector']
    poses={key:tuple(free.tracking.RomPose(maths.multiply(world,p.rotation),tuple(a+b for a,b in zip(vector(world,p.translation_mm),shift))) for p in values) for key,values in data.poses.items()}
    original=free.relative_motion(data,11,27,53,0,[0,0,1])[0]
    changed=free.relative_motion(replace(data,poses=poses),11,27,53,0,[0,0,1])[0]
    for key in original:assert changed[key]==pytest.approx(original[key],abs=1e-10)


@pytest.mark.parametrize('change',[
    lambda p:p.update(reference=99),
    lambda p:p['comparisons'][0].update(target=27),
    lambda p:p['comparisons'][0].update(axis=[0,0,0]),
    lambda p:p['comparisons'][0].update(outputs=[]),
    lambda p:p['comparisons'][0].update(outputs=[{}]),
    lambda p:p.update(baseline_seconds=float('nan')),
    lambda p:p['markers'].update({'53':'Fixture A'}),
])
def test_invalid_profile_does_not_replace_existing_profile(free,tmp_path,change):
    path=tmp_path/'free_analysis_config.local.json'
    original=profile(free);save_profile(path,original,free.validate_profile)
    before=path.read_bytes();changed=copy.deepcopy(original);change(changed)
    with pytest.raises(ValueError):save_profile(path,changed,free.validate_profile)
    assert path.read_bytes()==before
    assert not list(tmp_path.glob('.free-profile-*'))


def write_csv(free,path,missing=False,backwards=False,elapsed=True):
    fields=['frame_counter','time_since_start_s','computer_monotonic_s','geometry_id','visible','x_mm','y_mm','z_mm',*free.tracking.common.ROTATION_COLUMNS]
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for frame in range(31):
            t=frame*.1+2
            if backwards and frame==15:t-=1
            for marker in (11,27,53,81):
                row=dict(frame_counter=frame,time_since_start_s=t if elapsed else '',computer_monotonic_s=1000+t,
                         geometry_id=marker,visible=0 if missing and marker==81 and 10<=frame<15 else 1,
                         x_mm=frame*.1 if marker in (53,81) else 0,y_mm=0,z_mm=0)
                row.update(zip(free.tracking.common.ROTATION_COLUMNS,[1,0,0,0,1,0,0,0,1]));writer.writerow(row)


@pytest.mark.parametrize('elapsed',[True,False])
def test_multiple_comparisons_preserve_time_origin_and_independent_visibility(free,tmp_path,elapsed):
    source=tmp_path/'objects.csv';write_csv(free,source,missing=True,elapsed=elapsed)
    p=profile(free);p['comparisons'].append(dict(name='C relative to A',anchor=27,target=81,outputs=['tx','path'],axis=[0,0,1]))
    raw=source.read_bytes();sidecar=source.with_suffix('.json');sidecar.write_bytes(b'original metadata')
    figure=free.analyze(source,p);report=json.loads(figure.with_suffix('.json').read_text())
    assert source.read_bytes()==raw and sidecar.read_bytes()==b'original metadata'
    first,second=report['results']
    assert first['complete_samples']==31 and second['complete_samples']==26
    assert first['times_s'][0]==pytest.approx(2 if elapsed else 0)
    assert second['times_s'][0]==first['times_s'][0]
    assert second['gaps'] and second['issues']
    assert second['gap_bands'][0][2]=='Marker not recognized'
    assert report['protocol_approval'] is False
    assert 'NO PROTOCOL APPROVAL' in figure.read_text()
    for svg in figure.parent.glob('*.svg'):ET.parse(svg)
    # Re-evaluation has its own profile snapshot and cannot overwrite the previous result.
    another=free.analyze(source,p)
    assert another.parent!=figure.parent
    assert json.loads((figure.parent/'profile_snapshot.json').read_text())==free.validate_profile(p)


def test_backwards_clock_is_not_sorted_away_or_given_motion_values(free,tmp_path):
    source=tmp_path/'objects.csv';write_csv(free,source,backwards=True)
    figure=free.analyze(source,profile(free));report=json.loads(figure.with_suffix('.json').read_text())
    assert not report['results'][0]['curves']
    assert 'strictly increasing' in report['results'][0]['issues'][0]


def test_failed_atomic_replacement_preserves_profile_and_removes_only_own_temp(free,tmp_path,monkeypatch):
    from atracsys_sync import profile_store
    path=tmp_path/'profile.json';path.write_bytes(b'previous')
    def fail(*_args):raise OSError('simulated replacement failure')
    monkeypatch.setattr(profile_store.os,'replace',fail)
    with pytest.raises(OSError):save_profile(path,profile(free),free.validate_profile)
    assert path.read_bytes()==b'previous' and set(tmp_path.iterdir())=={path}


def test_returning_to_start_has_zero_net_displacement_but_nonzero_path(free):
    from dataclasses import replace
    data=dataset(free)
    positions=[0.,1.,2.,1.,0.]
    poses={key:values[:5] for key,values in data.poses.items()}
    poses[53]=tuple(free.tracking.RomPose(rz(0),(x,0,0)) for x in positions)
    curves,_,_=free.relative_motion(replace(data,times_s=data.times_s[:5],poses=poses),11,27,53,0,[0,0,1])
    assert curves['displacement'][-1]==pytest.approx(0)
    assert curves['path'][-1]==pytest.approx(4)


def test_rotation_branch_limit_is_reported_instead_of_guessing_turn_count(free):
    from dataclasses import replace
    data=dataset(free)
    poses=dict(data.poses);poses[53]=tuple(free.tracking.RomPose(rz(math.radians(179)*i/30),(0,0,0)) for i in range(31))
    curves,_,branch=free.relative_motion(replace(data,poses=poses),11,27,53,0,[0,0,1])
    assert branch and curves['rotation_total'][-1]==pytest.approx(179)


def test_absent_reference_produces_explicit_failure_without_fabricated_values(free,tmp_path):
    source=tmp_path/'objects.csv';write_csv(free,source)
    p=profile(free);p['markers']['99']='Absent reference';p['reference']=99
    figure=free.analyze(source,p)
    result=json.loads(figure.with_suffix('.json').read_text())['results'][0]
    assert not result['curves'] and 'reference 99' in result['issues'][0]


def test_profile_file_survives_source_update(free,tmp_path):
    # The updater must preserve this new local configuration like existing calibrations.
    from test_updater import runtime, archive, REVISION
    from atracsys_sync.updater import install_update
    app=runtime(tmp_path);path=app/'analysis_scripts/free_analysis_config.local.json'
    save_profile(path,profile(free),free.validate_profile)
    before=path.read_bytes();update,digest=archive(tmp_path)
    result=install_update(app,update,tmp_path/'versions',digest,REVISION)
    assert (result/'analysis_scripts/free_analysis_config.local.json').read_bytes()==before
    assert path.read_bytes()==before
