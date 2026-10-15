# Automatic ROM curve assessment

The English figure and JSON report use three outcomes:

* **Approved:** no rejection or borderline criterion was triggered in the stated scope.
* **Not approved — REPEAT TEST:** a rejection criterion was triggered. Each finding includes the observed problem and a corrective action. Recovering intact raw data and rerunning the analysis can resolve export problems without repeating the physical test.
* **Check data and if needed repeat:** a result lies within a narrow review interval. This is not an approval.

Approval is an automated measurement-quality result, not proof of specimen integrity or a clinical conclusion. Camera-only retrospective analysis explicitly displays **CAMERA TRACE ONLY; LOAD NOT VERIFIED**. Full-test approval requires the corresponding machine load data and the configured protocol. Camera curves alone cannot detect every incorrect load or setup.

## Mathematical checks

Rule version 2.1 uses acquisition order rather than sorting away clock errors. Repeated/backwards timestamps, invalid marker rotations and nonfinite poses prevent approval. Frames with no visible required markers are included in completeness accounting. Completely unusable input produces a red result figure rather than an empty successful analysis.

Noise is estimated robustly from deviations from time-weighted local interpolation, using only approximately regular intervals. This avoids mistaking uneven sampling for noise. The estimated ROM noise allowance is `3.92 * estimated_noise / ROM`. Above 15% it rejects; between 10% and 15% it requests review. This is a screening estimate, not a calibrated measurement uncertainty or confidence interval.

Isolated reversals exceeding 5% of ROM and eight empirical noise units reject. Abrupt regular-interval movement exceeding 20% of ROM, twelve typical movement steps and eight noise units also rejects. These checks do not repair, clip or smooth away raw extrema.

Gaps are assessed using elapsed time and motion spanned, rather than a fixed lost-frame count. Rejection occurs for a gap spanning more than 5% of segment/clamp ROM, or a sufficiently long gap near an extremum (over three regular intervals and 3% of recording duration). A narrower near-peak impact band requests review. Short high-rate gaps with negligible motion are not automatically rejected.

Loading-branch consistency uses at most 100 median time bins and detects every substantial clamp reversal, including repeated cycles. Branch ends are excluded to accommodate normal rounded peaks and small phase lag. Opposed segment excursions above 5% ROM reject; 3.5–5% requests review. This heuristic is disabled for axial compression because fixture motion and specimen creep need not be monotonic together. Inspect the load trace when a branch-consistency rule triggers.

Segment/clamp correlation below 0.35 rejects when signal resolution is adequate; 0.35–0.65 requests review. A low aggregate vertebra/clamp ROM ratio alone does **not** establish where fixation movement occurred. Version 2.1 separately tests cranial vertebra versus upper clamp, and caudal vertebra versus lower clamp, under the operator's same-bone rigid-attachment protocol.

### Same-bone attachment protocol

Vertebral ROM exceeding its corresponding fixture prediction by more than the noise-aware allowance rejects. A cranial ROM deficit over 1.5 times that allowance rejects; a deficit between 1 and 1.5 times the allowance requests review. Caudal motion exceeding the lower-fixture prediction rejects separately. Aggregate angular segment motion also must not exceed clamp ROM beyond the allowance. These are protocol checks for this fixture arrangement, not universal physiological limits.

The tolerance is `max(configured absolute floor, 6 * empirical noise of the paired difference)`. The provisional angular floor is 0.02 degrees. It is an engineering starting point, not a manufacturer accuracy claim. Translational comparisons use empirical paired-difference noise by default; no unsupported fixed millimetre accuracy is claimed. Both absolute floors are configurable. This does not replace a static repeatability and rigid-reference-motion calibration.

For translations, the expected vertebral marker position is predicted from the clamp's full rigid-body rotation/translation and its baseline offset to the bone marker. This compensates marker lever arms: comparing raw translations at different points during rotation can otherwise falsely suggest slip. The calculated tolerances, comparison method and individual ROMs are reported in JSON and the English findings.

Excess vertebral motion prompts inspection for soft-tissue/clamp contact, incorrect marker identity, or anchorage crossing the disc instead of bone-only attachment. Cranial motion loss or excessive caudal motion prompts inspection of clamp purchase. The mathematical symptom is observed; the proposed physical cause remains a hypothesis to inspect.

Before interpreting these checks, verify the marker assignment and that the fixture and vertebral marker should follow the same rigid motion in the actual setup. Bone/fixture compliance and differences in the chosen motion projection can also produce a mismatch. A smooth curve or a historical folder label cannot prove rigid fixation. The provisional noise allowance does not establish a biomechanically acceptable amount of fixation movement; a static recording followed by known rigid-reference motion is needed to establish setup-specific limits. Do not widen limits merely to obtain a preferred approval percentage.

### Slope changes and missing-observation provenance

An abrupt slope change is tested away from ordinary turning points using two highly linear neighbouring windows. Both slopes must have the same sign, a ratio above three, and an effect exceeding 1.5% ROM and eight empirical noise units. Findings identify the affected marker and interval. Inspect dirty/damaged reflectors, unfavourable marker orientation and the load trace: a genuine actuator/load change must be distinguished from a tracking artefact.

Orange bands identify explicit invisible-marker flags in received frames. Blue bands identify intervals with no saved frame in a current-format export. They do not by themselves identify the hardware, transport, recording scheduler or export as the cause. Grey bands identify gaps whose cause is not observable, especially historical exports without a frame heartbeat or visibility flags. Missing peaks inherit the available provenance. A missing saved frame is never automatically described as dirty or unseen spheres.

Small but resolved fixture and specimen motion is permitted: a stiff construct can move little under the prescribed load. Constant or unresolved traces fail; an arbitrary minimum angle is not a substitute for checking the applied load. The legacy minimum-clamp settings now gate only the correlation comparison, not rejection of a resolved small ROM.

More than 5% incomplete five-marker frames rejects; 2–5% requests review. Smaller losses remain informational unless their timing or motion impact triggers another rule. Registration-error limits remain configurable and are not treated as calibrated ROM accuracy.

When machine data are required, missing/invalid traces prevent approval. More than 10% duration disagreement rejects; 7–10% requests review. For an active bipolar protocol, attaining less than 95% of either prescribed load limit rejects; 95–98% requests review. More than 10% overshoot rejects and requests specimen inspection before another physical test. Recording-boundary extrema in an active test reject as potentially truncated cycles.

## Interpretation and validation

Thresholds are engineering screening tolerances, not published universal biomechanical acceptance limits. They were examined against a local historical archive without uploading measurements, identifiers, calibrations or source images. Archive labels are not a representative independently adjudicated ground truth. Some visually smooth records marked unusable cannot be distinguished from valid records using camera data alone; the system must not claim that all historical rejected records are detectable.

Regression tests use synthetic signals: smooth and stiff responses, multiple cycles and sampling rates, short benign gaps, missing peak intervals, spikes, noise, clock errors, marker loss, machine limits and complete English figure output. Real records remain local and are used only in separate retrospective checks. No record-specific IDs, thresholds or label lookups are embedded in the software. Unchanged raw ROM values are retained in the report.

Relevant measurement background: [Atracsys device specification](https://atracsys.com/product/fusiontrack-500/) defines fiducial accuracy under stated conditions; it is not an angular ROM uncertainty calibration. [Experimental study on component selection and kinematic measurement](https://pubmed.ncbi.nlm.nih.gov/17067608/) examines machine and marker-placement influences. The numeric thresholds above are our screening choices, not thresholds attributed to either source.
