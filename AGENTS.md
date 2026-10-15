# Maintenance rules

- Preserve raw measurements, their JSON/log sidecars, geometry and calibration files.
- Do not use live devices for automated tests; use synthetic fixtures and an isolated app copy.
- Do not change timestamp semantics or claim hardware synchronization without a trigger.
- Keep source changes in this repository; build into a new directory, never edit a deployed copy.
- Keep licensed SDK/base EXE, vendor libraries, local settings and real measurements out of Git.
- Review session lifecycle, file finalization and package path changes with independent tests.
- Run `python -m pytest tests -q`; run packaged mock selftest for runtime/UI changes.
- Never rebuild from an unverified older source tree over a newer installation.
