import { useState } from "react";
import type { CloudImportState, Device, DevicesView, Settings, Snapshot, Status, Summary } from "./api";
import { activeInputs, PV_INPUT_COLORS, useResource, useStale } from "./api";
import { EnergyFlow } from "./EnergyFlow";
import { navigate } from "./route";
import { Notice } from "./ui";
import { DeviceIcon, deviceStatus } from "./DevicesPage";
import { kw, kwh, percent, time, updatedLabel, num } from "./format";
import { t } from "./i18n";

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
  return names?.[index]?.trim() || t("overview.inputName.stringNumber", { number: index + 1 });
}

export function PvInputsCard({ snap }: { snap: Snapshot | null }) {
  const { data: settings } = useResource<Settings>("/api/settings");
  const inputs = activeInputs(snap, settings?.values["pv.hidden_inputs"]);
  if (inputs.length < 2) return null; // a single input is already the PV total
  const max = Math.max(...inputs.map((i) => i.power ?? 0), 1);
  return (
    <>
      <div className="section-title">{t("overview.pvInputsCard.title")}</div>
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
  ["inverter", t("common.inverter")], ["ambient", t("overview.temperatureLabels.ambient")], ["battery", t("common.battery")],
  ["battery2", t("overview.temperatureLabels.battery2")],
];

export function TemperaturesCard({ snap }: { snap: Snapshot | null }) {
  const temps = snap?.temperatures ?? {};
  const rows = TEMPERATURE_LABELS.filter(([key]) => temps[key] != null);
  if (!rows.length) return null;
  // with cell temperatures, the battery's own sensor is the electronics (BMS), not the cells (#16)
  const cells = (prefix: "battery" | "battery2") => {
    const lo = temps[`${prefix}_cell_min`], hi = temps[`${prefix}_cell_max`];
    return lo != null && hi != null ? `${num(lo, 1)}–${num(hi, 1)}\u00a0°C` : null;
  };
  return (
    <>
      <div className="section-title">{t("common.temperatures")}</div>
      <div className="tiles">
        {rows.map(([key, label]) => (
          <div className="tile" key={key}>
            <div className="tile-label">{label}</div>
            {(key === "battery" || key === "battery2") && cells(key) ? <>
              <div className="tile-value">{cells(key)}</div>
              <div className="meta">{t("overview.temperaturesCard.cellsElectronics", { temperature: num(temps[key]!, 1) })}&nbsp;°C</div>
            </> : <div className="tile-value">{num(temps[key]!, 1)} °C</div>}
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
  const soc = percent(snap.battery_soc);
  return (
    <div className="off-grid" role="alert">
      <strong>{t("overview.offGridNotice.title")}</strong>
      <span>{t("overview.offGridNotice.gridDown")}{" "}
        {hours != null ? t("overview.offGridNotice.batteryLastsAbout", { soc, duration: hours >= 24
          ? t("overview.offGridNotice.days", { days: num(hours / 24, 0) }) : t("overview.offGridNotice.hours", { hours: num(Math.max(hours, 0.1), hours < 10 ? 1 : 0) }) })
          : draw <= 50 ? t("overview.offGridNotice.batterySunCovering", { soc }) : t("overview.offGridNotice.batterySoc", { soc })}
        {" "}{t("overview.offGridNotice.switchOffLargeLoads")}</span>
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
      <div><strong>{t("overview.importHint.title")}</strong>{" "}
        {t("overview.importHint.hint")}</div>
      <div className="actions">
        <button className="link" onClick={() => navigate("more/data")}>{t("common.setUp")}</button>
        <button className="link" onClick={hide}>{t("overview.importHint.hide")}</button>
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
      <strong>{t("overview.tips.title")}</strong>
      <ul>
        <li>{t("overview.tips.flowLines")}</li>
        <li>{t("overview.tips.importAndFeedIn")}</li>
        <li>{t("overview.tips.report")}</li>
        <li>{t("overview.tips.devicesAndMore")}</li>
      </ul>
      <button className="link" onClick={hide}>{t("overview.tips.dismiss")}</button>
    </div>
  );
}

/** Compact list of the extra devices; tap to control them under "Geräte". */
function DevicesCard({ devices, todayWh, gridCharging }: { devices: Device[]; todayWh: Record<string, number>; gridCharging: boolean }) {
  if (!devices.length && !gridCharging) return null;
  return (
    <>
      <div className="section-title">{t("common.devices")}</div>
      <div className="card menu">
        {gridCharging && <div className="device-row-compact"><span className="grow">{t("overview.devicesCard.batteryChargingFromGrid")}</span></div>}
        {devices.map((d) => (
          <button key={d.key} className="device-row-compact" onClick={() => navigate("devices")}>
            <DeviceIcon kind={d.kind} size={36} />
            <span className="grow"><strong>{d.name}</strong><span className="menu-hint">{deviceStatus(d)}
              {d.temperature_c != null && d.kind === "heating_rod" ? ` · ${num(d.temperature_c, 0)} °C` : ""}</span></span>
            <span className="device-today">{todayWh[d.key] != null ? kwh(todayWh[d.key]) : ""}<small>{t("overview.devicesCard.today")}</small></span>
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
        <h1>{t("common.overview")}</h1>
        <div className={`sub ${snap && !stale ? "" : "off"}`} role="status">
          {!snap ? t("overview.dashboard.connecting")
            : stale ? t("overview.dashboard.inverterUnreachable", { time: updatedLabel(snap.timestamp) })
            : t("overview.dashboard.lastUpdated", { time: updatedLabel(snap.timestamp) })}
        </div>
      </div>
      {snap?.off_grid && !stale && <OffGridNotice snap={snap} />}

      {status?.clock_wrong && (
        <Notice kind="error">{t("overview.dashboard.clockWrong")}</Notice>
      )}
      <ImportHint />
      <EnergyFlow snap={snap} stale={stale} devices={devices} gridCharging={!!status?.devices.grid_charging} />

      <div className="section-title">{t("overview.dashboard.todayTitle")}</div>
      {today?.partial_since && <p className="hint">{t("overview.dashboard.recordedSince", { time: time(today.partial_since) })}</p>}
      <div className="tiles">
        <Tile label={t("common.generated")} value={kwh(e?.pv)} color="var(--pv)" />
        <Tile label={t("common.consumed")} value={kwh(e?.load)} color="var(--house)" />
        <Tile label={t("overview.dashboard.toGrid")} value={kwh(e?.grid_export)} color="var(--grid)" />
        <Tile label={t("overview.dashboard.fromGrid")} value={kwh(e?.grid_import)} color="var(--grid)" />
        <Tile label={t("overview.dashboard.batteryStored")} value={kwh(e?.battery_charge)} color="var(--battery)" />
        <Tile label={t("overview.dashboard.batteryUsed")} value={kwh(e?.battery_discharge)} color="var(--battery)" />
      </div>
      <div className="card">
        <Ratio label={t("common.selfSufficient")} value={today?.autarky ?? null} />
      </div>

      <DevicesCard devices={devices} todayWh={devicesView?.today_wh ?? {}} gridCharging={!!status?.devices.grid_charging} />
      <Tips />
      <p className="hint center">{t("overview.dashboard.stringsAndTemperaturesHint", { place: `${t("common.more")} → ${t("common.mySystem")}` })}</p>
    </div>
  );
}
