#!/usr/bin/env python
"""Generate the parts of the documentation that come from the code: API pages and the llms files

    python tools/gen_docs.py             # writes docs/api/*.md, docs/llms.txt, docs/llms-full.txt
    python tools/gen_docs.py --check     # exits 1 if zensical.toml's API nav and API_PAGES disagree

`python tools/builder.py -d` runs this, then `zensical build`. Zensical runs no MkDocs plugins (gen-files), so the pages that
render each module (`::: pysdrlib.<module>`, mkdocstrings) are written here, one per API_PAGES entry. The source is read
with griffe, as mkdocstrings does: nothing is imported, so no compiled extension or vendor library is needed.

llms.txt (llmstxt.org) lists the pages; llms-full.txt is AGENTS.md, then for every API page's modules their docstring
and a one-line signature and summary of what they define (each driver's declared CAPS too), so an agent can read the
whole library in one file. Both are copied into the site by the build.
"""
import argparse
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


# The API reference: one page per entry, rendered by mkdocstrings from the docstrings (read statically: no compiled
# extension is imported). A name ending in "/" is a package: its docstring, its own functions and classes, and each
# public submodule. Every page must be in zensical.toml's nav (`--check` and tests/test_docs.py check).
API_PAGES = {
    "pysdrlib": ["pysdrlib"],
    "devices": ["pysdrlib.devices", "pysdrlib.targets"],
    "device": ["pysdrlib.base.device"],
    "caps": ["pysdrlib.base.caps", "pysdrlib.base.antennas"],
    "stream": ["pysdrlib.base.stream"],
    "formats": ["pysdrlib.base.formats", "pysdrlib.base.convert"],
    "errors": ["pysdrlib.err", "pysdrlib.warn"],
    "file": ["pysdrlib.file/"],
    "remote": ["pysdrlib.remote/"],
    "sim": ["pysdrlib.sim/"],
    "shm": ["pysdrlib.shm/"],
    "native": ["pysdrlib.base.native"],
    "testing": ["pysdrlib.testing"],
    "contributing": ["pysdrlib.contributing"],
    "scaffold": ["pysdrlib.scaffold"],
    "rtl_sdr": ["pysdrlib.rtl_sdr/"],
    "hackrf": ["pysdrlib.hackrf/"],
    "uhd": ["pysdrlib.uhd/"],
    "bladerf": ["pysdrlib.bladerf/"],
    "extio": ["pysdrlib.extio/"],
    "tcpiq": ["pysdrlib.tcpiq/"],
}
NOT_API = {"lib", "vendor"}   # a driver's compiled extension and its vendored sources


def load_package():
    """pysdrlib as griffe reads it from src/, without importing it"""
    import griffe
    return griffe.load("pysdrlib", search_paths=[str(ROOT / "src")], resolve_aliases=False)


def _summary(obj):
    return obj.docstring.value.strip().splitlines()[0] if obj.docstring else ""


def api_stub(pkg, target):
    """The mkdocstrings directive(s) for one API_PAGES entry"""
    name = target.rstrip("/")
    if name == "pysdrlib":   # its names are re-exports, each documented on its own page
        return "::: pysdrlib\n    options:\n      members: false\n"
    if not target.endswith("/"):
        return f"::: {name}\n"
    mod = pkg[name.removeprefix("pysdrlib.")]
    members = [n for n, m in mod.members.items()
               if not n.startswith("_") and not m.is_alias and (m.is_function or m.is_class)]
    members += [n for n, m in sorted(mod.modules.items()) if not m.is_alias and not n.startswith("_") and n not in NOT_API]
    return f"::: {name}\n    options:\n      show_submodules: true\n      members: [{', '.join(members)}]\n"


def api_pages(pkg):
    """docs/api/<page>.md for every API_PAGES entry, and the index (docs/api/ is generated: gitignored)"""
    out = DOCS / "api"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    rows = []
    for page, targets in API_PAGES.items():
        (out / f"{page}.md").write_text("\n".join(api_stub(pkg, t) for t in targets))
        for t in targets:
            name = t.rstrip("/")
            obj = pkg if name == "pysdrlib" else pkg[name.removeprefix("pysdrlib.")]
            rows.append(f"| [{name}]({page}.md) | {_summary(obj).replace('|', '\\|')} |")
    (out / "index.md").write_text("# API reference\n\nEvery public module, rendered from its docstrings. How to use "
                                  "them is in the guides; the device API every driver implements is "
                                  "[pysdrlib.base.device](device.md).\n\n| Module | What it is |\n|---|---|\n"
                                  + "\n".join(rows) + "\n")



def site_url():
    with open(ROOT / "zensical.toml", "rb") as f:
        return tomllib.load(f)["project"]["site_url"].rstrip("/")


def nav_pages():
    """[(section, path)] for every page in zensical.toml's nav, in order"""
    with open(ROOT / "zensical.toml", "rb") as f:
        nav = tomllib.load(f)["project"]["nav"]
    out = []
    def walk(items, section):
        for item in items:
            if isinstance(item, str):
                out.append((section, item))
                continue
            for title, value in item.items():
                if isinstance(value, str):
                    out.append((section or title, value))
                else:
                    walk(value, section or title)
    walk(nav, None)
    return out


