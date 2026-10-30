# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The website shows the device knowledge of docs/devices/ (#512).

scripts/markdown.mjs renders the Markdown without a dependency and must be safe on its own: the pages are not checked
against the allowed tags like the texts of the website (#355). scripts/build-site-pages.mjs puts every page into every
language and turns the links into links of the website.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "scripts" / "markdown.mjs"
BUILD_PAGES = ROOT / "scripts" / "build-site-pages.mjs"
DOCS = ROOT / "docs" / "devices"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

RENDER = """
import { renderMarkdown } from %s;
let source = "";
process.stdin.on("data", (chunk) => { source += chunk; }).on("end", () => {
  // like the website: https:// and relative addresses, nothing with another scheme
  const link = (href) => (/^https:\\/\\//.test(href) || !href.includes(":") ? href : null);
  process.stdout.write(JSON.stringify(renderMarkdown(source, { link })));
});
"""


def render(source: str) -> dict:
    script = RENDER % json.dumps(MARKDOWN.as_uri())
    result = subprocess.run(["node", "--input-type=module", "-e", script], input=source, capture_output=True, text=True,
                            timeout=60, check=True)
    return json.loads(result.stdout)


def build_pages(cwd: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(BUILD_PAGES), str(out)], cwd=cwd, capture_output=True, text=True, timeout=120)


@needs_node
@pytest.mark.parametrize("source", [
    "<script>alert(1)</script>",
    "text <img src=x onerror=alert(1)> text",
    "| a |\n|---|\n| <iframe src=x> |",
    "> <svg onload=alert(1)>",
    "- <b onclick=alert(1)>x</b>",
    "```\n<script>alert(1)</script>\n```",
    "`<script>`",
])
def test_html_in_the_markdown_is_shown_as_text(source):
    html = render(source)["html"]
    assert not re.search(r"<(script|img|iframe|svg|b)\b", html), html
    assert "&lt;" in html


@needs_node
@pytest.mark.parametrize("source", [
    "[x](javascript:alert(1))",
    "[x](JavaScript:alert(1))",
    "[x](data:text/html,<script>alert(1)</script>)",
    "[x](<javascript:alert(1)>)",
    "![x](javascript:alert(1))",
    "<javascript:alert(1)>",
])
def test_a_link_with_another_scheme_keeps_only_its_text(source):
    html = render(source)["html"]
    assert "<a" not in html
    assert "javascript:" not in html.lower() or "&lt;" in html


@needs_node
def test_an_address_cannot_leave_its_attribute():
    html = render('[x](a.md"onmouseover="alert(1))')["html"]
    assert '"onmouseover' not in html
    assert "&quot;onmouseover" in html


@needs_node
def test_a_nul_in_the_text_cannot_stand_for_rendered_html():
    # NUL marks the rendered parts while the emphasis is matched
    html = render("[x](https://example.com) \0" + "0\0 **b**")["html"]
    assert html.count("<a ") == 1
    assert "\0" not in html


@needs_node
def test_the_title_and_id_of_a_heading_are_plain_text():
    page = render("# Map `<script>` & **more**")
    assert page["title"] == "Map <script> & more"  # escaped where it is used, like any other text of the page
    assert page["ids"] == ["map-script--more"]


@needs_node
def test_the_markdown_of_the_device_pages():
    page = render(
        "# FoxESS H3\n\n"
        "> **Not official** information. See [Device knowledge](../README.md).\n\n"
        "**Applies to:** H3 · **OpenAmpere:** driver `foxess`\n"
        "second line of the paragraph, see #141 and https://example.com/a_(b).\n\n"
        "## Remote control (grid charging)\n\n"
        "| Register | Meaning | Status |\n|---|:-:|---|\n| 44000 | Remote `a\\|b` | *derived* |\n\n"
        "- one\n- two\n  - nested\n\n1. first\n2. second\n\n"
        "## Remote control (grid charging)\n"
    )
    html = page["html"]
    assert page["title"] == "FoxESS H3"
    # the ids of GitHub, so "h3.md#remote-control-grid-charging" keeps working
    assert page["ids"] == ["foxess-h3", "remote-control-grid-charging", "remote-control-grid-charging-1"]
    assert '<blockquote>\n<p><strong>Not official</strong> information. See <a href="../README.md">Device knowledge</a>.</p>' in html
    assert "<strong>Applies to:</strong> H3 · <strong>OpenAmpere:</strong> driver <code>foxess</code>\nsecond line" in html
    assert '<a href="https://example.com/a_(b)">https://example.com/a_(b)</a>.' in html
    assert '<th class="center">Meaning</th>' in html
    assert "<td>44000</td><td class=\"center\">Remote <code>a|b</code></td><td><em>derived</em></td>" in html
    assert "<ul>\n<li>one</li>\n<li>two\n<ul>\n<li>nested</li>\n</ul></li>\n</ul>" in html
    assert "<ol>\n<li>first</li>\n<li>second</li>\n</ol>" in html


