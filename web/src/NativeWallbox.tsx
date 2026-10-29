// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import { postJson, putJson, useResource } from "./api";
import { DeviceSearch } from "./DeviceSearch";
import { kw, num, timeZone } from "./format";
import { LOCALE, t } from "./i18n";
import { WALLBOX_DIRECT_GUIDE } from "./links";
import { navigate } from "./route";
import { Button, Dialog, Field, HOST_INPUT, hostText, LoadState, Notice, Segmented, Switch, toast } from "./ui";
import { useCheckModels, WallboxCheck } from "./WallboxCheck";

/** Wallboxes OpenAmpere reads and controls itself, without evcc: the portable dé wallbox over Tuya, and the FoxESS
 *  AC EV Charger over Modbus TCP, which OpenAmpere only shows (#439: nothing written); its charging modes are only a
 *  simulation (#440), shown with what OpenAmpere would do next to what the charger really does. A portable
 *  wallbox is often unplugged: it is then shown as away, without old values, until it answers again. A wallbox whose
 *  stored key cannot be read (a backup restored on another server, #391) waits for a new key. */

export type WallboxMode = "manual" | "off" | "pv" | "minpv" | "now";
export type WallboxReading = {
  state: "idle" | "plugged_in" | "charging" | "waiting" | "paused" | "finished" | "fault" | "unknown";
  connected: boolean; charging: boolean; power_w: number; voltage_v: (number | null)[]; current_a: (number | null)[];
  installed_phases: number; active_phases: number; session_kwh: number | null; session_s: number | null;
  temperature_c: number | null; set_current_a: number | null; max_current_a: number | null; steps_a: number[];
  firmware: string | null; cp_v: number | null;
  /** FoxESS: how it starts a charge by itself; null when the wallbox does not tell. */
  work_mode?: "controlled" | "plug_and_charge" | "locked" | null; phase_switching?: boolean | null;
  /** FoxESS: the shortest time between two phase switches in minutes. */
  phase_switch_min?: number | null;
};
export type NativeWallbox = {
  id: string; name: string; model: string; label: string; host: string; device_id: string | null;
  local_key_set: boolean; max_current_a: number; mode: WallboxMode; enabled: boolean;
  mode_current_a: Partial<Record<WallboxMode, number>>; stop_after_min: number;
  online: boolean | null; offline_since: number | null; updated: number | null; error: string | null;
  reading: WallboxReading | null; controlled: boolean;
  /** Modbus models: port and unit id; null for Tuya. */
  port?: number | null; unit?: number | null;
  /** Only shown (#439): nothing is written to it. */
  read_only?: boolean;
  /** Its modes other than "manual" only simulate the control (#440); `simulation`: what it would do now. */
  simulated?: boolean; simulation?: WallboxSimulation | null;
  /** The work modes OpenAmpere can set on the wallbox itself (#464), e.g. to unlock a FoxESS charger. */
  work_modes?: WorkMode[];
  /** FoxESS: real control can be switched on by the owner (#465); `control`: it is on. */
  controllable?: boolean; control?: boolean;
  /** Real control held back: the charger did not take the fallback current of 6 A (#465). */
  not_prepared?: boolean;
  /** No current step of the wallbox is at or below the owner's highest current: not charged (#481). */
  above_limit?: { lowest_a: number; limit_a: number; source: "app" | "mode" | "wallbox" } | null;
};
type WorkMode = "controlled" | "plug_and_charge" | "locked";
export type WallboxSimulation = {
  state: "charging" | "waiting" | "no_surplus" | "above_limit" | "off" | "finished" | "no_car" | "unknown";
  current_a: number | null; phases: number | null; power_w: number;
};
export type WallboxModel = { key: string; label: string; protocol: "tuya" | "modbus"; read_only?: boolean;
  simulated?: boolean; modes?: WallboxMode[]; sold_by?: string[]; port?: number; unit?: number };
export type WallboxesView = { wallboxes: NativeWallbox[]; models: WallboxModel[]; max: number; search: boolean };
type Found = { device_id: string; host: string; product_key: string | null };

const MODES: [WallboxMode, string][] = [["manual", t("devices.nativeWallbox.modeManual")], ["off", t("common.off")],
  ["pv", t("devices.modes.solar")], ["minpv", t("devices.modes.minSolar")], ["now", t("devices.modes.fast")]];
function modeHint(wb: NativeWallbox): string {
  if (wb.simulated) return simulationHint(wb);
  switch (wb.mode) {
    case "manual": return t("devices.nativeWallbox.hintManual");
    case "off": return t("devices.modeHint.off");
    case "pv": return t("devices.nativeWallbox.hintPv", { count: wb.stop_after_min });
    case "minpv": return t("devices.nativeWallbox.hintMinpv");
    case "now": return t("devices.nativeWallbox.hintNow");
  }
}

