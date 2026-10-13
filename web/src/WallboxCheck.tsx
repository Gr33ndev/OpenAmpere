// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { Fragment, useState } from "react";
import { postJson, useResource } from "./api";
import { DeviceSearch } from "./DeviceSearch";
import { num } from "./format";
import { t } from "./i18n";
import { navigate } from "./route";
import { Button, copyText, Field, LoadState, Notice, toast } from "./ui";

/** A wallbox without evcc (#318), so far only a read-only check (#325): nothing is written to the wallbox, the
 *  result goes into the diagnostics report so the registers can be confirmed on real devices. */

type Outcome = "readable" | "busy" | "rejected" | "no_answer" | "connections_taken" | "unreachable";
type Findings = { model?: string | null; firmware?: string | null; status?: string | null; max_power_kw?: number | null;
  min_current_a?: number | null; max_current_a?: number | null; work_mode?: string | null; fallback_current_a?: number | null;
  validity_s?: number | null; phase_switching?: boolean | null };
export type WallboxCheckResult = { model: string; label: string; host: string; port: number | null; unit: number;
  outcome: Outcome; tried_ports: number[]; findings: Findings };

const OUTCOME: Record<Exclude<Outcome, "unreachable">, string> = {
  readable: t("devices.wallboxDirect.outcomeReadable"),
  busy: t("devices.wallboxDirect.outcomeBusy"),
  rejected: t("devices.wallboxDirect.outcomeRejected"),
  no_answer: t("devices.wallboxDirect.outcomeNoAnswer"),
  connections_taken: t("devices.wallboxDirect.outcomeConnectionsTaken"),
};
const STATUS: Record<string, string> = {
  idle: t("devices.wallboxDirect.statusIdle"), connected: t("devices.wallboxDirect.statusConnected"),
  starting: t("devices.wallboxDirect.statusStarting"), charging: t("devices.wallboxDirect.statusCharging"),
  paused: t("devices.wallboxDirect.statusPaused"), finished: t("devices.wallboxDirect.statusFinished"),
  fault: t("devices.wallboxDirect.statusFault"), locked: t("devices.wallboxDirect.statusLocked"),
  switching_phases: t("devices.wallboxDirect.statusSwitchingPhases"),
};
const WORK_MODE: Record<string, string> = {
  controlled: t("devices.wallboxDirect.workModeControlled"), plug_and_charge: t("devices.wallboxDirect.workModePlugAndCharge"),
  locked: t("devices.wallboxDirect.workModeLocked"),
};

function Facts({ f }: { f: Findings }) {
  const rows: [string, string][] = [];
  if (f.model) rows.push([t("devices.wallboxDirect.modelCode"), f.model]);
  if (f.firmware) rows.push([t("devices.wallboxDirect.firmware"), f.firmware]);
  if (f.status) rows.push([t("devices.wallboxDirect.status"), STATUS[f.status] ?? f.status]);
  if (f.max_power_kw != null) rows.push([t("devices.wallboxDirect.maxPower"), `${num(f.max_power_kw, 1)} kW`]);
  if (f.min_current_a != null && f.max_current_a != null) {
    rows.push([t("devices.wallboxDirect.currentRange"), `${num(f.min_current_a, 1)}–${num(f.max_current_a, 1)} A`]);
  }
  if (f.work_mode) rows.push([t("devices.wallboxDirect.workMode"), WORK_MODE[f.work_mode] ?? f.work_mode]);
  if (f.fallback_current_a != null) rows.push([t("devices.wallboxDirect.fallbackCurrent"), `${num(f.fallback_current_a, 1)} A`]);
  if (f.phase_switching != null) {
    rows.push([t("devices.wallboxDirect.phaseSwitching"), f.phase_switching ? t("common.on") : t("common.off")]);
  }
  if (!rows.length) return null;
  return (
    <dl className="facts">
      {rows.map(([label, value]) => <Fragment key={label}><dt>{label}</dt><dd>{value}</dd></Fragment>)}
    </dl>
  );
}

type OcppView = { active: boolean; remaining_s: number; connected: boolean; path: string; charge_point: string | null;
  vendor: string | null; model: string | null; firmware: string | null; configuration_keys: number; status: string | null;
  messages: number };

