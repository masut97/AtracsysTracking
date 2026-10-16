"""English offline report for arbitrary relative marker comparisons."""
import html
from pathlib import Path

from _lib.free_motion import OUTPUTS
from _lib.rom_presentation import CSS, SVG_STYLE


def write_report(output, report):
    from rom_auswertung import _svg_multi_plot
    escape = lambda value: html.escape(str(value), quote=True)
    profile = report['profile']
    labels = profile['markers']
    available = sum(bool(row['curves']) for row in report['results'])
    status = 'Check tracking data' if any(row['issues'] for row in report['results']) else 'Motion data available'
    color = '#966500' if status.startswith('Check') else '#245b93'
    ref = f"{labels[str(profile['reference'])]} ({profile['reference']})"
    options, sections = [], []
    colors = {'tx':'#2266ad', 'ty':'#c45c13', 'tz':'#23806a', 'displacement':'#6c4ca2',
              'path':'#2266ad', 'rotation_axis':'#c45c13', 'rotation_total':'#2266ad'}
    for index, result in enumerate(report['results']):
        pair = result['comparison']
        options.append(f'<option value="comparison-{index}">{escape(pair["name"])}</option>')
        direction = (f"{labels[str(pair['target'])]} ({pair['target']}) relative to "
                     f"{labels[str(pair['anchor'])]} ({pair['anchor']})")
        cards = [f'<h2>{escape(pair["name"])}</h2><p>{escape(direction)}</p>']
        if 'rotation_axis' in pair['outputs']:
            cards.append('<p class="muted">Projected rotation axis in reference coordinates: ['+
                         ', '.join(f'{v:.4g}' for v in pair['axis'])+'].</p>')
        for issue in result['issues']:
            cards.append('<div class="notice"><b>Observed issue / next step</b><p>'+escape(issue)+'</p></div>')
        if not result['curves']:
            cards.append('<section class="card"><h3>Cannot evaluate this comparison</h3><p>No motion values were fabricated. Correct the stated input problem and rerun the analysis.</p></section>')
        else:
            cards.append(f'<p class="muted">{result["complete_samples"]} complete samples · {result["baseline_samples"]} baseline samples. Reference axes: {escape(ref)}.</p>')
            for title, keys in [('Relative displacement components', ('tx','ty','tz')),
                                ('Distance and path length', ('displacement','path')),
                                ('Relative rotation', ('rotation_axis','rotation_total'))]:
                keys = [key for key in keys if key in result['curves']]
                if not keys:
                    continue
                unit = OUTPUTS[keys[0]][1]
                series = [(OUTPUTS[key][0], result['times_s'], result['curves'][key], colors[key]) for key in keys]
                graph = _svg_multi_plot(series, (90.,150.,740.,330.), 'Elapsed time [s]',
                                        f'Motion [{unit}]', title, split_gap_factor=1.75,
                                        vertical_bands=result.get('gap_bands',[]),
                                        dashed_labels=(OUTPUTS['rotation_total'][0], OUTPUTS['path'][0]))
                short_name = pair['name'][:64]+('…' if len(pair['name'])>64 else '')
                context = f'Ref {profile["reference"]} · target {pair["target"]} relative to anchor {pair["anchor"]}'
                svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560"><title>{escape(pair["name"])}</title><style>{SVG_STYLE}</style><rect width="100%" height="100%" fill="white"/><text x="90" y="23" class="small">{escape(short_name)}</text><text x="90" y="44" class="small">{escape(context)}</text>{graph}</svg>'
                filename = f'comparison_{result["index"]:02d}_{keys[0]}.svg'
                (Path(output).parent/filename).write_text(svg, encoding='utf-8')
                cards.append('<section class="card plot">'+svg+f'<a class="button" href="{filename}">Open SVG</a></section>')
            rows = []
            for key, values in result['statistics'].items():
                rows.append('<tr><td>'+escape(OUTPUTS[key][0])+'</td><td>'+escape(values['unit'])+'</td>'+''.join(f'<td>{values[field]:.5g}</td>' for field in ('minimum','maximum','observed_range','final'))+'</tr>')
            cards.append('<section class="card"><h3>Observed values</h3><p>Span = maximum − minimum of this quantity. Distance and net-angle magnitude are unsigned; their spans are not a signed joint ROM. Missing observations can underestimate extrema and cumulative path.</p><div style="overflow-x:auto"><table><thead><tr><th>Quantity</th><th>Unit</th><th>Minimum</th><th>Maximum</th><th>Observed span</th><th>Final</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'+
                         f'<p><a class="button" href="comparison_{result["index"]:02d}_motion.csv">Motion CSV</a></p></section>')
        sections.append(f'<section class="comparison" id="comparison-{index}"{ "" if index==0 else " hidden"}>'+''.join(cards)+'</section>')
    definitions = ''.join('<p><b>'+escape(label)+':</b> '+escape(report[key])+'</p>' for key,label in
                          [('reference_axes','Axes'),('rotation_convention','Rotation convention'),('baseline','Baseline')])
    document = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Free marker analysis</title><style>'+CSS+
        'select{font:inherit;max-width:100%;padding:10px;border:1px solid #a7b9cc;border-radius:6px}.comparison{margin-top:20px}th{width:auto}@media print{.comparison[hidden]{display:block!important}.comparison{break-before:page}.selector{display:none}}</style></head><body><main>'+
        '<h1>Free marker analysis</h1><p class="source">'+escape(Path(report['input']).name)+'</p>'+
        f'<section class="status" style="--accent:{color}"><h2>{status}</h2><p>{available} / {len(report["results"])} comparisons contain motion data.</p><div class="scope"><b>TECHNICAL DATA CHECKS ONLY — NO PROTOCOL APPROVAL</b><br>No anatomical relationships, clamp limits or load requirements are assumed.</div></section>'+
        '<section class="card"><h3>'+escape(profile['name'])+'</h3><p>Reference axes: '+escape(ref)+'. Each pair defines its own anchor/origin. Recorded marker coordinate systems are used; optional transforms from other analysis profiles are not silently applied.</p>'+
        '<label class="selector">Comparison <select id="comparison-select">'+''.join(options)+'</select></label></section>'+
        ''.join(sections)+'<details class="card"><summary>Definitions &amp; saved profile</summary>'+definitions+
        '<p>Time origin: '+escape(report['time_origin'])+'. Original elapsed recording timestamps are retained when available; otherwise a common monotonic-clock origin is used.</p>'+
        '<p>Relative displacement removes anchor rigid-body motion, including marker lever arms. XYZ and projected rotation are expressed in the instantaneous reference axes. Cumulative path is calculated in anchor-local coordinates; missing intervals contribute only their endpoint distance. No interpolation or resampling is performed. Comparisons share the stated time origin and use independent complete samples.</p>'+
        '<p>Projected rotation is the component of the shortest rotation vector along the normalized axis defined in the profile. It is not an independent Euler angle. Net rotation magnitude is 0–180 degrees and does not count full turns.</p>'+
        '<p>Time shading: orange = explicitly unseen required marker; blue = no recorded camera frame; grey = unknown/invalid-pose gap source. Neither timestamps nor absent saved frames alone identify a hardware failure or dirty reflectors.</p>'+
        '<p>Profile SHA-256: '+escape(report['profile_sha256'])+'</p><a class="button" href="profile_snapshot.json">Saved profile</a></details>'+
        '<div class="toolbar"><button id="print-report">Print / Save PDF</button></div></main><script>document.getElementById("comparison-select").addEventListener("change",function(){document.querySelectorAll(".comparison").forEach(s=>s.hidden=s.id!==this.value);});document.getElementById("print-report").addEventListener("click",()=>window.print());</script></body></html>')
    Path(output).write_text(document, encoding='utf-8')
