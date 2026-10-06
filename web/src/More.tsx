import { Fragment } from "react";
import type { Settings, Snapshot, Status } from "./api";
import { activeInputs, useResource, useStale } from "./api";
import { kwh, percent, todayIso, updatedLabel, num } from "./format";
import { BatteryIcon, CheckCircle, InverterIcon, WarnCircle } from "./icons";
import { AboutPage, AppearancePage, ConnectionPage, ControlPage, DataPage, ExportLimitPage, LicensesPage, PvSystemPage, TariffPage } from "./SettingsPages";
import { SecurityPage } from "./AuthScreens";
import { AppsPage } from "./AppsPage";
import { RemotePage } from "./RemotePage";
import { NotifyPage } from "./NotifyPage";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { MenuRow, Notice, SubPage } from "./ui";
import { t } from "./i18n";
import { deviceStatus } from "./DevicesPage";
import { PvInputsCard, TemperaturesCard } from "./Dashboard";
import { TemperatureSection } from "./Report";
import { goBack, navigate } from "./route";
import { ConsumersPage } from "./ConsumersPage";
import { WallboxPage } from "./WallboxPage";
import { BillingPage } from "./BillingPage";
import { GridMeterPage } from "./GridMeterPage";
import { BatteryHealthCard, FirmwareFacts } from "./BatteryHealth";

function StatusPill({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div className={`status-pill ${ok ? "" : "warn"}`}>
      {ok ? <CheckCircle /> : <WarnCircle />}
      <span><strong>{t("settings.statusPill.label")}</strong> {text}</span>
    </div>
  );
}

const RULES: Record<string, string> = { unknown: t("common.notSpecified"), limit_60: t("settings.rules.limit60Percent"),
  limit_70: t("settings.rules.limit70Percent"), operator: t("settings.rules.operator"), none: t("common.noLimitValue") };

