// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import type { AuthStatus, Status } from "./api";
import { directAddress, postJson, refreshAll, useResource } from "./api";
import { t, tx } from "./i18n";
import { Button, Dialog, Field, Notice, SubPage, toast, useModal } from "./ui";

const MIN_PASSWORD_LENGTH = 8; // as the server checks it (auth.py)

function PasswordInput({ value, onChange, placeholder, autoComplete }: {
  value: string; onChange: (v: string) => void; placeholder?: string; autoComplete: string;
}) {
  return <input className="input" type="password" value={value} autoComplete={autoComplete} placeholder={placeholder}
    onChange={(e) => onChange(e.target.value)} />;
}

/** First start: choose the password that protects all settings. */
export function PasswordSetup({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatch = repeat.length > 0 && password !== repeat;

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await postJson("/api/auth/setup", { password });
      refreshAll(); // logged in now: load the values that are hidden without login (#164)
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page setup">
      <div className="page-head"><h1>{t("shell.passwordSetup.setPassword")}</h1></div>
      <div className="card">
        <p>{t("shell.passwordSetup.intro")}</p>
        <p className="hint">{t("shell.passwordSetup.passwordHint")}</p>
      </div>
      <form className="card form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <Field label={t("common.password")}><PasswordInput value={password} onChange={setPassword} autoComplete="new-password" /></Field>
        <Field label={t("shell.passwordSetup.repeatPassword")} hint={mismatch ? t("shell.passwordSetup.passwordsDoNotMatch") : undefined}>
          <PasswordInput value={repeat} onChange={setRepeat} autoComplete="new-password" />
        </Field>
        {error && <Notice kind="error">{error}</Notice>}
        <Button type="submit" busy={busy} disabled={password.length < MIN_PASSWORD_LENGTH || password !== repeat}>{t("shell.passwordSetup.continue")}</Button>
      </form>
    </div>
  );
}

/** Home Assistant app, opened by its IP address before a password exists (#300): the password is set in the
 *  sidebar of Home Assistant, so that nobody else in the home network can choose it first. */
export function DirectAccessLocked({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="page setup">
      <div className="page-head"><h1>{t("shell.passwordSetup.setPassword")}</h1></div>
      <div className="card form">
        <p className="flush">{t("shell.homeAssistant.setPasswordInSidebar")}</p>
        <Button variant="secondary" onClick={onRetry}>{t("common.tryAgainNow")}</Button>
      </div>
    </div>
  );
}

/** The database was damaged and its password could not be taken over: the first-password setup stays closed until the
 *  owner runs reset-password on the server, so that nobody else in the home network can choose a password first. */
export function SetupLocked({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="page setup">
      <div className="page-head"><h1>{t("shell.passwordSetup.setPassword")}</h1></div>
      <div className="card form">
        <p className="flush">{t("shell.setupLocked.text")}</p>
        <p className="flush">{tx("shell.setupLocked.nextStep", {
          command: <code>cd /opt/openampere && sudo docker compose exec openampere openampere reset-password</code> })}</p>
        <Button variant="secondary" onClick={onRetry}>{t("common.tryAgainNow")}</Button>
      </div>
    </div>
  );
}

/** Access protection in the Home Assistant sidebar: Home Assistant already checked the login, the password is only
 *  for direct access by IP address (evcc, phones). It can be set again without the old one (#300). */
