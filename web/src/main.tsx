import { StrictMode, useRef, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import type { AuthStatus, Status } from "./api";
import { useLive, useResource } from "./api";
import { PasswordSetup, useLoginPrompt } from "./AuthScreens";
import { Dashboard } from "./Dashboard";
import { RetroDefs } from "./EnergyFlow";
import { NavDevices, NavHome, NavMore, NavReport } from "./icons";
import { DevicesTab } from "./DevicesPage";
import { More } from "./More";
import { Report } from "./Report";
import { applyTheme, storedTheme } from "./SettingsPages";
import { DatabaseNotice } from "./Restore";
import { Setup } from "./Setup";
import { navigate, useRoute } from "./route";
import { setEnergyStep, setTimeZone } from "./format";
import { DEMO } from "./demo/flag";
import { lang, t, tx } from "./i18n";
import { UpdateBanner } from "./Updates";
import { Button, Notice, ToastHost } from "./ui";
import "./styles.css";

type Tab = "dashboard" | "devices" | "report" | "more";

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: "dashboard", label: t("common.overview"), icon: <NavHome /> },
  { id: "devices", label: t("common.devices"), icon: <NavDevices /> },
  { id: "report", label: t("common.report"), icon: <NavReport /> },
  { id: "more", label: t("common.more"), icon: <NavMore /> },
];

applyTheme(storedTheme());
// the demo inside the phone frame on the website: scroll without a visible scrollbar
if (DEMO && window.self !== window.top) document.documentElement.classList.add("embedded");
// screenshots for the README (scripts/screenshots.sh) are taken without the demo banner
// the website around the demo, in the same language (German at the root, others in /<lang>/)
const SITE = lang() === "de" ? "../" : `../${lang()}/`;
const SCREENSHOT = DEMO && new URLSearchParams(window.location.search).has("screenshot");

function App() {
  const route = useRoute();
  const tab: Tab = TABS.some((item) => item.id === route[0]) ? (route[0] as Tab) : "dashboard";
  const setTab = (next: Tab) => navigate(next);
  const { data: status, error, reload } = useResource<Status>("/api/status", 15_000);
  const { data: auth, reload: reloadAuth } = useResource<AuthStatus>("/api/auth/status");
  const { snap, online } = useLive();
  const login = useLoginPrompt(reloadAuth);
  setTimeZone(status?.timezone);
  setEnergyStep(status?.energy_step_wh);
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
                  <div>{t("shell.app.serviceRunningHint")}</div>
                </Notice>
                <Button variant="secondary" onClick={reload}>{t("common.tryAgainNow")}</Button>
              </>
            ) : <p className="hint">{t("common.loading")}</p>}
          </div>
        </main>
        <ToastHost />
      </div>
    );
  }
  if (auth && !auth.configured) {
    return (
      <div className="app">
        <main><DatabaseNotice status={status} auth={auth} /><PasswordSetup onDone={reloadAuth} /></main>
        <ToastHost />
      </div>
    );
  }
  if (!status.configured) {
    return (
      <div className="app">
        <main><DatabaseNotice status={status} auth={auth} /><Setup onDone={reload} /></main>
        {login.dialog}
        <ToastHost />
      </div>
    );
  }

  return (
    <div className="app">
      <div className="status-bar-backdrop" aria-hidden="true" />
      {DEMO && !SCREENSHOT && (
        <div className="demo-banner" role="note">
          <span>{tx("shell.app.demoBanner", { demo: <strong>{t("shell.app.demo")}</strong> })}</span>
          <a href={SITE}>{t("shell.app.about")}</a>
          <a href={`${SITE}impressum.html`}>{t("shell.app.legalNotice")}</a>
        </div>
      )}
      {!DEMO && !error && <UpdateBanner />}
      {error && <div className="offline-banner" role="alert">{t("shell.app.noConnectionRetrying")}</div>}
      {status.web_build && firstBuild.current && status.web_build !== firstBuild.current && (
        <div className="update-banner" role="status">
          {t("shell.app.newVersionInstalled")}
          <button type="button" className="link" onClick={() => window.location.reload()}>{t("shell.app.reloadNow")}</button>
        </div>
      )}
      <main>
        <DatabaseNotice status={status} auth={auth} />
        {tab === "dashboard" && <Dashboard snap={snap} online={online} status={status} />}
        {tab === "devices" && <DevicesTab page={route[1] ?? null} snap={snap} />}
        {tab === "report" && <Report />}
        {tab === "more" && <More snap={snap} page={route[1] ?? null} />}
      </main>
      <nav className="bottom">
        {TABS.map((item) => (
          <button type="button" key={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}
            aria-current={tab === item.id ? "page" : undefined}>
            {item.icon}
            <span className="nav-label">{item.label}</span>
          </button>
        ))}
      </nav>
      {login.dialog}
      <ToastHost />
      <RetroDefs />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
