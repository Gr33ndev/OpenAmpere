import { useEffect, useState } from "react";
import type { Settings } from "./api";
import { postJson, putJson, useResource } from "./api";
import { num, timeZone } from "./format";
import type { PageProps } from "./SettingsPages";
import { Button, Field, LoadState, Notice, Segmented, Slider, SubPage, toast } from "./ui";

export type EvccLoadpoint = {
  id: number; title: string; heating: boolean; mode: "off" | "pv" | "minpv" | "now" | null; connected: boolean;
  charging: boolean; enabled: boolean; power_w: number; session_wh: number | null; session_solar_pct: number | null;
  vehicle_name: string | null; vehicle_title: string | null; soc: number | null; range_km: number | null;
  limit_soc: number | null; phases: number; pv_action: string | null; pv_remaining_s: number | null;
  remaining_s: number | null; plan_active: boolean; plan_time: string | null; plan_soc: number | null;
};
export type EvccView = {
  configured: boolean; url: string | null; error: string | null; updated: number | null;
  priority: "wallbox_first" | "devices_first";
  state: { version: string | null; site_title: string | null; loadpoints: EvccLoadpoint[] } | null;
};
type Session = { created: string | null; finished: string | null; loadpoint: string | null; vehicle: string | null;
  energy_kwh: number | null; duration_s: number | null; solar_pct: number | null; price: number | null };

export const EVCC_URL = "https://evcc.io";
const MODES: [NonNullable<EvccLoadpoint["mode"]>, string][] = [["off", "Aus"], ["pv", "Solar"], ["minpv", "Min + Solar"], ["now", "Sofort"]];
const MODE_HINT: Record<string, string> = {
  off: "Lädt nicht.",
  pv: "Lädt nur mit Solarüberschuss.",
  minpv: "Lädt immer mit kleiner Leistung, bei Sonne schneller.",
  now: "Lädt sofort mit voller Leistung, auch aus dem Netz.",
};

const kw = (w: number) => `${num(w / 1000, 1)} kW`;
const duration = (s: number | null) => {
  if (s == null || s <= 0) return null;
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
};
const clock = (iso: string | null) => (iso ? new Date(iso).toLocaleString("de-DE", { weekday: "short", hour: "2-digit",
  minute: "2-digit", timeZone: timeZone() }) : "");

function status(lp: EvccLoadpoint): string {
  if (!lp.connected) return lp.heating ? "nicht bereit" : "kein Fahrzeug angeschlossen";
  if (lp.charging) return lp.heating ? `heizt mit ${kw(lp.power_w)}` : `lädt mit ${kw(lp.power_w)}`;
  if (lp.pv_action === "enable" && lp.pv_remaining_s) return `startet in ${duration(lp.pv_remaining_s)} (genug Sonne)`;
  if (lp.mode === "pv" || lp.mode === "minpv") return "wartet auf Solarüberschuss";
  if (lp.soc != null && lp.limit_soc != null && lp.soc >= lp.limit_soc) return "Ziel erreicht";
  return lp.heating ? "bereit" : "angeschlossen";
}

