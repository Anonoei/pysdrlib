"""Converters and antennas between a radio and the air: over-the-air frequencies, chains for both directions, antenna standards

Pure functions of capabilities (selection), the base Device on the simulator (tuning) and the server's frontends file.
"""
import json

import pytest

from pysdrlib.base import antennas
from pysdrlib.base import caps as _caps
from pysdrlib.base.caps import Capabilities, Chain, Range
from pysdrlib.remote import choose, frontends


def radio(chains=(), freq=((950e6, 2150e6),), rates=((1e6, 20e6),), tx=0):
    return Capabilities(driver="x", channels={"rx": 1, "tx": tx}, freq=[Range(*r) for r in freq],
                        rates=[Range(*r) for r in rates], chains=list(chains))


LNB = Chain(lo=9.75e9, name="lnb")                       # radio tunes RF - 9.75 GHz
MIRROR = Chain(lo=12e9, invert=True, name="mirror")      # radio tunes 12 GHz - RF, spectrum comes mirrored


# --- ranges and chains --- #
def test_at_least_takes_the_next_step_up():
    steps = [Range(2e6, 30e6, 4e6)]
    assert _caps.at_least(steps, 20.5e6) == 22e6 and _caps.at_least(steps, 22e6) == 22e6 and _caps.at_least(steps, 1e6) == 2e6
    assert _caps.at_least(steps, 31e6) is None
    assert _caps.at_least([Range(8e6, 8e6), Range(16e6, 16e6)], 9e6) == 16e6
    assert _caps.at_least([], 1.0) is None


def test_a_chain_maps_both_ways():
    assert LNB.to_if(11.7e9) == 1.95e9 and LNB.to_rf(1.95e9) == 11.7e9
    assert MIRROR.to_if(11.7e9) == 300e6 and MIRROR.to_rf(300e6) == 11.7e9
    assert MIRROR.span_to_if(11.7e9, 11.9e9) == (100e6, 300e6)      # the ends swap places
    with pytest.raises(ValueError):
        Chain(invert=True)          # no LO to be above the signal
    with pytest.raises(ValueError):
        Chain(direction="up")


def test_capabilities_carry_their_chains_over_the_wire():
    c = radio([Chain(lo=9.75e9, antenna="ANT500", port="RX2", name="lnb")])
    back = _caps.from_json(c.json())
    assert back.chains == [Chain(lo=9.75e9, antenna="ANT500", port="RX2", name="lnb",
                                 optimal=[Range(75e6, 1e9)])]       # what the antenna said is sent along
    payload = c.json()
    del payload["chains"]
    assert _caps.from_json(payload).chains == []        # a server from before chains


# --- selection --- #
def test_a_radio_behind_an_lnb_is_asked_for_over_the_air_frequencies():
    c = radio([LNB])
    assert choose.met(c, {"freq": 11.7e9}) and choose.met(c, {"freq": [10.7e9, 11.9e9]})
    bad = choose.problems(c, {"freq": 9.0e9})
    assert bad and "10.7 GHz-11.9 GHz via lnb" in bad[0]
    assert choose.problems(c, {"freq": [11e9, 12e9]})        # the top of it is past what the radio tunes
    assert choose.met(radio(), {"freq": 1.5e9}) and choose.problems(radio(), {"freq": 11.7e9})     # without a chain, as before


def test_granted_says_what_the_radio_tunes_and_whether_it_is_mirrored():
    got = choose.granted(radio([MIRROR]), {"freq": 10.5e9})      # it reaches 9.85-11.05 GHz, mirrored
    assert got == {"freq": 10.5e9, "if_freq": 1.5e9, "antenna": None, "inverted": True}
    assert choose.granted(radio(), {"freq": 1.5e9}) == {"freq": 1.5e9}       # nothing added for a radio with nothing on it
    assert choose.granted(radio([LNB]), {"freq": 9e9})["freq"] == 10.7e9     # the nearest it reaches, over the air


