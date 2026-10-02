import { useMemo, useState } from "react";
import type { EnergyEntry, Period, PowerEntry, PvInputsTimeline, Summary } from "./api";
import { PV_INPUT_COLORS, useResource } from "./api";
import { Chart, type Series } from "./Chart";
import { Ratio } from "./Dashboard";
import { isoDate, kwh, percent } from "./format";
import { CalendarIcon, Chevron } from "./icons";
import { Segmented } from "./ui";

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

const fmtHour = (ts: number) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const fmtDay = (ts: number) => new Date(ts * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
const fmtMonth = (ts: number) => new Date(ts * 1000).toLocaleDateString("de-DE", { month: "short" });

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

export function Report() {
  const [tab, setTab] = useState<"system" | "autarky">("system");
  const [period, setPeriod] = useState<Period>("day");
  const [date, setDate] = useState(() => new Date());
  const [mode, setMode] = useState<"power" | "energy">("power");
  const day = isoDate(date);
  const showPower = period === "day" && mode === "power";

  const { data: summary } = useResource<Summary>(`/api/energy/summary?period=${period}&date=${day}`, 60_000);
  const { data: energy } = useResource<{ entries: EnergyEntry[] }>(
    showPower ? null : `/api/energy/timeline?period=${period}&date=${day}&resolution=${RESOLUTION[period]}`, 60_000);
  const { data: power } = useResource<{ entries: PowerEntry[] }>(
    showPower ? `/api/power/timeline?date=${day}&step=300` : null, 60_000);

  const xFormat = period === "day" ? fmtHour : period === "year" ? fmtMonth : fmtDay;
  const e = summary?.energy_wh;
  const next = shift(date, period, 1);

  const chart = useMemo(() => {
    if (showPower) {
      const rows = power?.entries ?? [];
      const kwOf = (v: number | null) => (v == null ? null : v / 1000);
      return {
        x: rows.map((r) => r.ts),
        series: [
          { label: "Erzeugung", color: "var(--pv)", values: rows.map((r) => kwOf(r.pv)), unit: "kW" },
          { label: "Verbrauch", color: "var(--house)", values: rows.map((r) => kwOf(r.house)), unit: "kW" },
          { label: "Netz", color: "var(--grid)", values: rows.map((r) => kwOf(r.grid)), unit: "kW" },
          { label: "Speicher", color: "var(--battery)", values: rows.map((r) => kwOf(r.battery)), unit: "kW" },
        ] as Series[],
      };
    }
    const rows = energy?.entries ?? [];
    const k = (v: number | null) => (v == null ? null : v / 1000);
    return {
      x: rows.map((r) => r.ts),
      // consumption is drawn as total (blue = from grid) with the self-supplied part (orange) on top
      series: [
        { label: "Erzeugung", color: "var(--pv)", values: rows.map((r) => k(r.pv)), unit: "kWh", barAlign: -1 },
        { label: "Netzbezug", color: "var(--grid)", values: rows.map((r) => k(r.load)), unit: "kWh", barAlign: 1 },
        { label: "Eigenversorgung", color: "var(--house)", unit: "kWh", barAlign: 1,
          values: rows.map((r) => (r.load == null ? null : Math.max(0, (r.load - (r.grid_import ?? 0)) / 1000))) },
      ] as Series[],
    };
  }, [showPower, power, energy]);

  return (
    <div className="page">
      <div className="text-tabs">
        <button className={tab === "system" ? "active" : ""} onClick={() => setTab("system")}>Verlauf</button>
        <button className={tab === "autarky" ? "active" : ""} onClick={() => setTab("autarky")}>Autarkie</button>
      </div>

      <div className="toolbar">
        <button className="cal" onClick={() => setDate(new Date())} aria-label="Heute"><CalendarIcon /></button>
        <Segmented value={period} options={Object.entries(PERIOD_LABEL) as [Period, string][]}
          onChange={(p) => { setPeriod(p); if (p !== "day") setMode("energy"); }} />
      </div>
      <div className="date-nav">
        <button onClick={() => setDate(shift(date, period, -1))} aria-label="zurück"><Chevron dir="left" /></button>
        <span>{title(date, period)}</span>
        <button onClick={() => setDate(next)} disabled={next > new Date()} aria-label="weiter"><Chevron /></button>
      </div>

      {tab === "system" ? (
        <>
          <div className="section-title">Detaillierte Nutzung</div>
          {chart.x.length ? (
            <Chart x={chart.x} series={chart.series} bars={!showPower} xFormat={xFormat} height={300} />
          ) : (
            <p className="empty">Für diesen Zeitraum liegen keine Daten vor.</p>
          )}
          <Legend items={[
            { color: "var(--pv)", label: "Erzeugt", value: kwh(e?.pv) },
            { color: "var(--house)", label: "Verbraucht", value: kwh(e?.load) },
            { color: "var(--grid)", label: "Netzbezug", value: kwh(e?.grid_import) },
            ...(showPower ? [{ color: "var(--battery)", label: "Speicher" }] : []),
          ]} />
          <div className="row-info">
            <Segmented value={showPower ? "power" : "energy"}
              options={period === "day" ? [["power", "Leistung"], ["energy", "Arbeit"]] : [["energy", "Arbeit"]]}
              onChange={setMode} />
          </div>

          <PvInputsSection period={period} day={day} showPower={showPower} xFormat={xFormat} />
          {period === "day" && <TemperatureSection day={day} />}
        </>
      ) : (
        <>
          <div className="card">
            <div className="big-number">{summary?.autarky != null ? Math.round(summary.autarky * 100) : "–"}<small>%</small></div>
            <p className="hint">autark – dieser Anteil deines Verbrauchs kam nicht aus dem Netz.</p>
          </div>
          <div className="card">
            <Ratio label="Eigenverbrauch" value={summary?.self_consumption ?? null} />
            <p className="hint" style={{ marginTop: 8 }}>Anteil des Solarstroms, der im Haus genutzt oder gespeichert wurde.</p>
          </div>
          <div className="card">
            <dl className="facts">
              <dt>Erzeugt</dt><dd>{kwh(e?.pv)}</dd>
              <dt>Verbraucht</dt><dd>{kwh(e?.load)}</dd>
              <dt>Aus dem Netz</dt><dd>{kwh(e?.grid_import)}</dd>
              <dt>Ins Netz</dt><dd>{kwh(e?.grid_export)}</dd>
              <dt>Autarkie</dt><dd>{percent(summary?.autarky, true)}</dd>
            </dl>
          </div>
        </>
      )}
    </div>
  );
}

/** Solar yield per PV input (module array): power curve for a day, energy per bucket otherwise (stacked bars). */
function PvInputsSection({ period, day, showPower, xFormat }: {
  period: Period; day: string; showPower: boolean; xFormat: (ts: number) => string;
}) {
  const mode = showPower ? "power" : "energy";
  const { data } = useResource<PvInputsTimeline>(
    `/api/pv/inputs?period=${period}&date=${day}&mode=${mode}&resolution=${RESOLUTION[period]}`, 60_000);

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
          <span key={label}>
            <span className="dot" style={{ background: PV_INPUT_COLORS[i % 4] }} />
            {label}{data.totals_wh && <strong>{kwh(data.totals_wh[i])}</strong>}
          </span>
        ))}
      </div>
    </>
  );
}

function TemperatureSection({ day }: { day: string }) {
  const { data } = useResource<{ entries: { ts: number; inverter: number | null; battery: number | null }[] }>(
    `/api/temperatures/timeline?date=${day}`, 60_000);
  const chart = useMemo(() => {
    const rows = data?.entries ?? [];
    return {
      x: rows.map((r) => r.ts),
      series: [
        { label: "Wechselrichter", color: "var(--coral)", values: rows.map((r) => r.inverter), unit: "°C" },
        { label: "Speicher", color: "var(--battery)", values: rows.map((r) => r.battery), unit: "°C" },
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
      </div>
    </>
  );
}
