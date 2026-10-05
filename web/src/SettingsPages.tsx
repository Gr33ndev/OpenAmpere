import { useEffect, useState } from "react";
import type { AuthStatus, BatterySettings, BatteryState, CloudImportState, ExportLimit, FeedInRule, SecretKey, SettingKey, Settings, Snapshot, Status } from "./api";
import { OFFLINE_MESSAGE, postFile, postJson, putJson, PV_INPUT_COLORS, useResource } from "./api";
import { DEMO } from "./demo/flag";
import { IMPRINT_URL, ISSUES_URL, LICENSES_DATA_URL, REPO_URL } from "./links";
import { isoDate, kw, num, timeZone, todayIso, updatedLabel } from "./format";
import { Chart } from "./Chart";
import { Chevron } from "./icons";
import { ConnectionForm, SetupHelp } from "./Setup";
import { ControlModeBar } from "./ControlMode";
import { UpdatesCard } from "./Updates";
import { Button, Checkbox, Dialog, Field, LearnMore, LoadState, MenuRow, Notice, Segmented, Slider, SubPage, SwitchRow, toast, Unsaved } from "./ui";

export type PageProps = { onBack: () => void; onNavigate?: (page: string) => void };

/** Loads settings and saves partial changes. */
export function useSettings() {
  const { data, setData, error, reload } = useResource<Settings>("/api/settings");
  const save = async (changes: Partial<Settings["values"]> & Partial<Record<SecretKey, string>>) => {
    try {
      setData(await putJson<Settings>("/api/settings", { ...changes, _revision: data?.revision }));
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

const FIELDS = ["work_mode", "min_soc", "max_soc", "min_soc_on_grid"] as const;

function SocSlider({ value, min, max, disabled, onChange }: {
  value: number | null; min: number; max: number; disabled: boolean; onChange: (v: number) => void;
}) {
  if (value == null) return <p className="hint">? – konnte nicht gelesen werden</p>;
  return <Slider value={value} min={min} max={Math.max(min, max)} unit="%" disabled={disabled} onChange={onChange} />;
}

/** The battery from 0 to 100 %: which part is used when. */
function SocBar({ min, reserve, max }: { min: number | null; reserve: number | null; max: number | null }) {
  if (min == null || reserve == null || max == null) return null;
  const zones = [
    { from: 0, to: min, cls: "never", label: "Wird nie genutzt" },
    { from: min, to: reserve, cls: "backup", label: "Nur bei Stromausfall" },
    { from: reserve, to: max, cls: "daily", label: "Alltag" },
    { from: max, to: 100, cls: "unused", label: "Wird nicht geladen" },
  ].filter((z) => z.to > z.from);
  return (
    <div className="card">
      <div className="soc-bar" role="img"
        aria-label={zones.map((z) => `${z.label}: ${z.from} bis ${z.to} %`).join(", ")}>
        {zones.map((z) => <div key={z.cls} className={`zone ${z.cls}`} style={{ flexGrow: z.to - z.from }} />)}
      </div>
      <div className="soc-legend">
        {zones.map((z) => <span key={z.cls}><i className={`zone ${z.cls}`} />{z.label} ({z.from}–{z.to} %)</span>)}
      </div>
    </div>
  );
}

export function BatteryPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data: current, error, reload } = useResource<BatteryState>("/api/battery/settings");
  const [form, setForm] = useState<BatterySettings | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (current) setForm({ work_mode: current.work_mode, min_soc: current.min_soc, max_soc: current.max_soc,
      min_soc_on_grid: current.min_soc_on_grid });
  }, [current]);

  const control = status?.control;
  const deviceSupportsControl = status?.device?.supports_control ?? true;
  const editable = !!control?.enabled && deviceSupportsControl;
  const changed = form && current && FIELDS.some((k) => form[k] !== current[k]);
  const socEditable = editable && !!current && !current.unreadable.some((k) => k !== "work_mode");
  const set = (patch: Partial<BatterySettings>) => setForm((f) => (f ? { ...f, ...patch } : f));

  const save = async () => {
    if (!form || !current) return;
    const changes = Object.fromEntries(FIELDS.filter((k) => form[k] !== current[k]).map((k) => [k, form[k]]));
    setBusy(true);
    try {
      const r = await putJson<{ dry_run: boolean; written: object; result?: string; warning?: string | null }>("/api/battery/settings", changes);
      toast(r.dry_run ? "Testmodus: Änderung wurde nur protokolliert" : r.result === "ok" ? "Am Wechselrichter gespeichert" : r.result ?? "Gespeichert");
      if (r.warning) toast(r.warning, "error");
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Speicher & Notstrom" onBack={onBack}>
      {!deviceSupportsControl ? (
        <Notice kind="info">Nur Anzeige – für {status?.device?.manufacturer}-Geräte kann OpenAmpere Einstellungen noch nicht ändern.</Notice>
      ) : <ControlModeBar compact />}
      {!form && (error ? <LoadState error={error} onRetry={reload} /> : <p className="hint">Lese Einstellungen vom Wechselrichter …</p>)}

      {current?.external_change && (
        <Notice kind="error">
          Ein anderes Gerät (z. B. die bisherige Smartbox) hat deine Änderung kurz danach wieder überschrieben:{" "}
          {Object.entries(current.external_change.found).map(([k, v]) => `${LOG_KEYS[k] ?? k} jetzt ${logValue(v)}`).join(", ")}.
          Solange es angeschlossen ist, lassen sich diese Werte nicht dauerhaft ändern.
          {" "}Eine bisherige Smartbox holt sich ihre Einstellungen regelmäßig aus der Cloud. Sperrst du ihr im Router den
          Internetzugang, bleiben deine Änderungen bestehen.
        </Notice>
      )}
      {form && current && current.unreadable.length > 0 && (
        <Notice kind="warn">
          Einige Werte konnten gerade nicht gelesen werden ({current.unreadable.map((k) => LOG_KEYS[k] ?? k).join(", ")}).
          Sie werden mit „?“ angezeigt; Änderungen an den Grenzen sind erst möglich, wenn alle Werte gelesen wurden.
        </Notice>
      )}

      {form && (
        <>
          <SocBar min={form.min_soc} reserve={form.min_soc_on_grid} max={form.max_soc} />

          <div className="section-title">Notstrom-Reserve</div>
          <div className="card form">
            <p className="hint">So viel bleibt im Alltag immer im Speicher, damit bei einem Stromausfall Energie da ist.</p>
            <SocSlider value={form.min_soc_on_grid} disabled={!socEditable}
              min={Math.max(10, form.min_soc ?? 10)} max={Math.min(99, (form.max_soc ?? 100) - 1)}
              onChange={(v) => set({ min_soc_on_grid: v })} />
          </div>

          <div className="section-title">Ladegrenzen</div>
          <div className="card form">
            <Field label="Maximaler Ladestand" hint="Bis zu diesem Wert wird der Speicher geladen.">
              <SocSlider value={form.max_soc} disabled={!socEditable}
                min={Math.max(20, (form.min_soc_on_grid ?? 10) + 1)} max={100} onChange={(v) => set({ max_soc: v })} />
            </Field>
            <Field label="Untergrenze im Notstrombetrieb"
              hint="Während eines Stromausfalls wird der Speicher bis hierhin entladen, nicht weiter. Höchstens so hoch wie die Notstrom-Reserve.">
              <SocSlider value={form.min_soc} disabled={!socEditable}
                min={10} max={form.min_soc_on_grid ?? 100} onChange={(v) => set({ min_soc: v })} />
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
          {editable && <Unsaved show={!!changed} />}
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

type Window = { from: string; to: string; price_ct: number };
type TariffForm = { valid_from: string; kind: "fixed" | "time" | "dynamic"; price_ct: string; surcharge_ct: string;
  vat_percent: string; feed_in_ct: string; area: "DE" | "AT"; base_fee_eur_month: string; windows: Window[] };
type TariffData = { valid_from: string; kind: "fixed" | "time" | "dynamic"; price_ct: number; surcharge_ct: number;
  vat_percent: number; feed_in_ct: number; area: "DE" | "AT"; base_fee_eur_month: number; windows?: Window[] };
const de = (v: number) => String(v).replace(".", ",");
const toNumber = (v: string) => (v.trim() === "" ? Number.NaN : Number(v.replace(",", ".")));

/** Own price windows, e.g. a night tariff or time-variable grid fees (§ 14a EnWG, module 3) (#24). */
function TimeWindows({ windows, onChange }: { windows: Window[]; onChange: (w: Window[]) => void }) {
  const set = (i: number, patch: Partial<Window>) => onChange(windows.map((w, j) => (j === i ? { ...w, ...patch } : w)));
  return (
    <div className="time-windows">
      <p className="hint">Zeiten mit eigenem Preis, z. B. Nachtstrom von 00:30 bis 05:30 oder die Zeitfenster eines
        zeitvariablen Netzentgelts. Ein Fenster darf über Mitternacht gehen.</p>
      {windows.map((w, i) => (
        <div className="time-window" key={i}>
          <Field label="Von"><input className="input" type="time" value={w.from} onChange={(e) => set(i, { from: e.target.value })} /></Field>
          <Field label="Bis"><input className="input" type="time" value={w.to} onChange={(e) => set(i, { to: e.target.value })} /></Field>
          <Field label="Preis"><div className="input-unit"><input className="input" inputMode="decimal" value={de(w.price_ct)}
            onChange={(e) => set(i, { price_ct: toNumber(e.target.value) || 0 })} /><span>ct</span></div></Field>
          <button className="link danger-link" onClick={() => onChange(windows.filter((_, j) => j !== i))}>Entfernen</button>
        </div>
      ))}
      {windows.length < 6 && <button className="link" onClick={() => onChange([...windows, { from: "00:00", to: "06:00", price_ct: 20 }])}>
        Zeitfenster hinzufügen</button>}
    </div>
  );
}

function PriceChart() {
  const { data } = useResource<{ kind: string; entries: { ts: number; ct: number }[] }>(`/api/prices?date=${todayIso()}`, 15 * 60_000);
  if (!data || data.kind === "fixed") return null;
  if (!data.entries.length) return <p className="hint">Noch keine Börsenpreise für heute geladen (braucht Internet).</p>;
  const x = data.entries.map((e) => e.ts);
  const cheapest = data.entries.reduce((a, b) => (b.ct < a.ct ? b : a));
  return (
    <>
      <div className="section-title">Strompreis heute</div>
      <Chart x={x} series={[{ label: "Preis", color: "var(--grid)", values: data.entries.map((e) => e.ct), unit: "ct" }]}
        xFormat={(ts) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: timeZone() })}
        height={180} label="Strompreis heute je Viertelstunde" />
      <p className="hint">Am günstigsten: {new Date(cheapest.ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: timeZone() })} Uhr
        mit {num(cheapest.ct, 1)} ct/kWh (inkl. Aufschlag).</p>
    </>
  );
}

export function TariffPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<{ tariffs: TariffData[] }>("/api/tariffs");
  const [forms, setForms] = useState<TariffForm[]>([]);
  const [busy, setBusy] = useState(false);
  const toForm = (t: TariffData): TariffForm => ({ ...t, price_ct: de(t.price_ct), surcharge_ct: de(t.surcharge_ct),
    vat_percent: de(t.vat_percent), feed_in_ct: de(t.feed_in_ct), base_fee_eur_month: de(t.base_fee_eur_month ?? 0),
    windows: t.windows ?? [] });
  useEffect(() => {
    if (data) setForms(data.tariffs.map(toForm));
  }, [data]);
  const update = (i: number, patch: Partial<TariffForm>) => setForms((f) => f.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const dirty = !!data && JSON.stringify(forms) !== JSON.stringify(data.tariffs.map(toForm));
  const valid = forms.length > 0 && forms.every((t) => t.valid_from && [t.feed_in_ct, t.kind === "dynamic" ? t.surcharge_ct : t.price_ct]
    .every((v) => Number.isFinite(toNumber(v))) && (t.kind !== "time" || t.windows.length > 0));
  const add = () => setForms((f) => [...f, { ...(f[f.length - 1] ?? { kind: "fixed", price_ct: "35", surcharge_ct: "20",
    vat_percent: "19", feed_in_ct: "8", area: "DE", base_fee_eur_month: "0", windows: [] }), valid_from: todayIso() } as TariffForm]);

  const save = async () => {
    setBusy(true);
    try {
      const tariffs = forms.map((t) => ({ ...t, price_ct: toNumber(t.price_ct) || 0, surcharge_ct: toNumber(t.surcharge_ct) || 0,
        vat_percent: toNumber(t.vat_percent) || 0, feed_in_ct: toNumber(t.feed_in_ct),
        base_fee_eur_month: toNumber(t.base_fee_eur_month) || 0 }));
      setData(await putJson<{ tariffs: TariffData[] }>("/api/tariffs", { tariffs }));
      toast("Gespeichert");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Stromtarif" onBack={onBack}>
      {!data && <LoadState error={error} onRetry={reload} />}
      <p className="hint">Damit rechnet OpenAmpere Ersparnis, Stromkosten und den Abgleich deiner Abschläge (Auswertung). Wechselst
        du den Tarif, lege einen neuen mit Startdatum an. Ältere Zeiträume rechnet OpenAmpere weiter mit dem alten Preis.</p>
      {forms.map((t, i) => (
        <div className="card form" key={i}>
          <div className="field-row">
            <Field label="Gültig ab"><input className="input" type="date" value={t.valid_from}
              onChange={(e) => update(i, { valid_from: e.target.value })} /></Field>
            <Field label="Art">
              <select className="input" value={t.kind} onChange={(e) => update(i, { kind: e.target.value as TariffForm["kind"] })}>
                <option value="fixed">Festpreis</option>
                <option value="time">Zeitvariabel (eigene Zeitfenster)</option>
                <option value="dynamic">Dynamisch (Börsenpreis)</option>
              </select>
            </Field>
          </div>
          {t.kind !== "dynamic" ? (<>
            <Field label={t.kind === "time" ? "Preis außerhalb der Zeitfenster" : "Strompreis (brutto)"}
              hint={t.kind === "time" ? undefined : "Was du pro Kilowattstunde aus dem Netz bezahlst."}>
              <div className="input-unit"><input className="input" inputMode="decimal" value={t.price_ct}
                onChange={(e) => update(i, { price_ct: e.target.value })} /><span>ct/kWh</span></div>
            </Field>
            {t.kind === "time" && <TimeWindows windows={t.windows} onChange={(windows) => update(i, { windows })} />}
          </>) : (
            <>
              <Field label="Aufschlag (brutto)" hint="Alles, was zum Börsenpreis dazukommt: Netzentgelt, Umlagen, Steuern, Marge. Steht im Vertrag oder auf der Rechnung.">
                <div className="input-unit"><input className="input" inputMode="decimal" value={t.surcharge_ct}
                  onChange={(e) => update(i, { surcharge_ct: e.target.value })} /><span>ct/kWh</span></div>
              </Field>
              <div className="field-row">
                <Field label="MwSt. auf Börsenpreis"><div className="input-unit"><input className="input" inputMode="decimal"
                  value={t.vat_percent} onChange={(e) => update(i, { vat_percent: e.target.value })} /><span>%</span></div></Field>
                <Field label="Preiszone">
                  <select className="input" value={t.area} onChange={(e) => update(i, { area: e.target.value as TariffForm["area"] })}>
                    <option value="DE">Deutschland</option><option value="AT">Österreich</option>
                  </select>
                </Field>
              </div>
            </>
          )}
          <Field label="Grundpreis" hint="Fester Betrag pro Monat, unabhängig vom Verbrauch. Steht im Vertrag.">
            <div className="input-unit"><input className="input" inputMode="decimal" value={t.base_fee_eur_month}
              onChange={(e) => update(i, { base_fee_eur_month: e.target.value })} /><span>€/Monat</span></div>
          </Field>
          <Field label="Einspeisevergütung" hint="Was du pro eingespeister Kilowattstunde erhältst (EEG).">
            <div className="input-unit"><input className="input" inputMode="decimal" value={t.feed_in_ct}
              onChange={(e) => update(i, { feed_in_ct: e.target.value })} /><span>ct/kWh</span></div>
          </Field>
          {forms.length > 1 && <button className="link" onClick={() => setForms((f) => f.filter((_, j) => j !== i))}>Tarif entfernen</button>}
        </div>
      ))}
      <Button variant="secondary" onClick={add}>Tarifwechsel hinzufügen</Button>
      <Button busy={busy} disabled={!valid || !dirty} onClick={save}>Speichern</Button>
      <Unsaved show={dirty} />
      {forms.some((t) => t.kind === "dynamic") && (
        <p className="hint">Börsenpreise kommen kostenlos von aWATTar (Day-Ahead-Markt). Dafür braucht der Server Internet.</p>
      )}
      <PriceChart />
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function ConnectionPage({ onBack, onNavigate }: PageProps) {
  const { settings, locked, lockedKeys, save, error, reload } = useSettings();
  const { data: status } = useResource<Status>("/api/status", 5000);
  const [pollInterval, setPollInterval] = useState(10);
  const [timeout, setTimeoutValue] = useState(3);
  const [mode, setMode] = useState<"persistent" | "per_poll">("persistent");
  const [registerMap, setRegisterMap] = useState("auto");
  const [readFunction, setReadFunction] = useState("auto");
  useEffect(() => {
    if (!settings) return;
    setPollInterval(settings["inverter.poll_interval"]);
    setTimeoutValue(settings["inverter.timeout"]);
    setMode(settings["inverter.connection_mode"]);
    setRegisterMap(settings["inverter.register_map"]);
    setReadFunction(settings["inverter.read_function"]);
  }, [settings]);

  return (
    <SubPage title="Verbindung" onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <Notice kind={status?.connected ? "ok" : "warn"}>
        {status?.connected
          ? `Verbunden mit ${status.device?.manufacturer} ${status.device?.model}`
          : `Nicht verbunden${status?.last_error ? `: ${status.last_error}` : ""}`}
      </Notice>
      <div className="section-title">Weitere Geräte</div>
      <div className="card menu">
        <MenuRow label="Heizstab und weitere Geräte" hint="my-PV, Shelly, eigene Web-Adressen" onClick={() => onNavigate?.("device-setup")} />
        <MenuRow label="Wallbox" hint="über evcc" onClick={() => onNavigate?.("wallbox")} />
      </div>
      <div className="section-title">Netzbetreiber</div>
      <div className="card menu">
        <MenuRow label="Zählerwerte" hint="Smart-Meter-Werte aus dem Kundenportal" onClick={() => onNavigate?.("gridmeter")} />
      </div>
      <div className="section-title">Wechselrichter</div>
      {settings && (
        <ConnectionForm
          key={settings["inverter.host"]}
          initial={{ host: settings["inverter.host"], port: settings["inverter.port"], unit: settings["inverter.unit"],
            driver: settings["inverter.driver"] }}
          locked={lockedKeys}
          onSaved={() => toast("Gespeichert – verbinde neu …")}
        />
      )}
      {!status?.connected && <SetupHelp />}

      {status?.relocated && (
        <Notice kind="info">
          Der Wechselrichter hatte eine neue IP-Adresse und wurde am {updatedLabel(status.relocated.ts)} automatisch
          wiedergefunden ({status.relocated.from} → {status.relocated.to}). Tipp: Vergib ihm im Router eine feste Adresse.
        </Notice>
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
            <Field label="Verbindung" locked={locked("inverter.connection_mode")}
              hint={mode === "per_poll"
                ? "OpenAmpere verbindet sich für jede Abfrage neu und gibt den Zugang danach wieder frei – für Geräte, an denen noch ein anderer Energiemanager hängt."
                : "Eine dauerhafte Verbindung ist am schnellsten. Bricht die Verbindung eines anderen Energiemanagers (z. B. der Smartbox) ab, wähle „Pro Abfrage“."}>
              <Segmented value={mode} onChange={setMode} disabled={locked("inverter.connection_mode")}
                options={[["persistent", "Dauerhaft"], ["per_poll", "Pro Abfrage"]]} />
            </Field>
            <Button variant="secondary"
              disabled={pollInterval === settings["inverter.poll_interval"] && timeout === settings["inverter.timeout"]
                && mode === settings["inverter.connection_mode"]}
              onClick={() => save({ "inverter.poll_interval": pollInterval, "inverter.timeout": timeout,
                "inverter.connection_mode": mode })}>Speichern</Button>
            <Unsaved show={!(pollInterval === settings["inverter.poll_interval"] && timeout === settings["inverter.timeout"]
              && mode === settings["inverter.connection_mode"])} />
          </div>

          {(status?.device?.driver ?? settings["inverter.driver"]) === "foxess" && (
            <details className="card expert">
              <summary>Für Experten</summary>
              <p className="hint">Nur ändern, wenn die automatische Erkennung falsch liegt. Beim Speichern wird neu verbunden.</p>
              <Field label="FoxESS-Registerkarte" locked={locked("inverter.register_map")}>
                <select className="input" value={registerMap} onChange={(e) => setRegisterMap(e.target.value)}>
                  <option value="auto">Automatisch erkennen</option>
                  <option value="foxess_h3_new">FoxESS H3 – neuere Firmware / Smart / Pro</option>
                  <option value="foxess_h3_legacy">FoxESS H3 – ältere Firmware</option>
                </select>
              </Field>
              <Field label="Leseverfahren (Modbus-Funktionscode)" locked={locked("inverter.read_function")}>
                <select className="input" value={readFunction} onChange={(e) => setReadFunction(e.target.value)}>
                  <option value="auto">Automatisch</option>
                  <option value="input">Input-Register (FC04)</option>
                  <option value="holding">Holding-Register (FC03)</option>
                </select>
              </Field>
              <Button variant="secondary"
                disabled={registerMap === settings["inverter.register_map"] && readFunction === settings["inverter.read_function"]}
                onClick={() => save({ "inverter.register_map": registerMap, "inverter.read_function": readFunction })}>
                Speichern und neu verbinden
              </Button>
            </details>
          )}
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

const LOG_KEYS: Record<string, string> = {
  "control.enabled": "Steuerung", "control.dry_run": "Testmodus", "grid.feed_in_rule": "Einspeiseregel",
  "pv.installed_kwp": "Modulleistung (kWp)", export_limit_w: "Einspeisebegrenzung (W)", min_soc: "Entladegrenze (%)",
  min_soc_on_grid: "Reserve am Netz (%)", max_soc: "Ladegrenze (%)", work_mode: "Betriebsmodus",
  power_w: "Ladeleistung (W)", target_soc: "Ladeziel (%)", enabled: "Eingeschaltet", soc: "Ladestand (%)",
  consumer: "Gerät", on: "An",
};
const LOG_VALUES: Record<string, string> = {
  true: "an", false: "aus", unknown: "unbekannt", limit_60: "60 %", limit_70: "70 %", operator: "Wert vom Netzbetreiber",
  none: "keine Begrenzung", self_use: "Eigenverbrauch", feed_in_first: "Einspeisung bevorzugen", backup: "Notstromreserve",
  peak_shaving: "Spitzenlast begrenzen",
};
const logValue = (v: unknown) => (v == null ? "–" : LOG_VALUES[String(v)] ?? String(v));

type LogEntry = { ts: number; action: string; details: { from: Record<string, unknown>; to: Record<string, unknown> }; dry_run: boolean; result: string };

export function ControlPage({ onBack }: PageProps) {
  const { data: log, reload } = useResource<{ entries: LogEntry[] }>("/api/control/log", 30_000);

  return (
    <SubPage title="Steuerung und Protokoll" onBack={onBack}>
      <ControlModeBar />
      <Notice kind="info">
        Solange ein anderer Energiemanager (z. B. die bisherige Smartbox) angeschlossen ist, kann er Einstellungen wieder überschreiben.
        Prüfe nach Änderungen, ob sie erhalten bleiben.
      </Notice>

      <div className="section-title">Protokoll</div>
      <div className="card">
        {!log?.entries.length && <p className="hint">Noch keine Änderungen.</p>}
        {log?.entries.map((e) => (
          <div className="log-row" key={e.ts}>
            <div className="meta">{new Date(e.ts * 1000).toLocaleString("de-DE")}{e.dry_run && " · Testmodus"}</div>
            <div>{Object.entries(e.details.to).map(([k, v]) => `${LOG_KEYS[k] ?? k}: ${logValue(e.details.from[k])} → ${logValue(v)}`).join(", ")}</div>
            <div className="meta">{e.result}</div>
          </div>
        ))}
        {!!log?.entries.length && <button className="link" onClick={reload}>Aktualisieren</button>}
      </div>

    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export type Theme = "auto" | "light" | "dark";

const THEME_COLORS = { light: "#f4f4f4", dark: "#21262b" };

export function applyTheme(theme: Theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  // status bar colour of the installed app follows the chosen design
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
    const scheme = meta.media.includes("dark") ? "dark" : "light";
    meta.content = THEME_COLORS[theme === "auto" ? scheme : theme];
  });
}

export function storedTheme(): Theme {
  try {
    const value = localStorage.getItem("openampere.theme");
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    return "auto";
  }
}

const TIMEZONES = ["Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/Amsterdam", "Europe/Brussels",
  "Europe/Luxembourg", "Europe/Paris", "Europe/Rome", "Europe/Madrid", "Europe/Warsaw", "Europe/Prague", "Europe/London"];

export function AppearancePage({ onBack }: PageProps) {
  const { settings, locked, save } = useSettings();
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
      {settings && (
        <div className="card form">
          <Field label="Zeitzone der Anlage" locked={locked("timezone")}
            hint="Tage, Uhrzeiten und Tageswerte richten sich danach – auch wenn du die App gerade im Ausland öffnest.">
            <select className="input" value={settings.timezone} disabled={locked("timezone")}
              onChange={(e) => void save({ timezone: e.target.value })}>
              {[...new Set([settings.timezone, ...TIMEZONES])].map((z) => <option key={z} value={z}>{z.replace("_", " ")}</option>)}
            </select>
          </Field>
        </div>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

/** Started from the home screen: downloads must open in their own window, the preview iOS shows in the app window
 * itself has no way back (#13). */
const IOS_HOME_SCREEN = (navigator as Navigator & { standalone?: boolean }).standalone === true;
const HOME_SCREEN_APP = window.matchMedia?.("(display-mode: standalone)").matches || IOS_HOME_SCREEN;
const downloadProps = HOME_SCREEN_APP ? { target: "_blank", rel: "noopener" } : { download: "" };
/** On the iPhone that own window only shows a blank page and saves nothing (#70): there the app fetches the file
 * itself and hands it to the share sheet ("In Dateien sichern"). */
const SHARE_FILES = IOS_HOME_SCREEN && (() => {
  try {
    return !!navigator.canShare?.({ files: [new File(["x"], "x.csv", { type: "text/csv" })] });
  } catch {
    return false;
  }
})();

function DownloadButton({ href, label }: { href: string; label: string }) {
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState<File | null>(null);
  useEffect(() => setReady(null), [href]);
  if (!SHARE_FILES) return <a className="btn secondary" href={href} {...downloadProps}>{label}</a>;

  const share = (file: File) => navigator.share({ files: [file] }).then(() => setReady(null), (err: Error) => {
    if (err.name === "NotAllowedError") setReady(file); // loading took too long for iOS: one more tap opens the sheet
    else {
      setReady(null);
      if (err.name !== "AbortError") toast("Die Datei konnte nicht geteilt werden.", "error");
    }
  });
  const load = async () => {
    setBusy(true);
    try {
      const response = await fetch(href, { credentials: "same-origin" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : `Fehler ${response.status}`);
      }
      const name = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? "openampere";
      const blob = await response.blob();
      await share(new File([blob], name, { type: blob.type || "application/octet-stream" }));
    } catch (err) {
      toast(err instanceof TypeError || !(err instanceof Error) ? OFFLINE_MESSAGE : err.message, "error"); // TypeError: network
    } finally {
      setBusy(false);
    }
  };
  return ready ? <Button variant="secondary" onClick={() => void share(ready)}>Datei sichern oder teilen</Button>
    : <Button variant="secondary" busy={busy} onClick={() => void load()}>{label}</Button>;
}

function CsvExportCard() {
  const thisYear = new Date().getFullYear();
  const [from, setFrom] = useState(`${thisYear}-01-01`);
  const [to, setTo] = useState(isoDate(new Date()));
  const [resolution, setResolution] = useState<"15m" | "60m" | "day" | "month">("day");
  const valid = !!from && !!to && from <= to;
  const href = `/api/export/csv?from=${from}&to=${to}&resolution=${resolution}`;
  return (
    <div className="card form">
      <h2>Als Tabelle exportieren</h2>
      <p className="hint">Energiewerte als CSV-Datei, z. B. für Excel, Numbers oder die Steuererklärung.</p>
      <div className="field-row">
        <Field label="Von"><input className="input" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="Bis"><input className="input" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></Field>
      </div>
      <Segmented value={resolution} onChange={setResolution}
        options={[["15m", "15 min"], ["60m", "Stunde"], ["day", "Tag"], ["month", "Monat"]]} />
      {DEMO ? <p className="hint">In der Demo nicht verfügbar.</p> : valid ? <DownloadButton href={href} label="CSV herunterladen" />
        : <p className="hint">Bitte einen gültigen Zeitraum wählen.</p>}
    </div>
  );
}

/** The separate download window does not share the login cookie: it gets a link that is valid for 10 minutes. */
function BackupLink() {
  const [url, setUrl] = useState<string | null>(HOME_SCREEN_APP ? null : "/api/backup");
  useEffect(() => {
    if (!HOME_SCREEN_APP) return;
    const fetchLink = () => postJson<{ url: string }>("/api/backup/link", {}).then((r) => setUrl(r.url)).catch(() => setUrl(null));
    fetchLink();
    const timer = window.setInterval(fetchLink, 5 * 60_000);
    return () => window.clearInterval(timer);
  }, []);
  if (!url) return <Button variant="secondary" disabled>Datensicherung herunterladen</Button>;
  return <a className="btn secondary" href={url} {...downloadProps}>Datensicherung herunterladen</a>;
}

type StorageUsage = { db_bytes: number | null; free_bytes: number | null; samples: number; first_sample: number | null;
  bytes_per_year: number; retention_days: number };
const RETENTION: [number, string][] = [[30, "30 Tage"], [365, "1 Jahr"], [1825, "5 Jahre"], [3650, "10 Jahre"], [0, "Unbegrenzt"]];
const size = (bytes: number) => (bytes >= 1e9 ? `${num(bytes / 1e9, 1)} GB` : `${num(Math.max(bytes, 1e6) / 1e6, 0)} MB`);

/** What the chosen retention costs: detail readings need about 0.4 GB per year with a reading every 10 s. */
function storageHint(u: StorageUsage, days: number): string {
  const now = u.db_bytes != null ? `Die Datenbank ist jetzt ${size(u.db_bytes)} groß` : "";
  const free = u.free_bytes != null ? `, frei sind noch ${size(u.free_bytes)}.` : ".";
  const need = days > 0 ? ` Für ${days >= 365 ? `${num(days / 365, 0)} ${days >= 730 ? "Jahre" : "Jahr"}` : `${days} Tage`} Detaildaten `
    + `braucht OpenAmpere etwa ${size(u.bytes_per_year * days / 365)}.`
    : ` Ohne Grenze wächst sie um etwa ${size(u.bytes_per_year)} pro Jahr.`;
  return now + free + need;
}

export function DataPage({ onBack }: PageProps) {
  const { settings, locked, save, error, reload } = useSettings();
  const { data: auth } = useResource<AuthStatus>("/api/auth/status");
  const [days, setDays] = useState(30);
  const { data: usage } = useResource<StorageUsage>("/api/storage");
  useEffect(() => { if (settings) setDays(settings["storage.raw_retention_days"]); }, [settings]);
  const options: [string, string][] = RETENTION.map(([d, label]) => [String(d), label]);
  if (!RETENTION.some(([d]) => d === days)) options.unshift([String(days), `${days} Tage`]);

  return (
    <SubPage title="Daten & Sicherung" onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <div className="card form">
        <Field label="Detaildaten aufbewahren" locked={locked("storage.raw_retention_days")}
          hint="Messwerte alle paar Sekunden für die Leistungskurve. Viertelstunden- und Tageswerte bleiben immer erhalten.">
          <select className="input" value={String(days)} disabled={locked("storage.raw_retention_days")}
            onChange={(e) => setDays(Number(e.target.value))}>
            {options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </Field>
        {usage && <p className="hint">{storageHint(usage, days)}</p>}
        <Button variant="secondary" disabled={!settings || days === settings["storage.raw_retention_days"]}
          onClick={() => save({ "storage.raw_retention_days": days })}>Speichern</Button>
        <Unsaved show={!!settings && days !== settings["storage.raw_retention_days"]} />
      </div>
      <CloudImportCard />
      <CsvExportCard />
      <div className="card form">
        <h2>Sicherung</h2>
        <p className="hint">Lädt die komplette Datenbank mit allen Messwerten und Einstellungen herunter. Bewahre die Datei sicher auf.
          Passwörter und API-Schlüssel sind nicht enthalten.</p>
        {DEMO ? <p className="hint">In der Demo nicht verfügbar.</p> : auth?.authenticated ? (
          SHARE_FILES ? <DownloadButton href="/api/backup" label="Datensicherung herunterladen" /> : <BackupLink />
        ) : (
          <Button variant="secondary" onClick={() => window.dispatchEvent(new CustomEvent("openampere:auth", { detail: "login_required" }))}>
            Anmelden zum Herunterladen
          </Button>
        )}
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
      {!DEMO && <UpdatesCard />}
      <div className="card">
        <p>OpenAmpere ist ein unabhängiges Community-Projekt für Solaranlagen mit Batteriespeicher. Es läuft komplett lokal und braucht keine Cloud.</p>
        <p className="hint">Alle genannten Produktnamen und Marken gehören ihren jeweiligen Inhabern. Rechtliche Hinweise und Hintergrund: siehe README im Quellcode.</p>
        <p className="hint">FoxESS-Registerdefinitionen basieren auf foxess_modbus (MIT-Lizenz).</p>
        <p className="hint">Wallboxen steuert <a href="https://evcc.io" target="_blank" rel="noreferrer">evcc</a>, ein
          eigenständiges Open-Source-Projekt. OpenAmpere nutzt dessen offene Schnittstelle. Danke an die evcc-Community!</p>
        {!DEMO && <p className="hint">Diese Installation betreibst du selbst auf deinem Rechner. OpenAmpere sendet keine
          Daten an das Projekt.</p>}
      </div>
      <div className="card menu">
        <a className="menu-row" href={IMPRINT_URL} target="_blank" rel="noopener noreferrer">
          <span>Projektseite und Impressum<span className="menu-hint">Wer hinter OpenAmpere steht</span></span><span aria-hidden>↗</span>
        </a>
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
      const data = await postFile<{ days: number; inserted: number }>("/api/import/cloud/file", file);
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
  // two steps, the percentage covers both: energy values first, then the battery's state of charge (#17)
  const phaseText = !job?.phase ? "Verbinde mit der EKD-Cloud …"
    : job.phase === "search" ? "Suche den Beginn deiner Aufzeichnungen …"
    : job?.phase === "soc" ? `Schritt 2 von 2: Ladestand des Speichers, ${job.soc_done} von ${total} Tagen`
    : `Schritt 1 von 2: Energiewerte, ${job?.work_done ?? 0} von ${total} Tagen`;

  return (
    <>
      <div className="section-title">Verlauf aus der EKD-Cloud</div>
      <div className="card form">
        <p className="hint">Übernimm deinen Verlauf aus der App „Ampere.IQ“, solange die EKD-Cloud noch läuft. Den Schlüssel
          findest du in der Ampere.IQ-App unter <strong>Mehr → Konfiguration API-Zugang</strong>.</p>
        <p className="hint"><strong>OpenAmpere ist unabhängig und hat nichts mit EKD zu tun.</strong></p>
        <LearnMore summary="Mehr dazu">
          <p className="hint">OpenAmpere ist ein unabhängiges Projekt und hat nichts mit der Energiekonzepte Deutschland
            GmbH (EKD) zu tun. Es wurde von EKD weder beauftragt noch autorisiert. Der Import nutzt ausschließlich die
            Kunden-API der EKD-Cloud mit deinem persönlichen Schlüssel. „EKD“ und „Ampere.IQ“ sind Bezeichnungen ihrer Inhaber.</p>
        </LearnMore>

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
              {total > 0 && <strong className="nowrap">{Math.round(progress * 100)} % insgesamt</strong>}
            </div>
            {total > 0 && <div className="bar"><div className="bar-fill" style={{ width: `${progress * 100}%` }} /></div>}
            {job.start && <div className="field-hint">Zeitraum {new Date(job.start).toLocaleDateString("de-DE")} – {new Date(job.end ?? job.start).toLocaleDateString("de-DE")}
              {job.imported ? ` · ${job.imported.toLocaleString("de-DE")} Viertelstunden Energiewerte übernommen` : ""}</div>}
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
        {job && !job.key_set && <p className="field-hint">Zum Starten zuerst den API-Schlüssel speichern.</p>}
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

const FEED_IN_RULES: { id: FeedInRule; label: string; hint: string }[] = [
  { id: "limit_60", label: "60 % der Modulleistung",
    hint: "Solarspitzengesetz: Inbetriebnahme ab 25.02.2025, solange kein intelligentes Messsystem mit Steuerbox eingebaut ist." },
  { id: "limit_70", label: "70 % der Modulleistung",
    hint: "Frühere Regel. Entfallen für Anlagen bis 25 kWp mit Inbetriebnahme nach dem 14.09.2022 und für ältere Anlagen bis 7 kWp." },
  { id: "operator", label: "Fester Wert vom Netzbetreiber",
    hint: "Steht in der Netzanschlusszusage, z. B. Nulleinspeisung. Jede Erhöhung braucht seine schriftliche Zustimmung." },
  { id: "none", label: "Keine Begrenzung",
    hint: "Weder Gesetz noch Netzanschlusszusage begrenzen die Einspeisung." },
  { id: "unknown", label: "Weiß ich nicht",
    hint: "Frag deinen Installationsbetrieb oder Netzbetreiber. Bis dahin braucht jede Erhöhung dessen schriftliche Zustimmung." },
];

function FeedInRuleCard({ onSaved }: { onSaved: () => void }) {
  const { settings, save, locked } = useSettings();
  const [kwp, setKwp] = useState("");
  const [declareNone, setDeclareNone] = useState(false);
  const [declared, setDeclared] = useState(false);
  useEffect(() => { if (settings) setKwp(settings["pv.installed_kwp"] ? String(settings["pv.installed_kwp"]).replace(".", ",") : ""); }, [settings]);
  if (!settings) return null;
  const kwpValue = Number(kwp.replace(",", "."));
  const kwpValid = kwp.trim() === "" || (Number.isFinite(kwpValue) && kwpValue >= 0 && kwpValue <= 1000);
  const kwpChanged = kwpValid && (kwp.trim() === "" ? 0 : kwpValue) !== settings["pv.installed_kwp"];
  const rule = settings["grid.feed_in_rule"];
  const pick = async (id: FeedInRule) => {
    if (id === rule) return;
    if (id === "none") { setDeclared(false); setDeclareNone(true); return; }
    if (await save({ "grid.feed_in_rule": id })) onSaved();
  };

  return (
    <>
      <div className="section-title">Deine Anlage</div>
      <div className="card form">
        <Field label="Installierte Modulleistung" locked={locked("pv.installed_kwp")}
          hint="Summe aller Module, z. B. aus dem Marktstammdatenregister oder der Rechnung. Die Prozentregeln beziehen sich darauf – nicht auf den Wechselrichter.">
          <div className="input-unit">
            <input className="input" inputMode="decimal" value={kwp} placeholder="z. B. 9,8" disabled={locked("pv.installed_kwp")}
              onChange={(e) => setKwp(e.target.value)} />
            <span>kWp</span>
          </div>
        </Field>
        {kwpChanged && (
          <Button onClick={async () => { if (await save({ "pv.installed_kwp": kwp.trim() === "" ? 0 : kwpValue })) onSaved(); }}>
            Modulleistung speichern
          </Button>
        )}
      </div>
      <div className="section-title">Welche Begrenzung gilt für dich?</div>
      <div className="card choices">
        {FEED_IN_RULES.map((r) => (
          <button key={r.id} className={`choice ${rule === r.id ? "active" : ""}`} disabled={locked("grid.feed_in_rule")}
            onClick={() => void pick(r.id)}>
            <span className="radio" />
            <span><strong>{r.label}</strong><span className="meta">{r.hint}</span></span>
          </button>
        ))}
      </div>
      {declareNone && (
        <Dialog title="Keine Begrenzung erklären?" danger confirm="Erklärung abgeben" disabled={!declared}
          onCancel={() => setDeclareNone(false)}
          onConfirm={async () => { setDeclareNone(false); if (await save({ "grid.feed_in_rule": "none" })) onSaved(); }}>
          <p>Danach kannst du die Einspeisung bis zur Leistung des Wechselrichters freigeben, ohne weitere Nachfrage.</p>
          <Checkbox checked={declared} onChange={setDeclared}>
            Ich erkläre, dass für meine Anlage <strong>weder gesetzlich noch in der Netzanschlusszusage</strong> eine
            Begrenzung der Einspeisung gilt – zum Beispiel, weil ein intelligentes Messsystem mit Steuerbox eingebaut ist.
          </Checkbox>
          <p className="hint">Die Erklärung wird im Protokoll gespeichert.</p>
        </Dialog>
      )}
    </>
  );
}

export function ExportLimitPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data: current, error, reload } = useResource<ExportLimit>("/api/grid/export-limit");
  const [preset, setPreset] = useState<"max" | "custom">("custom");
  const [custom, setCustom] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [reference, setReference] = useState("");
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);

  const rated = current?.rated_power_w ?? null;
  const rule = current?.rule ?? "unknown";
  const kwp = current?.installed_kwp || 0;
  // highest value allowed: the declared legal share of the module power, never more than the inverter can do
  const legalMax = current?.legal_max_w ?? null;
  const cap = legalMax != null ? Math.min(legalMax, rated ?? legalMax) : rated;
  const hasPreset = cap != null && (legalMax != null || rule === "none");
  const presetLabel = rule === "none" ? "Keine Begrenzung" : `${rule === "limit_70" ? 70 : 60} % (${watt(cap)})`;

  // start from the current value, so nothing is "changed" (and no warning shown) until the user picks something
  useEffect(() => {
    if (!current?.supported || current.limit_w == null) return;
    setPreset(hasPreset && current.limit_w === cap ? "max" : "custom");
    setCustom(String(current.limit_w));
  }, [current, hasPreset, cap]);

  const target = preset === "max" && cap != null ? cap : Math.round(Number(custom.replace(",", ".")));
  const valid = Number.isFinite(target) && target >= 0 && target <= (cap ?? 99_999);
  const raising = current?.limit_w == null || (valid && target > current.limit_w);
  const needsConsent = raising && legalMax == null && rule !== "none";
  const unchanged = valid && target === current?.limit_w;
  const editable = !!status?.control.enabled && !!current?.supported;
  const canSubmit = editable && valid && !unchanged && (!needsConsent || (confirmed && reference.trim().length >= 3));
  const pct = (w: number | null | undefined) => (kwp && w != null ? ` (${Math.round(w / (kwp * 10))} % der Modulleistung)` : "");

  const submit = async () => {
    setDialog(false);
    setBusy(true);
    try {
      const r = await putJson<{ dry_run: boolean; result?: string }>("/api/grid/export-limit", {
        limit_w: target, grid_operator_confirmed: confirmed, confirmation_reference: reference,
      });
      toast(r.dry_run ? "Testmodus: Änderung wurde nur protokolliert" : r.result === "ok" ? "Einspeisebegrenzung geändert" : r.result ?? "Gespeichert");
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
      <Notice kind="warn"><strong>Rechtlich vorgegeben:</strong> Wer mehr einspeist als erlaubt, riskiert Zahlungen an den
        Netzbetreiber.</Notice>
      <LearnMore>
        <p className="hint">Die Begrenzung folgt aus dem Gesetz (z. B. 60 % der Modulleistung nach dem Solarspitzengesetz)
          oder aus deiner Netzanschlusszusage. Verstöße können nach § 52 EEG Zahlungen auslösen. Normalerweise stellt der
          Installationsbetrieb die Begrenzung ein.</p>
      </LearnMore>

      <FeedInRuleCard onSaved={reload} />

      {!current ? <LoadState error={error} onRetry={reload} /> : (
        <>
          <div className="section-title">Aktuell im Wechselrichter</div>
          <div className="card">
            {current.supported ? (
              <>
                <div className="big-value">{watt(current.limit_w)}</div>
                <p className="hint">
                  {kwp ? `${Math.round((current.limit_w ?? 0) / (kwp * 10))} % von ${kwp.toLocaleString("de-DE")} kWp` : "Modulleistung nicht angegeben"}
                  {rated ? ` · Wechselrichter max. ${watt(rated)}` : ""}
                </p>
                {legalMax != null && current.limit_w != null && current.limit_w > legalMax && (
                  <Notice kind="error">Der eingestellte Wert liegt über dem, was die gewählte Regel erlaubt ({watt(legalMax)}).</Notice>
                )}
              </>
            ) : (
              <p className="hint">Bei diesem Gerät lässt sich die Einspeisebegrenzung nicht über Modbus lesen oder ändern.
                Wende dich an einen Elektrofachbetrieb.</p>
            )}
          </div>

          {current.supported && (
            <>
              <div className="section-title">Neue Begrenzung</div>
              {(rule === "limit_60" || rule === "limit_70") && !kwp && (
                <Notice kind="info">Gib oben die Modulleistung an, damit OpenAmpere den erlaubten Wert berechnen kann.</Notice>
              )}
              <ControlModeBar compact />
              <div className="card form">
                {hasPreset && (
                  <Segmented value={preset} onChange={setPreset} disabled={!editable}
                    options={[["max", presetLabel], ["custom", "Eigener Wert"]]} />
                )}
                {preset === "custom" && (
                  <Field label="Maximale Einspeiseleistung" hint={cap != null ? `Höchstens ${watt(cap)}` : undefined}>
                    <div className="input-unit">
                      <input className="input" inputMode="numeric" value={custom} disabled={!editable}
                        onChange={(e) => setCustom(e.target.value)} placeholder={cap != null ? `0 – ${cap}` : "z. B. 6000"} />
                      <span>W</span>
                    </div>
                  </Field>
                )}
                {valid && !unchanged && <p className="hint">Neu: <strong>{watt(target)}</strong>{pct(target)}</p>}
                {!valid && custom.trim() !== "" && cap != null && <p className="hint">Erlaubt sind 0 bis {watt(cap)}.</p>}
                {unchanged && <p className="hint">Das ist bereits der aktuelle Wert.</p>}
              </div>

              {editable && valid && !unchanged && needsConsent && (
                <>
                  <Notice kind="error">
                    <strong>Du erhöhst die Einspeiseleistung.</strong> Bei einem festen Wert vom Netzbetreiber (oder wenn
                    du die Regel nicht kennst) ist das nur mit dessen schriftlicher Zustimmung zulässig. Je nach
                    Netzbetreiber muss zusätzlich ein eingetragener Elektrofachbetrieb die Änderung vornehmen oder melden.
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
                <Button variant={needsConsent ? "danger" : "primary"} busy={busy} disabled={!canSubmit} onClick={() => setDialog(true)}>
                  Einspeisebegrenzung ändern
                </Button>
              )}
            </>
          )}

          <Notice kind="info">
            Prüfe, ob die Begrenzung bisher von der Smartbox umgesetzt wurde: Dann steht der Wechselrichter womöglich auf
            100 %, und ohne die Box gilt nur noch der Wert hier.
          </Notice>
        </>
      )}

      {dialog && current && (
        <Dialog title="Einspeisebegrenzung wirklich ändern?" danger={needsConsent}
          confirm={needsConsent ? "Zustimmung liegt vor – ändern" : "Ändern"} onCancel={() => setDialog(false)} onConfirm={() => void submit()}>
          <p>Bisher: <strong>{watt(current.limit_w)}</strong>{pct(current.limit_w)}<br />Neu: <strong>{watt(target)}</strong>{pct(target)}</p>
          {needsConsent && <p>Du bestätigst, dass die schriftliche Zustimmung deines Netzbetreibers vorliegt ({reference.trim()}).</p>}
          <p>Die Verantwortung für die Einhaltung der Netzanschlussbedingungen liegt bei dir als Anlagenbetreiber.</p>
        </Dialog>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function PvSystemPage({ onBack, snap }: PageProps & { snap: Snapshot | null }) {
  const { settings, save, error, reload } = useSettings();
  const [names, setNames] = useState<string[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  // every input the inverter reports, also unconnected and hidden ones, so they can be hidden or shown again (#58)
  const inputs = (snap?.pv_inputs ?? []).map((input, index) => ({ ...input, index }));
  useEffect(() => {
    if (settings) { setNames(settings["pv.input_names"]); setHidden(settings["pv.hidden_inputs"] ?? []); }
  }, [settings]);
  const toggle = (i: number, show: boolean) =>
    setHidden((h) => (show ? h.filter((x) => x !== String(i + 1)) : [...h, String(i + 1)].sort()));
  const dirty = !!settings && (JSON.stringify(names) !== JSON.stringify(settings["pv.input_names"])
    || JSON.stringify(hidden) !== JSON.stringify(settings["pv.hidden_inputs"] ?? []));
  const count = Math.max(inputs.length, names.length, ...hidden.map(Number).filter(Number.isFinite));
  const setName = (i: number, value: string) => setNames((n) => {
    const next = [...n];
    while (next.length <= i) next.push("");
    next[i] = value;
    return next;
  });

  return (
    <SubPage title="PV-Anlage" onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <p className="hint">Gib deinen Modulfeldern Namen wie „Süddach“ oder „Garage“. Die aktuelle Leistung hilft beim Zuordnen.
        Meldet der Wechselrichter einen Eingang, an dem nichts angeschlossen ist, blende ihn aus.</p>
      {settings && (count === 0 ? (
        <Notice kind="info">Noch keine PV-Eingänge erkannt. Bei Dunkelheit liefern die Eingänge keine Spannung – schau tagsüber noch einmal vorbei.</Notice>
      ) : (
        <div className="card form">
          {Array.from({ length: count }, (_, i) => {
            const live = inputs.find((x) => x.index === i);
            return (
              <div key={i} className="pv-input-row">
                <Field label={`Eingang ${i + 1}`}
                  hint={live ? `jetzt ${kw(live.power)} · ${num(live.voltage, 0)} V` : "derzeit keine Leistung"}>
                  <div className="input-unit">
                    <span className="dot" style={{ background: PV_INPUT_COLORS[i % 4] }} />
                    <input className="input" maxLength={30} value={names[i] ?? ""} placeholder={`Modulfeld ${i + 1}`}
                      disabled={hidden.includes(String(i + 1))} onChange={(e) => setName(i, e.target.value)} />
                  </div>
                </Field>
                <SwitchRow label="Anzeigen" checked={!hidden.includes(String(i + 1))} onChange={(v) => toggle(i, v)} />
              </div>
            );
          })}
          <Button disabled={!dirty}
            onClick={() => save({ "pv.input_names": names.slice(0, 6), "pv.hidden_inputs": hidden })}>Speichern</Button>
          <Unsaved show={dirty} />
          {hidden.length > 0 && <p className="hint">Ausgeblendete Eingänge erscheinen nirgends in Anzeige und Auswertung.
            Ihre Messwerte speichert OpenAmpere weiter, du kannst sie jederzeit wieder einblenden.</p>}
        </div>
      ))}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

type ChargingSettings = { enabled: boolean; mode: "cheapest" | "window"; target_soc: number; ready_by: number;
  window_start: number; window_end: number; max_price_ct: number | null; power_w: number; battery_kwh: number;
  legal_confirmed: boolean };
export type ChargingView = { settings: ChargingSettings; active: boolean; last_error: string | null;
  plan: { quarters: number[]; reason: string; needed_wh?: number; prices?: Record<string, number> } };

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;

/** Consecutive quarter hours as readable ranges: "02:00–03:30". */
function ranges(quarters: number[]): string[] {
  const out: string[] = [];
  const fmt = (ts: number) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });
  let start = quarters[0];
  for (let i = 1; i <= quarters.length; i++) {
    if (i === quarters.length || quarters[i] !== quarters[i - 1] + 900) {
      out.push(`${fmt(start)}–${fmt(quarters[i - 1] + 900)}`);
      start = quarters[i];
    }
  }
  return out;
}

export function ChargingPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data, error, reload, setData } = useResource<ChargingView>("/api/charging", 30_000);
  const [form, setForm] = useState<ChargingSettings | null>(null);
  const [legal, setLegal] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setForm(data.settings); }, [data]);
  const rated = status?.device?.rated_power_w ?? 10_000;
  const { settings, save: saveSettings } = useSettings();
  const [batteryMax, setBatteryMax] = useState("");
  useEffect(() => { if (settings) setBatteryMax(settings["battery.max_charge_kw"] ? de(settings["battery.max_charge_kw"]) : ""); }, [settings]);
  const batteryMaxKw = batteryMax.trim() ? toNumber(batteryMax) : 0;
  // levels like the former app: 50 / 75 / 100 % of what the battery allows (datasheet), at most the inverter (#23)
  const base = Math.min(rated, batteryMaxKw > 0 ? batteryMaxKw * 1000 : rated);
  const level = (share: number) => Math.round((base * share) / 100) * 100;
  const set = (patch: Partial<ChargingSettings>) => setForm((f) => (f ? { ...f, ...patch } : f));
  const maxChanged = !!settings && batteryMaxKw !== (settings["battery.max_charge_kw"] ?? 0);
  const changed = (form && data && JSON.stringify(form) !== JSON.stringify(data.settings)) || maxChanged;

  const save = async (next: ChargingSettings) => {
    setBusy(true);
    try {
      if (maxChanged && !(await saveSettings({ "battery.max_charge_kw": Number.isFinite(batteryMaxKw) ? batteryMaxKw : 0 }))) return;
      setData(await putJson<ChargingView>("/api/charging", next));
      toast("Gespeichert");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Laden aus dem Netz" onBack={onBack}>
      <p className="hint">Lädt den Speicher aus dem Netz, wenn Strom günstig ist oder in einem festen Zeitfenster
        (z. B. Nachtstrom). <strong>Experimentell.</strong></p>
      <LearnMore>
        <p className="hint">OpenAmpere nutzt dafür die Fernsteuerung des Wechselrichters mit Zeitbegrenzung: Stoppt
          OpenAmpere, kehrt der Wechselrichter nach 3 Minuten von selbst in den Normalbetrieb zurück. Für „Günstigste Zeit“
          brauchst du einen dynamischen oder zeitvariablen Stromtarif.</p>
      </LearnMore>
      <ControlModeBar compact />
      {!form && <LoadState error={error} onRetry={reload} />}
      {form && data && (
        <>
          <div className="card form">
            <SwitchRow label="Laden aus dem Netz" checked={form.enabled}
              hint={data.active ? "Lädt gerade." : data.plan.reason}
              onChange={(v) => (v && !form.legal_confirmed ? setLegal(true) : void save({ ...form, enabled: v }))} />
            {data.last_error && <Notice kind="error">{data.last_error}</Notice>}
            {form.enabled && data.plan.quarters.length > 0 && (
              <p className="hint">Geplant: {ranges(data.plan.quarters).join(", ")} Uhr
                {data.plan.needed_wh ? ` – etwa ${num(data.plan.needed_wh / 1000, 1)} kWh` : ""}.</p>
            )}
          </div>

          <div className="card form">
            <Segmented value={form.mode} onChange={(mode) => set({ mode })}
              options={[["cheapest", "Günstigste Zeit"], ["window", "Festes Zeitfenster"]]} />
            <Field label="Laden bis" hint="Ladestand, bei dem das Laden aus dem Netz endet.">
              <Slider value={form.target_soc} min={20} max={100} unit="%" onChange={(v) => set({ target_soc: v })} />
            </Field>
            {form.mode === "cheapest" ? (
              <>
                <Field label="Fertig bis" hint="Sucht die günstigsten Viertelstunden bis zu dieser Uhrzeit. Braucht einen dynamischen oder zeitvariablen Tarif.">
                  <select className="input" value={form.ready_by} onChange={(e) => set({ ready_by: Number(e.target.value) })}>
                    {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                  </select>
                </Field>
                <Field label="Höchstpreis (optional)" hint="Darüber wird nie geladen, auch wenn das Ziel nicht erreicht wird.">
                  <div className="input-unit"><input className="input" inputMode="decimal"
                    value={form.max_price_ct == null ? "" : String(form.max_price_ct).replace(".", ",")}
                    onChange={(e) => set({ max_price_ct: e.target.value.trim() === "" ? null : Number(e.target.value.replace(",", ".")) })} />
                    <span>ct/kWh</span></div>
                </Field>
              </>
            ) : (
              <div className="field-row">
                <Field label="Von">
                  <select className="input" value={form.window_start} onChange={(e) => set({ window_start: Number(e.target.value) })}>
                    {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                  </select>
                </Field>
                <Field label="Bis">
                  <select className="input" value={form.window_end} onChange={(e) => set({ window_end: Number(e.target.value) })}>
                    {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                  </select>
                </Field>
              </div>
            )}
            <Field label="Zulässige Ladeleistung des Speichers"
              hint="Laut Datenblatt, bei kleinen Speichern oft weniger als der Wechselrichter kann, z. B. 5,5 kW bei 6,6 kWh. Leer lassen, wenn unbekannt.">
              <div className="input-unit"><input className="input" inputMode="decimal" value={batteryMax} placeholder={de(rated / 1000)}
                onChange={(e) => setBatteryMax(e.target.value)} /><span>kW</span></div>
            </Field>
            <Field label="Ladeleistung">
              <Segmented value={form.power_w === level(0.5) ? "gentle" : form.power_w === level(0.75) ? "fast"
                : form.power_w === level(1) ? "max" : "custom"}
                onChange={(v) => v !== "custom" && set({ power_w: level(({ gentle: 0.5, fast: 0.75, max: 1 } as const)[v]) })}
                options={[["gentle", "Schonend"], ["fast", "Schnell"], ["max", "Maximal"], ["custom", `${num(form.power_w / 1000, 1)} kW`]]} />
            </Field>
            <p className="hint">Schonend {num(level(0.5) / 1000, 1)}&nbsp;kW, schnell {num(level(0.75) / 1000, 1)}&nbsp;kW,
              maximal {num(level(1) / 1000, 1)}&nbsp;kW: 50, 75 und 100&nbsp;% der zulässigen Ladeleistung.</p>
            {form.power_w > base && <Notice kind="warn">Die eingestellte Ladeleistung ist höher, als der Speicher zulässt.
              Bitte eine Stufe wählen.</Notice>}
            {form.power_w > 4200 && <p className="hint">Über 4,2 kW Ladeleistung aus dem Netz kann der Speicher unter § 14a EnWG
              (steuerbare Verbraucher) fallen. Kläre das mit deinem Netzbetreiber.</p>}
            <Field label="Nutzbare Speichergröße" hint="Aus dem Datenblatt, für die Berechnung der Ladedauer.">
              <div className="input-unit"><input className="input" inputMode="decimal" value={String(form.battery_kwh).replace(".", ",")}
                onChange={(e) => set({ battery_kwh: Number(e.target.value.replace(",", ".")) || 0 })} /><span>kWh</span></div>
            </Field>
            <Button busy={busy} disabled={!changed} onClick={() => void save(form)}>Speichern</Button>
            <Unsaved show={!!changed} />
          </div>
        </>
      )}
      {legal && form && (
        <Dialog title="Laden aus dem Netz einschalten?" confirm="Einschalten" danger disabled={!form.legal_confirmed}
          onCancel={() => { setLegal(false); set({ legal_confirmed: false }); }}
          onConfirm={() => { setLegal(false); void save({ ...form, enabled: true }); }}>
          <p>Lädt der Speicher Netzstrom, kann das die EEG-Vergütung für Strom betreffen, der später aus dem Speicher
            eingespeist wird (Ausschließlichkeitsprinzip). Seit 2025 gibt es dafür Abgrenzungs- und Pauschalregeln, die
            beim Netzbetreiber angemeldet werden müssen. Ein Speicher mit mehr als 4,2 kW Netzladeleistung kann zudem
            unter § 14a EnWG fallen.</p>
          <p className="hint">OpenAmpere lädt nur und entlädt nie ins Netz. Kläre die Anmeldung mit deinem Netzbetreiber
            oder Steuerberater, bevor du die Funktion nutzt.</p>
          <Checkbox checked={form.legal_confirmed} onChange={(v) => set({ legal_confirmed: v })}>
            Ich habe das gelesen und kläre die Anmeldung selbst.
          </Checkbox>
        </Dialog>
      )}
    </SubPage>
  );
}