function SidebarSecurity({ auth, onChange }: { auth: AuthStatus; onChange: () => void }) {
  const { data: status } = useResource<Status>("/api/status");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [confirmAll, setConfirmAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const mismatch = repeat.length > 0 && password !== repeat;
  const address = status?.home_assistant ? directAddress(status.home_assistant) : null;

  const save = async () => {
    setBusy(true);
    try {
      if (auth.configured) await postJson("/api/auth/password", { new: password });
      else await postJson("/api/auth/setup", { password });
      toast(auth.configured ? t("shell.securityPage.passwordChanged") : t("shell.homeAssistant.passwordSet"));
      setPassword(""); setRepeat("");
      onChange();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const logoutAll = async () => {
    await postJson("/api/auth/logout?everywhere=true", {}).catch(() => undefined);
    toast(t("shell.securityPage.allDevicesLoggedOut"));
  };

  // only admins of Home Assistant may change anything through the sidebar (#300)
  if (!auth.authenticated) return <Notice kind="info">{t("shell.homeAssistant.adminsOnly")}</Notice>;
  return (
    <>
      <Notice kind="ok">{t("shell.homeAssistant.signedIn")}</Notice>
      <p className="hint">
        {address ? tx("shell.homeAssistant.passwordForDirectAccess", { address: <code>{address}</code> })
          : t("shell.homeAssistant.passwordForDirectAccessOff")}
      </p>
      <form className="card form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <h2>{auth.configured ? t("shell.securityPage.changePassword") : t("shell.passwordSetup.setPassword")}</h2>
        <Field label={t("shell.securityPage.newPassword")} hint={t("shell.securityPage.passwordHint")}>
          <PasswordInput value={password} onChange={setPassword} autoComplete="new-password" />
        </Field>
        <Field label={t("shell.passwordSetup.repeatPassword")} hint={mismatch ? t("shell.passwordSetup.passwordsDoNotMatch") : undefined}>
          <PasswordInput value={repeat} onChange={setRepeat} autoComplete="new-password" />
        </Field>
        <Button type="submit" busy={busy} disabled={password.length < MIN_PASSWORD_LENGTH || password !== repeat}>
          {auth.configured ? t("shell.securityPage.changePassword") : t("shell.passwordSetup.setPassword")}
        </Button>
      </form>
      {auth.configured && (
        <div className="card form">
          <Button variant="secondary" onClick={() => setConfirmAll(true)}>{t("shell.securityPage.logOutAllDevices")}</Button>
        </div>
      )}
      {confirmAll && (
        <Dialog title={t("shell.securityPage.logOutAllQuestion")} confirm={t("shell.securityPage.logOut")} onCancel={() => setConfirmAll(false)}
          onConfirm={() => { setConfirmAll(false); void logoutAll(); }}>
          <p>{t("shell.securityPage.logOutAllHint")}</p>
        </Dialog>
      )}
    </>
  );
}

/** Login form, used as a dialog whenever a change needs authentication. */
export function LoginDialog({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const ref = useRef<HTMLFormElement>(null);
  useModal(ref, onCancel);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const login = async () => {
    setBusy(true); setError(null);
    try {
      await postJson("/api/auth/login", { password });
      refreshAll(); // pages loaded before show the values that are hidden without login (#164)
      toast(t("shell.loginDialog.loggedIn"));
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions lint/a11y/useKeyWithClickEvents: Escape closes the dialog (useModal), the backdrop click is an extra for pointer users
    <div className="overlay" onClick={onCancel}>
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: only keeps a click inside the dialog from closing it */}
      <form className="dialog" ref={ref} role="dialog" aria-modal="true" aria-labelledby="login-title" onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => { e.preventDefault(); void login(); }}>
        <h2 id="login-title">{t("common.logIn")}</h2>
        <p className="hint">{t("shell.loginDialog.passwordNeeded")}</p>
        <PasswordInput value={password} onChange={setPassword} autoComplete="current-password" placeholder={t("common.password")} />
        {error && <Notice kind="error">{error}</Notice>}
        <div className="dialog-actions">
          <Button type="submit" busy={busy} disabled={!password}>{t("common.logIn")}</Button>
          <Button variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
        </div>
      </form>
    </div>
  );
}

/** In place of values that are only shown after login (#164): says why and offers the login. */
export function LoginToSee() {
  return (
    <div className="card form">
      <p className="flush login-to-see">{t("shell.loginToSee.text")}</p>
      <Button variant="secondary" onClick={() => window.dispatchEvent(new CustomEvent("openampere:auth", { detail: "login_required" }))}>
        {t("common.logIn")}
      </Button>
    </div>
  );
}

/** Listens for 401 answers anywhere in the app and asks for the password. */
export function useLoginPrompt(onLoggedIn: () => void) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const handler = (e: Event) => {
      if ((e as CustomEvent).detail === "login_required") setOpen(true);
    };
    window.addEventListener("openampere:auth", handler);
    return () => window.removeEventListener("openampere:auth", handler);
  }, []);
  const dialog = open ? <LoginDialog onDone={() => { setOpen(false); onLoggedIn(); }} onCancel={() => setOpen(false)} /> : null;
  return { dialog, open: () => setOpen(true) };
}

