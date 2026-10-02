import { useState } from "react";
import type { Snapshot, Status } from "./api";
import { useResource } from "./api";
import { kwh, percent, updatedLabel, num } from "./format";
import { BatteryIcon, CheckCircle, InverterIcon, WarnCircle } from "./icons";
import { AboutPage, AppearancePage, BatteryPage, ConnectionPage, ControlPage, DataPage, ExportLimitPage, LicensesPage, PvSystemPage, TariffPage } from "./SettingsPages";
import { SecurityPage } from "./AuthScreens";
import { MenuRow, SubPage } from "./ui";

function StatusPill({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div className={`status-pill ${ok ? "" : "warn"}`}>
      {ok ? <CheckCircle /> : <WarnCircle />}
      <span><strong>Status:</strong> {text}</span>
    </div>
  );
}

function InstallationPage({ snap, onBack }: { snap: Snapshot | null; onBack: () => void }) {
  const { data: status } = useResource<Status>("/api/status", 10_000);
  const device = status?.device;
  const alarms = snap?.alarms.some((a) => a !== 0) ?? false;

  return (
    <SubPage title="Meine Anlage" onBack={onBack}>
      <div className="section-title">Infos &amp; Status</div>
      <div className="card">
        <div className="device-row"><BatteryIcon size={44} soc={snap?.battery_soc ?? null} />Speicher</div>
        <StatusPill ok={!alarms && snap?.battery_soc != null} text={alarms ? "Störung gemeldet" : snap ? "In Ordnung" : "Keine Daten"} />
        <dl className="facts">
          <dt>Ladestand</dt><dd>{percent(snap?.battery_soc)}</dd>
          <dt>Gesundheit (SoH)</dt><dd>{percent(snap?.battery_soh)}</dd>
          <dt>Temperatur</dt><dd>{snap?.battery_temperature != null ? `${num(snap.battery_temperature, 1)} °C` : "–"}</dd>
          <dt>Gesamt geladen</dt><dd>{kwh(snap?.totals.battery_charge)}</dd>
          <dt>Gesamt entladen</dt><dd>{kwh(snap?.totals.battery_discharge)}</dd>
        </dl>
      </div>

      <div className="card">
        <div className="device-row"><InverterIcon size={44} />Wechselrichter</div>
        <StatusPill ok={!!status?.connected} text={status?.connected ? "Verbunden" : status?.last_error ?? "Getrennt"} />
        <dl className="facts">
          <dt>Modell</dt><dd>{device ? `${device.manufacturer} ${device.model}` : "–"}</dd>
          <dt>Seriennr.</dt><dd>{device?.serial ?? "–"}</dd>
          <dt>Firmware</dt><dd>{device?.firmware ?? "–"}</dd>
          <dt>Letzte Messung</dt><dd>{status?.last_update ? updatedLabel(status.last_update) : "–"}</dd>
        </dl>
      </div>

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

export function More({ snap }: { snap: Snapshot | null }) {
  const [page, setPage] = useState<string | null>(null);
  const { data: status } = useResource<Status>("/api/status", 10_000);
  const back = () => setPage(null);
  const nav = { onBack: back, onNavigate: setPage };

  switch (page) {
    case "installation": return <InstallationPage snap={snap} onBack={back} />;
    case "battery": return <BatteryPage {...nav} />;
    case "tariff": return <TariffPage {...nav} />;
    case "pv": return <PvSystemPage {...nav} snap={snap} />;
    case "export-limit": return <ExportLimitPage {...nav} />;
    case "connection": return <ConnectionPage {...nav} />;
    case "control": return <ControlPage {...nav} />;
    case "appearance": return <AppearancePage {...nav} />;
    case "data": return <DataPage {...nav} />;
    case "about": return <AboutPage {...nav} />;
    case "security": return <SecurityPage onBack={back} />;
    case "licenses": return <LicensesPage onBack={() => setPage("about")} />;
  }

  const control = status?.control;
  return (
    <div className="page">
      <div className="page-head"><h1>Mehr</h1></div>

      <div className="section-title">Mein System</div>
      <div className="card menu">
        <MenuRow label="Meine Anlage" hint={status?.device?.model ?? undefined} onClick={() => setPage("installation")} />
        <MenuRow label="PV-Anlage" hint="Modulfelder benennen" onClick={() => setPage("pv")} />
        <MenuRow label="Speicher & Notstrom" onClick={() => setPage("battery")} />
        <MenuRow label="Einspeisebegrenzung" hint="Nur nach Zustimmung des Netzbetreibers ändern" onClick={() => setPage("export-limit")} />
        <MenuRow label="Stromtarif" onClick={() => setPage("tariff")} />
      </div>

      <div className="section-title">Einstellungen</div>
      <div className="card menu">
        <MenuRow label="Verbindung" hint={status?.connected ? "Verbunden" : "Nicht verbunden"} onClick={() => setPage("connection")} />
        <MenuRow label="Steuerung"
          hint={control?.enabled ? (control.dry_run ? "Probemodus" : "Aktiv") : "Aus"} onClick={() => setPage("control")} />
        <MenuRow label="Zugriffsschutz" hint="Passwort, Anmeldung" onClick={() => setPage("security")} />
        <MenuRow label="Darstellung" onClick={() => setPage("appearance")} />
        <MenuRow label="Daten & Sicherung" hint="Sicherung, Verlauf aus der EKD-Cloud" onClick={() => setPage("data")} />
      </div>

      <div className="card menu">
        <MenuRow label="Über OpenAmpere" onClick={() => setPage("about")} />
      </div>
    </div>
  );
}
