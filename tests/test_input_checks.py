"""#223: unexpected input gets a German message instead of an error 500 and is stored in one form; the diagnostics
report contains durations, not the times of the owner's actions."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from openampere import billing
from openampere.api import create_app
from openampere.diagnostics import _Pseudonyms
from openampere.runtime import Runtime
from openampere.storage import Storage
from openampere.tariffs import validate as validate_tariffs

TARIFF = {"valid_from": "2024-01-01", "kind": "fixed", "price_ct": 30, "feed_in_ct": 8, "vat_percent": 19}


@pytest.fixture
def client(tmp_path, authed):
    return authed(TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db")))))


@pytest.mark.parametrize("vat", ["nan", "inf", -1, 101])
def test_vat_outside_0_to_100_is_refused_and_the_tariffs_keep_working(client, vat):
    answer = client.put("/api/tariffs", json={"tariffs": [{**TARIFF, "vat_percent": vat}]})
    assert answer.status_code == 400 and "Mehrwertsteuer" in answer.json()["detail"]
    assert client.get("/api/tariffs").status_code == 200


def test_dates_are_stored_in_one_form():
    assert validate_tariffs([{**TARIFF, "valid_from": "20240101"}])[0].valid_from == "2024-01-01"
    payments = billing.validate({"import": {"payments": [{"from": "2024-1", "eur": 80}]}})["import"]["payments"]
    assert payments == [{"from": "2024-01", "eur": 80.0}]
    with pytest.raises(ValueError, match="selben Monat"):
        billing.validate({"import": {"payments": [{"from": "2024-1", "eur": 80}, {"from": "2024-01", "eur": 90}]}})


@pytest.mark.parametrize("url", ["/api/energy/summary?period=day&date=9999-12-31", "/api/prices?date=9999-12-31",
                                 "/api/export/csv?from=2026-01-01&to=9999-12-31"])
def test_a_date_at_the_end_of_the_calendar_is_a_message_not_an_error(client, url):
    answer = client.get(url)
    assert answer.status_code == 400 and answer.json()["detail"] == "Ungültiges Datum"


def zip_with(**files) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(f"cloud-export/x/work/{name}.json", content)
    return buffer.getvalue()


@pytest.mark.parametrize("content", ["[1, 2]", json.dumps({"status": 200, "body": {"generation": {"timeline": [{"value": 1}]}}})])
def test_unexpected_content_in_a_cloud_export_is_skipped(client, content):
    answer = client.post("/api/import/cloud/file", content=zip_with(day=content))
    assert answer.status_code == 400 and "keine Verlaufsdaten" in answer.json()["detail"]


def test_a_damaged_entry_in_a_cloud_export_is_skipped(client):
    data = bytearray(zip_with(day=json.dumps({"status": 200, "body": {}}) + " " * 200))
    data[data.index(b'"status"')] ^= 0xFF  # breaks the checksum of the stored entry
    answer = client.post("/api/import/cloud/file", content=bytes(data))
    assert answer.status_code == 400


def test_a_malformed_content_length_is_refused(client):
    answer = client.post("/api/backup/restore", content=b"x", headers={"content-length": "abc"})
    assert answer.status_code == 400


def test_the_diagnostics_text_has_no_time_stamps_of_database_copies():
    text = "kept as openampere.db.before-restore-20261010-153012 and openampere.db.damaged-20261009-020000"
    cleaned = _Pseudonyms(None).text(text)
    assert "20261010" not in cleaned and "153012" not in cleaned and cleaned.count("<Zeitpunkt>") == 2
