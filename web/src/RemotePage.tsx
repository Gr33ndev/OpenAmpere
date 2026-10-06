import { useState } from "react";
import { postJson, useResource } from "./api";
import { CopyValue } from "./AppsPage";
import { REPO_URL } from "./links";
import { Button, copyText, Dialog, LearnMore, LoadState, Notice, SubPage, toast } from "./ui";

type RemoteView = {
  available: boolean; port: number;
  state: "unavailable" | "off" | "starting" | "login" | "approval" | "connected" | "stopping" | "failed";
  login_url?: string | null; address?: string | null; name?: string | null; ip?: string | null; account?: string | null;
};

const INSTALL = "curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | OPENAMPERE_TAILSCALE=ja bash";
const DOWNLOAD_URL = "https://tailscale.com/download";
const ADMIN_URL = "https://login.tailscale.com/admin/machines";
const DOCS_URL = `${REPO_URL}/blob/main/README.de.md#zugriff-von-unterwegs`;

/** Access from anywhere with Tailscale (#83): set up with one tap, the tailscale container does the work. */
export function RemotePage({ onBack }: { onBack: () => void }) {
  // refreshed every few seconds: after logging in at Tailscale the page should move on by itself
  const { data, error, reload, setData } = useResource<RemoteView>("/api/remote", 3000);
  const [busy, setBusy] = useState(false);
  const [disconnect, setDisconnect] = useState(false);

  const act = async (action: "login" | "logout") => {
    setBusy(true);
    try {
      setData(await postJson<RemoteView>("/api/remote", { action }));
      if (action === "logout") toast("Zugriff von unterwegs getrennt");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Zugriff von unterwegs" onBack={onBack}>
      <p className="hint">Mit Tailscale erreichst du OpenAmpere sicher von überall, ganz ohne Portfreigabe im Router. Tailscale
        verbindet deine Geräte verschlüsselt direkt miteinander. Dafür brauchst du ein kostenloses Konto bei Tailscale, die
        Anmeldung geht zum Beispiel mit Google, Apple oder Microsoft.</p>
      {!data && <LoadState error={error} onRetry={reload} />}

      {data?.state === "unavailable" && (
        <div className="card">
          <Notice kind="info">
            Tailscale ist auf diesem Rechner noch nicht eingerichtet. Führe dafür einmal das Install-Script erneut aus:
            <code className="code-inline">{INSTALL}</code>
            <button className="link" onClick={async () => { if (await copyText(INSTALL)) toast("Befehl kopiert"); }}>Befehl kopieren</button>{" "}
            Danach geht es hier mit einem Tipp weiter. Installiert von Hand? Siehe <a href={DOCS_URL} target="_blank" rel="noopener">Anleitung</a>.
          </Notice>
        </div>
      )}

      {(data?.state === "off" || data?.state === "failed") && (
        <div className="card form">
          {data.state === "failed" && (
            <Notice kind="error">Tailscale hat keinen Anmelde-Link geliefert. Prüfe die Internetverbindung des Rechners und
              versuche es noch einmal.</Notice>
          )}
          <ol className="steps-list">
            <li><span>Auf <strong>Einrichten</strong> tippen.</span></li>
            <li><span>Bei Tailscale anmelden oder ein Konto anlegen.</span></li>
            <li><span>Die Tailscale-App aufs Handy laden und mit demselben Konto anmelden.</span></li>
          </ol>
          <Button busy={busy} onClick={() => void act("login")}>Einrichten</Button>
        </div>
      )}

      {(data?.state === "starting" || data?.state === "stopping") && (
        <div className="card"><p>{data.state === "starting" ? "Verbindung zu Tailscale wird vorbereitet …" : "Verbindung wird getrennt …"}</p></div>
      )}

      {data?.state === "login" && data.login_url && (
        <div className="card form">
          <p>Melde dich jetzt bei Tailscale an. Hast du noch kein Konto, legst du dort eins an.</p>
          <a className="btn primary" href={data.login_url} target="_blank" rel="noopener noreferrer">Bei Tailscale anmelden</a>
          <p className="hint">Danach geht es hier von selbst weiter.</p>
          <button className="link" disabled={busy} onClick={() => void act("logout")}>Abbrechen</button>
        </div>
      )}

      {data?.state === "approval" && (
        <div className="card form">
          <Notice kind="warn">Dein Tailscale-Konto verlangt, dass neue Geräte freigegeben werden. Gib
            „{data.name?.split(".")[0] || "openampere"}“ in der <a href={ADMIN_URL} target="_blank" rel="noopener noreferrer">Tailscale-Verwaltung</a> frei.</Notice>
        </div>
      )}

      {data?.state === "connected" && (
        <>
          <div className="card form">
            <Notice kind="ok">OpenAmpere ist von unterwegs erreichbar.</Notice>
            <dl className="facts">
              <dt>Adresse</dt><dd>{data.address ? <CopyValue value={data.address} /> : "–"}</dd>
              {data.account && <><dt>Konto</dt><dd>{data.account}</dd></>}
            </dl>
          </div>
          <div className="section-title">So geht es auf dem Handy</div>
          <div className="card">
            <ol className="steps-list">
              <li><span>Die <a href={DOWNLOAD_URL} target="_blank" rel="noopener noreferrer">Tailscale-App</a> installieren.</span></li>
              <li><span>Mit demselben Konto anmelden{data.account ? <> ({data.account})</> : null} und Tailscale einschalten.</span></li>
              <li><span>Die Adresse oben im Browser öffnen und zum Home-Bildschirm hinzufügen.</span></li>
            </ol>
            <p className="hint">Zu Hause funktioniert weiterhin die gewohnte Adresse. Auf dem iPhone kann immer nur ein VPN
              gleichzeitig an sein.</p>
          </div>
          <div className="card form">
            <Button variant="secondary" disabled={busy} onClick={() => setDisconnect(true)}>Trennen</Button>
          </div>
        </>
      )}

      <LearnMore summary="Was ist Tailscale?">
        <p>Tailscale (Tailscale Inc., USA) vermittelt die Verbindung zwischen deinen Geräten. Die Daten selbst laufen
          verschlüsselt direkt zwischen Handy und diesem Rechner. Erreichbar ist OpenAmpere so nur für Geräte, die in deinem
          Tailscale-Konto angemeldet sind. Für private Nutzung ist Tailscale kostenlos.</p>
        <p>OpenAmpere schaltet das Senden von Protokolldaten an Tailscale ab und stellt nichts öffentlich ins Internet.
          Einstellungen ändern geht auch von unterwegs nur mit deinem Passwort.</p>
      </LearnMore>

      {disconnect && (
        <Dialog title="Zugriff von unterwegs trennen?" confirm="Trennen" danger
          onConfirm={() => { setDisconnect(false); void act("logout"); }} onCancel={() => setDisconnect(false)}>
          <p>Danach ist OpenAmpere nur noch zu Hause erreichbar. Du kannst es jederzeit wieder einrichten.</p>
          <p className="hint">In deiner <a href={ADMIN_URL} target="_blank" rel="noopener noreferrer">Tailscale-Übersicht</a> bleibt
            das Gerät als „offline“ stehen. Dort kannst du es löschen, wenn du Tailscale nicht mehr nutzen willst.</p>
        </Dialog>
      )}
    </SubPage>
  );
}
