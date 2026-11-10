"""A relay: a server on this machine hosting a device of a far server, so local programs share one connection to it.

The far server here serves sockets only (as one on another machine would); the relay serves its clients shared memory.
"""
import sys
import threading
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.remote import Client, Server
from pysdrlib.remote.cli import main as cli

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="ZeroMQ has no ipc:// transport on Windows")

SIM = {"realtime": True, "block_samples": 8192}


@pytest.fixture
def far():
    """The server next to the radio: sockets only, with a simulated radio it hosts (nobody controls it yet)"""
    with Server("tcp://127.0.0.1:0", devices=["sim"], lease=0, shm=False) as server:
        worker, _ = server.open_device("sim", init_kwargs=SIM)
        server.radio = worker
        yield server


@pytest.fixture
def near(tmp_path):
    with Server(f"ipc://{tmp_path}/relay", lease=0) as server:
        yield server


def local(near, id, **kw):
    dev = pysdrlib.device("remote", near.endpoint, id=id, **kw)
    dev.open()
    return dev


def wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


class TestHosting:
    def test_the_relay_hosts_the_far_device_under_its_own_name(self, far, near):
        worker = near.relay(far.endpoint, far.radio.id)
        assert worker.id == far.radio.id and near.workers[worker.id] is worker
        with Client(near.endpoint) as client:
            (listed,) = client.list()
        assert listed["id"] == far.radio.id and listed["name"] == "Simulated" and not listed["controlled"]

    def test_it_can_be_given_another_name(self, far, near):
        worker = near.relay(far.endpoint, far.radio.id, name="radio")
        assert worker.id == "radio" and "radio" in near.workers

    def test_a_name_in_use_is_not_taken_twice(self, far, near):
        first = near.relay(far.endpoint, far.radio.id, control=False)
        second = near.relay(far.endpoint, far.radio.id, control=False)
        assert first.id == far.radio.id and second.id == f"{far.radio.id}-r1"

    def test_a_device_of_a_type_can_be_opened_there(self, far, near):
        worker = near.relay(far.endpoint, open="sim")
        assert worker.upstream.token and len(far.workers) == 2
        near.stop()
        assert list(far.workers) == [far.radio.id]    # closing the relay closed what it opened

    def test_it_takes_exactly_one_target(self, far, near):
        with pytest.raises(ValueError):
            near.relay(far.endpoint)
        with pytest.raises(ValueError):
            near.relay(far.endpoint, far.radio.id, open="sim")

    def test_a_device_nobody_hosts_is_an_error(self, far, near):
        with pytest.raises(err.NoDevice):
            near.relay(far.endpoint, "sim-99")
        assert not near.workers

    def test_it_takes_control_when_free_and_watches_when_not(self, far, near):
        first = near.relay(far.endpoint, far.radio.id, name="a")
        assert first.upstream.token and far.radio.token                    # free: taken
        second = near.relay(far.endpoint, far.radio.id, name="b")
        assert not second.upstream.token                                     # now someone has it: watching
        with pytest.raises(err.Busy):
            near.relay(far.endpoint, far.radio.id, name="c", control=True)   # unless it was insisted on


