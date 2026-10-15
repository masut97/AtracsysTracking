# Automatic ROM curve assessment

The English figure and JSON report use three outcomes:

* **Approved:** no rejection or borderline criterion was triggered in the stated scope.
* **Not approved — REPEAT TEST:** a rejection criterion was triggered. Each finding includes the observed problem and a corrective action. Recovering intact raw data and rerunning the analysis can resolve export problems without repeating the physical test.
* **Check data and if needed repeat:** a result lies within a narrow review interval. This is not an approval.

Approval is an automated measurement-quality result, not proof of specimen integrity or a clinical conclusion. Camera-only retrospective analysis explicitly displays **CAMERA TRACE ONLY; LOAD NOT VERIFIED**. Full-test approval requires the corresponding machine load data and the configured protocol. Camera curves alone cannot detect every incorrect load or setup.

## Mathematical checks

Rule version 2.0 uses acquisition order rather than sorting away clock errors. Repeated/backwards timestamps, invalid marker rotations and nonfinite poses prevent approval. Frames with no visible required markers are included in completeness accounting. Completely unusable input produces a red result figure rather than an empty successful analysis.

Noise is estimated robustly from deviations from time-weighted local interpolation, using only approximately regular intervals. This avoids mistaking uneven sampling for noise. The estimated ROM noise allowance is `3.92 * estimated_noise / ROM`. Above 15% it rejects; between 10% and 15% it requests review. This is a screening estimate, not a calibrated measurement uncertainty or confidence interval.

Isolated reversals exceeding 5% of ROM and eight empirical noise units reject. Abrupt regular-interval movement exceeding 20% of ROM, twelve typical movement steps and eight noise units also rejects. These checks do not repair, clip or smooth away raw extrema.

Gaps are assessed using elapsed time and motion spanned, rather than a fixed lost-frame count. Rejection occurs for a gap spanning more than 5% of segment/clamp ROM, or a sufficiently long gap near an extremum (over three regular intervals and 3% of recording duration). A narrower near-peak impact band requests review. Short high-rate gaps with negligible motion are not automatically rejected.

Loading-branch consistency uses at most 100 median time bins and detects every substantial clamp reversal, including repeated cycles. Branch ends are excluded to accommodate normal rounded peaks and small phase lag. Opposed segment excursions above 5% ROM reject; 3.5–5% requests review. This heuristic is disabled for axial compression because fixture motion and specimen creep need not be monotonic together. Inspect the load trace when a branch-consistency rule triggers.

Segment/clamp correlation below 0.35 rejects when signal resolution is adequate; 0.35–0.65 requests review. A low vertebra/clamp ROM ratio alone does **not** prove slippage and does not reject. Stiff constructs, fixture compliance, hysteresis and coordinate geometry must be distinguished from marker fixation problems. A ratio above 1.30 requests a geometry check rather than asserting a mechanical cause.

Small but resolved fixture and specimen motion is permitted: a stiff construct can move little under the prescribed load. Constant or unresolved traces fail; an arbitrary minimum angle is not a substitute for checking the applied load. The legacy minimum-clamp settings now gate only the correlation comparison, not rejection of a resolved small ROM.

More than 5% incomplete five-marker frames rejects; 2–5% requests review. Smaller losses remain informational unless their timing or motion impact triggers another rule. Registration-error limits remain configurable and are not treated as calibrated ROM accuracy.

When machine data are required, missing/invalid traces prevent approval. More than 10% duration disagreement rejects; 7–10% requests review. For an active bipolar protocol, attaining less than 95% of either prescribed load limit rejects; 95–98% requests review. More than 10% overshoot rejects and requests specimen inspection before another physical test. Recording-boundary extrema in an active test reject as potentially truncated cycles.

## Interpretation and validation

Thresholds are engineering screening tolerances, not published universal biomechanical acceptance limits. They were examined against a local historical archive without uploading measurements, identifiers, calibrations or source images. Archive labels are not a representative independently adjudicated ground truth. Some visually smooth records marked unusable cannot be distinguished from valid records using camera data alone; the system must not claim that all historical rejected records are detectable.

Regression tests use synthetic signals: smooth and stiff responses, multiple cycles and sampling rates, short benign gaps, missing peak intervals, spikes, noise, clock errors, marker loss, machine limits and complete English figure output. Real records remain local and are used only in separate retrospective checks. No record-specific IDs, thresholds or label lookups are embedded in the software. Unchanged raw ROM values are retained in the report.

Relevant measurement background: [Atracsys device specification](https://atracsys.com/product/fusiontrack-500/) defines fiducial accuracy under stated conditions; it is not an angular ROM uncertainty calibration. [Experimental study on component selection and kinematic measurement](https://pubmed.ncbi.nlm.nih.gov/17067608/) examines machine and marker-placement influences. The numeric thresholds above are our screening choices, not thresholds attributed to either source.
