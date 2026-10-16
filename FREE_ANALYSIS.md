# Free marker analysis

This mode evaluates arbitrary tracked objects. It does not assume vertebrae,
clamps, anatomical roles, applied loads or biomechanical acceptance limits.
Reports are in English. Measurements and profiles remain local.

## Application workflow

1. Open **Freie Zuordnung …** in the Analysis tab.
2. Enter marker IDs and meaningful, distinct names. Geometry filenames supply
   suggestions; IDs can also be read from a current recording or entered manually.
   Verify the actual IDs before recording.
3. Explicitly select the reference marker. It defines the output axes.
4. Add comparisons. Each comparison has an anchor/base and a target, a free name,
   selected quantities, and a rotation axis expressed in reference coordinates.
   Example names may be any objects, not anatomical roles.
5. Set the baseline duration. Record an initially stationary baseline. Zero seconds
   uses the first complete sample; positive durations average position and orientation
   starting at the first complete sample of that comparison.
6. Save. The application selects **Freie Marker-Auswertung**, preserving the existing
   automatic-after-stop checkbox. Enable that checkbox when automatic processing is
   wanted. **CSV frei auswerten …** processes an existing current-format recording.

Profiles can be imported/exported. The active profile is
`analysis_scripts/free_analysis_config.local.json`; source updates preserve it.
Exports use the `.free-profile.json` suffix to avoid replacing measurement metadata.
An offline analysis receives a validated profile copy. Editing the active profile,
running an offline analysis, starting a recording and installing an update are
guarded against conflicting activity. A running free-analysis worker must finish
before the application closes.

## Meaning of a comparison

“Target B relative to anchor A” measures the target's change relative to A,
including compensation for rotation of the anchor. The reference R supplies
coordinate axes; it does not replace the chosen anchor/origin. A or B may also be R.
The recorded marker coordinate systems are used. Optional additional transforms
from other specialized analysis profiles are not silently applied or modified.

For marker pose `(R_i, p_i)`, define:

```
q(t) = R_A(t)^T [p_B(t) - p_A(t)]
S(t) = R_A(t)^T R_B(t)
d_R(t) = R_R(t)^T R_A(t) [q(t) - mean_baseline(q)]
v_R(t) = R_R(t)^T R_A(t) log[S(t) mean_baseline(S)^T]
```

The rotational baseline uses the shared quaternion averaging implementation.
The logarithm is a shortest rotation vector in radians. Rotation outputs convert
to degrees. The profile axis is normalized before its dot product with `v_R`.

Available quantities:

| Quantity | Meaning |
| --- | --- |
| X/Y/Z displacement | Components of `d_R`, in mm |
| Distance from baseline | Length of `d_R`, in mm; unsigned |
| Cumulative relative path | Sum of successive distances between anchor-local `q` points, in mm |
| Projected rotation | Signed component of the shortest rotation vector along the selected reference axis |
| Net rotation magnitude | Length of the shortest rotation vector, from 0 to 180 degrees |

Projected rotation is **not** an independent Euler angle. Net angle magnitude and
distance from baseline are unsigned: their maximum-minus-minimum spans are not
signed joint ROM. Cumulative path can increase while net displacement returns to
zero. Rotation approaching 180 degrees is flagged; multi-turn rotation is not
inferred or unwrapped.

## Missing data and interpretation

Each comparison requires only its reference, anchor and target. A missing marker
in another pair does not discard this pair's available samples. Input acquisition
order is retained. Ambiguous complete-sample timestamps prevent calculating that
comparison. Invalid poses are omitted and reported; no samples are interpolated.

Original `time_since_start_s` values are retained. If only monotonic host times
exist, all comparisons use the first recorded frame as their shared zero. A pair
starting late is explicitly reported. Its baseline starts at its first complete
sample, not at an invented initial pose.

Gaps break plotted lines. Orange time bands identify explicit invisible-marker
flags in received frames; blue identifies absent recorded frames; grey indicates
an unknown or invalid-pose source. Absent recorded frames do not establish a
hardware cause or dirty reflectors. A cumulative path across a gap includes only
the endpoint distance, so it is a lower bound; missing extrema can underestimate
observed spans. A baseline spanning the whole recording is also flagged.

The report says **technical data checks only — no protocol approval**. Existing
ROM-specific clamp/vertebra rules are not applied to arbitrary objects.

## Results and reproducibility

Each execution creates a unique `run_...` subfolder inside `<recording>_free_analysis`
(or the requested output directory). Previous results and input sidecars are not
overwritten. Each run contains an English HTML report, SVG plots, a separate motion
CSV per comparison, a JSON report, and the validated profile snapshot with its
SHA-256 in the report. Printing includes all comparisons, including those currently
hidden by the HTML comparison selector. No external resources are fetched.

Current long-form recordings must contain explicit `geometry_id` values and poses.
Anonymous historical column positions cannot safely identify arbitrary markers;
those files retain their specialized historical analysis workflow.
