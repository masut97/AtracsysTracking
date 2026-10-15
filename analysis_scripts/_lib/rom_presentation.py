"""Local, responsive ROM reports and a complete static SVG export."""
import html
import json
import textwrap
from urllib.parse import quote


SVG_STYLE = '''
text{font-family:Arial,sans-serif;fill:#24364b}
.background{fill:white}.plot-bg{fill:#f8fafc}.frame{fill:none;stroke:#b8c6d5;stroke-width:1}
.grid{stroke:#dae2eb;stroke-width:1}.limit{stroke:#64748b;stroke-width:1.5;stroke-dasharray:8 6}
.panel-title{font-size:21px;font-weight:700}.axis-label{font-size:17px}
.tick{font-size:14px;fill:#53657b}.legend{font-size:16px;fill:#354b63}
.peak-gap{fill:#dc2626;fill-opacity:.10;stroke:#dc2626;stroke-dasharray:5 4}
.visibility-gap{fill:#e68a00;fill-opacity:.12;stroke:#b96c00}
.frame-gap{fill:#2872c7;fill-opacity:.10;stroke:#2872c7}
.unknown-gap{fill:#6b7280;fill-opacity:.12;stroke:#6b7280}
.title{font-size:30px;font-weight:700}.subtitle{font-size:17px;fill:#53657b}
.body{font-size:18px}.small{font-size:16px;fill:#53657b}.heading{font-size:22px;font-weight:700}
'''

CSS = '''
:root{font:16px/1.55 "Segoe UI",Arial,sans-serif;color:#20334c;background:#eef3f8;--line:#dce4ed}
*{box-sizing:border-box}body{margin:0}main{max-width:1440px;margin:auto;padding:24px}
h1{font-size:28px;line-height:1.25;margin:0 0 8px}h2{font-size:22px;margin:0 0 14px}h3{font-size:18px;margin:0 0 10px}p{margin:8px 0}
.source{color:#53657b;overflow-wrap:anywhere}.status{background:white;border-left:7px solid var(--accent);padding:22px 26px;border-radius:10px}
.status-line{display:flex;align-items:baseline;gap:20px;flex-wrap:wrap}.status-line h2{color:var(--accent);font-size:27px;margin:0}
.action{font-weight:700;color:var(--accent)}.scope{font-size:14px;color:#475c72;border-top:1px solid var(--line);margin-top:14px;padding-top:12px}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.metric,.card{background:white;border:1px solid var(--line);border-radius:10px;padding:20px}
.metric strong{display:block;font-size:25px}.metric span{color:#53657b;font-size:14px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid #bac9d9;margin:20px 0}.tabs button{border:0;border-bottom:3px solid transparent;background:transparent;border-radius:0;padding:12px 18px;font-size:16px}
.tabs button[aria-selected=true]{border-bottom-color:#245b93;color:#164f86;font-weight:700;background:#e0ebf5}
button,a.button{font:inherit;cursor:pointer;border:1px solid #a7b9cc;color:#244e78;background:white;border-radius:6px;padding:7px 12px;text-decoration:none;display:inline-block}
button:hover,a.button:hover{background:#edf4fb}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #1976d2;outline-offset:3px}
[hidden]{display:none!important}.overview{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:18px;align-items:start}.pair-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.plot{min-width:0;overflow-x:auto}.plot svg{display:block;width:100%;min-width:600px;height:auto}.plot-tools{display:flex;gap:6px;align-items:center;justify-content:flex-end}.plot-tools small{margin-right:auto;color:#53657b}
.legend-bands{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:#435a73;margin-top:12px}.swatch{display:inline-block;width:12px;height:12px;margin-right:5px;border:1px solid currentColor;vertical-align:-1px}
.finding{border-left:5px solid var(--accent);margin-bottom:16px}.finding .severity{font-size:12px;font-weight:700;color:var(--accent);letter-spacing:.04em}
.finding p{overflow-wrap:anywhere}.next-step{background:#f1f6fa;border-radius:6px;padding:12px 14px;margin-top:14px}.finding summary{cursor:pointer;color:#365a7d;margin-top:10px}
.why{padding-left:22px}.why li{padding-bottom:14px}.why a{color:#244e78}.muted{color:#53657b}.foot{margin-top:22px;font-size:13px;color:#53657b}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}th{width:32%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.toolbar{display:flex;justify-content:flex-end;gap:8px;margin:16px 0}.notice{border-left:4px solid #7891aa;padding:12px 16px;background:#f5f8fb;margin-top:12px}
@media(max-width:1050px){.overview,.pair-grid{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}}
@media(max-width:600px){main{padding:12px}.card,.status,.metric{padding:15px}.tabs button{padding:10px;font-size:14px}h1{font-size:23px}.metric strong{font-size:21px}.status-line h2{font-size:24px}}
@media print{body{background:white}main{max-width:none;padding:0}.tabs,.toolbar,.plot-tools{display:none}.plot svg{min-width:0}.tab-panel[hidden]{display:block!important}.tab-panel{margin:20px 0}.overview,.pair-grid{display:block}.card{margin:14px 0;break-inside:avoid}.finding details>*{display:block}.finding details>summary{display:none}.foot{display:none}}
'''

