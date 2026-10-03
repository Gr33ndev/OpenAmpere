import { useState } from "react";
import type { Settings, Status } from "./api";
import { putJson, refreshAll, useResource } from "./api";
import { Checkbox, Dialog, Segmented, toast } from "./ui";

export type ControlMode = "off" | "test" | "live";

export function controlMode(control: Status["control"] | undefined): ControlMode {
  if (!control?.enabled) return "off";
  return control.dry_run ? "test" : "live";
}

const HINT: Record<ControlMode, string> = {
  off: "OpenAmpere zeigt nur an und ändert nichts an deinen Geräten.",
  test: "Du kannst alles einstellen. Änderungen landen nur im Protokoll und werden nicht gesendet.",
  live: "Änderungen werden an Wechselrichter und Geräte gesendet.",
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
      toast(next === "off" ? "Nur ansehen" : next === "test" ? "Testmodus: nichts wird gesendet" : "Steuerung aktiv");
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
      <div className="control-mode-title">Änderungen an Geräten</div>
      <Segmented value={mode} disabled={busy || locked} onChange={choose}
        options={[["off", "Nur ansehen"], ["test", "Testen"], ["live", "Aktiv"]]} />
      <p className="hint">{locked ? "Fest eingestellt (Umgebungsvariable)." : HINT[mode]}</p>
      {confirm && (
        <Dialog title="Steuerung aktivieren?" confirm="Ja, Änderungen senden" danger disabled={!understood}
          onCancel={() => setConfirm(false)} onConfirm={() => { setConfirm(false); void apply("live"); }}>
          <p>Änderungen werden ab jetzt an Wechselrichter und Geräte gesendet. Falsche Einstellungen können dazu führen,
            dass der Speicher nicht wie gewohnt arbeitet.</p>
          <p className="hint">OpenAmpere ist ein kostenloses Gemeinschaftsprojekt ohne Gewähr und ersetzt keinen
            Elektrofachbetrieb. Ungeeignete Einstellungen können den Speicher belasten und Garantie- oder
            Gewährleistungsansprüche (gegenüber Hersteller, Händler oder Insolvenzverwalter) gefährden.
            Notiere die bisherigen Werte, bevor du etwas änderst.</p>
          <Checkbox checked={understood} onChange={setUnderstood}>Ich habe das verstanden und handle auf eigene Verantwortung.</Checkbox>
        </Dialog>
      )}
    </div>
  );
}