export function SecurityPage({ onBack }: { onBack: () => void }) {
  const { data: status, reload } = useResource<AuthStatus>("/api/auth/status");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmAll, setConfirmAll] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const change = async () => {
    setBusy(true);
    try {
      await postJson("/api/auth/password", { current, new: next });
      toast(t("shell.securityPage.passwordChanged"));
      setCurrent(""); setNext("");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const logout = async (everywhere: boolean) => {
    await postJson(`/api/auth/logout${everywhere ? "?everywhere=true" : ""}`, {}).catch(() => undefined);
    toast(everywhere ? t("shell.securityPage.allDevicesLoggedOut") : t("shell.securityPage.loggedOut"));
    refreshAll(); // hides the private values again (#164)
  };

  if (status?.home_assistant === "ingress") {
    return <SubPage title={t("common.accessProtection")} onBack={onBack}><SidebarSecurity auth={status} onChange={reload} /></SubPage>;
  }
  return (
    <SubPage title={t("common.accessProtection")} onBack={onBack}>
      <Notice kind={status?.authenticated ? "ok" : "info"}>
        {status?.authenticated ? t("shell.securityPage.loggedIn") : t("shell.securityPage.notLoggedIn")}
      </Notice>
      <p className="hint">
        {tx("shell.securityPage.remoteAccessHint", { warning: <strong>{t("shell.securityPage.neverExpose")}</strong> })}
      </p>
      {status && !status.authenticated && <Button onClick={() => setLoginOpen(true)}>{t("common.logIn")}</Button>}
      {loginOpen && <LoginDialog onDone={() => { setLoginOpen(false); reload(); }} onCancel={() => setLoginOpen(false)} />}
      {status?.authenticated && (
        <>
          <form className="card form" onSubmit={(e) => { e.preventDefault(); void change(); }}>
            <h2>{t("shell.securityPage.changePassword")}</h2>
            <Field label={t("shell.securityPage.currentPassword")}><PasswordInput value={current} onChange={setCurrent} autoComplete="current-password" /></Field>
            <Field label={t("shell.securityPage.newPassword")} hint={t("shell.securityPage.passwordHint")}><PasswordInput value={next} onChange={setNext} autoComplete="new-password" /></Field>
            <Button type="submit" busy={busy} disabled={!current || next.length < MIN_PASSWORD_LENGTH}>{t("shell.securityPage.changePassword")}</Button>
          </form>
          <div className="card form">
            <Button variant="secondary" onClick={() => void logout(false)}>{t("shell.securityPage.logOutDevice")}</Button>
            <Button variant="secondary" onClick={() => setConfirmAll(true)}>{t("shell.securityPage.logOutAllDevices")}</Button>
          </div>
        </>
      )}
      <p className="hint">{status?.home_assistant ? t("shell.homeAssistant.forgotPassword") : tx("shell.securityPage.forgotPassword", {
        command: <code>cd /opt/openampere && sudo docker compose exec openampere openampere reset-password</code> })}</p>
      {confirmAll && (
        <Dialog title={t("shell.securityPage.logOutAllQuestion")} confirm={t("shell.securityPage.logOut")} onCancel={() => setConfirmAll(false)}
          onConfirm={() => { setConfirmAll(false); void logout(true); }}>
          <p>{t("shell.securityPage.logOutAllHint")}</p>
        </Dialog>
      )}
    </SubPage>
  );
}
