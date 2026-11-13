"""UHD's X310 and X410 over a fake driver, and finding them: no hardware, no network, no libuhd that has these radios.

What B210 does is in test_uhd_api.py; this is what is particular to the X series and to finding the families.
"""
from types import SimpleNamespace

import pytest

import pysdrlib
from pysdrlib import devices, err

pytest.importorskip("pysdrlib.uhd", exc_type=ImportError)

from devices import FakeDevice
from pysdrlib import uhd as uhd_package
from pysdrlib.uhd import uhd as uhd_module
from pysdrlib.uhd import lib as real_lib

B200 = {"type": "b200", "name": "MyB210", "serial": "31B92DD", "product": "B210"}
X300 = {"type": "x300", "addr": "192.168.10.2", "name": "", "serial": "3125A1F", "product": "X310"}
X400 = {"type": "x4xx", "addr": "192.168.10.3", "name": "", "serial": "32C0FFE", "product": "x410"}
N300 = {"type": "n3xx", "addr": "192.168.10.9", "product": "n310"}   # a radio there is no class for

def fake(channels=2, rx_gain=(0.0, 31.5, 0.5), freq=(10e6, 6e9)):
    state = {"rate": 10e6}
    return {
        "num_rx_channels": lambda: channels, "num_tx_channels": lambda: channels,
        "set_rx_rate": lambda rate, c=0: state.update(rate=rate), "get_rx_rate": lambda c=0: state["rate"],
        "set_tx_rate": lambda rate, c=0: state.update(rate=rate), "get_tx_rate": lambda c=0: state["rate"],
        "rx_freq_range": lambda c=0: freq, "tx_freq_range": lambda c=0: freq,
        "rx_gain_range": lambda c=0: list(rx_gain), "tx_gain_range": lambda c=0: [0.0, 31.5, 0.5],
        "rx_bandwidth_range": lambda c=0: (20e6, 160e6), "tx_bandwidth_range": lambda c=0: (20e6, 160e6),
        "rx_antennas": lambda c=0: ["TX/RX", "RX2"], "tx_antennas": lambda c=0: ["TX/RX"],
        "clock_sources": lambda: ["internal", "external", "gpsdo"], "info": lambda: {"mboard_serial": "3125A1F"},
        "get_time_source": lambda: "internal", "mboard_sensors": lambda: {}, "rx_sensors": lambda c=0: {}, "tx_sensors": lambda c=0: {},
    }

@pytest.fixture
def opened(monkeypatch):
    """Open a class over a FakeDevice: opened(X310, **kwargs) -> device; what UHD was asked to open is `opened.args`"""
    asked, radio = [], {}
    def open_(args=""):
        asked.append(args)
        return FakeDevice(fake(**radio), uhd_module.X310.SAMPLE_FORMAT)
    monkeypatch.setattr(uhd_module, "lib", SimpleNamespace(uhd=SimpleNamespace(open=open_, init=lambda: None), err=real_lib.err))
    monkeypatch.setattr(uhd_module._XSeries, "_warned", True)
    def make(cls, **kwargs):
        sdr = cls()
        sdr.open(**kwargs)
        return sdr
    make.args = asked
    make.fake = radio   # what the radio is like: arguments of `fake`, set before opening
    return make


# ---------------------------------------------------------------- which driver is which
class TestDrivers:
    def test_the_families_are_drivers_of_their_own(self):
        assert {"uhd", "x310", "x410"} <= set(devices.ls())
        assert devices.get("x310").Device is uhd_module.X310 and devices.get("x410").Device is uhd_module.X410
        assert isinstance(pysdrlib.device("x310"), uhd_module.X310)
        assert pysdrlib.device("uhd").__class__ is uhd_module.B210 and uhd_package.Device is uhd_module.B210

    def test_they_cannot_be_taken_by_a_plugin(self):
        with pytest.raises(ValueError, match="x310"):
            pysdrlib.register("x310", uhd_module.B210)
        assert devices.unregister("x310") is False

    @pytest.mark.parametrize("address, driver", [(B200, "uhd"), (X300, "x310"), (X400, "x410"), (N300, None),
                                                 ({"type": "usrp2"}, None), ({}, None),
                                                 ({"product": "X300"}, "x310"), ({"product": "B205mini"}, "uhd")])
    def test_the_driver_of_an_address(self, address, driver):
        assert uhd_package._driver_of(address) == driver

    def test_a_family_class_is_listed_by_its_own_name(self):
        assert [uhd_module.B210.DRIVER, uhd_module.X310.DRIVER, uhd_module.X410.DRIVER] == ["uhd", "x310", "x410"]


