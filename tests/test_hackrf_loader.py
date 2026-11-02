"""libhackrf is never built while packaging; it is found or built on the user's machine at import.

These cover the build/load logic in pysdrlib._ext.hackrf without needing cmake, libusb or a device.
"""
import sys
from pathlib import Path

import pytest

from pysdrlib._ext import hackrf as ext


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Isolated cache locations: (package cache, user cache)"""
    pkg, user = tmp_path / "pkg" / "hackrf", tmp_path / "user" / "hackrf"
    monkeypatch.setattr(ext.utils, "PATH_LIB", pkg.parent)
    monkeypatch.setenv("PYSDRLIB_CACHE", str(user.parent))
    return pkg, user


@pytest.fixture
def loaded(monkeypatch):
    """Record library loads instead of doing them"""
    calls = []
    monkeypatch.setattr(ext, "_load", lambda path: calls.append(str(path)))
    return calls


class TestExtensionBuild:
    """What setup.py gets: no building, no linking against libhackrf"""

    def test_get_never_builds(self, monkeypatch):
        monkeypatch.setattr(ext, "mk_lib", lambda *a, **k: pytest.fail("packaging must not build libhackrf"))
        monkeypatch.setattr(ext.subprocess, "check_call", lambda *a, **k: pytest.fail("no subprocesses"))
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda *a: pytest.fail("no host probing"))
        extension = ext.get()
        assert extension.name == "pysdrlib.hackrf.lib._hackrf"

    def test_does_not_link_or_pin_the_library(self):
        extension = ext.get()
        assert not extension.libraries and not extension.library_dirs and not extension.runtime_library_dirs
        assert not any("rpath" in a for a in extension.extra_link_args)

    def test_compiles_against_vendored_header_only(self):
        extension = ext.get()
        assert any(d.endswith("_build/libhackrf/src") for d in extension.include_dirs)

    def test_sources_exist(self):
        root = Path(__file__).resolve().parents[1]
        for src in ext.get().sources:
            assert (root / src).exists(), src

    def test_failing_cmake_cannot_break_get(self, monkeypatch):
        monkeypatch.setattr(ext.shutil, "which", lambda name: None)
        ext.get()

    def test_optional_only_on_windows(self, monkeypatch):
        assert ext.get().optional == (sys.platform == "win32")
        monkeypatch.setattr(ext.sys, "platform", "win32")
        assert ext.get().optional is True

    def test_macos_resolves_symbols_at_load_time(self, monkeypatch):
        monkeypatch.setattr(ext.sys, "platform", "darwin")
        assert ext.get().extra_link_args == ["-undefined", "dynamic_lookup"]


class TestLoadOrder:
    def test_prefers_system_library(self, cache, loaded, monkeypatch):
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda n: "libhackrf.so.0")
        monkeypatch.setattr(ext, "mk_lib", lambda: pytest.fail("must not build when installed"))
        assert "system" in ext.load_library()
        assert loaded == ["libhackrf.so.0"]

    def test_uses_cached_copy_before_building(self, cache, loaded, monkeypatch):
        pkg, _ = cache
        pkg.mkdir(parents=True)
        (pkg / "libhackrf.so.0").write_bytes(b"")
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda n: None)
        monkeypatch.setattr(ext, "mk_lib", lambda: pytest.fail("must not rebuild a cached library"))
        assert "cached" in ext.load_library()
        assert loaded == [str(pkg / "libhackrf.so.0")]

    def test_user_cache_is_searched(self, cache, loaded, monkeypatch):
        _, user = cache
        user.mkdir(parents=True)
        (user / "libhackrf.so").write_bytes(b"")
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda n: None)
        ext.load_library()
        assert loaded == [str(user / "libhackrf.so")]

    def test_builds_only_when_nothing_else_works(self, cache, loaded, monkeypatch):
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda n: None)
        built = []
        monkeypatch.setattr(ext, "mk_lib", lambda: built.append(1) or Path("/x/libhackrf.so.0"))
        assert "built" in ext.load_library()
        assert built == [1] and loaded == ["/x/libhackrf.so.0"]

    def test_unloadable_system_library_falls_through(self, cache, monkeypatch):
        attempts = []
        def load(path):
            attempts.append(str(path))
            if str(path) == "libhackrf.so.0":
                raise OSError("wrong architecture")
        monkeypatch.setattr(ext, "_load", load)
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda n: "libhackrf.so.0")
        monkeypatch.setattr(ext, "mk_lib", lambda: Path("/built/libhackrf.so.0"))
        assert "built" in ext.load_library()
        assert attempts == ["libhackrf.so.0", "/built/libhackrf.so.0"]

    def test_corrupt_cache_is_rebuilt(self, cache, monkeypatch):
        pkg, _ = cache
        pkg.mkdir(parents=True)
        (pkg / "libhackrf.so.0").write_bytes(b"garbage")
        rebuilt = Path("/rebuilt/libhackrf.so.0")
        def load(path):
            if Path(path) != rebuilt:
                raise OSError("invalid ELF header")
        monkeypatch.setattr(ext, "_load", load)
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda n: None)
        monkeypatch.setattr(ext, "mk_lib", lambda: rebuilt)
        assert "built" in ext.load_library()

    def test_build_failure_propagates_with_instructions(self, cache, loaded, monkeypatch):
        monkeypatch.setattr(ext.ctypes.util, "find_library", lambda n: None)
        monkeypatch.setattr(ext.shutil, "which", lambda n: None)
        with pytest.raises(RuntimeError, match="cmake") as exc:
            ext.load_library()
        assert "libhackrf0" in str(exc.value) and "libusb" in str(exc.value)


class TestMkLib:
    @pytest.fixture
    def fake_cmake(self, monkeypatch, tmp_path):
        """Pretend cmake: record calls, and 'build' a library into the -B directory"""
        calls = []
        def check_call(cmd, **kw):
            calls.append(cmd)
            if "--build" in cmd:
                out = Path(cmd[2]) / "src"
                out.mkdir(parents=True, exist_ok=True)
                (out / "libhackrf.so.0.9.1").write_bytes(b"lib")
                (out / "libhackrf.so.0").symlink_to("libhackrf.so.0.9.1")
                (out / "libhackrf.so").symlink_to("libhackrf.so.0.9.1")
                (out / "libhackrf.a").write_bytes(b"static")
        monkeypatch.setattr(ext.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(ext.subprocess, "check_call", check_call)
        return calls

    def test_builds_out_of_tree_and_caches(self, cache, fake_cmake):
        pkg, _ = cache
        result = ext.mk_lib()
        assert result == pkg / "libhackrf.so.0"
        assert sorted(p.name for p in pkg.iterdir()) == ["libhackrf.so", "libhackrf.so.0", "libhackrf.so.0.9.1"]
        assert all(p.is_file() and not p.is_symlink() for p in pkg.iterdir())   # real files, no .a
        configure = fake_cmake[0]
        assert configure[:2] == ["cmake", "-S"] and Path(configure[2]) == ext.SOURCE
        assert "-B" in configure and "Release" in " ".join(configure)

    def test_never_writes_into_the_package(self, cache, fake_cmake):
        before = sorted(ext.SOURCE.rglob("*"))
        ext.mk_lib()
        assert sorted(ext.SOURCE.rglob("*")) == before
        assert not (ext.SOURCE.parent / "build").exists()

    def test_read_only_package_uses_user_cache(self, cache, fake_cmake, monkeypatch):
        pkg, user = cache
        monkeypatch.setattr(ext, "_writable", lambda p: user.parent in p.parents or p == user)
        assert ext.mk_lib().parent == user

    def test_cmake_missing(self, cache, monkeypatch):
        monkeypatch.setattr(ext.shutil, "which", lambda n: None)
        with pytest.raises(RuntimeError, match="cmake"):
            ext.mk_lib()

    def test_cmake_failure_is_explained(self, cache, monkeypatch):
        monkeypatch.setattr(ext.shutil, "which", lambda n: "/usr/bin/cmake")
        def boom(*a, **k):
            raise ext.subprocess.CalledProcessError(1, "cmake")
        monkeypatch.setattr(ext.subprocess, "check_call", boom)
        with pytest.raises(RuntimeError, match="libusb"):
            ext.mk_lib()

    def test_missing_sources(self, cache, monkeypatch, tmp_path):
        monkeypatch.setattr(ext.shutil, "which", lambda n: "/usr/bin/cmake")
        monkeypatch.setattr(ext, "SOURCE", tmp_path / "nowhere")
        with pytest.raises(RuntimeError, match="submodule"):
            ext.mk_lib()

    def test_no_writable_cache(self, cache, monkeypatch):
        monkeypatch.setattr(ext.shutil, "which", lambda n: "/usr/bin/cmake")
        monkeypatch.setattr(ext, "_writable", lambda p: False)
        with pytest.raises(RuntimeError, match="PYSDRLIB_CACHE"):
            ext.mk_lib()

    def test_build_that_produces_nothing(self, cache, monkeypatch):
        monkeypatch.setattr(ext.shutil, "which", lambda n: "/usr/bin/cmake")
        monkeypatch.setattr(ext.subprocess, "check_call", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="no library"):
            ext.mk_lib()


class TestCacheLocation:
    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PYSDRLIB_CACHE", str(tmp_path))
        assert ext.user_cache() == tmp_path / "hackrf"

    def test_xdg(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PYSDRLIB_CACHE", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setattr(ext.sys, "platform", "linux")
        assert ext.user_cache() == tmp_path / "pysdrlib" / "hackrf"

    def test_helpers_have_no_import_time_side_effects(self):
        """The install location may be read-only: importing _ext.utils must not create directories"""
        import ast
        tree = ast.parse(Path(ext.utils.__file__).read_text())
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                assert "mkdir" not in ast.dump(node), "module-level mkdir in _ext/utils.py"

    def test_find_cached_none(self, cache):
        assert ext.find_cached() is None


class TestImportFallback:
    @pytest.mark.parametrize("msg,expected", [
        ("/x/_hackrf.so: undefined symbol: hackrf_init", True),
        ("dlopen: symbol not found in flat namespace '_hackrf_open'", True),
        ("DLL load failed while importing _hackrf", True),
        ("libhackrf.so.0: cannot open shared object file", True),
        ("cannot import name '_hackrf' from 'pysdrlib.hackrf.lib'", False),
        ("No module named 'numpy'", False),
    ])
    def test_classifies_import_errors(self, msg, expected):
        import importlib
        mod = importlib.import_module("pysdrlib.hackrf")
        assert mod._is_missing_library(ImportError(msg)) is expected