/** One evcc charge point: shown on the dashboard and on the wallbox page. */
export function WallboxCard({ lp, onChange }: { lp: EvccLoadpoint; onChange: (view: EvccView) => void }) {
  const [limit, setLimit] = useState(lp.limit_soc ?? 80);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (lp.limit_soc != null) setLimit(lp.limit_soc); }, [lp.limit_soc]);
  const unit = lp.heating ? "°C" : "%";
  const plugged = lp.connected || lp.heating; // without a car the vehicle values are stale
  const send = async (action: string, value?: unknown) => {
    setBusy(true);
    try {
      const view = await postJson<EvccView & { dry_run?: boolean }>(`/api/evcc/loadpoints/${lp.id}`, { action, value });
      if (view.dry_run) toast("Testmodus: nur im Protokoll, nicht an evcc gesendet");
      onChange(view);
    } catch (e) {
      toast((e as Error).message, "error");
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
        <div className="soc-track" role="img" aria-label={`Ladestand ${lp.soc} %, Ziel ${lp.limit_soc ?? "–"} %`}>
          <div className="fill" style={{ width: `${Math.min(100, lp.soc)}%` }} />
          {lp.limit_soc != null && <div className="target" style={{ left: `${lp.limit_soc}%` }} />}
        </div>
      )}
      <Segmented value={lp.mode ?? "off"} disabled={busy} onChange={(m) => void send("mode", m)} options={MODES} />
      <p className="hint">{MODE_HINT[lp.mode ?? "off"]}</p>
      <Field label={lp.heating ? "Zieltemperatur" : "Laden bis"}>
        <Slider value={limit} min={lp.heating ? 30 : 20} max={lp.heating ? 90 : 100} step={lp.heating ? 1 : 5} unit={unit}
          onChange={setLimit} />
      </Field>
      {limit !== lp.limit_soc && <Button variant="secondary" busy={busy} onClick={() => void send("limit_soc", limit)}>Ziel übernehmen</Button>}
      <dl className="facts">
        {plugged && !!lp.session_wh && <><dt>Diesmal geladen</dt><dd>{num(lp.session_wh / 1000, 1)} kWh
          {lp.session_solar_pct != null ? ` · ${num(lp.session_solar_pct, 0)} % Sonne` : ""}</dd></>}
        {plugged && lp.range_km != null && <><dt>Reichweite</dt><dd>{num(lp.range_km, 0)} km</dd></>}
        {lp.charging && duration(lp.remaining_s) && <><dt>Fertig in</dt><dd>{duration(lp.remaining_s)}</dd></>}
        {lp.plan_active && lp.plan_time && <><dt>Ladeplan</dt><dd>{lp.plan_soc != null ? `${num(lp.plan_soc, 0)} % ` : ""}bis {clock(lp.plan_time)}</dd></>}
      </dl>
      {!lp.heating && lp.connected && lp.vehicle_name && <PlanForm lp={lp} busy={busy} send={send} />}
    </div>
  );
}

