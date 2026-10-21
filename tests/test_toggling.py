import csv
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

SCRIPTS = Path(__file__).resolve().parents[1]/'analysis_scripts'
sys.path.insert(0, str(SCRIPTS))
from _lib.toggling import motion, cycles
import toggling_auswertung as report


def observations(t, positions, rotations):
    return SimpleNamespace(times_s=tuple(t), poses={marker: tuple(
        SimpleNamespace(translation_mm=p, rotation=r) for p, r in zip(positions[marker], rotations[marker]))
        for marker in positions})


def test_common_rigid_motion_cancels_with_off_axis_head_point():
    t = np.arange(0., 3., .01)
    n = len(t)
    world_r = Rotation.from_euler('xyz', np.column_stack((t*20, t*12, t*5)), degrees=True).as_matrix()
    world_p = np.column_stack((t*3, t*t, t*.5))
    local = {340: np.array([0., 0., 0.]), 341: np.array([20., 10., 0.]), 342: np.array([40., -3., 5.])}
    positions = {m: world_p+np.einsum('nij,j->ni', world_r, p) for m,p in local.items()}
    rotations = {m: world_r for m in local}
    values, _ = motion(observations(t, positions, rotations), 340, 341, 342, [15., -7., 80.], 0)
    for v in values.values():
        np.testing.assert_allclose(v, 0, atol=1e-10)


def test_rotation_moves_virtual_head_through_lever_arm():
    t = np.arange(100)*.01
    identity = np.tile(np.eye(3), (len(t), 1, 1))
    rotations = {340: identity, 341: identity,
                 342: Rotation.from_euler('z', t*10, degrees=True).as_matrix()}
    positions = {m: np.zeros((len(t), 3)) for m in rotations}
    result, _ = motion(observations(t, positions, rotations), 340, 341, 342, [100, 0, 0], 0)
    expected = np.column_stack((100*np.cos(np.deg2rad(t*10))-100, 100*np.sin(np.deg2rad(t*10)), np.zeros(len(t))))
    np.testing.assert_allclose(result['compensated_screw_point'][:, :3], expected, atol=1e-10)
    np.testing.assert_allclose(result['compensated_screw_point'][:, 5], t*10, atol=1e-10)


def test_cycle_extrema_and_gaps_are_not_filled():
    t = np.arange(0., 20., .01)
    t = t[(t < 9) | (t > 11)]
    y = np.zeros((len(t), 6)); y[:, 0] = .4*np.sin(2*np.pi*t*2)
    result = cycles(t, {key: y for key in report.LABELS}, 2)
    good = [r for r in result['rows'] if r['accepted']]
    assert len(good) >= 20
    assert result['segments'] == 2
    assert all(r['end_s'] < 9 or r['start_s'] > 11 for r in good)
    assert abs(np.median([r['raw']['screw_point'][0] for r in good])-.8) < .02
    assert {r['segment'] for r in good} == {1, 2}


def test_no_periodic_motion_does_not_create_cycles():
    t = np.arange(0., 10., .01)
    result = cycles(t, {'compensated_screw_point': np.zeros((len(t), 6))})
    assert not result['rows']
    assert 'No reliable periodic' in result['reason']


def test_missing_head_coordinates_block_analysis(tmp_path):
    with pytest.raises(ValueError, match='not configured'):
        report.load_settings(tmp_path/'missing.local.json')


def test_settings_override_marker_ids_without_duplicate_keyword_error(tmp_path):
    p = tmp_path/'settings.json'
    p.write_text(json.dumps(dict(reference=299, bone=331, screw=332, screw_point_mm=[0,0,0])))
    settings = report.load_settings(p)
    assert settings['reference'] == 299


def test_complete_report_uses_unique_output_and_preserves_input(tmp_path):
    source = tmp_path/'synthetic.csv'
    fields = ['frame_counter','time_since_start_s','geometry_id','tracking_id','visible','x_mm','y_mm','z_mm',
              *[f'r{i}{j}' for i in range(3) for j in range(3)]]
    with source.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for i in range(1000):
            for marker in (340,341,342):
                writer.writerow(dict(frame_counter=i, time_since_start_s=5+i*.01, geometry_id=marker,
                                     tracking_id=marker, visible=1, x_mm=.4*np.sin(2*np.pi*i*.02) if marker==342 else 0,
                                     y_mm=0,z_mm=0, **{f'r{j}{k}':int(j==k) for j in range(3) for k in range(3)}))
    before = source.read_bytes()
    settings = report.load_settings(tmp_path/'none.json', [0,0,0])
    page = report.analyze(source, settings, tmp_path/'results', 2)
    document = page.read_text(encoding='utf-8')
    record = json.loads((page.parent/'report.json').read_text())
    assert 'Defined screw point relative to bone' in document
    assert record['complete_samples'] == 1000
    assert record['cycles']['rows']
    assert source.read_bytes() == before
    assert report.analyze(source, settings, tmp_path/'results', 2).parent != page.parent
    assert float(next(csv.DictReader((page.parent/'screw_point_motion.csv').open(encoding='utf-8-sig')))['time_s']) == 5


def test_auto_dispatches_toggling_markers_without_running_other_analyses(tmp_path, monkeypatch):
    import auto_auswertung
    source = tmp_path/'markers.csv'
    source.write_text('geometry_id,visible\n340,1\n341,1\n342,1\n')
    calls = []
    monkeypatch.setattr(report, 'main', lambda argv: calls.append(argv) or 0)
    assert auto_auswertung.main([str(source), '--no-open']) == 0
    assert calls == [[str(source), '--no-open']]