SCRIPT = '''
const tabs=[...document.querySelectorAll('[role=tab]')];
function selectTab(tab){tabs.forEach(t=>{const selected=t===tab;t.setAttribute('aria-selected',selected);t.tabIndex=selected?0:-1;document.getElementById(t.getAttribute('aria-controls')).hidden=!selected;});}
tabs.forEach((tab,i)=>{tab.addEventListener('click',()=>selectTab(tab));tab.addEventListener('keydown',e=>{let next;if(e.key==='ArrowRight')next=(i+1)%tabs.length;else if(e.key==='ArrowLeft')next=(i+tabs.length-1)%tabs.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=tabs.length-1;else return;e.preventDefault();selectTab(tabs[next]);tabs[next].focus();});});
document.querySelectorAll('[data-findings]').forEach(a=>a.addEventListener('click',()=>{selectTab(document.getElementById('tab-problems'));}));
document.getElementById('print-report').addEventListener('click',()=>window.print());
let printState=[];window.addEventListener('beforeprint',()=>{printState=[...document.querySelectorAll('.plot svg')].map(svg=>[svg,svg.getAttribute('viewBox')]);printState.forEach(([svg])=>svg.setAttribute('viewBox','0 0 900 540'));});window.addEventListener('afterprint',()=>printState.forEach(([svg,view])=>svg.setAttribute('viewBox',view)));
document.querySelectorAll('.plot').forEach(plot=>{const svg=plot.querySelector('svg');let scale=1,x=0,y=0,drag=null;function apply(){svg.setAttribute('viewBox',`${x} ${y} ${900/scale} ${540/scale}`);svg.style.cursor=scale>1?'grab':'default';}plot.querySelectorAll('[data-zoom]').forEach(button=>button.addEventListener('click',()=>{const old=scale;scale=button.dataset.zoom==='reset'?1:Math.min(5,Math.max(1,scale*(button.dataset.zoom==='in'?1.4:1/1.4)));x+=450/old-450/scale;y+=270/old-270/scale;if(scale===1){x=0;y=0;}apply();}));svg.addEventListener('pointerdown',e=>{if(scale===1||e.button!==0)return;drag={px:e.clientX,py:e.clientY,x,y};svg.setPointerCapture(e.pointerId);});svg.addEventListener('pointermove',e=>{if(!drag)return;const r=svg.getBoundingClientRect();x=drag.x-(e.clientX-drag.px)/r.width*900/scale;y=drag.y-(e.clientY-drag.py)/r.height*540/scale;apply();});['pointerup','pointercancel','lostpointercapture'].forEach(name=>svg.addEventListener(name,()=>drag=null));});
'''


def _escape(value):
    return html.escape(str(value), quote=True)


def _color(severity):
    return {'critical':'#b4232c','warning':'#966500','ok':'#147449','info':'#49637e'}[severity]


