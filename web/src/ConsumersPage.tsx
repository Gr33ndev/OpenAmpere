import { useEffect, useState } from "react";
import { putJson, useResource } from "./api";
import { DeviceIcon } from "./DevicesPage";
import type { PageProps } from "./SettingsPages";
import { AmountInput, Button, Field, LoadState, SubPage, toast } from "./ui";
import { amountInput } from "./format";

export type ConsumerData = {
  id?: string; name: string; kind: "mypv" | "shelly1" | "shelly2" | "http"; host: string; port: number; unit: number;
  channel: number; url_on: string; url_off: string; power_w: number; min_power_w: number; min_on_min: number;
  min_off_min: number; battery_min_soc: number; price_limit_ct: number | null; enabled: boolean;
  state?: { on: boolean | null; power_w: number; since: number; error: string | null; temperature_c: number | null;
    target_c: number | null; status: string | null; actual_w: number | null };
};

type Choice = "mypv" | "shelly" | "http";
const CHOICES: { id: Choice; title: string; text: string; kind: "heating_rod" | "switch" }[] = [
  { id: "mypv", title: "Heizstab von my-PV", text: "AC ELWA-E, AC ELWA 2 oder AC THOR. Nimmt stufenlos genau so viel Sonnenstrom, wie übrig ist.", kind: "heating_rod" },
  { id: "shelly", title: "Gerät mit Shelly-Relais", text: "z. B. Wärmepumpe (SG-Ready-Kontakt) oder Heizstab mit fester Leistung. Wird ein- und ausgeschaltet.", kind: "switch" },
  { id: "http", title: "Eigene Web-Adressen", text: "Für andere Geräte, die sich über eine Adresse zum Ein- und eine zum Ausschalten steuern lassen.", kind: "switch" },
];

function template(choice: Choice): ConsumerData {
  const base = { name: "", host: "", port: 502, unit: 1, channel: 0, url_on: "", url_off: "", min_power_w: 500,
    battery_min_soc: 0, price_limit_ct: null, enabled: true };
  if (choice === "mypv") return { ...base, name: "Heizstab", kind: "mypv", power_w: 3000, min_on_min: 0, min_off_min: 0 };
  if (choice === "shelly") return { ...base, name: "Wärmepumpe", kind: "shelly2", power_w: 2000, min_on_min: 10, min_off_min: 5 };
  return { ...base, name: "Gerät", kind: "http", power_w: 1000, min_on_min: 10, min_off_min: 5 };
}

const kindLabel = (c: ConsumerData) => (c.kind === "mypv" ? "Heizstab von my-PV" : c.kind === "http" ? "Eigene Web-Adressen" : "Shelly-Relais");
const num = (v: string, fallback = 0) => (v.trim() === "" ? fallback : Number(v.replace(",", ".")) || 0);

function Editor({ value, onSave, onCancel, onRemove, busy }: {
  value: ConsumerData; onSave: (c: ConsumerData) => void; onCancel: () => void; onRemove?: () => void; busy: boolean;
}) {
  const [c, setC] = useState(value);
  const set = (patch: Partial<ConsumerData>) => setC((x) => ({ ...x, ...patch }));
  const mypv = c.kind === "mypv";
  const shelly = c.kind === "shelly1" || c.kind === "shelly2";
  const valid = c.name.trim() && (c.kind === "http" ? c.url_on.trim() && c.url_off.trim() : c.host.trim()) && c.power_w > 0;
  return (
    <div className="card form">
      <div className="device-card-head">
        <DeviceIcon kind={mypv ? "heating_rod" : "switch"} size={36} />
        <strong className="grow">{kindLabel(c)}</strong>
      </div>
      <Field label="Name"><input className="input" value={c.name} maxLength={40} onChange={(e) => set({ name: e.target.value })} /></Field>
      {c.kind === "http" ? (
        <>
          <Field label="Adresse zum Einschalten"><input className="input" value={c.url_on} placeholder="http://" onChange={(e) => set({ url_on: e.target.value })} /></Field>
          <Field label="Adresse zum Ausschalten"><input className="input" value={c.url_off} placeholder="http://" onChange={(e) => set({ url_off: e.target.value })} /></Field>
        </>
      ) : (
        <Field label={mypv ? "IP-Adresse des Heizstabs" : "IP-Adresse des Shelly"}>
          <input className="input" value={c.host} inputMode="decimal" placeholder="z. B. 192.168.178.40" onChange={(e) => set({ host: e.target.value })} />
        </Field>
      )}
      <Field label={mypv ? "Höchstens nutzen" : "Leistung des Geräts"}
        hint={mypv ? "So viel Leistung bekommt der Heizstab höchstens." : "Eingeschaltet wird, sobald so viel Sonnenstrom übrig ist."}>
        <div className="input-unit"><input className="input" inputMode="numeric" value={c.power_w}
          onChange={(e) => set({ power_w: num(e.target.value) })} /><span>W</span></div>
      </Field>
      {mypv && <p className="hint">Im Webinterface des Heizstabs die Ansteuerung auf „Modbus TCP“ stellen und den Zeitablauf
        der Ansteuerung auf 60 Sekunden. Bekommt er keine Vorgabe mehr, schaltet er sich dann von selbst ab.</p>}

      <details className="advanced">
        <summary>Erweitert</summary>
        {mypv && (
          <Field label="Mindestüberschuss zum Starten" hint="Erst ab so viel übrigem Sonnenstrom beginnt der Heizstab.">
            <div className="input-unit"><input className="input" inputMode="numeric" value={c.min_power_w}
              onChange={(e) => set({ min_power_w: num(e.target.value) })} /><span>W</span></div>
          </Field>
        )}
        {mypv && (
          <Field label="Port"><input className="input" inputMode="numeric" value={c.port} onChange={(e) => set({ port: num(e.target.value, 502) })} /></Field>
        )}
        {shelly && (
          <>
            <Field label="Shelly-Generation">
              <select className="input" value={c.kind} onChange={(e) => set({ kind: e.target.value as ConsumerData["kind"] })}>
                <option value="shelly2">Plus, Pro und neuer (Generation 2+)</option>
                <option value="shelly1">Generation 1</option>
              </select>
            </Field>
            <Field label="Kanal" hint="Bei Shellys mit mehreren Relais, sonst 0.">
              <input className="input" inputMode="numeric" value={c.channel} onChange={(e) => set({ channel: num(e.target.value) })} />
            </Field>
          </>
        )}
        {!mypv && (
          <div className="field-row">
            <Field label="Mindestlaufzeit"><div className="input-unit"><input className="input" inputMode="numeric" value={c.min_on_min}
              onChange={(e) => set({ min_on_min: num(e.target.value) })} /><span>min</span></div></Field>
            <Field label="Mindestpause"><div className="input-unit"><input className="input" inputMode="numeric" value={c.min_off_min}
              onChange={(e) => set({ min_off_min: num(e.target.value) })} /><span>min</span></div></Field>
          </div>
        )}
        <Field label="Auch mit günstigem Netzstrom" hint="Nur mit dynamischem oder zeitvariablem Tarif. Leer lassen für nur Sonnenstrom.">
          <div className="input-unit"><AmountInput value={c.price_limit_ct} format={amountInput}
            onChange={(v) => set({ price_limit_ct: v })} /><span>ct/kWh</span></div>
        </Field>
      </details>

      <div className="button-row">
        <Button busy={busy} disabled={!valid} onClick={() => onSave(c)}>Speichern</Button>
        <Button variant="secondary" onClick={onCancel}>Abbrechen</Button>
        {onRemove && <button className="link danger-link" onClick={onRemove}>Gerät entfernen</button>}
      </div>
    </div>
  );
}

