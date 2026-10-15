"""Compatibility gate for the inherited tracker API, before any hooks are installed."""
import inspect


def validate_tracker_contract(recording, window_class, bridge_class=None):
    contracts = [
        (recording.RecordingWorker, {
            "start_session": {"name", "target", "metadata", "sample_frequency_hz"},
            "enqueue": {"frame"}, "_write_frame": {"writer", "frame"},
            "stop": set(), "stats": set(),
        }),
        (window_class, {name: set() for name in (
            "__init__", "_start_recording", "_toggle_recording",
            "_refresh_recording_status", "closeEvent",
        )}),
    ]
    if bridge_class is not None:
        contracts.append((bridge_class, {"connect": set()}))
    for owner, methods in contracts:
        for name, parameters in methods.items():
            method = getattr(owner, name, None)
            try:
                compatible = callable(method) and parameters.issubset(inspect.signature(method).parameters)
            except (TypeError, ValueError):
                compatible = False
            if not compatible:
                raise RuntimeError(f"Diese Tracker-Version passt nicht zur Messskript-Erweiterung: {name}.")
    if not isinstance(getattr(recording, "CSV_COLUMNS", None), list):
        raise RuntimeError("Diese Tracker-Version besitzt keine kompatiblen CSV-Spalten.")
