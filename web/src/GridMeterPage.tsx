import { useEffect, useState } from "react";
import { postJson, useResource } from "./api";
import { updatedLabel } from "./format";
import type { PageProps } from "./SettingsPages";
import { useSettings } from "./SettingsPages";
import { Button, Checkbox, Field, LoadState, Notice, SubPage, toast } from "./ui";

type Provider = { key: string; label: string; portal: string; region: string };
type Meter = { id: string; name: string; kinds: ("import" | "export")[] };
export type GridMeterView = {
  providers: Provider[]; configured: boolean; meters: Meter[]; active: string[];
  synced: number | null; until: string | null; error: string | null; busy: boolean;
};

const MISSING_URL = "https://github.com/Gr33ndev/OpenAmpere/issues/new?template=feature_request.yml&title=Netzbetreiber%3A+";
const KIND = { import: "Bezug", export: "Einspeisung" };
const dayLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("de-DE", { day: "numeric", month: "long" });

/** Settings: fetch the daily values of the grid operator's smart meter from its customer portal (#60). */
export function GridMeterPage({ onBack }: PageProps) {
  const { settings, secrets, save, locked, error, reload } = useSettings();
  const { data: view, setData: setView, reload: reloadView } = useResource<GridMeterView>("/api/gridmeter");
  const [provider, setProvider] = useState("none");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!settings) return;
    setProvider(settings["meter.provider"]);
    setUsername(settings["meter.username"]);
  }, [settings]);
  const chosen = view?.providers.find((p) => p.key === provider);
  const passwordSet = secrets?.["meter.password"]?.set ?? false;
  const changed = !!settings && (provider !== settings["meter.provider"] || username !== settings["meter.username"] || !!password);

  const fetchNow = async () => {
    const result = await postJson<GridMeterView>("/api/gridmeter/sync", {});
    setView(result);
    if (result.error) toast(result.error, "error");
    else if (result.until) toast(`Zählerwerte vollständig bis ${dayLabel(result.until)}`);
  };

  const submit = async () => {
    setBusy(true);
    try {
      const ok = await save({ "meter.provider": provider, "meter.username": username,
        ...(password ? { "meter.password": password } : {}) });
      setPassword("");
      if (ok && provider !== "none") await fetchNow();
      else reloadView();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const toggleMeter = (id: string, on: boolean) => {
    if (!view) return;
    const next = on ? [...view.active, id] : view.active.filter((m) => m !== id);
    if (!next.length) return toast("Mindestens ein Zähler muss zählen.", "error");
    void save({ "meter.meter_ids": next }).then(() => reloadView());
  };

  return (
    <SubPage title="Zählerwerte" onBack={onBack}>
      <p className="hint">Abgerechnet wird nach dem Zähler deines Netzbetreibers. Hast du ein intelligentes Messsystem (Smart
        Meter), zeigt der Netzbetreiber die Tageswerte in seinem Kundenportal. OpenAmpere holt sie dort ab und vergleicht
        deine Abschläge dann mit diesen Werten statt mit denen des Wechselrichters.</p>
      {(!settings || !view) && <LoadState error={error} onRetry={reload} />}
      {view?.configured && view.error && <Notice kind="error">{view.error}</Notice>}
      {view?.configured && !view.error && view.until && (
        <Notice kind="ok">Zählerwerte vollständig bis {dayLabel(view.until)}{view.synced ? ` · abgerufen ${updatedLabel(view.synced).replace(/^(Heute|Gestern)/, (w) => w.toLowerCase())}` : ""}</Notice>
      )}

      {settings && view && (
        <div className="card form">
          <Field label="Netzbetreiber" locked={locked("meter.provider")}
            hint={chosen ? `Netzgebiet: ${chosen.region}. Anmeldung wie im Kundenportal ${chosen.portal}.` : "Steht auf deiner Stromrechnung oder am Zähler."}>
            <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="none">Keiner</option>
              {view.providers.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </Field>
          {provider !== "none" && <>
            <Field label="E-Mail" locked={locked("meter.username")}>
              <input className="input" type="email" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
            </Field>
            <Field label="Passwort" hint={passwordSet ? "Gespeichert. Leer lassen, um es zu behalten." : undefined}>
              <input className="input" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <p className="hint">Die Zugangsdaten bleiben auf deinem Gerät und gehen nur an {chosen?.label ?? "den Netzbetreiber"}.
              Mit Zwei-Faktor-Anmeldung klappt der Abruf nicht.</p>
          </>}
          <Button busy={busy} disabled={!changed || (provider !== "none" && (!username || (!password && !passwordSet)))}
            onClick={() => void submit()}>{provider === "none" ? "Speichern" : "Speichern und abrufen"}</Button>
          {view.configured && !changed && (
            <button className="link" disabled={view.busy} onClick={() => void fetchNow().catch((e) => toast((e as Error).message, "error"))}>
              Jetzt abrufen</button>
          )}
        </div>
      )}

      {view?.configured && view.meters.length > 0 && <>
        <div className="section-title">Zähler</div>
        <div className="card">
          {view.meters.length > 1 && <p className="hint">Wähle die Zähler, die zu deinem Hausanschluss gehören. Ein eigener
            Zähler, z. B. für die Wärmepumpe, wird getrennt abgerechnet.</p>}
          <ul className="sessions">
            {view.meters.map((m) => (
              <li key={m.id}>
                {view.meters.length > 1
                  ? <Checkbox checked={view.active.includes(m.id)} onChange={(on) => toggleMeter(m.id, on)}>
                      <strong>{m.name}</strong> {m.kinds.length > 1 && <span className="meta">{m.kinds.map((k) => KIND[k]).join(" und ")}</span>}</Checkbox>
                  : <span><strong>{m.name}</strong> {m.kinds.length > 1 && <span className="meta">{m.kinds.map((k) => KIND[k]).join(" und ")}</span>}</span>}
              </li>
            ))}
          </ul>
        </div>
      </>}

      <p className="hint">Der Netzbetreiber stellt einen Tag oft erst am Nachmittag danach bereit. OpenAmpere fragt alle drei
        Stunden nach und übernimmt nur vollständige Tage. Bis dahin zählen die Werte des Wechselrichters.</p>
      <p className="hint">Dein Netzbetreiber fehlt? <a href={MISSING_URL} target="_blank" rel="noreferrer">Wünsch ihn dir
        auf GitHub</a>. Jeder Netzbetreiber hat ein eigenes Kundenportal, daher kommt einer nach dem anderen dazu.</p>
    </SubPage>
  );
}
