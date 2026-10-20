from types import SimpleNamespace as NS
import threading
import pytest

from atracsys_sync.camera_health import StreamHealth, GuardedAdapter, CameraGuard, OVERFLOW_FLAG


class Clock:
    now=0.
    def __call__(self): return self.now


class Timeout(Exception): pass


def setup():
    clock=Clock();health=StreamHealth(clock)
    header=NS(state=2,reserved0=OVERFLOW_FLAG,heartbeat_ns=1)
    process=NS(poll=lambda:None)
    class Adapter:
        _process=process
        def _header(self):return header
        def read_frame(self):raise Timeout('no new valid image')
    return clock,health,header,process,GuardedAdapter(Adapter(),health,Timeout)


def test_continuous_overflow_keeps_session_alive_without_publishing_measurements():
    clock,health,header,process,adapter=setup()
    epoch=health.epoch
    for i in range(100):
        clock.now=i*.5;header.heartbeat_ns=i+1
        with pytest.raises(Timeout):adapter.read_frame()
        assert health.snapshot()['segmentation_overflow']
        assert not health.snapshot()['stable']
        assert not health.snapshot()['stalled']
        assert health.snapshot()['frames']==0
    assert health.epoch==epoch


@pytest.mark.parametrize('failure',['frozen','dead','reconnecting','other_error'])
def test_overflow_does_not_mask_dead_or_reconnecting_bridge(failure):
    clock,health,header,process,adapter=setup()
    health.observe(NS(frame_counter=1,device_timestamp_us=1))
    with pytest.raises(Timeout):adapter.read_frame()
    clock.now=4
    if failure=='dead':process.poll=lambda:1
    if failure=='reconnecting':header.state=3
    if failure=='other_error':header.reserved0=0
    with pytest.raises(RuntimeError,match='Verbindung'):adapter.read_frame()


def test_good_frame_clears_overflow_without_new_epoch():
    clock,health,header,process,adapter=setup()
    with pytest.raises(Timeout):adapter.read_frame()
    epoch=health.epoch
    frame=NS(frame_counter=50,device_timestamp_us=12345)
    adapter.adapter.read_frame=lambda:frame
    clock.now=1
    assert adapter.read_frame() is frame
    assert not health.snapshot()['segmentation_overflow']
    assert health.epoch==epoch


def test_recording_continues_with_explicit_gap_and_stops_on_real_disconnect(monkeypatch):
    clock,health,header,process,adapter=setup()
    health.observe(NS(frame_counter=1,device_timestamp_us=1))
    monkeypatch.setattr('atracsys_sync.camera_health.time.monotonic',clock)
    actions=[]
    recorder=NS(_camera_epoch=health.epoch,_camera_last_sample=0,_lock=threading.Lock(),_metadata={})
    recorder.stats=lambda:NS(state='Aufnahme')
    window=NS(recorder=recorder,acquisition=NS(_camera_health=health),
              _set_recording_message=lambda *args:None,
              _stop_recording=lambda reason:actions.append(reason),
              script_panel=NS(report_error=lambda message:None))
    guard=CameraGuard.__new__(CameraGuard);guard.window=window
    for i in range(12):
        clock.now=i*.5;header.heartbeat_ns=i+1
        with pytest.raises(Timeout):adapter.read_frame()
        guard.poll()
    assert not actions
    assert recorder._metadata['camera_data_incomplete']
    assert len(recorder._metadata['camera_segmentation_overflows'])==1
    clock.now+=4
    health.clear_overflow()
    guard.poll()
    assert actions==['camera_stream_error']
