from pathlib import Path
import ctypes.util

PATH_MOD = Path(__file__).parent.parent
PATH_LIB = PATH_MOD / "_ext" / "cache"
PATH_LIB.mkdir(exist_ok=True)

def rmdir(path: Path):
    for file in path.iterdir():
        if file.is_dir():
            rmdir(file)
            continue
        file.unlink()
    path.rmdir()

def is_installed(lib_name):
    libc_path = ctypes.util.find_library(lib_name)
    if libc_path is None:
        return False
    return True
