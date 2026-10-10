// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState, type ReactNode } from "react";
import type { BatteryState, Device, DevicesView, Snapshot } from "./api";
import { postJson, putJson, useResource } from "./api";
import { ControlModeBar } from "./ControlMode";
import { kw, kwh, num, timeZone } from "./format";
import { LOCALE, t, tx } from "./i18n";
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
  if (d.override?.mode === "off") return t("devices.deviceStatus.switchedOffManually");
  if (d.override?.mode === "boost") {
    return d.override.until ? t("devices.deviceStatus.fullPowerUntil", { time: clock(d.override.until) }) : t("devices.deviceStatus.fullPower");
  }
  if (d.kind === "wallbox") {
    if (d.active) return t("devices.deviceStatus.charging", { power: kw(d.power_w) });
    return d.connected === false ? t("devices.deviceStatus.noCarPlugged") : t("devices.deviceStatus.pluggedNotCharging");
  }
  if (d.active) return `${kw(d.power_w)}`;
  if (d.status) return d.status;
  return d.on == null ? t("devices.deviceStatus.waitingSurplus") : t("common.offValue");
}

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });

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
          <span>{t("devices.ownDeviceCard.water", { temperature: num(temp, 1) })}</span>
          {target != null && <span className="hint">{t("devices.ownDeviceCard.target", { temperature: num(target, 0) })}</span>}
          {target != null && (
            <div className="soc-track" role="img" aria-label={t("devices.ownDeviceCard.temperatureLabel", { temperature: temp, target })}>
              <div className="fill heat" style={{ width: `${Math.max(0, Math.min(100, (temp / target) * 100))}%` }} />
            </div>
          )}
        </div>
      )}
      <Segmented value={mode} disabled={busy} onChange={(v) => void setMode(v)}
        options={[["auto", t("common.automatic")], ["off", t("common.off")], ["boost", d.kind === "heating_rod" ? t("devices.ownDeviceCard.fullPower") : t("common.on")]]} />
      <p className="hint">{mode === "auto" ? t("devices.ownDeviceCard.autoHint")
        : mode === "off" ? t("devices.ownDeviceCard.offHint")
        : t("devices.ownDeviceCard.fullPowerHint")}</p>
      {today != null && <p className="hint">{t("devices.ownDeviceCard.today", { energy: kwh(today) })}</p>}
    </div>
  );
}

/** The home battery: what it does now and its most important settings. */
function BatteryCard({ snap }: { snap: Snapshot | null }) {
  const { data: settings } = useResource<BatteryState>("/api/battery/settings", 60_000);
  const { data: charging } = useResource<ChargingView>("/api/charging", 30_000);
  const power = snap?.battery_power ?? null;
  const state = power == null ? "–" : Math.abs(power) <= 30 ? t("devices.batteryCard.idle")
    : power > 0 ? t("devices.batteryCard.discharging", { power: kw(power) }) : t("devices.batteryCard.charging", { power: kw(power) });
  return (
    <div className="card device-card">
      <button type="button" className="device-card-head as-link" onClick={() => navigate("devices/battery")}>
        <BatteryIcon size={44} soc={snap?.battery_soc ?? null} />
        <div className="grow">
          <strong>{t("common.battery")}</strong>
          <div className="hint">{state}{settings?.work_mode ? ` · ${WORK_MODE_LABEL[settings.work_mode]}` : ""}</div>
        </div>
        <div className="device-power">{snap?.battery_soc != null ? `${num(snap.battery_soc, 0)} %` : "–"}</div>
      </button>
      {settings?.min_soc_on_grid != null && (
        <p className="hint">{t("devices.batteryCard.backupReserve", { soc: num(settings.min_soc_on_grid, 0) })}</p>
      )}
      <div className="button-row inline">
        <button type="button" className="link" onClick={() => navigate("devices/battery")}>{t("devices.batteryCard.batterySettings")}</button>
        <button type="button" className="link" onClick={() => navigate("devices/charging")}>
          {t("devices.batteryCard.gridCharging", { state: charging ? (charging.active ? t("devices.batteryCard.chargingNow") : charging.settings.enabled ? t("common.onValue") : t("common.offValue")) : "…" })}
        </button>
      </div>
    </div>
  );
}

const WORK_MODE_LABEL: Record<string, string> = { self_use: t("common.selfConsumption"), feed_in_first: t("devices.workModeLabel.feedFirst"),
  backup: t("common.backupReserve"), peak_shaving: t("devices.workModeLabel.peakShaving") };

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
      if (view.evcc_error) toast(t("devices.surplusOrder.evccError", { error: view.evcc_error }), "error");
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
      <div className="section-title">{t("devices.surplusOrder.title")}</div>
      <div className="card order-list">
        {data.items.map((item, i) => (
          <div key={item.key} className="order-item">
            <div className="order-row">
              <span className="order-pos">{i + 1}</span>
              {item.kind === "battery" ? <BatteryIcon size={32} soc={60} /> : <DeviceIcon kind={item.kind} size={32} />}
              <strong className="grow">{item.name}</strong>
              <div className="order-buttons">
                <button type="button" aria-label={t("devices.surplusOrder.moveUp", { name: item.name })} disabled={i === 0} onClick={() => move(i, -1)}>▲</button>
                <button type="button" aria-label={t("devices.surplusOrder.moveDown", { name: item.name })} disabled={i === data.items.length - 1} onClick={() => move(i, 1)}>▼</button>
              </div>
            </div>
            {item.kind === "battery" && (
              <div className="order-extra">
                <Slider value={soc ?? data.battery_soc} min={0} max={100} step={5} unit="%"
                  onChange={setSoc} onCommit={(v) => { setSoc(null); void save(keys, v); }} />
                <span className="hint">{t("devices.surplusOrder.batteryHint")}</span>
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
        <p>{t("devices.devicesTab.emptyHint")}</p>
        <div className="button-row">
          <Button onClick={() => navigate("more/device-setup")}>{t("devices.devicesTab.addDevice")}</Button>
          <Button variant="secondary" onClick={() => navigate("more/wallbox")}>{t("devices.devicesTab.connectWallbox")}</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head"><h1>{t("common.devices")}</h1></div>
      <ControlModeBar compact />
      <div className="section-title">{t("common.battery")}</div>
      <BatteryCard snap={snap} />
      {content}
      {!!loadpoints.length && (
        <>
          <div className="section-title">{t("common.wallbox")}</div>
          {loadpoints.map((lp) => <WallboxCard key={lp.id} lp={lp} onChange={setEvcc} />)}
          <p className="hint small-credit">{tx("devices.devicesTab.controlledBy", { link: <a href={EVCC_URL} target="_blank" rel="noreferrer">evcc</a> })}</p>
        </>
      )}
      {evcc?.configured && evcc.error && <Notice kind="error">{evcc.error}</Notice>}
      {!!own.length && (
        <>
          <div className="section-title">{t("devices.devicesTab.otherDevicesTitle")}</div>
          {own.map((d) => <OwnDeviceCard key={d.key} d={d} today={data?.today_wh[d.key]} onChange={reload} />)}
        </>
      )}
      <SurplusOrder />
      <p className="hint center">{tx("devices.devicesTab.addOrSetUp", { link: <button type="button" className="link" onClick={() => navigate("more/connection")}>{t("common.more")} → {t("common.connection")}</button> })}</p>
    </div>
  );
}
