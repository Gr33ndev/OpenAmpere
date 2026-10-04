import { Fragment, useEffect, useMemo, useState } from "react";
import type { DeviceSeries, EnergyEntry, Period, PowerEntry, PvInputsTimeline, Summary } from "./api";
import { PV_INPUT_COLORS, useResource } from "./api";
import { Chart, type Series } from "./Chart";
import { isoDate, kw, kwh, percent, timeZone, todayIso } from "./format";
import { CalendarIcon, Chevron } from "./icons";
import { Segmented } from "./ui";
import { KeyFigures } from "./KeyFigures";
import { colorsFor } from "./DevicesPage";
import { BillingSection } from "./BillingPage";
import { VehicleStats } from "./VehicleStats";
import { EvccSessions } from "./WallboxPage";

const PERIOD_LABEL: Record<Period, string> = { day: "Tag", week: "Woche", month: "Monat", year: "Jahr" };
const RESOLUTION: Record<Period, string> = { day: "60m", week: "day", month: "day", year: "month" };

function shift(date: Date, period: Period, step: number): Date {
  const d = new Date(date);
  if (period === "day") d.setDate(d.getDate() + step);
  if (period === "week") d.setDate(d.getDate() + 7 * step);
  if (period === "month") d.setMonth(d.getMonth() + step, 1);
  if (period === "year") d.setFullYear(d.getFullYear() + step, 0, 1);
  return d;
}

function title(date: Date, period: Period): string {
  if (period === "day") return date.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
  if (period === "week") {
    const monday = shift(date, "day", -((date.getDay() + 6) % 7));
    const sunday = shift(monday, "day", 6);
    return `${monday.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })} – ${sunday.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}`;
  }
  if (period === "month") return date.toLocaleDateString("de-DE", { month: "long", year: "numeric" });
  return String(date.getFullYear());
}

const fmtHour = (ts: number) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });
const fmtDay = (ts: number) => new Date(ts * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", timeZone: timeZone() });
const fmtMonth = (ts: number) => new Date(ts * 1000).toLocaleDateString("de-DE", { month: "short", timeZone: timeZone() });

function Legend({ items }: { items: { color: string; label: string; value?: string }[] }) {
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.label}>
          <span className="dot" style={{ background: i.color }} />
          {i.label}{i.value && <strong>{i.value}</strong>}
        </span>
      ))}
    </div>
  );
}


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

