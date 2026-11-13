"""Choosing a radio by program, serial, and what it needs (pysdrlib.remote.choose and the server's open)"""
import sys

import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.base.caps import Capabilities, Range
from pysdrlib.devices import DeviceInfo
from pysdrlib.remote import Client, Server, choose

SIM = {"realtime": False, "block_samples": 8192}


def caps(freq=((1e6, 6e9),), rates=((1e6, 20e6),), tx=0, rx=1, bw=()):
    return Capabilities(driver="x", channels={"rx": rx, "tx": tx}, freq=[Range(*r) for r in freq],
                        rates=[Range(*r) for r in rates], bandwidths={"rx": [Range(*r) for r in bw], "tx": []})


# --- the pure part --- #
def test_a_radio_meets_a_need_inside_its_limits():
    c = caps()
    assert choose.met(c, {"freq": 433e6, "rate": 10e6, "bandwidth": 10e6, "channels": 1})
    assert choose.met(c, {"freq": [88e6, 108e6]})


def test_each_limit_it_misses_is_named():
    c = caps()
    bad = choose.problems(c, {"freq": 7e9, "rate": 40e6, "channels": 2, "tx": True})
    assert len(bad) == 4
    assert any("cannot tune 7 GHz" in b for b in bad) and any("cannot transmit" in b for b in bad)


def test_a_span_must_fit_one_tuning_range():
    c = caps(freq=((1e6, 100e6), (200e6, 300e6)))
    assert choose.met(c, {"freq": [10e6, 90e6]})
    assert not choose.met(c, {"freq": [90e6, 210e6]})


def test_bandwidth_is_the_filter_where_there_is_one_else_the_rate():
    assert choose.met(caps(bw=((1e6, 8e6),)), {"bandwidth": 8e6})
    assert not choose.met(caps(bw=((1e6, 8e6),)), {"bandwidth": 10e6})
    assert not choose.met(caps(), {"bandwidth": 30e6})


def test_a_limit_a_radio_does_not_declare_is_met():
    assert choose.met(Capabilities(driver="x"), {"freq": 1e12, "rate": 1e12})


def test_a_span_crosses_ranges_that_touch():
    c = caps(freq=((1e6, 100e6), (100e6, 6e9)))
    assert choose.met(c, {"freq": [50e6, 200e6]})
    assert choose.granted(c, {"freq": [50e6, 200e6]}) == {"freq": [50e6, 200e6]}


def test_granted_span_stays_in_one_stretch():
    c = caps(freq=((1e6, 100e6), (200e6, 6e9)))
    (lo, hi), = choose.granted(c, {"freq": [50e6, 300e6]}).values()
    assert choose.met(c, {"freq": [lo, hi]})


def test_unknown_capabilities_rank_after_known():
    unknown, fits, off = Capabilities(driver="x"), caps(), caps(rates=((1e6, 5e6),))
    want = {"rate": 20e6}
    assert choose.rank(fits, want) < choose.rank(off, want) < choose.rank(unknown, want)
    assert choose.rank(None, want) > choose.rank(off, want)


def test_more_rate_than_wanted_is_no_miss():
    fast = caps(rates=((2e6, 20e6),))
    assert choose.score(fast, {"rate": 1e6})[0] == 0
    assert 0 < choose.shortfall(fast, {"rate": 1e6}) < 0.2


def test_fewest_keys_missed_beats_smallest_total():
    # two keys each a little off, against one key missed by half
    two = caps(freq=((1e6, 90e6),), rates=((1e6, 18e6),))
    one = caps(freq=((1e6, 6e9),), rates=((1e6, 10e6),))
    want = {"freq": 100e6, "rate": 20e6}
    assert choose.score(two, want)[0] == 2 and choose.score(one, want)[0] == 1
    assert choose.rank(one, want) < choose.rank(two, want)


def test_a_radio_that_can_transmit_beats_one_that_cannot():
    tx, rx = caps(rates=((2e6, 20e6),), tx=1), caps(rates=((225e3, 3.2e6),))
    want = {"rate": 1e6, "tx": True}
    assert choose.rank(tx, want) < choose.rank(rx, want)


