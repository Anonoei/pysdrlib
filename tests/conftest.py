import pytest

from devices import HW, SPECS, fake_lib


@pytest.fixture(params=SPECS, ids=lambda spec: spec.name)
def spec(request):
    """Each hardware device in turn; contract tests take this and so run against all of them"""
    return request.param


@pytest.fixture
def make_sdr(spec, monkeypatch):
    """Factory for the Python device over a fake driver: make_sdr(open_error=None) -> unopened device"""
    def make(open_error=None):
        monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec, open_error))
        return spec.device_class()
    return make


@pytest.fixture
def sdr(make_sdr):
    """An opened device whose driver is a FakeDevice (`sdr.device`)"""
    dev = make_sdr()
    dev.open()
    return dev


@pytest.fixture
def hardware(spec):
    """The extension's module for `spec`, if PYSDRLIB_HW=1 and one of its devices is attached"""
    if not HW:
        pytest.skip("set PYSDRLIB_HW=1 with the device attached")
    native = spec.native
    if not spec.present(native):
        pytest.skip(f"no {spec.name} device attached")
    return native
