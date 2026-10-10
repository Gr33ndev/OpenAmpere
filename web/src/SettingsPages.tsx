// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { Fragment, useEffect, useState } from "react";
import type { AuthStatus, BatterySettings, BatteryState, CloudImportState, ExportLimit, FeedInRule, SecretKey, SettingKey, Settings, Snapshot, Status } from "./api";
import { getJson, OFFLINE_MESSAGE, postFile, postJson, putJson, PV_INPUT_COLORS, useOnServerChange, useResource } from "./api";
import { DEMO } from "./demo/flag";
import { CHANGELOG_URL, IMPRINT_URL, ISSUES_URL, LICENSES_DATA_URL, REPO_URL } from "./links";
import { LANGUAGES, lang, LOCALE, setLang, t, tx, type Lang } from "./i18n";
import { amountInput, ct, dayOf, isoDate, kw, num, timeZone, todayIso, updatedLabel } from "./format";
import { decimalInput, parseDecimal as toNumber, parseWatts } from "./decimal";
import { batterySettingName, batterySettingValue, describe, logCsv, statusLabel, type LogEntry } from "./controlLog";
import { Chart } from "./Chart";
import { Chevron } from "./icons";
import { ConnectionForm, SetupHelp } from "./Setup";
import { ControlModeBar } from "./ControlMode";
import { LastRestore, RestoreButton } from "./Restore";
import { UpdatesCard } from "./Updates";
import { AmountInput, Button, Checkbox, Dialog, Field, LearnMore, LoadState, MenuRow, Notice, Segmented, Slider, SubPage, SwitchRow, toast, Unsaved } from "./ui";

export type PageProps = { onBack: () => void; onNavigate?: (page: string) => void };

/** A value hidden without login (#164) arrives as null; it is never sent back, so it cannot overwrite the real one. */
const withoutPlaceholders = (changes: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(changes).filter(([, value]) => value !== null));

/** Loads settings and saves partial changes. */
// numbers in input fields with the decimal separator of the app's language (#223)
const de = (v: number) => decimalInput(v, (1.5).toLocaleString(LOCALE).includes(","));

export function useSettings() {
  const { data, setData, error, reload } = useResource<Settings>("/api/settings");
  const save = async (changes: Partial<Settings["values"]> & Partial<Record<SecretKey, string>>) => {
    try {
      setData(await putJson<Settings>("/api/settings", { ...withoutPlaceholders(changes), _revision: data?.revision }));
      toast(t("common.saved"));
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    }
  };
  const locked = (key: SettingKey | "cloud.api_key") => data?.locked.includes(key) ?? false;
  /** Shown only after login (#164). */
  const hidden = (key: SettingKey) => data?.hidden?.includes(key) ?? false;
  return { settings: data?.values ?? null, secrets: data?.secrets ?? null, locked, hidden, lockedKeys: data?.locked ?? [],
           save, error, reload };
}

// ---------------------------------------------------------------------------

const WORK_MODES: { id: NonNullable<BatterySettings["work_mode"]>; label: string; hint: string }[] = [
  { id: "self_use", label: t("common.selfConsumption"), hint: t("settings.workModes.selfUseHint") },
  { id: "feed_in_first", label: t("common.preferFeedIn"), hint: t("settings.workModes.feedInFirstHint") },
  { id: "backup", label: t("common.backupReserve"), hint: t("settings.workModes.backupHint") },
  { id: "peak_shaving", label: t("settings.workModes.peakShaving"), hint: t("settings.workModes.peakShavingHint") },
];

const FIELDS = ["work_mode", "min_soc", "max_soc", "min_soc_on_grid"] as const;

function SocSlider({ value, min, max, disabled, onChange }: {
  value: number | null; min: number; max: number; disabled: boolean; onChange: (v: number) => void;
}) {
  if (value == null) return <p className="hint">{t("settings.socSlider.couldNotRead")}</p>;
  return <Slider value={value} min={min} max={Math.max(min, max)} unit="%" disabled={disabled} onChange={onChange} />;
}

/** The battery from 0 to 100 %: which part is used when. */
function SocBar({ min, reserve, max }: { min: number | null; reserve: number | null; max: number | null }) {
  if (min == null || reserve == null || max == null) return null;
  const zones = [
    { from: 0, to: min, cls: "never", label: t("settings.socBar.neverUsed") },
    { from: min, to: reserve, cls: "backup", label: t("settings.socBar.onlyDuringPowerCut") },
    { from: reserve, to: max, cls: "daily", label: t("settings.socBar.everydayUse") },
    { from: max, to: 100, cls: "unused", label: t("settings.socBar.notCharged") },
  ].filter((z) => z.to > z.from);
  return (
    <div className="card">
      <div className="soc-bar" role="img"
        aria-label={zones.map((z) => t("settings.socBar.zone", { label: z.label, from: z.from, to: z.to })).join(", ")}>
        {zones.map((z) => <div key={z.cls} className={`zone ${z.cls}`} style={{ flexGrow: z.to - z.from }} />)}
      </div>
      <div className="soc-legend">
        {zones.map((z) => <span key={z.cls}><i className={`zone ${z.cls}`} />{z.label} ({z.from}–{z.to} %)</span>)}
      </div>
    </div>
  );
}

/** After a write: the values read back from the inverter, before the button can be used again (#244). */
async function readBack<T>(path: string, setData: (value: T) => void, reload: () => void): Promise<T | null> {
  try {
    const value = await getJson<T>(path);
    setData(value);
    return value;
  } catch {
    reload(); // the write went through; the page keeps trying to load
    return null;
  }
}

const formOf = (s: BatteryState): BatterySettings => ({ work_mode: s.work_mode, min_soc: s.min_soc, max_soc: s.max_soc,
  min_soc_on_grid: s.min_soc_on_grid });

