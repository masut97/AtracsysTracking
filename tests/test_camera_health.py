from types import SimpleNamespace as NS
import threading

import pytest

from atracsys_sync.camera_health import CameraGuard, GuardedAdapter, StreamHealth, sources_stable


class Clock:
    now = 0.0
    def __call__(self):
        return self.now


def frame(index):
    return NS(frame_counter=index, device_timestamp_us=index * 10000, markers=[])


def stable_health():
    clock = Clock()
    health = StreamHealth(clock)
    for index in range(7):
        clock.now = index * 0.1
        health.observe(frame(index))
    return clock, health


def test_empty_marker_scene_is_a_healthy_stream_and_single_frame_is_not_ready():
    clock = Clock()
    health = StreamHealth(clock)
    health.observe(frame(1))
    assert not health.snapshot()['stable']
    clock, health = stable_health()
    assert health.snapshot()['stable']
    clock.now += 0.6
    assert not health.snapshot()['stable']
    health.observe(frame(10))
    assert not health.snapshot()['stable']


def test_repeated_or_older_packets_do_not_hide_a_stall():
    clock, health = stable_health()
    clock.now += 3
    assert not health.observe(frame(6))
    assert not health.observe(frame(5))
    assert health.snapshot()['stalled']


def test_reconnect_requires_fresh_warmup_and_has_a_new_epoch():
    clock, health = stable_health()
    old = health.snapshot()['epoch']
    health.reset()
    assert health.snapshot()['epoch'] != old
    assert not health.snapshot()['stable']
    assert health.observe(frame(0))


def test_cold_start_has_longer_grace_than_an_established_stream():
    clock = Clock()
    health = StreamHealth(clock)
    clock.now = 10
    assert not health.snapshot()['stable']
    assert not health.snapshot()['stalled']
    clock.now = 30
    assert health.snapshot()['stalled']
    health.observe(frame(1))
    clock.now += 3.1
    assert health.snapshot()['stalled']


def test_frame_counter_wrap_with_advancing_device_time_is_not_a_stall():
    clock, health = stable_health()
    assert health.observe(NS(frame_counter=0, device_timestamp_us=70000))


def test_single_or_stale_sensor_value_cannot_release_start_gate():
    report = dict(error='', samples=1, age_s=0.01)
    assert not sources_stable({'sensor': report})
    report.update(samples=30, age_s=2)
    assert not sources_stable({'sensor': report})
    report.update(age_s=0.01)
    assert sources_stable({'sensor': report})
    assert sources_stable({})


@pytest.mark.parametrize('duplicate', [False, True])
def test_persistent_timeouts_or_frozen_frame_escalate_to_reconnect(duplicate):
    clock, health = stable_health()
    class Timeout(Exception): pass
    class Adapter:
        def read_frame(self, **kwargs):
            if duplicate:
                return frame(6)
            raise Timeout('no frames')
    guarded = GuardedAdapter(Adapter(), health, Timeout)
    clock.now += 0.2
    with pytest.raises(Timeout):
        guarded.read_frame(timeout_ms=1000)
    clock.now += 3
    with pytest.raises(RuntimeError, match='Verbindung wird neu aufgebaut'):
        guarded.read_frame(timeout_ms=1000)


@pytest.mark.parametrize('failure', ['stalled', 'reconnected', 'pose_stopped'])
def test_recording_failure_finalizes_partial_capture_without_automatic_analysis(monkeypatch, failure):
    clock, health = stable_health()
    monkeypatch.setattr('atracsys_sync.camera_health.time.monotonic', clock)
    recorder = NS(_camera_epoch=health.snapshot()['epoch'], _camera_last_sample=clock.now,
                  _lock=threading.Lock(), _metadata={}, _last_error='')
    recorder.stats = lambda: NS(state='Aufnahme')
    actions = []
    window = NS(recorder=recorder, acquisition=NS(_camera_health=health),
                _stop_recording=lambda reason: actions.append(('stop', reason)),
                script_panel=NS(report_error=lambda message: actions.append(('error', message))))
    guard = CameraGuard.__new__(CameraGuard)
    guard.window = window
    guard.poll()
    assert not actions
    if failure == 'stalled':
        clock.now += 3.1
    elif failure == 'reconnected':
        health.reset()
    else:
        recorder._camera_last_sample -= 3.1
    guard.poll()
    assert recorder._camera_abort
    assert 'camera_stream_error' in recorder._metadata
    assert actions[0] == ('stop', 'camera_stream_error')
    assert actions[1][0] == 'error'
