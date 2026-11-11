"""Vendor libraries are never built while packaging, and never as a side effect of importing a driver: each is found
(installed, or built before), or `pysdrlib build <driver>` builds it on the user's machine. Covers pysdrlib._ext for every
device in tests/devices.py without needing cmake, libusb or a device.
"""
import os
import sys
from pathlib import Path

import pytest

from pysdrlib._ext import utils

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ext(spec):
    return spec.ext


@pytest.fixture
def lib(ext):
    return ext.LIB


@pytest.fixture
def cache(lib, tmp_path, monkeypatch):
    """Isolated cache locations: (package cache, user cache)"""
    pkg, user = tmp_path / "pkg" / lib.name, tmp_path / "user" / lib.name
    monkeypatch.setattr(utils, "PATH_LIB", pkg.parent)
    monkeypatch.setenv("PYSDRLIB_CACHE", str(user.parent))
    return pkg, user


@pytest.fixture
def loaded(monkeypatch):
    """Record library loads instead of doing them"""
    calls = []
    monkeypatch.setattr(utils, "load_global", lambda path: calls.append(str(path)))
    return calls


@pytest.fixture
def no_system_library(monkeypatch):
    monkeypatch.setattr(utils.ctypes.util, "find_library", lambda name: None)


class TestExtensionBuild:
    """What setup.py gets: no building, no linking against the vendor library"""

    def test_get_never_builds_or_probes_the_host(self, ext, monkeypatch):
        monkeypatch.setattr(utils.VendorLibrary, "build", lambda *a: pytest.fail("packaging must not build"))
        monkeypatch.setattr(utils.subprocess, "check_call", lambda *a, **k: pytest.fail("no subprocesses"))
        monkeypatch.setattr(utils.ctypes.util, "find_library", lambda *a: pytest.fail("no host probing"))
        ext.get()

    def test_names_the_package_extension(self, spec, ext):
        assert ext.get().name == f"pysdrlib.{spec.name}.lib._{spec.name}"

    def test_does_not_link_or_pin_the_library(self, ext):
        extension = ext.get()
        assert not extension.libraries and not extension.library_dirs and not extension.runtime_library_dirs
        assert not any("rpath" in a for a in extension.extra_link_args)

    def test_sources_headers_and_dependencies_exist(self, ext):
        extension = ext.get()
        for path in [*extension.sources, *extension.depends]:
            assert (ROOT / path).exists(), path
        assert all((ROOT / d).is_dir() for d in extension.include_dirs)

    def test_uses_the_shared_hardware_headers(self, ext):
        depends = " ".join(ext.get().depends)
        assert all(h in depends for h in ("hardware_wrapper.hpp", "hardware_numpy.hpp", "hardware_pybind.hpp"))

    def test_optional_only_on_windows(self, ext, monkeypatch):
        assert ext.get().optional == (sys.platform == "win32")
        monkeypatch.setattr(utils.sys, "platform", "win32")
        assert ext.get().optional is True

    def test_macos_resolves_symbols_at_load_time(self, ext, monkeypatch):
        monkeypatch.setattr(utils.sys, "platform", "darwin")
        assert ext.get().extra_link_args == ["-undefined", "dynamic_lookup"]


