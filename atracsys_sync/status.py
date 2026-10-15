"""Pure status formatting shared by arbitrary measurement sources and the UI."""


def format_values(values):
    values = values or {}
    if values.get("force_N") is not None and values.get("sensor_raw") is not None:
        return f"Kraft: {values['force_N']:.3f} N · Roh: {values['sensor_raw']:.3f}"
    return " · ".join(f"{key}: {value:.3f}" if value is not None else f"{key}: —"
                      for key, value in values.items()) or "—"


def synchronization_status(sync_mode, report):
    if report.get("error"):
        return "Fehler · Details öffnen"
    values = report.get("values") or {}
    if sync_mode == "atracsys_timestamp_correlated_v1":
        if not values:
            return "Warte auf FTK-EIO"
        age = values.get("adc_age_at_exposure_us")
        delta = f"{age:.0f} µs" if age is not None else "—"
        calibration = "kalibriert" if values.get("calibrated") else "Rohdaten"
        return f"EIO aktiv · Δ {delta} · {calibration}"
    if sync_mode == "best_effort_host_time_v1":
        if not values:
            return "Warte auf Sensordaten"
        delta = values.get("sensor_to_tracker_ms")
        offset = f" · Δ {delta:+.2f} ms" if delta is not None else ""
        reused = " · wiederholt" if values.get("sensor_reused") else ""
        return f"Software{offset}{reused}"
    return "Simulation" if sync_mode == "simulation" else "Keine Synchronisierung"
