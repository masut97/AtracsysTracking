"""Snapshot local coordinate definitions into NEW recording metadata only."""
import hashlib
import json
from pathlib import Path


def snapshot(project_root, geometry_names):
    root = Path(project_root).resolve()
    result = {'version': 1, 'geometry_sha256': {}}
    try:
        config = json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
        directory = Path(config.get('geometry_directory') or 'geometries')
        directory = (directory if directory.is_absolute() else root/directory).resolve(strict=True)
        for name in geometry_names:
            name = str(name)
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('Geometry metadata must contain plain filenames.')
            path = (directory/name).resolve(strict=True)
            if path.parent != directory or not path.is_file():
                raise ValueError('Geometry escaped the configured directory.')
            result['geometry_sha256'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        path = root/'analysis_scripts/toggling_config.local.json'
        if path.is_file():
            setup = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(setup, dict):
                raise ValueError('Toggling settings must be an object.')
            result['toggling_setup'] = setup
    except (OSError, ValueError, TypeError) as error:
        # Acquisition remains available; a point analysis requiring provenance
        # must reject this incomplete snapshot rather than invent a coordinate frame.
        result['error'] = str(error)
    return result