/** Listening over OCPP (#328): the wallbox connects to OpenAmpere, which answers "Pending" and only asks. */
function OcppListen({ origin }: { origin: string }) {
  const { data: view, setData } = useResource<OcppView>("/api/wallbox/ocpp", 3000);
  const [busy, setBusy] = useState(false);
  // the wallbox connects to the direct address, never to the one of the Home Assistant sidebar
  const address = origin ? `${origin.replace(/^http/, "ws")}${view?.path ?? "/ocpp"}` : "";
  const run = async (path: string) => {
    setBusy(true);
    try {
      setData(await postJson<OcppView>(path, {}));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    if (await copyText(address)) toast(t("devices.wallboxDirect.ocppCopied"));
  };
  return (
    <>
      <div className="section-title">{t("devices.wallboxDirect.ocppTitle")}</div>
      <div className="card form">
        <p className="hint flush">{t("devices.wallboxDirect.ocppIntro")}</p>
        <Notice kind="warn">{t("devices.wallboxDirect.ocppWarning")}</Notice>
        {!origin ? <Notice kind="info">{t("devices.wallboxPage.directAccessOff")}</Notice> : !view?.active ? (
          <Button variant="secondary" busy={busy} disabled={busy || !view} onClick={() => void run("/api/wallbox/ocpp/start")}>
            {t("devices.wallboxDirect.ocppStart")}</Button>
        ) : (
          <>
            <p className="hint flush">{t("devices.wallboxDirect.ocppAddress")}</p>
            <pre className="code-block">{address}</pre>
            <button type="button" className="link" onClick={() => void copy()}>{t("devices.wallboxDirect.ocppCopy")}</button>
            <Notice kind={view.connected ? "ok" : "info"}>
              {view.connected ? t("devices.wallboxDirect.ocppConnected") : t("devices.wallboxDirect.ocppWaiting",
                { minutes: Math.ceil(view.remaining_s / 60) })}
            </Notice>
            {(view.vendor || view.model) && (
              <dl className="facts">
                <dt>{t("devices.wallboxDirect.ocppVendor")}</dt><dd>{[view.vendor, view.model].filter(Boolean).join(" ")}</dd>
                {view.firmware && <><dt>{t("devices.wallboxDirect.firmware")}</dt><dd>{view.firmware}</dd></>}
                {view.status && <><dt>{t("devices.wallboxDirect.status")}</dt><dd>{view.status}</dd></>}
                <dt>{t("devices.wallboxDirect.ocppSettings")}</dt><dd>{view.configuration_keys}</dd>
              </dl>
            )}
            <Button variant="secondary" busy={busy} disabled={busy} onClick={() => void run("/api/wallbox/ocpp/stop")}>
              {t("devices.wallboxDirect.ocppStop")}</Button>
          </>
        )}
        {!!view?.messages && (
          <>
            <p className="hint flush">{t("devices.wallboxDirect.ocppSwitchBack")}</p>
            <Button variant="secondary" onClick={() => navigate("more/diagnostics")}>{t("devices.wallboxDirect.toReport")}</Button>
          </>
        )}
      </div>
    </>
  );
}

export function DirectWallbox({ origin }: { origin: string }) {
  const { data, error, reload } = useResource<{ models: { key: string; label: string }[] }>("/api/wallbox/models");
  const models = data?.models ?? [];
  const [model, setModel] = useState("");
  const chosen = model || models[0]?.key || "";
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [unit, setUnit] = useState("1");
  const [appMode, setAppMode] = useState("unknown");
  const [wired, setWired] = useState("unknown");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WallboxCheckResult | null>(null);

  const check = async () => {
    const p = port.trim() ? Number(port) : null;
    const u = Number(unit);
    if ((p != null && !Number.isInteger(p)) || !Number.isInteger(u)) {
      toast(t("devices.wallboxDirect.invalidNumbers"), "error");
      return;
    }
    setBusy(true);
    try {
      setResult(await postJson<WallboxCheckResult>("/api/wallbox/check",
        { model: chosen, host: host.trim(), port: p, unit: u, app_mode: appMode, wired }));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  // the choice of the model only once the list is there (no flash of the hint for other wallboxes)
  if (!data) return <LoadState error={error} onRetry={reload} />;
  return (
    <>
      <Notice kind="info">{t("devices.wallboxDirect.preview")}</Notice>
      <div className="card form">
        <Field label={t("devices.wallboxDirect.model")}>
          <select className="input" value={chosen || "other"} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
            <option value="other">{t("devices.wallboxDirect.modelOther")}</option>
          </select>
        </Field>
        {chosen === "other" || !models.length ? (
          <>
            <p className="hint flush">{t("devices.wallboxDirect.otherHint")}</p>
            <Button variant="secondary" onClick={() => navigate("more/device-reader")}>{t("devices.wallboxDirect.toDeviceReader")}</Button>
          </>
        ) : (
          <>
            <DeviceSearch label={t("devices.wallboxDirect.search")}
              onUse={(d) => { setHost(d.host); if (d.port) setPort(String(d.port)); }} />
            <Field label={t("devices.wallboxDirect.address")} hint={t("devices.wallboxDirect.addressHint", { example: "192.168.178.60" })}>
              <input className="input" value={host} inputMode="decimal" autoComplete="off" onChange={(e) => setHost(e.target.value)} />
            </Field>
            <details className="advanced">
              <summary>{t("common.advanced")}</summary>
              <Field label={t("devices.wallboxDirect.port")} hint={t("devices.wallboxDirect.portHint")}>
                <input className="input" value={port} inputMode="numeric" placeholder="502" onChange={(e) => setPort(e.target.value)} />
              </Field>
              <Field label={t("devices.wallboxDirect.unit")} hint={t("devices.wallboxDirect.unitHint")}>
                <input className="input" value={unit} inputMode="numeric" onChange={(e) => setUnit(e.target.value)} />
              </Field>
            </details>
            <Field label={t("devices.wallboxDirect.appMode")} hint={t("devices.wallboxDirect.appModeHint")}>
              <select className="input" value={appMode} onChange={(e) => setAppMode(e.target.value)}>
                <option value="default">{t("devices.wallboxDirect.appModeDefault")}</option>
                <option value="modbus_tcp">Modbus TCP</option>
                <option value="ocpp">OCPP</option>
                <option value="unknown">{t("devices.wallboxDirect.dontKnow")}</option>
              </select>
            </Field>
            <Field label={t("devices.wallboxDirect.wired")}>
              <select className="input" value={wired} onChange={(e) => setWired(e.target.value)}>
                <option value="yes">{t("devices.wallboxDirect.yes")}</option>
                <option value="no">{t("devices.wallboxDirect.no")}</option>
                <option value="unknown">{t("devices.wallboxDirect.dontKnow")}</option>
              </select>
            </Field>
            {wired === "yes" && <Notice kind="warn">{t("devices.wallboxDirect.wiredHint")}</Notice>}
            <p className="hint flush">{t("devices.wallboxDirect.nothingChanges")}</p>
            <Button busy={busy} disabled={busy || !host.trim()} onClick={() => void check()}>
              {t("devices.wallboxDirect.check")}</Button>
          </>
        )}
      </div>

      {result && (
        <>
          <div className="section-title">{t("devices.wallboxDirect.result")}</div>
          <Notice kind={result.outcome === "readable" ? "ok" : result.outcome === "unreachable" ? "error" : "warn"}>
            {result.outcome === "unreachable"
              ? t("devices.wallboxDirect.outcomeUnreachable", { ports: result.tried_ports.join(", ") })
              : OUTCOME[result.outcome]}
          </Notice>
          <div className="card form">
            <Facts f={result.findings} />
            {result.findings.fallback_current_a != null && <p className="hint flush">{t("devices.wallboxDirect.fallbackHint")}</p>}
            <p className="hint flush">{t("devices.wallboxDirect.reportHint")}</p>
            <Button variant="secondary" onClick={() => navigate("more/diagnostics")}>{t("devices.wallboxDirect.toReport")}</Button>
          </div>
        </>
      )}
      <OcppListen origin={origin} />
    </>
  );
}
