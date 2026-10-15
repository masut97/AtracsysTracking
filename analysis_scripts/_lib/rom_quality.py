"""Deterministic, rate-aware ROM curve checks. No training data or device IDs."""
import math
import statistics

VERSION = "2.0"
DEFAULTS = {
    "noise_uncertainty_review": 0.10,
    "noise_uncertainty_reject": 0.15,
    "spike_rom_fraction": 0.05,
    "jump_rom_fraction": 0.20,
    "gap_rom_fraction_reject": 0.05,
    "gap_duration_fraction_reject": 0.03,
    "correlation_reject": 0.35,
    "correlation_review": 0.65,
}


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def span(values):
    return max(values) - min(values) if values else 0.0


def correlation(left, right):
    a, b = statistics.mean(left), statistics.mean(right)
    xx = sum((v-a)**2 for v in left)
    yy = sum((v-b)**2 for v in right)
    if xx * yy <= 1e-24:
        return None
    return sum((x-a)*(y-b) for x,y in zip(left,right)) / math.sqrt(xx*yy)


def branch_reversal(times, segment, clamp):
    """Largest opposed excursion inside loading branches, on a rate-neutral time grid.

    Use at most 100 time bins, retaining their medians; skip 10% at each branch
    end to avoid penalizing ordinary rounded extrema or small phase lag.
    """
    duration = times[-1]-times[0]
    if duration <= 0 or any(b<=a for a,b in zip(times,times[1:])):
        return 0.0
    bins = [[] for _ in range(min(100,len(times)))]
    for t,y,c in zip(times,segment,clamp):
        index=min(len(bins)-1,int((t-times[0])/duration*len(bins)))
        bins[index].append((y,c))
    pairs=[(statistics.median([p[0] for p in group]),statistics.median([p[1] for p in group]))
           for group in bins if group]
    y,c=zip(*pairs)
    # Detect every substantial reversal; repeated cycles must not be treated as
    # one monotonic branch between the global minimum and global maximum.
    ends=[0]
    direction=1
    candidate=0
    for i,value in enumerate(c):
        if direction*(value-c[candidate])>=0:
            candidate=i
        elif direction*(c[candidate]-value)>.2*span(c):
            if candidate>ends[-1]:ends.append(candidate)
            direction=-direction
            candidate=i
    ends.append(len(c)-1)
    maximum=0.0
    for start,end in zip(ends,ends[1:]):
        if end-start<6 or abs(c[end]-c[start]) < .2*span(c):
            continue
        trim=max(1,round((end-start)*.1))
        values=list(y[start+trim:end-trim+1])
        sign=1 if c[end]>c[start] else -1
        best=sign*values[0]
        for value in values:
            best=max(best,sign*value)
            maximum=max(maximum,best-sign*value)
    return maximum/max(span(segment),1e-12)


