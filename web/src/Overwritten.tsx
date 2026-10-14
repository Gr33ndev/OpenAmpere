// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import type { Status } from "./api";
import { batterySettingName } from "./controlLog";
import { updatedLabel } from "./format";
import { list, t } from "./i18n";
import { SMARTBOX_GUIDE } from "./links";
import { Dialog } from "./ui";

/** The overwrite the owner confirmed last, per browser: a new one (a later time) is shown again. */
const SEEN = "openampere.overwrittenSeen";
const seen = () => { try { return localStorage.getItem(SEEN); } catch { return null; } };
const remember = (v: string) => { try { localStorage.setItem(SEEN, v); } catch { /* private mode */ } };

const settingName = (key: string) => (key === "export_limit_w" ? t("common.exportLimit") : batterySettingName(key));

/** Explains on every page, once per case, that another device (usually the old smartbox, #25) set a change by
 *  OpenAmpere back, and what the owner can do about it. */
export function OverwrittenDialog({ status }: { status: Status }) {
  const overwritten = status.overwritten;
  const key = overwritten ? String(overwritten.ts) : null;
  const [closed, setClosed] = useState<string | null>(seen);
  if (!overwritten || !key || key === closed) return null;
  const confirm = () => {
    remember(key);
    setClosed(key);
  };
  return (
    <Dialog title={t("shell.overwritten.title")} confirm={t("shell.overwritten.understood")}
      cancel={t("shell.overwritten.close")} onConfirm={confirm} onCancel={() => setClosed(key)}>
      <p>{t("shell.overwritten.what", { settings: list(overwritten.settings.map(settingName)) })}</p>
      <p className="hint">{t("shell.overwritten.noticed", { date: updatedLabel(overwritten.ts) })}</p>
      <p>{t("shell.overwritten.cause")}</p>
      {/* the first link gets the focus: here it keeps a long dialog on a phone from opening scrolled down */}
      <p><a href={SMARTBOX_GUIDE()} target="_blank" rel="noopener">{t("shell.overwritten.guide")}</a></p>
      <p><strong>{t("shell.overwritten.fixTitle")}</strong></p>
      <ul>
        <li>{t("shell.overwritten.blockInternet")}</li>
        <li>{t("shell.overwritten.disconnect")}</li>
      </ul>
      <p>{t("shell.overwritten.thenAgain")}</p>
    </Dialog>
  );
}