def test_granted_is_the_nearest_it_can_do():
    g = choose.granted(caps(), {"freq": 7e9, "rate": 40e6, "channels": 3})
    assert g == {"freq": 6e9, "rate": 20e6, "channels": 1}


def test_shortfall_orders_radios():
    near, far = caps(rates=((1e6, 20e6),)), caps(rates=((1e6, 5e6),))
    want = {"rate": 20e6}
    assert choose.shortfall(near, want) == 0 < choose.shortfall(far, want)


def test_nonsense_is_refused():
    for spec in ({"colour": 1}, {"freq": [2, 1]}, {"rate": -1}, {"channels": 0}, [1], {"rate": float("nan")},
                 {"rate": float("inf")}, {"rate": "fast"}, {"tx": "maybe"}, {"channels": 1.9}, {"freq": [1]}, {"freq": ["a", 2]}):
        with pytest.raises(ValueError):
            choose.normalize(spec)


def test_strings_from_a_command_line_are_understood():
    assert choose.normalize({"tx": "false", "freq": ["9e6", "10e6"], "rate": "2e6"}) == {"tx": False, "freq": [9e6, 10e6], "rate": 2e6}
    assert choose.normalize({"tx": 1, "channels": 2.0}) == {"tx": True, "channels": 2}


def test_the_cli_parses_a_span_and_a_flag():
    from pysdrlib.remote import cli
    assert cli._need("freq=430e6:440e6") == ("freq", [430e6, 440e6])
    assert cli._need("tx=true") == ("tx", "true")
    assert choose.normalize(dict([cli._need("freq=430e6:440e6"), cli._need("tx=true")])) == {"freq": [430e6, 440e6], "tx": True}


def test_a_rate_must_be_one_the_radio_can_run_at():
    discrete = caps(rates=((8e6, 8e6), (16e6, 16e6), (32e6, 32e6)))
    assert choose.met(discrete, {"rate": 16e6})
    assert choose.problems(discrete, {"rate": 20e6}) == ["cannot run at 20 MHz (nearest is 16 MHz)"]
    assert choose.problems(caps(rates=((2e6, 20e6),)), {"rate": 1e6})      # below its lowest
    assert choose.met(caps(rates=((2e6, 20e6),)), {"rate": 20e6})
    assert choose.met(caps(rates=((0, 20e6, 1e6),)), {"rate": 3e6}) and not choose.met(caps(rates=((0, 20e6, 1e6),)), {"rate": 3.5e6})


def test_rtl_declares_its_rate_gap():
    from pysdrlib.rtl_sdr.rtlsdr import RTLSDR
    c = RTLSDR.CAPS
    assert choose.problems(c, {"rate": 2.4e6}) == []
    assert choose.granted(c, {"rate": 500e3})["rate"] in (300e3, 900_001)


# --- through a server --- #
pytestmark_zmq = pytest.mark.skipif(sys.platform == "win32", reason="ZeroMQ has no ipc:// transport on Windows")


@pytest.fixture
def server(tmp_path, monkeypatch):
    units = [DeviceInfo("sim", "Sim A", "AAA", 0, {"serial": "AAA"}), DeviceInfo("sim", "Sim B", "BBB", 1, {"serial": "BBB"})]
    monkeypatch.setattr("pysdrlib.remote.server.devices.find", lambda *a, **k: list(units))
    with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0) as s:
        yield s


def connect(server, *args, **kw):
    kw.setdefault("init_kwargs", SIM)
    return pysdrlib.connect(server.endpoint, *args, **kw)


@pytestmark_zmq
def test_the_program_is_shown_to_whoever_lists(server):
    sdr = connect(server, "sim", program="pyspecan")
    try:
        with Client(server.endpoint) as c:
            (d,) = c.list()
        assert d["program"] == "pyspecan" and d["controlled"]
        sdr.release()
        with Client(server.endpoint) as c:
            assert c.list()[0]["program"] is None
    finally:
        sdr.close()