# ---------------------------------------------------------------- finding them
class TestFind:
    @pytest.fixture
    def asked(self, monkeypatch):
        """What `find` asks libuhd, in order; it answers with what a test puts in `answers[query]`"""
        class Asked(list):
            answers = None
        asked, answers = Asked(), {}
        native = SimpleNamespace(find=lambda args="": asked.append(args) or answers.get(args, []), init=lambda: None)
        import pysdrlib.uhd.lib as lib_module
        monkeypatch.setattr(lib_module, "uhd", native)
        asked.answers = answers
        return asked

    def test_plain_find_looks_only_at_b2xx_and_never_at_a_network(self, asked):
        asked.answers["type=b200"] = [B200]
        found = uhd_package.find()
        assert asked == ["type=b200"]
        assert [(i.driver, i.serial, i.open_args) for i in found] == [("uhd", "31B92DD", {"serial": "31B92DD"})]

    def test_addresses_are_asked_one_by_one(self, asked):
        asked.answers.update({"type=b200": [B200], "addr=192.168.10.2": [X300], "addr=192.168.10.3": [X400, N300]})
        found = uhd_package.find(["192.168.10.2", "192.168.10.3"])
        assert asked == ["type=b200", "addr=192.168.10.2", "addr=192.168.10.3"]
        assert [(i.driver, i.serial) for i in found] == [("uhd", "31B92DD"), ("x310", "3125A1F"), ("x410", "32C0FFE")]
        assert found[1].open_args == {"serial": "3125A1F", "addr": "192.168.10.2"}
        assert found[1].label == "UHD X310 3125A1F" and found[1].extra["type"] == "x300"

    def test_one_address_may_be_a_string(self, asked):
        uhd_package.find("192.168.10.2")
        assert asked == ["type=b200", "addr=192.168.10.2"]

    def test_pysdrlib_find_passes_the_addresses_on_and_lists_each_once(self, asked):
        asked.answers.update({"type=b200": [B200], "addr=192.168.10.2": [X300]})
        found = pysdrlib.find(addresses="192.168.10.2")
        uhds = [i for i in found if i.driver in ("uhd", "x310", "x410")]
        assert [(i.driver, i.serial) for i in uhds] == [("uhd", "31B92DD"), ("x310", "3125A1F")]
        assert asked.count("type=b200") == 1   # not once for each class

    def test_find_by_family_name(self, asked):
        asked.answers.update({"type=b200": [B200], "addr=192.168.10.2": [X300]})
        assert [i.driver for i in pysdrlib.find("x310", addresses="192.168.10.2")] == ["x310"]
        assert pysdrlib.find("x410", addresses="192.168.10.2") == []

    def test_a_found_x310_opens_as_one(self, asked, opened):
        asked.answers["addr=192.168.10.2"] = [X300]
        info = pysdrlib.find("x310", addresses="192.168.10.2")[0]
        sdr = info.open()
        assert isinstance(sdr, uhd_module.X310)
        assert opened.args == ["serial=3125A1F,addr=192.168.10.2"]


# ---------------------------------------------------------------- opening one
class TestOpen:
    def test_by_address(self, opened):
        opened(uhd_module.X310, addr="192.168.10.2")
        opened(uhd_module.X410, addr="10.0.0.5", args="master_clock_rate=250e6")
        opened(uhd_module.X310, serial="3125A1F", addr="192.168.10.2", args="type=x300")
        assert opened.args == ["addr=192.168.10.2", "addr=10.0.0.5,master_clock_rate=250e6",
                               "serial=3125A1F,addr=192.168.10.2,type=x300"]

    def test_not_found_names_the_radio_and_what_was_asked(self, monkeypatch):
        def refuse(args=""):
            raise real_lib.err.NOT_FOUND("none")
        monkeypatch.setattr(uhd_module, "lib", SimpleNamespace(uhd=SimpleNamespace(open=refuse, init=lambda: None), err=real_lib.err))
        with pytest.raises(err.NoDevice, match=r"USRP X410.*addr=10.0.0.5"):
            uhd_module.X410().open(addr="10.0.0.5")

    def test_experimental_is_said_once(self, monkeypatch, capsys):
        monkeypatch.setattr(uhd_module, "lib", SimpleNamespace(
            uhd=SimpleNamespace(open=lambda args="": FakeDevice(fake(), uhd_module.X310.SAMPLE_FORMAT), init=lambda: None), err=real_lib.err))
        monkeypatch.setattr(uhd_module._XSeries, "_warned", False)
        for _ in range(3):
            uhd_module.X310().open(addr="a")
        assert capsys.readouterr().err.count("experimental") == 1   # (pysdrlib's logger writes to stderr)


