import { useEffect, useRef, useState } from "react";
import type { AuthStatus } from "./api";
import { postJson, useResource } from "./api";
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
      <div className="page-head"><h1>Passwort festlegen</h1></div>
      <div className="card">
        <p>Das Passwort schützt alle Einstellungen deiner Anlage. Ansehen kann man die Werte im Heimnetz auch ohne
          Passwort – ändern aber nur, wer es kennt.</p>
        <p className="hint">Mindestens 6 Zeichen. Bewahre es gut auf – zurücksetzen kann es nur, wer Zugriff auf den Server hat.</p>
      </div>
      <form className="card form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <Field label="Passwort"><PasswordInput value={password} onChange={setPassword} autoComplete="new-password" /></Field>
        <Field label="Passwort wiederholen" hint={mismatch ? "Die Passwörter stimmen nicht überein." : undefined}>
          <PasswordInput value={repeat} onChange={setRepeat} autoComplete="new-password" />
        </Field>
        {error && <Notice kind="error">{error}</Notice>}
        <Button type="submit" busy={busy} disabled={password.length < 6 || password !== repeat}>Weiter</Button>
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
      toast("Angemeldet");
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
        <h2 id="login-title">Anmelden</h2>
        <p className="hint">Zum Ändern von Einstellungen brauchst du das Passwort von OpenAmpere.</p>
        <PasswordInput value={password} onChange={setPassword} autoComplete="current-password" placeholder="Passwort" />
        {error && <Notice kind="error">{error}</Notice>}
        <div className="dialog-actions">
          <Button type="submit" busy={busy} disabled={!password}>Anmelden</Button>
          <Button variant="secondary" onClick={onCancel}>Abbrechen</Button>
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
      toast("Passwort geändert – andere Geräte müssen sich neu anmelden");
      setCurrent(""); setNext("");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const logout = async (everywhere: boolean) => {
    await postJson(`/api/auth/logout${everywhere ? "?everywhere=true" : ""}`, {}).catch(() => undefined);
    toast(everywhere ? "Alle Geräte abgemeldet" : "Abgemeldet");
    reload();
  };

  return (
    <SubPage title="Zugriffsschutz" onBack={onBack}>
      <Notice kind={status?.authenticated ? "ok" : "info"}>
        {status?.authenticated ? "Dieses Gerät ist angemeldet und darf Einstellungen ändern." : "Dieses Gerät ist nicht angemeldet – nur Anzeige."}
      </Notice>
      <p className="hint">
        Ansehen ist im Heimnetz ohne Passwort möglich. Für Zugriff von unterwegs nutze ein VPN (z. B. das deiner FRITZ!Box,
        WireGuard oder Tailscale) – <strong>gib OpenAmpere niemals per Portfreigabe ins Internet frei.</strong>
      </p>
      {status && !status.authenticated && <Button onClick={() => setLoginOpen(true)}>Anmelden</Button>}
      {loginOpen && <LoginDialog onDone={() => { setLoginOpen(false); reload(); }} onCancel={() => setLoginOpen(false)} />}
      {status?.authenticated && (
        <>
          <form className="card form" onSubmit={(e) => { e.preventDefault(); void change(); }}>
            <h2>Passwort ändern</h2>
            <Field label="Bisheriges Passwort"><PasswordInput value={current} onChange={setCurrent} autoComplete="current-password" /></Field>
            <Field label="Neues Passwort" hint="Mindestens 6 Zeichen"><PasswordInput value={next} onChange={setNext} autoComplete="new-password" /></Field>
            <Button type="submit" busy={busy} disabled={!current || next.length < 6}>Passwort ändern</Button>
          </form>
          <div className="card form">
            <Button variant="secondary" onClick={() => void logout(false)}>Dieses Gerät abmelden</Button>
            <Button variant="secondary" onClick={() => setConfirmAll(true)}>Alle Geräte abmelden</Button>
          </div>
        </>
      )}
      <p className="hint">Passwort vergessen? Auf dem Server <code>openampere reset-password</code> ausführen
        (Docker: <code>docker compose exec openampere openampere reset-password</code>), dann ein neues festlegen.</p>
      {confirmAll && (
        <Dialog title="Alle Geräte abmelden?" confirm="Abmelden" onCancel={() => setConfirmAll(false)}
          onConfirm={() => { setConfirmAll(false); void logout(true); }}>
          <p>Alle Handys und Computer müssen sich danach neu anmelden, um Einstellungen zu ändern.</p>
        </Dialog>
      )}
    </SubPage>
  );
}