class TestLoadOrder:
    def test_prefers_the_system_library(self, lib, cache, loaded, monkeypatch):
        monkeypatch.setattr(utils.ctypes.util, "find_library", lambda n: f"lib{n}.so")
        monkeypatch.setattr(lib, "build", lambda: pytest.fail("must not build when installed"))
        assert "system" in lib.load()
        assert loaded == [f"lib{lib.name}.so"]

    def test_then_the_package_cache_without_rebuilding(self, lib, cache, loaded, no_system_library, monkeypatch):
        pkg, _ = cache
        pkg.mkdir(parents=True)
        (pkg / lib.files[0]).write_bytes(b"lib")
        monkeypatch.setattr(lib, "build", lambda: pytest.fail("must not rebuild a cached library"))
        assert "cached" in lib.load()
        assert loaded == [str(pkg / lib.files[0])]

    def test_then_the_user_cache(self, lib, cache, loaded, no_system_library):
        _, user = cache
        user.mkdir(parents=True)
        (user / lib.files[-1]).write_bytes(b"lib")
        lib.load()
        assert loaded == [str(user / lib.files[-1])]

    def test_builds_only_when_asked_and_nothing_else_works(self, lib, cache, loaded, no_system_library, monkeypatch):
        built = []
        monkeypatch.setattr(lib, "build", lambda: built.append(1) or Path("/x") / lib.files[0])
        assert "built" in lib.load(build=True)
        assert built == [1] and loaded == [str(Path("/x") / lib.files[0])]

    def test_a_missing_library_is_not_built_by_importing(self, lib, cache, loaded, no_system_library, monkeypatch):
        monkeypatch.delenv("PYSDRLIB_BUILD", raising=False)
        monkeypatch.setattr(lib, "build", lambda: pytest.fail("importing must not build"))
        with pytest.raises(RuntimeError, match=rf"pysdrlib build {lib.driver}") as exc:
            lib.load()
        assert "PYSDRLIB_BUILD=auto" in str(exc.value) and lib.help in str(exc.value) and loaded == []

    @pytest.mark.parametrize("value", ["auto", "AUTO", "1", "yes", "true"])
    def test_the_environment_can_allow_building_on_first_use(self, lib, cache, loaded, no_system_library, monkeypatch, value):
        monkeypatch.setenv("PYSDRLIB_BUILD", value)
        monkeypatch.setattr(lib, "build", lambda: Path("/x") / lib.files[0])
        assert "built" in lib.load()

    @pytest.mark.parametrize("value", ["", "no", "0", "never"])
    def test_other_values_do_not(self, lib, cache, loaded, no_system_library, monkeypatch, value):
        monkeypatch.setenv("PYSDRLIB_BUILD", value)
        monkeypatch.setattr(lib, "build", lambda: pytest.fail("not allowed"))
        with pytest.raises(RuntimeError, match="pysdrlib build"):
            lib.load()

    def test_where_it_is_is_known(self, lib, cache, no_system_library):
        pkg, _ = cache
        assert lib.locate() is None and not lib.available()
        pkg.mkdir(parents=True)
        (pkg / lib.files[0]).write_bytes(b"lib")
        assert lib.locate() == ("cached", str(pkg / lib.files[0])) and lib.available()

    def test_every_library_names_its_driver(self, lib, spec):
        assert lib.driver == spec.name

    def test_unloadable_system_library_falls_through(self, lib, cache, monkeypatch):
        attempts = []
        def load(path):
            attempts.append(str(path))
            if len(attempts) == 1:
                raise OSError("wrong architecture")
        monkeypatch.setattr(utils, "load_global", load)
        monkeypatch.setattr(utils.ctypes.util, "find_library", lambda n: "system.so")
        monkeypatch.setattr(lib, "build", lambda: Path("/built") / lib.files[0])
        assert "built" in lib.load(build=True)
        assert attempts == ["system.so", str(Path("/built") / lib.files[0])]

    def test_a_built_library_that_will_not_load_is_explained(self, lib, cache, no_system_library, monkeypatch):
        def load(path):
            raise OSError("bad ELF")
        monkeypatch.setattr(utils, "load_global", load)
        monkeypatch.setattr(lib, "build", lambda: Path("/built") / lib.files[0])
        with pytest.raises(RuntimeError, match="bad ELF"):
            lib.load(build=True)

    def test_build_failure_comes_with_instructions(self, lib, cache, loaded, no_system_library, monkeypatch):
        monkeypatch.setattr(utils.shutil, "which", lambda n: None)
        with pytest.raises(RuntimeError, match="cmake") as exc:
            lib.load(build=True)
        assert "libusb" in str(exc.value)


