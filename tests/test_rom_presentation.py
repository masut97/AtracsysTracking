"""Report navigation, evidence visibility and truthful display reduction."""
import importlib
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import pytest


@pytest.fixture
def rom(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'analysis_scripts'))
    return importlib.import_module('rom_auswertung')


class Markup(HTMLParser):
    def __init__(self,source):
        super().__init__();self.tags=[];self.feed(source)

    def handle_starttag(self,tag,attrs):
        self.tags.append((tag,dict(attrs)))


def report(rom,tmp_path,machine=False):
    times=tuple(i*.05 for i in range(201))
    signal=tuple(math.sin(t) for t in times)
    curves={key:signal for key in ('clamp_up','clamp_down','vertebra_cranial','vertebra_caudal','clamp_segmental','segmental')}
    data=rom.RomTrackingData(times,{},len(times),0,'synthetic')
    findings=[rom.QualityFinding('critical','fixture_test','Fixation needs inspection','Observed movement differs.','Check fixation, then repeat.'),
              rom.QualityFinding('info','scope_test','Scope note','Physical causes are hypotheses.','Inspect the original data.')]
    zwick=rom.RomZwickData(times,signal,signal,signal,'moment','angle','Nm','deg',Path('synthetic.tra')) if machine else None
    markers=dict(reference=101,clamp_up=102,clamp_down=103,vertebra_cranial=104,vertebra_caudal=105)
    output=tmp_path/'result.svg'
    rom.render_figure(output,'specimen <unsafe> & "quoted".csv',data,curves,'AR',[0,0,1],markers,zwick,None if not machine else
        rom.RomMetrics(1.,1.,-1.,1.,1.,0.,None,0.),findings,{},machine,rom.DEFAULT_CONFIG,[])
    return output


@pytest.mark.parametrize('machine',[False,True])
def test_report_has_accessible_tabs_and_no_empty_machine_chart(rom,tmp_path,machine):
    output=report(rom,tmp_path,machine)
    source=output.with_suffix('.html').read_text(encoding='utf-8')
    markup=Markup(source)
    tabs=[attrs for _,attrs in markup.tags if attrs.get('role')=='tab']
    panels={attrs['id']:attrs for _,attrs in markup.tags if attrs.get('role')=='tabpanel'}
    assert sum(t['aria-selected']=='true' for t in tabs)==1
    assert all(t['aria-controls'] in panels for t in tabs)
    assert len(tabs)==(5 if machine else 4)
    assert ('panel-load' in panels)==machine
    assert 'fixture_test' in source and 'Check fixation, then repeat.' in source
    assert 'Observed:' in source and 'Next step' in source
    assert 'CAMERA TRACE ONLY; LOAD NOT VERIFIED' in source if not machine else 'CAMERA + LOAD SCREENING' in source
    assert 'specimen <unsafe>' not in source and 'specimen &lt;unsafe&gt;' in source
    assert 'Clamp (102)' in source and 'Caudal vertebra (105)' in source
    assert 'window.print()' in source and 'beforeprint' in source


def test_static_export_includes_all_plots_findings_and_scope_without_nested_svg(rom,tmp_path):
    output=report(rom,tmp_path)
    svg=ET.fromstring(output.read_text(encoding='utf-8'))
    ns={'s':'http://www.w3.org/2000/svg'}
    assert not svg.findall('.//s:svg',ns)  # supported by the packaged Qt SVG renderer
    assert len(svg.findall('.//s:path',ns))==6
    text=' '.join(svg.itertext())
    assert 'Not approved — REPEAT TEST' in text and 'LOAD NOT VERIFIED' in text
    assert 'SOLUTION: Check fixation, then repeat.' in text and 'Scope note' in text


def test_display_reduction_preserves_a_narrow_extreme_and_breaks_at_a_gap(rom):
    times=list(range(5000));values=[0.]*5000;values[1]=10.
    times=[t if t<2500 else t+100 for t in times]
    source=rom._svg_multi_plot([('Vertebra',times,values,'#c45c13')],(85,110,750,345),'Time','Motion','Synthetic',split_gap_factor=1.75)
    svg=ET.fromstring('<svg>'+source+'</svg>')
    path=next(n for n in svg if n.tag=='path')
    points=re.findall(r'[ML] ([\d.-]+) ([\d.-]+)',path.attrib['d'])
    _low,high,_=rom.common._nice_ticks(values)
    low=rom.common._nice_ticks(values)[0]
    expected_y=110+345-(10-low)/(high-low)*345
    assert any(float(y)==pytest.approx(expected_y,abs=.01) for _x,y in points)
    assert path.attrib['d'].count('M ')==2


@pytest.mark.parametrize('value,expected',[(-.5,'-0.5'),(-.02,'-0.02'),(-0.,'0'),(.5,'0.5')])
def test_axis_labels_preserve_negative_fractional_values(rom,value,expected):
    assert rom.common._format_tick(value)==expected
