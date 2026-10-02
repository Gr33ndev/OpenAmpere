import { useEffect, useState } from "react";
import { getJson, postJson, putJson } from "./api";
import { kw, percent } from "./format";
import { InverterIcon } from "./icons";
import { Button, Field, Notice } from "./ui";
import { navigate } from "./route";

type Device = { manufacturer: string; model: string; serial: string | null; firmware: string | null;
  driver: string; unit: number | null; supports_control: boolean };
type TestResult = { ok: boolean; error?: string; device?: Device; label?: string;
  sample?: { pv_power: number | null; battery_soc: number | null } | null };
type Found = { host: string; port: number; model: string | null; manufacturer: string | null; driver: string | null; unit: number | null };
type DriverOption = { key: string; label: string };

/** Find / enter the inverter address, detect the device type, test and save.
 *  Used by the setup wizard and the connection settings. */
export function ConnectionForm({ initial, onSaved, saveLabel = "Speichern", locked = [] }: {
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
        <h2>Im Netzwerk suchen</h2>
        <p className="hint">Sucht im Heimnetz nach Wechselrichtern und erkennt Hersteller und Modell automatisch.</p>
        <Field label="Netzwerk" hint="Die ersten drei Zahlen deiner IP-Adressen, z. B. 192.168.178">
          <input className="input" value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="192.168.178" inputMode="decimal" />
        </Field>
        <Button variant="secondary" onClick={scan} busy={scanning} disabled={!prefix}>Netzwerk durchsuchen</Button>
        {scanning && <p className="hint">Das dauert einige Sekunden …</p>}
        {found && (found.length === 0 ? (
          <Notice kind="warn">Kein Gerät gefunden. Prüfe das Netzwerk oder gib die Adresse unten von Hand ein.</Notice>
        ) : (
          <div className="choices">
            {found.map((d) => (
              <button key={d.host} className={`choice ${d.host === host ? "active" : ""}`}
                onClick={() => { setHost(d.host); reset(); void test(d.host); }}>
                <InverterIcon size={36} />
                <span>
                  <strong>{d.model ? `${d.manufacturer ?? ""} ${d.model}`.trim() : "Unbekanntes Modbus-Gerät"}</strong>
                  <span className="meta">{d.host}</span>
                </span>
              </button>
            ))}
          </div>
        ))}
      </div>

      <div className="card form">
        <h2>Adresse von Hand eingeben</h2>
        <Field label="IP-Adresse des Wechselrichters" locked={hostLocked}>
          <input className="input" value={host} onChange={(e) => { setHost(e.target.value); reset(); }}
            placeholder="z. B. 192.168.178.50" inputMode="decimal" disabled={hostLocked} />
        </Field>
        {advanced ? (
          <>
            <Field label="Gerätetyp" hint="„Automatisch erkennen“ funktioniert in fast allen Fällen.">
              <select className="input" value={driver} onChange={(e) => { setDriver(e.target.value); reset(); }}>
                {(drivers.length ? drivers : [{ key: "auto", label: "Automatisch erkennen" }]).map((d) => (
                  <option key={d.key} value={d.key}>{d.label}</option>
                ))}
              </select>
            </Field>
            <div className="field-row">
              <Field label="Port" hint="Standard 502 – bei einem Modbus-Proxy dessen Port">
                <input className="input" type="number" value={port} onChange={(e) => { setPort(Number(e.target.value)); reset(); }} />
              </Field>
              <Field label="Geräteadresse" hint="0 = automatisch">
                <input className="input" type="number" value={unit} onChange={(e) => { setUnit(Number(e.target.value)); reset(); }} />
              </Field>
            </div>
          </>
        ) : (
          <button className="link" onClick={() => setAdvanced(true)}>Erweitert: Gerätetyp, Port, Geräteadresse</button>
        )}
        <Button variant="secondary" onClick={() => test()} busy={testing} disabled={!host.trim()}>Verbindung testen</Button>
        {testing && <p className="hint">Erkenne das Gerät …</p>}

        {result?.ok && result.device && (
          <Notice kind="ok">
            <strong>Erkannt: {result.device.manufacturer} {result.device.model}</strong>
            {result.label && <div>{result.label}</div>}
            {result.device.serial && <div>Seriennummer {result.device.serial}</div>}
            {result.sample && <div>Aktuell: PV {kw(result.sample.pv_power)}, Speicher {percent(result.sample.battery_soc)}</div>}
            {!result.device.supports_control && <div className="hint">Für dieses Gerät zeigt OpenAmpere Werte an; Einstellungen ändern ist noch nicht möglich.</div>}
          </Notice>
        )}
        {result && !result.ok && <Notice kind="error">{result.error}</Notice>}
        {error && <Notice kind="error">{error}</Notice>}
      </div>

      <Button onClick={save} busy={saving} disabled={!host.trim() || !result?.ok}>{saveLabel}</Button>
      {!result?.ok && host.trim() && <p className="hint center">Bitte zuerst die Verbindung testen.</p>}
    </>
  );
}

