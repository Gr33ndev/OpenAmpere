// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useEffect, useState } from "react";
import type { Settings } from "./api";
import { postJson, putJson, useResource } from "./api";
import { num, plantTime, timeZone, todayIso } from "./format";
import { nextDay } from "./zoned";
import { lang, LOCALE, t, tx } from "./i18n";
import type { PageProps } from "./SettingsPages";
import { Button, copyText, Field, LoadState, Notice, Segmented, Slider, SubPage, toast } from "./ui";

export type EvccLoadpoint = {
  id: number; title: string; heating: boolean; mode: "off" | "pv" | "minpv" | "now" | null; connected: boolean;
  charging: boolean; enabled: boolean; power_w: number; session_wh: number | null; session_solar_pct: number | null;
  vehicle_name: string | null; vehicle_title: string | null; soc: number | null; range_km: number | null;
  limit_soc: number | null; phases: number; pv_action: string | null; pv_remaining_s: number | null;
  remaining_s: number | null; plan_active: boolean; plan_time: string | null; plan_soc: number | null;
  min_soc: number; vehicle_capacity_kwh: number | null;
};
export type EvccView = {
  configured: boolean; url: string | null; error: string | null; updated: number | null;
  priority: "wallbox_first" | "devices_first";
  state: { version: string | null; site_title: string | null; loadpoints: EvccLoadpoint[] } | null;
};
export type EvccSession = { created: string | null; finished: string | null; loadpoint: string | null; vehicle: string | null;
  energy_kwh: number | null; duration_s: number | null; solar_pct: number | null; price: number | null;
  odometer_km: number | null; soc_start: number | null; soc_end: number | null; added_range_km: number | null;
  cost_eur: number | null; grid_cost_eur: number | null };

export const EVCC_URL = "https://evcc.io";
const MODES: [NonNullable<EvccLoadpoint["mode"]>, string][] = [["off", t("common.off")], ["pv", t("devices.modes.solar")], ["minpv", t("devices.modes.minSolar")],
  ["now", t("devices.modes.fast")]];
const MODE_HINT: Record<string, string> = {
  off: t("devices.modeHint.off"),
  pv: t("devices.modeHint.pv"),
  minpv: t("devices.modeHint.minpv"),
  now: t("devices.modeHint.now"),
};

const kw = (w: number) => `${num(w / 1000, 2)} kW`;
const duration = (s: number | null) => {
  if (s == null || s <= 0) return null;
  const total = Math.round(s / 60), h = Math.floor(total / 60), m = total % 60; // never "1 h 60 min"
  return h ? `${h} h ${m} min` : `${m} min`;
};
const at = (s: number) => new Date(Date.now() + s * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit",
  timeZone: timeZone() });
const clock = (iso: string | null) => (iso ? new Date(iso).toLocaleString(LOCALE, { weekday: "short", hour: "2-digit",
  minute: "2-digit", timeZone: timeZone() }) : "");

function status(lp: EvccLoadpoint): string {
  if (!lp.connected) return lp.heating ? t("devices.status.notReady") : t("devices.status.noVehiclePlugged");
  if (lp.charging) return lp.heating ? t("devices.status.heating", { power: kw(lp.power_w) }) : t("devices.status.charging", { power: kw(lp.power_w) });
  if (lp.pv_action === "enable" && lp.pv_remaining_s) return t("devices.status.startsIn", { duration: duration(lp.pv_remaining_s)! });
  if (lp.mode === "pv" || lp.mode === "minpv") return t("devices.status.waitingSolarSurplus");
  if (lp.soc != null && lp.limit_soc != null && lp.soc >= lp.limit_soc) return t("devices.status.targetReached");
  return lp.heating ? t("devices.status.ready") : t("devices.status.plugged");
}

