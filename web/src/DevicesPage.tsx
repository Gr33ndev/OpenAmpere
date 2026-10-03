import { useState, type ReactNode } from "react";
import type { BatteryState, Device, DevicesView, Snapshot } from "./api";
import { postJson, useResource } from "./api";
import { ConsumersPage } from "./ConsumersPage";
import { ControlModeBar } from "./ControlMode";
import { kw, kwh, num, timeZone } from "./format";
import { BatteryIcon, CarIcon, HeaterIcon, HeatPumpIcon, PlugIcon } from "./icons";
import { BatteryPage, ChargingPage, type ChargingView } from "./SettingsPages";
import { goBack, navigate } from "./route";
import { Button, MenuRow, Notice, Segmented, toast } from "./ui";
import { EVCC_URL, WallboxCard, WallboxPage, type EvccView } from "./WallboxPage";

const COLORS: Record<Device["kind"], string[]> = {
  wallbox: ["var(--wallbox)", "var(--wallbox-2)"],
  heating_rod: ["var(--heater)", "var(--heater-2)"],
  heat_pump: ["var(--heatpump)", "var(--heatpump)"],
  switch: ["var(--plug)", "var(--plug-2)"],
};

/** Stable colour per device: by kind, then by position among devices of the same kind. */
export function deviceColor(kind: Device["kind"], index: number): string {
  const list = COLORS[kind] ?? COLORS.switch;
  return list[index % list.length];
}

export function colorsFor(devices: { key: string; kind: Device["kind"] }[]): Record<string, string> {
  const seen: Record<string, number> = {};
  return Object.fromEntries(devices.map((d) => {
    const i = seen[d.kind] ?? 0;
    seen[d.kind] = i + 1;
    return [d.key, deviceColor(d.kind, i)];
  }));
}

export function DeviceIcon({ kind, size = 44 }: { kind: Device["kind"]; size?: number }) {
  if (kind === "wallbox") return <CarIcon size={size} />;
  if (kind === "heating_rod") return <HeaterIcon size={size} />;
  if (kind === "heat_pump") return <HeatPumpIcon size={size} />;
  return <PlugIcon size={size} />;
}

export function deviceStatus(d: Device): string {
  if (d.error) return d.error;
  if (d.override?.mode === "off") return "von Hand aus";
  if (d.override?.mode === "boost") return `volle Leistung${d.override.until ? ` bis ${clock(d.override.until)}` : ""}`;
  if (d.kind === "wallbox") return d.active ? `lädt mit ${kw(d.power_w)}` : "lädt nicht";
  if (d.active) return `${kw(d.power_w)}`;
  if (d.status) return d.status;
  return d.on == null ? "wartet auf Überschuss" : "aus";
}

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });

