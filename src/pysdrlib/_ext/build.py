"""`pysdrlib build`: build the vendor libraries a driver needs

    pysdrlib build                  # which drivers have their library, and which need one built
    pysdrlib build uhd              # build UHD's (a few minutes)
    pysdrlib build --all            # every library that is missing
    pysdrlib build uhd --force      # build it again, even where one is installed or built before

Drivers need their vendor's library (libhackrf, libuhd...). Use the one you installed, or build the copy that comes with
pysdrlib: it is built out of the source tree with cmake into a per-user cache, and nothing is installed system-wide. Importing
a driver never builds anything by itself (UHD alone takes minutes): it says to run this. `PYSDRLIB_BUILD=auto` in the
environment restores building on first use.
"""
import importlib
import sys
import time
from pathlib import Path

from . import utils

NOT_DRIVERS = ("__init__", "utils", "build", "stubs")


def libraries() -> dict:
    """{driver name: its utils.VendorLibrary} for every driver with a vendor library to find or build"""
    found = {}
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem in NOT_DRIVERS:
            continue
        lib = getattr(importlib.import_module(f"pysdrlib._ext.{path.stem}"), "LIB", None)
        if lib is not None:
            found[path.stem] = lib
    return found


def add_arguments(sub):
    """Add the `build` command to the `pysdrlib` command's subparsers"""
    parser = sub.add_parser("build", help="build the vendor libraries drivers need (importing a driver does not)",
                            description=__doc__.split("\n\n")[0])
    parser.add_argument("drivers", nargs="*", metavar="DRIVER", help="drivers to build the library of (none: show where each stands)")
    parser.add_argument("--all", action="store_true", help="build every library that is missing")
    parser.add_argument("--force", action="store_true", help="build even if the library is installed or was built before")
    return parser


def run(args) -> int:
    """`pysdrlib build`: returns the exit status"""
    libs = libraries()
    unknown = [d for d in args.drivers if d not in libs]
    if unknown:
        print(f"error: no driver {', '.join(map(repr, unknown))} with a library to build (have: {', '.join(libs)})", file=sys.stderr)
        return 2
    targets = list(libs) if args.all else args.drivers
    if not targets:
        return status(libs)
    failed = 0
    for driver in targets:
        lib = libs[driver]
        have = lib.locate()
        if have and not args.force:
            print(f"{driver}: already available ({have[0]}: {have[1]})")
            continue
        print(f"{driver}: building {lib.name}" + (f" ({lib.note})" if lib.note else "") + " ...", flush=True)
        started = time.monotonic()
        try:
            path = lib.build()
        except RuntimeError as exc:
            print(f"{driver}: {exc}", file=sys.stderr)
            failed += 1
            continue
        print(f"{driver}: built {path} in {time.monotonic() - started:.0f} s")
        if have and have[0] == "system":
            print(f"{driver}: (the installed {have[1]} is still the one that gets loaded)")
    return 1 if failed else 0


def status(libs) -> int:
    """Print where each driver's library is; 0"""
    width = max(map(len, libs))
    for driver, lib in libs.items():
        have = lib.locate()
        where = f"{have[0]}: {have[1]}" if have else f"missing: run `pysdrlib build {driver}`" + (f" ({lib.note})" if lib.note else "")
        print(f"{driver:<{width}}  {lib.name:<10} {where}")
    return 0
