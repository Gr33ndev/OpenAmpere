import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from openampere import tariffs as tariffs_module
from openampere.storage import Storage
from openampere.tariffs import Tariffs, validate

TZ = ZoneInfo("Europe/Berlin")


def row(ts, load=1000.0, grid_import=200.0, grid_export=500.0):
    return {"ts": ts, "pv": 1500.0, "load": load, "grid_import": grid_import, "grid_export": grid_export,
            "battery_charge": 0.0, "battery_discharge": 0.0, "soc": 50.0}


def test_money_uses_the_tariff_valid_on_each_day(tmp_path):
    storage = Storage(tmp_path / "t.db")
    day1 = datetime(2026, 3, 31, 12, 0, tzinfo=TZ).timestamp()
    day2 = datetime(2026, 4, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.import_energy([row(int(day1)), row(int(day2))], "local")
    t = Tariffs(storage, lambda: (35.0, 8.0))
    t.save([{"valid_from": "2025-01-01", "price_ct": 30, "feed_in_ct": 8},
            {"valid_from": "2026-04-01", "price_ct": 40, "feed_in_ct": 7}])
    money = t.money(day1 - 3600, day2 + 3600, TZ)
    # day 1: 800 Wh self-used * 30 ct + 500 Wh * 8 ct; day 2: 800 * 40 ct + 500 * 7 ct
    assert money["savings_eur"] == round(0.8 * 0.30 + 0.5 * 0.08 + 0.8 * 0.40 + 0.5 * 0.07, 2)
    assert money["grid_cost_eur"] == round(0.2 * 0.30 + 0.2 * 0.40, 2)
    assert not money["incomplete"]


def test_dynamic_tariff_uses_exchange_prices(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "t.db")
    ts = int(datetime(2026, 6, 1, 13, 0, tzinfo=TZ).timestamp())
    storage.import_energy([row(ts)], "local")
    t = Tariffs(storage, lambda: (35.0, 8.0))
    t.save([{"valid_from": "2026-01-01", "kind": "dynamic", "surcharge_ct": 20, "vat_percent": 19, "feed_in_ct": 8}])

    payload = {"data": [{"start_timestamp": ts * 1000, "end_timestamp": (ts + 3600) * 1000, "marketprice": 100.0}]}
    monkeypatch.setattr(tariffs_module.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    import asyncio
    assert asyncio.run(t.refresh_prices(now=ts)) == 4  # one hour -> four quarter hours
    assert asyncio.run(t.refresh_prices(now=ts + 60)) == 0  # not again within the hour
    # 100 €/MWh = 10 ct net -> 11.9 ct gross + 20 ct surcharge = 31.9 ct
    money = t.money(ts - 60, ts + 3600, TZ)
    assert money["grid_cost_eur"] == round(0.2 * 0.319, 2)
    # charging a car for that hour: 10 kWh, half of it solar (valued at the lost feed-in pay)
    assert t.charge_cost(ts, ts + 3600, 10, 0.5, TZ) == (round(10 * (0.5 * 8 + 0.5 * 31.9) / 100, 2), round(10 * 31.9 / 100, 2))
    # no exchange price known for that time: the surcharge alone, like in money()
    assert t.charge_cost(ts + 86400 * 30, ts + 86400 * 30 + 3600, 10, 0, TZ) == (2.0, 2.0)


def test_tariff_validation():
    with pytest.raises(ValueError):
        validate([])
    with pytest.raises(ValueError):
        validate([{"valid_from": "2026-13-01", "price_ct": 30}])
    with pytest.raises(ValueError):
        validate([{"valid_from": "2026-01-01"}, {"valid_from": "2026-01-01"}])
    assert [t.valid_from for t in validate([{"valid_from": "2026-05-01"}, {"valid_from": "2025-01-01"}])] == \
        ["2025-01-01", "2026-05-01"]
