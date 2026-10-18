// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useState } from "react";
import { postJson, putJson, useResource } from "./api";
import { num, updatedLabel } from "./format";
import { t } from "./i18n";
import type { WallboxesView } from "./NativeWallbox";
import { navigate } from "./route";
import { Button, Dialog, Field, LoadState, Notice, SubPage, Switch, toast } from "./ui";

/** Cars and what their manufacturer says about them (#401): state of charge, range and odometer, read from the
 *  EU Data Act portal of the Volkswagen group. The portal delivers about every 15 minutes, some 15 minutes late, so
 *  every value is shown with its age. OpenAmpere only reads; the charges come from a wallbox it controls. */

export type CarValues = {
  soc: number | null; soc_ts: number | null; range_km: number | null; range_km_ts: number | null;
  odometer_km?: number | null; odometer_km_ts?: number | null; plugged: boolean | null; plugged_ts: number | null;
  charging: boolean | null; charging_ts: number | null; target_soc: number | null; target_soc_ts: number | null;
  ts: number | null;
};
export type CarCharge = { start: number; end: number | null; kwh: number; soc_start: number | null; soc_end: number | null;
  odometer_km: number | null; km_before: number | null; km_since: number | null };
export type Car = {
  id: string; name: string; source: string; brand: string; enabled: boolean; wallbox_id: string | null;
  wallbox: string | null; password_set: boolean; vin_set: boolean; username?: string; vin_end?: string | null;
  status: "ok" | "waiting" | "setup" | "auth" | "error" | null; error: string | null; checked: number | null;
  values: CarValues; charge: CarCharge | null;
};
type Source = { key: string; label: string; brands: { key: string; label: string }[] };
export type CarsView = { cars: Car[]; sources: Source[]; max: number };
type Vehicle = { vin: string; name: string };

export const PORTAL_URL = "https://eu-data-act.drivesomethinggreater.com";

/** "vor 20 Minuten", "vor 3 Stunden", or the date: the portal's values are never live. */
export function age(ts: number): string {
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (minutes < 60) return t("devices.cars.minutesAgo", { count: minutes });
  if (minutes < 24 * 60) return t("devices.cars.hoursAgo", { count: Math.round(minutes / 60) });
  return updatedLabel(ts);
}

function statusLine(car: Car): string {
  const v = car.values;
  if (!car.vin_set) return t("devices.cars.chooseCar");
  if (car.status === "auth") return t("devices.cars.loginRefused");
  if (car.status === "setup") return t("devices.cars.setupMissing");
  if (car.status === "waiting" || (car.status === "ok" && v.ts == null)) return t("devices.cars.waitingFirstData");
  if (car.status === "error" && v.ts == null) return t("devices.cars.notReachable");
  if (car.status == null && v.ts == null) return t("devices.cars.asking");
  if (v.charging) return t("devices.cars.charging");
  if (v.plugged) return t("devices.cars.plugged");
  return v.plugged === false ? t("devices.cars.notPlugged") : t("devices.cars.noState");
}

