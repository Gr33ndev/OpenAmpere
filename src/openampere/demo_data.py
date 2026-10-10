# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Fill a database with simulated history (for development, screenshots and demos).

    python -m openampere.demo_data --db data/demo.db --days 60
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta

from .simulator import EnergyModel
from .storage import FLOWS, QUARTER, Storage

STEP_S = 60


def generate(storage: Storage, days: int, seed: int = 1) -> int:
    random.seed(seed)
    model = EnergyModel()
    now = datetime.now().replace(second=0, microsecond=0)
    t = (now - timedelta(days=days)).replace(hour=0, minute=0)
    quarter_start = None
    quarter_totals = None
    rows = []
    samples = []
    pv_rows: dict[tuple[int, int], float] = {}
    while t < now:
        # seasonal + daily weather variation
        if t.hour == 0 and t.minute == 0:
            model.cloud = random.uniform(0.25, 1.0)
        model.step(t, STEP_S)
        ts = t.timestamp()
        quarter = int(ts // QUARTER * QUARTER)
        for i, power in enumerate(model.pv_inputs_w):
            pv_rows[(quarter, i + 1)] = pv_rows.get((quarter, i + 1), 0.0) + power * STEP_S / 3600
        if t.minute % 5 == 0:
            inputs = (model.pv_inputs_w + [None] * 4)[:4]
            samples.append((ts, model.pv_w, model.house_w, model.grid_w, model.battery_w, round(model.soc),
                            *inputs, 28 + model.pv_w / 400, 22 + abs(model.battery_w) / 1500))
        if quarter != quarter_start:
            if quarter_start is not None:
                row = {f: model.totals[f] - quarter_totals[f] for f in FLOWS}
                rows.append({"ts": quarter_start, **row, "soc": round(model.soc)})
            quarter_start, quarter_totals = quarter, dict(model.totals)
        t += timedelta(seconds=STEP_S)

    with storage._db:
        storage._db.executemany(
            "INSERT OR REPLACE INTO samples(ts, pv, house, grid, battery, soc, pv1, pv2, pv3, pv4, t_inverter, t_battery) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", samples)
        storage._db.executemany("INSERT OR REPLACE INTO pv_input_15m VALUES (?,?,?)",
                                [(ts, i, wh) for (ts, i), wh in pv_rows.items()])
    storage._db.execute("DELETE FROM energy_15m WHERE source='demo'")
    return storage.import_energy(rows, "demo")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="data/demo.db")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    storage = Storage(args.db)
    count = generate(storage, args.days)
    print(f"{count} quarter-hour rows written to {args.db}")


if __name__ == "__main__":
    main()
