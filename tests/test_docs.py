"""The documentation is complete: every public package has its page's text, every API page is in the site's nav, every
example is shown by a guide, the README's quickstart is the tested one, and the llms files can be generated. (The site
itself is built by `python tools/builder.py -d`, in CI.)"""
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_docs  # noqa: E402

pytestmark = pytest.mark.skipif(importlib.util.find_spec("griffe") is None, reason="needs the docs extra (griffe)")


def nav_paths():
    with open(ROOT / "zensical.toml", "rb") as f:
        nav = tomllib.load(f)["project"]["nav"]
    out = []
    def walk(items):
        for item in items:
            if isinstance(item, str):
                out.append(item)
            else:
                for value in item.values():
                    walk([value] if isinstance(value, str) else value)
    walk(nav)
    return out


def test_every_api_page_is_in_the_nav_and_every_nav_page_exists():
    nav = nav_paths()
    for page in gen_docs.API_PAGES:
        assert f"api/{page}.md" in nav, f"api/{page}.md is missing from zensical.toml's nav"
    for path in nav:
        if not path.startswith("api/"):
            assert (ROOT / "docs" / path).is_file(), path
        else:
            assert path == "api/index.md" or Path(path).stem in gen_docs.API_PAGES, path


def test_api_nav_matches_api_pages():
    assert gen_docs.check_nav(), "zensical.toml's API nav and gen_docs.API_PAGES disagree (python tools/gen_docs.py --check)"


def test_every_driver_has_an_api_page():
    pkg = gen_docs.load_package()
    drivers = [n for n, m in pkg.modules.items() if not m.is_alias and "Device" in m.members and n not in ("base",)]
    pages = {t.rstrip("/") for targets in gen_docs.API_PAGES.values() for t in targets}
    for name in drivers:
        assert f"pysdrlib.{name}" in pages, f"pysdrlib.{name} has no page in gen_docs.API_PAGES"


def test_every_documented_module_has_a_docstring():
    pkg = gen_docs.load_package()
    for targets in gen_docs.API_PAGES.values():
        for target in targets:
            name = target.rstrip("/")
            mod = pkg if name == "pysdrlib" else pkg[name.removeprefix("pysdrlib.")]
            assert mod.docstring and len(mod.docstring.value.strip().splitlines()) >= 3 or \
                (mod.docstring and len(mod.docstring.value) > 80), f"{name} has no real docstring"


def test_no_package_docstring_is_read_from_a_file():
    """mkdocstrings reads the source: a __doc__ assigned at import time never reaches the site"""
    for init in (ROOT / "src" / "pysdrlib").glob("*/__init__.py"):
        assert not re.search(r"^__doc__\s*=", init.read_text(), re.M), init


def test_every_example_is_shown_by_a_guide():
    shown = "\n".join(p.read_text() for p in (ROOT / "docs").rglob("*.md"))
    for script in (ROOT / "examples").glob("*.py"):
        assert f'"examples/{script.name}' in shown, f"no guide embeds examples/{script.name}"


def test_every_snippet_section_exists():
    """--8<-- "examples/x.py:name" needs the [start:name] / [end:name] markers (the build checks paths, not sections)"""
    for page in (ROOT / "docs").rglob("*.md"):
        for path, section in re.findall(r'^--8<-- "([^":]+):([^"]+)"', page.read_text(), re.M):
            text = (ROOT / path).read_text()
            assert f"[start:{section}]" in text and f"[end:{section}]" in text, f"{page.name}: {path}:{section}"


def test_readme_quickstart_is_the_tested_one():
    readme = (ROOT / "README.md").read_text()
    example = (ROOT / "examples" / "quickstart.py").read_text()
    for section in ("find", "read"):
        code = re.search(rf"# --8<-- \[start:{section}\]\n(.*?)# --8<-- \[end:{section}\]", example, re.S).group(1)
        assert code in readme, f"README.md's quickstart differs from examples/quickstart.py ({section})"


def test_llms_files(tmp_path):
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "tools" / "gen_docs.py"), "--out", str(tmp_path)], check=True,
                   capture_output=True)
    index = (tmp_path / "llms.txt").read_text()
    assert index.startswith("# pysdrlib\n\n> ") and "/guide/receiving/" in index and "/api/hackrf/" in index
    full = (tmp_path / "llms-full.txt").read_text()
    assert full.startswith((ROOT / "AGENTS.md").read_text().strip()[:200])
    assert "def " not in full[:100] and "set_rx_gain(" in full and "## pysdrlib.remote" in full
    assert len(full.encode()) < 240_000, "llms-full.txt has outgrown its budget (~60k tokens)"
