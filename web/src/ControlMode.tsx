// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import type { Settings, Status } from "./api";
import { putJson, refreshAll, useResource } from "./api";
import { t } from "./i18n";
import { Checkbox, Dialog, Segmented, toast } from "./ui";

export type ControlMode = "off" | "test" | "live";

export function controlMode(control: Status["control"] | undefined): ControlMode {
  if (!control?.enabled) return "off";
  return control.dry_run ? "test" : "live";
}

const HINT: Record<ControlMode, string> = {
  off: t("devices.hint.off"),
  test: t("devices.hint.test"),
  live: t("devices.hint.live"),
};

/** One switch for "may OpenAmpere change things?", shown wherever something can be controlled. */
export function ControlModeBar({ compact = false }: { compact?: boolean }) {
  const { data: status } = useResource<Status>("/api/status", 15_000);
  const { data: settings } = useResource<Settings>("/api/settings");
  const [confirm, setConfirm] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const mode = controlMode(status?.control);
  const locked = settings?.locked.some((k) => k === "control.enabled" || k === "control.dry_run") ?? false;

  const apply = async (next: ControlMode) => {
    setBusy(true);
    try {
      await putJson("/api/settings", { "control.enabled": next !== "off", "control.dry_run": next !== "live",
        _revision: settings?.revision });
      toast(next === "off" ? t("common.viewOnly") : next === "test" ? t("devices.controlModeBar.testModeActive") : t("devices.controlModeBar.controlActive"));
      refreshAll();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const choose = (next: ControlMode) => {
    if (next === mode) return;
    if (next === "live") {
      setUnderstood(false);
      setConfirm(true);
    } else {
      void apply(next);
    }
  };

  if (!status) return null;
  return (
    <div className={`card control-mode ${mode} ${compact ? "compact" : ""}`}>
      <div className="control-mode-title">{t("devices.controlModeBar.title")}</div>
      <Segmented value={mode} disabled={busy || locked} onChange={choose}
        options={[["off", t("common.viewOnly")], ["test", t("common.testing")], ["live", t("common.active")]]} />
      <p className="hint">{locked ? t("devices.controlModeBar.lockedByEnv") : HINT[mode]}</p>
      {confirm && (
        <Dialog title={t("devices.controlModeBar.confirmTitle")} confirm={t("devices.controlModeBar.yesSendChanges")} danger disabled={!understood}
          onCancel={() => setConfirm(false)} onConfirm={() => { setConfirm(false); void apply("live"); }}>
          <p>{t("devices.controlModeBar.liveWarning")}</p>
          <p className="hint">{t("devices.controlModeBar.disclaimer")}</p>
          <Checkbox checked={understood} onChange={setUnderstood}>{t("devices.controlModeBar.acceptRisk")}</Checkbox>
        </Dialog>
      )}
    </div>
  );
}
