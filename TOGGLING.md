# Toggling analysis

Select **Toggling – Schraube relativ zum Knochen**, or use automatic detection
with reference 340, bone 341 and screw 342. Alternative IDs can be configured
locally. The former 340/341/342 marker-check script remains an advanced manual
tracking check; it is not the point-based toggling analysis.

## Mandatory screw measurement point

Displacement is evaluated at a defined point, such as the screw-head centre or
the screw exit from its holder,
not silently at the optical marker origin. Its XYZ coordinates in mm must be
known in the **recorded screw-marker coordinate system**. For geometries generated
directly from the new STEP assembly coordinates, use that same assembly frame.
Do not mix old calibrated marker coordinates with new CAD coordinates.

Save `analysis_scripts/toggling_config.local.json` locally, for example:

```json
{
  "reference": 340,
  "bone": 341,
  "screw": 342,
  "baseline_seconds": 1.0,
  "point_name": "Defined screw point",
  "marker_attachment": "rigid_screw_shaft",
  "screw_point_mm": null
}
```

Replace `null` with the measured/confirmed XYZ array; it deliberately prevents
evaluation until the point has been defined. Zero is valid only if the named
point is actually the CAD origin. A holder-exit point is labelled as such and
must not be described as the screw-head centre. Keep a static baseline before loading.
If a marker geometry/coordinate system or mounting changes, determine the point
again. Existing local calibration files are preserved, not applied implicitly.

For command-line use, supply the point with `--screw-point-mm X Y Z`, or use
`--config PATH`. `--frequency HZ` constrains the optical frequency search;
`--no-open` prevents opening the report browser.

The point is a rigid extension of the tracked body:
`p_point = p_marker + R_marker * point_marker`.
Both this lever arm and the full bone rotation/translation are included in the
compensation. A marker attached to a mobile polyaxial head cannot establish shaft
motion independently; document the attachment and locking condition.

### Different screws and gripping positions

Changing the point changes displacement through the rotational lever arm;
rotation of the same rigid shaft is point-independent. With a 10 mm point shift,
1 degree of rigid rotation alone can change the measured displacement by about
0.17 mm. Screw length and exposed length also change physical test boundary
conditions/load lever arms, so equal rotations do not prove mechanically
equivalent tests.

Document `screw_length_mm`, `free_length_mm` (holder exit to bone entry along the
shaft) and `head_to_holder_exit_mm` in each local setup; use null when unknown,
never a fabricated zero. These are reported as metadata, not used to invent a
head location or normalize the mechanical response. To compare displacement at
the head or bone entry across configurations, specify that common physical point
in marker coordinates for each mounting. A scalar length alone does not define
its three-dimensional direction.

New recordings save `coordinate_context` in their existing JSON sidecar: exact
geometry-file SHA-256 fingerprints and a snapshot of the local toggling setup.
Automatic analysis uses the recorded setup before current local settings. A
configuration bound through `screw_geometry_sha256` (and optionally
`screw_geometry_file`) rejects recordings with absent or mismatching geometry
evidence. In particular, a newly shifted 342 origin must not be silently applied
to old 342 recordings. An explicit historical/unbound configuration remains
possible with a visible coordinate-verification warning; old recordings and
their sidecars are never rewritten.

## Results

Each run creates a new folder under `<recording>_toggling/`, without overwriting
earlier output or modifying input CSV/JSON files. English HTML, SVG and CSV exports
contain:

- Defined screw-point displacement and rotation relative to the reference marker.
- Bone-marker displacement and rotation relative to the reference marker.
- Defined screw-point motion relative to bone, with bone rigid-body motion removed.
- Raw and explicitly labelled low-pass peak-to-peak values for each accepted
  optical cycle candidate; exclusions and continuous segment IDs remain in CSV.
- Observed data issues and corrective actions, configuration snapshot and methods.

The compensated displacement is the change in head position in bone coordinates,
expressed through the mean baseline bone orientation in reference coordinates.
The other two curves use the instantaneous reference coordinates. Rotations are
shortest rotation-vector XYZ components, **not** independent Euler angles.
Motion near the 180-degree branch is rejected. Anatomical/loading directions
must be assigned from the actual CAD/setup axes.

## Cycles, gaps and filtering

Frequency is estimated from the longest uninterrupted region of relative motion.
A sufficiently concentrated periodic signal is required; absent/weak periodic
motion does not prove stability or failure. Repeated/backwards timestamps are
rejected, never sorted into artificial time order. Saved elapsed times are
retained; otherwise the first recorded frame is the common time origin.

Segments are split at complete-observation intervals exceeding 1.75 times the
median interval. No missing interval is interpolated or filtered across. Within
continuous segments, resampling supports a fourth-order forward/backward
Butterworth filter. Its cutoff is three times the measured frequency, capped at
40% of sample rate. Cycles near segment edges and with inadequate duration/sample
counts are excluded. Raw extrema are computed from original samples; filtered
extrema are exported separately. This reduces neither missing-data uncertainty
nor the sensitivity of raw extrema to noise.

Cycle candidates come from optical motion, not the test machine counter. Missing
cycles are unknown; do not interpret candidate numbers as delivered load cycles.
Reported gaps distinguish explicitly unseen markers from absent saved camera
frames where the recording provides evidence. Timing alone cannot diagnose the
camera hardware or dirty reflectors.

No force stages, stiffness, hysteresis, loosening threshold or pass/fail result
are inferred without an appropriate protocol and aligned force measurements.
The report provides kinematic results and input-quality checks only.

## Local marker files

STEP models, generated geometries, previous marker definitions and point
configuration remain local. They do not belong in the public source repository.
Switching to the new CAD definitions changes the recorded marker coordinates;
old calibration transforms must not be reused without verification.

## Development validation

`python -m pytest tests -q` includes independent synthetic checks for common
rigid-motion cancellation, off-axis head-point lever arms, cycle extrema,
gap separation, missing-point rejection, non-overwriting output and automatic
dispatch. Runtime/UI changes also require the packaged mock selftest in a new
isolated installation. No live devices are used by these tests.
