#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
#
# What the AI review changed in the checkout besides its summary, one `git status --porcelain` line each; empty when
# nothing. Run by .github/workflows/ai-review.yml from a copy outside the checkout, in two steps:
#   ai-review-changed.sh --save FILE   before the review: keeps .git/config and .git/info/ in FILE
#   ai-review-changed.sh FILE          after it: fails if they differ, then lists the changes
# git status can run commands that .git/config names (fsmonitor, clean filters for paths .gitattributes maps to
# them), so it runs only after the comparison shows that the review did not change them, and with fsmonitor off.
# Not counted: the empty files the sandbox of Claude Code puts over paths it protects (.env, package.json, .npmrc,
# ...; git lists them as ignored) and the folder .claude-pr/ of the action (the workflow removes it before the
# retry). Fails when git cannot tell, e.g. with a broken .git/config.
set -e
git_state() {
  for file in .git/config $(find .git/info -type f 2>/dev/null | LC_ALL=C sort); do
    printf '== %s\n' "$file"
    cat "$file"
  done
}
if [ "$1" = "--save" ]; then
  git_state > "$2"
  exit 0
fi
if [ -z "$1" ] || ! git_state | cmp -s - "$1"; then
  echo "the git configuration of the checkout changed during the review" >&2
  exit 1
fi
status=$(mktemp)
trap 'rm -f "$status"' EXIT
if ! git -c core.fsmonitor=false status --porcelain --ignored --untracked-files=all > "$status"; then
  echo "git status failed: the checkout cannot be checked" >&2
  exit 1
fi
while IFS= read -r line; do
  path=${line#???}
  case "$line" in
    "?? .ai-review-summary.md" | "?? .claude-pr/"* | "!! .claude-pr/"*) continue ;;
    "!! "*) [ -f "$path" ] && [ ! -L "$path" ] && [ ! -s "$path" ] && continue ;;
  esac
  printf '%s\n' "$line"
done < "$status"
