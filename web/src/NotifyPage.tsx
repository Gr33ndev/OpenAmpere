import { useEffect, useState } from "react";
import type { Settings } from "./api";
import { postJson, putJson, useResource } from "./api";
import type { PageProps } from "./SettingsPages";
import { Button, Field, LearnMore, LoadState, Notice, SubPage, SwitchRow, toast } from "./ui";

type NotifyKey = "notify.on_unreachable" | "notify.on_alarm" | "notify.on_overwritten" | "notify.on_battery_full"
  | "notify.on_cheap_power" | "notify.on_firmware" | "notify.on_battery_health";

const EVENTS: { key: NotifyKey; label: string; hint: string }[] = [
  { key: "notify.on_unreachable", label: "Wechselrichter nicht erreichbar", hint: "Nach 15 Minuten ohne Verbindung, und wenn sie wieder steht." },
  { key: "notify.on_alarm", label: "Störung gemeldet", hint: "Wenn der Wechselrichter einen Störungscode meldet." },
  { key: "notify.on_overwritten", label: "Einstellung überschrieben", hint: "Wenn ein anderes Gerät eine Änderung von OpenAmpere zurücksetzt." },
  { key: "notify.on_battery_full", label: "Speicher voll", hint: "Einmal am Tag, guter Moment für große Verbraucher." },
  { key: "notify.on_cheap_power", label: "Günstigster Strom morgen", hint: "Nur mit dynamischem Tarif, sobald die Preise für morgen da sind." },
  { key: "notify.on_battery_health", label: "Speicher prüfen", hint: "Batteriezellen sehr warm oder ungewöhnlich unterschiedlich warm." },
  { key: "notify.on_firmware", label: "Neue Firmware", hint: "Der Wechselrichter meldet eine andere Firmware, etwa nach einem Update." },
];

function randomTopic(): string {
  const bytes = new Uint8Array(9);
  crypto.getRandomValues(bytes);
  return `openampere-${Array.from(bytes, (b) => b.toString(36).padStart(2, "0")).join("").slice(0, 14)}`;
}

export function NotifyPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<Settings>("/api/settings");
  // the cheapest-hour message needs exchange prices, i.e. a dynamic tariff
  const { data: tariffs } = useResource<{ tariffs: { kind: string }[] }>("/api/tariffs");
  const dynamic = !!tariffs?.tariffs.some((t) => t.kind === "dynamic");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setUrl(data.values["notify.ntfy_url"]); }, [data]);
  const values = data?.values;
  const locked = (key: string) => data?.locked.includes(key) ?? false;

  const save = async (changes: Record<string, unknown>) => {
    try {
      setData(await putJson<Settings>("/api/settings", { ...changes, _revision: data?.revision }));
      toast("Gespeichert");
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    }
  };
  const test = async () => {
    setBusy(true);
    try {
      await postJson("/api/notify/test", {});
      toast("Testnachricht gesendet");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Benachrichtigungen" onBack={onBack}>
      {!values && <LoadState error={error} onRetry={reload} />}
      <p className="hint">Hinweise aufs Handy mit der kostenlosen App <strong>ntfy</strong> (iPhone und Android, ohne Konto).</p>
      <LearnMore summary="So geht's">
        <p className="hint">ntfy installieren, unten ein Thema eintragen oder vorschlagen lassen und in der App genau dieses
          Thema abonnieren. Mit „Testnachricht senden“ prüfst du, ob alles ankommt.</p>
      </LearnMore>
      {values && (
        <>
          <div className="card form">
            <Field label="ntfy-Adresse" locked={locked("notify.ntfy_url")}
              hint="Server und Thema, z. B. https://ntfy.sh/ein-langes-zufälliges-wort. Wer das Thema kennt, kann mitlesen – also nichts Erratbares wählen.">
              <input className="input" value={url} placeholder="https://ntfy.sh/" onChange={(e) => setUrl(e.target.value)} />
            </Field>
            {!url && <button className="link" onClick={() => setUrl(`https://ntfy.sh/${randomTopic()}`)}>Zufälliges Thema vorschlagen</button>}
            <Field label="Zugangs-Token (optional)" hint={data?.secrets["notify.ntfy_token"]?.set ? "Gespeichert. Leer lassen, um es zu behalten." : "Nur für geschützte Themen auf eigenen Servern."}>
              <input className="input" type="password" value={token} autoComplete="off" onChange={(e) => setToken(e.target.value)} />
            </Field>
            <Button variant="secondary" disabled={url === values["notify.ntfy_url"] && !token}
              onClick={async () => { if (await save({ "notify.ntfy_url": url, ...(token ? { "notify.ntfy_token": token } : {}) })) setToken(""); }}>
              Speichern
            </Button>
            {values["notify.ntfy_url"] && <Button variant="secondary" busy={busy} onClick={() => void test()}>Testnachricht senden</Button>}
          </div>
          <div className="section-title">Wann benachrichtigen?</div>
          <div className="card form">
            {EVENTS.map((e) => (
              <SwitchRow key={e.key} label={e.label} hint={e.hint} checked={values[e.key] && !(e.key === "notify.on_cheap_power" && !dynamic)}
                disabled={locked(e.key) || (e.key === "notify.on_cheap_power" && !dynamic)}
                onChange={(v) => void save({ [e.key]: v })} />
            ))}
          </div>
          <Notice kind="info">Auf ntfy.sh liegen die Nachrichten kurz auf einem fremden Server. Wer das nicht möchte,
            kann einen eigenen ntfy-Server im Heimnetz betreiben.</Notice>
        </>
      )}
    </SubPage>
  );
}
