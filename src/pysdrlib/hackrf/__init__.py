def _is_missing_library(exc):
    """True if an ImportError means the extension loaded but libhackrf's symbols aren't available"""
    msg = str(exc).lower()
    return any(s in msg for s in ("undefined symbol", "symbol not found", "libhackrf", "hackrf_", "dll load failed"))

def _import_hackrf():
    try:
        from .hackrf import HackRF
        return HackRF
    except ImportError as first:
        from .._ext import hackrf as ext_hackrf
        if not _is_missing_library(first):
            raise ImportError(
                "pysdrlib.hackrf's compiled extension is not available for this installation "
                "(e.g. an unsupported platform, or a source checkout that was never built). "
                "Install a wheel for your platform, or build it with `pip install .`."
            ) from first
        # The extension is there but libhackrf isn't loaded: find it, or build the bundled copy on
        # this machine (offline, cached), then retry.
        try:
            ext_hackrf.load_library()
        except RuntimeError as exc:
            raise ImportError(str(exc)) from first
        from .hackrf import HackRF
        return HackRF

HackRF = _import_hackrf()
Device = HackRF