/** One evcc charge point: shown on the dashboard and on the wallbox page. */
export function WallboxCard({ lp, onChange }: { lp: EvccLoadpoint; onChange: (view: EvccView) => void }) {
  const [limit, setLimit] = useState(lp.limit_soc ?? 80);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (lp.limit_soc != null) setLimit(lp.limit_soc); }, [lp.limit_soc]);
  const unit = lp.heating ? "°C" : "%";
  const plugged = lp.connected || lp.heating; // without a car the vehicle values are stale
  /** true if evcc took the command, so a form can close */
  const send = async (action: string, value?: unknown): Promise<boolean> => {
    setBusy(true);
    try {
      const view = await postJson<EvccView & { dry_run?: boolean }>(`/api/evcc/loadpoints/${lp.id}`, { action, value });
      if (view.dry_run) toast(t("devices.wallboxCard.testModeLogged"));
      onChange(view);
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card wallbox">
      <div className="wallbox-head">
        <div>
          <strong>{lp.title}</strong>
          <div className="hint">{plugged && lp.vehicle_title ? `${lp.vehicle_title} · ` : ""}{status(lp)}</div>
        </div>
        {plugged && lp.soc != null && (
          <div className="wallbox-soc"><span>{num(lp.soc, 0)}</span><small>{unit}</small></div>
        )}
      </div>
      {plugged && lp.soc != null && !lp.heating && (
        <div className="soc-track" role="img" aria-label={t("devices.wallboxCard.socLabel", { soc: lp.soc, target: lp.limit_soc ?? "–" })}>
          <div className="fill" style={{ width: `${Math.min(100, lp.soc)}%` }} />
          {lp.limit_soc != null && <div className="target" style={{ left: `${lp.limit_soc}%` }} />}
        </div>
      )}
      <Segmented value={lp.mode ?? "off"} disabled={busy} onChange={(m) => void send("mode", m)} options={MODES} />
      <p className="hint">{MODE_HINT[lp.mode ?? "off"]}</p>
      <Field label={lp.heating ? t("devices.wallboxCard.targetTemperature") : t("devices.wallboxCard.chargeUntil")}>
        <Slider value={limit} min={lp.heating ? 30 : 20} max={lp.heating ? 90 : 100} step={lp.heating ? 1 : 5} unit={unit}
          onChange={setLimit} />
      </Field>
      {limit !== lp.limit_soc && <Button variant="secondary" busy={busy} onClick={() => void send("limit_soc", limit)}>{t("devices.wallboxCard.applyTarget")}</Button>}
      <dl className="facts">
        {plugged && !!lp.session_wh && <><dt>{t("devices.wallboxCard.chargedThisSession")}</dt><dd>{num(lp.session_wh / 1000, 2)} kWh
          {lp.session_solar_pct != null ? ` · ${t("devices.wallboxCard.solarShare", { percent: num(lp.session_solar_pct, 0) })}` : ""}</dd></>}
        {plugged && lp.range_km != null && <><dt>{t("devices.wallboxCard.range")}</dt><dd>{num(lp.range_km, 0)} km</dd></>}
        {lp.charging && duration(lp.remaining_s) && <><dt>{lp.heating ? t("devices.wallboxCard.doneAt") : t("devices.wallboxCard.targetReachedAt")}</dt>
          <dd>{t("devices.wallboxCard.timeIn", { time: at(lp.remaining_s!), duration: duration(lp.remaining_s)! })}</dd></>}
        {lp.plan_active && lp.plan_time && <><dt>{t("devices.wallboxCard.chargingPlan")}</dt><dd>{lp.plan_soc != null
          ? t("devices.wallboxCard.planSocTime", { soc: num(lp.plan_soc, 0), time: clock(lp.plan_time) }) : t("devices.wallboxCard.planTime", { time: clock(lp.plan_time) })}</dd></>}
      </dl>
      {!lp.heating && lp.vehicle_name && <MinSoc lp={lp} busy={busy} send={send} />}
      {!lp.heating && lp.connected && lp.vehicle_name && <PlanForm lp={lp} busy={busy} send={send} />}
    </div>
  );
}

