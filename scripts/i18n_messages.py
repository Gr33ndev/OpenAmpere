#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Lists the messages the server sends to the web app, as keys for src/openampere/locales/<lang>.json (#104).

Reads the Python code (not running it) and collects the German texts of
  - exceptions that are raised: raise SomeError("…"),
  - HTTPException(status, "…"),
  - {"detail": "…"} in JSON responses and
  - results and reasons of the control log (#153): the result of log_control(…), reasons handed to stop(…),
    _log_stop(…), switch(…), set_power(…) and _safe_off(…), {"reason": "…"}, reason = "…" and result = "…".
Strings split over several lines are joined, values in f-strings become {name} placeholders.

  scripts/i18n_messages.py              print every message
  scripts/i18n_messages.py --missing    print the messages that have no key in locales/de.json yet

A new message in the code needs a key in src/openampere/locales/de.json (grouped by module, English camelCase name,
the German text as value) and ideally the same key in the other languages; tests/test_i18n.py checks this.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "openampere"
# German, not one of the English developer messages of the drivers (those never reach the user as they are)
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(der|die|das|den|dem|bitte|nicht|kein|keine|ist|sind|wird|und|oder|nur|noch|gibt|"
                    r"falsch\w*|unbekannt\w*|fehler|zwei|mindest\w*|leistung|meldet|antwort|unerwartete|gefunden|"
                    r"datenbank|messwerte|zeitraum|neuere|einstellung|aktion|befehl|lademodus|tarife|beginnen|tag)\b",
                    re.I)


def _name(node: ast.AST, used: set[str]) -> str:
    words = re.findall(r"[A-Za-z_]\w*", ast.unparse(node)) or ["value"]
    name = words[-1] if words[-1] not in ("g", "f", "d") else words[0]
    candidate, n = name, 2
    while candidate in used:
        candidate, n = f"{name}{n}", n + 1
    used.add(candidate)
    return candidate


def template(node: ast.AST) -> str | None:
    """The text of a string or f-string node, values as {name}; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts, used = [], set()
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + _name(value.value, used) + "}")
        return "".join(parts)
    return None


# control log: function -> position of the result or reason argument
LOG_ARGS = {"log_control": 3, "stop": 0, "_log_stop": 0, "switch": 2, "set_power": 2, "_safe_off": 1}
# results that are codes, not texts: the app shows them in its own words
LOG_CODES = {"ok", "manual", "auto"}


def messages() -> dict[str, str]:
    """German message -> file:line where it appears first."""
    found: dict[str, str] = {}

    def add(node: ast.AST, path: Path, log: bool = False) -> None:
        if isinstance(node, ast.IfExp):  # "a" if … else "b"
            add(node.body, path, log)
            add(node.orelse, path, log)
            return
        text = template(node)
        # every text of the control log is German, also short ones like "im Ladefenster"
        german = text not in LOG_CODES and re.search(r"[A-Za-zÄÖÜäöü]", text) if log and text else GERMAN.search(text or "")
        if text and german and text not in found:
            found[text] = f"{path.relative_to(ROOT)}:{node.lineno}"

    for path in sorted(SRC.rglob("*.py")):
        if "web" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                add(node.exc.args[0], path)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPException" and len(node.args) > 1:
                add(node.args[1], path)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "detail" and value is not None:
                        add(value, path)
                    elif isinstance(key, ast.Constant) and key.value == "reason" and value is not None:
                        add(value, path, log=True)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in LOG_ARGS and len(node.args) > LOG_ARGS[name]:
                    add(node.args[LOG_ARGS[name]], path, log=True)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
                if isinstance(target, ast.Name) and target.id in ("reason", "result"):
                    add(value, path, log=True)
                elif isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
                    for t, v in zip(target.elts, value.elts):
                        if isinstance(t, ast.Name) and t.id == "reason":
                            add(v, path, log=True)
    return found


def main() -> None:
    found = messages()
    if sys.argv[1:] == ["--missing"]:
        def values(tree: dict) -> set[str]:
            return {v for x in tree.values() for v in (values(x) if isinstance(x, dict) else [x])}
        done = values(json.loads((SRC / "locales" / "de.json").read_text(encoding="utf-8")))
        found = {text: where for text, where in found.items() if text not in done}
    for text, where in found.items():
        print(f"{where}\t{text}")
    print(f"{len(found)} messages", file=sys.stderr)


if __name__ == "__main__":
    main()
