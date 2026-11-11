#!/usr/bin/env python
"""Maintain the trimmed vendor snapshots under src/pysdrlib/<driver>/vendor/ (a development tool, never shipped).

    python tools/vendor.py snapshot uhd                  # download the pinned upstream tag, trace a build, write the snapshot
    python tools/vendor.py snapshot uhd --source DIR     # use an upstream checkout instead of downloading
    python tools/vendor.py snapshot uhd --out DIR        # write somewhere other than the recipe's `dest` (to review first)
    python tools/vendor.py trace uhd                     # only list the upstream files the build reads and what that weighs
    python tools/vendor.py snapshot --all                # every recipe

A recipe is `tools/vendor/<name>/`:
    vendor.toml          upstream repo and tag (or commit), and what to keep (see tools/vendor/uhd/vendor.toml)
    CMakeLists.txt       copied into the snapshot as the project `pysdrlib build` runs (optional if upstream's is used)
    trace/CMakeLists.txt (mode "trace", the default) builds the library from the *whole* upstream tree with the options
                         pysdrlib uses (-DVENDOR_SRC=...); the snapshot's CMakeLists.txt includes the sources.cmake written here
Recipes with `mode = "copy"` keep the files matching `include` (globs relative to the upstream root): for small libraries.

How it works: it configures and builds the trace project with Ninja and the CMake File API, then keeps exactly the
upstream files the compiler read (`ninja -t deps`), plus the outputs of generators
(Python scripts and the like) so a snapshot's build needs none of them. The source list, include directories and
upstream version come from the CMake code model, so bumping `ref` and running `snapshot` is the whole upgrade.
Check the result by building it (`pysdrlib build <driver>`) and running the tests before committing.
"""
import argparse
import collections
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = Path(__file__).resolve().parent / "vendor"


def load(name: str) -> dict:
    folder = RECIPES / name
    if not (folder / "vendor.toml").exists():
        sys.exit(f"error: no recipe {name!r} (have: {', '.join(sorted(p.name for p in RECIPES.iterdir() if p.is_dir()))})")
    cfg = tomllib.loads((folder / "vendor.toml").read_text())
    cfg.update(name=name, folder=folder)
    return cfg


def fetch(cfg: dict, into: Path) -> tuple[Path, str]:
    """Download and unpack the upstream tag; returns (its root directory, the tarball's sha256)"""
    url = f"https://github.com/{cfg['repo']}/archive/{cfg['ref']}.tar.gz"      # a tag or a commit
    print(f"downloading {url}")
    data = urllib.request.urlopen(url, timeout=300).read()
    digest = hashlib.sha256(data).hexdigest()
    print(f"sha256 {digest}")
    if cfg.get("sha256") and cfg["sha256"] != digest:
        sys.exit(f"error: {url} has sha256 {digest}, but vendor.toml says {cfg['sha256']}")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(into, filter="data")          # "data": no absolute paths, links out of `into`, or devices
    roots = [p for p in into.iterdir() if p.is_dir()]
    if len(roots) != 1:
        sys.exit(f"error: expected one directory in the tarball, found {len(roots)}")
    return roots[0], digest


