import { useState } from "react";
import { postJson, useResource } from "./api";
import { CopyValue } from "./AppsPage";
import { t, tx } from "./i18n";
import { REPO_URL } from "./links";
import { Button, copyText, Dialog, LearnMore, LoadState, Notice, SubPage, toast } from "./ui";

type RemoteView = {
  available: boolean; port: number;
  state: "unavailable" | "off" | "starting" | "login" | "approval" | "connected" | "stopping" | "failed";
  login_url?: string | null; address?: string | null; name?: string | null; ip?: string | null; account?: string | null;
  https?: { state: "off" | "starting" | "enable" | "on" | "failed"; url?: string | null; enable_url?: string; error?: string };
};

const INSTALL = "curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | OPENAMPERE_TAILSCALE=ja bash";
const DOWNLOAD_URL = "https://tailscale.com/download";
const ADMIN_URL = "https://login.tailscale.com/admin/machines";
const DNS_URL = "https://login.tailscale.com/admin/dns";
const DOCS_URL = `${REPO_URL}/blob/main/README.de.md#zugriff-von-unterwegs`;

/** Access from anywhere with Tailscale (#83): set up with one tap, the tailscale container does the work. */
export function RemotePage({ onBack }: { onBack: () => void }) {
  // refreshed every few seconds: after logging in at Tailscale the page should move on by itself
  const { data, error, reload, setData } = useResource<RemoteView>("/api/remote", 3000);
  const [busy, setBusy] = useState(false);
  const [disconnect, setDisconnect] = useState(false);

  const act = async (action: "login" | "logout" | "https_on" | "https_off") => {
    setBusy(true);
    try {
      setData(await postJson<RemoteView>("/api/remote", { action }));
      if (action === "logout") toast(t("settings.remotePage.disconnected"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title={t("common.remoteAccess")} onBack={onBack}>
      <p className="hint">{t("settings.remotePage.intro")}</p>
      {!data && <LoadState error={error} onRetry={reload} />}

      {data?.state === "unavailable" && (
        <div className="card">
          <Notice kind="info">
            {t("settings.remotePage.notInstalled")}
            <code className="code-inline">{INSTALL}</code>
            <button type="button" className="link" onClick={async () => { if (await copyText(INSTALL)) toast(t("settings.remotePage.commandCopied")); }}>{t("settings.remotePage.copyCommand")}</button>{" "}
            {tx("settings.remotePage.installScriptHint", { link: <a href={DOCS_URL} target="_blank" rel="noopener">{t("settings.remotePage.guide")}</a> })}
          </Notice>
        </div>
      )}

      {(data?.state === "off" || data?.state === "failed") && (
        <div className="card form">
          {data.state === "failed" && (
            <Notice kind="error">{t("settings.remotePage.noLoginLink")}</Notice>
          )}
          <ol className="steps-list">
            <li><span>{tx("settings.remotePage.setUpStep", { button: <strong>{t("common.setUp")}</strong> })}</span></li>
            <li><span>{t("settings.remotePage.accountStep")}</span></li>
            <li><span>{t("settings.remotePage.appStep")}</span></li>
          </ol>
          <Button busy={busy} onClick={() => void act("login")}>{t("common.setUp")}</Button>
        </div>
      )}

      {(data?.state === "starting" || data?.state === "stopping") && (
        <div className="card"><p>{data.state === "starting" ? t("settings.remotePage.preparing") : t("settings.remotePage.disconnecting")}</p></div>
      )}

      {data?.state === "login" && data.login_url && (
        <div className="card form">
          <p>{t("settings.remotePage.logInHint")}</p>
          <a className="btn primary" href={data.login_url} target="_blank" rel="noopener noreferrer">{t("settings.remotePage.logIn")}</a>
          <p className="hint">{t("settings.remotePage.continuesAutomatically")}</p>
          <button type="button" className="link" disabled={busy} onClick={() => void act("logout")}>{t("common.cancel")}</button>
        </div>
      )}

      {data?.state === "approval" && (
        <div className="card form">
          <Notice kind="warn">{tx("settings.remotePage.approvalNeeded", { name: data.name?.split(".")[0] || "openampere",
            link: <a href={ADMIN_URL} target="_blank" rel="noopener noreferrer">{t("settings.remotePage.adminConsole")}</a> })}</Notice>
        </div>
      )}

      {data?.state === "connected" && (
        <>
          <div className="card form">
            <Notice kind="ok">{t("settings.remotePage.connected")}</Notice>
            <dl className="facts">
              <dt>{t("common.address")}</dt><dd>{data.address ? <CopyValue value={data.address} /> : "–"}</dd>
              {data.account && <><dt>{t("settings.remotePage.account")}</dt><dd>{data.account}</dd></>}
            </dl>
          </div>
          <HttpsCard https={data.https} busy={busy} onSwitch={(on) => void act(on ? "https_on" : "https_off")} />
          <div className="section-title">{t("settings.remotePage.phoneTitle")}</div>
          <div className="card">
            <ol className="steps-list">
              <li><span>{tx("settings.remotePage.installAppStep",
                { link: <a href={DOWNLOAD_URL} target="_blank" rel="noopener noreferrer">{t("settings.remotePage.tailscaleApp")}</a> })}</span></li>
              <li><span>{data.account ? t("settings.remotePage.logInStepAccount", { account: data.account }) : t("settings.remotePage.logInStep")}</span></li>
              <li><span>{t("settings.remotePage.homeScreenStep")}</span></li>
            </ol>
            <p className="hint">{t("settings.remotePage.homeHint")}</p>
          </div>
          <div className="card form">
            <Button variant="secondary" disabled={busy} onClick={() => setDisconnect(true)}>{t("settings.remotePage.disconnect")}</Button>
          </div>
        </>
      )}

      <LearnMore summary={t("settings.remotePage.whatIsTailscale")}>
        <p>{t("settings.remotePage.about")}</p>
        <p>{t("settings.remotePage.privacy")}</p>
      </LearnMore>

      {disconnect && (
        <Dialog title={t("settings.remotePage.disconnectTitle")} confirm={t("settings.remotePage.disconnect")} danger
          onConfirm={() => { setDisconnect(false); void act("logout"); }} onCancel={() => setDisconnect(false)}>
          <p>{t("settings.remotePage.disconnectText")}</p>
          <p className="hint">{tx("settings.remotePage.offlineHint",
            { link: <a href={ADMIN_URL} target="_blank" rel="noopener noreferrer">{t("settings.remotePage.tailscaleOverview")}</a> })}</p>
        </Dialog>
      )}
    </SubPage>
  );
}

/** Optional HTTPS with a certificate for the tailnet name, through `tailscale serve` (#116). */
function HttpsCard({ https, busy, onSwitch }: {
  https: RemoteView["https"]; busy: boolean; onSwitch: (on: boolean) => void;
}) {
  const state = https?.state ?? "off";
  return (
    <>
      <div className="section-title">{t("settings.remotePage.httpsTitle")}</div>
      <div className="card form">
        <p className="hint">{tx("settings.remotePage.httpsHint",
          { link: <a href={DNS_URL} target="_blank" rel="noopener noreferrer">{t("settings.remotePage.httpsDnsLink")}</a> })}</p>
        {state === "on" && <Notice kind="ok">{t("settings.remotePage.httpsOn")}</Notice>}
        {state === "starting" && <p>{t("settings.remotePage.httpsStarting")}</p>}
        {state === "enable" && https?.enable_url && (
          <>
            <Notice kind="info">{t("settings.remotePage.httpsEnable")}</Notice>
            <a className="btn primary" href={https.enable_url} target="_blank" rel="noopener noreferrer">
              {t("settings.remotePage.httpsEnableButton")}</a>
          </>
        )}
        {state === "failed" && <Notice kind="error">{t("settings.remotePage.httpsFailed", { error: https?.error ?? "" })}</Notice>}
        {state === "on" || state === "starting" || state === "enable"
          ? <Button variant="secondary" disabled={busy} onClick={() => onSwitch(false)}>{t("settings.remotePage.httpsSwitchOff")}</Button>
          : <Button disabled={busy} onClick={() => onSwitch(true)}>{t("settings.remotePage.httpsSwitchOn")}</Button>}
      </div>
    </>
  );
}
