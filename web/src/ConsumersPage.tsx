import { useEffect, useState } from "react";
import type { Status } from "./api";
import { postJson, putJson, useResource } from "./api";
import type { PageProps } from "./SettingsPages";
import { Button, Field, LoadState, Notice, Slider, SubPage, SwitchRow, toast } from "./ui";

export type ConsumerData = {
  id?: string; name: string; kind: "mypv" | "shelly1" | "shelly2" | "http"; host: string; port: number; unit: number;
  channel: number; url_on: string; url_off: string; power_w: number; min_power_w: number; min_on_min: number;
  min_off_min: number; battery_min_soc: number; price_limit_ct: number | null; enabled: boolean;
  state?: { on: boolean | null; power_w: number; since: number; error: string | null; temperature_c: number | null;
    target_c: number | null; status: string | null; actual_w: number | null };
};

const NEW_CONSUMER: ConsumerData = {
  name: "Heizstab", kind: "mypv", host: "", port: 502, unit: 1, channel: 0, url_on: "", url_off: "",
  power_w: 3000, min_power_w: 500, min_on_min: 0, min_off_min: 0, battery_min_soc: 80, price_limit_ct: null, enabled: true,
};

function liveText(c: ConsumerData, state: ConsumerData["state"]): string {
  if (!state) return "noch nicht verbunden";
  if (state.error) return state.error;
  if (c.kind === "mypv") {
    const parts = [state.status ?? "", state.actual_w ? `${(state.actual_w / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} kW` : ""];
    if (state.temperature_c != null) parts.push(`${state.temperature_c.toLocaleString("de-DE")} °C${state.target_c != null ? ` von ${state.target_c.toLocaleString("de-DE")} °C` : ""}`);
    return parts.filter(Boolean).join(" · ") || "verbunden";
  }
  return state.on == null ? "noch nicht geschaltet" : state.on ? "an" : "aus";
}