/** Minimum charge of the car (an evcc vehicle setting): up to it, evcc charges right away, also from the grid. */
function MinSoc({ lp, busy, send }: { lp: EvccLoadpoint; busy: boolean; send: (action: string, value?: unknown) => Promise<boolean> }) {
  const [value, setValue] = useState(lp.min_soc);
  useEffect(() => setValue(lp.min_soc), [lp.min_soc]);
  return (
    <details className="advanced">
      <summary>{t("devices.minSoc.summary", { value: lp.min_soc ? `${num(lp.min_soc, 0)} %` : t("common.offValue") })}</summary>
      <p className="hint">{t("devices.minSoc.hint")}</p>
      <Slider value={value} min={0} max={50} step={5} unit="%" onChange={setValue} />
      {value !== lp.min_soc && <Button variant="secondary" busy={busy} onClick={() => void send("min_soc", value)}>
        {value ? t("devices.minSoc.apply") : t("devices.minSoc.turnOff")}</Button>}
    </details>
  );
}

function PlanForm({ lp, busy, send }: { lp: EvccLoadpoint; busy: boolean; send: (action: string, value?: unknown) => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [soc, setSoc] = useState(80);
  const [time, setTime] = useState("07:00");
  if (!open) {
    return (
      <div className="button-row inline">
        <button type="button" className="link" onClick={() => setOpen(true)}>{t("devices.planForm.title")}</button>
        {lp.plan_active && <button type="button" className="link" onClick={() => void send("plan_delete")}>{t("devices.planForm.deletePlan")}</button>}
      </div>
    );
  }
  // the time is meant in the plant's time zone, like everything the app shows (also from a phone abroad)
  const target = () => {
    const [h, m] = time.split(":").map(Number);
    const day = todayIso();
    const today = plantTime(day, h, m);
    return today * 1000 < Date.now() + 15 * 60_000 ? plantTime(nextDay(day), h, m) : today;
  };
  return (
    <div className="plan-form">
      <p className="hint">{t("devices.planForm.hint")}</p>
      <div className="field-row">
        <Field label={t("devices.planForm.target")}><Slider value={soc} min={20} max={100} step={5} unit="%" onChange={setSoc} /></Field>
        <Field label={t("devices.planForm.time")}><input className="input" type="time" value={time} onChange={(e) => setTime(e.target.value)} /></Field>
      </div>
      <div className="button-row inline">
        <Button busy={busy} onClick={async () => { if (await send("plan", { soc, time: target() })) setOpen(false); }}>{t("devices.planForm.setPlan")}</Button>
        <button type="button" className="link" onClick={() => setOpen(false)}>{t("common.cancel")}</button>
      </div>
    </div>
  );
}

function siteYaml(origin: string): string {
  const meter = (name: string, power: string, extra = "") => `  - name: ${name}
    type: custom
    power:
      source: http
      uri: ${origin}/api/evcc/site
      jq: .${power}${extra}`;
  return `meters:
${meter("openampere_grid", "grid_power", `
    energy:
      source: http
      uri: ${origin}/api/evcc/site
      jq: .grid_import_kwh`)}
${meter("openampere_pv", "pv_power")}
${meter("openampere_battery", "battery_power", `
    soc:
      source: http
      uri: ${origin}/api/evcc/site
      jq: .battery_soc`)}

site:
  meters:
    grid: openampere_grid
    pv: [openampere_pv]
    battery: [openampere_battery]`;
}

export function WallboxPage({ onBack }: PageProps) {
  const { data: settings, reload: reloadSettings } = useResource<Settings>("/api/settings");
  const { data: view, error, reload } = useResource<EvccView>("/api/evcc?refresh=true", 10_000);
  const [url, setUrl] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (settings) setUrl(settings.values["evcc.url"]); }, [settings]);
  const origin = window.location.origin;

  const save = async (changes: Record<string, unknown>) => {
    setBusy(true);
    try {
      await putJson("/api/settings", { ...changes, _revision: settings?.revision });
      reloadSettings();
      reload();
      toast(t("common.saved"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const [showYaml, setShowYaml] = useState(false);
  const copy = async () => {
    if (await copyText(siteYaml(origin))) {
      toast(t("devices.wallboxPage.configurationCopied"));
    } else {
      setShowYaml(true);
      toast(t("devices.wallboxPage.copyFailed"), "error");
    }
  };
  const cars = view?.state?.loadpoints ?? [];

  return (
    <SubPage title={t("devices.wallboxPage.title")} onBack={onBack}>
      <p className="hint">{tx("devices.wallboxPage.intro", { link: <a href={EVCC_URL} target="_blank" rel="noreferrer">evcc</a> })}</p>
      {!view && <LoadState error={error} onRetry={reload} />}
      {view?.state && <Notice kind="ok">{t("devices.wallboxPage.connected", { version: view.state.version ?? "" })} ·{" "}
        {t("devices.wallboxPage.chargePoints", { count: cars.length })}</Notice>}
      {view?.configured && view.error && <Notice kind="error">{view.error}</Notice>}

      <ol className="steps-list">
        <li>
          <strong>{t("devices.wallboxPage.installStep")}</strong>
          <p className="hint">{tx("devices.wallboxPage.installHint",
            { link: <a href="https://docs.evcc.io" target="_blank" rel="noreferrer">{t("devices.wallboxPage.evccDocs")}</a> })}</p>
        </li>
        <li>
          <strong>{t("devices.wallboxPage.meterStep")}</strong>
          <p className="hint">{t("devices.wallboxPage.meterHint")}</p>
          <Button variant="secondary" onClick={() => void copy()}>{t("devices.wallboxPage.copyConfiguration")}</Button>
          <details className="advanced" open={showYaml} onToggle={(e) => setShowYaml(e.currentTarget.open)}>
            <summary>{t("devices.wallboxPage.showConfiguration")}</summary>
            <pre className="code-block">{siteYaml(origin)}</pre>
          </details>
        </li>
        <li>
          <strong>{t("devices.wallboxPage.connectStep")}</strong>
          <div className="card form">
            <Field label={t("devices.wallboxPage.evccAddress")} hint={t("devices.wallboxPage.addressHint", { local: "http://localhost:7070", example: "http://192.168.178.20:7070" })}>
              <input className="input" value={url} placeholder="http://" onChange={(e) => setUrl(e.target.value)} />
            </Field>
            <details className="advanced">
              <summary>{t("common.advanced")}</summary>
              <Field label={t("devices.wallboxPage.evccAdminPassword")}
                hint={settings?.secrets["evcc.password"]?.set ? t("devices.wallboxPage.savedKeep") : t("devices.wallboxPage.passwordHint")}>
                <input className="input" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
            </details>
            <Button busy={busy} disabled={!settings || (url === settings.values["evcc.url"] && !password)}
              onClick={() => void save({ "evcc.url": url, ...(password ? { "evcc.password": password } : {}) })}>{t("devices.wallboxPage.saveAndConnect")}</Button>
          </div>
        </li>
      </ol>

      <p className="hint">{t("devices.wallboxPage.priorityHint")}</p>
      <p className="hint">{t("devices.wallboxPage.evccCredit")}</p>
    </SubPage>
  );
}

/** Recent charging sessions from evcc, for the analysis page. */
export function EvccSessions() {
  const { data: view } = useResource<EvccView>("/api/evcc");
  const { data } = useResource<{ sessions: EvccSession[] }>(view?.state ? "/api/evcc/sessions?limit=20" : null);
  if (!data?.sessions.length) return null;
  return (
    <>
      <div className="section-title">{t("devices.evccSessions.title")}</div>
      <div className="card">
        <ul className="sessions">
          {data.sessions.map((s, i) => (
            <li key={i}>
              <span><strong>{s.created ? new Date(s.created).toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit", timeZone: timeZone() }) : "–"}</strong>
                {" "}{s.vehicle ?? s.loadpoint ?? ""}</span>
              <span>{s.energy_kwh != null ? `${num(s.energy_kwh, 2)} kWh` : "–"}{s.solar_pct != null ? ` · ${t("devices.evccSessions.solarShare", { percent: num(s.solar_pct, 0) })}` : ""}</span>
            </li>
          ))}
        </ul>
        {view?.url && <a className="link" href={`${view.url}/api/sessions?format=csv&lang=${lang()}`}>{t("devices.evccSessions.downloadCsv")}</a>}
      </div>
    </>
  );
}
