import { useEffect, useState } from "react";
import type { Settings } from "./api";
import { postJson, putJson, useResource } from "./api";
import { t, tx } from "./i18n";
import type { PageProps } from "./SettingsPages";
import { Button, Field, LearnMore, LoadState, Notice, SubPage, SwitchRow, toast } from "./ui";

type NotifyKey = "notify.on_unreachable" | "notify.on_alarm" | "notify.on_overwritten" | "notify.on_battery_full"
  | "notify.on_cheap_power" | "notify.on_firmware" | "notify.on_battery_health" | "notify.on_off_grid";

const EVENTS: { key: NotifyKey; label: string; hint: string }[] = [
  { key: "notify.on_unreachable", label: t("settings.events.inverterUnreachable"), hint: t("settings.events.unreachableHint") },
  { key: "notify.on_alarm", label: t("common.faultReported"), hint: t("settings.events.alarmHint") },
  { key: "notify.on_overwritten", label: t("settings.events.settingOverwritten"), hint: t("settings.events.overwrittenHint") },
  { key: "notify.on_battery_full", label: t("settings.events.batteryFull"), hint: t("settings.events.batteryFullHint") },
  { key: "notify.on_cheap_power", label: t("settings.events.cheapPower"), hint: t("settings.events.cheapPowerHint") },
  { key: "notify.on_off_grid", label: t("settings.events.powerOutage"), hint: t("settings.events.offGridHint") },
  { key: "notify.on_battery_health", label: t("settings.events.checkBattery"), hint: t("settings.events.batteryHealthHint") },
  { key: "notify.on_firmware", label: t("settings.events.newFirmware"), hint: t("settings.events.firmwareHint") },
];

function randomTopic(): string {
  const bytes = new Uint8Array(9);
  crypto.getRandomValues(bytes);
  return `openampere-${Array.from(bytes, (b) => b.toString(36).padStart(2, "0")).join("").slice(0, 14)}`;
}

export function NotifyPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<Settings>("/api/settings");
  // the cheapest-hour message needs exchange prices, i.e. a dynamic tariff
  const { data: tariffs } = useResource<{ tariffs: { kind: string }[] }>("/api/tariffs");
  const dynamic = !!tariffs?.tariffs.some((tariff) => tariff.kind === "dynamic");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setUrl(data.values["notify.ntfy_url"]); }, [data]);
  const values = data?.values;
  const locked = (key: string) => data?.locked.includes(key) ?? false;

  const save = async (changes: Record<string, unknown>) => {
    try {
      setData(await putJson<Settings>("/api/settings", { ...changes, _revision: data?.revision }));
      toast(t("common.saved"));
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    }
  };
  const test = async () => {
    setBusy(true);
    try {
      await postJson("/api/notify/test", {});
      toast(t("settings.notifyPage.testMessageSent"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title={t("common.notifications")} onBack={onBack}>
      {!values && <LoadState error={error} onRetry={reload} />}
      <p className="hint">{tx("settings.notifyPage.intro", { app: <strong>ntfy</strong> })}</p>
      <LearnMore summary={t("common.howItWorks")}>
        <p className="hint">{t("settings.notifyPage.howTo")}</p>
      </LearnMore>
      {values && (
        <>
          <div className="card form">
            <Field label={t("settings.notifyPage.ntfyAddress")} locked={locked("notify.ntfy_url")}
              hint={t("settings.notifyPage.addressHint")}>
              <input className="input" value={url} placeholder="https://ntfy.sh/" onChange={(e) => setUrl(e.target.value)} />
            </Field>
            {!url && <button className="link" onClick={() => setUrl(`https://ntfy.sh/${randomTopic()}`)}>{t("settings.notifyPage.suggestRandomTopic")}</button>}
            <Field label={t("settings.notifyPage.token")} hint={data?.secrets["notify.ntfy_token"]?.set ? t("settings.notifyPage.savedKeep") : t("settings.notifyPage.tokenHint")}>
              <input className="input" type="password" value={token} autoComplete="off" onChange={(e) => setToken(e.target.value)} />
            </Field>
            <Button variant="secondary" disabled={url === values["notify.ntfy_url"] && !token}
              onClick={async () => { if (await save({ "notify.ntfy_url": url, ...(token ? { "notify.ntfy_token": token } : {}) })) setToken(""); }}>
              {t("common.save")}
            </Button>
            {values["notify.ntfy_url"] && <Button variant="secondary" busy={busy} onClick={() => void test()}>{t("settings.notifyPage.sendTestMessage")}</Button>}
          </div>
          <div className="section-title">{t("settings.notifyPage.eventsTitle")}</div>
          <div className="card form">
            {EVENTS.map((e) => (
              <SwitchRow key={e.key} label={e.label} hint={e.hint} checked={values[e.key] && !(e.key === "notify.on_cheap_power" && !dynamic)}
                disabled={locked(e.key) || (e.key === "notify.on_cheap_power" && !dynamic)}
                onChange={(v) => void save({ [e.key]: v })} />
            ))}
          </div>
          <Notice kind="info">{t("settings.notifyPage.privacyHint")}</Notice>
        </>
      )}
    </SubPage>
  );
}
