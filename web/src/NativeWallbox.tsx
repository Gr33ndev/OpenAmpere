// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import { postJson, putJson, useResource } from "./api";
import { kw, num, timeZone } from "./format";
import { LOCALE, t } from "./i18n";
import { WALLBOX_DIRECT_GUIDE } from "./links";
import { navigate } from "./route";
import { Button, Dialog, Field, LoadState, Notice, Segmented, Switch, toast } from "./ui";
import { useCheckModels, WallboxCheck } from "./WallboxCheck";

/** Wallboxes OpenAmpere reads and controls itself, without evcc: so far the portable dé wallbox over Tuya. A
 *  portable wallbox is often unplugged: it is then shown as away, without old values, until it answers again. */

export type WallboxMode = "manual" | "off" | "pv" | "minpv" | "now";
export type WallboxReading = {
  state: "idle" | "plugged_in" | "charging" | "waiting" | "paused" | "finished" | "fault" | "unknown";
  connected: boolean; charging: boolean; power_w: number; voltage_v: (number | null)[]; current_a: (number | null)[];
  installed_phases: number; active_phases: number; session_kwh: number | null; session_s: number | null;
  temperature_c: number | null; set_current_a: number | null; max_current_a: number | null; steps_a: number[];
  firmware: string | null; cp_v: number | null;
};
export type NativeWallbox = {
  id: string; name: string; model: string; label: string; host: string; device_id: string | null;
  local_key_set: boolean; max_current_a: number; mode: WallboxMode; enabled: boolean;
  online: boolean | null; offline_since: number | null; updated: number | null; error: string | null;
  reading: WallboxReading | null; controlled: boolean;
};
export type WallboxesView = { wallboxes: NativeWallbox[]; models: { key: string; label: string; protocol: "tuya" }[]; max: number;
  search: boolean };
type Found = { device_id: string; host: string; product_key: string | null };

const MODES: [WallboxMode, string][] = [["manual", t("devices.nativeWallbox.modeManual")], ["off", t("common.off")],
  ["pv", t("devices.modes.solar")], ["minpv", t("devices.modes.minSolar")], ["now", t("devices.modes.fast")]];
const MODE_HINT: Record<WallboxMode, string> = {
  manual: t("devices.nativeWallbox.hintManual"),
  off: t("devices.modeHint.off"),
  pv: t("devices.nativeWallbox.hintPv"),
  minpv: t("devices.modeHint.minpv"),
  now: t("devices.modeHint.now"),
};

/** When a wallbox went away: the weekday and time within a week, the date before (it may be unplugged for long). */
function since(ts: number): string {
  const options: Intl.DateTimeFormatOptions = Date.now() / 1000 - ts < 6 * 86_400
    ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "numeric", year: "numeric" };
  return new Date(ts * 1000).toLocaleString(LOCALE, { ...options, timeZone: timeZone() });
}

function status(wb: NativeWallbox): string {
  const r = wb.reading;
  if (wb.online === false) {
    return wb.offline_since ? t("devices.nativeWallbox.awaySince", { time: since(wb.offline_since) }) : t("devices.nativeWallbox.away");
  }
  if (!r) return t("devices.nativeWallbox.asking");
  if (r.charging) return t("devices.status.charging", { power: kw(r.power_w) });
  if (r.state === "fault") return t("devices.nativeWallbox.fault");
  if (r.state === "finished") return t("devices.nativeWallbox.finished");
  if (!r.connected) return t("devices.status.noVehiclePlugged");
  if (wb.mode === "pv") return t("devices.status.waitingSolarSurplus");
  return t("devices.status.plugged");
}

const amps = (values: (number | null)[]) => values.filter((v): v is number => v != null).map((v) => num(v, 1)).join(" / ");

