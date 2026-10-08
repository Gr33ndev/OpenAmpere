import { useEffect, useRef, useState } from "react";
import type { AuthStatus } from "./api";
import { postJson, useResource } from "./api";
import { t, tx } from "./i18n";
import { Button, Dialog, Field, Notice, SubPage, toast, useModal } from "./ui";

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
        <Button type="submit" busy={busy} disabled={password.length < 6 || password !== repeat}>{t("shell.passwordSetup.continue")}</Button>
      </form>
    </div>
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
      toast(t("shell.loginDialog.loggedIn"));
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="overlay" onClick={onCancel}>
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
    reload();
  };

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
            <Button type="submit" busy={busy} disabled={!current || next.length < 6}>{t("shell.securityPage.changePassword")}</Button>
          </form>
          <div className="card form">
            <Button variant="secondary" onClick={() => void logout(false)}>{t("shell.securityPage.logOutDevice")}</Button>
            <Button variant="secondary" onClick={() => setConfirmAll(true)}>{t("shell.securityPage.logOutAllDevices")}</Button>
          </div>
        </>
      )}
      <p className="hint">{tx("shell.securityPage.forgotPassword", {
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
