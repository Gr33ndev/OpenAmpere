import { useState } from "react";
import type { CloudImportState, Device, DevicesView, Settings, Snapshot, Status, Summary } from "./api";
import { activeInputs, PV_INPUT_COLORS, useResource, useStale } from "./api";
import { EnergyFlow } from "./EnergyFlow";
import { navigate } from "./route";
import { Notice } from "./ui";
import { DeviceIcon, deviceStatus } from "./DevicesPage";
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

export function PvInputsCard({ snap }: { snap: Snapshot | null }) {
  const { data: settings } = useResource<Settings>("/api/settings");
  const inputs = activeInputs(snap, settings?.values["pv.hidden_inputs"]);
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

export function TemperaturesCard({ snap }: { snap: Snapshot | null }) {
  const t = snap?.temperatures ?? {};
  const rows = TEMPERATURE_LABELS.filter(([key]) => t[key] != null);
  if (!rows.length) return null;
  // with cell temperatures, the battery's own sensor is the electronics (BMS), not the cells (#16)
  const cells = (prefix: "battery" | "battery2") => {
    const lo = t[`${prefix}_cell_min`], hi = t[`${prefix}_cell_max`];
    return lo != null && hi != null ? `${num(lo, 1)}–${num(hi, 1)}\u00a0°C` : null;
  };
  return (
    <>
      <div className="section-title">Temperaturen</div>
      <div className="tiles">
        {rows.map(([key, label]) => (
          <div className="tile" key={key}>
            <div className="tile-label">{label}</div>
            {(key === "battery" || key === "battery2") && cells(key) ? <>
              <div className="tile-value">{cells(key)}</div>
              <div className="meta">Zellen · Elektronik {num(t[key]!, 1)}&nbsp;°C</div>
            </> : <div className="tile-value">{num(t[key]!, 1)} °C</div>}
          </div>
        ))}
      </div>
    </>
  );
}

/** Power cut: the inverter runs the house as an island from battery and solar (#48). */
function OffGridNotice({ snap }: { snap: Snapshot }) {
  const { data: settings } = useResource<Settings>("/api/settings");
  const capacity = settings?.values["battery.capacity_kwh"] ?? 0;
  const draw = snap.battery_power ?? 0; // + = discharging
  const hours = capacity > 0 && snap.battery_soc != null && draw > 50 ? (capacity * snap.battery_soc / 100 * 1000) / draw : null;
  return (
    <div className="off-grid" role="alert">
      <strong>Stromausfall: Notstrombetrieb</strong>
      <span>Das Netz ist weg, das Haus läuft über Speicher und Solaranlage. Speicher {percent(snap.battery_soc)}
        {hours != null ? `, reicht beim jetzigen Verbrauch etwa ${hours >= 24 ? `${num(hours / 24, 0)} Tage` : `${num(Math.max(hours, 0.1), hours < 10 ? 1 : 0)} Std.`}`
          : draw <= 50 ? ", die Sonne deckt gerade den Verbrauch" : ""}.
        {" "}Große Verbraucher besser ausschalten.</span>
    </div>
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
        Daten übernehmen. (OpenAmpere ist ein unabhängiges Projekt ohne Verbindung zu EKD.)</div>
      <div className="actions">
        <button className="link" onClick={() => navigate("more/data")}>Einrichten</button>
        <button className="link" onClick={hide}>Ausblenden</button>
      </div>
    </div>
  );
}

const HIDE_TIPS_KEY = "openampere.hideTips";

/** Short introduction for the first visits. */
function Tips() {
  const [hidden, setHidden] = useState(() => {
    try { return localStorage.getItem(HIDE_TIPS_KEY) === "1"; } catch { return false; }
  });
  if (hidden) return null;
  const hide = () => {
    try { localStorage.setItem(HIDE_TIPS_KEY, "1"); } catch { /* private mode */ }
    setHidden(true);
  };
  return (
    <div className="card tips">
      <strong>So liest du die Übersicht</strong>
      <ul>
        <li>Die Linien zeigen, wohin der Strom gerade fließt: vom Dach, aus dem Speicher und aus dem Netz zum Haus und zu Geräten wie Wallbox oder Heizstab.</li>
        <li>„Bezug“ heißt: Strom kommt aus dem Netz. „Einspeisung“: Du gibst Strom ab.</li>
        <li>In der Auswertung siehst du Tage, Wochen und Jahre. Tippe auf ein Diagramm für die genauen Werte.</li>
        <li>Unter „Geräte“ bedienst du Wallbox und Heizstab, unter „Mehr“ findest du alle Einstellungen.</li>
      </ul>
      <button className="link" onClick={hide}>Verstanden, ausblenden</button>
    </div>
  );
}

/** Compact list of the extra devices; tap to control them under "Geräte". */
function DevicesCard({ devices, todayWh, gridCharging }: { devices: Device[]; todayWh: Record<string, number>; gridCharging: boolean }) {
  if (!devices.length && !gridCharging) return null;
  return (
    <>
      <div className="section-title">Geräte</div>
      <div className="card menu">
        {gridCharging && <div className="device-row-compact"><span className="grow">Speicher lädt aus dem Netz</span></div>}
        {devices.map((d) => (
          <button key={d.key} className="device-row-compact" onClick={() => navigate("devices")}>
            <DeviceIcon kind={d.kind} size={36} />
            <span className="grow"><strong>{d.name}</strong><span className="menu-hint">{deviceStatus(d)}
              {d.temperature_c != null && d.kind === "heating_rod" ? ` · ${num(d.temperature_c, 0)} °C` : ""}</span></span>
            <span className="device-today">{todayWh[d.key] != null ? kwh(todayWh[d.key]) : ""}<small>heute</small></span>
          </button>
        ))}
      </div>
    </>
  );
}

export function Dashboard({ snap, online, status }: { snap: Snapshot | null; online: boolean; status: Status | null }) {
  const { data: today } = useResource<Summary>("/api/energy/summary?period=day", 60_000);
  const { data: devicesView } = useResource<DevicesView>("/api/devices", 30_000);
  const e = today?.energy_wh;
  const stale = useStale(snap, online, status);
  const devices = status?.devices.items ?? [];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Übersicht</h1>
        <div className={`sub ${snap && !stale ? "" : "off"}`} role="status">
          {!snap ? "Verbinde …"
            : stale ? `Wechselrichter nicht erreichbar seit ${updatedLabel(snap.timestamp)} – angezeigt werden die letzten Werte`
            : `Zuletzt aktualisiert: ${updatedLabel(snap.timestamp)}`}
        </div>
      </div>
      {snap?.off_grid && !stale && <OffGridNotice snap={snap} />}

      {status?.clock_wrong && (
        <Notice kind="error">Die Uhrzeit des Servers stimmt nicht (keine Internetzeit?). Bis sie korrekt ist, speichert
          OpenAmpere keine Messwerte, damit sie nicht auf falschen Tagen landen.</Notice>
      )}
      <ImportHint />
      <EnergyFlow snap={snap} stale={stale} devices={devices} gridCharging={!!status?.devices.grid_charging} />

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

      <DevicesCard devices={devices} todayWh={devicesView?.today_wh ?? {}} gridCharging={!!status?.devices.grid_charging} />
      <Tips />
      <p className="hint center">Leistung je Modulfeld und Temperaturen findest du unter Mehr → Meine Anlage.</p>
    </div>
  );
}
