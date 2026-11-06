// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import type { Settings } from "./api";
import { postJson, useOnServerChange, useResource } from "./api";
import { t } from "./i18n";
import { LoginToSee } from "./AuthScreens";
import { Button, Field, HOST_INPUT, LearnMore, Notice, Unsaved, toast } from "./ui";

export type MailStatus = { mail: { error: string | null; since: number | null; queued: number } };

type Security = "starttls" | "tls";
type Form = { host: string; port: number; security: Security; user: string; from: string; to: string };

// the usual port of each kind of encryption: switching the one switches the other, unless it was changed by hand
const PORTS: Record<Security, number> = { starttls: 587, tls: 465 };

const lines = (text: string) => text.split(/[\s,;]+/).map((line) => line.trim()).filter(Boolean);

function formOf(values: Settings["values"]): Form {
  return {
    host: values["notify.mail_host"], port: values["notify.mail_port"], security: values["notify.mail_security"],
    user: values["notify.mail_user"] ?? "", from: values["notify.mail_from"] ?? "",
    to: (values["notify.mail_to"] ?? []).join("\n"),
  };
}

/** Notifications by e-mail through the owner's own mail server (#594), next to ntfy. The password is never sent
 *  back; the addresses are shown only after login (#164). */
export function NotifyMail({ settings, save }: {
  settings: Settings; save: (changes: Record<string, unknown>) => Promise<boolean>;
}) {
  const values = settings.values;
  const hidden = settings.hidden.includes("notify.mail_user"); // not logged in (#164)
  // only after login, like the mail settings themselves
  const { data: status, reload: reloadStatus } = useResource<MailStatus>(hidden ? null : "/api/notify/status", 60_000);
  const [form, setForm] = useState<Form>(() => formOf(values));
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "off" | null>(null);
  const [sent, setSent] = useState(false);
  useOnServerChange(hidden ? null : formOf(values), setForm);
  if (hidden) return <LoginToSee />;

  const locked = (key: string) => settings.locked.includes(key);
  const passwordSet = settings.secrets["notify.mail_password"]?.set ?? false;
  const saved = formOf(values);
  const changed = JSON.stringify({ ...form, to: lines(form.to) }) !== JSON.stringify({ ...saved, to: lines(saved.to) })
    || password !== "";
  const configured = !!values["notify.mail_host"];
  const update = (changes: Partial<Form>) => { setForm({ ...form, ...changes }); setSent(false); };
  const setSecurity = (security: Security) =>
    update({ security, port: form.port === PORTS[form.security] ? PORTS[security] : form.port });

  const store = async () => {
    const ok = await save({
      "notify.mail_host": form.host.trim(), "notify.mail_port": form.port, "notify.mail_security": form.security,
      "notify.mail_user": form.user.trim(), "notify.mail_from": form.from.trim(), "notify.mail_to": lines(form.to),
      ...(password ? { "notify.mail_password": password } : {}),
    });
    if (ok) setPassword("");
    return ok;
  };
  const submit = async () => {
    setBusy("save");
    await store();
    setBusy(null);
  };
  // saves what is in the form first: the test uses the saved settings
  const test = async () => {
    setBusy("test");
    try {
      if (changed && !(await store())) return;
      await postJson("/api/notify/mail/test", {});
      toast(t("settings.notifyMail.testSent"));
      setSent(true);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
      reloadStatus();
    }
  };
  const switchOff = async () => {
    setBusy("off");
    // without a server no e-mail goes out; the password goes with the server (it belongs to it)
    await save({ "notify.mail_host": "" });
    setBusy(null);
    reloadStatus();
  };

  const mail = status?.mail;
  return (
    <>
      <div className="section-title">{t("settings.notifyMail.title")}</div>
      <form className="card form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <p className="flush">{t("settings.notifyMail.intro")}</p>
        {configured && mail?.error && (
          <Notice kind="warn">
            <p className="flush"><strong>{t("settings.notifyMail.failed")}</strong> {mail.error}</p>
            {mail.queued > 0 && <p className="flush">{t("settings.notifyMail.queued", { count: mail.queued })}</p>}
          </Notice>
        )}
        <Field label={t("settings.notifyMail.server")} hint={t("settings.notifyMail.serverHint")} locked={locked("notify.mail_host")}>
          <input className="input" value={form.host} placeholder="smtp.example.com" disabled={locked("notify.mail_host")}
            {...HOST_INPUT} onChange={(e) => update({ host: e.target.value })} />
        </Field>
        <Field label={t("settings.notifyMail.security")} locked={locked("notify.mail_security")}>
          <select className="input" value={form.security} disabled={locked("notify.mail_security")}
            onChange={(e) => setSecurity(e.target.value as Security)}>
            <option value="starttls">{t("settings.notifyMail.starttls")}</option>
            <option value="tls">{t("settings.notifyMail.tls")}</option>
          </select>
        </Field>
        <Field label={t("settings.notifyMail.port")} locked={locked("notify.mail_port")}>
          <input className="input" type="number" min={1} max={65535} value={form.port} disabled={locked("notify.mail_port")}
            onChange={(e) => update({ port: Number(e.target.value) })} />
        </Field>
        <Field label={t("settings.notifyMail.user")} hint={t("settings.notifyMail.userHint")} locked={locked("notify.mail_user")}>
          <input className="input" value={form.user} disabled={locked("notify.mail_user")} {...HOST_INPUT}
            autoComplete="username" onChange={(e) => update({ user: e.target.value })} />
        </Field>
        <Field label={t("settings.notifyMail.password")} locked={locked("notify.mail_password")}
          hint={passwordSet ? t("settings.notifyPage.savedKeep") : t("settings.notifyMail.passwordHint")}>
          <input className="input" type="password" value={password} autoComplete="new-password"
            disabled={locked("notify.mail_password")} onChange={(e) => { setPassword(e.target.value); setSent(false); }} />
        </Field>
        <Field label={t("settings.notifyMail.recipients")} hint={t("settings.notifyMail.recipientsHint")} locked={locked("notify.mail_to")}>
          <textarea className="input" rows={2} value={form.to} disabled={locked("notify.mail_to")} {...HOST_INPUT}
            onChange={(e) => update({ to: e.target.value })} />
        </Field>
        <LearnMore summary={t("settings.notifyMail.senderTitle")}>
          <Field label={t("settings.notifyMail.sender")} hint={t("settings.notifyMail.senderHint")} locked={locked("notify.mail_from")}>
            <input className="input" value={form.from} disabled={locked("notify.mail_from")} {...HOST_INPUT}
              onChange={(e) => update({ from: e.target.value })} />
          </Field>
        </LearnMore>
        <Unsaved show={changed} />
        <Button type="submit" variant="secondary" busy={busy === "save"} disabled={!changed || busy !== null}>
          {t("common.save")}
        </Button>
        <Button variant={sent ? "secondary" : "primary"} busy={busy === "test"} disabled={!form.host.trim() || busy !== null}
          onClick={() => void test()}>
          {t("settings.notifyMail.sendTest")}
        </Button>
        {sent && <Notice kind="ok">{t("settings.notifyMail.testHint")}</Notice>}
        {configured && (
          <Button variant="secondary" busy={busy === "off"} disabled={locked("notify.mail_host") || busy !== null}
            onClick={() => void switchOff()}>
            {t("settings.notifyMail.switchOff")}
          </Button>
        )}
      </form>
    </>
  );
}