export function ConsumersPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<{ consumers: ConsumerData[] }>("/api/consumers", 15_000);
  const [list, setList] = useState<ConsumerData[] | null>(null);
  const [editing, setEditing] = useState<number | "choose" | ConsumerData | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setList(data.consumers); }, [data]);

  const persist = async (next: ConsumerData[]) => {
    setBusy(true);
    try {
      const saved = await putJson<{ consumers: ConsumerData[] }>("/api/consumers", { consumers: next.map(({ state: _s, ...c }) => c) });
      setData(saved);
      setEditing(null);
      toast("Gespeichert");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (!list) {
    body = <LoadState error={error} onRetry={reload} />;
  } else if (editing === "choose") {
    body = (
      <>
        <div className="section-title">Was möchtest du hinzufügen?</div>
        <div className="card choices">
          {CHOICES.map((ch) => (
            <button key={ch.id} className="choice" onClick={() => setEditing(template(ch.id))}>
              <DeviceIcon kind={ch.kind} size={36} />
              <span><strong>{ch.title}</strong><span className="meta">{ch.text}</span></span>
            </button>
          ))}
        </div>
        <Button variant="secondary" onClick={() => setEditing(null)}>Abbrechen</Button>
      </>
    );
  } else if (typeof editing === "number") {
    body = <Editor value={list[editing]} busy={busy} onCancel={() => setEditing(null)}
      onSave={(c) => void persist(list.map((x, i) => (i === editing ? c : x)))}
      onRemove={() => void persist(list.filter((_, i) => i !== editing))} />;
  } else if (editing) {
    body = <Editor value={editing} busy={busy} onCancel={() => setEditing(null)} onSave={(c) => void persist([...list, c])} />;
  } else {
    body = (
      <>
        {list.length ? (
          <div className="card menu">
            {list.map((c, i) => (
              <button key={c.id ?? i} className="device-row-compact" onClick={() => setEditing(i)}>
                <DeviceIcon kind={c.kind === "mypv" ? "heating_rod" : "switch"} size={36} />
                <span className="grow"><strong>{c.name}</strong><span className="menu-hint">{kindLabel(c)} · {c.kind === "http" ? "Web-Adressen" : c.host}</span></span>
                <span className="link">Bearbeiten</span>
              </button>
            ))}
          </div>
        ) : <p className="hint">Noch keine Geräte eingerichtet.</p>}
        {list.length < 8 && <Button onClick={() => setEditing("choose")}>Gerät hinzufügen</Button>}
        <p className="hint">Bedienen kannst du die Geräte unter „Geräte“. Dort legst du auch fest, wer zuerst Sonnenstrom
          bekommt. Wärmepumpen bitte nur über ihren SG-Ready- oder EVU-Eingang schalten und von einer Elektrofachkraft
          anschließen lassen.</p>
      </>
    );
  }

  return <SubPage title="Heizstab und weitere Geräte" onBack={editing !== null ? () => setEditing(null) : onBack}>{body}</SubPage>;
}
