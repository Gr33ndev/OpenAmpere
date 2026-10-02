import { useState } from "react";
import type { CloudImportState, Settings, Snapshot, Status, Summary } from "./api";
import { activeInputs, PV_INPUT_COLORS, useResource, useStale } from "./api";
import { EnergyFlow } from "./EnergyFlow";
import { navigate } from "./route";
import { kw, kwh, percent, time, updatedLabel, num } from "./format";

function Tile({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="tile">
      <div className="tile-label"><span className="dot" style={{ background: color }} />{label}</div>
      <div className="tile-value">{value}</div>
    </div>
  );
}

export function Ratio({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="ratio">
      <div className="ratio-head">
        <span>{label}</span>
        <strong>{percent(value, true)}</strong>
      </div>
      <div className="bar">
        <div className="bar-fill" style={{ width: `${(value ?? 0) * 100}%` }} />
      </div>
    </div>
  );
}

export function inputName(names: string[] | undefined, index: number) {
  return names?.[index]?.trim() || `Modulfeld ${index + 1}`;
}

function PvInputsCard({ snap }: { snap: Snapshot | null }) {
  const { data: settings } = useResource<Settings>("/api/settings");
  const inputs = activeInputs(snap);
  if (inputs.length < 2) return null; // a single input is already the PV total
  const max = Math.max(...inputs.map((i) => i.power ?? 0), 1);
  return (
    <>
      <div className="section-title">Solar nach Modulfeldern</div>
      <div className="card inputs">
        {inputs.map((i) => (
          <div className="input-row" key={i.index}>
            <div className="input-head">
              <span><span className="dot" style={{ background: PV_INPUT_COLORS[i.index % 4] }} />{inputName(settings?.values["pv.input_names"], i.index)}</span>
              <strong>{kw(i.power)}</strong>
            </div>
            <div className="bar"><div className="bar-fill" style={{ width: `${((i.power ?? 0) / max) * 100}%`, background: PV_INPUT_COLORS[i.index % 4] }} /></div>
            <div className="meta">{i.voltage != null ? `${num(i.voltage, 0)} V` : "–"} · {i.current != null ? `${num(i.current, 1)} A` : "–"}</div>
          </div>
        ))}
      </div>
    </>
  );
}

const TEMPERATURE_LABELS: [keyof Snapshot["temperatures"], string][] = [
  ["inverter", "Wechselrichter"], ["ambient", "Umgebung"], ["battery", "Speicher"],
  ["battery2", "Speicher 2"],
];

function TemperaturesCard({ snap }: { snap: Snapshot | null }) {
  const t = snap?.temperatures ?? {};
  const rows = TEMPERATURE_LABELS.filter(([key]) => t[key] != null);
  if (!rows.length) return null;
  const cells = (prefix: "battery" | "battery2") => {
    const lo = t[`${prefix}_cell_min`], hi = t[`${prefix}_cell_max`];
    return lo != null && hi != null ? `Zellen ${num(lo, 1)} – ${num(hi, 1)} °C` : null;
  };
  return (
    <>
      <div className="section-title">Temperaturen</div>
      <div className="tiles">
        {rows.map(([key, label]) => (
          <div className="tile" key={key}>
            <div className="tile-label">{label}</div>
            <div className="tile-value">{num(t[key]!, 1)} °C</div>
            {(key === "battery" || key === "battery2") && cells(key) && <div className="meta">{cells(key)}</div>}
          </div>
        ))}
      </div>
    </>
  );
}

const HIDE_IMPORT_KEY = "openampere.hideImportHint";

/** Until a history import has run, remind people that the old cloud may switch off at any time. */
function ImportHint() {
  const { data: job } = useResource<CloudImportState & { key_set: boolean }>("/api/import/cloud");
  const [hidden, setHidden] = useState(() => {
    try { return localStorage.getItem(HIDE_IMPORT_KEY) === "1"; } catch { return false; }
  });
  if (hidden || !job || job.status !== "idle") return null;
  const hide = () => {
    try { localStorage.setItem(HIDE_IMPORT_KEY, "1"); } catch { /* private mode */ }
    setHidden(true);
  };
  return (
    <div className="notice info import-hint">
      <div><strong>Verlauf aus der EKD-Cloud sichern?</strong> Solange die Cloud noch läuft, kannst du deine bisherigen
        Daten übernehmen.</div>
      <div className="actions">
        <button className="link" onClick={() => navigate("more/data")}>Einrichten</button>
        <button className="link" onClick={hide}>Ausblenden</button>
      </div>
    </div>
  );
}

export function Dashboard({ snap, online, status }: { snap: Snapshot | null; online: boolean; status: Status | null }) {
  const { data: today } = useResource<Summary>("/api/energy/summary?period=day", 60_000);
  const e = today?.energy_wh;
  const stale = useStale(snap, online, status);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Dashboard</h1>
        <div className={`sub ${snap && !stale ? "" : "off"}`} role="status">
          {!snap ? "Verbinde …"
            : stale ? `Wechselrichter nicht erreichbar seit ${updatedLabel(snap.timestamp)} – angezeigt werden die letzten Werte`
            : `Zuletzt aktualisiert: ${updatedLabel(snap.timestamp)}`}
          {snap?.off_grid && " · Notstrombetrieb"}
        </div>
      </div>

      <ImportHint />
      <EnergyFlow snap={snap} stale={stale} />

      <div className="section-title">Tageswerte</div>
      {today?.partial_since && <p className="hint">Erfasst seit {time(today.partial_since)} Uhr (OpenAmpere läuft erst seit heute).</p>}
      <div className="tiles">
        <Tile label="Erzeugt" value={kwh(e?.pv)} color="var(--pv)" />
        <Tile label="Verbraucht" value={kwh(e?.load)} color="var(--house)" />
        <Tile label="Ins Netz" value={kwh(e?.grid_export)} color="var(--grid)" />
        <Tile label="Aus dem Netz" value={kwh(e?.grid_import)} color="var(--grid)" />
        <Tile label="Gespeichert" value={kwh(e?.battery_charge)} color="var(--battery)" />
        <Tile label="Genutzt" value={kwh(e?.battery_discharge)} color="var(--battery)" />
      </div>
      <div className="card">
        <Ratio label="Autark" value={today?.autarky ?? null} />
      </div>

      <PvInputsCard snap={snap} />
      <TemperaturesCard snap={snap} />
    </div>
  );
}