def test_a_chain_is_held_to_what_it_is_usable_at():
    c = radio([Chain(lo=9.75e9, usable=[Range(11e9, 11.5e9)])])
    assert choose.met(c, {"freq": 11.2e9}) and not choose.met(c, {"freq": 11.7e9})


def test_either_chain_of_a_radio_will_do():
    c = radio([LNB, Chain(lo=0.0, port="direct", usable=[Range(1e9, 2e9)])])
    assert choose.met(c, {"freq": 11.7e9}) and choose.met(c, {"freq": 1.5e9})
    assert choose.granted(c, {"freq": 1.5e9}).get("antenna") == "direct"


def test_a_transmit_need_asks_the_transmit_chain_too():
    up = Chain(direction="tx", lo=9e9, name="upconverter")
    c = radio([LNB, up], tx=1, freq=((950e6, 2150e6),))
    assert choose.met(c, {"freq": 11.0e9, "tx": True})      # receives at 1.25 GHz, transmits from 2 GHz
    bad = choose.problems(c, {"freq": 11.7e9, "tx": True})
    assert bad and "cannot transmit on 11.7 GHz" in bad[0] and "via upconverter" in bad[0]
    assert choose.met(c, {"freq": 11.7e9})      # to receive only, the transmit chain does not matter


# --- antennas --- #
def test_antenna_standards():
    ant = antennas.get("ant500")
    assert ant.optimal == [Range(75e6, 1e9)] and ant.size == (0.20, 0.88) and ant.kind == "whip" and ant.impedance == 50.0
    with pytest.raises(ValueError, match="ANT500"):
        antennas.get("nope")
    with pytest.raises(ValueError):
        antennas.register(antennas.Antenna("Ant500"))


def test_the_radio_whose_antenna_is_best_at_the_frequency_comes_first():
    whip = radio([Chain(antenna="ANT500")], freq=((1e6, 6e9),))
    bare = radio(freq=((1e6, 6e9),))
    want = {"freq": 433e6}
    assert choose.rank(whip, want) == choose.rank(bare, want)       # nothing is lost for it
    other = radio([Chain(antenna="ANT500", optimal=[Range(2e9, 3e9)])], freq=((1e6, 6e9),))
    assert choose.rank(whip, want) < choose.rank(other, want)       # an explicit optimal wins over the standard's
    assert choose.rank(other, {"freq": 2.4e9}) < choose.rank(whip, {"freq": 2.4e9})
    assert choose.met(whip, {"freq": 2.4e9})     # outside optimal is a preference, not a limit


# --- tuning --- #
@pytest.fixture
def sim():
    from pysdrlib.sim import Device
    d = Device(realtime=False)
    d.open()
    d.caps.antennas = {"rx": ["A", "B"], "tx": []}
    seen = []
    d._set_rx_freq = lambda freq, channel=0: seen.append(freq)
    d._set_antenna = lambda *a, **k: None
    d.seen = seen
    yield d
    d.close()


def test_the_radio_is_tuned_to_the_if_and_reports_the_air(sim):
    sim.caps.chains = [LNB]
    assert sim.set_rx_freq(11.7e9) == 11.7e9
    assert sim.seen == [1.95e9] and sim.get_rx_freq() == 11.7e9
    assert not sim.get_rx_inverted()
    sim.caps.chains = [MIRROR]
    sim.set_rx_freq(11.7e9)
    assert sim.seen[-1] == 300e6 and sim.get_rx_inverted()


def test_a_radio_with_no_chain_is_tuned_as_before(sim):
    sim.set_rx_freq(433e6)
    assert sim.seen == [433e6] and sim.get_rx_freq() == 433e6


