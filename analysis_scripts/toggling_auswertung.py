"""Toggling: a defined screw point and bone motion, with rigid-body compensation."""
import argparse
import csv
import html
import json
from pathlib import Path
import sys
import time
import uuid
import webbrowser

import numpy as np

DIRECTORY = Path(__file__).resolve().parent
if str(DIRECTORY) not in sys.path:
    sys.path.insert(0, str(DIRECTORY))

import rom_auswertung as tracking
from _lib.toggling import motion, cycles, observation_gaps
from _lib.rom_presentation import CSS, SVG_STYLE

LABELS = {'screw_point': 'Defined screw point relative to reference',
          'bone': 'Bone marker relative to reference',
          'compensated_screw_point': 'Defined screw point relative to bone (bone motion removed)'}
COLUMNS = ('x_mm', 'y_mm', 'z_mm', 'rx_deg', 'ry_deg', 'rz_deg')


def load_settings(config=None, point=None, recorded=None):
    path = Path(config) if config else Path(__file__).with_name('toggling_config.local.json')
    settings = (recorded if recorded is not None else
                json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {})
    if not isinstance(settings, dict):
        raise ValueError('Toggling configuration must be an object.')
    settings = {**dict(reference=340, bone=341, screw=342, baseline_seconds=1.), **settings}
    settings.setdefault('point_name', 'Defined screw point')
    if not isinstance(settings['point_name'], str) or not 1 <= len(settings['point_name'].strip()) <= 80:
        raise ValueError('A point_name of 1–80 characters is required.')
    for key in ('screw_length_mm', 'free_length_mm', 'head_to_holder_exit_mm'):
        value = settings.get(key)
        if value is not None and (type(value) not in (int, float) or not np.isfinite(value) or value < 0):
            raise ValueError(key+' must be a finite nonnegative length in mm or null.')
    if point is not None:
        settings['screw_point_mm'] = list(point)
    value = settings.get('screw_point_mm')
    if value is None:
        raise ValueError('Screw point is not configured. Supply --screw-point-mm X Y Z in the recorded screw-marker coordinates. No marker-origin substitution is allowed.')
    if np.asarray(value).shape != (3,) or not np.isfinite(np.asarray(value, dtype=float)).all():
        raise ValueError('screw_point_mm must contain three finite coordinates in mm.')
    ids = [settings[key] for key in ('reference', 'bone', 'screw')]
    if any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != 3:
        raise ValueError('Reference, bone and screw need three distinct positive marker IDs.')
    return settings


def recording_context(source):
    sidecar = Path(source).with_suffix('.json')
    if not sidecar.is_file():
        return {}
    metadata = json.loads(sidecar.read_text(encoding='utf-8-sig'))
    if not isinstance(metadata, dict):
        raise ValueError('Recording sidecar must be an object.')
    context = metadata.get('coordinate_context', {})
    if not isinstance(context, dict):
        raise ValueError('Invalid coordinate context in the recording sidecar.')
    return context


def verify_coordinate_frame(source, settings):
    expected = settings.get('screw_geometry_sha256')
    if expected is None:
        return False
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('screw_geometry_sha256 must be a lowercase SHA-256 fingerprint.')
    context = recording_context(source)
    filename = settings.get('screw_geometry_file', f'geometry{settings["screw"]}.json5')
    fingerprints = context.get('geometry_sha256', {})
    if not isinstance(fingerprints, dict) or context.get('error') or fingerprints.get(filename) != expected:
        raise ValueError('Recorded screw geometry is not verified against the configured point. '
                         'Do not apply a new CAD origin to older recordings. Use the correct historical coordinate definition; '
                         'existing measurements are preserved and were not reinterpreted.')
    return True


def figure_svg(title, x, values, xlabel, gaps=()):
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 1260"><style>{SVG_STYLE}</style>',
              '<rect width="1200" height="1260" fill="white"/>',
              f'<text x="55" y="35" font-family="Arial" font-size="20">{html.escape(title)}</text>']
    for index in range(6):
        column, row = index//3, index%3
        unit = 'mm' if column == 0 else 'deg'
        pieces.append(tracking._svg_multi_plot(
            [(COLUMNS[index], x, values[:, index], ('#2266ad', '#c45c13', '#23806a')[row])],
            (90+column*590, 120+row*400, 440, 270), xlabel,
            f'{"XYZ"[row]} ({unit})', 'Translation' if column == 0 else 'Rotation-vector component',
            vertical_bands=gaps, split_gap_factor=1.75 if xlabel.startswith('Time') else None))
    return ''.join(pieces)+'</svg>'


