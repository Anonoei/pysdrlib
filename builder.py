#!/usr/bin/env python
"""Development helper: bump the version, compile the extensions, build, install and test.

Releases are published by CI (.github/workflows/python-publish.yml) from a GitHub release tagged vX.Y.Z, and the docs by
deploy-docs.yml: neither is done from here.
"""

import argparse
import importlib.metadata
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent


def run(*cmd):
    """Run a command in the repository; exit with its status if it fails (later steps would build on a failure)"""
    print(f"Running '{shlex.join(cmd)}'", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        sys.exit(f"'{shlex.join(cmd)}' failed with status {result.returncode}")


def need(*packages):
    """Exit, saying how to install them, if any of these distributions is missing"""
    missing = []
    for name in packages:
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        sys.exit(f"missing {', '.join(missing)}: pip install -e '.[dev]'")


def version():
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def cmd_version(args):
    need("bumpver")
    part = {"M": "--major", "m": "--minor", "p": "--patch"}[args.version]
    bump = ["bumpver", "update", "--allow-dirty", "--no-tag-commit", part]   # releases are tagged vX.Y.Z on GitHub
    if not args.run:
        bump += ["--dry", "-n"]
    run(*bump)
    if args.run:
        print(f"Bumped to {version()}: to release it, publish a GitHub release tagged v{version()}")


def cmd_compile(args):
    need("pybind11", "setuptools")
    run(sys.executable, "setup.py", "build_ext", "-i")


def cmd_build(args):
    need("build")
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    run(sys.executable, "-m", "build")


def cmd_local(args):
    run(sys.executable, "-m", "pip", "install", "-e", ".")


def cmd_test(args):
    need("pytest")
    run(sys.executable, "-m", "pytest")


def cmd_new_device(args):
    run(sys.executable, "-m", "pysdrlib.scaffold", args.new_device)


def main():
    parser = argparse.ArgumentParser("builder.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_argument_group("Actions", "run in this order")
    actions.add_argument("-n", "--new-device", metavar="NAME", help="generate the skeleton of a new driver")
    actions.add_argument("-v", "--version", choices=["M", "m", "p"], default=None,
                         help="bump the major, minor or patch version and commit it (a dry run without -r)")
    actions.add_argument("-c", "--compile", action="store_true", help="compile the extensions in place")
    actions.add_argument("-b", "--build", action="store_true", help="build the sdist and wheel into dist/")
    actions.add_argument("-l", "--local", action="store_true", help="install the package in editable mode")
    actions.add_argument("-t", "--test", action="store_true", help="run the tests")
    parser.add_argument("-r", "--run", action="store_true", help="really bump the version (-v)")
    args = parser.parse_args()

    os.chdir(ROOT)
    steps = [(args.new_device, cmd_new_device), (args.version, cmd_version), (args.compile, cmd_compile),
             (args.build, cmd_build), (args.local, cmd_local), (args.test, cmd_test)]
    if not any(wanted for wanted, _ in steps):
        parser.print_help()
        return
    for wanted, step in steps:
        if wanted:
            step(args)


if __name__ == "__main__":
    main()
