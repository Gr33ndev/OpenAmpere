# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""scripts/ai-review-trace.sh: what one run of the AI review did, printed from the action's execution file (#435)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ai-review-trace.sh"
pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq, as on the GitHub runner")


def trace(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), str(path)], capture_output=True, text=True)


def test_prints_tool_calls_denials_and_the_last_message_but_no_tool_results(tmp_path):
    messages = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking.\n::error::from the model"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "gh pr diff 7"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "RESULT-TEXT"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "ReportFindings", "input": {"findings": []}},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/w/.ai-review-summary.md", "content": "BODY"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reported\ntwo findings."}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 4,
         "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "gh api -X PATCH x"}}]},
    ]
    file = tmp_path / "out.json"
    file.write_text(json.dumps(messages))
    done = trace(file)
    assert done.returncode == 0
    lines = done.stdout.splitlines()
    assert lines[1:-1] == [
        "tool: Bash gh pr diff 7",
        "tool: ReportFindings ",
        "tool: Write /w/.ai-review-summary.md",
        "result: success, 4 turns, error: false",
        "denied: Bash gh api -X PATCH x",
        "last message: Reported two findings.",
    ]
    # text of the model is printed with workflow commands switched off, and the same token turns them on again
    token = lines[0].removeprefix("::stop-commands::")
    assert token.startswith("trace-") and lines[-1] == f"::{token}::"
    assert "RESULT-TEXT" not in done.stdout and "BODY" not in done.stdout


@pytest.mark.parametrize("content", [None, "{broken"])
def test_never_fails(tmp_path, content):
    file = tmp_path / "out.json"
    if content is not None:
        file.write_text(content)
    assert trace(file).returncode == 0