/** A simulated control (#440): what OpenAmpere would do in this mode, never written. */
function simulationHint(wb: NativeWallbox): string {
  switch (wb.mode) {
    case "manual": return t("devices.nativeWallbox.simHintManual");
    case "off": return t("devices.nativeWallbox.simHintOff");
    case "pv": return t("devices.nativeWallbox.simHintPv", { count: wb.stop_after_min });
    case "minpv": return t("devices.nativeWallbox.simHintMinpv");
    case "now": return t("devices.nativeWallbox.simHintNow");
  }
}

/** What the simulated control would do now, as a sentence. */
function planText(plan: WallboxSimulation): string {
  switch (plan.state) {
    case "charging": return t("devices.nativeWallbox.planCharging", { count: plan.phases ?? 0, current: plan.current_a ?? 0,
      power: kw(plan.power_w) });
    case "waiting": return t("devices.nativeWallbox.planWaiting");
    case "no_surplus": return t("devices.nativeWallbox.planNoSurplus");
    case "above_limit": return t("devices.nativeWallbox.planAboveLimit");
    case "off": return t("devices.nativeWallbox.planOff");
    case "finished": return t("devices.nativeWallbox.planFinished");
    case "no_car": return t("devices.nativeWallbox.planNoCar");
    default: return t("devices.nativeWallbox.planUnknown");
  }
}

// what the owner has to change depends on where the highest current comes from (#481)
function aboveLimitText(above: NonNullable<NativeWallbox["above_limit"]>): string {
  const values = { lowest: above.lowest_a, limit: above.limit_a };
  if (above.source === "wallbox") return t("devices.nativeWallbox.aboveLimitWallbox", values);
  if (above.source === "mode") return t("devices.nativeWallbox.aboveLimitMode", values);
  return t("devices.nativeWallbox.aboveLimit", values);
}

/** Steps a wallbox may offer, when it has not told its own yet (e.g. unplugged). */
const STEPS = [6, 8, 10, 13, 16, 20, 25, 32];

/** Currents to choose for a mode: the wallbox's steps up to the owner's highest current, the chosen one included. */
function currentOptions(wb: NativeWallbox, chosen: number): [string, string][] {
  const own = wb.reading?.steps_a ?? [];
  // a wallbox that takes any whole ampere (the FoxESS charger: 6 to 32 A) gets the usual steps within its limits
  const steps = !own.length ? STEPS : own.length > STEPS.length
    ? STEPS.filter((s) => s >= own[0] && s <= own[own.length - 1]) : own;
  const shown = [...new Set([...steps.filter((s) => s <= wb.max_current_a), chosen])].sort((a, b) => a - b);
  return shown.map((s) => [String(s), t("devices.nativeWallbox.amps", { current: s })]);
}

/** When a wallbox went away: the weekday and time within a week, the date before (it may be unplugged for long). */
function since(ts: number): string {
  const options: Intl.DateTimeFormatOptions = Date.now() / 1000 - ts < 6 * 86_400
    ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "numeric", year: "numeric" };
  return new Date(ts * 1000).toLocaleString(LOCALE, { ...options, timeZone: timeZone() });
}

function status(wb: NativeWallbox): string {
  const r = wb.reading;
  if (!wb.local_key_set) return t("devices.nativeWallbox.keyLost");
  if (wb.online === false) {
    return wb.offline_since ? t("devices.nativeWallbox.awaySince", { time: since(wb.offline_since) }) : t("devices.nativeWallbox.away");
  }
  if (!r) return t("devices.nativeWallbox.asking");
  if (r.charging) return t("devices.status.charging", { power: kw(r.power_w) });
  if (r.state === "fault") return t("devices.nativeWallbox.fault");
  if (r.state === "finished") return t("devices.nativeWallbox.finished");
  if (r.work_mode === "locked") return t("devices.nativeWallbox.locked");
  if (!r.connected) return t("devices.status.noVehiclePlugged");
  if (wb.mode === "pv" && !wb.simulated) return t("devices.status.waitingSolarSurplus");
  return t("devices.status.plugged");
}

const amps = (values: (number | null)[]) => values.filter((v): v is number => v != null).map((v) => num(v, 1)).join(" / ");

const WORK_MODE: Record<string, string> = {
  controlled: t("devices.wallboxDirect.workModeControlled"), plug_and_charge: t("devices.wallboxDirect.workModePlugAndCharge"),
  locked: t("devices.wallboxDirect.workModeLocked"),
};