class TestSharing:
    def test_any_number_of_local_programs_cost_the_far_server_one_subscriber(self, far, near):
        worker = near.relay(far.endpoint, far.radio.id)
        ctl = local(near, worker.id)
        ctl.claim()
        ctl.initialize(cf=100e6, Fs=2e6)
        ctl.start_rx()
        viewers = [local(near, worker.id) for _ in range(3)]
        try:
            assert all(v._shm_mode for v in viewers) and ctl._shm_mode
            time.sleep(0.05)
            got = [dict(zip(*self._read(dev))) for dev in (ctl, *viewers)]
            assert far.radio._subs == {far.radio.id.encode(): 1}              # four programs here, one connection there
            for other in got[1:]:                                              # and the same samples
                common = sorted(set(got[0]) & set(other))
                assert len(common) > 1000 and all(got[0][i] == other[i] for i in common[::97])
        finally:
            for v in viewers:
                v.close()
            ctl.close()

    @staticmethod
    def _read(dev, blocks=12, count=4096):
        index, samples = [], []
        for _ in range(blocks):
            x, m = dev.read(count, timeout=5, meta=True)
            if not m.gaps:
                index.extend(range(m.index, m.index + count))
                samples.extend(x.tolist())
        return index, samples

    def test_the_far_device_is_told_what_the_local_controller_asks(self, far, near):
        worker = near.relay(far.endpoint, far.radio.id)
        ctl = local(near, worker.id)
        ctl.claim()
        ctl.initialize(cf=100e6, Fs=2e6)
        assert ctl.set_rx_freq(433e6) == 433e6 and far.radio.device.get_rx_freq() == 433e6
        assert ctl.set_rx_if_gain(12) == 12 and far.radio.device.get_rx_gain_db("IF") == 12   # a method of the far driver
        assert ctl.get_rx_freq() == 433e6
        viewer = local(near, worker.id)
        assert viewer.get_rx_freq() == 433e6
        with pytest.raises(err.NotController):
            viewer.set_rx_freq(1e6)                                             # the relay's own ownership applies
        viewer.close()
        ctl.close()

    def test_local_programs_see_what_the_far_device_can_do(self, far, near):
        worker = near.relay(far.endpoint, far.radio.id)
        viewer = local(near, worker.id)
        assert viewer.caps.driver == "Simulated" and viewer.caps.stage("rx", "IF").max == 40
        assert "set_rx_if_gain" in viewer.capabilities["methods"]
        viewer.close()

    def test_a_restarted_far_stream_reaches_local_programs_from_its_start(self, far, near):
        theirs = pysdrlib.device("remote", far.endpoint, id=far.radio.id)   # someone upstream controls the radio
        theirs.open()
        theirs.claim()
        theirs.initialize(cf=100e6, Fs=2e6)
        theirs.start_rx()
        worker = near.relay(far.endpoint, far.radio.id)
        assert not worker.upstream.token                                      # so the relay only watches
        viewer = local(near, worker.id)
        try:
            viewer.read(8192, timeout=5)
            theirs.stop_rx()
            theirs.start_rx()
            for _ in range(300):
                x, m = viewer.read(4096, timeout=5, meta=True)
                if m.index == 0:
                    break
            assert m.index == 0
        finally:
            viewer.close()
            theirs.close()

    def test_transmitting_goes_through_to_the_far_device(self, far, near):
        radio, _ = far.open_device("sim", init_kwargs={**SIM, "transmit": True})
        worker = near.relay(far.endpoint, radio.id)
        ctl = local(near, worker.id)
        ctl.claim()
        try:
            ctl.initialize(cf=100e6, Fs=2e6)
            ctl.start_tx()
            sent = (np.arange(4096) % 100 / 100).astype(np.complex64)
            ctl.write(sent)
            assert wait_for(lambda: sum(len(b) for b in radio.device.transmitted[0]) >= 4096)
            np.testing.assert_allclose(np.concatenate(radio.device.transmitted[0])[:4096], sent, atol=0.005)   # int8 on the wire (1/128 steps)
        finally:
            ctl.close()


class TestWatching:
    def test_a_watching_relay_follows_the_far_device_but_cannot_change_it(self, far, near):
        theirs = pysdrlib.device("remote", far.endpoint, id=far.radio.id)
        theirs.open()
        theirs.claim()
        theirs.initialize(cf=100e6, Fs=2e6)
        worker = near.relay(far.endpoint, far.radio.id)
        ctl = local(near, worker.id)
        try:
            ctl.claim()
            assert not ctl.state["rx"]
            theirs.start_rx()                                                  # started upstream, not through the relay
            assert wait_for(lambda: ctl.refresh()["state"]["rx"])
            assert len(ctl.read(4096, timeout=5)) == 4096                    # and it can be received here
            with pytest.raises(err.NotController):
                ctl.set_rx_freq(433e6)                                         # but this relay has no control to pass on
            theirs.stop_rx()
            assert wait_for(lambda: not ctl.refresh()["state"]["rx"])
        finally:
            ctl.close()
            theirs.close()


class TestEnding:
    def test_closing_the_relay_leaves_the_far_device_free_for_others(self, far, near):
        near.relay(far.endpoint, far.radio.id)
        assert far.radio.token
        near.stop()
        assert far.radio.id in far.workers and far.radio.token is None and far.radio.device.state["open"]

    def test_a_far_server_that_stops_answering_is_noticed(self, far, near):
        worker = near.relay(far.endpoint, far.radio.id, timeout=0.3)
        client = local(near, worker.id)
        far.stop()
        assert wait_for(worker.lost.is_set, 10)
        with Client(near.endpoint) as c:
            assert c.list()[0]["lost"]                                         # and said so to whoever asks
        with pytest.raises((TimeoutError, err.RemoteError)):
            client.get_rx_freq()
        client.close()


class TestCommand:
    def test_the_command_relays_until_the_far_server_goes(self, far, tmp_path, capsys):
        bind = f"ipc://{tmp_path}/cli"
        outcome = []

        def run():
            try:
                cli(["relay", far.endpoint, far.radio.id, "--bind", bind, "--timeout", "0.3"])
            except SystemExit as exit:
                outcome.append(str(exit))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            def listed():
                try:
                    with Client(bind, 0.5) as c:
                        return [d["id"] for d in c.list()]
                except (TimeoutError, OSError):
                    return []
            assert wait_for(lambda: listed() == [far.radio.id], 10)
            viewer = pysdrlib.device("remote", bind, id=far.radio.id)
            viewer.open()
            assert viewer.caps.driver == "Simulated"
            viewer.close()
            far.stop()
            thread.join(15)
            assert outcome and "stopped answering" in outcome[0]
        finally:
            far.stop()
        assert "relaying" in capsys.readouterr().out

    def test_it_needs_a_device_or_a_type(self, far):
        with pytest.raises(SystemExit, match="device"):
            cli(["relay", far.endpoint])
        with pytest.raises(SystemExit, match="device"):
            cli(["relay", far.endpoint, far.radio.id, "--open", "sim"])
