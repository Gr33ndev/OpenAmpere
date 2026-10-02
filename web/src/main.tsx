import { StrictMode, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import type { AuthStatus, Status } from "./api";
import { useLive, useResource } from "./api";
import { PasswordSetup, useLoginPrompt } from "./AuthScreens";
import { Dashboard } from "./Dashboard";
import { NavHome, NavMore, NavReport } from "./icons";
import { More } from "./More";
import { Report } from "./Report";
import { applyTheme, storedTheme } from "./SettingsPages";
import { Setup } from "./Setup";
import { Button, Notice, ToastHost } from "./ui";
import "./styles.css";

type Tab = "dashboard" | "report" | "more";

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: "dashboard", label: "Dashboard", icon: <NavHome /> },
  { id: "report", label: "Report", icon: <NavReport /> },
  { id: "more", label: "Mehr", icon: <NavMore /> },
];

applyTheme(storedTheme());

function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const { data: status, error, reload } = useResource<Status>("/api/status", 15_000);
  const { data: auth, reload: reloadAuth } = useResource<AuthStatus>("/api/auth/status");
  const { snap, online } = useLive();
  const login = useLoginPrompt(reloadAuth);

  if (!status) {
    return (
      <div className="app">
        <main>
          <div className="page setup">
            <div className="page-head"><h1>OpenAmpere</h1></div>
            {error ? (
              <>
                <Notice kind="error">
                  <strong>{error}</strong>
                  <div>Läuft der OpenAmpere-Dienst? Neuer Versuch alle paar Sekunden.</div>
                </Notice>
                <Button variant="secondary" onClick={reload}>Jetzt erneut versuchen</Button>
              </>
            ) : <p className="hint">Lade …</p>}
          </div>
        </main>
        <ToastHost />
      </div>
    );
  }
  if (auth && !auth.configured) {
    return (
      <div className="app">
        <main><PasswordSetup onDone={reloadAuth} /></main>
        <ToastHost />
      </div>
    );
  }
  if (!status.configured) {
    return (
      <div className="app">
        <main><Setup onDone={reload} /></main>
        {login.dialog}
        <ToastHost />
      </div>
    );
  }

  return (
    <div className="app">
      {error && <div className="offline-banner" role="alert">Keine Verbindung zum OpenAmpere-Server – versuche erneut …</div>}
      <main>
        {tab === "dashboard" && <Dashboard snap={snap} online={online} />}
        {tab === "report" && <Report />}
        {tab === "more" && <More snap={snap} />}
      </main>
      <nav className="bottom">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)} aria-label={t.label}>
            {t.icon}
          </button>
        ))}
      </nav>
      {login.dialog}
      <ToastHost />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
