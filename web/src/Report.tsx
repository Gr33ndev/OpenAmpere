// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { Fragment, useEffect, useMemo, useState } from "react";
import type { DeviceSeries, EnergyEntry, Period, PowerEntry, PvInputsTimeline, Settings, Summary } from "./api";
import { PV_INPUT_COLORS, useResource } from "./api";
import { Chart, type Series } from "./Chart";
import { energyDigits, energyKwh, isoDate, kw, kwh, percent, timeZone, todayIso } from "./format";
import { LOCALE, t } from "./i18n";
import { CalendarIcon, Chevron } from "./icons";
import { Segmented } from "./ui";
import { KeyFigures } from "./KeyFigures";
import { colorsFor } from "./DevicesPage";
import { BillingSection } from "./BillingPage";
import { OutagesSection } from "./Outages";
import { VehicleStats } from "./VehicleStats";
import { EvccSessions } from "./WallboxPage";
import { periodBuckets } from "./zoned";

const PERIOD_LABEL: Record<Period, string> = { day: t("common.day"), week: t("report.periodLabel.week"), month: t("common.month"), year: t("report.periodLabel.year") };
const RESOLUTION: Record<Period, string> = { day: "60m", week: "day", month: "day", year: "month" };

function shift(date: Date, period: Period, step: number): Date {
  const d = new Date(date);
  if (period === "day") d.setDate(d.getDate() + step);
  if (period === "week") d.setDate(d.getDate() + 7 * step);
  if (period === "month") d.setMonth(d.getMonth() + step, 1);
  if (period === "year") d.setFullYear(d.getFullYear() + step, 0, 1);
  return d;
}

/** First day of the period that contains `date`: Monday for weeks (like the server), the 1st for months. */
function periodStart(date: Date, period: Period): Date {
  if (period === "week") return shift(date, "day", -((date.getDay() + 6) % 7));
  if (period === "month") return new Date(date.getFullYear(), date.getMonth(), 1);
  if (period === "year") return new Date(date.getFullYear(), 0, 1);
  return date;
}

function title(date: Date, period: Period): string {
  if (period === "day") return date.toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit", year: "numeric" });
  if (period === "week") {
    const monday = shift(date, "day", -((date.getDay() + 6) % 7));
    const sunday = shift(monday, "day", 6);
    return `${monday.toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit" })} – ${sunday.toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit", year: "numeric" })}`;
  }
  if (period === "month") return date.toLocaleDateString(LOCALE, { month: "long", year: "numeric" });
  return String(date.getFullYear());
}

const fmtHour = (ts: number) => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });
const fmtDay = (ts: number) => new Date(ts * 1000).toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit", timeZone: timeZone() });
const fmtMonth = (ts: number) => new Date(ts * 1000).toLocaleDateString(LOCALE, { month: "short", timeZone: timeZone() });

type LegendItem = { key?: string; color: string; label: string; value?: string };

/** With onToggle, an entry with a key is a button that shows or hides its line (#400). */
function Legend({ items, hidden, onToggle }: {
  items: LegendItem[]; hidden?: Set<string>; onToggle?: (key: string) => void;
}) {
  return (
    <div className="legend">
      {items.map((i) => {
        const content = <><span className="dot" style={{ background: i.color }} />{i.label}{i.value && <strong>{i.value}</strong>}</>;
        const key = i.key;
        return key && onToggle ? (
          <button key={key} type="button" aria-pressed={!hidden?.has(key)} onClick={() => onToggle(key)}>{content}</button>
        ) : <span key={i.label}>{content}</span>;
      })}
    </div>
  );
}

/** Lines hidden with a tap on the legend (#400), kept in this browser for the next visit. A line stays visible if it is
 *  the last one, and a view whose lines are all hidden (e.g. hidden in another view) shows them all. */
function useHidden(name: string, keys: string[]): [Set<string>, (key: string) => void] {
  const storageKey = `openampere.report.hidden.${name}`;
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(storageKey) ?? "[]") as string[]);
    } catch {
      return new Set();
    }
  });
  const toggle = (key: string) => {
    const next = new Set(hidden);
    if (next.has(key)) next.delete(key);
    else if (keys.some((k) => k !== key && !next.has(k))) next.add(key);
    else return;
    setHidden(next);
    try { localStorage.setItem(storageKey, JSON.stringify([...next])); } catch { /* private mode: only for this visit */ }
  };
  return [keys.length > 0 && keys.every((k) => hidden.has(k)) ? NONE_HIDDEN : hidden, toggle];
}
const NONE_HIDDEN = new Set<string>();


