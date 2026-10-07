"""Messages from the server in the language of the web app (#104).

Error messages are written in German in the code. The catalogs in locales/<lang>.json hold them under English keys,
grouped by module, the same way as the web app (web/src/locales/):

    de.json: {"control": {"valueOutOfRange": "Wert muss zwischen 0 und {maximum} W liegen"}}
    en.json: {"control": {"valueOutOfRange": "Value must be between 0 and {maximum} W"}}

When the web app asks for another language with the header X-OpenAmpere-Lang, the "detail" of an error response is
looked up in de.json (the German message, values in {name} matched) and replaced by the same key in that language.
A message without a translation stays German. Other apps (Home Assistant) do not send the header and get German.

Adding a language needs no code: add locales/<code>.json with the keys of de.json. scripts/i18n_messages.py lists the
messages in the code and which ones have no key in de.json yet; tests/test_i18n.py checks the catalogs.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

HEADER = "x-openampere-lang"
REFERENCE = "de"
LOCALES = Path(__file__).parent / "locales"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def languages() -> set[str]:
    return {path.stem for path in LOCALES.glob("*.json")}


def flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            out.update(flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


@lru_cache
def catalog(lang: str) -> dict[str, str]:
    """key -> message of one language."""
    path = LOCALES / f"{lang}.json"
    return flatten(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}


@lru_cache
def _patterns() -> list[tuple[re.Pattern, str, list[str]]]:
    """The German messages as patterns, values in {name} matched, with their key."""
    patterns = []
    for key, german in catalog(REFERENCE).items():
        names = PLACEHOLDER.findall(german)
        pattern = re.escape(german)
        for name in names:
            pattern = pattern.replace(re.escape("{" + name + "}"), f"(?P<{name}>.+?)", 1)
        patterns.append((re.compile(pattern + r"\Z", re.S), key, names))
    # exact messages first, then the ones with values, the most specific (longest) first
    patterns.sort(key=lambda p: (bool(p[2]), -len(p[0].pattern)))
    return patterns


def match(text: str) -> tuple[str, dict[str, str]] | None:
    """The key of a German message and the values in its placeholders, None for an unknown text."""
    for pattern, key, names in _patterns():
        found = pattern.match(text)
        if found:
            return key, {name: found.group(name) for name in names}
    return None


def translate(text: str, lang: str | None) -> str:
    """The message in the requested language, unchanged if there is no translation. Values that are messages
    themselves are translated as well, e.g. the reason in "Laden beendet: {reason}"."""
    if not text or not lang or lang == REFERENCE or lang not in languages():
        return text
    found = match(text)
    if found is None:
        return text
    key, values = found
    translated = catalog(lang).get(key)
    if translated is None:
        return text
    for name, value in values.items():
        translated = translated.replace("{" + name + "}", translate(value, lang))
    return translated


# Results of the control log that the app already says in its own sentence, and results that only wrap a reason (#153)
LOG_SAID = {"ok", "manual", "auto"}
LOG_SAID_KEYS = {"api.logNoPowerCut", "control.logOverwritten", "control.logNotDoneTestMode", "evcc.logNotSentTestMode"}
LOG_REASON_KEYS = {"charging.logStarted", "charging.logFinished", "charging.logWouldCharge", "charging.logRemoteReleased",
                   "consumers.logNotSwitchedTestMode", "consumers.logNotSentTestMode"}


def log_note(result: str, lang: str | None) -> str | None:
    """What the app shows below a control log entry: the reason or the error, in the language of the app."""
    if not result or result in LOG_SAID:
        return None
    found = match(result)
    if found and found[0] in LOG_SAID_KEYS:
        return None
    if found and found[0] in LOG_REASON_KEYS:
        return translate(found[1]["reason"], lang)
    return translate(result, lang)
