"""The `pysdrlib` command (also `python -m pysdrlib`): sends each subcommand to the module that does it

    pysdrlib build uhd                                   build a driver's vendor library             (pysdrlib._ext.build)
    pysdrlib server --bind tcp://0.0.0.0:5555            host the radios on this machine             (pysdrlib.remote.cli)
    pysdrlib relay tcp://host:5555 <driver>-0          share a far radio with programs here        (pysdrlib.remote.cli)
    pysdrlib client tcp://host:5555 list                 use the radios of a server                  (pysdrlib.remote.cli)

`build` needs nothing beyond pysdrlib; the others need pyzmq (`pip install pysdrlib[remote]`).
"""
import argparse
import sys

from ._ext import build
from .remote import cli as remote


def build_parser():
    """The `pysdrlib` argument parser, with the subcommands of every module"""
    parser = argparse.ArgumentParser(prog="pysdrlib", description="pysdrlib: build drivers' libraries, serve and use radios over a network")
    sub = parser.add_subparsers(dest="mode", required=True)
    build.add_arguments(sub)
    remote.add_parsers(sub)
    return parser


def main(argv=None):
    """Entry point of the `pysdrlib` command"""
    args = build_parser().parse_args(argv)
    if args.mode == "build":   # needs no pyzmq
        raise SystemExit(build.run(args))
    remote.run(args)


if __name__ == "__main__":
    sys.exit(main())