/** The day that is "today" right now – updates itself at midnight, also in an app left open overnight. */
function useToday(): string {
  const [today, setToday] = useState(todayIso);
  useEffect(() => {
    const timer = window.setInterval(() => setToday(todayIso()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return today;
}

function fromIso(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** Puts the entries on every bucket of the period, empty where there is no data (yet), so the axis shows the whole
 *  week, month or year with each label once (#403). Without buckets (a day) the entries stay as they are. */
function fillPeriod<T extends { ts: number }>(entries: T[], buckets: number[] | null, empty: (ts: number) => T): T[] {
  if (!buckets) return entries;
  const slots = buckets.map(empty);
  for (const e of entries) {
    let i = buckets.length - 1;
    while (i > 0 && buckets[i] > e.ts) i--;
    slots[i] = e;
  }
  return slots;
}

const emptyEnergy = (ts: number): EnergyEntry => ({
  ts, soc: null, pv: null, load: null, grid_import: null, grid_export: null, battery_charge: null, battery_discharge: null,
});
const emptyValues = (ts: number) => ({ ts, values: [] });

/** Values of the touched bar / point, so nobody has to guess from the axis. */
function Readout({ rows, index, showPower, xFormat, extra = [], hidden, externalLabel }: {
  rows: (EnergyEntry | PowerEntry)[]; index: number | null; showPower: boolean; xFormat: (ts: number) => string;
  extra?: { label: string; values: (number | null)[] }[]; hidden: Set<string>; externalLabel: string;
}) {
  if (index == null || !rows[index]) return <p className="hint readout-hint">{t("report.readout.tapHint")}</p>;
  const r = rows[index];
  const items: [string, string][] = [];
  if (showPower) {
    const p = r as PowerEntry;
    const grid = kw(Math.abs(p.grid ?? 0)), battery = kw(Math.abs(p.battery ?? 0));
    const lines: [string, [string, string]][] = [["pv", [t("common.generation"), kw(p.pv)]], ["house", [t("common.consumption"), kw(p.house)]],
      ["grid", [t("common.grid"), p.grid == null ? "–" : p.grid >= 0 ? t("report.readout.gridImport", { power: grid }) : t("report.readout.gridFeedIn", { power: grid })]],
      ["battery", [t("common.battery"), p.battery == null ? "–"
        : p.battery >= 0 ? t("report.readout.discharging", { power: battery }) : t("report.readout.charging", { power: battery })]]];
    items.push(...lines.filter(([key]) => !hidden.has(key)).map(([, item]) => item));
    if (p.pv_external != null && !hidden.has("pv_external")) items.splice(1, 0, [externalLabel, kw(p.pv_external)]);
  } else {
    const e = r as EnergyEntry;
    if (!hidden.has("pv")) items.push([t("common.generated"), energyKwh(e.pv)]);
    if (e.pv_external != null && !hidden.has("pv_external")) {
      items.push([t("report.report.externalPart", { name: externalLabel }), energyKwh(e.pv_external)]);
    }
    items.push([t("common.consumed"), energyKwh(e.load)]);
    if (!hidden.has("grid")) items.push([t("common.gridImport"), energyKwh(e.grid_import)]);
    items.push([t("report.readout.fedIn"), energyKwh(e.grid_export)], [t("common.charged"), energyKwh(e.battery_charge)], [t("report.readout.discharged"), energyKwh(e.battery_discharge)]);
  }
  for (const x of extra) {
    const v = x.values[index];
    if (v != null) items.push([x.label, kw(v * 1000)]);
  }
  if (r.soc != null && !hidden.has("soc")) items.push([t("common.stateOfCharge"), percent(r.soc)]);
  return (
    <div className="readout" aria-live="polite">
      <strong>{xFormat(r.ts)}</strong>
      {items.map(([k, v]) => <span key={k}>{k} <b>{v}</b></span>)}
    </div>
  );
}

export function Report() {
  const [period, setPeriod] = useState<Period>("day");
  const today = useToday();
  const [picked, setPicked] = useState<string | null>(null); // null = follow "today"
  const day = picked ?? today;
  const date = fromIso(day);
  const setDate = (d: Date) => setPicked(isoDate(d) === today ? null : isoDate(d));
  // only a day can be shown as a power curve; energy per quarter hour or per hour
  const [dayView, setDayView] = useState<"power" | "15m" | "60m">("power");
  const [hover, setHover] = useState<number | null>(null);
  const showPower = period === "day" && dayView === "power";
  const resolution = period === "day" ? (dayView === "15m" ? "15m" : "60m") : RESOLUTION[period];
  // the running period changes, past periods do not: only refresh what can still change
  const running = fromIso(today) < periodStart(shift(date, period, 1), period) && periodStart(date, period) <= fromIso(today);
  const refresh = running ? 60_000 : 0;

  const { data: summary } = useResource<Summary>(`/api/energy/summary?period=${period}&date=${day}`, refresh);
  const { data: settings } = useResource<Settings>("/api/settings");
  const { data: energy, error: energyError } = useResource<{ entries: EnergyEntry[] }>(
    showPower ? null : `/api/energy/timeline?period=${period}&date=${day}&resolution=${resolution}`, refresh);
  const { data: power, error: powerError } = useResource<{ entries: PowerEntry[] }>(
    showPower ? `/api/power/timeline?date=${day}&step=300` : null, refresh);
  const { data: devPower } = useResource<DeviceSeries>(showPower ? `/api/devices/power?date=${day}&step=300` : null, refresh);
  const { data: devEnergy } = useResource<DeviceSeries>(
    `/api/devices/energy?period=${period}&date=${day}&resolution=${showPower ? "60m" : resolution}`, refresh);
  const loaded = showPower ? power : energy;
  const loadError = showPower ? powerError : energyError;

  const xFormat = period === "day" ? fmtHour : period === "year" ? fmtMonth : fmtDay;
  const next = shift(date, period, 1);
  const buckets = useMemo(() => (period === "day" ? null : periodBuckets(isoDate(periodStart(fromIso(day), period)), period, timeZone())),
    [period, day]);
  const rows: (EnergyEntry | PowerEntry)[] = useMemo(
    () => (showPower ? power?.entries ?? [] : fillPeriod(energy?.entries ?? [], buckets, emptyEnergy)), [showPower, power, energy, buckets]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: the hover is reset whenever another period or view is shown
  useEffect(() => setHover(null), [period, day, dayView, resolution]);

  const deviceColors = useMemo(() => colorsFor(devPower?.devices ?? []), [devPower]);
  /** Device power on the same time axis as the main power curve (dashed lines). */
  const deviceSeries = (xs: number[]): Series[] => {
    if (!devPower?.devices.length) return [];
    const byTs = new Map(devPower.entries.map((e) => [e.ts, e.values]));
    return devPower.devices.map((d, i) => ({
      key: `device:${d.key}`, label: d.name, color: deviceColors[d.key], unit: "kW", dash: true,
      values: xs.map((ts) => { const v = byTs.get(ts)?.[i]; return v == null ? null : v / 1000; }),
    }));
  };

  const hasSoc = rows.some((r) => r.soc != null) && (showPower || period === "day");
  // a second PV inverter (#419): its part of the generation, drawn over the PV bar or as its own line
  const hasExternal = rows.some((r) => (r.pv_external ?? 0) > 0);
  const externalLabel = settings?.values["pv.external_name"] || t("overview.energyFlow.secondInverter");
  const legend: LegendItem[] = [
    { key: "pv", color: "var(--pv)", label: t("common.generated") },
    ...(hasExternal ? [{ key: "pv_external", color: "var(--pv-external)", label: t("report.report.externalPart", { name: externalLabel }) }] : []),
    { key: "house", color: "var(--house)", label: showPower ? t("common.consumption") : t("report.report.selfSupplied") },
    { key: "grid", color: "var(--grid)", label: showPower ? t("common.grid") : t("report.report.fromGrid") },
    ...(showPower ? [{ key: "battery", color: "var(--battery)", label: t("common.battery") }] : []),
    ...(hasSoc ? [{ key: "soc", color: "var(--label)", label: t("report.report.socRightAxis") }] : []),
    ...(showPower ? (devPower?.devices ?? []).map((d) => ({ key: `device:${d.key}`, color: deviceColors[d.key], label: t("report.report.dashed", { name: d.name }) })) : []),
  ];
  const [hidden, toggle] = useHidden("main", legend.map((i) => i.key as string));

  const digits = energyDigits();
  // biome-ignore lint/correctness/useExhaustiveDependencies: deviceSeries is recreated on every render, its inputs devPower and deviceColors are listed
  const chart = useMemo(() => {
    const soc: Series = { key: "soc", label: t("common.stateOfCharge"), color: "var(--battery)", values: rows.map((r) => r.soc), unit: "%", scale: "soc" };
    const visible = (series: Series[]) => series.filter((s) => !s.key || !hidden.has(s.key));
    if (showPower) {
      const p = rows as PowerEntry[];
      const kwOf = (v: number | null) => (v == null ? null : v / 1000);
      return {
        x: p.map((r) => r.ts),
        series: visible([
          { key: "pv", label: t("common.generation"), color: "var(--pv)", values: p.map((r) => kwOf(r.pv)), unit: "kW" },
          ...(hasExternal ? [{ key: "pv_external", label: externalLabel, color: "var(--pv-external)", unit: "kW",
            values: p.map((r) => kwOf(r.pv_external ?? null)) }] : []),
          { key: "house", label: t("common.consumption"), color: "var(--house)", values: p.map((r) => kwOf(r.house)), unit: "kW" },
          { key: "grid", label: t("common.grid"), color: "var(--grid)", values: p.map((r) => kwOf(r.grid)), unit: "kW" },
          { key: "battery", label: t("common.battery"), color: "var(--battery)", values: p.map((r) => kwOf(r.battery)), unit: "kW" },
          ...deviceSeries(p.map((r) => r.ts)),
          ...(hasSoc ? [{ ...soc, color: "var(--label)" }] : []),
        ] as Series[]),
      };
    }
    const en = rows as EnergyEntry[];
    const k = (v: number | null) => (v == null ? null : v / 1000);
    return {
      x: en.map((r) => r.ts),
      // consumption is drawn as total (blue = from grid) with the self-supplied part (orange) on top; without the
      // self-supplied part the blue bar is only what came from the grid
      series: visible([
        { key: "pv", label: t("common.generation"), color: "var(--pv)", values: en.map((r) => k(r.pv)), unit: "kWh", digits, barAlign: -1 },
        ...(hasExternal ? [{ key: "pv_external", label: externalLabel, color: "var(--pv-external)", unit: "kWh", digits,
          barAlign: -1, values: en.map((r) => k(r.pv_external ?? null)) }] : []),
        { key: "grid", label: t("common.gridImport"), color: "var(--grid)", unit: "kWh", digits, barAlign: 1,
          values: en.map((r) => k(hidden.has("house") ? r.grid_import : r.load)) },
        { key: "house", label: t("report.report.selfSupply"), color: "var(--house)", unit: "kWh", digits, barAlign: 1,
          values: en.map((r) => (r.load == null ? null : Math.max(0, (r.load - (r.grid_import ?? 0)) / 1000))) },
        ...(hasSoc ? [{ ...soc, color: "var(--label)" }] : []),
      ] as Series[]),
    };
  }, [showPower, rows, hasSoc, hasExternal, externalLabel, devPower, deviceColors, digits, hidden]);

  return (
    <div className="page">
      <div className="page-head"><h1>{t("common.report")}</h1></div>
      <div className="toolbar">
        <label className="cal" aria-label={t("report.report.chooseDate")}>
          <CalendarIcon />
          <input type="date" value={day} max={today} onChange={(ev) => ev.target.value && setDate(fromIso(ev.target.value))} />
        </label>
        <Segmented value={period} options={Object.entries(PERIOD_LABEL) as [Period, string][]}
          onChange={setPeriod} />
      </div>
      <div className="date-nav">
        <button type="button" onClick={() => setDate(shift(date, period, -1))} aria-label={t("report.report.previousPeriod")}><Chevron dir="left" /></button>
        <span>{title(date, period)}</span>
        {picked ? <button type="button" onClick={() => setPicked(null)} className="today-link">{t("common.today")}</button> : null}
        <button type="button" onClick={() => setDate(next)} disabled={periodStart(next, period) > fromIso(today)} aria-label={t("report.report.nextPeriod")}><Chevron /></button>
      </div>

      <KeyFigures summary={summary} />

      <div className="section-title">{t("report.report.history")}</div>
      {period === "day" && (
        <div className="row-info">
          <Segmented value={dayView} onChange={setDayView}
            options={[["power", t("report.report.power")], ["15m", t("report.report.energyQuarterHours")], ["60m", t("report.report.energyHours")]]} />
        </div>
      )}
      {!loaded ? (
        <p className="empty">{loadError ? t("report.report.loadError", { error: loadError }) : t("common.loading")}</p>
      ) : chart.x.length ? (
        <>
          <Chart x={chart.x} series={chart.series} bars={!showPower} xFormat={xFormat} height={300} onHover={setHover}
            label={showPower ? t("report.report.powerChart", { period: title(date, period) })
              : t("report.report.energyChart", { period: title(date, period) })} />
          <Readout rows={rows} index={hover} showPower={showPower} xFormat={xFormat} hidden={hidden} externalLabel={externalLabel}
            extra={chart.series.filter((x) => x.dash).map((x) => ({ label: x.label, values: x.values }))} />
        </>
      ) : (
        <p className="empty">{t("report.report.noData")}</p>
      )}
      <Legend items={legend} hidden={hidden} onToggle={toggle} />
      <p className="hint">{t("report.report.legendHint")}</p>
      {showPower && <p className="hint">{t("report.report.signHint")}</p>}
      {period === "day" && summary?.recorded_since && (
        <p className="hint">{t("report.report.recordingSince", {
          time: new Date(summary.recorded_since * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() }) })}</p>
      )}

      <DevicesSection data={showPower ? devPower : devEnergy} power={showPower} buckets={buckets} totals={devEnergy?.totals_wh}
        colors={colorsFor((showPower ? devPower : devEnergy)?.devices ?? [])} xFormat={xFormat} load={summary?.energy_wh.load ?? null} />
      <PvInputsSection period={period} day={day} showPower={showPower} resolution={resolution} xFormat={xFormat} refresh={refresh}
        buckets={buckets} />
      <BillingSection />
      <OutagesSection />
      <VehicleStats />
      <EvccSessions />
    </div>
  );
}

/** Solar yield per PV input (module array): power curve for a day, energy per bucket otherwise (stacked bars). */
function PvInputsSection({ period, day, showPower, resolution, xFormat, refresh, buckets }: {
  period: Period; day: string; showPower: boolean; resolution: string; xFormat: (ts: number) => string; refresh: number;
  buckets: number[] | null;
}) {
  const mode = showPower ? "power" : "energy";
  const { data } = useResource<PvInputsTimeline>(
    `/api/pv/inputs?period=${period}&date=${day}&mode=${mode}&resolution=${resolution}`, refresh);
  // colour of the input itself, so it stays the same when another input is hidden
  const inputColor = (i: number) => PV_INPUT_COLORS[((data?.inputs?.[i] ?? i + 1) - 1) % 4];
  const inputKey = (i: number) => String(data?.inputs?.[i] ?? i + 1);
  const [hidden, toggle] = useHidden("inputs", (data?.labels ?? []).map((_, i) => inputKey(i)));

  // biome-ignore lint/correctness/useExhaustiveDependencies: inputColor and inputKey are recreated on every render and only depend on data
  const chart = useMemo(() => {
    if (!data || data.labels.length < 2) return null;
    const entries = fillPeriod(data.entries, mode === "power" ? null : buckets, emptyValues);
    const x = entries.map((e) => e.ts);
    const shown = data.labels.map((_, i) => i).filter((i) => !hidden.has(inputKey(i)));
    const col = (i: number) => entries.map((e) => (e.values[i] == null ? null : (e.values[i] as number) / 1000));
    if (mode === "power") {
      return { x, bars: false, series: shown.map((i) => ({ label: data.labels[i], color: inputColor(i), values: col(i), unit: "kW" })) as Series[] };
    }
    // stacked: draw the running sums of the shown inputs from the top down so each shows as its own segment
    const stacked = shown.map((_, n) => entries.map((e) => shown.slice(0, n + 1).reduce((a: number, i) => a + (e.values[i] ?? 0), 0) / 1000));
    const series = shown.map((i, n) => ({ label: data.labels[i], color: inputColor(i), values: stacked[n], unit: "kWh", barAlign: 0 as const }));
    return { x, bars: true, series: series.reverse() as Series[] };
  }, [data, mode, buckets, hidden]);

  if (!data || data.labels.length < 2) return null;
  return (
    <>
      <div className="section-title">{t("report.pvInputsSection.title")}</div>
      {chart?.x.length ? (
        <Chart x={chart.x} series={chart.series} bars={chart.bars} xFormat={xFormat} height={220} />
      ) : <p className="empty">{t("report.pvInputsSection.emptyState")}</p>}
      <Legend hidden={hidden} onToggle={toggle} items={data.labels.map((label, i) => ({ key: inputKey(i), color: inputColor(i), label,
        value: data.totals_wh ? kwh(data.totals_wh[i]) : undefined }))} />
    </>
  );
}

export function TemperatureSection({ day, refresh, heading = t("common.temperatures") }: { day: string; refresh: number; heading?: string | null }) {
  const { data } = useResource<{ entries: { ts: number; inverter: number | null; battery: number | null;
    cell_max?: number | null; cell_min?: number | null }[] }>(
    `/api/temperatures/timeline?date=${day}`, refresh);
  const chart = useMemo(() => {
    const rows = data?.entries ?? [];
    return {
      x: rows.map((r) => r.ts),
      series: [
        { label: t("common.inverter"), color: "var(--coral)", values: rows.map((r) => r.inverter), unit: "°C" },
        { label: rows.some((r) => r.cell_max != null) ? t("report.temperatureSection.bms") : t("common.battery"), color: "var(--battery)",
          values: rows.map((r) => r.battery), unit: "°C" },
        ...(rows.some((r) => r.cell_max != null) ? [
          { label: t("report.temperatureSection.warmestCell"), color: "var(--pv)", values: rows.map((r) => r.cell_max ?? null), unit: "°C", dash: true },
          { label: t("report.temperatureSection.coolestCell"), color: "var(--sky)", values: rows.map((r) => r.cell_min ?? null), unit: "°C", dash: true },
        ] : []),
      ] as Series[],
    };
  }, [data]);
  if (!chart.x.length || chart.series.every((s) => s.values.every((v) => v == null))) return null;
  return (
    <>
      {heading && <div className="section-title">{heading}</div>}
      <Chart x={chart.x} series={chart.series} xFormat={fmtHour} height={180} />
      <div className="legend">
        <span><span className="dot" style={{ background: "var(--coral)" }} />{t("common.inverter")}</span>
        <span><span className="dot" style={{ background: "var(--battery)" }} />{chart.series.length > 2 ? t("report.temperatureSection.bms") : t("common.battery")}</span>
        {chart.series.length > 2 && <span><span className="dot" style={{ background: "var(--pv)" }} />{t("report.temperatureSection.warmestCell")}</span>}
        {chart.series.length > 2 && <span><span className="dot" style={{ background: "var(--sky)" }} />{t("report.temperatureSection.coolestCell")}</span>}
      </div>
    </>
  );
}

/** Energy (stacked bars) or power (lines) per device: wallbox, heating rod, ... */
function DevicesSection({ data, power, buckets, totals, colors, xFormat, load }: {
  data: DeviceSeries | null; power: boolean; buckets: number[] | null; totals?: number[]; colors: Record<string, string>;
  xFormat: (ts: number) => string; load: number | null;
}) {
  const chart = useMemo(() => {
    if (!data?.devices.length || !data.entries.some((e) => e.values.some((v) => (v ?? 0) > 0))) return null;
    if (power) {
      return { x: data.entries.map((e) => e.ts), bars: false, series: data.devices.map((d, i) => ({ label: d.name, color: colors[d.key], unit: "kW",
        values: data.entries.map((e) => (e.values[i] == null ? null : (e.values[i] as number) / 1000)) })) as Series[] };
    }
    const entries = fillPeriod(data.entries, buckets, emptyValues);
    const x = entries.map((e) => e.ts);
    const stacked = data.devices.map((_, i) => entries.map((e) => e.values.slice(0, i + 1).reduce((a: number, v) => a + (v ?? 0), 0) / 1000));
    const series = data.devices.map((d, i) => ({ label: d.name, color: colors[d.key], values: stacked[i], unit: "kWh", barAlign: 0 as const }));
    return { x, bars: true, series: series.reverse() as Series[] };
  }, [data, power, colors, buckets]);
  if (!data?.devices.length) return null;
  const deviceSum = (totals ?? []).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="section-title">{t("report.devicesSection.title")}</div>
      {chart ? <Chart x={chart.x} series={chart.series} bars={chart.bars} xFormat={xFormat} height={200}
        label={t("report.devicesSection.chartLabel")} />
        : <p className="empty">{t("report.devicesSection.emptyState")}</p>}
      <div className="card"><dl className="facts">
        {load != null && <><dt>{t("report.devicesSection.household")}</dt><dd>{energyKwh(Math.max(0, load - deviceSum))}</dd></>}
        {data.devices.map((d, i) => (
          <Fragment key={d.key}>
            <dt><span className="dot" style={{ background: colors[d.key] }} /> {d.name}</dt>
            <dd>{kwh(totals?.[i])}{load ? ` · ${Math.round(((totals?.[i] ?? 0) / load) * 100)} %` : ""}</dd>
          </Fragment>
        ))}
      </dl></div>
    </>
  );
}