/** One wallbox on the devices tab: what it does now and the charging mode. */
export function NativeWallboxCard({ wb, onChange }: { wb: NativeWallbox; onChange: (view: WallboxesView) => void }) {
  const [busy, setBusy] = useState(false);
  const r = wb.online ? wb.reading : null; // no old values of a wallbox that is away
  const setMode = async (mode: WallboxMode) => {
    setBusy(true);
    try {
      onChange(await postJson<WallboxesView>(`/api/wallboxes/${wb.id}/mode`, { mode }));
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
      {wb.online === false && <p className="hint">{t("devices.nativeWallbox.awayHint")}</p>}
      <Segmented value={wb.mode} disabled={busy} onChange={(m) => void setMode(m)} options={MODES} />
      <p className="hint">{MODE_HINT[wb.mode]}</p>
      {r && (
        <dl className="facts">
          {r.set_current_a != null && <><dt>{t("devices.nativeWallbox.current")}</dt>
            <dd>{t("devices.nativeWallbox.currentValue", { current: r.set_current_a, max: Math.min(wb.max_current_a, r.max_current_a ?? wb.max_current_a) })}</dd></>}
          {r.charging && <><dt>{t("devices.nativeWallbox.phaseCurrents")}</dt><dd>{amps(r.current_a)} A</dd></>}
          <dt>{t("devices.nativeWallbox.phases")}</dt><dd>{r.active_phases || r.installed_phases}</dd>
          {r.connected && !!r.session_kwh && <><dt>{t("devices.wallboxCard.chargedThisSession")}</dt><dd>{num(r.session_kwh, 1)} kWh</dd></>}
        </dl>
      )}
    </div>
  );
}

type Draft = { id?: string; name: string; model: string; host: string; device_id: string; local_key: string;
  max_current_a: string; mode: WallboxMode; enabled: boolean };
const empty = (model: string): Draft => ({ name: t("devices.nativeWallbox.defaultName"), model, host: "", device_id: "",
  local_key: "", max_current_a: "16", mode: "manual", enabled: true });
const toItem = (wb: NativeWallbox | Draft) => ({ id: wb.id, name: wb.name, model: wb.model, host: wb.host,
  device_id: wb.device_id ?? "", local_key: "local_key" in wb ? wb.local_key : "", max_current_a: Number(wb.max_current_a),
  mode: wb.mode, enabled: wb.enabled });

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
              <div className="hint">{wb.label} · {wb.host} · {status(wb)}</div>
            </div>
            <Switch checked={wb.enabled} disabled={busy} label={t("devices.nativeWallbox.enabled")}
              onChange={(on) => void save(view.wallboxes.map((w) => toItem(w.id === wb.id ? { ...w, enabled: on } : w)))} />
          </div>
          {wb.online === false && wb.error && <Notice kind="warn">{wb.error}</Notice>}
          <div className="button-row">
            <Button variant="secondary" onClick={() => { setAdding(false); setDraft({ ...toItem(wb), id: wb.id,
              device_id: wb.device_id ?? "", local_key: "", max_current_a: String(wb.max_current_a) }); }}>
              {t("devices.nativeWallbox.edit")}</Button>
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

/** One form for every wallbox without evcc: first which one, then what it needs. A wallbox OpenAmpere controls gets
 *  its connection details (for Tuya with the steps to the local key); one it can only check so far gets the read-only
 *  check with its report; any other one is pointed to the device reader. */
function AddWallbox({ view, origin, busy, onSave, onCancel }: {
  view: WallboxesView; origin: string; busy: boolean; onSave: (d: Draft) => void; onCancel: () => void;
}) {
  const { data: checkable, error, reload } = useCheckModels();
  const [model, setModel] = useState(view.models[0]?.key ?? "other");
  if (!checkable) return <LoadState error={error} onRetry={reload} />;
  const controlled = view.models.find((m) => m.key === model);
  const checked = checkable.models.find((m) => m.key === model);
  const full = view.wallboxes.length >= view.max;
  // the choice stays the same element while the fields below change: focus and keyboard keep working
  return (
    <div className="card form">
      <Field label={t("devices.nativeWallbox.which")} hint={controlled ? t("devices.nativeWallbox.whichControlled")
        : checked ? t("devices.nativeWallbox.whichChecked") : t("devices.wallboxDirect.otherHint")}>
        <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
          <optgroup label={t("devices.nativeWallbox.groupControlled")}>
            {view.models.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </optgroup>
          <optgroup label={t("devices.nativeWallbox.groupChecked")}>
            {checkable.models.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </optgroup>
          <option value="other">{t("devices.wallboxDirect.modelOther")}</option>
        </select>
      </Field>
      {controlled && full && (
        <>
          <Notice kind="info">{t("devices.nativeWallbox.full", { max: view.max })}</Notice>
          <Button variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
        </>
      )}
      {controlled && !full && <WallboxForm key={model} view={view} draft={empty(model)} busy={busy} onSave={onSave} onCancel={onCancel} />}
      {checked && <WallboxCheck key={model} origin={origin} model={model} onCancel={onCancel} />}
      {!controlled && !checked && (
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
  const test = async () => {
    setTesting(true);
    try {
      setTested((await postJson<{ reading: WallboxReading }>("/api/wallboxes/test",
        { model: d.model, host: d.host.trim(), local_key: d.local_key, id: d.id ?? null })).reading);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setTesting(false);
    }
  };
  const tuya = view.models.find((m) => m.key === d.model)?.protocol === "tuya";
  const keyMissing = tuya && !d.local_key && !stored?.local_key_set;

  return (
    <>
      <Field label={t("devices.nativeWallbox.name")} hint={t("devices.nativeWallbox.nameHint")}>
        <input className="input" value={d.name} maxLength={40} onChange={(e) => set({ name: e.target.value })} />
      </Field>
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
        <input className="input" value={d.host} inputMode="decimal" autoComplete="off" onChange={(e) => set({ host: e.target.value })} />
      </Field>
      {tuya && (
        <Field label={t("devices.nativeWallbox.localKey")}
          hint={stored?.local_key_set ? t("devices.nativeWallbox.localKeySaved") : t("devices.nativeWallbox.localKeyHint")}>
          <input className="input" type="password" autoComplete="off" value={d.local_key} maxLength={16}
            onChange={(e) => set({ local_key: e.target.value })} />
        </Field>
      )}
      <Field label={t("devices.nativeWallbox.maxCurrent")} hint={t("devices.nativeWallbox.maxCurrentHint")}>
        <input className="input" value={d.max_current_a} inputMode="numeric" onChange={(e) => set({ max_current_a: e.target.value })} />
      </Field>
      {tuya && (
        <details className="advanced">
          <summary>{t("common.advanced")}</summary>
          <Field label={t("devices.nativeWallbox.deviceId")} hint={t("devices.nativeWallbox.deviceIdHint")}>
            <input className="input" value={d.device_id} autoComplete="off" onChange={(e) => set({ device_id: e.target.value })} />
          </Field>
        </details>
      )}
      {tested && (
        <Notice kind="ok">{t("devices.nativeWallbox.testOk", { phases: tested.installed_phases,
          current: tested.set_current_a ?? "–", firmware: tested.firmware ?? "–" })}</Notice>
      )}
      <p className="hint">{t("devices.nativeWallbox.testHint")}</p>
      <div className="button-row">
        <Button variant="secondary" busy={testing} disabled={!d.host.trim() || keyMissing} onClick={() => void test()}>
          {t("devices.nativeWallbox.test")}</Button>
        <Button busy={busy} disabled={!d.name.trim() || !d.host.trim() || keyMissing} onClick={() => onSave(d)}>{t("common.save")}</Button>
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
