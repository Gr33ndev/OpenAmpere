import { useEffect, useState } from "react";
import type { Settings } from "./api";
import { postJson, putJson, useResource } from "./api";
import { t, tx } from "./i18n";
import { QrCode } from "./QrCode";
import type { PageProps } from "./SettingsPages";
import { Button, copyText, Field, LearnMore, LoadState, Notice, SubPage, SwitchRow, toast } from "./ui";

type NotifyKey = "notify.on_unreachable" | "notify.on_alarm" | "notify.on_overwritten" | "notify.on_battery_full"
  | "notify.on_cheap_power" | "notify.on_firmware" | "notify.on_battery_health" | "notify.on_off_grid"
  | "notify.on_storage";

const EVENTS: { key: NotifyKey; label: string; hint: string }[] = [
  { key: "notify.on_unreachable", label: t("settings.events.inverterUnreachable"), hint: t("settings.events.unreachableHint") },
  { key: "notify.on_alarm", label: t("common.faultReported"), hint: t("settings.events.alarmHint") },
  { key: "notify.on_overwritten", label: t("settings.events.settingOverwritten"), hint: t("settings.events.overwrittenHint") },
  { key: "notify.on_battery_full", label: t("settings.events.batteryFull"), hint: t("settings.events.batteryFullHint") },
  { key: "notify.on_cheap_power", label: t("settings.events.cheapPower"), hint: t("settings.events.cheapPowerHint") },
  { key: "notify.on_off_grid", label: t("settings.events.powerOutage"), hint: t("settings.events.offGridHint") },
  { key: "notify.on_battery_health", label: t("settings.events.checkBattery"), hint: t("settings.events.batteryHealthHint") },
  { key: "notify.on_firmware", label: t("settings.events.newFirmware"), hint: t("settings.events.firmwareHint") },
  { key: "notify.on_storage", label: t("settings.events.storage"), hint: t("settings.events.storageHint") },
];

// the official store pages, as linked on docs.ntfy.sh/subscribe/phone/
const APP_STORE_URL = "https://apps.apple.com/us/app/ntfy/id1625396347";
const GOOGLE_PLAY_URL = "https://play.google.com/store/apps/details?id=io.heckel.ntfy";

// iPads report themselves as a Mac, but have a touch screen
const PHONE: "ios" | "android" | null = /iPhone|iPad|iPod/.test(navigator.userAgent)
  || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1) ? "ios"
  : /Android/.test(navigator.userAgent) ? "android" : null;

// no 0/o, 1/l/i: people type the topic from the screen, these look alike
const TOPIC_CHARS = "abcdefghjkmnpqrstuvwxyz23456789";

function randomTopic(): string {
  let topic = "";
  while (topic.length < 14) {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    // 248 = 8 × 31: dropping larger values keeps every character equally likely
    for (const b of bytes) if (b < 248 && topic.length < 14) topic += TOPIC_CHARS[b % TOPIC_CHARS.length];
  }
  return `openampere-${topic}`;
}

/** Server and topic of a saved address like https://ntfy.sh/topic, null if it does not have that form. */
function parseTopic(url: string) {
  try {
    const parsed = new URL(url);
    const topic = parsed.pathname.replace(/^\/+|\/+$/g, "");
    if (!/^https?:$/.test(parsed.protocol) || !topic || topic.includes("/")) return null;
    const secure = parsed.protocol === "https:";
    return {
      topic, host: parsed.host, web: `${parsed.origin}/${topic}`,
      // opens the Android app and subscribes (docs.ntfy.sh/subscribe/phone/#ntfy-links); the iPhone app has no such link
      app: `ntfy://${parsed.host}/${topic}?display=OpenAmpere${secure ? "" : "&secure=false"}`,
    };
  } catch {
    return null;
  }
}

