import { useEffect, useState } from "react";
import { getJson, postJson, putJson } from "./api";
import { kw, percent } from "./format";
import { InverterIcon } from "./icons";
import { Button, Field, Notice } from "./ui";
import { navigate } from "./route";
import { t, tx } from "./i18n";

type Device = { manufacturer: string; model: string; serial: string | null; firmware: string | null;
  driver: string; unit: number | null; supports_control: boolean };
type TestResult = { ok: boolean; error?: string; device?: Device; label?: string;
  sample?: { pv_power: number | null; battery_soc: number | null } | null };
type Found = { host: string; port: number; model: string | null; manufacturer: string | null; driver: string | null; unit: number | null;
  serial?: string | null };
type DriverOption = { key: string; label: string };

/** Find / enter the inverter address, detect the device type, test and save.
 *  Used by the setup wizard and the connection settings. */
export function ConnectionForm({ initial, onSaved, saveLabel = t("common.save"), locked = [] }: {
  initial: { host: string; port: number; unit: number; driver?: string };
  onSaved: () => void;
  saveLabel?: string;
  locked?: string[];
}) {
  const [host, setHost] = useState(initial.host);
  const [port, setPort] = useState(initial.port);
  const [unit, setUnit] = useState(initial.unit);
  const [driver, setDriver] = useState(initial.driver ?? "auto");
  const [drivers, setDrivers] = useState<DriverOption[]>([]);
  const [advanced, setAdvanced] = useState(initial.port !== 502 || initial.unit !== 0 || (initial.driver ?? "auto") !== "auto");
  const [prefix, setPrefix] = useState("");
  const [scanning, setScanning] = useState(false);
  const [found, setFound] = useState<Found[] | null>(null);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hostLocked = locked.includes("inverter.host");

  useEffect(() => {
    getJson<{ prefixes: string[] }>("/api/setup/networks")
      .then((r) => setPrefix((p) => p || r.prefixes[0] || ""))
      .catch(() => undefined);
    getJson<{ drivers: DriverOption[] }>("/api/setup/drivers").then((r) => setDrivers(r.drivers)).catch(() => undefined);
  }, []);

  const scan = async () => {
    setScanning(true); setFound(null); setError(null);
    try {
      const r = await postJson<{ devices: Found[] }>("/api/setup/scan", { prefix, port, unit });
      setFound(r.devices);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScanning(false);
    }
  };

  const test = async (target = host) => {
    setTesting(true); setResult(null); setError(null);
    try {
      setResult(await postJson<TestResult>("/api/setup/test", { host: target, port, unit, driver }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    if (!result?.device) return;
    setSaving(true); setError(null);
    try {
      // store what was detected, so the next start does not need to probe again
      const changes: Record<string, unknown> = {
        "inverter.port": port, "inverter.driver": result.device.driver, "inverter.unit": result.device.unit ?? 0,
      };
      if (!hostLocked) changes["inverter.host"] = host.trim();
      await putJson("/api/settings", changes);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const reset = () => setResult(null);

  return (
    <>
      <div className="card form">
        <h2>{t("setup.connectionForm.searchTitle")}</h2>
        <p className="hint">{t("setup.connectionForm.searchHint")}</p>
        <Field label={t("setup.connectionForm.network")} hint={t("setup.connectionForm.networkHint", { example: "192.168.178" })}>
          <input className="input" value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="192.168.178" inputMode="decimal" />
        </Field>
        <Button variant="secondary" onClick={scan} busy={scanning} disabled={!prefix}>{t("setup.connectionForm.searchButton")}</Button>
        {scanning && <p className="hint">{t("setup.connectionForm.takesFewSeconds")}</p>}
        {found && (found.length === 0 ? (
          <Notice kind="warn">{t("setup.connectionForm.noDeviceFound")}</Notice>
        ) : (
          <div className="choices">
            {found.map((d) => (
              <button key={d.host} className={`choice ${d.host === host ? "active" : ""}`}
                onClick={() => { setHost(d.host); reset(); void test(d.host); }}>
                <InverterIcon size={36} />
                <span>
                  <strong>{d.model ? `${d.manufacturer ?? ""} ${d.model}`.trim() : t("setup.connectionForm.unknownModbusDevice")}</strong>
                  <span className="meta">{d.host}{d.serial ? ` · ${t("setup.connectionForm.serialEnd", { end: d.serial.slice(-4) })}` : ""}</span>
                </span>
              </button>
            ))}
          </div>
        ))}
        {found && found.some((d, i) => d.serial && found.findIndex((x) => x.serial === d.serial) !== i) && (
          <p className="hint">{t("setup.connectionForm.sameSerialHint")}</p>
        )}
      </div>

      <div className="card form">
        <h2>{t("setup.connectionForm.enterAddressManually")}</h2>
        <Field label={t("setup.connectionForm.inverterIp")} locked={hostLocked}>
          <input className="input" value={host} onChange={(e) => { setHost(e.target.value); reset(); }}
            placeholder={t("setup.connectionForm.example", { example: "192.168.178.50" })} inputMode="decimal" disabled={hostLocked} />
        </Field>
        {advanced ? (
          <>
            <Field label={t("setup.connectionForm.deviceType")} hint={t("setup.connectionForm.detectHint")}>
              <select className="input" value={driver} onChange={(e) => { setDriver(e.target.value); reset(); }}>
                {(drivers.length ? drivers : [{ key: "auto", label: t("common.detectAutomatically") }]).map((d) => (
                  <option key={d.key} value={d.key}>{d.label}</option>
                ))}
              </select>
            </Field>
            <div className="field-row">
              <Field label={t("common.port")} hint={t("setup.connectionForm.portHint")}>
                <input className="input" type="number" value={port} onChange={(e) => { setPort(Number(e.target.value)); reset(); }} />
              </Field>
              <Field label={t("setup.connectionForm.deviceAddress")} hint={t("setup.connectionForm.deviceAddressHint")}>
                <input className="input" type="number" value={unit} onChange={(e) => { setUnit(Number(e.target.value)); reset(); }} />
              </Field>
            </div>
          </>
        ) : (
          <button className="link" onClick={() => setAdvanced(true)}>{t("setup.connectionForm.advanced")}</button>
        )}
        <Button variant="secondary" onClick={() => test()} busy={testing} disabled={!host.trim()}>{t("setup.connectionForm.testConnection")}</Button>
        {testing && <p className="hint">{t("setup.connectionForm.detectingDevice")}</p>}

        {result?.ok && result.device && (
          <Notice kind="ok">
            <strong>{t("setup.connectionForm.detectedDevice", { device: `${result.device.manufacturer} ${result.device.model}` })}</strong>
            {result.label && <div>{result.label}</div>}
            {result.device.serial && <div>{t("setup.connectionForm.serialNumber", { serial: result.device.serial })}</div>}
            {result.sample && <div>{t("setup.connectionForm.currentValues", { pv: kw(result.sample.pv_power), soc: percent(result.sample.battery_soc) })}</div>}
            {!result.device.supports_control && <div className="hint">{t("setup.connectionForm.readOnlyDevice")}</div>}
          </Notice>
        )}
        {result && !result.ok && <Notice kind="error">{result.error}</Notice>}
        {error && <Notice kind="error">{error}</Notice>}
      </div>

      <Button onClick={save} busy={saving} disabled={!host.trim() || !result?.ok}>{saveLabel}</Button>
      {!result?.ok && host.trim() && <p className="hint center">{t("setup.connectionForm.testFirst")}</p>}
    </>
  );
}

/** Help for people who have never heard of Modbus: where the address comes from and what to check. */
export function SetupHelp() {
  return (
    <div className="card">
      <details className="help">
        <summary>{t("setup.setupHelp.deviceNotFound")}</summary>
        <ul>
          <li><strong>{t("setup.setupHelp.cableLabel")}</strong>{" "}
            {t("setup.setupHelp.cableHint")}</li>
          <li><strong>Modbus TCP:</strong>{" "}
            {t("setup.setupHelp.modbusHint")}</li>
          <li>{tx("setup.setupHelp.foxessAddress", { model: <strong>FoxESS H3</strong> })}</li>
          <li>{tx("setup.setupHelp.sajAddress", { model: <strong>SAJ H2/HS2</strong> })}</li>
          <li><strong>{t("setup.setupHelp.findIpLabel")}</strong>{" "}
            {t("setup.setupHelp.findIpHint")}</li>
          <li>{tx("setup.setupHelp.otherManager", { lead: <strong>{t("setup.setupHelp.otherManagerLead")}</strong> })}</li>
        </ul>
      </details>
      <details className="help">
        <summary>{t("setup.setupHelp.fixedIpTitle")}</summary>
        <p>{t("setup.setupHelp.fixedIpHint")}</p>
      </details>
    </div>
  );
}

export function Setup({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<"welcome" | "connect" | "history">("welcome");

  if (step === "history") {
    return (
      <div className="page setup">
        <div className="page-head"><h1>{t("setup.setup.importTitle")}</h1></div>
        <div className="card">
          <p>{t("setup.setup.importIntro")}{" "}
            <strong>{t("setup.setup.ideallyNow")}</strong>{" "}
            {t("setup.setup.cloudMayDisappear")}</p>
          <p className="hint">{t("setup.setup.apiKeyHint")}</p>
        </div>
        <Button onClick={() => { navigate("more/data"); onDone(); }}>{t("setup.setup.setUpNow")}</Button>
        <Button variant="secondary" onClick={onDone}>{t("setup.setup.later")}</Button>
      </div>
    );
  }

  return (
    <div className="page setup">
      {step === "welcome" ? (
        <>
          <div className="page-head"><h1>{t("setup.setup.welcome")}</h1></div>
          <div className="card">
            <p>{t("setup.setup.intro")}</p>
            <p>{tx("setup.setup.connectsVia", { protocol: <strong>Modbus TCP</strong> })}</p>
            <p className="hint">{t("setup.setup.whatYouNeed")}</p>
          </div>
          <Notice>{t("setup.setup.readOnlyNotice")}</Notice>
          <Button onClick={() => setStep("connect")}>{t("setup.setup.start")}</Button>
        </>
      ) : (
        <>
          <div className="page-head"><h1>{t("setup.setup.connectInverter")}</h1></div>
          <ConnectionForm initial={{ host: "", port: 502, unit: 0, driver: "auto" }} onSaved={() => setStep("history")} saveLabel={t("setup.setup.saveAndContinue")} />
          <SetupHelp />
        </>
      )}
    </div>
  );
}
