import hashlib
import json
from pathlib import Path
import sys

import pytest

from atracsys_sync.coordinate_context import snapshot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'analysis_scripts'))
import toggling_auswertung as analysis


def setup(root):
    (root/'geometries').mkdir()
    (root/'analysis_scripts').mkdir()
    (root/'config.json').write_text(json.dumps({'geometry_directory': 'geometries'}))
    geometry = root/'geometries/geometry342.json5'
    geometry.write_text('new coordinate definition')
    digest = hashlib.sha256(geometry.read_bytes()).hexdigest()
    config = {'screw_point_mm': [0,0,0], 'point_name': 'Holder-exit centre',
              'screw_geometry_sha256': digest, 'marker_attachment': 'rigid_screw_shaft'}
    (root/'analysis_scripts/toggling_config.local.json').write_text(json.dumps(config))
    return config


def test_snapshot_is_read_only_and_copies_exact_coordinate_version(tmp_path):
    config = setup(tmp_path)
    before = {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = snapshot(tmp_path, ['geometry342.json5'])
    assert result['geometry_sha256']['geometry342.json5'] == config['screw_geometry_sha256']
    assert result['toggling_setup'] == config
    assert before == {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_geometry_binding_rejects_legacy_and_wrong_coordinate_sidecars(tmp_path):
    config = setup(tmp_path)
    config = analysis.load_settings(recorded=config)
    source = tmp_path/'test.csv'
    with pytest.raises(ValueError, match='not verified'):
        analysis.verify_coordinate_frame(source, config)
    sidecar = source.with_suffix('.json')
    sidecar.write_text(json.dumps({'coordinate_context': {'geometry_sha256': {'geometry342.json5': '0'*64}}}))
    original = sidecar.read_bytes()
    with pytest.raises(ValueError, match='not verified'):
        analysis.verify_coordinate_frame(source, config)
    assert sidecar.read_bytes() == original
    sidecar.write_text(json.dumps({'coordinate_context': snapshot(tmp_path, ['geometry342.json5'])}))
    assert analysis.verify_coordinate_frame(source, config)


def test_recorded_setup_wins_over_current_local_configuration(tmp_path):
    current = tmp_path/'current.json'
    current.write_text(json.dumps({'screw_point_mm': [50, 0, 0]}))
    result = analysis.load_settings(current, recorded={'screw_point_mm': [0,0,0], 'point_name': 'Holder exit'})
    assert result['screw_point_mm'] == [0,0,0]
    assert result['point_name'] == 'Holder exit'


def test_snapshot_failure_is_explicit_without_reading_outside_geometry_directory(tmp_path):
    setup(tmp_path)
    result = snapshot(tmp_path, ['../outside.json5'])
    assert result['error']
    assert not result['geometry_sha256']


def test_setup_length_metadata_is_never_inferred(tmp_path):
    config = analysis.load_settings(tmp_path/'absent.json', [0,0,0])
    assert config.get('free_length_mm') is None
    with pytest.raises(ValueError, match='free_length_mm'):
        analysis.load_settings(recorded={'screw_point_mm': [0,0,0], 'free_length_mm': -1})
