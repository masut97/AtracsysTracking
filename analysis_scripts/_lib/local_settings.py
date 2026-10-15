"""Device calibration is supplied locally and never bundled in source updates."""
import json
from pathlib import Path

import numpy as np


def load_transform(marker):
    path = Path(__file__).resolve().parent.parent / "marker_transforms.local.json"
    if not path.is_file():
        return None, None
    value = json.loads(path.read_text(encoding="utf-8-sig")).get(str(marker))
    if value is None:
        return None, None
    rotation, origin = np.asarray(value["rotation"], dtype=float), np.asarray(value["origin_mm"], dtype=float)
    if (rotation.shape != (3, 3) or origin.shape != (3,) or not np.isfinite(rotation).all()
            or not np.isfinite(origin).all() or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(rotation), 1.0)):
        raise ValueError("Ungültige lokale Marker-Kalibrierung.")
    return rotation, origin