def peak_series(cyclic, channel, method='filtered'):
    series = {name: ([], []) for name in ('min', 'max')}
    previous = None
    for row in cyclic['rows']:
        if not row['accepted']:
            continue
        interrupted = previous is not None and (row['segment'] != previous['segment']
                                                or row['cycle'] != previous['cycle']+1)
        extrema = row['extrema'][method]
        for name, (times, values) in series.items():
            if interrupted:
                times.append(float('nan')); values.append(float('nan'))
            times.append(extrema[name+'_time_s'][channel]); values.append(extrema[name][channel])
        previous = row
    return series


def peak_figure_svg(title, cyclic, method='filtered'):
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 1260"><style>{SVG_STYLE}</style>',
              '<rect width="1200" height="1260" fill="white"/>',
              f'<text x="55" y="35" font-family="Arial" font-size="20">{html.escape(title)}</text>']
    for index in range(6):
        column, row = index//3, index%3
        extrema = peak_series(cyclic, index, method)
        pieces.append(tracking._svg_multi_plot(
            [(label, *extrema[name], color) for name, label, color in
             [('min', 'Minimum', '#d62728'), ('max', 'Maximum', '#2266ad')]],
            (90+column*590, 120+row*400, 440, 270), 'Time (s)',
            f'{"XYZ"[row]} ({"mm" if column == 0 else "deg"})',
            'Translation' if column == 0 else 'Rotation-vector component'))
    return ''.join(pieces)+'</svg>'