/** Values of the touched bar / point, so nobody has to guess from the axis. */
function Readout({ rows, index, showPower, xFormat, extra = [] }: {
  rows: (EnergyEntry | PowerEntry)[]; index: number | null; showPower: boolean; xFormat: (ts: number) => string;
  extra?: { label: string; values: (number | null)[] }[];
}) {
  if (index == null || !rows[index]) return <p className="hint readout-hint">Tippe auf das Diagramm, um die Werte zu sehen.</p>;
  const r = rows[index];
  const items: [string, string][] = [];
  if (showPower) {
    const p = r as PowerEntry;
    items.push(["Erzeugung", kw(p.pv)], ["Verbrauch", kw(p.house)],
      ["Netz", p.grid == null ? "–" : `${kw(Math.abs(p.grid))} ${p.grid >= 0 ? "Bezug" : "Einspeisung"}`],
      ["Speicher", p.battery == null ? "–" : `${kw(Math.abs(p.battery))} ${p.battery >= 0 ? "entladen" : "laden"}`]);
  } else {
    const e = r as EnergyEntry;
    items.push(["Erzeugt", kwh(e.pv)], ["Verbraucht", kwh(e.load)], ["Netzbezug", kwh(e.grid_import)],
      ["Eingespeist", kwh(e.grid_export)], ["Geladen", kwh(e.battery_charge)], ["Entladen", kwh(e.battery_discharge)]);
  }
  for (const x of extra) {
    const v = x.values[index];
    if (v != null) items.push([x.label, kw(v * 1000)]);
  }
  if (r.soc != null) items.push(["Ladestand", percent(r.soc)]);
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
  const running = fromIso(today) < shift(date, period, 1) && !(fromIso(today) < date);
  const refresh = running ? 60_000 : 0;

  const { data: summary } = useResource<Summary>(`/api/energy/summary?period=${period}&date=${day}`, refresh);
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
  const rows: (EnergyEntry | PowerEntry)[] = (showPower ? power?.entries : energy?.entries) ?? [];
  useEffect(() => setHover(null), [period, day, dayView, resolution]);

  const deviceColors = useMemo(() => colorsFor(devPower?.devices ?? []), [devPower]);
  /** Device power on the same time axis as the main power curve (dashed lines). */
  const deviceSeries = (xs: number[]): Series[] => {
    if (!devPower?.devices.length) return [];
    const byTs = new Map(devPower.entries.map((e) => [e.ts, e.values]));
    return devPower.devices.map((d, i) => ({
      label: d.name, color: deviceColors[d.key], unit: "kW", dash: true,
      values: xs.map((ts) => { const v = byTs.get(ts)?.[i]; return v == null ? null : v / 1000; }),
    }));
  };

  const chart = useMemo(() => {
    const soc: Series = { label: "Ladestand", color: "var(--battery)", values: rows.map((r) => r.soc), unit: "%", scale: "soc" };
    const hasSoc = rows.some((r) => r.soc != null);
    if (showPower) {
      const p = rows as PowerEntry[];
      const kwOf = (v: number | null) => (v == null ? null : v / 1000);
      return {
        x: p.map((r) => r.ts),
        series: [
          { label: "Erzeugung", color: "var(--pv)", values: p.map((r) => kwOf(r.pv)), unit: "kW" },
          { label: "Verbrauch", color: "var(--house)", values: p.map((r) => kwOf(r.house)), unit: "kW" },
          { label: "Netz", color: "var(--grid)", values: p.map((r) => kwOf(r.grid)), unit: "kW" },
          { label: "Speicher", color: "var(--battery)", values: p.map((r) => kwOf(r.battery)), unit: "kW" },
          ...deviceSeries(p.map((r) => r.ts)),
          ...(hasSoc ? [{ ...soc, color: "var(--label)" }] : []),
        ] as Series[],
      };
    }
    const en = rows as EnergyEntry[];
    const k = (v: number | null) => (v == null ? null : v / 1000);
    return {
      x: en.map((r) => r.ts),
      // consumption is drawn as total (blue = from grid) with the self-supplied part (orange) on top
      series: [
        { label: "Erzeugung", color: "var(--pv)", values: en.map((r) => k(r.pv)), unit: "kWh", barAlign: -1 },
        { label: "Netzbezug", color: "var(--grid)", values: en.map((r) => k(r.load)), unit: "kWh", barAlign: 1 },
        { label: "Eigenversorgung", color: "var(--house)", unit: "kWh", barAlign: 1,
          values: en.map((r) => (r.load == null ? null : Math.max(0, (r.load - (r.grid_import ?? 0)) / 1000))) },
        ...(hasSoc && period === "day" ? [{ ...soc, color: "var(--label)" }] : []),
      ] as Series[],
    };
  }, [showPower, rows, period, devPower, deviceColors]);

  return (
    <div className="page">
      <div className="page-head"><h1>Auswertung</h1></div>
      <div className="toolbar">
        <label className="cal" aria-label="Datum wählen">
          <CalendarIcon />
          <input type="date" value={day} max={today} onChange={(ev) => ev.target.value && setDate(fromIso(ev.target.value))} />
        </label>
        <Segmented value={period} options={Object.entries(PERIOD_LABEL) as [Period, string][]}
          onChange={setPeriod} />
      </div>
      <div className="date-nav">
        <button onClick={() => setDate(shift(date, period, -1))} aria-label="Zeitraum zurück"><Chevron dir="left" /></button>
        <span>{title(date, period)}</span>
        {picked ? <button onClick={() => setPicked(null)} className="today-link">Heute</button> : null}
        <button onClick={() => setDate(next)} disabled={next > fromIso(today)} aria-label="Zeitraum weiter"><Chevron /></button>
      </div>

      <KeyFigures summary={summary} />

      <div className="section-title">Verlauf</div>
      {period === "day" && (
        <div className="row-info">
          <Segmented value={dayView} onChange={setDayView}
            options={[["power", "Leistung"], ["15m", "Arbeit · 15 min"], ["60m", "Arbeit · 1 h"]]} />
        </div>
      )}
      {!loaded ? (
        <p className="empty">{loadError ? `Konnte nicht geladen werden: ${loadError}` : "Lade …"}</p>
      ) : chart.x.length ? (
        <>
          <Chart x={chart.x} series={chart.series} bars={!showPower} xFormat={xFormat} height={300} onHover={setHover}
            label={`Diagramm ${showPower ? "Leistung" : "Energie"} für ${title(date, period)}`} />
          <Readout rows={rows} index={hover} showPower={showPower} xFormat={xFormat}
            extra={chart.series.filter((x) => x.dash).map((x) => ({ label: x.label, values: x.values }))} />
        </>
      ) : (
        <p className="empty">Für diesen Zeitraum liegen keine Daten vor.</p>
      )}
      <Legend items={[
        { color: "var(--pv)", label: "Erzeugt" },
        { color: "var(--house)", label: showPower ? "Verbrauch" : "Selbst versorgt" },
        { color: "var(--grid)", label: showPower ? "Netz" : "Aus dem Netz" },
        ...(showPower ? [{ color: "var(--battery)", label: "Speicher" }] : []),
        ...(chart.series.some((x) => x.scale === "soc") ? [{ color: "var(--label)", label: "Ladestand (rechte Achse)" }] : []),
        ...(showPower ? (devPower?.devices ?? []).map((d) => ({ color: deviceColors[d.key], label: `${d.name} (gestrichelt)` })) : []),
      ]} />
      {showPower && <p className="hint">Netz über null heißt Bezug, darunter Einspeisung. Speicher über null heißt Entladen, darunter Laden.</p>}
      {period === "day" && summary?.recorded_since && (
        <p className="hint">OpenAmpere zeichnet seit {new Date(summary.recorded_since * 1000).toLocaleTimeString("de-DE",
          { hour: "2-digit", minute: "2-digit", timeZone: timeZone() })} Uhr auf, deshalb beginnt der Verlauf erst dann. Die
          Tageswerte oben stammen aus den Zählern des Wechselrichters und gelten für den ganzen Tag.</p>
      )}

      <DevicesSection data={showPower ? devPower : devEnergy} power={showPower} totals={devEnergy?.totals_wh}
        colors={colorsFor((showPower ? devPower : devEnergy)?.devices ?? [])} xFormat={xFormat} load={summary?.energy_wh.load ?? null} />
      <PvInputsSection period={period} day={day} showPower={showPower} resolution={resolution} xFormat={xFormat} refresh={refresh} />
      <BillingSection />
      <VehicleStats />
      <EvccSessions />
    </div>
  );
}

