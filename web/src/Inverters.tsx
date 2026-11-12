// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/** More than one inverter in one installation (#619): the settings of the further ones, a card per inverter on the
 *  overview and the choice of the inverter whose battery settings are shown. */
import { useEffect, useState } from "react";
import type { ExtraInverter, InverterReading, InverterStatus, Settings, Snapshot, Status } from "./api";
import { inverterName, MAIN_INVERTER } from "./api";
import { kw, num, percent } from "./format";
import { t } from "./i18n";
import { BatteryIcon, InverterIcon } from "./icons";
import { type Detected, ConnectionForm } from "./Setup";
import { Button, Dialog, Field, Notice, Segmented, Slider, SwitchRow, toast, Unsaved } from "./ui";

const MAX_EXTRA = 5;

/** A tested device as a further inverter; without a name the app shows "Wechselrichter <position>". */
function newEntry(found: Detected): ExtraInverter {
  return {
    name: "", driver: found.driver as ExtraInverter["driver"],
    host: found.host, port: found.port, unit: found.unit, register_map: "auto", read_function: "auto", timeout: 3,
    connection_mode: "persistent", capacity_kwh: 0, own_grid: false, in_first: false, rated_power_w: 0, rated_power_reported_w: 0,
  };
}

function capacityText(value: number): string {
  return value ? num(value, Number.isInteger(value) ? 0 : 1) : "";
}

function parseCapacity(text: string): number | null {
  if (!text.trim()) return 0;
  const value = Number(text.trim().replace(",", "."));
  return Number.isFinite(value) && value >= 0 && value <= 200 ? value : null;
}

/** The connection state of one inverter as one short line. */
function stateLine(item: InverterStatus | undefined): string {
  if (!item) return t("settings.inverters.notChecked");
  if (item.wrong_device) return t("settings.inverters.wrongDevice");
  if (!item.connected) {
    return item.last_error ? t("settings.inverters.notConnectedError", { error: item.last_error }) : t("settings.inverters.notConnected");
  }
  const device = item.device ? `${item.device.manufacturer} ${item.device.model}`.trim() : "";
  return device ? t("settings.inverters.connectedTo", { device }) : t("settings.inverters.connected");
}

/** Name and battery size of the first inverter, and the further ones: add, change, remove. */
export function InvertersSettings({ settings, status, locked, save }: {
  settings: Settings["values"];
  status: Status | null;
  locked: (key: "inverter.name" | "inverter.capacity_kwh" | "inverters") => boolean;
  save: (changes: Partial<Settings["values"]>) => Promise<boolean>;
}) {
  const extras = settings.inverters ?? [];
  const items = status?.inverters ?? [];
  const [name, setName] = useState(settings["inverter.name"] ?? "");
  const [capacity, setCapacity] = useState(capacityText(settings["inverter.capacity_kwh"] ?? 0));
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    setName(settings["inverter.name"] ?? "");
    setCapacity(capacityText(settings["inverter.capacity_kwh"] ?? 0));
  }, [settings]);
  const capacityValue = parseCapacity(capacity);
  const mainDirty = name.trim() !== (settings["inverter.name"] ?? "")
    || (extras.length > 0 && capacityValue !== (settings["inverter.capacity_kwh"] ?? 0));

  const saveList = (list: ExtraInverter[]) => save({ inverters: list });
  const add = async (found: Detected) => {
    const ok = await saveList([...extras, newEntry(found)]);
    if (ok) setAdding(false);
    return ok;
  };

  return (
    <>
      <div className="card form">
        <h2>{t("settings.inverters.firstTitle")}</h2>
        <Field label={t("settings.inverters.name")} hint={t("settings.inverters.nameHint")} locked={locked("inverter.name")}>
          <input className="input" value={name} maxLength={30} disabled={locked("inverter.name")}
            placeholder={inverterName("", 0)} onChange={(e) => setName(e.target.value)} />
        </Field>
        {extras.length > 0 && (
          <Field label={t("settings.inverters.capacity")} hint={t("settings.inverters.capacityHint")}
            locked={locked("inverter.capacity_kwh")}>
            <div className="input-unit"><input className="input" inputMode="decimal" value={capacity}
              disabled={locked("inverter.capacity_kwh")} onChange={(e) => setCapacity(e.target.value)} /><span>kWh</span></div>
          </Field>
        )}
        {capacityValue === null && <Notice kind="error">{t("settings.inverters.capacityInvalid")}</Notice>}
        <Button variant="secondary" disabled={!mainDirty || capacityValue === null}
          onClick={() => void save({ "inverter.name": name.trim(),
            ...(extras.length > 0 && capacityValue !== null ? { "inverter.capacity_kwh": capacityValue } : {}) })}>
          {t("common.save")}
        </Button>
        <Unsaved show={mainDirty} />
      </div>

      <div className="section-title">{t("settings.inverters.furtherTitle")}</div>
      {extras.length === 0 && !adding && <p className="hint">{t("settings.inverters.intro")}</p>}
      {extras.map((entry, index) => (
        <ExtraCard key={entry.id ?? index} entry={entry} number={index + 2}
          item={items.find((i) => i.id === entry.id)} disabled={locked("inverters")}
          onSave={(changed) => saveList(extras.map((e, j) => (j === index ? changed : e)))}
          onRemove={() => saveList(extras.filter((_, j) => j !== index))} />
      ))}
      {extras.length > 0 && <p className="hint">{t("settings.inverters.combinedHint")}</p>}
      {adding ? (
        <>
          <div className="card"><p>{t("settings.inverters.addIntro")}</p></div>
          <ConnectionForm initial={{ host: "", port: 502, unit: 0, driver: "auto" }} onUse={add}
            onSaved={() => toast(t("settings.inverters.added"))} saveLabel={t("settings.inverters.addSave")} />
          <Button variant="secondary" onClick={() => setAdding(false)}>{t("common.cancel")}</Button>
        </>
      ) : extras.length < MAX_EXTRA && !locked("inverters") && (
        <Button variant="secondary" onClick={() => setAdding(true)}>{t("settings.inverters.add")}</Button>
      )}
    </>
  );
}

