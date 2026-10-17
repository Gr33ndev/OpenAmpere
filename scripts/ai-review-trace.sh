#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Prints what one run of the AI review did (.github/workflows/ai-review.yml), from the execution file of
# claude-code-action: each tool call with its command or path, the denied ones and the last message. The action
# itself only prints the number of turns and denials, which left a run that posted nothing unexplained (#435).
# Tool results are not printed. Never fails: it only explains, the workflow decides.
file="$1"
[ -f "$file" ] || { echo "no execution file"; exit 0; }
# the output is written by the model: no workflow commands ("::error::" and the like) from it
token="trace-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
echo "::stop-commands::$token"
jq -r '
  def short: tostring | gsub("\\s+"; " ") | .[0:200];
  def what: (.command // .file_path // .path // "") | short;
  (.[] | select(.type == "assistant") | .message.content[]? | select(.type == "tool_use")
    | "tool: \(.name) \(.input | what)"),
  (.[] | select(.type == "result")
    | "result: \(.subtype), \(.num_turns) turns, error: \(.is_error)",
      (.permission_denials[]? | "denied: \(.tool_name) \(.tool_input | what)")),
  ([.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | last // ""
    | "last message: \(.[0:500] | gsub("\\s+"; " "))")
' "$file" || echo "could not read the execution file"
echo "::$token::"
exit 0