/** One car on the devices tab: its state of charge, range and the last charge at its wallbox. */
export function CarCard({ car }: { car: Car }) {
  const v = car.values, c = car.charge;
  return (
    <div className="card wallbox car">
      <div className="wallbox-head">
        <div>
          <strong>{car.name}</strong>
          <div className="hint">{statusLine(car)}</div>
        </div>
        {v.soc != null && <div className="wallbox-soc"><span>{num(v.soc, 0)}</span><small>%</small></div>}
      </div>
      {v.soc != null && (
        <div className="soc-track" role="img" aria-label={v.target_soc != null
          ? t("devices.wallboxCard.socLabel", { soc: num(v.soc, 0), target: num(v.target_soc, 0) })
          : t("devices.cars.socLabel", { soc: num(v.soc, 0) })}>
          <div className="fill" style={{ width: `${Math.min(100, v.soc)}%` }} />
          {v.target_soc != null && <div className="target" style={{ left: `${v.target_soc}%` }} />}
        </div>
      )}
      {car.error && car.status !== "ok" && <Notice kind={car.status === "error" ? "warn" : "error"}>{car.error}</Notice>}
      <dl className="facts">
        {v.soc_ts != null && <><dt>{t("devices.cars.asOf")}</dt><dd>{age(v.soc_ts)}</dd></>}
        {v.range_km != null && <><dt>{t("devices.wallboxCard.range")}</dt><dd>{num(v.range_km, 0)} km</dd></>}
        {v.target_soc != null && <><dt>{t("devices.cars.target")}</dt><dd>{num(v.target_soc, 0)} %</dd></>}
        {v.odometer_km != null && <><dt>{t("devices.cars.odometer")}</dt><dd>{num(v.odometer_km, 0)} km</dd></>}
        {c && <>
          <dt>{c.end == null ? t("devices.cars.thisCharge") : t("devices.cars.lastCharge")}</dt>
          <dd>{updatedLabel(c.start)}</dd>
          {c.soc_start != null && <><dt>{t("devices.cars.socAtPlugIn")}</dt><dd>{num(c.soc_start, 0)} %</dd></>}
          {c.soc_end != null && <><dt>{t("devices.cars.socAtEnd")}</dt><dd>{num(c.soc_end, 0)} %</dd></>}
          <dt>{t("common.charged")}</dt><dd>{num(c.kwh, 1)} kWh</dd>
          {c.km_before != null && <><dt>{t("devices.cars.drivenBefore")}</dt><dd>{num(c.km_before, 0)} km</dd></>}
          {c.km_since != null && <><dt>{t("devices.cars.drivenSince")}</dt><dd>{num(c.km_since, 0)} km</dd></>}
        </>}
      </dl>
      {!car.wallbox_id && <p className="hint">{t("devices.cars.noWallboxHint")}</p>}
      {(car.status === "auth" || car.status === "setup" || !car.vin_set) && (
        <Button variant="secondary" onClick={() => navigate("more/cars")}>{t("devices.cars.toSetup")}</Button>
      )}
    </div>
  );
}

/** The cars of the devices tab, below the wallboxes. */
export function CarCards() {
  const { data } = useResource<CarsView>("/api/cars", 60_000);
  const cars = (data?.cars ?? []).filter((c) => c.enabled);
  if (!cars.length) return null;
  return (
    <>
      <div className="section-title">{t("devices.cars.title")}</div>
      {cars.map((car) => <CarCard key={car.id} car={car} />)}
      <p className="hint small-credit">{t("devices.cars.delayHint")}</p>
    </>
  );
}

type Draft = { id?: string; name: string; source: string; brand: string; username: string; password: string; vin: string;
  wallbox_id: string; enabled: boolean };
const toItem = (c: Car | Draft) => ({ id: c.id, name: c.name, source: c.source, brand: c.brand, username: c.username ?? "",
  password: "password" in c ? c.password : "", vin: "vin" in c ? c.vin : "", wallbox_id: c.wallbox_id ?? "",
  enabled: c.enabled });