/** Solar yield per PV input (module array): power curve for a day, energy per bucket otherwise (stacked bars). */
function PvInputsSection({ period, day, showPower, resolution, xFormat, refresh }: {
  period: Period; day: string; showPower: boolean; resolution: string; xFormat: (ts: number) => string; refresh: number;
}) {
  const mode = showPower ? "power" : "energy";
  const { data } = useResource<PvInputsTimeline>(
    `/api/pv/inputs?period=${period}&date=${day}&mode=${mode}&resolution=${resolution}`, refresh);

  const chart = useMemo(() => {
    if (!data || data.labels.length < 2) return null;
    const x = data.entries.map((e) => e.ts);
    const col = (i: number) => data.entries.map((e) => (e.values[i] == null ? null : (e.values[i] as number) / 1000));
    if (mode === "power") {
      return { x, bars: false, series: data.labels.map((label, i) => ({ label, color: PV_INPUT_COLORS[i % 4], values: col(i), unit: "kW" })) as Series[] };
    }
    // stacked: draw the running sums from the top down so each input shows as its own segment
    const stacked = data.labels.map((_, i) => data.entries.map((e) => e.values.slice(0, i + 1).reduce((a: number, v) => a + (v ?? 0), 0) / 1000));
    const series = data.labels.map((label, i) => ({ label, color: PV_INPUT_COLORS[i % 4], values: stacked[i], unit: "kWh", barAlign: 0 as const }));
    return { x, bars: true, series: series.reverse() as Series[] };
  }, [data, mode]);

  if (!data || data.labels.length < 2) return null;
  return (
    <>
      <div className="section-title">Nach Modulfeldern</div>
      {chart && chart.x.length ? (
        <Chart x={chart.x} series={chart.series} bars={chart.bars} xFormat={xFormat} height={220} />
      ) : <p className="empty">Für diesen Zeitraum liegen keine Werte je Modulfeld vor.</p>}
      <div className="legend">
        {data.labels.map((label, i) => (
          <span key={i}>
            <span className="dot" style={{ background: PV_INPUT_COLORS[i % 4] }} />
            {label}{data.totals_wh && <strong>{kwh(data.totals_wh[i])}</strong>}
          </span>
        ))}
      </div>
    </>
  );
}

