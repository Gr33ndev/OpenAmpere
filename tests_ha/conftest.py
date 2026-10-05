"""Tests of the Home Assistant integration. The OpenAmpere server is replaced by a fake client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openampere.const import CONF_FINGERPRINT, CONF_TOKEN, DOMAIN

FINGERPRINT = "ab" * 32
INSTALLATION = "1f0e7c3a-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def info(scope: str = "control") -> dict:
    return {
        "api_version": 1, "version": "0.7.0", "installation_id": INSTALLATION,
        "token": {"name": "Home Assistant", "scope": scope},
        "device": {"manufacturer": "FoxESS", "model": "H3-10.0-Smart", "firmware": "1.40", "rated_power_w": 10000,
                   "supports_control": True},
        "pv_inputs": [{"index": 0, "name": "Süddach"}, {"index": 1, "name": "Westdach"}],
        "devices": [{"id": "rod1", "name": "Heizstab", "kind": "heating_rod"}],
        "poll_interval": 10, "web_port": 8080,
    }


def state(control_enabled: bool = True) -> dict:
    return {
        "type": "state",
        "live": {"timestamp": 1.0, "pv_power": 5200.0, "house_power": 800.0, "grid_power": -3400.0,
                 "battery_power": -1000.0, "battery_soc": 63.0, "battery_soh": 98.0, "battery_voltage": 410.2,
                 "battery_current": -2.4, "battery_temperature": 24.5, "off_grid": False, "alarms": [],
                 "temperatures": {"inverter": 41.0},
                 "pv_inputs": [{"power": 3000.0, "voltage": 400.0, "current": 7.5}, {"power": 2200.0, "voltage": 380.0, "current": 5.8}],
                 "totals": {"pv": 1234567.0, "load": 2345678.0, "grid_import": 345678.0, "grid_export": 456789.0,
                            "battery_charge": 56789.0, "battery_discharge": 45678.0}},
        "status": {"connected": True, "stale": False, "last_update": 1.0, "off_grid": False, "grid_charging": False,
                   "control": {"enabled": control_enabled, "dry_run": False}},
        "devices": [{"id": "rod1", "name": "Heizstab", "kind": "heating_rod", "enabled": True, "power_w": 1500.0,
                     "on": True, "temperature_c": 52.5, "target_c": 60.0, "status": None, "error": None, "mode": "auto"}],
        "battery_settings": {"work_mode": "self_use", "min_soc": 0, "max_soc": 100, "min_soc_on_grid": 24},
        "grid_charging": {"enabled": False, "target_soc": 80, "active": False},
        "price_ct": 31.5,
    }


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, unique_id=INSTALLATION, title="OpenAmpere",
                           data={"host": "192.168.178.20", "port": 8443, CONF_FINGERPRINT: FINGERPRINT,
                                 CONF_TOKEN: "oa_secret"})


@pytest.fixture
def client():
    """The fake OpenAmpere: every method of the client is a mock."""
    base = "custom_components.openampere.api.OpenAmpereClient"
    with patch(f"{base}.info", AsyncMock(return_value=info())) as info_mock, \
            patch(f"{base}.state", AsyncMock(return_value=state())) as state_mock, \
            patch(f"{base}.listen", AsyncMock(return_value=None)) as listen_mock, \
            patch(f"{base}.set_battery", AsyncMock(return_value={})) as battery_mock, \
            patch(f"{base}.set_grid_charging", AsyncMock(return_value={})) as charging_mock, \
            patch(f"{base}.set_device_mode", AsyncMock(return_value={})) as device_mock:
        yield type("Fake", (), {"info": info_mock, "state": state_mock, "listen": listen_mock,
                                "set_battery": battery_mock, "set_grid_charging": charging_mock,
                                "set_device_mode": device_mock})