/** What a wallbox OpenAmpere only reads tells about itself: its limit, how it starts and whether it switches phases. */
function OwnSettings({ r }: { r: WallboxReading }) {
  return (
    <>
      {r.max_current_a != null && <><dt>{t("devices.nativeWallbox.maxOfWallbox")}</dt>
        <dd>{t("devices.nativeWallbox.amps", { current: r.max_current_a })}</dd></>}
      {r.work_mode && <><dt>{t("devices.wallboxDirect.workMode")}</dt><dd>{WORK_MODE[r.work_mode] ?? r.work_mode}</dd></>}
      {r.phase_switching != null && <><dt>{t("devices.wallboxDirect.phaseSwitching")}</dt>
        <dd>{r.phase_switching ? t("common.on") : t("common.off")}</dd></>}
    </>
  );
}

/** Real control of a FoxESS charger (#465): switched on after a question that says what happens if OpenAmpere fails,
 *  switched off back to the simulation. */
function RealControl({ wb, onChange }: { wb: NativeWallbox; onChange: (view: WallboxesView) => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!wb.controllable) return null;
  const set = async (on: boolean) => {
    setBusy(true);
    try {
      onChange(await postJson<WallboxesView>(`/api/wallboxes/${wb.id}/control`, { on }));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
      setAsking(false);
    }
  };
  const mode = wb.online ? wb.reading?.work_mode : null;
  if (wb.control) {
    return (
      <>
        <Notice kind="warn">{t("devices.nativeWallbox.realControlOn")}</Notice>
        {mode && mode !== "controlled" && <Notice kind="error">{t("devices.nativeWallbox.realControlNeedsControlled")}</Notice>}
        {wb.not_prepared && <Notice kind="error">{t("devices.nativeWallbox.realControlNotPrepared")}</Notice>}
        <Button variant="secondary" disabled={busy} onClick={() => void set(false)}>{t("devices.nativeWallbox.realControlOff")}</Button>
      </>
    );
  }
  return (
    <>
      <Button variant="secondary" disabled={busy} onClick={() => setAsking(true)}>{t("devices.nativeWallbox.realControlStart")}</Button>
      {asking && (
        <Dialog title={t("devices.nativeWallbox.realControlQuestion")} confirm={t("devices.nativeWallbox.realControlConfirm")}
          disabled={busy} onCancel={() => setAsking(false)} onConfirm={() => void set(true)}>
          <p>{t("devices.nativeWallbox.realControlExperimental")}</p>
          <p>{t("devices.nativeWallbox.realControlFallback")}</p>
          <p>{t("devices.nativeWallbox.realControlNeeds")}</p>
        </Dialog>
      )}
    </>
  );
}

