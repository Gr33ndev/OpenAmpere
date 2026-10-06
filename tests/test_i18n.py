"""Server messages in the language of the web app (#104)."""

import runpy
from pathlib import Path

from fastapi.testclient import TestClient

from openampere import i18n
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login

ROOT = Path(__file__).resolve().parent.parent
extractor = runpy.run_path(str(ROOT / "scripts" / "i18n_messages.py"))


def test_translate_exact_and_with_values():
    assert i18n.translate("Falsches Passwort.", "de") == "Falsches Passwort."
    assert i18n.translate("Falsches Passwort.", None) == "Falsches Passwort."
    assert i18n.translate("Falsches Passwort.", "en") == "Wrong password."
    assert i18n.translate("Wert muss zwischen 0 und 9000 W liegen", "en") == "Value must be between 0 and 9000 W"
    assert i18n.translate("Etwas völlig Unbekanntes.", "en") == "Etwas völlig Unbekanntes."  # stays German
    assert i18n.translate("Falsches Passwort.", "xx") == "Falsches Passwort."  # unknown language


def test_every_message_in_the_code_has_a_key_and_no_key_is_stale():
    in_code = set(extractor["messages"]())
    german = set(i18n.catalog("de").values())
    assert not in_code - german, f"messages without a key in locales/de.json: {sorted(in_code - german)}"
    assert not german - in_code, f"keys in locales/de.json whose message is not in the code: {sorted(german - in_code)}"


def test_every_language_matches_the_german_keys():
    reference = i18n.catalog("de")
    for lang in i18n.languages() - {"de"}:
        for key, text in i18n.catalog(lang).items():
            assert key in reference, f"{lang}.json: key does not exist in de.json: {key}"
            assert sorted(i18n.PLACEHOLDER.findall(text)) == sorted(i18n.PLACEHOLDER.findall(reference[key])), key


def test_api_errors_follow_the_language_of_the_web_app(tmp_path):
    client = TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db"))))
    login(client)
    client.post("/api/auth/logout")
    wrong = {"password": "falsch-falsch"}
    assert client.post("/api/auth/login", json=wrong).json()["detail"] == "Falsches Passwort."
    assert client.post("/api/auth/login", json=wrong, headers={i18n.HEADER: "en"}).json()["detail"] == "Wrong password."
    # errors of the security check are translated as well, other fields stay as they are
    response = client.get("/api/remote", headers={i18n.HEADER: "en"})
    assert response.status_code == 401 and response.json()["detail"] == i18n.translate("Bitte anmelden.", "en")
    assert response.json()["detail"] != "Bitte anmelden." and response.json()["code"] == "login_required"
