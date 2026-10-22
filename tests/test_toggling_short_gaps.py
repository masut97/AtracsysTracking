import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location('short_gap_cycles', Path(__file__).resolve().parents[1]/'analysis_scripts/_lib/toggling.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def curves(t, frequency=1):
    values = np.zeros((len(t), 6))
    values[:, 4] = .001*t+(.4+.001*t)*np.sin(2*np.pi*t*frequency)
    return {'compensated_screw_point': values}


def test_short_irregular_intervals_recover_known_extrema_without_changing_raw_data():
    t = np.arange(0., 30., .01)
    t += np.random.default_rng(51).uniform(-.003, .003, len(t))
    t = t[np.arange(len(t)) % 25 != 12]
    values = curves(t)
    saved_t, saved_values = t.copy(), values['compensated_screw_point'].copy()
    result = module.cycles(t, values)
    good = [r for r in result['rows'] if r['accepted']]
    assert len(good) >= 24
    assert abs(result['frequency_hz']-1) < .02
    assert result['tolerated_short_intervals'] > 50
    assert result['segments'] == 1
    assert result['maximum_observation_interval_s'] <= .05
    for row in good:
        middle = (row['start_s']+row['end_s'])/2
        expected_range = 2*(.4+.001*middle)
        assert abs(row['raw']['compensated_screw_point'][4]-expected_range) < .012
        assert abs(row['filtered']['compensated_screw_point'][4]-expected_range) < .012
        for name, sign in [('min', -1), ('max', 1)]:
            extrema = row['extrema']['raw']
            at = extrema[name+'_time_s'][4]
            index = np.argmin(abs(t-at))
            assert t[index] == at
            assert saved_values[index, 4] == extrema[name][4]
            assert abs(extrema[name][4]-(.001*middle+sign*(.4+.001*middle))) < .008
    np.testing.assert_array_equal(t, saved_t)
    np.testing.assert_array_equal(values['compensated_screw_point'], saved_values)


def test_long_interruption_still_separates_cycles_and_cannot_be_bridged():
    t = np.arange(0., 30., .01)
    t = t[(t < 14) | (t >= 15.7)]
    result = module.cycles(t, curves(t), 1)
    assert result['segments'] == 2
    assert result['long_observation_gaps'] == 1
    good = [r for r in result['rows'] if r['accepted']]
    assert len(good) > 15
    assert all(r['end_s'] < 14 or r['start_s'] >= 15.7 for r in good)


def test_sparse_cycles_are_rejected_even_when_individual_intervals_are_short():
    t = np.arange(0., 12., .01)
    keep = ~((t >= 3) & (t < 8) & (np.arange(len(t)) % 5 == 2))
    t = t[keep]
    result = module.cycles(t, curves(t), 1)
    middle = [r for r in result['rows'] if 4 < r['start_s'] < r['end_s'] < 7]
    assert middle
    assert all(not r['accepted'] and 'coverage' in r['reason'] for r in middle)
    assert any(r['accepted'] for r in result['rows'])


def test_unobserved_peak_is_excluded_without_rejecting_the_entire_segment():
    t = np.arange(0., 12., .002)
    t = t[(t <= 4.726) | (t >= 4.774)]
    result = module.cycles(t, curves(t), 1)
    assert result['segments'] == 1
    affected = [r for r in result['rows'] if r['start_s'] < 4.75 < r['end_s']]
    assert affected
    assert all(not r['accepted'] and 'Peak observation support' in r['reason'] for r in affected)
    assert any(r['accepted'] for r in result['rows'])


def test_too_few_samples_per_period_do_not_produce_confident_peak_envelopes():
    t = np.arange(0., 12., .01)
    result = module.cycles(t, curves(t, 12), 12)
    assert not result['rows']
    assert '20 nominal samples' in result['reason']
