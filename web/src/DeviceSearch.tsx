// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import { postJson } from "./api";
import { t } from "./i18n";
import { Button, Notice, toast } from "./ui";

/** Devices that announce Modbus TCP in the home network (mDNS), for "Gerät auslesen" (#324) and the wallbox check
 *  (#325); "Übernehmen" hands address and port to the form around it. */

export type FoundDevice = { service: string; name: string; host: string; port: number | null };

export function DeviceSearch({ label, onUse }: { label: string; onUse: (device: FoundDevice) => void }) {
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<{ available: boolean; devices: FoundDevice[] } | null>(null);
  const search = async () => {
    setBusy(true);
    try {
      setFound(await postJson("/api/diagnostics/search", {}));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Button variant="secondary" busy={busy} disabled={busy} onClick={() => void search()}>{label}</Button>
      {found && !found.available && <Notice kind="info">{t("shell.deviceSearch.unavailable")}</Notice>}
      {found?.available && !found.devices.length && <p className="hint flush">{t("shell.deviceSearch.nothingFound")}</p>}
      {!!found?.devices.length && (
        <ul className="plain-list found-list">
          {found.devices.map((d) => (
            <li key={`${d.host}-${d.name}`}>
              <span><strong>{d.name}</strong> <span className="meta">{d.host}{d.port ? `:${d.port}` : ""}</span></span>
              <button type="button" className="link" onClick={() => onUse(d)}>{t("shell.deviceSearch.use")}</button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