def write_report(output, *, source_name, mode, markers, axis, quality, findings,
                 quality_metrics, camera_only, stats, panels, warnings):
    """Write both outputs from the same findings and plot fragments; never recalculate ROM."""
    ordered=sorted(findings,key=lambda f:{'critical':0,'warning':1,'info':2}[f.severity])
    actionable=[f for f in ordered if f.severity!='info']
    notes=[f for f in ordered if f.severity=='info']
    accent=_color(quality['status'])
    scope=('CAMERA TRACE ONLY; LOAD NOT VERIFIED' if camera_only else
           'CAMERA + LOAD SCREENING; VERIFY THE CONFIGURED PROTOCOL')
    action=('REPEAT TEST — correct the identified issues before repeating.' if quality['repeat_required'] else
            'Check the flagged data; repeat if the issue cannot be resolved.' if actionable else
            'No rejection or review criterion was triggered in the stated scope.')
    scope_note=('Camera approval does not approve the complete mechanical test.' if camera_only else
                'Automated screening does not independently verify the physical setup.')
    title='5-Marker ROM Analysis'
    band_legend=''.join(f'<span><i class="swatch" style="color:{color};background:{color}22"></i>{label}</span>'
        for color,label in [('#b96c00','Marker not recognized'),('#2872c7','No recorded frame'),
                            ('#6b7280','Gap origin unknown'),('#dc2626','Other signal fault')])
    def plot(panel):
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 540" role="img" aria-label="{_escape(panel[0])}"><title>{_escape(panel[0])}</title><style>{SVG_STYLE}</style>{panel[1]}</svg>'
        return ('<div class="card plot"><div class="plot-tools"><small>Zoom, then drag to pan</small>'
                '<button data-zoom="in" aria-label="Zoom in">+</button><button data-zoom="out" aria-label="Zoom out">−</button>'
                '<button data-zoom="reset">Reset</button></div>'+svg+
                '<div class="legend-bands">'+band_legend+'</div><p class="muted"><small>Shading marks affected time windows in the five-marker dataset. See findings for the affected markers.</small></p></div>')
    cards=[]
    for index,f in enumerate(actionable,1):
        cards.append(f'<article class="card finding" id="finding-{index}" style="--accent:{_color(f.severity)}">'
            f'<div class="severity">{index:02d} · {"REJECT" if f.severity=="critical" else "CHECK"}</div><h3>{_escape(f.title)}</h3>'
            f'<p><b>Observed:</b> {_escape(f.detail)}</p><div class="next-step"><b>Next step</b><p>{_escape(f.solution)}</p></div>'
            f'<details><summary>Rule and affected intervals</summary><p>{_escape(f.code)}</p><p>{_escape(_intervals(f.intervals))}</p></details></article>')
    why=''.join(f'<li><a data-findings href="#finding-{i}">{_escape(f.title)}</a></li>' for i,f in enumerate(actionable[:3],1))
    overview=(plot(panels[0])+'<aside class="card"><h2>Why this result?</h2>'+
        ('<ol class="why">'+why+'</ol><button data-findings>Open all problems &amp; solutions ('+str(len(actionable))+')</button>' if why else
         '<p>No rejection or review finding.</p><p class="muted">Retain the raw data and verify the intended loading protocol.</p>')+
        '<div class="notice">Physical causes in the recommendations are hypotheses to inspect, not proven diagnoses.</div></aside>')
    details=[('Source',source_name),('Movement',mode),('Reference marker',markers['reference']),
        ('Upper / lower clamp',f"{markers['clamp_up']} / {markers['clamp_down']}"),
        ('Cranial / caudal vertebra',f"{markers['vertebra_cranial']} / {markers['vertebra_caudal']}"),
        ('Projection axis in reference',', '.join(f'{float(v):g}' for v in axis)),('Rule version',quality['rule_version'])]
    rows=''.join(f'<tr><th>{_escape(k)}</th><td>{_escape(v)}</td></tr>' for k,v in details)
    notes_html=''.join(f'<article class="notice"><h3>{_escape(f.title)}</h3><p>{_escape(f.detail)}</p><p><b>Next step:</b> {_escape(f.solution)}</p></article>' for f in notes)
    tabs=[('overview','Overview'),('markers','Marker pairs'),('problems',f'Problems & solutions ({len(actionable)})')]
    if len(panels)>3:tabs.append(('load','Load data'))
    tabs.append(('details','Details'))
    nav=''.join(f'<button id="tab-{key}" role="tab" aria-controls="panel-{key}" aria-selected="{str(i==0).lower()}" tabindex="{0 if i==0 else -1}">{_escape(label)}</button>' for i,(key,label) in enumerate(tabs))
    def tabpanel(key,body,selected=False):
        return f'<section class="tab-panel" id="panel-{key}" role="tabpanel" aria-labelledby="tab-{key}"{ "" if selected else " hidden"}>{body}</section>'
    metric_html=''.join(f'<div class="metric"><span>{_escape(label)}</span><strong>{_escape(value)}</strong></div>' for label,value in stats)
    content=(f'<header><h1>{title} · {_escape(mode)}</h1><p class="source">{_escape(source_name)}</p></header>'
        f'<section class="status" style="--accent:{accent}"><div class="status-line"><h2>{_escape(quality["label"])}</h2>'
        f'<span>{len([f for f in actionable if f.severity=="critical"])} reject · {len([f for f in actionable if f.severity=="warning"])} check findings</span></div>'
        f'<p class="action">{_escape(action)}</p><div class="scope"><b>{scope}</b><br>{scope_note}</div></section>'
        f'<div class="metrics">{metric_html}</div><nav class="tabs" role="tablist" aria-label="Report sections">{nav}</nav>'
        +tabpanel('overview','<div class="overview">'+overview+'</div>',True)
        +tabpanel('markers','<p class="muted">Compare each vertebra with its corresponding clamp. Dashed blue = clamp; solid orange = vertebra. Each pair has its own vertical scale.</p><div class="pair-grid">'+plot(panels[1])+plot(panels[2])+'</div>')
        +tabpanel('problems',''.join(cards) or '<section class="card"><h2>No rejection or review findings</h2><p>Approval is limited to the scope stated above.</p></section>')
        +(tabpanel('load',plot(panels[3])) if len(panels)>3 else '')
        +tabpanel('details','<section class="card"><h2>Acquisition &amp; analysis</h2><table>'+rows+'</table><h2>Scope &amp; information</h2>'+notes_html+
                  ''.join('<p class="notice">'+_escape(w)+'</p>' for w in warnings)+
                  '<details><summary>Technical screening metrics</summary><pre>'+_escape(json.dumps(quality_metrics,indent=2,ensure_ascii=False))+'</pre></details></section>')
        +'<div class="toolbar"><button id="print-report">Print / Save PDF</button><a class="button" href="'+quote(output.name)+'">Open complete SVG</a></div>'
        +'<footer class="foot">All observations remain local. Display changes do not alter raw data, calculated ROM or classification thresholds.</footer>')
    document='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+' — '+_escape(source_name)+'</title><style>'+CSS+'</style></head><body><main>'+content+'</main><script>'+SCRIPT+'</script></body></html>'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.with_suffix('.html').write_text(document,encoding='utf-8')
    _write_svg(output,source_name,mode,quality,scope,action,stats,panels,ordered)


