// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { type ReactNode, useEffect, useState } from "react";
import { putJson, useResource } from "./api";
import { DeviceIcon } from "./DevicesPage";
import type { PageProps } from "./SettingsPages";
import { AmountInput, Button, Dialog, Field, LoadState, SubPage, toast } from "./ui";
import { amountInput } from "./format";
import { t } from "./i18n";

export type ConsumerData = {
  id?: string; name: string; kind: "mypv" | "shelly1" | "shelly2" | "http"; host: string; port: number; unit: number;
  channel: number; url_on: string; url_off: string; power_w: number; min_power_w: number; min_on_min: number;
  min_off_min: number; battery_min_soc: number; price_limit_ct: number | null; enabled: boolean;
  state?: { on: boolean | null; power_w: number; since: number; error: string | null; temperature_c: number | null;
    target_c: number | null; status: string | null; actual_w: number | null };
};

type Choice = "mypv" | "shelly" | "http";
const CHOICES: { id: Choice; title: string; text: string; kind: "heating_rod" | "switch" }[] = [
  { id: "mypv", title: t("common.myPvImmersionHeater"), text: t("devices.choices.myPvHint"), kind: "heating_rod" },
  { id: "shelly", title: t("devices.choices.shelly"), text: t("devices.choices.shellyHint"), kind: "switch" },
  { id: "http", title: t("common.customWebAddresses"), text: t("devices.choices.httpHint"), kind: "switch" },
];

function template(choice: Choice): ConsumerData {
  const base = { name: "", host: "", port: 502, unit: 1, channel: 0, url_on: "", url_off: "", min_power_w: 500,
    battery_min_soc: 0, price_limit_ct: null, enabled: true };
  if (choice === "mypv") return { ...base, name: t("devices.template.immersionHeater"), kind: "mypv", power_w: 3000, min_on_min: 0, min_off_min: 0 };
  if (choice === "shelly") return { ...base, name: t("devices.template.heatPump"), kind: "shelly2", power_w: 2000, min_on_min: 10, min_off_min: 5 };
  return { ...base, name: t("common.device"), kind: "http", power_w: 1000, min_on_min: 10, min_off_min: 5 };
}

const kindLabel = (c: ConsumerData) => (c.kind === "mypv" ? t("common.myPvImmersionHeater") : c.kind === "http" ? t("common.customWebAddresses") : t("devices.kindLabel.shellyRelay"));
const num = (v: string, fallback = 0) => (v.trim() === "" ? fallback : Number(v.replace(",", ".")) || 0);

function Editor({ value, onSave, onCancel, onRemove, busy }: {
  value: ConsumerData; onSave: (c: ConsumerData) => void; onCancel: () => void; onRemove?: () => void; busy: boolean;
}) {
  const [c, setC] = useState(value);
  const [removing, setRemoving] = useState(false);
  const set = (patch: Partial<ConsumerData>) => setC((x) => ({ ...x, ...patch }));
  const mypv = c.kind === "mypv";
  const shelly = c.kind === "shelly1" || c.kind === "shelly2";
  const valid = c.name.trim() && (c.kind === "http" ? c.url_on.trim() && c.url_off.trim() : c.host.trim()) && c.power_w > 0;
  return (
    <div className="card form">
      <div className="device-card-head">
        <DeviceIcon kind={mypv ? "heating_rod" : "switch"} size={36} />
        <strong className="grow">{kindLabel(c)}</strong>
      </div>
      <Field label={t("common.name")}><input className="input" value={c.name} maxLength={40} onChange={(e) => set({ name: e.target.value })} /></Field>
      {c.kind === "http" ? (
        <>
          <Field label={t("devices.editor.switchOnUrl")}><input className="input" value={c.url_on} placeholder="http://" onChange={(e) => set({ url_on: e.target.value })} /></Field>
          <Field label={t("devices.editor.switchOffUrl")}><input className="input" value={c.url_off} placeholder="http://" onChange={(e) => set({ url_off: e.target.value })} /></Field>
        </>
      ) : (
        <Field label={mypv ? t("devices.editor.heaterIp") : t("devices.editor.shellyIp")}>
          <input className="input" value={c.host} inputMode="decimal" placeholder={t("devices.editor.example", { example: "192.168.178.40" })} onChange={(e) => set({ host: e.target.value })} />
        </Field>
      )}
      <Field label={mypv ? t("devices.editor.maxPower") : t("devices.editor.devicePower")}
        hint={mypv ? t("devices.editor.maxPowerHint") : t("devices.editor.switchOnHint")}>
        <div className="input-unit"><input className="input" inputMode="numeric" value={c.power_w}
          onChange={(e) => set({ power_w: num(e.target.value) })} /><span>W</span></div>
      </Field>
      {mypv && <p className="hint">{t("devices.editor.myPvSetupHint")}</p>}

      <details className="advanced">
        <summary>{t("common.advanced")}</summary>
        {mypv && (
          <Field label={t("devices.editor.minSurplus")} hint={t("devices.editor.minSurplusHint")}>
            <div className="input-unit"><input className="input" inputMode="numeric" value={c.min_power_w}
              onChange={(e) => set({ min_power_w: num(e.target.value) })} /><span>W</span></div>
          </Field>
        )}
        {mypv && (
          <Field label={t("common.port")}><input className="input" inputMode="numeric" value={c.port} onChange={(e) => set({ port: num(e.target.value, 502) })} /></Field>
        )}
        {shelly && (
          <>
            <Field label={t("devices.editor.shellyGeneration")}>
              <select className="input" value={c.kind} onChange={(e) => set({ kind: e.target.value as ConsumerData["kind"] })}>
                <option value="shelly2">{t("devices.editor.newerGeneration")}</option>
                <option value="shelly1">{t("devices.editor.firstGeneration")}</option>
              </select>
            </Field>
            <Field label={t("devices.editor.channel")} hint={t("devices.editor.channelHint")}>
              <input className="input" inputMode="numeric" value={c.channel} onChange={(e) => set({ channel: num(e.target.value) })} />
            </Field>
          </>
        )}
        {!mypv && (
          <div className="field-row">
            <Field label={t("devices.editor.minimumRunTime")}><div className="input-unit"><input className="input" inputMode="numeric" value={c.min_on_min}
              onChange={(e) => set({ min_on_min: num(e.target.value) })} /><span>min</span></div></Field>
            <Field label={t("devices.editor.minimumPause")}><div className="input-unit"><input className="input" inputMode="numeric" value={c.min_off_min}
              onChange={(e) => set({ min_off_min: num(e.target.value) })} /><span>min</span></div></Field>
          </div>
        )}
        <Field label={t("devices.editor.cheapGridPower")} hint={t("devices.editor.cheapGridPowerHint")}>
          <div className="input-unit"><AmountInput value={c.price_limit_ct} format={amountInput}
            onChange={(v) => set({ price_limit_ct: v })} /><span>ct/kWh</span></div>
        </Field>
      </details>

      <div className="button-row">
        <Button busy={busy} disabled={!valid} onClick={() => onSave(c)}>{t("common.save")}</Button>
        <Button variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
        {onRemove && <button type="button" className="link danger-link" onClick={() => setRemoving(true)}>{t("devices.editor.removeDevice")}</button>}
      </div>
      {removing && onRemove && (
        <Dialog title={t("devices.editor.removeQuestion", { name: value.name })} confirm={t("common.remove")} danger
          onCancel={() => setRemoving(false)} onConfirm={() => { setRemoving(false); onRemove(); }}>
          <p>{t("devices.editor.removeHint")}</p>
        </Dialog>
      )}
    </div>
  );
}

