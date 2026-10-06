import { useEffect, useRef, useState } from "react";
import type { Status } from "./api";
import { getJson, postJson, putJson, useResource } from "./api";
import { t } from "./i18n";
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
  const [message, setMessage] = useState(() => t("shell.installing.preparingUpdate"));
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
        setMessage(t("shell.installing.restarting"));
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [from]);
  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-label={t("shell.installing.title")}>
        <h2>{t("shell.installing.updateToVersion", { version })}</h2>
        {result === "failed" ? <Notice kind="error">{message}</Notice> : <p>{message}</p>}
        {!result && <p className="hint">{t("shell.installing.takesMinutes")}</p>}
        {result && <Button variant="secondary" onClick={onClose}>{t("shell.installing.close")}</Button>}
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
      <span>{t("shell.updateBanner.versionAvailable", { version: latest })}
        {data.latest?.url && <> · <a className="link" href={data.latest.url} target="_blank" rel="noopener noreferrer">{t("shell.updateBanner.whatsNew")}</a></>}</span>
      {data.updater
        ? <button className="banner-button" onClick={async () => setInstalling(await install())}>{t("shell.updateBanner.update")}</button>
        : <button className="banner-button" onClick={() => navigate("more/about")}>{t("common.howItWorks")}</button>}
      <button className="banner-close" aria-label={t("shell.updateBanner.remindMeLater")} onClick={() => { remember(latest); setHidden(latest); }}>×</button>
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
      <div className="section-title">{t("shell.updatesCard.title")}</div>
      <div className="card form">
        <dl className="facts">
          <dt>{t("shell.updatesCard.installed")}</dt><dd>{data.current}</dd>
          <dt>{t("shell.updatesCard.latestVersion")}</dt><dd>{latest ?? "–"}</dd>
        </dl>
        {data.error && <p className="hint">{data.error}</p>}
        {data.status?.state === "failed" && Date.now() / 1000 - data.status.ts < 86_400 && <Notice kind="error">{data.status.message}</Notice>}
        {data.available && latest && (data.updater ? (
          <Button busy={busy} onClick={async () => setInstalling(await install())}>{t("shell.updatesCard.updateTo", { version: latest })}</Button>
        ) : (
          <Notice kind="info">
            {t("shell.updatesCard.updateHelperHint")}
            <code className="code-inline">curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | bash</code>
            {t("shell.updatesCard.manualUpdateHint")} git pull &amp;&amp; docker compose up -d --build
          </Notice>
        ))}
        {!data.available && latest && <p className="hint">{t("shell.updatesCard.upToDate")}</p>}
        <SwitchRow label={t("shell.updatesCard.autoCheck")} hint={t("shell.updatesCard.autoCheckHint")}
          checked={data.check} onChange={(v) => void setting("updates.check", v)} />
        <SwitchRow label={t("shell.updatesCard.autoInstall")} hint={t("shell.updatesCard.autoInstallHint")}
          checked={data.auto} disabled={!data.check || !data.updater} onChange={(v) => void setting("updates.auto", v)} />
        {data.check && <button className="link" disabled={busy} onClick={() => void check()}>{t("shell.updatesCard.checkUpdatesNow")}</button>}
      </div>
    </>
  );
}
