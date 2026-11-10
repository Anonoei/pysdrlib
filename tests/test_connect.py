"""pysdrlib.connect: an opened device of a server, by type or by id"""
import sys

import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.remote import RemoteDevice, Server

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="ZeroMQ has no ipc:// transport on Windows")

SIM = {"realtime": False, "block_samples": 8192}


@pytest.fixture
def server(tmp_path):
    with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0) as s:
        yield s


def test_a_type_opens_a_new_device_you_control(server):
    sdr = pysdrlib.connect(server.endpoint, "sim", init_kwargs=SIM)
    try:
        assert isinstance(sdr, RemoteDevice) and sdr.is_controller and sdr.state["open"]
        sdr.initialize(cf=100e6, Fs=2e6)
        sdr.start_rx()
        assert len(sdr.read(4096, timeout=5)) == 4096
    finally:
        sdr.close()


def test_an_id_attaches_to_what_the_server_hosts(server):
    worker, _ = server.open_device("sim", init_kwargs=SIM)
    view = pysdrlib.connect(server.endpoint, worker.id)
    try:
        assert view.id == worker.id and not view.is_controller and view.state["open"]
        with pytest.raises(err.NotController):
            view.set_rx_freq(1e6)
    finally:
        view.close()


def test_an_id_can_be_claimed_if_it_is_free(server):
    worker, _ = server.open_device("sim", init_kwargs=SIM)
    ctl = pysdrlib.connect(server.endpoint, worker.id, claim=True)
    try:
        assert ctl.is_controller and ctl.set_rx_freq(433e6) == 433e6
        with pytest.raises(err.Busy):
            pysdrlib.connect(server.endpoint, worker.id, claim=True)   # taken now
        assert len(server.workers) == 1 and server.workers[worker.id].token                # and the failed attempt left no client
    finally:
        ctl.close() if ctl.state["open"] else None


def test_a_device_the_server_neither_hosts_nor_can_open_is_named_with_what_it_has(server):
    worker, _ = server.open_device("sim", init_kwargs=SIM)
    with pytest.raises(err.NoDevice, match=rf"hosted: {worker.id}.*types: sim"):
        pysdrlib.connect(server.endpoint, "hackrf")


def test_reuse_takes_over_a_device_nobody_controls(server):
    left_open, _ = server.open_device("sim", init_kwargs=SIM)       # as a client that died would leave it
    sdr = pysdrlib.connect(server.endpoint, "sim", reuse=True)
    try:
        assert sdr.id == left_open.id and sdr.is_controller and len(server.workers) == 1
    finally:
        sdr.release()
        sdr.detach()


def test_reuse_opens_a_new_one_when_none_is_free(server):
    taken, token = server.open_device("sim", init_kwargs=SIM, controlled=True)
    sdr = pysdrlib.connect(server.endpoint, "sim", reuse=True, init_kwargs=SIM)
    try:
        assert sdr.id != taken.id and len(server.workers) == 2
    finally:
        sdr.close()


def test_without_reuse_a_type_always_opens_a_new_device(server):
    server.open_device("sim", init_kwargs=SIM)
    sdr = pysdrlib.connect(server.endpoint, "sim", init_kwargs=SIM)
    try:
        assert len(server.workers) == 2
    finally:
        sdr.close()


def test_other_arguments_reach_the_device(server):
    sdr = pysdrlib.connect(server.endpoint, "sim", init_kwargs=SIM, transport="zmq")
    try:
        assert sdr.transport == "zmq" and sdr.type == "sim"
    finally:
        sdr.close()


def test_it_is_part_of_the_package():
    assert pysdrlib.connect is pysdrlib.devices.connect and "connect" in pysdrlib.__all__
