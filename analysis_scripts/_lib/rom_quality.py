"""Deterministic, rate-aware ROM curve checks. No training data or device IDs."""
import math
import statistics

VERSION = "2.1"
DEFAULTS = {
    "noise_uncertainty_review": 0.10,
    "noise_uncertainty_reject": 0.15,
    "spike_rom_fraction": 0.05,
    "jump_rom_fraction": 0.20,
    "gap_rom_fraction_reject": 0.05,
    "gap_duration_fraction_reject": 0.03,
    "correlation_reject": 0.35,
    "correlation_review": 0.65,
    "fixture_rotation_floor_deg": 0.02,
    "fixture_translation_floor_mm": 0.0,
    "fixture_noise_multiplier": 6.0,
    "fixture_deficit_reject_tolerance_multiplier": 1.5,
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


def empirical_noise(times, values):
    steps=[b-a for a,b in zip(times,times[1:])]
    positive=[v for v in steps if v>0]
    if not positive:return 0.0
    dt=statistics.median(positive)
    residuals=[]
    for i in range(1,len(values)-1):
        before,after=steps[i-1],steps[i]
        if not (.25*dt<=before<=1.75*dt and .25*dt<=after<=1.75*dt):continue
        weight=before/(before+after)
        residuals.append((values[i]-(1-weight)*values[i-1]-weight*values[i+1])/
                         math.sqrt(1+(1-weight)**2+weight**2))
    center=statistics.median(residuals) if residuals else 0.0
    return 1.4826*statistics.median([abs(v-center) for v in residuals]) if residuals else 0.0


def fixture_checks(times, curves, mode, settings):
    """Operator-defined same-bone attachment checks, with explicit noise tolerances."""
    limits={**DEFAULTS,**settings}
    findings,metrics=[],{}
    angular=mode in {"FE","LB","AR"}
    unit="deg" if angular else "mm"
    floor=limits['fixture_rotation_floor_deg'] if angular else limits['fixture_translation_floor_mm']
    pairs=[('cranial','vertebra_cranial','clamp_up','cranial_fixture_expected'),
           ('caudal','vertebra_caudal','clamp_down','caudal_fixture_expected')]
    for label,bone_name,clamp_name,expected_name in pairs:
        bone=curves.get(bone_name,())
        # Translational marker locations require a full rigid-body lever-arm prediction.
        fixture=curves.get(clamp_name,()) if angular else curves.get(expected_name,())
        if len(bone)!=len(times) or len(fixture)!=len(times):continue
        if not all(math.isfinite(v) for series in (bone,fixture) for v in series):continue
        difference=[a-b for a,b in zip(bone,fixture)]
        tolerance=max(float(floor),limits['fixture_noise_multiplier']*empirical_noise(times,difference),1e-9)
        bone_rom,fixture_rom=span(bone),span(fixture)
        excess=bone_rom-fixture_rom
        metrics[label+'_fixture_comparison']={'vertebra_rom':bone_rom,'expected_fixture_rom':fixture_rom,
            'tolerance':tolerance,'unit':unit,'method':'reference_rotation' if angular else 'rigid_body_lever_arm_prediction'}
        def add(level,code,title,detail,solution):
            findings.append(dict(severity=level,code=code,title=title,detail=detail,solution=solution,intervals=()))
        if excess>tolerance+1e-9:
            add('critical',label+'_exceeds_fixture',label.capitalize()+' vertebra moves more than its fixture',
                f"Excess ROM {excess:.4g} {unit}; allowed noise-aware difference {tolerance:.4g} {unit}. Same-bone rigid attachment is assumed.",
                ("Poor lower-clamp purchase is suspected. Inspect lower fixation and marker contact/anchorage. Correct and repeat the test." if label=='caudal' else
                 "Check soft-tissue or clamp contact with the marker, bone-only anchorage (not across the disc), marker identity and fixation. Correct and repeat the test."))
        elif label=='cranial' and -excess>limits['fixture_deficit_reject_tolerance_multiplier']*tolerance+1e-9:
            add('critical','cranial_fixture_motion_loss','Cranial vertebra does not follow the upper fixture',
                f"ROM deficit {-excess:.4g} {unit} exceeds 1.5 times the {tolerance:.4g} {unit} noise-aware allowance.",
                "Poor clamp purchase or relative fixation movement is suspected. Inspect and secure upper-clamp fixation to the vertebra, then repeat.")
        elif label=='cranial' and -excess>tolerance+1e-9:
            add('warning','borderline_cranial_fixation','Borderline cranial-to-fixture motion loss',
                f"Deficit {-excess:.4g} {unit} is between 1 and 1.5 times the {tolerance:.4g} {unit} noise-aware allowance.",
                "Check upper-clamp purchase and the individual marker traces; repeat if fixation movement is confirmed.")
    # The operator's upper bound also applies to aggregate angular segment ROM.
    if angular and all(len(curves.get(k,()))==len(times) for k in ('segmental','clamp_segmental')):
        y,c=curves['segmental'],curves['clamp_segmental']
        tolerance=max(float(floor),limits['fixture_noise_multiplier']*empirical_noise(times,[a-b for a,b in zip(y,c)]),1e-9)
        metrics['segment_fixture_excess_tolerance']=tolerance
        if span(y)-span(c)>tolerance+1e-9:
            findings.append(dict(severity='critical',code='segment_exceeds_fixture',title='Segment motion exceeds clamp motion',
                detail=f"Excess {span(y)-span(c):.4g} deg exceeds a {tolerance:.4g} deg noise-aware allowance.",
                solution='Inspect marker contact, bone-only attachment, IDs and fixation. Correct the cause and repeat the test.',intervals=()))
    return findings,metrics


def slope_changes(times, curves):
    """Detect abrupt changes between two locally linear, same-direction branches."""
    if len(times)<24 or any(b<=a for a,b in zip(times,times[1:])):return []
    duration=times[-1]-times[0]
    window=max(.15,.02*duration)
    events=[]
    def fit(points):
        x,y=zip(*points);xm,ym=statistics.mean(x),statistics.mean(y)
        xx=sum((v-xm)**2 for v in x);yy=sum((v-ym)**2 for v in y)
        if xx<=0 or yy<=1e-24:return 0.,0.
        slope=sum((a-xm)*(b-ym) for a,b in points)/xx
        residual=sum((b-(ym+slope*(a-xm)))**2 for a,b in points)
        return slope,1-residual/yy
    # Time bins cap work and prevent a high frame rate from counting the same event repeatedly.
    for name in ('clamp_up','clamp_down','vertebra_cranial','vertebra_caudal'):
        values=curves.get(name,())
        if len(values)!=len(times) or span(values)<=1e-12:continue
        bins=[[] for _ in range(min(300,len(times)))]
        for t,y in zip(times,values):bins[min(len(bins)-1,int((t-times[0])/max(duration,1e-12)*len(bins)))].append((t,y))
        points=[(statistics.mean([p[0] for p in group]),statistics.median([p[1] for p in group])) for group in bins if group]
        noise=empirical_noise(times,values);extent=span(values);low,high=min(values),max(values)
        last=-math.inf
        for i,(t,y) in enumerate(points):
            if t-last<window or not low+.1*extent<y<high-.1*extent:continue
            left=[p for p in points[max(0,i-20):i] if t-window<=p[0]<t]
            right=[p for p in points[i+1:i+21] if t<p[0]<=t+window]
            if len(left)<4 or len(right)<4:continue
            a,r1=fit(left);b,r2=fit(right)
            if (a*b>0 and min(r1,r2)>.985 and max(abs(a),abs(b))>3*min(abs(a),abs(b))
                    and abs(a-b)*window>max(.015*extent,8*noise)):
                events.append((name,t-window,t+window,a,b));last=t
    return events


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
    if clamp_span < 1e-12:
        add("critical", "clamp_rom_too_small", "Fixture motion is unresolved",
            "The clamp curve is constant; applied motion cannot be verified from this trace.",
            "Check fresh marker poses, direction, export precision and the load trace. Verify resolution before repeating.")
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
    attachment_findings,attachment_metrics=fixture_checks(times,curves,mode,settings)
    findings.extend(attachment_findings);metrics.update(attachment_metrics)
    changes=slope_changes(times,curves)
    metrics['sudden_slope_change_count']=len(changes)
    if changes:
        add('critical','sudden_slope_change','Abrupt change in an otherwise linear slope',
            f"{len(changes)} slope changes occur away from ordinary turning points: "+', '.join(dict.fromkeys(c[0] for c in changes))+'.',
            'Inspect dirty/damaged fiducials, marker orientation and visibility. Also check the load trace for a real actuator/load change; correct the cause and repeat.',
            [(a,b) for _,a,b,_,_ in changes])
    return findings, metrics