function InstallationPage({ snap, onBack, onNavigate }: { snap: Snapshot | null; onBack: () => void; onNavigate: (p: string) => void }) {
  const { data: status } = useResource<Status>("/api/status", 10_000);
  const settings = useResource<Settings>("/api/settings").data?.values;
  const device = status?.device;
  const codes = (snap?.alarms ?? []).map((a, i) => ({ word: i + 1, value: a })).filter((a) => a.value !== 0);
  const alarms = codes.length > 0;
  const stale = useStale(snap, true, status);

  return (
    <SubPage title={t("common.mySystem")} onBack={onBack}>
      <div className="section-title">{t("settings.installationPage.infoStatus")}</div>
      <div className="card">
        <div className="device-row"><BatteryIcon size={44} soc={snap?.battery_soc ?? null} />{t("common.battery")}</div>
        <StatusPill ok={!alarms && !stale && snap?.battery_soc != null}
          text={alarms ? t("common.faultReported") : !snap ? t("settings.installationPage.noData") : stale ? t("settings.installationPage.noCurrentValues") : t("settings.installationPage.ok")} />
        {alarms && (
          <Notice kind="error">
            {t("settings.installationPage.faultCodes", { codes: codes.map((c) => t("settings.installationPage.faultCode",
              { word: c.word, code: `0x${c.value.toString(16).toUpperCase().padStart(4, "0")}` })).join(", ") })}{" "}
            {t("settings.installationPage.faultHint")}
          </Notice>
        )}
        <dl className="facts">
          <dt>{t("common.stateOfCharge")}</dt><dd>{percent(snap?.battery_soc)}</dd>
          <dt>{t("settings.installationPage.health")}</dt><dd>{percent(snap?.battery_soh)}</dd>
          {snap?.temperatures.battery_cell_max != null && snap.temperatures.battery_cell_min != null ? <>
            <dt>{t("settings.installationPage.cells")}</dt><dd>{num(snap.temperatures.battery_cell_min, 1)} – {num(snap.temperatures.battery_cell_max, 1)} °C</dd>
            <dt>{t("settings.installationPage.bms")}</dt><dd>{snap.battery_temperature != null ? `${num(snap.battery_temperature, 1)} °C` : "–"}</dd>
          </> : <><dt>{t("settings.installationPage.temperature")}</dt><dd>{snap?.battery_temperature != null ? `${num(snap.battery_temperature, 1)} °C` : "–"}</dd></>}
          <dt>{t("settings.installationPage.totalCharged")}</dt><dd>{kwh(snap?.totals.battery_charge)}</dd>
          <dt>{t("settings.installationPage.totalDischarged")}</dt><dd>{kwh(snap?.totals.battery_discharge)}</dd>
        </dl>
      </div>
      <BatteryHealthCard />

      <div className="card">
        <div className="device-row"><InverterIcon size={44} />{t("common.inverter")}</div>
        <StatusPill ok={!!status?.connected} text={status?.connected ? t("settings.installationPage.connected") : status?.last_error ?? t("settings.installationPage.disconnected")} />
        <dl className="facts">
          <dt>{t("settings.installationPage.model")}</dt><dd>{device ? `${device.manufacturer} ${device.model}` : "–"}</dd>
          <dt>{t("settings.installationPage.serialNumber")}</dt><dd>{device?.serial ?? "–"}</dd>
          <dt>Firmware</dt><dd>{device?.firmware ?? "–"}</dd>
          <dt>{t("settings.installationPage.lastReading")}</dt><dd>{status?.last_update ? updatedLabel(status.last_update) : "–"}</dd>
        </dl>
        <FirmwareFacts firmware={status?.firmware} />
      </div>

      <div className="section-title">{t("common.pvSystem")}</div>
      <div className="card">
        <dl className="facts">
          <dt>{t("settings.installationPage.pvCapacity")}</dt><dd>{settings?.["pv.installed_kwp"] ? `${num(settings["pv.installed_kwp"], 1)} kWp` : t("common.notSpecified")}</dd>
          <dt>{t("settings.installationPage.pvStrings")}</dt><dd>{activeInputs(snap, settings?.["pv.hidden_inputs"]).length || "–"}</dd>
          <dt>{t("common.feedInRule")}</dt><dd>{RULES[settings?.["grid.feed_in_rule"] ?? "unknown"]}</dd>
        </dl>
      </div>
      <PvInputsCard snap={snap} />
      <div className="card menu">
        <MenuRow label={t("settings.installationPage.nameStrings")} hint={t("settings.installationPage.stringNamesHint")} onClick={() => onNavigate("pv")} />
        <MenuRow label={t("common.exportLimit")} hint={t("settings.installationPage.pvCapacityAndRule")} onClick={() => onNavigate("export-limit")} />
      </div>

      {!!status?.devices.items.length && (
        <>
          <div className="section-title">{t("common.otherDevices")}</div>
          <div className="card">
            <dl className="facts">
              {status.devices.items.map((d) => (
                <Fragment key={d.key}><dt>{d.name}</dt><dd>{deviceStatus(d)}{d.source === "evcc" ? " (evcc)" : ""}</dd></Fragment>
              ))}
            </dl>
          </div>
        </>
      )}

      <TemperaturesCard snap={snap} />
      <TemperatureSection day={todayIso()} refresh={60_000} heading={null} /* the tiles above have the heading */ />

      <div className="section-title">{t("settings.installationPage.meterTotals")}</div>
      <div className="card">
        <dl className="facts">
          <dt>{t("settings.installationPage.pvGeneration")}</dt><dd>{kwh(snap?.totals.pv)}</dd>
          <dt>{t("common.consumption")}</dt><dd>{kwh(snap?.totals.load)}</dd>
          <dt>{t("common.gridImport")}</dt><dd>{kwh(snap?.totals.grid_import)}</dd>
          <dt>{t("common.feedIn")}</dt><dd>{kwh(snap?.totals.grid_export)}</dd>
        </dl>
      </div>
    </SubPage>
  );
}

