import { useEffect, useRef, useState } from "react";
import type { Status } from "./api";
import { getJson, postJson, putJson, useResource } from "./api";
import { navigate } from "./route";
import { Button, Notice, SwitchRow, toast } from "./ui";

export type UpdateView = {
  current: string; available: boolean; updater: boolean; requested: boolean; check: boolean; auto: boolean;
  latest: { version: string; url: string | null; notes: string; published: string | null } | null;
  status: { ts: number; state: "pulling" | "restarting" | "done" | "failed"; message: string } | null;
  checked: number | null; error: string | null;
};

const DISMISSED = "openampere.update.dismissed";
const dismissed = () => { try { return localStorage.getItem(DISMISSED); } catch { return null; } };
const remember = (v: string) => { try { localStorage.setItem(DISMISSED, v); } catch { /* private mode */ } };

async function install(): Promise<boolean> {
  try {
    await postJson<UpdateView>("/api/update", { action: "install" });
    return true;
  } catch (e) {
    toast((e as Error).message, "error");
    return false;
  }
}

/** While the updater replaces the app: progress, then reload once the new version answers. */
function Installing({ version, from, onClose }: { version: string; from: string; onClose: () => void }) {
  const [message, setMessage] = useState("Das Update wird vorbereitet.");
  const [result, setResult] = useState<"done" | "failed" | null>(null);
  const started = useRef(Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(async () => {
      try {
        const status = await getJson<Status>("/api/status");
        if (status.version !== from) { window.location.reload(); return; }
        const view = await getJson<UpdateView>("/api/update");
        if (view.status && view.status.ts >= started.current - 5) {
          setMessage(view.status.message);
          if (view.status.state === "failed" || view.status.state === "done") setResult(view.status.state);
        }
      } catch {
        setMessage("OpenAmpere startet neu. Gleich ist die neue Version da.");
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [from]);
  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Update">
        <h2>Update auf Version {version}</h2>
        {result === "failed" ? <Notice kind="error">{message}</Notice> : <p>{message}</p>}
        {!result && <p className="hint">Das dauert meist ein bis zwei Minuten. Die Seite lädt danach von selbst neu.</p>}
        {result && <Button variant="secondary" onClick={onClose}>Schließen</Button>}
      </div>
    </div>
  );
}

/** Banner on top when a new version is out. */
export function UpdateBanner() {
  const { data } = useResource<UpdateView>("/api/update", 30 * 60_000);
  const [hidden, setHidden] = useState(dismissed());
  const [installing, setInstalling] = useState(false);
  const latest = data?.latest?.version;
  if (installing && data && latest) return <Installing version={latest} from={data.current} onClose={() => setInstalling(false)} />;
  if (!data?.available || !latest || hidden === latest) return null;
  return (
    <div className="update-banner update-available" role="status">
      <span>Version {latest} ist da
        {data.latest?.url && <> · <a className="link" href={data.latest.url} target="_blank" rel="noopener noreferrer">Was ist neu?</a></>}</span>
      {data.updater
        ? <button className="banner-button" onClick={async () => setInstalling(await install())}>Aktualisieren</button>
        : <button className="banner-button" onClick={() => navigate("more/about")}>So geht's</button>}
      <button className="banner-close" aria-label="Später erinnern" onClick={() => { remember(latest); setHidden(latest); }}>×</button>
    </div>
  );
}

/** Versions and update settings, on the "Über OpenAmpere" page. */
export function UpdatesCard() {
  const { data, reload, setData } = useResource<UpdateView>("/api/update");
  const [busy, setBusy] = useState(false);
  const [installing, setInstalling] = useState(false);
  if (!data) return null;
  const latest = data.latest?.version;
  const setting = async (key: "updates.check" | "updates.auto", value: boolean) => {
    try {
      await putJson("/api/settings", { [key]: value });
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const check = async () => {
    setBusy(true);
    try {
      setData(await postJson<UpdateView>("/api/update", { action: "check" }));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {installing && latest && <Installing version={latest} from={data.current} onClose={() => setInstalling(false)} />}
      <div className="section-title">Updates</div>
      <div className="card form">
        <dl className="facts">
          <dt>Installiert</dt><dd>{data.current}</dd>
          <dt>Neueste Version</dt><dd>{latest ?? "–"}</dd>
        </dl>
        {data.error && <p className="hint">{data.error}</p>}
        {data.status?.state === "failed" && Date.now() / 1000 - data.status.ts < 86_400 && <Notice kind="error">{data.status.message}</Notice>}
        {data.available && latest && (data.updater ? (
          <Button busy={busy} onClick={async () => setInstalling(await install())}>Auf {latest} aktualisieren</Button>
        ) : (
          <Notice kind="info">
            Für Updates per Knopfdruck einmal das Install-Script erneut ausführen, das richtet den Update-Helfer ein:
            <code className="code-inline">curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | bash</code>
            Installiert von Hand? Dann im OpenAmpere-Ordner: git pull &amp;&amp; docker compose up -d --build
          </Notice>
        ))}
        {!data.available && latest && <p className="hint">OpenAmpere ist auf dem neuesten Stand.</p>}
        <SwitchRow label="Nach Updates suchen" hint="Alle 6 Stunden bei GitHub. Dabei werden keine Daten deiner Anlage gesendet."
          checked={data.check} onChange={(v) => void setting("updates.check", v)} />
        <SwitchRow label="Updates nachts automatisch installieren" hint="Zwischen 2 und 5 Uhr. Startet eine neue Version nicht, kommt die bisherige zurück."
          checked={data.auto} disabled={!data.check || !data.updater} onChange={(v) => void setting("updates.auto", v)} />
        {data.check && <button className="link" disabled={busy} onClick={() => void check()}>Jetzt nach Updates suchen</button>}
      </div>
    </>
  );
}
