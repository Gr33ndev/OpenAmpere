#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Commit changed files to the current branch through the GitHub API (used by CI).

Commits created this way are signed by GitHub and show as "Verified", unlike a plain `git push` from a
runner. Needs the `gh` CLI with GH_TOKEN, and GITHUB_REPOSITORY / GITHUB_REF_NAME from Actions.

Usage: scripts/commit_via_api.py [--branch NAME] "commit message" FILE [FILE ...]
Prints the new commit id, or nothing when the files are unchanged. Without --branch the current branch
(GITHUB_REF_NAME) is used; the branch must already exist and point at the checked-out commit.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile

MUTATION = """
mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { oid } }
}
"""


def changed(paths: list[str]) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--", *paths], check=True, capture_output=True, text=True)
    return [line[3:] for line in out.stdout.splitlines() if line.strip()]


def main() -> None:
    args = sys.argv[1:]
    branch = os.environ["GITHUB_REF_NAME"]
    if args[:1] == ["--branch"]:
        branch, args = args[1], args[2:]
    message, paths = args[0], args[1:]
    files = changed(paths)
    if not files:
        return
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    additions = [{"path": f, "contents": base64.b64encode(open(f, "rb").read()).decode()} for f in files]
    payload = {"query": MUTATION, "variables": {"input": {
        "branch": {"repositoryNameWithOwner": os.environ["GITHUB_REPOSITORY"], "branchName": branch},
        "message": {"headline": message},
        "expectedHeadOid": head,  # refuses to commit if the branch moved meanwhile
        "fileChanges": {"additions": additions},
    }}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
    result = subprocess.run(["gh", "api", "graphql", "--input", f.name], check=True, capture_output=True, text=True)
    print(json.loads(result.stdout)["data"]["createCommitOnBranch"]["commit"]["oid"])


if __name__ == "__main__":
    main()
