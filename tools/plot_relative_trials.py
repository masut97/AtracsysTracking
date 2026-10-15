"""Recreate the original Trochanter plot style from verified per-recording results."""
from pathlib import Path
import inspect
import sys
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--app", type=Path, required=True)
parser.add_argument("--base", type=Path, required=True, help="Trochanter parent folder")
parser.add_argument("--results", type=Path, required=True, help="Per-recording analysis folder")
parser.add_argument("--specimen", required=True)
args = parser.parse_args()
APP = args.app.resolve()
sys.path.insert(0, str(APP / "analysis_vendor"))
import trochanter_analysis_core as core

BASE = args.base.resolve()
RESULTS = args.results.resolve()
OUTPUT = BASE / f"{args.specimen}_analysis"
PLOTS = OUTPUT / "plots"
PLOTS.mkdir(parents=True, exist_ok=True)
core.SPECIMEN = args.specimen
core.DECLARED_FAILURES = {}
core.PLOT_LABELS.update(double2="Double 2", double3="Double 3")
core.PLOT_COLORS.update(double2="#3264ef", double3="#009d79")

# Reuse the original drawing functions; change only the displayed series keys.
for name in ("save_cycle_metric_plot_png", "save_grouped_bar_plot_png"):
    source = inspect.getsource(getattr(core, name))
    source = source.replace('("8er", "double", "screw")', '("double2", "double3", "double")')
    source = source.replace('{"8er": -115, "double": 0, "screw": 115}', '{"double2": -115, "double3": 0, "double": 115}')
    exec(compile(source, str(__file__), "exec"), core.__dict__)

summaries, cycles = [], []
for folder, series, original in (
    ("150N_double2", "double2", f"{args.specimen}_double2_150N.csv"),
    ("150N_double3", "double3", f"{args.specimen}double3_150N.csv"),
    ("300N_double", "double", f"{args.specimen}double_300N.csv"),
    ("450N_double", "double", f"{args.specimen}_double_450N.csv"),
):
    directory = RESULTS / folder / f"{args.specimen}_analysis"
    for filename, collection in (("summary.csv", summaries), ("cycle_metrics.csv", cycles)):
        frame = core.pd.read_csv(directory / filename)
        frame["cerclage"] = series
        frame["original_file"] = original
        collection.append(frame)

summary = core.pd.concat(summaries, ignore_index=True)
cycle = core.pd.concat(cycles, ignore_index=True)
summary.to_csv(OUTPUT / "summary.csv", index=False)
cycle.to_csv(OUTPUT / "cycle_metrics.csv", index=False)
for force in (150, 300, 450):
    core.save_cycle_metric_plot_png(
        cycle, force, "micromotion_mm",
        f"Relative translation per loading cycle · {force} N",
        "Trochanter–femur translation excursion per cycle [mm]", "mm",
        PLOTS / f"translation_zyklen_{force}N.png",
    )
    core.save_cycle_metric_plot_png(
        cycle, force, "rotation_micromotion_deg",
        f"Relative rotation per loading cycle · {force} N",
        "Trochanter–femur rotation excursion per cycle [deg]", "°",
        PLOTS / f"rotation_zyklen_{force}N.png",
    )
for column, title, label, unit, filename in (
    ("translation_first_to_last_recording_mm", "Net displacement: first to last valid position", "Relative trochanter–femur translation [mm]", "mm", "translation_erste_bis_letzte_position.png"),
    ("rotation_first_to_last_recording_deg", "Net rotation: first to last valid position", "Relative trochanter–femur rotation [deg]", "°", "rotation_erste_bis_letzte_position.png"),
):
    core.save_grouped_bar_plot_png(summary, column, title, label, unit, PLOTS / filename)

(OUTPUT / "HINWEISE.txt").write_text(
    "Originaler Trochanter-Diagrammstil; unveränderte Kennwerte aus der Auswertung.\n"
    "Double 2 und Double 3 sind getrennte 150-N-Aufnahmen; Double bezeichnet 300/450 N.\n"
    "Die Zyklusachse zeigt automatisch erkannte aktive Zyklen, keine bestätigten Maschinenzyklen.\n"
    "Nur 6,45 % gültige Marker bei Double 2; Ergebnisse stark eingeschränkt belastbar.\n"
    "Double 3: Nur 11 aktive Zyklen erkannt trotz langer Aufnahme.\n"
    "Die Achse bis 5000 Zyklen entspricht der Referenzdarstellung und bestätigt keine 5000 gefahrenen Zyklen.\n",
    encoding="utf-8",
)
print(f"Created {len(list(PLOTS.glob('*.png')))} plots in {PLOTS}")