def test_another_antenna_port_changes_the_chain_and_retunes(sim):
    sim.caps.chains = [Chain(port="A", lo=0.0), Chain(port="B", lo=1e9)]
    sim.set_rx_antenna("A")
    sim.set_rx_freq(2e9)
    assert sim.seen[-1] == 2e9
    sim.set_rx_antenna("B")
    assert sim.seen[-1] == 1e9 and sim.get_rx_freq() == 2e9     # the same frequency over the air, through the other chain


# --- the server's file --- #
def test_a_frontends_file(tmp_path):
    path = tmp_path / "f.toml"
    path.write_text('''
[antenna.MYWHIP]
optimal = [[430e6, 440e6]]

[[radio]]
serial = "ABC123"
[[radio.chain]]
direction = "rx"
port = "RX2"
antenna = "MYWHIP"
lo = 9.75e9
name = "lnb"
[[radio.chain]]
direction = "tx"
lo = 9e9
''')
    try:
        fe = frontends.load(path)
        chains = fe.chains_for("hackrf", "ABC123", "HackRF One")
        assert [c.direction for c in chains] == ["rx", "tx"] and chains[0].optimal_ranges() == [Range(430e6, 440e6)]
        assert fe.chains_for("hackrf", "other") == []
        c = radio()
        assert fe.apply(c, "hackrf", "ABC123").chains == chains and c.chains == []      # the class's own caps are untouched
        assert fe.apply(c, "hackrf", "other") is c
    finally:
        antennas.ANTENNAS.pop("MYWHIP", None)


def test_a_frontends_file_is_json_first_and_toml_failing_that(tmp_path):
    body = {"radio": [{"serial": "S", "chain": [{"lo": 9.75e9, "name": "lnb"}]}]}
    as_json = tmp_path / "f.json"
    as_json.write_text(json.dumps(body))
    nameless = tmp_path / "frontends"        # no extension: JSON is tried first
    nameless.write_text(json.dumps(body))
    as_toml = tmp_path / "frontends.cfg"     # not JSON, so TOML
    as_toml.write_text('[[radio]]\nserial = "S"\n[[radio.chain]]\nlo = 9.75e9\nname = "lnb"\n')
    for path in (as_json, nameless, as_toml):
        assert frontends.load(path).chains_for("x", "S") == [Chain(lo=9.75e9, name="lnb")], path
    wrong = tmp_path / "bad.json"
    wrong.write_text('{"radio": [')
    with pytest.raises(ValueError, match="not valid JSON"):
        frontends.load(wrong)


@pytest.mark.parametrize("text,message", [
    ("[[radio]]\nserial='1'\n[[radio.chain]]\nlo=1\nbogus=2\n", "no 'bogus'"),
    ("[[radio]]\n[[radio.chain]]\nlo=1\n", "needs a driver, serial or label"),
    ("[[radio]]\nserial='1'\n[[radio.chain]]\nantenna='NOPE'\n", "NOPE"),
    ("[[radio]]\nserial='1'\n[[radio.chain]]\nusable=[[2, 1]]\n", "low <= high"),
    ("[[radio\n", "f.toml"),
])
def test_a_wrong_frontends_file_says_what_is_wrong(tmp_path, text, message):
    path = tmp_path / "f.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match=message):
        frontends.load(path)


def test_a_missing_frontends_file_is_named(tmp_path):
    with pytest.raises(ValueError, match="nothing.toml"):
        frontends.load(tmp_path / "nothing.toml")


# --- antenna kinds and sizes --- #
def test_kinds_are_found_by_key_or_name_and_unknown_ones_are_allowed():
    assert antennas.find_kind("LPDA").key == "lpda"
    assert antennas.find_kind("log-periodic dipole array").key == "lpda"
    assert antennas.find_kind("Long-periodic Dipole Array").key == "lpda"      # the common misspelling
    assert antennas.find_kind("uwb - extremely wide bandwidth biconical").key == "biconical"
    assert antennas.find_kind("Parabolic dish").measures == "diameter"
    assert antennas.find_kind("corner reflector") is None and antennas.find_kind(None) is None
    with pytest.raises(ValueError):
        antennas.register_kind(antennas.Kind("dish", "Another dish"))
    odd = antennas.Antenna("ODD", kind="Corner reflector", size=(1, 1))
    assert odd.describe()["kind"] == "Corner reflector" and odd.describe()["measures"] == "size"