def title_of(path):
    """The first heading of a docs page (an API page: its module name)"""
    md = DOCS / path
    if path == "index.md":
        return "pysdrlib (the README)"
    if path.startswith("api/") and path != "api/index.md":
        page = Path(path).stem
        return ", ".join(t.rstrip("/") for t in API_PAGES[page])
    for line in md.read_text().splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(path).stem


def url_of(path):
    if path == "index.md":
        return site_url() + "/"
    return f"{site_url()}/{path.removesuffix('.md').removesuffix('/index')}/"


def first_line(obj):
    return obj.docstring.value.strip().splitlines()[0] if obj.docstring else ""


def signature(fn):
    params = []
    for p in fn.parameters:
        text = p.name
        if p.kind.value == "variadic positional":
            text = "*" + text
        elif p.kind.value == "variadic keyword":
            text = "**" + text
        if p.annotation is not None:
            text += f": {p.annotation}"
        if p.default is not None:
            text += f"={p.default}"
        params.append(text)
    ret = f" -> {fn.returns}" if fn.returns is not None else ""
    return f"{fn.name}({', '.join(params)}){ret}"


def public(obj):
    return [m for n, m in obj.members.items() if not n.startswith("_") and not m.is_alias]


def describe_module(mod, depth=0):
    """A module's docstring and its public definitions, one line each"""
    lines = [f"\n{'#' * (2 + depth)} {mod.path}\n"]
    if mod.docstring:
        lines.append(mod.docstring.value.strip() + "\n")
    defs = []
    for m in public(mod):
        if m.is_function:
            defs.append(f"- `{signature(m)}`: {first_line(m)}")
        elif m.is_class:
            bases = ", ".join(str(b) for b in m.bases)
            defs.append(f"- class `{m.name}({bases})`: {first_line(m)}")
            for meth in public(m):
                if meth.is_function:
                    defs.append(f"  - `{signature(meth)}`: {first_line(meth)}")
                elif meth.is_attribute and meth.name == "CAPS":
                    defs.append(f"  - `CAPS = {meth.value}`")
    if defs:
        lines.append("\n".join(defs) + "\n")
    return lines


def llms_txt(pages):
    summary = (load_package().docstring.value.strip().splitlines()[0])
    out = ["# pysdrlib", "", f"> {summary}", "",
           "Every radio (RTL-SDR, HackRF, USRP B2xx/X310/X410, bladeRF 2.0, RX-888, a TCP I/Q stream, a simulated radio, a "
           "recording) is a `pysdrlib.Device` with the same calls. Radios can be hosted by a server and used from other "
           "machines or shared between programs (pysdrlib.remote).", ""]
    sections = {}
    for section, path in pages:
        sections.setdefault(section or "Docs", []).append(path)
    for section, paths in sections.items():
        out += [f"## {section}", ""]
        for path in paths:
            out.append(f"- [{title_of(path)}]({url_of(path)})")
        out.append("")
    out += ["## Optional", "", f"- [llms-full.txt]({site_url()}/llms-full.txt): AGENTS.md, every module docstring and "
            "the public signatures in one file", ""]
    return "\n".join(out)


def llms_full():
    pkg = load_package()
    out = [(ROOT / "AGENTS.md").read_text().strip(), "", "# API: module docstrings and public signatures"]
    for targets in API_PAGES.values():
        for target in targets:
            name = target.rstrip("/")
            mod = pkg if name == "pysdrlib" else pkg[name.removeprefix("pysdrlib.")]
            out += describe_module(mod)
            if target.endswith("/"):
                for sub_name, sub in sorted(mod.modules.items()):
                    if not sub.is_alias and not sub_name.startswith("_") and sub_name not in NOT_API:
                        out += describe_module(sub, 1)
    return "\n".join(out) + "\n"


def nav_api():
    """The API pages zensical.toml's nav lists, in order (the Drivers group flattened)"""
    for section, path in nav_pages():
        if path.startswith("api/"):
            yield path


def check_nav():
    want = ["api/index.md"] + [f"api/{p}.md" for p in API_PAGES]
    have = list(nav_api())
    if have != want:
        print("zensical.toml nav API should list, in this order:\n" + "\n".join(f'    "{p}",' for p in want))
        return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="only check zensical.toml's API nav")
    ap.add_argument("--out", type=Path, default=DOCS, help="folder for llms.txt and llms-full.txt (default: docs/)")
    a = ap.parse_args(argv)
    if a.check:
        sys.exit(0 if check_nav() else 1)
    pkg = load_package()
    api_pages(pkg)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "llms.txt").write_text(llms_txt(nav_pages()))
    full = llms_full()
    (a.out / "llms-full.txt").write_text(full)
    if not check_nav():
        sys.exit(1)
    print(f"wrote {DOCS / 'api'}, {a.out / 'llms.txt'}, {a.out / 'llms-full.txt'} ({len(full.encode()) // 1024} KiB)")


if __name__ == "__main__":
    main()