@needs_node
def test_every_page_of_the_knowledge_is_on_the_website(tmp_path):
    out = tmp_path / "out"
    result = build_pages(ROOT, out)
    assert result.returncode == 0, result.stderr
    # every link of the knowledge leads to a file and every heading link to a heading
    assert "docs/devices/" not in result.stderr, result.stderr
    pages = sorted(path.relative_to(DOCS) for path in DOCS.rglob("*.md"))
    assert pages
    for page in pages:
        name = "index.html" if page.name == "README.md" else page.with_suffix(".html").name
        for lang_dir in (out, out / "en"):
            assert (lang_dir / "docs" / "devices" / page.parent / name).exists(), page

    h3 = (out / "docs" / "devices" / "foxess" / "h3.html").read_text()
    assert '<html lang="de">' in h3 and '<article class="doc" lang="en">' in h3
    assert "<title>FoxESS H3, H3 Smart, H3 Pro | Gerätewissen | OpenAmpere</title>" in h3
    assert 'href="h3-new-map.html"' in h3
    assert 'href="../index.html"' in h3
    assert 'href="https://github.com/Gr33ndev/OpenAmpere/blob/main/src/openampere/drivers/foxess/driver.py"' in h3
    assert 'href="https://github.com/Gr33ndev/OpenAmpere/blob/main/docs/devices/foxess/h3.md"' in h3
    # the frame: assets from the site's root, the rest from the language's root
    assert 'href="../../../style.css"' in h3 and 'href="../../../faq.html"' in h3
    english = (out / "en" / "docs" / "devices" / "foxess" / "h3.html").read_text()
    assert 'href="../../../../style.css"' in english and 'href="../../../faq.html"' in english
    assert 'href="../../../../docs/devices/foxess/h3.html" hreflang="de"' in english
    for path in out.rglob("docs/devices/**/*.html"):
        # no relative link to a Markdown file is left
        assert not re.search(r'href="(?!https://)[^"]*\.md[#"]', path.read_text()), path
    assert "<title>Gerätewissen | OpenAmpere</title>" in (out / "docs" / "devices" / "index.html").read_text()
    assert 'href="docs/devices/"' in (out / "index.html").read_text()


@needs_node
def test_a_link_that_leads_nowhere_is_reported(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "site", repo / "site")
    for meta in (ROOT / "web" / "src" / "locales").glob("*/meta.json"):
        target = repo / "web" / "src" / "locales" / meta.parent.name / "meta.json"
        target.parent.mkdir(parents=True)
        shutil.copy(meta, target)
    (repo / "web" / "public").mkdir(parents=True)
    shutil.copy(ROOT / "web" / "public" / "changelog.json", repo / "web" / "public" / "changelog.json")
    docs = repo / "docs" / "devices"
    (docs / "foxess").mkdir(parents=True)
    (docs / "README.md").write_text("# Device knowledge\n\n## Pages\n\n[H3](foxess/h3.md), [up](../../..)\n")
    (docs / "foxess" / "h3.md").write_text(
        "# FoxESS H3\n\n[gone](h3-gone.md), [no heading](../README.md#nothing), [here](#nowhere), [ok](../README.md#pages), "
        "[outside](../../../../etc/passwd), [script](javascript:alert(1))\n")

    out = tmp_path / "out"
    result = build_pages(repo, out)
    assert result.returncode == 0, result.stderr
    assert "docs/devices/foxess/h3.md: the link to h3-gone.md leads nowhere" in result.stderr
    assert "docs/devices/foxess/h3.md: the link to README.md#nothing leads to no heading" in result.stderr
    assert "docs/devices/foxess/h3.md: the link to foxess/h3.md#nowhere leads to no heading" in result.stderr
    assert "../../../../etc/passwd is left out" in result.stderr
    assert "javascript:alert(1) is left out" in result.stderr
    assert "docs/devices/README.md: the link to ../../.. is left out" in result.stderr
    assert "#pages" not in result.stderr
    html = (out / "docs" / "devices" / "foxess" / "h3.html").read_text()
    assert 'href="../index.html#pages"' in html
    assert "javascript:" not in html


@needs_node
def test_a_page_without_a_title_fails_the_build(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "site", repo / "site")
    (repo / "web" / "public").mkdir(parents=True)
    shutil.copy(ROOT / "web" / "public" / "changelog.json", repo / "web" / "public" / "changelog.json")
    (repo / "docs" / "devices").mkdir(parents=True)
    (repo / "docs" / "devices" / "README.md").write_text("No heading here.\n")
    result = build_pages(repo, tmp_path / "out")
    assert result.returncode == 1
    assert 'docs/devices/README.md: the page has no heading "# …" for its title' in result.stderr
