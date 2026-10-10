import { useState } from "react";
import { deleteJson, postJson, useResource } from "./api";
import { DEMO } from "./demo/flag";
import { timeZone } from "./format";
import { LOCALE, t, tx } from "./i18n";
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
const SCOPES: [Scope, string][] = [["read", t("common.readOnly")], ["control", t("common.readAndControl")]];
const when = (ts: number) => new Date(ts * 1000).toLocaleString(LOCALE, { dateStyle: "short", timeStyle: "short", timeZone: timeZone() });

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
      toast(t("settings.appsPage.accessRemoved", { name: revoke.name }));
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setRevoke(null);
  };

  return (
    <SubPage title={t("common.connectedApps")} onBack={onBack}>
      <p className="hint">{t("settings.appsPage.intro")}{" "}
        <a href={DOCS_URL} target="_blank" rel="noopener">{t("settings.appsPage.haGuide")}</a></p>
      {!data && <LoadState error={error} onRetry={reload} />}
      {data && !code && <ConnectionStatus data={data} />}

      {data?.tls.error && <Notice kind="warn">{t("settings.appsPage.httpsError", { error: data.tls.error })}</Notice>}
      {data?.pairing.map((p) => <PairingCard key={p.id} request={p} onDone={setData} />)}

      {code && (
        <div className="card form">
          <h2>{t("settings.appsPage.connectionCode")}</h2>
          <Notice kind="warn">{t("settings.appsPage.codeShownOnce")}</Notice>
          <textarea className="input code-box" readOnly rows={4} value={code} onFocus={(e) => e.target.select()} />
          <Button onClick={async () => { if (await copyText(code)) toast(t("common.copied")); }}>{t("common.copy")}</Button>
          <p className="hint">{t("settings.appsPage.haCodeHint")}</p>
          <Button variant="secondary" onClick={() => setCode(null)}>{t("settings.appsPage.done")}</Button>
        </div>
      )}

      {data && !code && (data.tls.port ? (
        <div className="card form">
          <h2>{t("settings.appsPage.connectNewApp")}</h2>
          <p className="hint">{t("settings.appsPage.pairingHint")}</p>
          <Field label={t("common.name")}><input className="input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label={t("common.permission")}>
            <Segmented value={scope} onChange={setScope} options={SCOPES} />
          </Field>
          <p className="hint">{scope === "read"
            ? t("settings.appsPage.readScopeHint")
            : t("settings.appsPage.controlScopeHint")}</p>
          <Button busy={busy} disabled={DEMO || !name.trim()} onClick={create}>{t("settings.appsPage.createAccess")}</Button>
          {DEMO && <p className="hint">{t("settings.appsPage.notInDemo")}</p>}
        </div>
      ) : <Notice kind="info">{t("settings.appsPage.httpsOff")}</Notice>)}

      {data && data.tokens.length > 0 && (
        <div className="card">
          <h2>{t("settings.appsPage.accessList")}</h2>
          <ul className="plain-list">
            {data.tokens.map((token) => (
              <li key={token.id} className="token-row">
                <div>
                  <strong>{token.name}</strong> · {token.scope === "control" ? t("common.readAndControl") : t("common.readOnly")}
                  <div className="hint">{t("settings.appsPage.created", { date: when(token.created) })} · {token.last_used
                    ? t("settings.appsPage.lastUsed", { date: when(token.last_used) }) : t("settings.appsPage.neverUsed")}</div>
                </div>
                <button type="button" className="link danger-link" onClick={() => setRevoke(token)}>{t("common.remove")}</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data?.tls.fingerprint && (
        <p className="hint">{t("settings.appsPage.httpsFingerprint", { port: data.tls.port ?? "–" })} <code className="fingerprint">
          {data.tls.fingerprint.match(/.{1,2}/g)?.join(":")}</code></p>
      )}

      {revoke && (
        <Dialog title={t("settings.appsPage.removeTitle")} confirm={t("common.remove")} danger onCancel={() => setRevoke(null)} onConfirm={() => void confirmRevoke()}>
          <p>{t("settings.appsPage.removeText", { name: revoke.name })}</p>
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
      toast(approve ? t("settings.pairingCard.connected", { name: request.name }) : t("settings.pairingCard.declined"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card form pairing">
      <h2>{t("settings.pairingCard.title", { name: request.name })}</h2>
      <p className="pairing-code">
        <span aria-hidden="true">{request.code.slice(0, 3)} {request.code.slice(3)}</span>
        <span className="sr-only">{t("settings.pairingCard.codeLabel", { code: request.code.split("").join(" ") })}</span></p>
      <p className="hint">{tx("settings.pairingCard.checkCode", { code: <strong>{t("settings.pairingCard.exactCode")}</strong> })}</p>
      <Field label={t("common.permission")}><Segmented value={scope} onChange={setScope} options={SCOPES} /></Field>
      <div className="button-row">
        <Button busy={busy} onClick={() => void decide(true)}>{t("settings.pairingCard.allow")}</Button>
        <Button variant="secondary" disabled={busy} onClick={() => void decide(false)}>{t("settings.pairingCard.decline")}</Button>
      </div>
    </div>
  );
}

/** Is Home Assistant (or another app) connected right now? If not: what to check, or how to set it up (#79). */
function ConnectionStatus({ data }: { data: Tokens }) {
  const live = data.tokens.filter((token) => token.live_since);
  if (live.length) {
    return (
      <Notice kind="ok">
        {live.map((token) => <div key={token.id}><strong>{token.name}</strong>{" "}
          {t("settings.connectionStatus.connectedSince", { date: when(token.live_since!) })}</div>)}
      </Notice>
    );
  }
  const host = DEMO ? "192.168.178.20" : window.location.hostname; // the demo runs on the project website
  const port = data.tls.port;
  if (data.tokens.length) {
    const last = Math.max(...data.tokens.map((token) => token.last_used ?? 0));
    return (
      <div className="card form">
        <Notice kind="warn">{last ? t("settings.connectionStatus.notConnectedSince", { date: when(last) }) : t("settings.connectionStatus.notConnected")}</Notice>
        <ul className="plain-list">
          <li>{t("settings.connectionStatus.checkRunning")}</li>
          <li>{tx("settings.connectionStatus.checkAddress", { host: <strong>{host}</strong>, port: <strong>{port ?? "–"}</strong> })}</li>
          <li>{t("settings.connectionStatus.reconnectHint")}</li>
        </ul>
      </div>
    );
  }
  return (
    <div className="card form">
      <h2>{t("settings.connectionStatus.title")}</h2>
      <p className="hint">{t("settings.connectionStatus.intro")}</p>
      <ol className="setup-steps">
        <li>{tx("settings.connectionStatus.installHacsStep", { link: <a href={HACS_URL} target="_blank" rel="noopener">HACS</a> })}</li>
        <li>{t("settings.connectionStatus.installIntegrationStep")}{" "}
          <a href={MY_HA_REPOSITORY} target="_blank" rel="noopener">{t("settings.connectionStatus.openHomeAssistant")}</a></li>
        <li>{tx("settings.connectionStatus.addIntegrationStep",
          { link: <a href={MY_HA_SETUP} target="_blank" rel="noopener">{t("settings.connectionStatus.setUpIntegration")}</a> })}
          <dl className="facts">
            <dt>{t("common.address")}</dt><dd><CopyValue value={host} /></dd>
            <dt>{t("settings.connectionStatus.httpsPort")}</dt><dd>{port ? <CopyValue value={String(port)} /> : t("settings.connectionStatus.turnedOff")}</dd>
          </dl>
        </li>
        <li>{t("settings.connectionStatus.allowStep")}</li>
      </ol>
      <p className="hint">{t("settings.connectionStatus.manualHint")}</p>
    </div>
  );
}

export function CopyValue({ value }: { value: string }) {
  return (
    <span className="copy-value"><code>{value}</code>
      <button type="button" className="link" onClick={async () => { if (await copyText(value)) toast(t("common.copied")); }}>{t("common.copy")}</button>
    </span>
  );
}
