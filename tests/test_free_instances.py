import csv
import importlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from atracsys_sync.marker_selection import visible_marker_choices


def test_live_selection_excludes_invisible_stale_invalid_and_dismissed():
    def state(g, track, identity, visible=True, age=10, valid=True):
        return NS(pose=NS(geometry_id=g, tracking_id=track, valid=valid),
                  identity_id=identity, visible=visible, age_ms=lambda: age)
    states=[state(299,10,1),state(301,20,1),state(301,21,2),
            state(302,30,1,visible=False),state(303,31,1,age=900),
            state(304,32,1,valid=False),state(305,33,1)]
    window=NS(status_store=NS(get=lambda:NS(connected=True)),
              marker_registry=NS(states=lambda:states),_dismissed_geometry_ids={305})
    assert list(visible_marker_choices(window))==['299:10','301:20','301:21']
    window.status_store.get=lambda:NS(connected=False)
    assert visible_marker_choices(window)=={}


@pytest.mark.parametrize('reverse', [False,True])
def test_same_geometry_instances_are_independent_and_never_filled_from_other_track(tmp_path,monkeypatch,reverse):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'analysis_scripts'))
    free=importlib.import_module('free_auswertung')
    source=tmp_path/'instances.csv'
    fields=['frame_counter','computer_monotonic_s','geometry_id','tracking_id','visible',
            'x_mm','y_mm','z_mm',*free.tracking.common.ROTATION_COLUMNS]
    with source.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for i in range(10):
            rows=[]
            for g,track,x in [(299,10,0),(301,20,i),(301,21,3*i)]:
                if i==5 and track==21:
                    continue
                row=dict(frame_counter=i,computer_monotonic_s=100+i*.1,
                         geometry_id=g,tracking_id=track,visible=1,x_mm=x,y_mm=0,z_mm=0)
                row.update(zip(free.tracking.common.ROTATION_COLUMNS,[1,0,0,0,1,0,0,0,1]))
                rows.append(row)
            writer.writerows(reversed(rows) if reverse else rows)
    p=dict(version=1,name='Instances',reference='299:10',baseline_seconds=0,
           markers={'299:10':'Reference','301:20':'First','301:21':'Second'},
           comparisons=[dict(name='Two instances',anchor='301:20',target='301:21',outputs=['tx'],axis=[0,0,1])])
    original=source.read_bytes()
    figure=free.analyze(source,p)
    result=json.loads(figure.with_suffix('.json').read_text())['results'][0]
    assert result['complete_samples']==9
    assert result['incomplete_samples']==1
    assert result['curves']['tx'][-1]==pytest.approx(18)
    assert source.read_bytes()==original
    # A legacy geometry-only selection cannot arbitrarily select one of two instances.
    p.update(reference=299,markers={'299':'Ref','301':'Ambiguous'})
    p['comparisons']=[dict(name='Legacy',anchor=299,target=301,outputs=['tx'],axis=[0,0,1])]
    figure=free.analyze(source,p)
    result=json.loads(figure.with_suffix('.json').read_text())['results'][0]
    assert result.get('complete_samples',0) <= 1
    assert not result['curves']
