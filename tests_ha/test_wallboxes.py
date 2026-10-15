# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Wallboxes OpenAmpere reads and controls itself (#388): their values, and the charging mode with the highest current
of that mode (#409) through OpenAmpere."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from conftest import INSTALLATION, WALLBOX, info, state
from test_entities import entity_id, setup


def away(data: dict) -> dict:
    """The wallbox does not answer (unplugged): OpenAmpere sends no old values."""
    wallbox = {**data["wallboxes"][0], "online": False, "state": None, "connected": None, "charging": None,
               "power_w": None, "current_a": None}
    return {**data, "wallboxes": [wallbox]}


async def test_wallbox_values_on_its_own_device(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    power = hass.states.get(entity_id(hass, "sensor", f"wallbox_{WALLBOX}_wallbox_power"))
    assert power.state == "4140.0" and power.attributes["unit_of_measurement"] == "W"
    assert hass.states.get(entity_id(hass, "sensor", "_wallbox_state")).state == "charging"
    assert hass.states.get(entity_id(hass, "sensor", "_wallbox_current")).state == "6"
    assert hass.states.get(entity_id(hass, "binary_sensor", "_wallbox_plugged_in")).state == "on"
    assert hass.states.get(entity_id(hass, "binary_sensor", "_wallbox_charging")).state == "on"
    assert hass.states.get(entity_id(hass, "binary_sensor", "_wallbox_online")).state == "on"
    assert hass.states.get(entity_id(hass, "select", "_wallbox_mode")).state == "pv"
    device = next(d for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
                  if ("openampere", f"{INSTALLATION}_wallbox_{WALLBOX}") in d.identifiers)
    assert device.name == "Carport" and device.model == "dé Mobile Wallbox 11 kW (Tuya)"


async def test_charging_mode_goes_through_openampere(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    mode = entity_id(hass, "select", "_wallbox_mode")
    assert hass.states.get(mode).attributes["options"] == ["manual", "off", "pv", "minpv", "now"]
    await hass.services.async_call("select", "select_option", {"entity_id": mode, "option": "now"}, blocking=True)
    client.set_wallbox_mode.assert_called_once_with(WALLBOX, "now")


async def test_highest_current_of_the_mode_goes_through_openampere(hass: HomeAssistant, entry, client) -> None:
    """#409: the current of the mode shown, set together with that mode; OpenAmpere checks it against its limit."""
    await setup(hass, entry)
    current = entity_id(hass, "number", "_wallbox_mode_current")
    shown = hass.states.get(current)
    assert shown.state == "10" and shown.attributes["unit_of_measurement"] == "A"
    assert shown.attributes["min"] == 6 and shown.attributes["max"] == 16
    await hass.services.async_call("number", "set_value", {"entity_id": current, "value": 13}, blocking=True)
    client.set_wallbox_mode.assert_called_once_with(WALLBOX, "pv", 13)


async def test_highest_current_follows_the_mode_and_is_unavailable_without_charging(hass: HomeAssistant, entry,
                                                                                    client) -> None:
    await setup(hass, entry)
    coordinator, current = entry.runtime_data, entity_id(hass, "number", "_wallbox_mode_current")
    for mode, expected in (("now", "16"), ("off", "unavailable"), ("manual", "unavailable")):
        wallboxes = [{**coordinator.data["wallboxes"][0], "mode": mode}]
        coordinator._on_push({"type": "live", "live": coordinator.data["live"], "status": coordinator.data["status"],
                              "devices": coordinator.data["devices"], "wallboxes": wallboxes})
        await hass.async_block_till_done()
        assert hass.states.get(current).state == expected, mode


async def test_highest_current_unavailable_with_an_older_openampere(hass: HomeAssistant, entry, client) -> None:
    older = state()
    wallbox = older["wallboxes"][0]
    older["wallboxes"] = [{k: v for k, v in wallbox.items() if k not in ("max_current_a", "mode_current_a")}]
    client.state.return_value = older
    await setup(hass, entry)
    assert hass.states.get(entity_id(hass, "number", "_wallbox_mode_current")).state == "unavailable"
    assert hass.states.get(entity_id(hass, "select", "_wallbox_mode")).state == "pv"


async def test_charging_mode_unavailable_while_control_is_off(hass: HomeAssistant, entry, client) -> None:
    client.state.return_value = state(control_enabled=False)
    await setup(hass, entry)
    assert hass.states.get(entity_id(hass, "select", "_wallbox_mode")).state == "unavailable"
    assert hass.states.get(entity_id(hass, "number", "_wallbox_mode_current")).state == "unavailable"
    assert hass.states.get(entity_id(hass, "sensor", "_wallbox_power")).state == "4140.0"  # reading goes on


async def test_read_only_access_has_no_charging_mode(hass: HomeAssistant, entry, client) -> None:
    client.info.return_value = info("read")
    await setup(hass, entry)
    assert entity_id(hass, "select", "_wallbox_mode") is None
    assert entity_id(hass, "number", "_wallbox_mode_current") is None
    assert entity_id(hass, "sensor", "_wallbox_power") is not None


async def test_an_unplugged_wallbox_has_no_old_values(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    coordinator = entry.runtime_data
    coordinator._on_push({"type": "live", "live": coordinator.data["live"], "status": coordinator.data["status"],
                          "devices": coordinator.data["devices"], "wallboxes": away(coordinator.data)["wallboxes"]})
    await hass.async_block_till_done()
    for platform, key in (("sensor", "_wallbox_power"), ("sensor", "_wallbox_state"),
                          ("binary_sensor", "_wallbox_plugged_in"), ("binary_sensor", "_wallbox_charging")):
        assert hass.states.get(entity_id(hass, platform, key)).state == "unavailable", key
    assert hass.states.get(entity_id(hass, "binary_sensor", "_wallbox_online")).state == "off"
    assert hass.states.get(entity_id(hass, "select", "_wallbox_mode")).state == "pv"  # can still be chosen


async def test_a_wallbox_added_or_removed_in_openampere_sets_up_again(hass: HomeAssistant, entry, client) -> None:
    await setup(hass, entry)
    first = entry.runtime_data
    added = {"id": "9f8e7d6c", "name": "Garage", "model": "dé Mobile Wallbox 11 kW (Tuya)"}
    client.info.return_value = {**info(), "wallboxes": [*info()["wallboxes"], added]}
    pushed = [*first.data["wallboxes"], {**first.data["wallboxes"][0], "id": added["id"], "name": "Garage"}]
    client.state.return_value = {**state(), "wallboxes": pushed}
    first._on_push({"type": "live", "live": first.data["live"], "status": first.data["status"],
                    "devices": first.data["devices"], "wallboxes": pushed})
    await hass.async_block_till_done()
    assert entry.runtime_data is not first  # set up again, once
    assert entity_id(hass, "select", "9f8e7d6c_wallbox_mode") is not None
    second = entry.runtime_data
    second._on_push({"type": "live", "live": second.data["live"], "status": second.data["status"],
                     "devices": second.data["devices"], "wallboxes": pushed})
    await hass.async_block_till_done()
    assert entry.runtime_data is second


async def test_an_older_openampere_without_wallboxes(hass: HomeAssistant, entry, client) -> None:
    older_info, older_state = info(), state()
    del older_info["wallboxes"], older_state["wallboxes"]
    client.info.return_value, client.state.return_value = older_info, older_state
    await setup(hass, entry)
    assert entity_id(hass, "sensor", "_wallbox_power") is None and entity_id(hass, "select", "_wallbox_mode") is None
    assert hass.states.get(entity_id(hass, "sensor", "_pv_power")).state == "5200.0"
