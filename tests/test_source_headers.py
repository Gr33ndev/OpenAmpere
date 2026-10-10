# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Every source file names its copyright holder and license near the top (CONTRIBUTING.md#code-rules)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ["*.py", "*.sh", "*.ts", "*.tsx", "*.mjs", "*.css", "*.html", "Dockerfile"]
COPYRIGHT = "SPDX-FileCopyrightText: Copyright the OpenAmpere contributors"
LICENSE = "SPDX-License-Identifier: MIT"


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_every_source_file_has_copyright_and_license():
    files = subprocess.run(["git", "ls-files", *PATTERNS], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split()
    assert files
    missing = []
    for name in files:
        head = "".join((ROOT / name).read_text(encoding="utf-8").splitlines(keepends=True)[:5])
        if COPYRIGHT not in head or LICENSE not in head:
            missing.append(name)
    assert not missing, f"add the two SPDX lines from CONTRIBUTING.md#code-rules to: {', '.join(missing)}"
