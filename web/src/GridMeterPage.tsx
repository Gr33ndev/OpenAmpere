import { useEffect, useState } from "react";
import { postJson, useResource } from "./api";
import { updatedLabel } from "./format";
import { list, LOCALE, t, tx } from "./i18n";
import type { PageProps } from "./SettingsPages";
import { useSettings } from "./SettingsPages";
import { LoginToSee } from "./AuthScreens";
import { Button, Checkbox, Field, LoadState, Notice, SubPage, toast } from "./ui";

type Provider = { key: string; label: string; portal: string; region: string };
type Meter = { id: string; name: string; kinds: ("import" | "export")[] };
export type GridMeterView = {
  providers: Provider[]; configured: boolean; meters: Meter[]; active: string[];
  synced: number | null; until: string | null; error: string | null; busy: boolean;
};

const MISSING_URL = "https://github.com/Gr33ndev/OpenAmpere/issues/new?template=feature_request.yml&title=Netzbetreiber%3A+";
const KIND = { import: t("common.import"), export: t("common.feedIn") };
const dayLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(LOCALE, { day: "numeric", month: "long" });

/** Settings: fetch the daily values of the grid operator's smart meter from its customer portal (#60). */
export function GridMeterPage({ onBack }: PageProps) {
  const { settings, secrets, save, locked, hidden, error, reload } = useSettings();
  const { data: view, setData: setView, reload: reloadView } = useResource<GridMeterView>("/api/gridmeter");
  const [provider, setProvider] = useState("none");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!settings) return;
    setProvider(settings["meter.provider"]);
    setUsername(settings["meter.username"] ?? "");
  }, [settings]);
  const chosen = view?.providers.find((p) => p.key === provider);
  const passwordSet = secrets?.["meter.password"]?.set ?? false;
  const changed = !!settings && (provider !== settings["meter.provider"] || username !== settings["meter.username"] || !!password);

  const fetchNow = async () => {
    const result = await postJson<GridMeterView>("/api/gridmeter/sync", {});
    setView(result);
    if (result.error) toast(result.error, "error");
    else if (result.until) toast(t("settings.gridMeterPage.completeUntil", { date: dayLabel(result.until) }));
  };

  const submit = async () => {
    setBusy(true);
    try {
      const ok = await save({ "meter.provider": provider, "meter.username": username,
        ...(password ? { "meter.password": password } : {}) });
      setPassword("");
      if (ok && provider !== "none") await fetchNow();
      else reloadView();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const toggleMeter = (id: string, on: boolean) => {
    if (!view) return;
    const next = on ? [...view.active, id] : view.active.filter((m) => m !== id);
    if (!next.length) return toast(t("settings.gridMeterPage.atLeastOneMeter"), "error");
    void save({ "meter.meter_ids": next }).then(() => reloadView());
  };

  return (
    <SubPage title={t("common.meterReadings")} onBack={onBack}>
      <p className="hint">{t("settings.gridMeterPage.intro")}</p>
      <p className="hint">{t("settings.gridMeterPage.unofficialHint")}</p>
      {(!settings || !view) && <LoadState error={error} onRetry={reload} />}
      {view?.configured && view.error && <Notice kind="error">{view.error}</Notice>}
      {view?.configured && !view.error && view.until && (
        <Notice kind="ok">{t("settings.gridMeterPage.completeUntil", { date: dayLabel(view.until) })}{view.synced
          // "abgerufen heute, 16:32": lower-case "Heute"/"Gestern" in the middle of the sentence
          ? ` · ${t("settings.gridMeterPage.fetched", { date: updatedLabel(view.synced).replace(/^\p{Lu}/u, (c) => c.toLowerCase()) })}` : ""}</Notice>
      )}

      {settings && view && hidden("meter.username") && <LoginToSee />}
      {settings && view && !hidden("meter.username") && (
        <div className="card form">
          <Field label={t("common.gridOperator")} locked={locked("meter.provider")}
            hint={chosen ? t("settings.gridMeterPage.regionHint", { region: chosen.region, portal: chosen.portal })
              : t("settings.gridMeterPage.operatorHint")}>
            <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="none">{t("settings.gridMeterPage.none")}</option>
              {view.providers.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </Field>
          {provider !== "none" && <>
            <Field label={t("settings.gridMeterPage.email")} locked={locked("meter.username")}>
              <input className="input" type="email" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
            </Field>
            <Field label={t("common.password")} hint={passwordSet ? t("settings.gridMeterPage.savedKeep") : undefined}>
              <input className="input" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <p className="hint">{chosen
              ? t("settings.gridMeterPage.credentialsHint", { operator: chosen.label })
              : t("settings.gridMeterPage.credentialsHintGeneric")}</p>
          </>}
          <Button busy={busy} disabled={!changed || (provider !== "none" && (!username || (!password && !passwordSet)))}
            onClick={() => void submit()}>{provider === "none" ? t("common.save") : t("settings.gridMeterPage.saveAndFetch")}</Button>
          {view.configured && !changed && (
            <button className="link" disabled={view.busy} onClick={() => void fetchNow().catch((e) => toast((e as Error).message, "error"))}>
              {t("settings.gridMeterPage.fetchNow")}</button>
          )}
        </div>
      )}

      {view?.configured && view.meters.length > 0 && <>
        <div className="section-title">{t("settings.gridMeterPage.meters")}</div>
        <div className="card">
          {view.meters.length > 1 && <p className="hint">{t("settings.gridMeterPage.metersHint")}</p>}
          <ul className="sessions">
            {view.meters.map((m) => (
              <li key={m.id}>
                {view.meters.length > 1
                  ? <Checkbox checked={view.active.includes(m.id)} onChange={(on) => toggleMeter(m.id, on)}>
                      <strong>{m.name}</strong> {m.kinds.length > 1 && <span className="meta">{list(m.kinds.map((k) => KIND[k]))}</span>}</Checkbox>
                  : <span><strong>{m.name}</strong> {m.kinds.length > 1 && <span className="meta">{list(m.kinds.map((k) => KIND[k]))}</span>}</span>}
              </li>
            ))}
          </ul>
        </div>
      </>}

      <p className="hint">{t("settings.gridMeterPage.delayHint")}</p>
      <p className="hint">{tx("settings.gridMeterPage.operatorMissing",
        { link: <a href={MISSING_URL} target="_blank" rel="noreferrer">{t("settings.gridMeterPage.requestOnGithub")}</a> })}{" "}
        {t("settings.gridMeterPage.moreOperatorsHint")}</p>
    </SubPage>
  );
}