@pytestmark_zmq
def test_a_serial_picks_the_unit_and_it_is_then_taken(server):
    b = connect(server, serial="BBB", program="rec")
    try:
        with Client(server.endpoint) as c:
            avail = {a["serial"]: a["hosted"] for a in c.available()}
        assert avail == {"AAA": None, "BBB": b.id}
        a = connect(server, "sim", serial="AAA")      # BBB is in use: only AAA fits anyway
        a.close()
        with pytest.raises(err.NoDevice):
            connect(server, serial="BBB")             # nobody can have BBB twice
    finally:
        b.close()


@pytestmark_zmq
def test_no_driver_needed_when_the_need_picks_it(server):
    sdr = connect(server, need={"freq": 433e6, "rate": 2e6}, want={"rate": 10e6}, program="x")
    try:
        assert sdr.type == "sim" and sdr.is_controller
        assert sdr.granted["rate"] == 10e6
        sdr.initialize(cf=433e6, Fs=10e6)
    finally:
        sdr.close()


@pytestmark_zmq
def test_a_need_nothing_meets_names_why(server):
    with pytest.raises(err.NoDevice, match=r"sim: .*cannot tune"):
        connect(server, need={"freq": 50e9})
    assert not server.workers      # nothing was opened for it


@pytestmark_zmq
def test_a_free_hosted_radio_is_taken_over_for_a_need(server, monkeypatch):
    monkeypatch.setattr("pysdrlib.remote.server.devices.find", lambda *a, **k: [])    # no other unit to open
    worker, _ = server.open_device("sim", init_kwargs=SIM)      # a client left it open
    sdr = connect(server, need={"rate": 1e6}, program="late")
    try:
        assert sdr.id == worker.id and worker.program == "late"
    finally:
        sdr.close()


@pytestmark_zmq
def test_attaching_and_claiming_say_who(server, caplog):
    worker, _ = server.open_device("sim", init_kwargs=SIM)
    ctl = connect(server, worker.id, claim=True, program="tuner", init_kwargs=None)
    try:
        assert worker.program == "tuner"
    finally:
        ctl.close()


@pytestmark_zmq
def test_a_need_the_open_unit_cannot_meet_moves_on(server, monkeypatch):
    """The class says it fits, the opened unit refines that and does not: nothing is left open and the error says why"""
    import pysdrlib.remote.server as srv
    real = srv.choose.problems
    calls = []

    def problems(c, spec):
        calls.append(c)
        return ["refined away"] if len(calls) > 2 else real(c, spec)     # the two unit checks pass, the live ones do not
    monkeypatch.setattr(srv.choose, "problems", problems)
    with pytest.raises(err.NoDevice, match="refined away"):
        connect(server, need={"rate": 1e6})
    assert not server.workers


class _Narrow:
    """Makes the first unit opened run at 1 MS/s only once open, as a driver with discrete rates does"""
    done = False


@pytestmark_zmq
def test_an_open_unit_that_does_less_of_want_hands_over_to_the_next(server, monkeypatch):
    from pysdrlib.sim.sim import Sim
    real, state = Sim._capabilities, _Narrow()

    def narrow(self, c):
        c = real(self, c)
        if not state.done:
            state.done = True
            c.rates = [Range(1e6, 1e6)]
        return c
    monkeypatch.setattr(Sim, "_capabilities", narrow)
    sdr = connect(server, need={"rate": 1e6}, want={"rate": 10e6}, program="x")
    try:
        assert sdr.granted["rate"] == 10e6
        assert len(server.workers) == 1     # the one that could not do it was closed
    finally:
        sdr.close()


@pytestmark_zmq
def test_a_unit_that_does_less_is_kept_when_nothing_is_better(server, monkeypatch):
    from pysdrlib.sim.sim import Sim
    real = Sim._capabilities

    def narrow(self, c):
        c = real(self, c)
        c.rates = [Range(1e6, 1e6)]
        return c
    monkeypatch.setattr(Sim, "_capabilities", narrow)
    sdr = connect(server, want={"rate": 10e6}, program="x", serial="AAA")
    try:
        assert sdr.granted["rate"] == 1e6
    finally:
        sdr.close()
