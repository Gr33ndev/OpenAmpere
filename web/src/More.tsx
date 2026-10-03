import { Fragment } from "react";
import type { Settings, Snapshot, Status } from "./api";
import { useResource, useStale } from "./api";
import { kwh, percent, todayIso, updatedLabel, num } from "./format";
import { BatteryIcon, CheckCircle, InverterIcon, WarnCircle } from "./icons";
import { AboutPage, AppearancePage, ConnectionPage, ControlPage, DataPage, ExportLimitPage, LicensesPage, PvSystemPage, TariffPage } from "./SettingsPages";
import { SecurityPage } from "./AuthScreens";
import { NotifyPage } from "./NotifyPage";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { MenuRow, Notice, SubPage } from "./ui";
import { deviceStatus } from "./DevicesPage";
import { PvInputsCard, TemperaturesCard } from "./Dashboard";
import { TemperatureSection } from "./Report";
import { goBack, navigate } from "./route";
import { ConsumersPage } from "./ConsumersPage";
import { WallboxPage } from "./WallboxPage";
import { BillingPage } from "./BillingPage";
import { BatteryHealthCard, FirmwareFacts } from "./BatteryHealth";

function StatusPill({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div className={`status-pill ${ok ? "" : "warn"}`}>
      {ok ? <CheckCircle /> : <WarnCircle />}
      <span><strong>Status:</strong> {text}</span>
    </div>
  );
}

const RULES: Record<string, string> = { unknown: "nicht angegeben", limit_60: "60 % der Modulleistung",
  limit_70: "70 % der Modulleistung", operator: "Wert vom Netzbetreiber", none: "keine Begrenzung" };

