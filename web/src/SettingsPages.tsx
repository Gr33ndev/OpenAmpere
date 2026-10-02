import { useEffect, useState } from "react";
import type { BatterySettings, CloudImportState, ExportLimit, SettingKey, Settings, Snapshot, Status } from "./api";
import { activeInputs, postJson, putJson, PV_INPUT_COLORS, useResource } from "./api";
import { ISSUES_URL, LICENSES_DATA_URL, REPO_URL } from "./links";
import { kw, num } from "./format";
import { Chevron } from "./icons";
import { ConnectionForm } from "./Setup";
import { Button, Checkbox, Dialog, Field, LoadState, Notice, Segmented, Slider, SubPage, SwitchRow, toast } from "./ui";

type PageProps = { onBack: () => void; onNavigate?: (page: string) => void };

/** Loads settings and saves partial changes. */
function useSettings() {
  const { data, setData, error, reload } = useResource<Settings>("/api/settings");
  const save = async (changes: Partial<Settings["values"]> & { "cloud.api_key"?: string }) => {
    try {
      setData(await putJson<Settings>("/api/settings", changes));
      toast("Gespeichert");
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    }
  };
  const locked = (key: SettingKey | "cloud.api_key") => data?.locked.includes(key) ?? false;
  return { settings: data?.values ?? null, secrets: data?.secrets ?? null, locked, lockedKeys: data?.locked ?? [],
           save, error, reload };
}

// ---------------------------------------------------------------------------

const WORK_MODES: { id: NonNullable<BatterySettings["work_mode"]>; label: string; hint: string }[] = [
  { id: "self_use", label: "Eigenverbrauch", hint: "Solarstrom zuerst im Haus nutzen, Überschuss speichern. Empfohlen." },
  { id: "feed_in_first", label: "Einspeisung bevorzugen", hint: "Überschuss zuerst ins Netz, Speicher wird nicht entladen." },
  { id: "backup", label: "Notstromreserve", hint: "Speicher wird nur geladen und für Stromausfälle voll gehalten." },
  { id: "peak_shaving", label: "Spitzenlast begrenzen", hint: "Speicher deckt nur hohe Verbrauchsspitzen." },
];