function ExtraCard({ entry, number, item, disabled, onSave, onRemove }: {
  entry: ExtraInverter; number: number; item: InverterStatus | undefined; disabled: boolean;
  onSave: (entry: ExtraInverter) => Promise<boolean>; onRemove: () => Promise<boolean>;
}) {
  const [name, setName] = useState(entry.name);
  const [capacity, setCapacity] = useState(capacityText(entry.capacity_kwh));
  const [grid, setGrid] = useState(entry.own_grid);
  const [inFirst, setInFirst] = useState(!!entry.in_first);
  const [mode, setMode] = useState(entry.connection_mode);
  const [timeout, setTimeoutValue] = useState(entry.timeout);
  const [registerMap, setRegisterMap] = useState(entry.register_map);
  const [readFunction, setReadFunction] = useState(entry.read_function);
  const [removing, setRemoving] = useState(false);
  useEffect(() => {
    setName(entry.name); setCapacity(capacityText(entry.capacity_kwh)); setGrid(entry.own_grid); setInFirst(!!entry.in_first); setMode(entry.connection_mode);
    setTimeoutValue(entry.timeout); setRegisterMap(entry.register_map); setReadFunction(entry.read_function);
  }, [entry]);
  const capacityValue = parseCapacity(capacity);
  const dirty = name.trim() !== entry.name || capacityValue !== entry.capacity_kwh || grid !== entry.own_grid || inFirst !== !!entry.in_first
    || mode !== entry.connection_mode || timeout !== entry.timeout || registerMap !== entry.register_map
    || readFunction !== entry.read_function;
  const foxess = (item?.device?.driver ?? entry.driver) === "foxess";
  const title = inverterName(entry.name, number - 1);

  return (
    <div className="card form" data-testid={`inverter-${entry.id ?? number}`}>
      <h2>{title}</h2>
      <p className="hint">{entry.host}{entry.port !== 502 ? `:${entry.port}` : ""} · {stateLine(item)}</p>
      <Field label={t("settings.inverters.name")}>
        <input className="input" value={name} maxLength={30} disabled={disabled} placeholder={inverterName("", number - 1)}
          onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label={t("settings.inverters.capacity")} hint={t("settings.inverters.capacityHint")}>
        <div className="input-unit"><input className="input" inputMode="decimal" value={capacity} disabled={disabled}
          onChange={(e) => setCapacity(e.target.value)} /><span>kWh</span></div>
      </Field>
      {capacityValue === null && <Notice kind="error">{t("settings.inverters.capacityInvalid")}</Notice>}
      <SwitchRow label={t("settings.inverters.inFirst")} hint={t("settings.inverters.inFirstHint")} checked={inFirst}
        disabled={disabled} onChange={setInFirst} />
      {!inFirst && (
        <SwitchRow label={t("settings.inverters.ownGrid")} hint={t("settings.inverters.ownGridHint")} checked={grid}
          disabled={disabled} onChange={setGrid} />
      )}
      <Field label={t("common.connection")} hint={mode === "per_poll" ? t("settings.connectionPage.perPollHint")
        : t("settings.connectionPage.permanentHint")}>
        <Segmented value={mode} onChange={setMode} disabled={disabled}
          options={[["persistent", t("settings.connectionPage.permanent")], ["per_poll", t("settings.connectionPage.perPoll")]]} />
      </Field>
      <details className="expert">
        <summary>{t("settings.connectionPage.experts")}</summary>
        <Field label={t("settings.connectionPage.timeout")} hint={t("settings.connectionPage.timeoutHint")}>
          <Slider value={timeout} min={1} max={30} unit="s" onChange={setTimeoutValue} />
        </Field>
        {foxess && (
          <>
            <Field label={t("settings.connectionPage.foxessRegisterMap")}>
              <select className="input" value={registerMap} disabled={disabled} onChange={(e) => setRegisterMap(e.target.value)}>
                <option value="auto">{t("common.detectAutomatically")}</option>
                <option value="foxess_h3_new">{t("settings.connectionPage.foxessNewer")}</option>
                <option value="foxess_h3_legacy">{t("settings.connectionPage.foxessOlder")}</option>
              </select>
            </Field>
            <Field label={t("settings.connectionPage.readMethod")}>
              <select className="input" value={readFunction} disabled={disabled} onChange={(e) => setReadFunction(e.target.value)}>
                <option value="auto">{t("common.automatic")}</option>
                <option value="input">{t("settings.connectionPage.inputRegisters")}</option>
                <option value="holding">{t("settings.connectionPage.holdingRegisters")}</option>
              </select>
            </Field>
          </>
        )}
      </details>
      <Button variant="secondary" disabled={disabled || !dirty || capacityValue === null}
        onClick={() => void onSave({ ...entry, name: name.trim(), capacity_kwh: capacityValue ?? 0, own_grid: grid, in_first: inFirst,
          connection_mode: mode, timeout, register_map: registerMap, read_function: readFunction })}>
        {t("common.save")}
      </Button>
      <Unsaved show={dirty} />
      {!disabled && <button type="button" className="link" onClick={() => setRemoving(true)}>{t("settings.inverters.remove")}</button>}
      {removing && (
        <Dialog title={t("settings.inverters.removeTitle", { name: title })} confirm={t("settings.inverters.removeConfirm")} danger
          onCancel={() => setRemoving(false)} onConfirm={() => { setRemoving(false); void onRemove(); }}>
          <p>{t("settings.inverters.removeHint")}</p>
        </Dialog>
      )}
    </div>
  );
}

