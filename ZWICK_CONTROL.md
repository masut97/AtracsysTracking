# Zwick / testXpert control

Open **Einstellungen → Zwick / testXpert** in the tracking application. Copy the
displayed **HKEY_CURRENT_USER** path into the external software. The path can be
changed there while recording is stopped. Existing installations keep their
configured path; changing it does not delete the old registry key.

No `ExperimentName.txt` is required. Run both applications as the same Windows
user. The tracker must be running. `AppReady=1` means that its control interface
is available, not that the camera or sensors have already passed preparation.

## Write these values from Zwick

| Value | Windows type | Example | Purpose |
| --- | --- | --- | --- |
| `RecordingName` | `REG_SZ` | `Sample01_FE` | Common file basename; an optional `.tra` or `.csv` extension is removed. |
| `RecordingDirectory` | `REG_SZ` | `D:\Measurements` | Absolute output directory. Empty uses the folder shown in the tracker settings. |
| `RecordingFrequencyHz` | `REG_SZ` | `100` | Integer decimal frequency without a unit. The maximum is the available pose rate shown by the app. |
| `StartRecord` | `REG_DWORD` | `1` | Request one recording after observing a reset to `0`. |
| `StopRecord` | `REG_DWORD` | `1` | Stop recording or cancel preparation. Tracker acknowledges it by clearing both commands. |

Existing integer `REG_DWORD` frequencies are accepted and migrated losslessly
to readable decimal text. Start/stop flags remain DWORD values; in Registry
Editor their only valid values are `0` and `1`.

## Sequence

1. Check `AppReady=1` and that `HeartbeatUnixMs` continues to change.
2. Set `StartRecord=0` and `StopRecord=0`; allow at least one tracker poll
   (use 500 ms) before raising StartRecord again. A stale `1` at application
   startup is deliberately not executed.
3. Write basename, output directory and frequency. Use the same basename and
   folder for the machine `.tra` export. Write all parameters **before** start.
4. Set `StartRecord=1` last. Wait for `RecordingState=Aufnahme`. Preparation may
   take several seconds; this is a software handshake, not a hardware trigger.
5. At test end set `StopRecord=1`. Wait for `RecordingState=Gespeichert` and read
   `LastRecording`. The tracker JSON and sensor sidecars are finalized before
   that state is reported. Automatic analysis can still be running afterwards.
6. On `RecordingState=Fehler`, inspect `LastError`, reset `StartRecord=0`, correct
   the problem, then make a fresh start request. A held `1` is never retried
   automatically after a failure or stop.

For older integrations, changing `StartRecord` from `1` to `0` still stops the
recording, including preparation. `StopRecord` takes priority over start and
parameter validation, so an invalid frequency cannot prevent stopping.

Name, directory and frequency are frozen during preparation and acquisition.
Changes written during that time do not change the active session. Existing
tracker measurements or sidecars cause a clear error; the tracker does not add
suffixes or overwrite them. A matching pre-existing machine `.tra` is allowed
and left untouched. An unavailable/unwritable output directory prevents start.

## Read-only status

`RecordingState`: `Bereit`, `Vorbereitung`, `Aufnahme` (or the recorder's current
active state), `Gespeichert`, `Gestoppt`, or `Fehler`.

`LastError`: reason for failure. `LastRecording`: last completed tracker CSV path,
retained until another recording is completed. `HeartbeatUnixMs`: host status
heartbeat, not a measurement timestamp. `AppReady` becomes `0` on normal exit;
also check the heartbeat because crashes cannot update registry status.

The Start and Stop buttons are separate and always visible together with the
frequency and marker footer. Manual output settings, the editable control path
and software updates are in **Einstellungen**. ROM report text remains English.

This interface does not change acquisition timestamp semantics or provide
physical synchronization with the machine, camera, or sensor without a trigger.
