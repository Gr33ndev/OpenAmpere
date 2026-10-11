# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Values that are no numbers (text, null, NaN, Infinity) are refused with a sentence, not a server error."""

import pytest

from openampere.cloud_import import soc_values, work_rows
from openampere.consumers import SurplusControl
from openampere.control import BatteryControl
from openampere.evcc import _whole
from openampere.runtime import Runtime
from openampere.storage import Storage


def test_cloud_export_values_that_are_no_energy_are_left_out():
    timeline = [{"fromTimestamp": f"2026-06-01T{i:02d}:00:00Z", "value": v}
                for i, v in enumerate([1.5, "NaN", "Infinity", -2.0, None, "abc", 0.0])]
    rows = work_rows({"generation": {"timeline": timeline}})
    assert sorted(r["pv"] for r in rows) == [0.0, 1.5]
    socs = soc_values({"timeline": [{"fromTimestamp": "2026-06-01T00:00:00Z", "value": v} for v in (50, 150, "NaN", -1)]})
    assert [s for _, s in socs] == [50.0]


@pytest.mark.parametrize("value", [float("inf"), "abc", None, [1]])
def test_a_whole_number_or_a_sentence(value):
    with pytest.raises(ValueError, match="ganze Zahl"):
        _whole(value)


async def test_battery_limits_that_are_no_numbers_are_refused_before_anything_is_read(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled = True
    with pytest.raises(ValueError, match="ganze Prozentwerte"):
        await BatteryControl(runtime).write({"min_soc": float("inf")})
    runtime.storage.close()


@pytest.mark.parametrize("hours", [float("inf"), float("nan"), "abc", 0, 49])
def test_an_override_needs_a_duration_up_to_48_hours(tmp_path, hours):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    control = SurplusControl(runtime)
    control.save([{"name": "Pumpe", "kind": "shelly1", "host": "pump.local"}])
    with pytest.raises(ValueError, match="48 Stunden"):
        control.set_override(control.consumers[0].id, "boost", hours)
    runtime.storage.close()
