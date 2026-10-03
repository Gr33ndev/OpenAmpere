import { StrictMode, useRef, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import type { AuthStatus, Status } from "./api";
import { useLive, useResource } from "./api";
import { PasswordSetup, useLoginPrompt } from "./AuthScreens";
import { Dashboard } from "./Dashboard";
import { NavDevices, NavHome, NavMore, NavReport } from "./icons";
import { DevicesTab } from "./DevicesPage";
import { More } from "./More";
import { Report } from "./Report";
import { applyTheme, storedTheme } from "./SettingsPages";
import { Setup } from "./Setup";
import { navigate, useRoute } from "./route";
import { setTimeZone } from "./format";
import { DEMO } from "./demo/flag";
import { Button, Notice, ToastHost } from "./ui";
import "./styles.css";

type Tab = "dashboard" | "devices" | "report" | "more";

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: "dashboard", label: "Übersicht", icon: <NavHome /> },
  { id: "devices", label: "Geräte", icon: <NavDevices /> },
  { id: "report", label: "Auswertung", icon: <NavReport /> },
  { id: "more", label: "Mehr", icon: <NavMore /> },
];

applyTheme(storedTheme());
// the demo inside the phone frame on the website: scroll without a visible scrollbar
if (DEMO && window.self !== window.top) document.documentElement.classList.add("embedded");
// screenshots for the README (scripts/screenshots.sh) are taken without the demo banner
const SCREENSHOT = DEMO && new URLSearchParams(window.location.search).has("screenshot");

function App() {
  const route = useRoute();
  const tab: Tab = TABS.some((t) => t.id === route[0]) ? (route[0] as Tab) : "dashboard";
  const setTab = (t: Tab) => navigate(t);
  const { data: status, error, reload } = useResource<Status>("/api/status", 15_000);
  const { data: auth, reload: reloadAuth } = useResource<AuthStatus>("/api/auth/status");
  const { snap, online } = useLive();
  const login = useLoginPrompt(reloadAuth);
  setTimeZone(status?.timezone);
  const firstBuild = useRef<string | null>(null);
  if (status?.web_build && !firstBuild.current) firstBuild.current = status.web_build;

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
      {DEMO && !SCREENSHOT && (
        <div className="demo-banner" role="note">
          <span><strong>Demo</strong> mit erfundenen Werten</span>
          <a href="../">Infos</a>
          <a href="../impressum.html">Impressum</a>
        </div>
      )}
      {error && <div className="offline-banner" role="alert">Keine Verbindung zum OpenAmpere-Server – versuche erneut …</div>}
      {status.web_build && firstBuild.current && status.web_build !== firstBuild.current && (
        <div className="update-banner" role="status">
          Eine neue Version von OpenAmpere ist installiert.
          <button className="link" onClick={() => window.location.reload()}>Jetzt neu laden</button>
        </div>
      )}
      <main>
        {tab === "dashboard" && <Dashboard snap={snap} online={online} status={status} />}
        {tab === "devices" && <DevicesTab page={route[1] ?? null} snap={snap} />}
        {tab === "report" && <Report />}
        {tab === "more" && <More snap={snap} page={route[1] ?? null} />}
      </main>
      <nav className="bottom">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}
            aria-current={tab === t.id ? "page" : undefined}>
            {t.icon}
            <span className="nav-label">{t.label}</span>
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
