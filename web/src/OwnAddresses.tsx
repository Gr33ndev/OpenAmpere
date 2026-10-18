// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useEffect, useState } from "react";
import { t } from "./i18n";
import { useSettings } from "./SettingsPages";
import { Button, Field, HOST_INPUT, Notice, Unsaved } from "./ui";

const names = (text: string) => text.split(/[\s,]+/).map((name) => name.trim().toLowerCase()).filter(Boolean);

/** Own host names OpenAmpere may be opened with besides the usual ones, e.g. a domain on a reverse proxy (#437). Only
 *  after login: the names are hidden without it, like other private values (#164). */
export function OwnAddressesCard() {
  const { settings, save, locked } = useSettings();
  const stored = settings?.["server.allowed_hosts"];
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (stored) setText(stored.join("\n")); }, [stored]);
  if (!stored) return null;

  const fixed = locked("server.allowed_hosts");
  const changed = names(text).join("\n") !== stored.join("\n");
  const submit = async () => {
    setBusy(true);
    await save({ "server.allowed_hosts": names(text) });
    setBusy(false);
  };

  return (
    <form className="card form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <h2>{t("shell.ownAddresses.title")}</h2>
      <p className="hint">{t("shell.ownAddresses.intro")}</p>
      {stored.includes("*") && <Notice kind="warn">{t("shell.ownAddresses.wildcard")}</Notice>}
      <Field label={t("shell.ownAddresses.label")} hint={t("shell.ownAddresses.hint")} locked={fixed}>
        <textarea className="input" rows={3} value={text} disabled={fixed} placeholder={t("shell.ownAddresses.placeholder")} {...HOST_INPUT}
          onChange={(e) => setText(e.target.value)} />
      </Field>
      <p className="hint">{t("shell.ownAddresses.proxyHint")}</p>
      <Unsaved show={changed} />
      <Button type="submit" busy={busy} disabled={fixed || !changed}>{t("common.save")}</Button>
    </form>
  );
}