export function BatteryPage({ onBack }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data: current, error, reload, setData } = useResource<BatteryState>("/api/battery/settings");
  const [form, setForm] = useState<BatterySettings | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (current) setForm(formOf(current));
  }, [current]);

  const control = status?.control;
  const deviceSupportsControl = status?.device?.supports_control ?? true;
  const editable = !!control?.enabled && deviceSupportsControl;
  const changed = form && current && FIELDS.some((k) => form[k] !== current[k]);
  const socEditable = editable && !!current && !current.unreadable.some((k) => k !== "work_mode");
  const set = (patch: Partial<BatterySettings>) => setForm((f) => (f ? { ...f, ...patch } : f));

  const save = async () => {
    if (!form || !current) return;
    const changes = Object.fromEntries(FIELDS.filter((k) => form[k] !== current[k]).map((k) => [k, form[k]]));
    setBusy(true);
    try {
      const r = await putJson<{ dry_run: boolean; written: object; result?: string; warning?: string | null }>("/api/battery/settings", changes);
      toast(r.dry_run ? t("settings.batteryPage.testModeLogged") : r.result === "ok" ? t("settings.batteryPage.saved") : r.result ?? t("common.saved"));
      if (r.warning) toast(r.warning, "error");
      // busy until the values read back are shown, the form in the same render (not one later in the effect)
      const fresh = await readBack<BatteryState>("/api/battery/settings", setData, reload);
      if (fresh) setForm(formOf(fresh));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title={t("settings.batteryPage.title")} onBack={onBack}>
      {!deviceSupportsControl ? (
        <Notice kind="info">{t("settings.batteryPage.readOnly",
          { manufacturer: status?.device?.manufacturer ?? "" })}</Notice>
      ) : <ControlModeBar compact />}
      {!form && (error ? <LoadState error={error} onRetry={reload} /> : <p className="hint">{t("settings.batteryPage.reading")}</p>)}

      {current?.external_change && (
        <Notice kind="error">
          {t("settings.batteryPage.overwritten", { changes: Object.entries(current.external_change.found)
            .map(([k, v]) => t("settings.batteryPage.changedValue", { name: batterySettingName(k), value: batterySettingValue(k, v) })).join(", ") })}{" "}
          {t("settings.batteryPage.overwrittenHint")}
          {" "}{t("settings.batteryPage.smartboxHint")}
        </Notice>
      )}
      {form && current && current.unreadable.length > 0 && (
        <Notice kind="warn">
          {t("settings.batteryPage.unreadValues",
            { values: current.unreadable.map(batterySettingName).join(", ") })}
        </Notice>
      )}

      {form && (
        <>
          <SocBar min={form.min_soc} reserve={form.min_soc_on_grid} max={form.max_soc} />

          <div className="section-title">{t("settings.batteryPage.backupReserve")}</div>
          <div className="card form">
            <p className="hint">{t("settings.batteryPage.backupReserveHint")}</p>
            <SocSlider value={form.min_soc_on_grid} disabled={!socEditable}
              min={Math.max(10, form.min_soc ?? 10)} max={Math.min(99, (form.max_soc ?? 100) - 1)}
              onChange={(v) => set({ min_soc_on_grid: v })} />
          </div>

          <div className="section-title">{t("settings.batteryPage.chargeLimits")}</div>
          <div className="card form">
            <Field label={t("settings.batteryPage.maxSoc")} hint={t("settings.batteryPage.maxSocHint")}>
              <SocSlider value={form.max_soc} disabled={!socEditable}
                min={Math.max(20, (form.min_soc_on_grid ?? 10) + 1)} max={100} onChange={(v) => set({ max_soc: v })} />
            </Field>
            <Field label={t("settings.batteryPage.backupLowerLimit")}
              hint={t("settings.batteryPage.backupLowerLimitHint")}>
              <SocSlider value={form.min_soc} disabled={!socEditable}
                min={0} max={form.min_soc_on_grid ?? 100} onChange={(v) => set({ min_soc: v })} />
            </Field>
          </div>

          <div className="section-title">{t("common.operatingMode")}</div>
          <div className="card choices">
            {WORK_MODES.map((m) => (
              <button type="button" key={m.id} className={`choice ${form.work_mode === m.id ? "active" : ""}`} disabled={!editable}
                onClick={() => set({ work_mode: m.id })}>
                <span className="radio" />
                <span><strong>{m.label}</strong><span className="meta">{m.hint}</span></span>
              </button>
            ))}
          </div>

          {editable && <Button onClick={save} busy={busy} disabled={!changed}>{t("settings.batteryPage.apply")}</Button>}
          {editable && <Unsaved show={!!changed && !busy} />}
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

type Window = { from: string; to: string; price_ct: number };
type TariffForm = { valid_from: string; kind: "fixed" | "time" | "dynamic"; price_ct: string; surcharge_ct: string;
  vat_percent: string; feed_in_ct: string; area: "DE" | "AT"; base_fee_eur_month: string; windows: Window[] };
type TariffData = { valid_from: string; kind: "fixed" | "time" | "dynamic"; price_ct: number; surcharge_ct: number;
  vat_percent: number; feed_in_ct: number; area: "DE" | "AT"; base_fee_eur_month: number; windows?: Window[] };

/** Own price windows, e.g. a night tariff or time-variable grid fees (§ 14a EnWG, module 3) (#24). */
function TimeWindows({ windows, onChange }: { windows: Window[]; onChange: (w: Window[]) => void }) {
  const set = (i: number, patch: Partial<Window>) => onChange(windows.map((w, j) => (j === i ? { ...w, ...patch } : w)));
  return (
    <div className="time-windows">
      <p className="hint">{t("settings.timeWindows.hint")}</p>
      {windows.map((w, i) => (
        <div className="time-window" key={i}>
          <Field label={t("common.from")}><input className="input" type="time" value={w.from} onChange={(e) => set(i, { from: e.target.value })} /></Field>
          <Field label={t("common.to")}><input className="input" type="time" value={w.to} onChange={(e) => set(i, { to: e.target.value })} /></Field>
          <Field label={t("common.price")}><div className="input-unit"><AmountInput value={w.price_ct} format={amountInput}
            onChange={(v) => set(i, { price_ct: v ?? 0 })} /><span>ct</span></div></Field>
          <button type="button" className="link danger-link" onClick={() => onChange(windows.filter((_, j) => j !== i))}>{t("common.remove")}</button>
        </div>
      ))}
      {windows.length < 6 && <button type="button" className="link" onClick={() => onChange([...windows, { from: "00:00", to: "06:00", price_ct: 20 }])}>
        {t("settings.timeWindows.addTimeWindow")}</button>}
    </div>
  );
}

function PriceChart() {
  const { data } = useResource<{ kind: string; entries: { ts: number; ct: number }[] }>(`/api/prices?date=${todayIso()}`, 15 * 60_000);
  if (!data || data.kind === "fixed") return null;
  if (!data.entries.length) return <p className="hint">{t("settings.priceChart.noPrices")}</p>;
  const x = data.entries.map((e) => e.ts);
  const cheapest = data.entries.reduce((a, b) => (b.ct < a.ct ? b : a));
  return (
    <>
      <div className="section-title">{t("settings.priceChart.title")}</div>
      <Chart x={x} series={[{ label: t("common.price"), color: "var(--grid)", values: data.entries.map((e) => e.ct), unit: "ct" }]}
        xFormat={(ts) => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() })}
        height={180} label={t("settings.priceChart.chartLabel")} />
      <p className="hint">{t("settings.priceChart.cheapest", {
        time: new Date(cheapest.ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() }),
        price: ct(cheapest.ct) })}</p>
    </>
  );
}

type EegZone = { from_kw: number; to_kw: number; kw: number; share: number; ct: number };
type EegView = { auto: boolean; full: boolean; commissioning_date: string; installed_kwp: number; error: string | null;
  rate: { ct: number; period_from: string; period_to: string; full: boolean; funding_until: string; zones: EegZone[] } | null };
const deDate = (iso: string) => iso.split("-").reverse().join(".");

/** Feed-in compensation from the EEG rates: commissioning date, installed power and kind of feed-in (#71). */
function EegCard({ eeg, onSaved }: { eeg: EegView; onSaved: () => void }) {
  const { settings, save, locked } = useSettings();
  const [form, setForm] = useState({ auto: eeg.auto, date: eeg.commissioning_date, kwp: "", full: eeg.full });
  const initial = { auto: eeg.auto, date: eeg.commissioning_date, kwp: eeg.installed_kwp ? de(eeg.installed_kwp) : "", full: eeg.full };
  useOnServerChange(initial, setForm);
  const kwp = form.kwp.trim() === "" ? 0 : toNumber(form.kwp);
  const valid = Number.isFinite(kwp) && kwp >= 0 && kwp <= 1000;
  // by value, not by text: "9,80" saved as "9,8" is not a change (#244)
  const dirty = form.auto !== initial.auto || form.date !== initial.date || form.full !== initial.full
    || kwp !== (eeg.installed_kwp || 0);
  const lock = ["tariff.feed_in_auto", "tariff.feed_in_full", "pv.commissioning_date", "pv.installed_kwp"]
    .some((k) => locked(k as SettingKey));
  const rate = eeg.rate;
  return (
    <div className="card form">
      <h2>{t("settings.eegCard.title")}</h2>
      <SwitchRow label={t("settings.eegCard.useEegRates")} checked={form.auto} disabled={!settings || lock}
        hint={t("settings.eegCard.intro")}
        onChange={(auto) => setForm({ ...form, auto })} />
      {form.auto && (<>
        <div className="field-row">
          <Field label={t("settings.eegCard.commissioningDate")}>
            <input className="input" type="date" value={form.date} min="2000-01-01" max={todayIso()} disabled={lock}
              onChange={(e) => setForm({ ...form, date: e.target.value })} />
          </Field>
          <Field label={t("settings.eegCard.pvCapacity")}>
            <div className="input-unit"><input className="input" inputMode="decimal" value={form.kwp} placeholder={t("common.kwpPlaceholder")} disabled={lock}
              onChange={(e) => setForm({ ...form, kwp: e.target.value })} /><span>kWp</span></div>
          </Field>
        </div>
        <p className="hint">{t("settings.eegCard.whereToFind")}</p>
        <Field label={t("settings.eegCard.feedInType")}>
          <Segmented value={form.full ? "full" : "partial"} disabled={lock} onChange={(v) => setForm({ ...form, full: v === "full" })}
            options={[["partial", t("settings.eegCard.surplus")], ["full", t("settings.eegCard.fullFeedIn")]]} />
        </Field>
        <p className="hint">{t("settings.eegCard.typeHint")}</p>
        {!dirty && (rate ? (
          <div>
            <p>{tx("settings.eegCard.rate", { rate: <strong>{ct(rate.ct)} ct/kWh</strong> })}</p>
            <p className="hint">
              {rate.zones.length > 1
                ? tx("settings.eegCard.tiers", { tiers: <>{rate.zones.map((z, i) => (
                  <span key={z.from_kw}>{i > 0 && " + "}{t("settings.eegCard.tier", { kw: num(z.kw, 3), price: ct(z.ct) })}</span>))}</> })
                : t("settings.eegCard.rateUpTo10Kw", { price: ct(rate.zones[0].ct) })}
              {" "}{form.full && !rate.full
                ? t("settings.eegCard.periodWithoutFullFeedIn",
                  { from: deDate(rate.period_from), to: deDate(rate.period_to) })
                : t("settings.eegCard.period", { from: deDate(rate.period_from), to: deDate(rate.period_to) })}
              {" "}{t("settings.eegCard.paidUntil", { until: deDate(rate.funding_until) })}
            </p>
            <p className="hint">{t("settings.eegCard.differentHint")}</p>
          </div>
        ) : eeg.error && <Notice kind="warn">{eeg.error}</Notice>)}
      </>)}
      {dirty && <Button disabled={!valid || lock} onClick={async () => {
        if (await save({ "tariff.feed_in_auto": form.auto, "tariff.feed_in_full": form.full, "pv.commissioning_date": form.date,
          "pv.installed_kwp": kwp })) onSaved();
      }}>{t("common.save")}</Button>}
    </div>
  );
}

export function TariffPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<{ tariffs: TariffData[]; eeg: EegView }>("/api/tariffs");
  const eegActive = !!data?.eeg.auto && !!data.eeg.rate;
  const [forms, setForms] = useState<TariffForm[]>([]);
  const [busy, setBusy] = useState(false);
  const toForm = (t: TariffData): TariffForm => ({ ...t, price_ct: amountInput(t.price_ct), surcharge_ct: amountInput(t.surcharge_ct),
    vat_percent: de(t.vat_percent), feed_in_ct: amountInput(t.feed_in_ct), base_fee_eur_month: amountInput(t.base_fee_eur_month ?? 0),
    windows: t.windows ?? [] });
  useOnServerChange(data?.tariffs, (tariffs) => setForms(tariffs.map(toForm)));
  const update = (i: number, patch: Partial<TariffForm>) => setForms((f) => f.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const payload = (list: TariffForm[]) => list.map((t) => ({ ...t, price_ct: toNumber(t.price_ct) || 0,
    surcharge_ct: toNumber(t.surcharge_ct) || 0, vat_percent: toNumber(t.vat_percent) || 0, feed_in_ct: toNumber(t.feed_in_ct),
    base_fee_eur_month: toNumber(t.base_fee_eur_month) || 0 }));
  // by value, not by text: "13,70" saved as "13,7" is not a change (#244)
  const dirty = !!data && JSON.stringify(payload(forms)) !== JSON.stringify(payload(data.tariffs.map(toForm)));
  const valid = forms.length > 0 && forms.every((t) => t.valid_from && [t.feed_in_ct, t.kind === "dynamic" ? t.surcharge_ct : t.price_ct]
    .every((v) => Number.isFinite(toNumber(v))) && (t.kind !== "time" || t.windows.length > 0));
  const add = () => setForms((f) => [...f, { ...(f[f.length - 1] ?? { kind: "fixed", price_ct: "35,00", surcharge_ct: "20,00",
    vat_percent: "19", feed_in_ct: "8,00", area: "DE", base_fee_eur_month: "0,00", windows: [] }), valid_from: todayIso() } as TariffForm]);

  const save = async () => {
    setBusy(true);
    try {
      setData(await putJson<{ tariffs: TariffData[]; eeg: EegView }>("/api/tariffs", { tariffs: payload(forms) }));
      toast(t("common.saved"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title={t("common.electricityTariff")} onBack={onBack}>
      {!data && <LoadState error={error} onRetry={reload} />}
      <p className="hint">{t("settings.tariffPage.intro")}</p>
      {data && <EegCard eeg={data.eeg} onSaved={reload} />}
      {forms.map((tariff, i) => (
        <div className="card form" key={i}>
          <div className="field-row">
            <Field label={t("settings.tariffPage.validFrom")}><input className="input" type="date" value={tariff.valid_from}
              onChange={(e) => update(i, { valid_from: e.target.value })} /></Field>
            <Field label={t("settings.tariffPage.type")}>
              <select className="input" value={tariff.kind} onChange={(e) => update(i, { kind: e.target.value as TariffForm["kind"] })}>
                <option value="fixed">{t("settings.tariffPage.fixedPrice")}</option>
                <option value="time">{t("settings.tariffPage.timeOfUse")}</option>
                <option value="dynamic">{t("settings.tariffPage.dynamic")}</option>
              </select>
            </Field>
          </div>
          {tariff.kind !== "dynamic" ? (<>
            <Field label={tariff.kind === "time" ? t("settings.tariffPage.basePrice") : t("settings.tariffPage.priceGross")}
              hint={tariff.kind === "time" ? undefined : t("settings.tariffPage.priceHint")}>
              <div className="input-unit"><input className="input" inputMode="decimal" value={tariff.price_ct}
                onChange={(e) => update(i, { price_ct: e.target.value })} /><span>ct/kWh</span></div>
            </Field>
            {tariff.kind === "time" && <TimeWindows windows={tariff.windows} onChange={(windows) => update(i, { windows })} />}
          </>) : (
            <>
              <Field label={t("settings.tariffPage.surchargeGross")} hint={t("settings.tariffPage.surchargeHint")}>
                <div className="input-unit"><input className="input" inputMode="decimal" value={tariff.surcharge_ct}
                  onChange={(e) => update(i, { surcharge_ct: e.target.value })} /><span>ct/kWh</span></div>
              </Field>
              <div className="field-row">
                <Field label={t("settings.tariffPage.vat")}><div className="input-unit"><input className="input" inputMode="decimal"
                  value={tariff.vat_percent} onChange={(e) => update(i, { vat_percent: e.target.value })} /><span>%</span></div></Field>
                <Field label={t("settings.tariffPage.priceZone")}>
                  <select className="input" value={tariff.area} onChange={(e) => update(i, { area: e.target.value as TariffForm["area"] })}>
                    <option value="DE">{t("settings.tariffPage.germany")}</option><option value="AT">{t("settings.tariffPage.austria")}</option>
                  </select>
                </Field>
              </div>
            </>
          )}
          <Field label={t("common.standingCharge")} hint={t("settings.tariffPage.standingChargeHint")}>
            <div className="input-unit"><input className="input" inputMode="decimal" value={tariff.base_fee_eur_month}
              onChange={(e) => update(i, { base_fee_eur_month: e.target.value })} /><span>{t("settings.tariffPage.perMonthUnit")}</span></div>
          </Field>
          {!eegActive && <Field label={t("settings.tariffPage.feedInTariff")} hint={t("settings.tariffPage.feedInHint")}>
            <div className="input-unit"><input className="input" inputMode="decimal" value={tariff.feed_in_ct}
              onChange={(e) => update(i, { feed_in_ct: e.target.value })} /><span>ct/kWh</span></div>
          </Field>}
          {forms.length > 1 && <button type="button" className="link" onClick={() => setForms((f) => f.filter((_, j) => j !== i))}>{t("settings.tariffPage.removeTariff")}</button>}
        </div>
      ))}
      <Button variant="secondary" onClick={add}>{t("settings.tariffPage.addTariffChange")}</Button>
      <Button busy={busy} disabled={!valid || !dirty} onClick={save}>{t("common.save")}</Button>
      <Unsaved show={dirty} />
      {forms.some((tariff) => tariff.kind === "dynamic") && (
        <p className="hint">{t("settings.tariffPage.exchangePriceHint")}</p>
      )}
      <PriceChart />
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function ConnectionPage({ onBack, onNavigate }: PageProps) {
  const { settings, locked, lockedKeys, save, error, reload } = useSettings();
  const { data: status } = useResource<Status>("/api/status", 5000);
  const [pollInterval, setPollInterval] = useState(10);
  const [timeout, setTimeoutValue] = useState(3);
  const [mode, setMode] = useState<"persistent" | "per_poll">("persistent");
  const [registerMap, setRegisterMap] = useState("auto");
  const [readFunction, setReadFunction] = useState("auto");
  useEffect(() => {
    if (!settings) return;
    setPollInterval(settings["inverter.poll_interval"]);
    setTimeoutValue(settings["inverter.timeout"]);
    setMode(settings["inverter.connection_mode"]);
    setRegisterMap(settings["inverter.register_map"]);
    setReadFunction(settings["inverter.read_function"]);
  }, [settings]);

  return (
    <SubPage title={t("common.connection")} onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <Notice kind={status?.connected ? "ok" : "warn"}>
        {status?.connected
          ? t("settings.connectionPage.connectedTo", { manufacturer: status.device?.manufacturer ?? "", model: status.device?.model ?? "" })
          : status?.last_error ? t("settings.connectionPage.notConnectedError", { error: status.last_error }) : t("settings.connectionPage.notConnected")}
      </Notice>
      <div className="section-title">{t("common.otherDevices")}</div>
      <div className="card menu">
        <MenuRow label={t("settings.connectionPage.otherDevices")} hint={t("settings.connectionPage.otherDevicesHint")} onClick={() => onNavigate?.("device-setup")} />
        <MenuRow label={t("common.wallbox")} hint={t("settings.connectionPage.viaEvcc")} onClick={() => onNavigate?.("wallbox")} />
      </div>
      <div className="section-title">{t("settings.connectionPage.smartHome")}</div>
      <div className="card menu">
        <MenuRow label="Home Assistant" hint={t("settings.connectionPage.checkConnection")} onClick={() => onNavigate?.("apps")} />
      </div>
      <div className="section-title">{t("common.gridOperator")}</div>
      <div className="card menu">
        <MenuRow label={t("common.meterReadings")} hint={t("settings.connectionPage.gridMeterHint")} onClick={() => onNavigate?.("gridmeter")} />
      </div>
      <div className="section-title">{t("common.inverter")}</div>
      {settings && (
        <ConnectionForm
          key={settings["inverter.host"]}
          initial={{ host: settings["inverter.host"], port: settings["inverter.port"], unit: settings["inverter.unit"],
            driver: settings["inverter.driver"] }}
          locked={lockedKeys}
          onSaved={() => toast(t("settings.connectionPage.reconnecting"))}
        />
      )}
      {!status?.connected && <SetupHelp />}

      {status?.relocated && (
        <Notice kind="info">
          {t("settings.connectionPage.newIpFound",
            { when: updatedLabel(status.relocated.ts), from: status.relocated.from, to: status.relocated.to })}
        </Notice>
      )}

      {settings && (
        <>
          <div className="section-title">{t("common.advanced")}</div>
          <div className="card form">
            <Field label={t("settings.connectionPage.pollEvery")} hint={t("settings.connectionPage.pollHint")} locked={locked("inverter.poll_interval")}>
              <Slider value={pollInterval} min={5} max={60} unit="s" onChange={setPollInterval} />
            </Field>
            <Field label={t("settings.connectionPage.timeout")} locked={locked("inverter.timeout")}
              hint={t("settings.connectionPage.timeoutHint")}>
              <Slider value={timeout} min={1} max={30} unit="s" onChange={setTimeoutValue} />
            </Field>
            <Field label={t("common.connection")} locked={locked("inverter.connection_mode")}
              hint={mode === "per_poll"
                ? t("settings.connectionPage.perPollHint")
                : t("settings.connectionPage.permanentHint")}>
              <Segmented value={mode} onChange={setMode} disabled={locked("inverter.connection_mode")}
                options={[["persistent", t("settings.connectionPage.permanent")], ["per_poll", t("settings.connectionPage.perPoll")]]} />
            </Field>
            <Button variant="secondary"
              disabled={pollInterval === settings["inverter.poll_interval"] && timeout === settings["inverter.timeout"]
                && mode === settings["inverter.connection_mode"]}
              onClick={() => save({ "inverter.poll_interval": pollInterval, "inverter.timeout": timeout,
                "inverter.connection_mode": mode })}>{t("common.save")}</Button>
            <Unsaved show={!(pollInterval === settings["inverter.poll_interval"] && timeout === settings["inverter.timeout"]
              && mode === settings["inverter.connection_mode"])} />
          </div>

          {(status?.device?.driver ?? settings["inverter.driver"]) === "foxess" && (
            <details className="card expert">
              <summary>{t("settings.connectionPage.experts")}</summary>
              <p className="hint">{t("settings.connectionPage.expertHint")}</p>
              <Field label={t("settings.connectionPage.foxessRegisterMap")} locked={locked("inverter.register_map")}>
                <select className="input" value={registerMap} onChange={(e) => setRegisterMap(e.target.value)}>
                  <option value="auto">{t("common.detectAutomatically")}</option>
                  <option value="foxess_h3_new">{t("settings.connectionPage.foxessNewer")}</option>
                  <option value="foxess_h3_legacy">{t("settings.connectionPage.foxessOlder")}</option>
                </select>
              </Field>
              <Field label={t("settings.connectionPage.readMethod")} locked={locked("inverter.read_function")}>
                <select className="input" value={readFunction} onChange={(e) => setReadFunction(e.target.value)}>
                  <option value="auto">{t("common.automatic")}</option>
                  <option value="input">{t("settings.connectionPage.inputRegisters")}</option>
                  <option value="holding">{t("settings.connectionPage.holdingRegisters")}</option>
                </select>
              </Field>
              <Button variant="secondary"
                disabled={registerMap === settings["inverter.register_map"] && readFunction === settings["inverter.read_function"]}
                onClick={() => save({ "inverter.register_map": registerMap, "inverter.read_function": readFunction })}>
                {t("settings.connectionPage.saveAndReconnect")}
              </Button>
            </details>
          )}
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

/** "Heute", "Gestern" or the date with weekday, in the plant's time zone. */
function logDay(ts: number): string {
  const day = dayOf(ts), now = Date.now() / 1000;
  if (day === dayOf(now)) return t("common.today");
  if (day === dayOf(now - 86_400)) return t("shell.updatedLabel.yesterday");
  return new Date(ts * 1000).toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: timeZone() });
}

function LogRow({ entry }: { entry: LogEntry }) {
  const d = describe(entry);
  const clock = new Date(entry.ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });
  return (
    <div className={`log-row ${d.status}`}>
      <div>{d.text}</div>
      <div className="meta">
        {[clock, d.by].filter(Boolean).join(" · ")}
        {d.status !== "ok" && <span className="log-status"> · {statusLabel(d.status)}</span>}
      </div>
      {d.note && <div className="meta">{d.note}</div>}
    </div>
  );
}

export function ControlPage({ onBack }: PageProps) {
  const { data: log, reload } = useResource<{ entries: LogEntry[] }>("/api/control/log", 30_000);
  const days: [string, LogEntry[]][] = [];
  for (const e of log?.entries ?? []) {
    const day = logDay(e.ts);
    if (days.at(-1)?.[0] === day) days.at(-1)![1].push(e);
    else days.push([day, [e]]);
  }
  const exportLog = async () => {
    const { entries } = await getJson<{ entries: LogEntry[] }>("/api/control/log?limit=0");
    return new File([logCsv(entries)], `${t("settings.controlLog.fileName")}-${todayIso()}.csv`, { type: "text/csv;charset=utf-8" });
  };

  return (
    <SubPage title={t("common.controlLog")} onBack={onBack}>
      <ControlModeBar />
      <Notice kind="info">
        {t("settings.controlPage.overwriteHint")}
      </Notice>

      <div className="section-title">{t("settings.controlPage.log")}</div>
      <div className="card">
        {!log?.entries.length && <p className="hint">{t("settings.controlPage.emptyState")}</p>}
        {days.map(([day, entries]) => (
          <section key={day} className="log-day">
            <h3>{day}</h3>
            {entries.map((e, i) => <LogRow key={`${e.ts}-${i}`} entry={e} />)}
          </section>
        ))}
        {!!log?.entries.length && <button type="button" className="link" onClick={reload}>{t("settings.controlPage.refresh")}</button>}
      </div>
      {!!log?.entries.length && (
        <div className="card form">
          <p className="hint">{t("settings.controlLog.exportHint")}</p>
          <DownloadButton file={exportLog} label={t("settings.controlLog.export")} />
        </div>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export type Theme = "auto" | "light" | "dark";

const THEME_COLORS = { light: "#f4f4f4", dark: "#21262b" };

export function applyTheme(theme: Theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  // status bar colour of the installed app follows the chosen design
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
    const scheme = meta.media.includes("dark") ? "dark" : "light";
    meta.content = THEME_COLORS[theme === "auto" ? scheme : theme];
  });
}

export function storedTheme(): Theme {
  try {
    const value = localStorage.getItem("openampere.theme");
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    return "auto";
  }
}

const TIMEZONES = ["Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/Amsterdam", "Europe/Brussels",
  "Europe/Luxembourg", "Europe/Paris", "Europe/Rome", "Europe/Madrid", "Europe/Warsaw", "Europe/Prague", "Europe/London"];

export function AppearancePage({ onBack }: PageProps) {
  const { settings, locked, save } = useSettings();
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const change = (value: Theme) => {
    setTheme(value);
    applyTheme(value);
    try { localStorage.setItem("openampere.theme", value); } catch { /* private mode */ }
  };
  return (
    <SubPage title={t("common.appearance")} onBack={onBack}>
      <div className="card form">
        <Field label={t("settings.appearancePage.theme")} hint={t("settings.appearancePage.themeHint")}>
          <Segmented value={theme} onChange={change}
            options={[["auto", t("common.automatic")], ["light", t("settings.appearancePage.light")], ["dark", t("settings.appearancePage.dark")]]} />
        </Field>
        <Field label={t("settings.appearancePage.language")} hint={t("settings.appearancePage.languageHint")}>
          <select className="input" value={lang()} onChange={(e) => setLang(e.target.value as Lang)}>
            {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.complete ? l.name : `${l.name} (${t("settings.appearancePage.preview")})`}</option>)}
          </select>
        </Field>
      </div>
      {settings && (
        <div className="card form">
          <Field label={t("settings.appearancePage.timeZone")} locked={locked("timezone")}
            hint={t("settings.appearancePage.timeZoneHint")}>
            <select className="input" value={settings.timezone} disabled={locked("timezone")}
              onChange={(e) => void save({ timezone: e.target.value })}>
              {[...new Set([settings.timezone, ...TIMEZONES])].map((z) => <option key={z} value={z}>{z.replace("_", " ")}</option>)}
            </select>
          </Field>
        </div>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

/** Started from the home screen: downloads must open in their own window, the preview iOS shows in the app window
 * itself has no way back (#13). */
const IOS_HOME_SCREEN = (navigator as Navigator & { standalone?: boolean }).standalone === true;
const HOME_SCREEN_APP = window.matchMedia?.("(display-mode: standalone)").matches || IOS_HOME_SCREEN;
const downloadProps = HOME_SCREEN_APP ? { target: "_blank", rel: "noopener" } : { download: "" };
/** On the iPhone that own window only shows a blank page and saves nothing (#70): there the app fetches the file
 * itself and hands it to the share sheet ("In Dateien sichern"). */
const SHARE_FILES = IOS_HOME_SCREEN && (() => {
  try {
    return !!navigator.canShare?.({ files: [new File(["x"], "x.csv", { type: "text/csv" })] });
  } catch {
    return false;
  }
})();

/** Saves a file made in the browser, e.g. the control log as CSV. */
function saveFile(file: File) {
  const url = URL.createObjectURL(file);
  const link = Object.assign(document.createElement("a"), { href: url, download: file.name });
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** A file from the server (`href`) or made in the browser (`file`). */
function DownloadButton({ href, file, label }: { href?: string; file?: () => Promise<File>; label: string }) {
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState<File | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: a prepared file is dropped when the link changes
  useEffect(() => setReady(null), [href]);
  if (href && !SHARE_FILES) return <a className="btn secondary" href={href} {...downloadProps}>{label}</a>;

  const share = (file: File) => navigator.share({ files: [file] }).then(() => setReady(null), (err: Error) => {
    if (err.name === "NotAllowedError") setReady(file); // loading took too long for iOS: one more tap opens the sheet
    else {
      setReady(null);
      if (err.name !== "AbortError") toast(t("settings.downloadButton.shareFailed"), "error");
    }
  });
  const fetchFile = async (url: string) => {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : t("settings.downloadButton.error", { status: response.status }));
    }
    const name = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? "openampere";
    const blob = await response.blob();
    return new File([blob], name, { type: blob.type || "application/octet-stream" });
  };
  const load = async () => {
    setBusy(true);
    try {
      const made = file ? await file() : await fetchFile(href!);
      if (SHARE_FILES) await share(made);
      else saveFile(made);
    } catch (err) {
      toast(err instanceof TypeError || !(err instanceof Error) ? OFFLINE_MESSAGE : err.message, "error"); // TypeError: network
    } finally {
      setBusy(false);
    }
  };
  return ready ? <Button variant="secondary" onClick={() => void share(ready)}>{t("settings.downloadButton.saveOrShare")}</Button>
    : <Button variant="secondary" busy={busy} onClick={() => void load()}>{label}</Button>;
}

function CsvExportCard() {
  const thisYear = new Date().getFullYear();
  const [from, setFrom] = useState(`${thisYear}-01-01`);
  const [to, setTo] = useState(isoDate(new Date()));
  const [resolution, setResolution] = useState<"15m" | "60m" | "day" | "month">("day");
  const valid = !!from && !!to && from <= to;
  const href = `/api/export/csv?from=${from}&to=${to}&resolution=${resolution}`;
  return (
    <div className="card form">
      <h2>{t("settings.csvExportCard.title")}</h2>
      <p className="hint">{t("settings.csvExportCard.hint")}</p>
      <div className="field-row">
        <Field label={t("common.from")}><input className="input" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label={t("common.to")}><input className="input" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></Field>
      </div>
      <Segmented value={resolution} onChange={setResolution}
        options={[["15m", "15 min"], ["60m", t("settings.csvExportCard.hour")], ["day", t("common.day")], ["month", t("common.month")]]} />
      {DEMO ? <p className="hint">{t("settings.csvExportCard.notInDemo")}</p> : valid ? <DownloadButton href={href} label={t("settings.csvExportCard.downloadCsv")} />
        : <p className="hint">{t("settings.csvExportCard.invalidPeriod")}</p>}
    </div>
  );
}

/** The separate download window does not share the login cookie: it gets a link that is valid for 10 minutes. */
function BackupLink() {
  const [url, setUrl] = useState<string | null>(HOME_SCREEN_APP ? null : "/api/backup");
  useEffect(() => {
    if (!HOME_SCREEN_APP) return;
    const fetchLink = () => postJson<{ url: string }>("/api/backup/link", {}).then((r) => setUrl(r.url)).catch(() => setUrl(null));
    fetchLink();
    const timer = window.setInterval(fetchLink, 5 * 60_000);
    return () => window.clearInterval(timer);
  }, []);
  if (!url) return <Button variant="secondary" disabled>{t("common.downloadBackup")}</Button>;
  return <a className="btn secondary" href={url} {...downloadProps}>{t("common.downloadBackup")}</a>;
}

type StorageUsage = { db_bytes: number | null; free_bytes: number | null; samples: number; first_sample: number | null;
  bytes_per_year: number; retention_days: number };
const RETENTION: [number, string][] = [[30, t("settings.retention.days", { days: 30 })], [365, t("settings.retention.years", { count: 1 })],
  [1825, t("settings.retention.years", { count: 5 })], [3650, t("settings.retention.years", { count: 10 })], [0, t("settings.retention.unlimited")]];
const size = (bytes: number) => (bytes >= 1e9 ? `${num(bytes / 1e9, 1)} GB` : `${num(Math.max(bytes, 1e6) / 1e6, 0)} MB`);

/** What the chosen retention costs: detail readings need about 0.4 GB per year with a reading every 10 s. */
function storageHint(u: StorageUsage, days: number): string {
  // the server reports both sizes or neither
  const now = u.db_bytes == null ? null : u.free_bytes == null
    ? t("settings.storageHint.size", { size: size(u.db_bytes) })
    : t("settings.storageHint.sizeAndFree", { size: size(u.db_bytes), free: size(u.free_bytes) });
  const period = days >= 365 ? t("settings.storageHint.years", { count: Math.round(days / 365) }) : t("settings.storageHint.days", { days });
  const need = days > 0
    ? t("settings.storageHint.needed", { period, size: size(u.bytes_per_year * days / 365) })
    : t("settings.storageHint.growth", { size: size(u.bytes_per_year) });
  return now ? `${now} ${need}` : need;
}

export function DataPage({ onBack }: PageProps) {
  const { settings, locked, save, error, reload } = useSettings();
  const { data: auth } = useResource<AuthStatus>("/api/auth/status");
  const [days, setDays] = useState(30);
  const { data: usage } = useResource<StorageUsage>("/api/storage");
  const { data: status } = useResource<Status>("/api/status");
  useEffect(() => { if (settings) setDays(settings["storage.raw_retention_days"]); }, [settings]);
  const options: [string, string][] = RETENTION.map(([d, label]) => [String(d), label]);
  if (!RETENTION.some(([d]) => d === days)) options.unshift([String(days), t("settings.dataPage.days", { days })]);

  return (
    <SubPage title={t("common.dataBackup")} onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <div className="card form">
        <Field label={t("settings.dataPage.retention")} locked={locked("storage.raw_retention_days")}
          hint={t("settings.dataPage.retentionHint")}>
          <select className="input" value={String(days)} disabled={locked("storage.raw_retention_days")}
            onChange={(e) => setDays(Number(e.target.value))}>
            {options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </Field>
        {usage && <p className="hint">{storageHint(usage, days)}</p>}
        <Button variant="secondary" disabled={!settings || days === settings["storage.raw_retention_days"]}
          onClick={() => save({ "storage.raw_retention_days": days })}>{t("common.save")}</Button>
        <Unsaved show={!!settings && days !== settings["storage.raw_retention_days"]} />
      </div>
      <CloudImportCard />
      <CsvExportCard />
      <div className="card form">
        <h2>{t("settings.dataPage.backup")}</h2>
        <p className="hint">{t("settings.dataPage.backupHint")}</p>
        {DEMO ? <p className="hint">{t("settings.dataPage.notInDemo")}</p> : auth?.authenticated ? (
          SHARE_FILES ? <DownloadButton href="/api/backup" label={t("common.downloadBackup")} /> : <BackupLink />
        ) : (
          <Button variant="secondary" onClick={() => window.dispatchEvent(new CustomEvent("openampere:auth", { detail: "login_required" }))}>
            {t("settings.dataPage.logInToDownload")}
          </Button>
        )}
      </div>
      <div className="card form">
        <h2>{t("settings.restoreCard.title")}</h2>
        <p className="hint">{t("settings.restoreCard.hint")}</p>
        {DEMO ? <p className="hint">{t("settings.dataPage.notInDemo")}</p> : auth?.authenticated ? <RestoreButton /> : (
          <Button variant="secondary" onClick={() => window.dispatchEvent(new CustomEvent("openampere:auth", { detail: "login_required" }))}>
            {t("settings.restoreCard.logInToRestore")}
          </Button>
        )}
        <LastRestore restored={status?.database.restored ?? null} />
      </div>
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function AboutPage({ onBack, onNavigate }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  return (
    <SubPage title={t("common.aboutOpenampere")} onBack={onBack}>
      <div className="card">
        <dl className="facts">
          <dt>{t("settings.aboutPage.version")}</dt><dd>{status?.version ?? "–"}</dd>
        </dl>
      </div>
      {!DEMO && <UpdatesCard />}
      <div className="card">
        <p>{t("settings.aboutPage.intro")}</p>
        <p className="hint">{t("settings.aboutPage.trademarks")}</p>
        <p className="hint">{t("settings.aboutPage.foxessCredit")}</p>
        <p className="hint">{tx("settings.aboutPage.evccCredit", { link: <a href="https://evcc.io" target="_blank" rel="noreferrer">evcc</a> })}</p>
        {!DEMO && <p className="hint">{t("settings.aboutPage.selfHosted")}</p>}
      </div>
      <div className="card menu">
        <a className="menu-row" href={IMPRINT_URL} target="_blank" rel="noopener noreferrer">
          <span>{t("settings.aboutPage.projectWebsite")}<span className="menu-hint">{t("settings.aboutPage.projectWebsiteHint")}</span></span><span aria-hidden>↗</span>
        </a>
        <a className="menu-row" href={REPO_URL} target="_blank" rel="noopener noreferrer">
          <span>{t("settings.aboutPage.sourceCode")}<span className="menu-hint">github.com/Gr33ndev/OpenAmpere</span></span><span aria-hidden>↗</span>
        </a>
        <a className="menu-row" href={ISSUES_URL} target="_blank" rel="noopener noreferrer">
          <span>{t("settings.aboutPage.reportBugs")}<span className="menu-hint">GitHub Issues</span></span><span aria-hidden>↗</span>
        </a>
        <button type="button" className="menu-row" onClick={() => onNavigate?.("changelog")}>
          <span>{t("settings.changelogPage.title")}<span className="menu-hint">{t("settings.aboutPage.changelogHint")}</span></span><Chevron />
        </button>
        <button type="button" className="menu-row" onClick={() => onNavigate?.("licenses")}>
          <span>{t("common.openSourceLicenses")}<span className="menu-hint">{t("settings.aboutPage.licensesHint")}</span></span><Chevron />
        </button>
      </div>
      <p className="hint center">{t("settings.aboutPage.license")}</p>
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

function duration(seconds: number): string {
  const total = Math.round(seconds / 60); // never "1 h 60 min"
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h ? t("settings.duration.hoursMinutes", { h, m }) : t("settings.duration.minutes", { m });
}

function CloudImportCard() {
  const { secrets, locked, save } = useSettings();
  const { data: job, reload } = useResource<CloudImportState>("/api/import/cloud", 5000);
  const [key, setKey] = useState("");
  const [editing, setEditing] = useState(false);
  const [removingKey, setRemovingKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [upload, setUpload] = useState<string | null>(null);
  const keyInfo = secrets?.["cloud.api_key"];
  const keyLocked = locked("cloud.api_key");
  const showKeyForm = !keyLocked && (editing || (keyInfo && !keyInfo.set));

  const saveKey = async (value: string) => {
    if (await save({ "cloud.api_key": value })) {
      setKey("");
      setEditing(false);
      reload();
    }
  };
  const action = async (path: string) => {
    setBusy(true);
    try {
      await postJson(path, {});
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const uploadZip = async (file: File) => {
    setUpload(t("settings.cloudImportCard.readingFile"));
    try {
      const data = await postFile<{ days: number; inserted: number }>("/api/import/cloud/file", file);
      setUpload(t("settings.cloudImportCard.fileResult", { days: data.days, inserted: data.inserted }));
      toast(t("settings.cloudImportCard.importComplete"));
    } catch (e) {
      setUpload(null);
      toast((e as Error).message, "error");
    }
  };

  const total = job?.days_total ?? 0;
  const done = (job?.work_done ?? 0) + (job?.soc_done ?? 0);
  const progress = total ? done / (2 * total) : 0;
  // two steps, the percentage covers both: energy values first, then the battery's state of charge (#17)
  const phaseText = !job?.phase ? t("settings.cloudImportCard.connecting")
    : job.phase === "search" ? t("settings.cloudImportCard.findingStart")
    : job?.phase === "soc" ? t("settings.cloudImportCard.stepBattery", { done: job.soc_done ?? 0, total })
    : t("settings.cloudImportCard.stepEnergy", { done: job?.work_done ?? 0, total });

  return (
    <>
      <div className="section-title">{t("settings.cloudImportCard.title")}</div>
      <div className="card form">
        <p className="hint">{tx("settings.cloudImportCard.intro", { menu: <strong>Mehr → Konfiguration API-Zugang</strong> })}</p>
        <p className="hint"><strong>{t("settings.cloudImportCard.independent")}</strong></p>
        <LearnMore summary={t("settings.cloudImportCard.learnMore")}>
          <p className="hint">{t("settings.cloudImportCard.disclaimer")}</p>
        </LearnMore>

        {showKeyForm ? (
          <form className="field" onSubmit={(e) => { e.preventDefault(); void saveKey(key.trim()); }}>
            <span className="field-label">{t("settings.cloudImportCard.apiKey")}</span>
            <input className="input" type="password" autoComplete="off" spellCheck={false} value={key}
              onChange={(e) => setKey(e.target.value)} placeholder={t("settings.cloudImportCard.keyPlaceholder")} />
            <span className="field-hint">{t("settings.cloudImportCard.keyHint")}</span>
            <div className="button-row">
              <Button type="submit" disabled={!key.trim()}>{t("settings.cloudImportCard.saveKey")}</Button>
              {editing && <Button variant="secondary" onClick={() => { setEditing(false); setKey(""); }}>{t("common.cancel")}</Button>}
            </div>
          </form>
        ) : (
          <div className="key-row">
            <span>{keyInfo?.set
              ? tx("settings.cloudImportCard.keyStored", { status: <strong>{t("settings.cloudImportCard.keyStoredStatus", { hint: keyInfo.hint ?? "" })}</strong> })
              : t("settings.cloudImportCard.keyMissing")}
              {keyLocked && <span className="lock">{t("common.fixedSetting")}</span>}</span>
            {!keyLocked && (
              <span className="key-actions">
                <button type="button" className="link" onClick={() => setEditing(true)}>{t("common.change")}</button>
                {keyInfo?.set && <button type="button" className="link" onClick={() => setRemovingKey(true)}>{t("common.remove")}</button>}
              </span>
            )}
          </div>
        )}

        {removingKey && (
          <Dialog title={t("settings.cloudImportCard.removeKeyQuestion")} confirm={t("common.remove")} danger
            onCancel={() => setRemovingKey(false)} onConfirm={() => { setRemovingKey(false); void saveKey(""); }}>
            <p>{t("settings.cloudImportCard.removeKeyHint")}</p>
          </Dialog>
        )}
        {job && job.status !== "idle" && (
          <div className="import-status">
            <div className="ratio-head">
              <span>{job.status === "done" ? t("settings.cloudImportCard.importComplete") : job.status === "paused" ? t("settings.cloudImportCard.paused") : job.status === "error" ? t("settings.cloudImportCard.stopped") : phaseText}</span>
              {total > 0 && <strong className="nowrap">{t("settings.cloudImportCard.overall", { percent: Math.round(progress * 100) })}</strong>}
            </div>
            {total > 0 && <div className="bar"><div className="bar-fill" style={{ width: `${progress * 100}%` }} /></div>}
            {job.start && <div className="field-hint">{t("settings.cloudImportCard.period", { from: new Date(job.start).toLocaleDateString(LOCALE),
              to: new Date(job.end ?? job.start).toLocaleDateString(LOCALE) })}
              {job.imported ? ` · ${t("settings.cloudImportCard.imported", { count: job.imported.toLocaleString(LOCALE) })}` : ""}</div>}
            {job.status === "running" && job.eta_seconds ? <div className="field-hint">{t("settings.cloudImportCard.timeLeft", { time: duration(job.eta_seconds) })}</div> : null}
          </div>
        )}
        {job?.notice && job.status === "running" && <Notice kind="warn">{job.notice}</Notice>}
        {job?.status === "error" && job.error && <Notice kind="error">{job.error}</Notice>}

        {job?.status === "running" ? (
          <Button variant="secondary" busy={busy} onClick={() => void action("/api/import/cloud/stop")}>{t("settings.cloudImportCard.pause")}</Button>
        ) : (
          <Button busy={busy} disabled={!job?.key_set} onClick={() => void action("/api/import/cloud/start")}>
            {job?.status === "paused" ? t("settings.cloudImportCard.resume") : job?.status === "error" ? t("settings.cloudImportCard.tryAgain") : job?.status === "done" ? t("settings.cloudImportCard.syncAgain") : t("settings.cloudImportCard.startImport")}
          </Button>
        )}
        {job && !job.key_set && <p className="field-hint">{t("settings.cloudImportCard.saveKeyFirst")}</p>}
        <p className="hint">
          {t("settings.cloudImportCard.durationHint")}
        </p>
      </div>

      <div className="card form">
        <h2>{t("settings.cloudImportCard.fromExportFile")}</h2>
        <p className="hint">{t("settings.cloudImportCard.exportFileHint")}</p>
        <label className="btn secondary file-button">
          {t("settings.cloudImportCard.chooseZipFile")}
          <input type="file" accept=".zip,application/zip" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadZip(f); e.target.value = ""; }} />
        </label>
        {upload && <Notice kind="ok">{upload}</Notice>}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------

type LicensePackage = { name: string; version?: string; license: string; url?: string; texts: string[] };
type LicenseData = { self: LicensePackage; groups: { title: string; packages: LicensePackage[] }[] };

function LicenseRow({ pkg }: { pkg: LicensePackage }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="license-item">
      <button type="button" className="menu-row" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{pkg.name}{pkg.version && <span className="menu-hint">{t("settings.licenseRow.version", { version: pkg.version })}</span>}</span>
        <span className="license-meta">
          <span className="pill">{pkg.license || t("settings.licenseRow.seeText")}</span>
          <span className={`chevron ${open ? "open" : ""}`} aria-hidden>›</span>
        </span>
      </button>
      {open && (
        <div className="license-body">
          {pkg.url && <a className="link" href={pkg.url.replace(/^git\+/, "")} target="_blank" rel="noopener noreferrer">{t("settings.licenseRow.projectPage")}</a>}
          {pkg.texts.length ? pkg.texts.map((text, i) => <pre key={i} className="license-text">{text}</pre>)
            : <p className="hint">{t("settings.licenseRow.license", { license: pkg.license })}</p>}
        </div>
      )}
    </div>
  );
}

type ChangeItem = { scope: string | null; text: string };
type ChangelogVersion = { version: string; date: string; first: boolean; breaking: ChangeItem[];
  groups: Record<"feat" | "fix" | "perf" | "other", ChangeItem[]>; summary?: Record<string, string> };

/** The maintainer's summary of a version (#176) in the app's language, else German, else English. */
function Summary({ summary }: { summary?: Record<string, string> }) {
  const code = [lang(), "de", "en"].find((c) => summary?.[c]);
  if (!summary || !code) return null;
  return (
    <div className="changelog-summary" lang={code}>
      {summary[code].split(/\n\s*\n/).map((paragraph, i) => <p key={i}>{withLinks(paragraph)}</p>)}
    </div>
  );
}

/** "(#152)" in a commit title links to the pull request or issue. */
function withLinks(text: string) {
  return text.split(/(#\d+)/).map((part, i) => (/^#\d+$/.test(part)
    ? <a key={i} href={`${ISSUES_URL}/${part.slice(1)}`} target="_blank" rel="noopener noreferrer">{part}</a> : part));
}

function ChangeList({ items }: { items: ChangeItem[] }) {
  return (
    <ul className="changes">
      {items.map((item, i) => <li key={i}>{item.scope && <strong>{item.scope}: </strong>}{withLinks(item.text)}</li>)}
    </ul>
  );
}

/** Every version with its changes, from the commit titles (#155). */
export function ChangelogPage({ onBack }: PageProps) {
  const { data, error, reload } = useResource<{ versions: ChangelogVersion[] }>(CHANGELOG_URL);
  const { data: status } = useResource<Status>("/api/status");
  const sections = [["feat", t("settings.changelogPage.new")], ["fix", t("settings.changelogPage.fixed")],
    ["perf", t("settings.changelogPage.faster")], ["other", t("settings.changelogPage.otherChanges")]] as const;
  return (
    <SubPage title={t("settings.changelogPage.title")} onBack={onBack}>
      {!data && <LoadState error={error} onRetry={reload} />}
      {data && <p className="hint">{t("settings.changelogPage.intro")}</p>}
      {data?.versions.map((v) => (
        <div className="card changelog" key={v.version}>
          <div className="changelog-head">
            <h2>{t("settings.changelogPage.version", { version: v.version })}</h2>
            {v.version === status?.version && <span className="badge">{t("settings.changelogPage.installed")}</span>}
          </div>
          <p className="meta">{new Date(`${v.date}T12:00:00Z`).toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric" })}</p>
          <Summary summary={v.summary} />
          {v.first && <p>{t("settings.changelogPage.first")}</p>}
          {v.breaking.length > 0 && <><h3>{t("settings.changelogPage.attention")}</h3><ChangeList items={v.breaking} /></>}
          {sections.map(([group, title]) => v.groups[group].length > 0 && (
            <Fragment key={group}><h3>{title}</h3><ChangeList items={v.groups[group]} /></Fragment>
          ))}
        </div>
      ))}
    </SubPage>
  );
}

export function LicensesPage({ onBack }: PageProps) {
  const { data, error, reload } = useResource<LicenseData>(LICENSES_DATA_URL);
  return (
    <SubPage title={t("common.openSourceLicenses")} onBack={onBack}>
      {!data && <LoadState error={error} onRetry={reload} />}
      {data && (
        <>
          <p className="hint">
            {t("settings.licensesPage.intro")}
          </p>
          <div className="card menu"><LicenseRow pkg={data.self} /></div>
          {data.groups.map((g) => (
            <div key={g.title}>
              <div className="section-title">{g.title}</div>
              <div className="card menu">
                {g.packages.map((p) => <LicenseRow key={p.name} pkg={p} />)}
              </div>
            </div>
          ))}
        </>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

const watt = (w: number | null | undefined) => (w == null ? "–" : `${w.toLocaleString(LOCALE)} W`);

const FEED_IN_RULES: { id: FeedInRule; label: string; hint: string }[] = [
  { id: "limit_60", label: t("settings.feedInRules.percentOfPv", { percent: 60 }),
    hint: t("settings.feedInRules.limit60Hint") },
  { id: "limit_70", label: t("settings.feedInRules.percentOfPv", { percent: 70 }),
    hint: t("settings.feedInRules.limit70Hint") },
  { id: "operator", label: t("settings.feedInRules.operator"),
    hint: t("settings.feedInRules.operatorHint") },
  { id: "none", label: t("common.noLimit"),
    hint: t("settings.feedInRules.noneHint") },
  { id: "unknown", label: t("settings.feedInRules.unknown"),
    hint: t("settings.feedInRules.unknownHint") },
];

function FeedInRuleCard({ onSaved }: { onSaved: () => void }) {
  const { settings, save, locked } = useSettings();
  const [kwp, setKwp] = useState("");
  const [declareNone, setDeclareNone] = useState(false);
  const [declared, setDeclared] = useState(false);
  useEffect(() => { if (settings) setKwp(settings["pv.installed_kwp"] ? String(settings["pv.installed_kwp"]).replace(".", ",") : ""); }, [settings]);
  if (!settings) return null;
  const kwpValue = Number(kwp.replace(",", "."));
  const kwpValid = kwp.trim() === "" || (Number.isFinite(kwpValue) && kwpValue >= 0 && kwpValue <= 1000);
  const kwpChanged = kwpValid && (kwp.trim() === "" ? 0 : kwpValue) !== settings["pv.installed_kwp"];
  const rule = settings["grid.feed_in_rule"];
  const pick = async (id: FeedInRule) => {
    if (id === rule) return;
    if (id === "none") { setDeclared(false); setDeclareNone(true); return; }
    if (await save({ "grid.feed_in_rule": id })) onSaved();
  };

  return (
    <>
      <div className="section-title">{t("settings.feedInRuleCard.title")}</div>
      <div className="card form">
        <Field label={t("settings.feedInRuleCard.installedPvCapacity")} locked={locked("pv.installed_kwp")}
          hint={t("settings.feedInRuleCard.pvCapacityHint")}>
          <div className="input-unit">
            <input className="input" inputMode="decimal" value={kwp} placeholder={t("common.kwpPlaceholder")} disabled={locked("pv.installed_kwp")}
              onChange={(e) => setKwp(e.target.value)} />
            <span>kWp</span>
          </div>
        </Field>
        {kwpChanged && (
          <Button onClick={async () => { if (await save({ "pv.installed_kwp": kwp.trim() === "" ? 0 : kwpValue })) onSaved(); }}>
            {t("settings.feedInRuleCard.savePvCapacity")}
          </Button>
        )}
      </div>
      <div className="section-title">{t("settings.feedInRuleCard.ruleQuestion")}</div>
      <div className="card choices">
        {FEED_IN_RULES.map((r) => (
          <button type="button" key={r.id} className={`choice ${rule === r.id ? "active" : ""}`} disabled={locked("grid.feed_in_rule")}
            onClick={() => void pick(r.id)}>
            <span className="radio" />
            <span><strong>{r.label}</strong><span className="meta">{r.hint}</span></span>
          </button>
        ))}
      </div>
      {declareNone && (
        <Dialog title={t("settings.feedInRuleCard.declareTitle")} danger confirm={t("settings.feedInRuleCard.submitDeclaration")} disabled={!declared}
          onCancel={() => setDeclareNone(false)}
          onConfirm={async () => { setDeclareNone(false); if (await save({ "grid.feed_in_rule": "none" })) onSaved(); }}>
          <p>{t("settings.feedInRuleCard.declareHint")}</p>
          <Checkbox checked={declared} onChange={setDeclared}>
            {tx("settings.feedInRuleCard.declaration", { neither: <strong>{t("settings.feedInRuleCard.neitherLawNorGrid")}</strong> })}
          </Checkbox>
          <p className="hint">{t("settings.feedInRuleCard.declarationLogged")}</p>
        </Dialog>
      )}
    </>
  );
}

export function ExportLimitPage({ onBack }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data: current, error, reload, setData } = useResource<ExportLimit>("/api/grid/export-limit");
  const [preset, setPreset] = useState<"max" | "custom">("custom");
  const [custom, setCustom] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [reference, setReference] = useState("");
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);

  const rated = current?.rated_power_w ?? null;
  const rule = current?.rule ?? "unknown";
  const kwp = current?.installed_kwp || 0;
  // highest value allowed: the declared legal share of the module power, never more than the inverter can do
  const legalMax = current?.legal_max_w ?? null;
  const cap = legalMax != null ? Math.min(legalMax, rated ?? legalMax) : rated;
  const hasPreset = cap != null && (legalMax != null || rule === "none");
  const presetLabel = rule === "none" ? t("common.noLimit") : `${rule === "limit_70" ? 70 : 60} % (${watt(cap)})`;

  // start from the current value, so nothing is "changed" (and no warning shown) until the user picks something
  useEffect(() => {
    if (!current?.supported || current.limit_w == null) return;
    setPreset(hasPreset && current.limit_w === cap ? "max" : "custom");
    setCustom(String(current.limit_w));
  }, [current, hasPreset, cap]);

  const target = preset === "max" && cap != null ? cap : parseWatts(custom); // "5.000" is 5000 W, not 5 W (#223)
  const valid = Number.isFinite(target) && target >= 0 && target <= (cap ?? 99_999);
  const raising = current?.limit_w == null || (valid && target > current.limit_w);
  const needsConsent = raising && legalMax == null && rule !== "none";
  const unchanged = valid && target === current?.limit_w;
  const editable = !!status?.control.enabled && !!current?.supported;
  const canSubmit = editable && valid && !unchanged && (!needsConsent || (confirmed && reference.trim().length >= 3));
  const pct = (w: number | null | undefined) =>
    (kwp && w != null ? ` (${t("settings.exportLimitPage.percentOfPv", { percent: Math.round(w / (kwp * 10)) })})` : "");

  const submit = async () => {
    setDialog(false);
    setBusy(true);
    try {
      const r = await putJson<{ dry_run: boolean; result?: string }>("/api/grid/export-limit", {
        limit_w: target, grid_operator_confirmed: confirmed, confirmation_reference: reference,
      });
      toast(r.dry_run ? t("settings.exportLimitPage.testModeLogged") : r.result === "ok" ? t("settings.exportLimitPage.changed") : r.result ?? t("common.saved"));
      setConfirmed(false);
      setReference("");
      await readBack("/api/grid/export-limit", setData, reload); // busy until the value read back is shown (#244)
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title={t("common.exportLimit")} onBack={onBack}>
      <Notice kind="warn"><strong>{t("settings.exportLimitPage.legalLabel")}</strong>{" "}{t("settings.exportLimitPage.penaltyWarning")}</Notice>
      <LearnMore>
        <p className="hint">{t("settings.exportLimitPage.legalHint")}</p>
      </LearnMore>

      <FeedInRuleCard onSaved={reload} />

      {!current ? <LoadState error={error} onRetry={reload} /> : (
        <>
          <div className="section-title">{t("settings.exportLimitPage.current")}</div>
          <div className="card">
            {current.supported ? (
              <>
                <div className="big-value">{watt(current.limit_w)}</div>
                <p className="hint">
                  {kwp ? t("settings.exportLimitPage.percentOfKwp", { percent: Math.round((current.limit_w ?? 0) / (kwp * 10)), kwp: kwp.toLocaleString(LOCALE) })
                    : t("settings.exportLimitPage.pvCapacityMissing")}
                  {rated ? ` · ${t("settings.exportLimitPage.inverterMax", { power: watt(rated) })}` : ""}
                </p>
                {legalMax != null && current.limit_w != null && current.limit_w > legalMax && (
                  <Notice kind="error">{t("settings.exportLimitPage.aboveRule", { max: watt(legalMax) })}</Notice>
                )}
              </>
            ) : (
              <p className="hint">{t("settings.exportLimitPage.notSupported")}</p>
            )}
          </div>

          {current.supported && (
            <>
              <div className="section-title">{t("settings.exportLimitPage.newLimit")}</div>
              {(rule === "limit_60" || rule === "limit_70") && !kwp && (
                <Notice kind="info">{t("settings.exportLimitPage.enterPvCapacity")}</Notice>
              )}
              <ControlModeBar compact />
              <div className="card form">
                {hasPreset && (
                  <Segmented value={preset} onChange={setPreset} disabled={!editable}
                    options={[["max", presetLabel], ["custom", t("settings.exportLimitPage.customValue")]]} />
                )}
                {preset === "custom" && (
                  <Field label={t("settings.exportLimitPage.maxFeedIn")} hint={cap != null ? t("settings.exportLimitPage.atMost", { max: watt(cap) }) : undefined}>
                    <div className="input-unit">
                      <input className="input" inputMode="numeric" value={custom} disabled={!editable}
                        onChange={(e) => setCustom(e.target.value)} placeholder={cap != null ? `0 – ${cap}` : t("settings.exportLimitPage.wattPlaceholder")} />
                      <span>W</span>
                    </div>
                  </Field>
                )}
                {valid && !unchanged && <p className="hint">{t("settings.exportLimitPage.newLabel")}{" "}<strong>{watt(target)}</strong>{pct(target)}</p>}
                {!valid && custom.trim() !== "" && cap != null && <p className="hint">{t("settings.exportLimitPage.allowedRange", { max: watt(cap) })}</p>}
                {unchanged && <p className="hint">{t("settings.exportLimitPage.unchanged")}</p>}
              </div>

              {editable && valid && !unchanged && needsConsent && (
                <>
                  <Notice kind="error">
                    <strong>{t("settings.exportLimitPage.increasing")}</strong>{" "}{t("settings.exportLimitPage.consentNeeded")}
                  </Notice>
                  <div className="card form">
                    <Checkbox checked={confirmed} onChange={setConfirmed} disabled={!editable}>
                      {tx("settings.exportLimitPage.consentCheckbox", { consent: <strong>{t("settings.exportLimitPage.writtenConsent")}</strong> })}
                    </Checkbox>
                    <Field label={t("settings.exportLimitPage.consentReference")} hint={t("settings.exportLimitPage.referenceHint")}>
                      <input className="input" value={reference} disabled={!editable} maxLength={200}
                        onChange={(e) => setReference(e.target.value)} placeholder={t("settings.exportLimitPage.referencePlaceholder")} />
                    </Field>
                  </div>
                </>
              )}

              {editable && (
                <Button variant={needsConsent ? "danger" : "primary"} busy={busy} disabled={!canSubmit} onClick={() => setDialog(true)}>
                  {t("settings.exportLimitPage.changeExportLimit")}
                </Button>
              )}
            </>
          )}

          <Notice kind="info">
            {t("settings.exportLimitPage.smartboxHint")}
          </Notice>
        </>
      )}

      {dialog && current && (
        <Dialog title={t("settings.exportLimitPage.confirmTitle")} danger={needsConsent}
          confirm={needsConsent ? t("settings.exportLimitPage.confirmButton") : t("common.change")} onCancel={() => setDialog(false)} onConfirm={() => void submit()}>
          <p>{t("settings.exportLimitPage.beforeLabel")}{" "}<strong>{watt(current.limit_w)}</strong>{pct(current.limit_w)}<br />{t("settings.exportLimitPage.newLabel")}{" "}<strong>{watt(target)}</strong>{pct(target)}</p>
          {needsConsent && <p>{t("settings.exportLimitPage.confirmConsent", { reference: reference.trim() })}</p>}
          <p>{t("settings.exportLimitPage.responsibility")}</p>
        </Dialog>
      )}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

export function PvSystemPage({ onBack, snap }: PageProps & { snap: Snapshot | null }) {
  const { settings, save, error, reload } = useSettings();
  const [names, setNames] = useState<string[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  // every input the inverter reports, also unconnected and hidden ones, so they can be hidden or shown again (#58)
  const inputs = (snap?.pv_inputs ?? []).map((input, index) => ({ ...input, index }));
  useEffect(() => {
    if (settings) { setNames(settings["pv.input_names"]); setHidden(settings["pv.hidden_inputs"] ?? []); }
  }, [settings]);
  const toggle = (i: number, show: boolean) =>
    setHidden((h) => (show ? h.filter((x) => x !== String(i + 1)) : [...h, String(i + 1)].sort()));
  const dirty = !!settings && (JSON.stringify(names) !== JSON.stringify(settings["pv.input_names"])
    || JSON.stringify(hidden) !== JSON.stringify(settings["pv.hidden_inputs"] ?? []));
  const count = Math.max(inputs.length, names.length, ...hidden.map(Number).filter(Number.isFinite));
  const setName = (i: number, value: string) => setNames((n) => {
    const next = [...n];
    while (next.length <= i) next.push("");
    next[i] = value;
    return next;
  });

  return (
    <SubPage title={t("common.pvSystem")} onBack={onBack}>
      {!settings && <LoadState error={error} onRetry={reload} />}
      <p className="hint">{t("settings.pvSystemPage.intro")}</p>
      {settings && (count === 0 ? (
        <Notice kind="info">{t("settings.pvSystemPage.noInputs")}</Notice>
      ) : (
        <div className="card form">
          {Array.from({ length: count }, (_, i) => {
            const live = inputs.find((x) => x.index === i);
            return (
              <div key={i} className="pv-input-row">
                <Field label={t("settings.pvSystemPage.input", { number: i + 1 })}
                  hint={live ? t("settings.pvSystemPage.livePower", { power: kw(live.power), voltage: num(live.voltage, 0) }) : t("settings.pvSystemPage.noPower")}>
                  <div className="input-unit">
                    <span className="dot" style={{ background: PV_INPUT_COLORS[i % 4] }} />
                    <input className="input" maxLength={30} value={names[i] ?? ""} placeholder={t("settings.pvSystemPage.stringNumber", { number: i + 1 })}
                      disabled={hidden.includes(String(i + 1))} onChange={(e) => setName(i, e.target.value)} />
                  </div>
                </Field>
                <SwitchRow label={t("settings.pvSystemPage.show")} checked={!hidden.includes(String(i + 1))} onChange={(v) => toggle(i, v)} />
              </div>
            );
          })}
          <Button disabled={!dirty}
            onClick={() => save({ "pv.input_names": names.slice(0, 6), "pv.hidden_inputs": hidden })}>{t("common.save")}</Button>
          <Unsaved show={dirty} />
          {hidden.length > 0 && <p className="hint">{t("settings.pvSystemPage.hiddenHint")}</p>}
        </div>
      ))}
    </SubPage>
  );
}

// ---------------------------------------------------------------------------

type ChargingSettings = { enabled: boolean; mode: "cheapest" | "window"; target_soc: number; ready_by: number;
  window_start: number; window_end: number; max_price_ct: number | null; power_w: number; battery_kwh: number;
  legal_confirmed: boolean };
export type ChargingView = { settings: ChargingSettings; active: boolean; last_error: string | null;
  plan: { quarters: number[]; reason: string; needed_wh?: number; prices?: Record<string, number> } };

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;

/** Consecutive quarter hours as readable ranges: "02:00–03:30". */
function ranges(quarters: number[]): string[] {
  const out: string[] = [];
  const fmt = (ts: number) => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });
  let start = quarters[0];
  for (let i = 1; i <= quarters.length; i++) {
    if (i === quarters.length || quarters[i] !== quarters[i - 1] + 900) {
      out.push(`${fmt(start)}–${fmt(quarters[i - 1] + 900)}`);
      start = quarters[i];
    }
  }
  return out;
}

export function ChargingPage({ onBack }: PageProps) {
  const { data: status } = useResource<Status>("/api/status");
  const { data, error, reload, setData } = useResource<ChargingView>("/api/charging", 30_000);
  const [form, setForm] = useState<ChargingSettings | null>(null);
  const [legal, setLegal] = useState(false);
  const [busy, setBusy] = useState(false);
  useOnServerChange(data?.settings, setForm);
  const rated = status?.device?.rated_power_w ?? 10_000;
  const { settings, save: saveSettings } = useSettings();
  const [batteryMax, setBatteryMax] = useState("");
  useEffect(() => { if (settings) setBatteryMax(settings["battery.max_charge_kw"] ? de(settings["battery.max_charge_kw"]) : ""); }, [settings]);
  const batteryMaxKw = batteryMax.trim() ? toNumber(batteryMax) : 0;
  // levels like the former app: 50 / 75 / 100 % of what the battery allows (datasheet), at most the inverter (#23)
  const base = Math.min(rated, batteryMaxKw > 0 ? batteryMaxKw * 1000 : rated);
  const level = (share: number) => Math.round((base * share) / 100) * 100;
  const set = (patch: Partial<ChargingSettings>) => setForm((f) => (f ? { ...f, ...patch } : f));
  const maxChanged = !!settings && batteryMaxKw !== (settings["battery.max_charge_kw"] ?? 0);
  const changed = (form && data && JSON.stringify(form) !== JSON.stringify(data.settings)) || maxChanged;

  const save = async (next: ChargingSettings) => {
    setBusy(true);
    try {
      if (maxChanged && !(await saveSettings({ "battery.max_charge_kw": Number.isFinite(batteryMaxKw) ? batteryMaxKw : 0 }))) return;
      setData(await putJson<ChargingView>("/api/charging", next));
      toast(t("common.saved"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title={t("settings.chargingPage.title")} onBack={onBack}>
      <p className="hint">{t("settings.chargingPage.intro")}{" "}
        <strong>{t("settings.chargingPage.experimental")}</strong></p>
      <LearnMore>
        <p className="hint">{t("settings.chargingPage.remoteControlHint")}</p>
      </LearnMore>
      <ControlModeBar compact />
      {!form && <LoadState error={error} onRetry={reload} />}
      {form && data && (
        <>
          <div className="card form">
            <SwitchRow label={t("settings.chargingPage.title")} checked={form.enabled}
              hint={data.active ? t("settings.chargingPage.chargingNow") : data.plan.reason}
              onChange={(v) => (v && !form.legal_confirmed ? setLegal(true) : void save({ ...form, enabled: v }))} />
            {data.last_error && <Notice kind="error">{data.last_error}</Notice>}
            {form.enabled && data.plan.quarters.length > 0 && (
              <p className="hint">{data.plan.needed_wh
                ? t("settings.chargingPage.plannedEnergy", { times: ranges(data.plan.quarters).join(", "), energy: num(data.plan.needed_wh / 1000, 2) })
                : t("settings.chargingPage.planned", { times: ranges(data.plan.quarters).join(", ") })}</p>
            )}
          </div>

          <div className="card form">
            <Segmented value={form.mode} onChange={(mode) => set({ mode })}
              options={[["cheapest", t("settings.chargingPage.cheapestTime")], ["window", t("settings.chargingPage.fixedTimeWindow")]]} />
            <Field label={t("settings.chargingPage.chargeUntil")} hint={t("settings.chargingPage.targetHint")}>
              <Slider value={form.target_soc} min={20} max={100} unit="%" onChange={(v) => set({ target_soc: v })} />
            </Field>
            {form.mode === "cheapest" ? (
              <>
                <Field label={t("settings.chargingPage.readyBy")} hint={t("settings.chargingPage.cheapestHint")}>
                  <select className="input" value={form.ready_by} onChange={(e) => set({ ready_by: Number(e.target.value) })}>
                    {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                  </select>
                </Field>
                <Field label={t("settings.chargingPage.maxPrice")} hint={t("settings.chargingPage.maxPriceHint")}>
                  <div className="input-unit"><AmountInput value={form.max_price_ct} format={amountInput}
                    onChange={(v) => set({ max_price_ct: v })} />
                    <span>ct/kWh</span></div>
                </Field>
              </>
            ) : (
              <div className="field-row">
                <Field label={t("common.from")}>
                  <select className="input" value={form.window_start} onChange={(e) => set({ window_start: Number(e.target.value) })}>
                    {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                  </select>
                </Field>
                <Field label={t("common.to")}>
                  <select className="input" value={form.window_end} onChange={(e) => set({ window_end: Number(e.target.value) })}>
                    {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                  </select>
                </Field>
              </div>
            )}
            <Field label={t("settings.chargingPage.maxChargingPower")}
              hint={t("settings.chargingPage.maxPowerHint")}>
              <div className="input-unit"><input className="input" inputMode="decimal" value={batteryMax} placeholder={de(rated / 1000)}
                onChange={(e) => setBatteryMax(e.target.value)} /><span>kW</span></div>
            </Field>
            <Field label={t("settings.chargingPage.chargingPower")}>
              <Segmented value={form.power_w === level(0.5) ? "gentle" : form.power_w === level(0.75) ? "fast"
                : form.power_w === level(1) ? "max" : "custom"}
                onChange={(v) => v !== "custom" && set({ power_w: level(({ gentle: 0.5, fast: 0.75, max: 1 } as const)[v]) })}
                options={[["gentle", t("settings.chargingPage.gentle")], ["fast", t("settings.chargingPage.fast")], ["max", t("settings.chargingPage.maximum")], ["custom", `${num(form.power_w / 1000, 2)} kW`]]} />
            </Field>
            <p className="hint">{t("settings.chargingPage.powerLevels", {
              gentle: num(level(0.5) / 1000, 2), fast: num(level(0.75) / 1000, 2), max: num(level(1) / 1000, 2) })}</p>
            {form.power_w > base && <Notice kind="warn">{t("settings.chargingPage.powerTooHigh")}</Notice>}
            {form.power_w > 4200 && <p className="hint">{t("settings.chargingPage.section14aHint")}</p>}
            <Field label={t("settings.chargingPage.usableCapacity")} hint={t("settings.chargingPage.capacityHint")}>
              <div className="input-unit"><AmountInput value={form.battery_kwh} format={de}
                onChange={(v) => set({ battery_kwh: v ?? 0 })} /><span>kWh</span></div>
            </Field>
            <Button busy={busy} disabled={!changed} onClick={() => void save(form)}>{t("common.save")}</Button>
            <Unsaved show={!!changed} />
          </div>
        </>
      )}
      {legal && form && (
        <Dialog title={t("settings.chargingPage.turnOnTitle")} confirm={t("settings.chargingPage.turnOn")} danger disabled={!form.legal_confirmed}
          onCancel={() => { setLegal(false); set({ legal_confirmed: false }); }}
          onConfirm={() => { setLegal(false); void save({ ...form, enabled: true }); }}>
          <p>{t("settings.chargingPage.eegWarning")}</p>
          <p className="hint">{t("settings.chargingPage.registrationHint")}</p>
          <Checkbox checked={form.legal_confirmed} onChange={(v) => set({ legal_confirmed: v })}>
            {t("settings.chargingPage.acceptRegistration")}
          </Checkbox>
        </Dialog>
      )}
    </SubPage>
  );
}