@pytest.mark.parametrize("bad", [{"size": (2, 1)}, {"size": (0, 1)}, {"size": 1.0}, {"size": (1, 2, 3)}, {"impedance": 0}, {"impedance": -50}])
def test_a_size_or_impedance_that_makes_no_sense_is_refused(bad):
    with pytest.raises(ValueError):
        antennas.Antenna("X", **bad)


def test_the_description_says_what_the_size_measures():
    dish = antennas.Antenna("D", kind="dish", size=(0.6, 0.6), impedance=50.0).describe()
    assert dish["measures"] == "diameter" and dish["size"] == [0.6, 0.6] and dish["kind_name"] == "Parabolic dish"
    ant = antennas.get("ANT500").describe()
    assert ant["kind"] == "whip" and ant["size"] == [0.20, 0.88] and ant["impedance"] == 50.0


def test_a_client_without_the_registry_still_describes_the_antenna(monkeypatch):
    wire = radio([Chain(antenna="ANT500")]).json()
    assert wire["chains"][0]["antenna_info"]["kind"] == "whip"
    monkeypatch.delitem(antennas.ANTENNAS, "ANT500")        # as on a machine that does not know it
    back = _caps.from_json(wire).chains[0]
    assert back.describe()["size"] == [0.20, 0.88] and back.describe()["impedance"] == 50.0
    del wire["chains"][0]["antenna_info"]       # a server from before kinds
    assert _caps.from_json(wire).chains[0].describe() == {"name": "ANT500"}
    assert Chain().describe() is None


def test_an_antenna_in_a_frontends_file_has_a_kind_and_a_size(tmp_path):
    body = {"antenna": {"DISH60": {"kind": "dish", "size": [0.6, 0.6], "impedance": 50, "optimal": [[10e9, 12e9]]},
                        "OLD": {"length": [0.1, 0.5]}}}
    path = tmp_path / "a.json"
    path.write_text(json.dumps(body))
    try:
        frontends.load(path)
        assert antennas.get("dish60").describe()["measures"] == "diameter"
        assert antennas.get("OLD").size == (0.1, 0.5)       # `length`, as files had it
        for table, message in (({"size": [1, 2], "length": [1, 2]}, "both 'size'"), ({"size": [2, 1]}, r"\[antenna.BAD\]: antenna 'BAD': size"),
                               ({"kind": "dish", "colour": "red"}, "no 'colour'")):
            path.write_text(json.dumps({"antenna": {"BAD": table}}))
            with pytest.raises(ValueError, match=message):
                frontends.load(path)
    finally:
        antennas.ANTENNAS.pop("DISH60", None)
        antennas.ANTENNAS.pop("OLD", None)


def test_list_shows_the_antenna_and_its_converter():
    from pysdrlib.remote import cli
    assert cli.format_antenna(antennas.get("ANT500").describe()) == "ANT500 (Whip / monopole, length 20-88 cm, 50 ohm)"
    assert cli.format_antenna(antennas.Antenna("D", kind="dish", size=(0.6, 0.6)).describe()) == "D (Parabolic dish, diameter 60 cm)"
    assert cli.format_antenna({"name": "ANT9"}) == "ANT9" and cli.format_antenna(None) == "none"
    chain = {"direction": "rx", "port": "RX2", "lo": 9.75e9, "invert": False, "antenna": antennas.get("ANT500").describe()}
    assert cli.format_chain(chain) == "antenna rx RX2: ANT500 (Whip / monopole, length 20-88 cm, 50 ohm), LO 9750 MHz"
