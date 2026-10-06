import { useResource } from "./api";
import { num, timeZone } from "./format";

type Outage = {
  start: number; end: number; duration_s: number; soc_start: number | null; soc_end: number | null; soc_min: number | null;
  load_kwh: number | null; solar_kwh: number | null; battery_kwh: number | null; dark_since: number | null;
  gap_reason?: "battery_empty" | "no_data" | "inverter_off" | null;
};
export type OutagesView = {
  current: { start: number; soc_start: number | null; soc_last: number | null } | null;
  outages: Outage[]; count: number; total_s: number;
};

const day = (ts: number) => new Date(ts * 1000).toLocaleDateString("de-DE", { day: "numeric", month: "long", year: "numeric",
  timeZone: timeZone() });
const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });
const duration = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} Std. ${m} Min.` : `${Math.max(1, m)} Min.`;
};
const pct = (v: number | null) => (v == null ? "–" : `${num(v, 0)} %`);
// recorded before #89 without a reason: a battery that never went below 15 % was not empty
const batteryEmpty = (o: Outage) => (o.gap_reason ? o.gap_reason === "battery_empty" : (o.soc_min ?? 0) <= 15);

/** Power cuts recorded from the inverter's off-grid mode: how often, how long, how far battery and sun carried (#51). */
export function OutagesSection() {
  const { data } = useResource<OutagesView>("/api/outages", 60_000);
  if (!data || (!data.count && !data.current)) return null;
  return (
    <>
      <div className="section-title">Stromausfälle</div>
      <div className="card">
        <p className="outage-summary">
          {data.count === 1 ? "1 Stromausfall" : `${data.count} Stromausfälle`}
          {data.count > 0 && <>, zusammen {duration(data.total_s)}</>}
          {data.outages.length > 0 && <span className="meta"> seit {day(data.outages[data.outages.length - 1].start)}</span>}
        </p>
        {data.current && (
          <p className="hint">Gerade läuft einer: seit {time(data.current.start)} Uhr, Speicher {pct(data.current.soc_start)} →
            {" "}{pct(data.current.soc_last)}.</p>
        )}
        <ul className="sessions outages">
          {data.outages.map((o) => (
            <li key={o.start}>
              <span><strong>{day(o.start)}, {time(o.start)} Uhr</strong> · {duration(o.duration_s)}</span>
              <span className="meta">
                Speicher {pct(o.soc_start)} → {pct(o.soc_end)}{o.soc_min != null && o.soc_min < (o.soc_end ?? 101) ? ` (tiefster Stand ${pct(o.soc_min)})` : ""}
                {o.load_kwh != null && <> · Haus {num(o.load_kwh, 1)}&nbsp;kWh{o.solar_kwh != null ? `, davon Sonne ${num(Math.min(o.solar_kwh, o.load_kwh), 1)} kWh` : ""}</>}
              </span>
              {o.dark_since && (o.gap_reason === "inverter_off"
                ? <span className="meta">Ab {time(o.dark_since)} Uhr war der Wechselrichter nicht erreichbar, OpenAmpere lief aber weiter.
                  Vermutlich wurde er ausgeschaltet oder die Verbindung war unterbrochen. Die Dauer ist deshalb ungenau.</span>
                : batteryEmpty(o)
                ? <span className="meta warn-text">Ab {time(o.dark_since)} Uhr ohne Strom, vermutlich war der Speicher leer.</span>
                : <span className="meta">Ab {time(o.dark_since)} Uhr kamen keine Messwerte, etwa weil die Verbindung unterbrochen war.
                  Die Dauer ist deshalb ungenau.</span>)}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