export function BatteryPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data: current, error, reload } = useResource<BatterySettings>("/api/battery/settings");
  const [form, setForm] = useState<BatterySettings | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (current) setForm(current); }, [current]);

  const control = status?.control;
  const deviceSupportsControl = status?.device?.supports_control ?? true;
  const editable = !!control?.enabled && deviceSupportsControl;
  const changed = form && current && JSON.stringify(form) !== JSON.stringify(current);
  const set = (patch: Partial<BatterySettings>) => setForm((f) => (f ? { ...f, ...patch } : f));

  const save = async () => {
    if (!form || !current) return;
    const changes = Object.fromEntries(Object.entries(form).filter(([k, v]) => current[k as keyof BatterySettings] !== v));
    setBusy(true);
    try {
      const r = await putJson<{ dry_run: boolean; written: object; result?: string }>("/api/battery/settings", changes);
      toast(r.dry_run ? "Probemodus: Änderung wurde nur protokolliert" : r.result === "ok" ? "Am Wechselrichter gespeichert" : r.result ?? "Gespeichert");
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Speicher & Notstrom" onBack={onBack}>
      {!deviceSupportsControl && (
        <Notice kind="info">Nur Anzeige – für {status?.device?.manufacturer}-Geräte kann OpenAmpere Einstellungen noch nicht ändern.</Notice>
      )}
      {deviceSupportsControl && !editable && (
        <Notice kind="info">
          Nur Anzeige – die Steuerung ist ausgeschaltet.{" "}
          <button className="link" onClick={() => onNavigate?.("control")}>Steuerung freigeben</button>
        </Notice>
      )}
      {editable && control?.dry_run && (
        <Notice kind="warn">Probemodus aktiv: Änderungen werden nur protokolliert, nicht an den Wechselrichter gesendet.</Notice>
      )}
      {!form && (error ? <LoadState error={error} onRetry={reload} /> : <p className="hint">Lese Einstellungen vom Wechselrichter …</p>)}

      {form && (
        <>
          <div className="section-title">Notstrom-Reserve</div>
          <div className="card form">
            <p className="hint">Dieser Teil des Speichers bleibt im Normalbetrieb immer geladen, damit bei einem Stromausfall Energie zur Verfügung steht.</p>
            <Slider value={form.min_soc_on_grid ?? 10} min={10} max={100} unit="%" disabled={!editable}
              onChange={(v) => set({ min_soc_on_grid: v })} />
          </div>

          <div className="section-title">Ladegrenzen</div>
          <div className="card form">
            <Field label="Maximaler Ladestand" hint="Bis zu diesem Wert wird der Speicher geladen.">
              <Slider value={form.max_soc ?? 100} min={20} max={100} unit="%" disabled={!editable}
                onChange={(v) => set({ max_soc: v })} />
            </Field>
            <Field label="Entladegrenze bei Stromausfall" hint="Im Notstrombetrieb wird der Speicher nicht weiter entladen.">
              <Slider value={form.min_soc ?? 10} min={10} max={100} unit="%" disabled={!editable}
                onChange={(v) => set({ min_soc: v })} />
            </Field>
          </div>

          <div className="section-title">Betriebsmodus</div>
          <div className="card choices">
            {WORK_MODES.map((m) => (
              <button key={m.id} className={`choice ${form.work_mode === m.id ? "active" : ""}`} disabled={!editable}
                onClick={() => set({ work_mode: m.id })}>
                <span className="radio" />
                <span><strong>{m.label}</strong><span className="meta">{m.hint}</span></span>
              </button>
            ))}
          </div>

          {editable && <Button onClick={save} busy={busy} disabled={!changed}>Übernehmen</Button>}
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function TariffPage({ onBack }: PageProps) {
  const { settings, locked, save, error, reload } = useSettings();
  const [price, setPrice] = useState("");
  const [feedIn, setFeedIn] = useState("");
  useEffect(() => {
    if (settings) {
      setPrice(String(settings["tariff.electricity_price_ct"]).replace(".", ","));
      setFeedIn(String(settings["tariff.feed_in_ct"]).replace(".", ","));
    }
  }, [settings]);
  const parseNumber = (s: string) => Number(s.replace(",", "."));

  return (
    <SubPage title="Stromtarif" onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <div className="card form">
        <Field label="Strompreis (brutto)" hint="Was du pro Kilowattstunde aus dem Netz bezahlst." locked={locked("tariff.electricity_price_ct")}>
          <div className="input-unit">
            <input className="input" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
            <span>ct/kWh</span>
          </div>
        </Field>
        <Field label="Einspeisevergütung" hint="Was du pro eingespeister Kilowattstunde erhältst (EEG)." locked={locked("tariff.feed_in_ct")}>
          <div className="input-unit">
            <input className="input" inputMode="decimal" value={feedIn} onChange={(e) => setFeedIn(e.target.value)} />
            <span>ct/kWh</span>
          </div>
        </Field>
      </div>
      <p className="hint">Diese Werte werden für die Berechnung deiner Ersparnis verwendet.</p>
      <Button disabled={!settings || Number.isNaN(parseNumber(price)) || Number.isNaN(parseNumber(feedIn))}
        onClick={() => save({ "tariff.electricity_price_ct": parseNumber(price), "tariff.feed_in_ct": parseNumber(feedIn) })}>
        Speichern
      </Button>
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function ConnectionPage({ onBack }: PageProps) {
  const { settings, locked, lockedKeys, save, error, reload } = useSettings();
  const { data: status } = useResource<Status>("/api/status", 5000);
  const [pollInterval, setPollInterval] = useState(10);
  const [timeout, setTimeoutValue] = useState(3);
  useEffect(() => {
    if (!settings) return;
    setPollInterval(settings["inverter.poll_interval"]);
    setTimeoutValue(settings["inverter.timeout"]);
  }, [settings]);

  return (
    <SubPage title="Verbindung" onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <Notice kind={status?.connected ? "ok" : "warn"}>
        {status?.connected
          ? `Verbunden mit ${status.device?.manufacturer} ${status.device?.model}`
          : `Nicht verbunden${status?.last_error ? `: ${status.last_error}` : ""}`}
      </Notice>
      {settings && (
        <ConnectionForm
          key={settings["inverter.host"]}
          initial={{ host: settings["inverter.host"], port: settings["inverter.port"], unit: settings["inverter.unit"],
            driver: settings["inverter.driver"] }}
          locked={lockedKeys}
          onSaved={() => toast("Gespeichert – verbinde neu …")}
        />
      )}

      {settings && (
        <>
          <div className="section-title">Erweitert</div>
          <div className="card form">
            <Field label="Abfrage alle" hint="Wie oft der Wechselrichter abgefragt wird. 10 Sekunden sind ein guter Wert." locked={locked("inverter.poll_interval")}>
              <Slider value={pollInterval} min={5} max={60} unit="s" onChange={setPollInterval} />
            </Field>
            <Field label="Zeitlimit pro Anfrage" locked={locked("inverter.timeout")}
              hint="Hängt der Wechselrichter hinter einem Modbus-Proxy oder im WLAN, kann ein höherer Wert Verbindungsabbrüche vermeiden.">
              <Slider value={timeout} min={1} max={30} unit="s" onChange={setTimeoutValue} />
            </Field>
            <Button variant="secondary"
              disabled={pollInterval === settings["inverter.poll_interval"] && timeout === settings["inverter.timeout"]}
              onClick={() => save({ "inverter.poll_interval": pollInterval, "inverter.timeout": timeout })}>Speichern</Button>
            {(status?.device?.driver ?? settings["inverter.driver"]) === "foxess" && (<>
            <Field label="FoxESS-Registerkarte" hint="Nur ändern, wenn die automatische Erkennung falsch liegt." locked={locked("inverter.register_map")}>
              <select className="input" value={settings["inverter.register_map"]}
                onChange={(e) => save({ "inverter.register_map": e.target.value })}>
                <option value="auto">Automatisch erkennen</option>
                <option value="foxess_h3_new">FoxESS H3 – neuere Firmware / Smart / Pro</option>
                <option value="foxess_h3_legacy">FoxESS H3 – ältere Firmware</option>
              </select>
            </Field>
            <Field label="Leseverfahren" hint="Modbus-Funktionscode für Messwerte." locked={locked("inverter.read_function")}>
              <select className="input" value={settings["inverter.read_function"]}
                onChange={(e) => save({ "inverter.read_function": e.target.value })}>
                <option value="auto">Automatisch</option>
                <option value="input">Input-Register (FC04)</option>
                <option value="holding">Holding-Register (FC03)</option>
              </select>
            </Field>
            </>)}
          </div>
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

type LogEntry = { ts: number; action: string; details: { from: Record<string, unknown>; to: Record<string, unknown> }; dry_run: boolean; result: string };

export function ControlPage({ onBack }: PageProps) {
  const { settings, locked, save, error, reload: reloadSettings } = useSettings();
  const { data: log, reload } = useResource<{ entries: LogEntry[] }>("/api/control/log");
  const [confirm, setConfirm] = useState<null | "enable" | "live">(null);
  if (!settings) return <SubPage title="Steuerung" onBack={onBack}><LoadState error={error} onRetry={reloadSettings} /></SubPage>;
  const enabled = settings["control.enabled"];
  const dryRun = settings["control.dry_run"];

  return (
    <SubPage title="Steuerung" onBack={onBack}>
      <div className="card form">
        <SwitchRow label="Steuerung erlauben" checked={enabled} disabled={locked("control.enabled")}
          hint="Erlaubt OpenAmpere, Einstellungen am Wechselrichter zu ändern (z. B. Notstrom-Reserve)."
          onChange={(v) => (v ? setConfirm("enable") : save({ "control.enabled": false }))} />
        <SwitchRow label="Probemodus" checked={dryRun} disabled={!enabled || locked("control.dry_run")}
          hint="Änderungen werden nur protokolliert und nicht gesendet. Zum gefahrlosen Ausprobieren."
          onChange={(v) => (v ? save({ "control.dry_run": true }) : setConfirm("live"))} />
      </div>
      <Notice kind="info">
        Solange ein anderer Energiemanager (z. B. die bisherige Smartbox) angeschlossen ist, kann er Einstellungen wieder überschreiben.
        Prüfe nach Änderungen, ob sie erhalten bleiben.
      </Notice>

      <div className="section-title">Protokoll</div>
      <div className="card">
        {!log?.entries.length && <p className="hint">Noch keine Änderungen.</p>}
        {log?.entries.map((e) => (
          <div className="log-row" key={e.ts}>
            <div className="meta">{new Date(e.ts * 1000).toLocaleString("de-DE")}{e.dry_run && " · Probemodus"}</div>
            <div>{Object.entries(e.details.to).map(([k, v]) => `${k}: ${e.details.from[k] ?? "–"} → ${v}`).join(", ")}</div>
            <div className="meta">{e.result}</div>
          </div>
        ))}
        {!!log?.entries.length && <button className="link" onClick={reload}>Aktualisieren</button>}
      </div>

      {confirm === "enable" && (
        <Dialog title="Steuerung erlauben?" confirm="Erlauben"
          onCancel={() => setConfirm(null)}
          onConfirm={() => { setConfirm(null); void save({ "control.enabled": true, "control.dry_run": true }); }}>
          <p>OpenAmpere darf dann Einstellungen deines Wechselrichters ändern. Zur Sicherheit startet die Steuerung im <strong>Probemodus</strong> – es wird noch nichts gesendet.</p>
        </Dialog>
      )}
      {confirm === "live" && (
        <Dialog title="Probemodus beenden?" confirm="Ja, wirklich senden" danger
          onCancel={() => setConfirm(null)}
          onConfirm={() => { setConfirm(null); void save({ "control.dry_run": false }); }}>
          <p>Änderungen werden ab jetzt direkt an den Wechselrichter gesendet. Falsche Einstellungen können dazu führen, dass der Speicher nicht wie gewohnt arbeitet.</p>
          <p>Im Zweifel: Werte notieren, bevor du sie änderst.</p>
        </Dialog>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export type Theme = "auto" | "light" | "dark";

export function applyTheme(theme: Theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

export function storedTheme(): Theme {
  try {
    const value = localStorage.getItem("openampere.theme");
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    return "auto";
  }
}

export function AppearancePage({ onBack }: PageProps) {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const change = (value: Theme) => {
    setTheme(value);
    applyTheme(value);
    try { localStorage.setItem("openampere.theme", value); } catch { /* private mode */ }
  };
  return (
    <SubPage title="Darstellung" onBack={onBack}>
      <div className="card form">
        <Field label="Design" hint="„Automatisch“ folgt der Einstellung deines Geräts. Gilt nur für dieses Gerät.">
          <Segmented value={theme} onChange={change} options={[["auto", "Automatisch"], ["light", "Hell"], ["dark", "Dunkel"]]} />
        </Field>
      </div>
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function DataPage({ onBack }: PageProps) {
  const { settings, locked, save, error, reload } = useSettings();
  const [days, setDays] = useState(30);
  useEffect(() => { if (settings) setDays(settings["storage.raw_retention_days"]); }, [settings]);

  return (
    <SubPage title="Daten & Sicherung" onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <div className="card form">
        <Field label="Detaildaten aufbewahren" locked={locked("storage.raw_retention_days")}
          hint="Messwerte im Sekundenbereich für die Leistungskurve. Viertelstunden- und Tageswerte bleiben immer erhalten.">
          <Slider value={days} min={7} max={365} unit="Tage" onChange={setDays} />
        </Field>
        <Button variant="secondary" disabled={!settings || days === settings["storage.raw_retention_days"]}
          onClick={() => save({ "storage.raw_retention_days": days })}>Speichern</Button>
      </div>
      <CloudImportCard />
      <div className="card form">
        <h2>Sicherung</h2>
        <p className="hint">Lädt die komplette Datenbank mit allen Messwerten und Einstellungen herunter. Bewahre die Datei sicher auf.</p>
        <a className="btn secondary" href="/api/backup" download>Datensicherung herunterladen</a>
      </div>
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function AboutPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  return (
    <SubPage title="Über OpenAmpere" onBack={onBack}>
      <div className="card">
        <dl className="facts">
          <dt>Version</dt><dd>{status?.version ?? "–"}</dd>
        </dl>
      </div>
      <div className="card">
        <p>OpenAmpere ist ein unabhängiges Community-Projekt für Solaranlagen mit Batteriespeicher. Es läuft komplett lokal und braucht keine Cloud.</p>
        <p className="hint">Alle genannten Produktnamen und Marken gehören ihren jeweiligen Inhabern. Rechtliche Hinweise und Hintergrund: siehe README im Quellcode.</p>
        <p className="hint">FoxESS-Registerdefinitionen basieren auf foxess_modbus (MIT-Lizenz).</p>
      </div>
      <div className="card menu">
        <a className="menu-row" href={REPO_URL} target="_blank" rel="noopener noreferrer">
          <span>Quellcode auf GitHub<span className="menu-hint">github.com/Gr33ndev/OpenAmpere</span></span><span aria-hidden>↗</span>
        </a>
        <a className="menu-row" href={ISSUES_URL} target="_blank" rel="noopener noreferrer">
          <span>Fehler melden &amp; Ideen<span className="menu-hint">GitHub Issues</span></span><span aria-hidden>↗</span>
        </a>
        <button className="menu-row" onClick={() => onNavigate?.("licenses")}>
          <span>Open-Source-Lizenzen<span className="menu-hint">Verwendete Komponenten und ihre Lizenzen</span></span><Chevron />
        </button>
      </div>
      <p className="hint center">OpenAmpere ist freie Software unter der MIT-Lizenz.</p>
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

function duration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h} Std. ${m} Min.` : `${m} Min.`;
}

function CloudImportCard() {
  const { secrets, locked, save } = useSettings();
  const { data: job, reload } = useResource<CloudImportState>("/api/import/cloud", 5000);
  const [key, setKey] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [upload, setUpload] = useState<string | null>(null);
  const keyInfo = secrets?.["cloud.api_key"];
  const keyLocked = locked("cloud.api_key");
  const showKeyForm = !keyLocked && (editing || (keyInfo && !keyInfo.set));

  const saveKey = async (value: string) => {
    if (await save({ "cloud.api_key": value })) {
      setKey("");
      setEditing(false);
      reload();
    }
  };
  const action = async (path: string) => {
    setBusy(true);
    try {
      await postJson(path, {});
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const uploadZip = async (file: File) => {
    setUpload("Lese Datei …");
    try {
      const response = await fetch("/api/import/cloud/file", { method: "POST", body: file });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? response.statusText);
      setUpload(`${data.days} Tage gelesen, ${data.inserted} Viertelstunden übernommen.`);
      toast("Import abgeschlossen");
    } catch (e) {
      setUpload(null);
      toast((e as Error).message, "error");
    }
  };

  const total = job?.days_total ?? 0;
  const done = (job?.work_done ?? 0) + (job?.soc_done ?? 0);
  const progress = total ? done / (2 * total) : 0;
  const phaseText = !job?.phase ? "Verbinde mit der EKD-Cloud …"
    : job.phase === "search" ? "Suche den Beginn deiner Aufzeichnungen …"
    : job?.phase === "soc" ? `Ladestand: ${job.soc_done} von ${total} Tagen`
    : `Energiedaten: ${job?.work_done ?? 0} von ${total} Tagen`;

  return (
    <>
      <div className="section-title">Verlauf aus der EKD-Cloud</div>
      <div className="card form">
        <p className="hint">
          Hast du deine Anlage bisher mit der App „Ampere.IQ“ von EKD genutzt? Dann kannst du deinen bisherigen Verlauf aus der
          EKD-Cloud übernehmen, solange diese noch läuft. Den API-Schlüssel erzeugst du in der Ampere.IQ-App unter{" "}
          <strong>Mehr → Konfiguration API-Zugang</strong>.
        </p>
        <Notice kind="info">
          <strong>Hinweis:</strong> OpenAmpere ist ein unabhängiges Projekt und hat nichts mit der Energiekonzepte Deutschland
          GmbH (EKD) zu tun. Es wurde von EKD weder beauftragt noch autorisiert. Der Import nutzt ausschließlich die öffentliche
          Kunden-API der EKD-Cloud mit deinem persönlichen Schlüssel. „EKD“ und „Ampere.IQ“ sind Bezeichnungen ihrer Inhaber.
        </Notice>

        {showKeyForm ? (
          <form className="field" onSubmit={(e) => { e.preventDefault(); void saveKey(key.trim()); }}>
            <span className="field-label">API-Schlüssel</span>
            <input className="input" type="password" autoComplete="off" spellCheck={false} value={key}
              onChange={(e) => setKey(e.target.value)} placeholder="Schlüssel hier einfügen" />
            <span className="field-hint">Wird nur auf diesem Server gespeichert und nie wieder angezeigt.</span>
            <div className="button-row">
              <Button type="submit" disabled={!key.trim()}>Schlüssel speichern</Button>
              {editing && <Button variant="secondary" onClick={() => { setEditing(false); setKey(""); }}>Abbrechen</Button>}
            </div>
          </form>
        ) : (
          <div className="key-row">
            <span>API-Schlüssel {keyInfo?.set ? <strong>hinterlegt {keyInfo.hint ?? ""}</strong> : "fehlt"}
              {keyLocked && <span className="lock">fest eingestellt</span>}</span>
            {!keyLocked && (
              <span className="key-actions">
                <button className="link" onClick={() => setEditing(true)}>Ändern</button>
                {keyInfo?.set && <button className="link" onClick={() => void saveKey("")}>Entfernen</button>}
              </span>
            )}
          </div>
        )}

        {job && job.status !== "idle" && (
          <div className="import-status">
            <div className="ratio-head">
              <span>{job.status === "done" ? "Import abgeschlossen" : job.status === "paused" ? "Pausiert" : job.status === "error" ? "Abgebrochen" : phaseText}</span>
              {total > 0 && <strong>{Math.round(progress * 100)} %</strong>}
            </div>
            {total > 0 && <div className="bar"><div className="bar-fill" style={{ width: `${progress * 100}%` }} /></div>}
            {job.start && <div className="field-hint">Zeitraum {new Date(job.start).toLocaleDateString("de-DE")} – {new Date(job.end ?? job.start).toLocaleDateString("de-DE")}
              {job.imported ? ` · ${job.imported.toLocaleString("de-DE")} Viertelstunden übernommen` : ""}</div>}
            {job.status === "running" && job.eta_seconds ? <div className="field-hint">Noch ca. {duration(job.eta_seconds)}</div> : null}
          </div>
        )}
        {job?.notice && job.status === "running" && <Notice kind="warn">{job.notice}</Notice>}
        {job?.status === "error" && job.error && <Notice kind="error">{job.error}</Notice>}

        {job?.status === "running" ? (
          <Button variant="secondary" busy={busy} onClick={() => void action("/api/import/cloud/stop")}>Pausieren</Button>
        ) : (
          <Button busy={busy} disabled={!job?.key_set} onClick={() => void action("/api/import/cloud/start")}>
            {job?.status === "paused" ? "Fortsetzen" : job?.status === "error" ? "Erneut versuchen" : job?.status === "done" ? "Erneut abgleichen" : "Import starten"}
          </Button>
        )}
        <p className="hint">
          Die EKD-Cloud erlaubt nur etwa eine Abfrage pro Minute, deshalb dauert der Import einige Stunden (rund 2 Minuten pro Tag).
          Er läuft im Hintergrund weiter – auch wenn du die App schließt oder OpenAmpere neu startest.
          Eigene Messwerte von OpenAmpere werden dabei nie überschrieben.
        </p>
      </div>

      <div className="card form">
        <h2>Aus Export-Datei übernehmen</h2>
        <p className="hint">Hast du deinen Verlauf schon mit dem Export-Werkzeug gesichert? Dann den Export-Ordner als ZIP hier auswählen.</p>
        <label className="btn secondary file-button">
          ZIP-Datei auswählen
          <input type="file" accept=".zip,application/zip" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadZip(f); e.target.value = ""; }} />
        </label>
        {upload && <Notice kind="ok">{upload}</Notice>}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------

type LicensePackage = { name: string; version?: string; license: string; url?: string; texts: string[] };
type LicenseData = { self: LicensePackage; groups: { title: string; packages: LicensePackage[] }[] };

function LicenseRow({ pkg }: { pkg: LicensePackage }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="license-item">
      <button className="menu-row" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{pkg.name}{pkg.version && <span className="menu-hint">Version {pkg.version}</span>}</span>
        <span className="license-meta">
          <span className="pill">{pkg.license || "siehe Text"}</span>
          <span className={`chevron ${open ? "open" : ""}`} aria-hidden>›</span>
        </span>
      </button>
      {open && (
        <div className="license-body">
          {pkg.url && <a className="link" href={pkg.url.replace(/^git\+/, "")} target="_blank" rel="noopener noreferrer">Projektseite</a>}
          {pkg.texts.length ? pkg.texts.map((t, i) => <pre key={i} className="license-text">{t}</pre>)
            : <p className="hint">Lizenz: {pkg.license}</p>}
        </div>
      )}
    </div>
  );
}

export function LicensesPage({ onBack }: PageProps) {
  const { data, error, reload } = useResource<LicenseData>(LICENSES_DATA_URL);
  return (
    <SubPage title="Open-Source-Lizenzen" onBack={onBack}>
      {!data && <LoadState error={error} onRetry={reload} />}
      {data && (
        <>
          <p className="hint">
            OpenAmpere ist freie Software unter der MIT-Lizenz und baut auf diesen Open-Source-Komponenten auf.
            Danke an alle, die sie entwickeln!
          </p>
          <div className="card menu"><LicenseRow pkg={data.self} /></div>
          {data.groups.map((g) => (
            <div key={g.title}>
              <div className="section-title">{g.title}</div>
              <div className="card menu">
                {g.packages.map((p) => <LicenseRow key={p.name} pkg={p} />)}
              </div>
            </div>
          ))}
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

const watt = (w: number | null | undefined) => (w == null ? "–" : `${w.toLocaleString("de-DE")} W`);

export function ExportLimitPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data: current, error, reload } = useResource<ExportLimit>("/api/grid/export-limit");
  const [preset, setPreset] = useState<"60" | "70" | "100" | "custom">("100");
  const [custom, setCustom] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [reference, setReference] = useState("");
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);

  // start from the current value, so nothing is "changed" (and no warning shown) until the user picks something
  useEffect(() => {
    if (!current?.supported || current.limit_w == null) return;
    const share = current.rated_power_w ? Math.round((current.limit_w / current.rated_power_w) * 100) : null;
    const match = share === 60 ? "60" : share === 70 ? "70" : share === 100 ? "100" : null;
    setPreset(match ?? "custom");
    setCustom(String(current.limit_w));
  }, [current]);

  const rated = current?.rated_power_w ?? null;
  const target = preset === "custom" || !rated ? Math.round(Number(custom.replace(",", "."))) : Math.round((rated * Number(preset)) / 100);
  const valid = Number.isFinite(target) && target >= 0 && target <= (rated ?? 99_999);
  const raising = current?.limit_w == null || (valid && target > current.limit_w);
  const unchanged = valid && target === current?.limit_w;
  const editable = !!status?.control.enabled && !!current?.supported;
  const canSubmit = editable && valid && !unchanged && (!raising || (confirmed && reference.trim().length >= 3));
  const pct = (w: number | null | undefined) => (rated && w != null ? ` (${Math.round((w / rated) * 100)} % der Nennleistung)` : "");

  const submit = async () => {
    setDialog(false);
    setBusy(true);
    try {
      const r = await putJson<{ dry_run: boolean; result?: string }>("/api/grid/export-limit", {
        limit_w: target, grid_operator_confirmed: confirmed, confirmation_reference: reference,
      });
      toast(r.dry_run ? "Probemodus: Änderung wurde nur protokolliert" : r.result === "ok" ? "Einspeisebegrenzung geändert" : r.result ?? "Gespeichert");
      setConfirmed(false);
      setReference("");
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Einspeisebegrenzung" onBack={onBack}>
      <Notice kind="warn">
        <strong>Nur mit schriftlicher Zustimmung deines Netzbetreibers ändern.</strong> Die Einspeisebegrenzung ist Teil
        deiner Netzanschlusszusage – z. B. 60 % nach dem Solarspitzengesetz, 70 % nach früheren Regeln oder
        Nulleinspeisung. Normalerweise klärt der Installationsbetrieb das mit dem Netzbetreiber und stellt sie ein.
      </Notice>

      {!current ? <LoadState error={error} onRetry={reload} /> : (
        <>
          <div className="section-title">Aktuell</div>
          <div className="card">
            {current.supported ? (
              <>
                <div className="big-value">{watt(current.limit_w)}</div>
                <p className="hint">{rated ? `${Math.round(((current.limit_w ?? 0) / rated) * 100)} % der Nennleistung von ${watt(rated)}` : "Nennleistung unbekannt"}</p>
              </>
            ) : (
              <p className="hint">Bei diesem Gerät lässt sich die Einspeisebegrenzung nicht über Modbus lesen oder ändern.
                Wende dich an einen Elektrofachbetrieb.</p>
            )}
          </div>

          {current.supported && (
            <>
              <div className="section-title">Neue Begrenzung</div>
              {!status?.control.enabled && (
                <Notice kind="info">Nur Anzeige – die Steuerung ist ausgeschaltet.{" "}
                  <button className="link" onClick={() => onNavigate?.("control")}>Steuerung freigeben</button></Notice>
              )}
              {status?.control.enabled && status.control.dry_run && (
                <Notice kind="warn">Probemodus aktiv: Die Änderung wird nur protokolliert, nicht gesendet.</Notice>
              )}
              <div className="card form">
                {rated ? (
                  <Segmented value={preset} onChange={setPreset} disabled={!editable}
                    options={[["60", "60 %"], ["70", "70 %"], ["100", "Keine"], ["custom", "Eigener Wert"]]} />
                ) : null}
                {(preset === "custom" || !rated) && (
                  <Field label="Maximale Einspeiseleistung">
                    <div className="input-unit">
                      <input className="input" inputMode="numeric" value={custom} disabled={!editable}
                        onChange={(e) => setCustom(e.target.value)} placeholder={rated ? `0 – ${rated}` : "z. B. 6000"} />
                      <span>W</span>
                    </div>
                  </Field>
                )}
                {valid && !unchanged && (
                  <p className="hint">Neu: <strong>{watt(target)}</strong>{pct(target)}{preset === "100" && rated ? " – keine Begrenzung" : ""}</p>
                )}
                {unchanged && <p className="hint">Das ist bereits der aktuelle Wert.</p>}
              </div>

              {editable && valid && !unchanged && raising && (
                <>
                  <Notice kind="error">
                    <strong>Du erhöhst die Einspeiseleistung.</strong> Das ist nur zulässig, wenn dein Netzbetreiber der
                    neuen Leistung schriftlich zugestimmt hat. Je nach Netzbetreiber muss die Änderung zusätzlich von einem
                    eingetragenen Elektrofachbetrieb vorgenommen oder gemeldet und der Eintrag im Marktstammdatenregister
                    angepasst werden. Ohne Zustimmung kann der Netzbetreiber die Einspeisung sperren oder Kosten geltend machen.
                  </Notice>
                  <div className="card form">
                    <Checkbox checked={confirmed} onChange={setConfirmed} disabled={!editable}>
                      Mir liegt die <strong>schriftliche Zustimmung meines Netzbetreibers</strong> zu dieser Einspeiseleistung vor.
                    </Checkbox>
                    <Field label="Datum und Zeichen der Zustimmung" hint="Wird zusammen mit der Änderung im Protokoll gespeichert.">
                      <input className="input" value={reference} disabled={!editable} maxLength={200}
                        onChange={(e) => setReference(e.target.value)} placeholder="z. B. Schreiben vom 01.10.2026, Az. 12345" />
                    </Field>
                  </div>
                </>
              )}

              {editable && (
                <Button variant={raising ? "danger" : "primary"} busy={busy} disabled={!canSubmit} onClick={() => setDialog(true)}>
                  Einspeisebegrenzung ändern
                </Button>
              )}
            </>
          )}

          <Notice kind="info">
            Hinweis: Auch ein noch angeschlossener Energiemanager (z. B. die bisherige Smartbox) kann die Einspeisung
            zusätzlich begrenzen. Diese Einstellung hier betrifft nur den Wechselrichter.
          </Notice>
        </>
      )}

      {dialog && current && (
        <Dialog title="Einspeisebegrenzung wirklich ändern?" danger={raising}
          confirm={raising ? "Zustimmung liegt vor – ändern" : "Ändern"} onCancel={() => setDialog(false)} onConfirm={() => void submit()}>
          <p>Bisher: <strong>{watt(current.limit_w)}</strong>{pct(current.limit_w)}<br />Neu: <strong>{watt(target)}</strong>{pct(target)}</p>
          {raising && <p>Du bestätigst, dass die schriftliche Zustimmung deines Netzbetreibers vorliegt ({reference.trim()}).
            Die Verantwortung für die Einhaltung der Netzanschlussbedingungen liegt bei dir als Anlagenbetreiber.</p>}
        </Dialog>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function PvSystemPage({ onBack, snap }: PageProps & { snap: Snapshot | null }) {
  const { settings, save, error, reload } = useSettings();
  const [names, setNames] = useState<string[]>([]);
  const inputs = activeInputs(snap);
  useEffect(() => { if (settings) setNames(settings["pv.input_names"]); }, [settings]);
  const count = Math.max(inputs.length ? Math.max(...inputs.map((i) => i.index)) + 1 : 0, names.length);
  const setName = (i: number, value: string) => setNames((n) => {
    const next = [...n];
    while (next.length <= i) next.push("");
    next[i] = value;
    return next;
  });

  return (
    <SubPage title="PV-Anlage" onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <p className="hint">
        Dein Wechselrichter hat mehrere PV-Eingänge (MPPT). An jedem Eingang hängt ein Modulfeld – zum Beispiel
        eine Dachseite, die Garage oder ein Carport.
        Gib ihnen Namen wie „Süddach“ oder „Garage“ – die aktuelle Leistung hilft beim Zuordnen.
      </p>
      {settings && (count === 0 ? (
        <Notice kind="info">Noch keine PV-Eingänge erkannt. Bei Dunkelheit liefern die Eingänge keine Spannung – schau tagsüber noch einmal vorbei.</Notice>
      ) : (
        <div className="card form">
          {Array.from({ length: count }, (_, i) => {
            const live = inputs.find((x) => x.index === i);
            return (
              <Field key={i} label={`Eingang ${i + 1}`}
                hint={live ? `jetzt ${kw(live.power)} · ${num(live.voltage, 0)} V` : "derzeit keine Leistung"}>
                <div className="input-unit">
                  <span className="dot" style={{ background: PV_INPUT_COLORS[i % 4] }} />
                  <input className="input" maxLength={30} value={names[i] ?? ""} placeholder={`Modulfeld ${i + 1}`}
                    onChange={(e) => setName(i, e.target.value)} />
                </div>
              </Field>
            );
          })}
          <Button disabled={JSON.stringify(names) === JSON.stringify(settings["pv.input_names"])}
            onClick={() => save({ "pv.input_names": names.slice(0, 6) })}>Speichern</Button>
        </div>
      ))}
    </SubPage>
  );
}