export function TemperatureSection({ day, refresh }: { day: string; refresh: number }) {
  const { data } = useResource<{ entries: { ts: number; inverter: number | null; battery: number | null;
    cell_max?: number | null; cell_min?: number | null }[] }>(
    `/api/temperatures/timeline?date=${day}`, refresh);
  const chart = useMemo(() => {
    const rows = data?.entries ?? [];
    return {
      x: rows.map((r) => r.ts),
      series: [
        { label: "Wechselrichter", color: "var(--coral)", values: rows.map((r) => r.inverter), unit: "°C" },
        { label: "Speicher", color: "var(--battery)", values: rows.map((r) => r.battery), unit: "°C" },
        ...(rows.some((r) => r.cell_max != null) ? [
          { label: "Wärmste Zelle", color: "var(--pv)", values: rows.map((r) => r.cell_max ?? null), unit: "°C", dash: true },
          { label: "Kühlste Zelle", color: "var(--sky)", values: rows.map((r) => r.cell_min ?? null), unit: "°C", dash: true },
        ] : []),
      ] as Series[],
    };
  }, [data]);
  if (!chart.x.length || chart.series.every((s) => s.values.every((v) => v == null))) return null;
  return (
    <>
      <div className="section-title">Temperaturen</div>
      <Chart x={chart.x} series={chart.series} xFormat={fmtHour} height={180} />
      <div className="legend">
        <span><span className="dot" style={{ background: "var(--coral)" }} />Wechselrichter</span>
        <span><span className="dot" style={{ background: "var(--battery)" }} />Speicher</span>
        {chart.series.length > 2 && <span><span className="dot" style={{ background: "var(--pv)" }} />Wärmste Zelle</span>}
        {chart.series.length > 2 && <span><span className="dot" style={{ background: "var(--sky)" }} />Kühlste Zelle</span>}
      </div>
    </>
  );
}

/** Energy (stacked bars) or power (lines) per device: wallbox, heating rod, ... */
function DevicesSection({ data, power, totals, colors, xFormat, load }: {
  data: DeviceSeries | null; power: boolean; totals?: number[]; colors: Record<string, string>; xFormat: (ts: number) => string;
  load: number | null;
}) {
  const chart = useMemo(() => {
    if (!data?.devices.length || !data.entries.some((e) => e.values.some((v) => (v ?? 0) > 0))) return null;
    const x = data.entries.map((e) => e.ts);
    if (power) {
      return { x, bars: false, series: data.devices.map((d, i) => ({ label: d.name, color: colors[d.key], unit: "kW",
        values: data.entries.map((e) => (e.values[i] == null ? null : (e.values[i] as number) / 1000)) })) as Series[] };
    }
    const stacked = data.devices.map((_, i) => data.entries.map((e) => e.values.slice(0, i + 1).reduce((a: number, v) => a + (v ?? 0), 0) / 1000));
    const series = data.devices.map((d, i) => ({ label: d.name, color: colors[d.key], values: stacked[i], unit: "kWh", barAlign: 0 as const }));
    return { x, bars: true, series: series.reverse() as Series[] };
  }, [data, power, colors]);
  if (!data?.devices.length) return null;
  const deviceSum = (totals ?? []).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="section-title">Nach Geräten</div>
      {chart ? <Chart x={chart.x} series={chart.series} bars={chart.bars} xFormat={xFormat} height={200}
        label="Diagramm Verbrauch je Gerät" />
        : <p className="empty">In diesem Zeitraum haben die Geräte keinen Strom verbraucht.</p>}
      <div className="card"><dl className="facts">
        {load != null && <><dt>Haushalt</dt><dd>{kwh(Math.max(0, load - deviceSum))}</dd></>}
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
