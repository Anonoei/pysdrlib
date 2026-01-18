import pathlib
import importlib

from .base.device import Device

def ls():
    vendors = []
    for file in (pathlib.Path(__file__).parent).iterdir():
        if file.is_dir():
            if file.name in ("_ext", "base", "file", "__pycache__"):
                continue
            vendors.append(file.name)
    return vendors

def get(name: str):
    try:
        module = importlib.import_module(f".{name}", "pysdrlib")
    except ModuleNotFoundError:
        return None
    return module

def device(name: str, *args, **kwargs) -> Device:
    return get(name).Device(*args, **kwargs) # type: ignore