export function ConsumersPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<{ consumers: ConsumerData[] }>("/api/consumers", 15_000);
  const [list, setList] = useState<ConsumerData[] | null>(null);
  const [editing, setEditing] = useState<number | "choose" | ConsumerData | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setList(data.consumers); }, [data]);

  const persist = async (next: ConsumerData[]) => {
    setBusy(true);
    try {
      const saved = await putJson<{ consumers: ConsumerData[] }>("/api/consumers", { consumers: next.map(({ state: _s, ...c }) => c) });
      setData(saved);
      setEditing(null);
      toast(t("common.saved"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  let body: ReactNode;
  if (!list) {
    body = <LoadState error={error} onRetry={reload} />;
  } else if (editing === "choose") {
    body = (
      <>
        <div className="section-title">{t("devices.consumersPage.chooseKind")}</div>
        <div className="card choices">
          {CHOICES.map((ch) => (
            <button type="button" key={ch.id} className="choice" onClick={() => setEditing(template(ch.id))}>
              <DeviceIcon kind={ch.kind} size={36} />
              <span><strong>{ch.title}</strong><span className="meta">{ch.text}</span></span>
            </button>
          ))}
        </div>
        <Button variant="secondary" onClick={() => setEditing(null)}>{t("common.cancel")}</Button>
      </>
    );
  } else if (typeof editing === "number") {
    body = <Editor value={list[editing]} busy={busy} onCancel={() => setEditing(null)}
      onSave={(c) => void persist(list.map((x, i) => (i === editing ? c : x)))}
      onRemove={() => void persist(list.filter((_, i) => i !== editing))} />;
  } else if (editing) {
    body = <Editor value={editing} busy={busy} onCancel={() => setEditing(null)} onSave={(c) => void persist([...list, c])} />;
  } else {
    body = (
      <>
        {list.length ? (
          <div className="card menu">
            {list.map((c, i) => (
              <button type="button" key={c.id ?? i} className="device-row-compact" onClick={() => setEditing(i)}>
                <DeviceIcon kind={c.kind === "mypv" ? "heating_rod" : "switch"} size={36} />
                <span className="grow"><strong>{c.name}</strong><span className="menu-hint">{kindLabel(c)} · {c.kind === "http" ? t("devices.consumersPage.webAddresses") : c.host}</span></span>
                <span className="link">{t("devices.consumersPage.edit")}</span>
              </button>
            ))}
          </div>
        ) : <p className="hint">{t("devices.consumersPage.emptyState")}</p>}
        {list.length < 8 && <Button onClick={() => setEditing("choose")}>{t("devices.consumersPage.addDevice")}</Button>}
        <p className="hint">{t("devices.consumersPage.hint")}</p>
      </>
    );
  }

  return <SubPage title={t("devices.consumersPage.title")} onBack={editing !== null ? () => setEditing(null) : onBack}>{body}</SubPage>;
}
