import { useState } from "react";
import { deleteJson, postJson, useResource } from "./api";
import { DEMO } from "./demo/flag";
import { timeZone } from "./format";
import { REPO_URL } from "./links";
import { Button, copyText, Dialog, Field, LoadState, Notice, Segmented, SubPage, toast } from "./ui";

type Scope = "read" | "control";
type AppToken = { id: string; name: string; scope: Scope; created: number; last_used: number | null; live_since: number | null };
type PairingRequest = { id: string; name: string; code: string; created: number; expires: number };
type Tokens = { tokens: AppToken[]; pairing: PairingRequest[];
  tls: { port: number | null; fingerprint: string | null; error: string | null } };

const DOCS_URL = `${REPO_URL}/blob/main/docs/homeassistant.de.md`;
// plain links (no images or scripts from other servers in the app): they open the user's own Home Assistant
const HACS_URL = "https://hacs.xyz/docs/use/";
const MY_HA_REPOSITORY = "https://my.home-assistant.io/redirect/hacs_repository/?owner=Gr33ndev&repository=OpenAmpere&category=integration";
const MY_HA_SETUP = "https://my.home-assistant.io/redirect/config_flow_start/?domain=openampere";
const SCOPES: [Scope, string][] = [["read", "Nur lesen"], ["control", "Lesen + Steuern"]];
const when = (ts: number) => new Date(ts * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short", timeZone: timeZone() });

/** Access for other apps such as Home Assistant (#76): one token per app, shown once as a connection code. */
export function AppsPage({ onBack }: { onBack: () => void }) {
  // refreshed every few seconds: a pairing request from Home Assistant should show up right away
  const { data, error, reload, setData } = useResource<Tokens>("/api/tokens", 3000);
  const [name, setName] = useState("Home Assistant");
  const [scope, setScope] = useState<Scope>("read");
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState<string | null>(null);
  const [revoke, setRevoke] = useState<AppToken | null>(null);

  const create = async () => {
    setBusy(true);
    try {
      const result = await postJson<Tokens & { code: string }>("/api/tokens", { name: name.trim(), scope });
      setData({ tokens: result.tokens, pairing: result.pairing, tls: result.tls });
      setCode(result.code);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const confirmRevoke = async () => {
    if (!revoke) return;
    try {
      setData(await deleteJson<Tokens>(`/api/tokens/${revoke.id}`));
      toast(`Zugang „${revoke.name}“ entfernt`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setRevoke(null);
  };

  return (
    <SubPage title="Verbundene Apps" onBack={onBack}>
      <p className="hint">Andere Apps wie Home Assistant können die Daten von OpenAmpere lesen und, wenn du es erlaubst, den
        Speicher steuern. Jede App bekommt einen eigenen Zugang, den du jederzeit entfernen kannst. Die Verbindung ist
        verschlüsselt. <a href={DOCS_URL} target="_blank" rel="noopener">Anleitung für Home Assistant</a></p>
      {!data && <LoadState error={error} onRetry={reload} />}
      {data && !code && <ConnectionStatus data={data} />}

      {data?.tls.error && <Notice kind="warn">HTTPS für andere Apps ist nicht verfügbar: {data.tls.error}. Ein anderes Programm
        nutzt den Port. Abhilfe: in der docker-compose.yml OPENAMPERE_SERVER_TLS_PORT auf einen freien Port setzen, z. B.
        „8444“, und OpenAmpere neu starten.</Notice>}
      {data?.pairing.map((p) => <PairingCard key={p.id} request={p} onDone={setData} />)}

      {code && (
        <div className="card form">
          <h2>Verbindungscode</h2>
          <Notice kind="warn">Der Code wird nur jetzt angezeigt. Er ist wie ein Schlüssel: nicht weitergeben, nur in die App
            einfügen.</Notice>
          <textarea className="input code-box" readOnly rows={4} value={code} onFocus={(e) => e.target.select()} />
          <Button onClick={async () => { if (await copyText(code)) toast("Kopiert"); }}>Kopieren</Button>
          <p className="hint">In Home Assistant: Einstellungen → Geräte & Dienste → Integration hinzufügen → OpenAmpere, dann den
            Code einfügen.</p>
          <Button variant="secondary" onClick={() => setCode(null)}>Fertig</Button>
        </div>
      )}

      {data && !code && (data.tls.port ? (
        <div className="card form">
          <h2>Neue App verbinden</h2>
          <p className="hint">Am einfachsten: In Home Assistant die Integration OpenAmpere hinzufügen und „Mit OpenAmpere
            koppeln“ wählen. Die Anfrage erscheint dann hier. Alternativ hier einen Zugang erstellen und den
            Verbindungscode einfügen:</p>
          <Field label="Name"><input className="input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Berechtigung">
            <Segmented value={scope} onChange={setScope} options={SCOPES} />
          </Field>
          <p className="hint">{scope === "read"
            ? "Die App sieht Leistung, Energie, Ladestand und Zustand, ändern kann sie nichts."
            : "Die App darf zusätzlich Betriebsmodus, Speicher-Grenzen, Laden aus dem Netz und die Betriebsart deiner Geräte ändern – nur, solange die Steuerung hier eingeschaltet ist. Hauptschalter, Einspeisebegrenzung und Einstellungen bleiben OpenAmpere vorbehalten."}</p>
          <Button busy={busy} disabled={DEMO || !name.trim()} onClick={create}>Zugang erstellen</Button>
          {DEMO && <p className="hint">In der Demo nicht verfügbar.</p>}
        </div>
      ) : <Notice kind="info">Der HTTPS-Port ist ausgeschaltet (server.tls_port = 0). Ohne ihn können sich andere Apps nicht
        sicher verbinden.</Notice>)}

      {data && data.tokens.length > 0 && (
        <div className="card">
          <h2>Zugänge</h2>
          <ul className="plain-list">
            {data.tokens.map((t) => (
              <li key={t.id} className="token-row">
                <div>
                  <strong>{t.name}</strong> · {t.scope === "control" ? "Lesen + Steuern" : "Nur lesen"}
                  <div className="hint">Erstellt {when(t.created)} · {t.last_used ? `zuletzt benutzt ${when(t.last_used)}` : "noch nie benutzt"}</div>
                </div>
                <button className="link danger-link" onClick={() => setRevoke(t)}>Entfernen</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data?.tls.fingerprint && (
        <p className="hint">HTTPS auf Port {data.tls.port}. Fingerabdruck des Zertifikats (SHA-256): <code className="fingerprint">
          {data.tls.fingerprint.match(/.{1,2}/g)?.join(":")}</code></p>
      )}

      {revoke && (
        <Dialog title="Zugang entfernen?" confirm="Entfernen" danger onCancel={() => setRevoke(null)} onConfirm={() => void confirmRevoke()}>
          <p>„{revoke.name}“ kann danach nicht mehr auf OpenAmpere zugreifen. Für eine neue Verbindung erstellst du einen neuen
            Zugang.</p>
        </Dialog>
      )}
    </SubPage>
  );
}

/** A pairing request: the app shows the same six digits if nobody sits in between. */
function PairingCard({ request, onDone }: { request: PairingRequest; onDone: (data: Tokens) => void }) {
  const [scope, setScope] = useState<Scope>("read");
  const [busy, setBusy] = useState(false);
  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      onDone(await postJson<Tokens>(`/api/tokens/pairing/${request.id}`, { approve, scope }));
      toast(approve ? `„${request.name}“ ist verbunden` : "Anfrage abgelehnt");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card form pairing">
      <h2>„{request.name}“ möchte sich verbinden</h2>
      <p className="pairing-code" aria-label={`Code ${request.code.split("").join(" ")}`}>
        {request.code.slice(0, 3)} {request.code.slice(3)}</p>
      <p className="hint">Erlaube die Verbindung nur, wenn die App <strong>genau diesen Code</strong> anzeigt. Steht dort ein
        anderer, lehne ab: Dann hängt sich womöglich jemand dazwischen.</p>
      <Field label="Berechtigung"><Segmented value={scope} onChange={setScope} options={SCOPES} /></Field>
      <div className="button-row">
        <Button busy={busy} onClick={() => void decide(true)}>Code stimmt – erlauben</Button>
        <Button variant="secondary" disabled={busy} onClick={() => void decide(false)}>Ablehnen</Button>
      </div>
    </div>
  );
}

/** Is Home Assistant (or another app) connected right now? If not: what to check, or how to set it up (#79). */
function ConnectionStatus({ data }: { data: Tokens }) {
  const live = data.tokens.filter((t) => t.live_since);
  if (live.length) {
    return (
      <Notice kind="ok">
        {live.map((t) => <div key={t.id}><strong>{t.name}</strong> ist verbunden, Live-Werte seit {when(t.live_since!)}.</div>)}
      </Notice>
    );
  }
  const host = DEMO ? "192.168.178.20" : window.location.hostname; // the demo runs on the project website
  const port = data.tls.port;
  if (data.tokens.length) {
    const last = Math.max(...data.tokens.map((t) => t.last_used ?? 0));
    return (
      <div className="card form">
        <Notice kind="warn">Gerade ist keine App verbunden{last ? `, zuletzt ${when(last)}` : ""}.</Notice>
        <ul className="plain-list">
          <li>Läuft Home Assistant, und ist dort die Integration OpenAmpere eingerichtet?</li>
          <li>Erreicht Home Assistant diese Adresse? In der Integration muss <strong>{host}</strong> mit HTTPS-Port
            <strong> {port ?? "–"}</strong> eingetragen sein.</li>
          <li>Zeigt Home Assistant „Neu verbinden“? Dann wurde der Zugang hier entfernt oder das Zertifikat hat sich
            geändert: einfach neu koppeln.</li>
        </ul>
      </div>
    );
  }
  return (
    <div className="card form">
      <h2>Home Assistant verbinden</h2>
      <p className="hint">So siehst du die Werte von OpenAmpere in Home Assistant, auch im Energie-Dashboard, und kannst
        auf Wunsch den Speicher von dort steuern. OpenAmpere läuft schon, es fehlt nur noch die Integration.</p>
      <ol className="setup-steps">
        <li>In Home Assistant <a href={HACS_URL} target="_blank" rel="noopener">HACS</a> installieren, falls noch nicht
          geschehen.</li>
        <li>Die Integration OpenAmpere über HACS installieren und Home Assistant neu starten:{" "}
          <a href={MY_HA_REPOSITORY} target="_blank" rel="noopener">In Home Assistant öffnen</a></li>
        <li>Integration hinzufügen und „Mit OpenAmpere koppeln“ wählen:{" "}
          <a href={MY_HA_SETUP} target="_blank" rel="noopener">Integration einrichten</a>. Dort eintragen:
          <dl className="facts">
            <dt>Adresse</dt><dd><CopyValue value={host} /></dd>
            <dt>HTTPS-Port</dt><dd>{port ? <CopyValue value={String(port)} /> : "ausgeschaltet"}</dd>
          </dl>
        </li>
        <li>Die Anfrage erscheint dann hier oben mit einem Code. Stimmt er mit dem in Home Assistant überein, erlauben.</li>
      </ol>
      <p className="hint">Ohne HACS oder für andere Apps: unten einen Zugang erstellen und den Verbindungscode einfügen.
        Die Adresse muss die sein, unter der Home Assistant diesen Rechner erreicht, meist die IP-Adresse.</p>
    </div>
  );
}

export function CopyValue({ value }: { value: string }) {
  return (
    <span className="copy-value"><code>{value}</code>
      <button className="link" onClick={async () => { if (await copyText(value)) toast("Kopiert"); }}>Kopieren</button>
    </span>
  );
}
