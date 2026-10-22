import importlib.util
from pathlib import Path
import time

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('gap_module', Path(__file__).resolve().parents[1]/'analysis_scripts/_lib/toggling.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def reference(times, received, events, offset):
    result = []
    for i in np.flatnonzero(np.diff(times) > 1.75*np.median(np.diff(times))):
        inside = [r+offset for r in received if times[i] < r+offset < times[i+1]]
        unseen = {r+offset for r, _ in events}
        label = ('No recorded camera frame' if not inside else 'Marker not recognized'
                 if all(r in unseen for r in inside) else 'Gap origin unknown')
        result.append((float(times[i]), float(times[i+1]), label))
    return result


@pytest.mark.parametrize('offset', [0., -17.123456789, 1000.0000001])
def test_index_matches_original_strict_boundaries_duplicates_and_mixed_visibility(offset):
    received = np.arange(1000)*.01
    kept = np.ones(len(received), dtype=bool)
    kept[100:110] = False
    kept[300:320] = False
    kept[700:720] = False
    times = received[kept]+offset
    available = list(received[:100])+list(received[110:])  # first gap has no frames
    available += [received[300], received[319]]  # duplicate received timestamps
    events = [(r, (342,)) for r in received[300:320]]
    available.reverse()  # classification must not depend on storage order
    expected = reference(times, available, events, offset)
    assert {r[2] for r in expected} == {'No recorded camera frame', 'Marker not recognized', 'Gap origin unknown'}
    assert module.observation_gaps(times, available, events, offset) == expected


def test_long_recording_gap_index_has_bounded_runtime():
    # The former nested Python scan requires billions of comparisons here.
    received = np.arange(500_000)*.01
    kept = np.arange(len(received)) % 25 != 12
    times = received[kept]
    events = [(r, (342,)) for r in received[~kept]]
    started = time.monotonic()
    gaps = module.observation_gaps(times, received, events)
    assert len(gaps) == 20_000
    assert all(label == 'Marker not recognized' for _, _, label in gaps)
    assert time.monotonic()-started < 10  # generous allowance for shared CI hosts
