# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""One source for which devices are supported and confirmed (#526).

docs/devices/support.json holds it; scripts/device-support.mjs writes the tables in README.md, README.de.md and
docs/devices/topics/names.md, and scripts/build-site-pages.mjs renders the EKD device page and the home page list.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "device-support.mjs"
MARKDOWN = ROOT / "scripts" / "markdown.mjs"
SUPPORT = ROOT / "docs" / "devices" / "support.json"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

CALL = """
import * as support from %s;
let input = "";
process.stdin.on("data", (chunk) => { input += chunk; }).on("end", () => {
  const { name, args } = JSON.parse(input);
  let result;
  try {
    result = { value: support[name](...args) };
  } catch (error) {
    result = { error: error.message };
  }
  process.stdout.write(JSON.stringify(result));
});
"""


def call(name: str, *args):
    """The result of the function name of device-support.mjs, or {"error": …} when it throws."""
    script = CALL % json.dumps(SCRIPT.as_uri())
    result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps({"name": name, "args": args}),
                            capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)


def data(**changes) -> dict:
    """A small valid data set: one supported device, an EKD row using it and a planned EKD row."""
    names = {c: "x" for c in ["period", "sold", "model", "interface", "openampere", "status", "source"]}
    base = {
        "devices": [{"id": "inv", "name": "Inverter", "does": "Monitoring.", "short": {"de": "Anzeige", "en": "monitoring"},
                     "confirmed": {"de": "Anzeige bestätigt", "en": "Monitoring confirmed"}, "check": True, "state": "both",
                     "next": "deviceReport"}],
        "ekd": [
            {"id": "old", "group": "storage", "names": names, "sold": "Old", "device": "Old box", "period": "2020",
             "state": "planned", "note": {"de": "Zuerst nur die Anzeige.", "en": "Monitoring first."}, "issue": 7},
            {"id": "new", "group": "storage", "names": names, "sold": "New <b>", "device": "Inverter", "period": "2024",
             "support": "inv"},
        ],
    }
    base.update(changes)
    return base


@needs_node
def test_the_file_is_valid_and_every_generated_table_is_up_to_date():
    result = subprocess.run(["node", str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@needs_node
def test_every_ekd_row_of_the_website_is_in_names_md():
    support = json.loads(SUPPORT.read_text(encoding="utf-8"))
    names = (ROOT / "docs" / "devices" / "topics" / "names.md").read_text(encoding="utf-8")
    for entry in support["ekd"]:
        assert f"| {entry['names']['period']} | {entry['names']['sold']} |" in names, entry["id"]


@needs_node
def test_a_valid_data_set_has_no_errors():
    assert call("validate", data())["value"] == []


@needs_node
@pytest.mark.parametrize(("change", "message"), [
    ({"ekd": [{**data()["ekd"][1], "support": "nope"}]}, 'there is no device "nope"'),
    ({"ekd": [{**data()["ekd"][0], "state": "maybe"}]}, ".state: one of"),
    ({"ekd": [{**data()["ekd"][0], "note": {"de": "nur Deutsch"}}]}, ".note: needs a text"),
    ({"ekd": [{**data()["ekd"][1], "state": "planned"}]}, 'comes from the device "inv"'),
    ({"ekd": [{**data()["ekd"][0], "issue": "519"}]}, ".issue: an issue number"),
    ({"ekd": [{**data()["ekd"][0], "names": {**data()["ekd"][0]["names"], "status": "a | b"}}]}, "would break the table"),
    ({"devices": [{**data()["devices"][0], "page": "docs/devices/none.md"}]}, "is not a page of docs/devices/"),
    ({"ekd": [data()["ekd"][0], data()["ekd"][0]]}, "the id is used twice"),
])
def test_an_invalid_entry_is_reported(change, message):
    errors = call("validate", data(**change))["value"]
    assert any(message in error for error in errors), errors


@needs_node
def test_the_readme_table_marks_a_confirmed_device_and_links_its_page():
    devices = [{**data()["devices"][0], "page": "docs/devices/foxess/h3.md"}]
    table = call("readmeTable", data(devices=devices), "en")["value"]
    assert "| [Inverter](docs/devices/foxess/h3.md) | Monitoring. | ✅ Monitoring confirmed |" in table
    assert call("readmeTable", data(), "de")["value"].startswith("| Gerät | Was OpenAmpere kann |")


@needs_node
def test_a_row_with_support_takes_state_and_confirmation_from_the_device():
    labels = {"geraete.state.both": "BOTH", "geraete.state.planned": "PLANNED", "geraete.groups.storage": "STORAGE",
              "geraete.next.issue": "Issue", "geraete.next.deviceReport": "REPORT"}
    script = (CALL % json.dumps(SCRIPT.as_uri())).replace(
        "support[name](...args)", f"support.siteMatrix(args[0], args[1], (key) => ({json.dumps(labels)})[key])")
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            input=json.dumps({"name": "siteMatrix", "args": [data(), "en"]}),
                            capture_output=True, text=True, timeout=60, check=True)
    html = json.loads(result.stdout)["value"]
    rows = html.split("\n")
    assert rows[0] == '<tr class="group"><th colspan="5" scope="rowgroup">STORAGE</th></tr>'
    # supported first, its texts escaped
    assert "New &lt;b&gt;" in rows[1] and '<span class="state ok">BOTH</span>' in rows[1]
    assert "<small>Monitoring confirmed.</small>" in rows[1] and "<td>REPORT</td>" in rows[1]
    assert '<span class="state plan">PLANNED</span><small>Monitoring first.</small>' in rows[2]
    assert '<a href="https://github.com/Gr33ndev/OpenAmpere/issues/7">Issue #7</a>' in rows[2]


@needs_node
def test_the_home_page_list_in_german():
    assert call("siteList", data(), "de")["value"] == "<li><strong>Inverter:</strong> Anzeige. Anzeige bestätigt.</li>"


@needs_node
def test_a_generated_part_is_replaced_between_its_markers_only():
    begin = "<!-- generated:x from docs/devices/support.json by scripts/device-support.mjs; edit it there -->"
    content = f"before\n\n{begin}\n\nold\n\n<!-- /generated:x -->\n\nafter\n"
    assert call("section", content, "x", "new")["value"] == content.replace("old", "new")
    assert "markers" in call("section", "no markers here", "x", "new")["error"]


@needs_node
def test_the_markers_do_not_show_on_the_website():
    script = f"import {{ renderMarkdown }} from {json.dumps(MARKDOWN.as_uri())};" \
             "process.stdout.write(renderMarkdown('a\\n\\n<!-- generated:x -->\\n\\nb\\n').html);"
    html = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60,
                          check=True).stdout
    assert "generated" not in html and "&lt;!--" not in html
    assert html == "<p>a</p>\n<p>b</p>"
