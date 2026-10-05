"""Entities: values from OpenAmpere, push updates, commands, read-only access, diagnostics."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.openampere.api import InvalidAuth, OpenAmpereError
from custom_components.openampere.diagnostics import async_get_config_entry_diagnostics

from conftest import info


async def setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entity_id(hass: HomeAssistant, platform: str, key: str) -> str | None:
    registry = er.async_get(hass)
    return next((e.entity_id for e in registry.entities.values() if e.domain == platform and e.unique_id.endswith(key)), None)


async def test_sensors(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id(hass, "sensor", "_pv_power")).state == "5200.0"
    grid = hass.states.get(entity_id(hass, "sensor", "_grid_power"))
    assert grid.state == "-3400.0" and grid.attributes["unit_of_measurement"] == "W"
    # lifetime counters for the energy dashboard, shown in kWh
    energy = hass.states.get(entity_id(hass, "sensor", "_grid_export_energy"))
    assert energy.attributes["unit_of_measurement"] == "kWh" and energy.attributes["state_class"] == "total_increasing"
    assert float(energy.state) == pytest.approx(456.789)
    assert hass.states.get(entity_id(hass, "sensor", "_pv_input_1_power")).state == "2200.0"
    assert hass.states.get(entity_id(hass, "sensor", "_price")).state == "31.5"
    assert hass.states.get(entity_id(hass, "sensor", "_control")).state == "active"
    assert hass.states.get(entity_id(hass, "sensor", "rod1_device_temperature")).state == "52.5"
    assert hass.states.get(entity_id(hass, "binary_sensor", "_off_grid")).state == "off"
    assert hass.states.get(entity_id(hass, "binary_sensor", "rod1_device_on")).state == "on"
    client.listen.assert_called_once()


async def test_push_updates_live_values(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    coordinator = entry.runtime_data
    live = {**coordinator.data["live"], "pv_power": 6100.0, "off_grid": True}
    coordinator._on_push({"type": "live", "live": live, "status": {**coordinator.data["status"], "off_grid": True},
                          "devices": coordinator.data["devices"]})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, "sensor", "_pv_power")).state == "6100.0"
    assert hass.states.get(entity_id(hass, "binary_sensor", "_off_grid")).state == "on"
    assert coordinator.data["battery_settings"]["min_soc_on_grid"] == 24  # kept from the last full update


async def test_commands(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    work_mode = entity_id(hass, "select", "_work_mode")
    assert hass.states.get(work_mode).state == "self_use"
    await hass.services.async_call("select", "select_option", {"entity_id": work_mode, "option": "backup"}, blocking=True)
    client.set_battery.assert_called_with({"work_mode": "backup"})

    reserve = entity_id(hass, "number", "_min_soc_on_grid")
    state = hass.states.get(reserve)
    assert state.state == "24" and state.attributes["min"] == 10 and state.attributes["max"] == 99
    await hass.services.async_call("number", "set_value", {"entity_id": reserve, "value": 30}, blocking=True)
    client.set_battery.assert_called_with({"min_soc_on_grid": 30})
    assert hass.states.get(entity_id(hass, "number", "_min_soc")).attributes["min"] == 0  # #69

    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id(hass, "switch", "_grid_charging")},
                                   blocking=True)
    client.set_grid_charging.assert_called_with({"enabled": True})
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": entity_id(hass, "select", "rod1_device_mode"), "option": "boost"}, blocking=True)
    client.set_device_mode.assert_called_with("rod1", "boost")

    client.set_battery.side_effect = OpenAmpereError("Zu viele Änderungen in kurzer Zeit")
    with pytest.raises(HomeAssistantError, match="Zu viele Änderungen"):
        await hass.services.async_call("select", "select_option", {"entity_id": work_mode, "option": "self_use"},
                                       blocking=True)


async def test_read_only_access_has_no_controls(hass: HomeAssistant, entry, client) -> None:
    client.info.return_value = info("read")
    await setup(hass, entry)
    assert entity_id(hass, "select", "_work_mode") is None and entity_id(hass, "switch", "_grid_charging") is None
    assert entity_id(hass, "sensor", "_pv_power") is not None


async def test_controls_unavailable_while_control_is_off(hass: HomeAssistant, entry, client) -> None:
    from conftest import state

    client.state.return_value = state(control_enabled=False)
    await setup(hass, entry)
    assert hass.states.get(entity_id(hass, "select", "_work_mode")).state == "unavailable"
    assert hass.states.get(entity_id(hass, "sensor", "_control")).state == "off"


async def test_revoked_token_asks_for_a_new_connection(hass: HomeAssistant, entry, client) -> None:
    client.info.side_effect = InvalidAuth("Zugang ungültig")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_diagnostics_hide_the_secrets(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    text = str(await async_get_config_entry_diagnostics(hass, entry))
    assert "oa_secret" not in text and "192.168.178.20" not in text and "ab" * 32 not in text