/** The work mode of the wallbox itself (#464): a stored setting, written once after the owner confirms. */
function WorkModeSetting({ wb, onChange }: { wb: NativeWallbox; onChange: (view: WallboxesView) => void }) {
  const [asked, setAsked] = useState<WorkMode | null>(null);
  const [busy, setBusy] = useState(false);
  const current = wb.online ? wb.reading?.work_mode : null;
  if (!wb.work_modes?.length || !current) return null;
  const write = async (mode: WorkMode) => {
    setBusy(true);
    try {
      const result = await postJson<WallboxesView & { dry_run: boolean }>(`/api/wallboxes/${wb.id}/work-mode`, { mode });
      onChange(result);
      toast(result.dry_run ? t("devices.nativeWallbox.workModeTestMode") : t("devices.nativeWallbox.workModeSaved"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
      setAsked(null);
    }
  };
  return (
    <fieldset className="field field-group">
      <legend className="field-label">{t("devices.nativeWallbox.workModeTitle")}</legend>
      <Segmented value={current} disabled={busy} onChange={(m) => setAsked(m as WorkMode)}
        options={wb.work_modes.map((m) => [m, WORK_MODE_NAME[m]] as [string, string])} />
      <p className="hint">{current === "locked" ? t("devices.nativeWallbox.workModeLockedHint") : t("devices.nativeWallbox.workModeHint")}</p>
      {asked && (
        <Dialog title={t("devices.nativeWallbox.workModeQuestion", { mode: WORK_MODE_NAME[asked] })}
          confirm={t("devices.nativeWallbox.workModeConfirm")} disabled={busy}
          onCancel={() => setAsked(null)} onConfirm={() => void write(asked)}>
          <p>{WORK_MODE_EXPLAIN[asked]}</p>
          <p>{t("devices.nativeWallbox.workModeSmartbox")}</p>
        </Dialog>
      )}
    </fieldset>
  );
}

const WORK_MODE_NAME: Record<WorkMode, string> = {
  controlled: t("devices.nativeWallbox.workModeNameControlled"),
  plug_and_charge: t("devices.nativeWallbox.workModeNamePlugAndCharge"),
  locked: t("devices.nativeWallbox.workModeNameLocked"),
};
const WORK_MODE_EXPLAIN: Record<WorkMode, string> = {
  controlled: t("devices.nativeWallbox.workModeExplainControlled"),
  plug_and_charge: t("devices.nativeWallbox.workModeExplainPlugAndCharge"),
  locked: t("devices.nativeWallbox.workModeExplainLocked"),
};

/** A wallbox OpenAmpere only shows (#439): what it does by itself, no charging mode. */
function ReadOnlyCard({ wb, onChange }: { wb: NativeWallbox; onChange: (view: WallboxesView) => void }) {
  const r = wb.online ? wb.reading : null; // no old values of a wallbox that is away
  return (
    <div className="card wallbox">
      <div className="wallbox-head">
        <div>
          <strong>{wb.name}</strong>
          <div className="hint">{status(wb)}</div>
        </div>
      </div>
      {wb.online === false && <p className="hint">{t("devices.nativeWallbox.awayHintModbus")}</p>}
      <p className="hint">{t("devices.nativeWallbox.hintReadOnly")}</p>
      {r && (
        <dl className="facts">
          {r.charging && <><dt>{t("devices.nativeWallbox.phaseCurrents")}</dt><dd>{amps(r.current_a)} A</dd></>}
          <dt>{t("devices.nativeWallbox.phases")}</dt><dd>{r.active_phases || r.installed_phases}</dd>
          <OwnSettings r={r} />
        </dl>
      )}
      <WorkModeSetting wb={wb} onChange={onChange} />
    </div>
  );
}

/** One wallbox on the devices tab: what it does now and the charging mode (for the FoxESS charger a simulation). */
export function NativeWallboxCard({ wb, onChange }: { wb: NativeWallbox; onChange: (view: WallboxesView) => void }) {
  if (wb.read_only && !wb.simulated) return <ReadOnlyCard wb={wb} onChange={onChange} />;
  return <ControlledCard wb={wb} onChange={onChange} />;
}

function ControlledCard({ wb, onChange }: { wb: NativeWallbox; onChange: (view: WallboxesView) => void }) {
  const [busy, setBusy] = useState(false);
  const r = wb.online ? wb.reading : null; // no old values of a wallbox that is away
  const sim = !!wb.simulated; // only a simulation (#440): nothing is written, the charger charges as it is set
  const chosen = wb.mode_current_a?.[wb.mode] ?? wb.max_current_a;
  const setMode = async (mode: WallboxMode, current_a?: number) => {
    setBusy(true);
    try {
      onChange(await postJson<WallboxesView>(`/api/wallboxes/${wb.id}/mode`, { mode, current_a }));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card wallbox">
      <div className="wallbox-head">
        <div>
          <strong>{wb.name}</strong>
          <div className="hint">{status(wb)}</div>
        </div>
      </div>
      {wb.online === false && <p className="hint">{wb.port != null ? t("devices.nativeWallbox.awayHintModbus") : t("devices.nativeWallbox.awayHint")}</p>}
      {sim && <Notice kind="info">{t("devices.nativeWallbox.simulationNotice")}</Notice>}
      <RealControl wb={wb} onChange={onChange} />
      {r && !r.connected && wb.mode !== "manual" && <p className="hint">{sim ? t("devices.nativeWallbox.noCarHintSimulation")
        : t("devices.nativeWallbox.noCarHint")}</p>}
      {!wb.local_key_set && <>
        <Notice kind="warn">{t("devices.nativeWallbox.keyLostHint")}</Notice>
        <Button variant="secondary" onClick={() => navigate("more/wallbox")}>{t("devices.nativeWallbox.keyLost")}</Button>
      </>}
      <Segmented value={wb.mode} disabled={busy} onChange={(m) => void setMode(m)} options={MODES} />
      <p className="hint">{modeHint(wb)}</p>
      {!sim && wb.above_limit && wb.mode !== "manual" && wb.mode !== "off" && <Notice kind="warn">{aboveLimitText(wb.above_limit)}</Notice>}
      {(wb.mode === "pv" || wb.mode === "minpv" || wb.mode === "now") && (
        <fieldset className="field field-group">
          <legend className="field-label">{t("devices.nativeWallbox.modeCurrent")}</legend>
          <Segmented value={String(chosen)} disabled={busy} options={currentOptions(wb, chosen)}
            onChange={(c) => void setMode(wb.mode, Number(c))} />
        </fieldset>
      )}
      {r && (
        <dl className="facts">
          {r.set_current_a != null && <><dt>{t("devices.nativeWallbox.current")}</dt>
            <dd>{t("devices.nativeWallbox.currentValue", { current: r.set_current_a, max: Math.min(wb.max_current_a, r.max_current_a ?? wb.max_current_a) })}</dd></>}
          {r.charging && <><dt>{t("devices.nativeWallbox.phaseCurrents")}</dt><dd>{amps(r.current_a)} A</dd></>}
          <dt>{t("devices.nativeWallbox.phases")}</dt><dd>{r.active_phases || r.installed_phases}</dd>
          {r.connected && !!r.session_kwh && <><dt>{t("devices.wallboxCard.chargedThisSession")}</dt><dd>{num(r.session_kwh, 1)} kWh</dd></>}
          {(sim || wb.controllable) && <OwnSettings r={r} />}
          {sim && wb.simulation && <><dt>{t("devices.nativeWallbox.simulationPlan")}</dt><dd>{planText(wb.simulation)}</dd></>}
        </dl>
      )}
      <WorkModeSetting wb={wb} onChange={onChange} />
    </div>
  );
}

type Draft = { id?: string; name: string; model: string; host: string; device_id: string; local_key: string;
  max_current_a: string; mode: WallboxMode; enabled: boolean; mode_current_a: NativeWallbox["mode_current_a"];
  stop_after_min: string; port: string; unit: string };
/** Address, port and unit id of a wallbox found by the read-only check, to show it in OpenAmpere (#439). */
export type Prefill = { host: string; port: number | null; unit: number };
const empty = (model: WallboxModel | undefined, key: string, prefill?: Prefill | null): Draft => ({
  name: model?.protocol === "modbus" ? t("devices.nativeWallbox.defaultNameFixed") : t("devices.nativeWallbox.defaultName"),
  model: key, host: prefill?.host ?? "", device_id: "", local_key: "", max_current_a: "16", mode: "manual", enabled: true,
  mode_current_a: {}, stop_after_min: "10", port: String(prefill?.port ?? model?.port ?? ""),
  unit: String(prefill?.unit ?? model?.unit ?? "") });
const portNumber = (value: string | number | null | undefined) => (String(value ?? "").trim() ? Number(value) : null);
const toItem = (wb: NativeWallbox | Draft) => ({ id: wb.id, name: wb.name, model: wb.model, host: wb.host,
  device_id: wb.device_id ?? "", local_key: "local_key" in wb ? wb.local_key : "", max_current_a: Number(wb.max_current_a),
  mode: wb.mode, enabled: wb.enabled, mode_current_a: wb.mode_current_a ?? {}, stop_after_min: Number(wb.stop_after_min ?? 10),
  port: portNumber(wb.port), unit: portNumber(wb.unit), control: "control" in wb ? !!wb.control : false });
const toDraft = (wb: NativeWallbox): Draft => ({ ...toItem(wb), id: wb.id, device_id: wb.device_id ?? "", local_key: "",
  max_current_a: String(wb.max_current_a), stop_after_min: String(wb.stop_after_min ?? 10), port: String(wb.port ?? ""),
  unit: String(wb.unit ?? "") });

/** The setup: the wallboxes OpenAmpere controls itself, add, change, remove. */
export function NativeWallboxSetup({ origin }: { origin: string }) {
  const { data: view, error, reload, setData } = useResource<WallboxesView>("/api/wallboxes", 10_000);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<NativeWallbox | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (items: ReturnType<typeof toItem>[]): Promise<boolean> => {
    setBusy(true);
    try {
      setData(await putJson<WallboxesView>("/api/wallboxes", { wallboxes: items }));
      toast(t("common.saved"));
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  };
  if (!view) return <LoadState error={error} onRetry={reload} />;
  const others = (id?: string) => view.wallboxes.filter((w) => w.id !== id).map(toItem);
  const onSave = async (d: Draft) => {
    if (await save([...others(d.id), toItem(d)])) {
      setDraft(null);
      setAdding(false);
    }
  };

  return (
    <>
      <p className="hint">{t("devices.nativeWallbox.intro")}</p>
      {view.wallboxes.map((wb) => draft?.id === wb.id ? (
        <div className="card form" key={wb.id}>
          <p className="hint flush">{wb.label}</p>
          <WallboxForm view={view} draft={draft} busy={busy} onSave={onSave} onCancel={() => setDraft(null)} />
        </div>
      ) : (
        <div className="card" key={wb.id}>
          <div className="wallbox-head">
            <div>
              <strong>{wb.name}</strong>
              <div className="hint">{wb.label} · {wb.host}{wb.port ? `:${wb.port}` : ""} · {status(wb)}</div>
            </div>
            <Switch checked={wb.enabled} disabled={busy} label={t("devices.nativeWallbox.enabled")}
              onChange={(on) => void save(view.wallboxes.map((w) => toItem(w.id === wb.id ? { ...w, enabled: on } : w)))} />
          </div>
          {wb.online === false && wb.error && <Notice kind="warn">{wb.error}</Notice>}
          {!wb.local_key_set && <Notice kind="warn">{t("devices.nativeWallbox.keyLostHint")}</Notice>}
          <div className="button-row">
            <Button variant="secondary" onClick={() => { setAdding(false); setDraft(toDraft(wb)); }}>
              {wb.local_key_set ? t("devices.nativeWallbox.edit") : t("devices.nativeWallbox.keyLost")}</Button>
            <Button variant="secondary" onClick={() => setRemoving(wb)}>{t("common.remove")}</Button>
          </div>
        </div>
      ))}
      {adding ? (
        <AddWallbox view={view} origin={origin} busy={busy} onSave={onSave} onCancel={() => setAdding(false)} />
      ) : (
        <Button variant="secondary" onClick={() => { setDraft(null); setAdding(true); }}>{t("devices.nativeWallbox.add")}</Button>
      )}
      {removing && (
        <Dialog title={t("devices.nativeWallbox.removeQuestion", { name: removing.name })} confirm={t("common.remove")} danger
          onCancel={() => setRemoving(null)}
          onConfirm={() => { void save(others(removing.id)); setRemoving(null); }}>
          <p>{t("devices.nativeWallbox.removeHint")}</p>
        </Dialog>
      )}
    </>
  );
}

const modelLabel = (m: { label: string; sold_by?: string[] }) =>
  m.sold_by?.length ? t("devices.nativeWallbox.soldBy", { label: m.label, sellers: m.sold_by.join(", ") }) : m.label;

/** One form for every wallbox without evcc: first which one, then what it needs. A wallbox OpenAmpere controls or
 *  shows gets its connection details (for Tuya with the steps to the local key); one it can only check so far gets
 *  the read-only check with its report; one it shows and can check offers both, and a readable check fills in the
 *  form (#439); any other one is pointed to the device reader. */
function AddWallbox({ view, origin, busy, onSave, onCancel }: {
  view: WallboxesView; origin: string; busy: boolean; onSave: (d: Draft) => void; onCancel: () => void;
}) {
  const { data: checkable, error, reload } = useCheckModels();
  const [model, setModel] = useState(view.models[0]?.key ?? "other");
  const [way, setWay] = useState<"show" | "check">("show");
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  if (!checkable) return <LoadState error={error} onRetry={reload} />;
  const live = view.models.find((m) => m.key === model);
  const checked = checkable.models.find((m) => m.key === model);
  const checking = !!checked && (!live || way === "check");
  const full = view.wallboxes.length >= view.max;
  const controlledModels = view.models.filter((m) => !m.read_only);
  const shownModels = view.models.filter((m) => m.read_only);
  const onlyChecked = checkable.models.filter((m) => !view.models.some((v) => v.key === m.key));
  const hint = live ? (live.read_only ? t("devices.nativeWallbox.whichShown") : t("devices.nativeWallbox.whichControlled"))
    : checked ? t("devices.nativeWallbox.whichChecked") : t("devices.wallboxDirect.otherHint");
  // the choice stays the same element while the fields below change: focus and keyboard keep working
  return (
    <div className="card form">
      <Field label={t("devices.nativeWallbox.which")} hint={hint}>
        <select className="input" value={model} onChange={(e) => { setModel(e.target.value); setWay("show"); setPrefill(null); }}>
          {!!controlledModels.length && (
            <optgroup label={t("devices.nativeWallbox.groupControlled")}>
              {controlledModels.map((m) => <option key={m.key} value={m.key}>{modelLabel(m)}</option>)}
            </optgroup>
          )}
          {!!shownModels.length && (
            <optgroup label={t("devices.nativeWallbox.groupShown")}>
              {shownModels.map((m) => <option key={m.key} value={m.key}>{modelLabel(m)}</option>)}
            </optgroup>
          )}
          {!!onlyChecked.length && (
            <optgroup label={t("devices.nativeWallbox.groupChecked")}>
              {onlyChecked.map((m) => <option key={m.key} value={m.key}>{modelLabel(m)}</option>)}
            </optgroup>
          )}
          <option value="other">{t("devices.wallboxDirect.modelOther")}</option>
        </select>
      </Field>
      {live && checked && (
        <fieldset className="field field-group">
          <legend className="field-label">{t("devices.nativeWallbox.way")}</legend>
          <Segmented value={way} onChange={setWay}
            options={[["show", t("devices.nativeWallbox.wayShow")], ["check", t("devices.nativeWallbox.wayCheck")]]} />
        </fieldset>
      )}
      {live && !checking && full && (
        <>
          <Notice kind="info">{t("devices.nativeWallbox.full", { max: view.max })}</Notice>
          <Button variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
        </>
      )}
      {live && !checking && !full && <WallboxForm key={`${model}-${JSON.stringify(prefill)}`} view={view}
        draft={empty(live, model, prefill)} busy={busy} onSave={onSave} onCancel={onCancel} />}
      {checking && <WallboxCheck key={model} origin={origin} model={model} onCancel={onCancel}
        onShow={live ? (found) => { setPrefill(found); setWay("show"); } : undefined} />}
      {!live && !checked && (
        <div className="button-row">
          <Button variant="secondary" onClick={() => navigate("more/device-reader")}>{t("devices.wallboxDirect.toDeviceReader")}</Button>
          <Button variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
        </div>
      )}
    </div>
  );
}

/** The fields of a wallbox OpenAmpere controls; the card around them comes from the caller. */
function WallboxForm({ view, draft, busy, onSave, onCancel }: {
  view: WallboxesView; draft: Draft; busy: boolean; onSave: (d: Draft) => void; onCancel: () => void;
}) {
  const [d, setD] = useState(draft);
  const [found, setFound] = useState<Found[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [testing, setTesting] = useState(false);
  const [tested, setTested] = useState<WallboxReading | null>(null);
  const set = (changes: Partial<Draft>) => { setD({ ...d, ...changes }); setTested(null); };
  const stored = draft.id ? view.wallboxes.find((w) => w.id === draft.id) : undefined;

  const search = async () => {
    setSearching(true);
    try {
      const result = await postJson<{ available: boolean; devices: Found[] }>("/api/wallboxes/search", {});
      if (!result.available) toast(t("devices.nativeWallbox.searchUnavailable"), "error");
      setFound(result.available ? result.devices : null);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSearching(false);
    }
  };
  const model = view.models.find((m) => m.key === d.model);
  const tuya = model?.protocol === "tuya";
  const modbus = model?.protocol === "modbus";
  const readOnly = !!model?.read_only;
  const shownOnly = readOnly && !model?.simulated; // a simulated control (#440) uses the limit and the waiting time
  const keyMissing = tuya && !d.local_key && !stored?.local_key_set;
  // port and unit id: empty means the usual ones, anything else a whole number (the server checks the ranges)
  const numbersOk = () => {
    if ([d.port, d.unit].every((v) => !v.trim() || Number.isInteger(Number(v)))) return true;
    toast(t("devices.wallboxDirect.invalidNumbers"), "error");
    return false;
  };
  const test = async () => {
    if (modbus && !numbersOk()) return;
    setTesting(true);
    try {
      setTested((await postJson<{ reading: WallboxReading }>("/api/wallboxes/test", modbus
        ? { model: d.model, host: d.host.trim(), port: portNumber(d.port), unit: portNumber(d.unit), id: d.id ?? null }
        : { model: d.model, host: d.host.trim(), local_key: d.local_key, id: d.id ?? null })).reading);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <Field label={t("devices.nativeWallbox.name")} hint={t("devices.nativeWallbox.nameHint")}>
        <input className="input" value={d.name} maxLength={40} onChange={(e) => set({ name: e.target.value })} />
      </Field>
      {readOnly && <Notice kind="info">{t("devices.nativeWallbox.readOnlyIntro")}</Notice>}
      {modbus && <DeviceSearch label={t("devices.wallboxDirect.search")}
        onUse={(f) => set({ host: f.host, ...(f.port ? { port: String(f.port) } : {}) })} />}
      {tuya && <TuyaKeyGuide />}
      {tuya && (view.search ? (
        <>
          <Button variant="secondary" busy={searching} onClick={() => void search()}>{t("devices.nativeWallbox.search")}</Button>
          {found && !found.length && <p className="hint">{t("devices.nativeWallbox.nothingFound")}</p>}
          {found?.map((f) => (
            <div className="button-row" key={f.device_id}>
              <span>{t("devices.nativeWallbox.foundAt", { host: f.host })}</span>
              <Button variant="secondary" onClick={() => set({ host: f.host, device_id: f.device_id })}>{t("devices.nativeWallbox.use")}</Button>
            </div>
          ))}
        </>
      ) : <p className="hint">{t("devices.nativeWallbox.noSearchInApp")}</p>)}
      <Field label={t("devices.nativeWallbox.address")} hint={t("devices.nativeWallbox.addressHint", { example: "192.168.178.60" })}>
        <input className="input" value={d.host} {...HOST_INPUT} onChange={(e) => set({ host: hostText(e.target.value) })} />
      </Field>
      {tuya && (
        <Field label={t("devices.nativeWallbox.localKey")}
          hint={stored?.local_key_set ? t("devices.nativeWallbox.localKeySaved") : t("devices.nativeWallbox.localKeyHint")}>
          <input className="input" type="password" autoComplete="off" value={d.local_key} maxLength={16}
            onChange={(e) => set({ local_key: e.target.value })} />
        </Field>
      )}
      {!shownOnly && <>
        <Field label={t("devices.nativeWallbox.maxCurrent")}
          hint={readOnly ? t("devices.nativeWallbox.maxCurrentHintSimulation") : t("devices.nativeWallbox.maxCurrentHint")}>
          <input className="input" value={d.max_current_a} inputMode="numeric" onChange={(e) => set({ max_current_a: e.target.value })} />
        </Field>
        <Field label={t("devices.nativeWallbox.stopAfter")} hint={t("devices.nativeWallbox.stopAfterHint")}>
          <input className="input" value={d.stop_after_min} inputMode="numeric" onChange={(e) => set({ stop_after_min: e.target.value })} />
        </Field>
      </>}
      {modbus && (
        <details className="advanced">
          <summary>{t("common.advanced")}</summary>
          <Field label={t("devices.wallboxDirect.port")} hint={t("devices.nativeWallbox.portHint", { port: model?.port ?? 502 })}>
            <input className="input" value={d.port} inputMode="numeric" placeholder={String(model?.port ?? 502)}
              onChange={(e) => set({ port: e.target.value })} />
          </Field>
          <Field label={t("devices.wallboxDirect.unit")} hint={t("devices.wallboxDirect.unitHint")}>
            <input className="input" value={d.unit} inputMode="numeric" placeholder={String(model?.unit ?? 1)}
              onChange={(e) => set({ unit: e.target.value })} />
          </Field>
        </details>
      )}
      {tuya && (
        <details className="advanced">
          <summary>{t("common.advanced")}</summary>
          <Field label={t("devices.nativeWallbox.deviceId")} hint={t("devices.nativeWallbox.deviceIdHint")}>
            <input className="input" value={d.device_id} autoComplete="off" onChange={(e) => set({ device_id: e.target.value })} />
          </Field>
        </details>
      )}
      {tested && (readOnly
        ? <Notice kind="ok">{t("devices.nativeWallbox.testOkReadOnly", { phases: tested.installed_phases,
          current: tested.max_current_a ?? "–", firmware: tested.firmware ?? "–" })}</Notice>
        : <Notice kind="ok">{t("devices.nativeWallbox.testOk", { phases: tested.installed_phases,
          current: tested.set_current_a ?? "–", firmware: tested.firmware ?? "–" })}</Notice>
      )}
      <p className="hint">{t("devices.nativeWallbox.testHint")}</p>
      <div className="button-row">
        <Button variant="secondary" busy={testing} disabled={!d.host.trim() || keyMissing} onClick={() => void test()}>
          {t("devices.nativeWallbox.test")}</Button>
        <Button busy={busy} disabled={!d.name.trim() || !d.host.trim() || keyMissing}
          onClick={() => { if (!modbus || numbersOk()) onSave(d); }}>{t("common.save")}</Button>
        <Button variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
      </div>
    </>
  );
}

/** The local key of a Tuya device: what it is, and the steps on Tuya's developer platform folded in until tapped. */
function TuyaKeyGuide() {
  return (
    <>
      <Notice kind="info">{t("devices.nativeWallbox.keyIntro")}</Notice>
      <details className="advanced">
        <summary>{t("devices.nativeWallbox.keyHowTo")}</summary>
        <ol className="steps-list">
          <li>{t("devices.nativeWallbox.keyStep1")}</li>
          <li>{t("devices.nativeWallbox.keyStep2")}</li>
          <li>{t("devices.nativeWallbox.keyStep3")}</li>
          <li>{t("devices.nativeWallbox.keyStep4")}</li>
          <li>{t("devices.nativeWallbox.keyStep5")}</li>
        </ol>
        <p className="hint">{t("devices.nativeWallbox.keyChanges")}</p>
        <p className="hint"><a href={WALLBOX_DIRECT_GUIDE()} target="_blank" rel="noreferrer">{t("devices.nativeWallbox.guide")}</a></p>
      </details>
    </>
  );
}