# ---------------------------------------------------------------- the radios
@pytest.mark.parametrize("cls", [uhd_module.X310, uhd_module.X410])
class TestSeries:
    def test_conforms(self, cls):
        from pysdrlib import testing
        assert not testing.errors(testing.check_driver(cls))

    def test_caps_come_from_the_radio_that_is_open(self, cls, opened):
        opened.fake.update(freq=(1e6, 7.2e9), rx_gain=(0.0, 60.0, 1.0))
        sdr = opened(cls, addr="a")
        caps = sdr.caps
        assert caps.freq[0].lo == 1e6 and caps.freq[0].hi == 7.2e9
        stage = caps.stage("rx", "RF")
        assert (stage.min, stage.max, stage.step) == (0.0, 60.0, 1.0)
        assert caps.stage("tx", "RF").max == 31.5
        assert caps.gain_modes == {"rx": ["manual"], "tx": ["manual"]}
        assert caps.shared["freq"] == "channel" and caps.shared["rate"] == "direction"
        assert caps.antennas["rx"] == ["TX/RX", "RX2"] and caps.info["mboard_serial"] == "3125A1F"
        assert caps.channels == {"rx": 2, "tx": 2}

    def test_the_overall_gain_is_one_stage(self, cls, opened):
        sdr = opened(cls, addr="a")
        assert set(sdr.caps.gains["rx"]) == {"RF"} and set(sdr.caps.gains["tx"]) == {"RF"}
        assert sdr.set_rx_rf_gain(12) == 12 and sdr.device.of("set_rx_gain")[-1] == (12.0, 0)
        assert sdr.set_tx_gain_db(5, channel=1) == 5 and sdr.device.of("set_tx_gain")[-1] == (5.0, 1)
        assert sdr.set_rx_gain_db(20) == 20 and sdr.device.of("set_rx_gain")[-1] == (20.0, 0)

    def test_the_gain_is_clamped_to_the_radios_range(self, cls, opened):
        sdr = opened(cls, addr="a")
        with pytest.warns(Warning):
            assert sdr.set_rx_rf_gain(500) == 31.5

    def test_each_channel_has_its_own_frequency(self, cls, opened):
        sdr = opened(cls, addr="a")
        sdr.set_rx_freq(2.4e9, channel=1)
        sdr.set_tx_freq(2.5e9, channel=0)
        assert sdr.device.of("set_rx_freq") == [(2.4e9, 1)] and sdr.device.of("set_tx_freq") == [(2.5e9, 0)]

    def test_a_stream_has_one_rate(self, cls, opened):
        sdr = opened(cls, addr="a")
        sdr.set_rx_sample_rate(50e6)
        assert sdr.device.of("set_rx_rate") == [(50e6, 0), (50e6, 1)]

    def test_no_gain_mode_no_bias_tee(self, cls, opened):
        sdr = opened(cls, addr="a")
        with pytest.raises(err.NotSupported):
            sdr.set_rx_gain_mode("auto")
        with pytest.raises(err.NotSupported):
            sdr.set_bias_t(True)

    def test_clock_time_and_sensors_are_the_usrps(self, cls, opened):
        sdr = opened(cls, addr="a")
        sdr.set_clock_source("external")
        assert sdr.device.of("set_clock_source") == [("external",)]
        assert sdr.get_setting("time_source") == "internal"
        assert sdr.sensors() == {}

    def test_streams_the_channels_asked_for(self, cls, opened):
        sdr = opened(cls, addr="a")
        sdr.start_rx(channels=(0, 1))
        assert sdr.device.of("set_rx_channels")[-1] == ([0, 1],)
        sdr.stop_rx()


def test_x410_has_four_channels(opened):
    opened.fake.update(channels=4)
    sdr = opened(uhd_module.X410, addr="a")
    assert sdr.caps.channels == {"rx": 4, "tx": 4}
    sdr.set_rx_sample_rate(100e6)
    assert [c for _, c in sdr.device.of("set_rx_rate")] == [0, 1, 2, 3]
