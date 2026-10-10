// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import type { Status } from "./api";
import { putJson, useResource } from "./api";
import { num, timeZone } from "./format";
import { LOCALE, t } from "./i18n";
import { Button, Field, Notice, toast } from "./ui";

type Extreme = { value: number; ts: number } | null;
export type BatteryHealth = {
  capacity_kwh: number | null; charged_kwh: number | null; discharged_kwh: number | null; cycles: number | null;
  efficiency_pct: number | null; soh_pct: number | null; cell_max_now_c: number | null; cell_min_now_c: number | null;
  spread_now_c: number | null; days: number; warning: string | null;
  extremes: { cell_max: Extreme; cell_min: Extreme; spread: Extreme; inverter: Extreme; battery: Extreme };
};

const when = (ts: number) => new Date(ts * 1000).toLocaleString(LOCALE, { day: "2-digit", month: "2-digit", hour: "2-digit",
  minute: "2-digit", timeZone: timeZone() });
const deg = (v: number | null | undefined) => (v == null ? "–" : `${num(v, 1)} °C`);

function CapacityForm({ current, onSaved }: { current: number | null; onSaved: () => void }) {
  const [value, setValue] = useState(current ? current.toLocaleString(LOCALE, { useGrouping: false, maximumFractionDigits: 3 }) : "");
  const [busy, setBusy] = useState(false);
  const kwh = Number(value.replace(",", "."));
  const save = async () => {
    setBusy(true);
    try {
      await putJson("/api/settings", { "battery.capacity_kwh": kwh });
      toast(t("common.saved"));
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="form capacity-form">
      <Field label={t("report.capacityForm.usableCapacity")} hint={t("report.capacityForm.hint")}>
        <div className="input-unit"><input className="input" inputMode="decimal" value={value} placeholder={t("report.capacityForm.placeholder")}
          onChange={(e) => setValue(e.target.value)} /><span>kWh</span></div>
      </Field>
      <Button variant="secondary" busy={busy} disabled={!(kwh > 0 && kwh <= 200) || kwh === current} onClick={() => void save()}>{t("common.save")}</Button>
    </div>
  );
}

/** Full cycles, efficiency and cell temperatures of the home battery, for "Meine Anlage". */
export function BatteryHealthCard() {
  const { data, reload } = useResource<BatteryHealth>("/api/battery/health", 60_000);
  const [edit, setEdit] = useState(false);
  if (!data) return null;
  const x = data.extremes;
  return (
    <div className="card">
      <strong>{t("report.batteryHealthCard.title")}</strong>
      {data.warning && <Notice kind="warn">{data.warning}</Notice>}
      <dl className="facts">
        <dt>{t("report.batteryHealthCard.fullCycles")}</dt>
        <dd>{data.cycles != null ? t("report.batteryHealthCard.about", { count: num(data.cycles, 0) }) : "–"}</dd>
        <dt>{t("report.batteryHealthCard.efficiency")}</dt><dd>{data.efficiency_pct != null ? `${num(data.efficiency_pct, 0)} %` : "–"}</dd>
        {data.cell_max_now_c != null && <><dt>{t("report.batteryHealthCard.cellsNow")}</dt>
          <dd>{t("report.batteryHealthCard.range", { min: num(data.cell_min_now_c, 1), max: deg(data.cell_max_now_c) })}</dd></>}
        {x.cell_max && <><dt>{data.days <= 1 ? t("report.batteryHealthCard.warmestCellSoFar") : t("report.batteryHealthCard.warmestCellDays", { days: data.days })}</dt><dd>{deg(x.cell_max.value)} · {when(x.cell_max.ts)}</dd></>}
        {x.spread && <><dt>{t("report.batteryHealthCard.largestDifference")}</dt><dd>{deg(x.spread.value)} · {when(x.spread.ts)}</dd></>}
        {x.inverter && <><dt>{t("report.batteryHealthCard.inverterMax")}</dt><dd>{deg(x.inverter.value)} · {when(x.inverter.ts)}</dd></>}
      </dl>
      {(edit || !data.capacity_kwh) ? (
        <CapacityForm current={data.capacity_kwh} onSaved={() => { setEdit(false); reload(); }} />
      ) : (
        <button type="button" className="link" onClick={() => setEdit(true)}>{t("report.batteryHealthCard.changeCapacity", { capacity: num(data.capacity_kwh, 2) })}</button>
      )}
      <p className="hint">{t("report.batteryHealthCard.explanation")}</p>
    </div>
  );
}

/** When the inverter firmware changed (an update can change registers and values). */
export function FirmwareFacts({ firmware }: { firmware: Status["firmware"] }) {
  const history = firmware?.history ?? [];
  if (!firmware?.since && !history.length) return null;
  return (
    <>
      {firmware?.since && history.length > 0 && <p className="hint">{t("report.firmwareFacts.since", { date: new Date(firmware.since * 1000)
        .toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric", timeZone: timeZone() }) })}</p>}
      {history.length > 0 && (
        <details className="advanced">
          <summary>{t("report.firmwareFacts.changes", { count: history.length })}</summary>
          <ul className="sessions">
            {[...history].reverse().map((h) => (
              <li key={h.ts}><span>{new Date(h.ts * 1000).toLocaleDateString(LOCALE, { timeZone: timeZone() })}</span>
                <span>{h.old} → {h.new}</span></li>
            ))}
          </ul>
          <p className="hint">{t("report.firmwareFacts.hint")}</p>
        </details>
      )}
    </>
  );
}