def analyze(source, settings, output=None, expected_frequency=None):
    source = Path(source).resolve()
    started = time.perf_counter()
    print('Toggling analysis: reading recorded marker poses...', flush=True)
    geometry_verified = verify_coordinate_frame(source, settings)
    ids = [settings[key] for key in ('reference', 'bone', 'screw')]
    try:
        # SDK tracking IDs need not persist between frames. This setup assigns
        # roles by its unique physical geometry, rejecting simultaneous duplicate
        # poses without requiring a stable instance ID throughout the recording.
        data = tracking._load_current_tracking(source, ids, reject_duplicate_poses=True)
    except ValueError as error:
        raise ValueError(f'Cannot evaluate toggling markers {ids}: {error}') from error
    print(f'Toggling analysis: {len(data.times_s)} complete samples loaded '
          f'({time.perf_counter()-started:.1f} s); computing motion and cycles...', flush=True)
    values, baseline_count = motion(data, *ids, settings['screw_point_mm'], settings['baseline_seconds'])
    t = np.asarray(data.times_s)
    # Keep the original elapsed recording time when present, as the other analyses do.
    first_time = None
    elapsed = False
    with source.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            parsed = tracking._frame_key_and_time(row)
            if parsed:
                first_time = parsed[1]
                elapsed = bool(row.get('time_since_start_s', '').strip())
                break
    offset = first_time-data.received_times_s[0] if elapsed else -data.received_times_s[0]
    t = t+offset
    cyclic = cycles(t, values, expected_frequency)
    issues = []
    if not geometry_verified:
        issues.append('No recorded geometry fingerprint was bound to this point configuration. Verify the recorded coordinate definition before interpreting point displacements.')
    if settings.get('marker_attachment') != 'rigid_screw_shaft':
        issues.append('Rigid attachment to the screw shaft is not documented. Check attachment and any mobile head connection before interpreting screw/bone motion.')
    if settings.get('free_length_mm') is None or settings.get('head_to_holder_exit_mm') is None:
        issues.append('Free length/grip position is not documented. Record both before comparing different screws or gripping positions; displacement at different physical points and load lever arms is not directly interchangeable.')
    if data.incomplete_samples:
        issues.append(f'{data.incomplete_samples} received frames lack required valid marker poses. Check visibility, rigid fixation and marker IDs; excluded observations were not filled in.')
    if data.invalid_samples:
        issues.append(f'{data.invalid_samples} invalid/ambiguous rows. Check the raw export and duplicate marker instances.')
    if baseline_count == len(t):
        issues.append('Baseline covers the entire recording. Record a static baseline before loading or shorten its duration.')
    if cyclic['reason']:
        issues.append(cyclic['reason'])
    if any(np.max(np.linalg.norm(v[:, 3:], axis=1)) >= 175 for v in values.values()):
        raise ValueError('Rotation approaches the 180-degree branch; component curves/cycle ranges are not reliable.')
    print(f'Toggling analysis: indexing observation gaps ({time.perf_counter()-started:.1f} s)...', flush=True)
    gaps = observation_gaps(t, data.received_times_s, data.unseen_marker_events, offset)
    if gaps:
        issues.append(f'{len(gaps)} observation gaps. Filtering and cycle detection are separated at gaps. Missing cycle counts are unknown, not reconstructed.')
    if data.unseen_marker_events:
        issues.append('Required markers were explicitly not recognized in received frames. Check placement, occlusion, reflector condition and marker fixation; this differs from absent camera frames.')
    output = (Path(output) if output else source.parent/(source.stem+'_toggling'))/('run_'+uuid.uuid4().hex[:12])
    output.mkdir(parents=True, exist_ok=False)
    print(f'Toggling analysis: exporting curves and peak envelopes ({time.perf_counter()-started:.1f} s)...', flush=True)
    pages = []
    accepted = [r for r in cyclic['rows'] if r['accepted']]
    for key, v in values.items():
        label = LABELS[key].replace('Defined screw point', settings['point_name'])
        with (output/(key+'_motion.csv')).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream); writer.writerow(['time_s', *COLUMNS]); writer.writerows(zip(t, *v.T))
        raw_svg = figure_svg(label+' — raw, unfiltered', t, v, 'Time (s)')
        (output/(key+'_motion.svg')).write_text(raw_svg, encoding='utf-8')
        sections = [f'<h2>{html.escape(label)}</h2>', raw_svg]
        if accepted:
            numbers, raw, filtered = [], [], []
            previous = None
            for r in cyclic['rows']:
                if not r['accepted']:
                    continue
                if previous is not None and (r['segment'] != previous['segment'] or r['cycle'] != previous['cycle']+1):
                    numbers.append(float('nan')); raw.append([float('nan')]*6); filtered.append([float('nan')]*6)
                numbers.append(r['cycle']); raw.append(r['raw'][key]); filtered.append(r['filtered'][key]); previous = r
            for name, arr in [('raw', raw), ('filtered', filtered)]:
                svg = figure_svg(label+f' — {name} maximum minus minimum per cycle', numbers, np.array(arr), 'Detected cycle candidate')
                (output/(key+'_'+name+'_cycles.svg')).write_text(svg, encoding='utf-8')
                sections.append(f'<details><summary>{name.title()} peak-to-peak by cycle</summary>{svg}</details>')
        pages.append('<section class="card">'+''.join(sections)+'</section>')
    peak_title = LABELS['compensated_screw_point'].replace('Defined screw point', settings['point_name'])
    peak_sections = [f'<h2>{html.escape(peak_title)} — minima and maxima</h2>']
    if accepted:
        for method in ('filtered', 'raw'):
            svg = peak_figure_svg(peak_title+' — '+method+' peak envelopes', cyclic, method)
            (output/('compensated_screw_point_'+method+'_peak_envelopes.svg')).write_text(svg, encoding='utf-8')
            peak_sections.append(svg if method == 'filtered' else
                                 '<details><summary>Unfiltered peak envelopes</summary>'+svg+'</details>')
        peak_sections.append('<p>Red: cycle minima. Blue: cycle maxima. Lines connect only the extrema of '
                             'consecutive accepted cycles; they are envelopes, not a regression fit. '
                             'The main figure uses the documented low-pass filter; raw extrema are available in the expandable panel. '
                             'Connections stop at observation gaps or excluded cycles. No gap bands are drawn.</p>')
    else:
        peak_sections.append('<p>No reliable complete cycles detected; peak envelopes are unavailable. '
                             'No minima or maxima have been fabricated.</p>')
    pages.insert(0, '<section class="card" id="peak-envelopes">'+''.join(peak_sections)+'</section>')
    with (output/'compensated_screw_point_peak_envelopes.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['cycle', 'segment', 'method', 'channel', 'minimum_time_s', 'minimum', 'maximum_time_s', 'maximum'])
        for row in accepted:
            for method, extrema in row['extrema'].items():
                for index, channel in enumerate(COLUMNS):
                    writer.writerow([row['cycle'], row['segment'], method, channel,
                                     extrema['min_time_s'][index], extrema['min'][index],
                                     extrema['max_time_s'][index], extrema['max'][index]])
    with (output/'cycles.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['candidate', 'segment', 'start_s', 'end_s', 'accepted', 'exclusion_reason',
                         *[f'{key}_{method}_{name}' for key in values for method in ('raw', 'filtered') for name in COLUMNS]])
        for r in cyclic['rows']:
            writer.writerow([r['cycle'], r['segment'], r['start_s'], r['end_s'], r['accepted'], r['reason'],
                             *[value for key in values for method in ('raw', 'filtered')
                               for value in (r[method][key] if r['accepted'] else ['']*6)]])
    report = dict(analysis='toggling_screw_point', input=str(source), settings=settings,
                  baseline_samples=baseline_count, complete_samples=len(t), incomplete_samples=data.incomplete_samples,
                  invalid_rows=data.invalid_samples, issues=issues, gap_bands=gaps, cycles=cyclic,
                  force_based_metrics_available=False, protocol_approval=False,
                  screw_coordinate_frame_verified=geometry_verified,
                  coordinates='Recorded CAD marker axes; old local marker transforms are not applied.',
                  point='screw_point_mm in recorded screw-marker coordinates; rigid attachment is required',
                  time_origin='original elapsed recording time' if elapsed else 'first recorded frame',
                  compensated_basis='Mean baseline bone orientation expressed in reference coordinates',
                  rotation='Shortest rotation-vector XYZ components, not independent Euler angles')
    (output/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    (output/'settings_snapshot.local.json').write_text(json.dumps(settings, indent=2), encoding='utf-8')
    problem_html = ''.join('<li>'+html.escape(issue)+'</li>' for issue in issues) or '<li>No input integrity issue identified by these checks.</li>'
    status = 'Check data and loading' if issues else 'Kinematic results available'
    doc = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Toggling analysis</title>'
           f'<style>{CSS}svg{{width:100%;height:auto}}summary{{cursor:pointer;padding:12px}}</style><main>'
           f'<h1>Toggling analysis</h1><p>{html.escape(source.name)}</p><section class="card"><h2>{status}</h2>'
           f'<p>{len(t)} complete samples; {len(accepted)} accepted optical cycle candidates.</p>'
           '<p><a href="#peak-envelopes">Min/max peak envelopes</a> · '
           '<a href="compensated_screw_point_peak_envelopes.csv">Peak values and times (CSV)</a></p>'
           '<h3>Observed problems and corrective actions</h3><ul>'+problem_html+'</ul>'
           '<p>These are kinematic measurements, not a loosening/failure approval. Force, stiffness and hysteresis require an independently aligned force signal.</p>'
           f'<p>{html.escape(settings["point_name"])} in marker coordinates (mm): {html.escape(str(settings["screw_point_mm"]))}.</p>'
           f'<p>Screw length: {html.escape(str(settings.get("screw_length_mm")))} mm; '
           f'free length: {html.escape(str(settings.get("free_length_mm")))} mm; '
           f'head-to-holder-exit distance: {html.escape(str(settings.get("head_to_holder_exit_mm")))} mm. '
           'None means not documented, never zero.</p></section>'+
           ''.join(pages)+'<section class="card"><h2>Methods and limitations</h2>'
           '<p>Raw motion is unfiltered. Cycle CSVs contain both raw extrema and fourth-order forward/backward Butterworth low-pass extrema. '
           'The cutoff is three times the measured cycle frequency, capped at 40% of the sample rate. '
           'Only continuous observation segments are resampled internally for filtering; no gap is bridged. '
           'One period near each segment edge is excluded. Raw extrema use the original observations. '
           'Cycle timing is inferred from optical motion; machine cycle numbers and missing cycles are unknown. '
           'A weak or absent periodic relative signal does not establish screw stability or failure.</p>'
           '<p>Point displacement includes any motion between the marker and screw shank. A polyaxial/mobile head or loose mount can confound bone-interface interpretation. '
           'Changing the virtual point changes translation through its lever arm; rotation is point-independent. '
           'Do not interpret XYZ as anatomical/loading axes until the CAD axes have been checked.</p>'
           '<p>Export: <a href="cycles.csv">Cycle table</a> · <a href="report.json">Analysis record</a>. SVG figures are saved beside this report.</p>'
           '</section></main></html>')
    page = output/(source.stem+'_toggling_report.html')
    page.write_text(doc, encoding='utf-8')
    return page


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--screw-point-mm', type=float, nargs=3, metavar=('X', 'Y', 'Z'))
    parser.add_argument('--frequency', type=float)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)
    recorded = recording_context(args.input).get('toggling_setup') if args.config is None and args.screw_point_mm is None else None
    page = analyze(args.input, load_settings(args.config, args.screw_point_mm, recorded), args.output_dir, args.frequency)
    print(json.dumps({'status': 'ok', 'figure_html': str(page)}))
    if not args.no_open:
        webbrowser.open(page.resolve().as_uri())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