def run(*cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def ninja(build: Path, *args) -> str:
    return run("ninja", "-C", build, "-t", *args, capture_output=True, text=True).stdout


def trace_build(cfg: dict, source: Path, build: Path):
    """Configure and build the trace project with the File API queries in place"""
    for query in ("codemodel-v2",):
        (build / ".cmake/api/v1/query").mkdir(parents=True, exist_ok=True)
        (build / ".cmake/api/v1/query" / query).touch()
    run("cmake", "-G", "Ninja", "-S", cfg["folder"] / "trace", "-B", build, f"-DVENDOR_SRC={source}", "-DCMAKE_BUILD_TYPE=Release")
    run("cmake", "--build", build)


def reply(build: Path, kind: str) -> dict:
    folder = build / ".cmake/api/v1/reply"
    index = json.loads(max(folder.glob("index-*.json")).read_text())
    ref = next(o for o in index["objects"] if o["kind"] == kind)
    return json.loads((folder / ref["jsonFile"]).read_text())


def compiler_deps(build: Path) -> set[Path]:
    """Every file named in any object's depfile (sources and headers)"""
    found = set()
    for line in ninja(build, "deps").splitlines():
        if line.startswith("    ") and line.strip():
            path = Path(line.strip())
            found.add((path if path.is_absolute() else build / path).resolve())
    return found


class Trace:
    """What a build read: `upstream` (files inside the source root) and `generated` (inside the build dir)"""

    def __init__(self, cfg: dict, source: Path, build: Path):
        self.cfg, self.source, self.build = cfg, source.resolve(), build.resolve()
        self.configured = cfg.get("configured", {})
        self.skip = cfg.get("skip", {}).get("substrings", [])
        read = compiler_deps(build)
        self.upstream = {p for p in read if p.is_relative_to(self.source)}
        self.generated = {p for p in read if p.is_relative_to(self.build) and self.keeps(p)}
        for template in self.configured.values():
            self.upstream.add(self.source / template)
        for extra in cfg.get("always", []):
            # a trailing `/**` means every file below (Python 3.12's glob matches only the directories)
            found = [m for m in self.source.glob(extra + "/*" if extra.endswith("/**") else extra) if m.is_file()]
            if not found:
                sys.exit(f"error: `always` entry {extra!r} matches nothing in the upstream tree")
            self.upstream.update(found)

    def build_rel(self, path: Path) -> str:
        """`path` (in the build dir) relative to the upstream project's binary dir"""
        rel = path.relative_to(self.build).as_posix()
        prefix = self.cfg.get("build_prefix", "")
        if prefix:
            if not rel.startswith(prefix + "/"):
                raise ValueError(f"{rel} is not under build_prefix {prefix!r}")
            rel = rel[len(prefix) + 1:]
        return rel

    def keeps(self, path: Path) -> bool:
        rel = path.relative_to(self.build).as_posix()
        return not (rel.endswith((".o", ".obj", ".a")) or any(s in rel for s in self.skip)
                    or not rel.startswith(self.cfg.get("build_prefix", "") + "/")) \
            and self.build_rel(path) not in self.configured

    def destination(self, path: Path) -> str | None:
        """Where `path` goes in the snapshot, or None if it is not kept"""
        path = path.resolve()
        if path.is_relative_to(self.source):
            return f"src/{path.relative_to(self.source).as_posix()}"
        if path.is_relative_to(self.build):
            if self.build_rel(path) in self.configured:
                return None
            return f"generated/{self.build_rel(path)}" if path in self.generated or path.is_dir() else None
        return None

    def size(self) -> int:
        return sum(p.stat().st_size for p in self.upstream | self.generated)


def cmake_lists(cfg: dict, trace: Trace, snapshot: Path, version: str) -> str:
    """sources.cmake: the target's sources and include directories as the code model has them, relative to the snapshot"""
    prefix = cfg["prefix"]
    model = reply(trace.build, "codemodel")
    top = Path(model["paths"]["source"])
    ref = next(t for t in model["configurations"][0]["targets"] if t["name"] == cfg["target"])
    target = json.loads((trace.build / ".cmake/api/v1/reply" / ref["jsonFile"]).read_text())

    def variable(path: Path) -> str:
        """CMake expression for a source or include path"""
        path = path.resolve()
        if path.is_relative_to(trace.build) and trace.build_rel(path) in trace.configured:
            return "${CMAKE_CURRENT_BINARY_DIR}/" + trace.build_rel(path)
        destination = trace.destination(path)
        if destination is None or not (snapshot / destination).exists():
            raise ValueError(f"{path} is not in the snapshot")
        return "${" + prefix + "_SNAPSHOT}/" + destination

    base = [f["fragment"] for f in target["compileGroups"][0]["compileCommandFragments"]]
    plain, flagged = [], collections.defaultdict(list)
    for group in target["compileGroups"]:
        extra = [f["fragment"].strip() for f in group["compileCommandFragments"] if f["fragment"] not in base]
        for index in group["sourceIndexes"]:
            path = Path(target["sources"][index]["path"])
            entry = variable(path if path.is_absolute() else top / path)
            (flagged[" ".join(extra)] if extra else plain).append(entry)
    configured_dirs = {Path(c).parent.as_posix() for c in trace.configured}
    includes = []
    for item in target["compileGroups"][0]["includes"]:
        path = Path(item["path"]).resolve()
        if path.is_relative_to(trace.build) and trace.build_rel(path) in configured_dirs:
            includes.append("${CMAKE_CURRENT_BINARY_DIR}/" + trace.build_rel(path))
        elif path.is_relative_to(trace.source) or path.is_relative_to(trace.build):
            destination = trace.destination(path)
            if destination and (snapshot / destination).exists():
                includes.append("${" + prefix + "_SNAPSHOT}/" + destination)
        # anything else (boost, libusb) is found again by the flat CMakeLists

    def block(name, items):
        return f"set({name}\n" + "".join(f"    {item}\n" for item in items) + ")\n"

    text = (f"# Written by tools/vendor.py from the traced upstream build: do not edit\n"
            f'set(VENDOR_VERSION "{version}")\n' + block(f"{prefix}_SOURCES", sorted(plain)))
    for flags, items in flagged.items():
        # Compile-option groups (e.g. `-msse2`), named after the flag
        name = "".join(c if c.isalnum() else "" for c in flags).upper()
        text += block(f"{prefix}_{name}_SOURCES", sorted(items))
    text += block(f"{prefix}_INCLUDE_DIRS", list(dict.fromkeys(includes)))
    return text


def write_snapshot(cfg: dict, trace: Trace, out: Path, digest: str, version: str):
    if out.exists():
        for child in out.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted(trace.upstream):
        dst = out / "src" / path.relative_to(trace.source)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    for path in sorted(trace.generated):
        dst = out / "generated" / trace.build_rel(path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    shutil.copy2(cfg["folder"] / "CMakeLists.txt", out / "CMakeLists.txt")
    (out / "sources.cmake").write_text(cmake_lists(cfg, trace, out, version))
    kept = sum(1 for p in out.rglob("*") if p.is_file())
    (out / "PROVENANCE").write_text(
        f"Trimmed snapshot of https://github.com/{cfg['repo']} at {cfg['ref']}\n"
        f"tarball sha256: {digest}\n"
        f"Generated by tools/vendor.py (recipe tools/vendor/{cfg['name']}): the upstream files a build read, the outputs of\n"
        f"its generators (generated/), CMakeLists.txt, and sources.cmake. Upstream's licenses are kept; do not edit by hand.\n"
        f"{kept} files\n")


def snapshot_copy(cfg: dict, source: Path, out: Path, digest: str):
    """mode = "copy": keep the files matching `include`, below `into` (and without the `strip` prefix) in the destination"""
    if out.exists():
        for child in out.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    kept = {}
    for pattern in cfg["include"]:
        # a trailing `/**` means every file below (Python 3.12's glob matches only the directories)
        matches = [p for p in source.glob(pattern + "/*" if pattern.endswith("/**") else pattern) if p.is_file()]
        if not matches:
            sys.exit(f"error: nothing in the upstream tree matches {pattern!r}")
        for path in matches:
            rel = path.relative_to(source).as_posix()
            if cfg.get("strip") and rel.startswith(cfg["strip"]):
                rel = rel[len(cfg["strip"]):]
            kept[path] = Path(cfg.get("into", "")) / rel
    for path, rel in sorted(kept.items()):
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out / rel)
    if (cfg["folder"] / "CMakeLists.txt").exists():
        shutil.copy2(cfg["folder"] / "CMakeLists.txt", out / "CMakeLists.txt")
    (out / "PROVENANCE").write_text(
        f"Trimmed copy of https://github.com/{cfg['repo']} at {cfg['ref']}\n"
        f"tarball sha256: {digest}\n"
        f"Generated by tools/vendor.py (recipe tools/vendor/{cfg['name']}): only the files its build needs. Do not edit by hand.\n"
        f"{len(kept)} files, {sum(p.stat().st_size for p in kept) / 1e3:.0f} kB\n")
    print(f"kept {len(kept)} files, {sum(p.stat().st_size for p in kept) / 1e3:.0f} kB")


def report(trace: Trace):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        for path in sorted(trace.upstream | trace.generated):
            tar.add(path, arcname=path.name)
    print(f"upstream files read: {len(trace.upstream)}, generated kept: {len(trace.generated)}, "
          f"{trace.size() / 1e6:.2f} MB raw, ~{len(buf.getvalue()) / 1e6:.2f} MB compressed")
    sizes, counts = collections.Counter(), collections.Counter()
    for path in trace.upstream:
        parts = path.relative_to(trace.source).parts
        key = "/".join(parts[:3] if len(parts) > 3 else parts[:-1]) or "."
        sizes[key] += path.stat().st_size
        counts[key] += 1
    for key, size in sizes.most_common(8):
        print(f"  {size / 1e3:7.0f} kB {counts[key]:4d} files  {key}")


def run_recipe(args, name: str):
    cfg = load(name)
    out = (args.out or ROOT / cfg["dest"]).resolve()
    with tempfile.TemporaryDirectory(prefix=f"vendor-{name}-") as tmp:
        work = args.keep.resolve() if args.keep else Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        if args.source:
            source, digest = args.source.resolve(), "(local checkout)"
        else:
            source, digest = fetch(cfg, work / "upstream")
        if cfg.get("mode") == "copy":
            if args.action == "snapshot":
                snapshot_copy(cfg, source, out, digest)
            else:
                print("(copy recipe: nothing to trace)")
            return
        build = work / "build"
        trace_build(cfg, source, build)
        trace = Trace(cfg, source, build)
        report(trace)
        if args.action == "snapshot":
            write_snapshot(cfg, trace, out, digest, cfg["ref"].lstrip("v"))
            print(f"wrote {out}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["snapshot", "trace"])
    parser.add_argument("name", nargs="?", help="recipe: a directory in tools/vendor/")
    parser.add_argument("--all", action="store_true", help="every recipe")
    parser.add_argument("--source", type=Path, help="upstream checkout to use instead of downloading `ref`")
    parser.add_argument("--out", type=Path, help="write the snapshot here instead of the recipe's `dest`")
    parser.add_argument("--keep", type=Path, help="build the trace in this directory and leave it (default: a temporary one)")
    args = parser.parse_args(argv)
    names = sorted(p.name for p in RECIPES.iterdir() if p.is_dir()) if args.all else [args.name]
    if names == [None] or ((args.source or args.out or args.keep) and len(names) > 1):
        parser.error("give a recipe name (or --all; --source, --out and --keep need one recipe)")
    for name in names:
        print(f"== {name}")
        run_recipe(args, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
