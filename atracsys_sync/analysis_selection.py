"""Curated analysis entry points; helper modules are never executable choices."""
from pathlib import Path


PRIMARY = (
    ('auto_auswertung.py', 'Automatisch erkennen (empfohlen)'),
    ('rom_auswertung.py', 'ROM – fünf Marker'),
    ('trochanter_auswertung.py', 'Trochanter – Belastungsversuch'),
    ('free_auswertung.py', 'Freie Marker-Auswertung'),
)
SPECIAL = (
    ('auswertung.py', 'Monotest – drei Marker'),
    ('Auswertung_299_331_332.py', 'Markerprüfung – 299 / 331 / 332'),
    ('Auswertung_299_331_333.py', 'Markerprüfung – 299 / 331 / 333'),
    ('Auswertung_340_341_342.py', 'Markerprüfung – 340 / 341 / 342'),
)


def entries(directory, selected=None, advanced=False):
    """Keep an existing/imported executable selection without silently changing it."""
    directory = Path(directory).resolve()
    selected = Path(selected).resolve() if selected else None
    available = {p.name: p.resolve() for p in directory.glob('*.py')
                 if not p.name.startswith('_') and not p.stem.endswith('_core')
                 and (p.name != 'rom_csv_nachauswertung.py' or p.resolve() == selected)}
    result = []
    for name, label in PRIMARY + SPECIAL:
        path = available.pop(name, None)
        if path is not None and (advanced or (name, label) in PRIMARY or path == selected):
            result.append((label, path))
    # Imported and older local entry points remain available in the expanded list.
    for name, path in sorted(available.items(), key=lambda item: item[0].casefold()):
        if advanced or path == selected:
            result.append(('Zusätzliches Skript: ' + name, path))
    return result


def populate(window, selected=None):
    from PySide6.QtCore import Qt
    configured = selected or window._configured_analysis_script()
    directory = window.project_root / 'analysis_scripts'
    toggle = getattr(window, 'analysis_profiles_toggle', None)
    choices = entries(directory, configured, bool(toggle and toggle.isChecked()))
    combo = window.analysis_combo
    previous = combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem('Keine automatische Auswertung', '')
        selected_index = 0
        for label, path in choices:
            combo.addItem(label, str(path))
            combo.setItemData(combo.count()-1, path.name, Qt.ItemDataRole.ToolTipRole)
            if configured and path == Path(configured).resolve():
                selected_index = combo.count()-1
        combo.setCurrentIndex(selected_index)
    finally:
        combo.blockSignals(previous)


def add_profile_toggle(window, layout):
    from PySide6.QtWidgets import QCheckBox, QLabel, QHBoxLayout
    toggle = QCheckBox('Weitere Profile und eigene Skripte anzeigen', window)
    toggle.setObjectName('analysisProfilesToggle')
    toggle.setToolTip('Monotest und Markerprüfungen. Eine bestehende Auswahl bleibt auch bei eingeklappter Liste erhalten.')
    window.analysis_profiles_toggle = toggle
    toggle.toggled.connect(lambda _checked: populate(window, window.analysis_combo.currentData()))
    row = QHBoxLayout()
    row.addWidget(toggle); row.addStretch(1)
    from .free_analysis_ui import FreeAnalysisController
    window.free_analysis_controller = FreeAnalysisController(window, row)
    layout.addLayout(row)
    hint = QLabel('Automatisch erkennt ROM, Trochanter und Monotest anhand der Marker. '
                  'Historische ROM-Dateien können unten separat nachausgewertet werden.', window)
    hint.setObjectName('mutedLabel')
    hint.setWordWrap(True)
    layout.addWidget(hint)
    populate(window, window.analysis_combo.currentData())