/** Heating rod or switched device run by OpenAmpere itself. */
function OwnDeviceCard({ d, today, onChange }: { d: Device; today: number | undefined; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const mode = d.override?.mode ?? "auto";
  const setMode = async (value: "auto" | "off" | "boost") => {
    setBusy(true);
    try {
      await postJson(`/api/consumers/${d.id}/mode`, { mode: value, hours: value === "boost" ? 2 : undefined });
      onChange();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const temp = d.temperature_c, target = d.target_c;
  return (
    <div className="card device-card">
      <div className="device-card-head">
        <DeviceIcon kind={d.kind} />
        <div className="grow">
          <strong>{d.name}</strong>
          <div className="hint">{deviceStatus(d)}</div>
        </div>
        <div className="device-power">{kw(d.power_w)}</div>
      </div>
      {temp != null && (
        <div className="temp-row">
          <span>Wasser {num(temp, 1)} °C</span>
          {target != null && <span className="hint">Ziel {num(target, 0)} °C</span>}
          {target != null && (
            <div className="soc-track" role="img" aria-label={`Wassertemperatur ${temp} von ${target} Grad`}>
              <div className="fill heat" style={{ width: `${Math.max(0, Math.min(100, (temp / target) * 100))}%` }} />
            </div>
          )}
        </div>
      )}
      <Segmented value={mode} disabled={busy} onChange={(v) => void setMode(v)}
        options={[["auto", "Automatisch"], ["off", "Aus"], ["boost", d.kind === "heating_rod" ? "Volle Leistung" : "An"]]} />
      <p className="hint">{mode === "auto" ? "Läuft mit Solarüberschuss nach deinen Einstellungen."
        : mode === "off" ? "Bleibt aus, bis du wieder auf Automatisch stellst."
        : "Läuft 2 Stunden mit voller Leistung, auch mit Netzstrom. Danach wieder automatisch."}</p>
      {today != null && <p className="hint">Heute: {kwh(today)}</p>}
    </div>
  );
}

/** The home battery: what it does now and its most important settings. */
function BatteryCard({ snap }: { snap: Snapshot | null }) {
  const { data: settings } = useResource<BatteryState>("/api/battery/settings", 60_000);
  const { data: charging } = useResource<ChargingView>("/api/charging", 30_000);
  const power = snap?.battery_power ?? null;
  const state = power == null ? "–" : Math.abs(power) <= 30 ? "ruht" : power > 0 ? `entlädt ${kw(power)}` : `lädt ${kw(power)}`;
  return (
    <div className="card device-card">
      <button className="device-card-head as-link" onClick={() => navigate("devices/battery")}>
        <BatteryIcon size={44} soc={snap?.battery_soc ?? null} />
        <div className="grow">
          <strong>Speicher</strong>
          <div className="hint">{state}{settings?.work_mode ? ` · ${WORK_MODE_LABEL[settings.work_mode]}` : ""}</div>
        </div>
        <div className="device-power">{snap?.battery_soc != null ? `${num(snap.battery_soc, 0)} %` : "–"}</div>
      </button>
      {settings?.min_soc_on_grid != null && (
        <p className="hint">Notstrom-Reserve: {num(settings.min_soc_on_grid, 0)} %</p>
      )}
      <div className="button-row inline">
        <button className="link" onClick={() => navigate("devices/battery")}>Speicher einstellen</button>
        <button className="link" onClick={() => navigate("devices/charging")}>
          Laden aus dem Netz: {charging ? (charging.active ? "lädt gerade" : charging.settings.enabled ? "an" : "aus") : "…"}
        </button>
      </div>
    </div>
  );
}

const WORK_MODE_LABEL: Record<string, string> = { self_use: "Eigenverbrauch", feed_in_first: "Einspeisung bevorzugt",
  backup: "Notstromreserve", peak_shaving: "Spitzenlast begrenzen" };

export function DevicesTab({ page, snap }: { page: string | null; snap: Snapshot | null }) {
  const { data, reload } = useResource<DevicesView>("/api/devices", 5_000);
  const { data: evcc, setData: setEvcc } = useResource<EvccView>("/api/evcc", 10_000);
  const nav = { onBack: () => goBack("devices"), onNavigate: (p: string) => navigate(`more/${p}`) };
  if (page === "setup") return <ConsumersPage {...nav} />;
  if (page === "wallbox") return <WallboxPage {...nav} />;
  if (page === "battery") return <BatteryPage {...nav} />;
  if (page === "charging") return <ChargingPage {...nav} />;

  const own = (data?.devices ?? []).filter((d) => d.source === "openampere");
  const loadpoints = evcc?.state?.loadpoints ?? [];
  const noExtras = data && !own.length && !loadpoints.length;
  let content: ReactNode = null;
  if (noExtras) {
    content = (
      <div className="card">
        <p>Wallbox, Heizstab oder Wärmepumpe können deinen Sonnenstrom nutzen. Füge sie hier hinzu.</p>
        <div className="button-row">
          <Button onClick={() => navigate("devices/setup")}>Heizstab oder Gerät hinzufügen</Button>
          <Button variant="secondary" onClick={() => navigate("devices/wallbox")}>Wallbox mit evcc verbinden</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head"><h1>Geräte</h1></div>
      <ControlModeBar compact />
      <div className="section-title">Speicher</div>
      <BatteryCard snap={snap} />
      {content}
      {!!loadpoints.length && (
        <>
          <div className="section-title">Wallbox</div>
          {loadpoints.map((lp) => <WallboxCard key={lp.id} lp={lp} onChange={setEvcc} />)}
          <p className="hint small-credit">Gesteuert von <a href={EVCC_URL} target="_blank" rel="noreferrer">evcc</a></p>
        </>
      )}
      {evcc?.configured && evcc.error && <Notice kind="error">{evcc.error}</Notice>}
      {!!own.length && (
        <>
          <div className="section-title">Heizstab und weitere Geräte</div>
          {own.map((d) => <OwnDeviceCard key={d.key} d={d} today={data?.today_wh[d.key]} onChange={reload} />)}
        </>
      )}
      <div className="section-title">Einrichten</div>
      <div className="card menu">
        <MenuRow label="Heizstab und weitere Geräte" hint="my-PV, Shelly, Web-Adressen, Reihenfolge" onClick={() => navigate("devices/setup")} />
        <MenuRow label="Wallbox" hint={evcc?.configured ? "mit evcc verbunden" : "mit evcc verbinden"} onClick={() => navigate("devices/wallbox")} />
      </div>
    </div>
  );
}
