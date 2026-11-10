"""Type stubs for the compiled extensions (a development tool)

The extensions are C++ (pybind11), so an editor or a type checker sees nothing of them without `.pyi` files next to them:
`lib/_<name>.pyi` for each driver's module and the sample module, and `lib/err.pyi` for the error classes a module creates
when it is imported. They are generated from the built modules; run this after changing a binding:

    pip install pybind11-stubgen
    python -m pysdrlib._ext.stubs            # rewrite the stubs
    python -m pysdrlib._ext.stubs --check    # exit 1 if they differ from what the built modules say

`tests/test_stubs.py` checks that every name in the built modules is in a stub, without needing the tool.
"""
import argparse
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path

from . import utils

ROOT = utils.PATH_MOD   # src/pysdrlib


def modules():
    """Dotted names of the compiled modules (the sample module, then each driver's)"""
    return [utils.samples_extension().name, *(e.name for e in utils.driver_extensions())]


def stub_path(module: str) -> Path:
    """Where the stub of `pysdrlib.x.lib._x` goes"""
    return ROOT.joinpath(*module.split(".")[1:]).with_suffix(".pyi")


def error_stub(module: str):
    """The text of `lib/err.pyi` for a driver module, from the classes it registered at import; None if it has none"""
    package = module.rsplit(".", 1)[0]
    err = getattr(importlib.import_module(package), "err", None)
    classes = [c for c in vars(err).values() if isinstance(c, type) and issubclass(c, Exception)] if err else []
    if not classes:
        return None
    root = next(c for c in classes if c.__bases__ == (Exception,))
    lines = ['"""Errors of the compiled extension, made when it is imported (see hardware_pybind.hpp)"""', ""]
    for cls in [root, *sorted((c for c in classes if c is not root), key=lambda c: c.__name__)]:
        base = "Exception" if cls is root else root.__name__
        doc = (cls.__doc__ or "").strip()
        lines += [f"class {cls.__name__}({base}):", f'    """{doc}"""' if doc else "    ...", ""]
    return "\n".join(lines)


def generate(check=False) -> bool:
    """Write the stubs (or, with `check`, only compare); returns True if they were up to date"""
    current = True
    with tempfile.TemporaryDirectory() as tmp:
        for module in modules():
            subprocess.run([sys.executable, "-m", "pybind11_stubgen", module, "-o", tmp, "--ignore-all-errors"],
                           check=True, capture_output=True)
            made = Path(tmp).joinpath(*module.split(".")).with_suffix(".pyi")
            for dest, text in ((stub_path(module), made.read_text()),
                               (stub_path(module).with_name("err.pyi"), error_stub(module))):
                if text is None:
                    continue
                if not dest.exists() or dest.read_text() != text:
                    current = False
                    if not check:
                        dest.write_text(text)
    (ROOT / "py.typed").touch()
    return current


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m pysdrlib._ext.stubs", description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 instead of rewriting a stub that is out of date")
    args = parser.parse_args(argv)
    try:
        current = generate(check=args.check)
    except (ImportError, ModuleNotFoundError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"error: {exc} (pip install pybind11-stubgen, and build the extensions first)")
    print("stubs are up to date" if current else ("stubs are out of date" if args.check else "stubs written"))
    return 0 if current or not args.check else 1


if __name__ == "__main__":
    sys.exit(main())