export function More({ snap, page }: { snap: Snapshot | null; page: string | null }) {
  const setPage = (p: string) => navigate(`more/${p}`);
  const { data: status } = useResource<Status>("/api/status", 10_000);
  const back = () => goBack("more");
  const nav = { onBack: back, onNavigate: setPage };

  switch (page) {
    case "installation": return <InstallationPage snap={snap} onBack={back} onNavigate={setPage} />;
    case "notify": return <NotifyPage {...nav} />;
    case "diagnostics": return <DiagnosticsPage {...nav} />;
    case "tariff": return <TariffPage {...nav} />;
    case "billing": return <BillingPage {...nav} />;
    case "gridmeter": return <GridMeterPage onBack={() => goBack("more/connection")} />;
    // parts of "Meine Anlage"
    case "pv": return <PvSystemPage onBack={() => goBack("more/installation")} snap={snap} />;
    case "export-limit": return <ExportLimitPage onBack={() => goBack("more/installation")} onNavigate={setPage} />;
    // devices are set up under "Verbindung"
    case "device-setup": return <ConsumersPage onBack={() => goBack("more/connection")} />;
    case "wallbox": return <WallboxPage onBack={() => goBack("more/connection")} />;
    // moved to "Geräte"; old links still work
    case "battery": navigate("devices/battery"); return null;
    case "charging": navigate("devices/charging"); return null;
    case "connection": return <ConnectionPage {...nav} />;
    case "control": return <ControlPage {...nav} />;
    case "appearance": return <AppearancePage {...nav} />;
    case "data": return <DataPage {...nav} />;
    case "about": return <AboutPage {...nav} />;
    case "security": return <SecurityPage onBack={back} />;
    case "apps": return <AppsPage onBack={back} />;
    case "remote": return <RemotePage onBack={back} />;
    case "licenses": return <LicensesPage onBack={() => goBack("more/about")} />;
  }

  const control = status?.control;
  return (
    <div className="page">
      <div className="page-head"><h1>{t("common.more")}</h1></div>

      <div className="section-title">{t("settings.more.systemTitle")}</div>
      <div className="card menu">
        <MenuRow label={t("common.mySystem")} hint={t("settings.more.mySystemHint")} onClick={() => setPage("installation")} />
        <MenuRow label={t("common.electricityTariff")} hint={t("settings.more.tariffHint")} onClick={() => setPage("tariff")} />
        <MenuRow label={t("common.advancePayments")} hint={t("settings.more.billingHint")} onClick={() => setPage("billing")} />
      </div>

      <div className="section-title">{t("settings.more.settingsTitle")}</div>
      <div className="card menu">
        <MenuRow label={t("common.connection")} hint={t("settings.more.connectionHint")} onClick={() => setPage("connection")} />
        <MenuRow label={t("common.controlLog")}
          hint={control?.enabled ? (control.dry_run ? t("common.testing") : t("common.active")) : t("common.viewOnly")} onClick={() => setPage("control")} />
        <MenuRow label={t("common.notifications")} hint={t("settings.more.notificationsHint")} onClick={() => setPage("notify")} />
        <MenuRow label={t("common.accessProtection")} hint={t("common.password")} onClick={() => setPage("security")} />
        <MenuRow label={t("common.connectedApps")} hint={t("settings.more.appsHint")} onClick={() => setPage("apps")} />
        <MenuRow label={t("common.remoteAccess")} hint={t("settings.more.remoteHint")} onClick={() => setPage("remote")} />
        <MenuRow label={t("common.appearance")} hint={t("settings.more.appearanceHint")} onClick={() => setPage("appearance")} />
      </div>

      <div className="section-title">{t("settings.more.dataTitle")}</div>
      <div className="card menu">
        <MenuRow label={t("common.dataBackup")} hint={t("settings.more.dataHint")} onClick={() => setPage("data")} />
        <MenuRow label={t("common.diagnostics")} hint={t("settings.more.diagnosticsHint")} onClick={() => setPage("diagnostics")} />
        <MenuRow label={t("common.aboutOpenampere")} onClick={() => setPage("about")} />
      </div>
    </div>
  );
}
