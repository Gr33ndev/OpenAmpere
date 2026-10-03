import { useState, type ReactNode } from "react";
import type { BatteryState, Device, DevicesView, Snapshot } from "./api";
import { postJson, putJson, useResource } from "./api";
import { ControlModeBar } from "./ControlMode";
import { kw, kwh, num, timeZone } from "./format";
import { BatteryIcon, CarIcon, HeaterIcon, HeatPumpIcon, PlugIcon } from "./icons";
import { BatteryPage, ChargingPage, type ChargingView } from "./SettingsPages";
import { goBack, navigate } from "./route";
import { Button, Notice, Segmented, Slider, toast } from "./ui";
import { EVCC_URL, WallboxCard, type EvccView } from "./WallboxPage";

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
  if (d.kind === "wallbox") {
    if (d.active) return `lädt mit ${kw(d.power_w)}`;
    return d.connected === false ? "kein Auto angeschlossen" : "angeschlossen, lädt gerade nicht";
  }
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

type OrderView = { items: { key: string; name: string; kind: Device["kind"] | "battery" }[]; order: string[];
  battery_soc: number; evcc_error?: string };

/** One list for the whole site: who gets solar power first. */
function SurplusOrder() {
  const { data, setData } = useResource<OrderView>("/api/surplus-order", 60_000);
  const [soc, setSoc] = useState<number | null>(null);
  if (!data || data.items.length < 2) return null;
  const save = async (keys: string[], batterySoc: number) => {
    try {
      const view = await putJson<OrderView>("/api/surplus-order", { order: keys, battery_soc: batterySoc });
      setData(view);
      if (view.evcc_error) toast(`In evcc nicht übernommen: ${view.evcc_error}`, "error");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const keys = data.items.map((i) => i.key);
  const move = (index: number, dir: -1 | 1) => {
    const next = [...keys];
    [next[index], next[index + dir]] = [next[index + dir], next[index]];
    void save(next, data.battery_soc);
  };
  return (
    <>
      <div className="section-title">Wer bekommt Sonnenstrom zuerst?</div>
      <div className="card order-list">
        {data.items.map((item, i) => (
          <div key={item.key} className="order-item">
            <div className="order-row">
              <span className="order-pos">{i + 1}</span>
              {item.kind === "battery" ? <BatteryIcon size={32} soc={60} /> : <DeviceIcon kind={item.kind} size={32} />}
              <strong className="grow">{item.name}</strong>
              <div className="order-buttons">
                <button aria-label={`${item.name} nach oben`} disabled={i === 0} onClick={() => move(i, -1)}>▲</button>
                <button aria-label={`${item.name} nach unten`} disabled={i === data.items.length - 1} onClick={() => move(i, 1)}>▼</button>
              </div>
            </div>
            {item.kind === "battery" && (
              <div className="order-extra">
                <Slider value={soc ?? data.battery_soc} min={0} max={100} step={5} unit="%"
                  onChange={setSoc} onCommit={(v) => { setSoc(null); void save(keys, v); }} />
                <span className="hint">Der Speicher wird bis zu diesem Ladestand geladen. Danach bekommen die Geräte darunter den Sonnenstrom.</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

export function DevicesTab({ page, snap }: { page: string | null; snap: Snapshot | null }) {
  const { data, reload } = useResource<DevicesView>("/api/devices", 5_000);
  const { data: evcc, setData: setEvcc } = useResource<EvccView>("/api/evcc", 10_000);
  const nav = { onBack: () => goBack("devices"), onNavigate: (p: string) => navigate(`more/${p}`) };
  if (page === "setup") { navigate("more/device-setup"); return null; } // moved to Mehr → Verbindung
  if (page === "wallbox") { navigate("more/wallbox"); return null; }
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
          <Button onClick={() => navigate("more/device-setup")}>Heizstab oder Gerät hinzufügen</Button>
          <Button variant="secondary" onClick={() => navigate("more/wallbox")}>Wallbox mit evcc verbinden</Button>
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
      <SurplusOrder />
      <p className="hint center">Geräte hinzufügen oder einrichten: <button className="link" onClick={() => navigate("more/connection")}>Mehr → Verbindung</button></p>
    </div>
  );
}
