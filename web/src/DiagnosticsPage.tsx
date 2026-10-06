import { useState } from "react";
import { postJson, useResource } from "./api";
import { updatedLabel } from "./format";
import { t } from "./i18n";
import { ISSUES_URL } from "./links";
import type { PageProps } from "./SettingsPages";
import { Button, Checkbox, copyText, LearnMore, LoadState, Notice, SubPage, toast } from "./ui";

type Check = { id: string; title: string; status: "ok" | "warn" | "error" | "info" | "skipped"; summary: string; hint?: string };
type DiagnosticsData = { running: boolean; markdown: string | null;
  report: { created: number; checks: Check[] } | null };

const STATUS_LABEL: Record<Check["status"], string> = { ok: "OK", warn: t("settings.statusLabel.check"), error: t("settings.statusLabel.error"), info: "Info", skipped: "–" };

export function DiagnosticsPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<DiagnosticsData>("/api/diagnostics");
  const [connectionTest, setConnectionTest] = useState(false);
  const [serial, setSerial] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      setData(await postJson<DiagnosticsData>(`/api/diagnostics?connection_test=${connectionTest}&include_serial=${serial}`, {}));
      toast(t("settings.diagnosticsPage.done"));
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
      toast(t("settings.diagnosticsPage.reportCopied"));
    } else {
      setShowText(true);
      toast(t("settings.diagnosticsPage.copyFailed"), "error");
    }
  };

  return (
    <SubPage title={t("common.diagnostics")} onBack={onBack}>
      <p className="hint">{t("settings.diagnosticsPage.intro")} <strong>{t("settings.diagnosticsPage.readOnly")}</strong></p>
      <LearnMore>
        <p className="hint">{t("settings.diagnosticsPage.explanation")}</p>
      </LearnMore>
      {!data && <LoadState error={error} onRetry={reload} />}
      <div className="card form">
        <Checkbox checked={connectionTest} onChange={setConnectionTest}>
          {t("settings.diagnosticsPage.testConnections")}
        </Checkbox>
        <Checkbox checked={serial} onChange={setSerial}>{t("settings.diagnosticsPage.fullSerial")}</Checkbox>
        <Button busy={busy} onClick={() => void run()}>{t("settings.diagnosticsPage.start")}</Button>
        {busy && <p className="hint">{t("settings.diagnosticsPage.takesHalfMinute")}</p>}
      </div>

      {data?.report && (
        <>
          <div className="section-title">{t("settings.diagnosticsPage.result")}</div>
          <p className="hint">{t("settings.diagnosticsPage.asOf", { date: updatedLabel(data.report.created) })}</p>
          <div className="card">
            <ul className="checks">
              {data.report.checks.map((c) => (
                <li key={c.id} className={`check ${c.status}`}>
                  <span className="badge">{STATUS_LABEL[c.status]}</span>
                  <span><strong>{c.title}</strong><span className="meta">{c.summary}</span>{c.hint && <span className="meta check-hint">{c.hint}</span>}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="card form">
            <p className="hint">{t("settings.diagnosticsPage.shareHint")}</p>
            <Button variant="secondary" onClick={() => void copy()}>{t("settings.diagnosticsPage.copyReport")}</Button>
            <a className="btn secondary" href={`${ISSUES_URL}/new?template=device_report.yml`}
              target="_blank" rel="noreferrer">{t("settings.diagnosticsPage.openIssue")}</a>
            {data.markdown && (
              <details className="advanced" open={showText} onToggle={(e) => setShowText(e.currentTarget.open)}>
                <summary>{t("settings.diagnosticsPage.showReport")}</summary>
                <textarea className="input report-text" readOnly rows={12} value={data.markdown}
                  onFocus={(e) => e.currentTarget.select()} aria-label={t("settings.diagnosticsPage.report")} />
              </details>
            )}
          </div>
        </>
      )}

      <div className="section-title">{t("settings.diagnosticsPage.checkYourself")}</div>
      <div className="card">
        <ul className="plain-list">
          <li><strong>{t("settings.diagnosticsPage.gridOperatorLabel")}</strong>{" "}
            {t("settings.diagnosticsPage.gridOperatorQuestions")}</li>
          <li><strong>{t("settings.diagnosticsPage.controlLabel")}</strong>{" "}
            {t("settings.diagnosticsPage.checkAfterChange")}</li>
          <li><strong>{t("settings.diagnosticsPage.wallboxLabel")}</strong>{" "}
            {t("settings.diagnosticsPage.otherDevicesHint")}</li>
        </ul>
      </div>
      {data?.report?.checks.some((c) => c.status === "warn") && (
        <Notice kind="warn">{t("settings.diagnosticsPage.checkHint")}</Notice>
      )}
    </SubPage>
  );
}