/** Help for people who have never heard of Modbus: where the address comes from and what to check. */
export function SetupHelp() {
  return (
    <div className="card">
      <details className="help">
        <summary>Gerät wird nicht gefunden?</summary>
        <ul>
          <li><strong>Netzwerkkabel:</strong> Der Wechselrichter braucht eine Verbindung ins Heimnetz, meist per Kabel am
            LAN-Anschluss. Ein reiner Cloud-WLAN-Stick reicht oft nicht.</li>
          <li><strong>Modbus TCP:</strong> Die Schnittstelle muss eingeschaltet sein. Bei vielen Geräten ist sie das ab
            Werk, sonst kann der Installationsbetrieb sie aktivieren. Üblich ist Port 502.</li>
          <li><strong>FoxESS H3</strong> (auch als „Ampere.StoragePro E3“ verkauft): Geräteadresse 247.</li>
          <li><strong>SAJ H2/HS2</strong> (ältere „Ampere.StoragePro“): Geräteadresse 1 oder 2, je nach Kommunikationsmodul.</li>
          <li><strong>IP-Adresse herausfinden:</strong> In der Geräteliste deines Routers (FRITZ!Box: Heimnetz → Netzwerk)
            oder im Menü am Display des Wechselrichters.</li>
          <li><strong>Hängt noch ein anderer Energiemanager</strong> (z. B. die bisherige Smartbox) am Wechselrichter, sind
            evtl. alle Verbindungen belegt. Dann nach dem Einrichten unter Mehr → Verbindung „Pro Abfrage“ wählen.</li>
        </ul>
      </details>
      <details className="help">
        <summary>Tipp: Feste IP-Adresse vergeben</summary>
        <p>Der Router kann dem Wechselrichter irgendwann eine neue Adresse geben. OpenAmpere sucht ihn dann zwar
          automatisch anhand der Seriennummer, zuverlässiger ist aber eine feste Adresse. In der FRITZ!Box: Heimnetz →
          Netzwerk → Gerät bearbeiten → „Diesem Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen“.</p>
      </details>
    </div>
  );
}

export function Setup({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<"welcome" | "connect" | "history">("welcome");

  if (step === "history") {
    return (
      <div className="page setup">
        <div className="page-head"><h1>Bisherigen Verlauf übernehmen?</h1></div>
        <div className="card">
          <p>Hast du deine Anlage bisher mit der App „Ampere.IQ“ genutzt? Dann kannst du deinen Verlauf aus der
            EKD-Cloud übernehmen – <strong>aber nur, solange diese noch läuft.</strong> Danach ist er verloren.</p>
          <p className="hint">Du brauchst dafür den persönlichen API-Schlüssel aus der Ampere.IQ-App. OpenAmpere ist ein
            unabhängiges Projekt ohne Verbindung zu EKD.</p>
        </div>
        <Button onClick={() => { navigate("more/data"); onDone(); }}>Jetzt einrichten</Button>
        <Button variant="secondary" onClick={onDone}>Später oder nicht nötig</Button>
      </div>
    );
  }

  return (
    <div className="page setup">
      {step === "welcome" ? (
        <>
          <div className="page-head"><h1>Willkommen bei OpenAmpere</h1></div>
          <div className="card">
            <p>OpenAmpere zeigt die Daten deiner Solaranlage direkt aus deinem Heimnetz an, ganz ohne Cloud.</p>
            <p>Dafür verbindet sich OpenAmpere über <strong>Modbus TCP</strong> mit deinem Wechselrichter bzw. Speicher. Welches Gerät du hast, wird automatisch erkannt – unterstützt werden derzeit Speicher-Wechselrichter von FoxESS (H3-Serie) und SAJ (H2/HS2).</p>
            <p className="hint">Du brauchst: die IP-Adresse des Geräts – oder wir suchen sie gemeinsam.</p>
          </div>
          <Notice>OpenAmpere liest nur. Einstellungen am Wechselrichter werden erst geändert, wenn du die Steuerung ausdrücklich freigibst.</Notice>
          <Button onClick={() => setStep("connect")}>Los geht's</Button>
        </>
      ) : (
        <>
          <div className="page-head"><h1>Wechselrichter verbinden</h1></div>
          <ConnectionForm initial={{ host: "", port: 502, unit: 0, driver: "auto" }} onSaved={() => setStep("history")} saveLabel="Speichern und weiter" />
          <SetupHelp />
        </>
      )}
    </div>
  );
}
