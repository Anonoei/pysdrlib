"""Where UHD finds the FPGA images a B2xx loads when it is opened.

libuhd looks in `UHD_IMAGES_DIR`, then its own install prefix (`/usr/share/uhd/images`). A libuhd that pysdrlib built has no
images, so `configure()` points `UHD_IMAGES_DIR` at the first of these that has some:

    1. UHD_IMAGES_DIR as the user set it
    2. the system's UHD images (/usr/share/uhd/images, /usr/local/share/uhd/images)
    3. pysdrlib's cache, filled by `download()`
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .._ext import utils

SYSTEM_DIRS = (Path("/usr/share/uhd/images"), Path("/usr/local/share/uhd/images"))

def cache_dir() -> Path:
    return utils.user_cache("uhd") / "images"

def has_images(path) -> bool:
    path = Path(path)
    return path.is_dir() and any(path.glob("usrp_b2*_fpga*.bin"))

def find():
    """Directory holding B2xx FPGA images, or None"""
    candidates = [Path(p) for p in os.environ.get("UHD_IMAGES_DIR", "").split(os.pathsep) if p]
    candidates += [*SYSTEM_DIRS, cache_dir()]
    return next((p for p in candidates if has_images(p)), None)

def configure():
    """Make sure libuhd will find the images (does not download). Returns the directory, or None"""
    found = find()
    if found is not None and str(found) != os.environ.get("UHD_IMAGES_DIR"):
        os.environ["UHD_IMAGES_DIR"] = str(found)
    return found

def download():
    """Fetch the B2xx images into pysdrlib's cache with UHD's own downloader (needs network). Returns the directory"""
    tool = shutil.which("uhd_images_downloader")
    if tool is None:
        raise RuntimeError("uhd_images_downloader is not installed; install UHD's utilities (e.g. `apt install uhd-host`) "
                           "or download the images by hand and set UHD_IMAGES_DIR")
    target = cache_dir()
    target.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([tool, "-t", "b2xx", "-i", str(target)], stdout=sys.stderr)
    os.environ["UHD_IMAGES_DIR"] = str(target)
    return target

HELP = ("The B2xx FPGA images were not found. Install UHD's images (`uhd_images_downloader -t b2xx`, or "
        "`apt install uhd-host`), then set UHD_IMAGES_DIR to their directory if it isn't /usr/share/uhd/images.")
