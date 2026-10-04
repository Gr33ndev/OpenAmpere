import { useState } from "react";
import { postJson, useResource } from "./api";
import { updatedLabel } from "./format";
import { ISSUES_URL } from "./links";
import type { PageProps } from "./SettingsPages";
import { Button, Checkbox, copyText, LearnMore, LoadState, Notice, SubPage, toast } from "./ui";

type Check = { id: string; title: string; status: "ok" | "warn" | "error" | "info" | "skipped"; summary: string };
type DiagnosticsData = { running: boolean; markdown: string | null;
  report: { created: number; checks: Check[] } | null };

const STATUS_LABEL: Record<Check["status"], string> = { ok: "OK", warn: "Prüfen", error: "Fehler", info: "Info", skipped: "–" };

export function DiagnosticsPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<DiagnosticsData>("/api/diagnostics");
  const [connectionTest, setConnectionTest] = useState(false);
  const [serial, setSerial] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      setData(await postJson<DiagnosticsData>(`/api/diagnostics?connection_test=${connectionTest}&include_serial=${serial}`, {}));
      toast("Diagnose fertig");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const [showText, setShowText] = useState(false);
  const copy = async () => {
    if (!data?.markdown) return;
    if (await copyText(data.markdown)) {
      toast("Bericht kopiert");
    } else {
      setShowText(true);
      toast("Kopieren hat nicht geklappt. Bitte den Bericht unten markieren und kopieren.", "error");
    }
  };

  return (
    <SubPage title="Diagnose" onBack={onBack}>
      <p className="hint">Prüft, ob OpenAmpere deinen Wechselrichter richtig versteht. <strong>Es wird nur gelesen, nichts geändert.</strong></p>
      <LearnMore>
        <p className="hint">Die Diagnose fragt alle Registerblöcke ab, prüft Skalierungen und Einspeisebegrenzung und wertet aus,
          wie sich das Gerät nachts und beim Zurücksetzen der Tageszähler verhält. Den Bericht kannst du teilen, damit
          weitere Geräte unterstützt werden.</p>
      </LearnMore>
      {!data && <LoadState error={error} onRetry={reload} />}
      <div className="card form">
        <Checkbox checked={connectionTest} onChange={setConnectionTest}>
          Auch testen, wie viele Verbindungen gleichzeitig gehen (kann andere Geräte wie die Smartbox kurz stören)
        </Checkbox>
        <Checkbox checked={serial} onChange={setSerial}>Seriennummer vollständig in den Bericht aufnehmen</Checkbox>
        <Button busy={busy} onClick={() => void run()}>Diagnose starten</Button>
        {busy && <p className="hint">Das dauert etwa eine halbe Minute …</p>}
      </div>

      {data?.report && (
        <>
          <div className="section-title">Ergebnis vom {updatedLabel(data.report.created)}</div>
          <div className="card">
            <ul className="checks">
              {data.report.checks.map((c) => (
                <li key={c.id} className={`check ${c.status}`}>
                  <span className="badge">{STATUS_LABEL[c.status]}</span>
                  <span><strong>{c.title}</strong><span className="meta">{c.summary}</span></span>
                </li>
              ))}
            </ul>
          </div>
          <div className="card form">
            <p className="hint">Hilf anderen mit demselben Gerät: Kopiere den Bericht und füge ihn in ein Issue auf GitHub ein.</p>
            <Button variant="secondary" onClick={() => void copy()}>Bericht kopieren</Button>
            <a className="btn secondary" href={`${ISSUES_URL}/new?template=device_report.yml`}
              target="_blank" rel="noreferrer">Issue auf GitHub öffnen</a>
            {data.markdown && (
              <details className="advanced" open={showText} onToggle={(e) => setShowText(e.currentTarget.open)}>
                <summary>Bericht als Text anzeigen</summary>
                <textarea className="input report-text" readOnly rows={12} value={data.markdown}
                  onFocus={(e) => e.currentTarget.select()} aria-label="Diagnosebericht" />
              </details>
            )}
          </div>
        </>
      )}

      <div className="section-title">Selbst klären</div>
      <div className="card">
        <ul className="plain-list">
          <li><strong>Netzbetreiber:</strong> Welche Einspeisebegrenzung gilt (Inbetriebnahmedatum, kWp, intelligentes
            Messsystem)? Läuft die Steuerung nach § 9 EEG bzw. § 14a EnWG über die Smartbox oder eine Steuerbox?</li>
          <li><strong>Steuerung:</strong> Nach der ersten Änderung im Live-Betrieb in „Meine Anlage“ und im Protokoll
            prüfen, ob der Wert erhalten bleibt.</li>
          <li><strong>Wallbox:</strong> Hersteller und Modell notieren und in einem Issue nennen – danach richtet sich, ob
            und wie OpenAmpere sie einbinden kann.</li>
        </ul>
      </div>
      {data?.report?.checks.some((c) => c.status === "warn") && (
        <Notice kind="warn">Bei Punkten mit „Prüfen“ sind Werte womöglich falsch skaliert oder nicht vorhanden.
          Ein geteilter Bericht hilft, das für dein Gerät zu korrigieren.</Notice>
      )}
    </SubPage>
  );
}
