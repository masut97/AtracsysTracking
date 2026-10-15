"""Deterministic simulation for testing the joint recording pipeline."""
import math

MEASUREMENT_SCRIPT = {
    "api_version": 1,
    "name": "Demo-Kraft (Simulation)",
    "columns": ["force_N"],
    "simulated": True,
    "defaults": {"amplitude_N": 10.0, "frequency_Hz": 0.5},
}


class Source:
    def __init__(self, config):
        self.config = config

    def sample(self, tick):
        return {"force_N": float(self.config["amplitude_N"]) *
                math.sin(2 * math.pi * float(self.config["frequency_Hz"]) * float(tick["time_s"]))}

    def close(self):
        pass


def open_source(config, context):
    return Source(config)
