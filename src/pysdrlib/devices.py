import pathlib
import importlib

def ls():
    vendors = []
    for file in (pathlib.Path(__file__).parent).iterdir():
        if file.is_dir():
            if file.name in ("base", "file", "__pycache__"):
                continue
            vendors.append(file.name)
    return vendors

def get(name: str):
    path = pathlib.Path(__file__).parent
    try:
        module = importlib.import_module(f".{name}", "pysdrlib")
    except ModuleNotFoundError:
        return None
    return module
