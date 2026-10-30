# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The website build checks the links of a text after the placeholders are filled in (GHSA-w7hp-wv8j-xff9).

{{root}} is empty on the German pages, so a text such as "{{root}}javascript:…" passed the check of #355 on the raw
value and ended up as a javascript: link on the published page.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_PAGES = ROOT / "scripts" / "build-site-pages.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

PAYLOADS = [
    '<a href="{{root}}javascript:alert(document.domain)">x</a>',
    '<a href="java{{root}}script:alert(document.domain)">x</a>',
]


def build_pages(cwd: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(BUILD_PAGES), str(out)], cwd=cwd, capture_output=True, text=True, timeout=60)


def copy_of_the_site(tmp_path: Path) -> Path:
    """The real website sources (templates, texts, language folders of the app, changelog) in tmp_path/repo."""
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "site", repo / "site")
    for meta in (ROOT / "web" / "src" / "locales").glob("*/meta.json"):
        target = repo / "web" / "src" / "locales" / meta.parent.name / "meta.json"
        target.parent.mkdir(parents=True)
        shutil.copy(meta, target)
    (repo / "web" / "public").mkdir(parents=True)
    shutil.copy(ROOT / "web" / "public" / "changelog.json", repo / "web" / "public" / "changelog.json")
    (repo / "docs" / "devices").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "docs" / "devices" / "support.json", repo / "docs" / "devices" / "support.json")
    return repo


def add_to_eyebrow(repo: Path, lang: str, payload: str) -> None:
    path = repo / "site" / "locales" / lang / "index.json"
    texts = json.loads(path.read_text())
    texts["hero"]["eyebrow"] += " " + payload
    path.write_text(json.dumps(texts, ensure_ascii=False))


@needs_node
def test_the_copied_site_builds(tmp_path):
    result = build_pages(copy_of_the_site(tmp_path), tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "index.html").exists()


@needs_node
@pytest.mark.parametrize("payload", PAYLOADS)
def test_a_scheme_completed_by_a_placeholder_fails_the_build(tmp_path, payload):
    repo = copy_of_the_site(tmp_path)
    add_to_eyebrow(repo, "de", payload)
    out = tmp_path / "out"
    result = build_pages(repo, out)
    assert result.returncode == 1, result.stderr
    assert "index.hero.eyebrow" in result.stderr
    assert "neither https:// nor relative" in result.stderr
    # no page is written, in no language (the build stops before it creates the output folder)
    assert not list(out.rglob("*.html"))


@needs_node
def test_a_scheme_completed_by_the_language_code_fails_the_build(tmp_path):
    repo = copy_of_the_site(tmp_path)
    add_to_eyebrow(repo, "en", '<a href="{{lang}}:alert(1)">x</a>')  # "en:alert(1)" on the English page
    out = tmp_path / "out"
    result = build_pages(repo, out)
    assert result.returncode == 1, result.stderr
    assert "site/locales/en/index.json: index.hero.eyebrow" in result.stderr
    assert not list(out.rglob("*.html"))


@needs_node
def test_placeholders_in_relative_links_still_pass(tmp_path):
    repo = copy_of_the_site(tmp_path)
    add_to_eyebrow(repo, "de", '<a href="{{root}}demo/?lang={{lang}}">Demo</a>')
    result = build_pages(repo, tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert '<a href="demo/?lang=de">Demo</a>' in (tmp_path / "out" / "index.html").read_text()