function InstallationPage({ snap, onBack, onNavigate }: { snap: Snapshot | null; onBack: () => void; onNavigate: (p: string) => void }) {
  const { data: status } = useResource<Status>("/api/status", 10_000);
  const settings = useResource<Settings>("/api/settings").data?.values;
  const device = status?.device;
  const codes = (snap?.alarms ?? []).map((a, i) => ({ word: i + 1, value: a })).filter((a) => a.value !== 0);
  const alarms = codes.length > 0;
  const stale = useStale(snap, true, status);

  return (
    <SubPage title="Meine Anlage" onBack={onBack}>
      <div className="section-title">Infos &amp; Status</div>
      <div className="card">
        <div className="device-row"><BatteryIcon size={44} soc={snap?.battery_soc ?? null} />Speicher</div>
        <StatusPill ok={!alarms && !stale && snap?.battery_soc != null}
          text={alarms ? "Störung gemeldet" : !snap ? "Keine Daten" : stale ? "Keine aktuellen Werte" : "In Ordnung"} />
        {alarms && (
          <Notice kind="error">
            Der Wechselrichter meldet einen Störungscode:{" "}
            {codes.map((c) => `Wort ${c.word}: 0x${c.value.toString(16).toUpperCase().padStart(4, "0")}`).join(", ")}.
            Die Bedeutung steht im Handbuch des Herstellers. Nenne den Code deinem Installationsbetrieb, wenn die Meldung bleibt.
          </Notice>
        )}
        <dl className="facts">
          <dt>Ladestand</dt><dd>{percent(snap?.battery_soc)}</dd>
          <dt>Gesundheit (SoH)</dt><dd>{percent(snap?.battery_soh)}</dd>
          <dt>Temperatur</dt><dd>{snap?.battery_temperature != null ? `${num(snap.battery_temperature, 1)} °C` : "–"}</dd>
          <dt>Gesamt geladen</dt><dd>{kwh(snap?.totals.battery_charge)}</dd>
          <dt>Gesamt entladen</dt><dd>{kwh(snap?.totals.battery_discharge)}</dd>
        </dl>
      </div>
      <BatteryHealthCard />

      <div className="card">
        <div className="device-row"><InverterIcon size={44} />Wechselrichter</div>
        <StatusPill ok={!!status?.connected} text={status?.connected ? "Verbunden" : status?.last_error ?? "Getrennt"} />
        <dl className="facts">
          <dt>Modell</dt><dd>{device ? `${device.manufacturer} ${device.model}` : "–"}</dd>
          <dt>Seriennr.</dt><dd>{device?.serial ?? "–"}</dd>
          <dt>Firmware</dt><dd>{device?.firmware ?? "–"}</dd>
          <dt>Letzte Messung</dt><dd>{status?.last_update ? updatedLabel(status.last_update) : "–"}</dd>
        </dl>
        <FirmwareFacts firmware={status?.firmware} />
      </div>

      <div className="section-title">PV-Anlage</div>
      <div className="card">
        <dl className="facts">
          <dt>Modulleistung</dt><dd>{settings?.["pv.installed_kwp"] ? `${num(settings["pv.installed_kwp"], 1)} kWp` : "nicht angegeben"}</dd>
          <dt>Modulfelder</dt><dd>{snap?.pv_inputs.filter((p) => p.power != null).length || "–"}</dd>
          <dt>Einspeiseregel</dt><dd>{RULES[settings?.["grid.feed_in_rule"] ?? "unknown"]}</dd>
        </dl>
      </div>
      <PvInputsCard snap={snap} />
      <div className="card menu">
        <MenuRow label="Modulfelder benennen" hint="z. B. Süddach, Garage" onClick={() => onNavigate("pv")} />
        <MenuRow label="Einspeisebegrenzung" hint="Modulleistung und gesetzliche Regel" onClick={() => onNavigate("export-limit")} />
      </div>

      {!!status?.devices.items.length && (
        <>
          <div className="section-title">Weitere Geräte</div>
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
      <TemperatureSection day={todayIso()} refresh={60_000} />

      <div className="section-title">Zählerstände</div>
      <div className="card">
        <dl className="facts">
          <dt>PV-Erzeugung</dt><dd>{kwh(snap?.totals.pv)}</dd>
          <dt>Verbrauch</dt><dd>{kwh(snap?.totals.load)}</dd>
          <dt>Netzbezug</dt><dd>{kwh(snap?.totals.grid_import)}</dd>
          <dt>Einspeisung</dt><dd>{kwh(snap?.totals.grid_export)}</dd>
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
    case "licenses": return <LicensesPage onBack={() => goBack("more/about")} />;
  }

  const control = status?.control;
  return (
    <div className="page">
      <div className="page-head"><h1>Mehr</h1></div>

      <div className="section-title">Anlage</div>
      <div className="card menu">
        <MenuRow label="Meine Anlage" hint="Status, Module, Einspeisebegrenzung, Temperaturen" onClick={() => setPage("installation")} />
        <MenuRow label="Stromtarif" hint="Preise und Grundpreis" onClick={() => setPage("tariff")} />
        <MenuRow label="Abschläge" hint="Jahresabrechnung vorhersagen" onClick={() => setPage("billing")} />
      </div>

      <div className="section-title">Einstellungen</div>
      <div className="card menu">
        <MenuRow label="Verbindung" hint="Wechselrichter, Heizstab, Wallbox" onClick={() => setPage("connection")} />
        <MenuRow label="Steuerung und Protokoll"
          hint={control?.enabled ? (control.dry_run ? "Testen" : "Aktiv") : "Nur ansehen"} onClick={() => setPage("control")} />
        <MenuRow label="Benachrichtigungen" hint="Hinweise aufs Handy" onClick={() => setPage("notify")} />
        <MenuRow label="Zugriffsschutz" hint="Passwort" onClick={() => setPage("security")} />
        <MenuRow label="Darstellung" hint="Hell, dunkel, Zeitzone" onClick={() => setPage("appearance")} />
      </div>

      <div className="section-title">Daten und Hilfe</div>
      <div className="card menu">
        <MenuRow label="Daten & Sicherung" hint="Sicherung, Export, Verlauf aus der EKD-Cloud" onClick={() => setPage("data")} />
        <MenuRow label="Diagnose" hint="Gerät prüfen und Bericht teilen" onClick={() => setPage("diagnostics")} />
        <MenuRow label="Über OpenAmpere" onClick={() => setPage("about")} />
      </div>
    </div>
  );
}