/** Settings (Mehr → Verbindung → Auto): the cars, their account at the manufacturer and their wallbox. */
export function CarsPage({ onBack }: { onBack: () => void }) {
  const { data: view, error, reload, setData } = useResource<CarsView>("/api/cars", 30_000);
  const { data: wallboxes } = useResource<WallboxesView>("/api/wallboxes");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [removing, setRemoving] = useState<Car | null>(null);
  const [busy, setBusy] = useState(false);
  const [fetching, setFetching] = useState<string | null>(null);

  const save = async (items: ReturnType<typeof toItem>[]): Promise<CarsView | null> => {
    setBusy(true);
    try {
      const result = await putJson<CarsView>("/api/cars", { cars: items });
      setData(result);
      toast(t("common.saved"));
      return result;
    } catch (e) {
      toast((e as Error).message, "error");
      return null;
    } finally {
      setBusy(false);
    }
  };
  const fetchNow = async (id: string) => {
    setFetching(id);
    try {
      const result = await postJson<CarsView>(`/api/cars/${id}/refresh`, {});
      setData(result);
      const car = result.cars.find((c) => c.id === id);
      if (car?.error) toast(car.error, "error");
      else toast(t("devices.cars.fetched"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setFetching(null);
    }
  };

  const others = (id?: string) => (view?.cars ?? []).filter((c) => c.id !== id).map(toItem);
  const onSave = async (d: Draft) => {
    const result = await save([...others(d.id), toItem(d)]);
    if (!result) return;
    setDraft(null);
    const saved = result.cars.find((c) => (d.id ? c.id === d.id : c.name === d.name));
    if (saved?.vin_set && saved.enabled) void fetchNow(saved.id);
  };
  const source = view?.sources[0];
  const loggedOut = !!view && view.cars.some((c) => c.username === undefined);

  return (
    <SubPage title={t("devices.cars.title")} onBack={onBack}>
      <p className="hint">{t("devices.cars.intro")}</p>
      <PortalGuide />
      {!view && <LoadState error={error} onRetry={reload} />}
      {loggedOut && <Notice kind="info">{t("devices.cars.loginToChange")}</Notice>}
      {view && !loggedOut && view.cars.map((car) => draft?.id === car.id ? (
        <div className="card form" key={car.id}>
          <CarForm view={view} wallboxes={wallboxes} draft={draft} busy={busy} onSave={onSave} onCancel={() => setDraft(null)} />
        </div>
      ) : (
        <div className="card" key={car.id}>
          <div className="wallbox-head">
            <div>
              <strong>{car.name}</strong>
              <div className="hint">{[source?.brands.find((b) => b.key === car.brand)?.label, car.vin_end && `…${car.vin_end}`,
                car.wallbox].filter(Boolean).join(" · ")}</div>
              <div className="hint">{statusLine(car)}{car.values.ts != null ? ` · ${t("devices.cars.lastData", { age: age(car.values.ts) })}` : ""}</div>
            </div>
            <Switch checked={car.enabled} disabled={busy} label={t("devices.cars.enabled")}
              onChange={(on) => void save((view?.cars ?? []).map((c) => toItem(c.id === car.id ? { ...c, enabled: on } : c)))} />
          </div>
          {car.error && <Notice kind={car.status === "error" ? "warn" : "error"}>{car.error}</Notice>}
          {!car.password_set && <Notice kind="warn">{t("devices.cars.passwordLost")}</Notice>}
          <div className="button-row">
            <Button variant="secondary" onClick={() => setDraft({ id: car.id, name: car.name, source: car.source, brand: car.brand,
              username: car.username ?? "", password: "", vin: "", wallbox_id: car.wallbox ? car.wallbox_id ?? "" : "", enabled: car.enabled })}>
              {t("devices.cars.edit")}</Button>
            {car.vin_set && car.enabled && (
              <Button variant="secondary" busy={fetching === car.id} disabled={!!fetching} onClick={() => void fetchNow(car.id)}>
                {t("devices.cars.fetchNow")}</Button>
            )}
            <Button variant="secondary" onClick={() => setRemoving(car)}>{t("common.remove")}</Button>
          </div>
        </div>
      ))}
      {view && !loggedOut && source && (draft && !draft.id ? (
        <div className="card form">
          <CarForm view={view} wallboxes={wallboxes} draft={draft} busy={busy} onSave={onSave} onCancel={() => setDraft(null)} />
        </div>
      ) : view.cars.length < view.max ? (
        <Button variant="secondary" onClick={() => setDraft({ name: t("devices.cars.defaultName"), source: source.key,
          brand: source.brands[0]?.key ?? "", username: "", password: "", vin: "",
          wallbox_id: (wallboxes?.wallboxes ?? []).find((w) => !view.cars.some((c) => c.wallbox_id === w.id))?.id ?? "",
          enabled: true })}>{t("devices.cars.add")}</Button>
      ) : <Notice kind="info">{t("devices.cars.full", { max: view.max })}</Notice>)}
      <p className="hint">{t("devices.cars.readOnlyHint")}</p>
      <p className="hint">{t("devices.cars.unofficialHint")}</p>
      {removing && (
        <Dialog title={t("devices.cars.removeQuestion", { name: removing.name })} confirm={t("common.remove")} danger
          onCancel={() => setRemoving(null)}
          onConfirm={() => { void save(others(removing.id)); setRemoving(null); }}>
          <p>{t("devices.cars.removeHint")}</p>
        </Dialog>
      )}
    </SubPage>
  );
}

/** The one-time steps in the browser, without which the portal delivers nothing. */
function PortalGuide() {
  return (
    <details className="advanced">
      <summary>{t("devices.cars.guideTitle")}</summary>
      <ol className="steps-list">
        <li>{t("devices.cars.guideStep1")} <a href={PORTAL_URL} target="_blank" rel="noreferrer">{PORTAL_URL.replace("https://", "")}</a></li>
        <li>{t("devices.cars.guideStep2")}</li>
        <li>{t("devices.cars.guideStep3")}</li>
        <li>{t("devices.cars.guideStep4")}</li>
      </ol>
      <p className="hint">{t("devices.cars.guideWait")}</p>
    </details>
  );
}

/** The fields of a car: the account, then the car chosen from it, then its wallbox. */
function CarForm({ view, wallboxes, draft, busy, onSave, onCancel }: {
  view: CarsView; wallboxes: WallboxesView | null; draft: Draft; busy: boolean; onSave: (d: Draft) => void;
  onCancel: () => void;
}) {
  const [d, setD] = useState(draft);
  const [vehicles, setVehicles] = useState<Vehicle[] | null>(null);
  const [loading, setLoading] = useState(false);
  const stored = draft.id ? view.cars.find((c) => c.id === draft.id) : undefined;
  const source = view.sources.find((s) => s.key === d.source) ?? view.sources[0];
  const set = (changes: Partial<Draft>) => setD({ ...d, ...changes });
  const login = (changes: Partial<Draft>) => { set({ ...changes, vin: "" }); setVehicles(null); };
  const passwordKnown = !!d.password || !!stored?.password_set;

  const load = async () => {
    setLoading(true);
    try {
      const result = await postJson<{ vehicles: Vehicle[] }>("/api/cars/vehicles",
        { source: d.source, brand: d.brand, username: d.username.trim(), password: d.password, id: d.id ?? null });
      setVehicles(result.vehicles);
      if (result.vehicles.length === 1) set({ vin: result.vehicles[0].vin });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setLoading(false);
    }
  };
  const taken = new Set(view.cars.filter((c) => c.id !== d.id).map((c) => c.wallbox_id));
  const free = (wallboxes?.wallboxes ?? []).filter((w) => !taken.has(w.id));
  // another account: its car has to be chosen again (the server forgets the old one's VIN)
  const loginChanged = !!stored && (d.brand !== stored.brand || d.username.trim() !== (stored.username ?? ""));
  const vinChosen = !!d.vin || (!!stored?.vin_set && !vehicles && !loginChanged);

  return (
    <>
      <Field label={t("devices.cars.name")}>
        <input className="input" value={d.name} maxLength={40} onChange={(e) => set({ name: e.target.value })} />
      </Field>
      <Field label={t("devices.cars.brand")} hint={source?.label}>
        <select className="input" value={d.brand} onChange={(e) => login({ brand: e.target.value })}>
          {source?.brands.map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
        </select>
      </Field>
      <Field label={t("devices.cars.email")} hint={t("devices.cars.emailHint")}>
        <input className="input" type="email" autoComplete="off" value={d.username} onChange={(e) => login({ username: e.target.value })} />
      </Field>
      <Field label={t("common.password")} hint={stored?.password_set ? t("settings.gridMeterPage.savedKeep") : undefined}>
        <input className="input" type="password" autoComplete="off" value={d.password} onChange={(e) => login({ password: e.target.value })} />
      </Field>
      <Button variant="secondary" busy={loading} disabled={!d.username.trim() || !passwordKnown} onClick={() => void load()}>
        {t("devices.cars.loadVehicles")}</Button>
      {vehicles && (
        <Field label={t("devices.cars.vehicle")}>
          <select className="input" value={d.vin} onChange={(e) => set({ vin: e.target.value })}>
            {vehicles.length > 1 && <option value="">{t("devices.cars.pleaseChoose")}</option>}
            {vehicles.map((v) => <option key={v.vin} value={v.vin}>{v.name ? `${v.name} (…${v.vin.slice(-4)})` : `…${v.vin.slice(-4)}`}</option>)}
          </select>
        </Field>
      )}
      {!vehicles && stored?.vin_set && !loginChanged && <p className="hint">{t("devices.cars.vehicleChosen", { end: stored.vin_end ?? "" })}</p>}
      <Field label={t("devices.cars.wallbox")} hint={free.length ? t("devices.cars.wallboxHint") : t("devices.cars.noFreeWallbox")}>
        <select className="input" value={d.wallbox_id} onChange={(e) => set({ wallbox_id: e.target.value })}>
          <option value="">{t("devices.cars.noWallbox")}</option>
          {free.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </Field>
      <div className="button-row">
        <Button busy={busy} disabled={!d.name.trim() || !d.username.trim() || !passwordKnown || !vinChosen}
          onClick={() => onSave(d)}>{t("common.save")}</Button>
        <Button variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
      </div>
    </>
  );
}
