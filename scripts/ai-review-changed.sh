#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
#
# What the AI review changed in the checkout besides its summary, one `git status --porcelain` line each; empty when
# nothing. Run by .github/workflows/ai-review.yml from a copy outside the checkout, with fsmonitor off, so nothing the
# review wrote runs here. Not counted: the empty files the sandbox of Claude Code puts over paths it protects
# (.env, package.json, .npmrc, ...; git lists them as ignored) and the folder .claude-pr/ of the action.
git -c core.fsmonitor=false status --porcelain --ignored --untracked-files=all | while IFS= read -r line; do
  path=${line#???}
  case "$line" in
    "?? .ai-review-summary.md" | "?? .claude-pr/"* | "!! .claude-pr/"*) continue ;;
    "!! "*) [ -f "$path" ] && [ ! -L "$path" ] && [ ! -s "$path" ] && continue ;;
  esac
  printf '%s\n' "$line"
done
