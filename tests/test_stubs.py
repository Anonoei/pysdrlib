"""The type stubs of the compiled extensions (src/pysdrlib/_ext/stubs.py) cover what the built modules have

The generator itself needs pybind11-stubgen; this needs only the built modules and the stubs, so a binding added without
regenerating the stubs fails here.
"""
import ast
import importlib
import inspect

import pytest

from pysdrlib._ext import stubs


def declared(path):
    """{class name: {method names}} and the module-level names of a stub file"""
    tree = ast.parse(path.read_text())
    classes = {n.name: {m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
               | {t.id for m in n.body if isinstance(m, ast.AnnAssign) and isinstance((t := m.target), ast.Name)}
               for n in tree.body if isinstance(n, ast.ClassDef)}
    names = set(classes) | {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))} \
        | {t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
           for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
    return classes, names


@pytest.fixture(params=stubs.modules())
def built(request):
    try:
        return importlib.import_module(request.param)
    except ImportError as exc:
        pytest.skip(f"{request.param} unavailable: {exc}")


def test_every_module_has_a_stub_listing_what_it_has(built):
    path = stubs.stub_path(built.__name__)
    assert path.is_file(), f"{path.name} is missing: run python -m pysdrlib._ext.stubs"
    classes, names = declared(path)
    for name, obj in vars(built).items():
        if name.startswith("__") or name in ("err",) or inspect.ismodule(obj):
            continue
        assert name in names or name in ("_dispatch_rx", "_simulate_rx"), f"{built.__name__}.{name} is not in {path.name}"
        if inspect.isclass(obj) and name in classes:
            for member in vars(obj):
                if not member.startswith(("__", "_pybind11")):
                    assert member in classes[name], f"{built.__name__}.{name}.{member} is not in {path.name}"


def test_the_error_classes_are_typed(built):
    err = getattr(importlib.import_module(built.__name__.rsplit(".", 1)[0]), "err", None)
    if err is None:
        pytest.skip("no errors in this module")
    path = stubs.stub_path(built.__name__).with_name("err.pyi")
    assert path.is_file(), f"{path} is missing: run python -m pysdrlib._ext.stubs"
    classes, _ = declared(path)
    made = {n for n, c in vars(err).items() if isinstance(c, type) and issubclass(c, Exception)}
    assert made <= set(classes), f"missing from err.pyi: {sorted(made - set(classes))}"


def test_the_package_says_it_is_typed():
    assert (stubs.ROOT / "py.typed").is_file()
