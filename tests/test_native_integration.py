"""The native receive path (driver thread -> C++ queue -> get_samples) together with the rest of pysdrlib:
the File reader and the remote server/client. Each device in tests/devices.py runs against the real
extension's queue, fed through its `_simulate_rx` hook, so no hardware is needed (the `hardware` test
streams a real device through a server).
"""
import threading
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.base import Formats
from pysdrlib.file import File

from devices import SAMPLES, FakeDevice, fake_lib


class Queue:
    """The extension's device with its USB side stubbed out. The receive path (`set_rx_buffer`, `read_rx`,
    `rx_stats`...) is the extension's own: conversion, queue and counters in C++. `feed` plays the driver
    delivering a transfer, on a native thread. Tuning, gain and streaming calls go to a fake driver."""
    RECEIVE_PATH = ("set_rx_buffer", "reset_rx_stats", "read_rx", "read_rx_indexed", "wait_rx", "rx_stats", "rx_available",
                    "rx_slots", "read_rx_into", "rx_counters")

    def __init__(self, spec):
        self._native = spec.native.device()
        self._fake = FakeDevice(spec.fake_returns, spec.device_class.SAMPLE_FORMAT)

    def feed(self, raw):
        self._native._simulate_rx(raw, len(raw), len(raw))

    def __getattr__(self, name):
        return getattr(self._native if name in self.RECEIVE_PATH else self._fake, name)


@pytest.fixture
def sdr(spec):
    """The device's Python class over the extension's real queue (no hardware)"""
    dev = spec.device_class()
    dev.device, dev.state["open"] = Queue(spec), True
    dev.set_rx_sample_rate(spec.rate)
    return dev


class TestFile:
    def test_a_capture_reads_back_exactly(self, sdr, spec, tmp_path):
        """What get_samples returns, written as a capture (cf64: two float32, i.e. complex64), is what File reads"""
        sdr.start_rx()
        data, _ = spec.raw()
        for _ in range(40):
            sdr.device.feed(data)
        captured = sdr.get_samples()
        assert captured.dtype == np.complex64 and len(captured) == 40 * len(SAMPLES)

        path = tmp_path / "capture.cf64"
        with open(path, "wb") as f:
            Formats.cf64.write(f, captured)
        capture = File()
        capture.set_path(path)
        capture.set_fmt("cf64")
        capture.open()
        try:
            np.testing.assert_array_equal(capture.next(len(captured)), captured)
        finally:
            capture.close()

    def test_a_capture_streams_through_in_blocks(self, sdr, spec, tmp_path):
        sdr.start_rx()
        sdr.device.feed(spec.raw()[0] * 100)
        captured = sdr.get_samples()
        path = tmp_path / "capture.cf64"
        with open(path, "wb") as f:
            Formats.cf64.write(f, captured)
        capture = File()
        capture.set_path(path)
        capture.set_fmt("cf64")
        capture.open()
        blocks = list(capture.forward(40))
        capture.close()
        np.testing.assert_array_equal(np.concatenate(blocks), captured[: len(blocks) * 40])


zmq = pytest.importorskip("zmq")
from pysdrlib.remote import RemoteDevice, Server  # noqa: E402


@pytest.fixture
def served(spec, monkeypatch):
    """A server hosting the device, over a fake driver whose queue is swapped for the extension's real one"""
    from devices import fake_lib
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    with Server("tcp://127.0.0.1:0", devices=[spec.name], lease=0) as server:
        worker, _ = server.open_device(spec.name)
        queue = Queue(spec)
        worker.device.device = queue
        yield server, worker, queue


class TestRemote:
    def feeder(self, queue, spec, stop):
        data = spec.raw()[0]
        def run():
            while not stop.is_set():
                queue.feed(data * 64)
                time.sleep(0.005)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def test_samples_flow_from_the_native_queue_to_a_remote_client(self, served, spec):
        server, worker, queue = served
        stop = threading.Event()
        with RemoteDevice(server.endpoint, id=worker.id) as d:
            d.claim()
            d.set_rx_sample_rate(spec.rate)
            d.start_rx()
            t = self.feeder(queue, spec, stop)
            try:
                got = d.read(40 * len(SAMPLES), timeout=10)
            finally:
                stop.set()
                t.join()
                d.stop_rx()
        assert got.dtype == np.complex64
        np.testing.assert_allclose(got, np.tile(SAMPLES, 40), rtol=0, atol=spec.atol or 1e-7)

    @pytest.mark.parametrize("wire", ["native", "complex64", "int16"])
    def test_every_wire_format_carries_them(self, served, spec, wire):
        server, worker, queue = served
        stop = threading.Event()
        with RemoteDevice(server.endpoint, id=worker.id) as d:
            d.claim()
            d.set_wire(wire)
            d.set_rx_sample_rate(spec.rate)
            d.start_rx()
            t = self.feeder(queue, spec, stop)
            try:
                got = d.read(20 * len(SAMPLES), timeout=10)
            finally:
                stop.set()
                t.join()
                d.stop_rx()
        np.testing.assert_allclose(got, np.tile(SAMPLES, 20), rtol=0, atol=max(spec.atol, 1 / 32768))

    def test_a_stalled_native_device_is_reported_not_hidden(self, served, spec):
        """Nothing arrives: the server's pump sees StreamStalled from the native watchdog and keeps going"""
        server, worker, queue = served
        sdr = worker.device
        sdr.stall_timeout = 0.2
        sdr.set_rx_sample_rate(spec.rate)
        sdr.start_rx()
        time.sleep(0.5)
        with pytest.raises(err.StreamStalled):
            sdr.get_samples()
        assert sdr.stats.stalls == 1
        queue.feed(spec.raw()[0])   # and it recovers when data resumes
        assert len(sdr.get_samples()) == len(SAMPLES)
        sdr.stop_rx()


@pytest.mark.hardware
def test_hardware_through_a_remote_server(spec, hardware):
    """The real device, hosted by a server and read by a client, delivers its rate"""
    with Server("tcp://127.0.0.1:0", devices=[spec.name], lease=0) as server:
        with RemoteDevice(server.endpoint, spec.name) as d:
            d.initialize(cf=spec.caps.default_freq, Fs=spec.rate)
            d.start_rx()
            time.sleep(0.3)
            d.get_samples()   # discard the start-up
            start = time.perf_counter()
            got = 0
            while time.perf_counter() - start < 1.0:
                got += len(d.get_samples(timeout=0.1))
            elapsed = time.perf_counter() - start
            d.stop_rx()
    assert got > 0.5 * spec.rate * elapsed, f"{got / elapsed / 1e6:.2f} MS/s of {spec.rate / 1e6:.2f}"