export function NotifyPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<Settings>("/api/settings");
  // the cheapest-hour message needs exchange prices, i.e. a dynamic tariff
  const { data: tariffs } = useResource<{ tariffs: { kind: string }[] }>("/api/tariffs");
  const dynamic = !!tariffs?.tariffs.some((tariff) => tariff.kind === "dynamic");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [sent, setSent] = useState(false);
  const [arrived, setArrived] = useState<boolean | null>(null);
  useEffect(() => { if (data) setUrl(data.values["notify.ntfy_url"]); }, [data]);
  const values = data?.values;
  const locked = (key: string) => data?.locked.includes(key) ?? false;
  const saved = values?.["notify.ntfy_url"] ?? "";
  const target = parseTopic(saved);

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
  const suggest = async () => {
    setSuggesting(true);
    // saved right away: a suggested topic that was never saved is a common reason why nothing arrives
    await save({ "notify.ntfy_url": `https://ntfy.sh/${randomTopic()}` });
    setSuggesting(false);
  };
  const saveAdvanced = async () => {
    if (await save({ "notify.ntfy_url": url, ...(token ? { "notify.ntfy_token": token } : {}) })) {
      setToken("");
      setSent(false);
      setArrived(null);
    }
  };
  const test = async () => {
    setBusy(true);
    try {
      await postJson("/api/notify/test", {});
      toast(t("settings.notifyPage.testMessageSent"));
      setSent(true);
      setArrived(null);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const copy = async (text: string) => { if (await copyText(text)) toast(t("common.copied")); };

  const qr = target && (
    <div className="qr-box">
      <QrCode text={target.web} label={t("settings.notifyPage.qrLabel")} />
      <p className="hint center">{t("settings.notifyPage.qrHint")}</p>
    </div>
  );

  return (
    <SubPage title={t("common.notifications")} onBack={onBack}>
      {!values && <LoadState error={error} onRetry={reload} />}
      <p className="hint">{tx("settings.notifyPage.intro", { app: <strong>ntfy</strong> })}</p>
      {values && (
        <>
          <div className="section-title">{t("settings.notifyPage.installTitle")}</div>
          <div className="card form">
            <p className="flush">{tx("settings.notifyPage.installText", { app: <strong>ntfy</strong> })}</p>
            <a className={`btn ${PHONE === "android" ? "secondary" : "primary"}`} href={APP_STORE_URL} target="_blank"
              rel="noopener noreferrer">{t("settings.notifyPage.appStore")}</a>
            <a className={`btn ${PHONE === "android" ? "primary" : "secondary"}`} href={GOOGLE_PLAY_URL} target="_blank"
              rel="noopener noreferrer">{t("settings.notifyPage.googlePlay")}</a>
          </div>

          <div className="section-title">{t("settings.notifyPage.subscribeTitle")}</div>
          <div className="card form">
            {!saved && (
              <>
                <p className="flush">{t("settings.notifyPage.topicExplain")}</p>
                <Button busy={suggesting} disabled={locked("notify.ntfy_url")} onClick={() => void suggest()}>
                  {t("settings.notifyPage.suggestTopic")}
                </Button>
                <p className="hint flush">{tx("settings.notifyPage.ownTopicHint", { advanced: <strong>{t("common.advanced")}</strong> })}</p>
              </>
            )}
            {saved && !target && (
              <Notice kind="warn">{tx("settings.notifyPage.invalidAddress", { advanced: <strong>{t("common.advanced")}</strong> })}</Notice>
            )}
            {target && (
              <>
                <p className="flush">{t("settings.notifyPage.yourTopic")}</p>
                <div className="notify-topic">{target.topic}</div>
                {target.host !== "ntfy.sh" && <p className="hint flush center">{t("settings.notifyPage.onServer", { server: target.host })}</p>}
                {PHONE === "ios" && (
                  <>
                    <p className="flush">{tx("settings.notifyPage.iosSteps", { copy: <strong>{t("settings.notifyPage.copyTopic")}</strong> })}</p>
                    <Button onClick={() => void copy(target.topic)}>{t("settings.notifyPage.copyTopic")}</Button>
                  </>
                )}
                {PHONE === "android" && (
                  <>
                    <p className="flush">{t("settings.notifyPage.androidSteps")}</p>
                    <a className="btn primary" href={target.app}>{t("settings.notifyPage.openInNtfy")}</a>
                  </>
                )}
                {!PHONE && (
                  <>
                    <p className="flush">{t("settings.notifyPage.computerSteps")}</p>
                    {qr}
                    <a className="btn secondary" href={target.web} target="_blank" rel="noopener noreferrer">
                      {t("settings.notifyPage.openInNtfy")}
                    </a>
                  </>
                )}
                <Button variant="secondary" onClick={() => void copy(target.web)}>{t("settings.notifyPage.copyLink")}</Button>
                {PHONE && <LearnMore summary={t("settings.notifyPage.qrOtherPhone")}>{qr}</LearnMore>}
              </>
            )}
          </div>

          <div className="section-title">{t("settings.notifyPage.testTitle")}</div>
          <div className="card form">
            <p className="flush">{target ? t("settings.notifyPage.testText") : t("settings.notifyPage.testFirst")}</p>
            <Button variant={sent ? "secondary" : "primary"} busy={busy} disabled={!target} onClick={() => void test()}>
              {t("settings.notifyPage.sendTestMessage")}
            </Button>
            {sent && arrived === null && (
              <>
                <p className="flush center"><strong>{t("settings.notifyPage.arrivedQuestion")}</strong></p>
                <div className="button-row inline-buttons">
                  <Button variant="secondary" onClick={() => setArrived(true)}>{t("settings.notifyPage.arrivedYes")}</Button>
                  <Button variant="secondary" onClick={() => setArrived(false)}>{t("settings.notifyPage.arrivedNo")}</Button>
                </div>
              </>
            )}
            {arrived === true && <Notice kind="ok">{t("settings.notifyPage.allSet")}</Notice>}
            {arrived === false && target && (
              <Notice kind="warn">
                <p className="flush"><strong>{t("settings.notifyPage.helpTitle")}</strong></p>
                <ol className="setup-steps notify-help">
                  <li>{tx("settings.notifyPage.helpTopic", { topic: <strong className="notify-topic-inline">{target.topic}</strong> })}</li>
                  <li>{t("settings.notifyPage.helpAllow")}</li>
                  <li>{t("settings.notifyPage.helpFocus")}</li>
                  <li>{t("settings.notifyPage.helpBattery")}</li>
                  <li>{tx("settings.notifyPage.helpOwnServer", { advanced: <strong>{t("common.advanced")}</strong> })}</li>
                </ol>
                <p className="flush">{t("settings.notifyPage.helpRetry")}</p>
              </Notice>
            )}
          </div>

          <div className="card notify-advanced-card">
            <LearnMore summary={t("common.advanced")}>
              <div className="notify-advanced">
                <p className="hint flush">{t("settings.notifyPage.advancedHint")}</p>
                <Field label={t("settings.notifyPage.ntfyAddress")} locked={locked("notify.ntfy_url")}
                  hint={t("settings.notifyPage.addressHint")}>
                  <input className="input" value={url} placeholder="https://ntfy.sh/" onChange={(e) => setUrl(e.target.value)} />
                </Field>
                <Field label={t("settings.notifyPage.token")} hint={data?.secrets["notify.ntfy_token"]?.set ? t("settings.notifyPage.savedKeep") : t("settings.notifyPage.tokenHint")}>
                  <input className="input" type="password" value={token} autoComplete="off" onChange={(e) => setToken(e.target.value)} />
                </Field>
                <Button variant="secondary" disabled={url === saved && !token} onClick={() => void saveAdvanced()}>
                  {t("common.save")}
                </Button>
              </div>
            </LearnMore>
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