export function ConsumersPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data, error, reload, setData } = useResource<{ consumers: ConsumerData[] }>("/api/consumers", 15_000);
  const [forms, setForms] = useState<ConsumerData[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data && forms === null) setForms(data.consumers); }, [data, forms]);
  const update = (i: number, patch: Partial<ConsumerData>) =>
    setForms((f) => f && f.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const move = (i: number, dir: -1 | 1) => setForms((f) => {
    if (!f || i + dir < 0 || i + dir >= f.length) return f;
    const next = [...f];
    [next[i], next[i + dir]] = [next[i + dir], next[i]];
    return next;
  });

  const save = async () => {
    if (!forms) return;
    setBusy(true);
    try {
      const saved = await putJson<{ consumers: ConsumerData[] }>("/api/consumers",
        { consumers: forms.map(({ state: _state, ...c }) => c) });
      setData(saved);
      setForms(saved.consumers);
      toast("Gespeichert");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const test = async (c: ConsumerData, on: boolean) => {
    try {
      setData(await postJson<{ consumers: ConsumerData[] }>(`/api/consumers/${c.id}/switch?on=${on}`, {}));
      toast(status?.control.dry_run ? "Testmodus: nur protokolliert" : on ? "Eingeschaltet" : "Ausgeschaltet");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const live = (id?: string) => data?.consumers.find((c) => c.id === id)?.state;

  return (
    <SubPage title="Überschuss nutzen" onBack={onBack}>
      <p className="hint">Heizstab, Wärmepumpe (SG-Ready-Kontakt) oder andere Geräte bekommen den Solarstrom, der übrig
        ist. Wer oben steht, ist zuerst dran. Ein my-PV-Heizstab folgt dem Überschuss stufenlos, andere Geräte werden
        über ein Shelly-Relais oder zwei Web-Adressen ein- und ausgeschaltet.</p>
      <p className="hint">Eine Wallbox steuert evcc. Wer zuerst Überschuss bekommt, stellst du unter Mehr → Wallbox ein.</p>
      {!status?.control.enabled && (
        <Notice kind="info">Die Steuerung ist ausgeschaltet – es wird nichts geschaltet.{" "}
          <button className="link" onClick={() => onNavigate?.("control")}>Steuerung freigeben</button></Notice>
      )}
      {!forms && <LoadState error={error} onRetry={reload} />}
      {forms?.map((c, i) => {
        const state = live(c.id);
        return (
          <div className="card form" key={c.id ?? `new-${i}`}>
            <div className="consumer-head">
              <strong>{i + 1}. {c.name || "Neues Gerät"}</strong>
              <span className="hint">{liveText(c, state)}</span>
            </div>
            <Field label="Name">
              <input className="input" value={c.name} maxLength={40} onChange={(e) => update(i, { name: e.target.value })} />
            </Field>
            <Field label="Schalten über">
              <select className="input" value={c.kind} onChange={(e) => update(i, { kind: e.target.value as ConsumerData["kind"] })}>
                <option value="mypv">my-PV Heizstab (AC ELWA-E, AC ELWA 2, AC THOR), stufenlos</option>
                <option value="shelly2">Shelly (Plus/Pro, 2. Generation und neuer)</option>
                <option value="shelly1">Shelly (1. Generation)</option>
                <option value="http">Eigene Web-Adressen</option>
              </select>
            </Field>
            {c.kind === "mypv" ? (
              <>
                <div className="field-row">
                  <Field label="IP-Adresse des Heizstabs">
                    <input className="input" value={c.host} inputMode="decimal" onChange={(e) => update(i, { host: e.target.value })} />
                  </Field>
                  <Field label="Port">
                    <input className="input" inputMode="numeric" value={c.port} onChange={(e) => update(i, { port: Number(e.target.value) || 502 })} />
                  </Field>
                </div>
                <p className="hint">Im Webinterface des Heizstabs die Ansteuerung auf „Modbus TCP“ stellen und den
                  Zeitablauf der Ansteuerung auf 60 Sekunden. Bekommt der Heizstab keine Vorgabe mehr, schaltet er sich
                  dann von selbst ab.</p>
              </>
            ) : c.kind === "http" ? (
              <>
                <Field label="Adresse zum Einschalten">
                  <input className="input" value={c.url_on} placeholder="http://" onChange={(e) => update(i, { url_on: e.target.value })} />
                </Field>
                <Field label="Adresse zum Ausschalten">
                  <input className="input" value={c.url_off} placeholder="http://" onChange={(e) => update(i, { url_off: e.target.value })} />
                </Field>
              </>
            ) : (
              <div className="field-row">
                <Field label="IP-Adresse des Shelly">
                  <input className="input" value={c.host} inputMode="decimal" onChange={(e) => update(i, { host: e.target.value })} />
                </Field>
                <Field label="Kanal">
                  <input className="input" inputMode="numeric" value={c.channel}
                    onChange={(e) => update(i, { channel: Number(e.target.value) || 0 })} />
                </Field>
              </div>
            )}
            {c.kind === "mypv" && (
              <Field label="Mindestüberschuss zum Starten" hint="Erst ab diesem Überschuss beginnt der Heizstab. Danach folgt er dem Überschuss Watt für Watt.">
                <div className="input-unit">
                  <input className="input" inputMode="numeric" value={c.min_power_w}
                    onChange={(e) => update(i, { min_power_w: Number(e.target.value) || 0 })} />
                  <span>W</span>
                </div>
              </Field>
            )}
            <Field label={c.kind === "mypv" ? "Höchstens nutzen" : "Leistung des Geräts"}
              hint={c.kind === "mypv" ? "Maximale Leistung, die der Heizstab bekommt." : "Wird eingeschaltet, wenn mindestens so viel Überschuss da ist (plus 200 W Reserve)."}>
              <div className="input-unit">
                <input className="input" inputMode="numeric" value={c.power_w}
                  onChange={(e) => update(i, { power_w: Number(e.target.value) || 0 })} />
                <span>W</span>
              </div>
            </Field>
            <Field label="Speicher zuerst laden bis" hint="Erst ab diesem Ladestand bekommt das Gerät den Überschuss.">
              <Slider value={c.battery_min_soc} min={0} max={100} unit="%" onChange={(v) => update(i, { battery_min_soc: v })} />
            </Field>
            <div className="field-row">
              <Field label="Mindestlaufzeit">
                <div className="input-unit">
                  <input className="input" inputMode="numeric" value={c.min_on_min}
                    onChange={(e) => update(i, { min_on_min: Number(e.target.value) || 0 })} />
                  <span>min</span>
                </div>
              </Field>
              <Field label="Mindestpause">
                <div className="input-unit">
                  <input className="input" inputMode="numeric" value={c.min_off_min}
                    onChange={(e) => update(i, { min_off_min: Number(e.target.value) || 0 })} />
                  <span>min</span>
                </div>
              </Field>
            </div>
            <Field label="Auch mit günstigem Netzstrom (optional)"
              hint="Nur mit dynamischem Tarif: läuft zusätzlich, wenn der Strompreis höchstens so hoch ist. Leer lassen für nur Solarstrom.">
              <div className="input-unit">
                <input className="input" inputMode="decimal" value={c.price_limit_ct == null ? "" : String(c.price_limit_ct).replace(".", ",")}
                  onChange={(e) => update(i, { price_limit_ct: e.target.value.trim() === "" ? null : Number(e.target.value.replace(",", ".")) })} />
                <span>ct/kWh</span>
              </div>
            </Field>
            <SwitchRow label="Automatisch steuern" checked={c.enabled} onChange={(v) => update(i, { enabled: v })} />
            <div className="button-row inline">
              {i > 0 && <button className="link" onClick={() => move(i, -1)}>Nach oben</button>}
              {i < forms.length - 1 && <button className="link" onClick={() => move(i, 1)}>Nach unten</button>}
              {c.id && <button className="link" onClick={() => void test(c, true)}>Test: ein</button>}
              {c.id && <button className="link" onClick={() => void test(c, false)}>Test: aus</button>}
              <button className="link" onClick={() => setForms((f) => f && f.filter((_, j) => j !== i))}>Entfernen</button>
            </div>
          </div>
        );
      })}
      {forms && forms.length < 8 && (
        <Button variant="secondary" onClick={() => setForms((f) => [...(f ?? []), { ...NEW_CONSUMER }])}>Gerät hinzufügen</Button>
      )}
      {forms && <Button busy={busy} onClick={save}>Speichern</Button>}
      <p className="hint">Wärmepumpen bitte nur über ihren SG-Ready- oder EVU-Eingang schalten, nie die Stromversorgung
        trennen. Anschluss nur durch eine Elektrofachkraft.</p>
    </SubPage>
  );
}