def screen(times, curves, mode, settings):
    limits = {**DEFAULTS, **settings}
    findings, metrics = [], {"quality_rule_version": VERSION}

    def add(severity, code, title, detail, solution, intervals=()):
        findings.append(dict(severity=severity, code=code, title=title,
                             detail=detail, solution=solution, intervals=tuple(intervals)))

    segment = list(curves.get("segmental", ()))
    clamp = list(curves.get("clamp_segmental", ()))
    if (len(times) < 10 or len(segment) != len(times) or len(clamp) != len(times)
            or not all(math.isfinite(v) for series in (times, segment, clamp) for v in series)):
        add("critical", "invalid_curve", "Missing or invalid curve data",
            "At least ten finite, aligned five-marker samples are required.",
            "Restore visibility of all five markers, check marker assignment and repeat the test.")
        return findings, metrics
    steps = [b-a for a,b in zip(times,times[1:])]
    positive = [v for v in steps if v > 0]
    backwards = sum(v < 0 for v in steps)
    duplicates = sum(v == 0 for v in steps)
    metrics.update(backward_timestamps=backwards, duplicate_timestamps=duplicates)
    if backwards or duplicates:
        add("critical", "nonmonotonic_time", "Ambiguous acquisition time",
            f"{backwards} backwards and {duplicates} repeated timestamps; temporal order is not verified.",
            "Check timestamp generation and acquisition logs. Recover original ordered frames or repeat the test.")
    if not positive:
        return findings, metrics
    dt = statistics.median(positive)
    duration = max(times)-min(times)
    segment_span, clamp_span = span(segment), span(clamp)
    ratio = segment_span / clamp_span if clamp_span > 1e-12 else None
    metrics.update(vertebra_rom=segment_span, clamp_rom=clamp_span,
                   vertebra_to_clamp_ratio=ratio, median_sample_interval_s=dt,
                   median_sampling_hz=1/dt)
    minimum_clamp = float(settings.get("minimum_clamp_rom_rotation_deg" if mode in {"FE","LB","AR"}
                                        else "minimum_clamp_rom_translation_mm", .5 if mode in {"FE","LB","AR"} else .1))
    if clamp_span < minimum_clamp:
        add("critical", "clamp_rom_too_small", "Insufficient applied motion",
            f"Clamp ROM is {clamp_span:.4g}; the configured minimum is {minimum_clamp:.4g}.",
            "Check actuator movement, selected direction and marker IDs; apply the intended loading cycle and repeat.")
    if segment_span < 1e-12:
        add("critical", "unresolved_motion", "Segment motion is unresolved",
            "The segment curve is constant; a ROM measurement cannot be distinguished from frozen or rounded data.",
            "Check fresh marker poses and export precision. Verify resolution using a reference motion, then repeat.")
        return findings, metrics
    # Uneven timestamp spacing must not be mistaken for high-frequency noise.
    residuals, local = [], []
    for i in range(1,len(times)-1):
        before, after = steps[i-1], steps[i]
        if not (.25*dt <= before <= 1.75*dt and .25*dt <= after <= 1.75*dt):
            continue
        weight = before / (before+after)
        predicted = segment[i-1]*(1-weight)+segment[i+1]*weight
        residual = segment[i]-predicted
        residuals.append(residual/math.sqrt(1+(1-weight)**2+weight**2))
        local.append((i,residual))
    center = statistics.median(residuals) if residuals else 0.0
    noise = 1.4826 * statistics.median([abs(v-center) for v in residuals]) if residuals else 0.0
    uncertainty = 3.92 * noise / segment_span
    metrics.update(empirical_noise=noise, estimated_rom_noise_fraction=uncertainty,
                   noise_estimation_samples=len(residuals))
    if len(residuals) >= 10 and uncertainty > limits['noise_uncertainty_review']:
        severe = uncertainty > limits['noise_uncertainty_reject']
        add("critical" if severe else "warning", "unresolved_signal_to_noise",
            "Motion is too small relative to noise" if severe else "Borderline signal resolution",
            f"Empirical noise allowance is {uncertainty:.1%} of ROM (noise {noise:.4g}); this is a screening estimate, not calibrated accuracy.",
            "Check marker visibility and stability, camera distance and the selected axis. Verify resolution; do not interpret noise as construct motion.")
    # A single-sample extreme can dominate raw max-minus-min even when global MAD is small.
    spike_intervals = []
    for i,residual in local:
        if (abs(residual) > max(limits['spike_rom_fraction']*segment_span,8*noise)
                and (segment[i]-segment[i-1])*(segment[i+1]-segment[i]) < 0):
            spike_intervals.append((times[i-1],times[i+1]))
    if spike_intervals:
        add("critical", "rom_spike", "Isolated spike can distort ROM",
            f"{len(spike_intervals)} local reversals exceed both 5% of ROM and the adaptive noise threshold.",
            "Inspect the highlighted samples and marker fixation/visibility. Do not smooth away the fault; repeat the test.",spike_intervals)
    regular = [(i,abs(segment[i+1]-segment[i])) for i,v in enumerate(steps) if .25*dt <= v <= 1.75*dt]
    typical_step = statistics.median([v for _,v in regular]) if regular else 0
    jumps = [(times[i],times[i+1]) for i,v in regular
             if v > max(limits['jump_rom_fraction']*segment_span,12*typical_step,8*noise)]
    if jumps:
        add("critical", "abrupt_motion", "Abrupt motion or tracking jump",
            f"{len(jumps)} steps exceed 20% of ROM and 12 times a typical movement step.",
            "Check for marker obstruction, loose fixation or an actuator disturbance; correct the cause and repeat the test.",jumps)
    gap_intervals, borderline_gaps = [], []
    bounds = [(min(values),max(values),span(values)) for values in (segment,clamp)]
    for i,step in enumerate(steps):
        if step <= 1.75*dt:
            continue
        impact = max(abs(segment[i+1]-segment[i])/segment_span,
                     abs(clamp[i+1]-clamp[i])/max(clamp_span,1e-12))
        near_peak = any(min(values[i],values[i+1]) < low+.15*extent
                        or max(values[i],values[i+1]) > high-.15*extent
                        for values,(low,high,extent) in zip((segment,clamp),bounds))
        # Count elapsed time and probable ROM impact, not a fixed number of lost frames.
        if impact > limits['gap_rom_fraction_reject'] or (near_peak and step > max(3*dt,duration*limits['gap_duration_fraction_reject'])):
            gap_intervals.append((times[i],times[i+1]))
        elif near_peak and impact > .025 and step > 3*dt:
            borderline_gaps.append((times[i],times[i+1]))
    metrics.update(peak_data_gap_count=len(gap_intervals), borderline_gap_count=len(borderline_gaps))
    if gap_intervals:
        add("critical", "peak_data_gap", "Unobserved motion or peak",
            f"{len(gap_intervals)} gaps span substantial movement or a peak region; the extrema cannot be verified.",
            "Keep every marker visible, check acquisition/network interruptions and repeat the complete test.",gap_intervals)
    elif borderline_gaps:
        add("warning", "borderline_gap", "Small gap near a peak",
            "The estimated ROM impact is between the acceptance and rejection limits.",
            "Inspect the highlighted interval against the load trace; repeat if a peak cannot be verified.",borderline_gaps)
    match = correlation(segment,clamp)
    metrics['clamp_segment_correlation'] = match
    reversal=branch_reversal(times,segment,clamp)
    metrics['loading_branch_reversal_fraction']=reversal
    # Axial compression can creep and couple to rotation during a fixture ramp.
    # A reversal alone is not an adequate rejection rule in that mode.
    if mode != "AC" and reversal>.05:
        add("critical", "loading_branch_reversal", "Non-monotonic motion during loading",
            f"An opposed segment excursion reaches {reversal:.1%} of ROM inside a loading branch, away from normal turning points.",
            "Inspect actuator oscillation, marker fixation and the load trace. Correct the disturbance and repeat the test.")
    elif mode != "AC" and reversal>.035:
        add("warning", "borderline_branch_reversal", "Small reversal during loading",
            f"Opposed excursion: {reversal:.1%} of ROM, between the 3.5% and 5% screening limits.",
            "Check the corresponding load branch and marker traces; repeat if the disturbance changes the measured ROM.")
    # Low transfer ratio alone is not proof of slip: stiff constructs and fixture compliance exist.
    if match is not None and clamp_span >= minimum_clamp and uncertainty < .15:
        if match < limits['correlation_reject']:
            add("critical", "incoherent_motion", "Segment and clamp motion are inconsistent",
                f"Curve correlation is {match:.2f}; the applied motion is not coherently reproduced.",
                "Check marker identity, axis/sign, fixation and loading direction. Correct the setup and repeat the test.")
        elif match < limits['correlation_review']:
            add("warning", "borderline_motion_transfer", "Borderline motion consistency",
                f"Curve correlation is {match:.2f}; hysteresis, drift or fixation changes may contribute.",
                "Compare both loading branches and individual markers with the load trace; repeat if inconsistent.")
    if ratio is not None and ratio > 1.30:
        add("warning", "excess_motion_transfer", "Segment motion exceeds fixture motion",
            f"Segment/clamp ROM ratio is {ratio:.2f}; geometry and motion coupling must be checked.",
            "Check coordinate axes, lever arms and individual marker traces. Repeat if marker movement or a setup error is confirmed.")
    return findings, metrics
