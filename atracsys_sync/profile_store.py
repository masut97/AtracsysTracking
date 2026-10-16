"""Atomic local profile storage; failed validation never changes existing settings."""
import json
import os
from pathlib import Path
import tempfile


def save_profile(path, profile, validator):
    profile = validator(profile)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.free-profile-',
                                         suffix='.json', encoding='utf-8', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(profile, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return profile
