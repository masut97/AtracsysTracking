"""Point-based screw/bone kinematics and gap-aware cyclic measurements."""
import numpy as np
from scipy.signal import butter, detrend, find_peaks, sosfiltfilt, welch
from scipy.spatial.transform import Rotation


def observation_gaps(times, received_times, unseen_events, offset=0.):
    """Classify open time intervals without rescanning every frame per gap."""
    times = np.asarray(times)
    intervals = np.diff(times)
    if not len(intervals):
        return []
    indices = np.flatnonzero(intervals > 1.75*np.median(intervals))
    if not len(indices):
        return []
    received = np.sort(np.asarray(received_times, dtype=float)+offset)
    unseen = {r+offset for r, _ in unseen_events}
    hidden = np.fromiter((r in unseen for r in received), dtype=np.int64, count=len(received))
    hidden_prefix = np.concatenate(([0], np.cumsum(hidden)))
    # Bounds deliberately exclude both endpoints, matching the original strict
    # comparisons. Sorting is only an index for classification, not a data repair.
    starts = np.searchsorted(received, times[indices], side='right')
    ends = np.searchsorted(received, times[indices+1], side='left')
    return [(float(times[i]), float(times[i+1]),
             'No recorded camera frame' if end == start else 'Marker not recognized'
             if hidden_prefix[end]-hidden_prefix[start] == end-start else 'Gap origin unknown')
            for i, start, end in zip(indices, starts, ends)]


def motion(data, reference, bone, screw, point_mm, baseline_seconds=1.):
    """Return screw point, bone origin and bone-compensated screw point curves.

    The screw point is supplied in the RECORDED screw-marker coordinate system.
    Compensation uses full rigid transformations, never position subtraction.
    Compensated components use the mean baseline bone orientation in reference
    coordinates. Rotations are shortest rotation-vector components in degrees.
    """
    t = np.asarray(data.times_s, dtype=float)
    point = np.asarray(point_mm, dtype=float)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ValueError('A finite XYZ screw point in recorded marker coordinates is required.')
    if not np.isfinite(baseline_seconds) or baseline_seconds < 0:
        raise ValueError('Baseline duration must be finite and nonnegative.')
    if len(t) < 8 or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        raise ValueError('At least eight complete samples with increasing original timestamps are required.')
    count = max(1, int(np.searchsorted(t, baseline_seconds, side='right')))
    gaps = np.flatnonzero(np.diff(t) > 1.75 * np.median(np.diff(t)))
    if len(gaps):
        count = min(count, int(gaps[0]) + 1)
    arrays = {}
    for marker in (reference, bone, screw):
        poses = data.poses[marker]
        arrays[marker] = (np.array([p.translation_mm for p in poses]),
                          np.array([p.rotation for p in poses]))
    rp, rr = arrays[reference]
    bp, br = arrays[bone]
    sp, sr = arrays[screw]
    head = sp + np.einsum('nij,j->ni', sr, point)

    def relative(p, r, anchor_p, anchor_r):
        return (np.einsum('nji,nj->ni', anchor_r, p-anchor_p),
                np.einsum('nji,njk->nik', anchor_r, r))

    head_ref, screw_ref = relative(head, sr, rp, rr)
    bone_ref, bone_rotation_ref = relative(bp, br, rp, rr)
    head_bone, screw_bone = relative(head, sr, bp, br)
    basis = Rotation.from_matrix(bone_rotation_ref[:count]).mean().as_matrix()

    def change(p, r, basis=np.eye(3)):
        translation = (p - np.mean(p[:count], axis=0)) @ basis.T
        r0 = Rotation.from_matrix(r[:count]).mean().as_matrix()
        delta = np.einsum('nij,jk->nik', r, r0.T)
        rotation = Rotation.from_matrix(delta).as_rotvec(degrees=True) @ basis.T
        return np.column_stack((translation, rotation))

    curves = {'screw_point': change(head_ref, screw_ref),
              'bone': change(bone_ref, bone_rotation_ref),
              'compensated_screw_point': change(head_bone, screw_bone, basis)}
    return curves, count


