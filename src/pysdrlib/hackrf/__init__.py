def _import_hackrf():
    try:
        from .hackrf import HackRF
        return HackRF
    except ImportError as first:
        # Missing shared library (libhackrf not on the host): build the vendored copy
        # (offline, cached), load it by path, and retry. If the compiled extension itself
        # is missing there is nothing to rebuild at runtime.
        if "_hackrf" in str(getattr(first, "name", "") or "") and "libhackrf" not in str(first):
            raise ImportError(
                "pysdrlib.hackrf's compiled extension is missing; "
                "install pysdrlib with `pip install .` (or `python setup.py build_ext --inplace`)."
            ) from first
        import ctypes
        from .._ext import hackrf as ext_hackrf
        try:
            path = ext_hackrf.mk_lib()
            for lib in sorted(path.glob("libhackrf.so*")) + sorted(path.glob("libhackrf*.dylib")):
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                break
        except (RuntimeError, OSError) as exc:
            raise ImportError(f"libhackrf is not installed and could not be built: {exc}") from first
        from .hackrf import HackRF
        return HackRF

HackRF = _import_hackrf()
Device = HackRF