def _intervals(intervals):
    if not intervals:return 'Whole-recording finding; no single affected interval.'
    return '; '.join(f'{a:.3g}–{b:.3g} s' for a,b in intervals)


def _write_svg(output,source,mode,quality,scope,action,stats,panels,findings):
    """Readable print/export view: all plots and findings, no hidden tab content."""
    color=_color(quality['status']);parts=[]
    def text(x,y,value,cls='body'):
        parts.append(f'<text x="{x}" y="{y}" class="{cls}">{_escape(value)}</text>')
    text(45,48,'5-Marker ROM Analysis · '+mode,'title')
    y=80
    for line in textwrap.wrap(source,125):text(45,y,line,'subtitle');y+=24
    background={'critical':'#fff0f1','warning':'#fff7df','ok':'#edf9f2'}[quality['status']]
    parts.append(f'<rect x="40" y="{y}" width="1320" height="120" rx="10" fill="{background}" stroke="{color}"/>')
    label=quality['label']+(' — REPEAT TEST' if quality['repeat_required'] else '')
    text(60,y+34,label,'heading');text(60,y+65,action);text(60,y+94,scope,'small');y+=158
    for i,(label,value) in enumerate(stats):text(45+i*330,y,label,'small');text(45+i*330,y+32,value,'heading')
    y+=65
    text(45,y,'01 / Segment motion','heading');y+=20
    # Full width overview, then two marker-pair plots; load gets a panel only when present.
    def panel(fragment,x,top,width,height):
        scale=min(width/900,height/540)
        x+=(width-900*scale)/2
        top+=(height-540*scale)/2
        parts.append(f'<g transform="translate({x:.3f} {top:.3f}) scale({scale:.6f})">{fragment}</g>')
    panel(panels[0][1],40,y,1300,620);y+=645
    text(45,y,'02 / Marker pairs · dashed blue = clamp; solid orange = vertebra','heading');y+=24
    for i,item in enumerate(panels[1:3]):panel(item[1],30+i*680,y,675,480)
    y+=500
    if len(panels)>3:
        text(45,y,'03 / Load data','heading');y+=24
        panel(panels[3][1],40,y,1300,620);y+=645
    text(45,y,'Bands: orange = marker not recognized; blue = no recorded frame; grey = origin unknown; red = other signal fault.','small');y+=48
    text(45,y,'Problems & solutions','heading');y+=24
    for index,f in enumerate(findings,1):
        lines=[(f'{index:02d} / {f.severity.upper()} · {f.title}','heading'),(f'Observed: {f.detail}','body'),('SOLUTION: '+f.solution,'body')]
        rendered=[(line,cls) for value,cls in lines for line in textwrap.wrap(value,110 if cls=='heading' else 125)]
        height=len(rendered)*26+32
        parts.append(f'<rect x="40" y="{y}" width="1320" height="{height}" rx="8" fill="#f8fafc" stroke="#dce4ed"/><rect x="40" y="{y}" width="5" height="{height}" fill="{_color(f.severity)}"/>')
        yy=y+28
        for line,cls in rendered:text(60,yy,line,cls);yy+=26
        y+=height+16
    if not findings:text(45,y,'No rejection or review criterion was triggered. Verify the intended protocol.');y+=35
    output.write_text(f'<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="1400" height="{y+35}" viewBox="0 0 1400 {y+35}"><style>{SVG_STYLE}</style><rect width="100%" height="100%" class="background"/>'+''.join(parts)+'</svg>',encoding='utf-8')