def cycles(t, curves, expected_frequency=None):
    """Tolerate bounded short sampling interruptions, segment longer gaps.

    Cycle boundaries are optical timing estimates, not machine cycle counters.
    Store both raw sampled extrema and explicitly labelled low-pass extrema.
    """
    t = np.asarray(t, dtype=float)
    if expected_frequency is not None and (not np.isfinite(expected_frequency) or expected_frequency <= 0):
        raise ValueError('Expected frequency must be finite and positive.')
    if len(t) < 32 or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        return {'reason': 'Insufficient samples for cycle detection.', 'rows': []}
    fs = 1. / np.median(np.diff(t))
    # Bootstrap frequency estimation across only short interruptions. The final
    # segmentation is tightened to the measured period before any cycle export.
    bootstrap_limit = max(1.75/fs, .05)
    breaks = np.flatnonzero(np.diff(t) > bootstrap_limit) + 1
    segments = np.split(np.arange(len(t)), breaks)
    longest = max(segments, key=len)
    if len(longest) < 32:
        return {'reason': 'No sufficiently long continuous segment; restore marker visibility/acquisition.', 'rows': []}
    # Frequency identification uses a single uninterrupted region only.
    grid = np.arange(t[longest[0]], t[longest[-1]], 1/fs)
    data = curves['compensated_screw_point']
    best = None
    lower, upper = ((.7*expected_frequency, 1.3*expected_frequency) if expected_frequency
                    else (.3, min(12., .15*fs)))
    for channel in range(6):
        y = np.interp(grid, t[longest], data[longest, channel])
        f, power = welch(detrend(y), fs=fs, nperseg=min(len(grid), 8192))
        selected = np.flatnonzero((f >= lower) & (f <= upper) & (f < fs/8))
        if not len(selected) or np.ptp(y) < 1e-8:
            continue
        peak = selected[np.argmax(power[selected])]
        broad = (f >= .2) & (f < .45*fs)
        score = np.sum(power[max(1, peak-1):peak+2])/(np.sum(power[broad])+1e-30)
        if best is None or score > best[0]:
            best = (float(score), channel, float(f[peak]))
    if best is None or best[0] < .25:
        return {'reason': 'No reliable periodic screw/bone signal. Check loading and tracking; no cycles fabricated.', 'rows': []}
    score, channel, frequency = best
    if fs/frequency < 20:
        return {'reason': 'Insufficient temporal resolution: at least 20 nominal samples per cycle are required.', 'rows': []}
    gap_limit = max(1.75/fs, min(.05, .05/frequency))
    peak_support_limit = max(1.1/fs, .02/frequency)
    minimum_coverage = .90
    breaks = np.flatnonzero(np.diff(t) > gap_limit)+1
    segments = np.split(np.arange(len(t)), breaks)
    cutoff = min(3*frequency, .4*fs)
    lowpass = butter(4, cutoff, fs=fs, output='sos')
    timing_filter = butter(3, [.7*frequency, 1.3*frequency], btype='bandpass', fs=fs, output='sos')
    rows = []
    number = 0
    rejected = 0
    for segment_index, indices in enumerate(segments):
        times = t[indices]
        if len(indices) < 32 or times[-1]-times[0] < 4/frequency:
            continue
        grid = np.arange(times[0], times[-1], 1/fs)
        sampled = {key: np.column_stack([np.interp(grid, times, v[indices, j]) for j in range(6)])
                   for key, v in curves.items()}
        if len(grid) < 32:
            continue
        filtered = {key: sosfiltfilt(lowpass, v, axis=0) for key, v in sampled.items()}
        timing = sosfiltfilt(timing_filter, sampled['compensated_screw_point'][:, channel])
        if np.ptp(timing) < 1e-8:
            continue
        peaks, _ = find_peaks(timing, distance=max(1, int(.65*fs/frequency)), prominence=.15*np.ptp(timing))
        for a, b in zip(peaks[:-1], peaks[1:]):
            number += 1
            start, end = grid[a], grid[b]
            left, right = np.searchsorted(times, (start, end), side='left')
            raw_indices = indices[left:right]
            coverage = min(1., len(raw_indices)/(fs*(end-start)))
            reasons = []
            if not .7/frequency <= end-start <= 1.3/frequency:
                reasons.append('Cycle timing exclusion')
            if not (start >= times[0]+1/frequency and end <= times[-1]-1/frequency):
                reasons.append('Segment edge exclusion')
            if b-a < 8 or len(raw_indices) < 8:
                reasons.append('Insufficient observations')
            if coverage < minimum_coverage:
                reasons.append('Observation coverage exclusion')
            accepted = not reasons
            row = dict(cycle=number, segment=segment_index+1, start_s=float(start), end_s=float(end),
                       accepted=bool(accepted), reason='; '.join(reasons), observation_coverage=float(coverage))
            if accepted:
                row['extrema'] = {}
                for method, samples, sample_times in (
                        ('raw', curves['compensated_screw_point'][raw_indices], t[raw_indices]),
                        ('filtered', filtered['compensated_screw_point'][a:b], grid[a:b])):
                    extrema = {}
                    for name, select in (('min', np.argmin), ('max', np.argmax)):
                        selected = select(samples, axis=0)
                        extrema[name] = samples[selected, np.arange(6)].tolist()
                        extrema[name+'_time_s'] = sample_times[selected].tolist()
                    row['extrema'][method] = extrema
                peak_times = np.array([row['extrema']['filtered'][name+'_time_s'] for name in ('min', 'max')])
                raw_times = t[raw_indices]
                positions = np.searchsorted(raw_times, peak_times)
                before = raw_times[np.clip(positions-1, 0, len(raw_times)-1)]
                after = raw_times[np.clip(positions, 0, len(raw_times)-1)]
                distance = np.minimum(abs(before-peak_times), abs(after-peak_times))
                row['maximum_peak_support_distance_s'] = float(np.max(distance))
                if np.max(distance) > peak_support_limit:
                    row.update(accepted=False, reason='Peak observation support exclusion')
                    row.pop('extrema')
                else:
                    row['raw'] = {key: np.ptp(v[raw_indices], axis=0).tolist() for key, v in curves.items()}
                    row['filtered'] = {key: np.ptp(v[a:b], axis=0).tolist() for key, v in filtered.items()}
            if not row['accepted']:
                rejected += 1
            rows.append(row)
    return dict(frequency_hz=frequency, sampling_frequency_hz=float(fs), timing_channel=int(channel),
                spectral_concentration=score, lowpass_cutoff_hz=cutoff, filter_order=4,
                zero_phase=True, segments=len(segments), rejected_cycles=rejected, rows=rows,
                maximum_observation_interval_s=float(gap_limit), minimum_observation_coverage=minimum_coverage,
                maximum_peak_support_distance_s=float(peak_support_limit),
                tolerated_short_intervals=int(np.sum((np.diff(t) > 1.75/fs) & (np.diff(t) <= gap_limit))),
                long_observation_gaps=len(breaks),
                reason='' if any(r['accepted'] for r in rows) else 'No complete reliable cycles; inspect loading and tracking.')