function PlanForm({ lp, busy, send }: { lp: EvccLoadpoint; busy: boolean; send: (action: string, value?: unknown) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [soc, setSoc] = useState(80);
  const [time, setTime] = useState("07:00");
  if (!open) {
    return (
      <div className="button-row inline">
        <button className="link" onClick={() => setOpen(true)}>Bis zu einer Uhrzeit laden</button>
        {lp.plan_active && <button className="link" onClick={() => void send("plan_delete")}>Ladeplan löschen</button>}
      </div>
    );
  }
  const target = () => {
    const [h, m] = time.split(":").map(Number);
    const d = new Date();
    d.setHours(h, m, 0, 0);
    if (d.getTime() < Date.now() + 15 * 60_000) d.setDate(d.getDate() + 1);
    return d.getTime() / 1000;
  };
  return (
    <div className="plan-form">
      <p className="hint">evcc lädt dann so, dass das Ziel zur Uhrzeit erreicht ist, möglichst mit Sonne oder günstigem Strom.</p>
      <div className="field-row">
        <Field label="Ziel"><Slider value={soc} min={20} max={100} step={5} unit="%" onChange={setSoc} /></Field>
        <Field label="Uhrzeit"><input className="input" type="time" value={time} onChange={(e) => setTime(e.target.value)} /></Field>
      </div>
      <div className="button-row inline">
        <Button busy={busy} onClick={async () => { await send("plan", { soc, time: target() }); setOpen(false); }}>Ladeplan setzen</Button>
        <button className="link" onClick={() => setOpen(false)}>Abbrechen</button>
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
      toast("Gespeichert");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(siteYaml(origin));
      toast("Konfiguration kopiert");
    } catch {
      toast("Kopieren nicht möglich. Bitte den Text markieren.", "error");
    }
  };
  const cars = view?.state?.loadpoints ?? [];

  return (
    <SubPage title="Wallbox einrichten" onBack={onBack}>
      <p className="hint">Die Wallbox steuert <a href={EVCC_URL} target="_blank" rel="noreferrer">evcc</a>, ein
        eigenständiges Open-Source-Projekt für Solarladen. evcc kennt sehr viele Wallboxen und Fahrzeuge. Bedienen
        kannst du die Wallbox unter „Geräte“, die Ladevorgänge findest du in der Auswertung. evcc bekommt von OpenAmpere
        die Werte von Netz, Solar und Speicher.</p>
      {!view && <LoadState error={error} onRetry={reload} />}

      {view?.configured && view.error && <Notice kind="error">{view.error}</Notice>}

      <div className="section-title">Verbindung zu evcc</div>
      <div className="card form">
        <Field label="Adresse von evcc" hint="Zum Beispiel http://192.168.178.20:7070 oder http://evcc:7070 (Docker)">
          <input className="input" value={url} placeholder="http://" onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label="Admin-Passwort von evcc (optional)"
          hint={settings?.secrets["evcc.password"]?.set ? "Gespeichert. Leer lassen, um es zu behalten." : "Nur nötig, wenn evcc für Änderungen eine Anmeldung verlangt."}>
          <input className="input" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button busy={busy} disabled={!settings || (url === settings.values["evcc.url"] && !password)}
          onClick={() => void save({ "evcc.url": url, ...(password ? { "evcc.password": password } : {}) })}>Speichern und verbinden</Button>
        {view?.state && <Notice kind="ok">Verbunden mit evcc {view.state.version ?? ""} · {cars.length} Ladepunkt{cars.length === 1 ? "" : "e"}</Notice>}
      </div>

      <p className="hint">Ob die Wallbox vor oder nach Speicher und Heizstab Sonnenstrom bekommt, stellst du unter „Geräte“
        in der Liste „Wer bekommt Sonnenstrom zuerst?“ ein. OpenAmpere überträgt das an evcc.</p>

      <div className="section-title">evcc einrichten</div>
      <div className="card form">
        <p className="hint">So nutzt evcc die Messwerte von OpenAmpere und braucht keine eigene Verbindung zum
          Wechselrichter. Der Wechselrichter erlaubt nur wenige gleichzeitige Verbindungen. Füge in evcc diese Zähler
          hinzu, entweder in der evcc.yaml oder in der Oberfläche von evcc als „benutzerdefiniertes Gerät“:</p>
        <pre className="code-block">{siteYaml(origin)}</pre>
        <Button variant="secondary" onClick={() => void copy()}>Konfiguration kopieren</Button>
        <p className="hint">Wallbox und Fahrzeug richtest du direkt in evcc ein. Die Anleitung dazu steht in der
          <a href="https://docs.evcc.io" target="_blank" rel="noreferrer"> Dokumentation von evcc</a>.</p>
      </div>

      <p className="hint">evcc wird von der evcc-Community entwickelt und steht unter MIT-Lizenz. Für manche Geräte
        verlangt evcc ein Sponsoring. OpenAmpere nutzt nur die offene Schnittstelle von evcc und enthält keinen Code
        von evcc.</p>
    </SubPage>
  );
}

/** Recent charging sessions from evcc, for the analysis page. */
export function EvccSessions() {
  const { data: view } = useResource<EvccView>("/api/evcc");
  const { data } = useResource<{ sessions: Session[] }>(view?.state ? "/api/evcc/sessions?limit=20" : null);
  if (!data?.sessions.length) return null;
  return (
    <details className="report-section">
      <summary>Ladevorgänge</summary>
      <div className="card">
        <ul className="sessions">
          {data.sessions.map((s, i) => (
            <li key={i}>
              <span><strong>{s.created ? new Date(s.created).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", timeZone: timeZone() }) : "–"}</strong>
                {" "}{s.vehicle ?? s.loadpoint ?? ""}</span>
              <span>{s.energy_kwh != null ? `${num(s.energy_kwh, 1)} kWh` : "–"}{s.solar_pct != null ? ` · ${num(s.solar_pct, 0)} % Sonne` : ""}</span>
            </li>
          ))}
        </ul>
        {view?.url && <a className="link" href={`${view.url}/api/sessions?format=csv&lang=de`}>Alle Ladevorgänge als CSV (aus evcc)</a>}
      </div>
    </details>
  );
}