class TestBuild:
    @pytest.fixture
    def fake_cmake(self, lib, monkeypatch):
        """Pretend cmake: record calls, and 'build' the library into the -B directory"""
        calls = []
        def check_call(cmd, **kw):
            calls.append(cmd)
            if "--build" in cmd:
                out = Path(cmd[2]) / "out"
                out.mkdir(parents=True, exist_ok=True)
                (out / lib.files[0]).write_bytes(b"lib")
                (out / "unrelated.a").write_bytes(b"static")
                for name in lib.files[1:2]:
                    (out / name).symlink_to(lib.files[0])
        monkeypatch.setattr(utils.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(utils.subprocess, "check_call", check_call)
        return calls

    def test_builds_out_of_tree_and_caches(self, lib, cache, fake_cmake):
        pkg, _ = cache
        assert lib.build() == pkg / lib.files[0]
        cached = sorted(p.name for p in pkg.iterdir())
        assert lib.files[0] in cached and "unrelated.a" not in cached
        assert all(p.is_file() and not p.is_symlink() for p in pkg.iterdir())   # soname links become real files
        configure = fake_cmake[0]
        assert configure[:2] == ["cmake", "-S"] and Path(configure[2]) == lib.source
        assert "-B" in configure and "Release" in " ".join(configure)

    def test_never_writes_into_the_package(self, lib, cache, fake_cmake):
        def snapshot():
            # os.walk skips what it can't read; Path.rglob raises on 3.12 for the vendor symlinks a Windows checkout can't resolve
            return sorted(Path(root, name) for root, dirs, files in os.walk(lib.source) for name in dirs + files)
        before = snapshot()
        lib.build()
        assert snapshot() == before

    def test_read_only_package_uses_the_user_cache(self, lib, cache, fake_cmake, monkeypatch):
        _, user = cache
        monkeypatch.setattr(utils, "writable", lambda p: user.parent in p.parents or p == user)
        assert lib.build().parent == user

    def test_cmake_missing(self, lib, cache, monkeypatch):
        monkeypatch.setattr(utils.shutil, "which", lambda n: None)
        with pytest.raises(RuntimeError, match="cmake"):
            lib.build()

    def test_cmake_failure_is_explained(self, lib, cache, monkeypatch):
        monkeypatch.setattr(utils.shutil, "which", lambda n: "/usr/bin/cmake")
        def boom(*a, **k):
            raise utils.subprocess.CalledProcessError(1, "cmake")
        monkeypatch.setattr(utils.subprocess, "check_call", boom)
        with pytest.raises(RuntimeError, match="libusb"):
            lib.build()

    def test_missing_sources(self, lib, cache, monkeypatch, tmp_path):
        monkeypatch.setattr(utils.shutil, "which", lambda n: "/usr/bin/cmake")
        monkeypatch.setattr(lib, "source", tmp_path / "nowhere")
        with pytest.raises(RuntimeError, match="vendor.py snapshot"):
            lib.build()

    def test_no_writable_cache(self, lib, cache, monkeypatch):
        monkeypatch.setattr(utils.shutil, "which", lambda n: "/usr/bin/cmake")
        monkeypatch.setattr(utils, "writable", lambda p: False)
        with pytest.raises(RuntimeError, match="PYSDRLIB_CACHE"):
            lib.build()

    def test_build_that_produces_nothing(self, lib, cache, monkeypatch):
        monkeypatch.setattr(utils.shutil, "which", lambda n: "/usr/bin/cmake")
        monkeypatch.setattr(utils.subprocess, "check_call", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="no library"):
            lib.build()


class TestImportDevice:
    """A device package imports its extension; only if that is missing the library does it find or build it"""

    @pytest.fixture
    def fake_import(self, monkeypatch):
        """Make importing the device fail with `first`, then succeed once the library is loaded"""
        state = {"loaded": False, "calls": 0}
        def install(first):
            def import_module(name, package=None):
                state["calls"] += 1
                if not state["loaded"]:
                    raise first
                return type("M", (), {"Device": "the class"})
            monkeypatch.setattr(utils.importlib, "import_module", import_module)
            return state
        return install

    def test_imports_directly_when_the_extension_loads(self, lib, monkeypatch):
        monkeypatch.setattr(utils.importlib, "import_module", lambda name, package=None: type("M", (), {"Device": 1}))
        monkeypatch.setattr(lib, "load", lambda: pytest.fail("the library is not needed"))
        assert utils.import_device("pkg", ".mod", "Device", lib) == 1

    def test_loads_the_library_when_symbols_are_missing(self, lib, fake_import, monkeypatch):
        state = fake_import(ImportError("_x.so: undefined symbol: _ZN12Something"))
        monkeypatch.setattr(lib, "load", lambda: state.update(loaded=True))
        assert utils.import_device("pkg", ".mod", "Device", lib) == "the class"
        assert state["calls"] == 2

    def test_an_extension_that_was_never_built_is_not_papered_over(self, lib, fake_import, monkeypatch):
        fake_import(ImportError("No module named 'pkg.lib._x'"))
        monkeypatch.setattr(lib, "load", lambda: pytest.fail("building the library will not help"))
        with pytest.raises(ImportError, match="compiled extension"):
            utils.import_device("pkg", ".mod", "Device", lib)

    def test_a_missing_library_becomes_an_import_error_that_says_how_to_build_it(self, lib, fake_import, cache, no_system_library,
                                                                                 monkeypatch):
        fake_import(ImportError("undefined symbol: x"))
        monkeypatch.delenv("PYSDRLIB_BUILD", raising=False)
        with pytest.raises(ImportError, match=rf"pysdrlib build {lib.driver}"):
            utils.import_device("pkg", ".mod", "Device", lib)

    def test_build_failure_becomes_an_import_error_with_instructions(self, lib, fake_import, monkeypatch):
        fake_import(ImportError("undefined symbol: x"))
        def fail():
            raise RuntimeError("install cmake please")
        monkeypatch.setattr(lib, "load", fail)
        with pytest.raises(ImportError, match="install cmake please"):
            utils.import_device("pkg", ".mod", "Device", lib)

    def test_recognises_this_librarys_own_load_errors(self, lib):
        assert lib.is_missing(ImportError("x.so: undefined symbol: foo"))
        assert lib.is_missing(ImportError("dlopen: symbol not found"))
        assert not lib.is_missing(ImportError("No module named 'x'"))
        for hint in lib.missing_hints:
            assert lib.is_missing(ImportError(f"cannot open shared object file: {hint}.so"))


class TestSamplesExtension:
    """pysdrlib.base.lib._samples (conversion and ring) needs no vendor library, so it is always built"""

    def test_sources_and_dependencies_exist(self):
        extension = utils.samples_extension()
        assert extension.name == "pysdrlib.base.lib._samples"
        for path in [*extension.sources, *extension.depends]:
            assert (ROOT / path).exists(), path
        assert all((ROOT / d).is_dir() for d in extension.include_dirs)

    def test_links_nothing_and_is_not_optional(self, monkeypatch):
        extension = utils.samples_extension()
        assert not extension.libraries and not extension.library_dirs and not extension.optional
        monkeypatch.setattr(utils.sys, "platform", "win32")
        assert not utils.samples_extension().optional   # unlike the devices', which need a vendor library

    def test_rounds_exactly_like_the_reference_on_every_cpu(self, ext):
        """No fused multiply-add in the conversion, or results would differ between machines"""
        if sys.platform != "win32":
            for flag in ("-ffp-contract=off", "-fno-trapping-math"):   # exact rounding; vectorisable clamps
                assert flag in utils.samples_extension().extra_compile_args
                assert flag in ext.get().extra_compile_args
