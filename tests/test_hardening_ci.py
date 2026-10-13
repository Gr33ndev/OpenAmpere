# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Website build and release workflow (#355): the texts of the website may only contain a few harmless tags, and the
release pulls its QEMU and BuildKit helper images by digest, not by a tag that can change."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILD_PAGES = ROOT / "scripts" / "build-site-pages.mjs"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
PINNED = re.compile(r"^docker\.io/[a-z0-9]+/[a-z0-9-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def build_pages(cwd: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(BUILD_PAGES), str(out)], cwd=cwd, capture_output=True, text=True, timeout=60)


def site(tmp_path: Path, de: str, en: str = "Text") -> Path:
    """A website with one page that shows one text, in German and English."""
    (tmp_path / "site" / "pages").mkdir(parents=True)
    (tmp_path / "site" / "pages" / "index.html").write_text("<p>{{index.text}}</p>\n")
    for lang, name, value in (("de", "Deutsch", de), ("en", "English", en)):
        folder = tmp_path / "site" / "locales" / lang
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({"name": name}))
        (folder / "index.json").write_text(json.dumps({"text": value}))
        (folder / "common.json").write_text(json.dumps({"languageSwitch": {"label": name}}))
    return tmp_path


@needs_node
def test_the_texts_of_the_website_pass(tmp_path):
    result = build_pages(ROOT, tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert "error:" not in result.stderr


@needs_node
@pytest.mark.parametrize("value", [
    'Ein <a href="https://example.org/?a=1&amp;b=2">Link</a>',
    'Die <a href="{{root}}demo/?lang={{lang}}">Demo</a>, <a href="faq.html#top">Fragen</a>, <a href=".">Start</a>',
    "<p><strong>fett</strong>, <em>betont</em>, <code>code</code><br>neue Zeile<br /></p>",
    "A &lt; B &amp; C",
])
def test_harmless_html_is_allowed(tmp_path, value):
    result = build_pages(site(tmp_path, value), tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert value.replace("{{root}}", "").replace("{{lang}}", "de") in (tmp_path / "out" / "index.html").read_text()


@needs_node
@pytest.mark.parametrize("value", [
    "<script>alert(1)</script>",
    '<img src="x" onerror="alert(1)">',
    "<img src=x onerror=alert(1)>",
    '<a href="https://example.org" onclick="alert(1)">x</a>',
    '<strong onmouseover="alert(1)">x</strong>',
    '<a href="javascript:alert(1)">x</a>',
    '<a href="JavaScript:alert(1)">x</a>',
    '<a href=" javascript:alert(1)">x</a>',
    '<a href="\u0001javascript:alert(1)">x</a>',
    '<a href="java&#115;cript:alert(1)">x</a>',
    '<a href="javascript&colon;alert(1)">x</a>',
    '<a href="data:text/html,x">x</a>',
    '<a href="http://example.org">x</a>',
    '<a href="//example.org">x</a>',
    "<a href='javascript:alert(1)'>x</a>",
    '<iframe src="https://example.org"></iframe>',
    "<constructor>x</constructor>",
    "<svg/onload=alert(1)>",
    "A < B",
])
@pytest.mark.parametrize("lang", ["de", "en"])
def test_anything_else_fails_the_build(tmp_path, value, lang):
    texts = {"de": value} if lang == "de" else {"de": "Text", "en": value}
    result = build_pages(site(tmp_path, **texts), tmp_path / "out")
    assert result.returncode == 1
    assert f"error: site/locales/{lang}/index.json: index.text:" in result.stderr


def image_steps() -> dict[str, dict]:
    steps = yaml.safe_load(RELEASE.read_text())["jobs"]["image"]["steps"]
    return {step["uses"].split("@")[0]: step for step in steps if "uses" in step}


def test_the_qemu_image_is_pinned_by_digest():
    image = image_steps()["docker/setup-qemu-action"].get("with", {}).get("image", "")
    assert PINNED.match(image), image
    assert image.startswith("docker.io/tonistiigi/binfmt:")


def test_the_buildkit_image_is_pinned_by_digest():
    options = image_steps()["docker/setup-buildx-action"].get("with", {}).get("driver-opts", "")
    images = [option.removeprefix("image=") for option in options.split() if option.startswith("image=")]
    assert len(images) == 1 and PINNED.match(images[0]), options
    assert images[0].startswith("docker.io/moby/buildkit:")