/** A card per inverter on the overview, with what each one reads now. Nothing with one inverter. */
export function InverterCards({ snap, stale }: { snap: Snapshot | null; stale: boolean }) {
  const readings = snap?.inverters ?? [];
  if (!readings.length) return null;
  return (
    <>
      <div className="section-title">{t("overview.inverterCards.title")}</div>
      <div className="card menu">
        {readings.map((r, index) => <InverterRow key={r.id} reading={r} index={index} stale={stale} />)}
      </div>
      {readings.map((r, index) => r.left_out ? (
        <p key={r.id} className="hint">{t("overview.inverterCards.leftOutHint", { name: inverterName(r.name, index) })}</p>
      ) : null)}
      {readings.some((r) => !r.connected && !r.left_out) && <p className="hint">{t("overview.inverterCards.missingHint")}</p>}
    </>
  );
}

function InverterRow({ reading, index, stale }: { reading: InverterReading; index: number; stale: boolean }) {
  const name = inverterName(reading.name, index);
  if (!reading.connected || stale) {
    return (
      <div className="device-row-compact">
        <InverterIcon size={36} />
        <span className="grow"><strong>{name}</strong><span className="menu-hint">{t("overview.inverterCards.notConnected")}</span></span>
      </div>
    );
  }
  const battery = reading.battery_power;
  const flow = battery == null ? "" : battery < -20 ? t("overview.inverterCards.charging", { power: kw(-battery) })
    : battery > 20 ? t("overview.inverterCards.discharging", { power: kw(battery) }) : t("overview.inverterCards.idle");
  return (
    <div className="device-row-compact">
      {reading.battery_soc != null ? <BatteryIcon size={36} soc={reading.battery_soc} /> : <InverterIcon size={36} />}
      <span className="grow">
        <strong>{name}</strong>
        <span className="menu-hint">{t("overview.inverterCards.solar", { power: kw(reading.pv_power ?? null) })}
          {flow ? ` · ${flow}` : ""}{reading.in_first ? ` · ${t("overview.inverterCards.inFirst")}` : ""}</span>
      </span>
      {reading.battery_soc != null && <span className="device-today">{percent(reading.battery_soc)}</span>}
    </div>
  );
}

/** Which inverter's battery settings are shown, once there is more than one (#619). Inverters whose device cannot be
 *  controlled are shown, but cannot be chosen. */
export function InverterPicker({ status, value, onChange }: {
  status: Status | null; value: string; onChange: (id: string) => void;
}) {
  const items = status?.inverters ?? [];
  if (items.length < 2) return null;
  return (
    <Field label={t("settings.inverters.pickerLabel")} hint={t("settings.inverters.pickerHint")}>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {items.map((item, index) => (
          <option key={item.id} value={item.id} disabled={!!item.device && !item.device.supports_control}>
            {inverterName(item.name, index)}
            {item.device && !item.device.supports_control ? ` (${t("settings.inverters.readOnly")})` : ""}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** "?inverter=<id>" for a further inverter, nothing for the first one. */
export function inverterQuery(id: string): string {
  return id && id !== MAIN_INVERTER ? `?inverter=${encodeURIComponent(id)}` : "";
}

/** The first inverter's name when there are several: grid charging and the export limit act on it only. */
export function firstInverterOnly(status: Status | null): string | null {
  const items = status?.inverters ?? [];
  return items.length > 1 ? inverterName(items[0].name, 0) : null;
}

