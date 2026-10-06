import { useEffect, useState } from "react";
import { DEMO } from "./demo/flag";
import { demoRequest, snapshot as demoSnapshot } from "./demo/server";
import { lang, t } from "./i18n";

export type Counters = {
  pv: number | null;
  load: number | null;
  grid_import: number | null;
  grid_export: number | null;
  battery_charge: number | null;
  battery_discharge: number | null;
};

export type Snapshot = {
  timestamp: number;
  pv_power: number | null;
  house_power: number | null;
  grid_power: number | null; // + import
  battery_power: number | null; // + discharge
  battery_soc: number | null;
  pv_inputs: { power: number | null; voltage: number | null; current: number | null }[];
  temperatures: Partial<Record<"inverter" | "ambient" | "battery" | "battery_cell_max" | "battery_cell_min"
    | "battery2" | "battery2_cell_max" | "battery2_cell_min", number>>;
  battery_temperature: number | null;
  battery_soh: number | null;
  inverter_state: number | null;
  off_grid: boolean | null;
  alarms: number[];
  totals: Counters;
  today: Counters;
};

export type Status = {
  version: string;
  configured: boolean;
  connected: boolean;
  last_error: string | null;
  last_update: number | null;
  stale: boolean;
  poll_interval: number;
  relocated: { from: string; to: string; ts: number } | null;
  timezone: string;
  web_build: string | null;
  clock_wrong: boolean;
  devices: { grid_charging: boolean; items: Device[] };
  device: { manufacturer: string; model: string; serial: string | null; firmware: string | null; register_map: string | null;
    driver: string | null; unit: number | null; rated_power_w: number | null; supports_control: boolean } | null;
  control: { enabled: boolean; dry_run: boolean };
  firmware?: { serial?: string | null; firmware?: string; since?: number; history: { ts: number; old: string; new: string }[] };
};

export type Period = "day" | "week" | "month" | "year";

export type Summary = {
  period: Period;
  from: number;
  to: number;
  energy_wh: Counters;
  partial_since?: number | null;
  recorded_since?: number | null; // first day: the chart starts here, the totals come from the inverter
  conversion_loss_wh?: number;
  money?: { savings_eur: number; feed_in_eur: number; grid_cost_eur: number; base_fee_eur?: number; net_cost_eur?: number };
  autarky: number | null;
  self_consumption: number | null;
};

export type EnergyEntry = Counters & { ts: number; soc: number | null };
export type PowerEntry = { ts: number; pv: number | null; house: number | null; grid: number | null; battery: number | null; soc: number | null };

export type SecretKey = "cloud.api_key" | "notify.ntfy_token" | "evcc.password" | "meter.password";
export type Settings = {
  values: {
    "inverter.driver": "auto" | "foxess" | "saj";
    "inverter.host": string;
    "inverter.port": number;
    "inverter.unit": number;
    "inverter.register_map": string;
    "inverter.read_function": string;
    "inverter.poll_interval": number;
    "inverter.timeout": number;
    "inverter.connection_mode": "persistent" | "per_poll";
    "storage.raw_retention_days": number;
    "control.enabled": boolean;
    "control.dry_run": boolean;
    "tariff.electricity_price_ct": number;
    "tariff.feed_in_ct": number;
    "tariff.feed_in_auto": boolean;
    "tariff.feed_in_full": boolean;
    "pv.commissioning_date": string;
    timezone: string;
    "pv.input_names": string[];
    "pv.installed_kwp": number;
    "pv.hidden_inputs": string[];
    "battery.capacity_kwh": number;
    "battery.max_charge_kw": number;
    "updates.check": boolean;
    "updates.auto": boolean;
    "grid.feed_in_rule": FeedInRule;
    "notify.ntfy_url": string;
    "notify.on_unreachable": boolean;
    "notify.on_alarm": boolean;
    "notify.on_overwritten": boolean;
    "notify.on_battery_full": boolean;
    "notify.on_cheap_power": boolean;
    "notify.on_firmware": boolean;
    "notify.on_battery_health": boolean;
    "notify.on_off_grid": boolean;
    "evcc.url": string;
    "evcc.priority": "wallbox_first" | "devices_first";
    "meter.provider": string;
    "meter.username": string;
    "meter.meter_ids": string[];
  };
  secrets: Record<SecretKey, { set: boolean; hint: string | null }>;
  locked: string[];
  revision: number;
};

export type PvInputsTimeline = {
  mode: "energy" | "power";
  inputs?: number[]; // input numbers (1, 2, 3 ...) of the labels; hidden inputs are left out
  labels: string[];
  entries: { ts: number; values: (number | null)[] }[];
  totals_wh: number[] | null;
};

export const PV_INPUT_COLORS = ["#eec91d", "#a78bfa", "#4fc3f7", "#f48fb1"];

/** Inputs that actually carry PV (voltage or power seen) and are not hidden in the settings (e.g. an unused MPPT). */
export function activeInputs(snap: Snapshot | null, hidden: string[] = []) {
  return (snap?.pv_inputs ?? []).map((input, index) => ({ ...input, index }))
    .filter((i) => ((i.voltage ?? 0) > 1 || (i.power ?? 0) > 1) && !hidden.includes(String(i.index + 1)));
}

export type FeedInRule = "unknown" | "limit_60" | "limit_70" | "operator" | "none";
export type ExportLimit = {
  supported: boolean; limit_w: number | null; rated_power_w: number | null;
  rule: FeedInRule; installed_kwp: number; legal_max_w: number | null;
};

export type CloudImportState = {
  status: "idle" | "running" | "paused" | "done" | "error";
  phase?: "search" | "work" | "soc";
  days_total?: number;
  work_done?: number;
  soc_done?: number;
  imported?: number;
  start?: string;
  end?: string;
  error?: string | null;
  notice?: string | null;
  eta_seconds?: number | null;
  key_set: boolean;
};
export type SettingKey = keyof Settings["values"];

export type BatterySettings = {
  work_mode: "self_use" | "feed_in_first" | "backup" | "peak_shaving" | null;
  min_soc: number | null;
  max_soc: number | null;
  min_soc_on_grid: number | null;
};

export type BatteryState = BatterySettings & {
  unreadable: string[];
  external_change: { expected: Record<string, unknown>; found: Record<string, unknown> } | null;
};

export const OFFLINE_MESSAGE = t("shell.offlineMessage.noConnection");
/** Asks the server for error messages in the language of the app (#104); German needs no header. */
const LANGUAGE_HEADER: Record<string, string> = lang() === "de" ? {} : { "X-OpenAmpere-Lang": lang() };

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (DEMO && path.startsWith("/api/")) return demoRequest<T>(method, path);
  let response: Response;
  const headers: Record<string, string> = { ...LANGUAGE_HEADER };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-OpenAmpere"] = "1"; // required by the server for every change (CSRF protection)
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error(OFFLINE_MESSAGE); // network error, server not running
  }
  if (response.status === 401) {
    const data = await response.clone().json().catch(() => ({}));
    if (data.code === "login_required" || data.code === "setup_required") {
      window.dispatchEvent(new CustomEvent("openampere:auth", { detail: data.code }));
    }
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

// Several components ask for the same thing at the same moment (e.g. /api/status): share one request.
const recent = new Map<string, { at: number; promise: Promise<unknown> }>();
export function getJson<T>(path: string): Promise<T> {
  const hit = recent.get(path);
  if (hit && Date.now() - hit.at < 1500) return hit.promise as Promise<T>;
  const promise = request<T>("GET", path);
  recent.set(path, { at: Date.now(), promise });
  promise.catch(() => recent.delete(path));
  return promise;
}
/** Forget shared GET results, e.g. after a change was saved. */
export const invalidate = () => recent.clear();
export const putJson = <T,>(path: string, body: unknown) => { invalidate(); return request<T>("PUT", path, body); };
export const postJson = <T,>(path: string, body: unknown) => { invalidate(); return request<T>("POST", path, body); };
export const deleteJson = <T,>(path: string) => { invalidate(); return request<T>("DELETE", path); };

/** Live snapshots over WebSocket with automatic reconnect. */
export function useLive(): { snap: Snapshot | null; online: boolean } {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [online, setOnline] = useState(false);

  useEffect(() => {
    if (DEMO) {
      const tick = () => setSnap(demoSnapshot() as Snapshot | null);
      setOnline(true);
      tick();
      const timer = window.setInterval(tick, 5000);
      return () => window.clearInterval(timer);
    }
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/live/ws`);
      ws.onopen = () => setOnline(true);
      ws.onmessage = (event) => setSnap(JSON.parse(event.data));
      ws.onclose = () => {
        setOnline(false);
        if (!closed) retry = window.setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      ws?.close();
    };
  }, []);

  return { snap, online };
}

const RETRY_MS = 5000;
const REFRESH_EVENT = "openampere:refresh";

/** Reload every resource on screen, e.g. after a setting changed that several pages show. */
export function refreshAll(): void {
  invalidate();
  window.dispatchEvent(new Event(REFRESH_EVENT));
}

/** Re-fetches a JSON resource whenever the path changes and every refreshMs; path null = nothing to load.
 *  Failed loads are retried automatically every few seconds. */
export function useResource<T>(path: string | null, refreshMs = 0): {
  data: T | null; error: string | null; reload: () => void; setData: (value: T) => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    setData(null);
    setError(null);
    if (!path) return;
    let active = true;
    let retry: number | undefined;
    const load = () =>
      getJson<T>(path)
        .then((value) => {
          if (!active) return;
          setData(value);
          setError(null);
        })
        .catch((err: Error) => {
          if (!active) return;
          setError(err.message);
          window.clearTimeout(retry);
          retry = window.setTimeout(load, RETRY_MS);
        });
    load();
    const timer = refreshMs ? window.setInterval(load, refreshMs) : undefined;
    window.addEventListener(REFRESH_EVENT, load);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.clearTimeout(retry);
      window.removeEventListener(REFRESH_EVENT, load);
    };
  }, [path, refreshMs, nonce]);

  return { data, error, reload: () => setNonce((n) => n + 1), setData };
}

export type AuthStatus = { configured: boolean; authenticated: boolean };

/** POST with a raw body (file upload) – same headers as JSON requests. */
export async function postFile<T>(path: string, file: Blob): Promise<T> {
  if (DEMO) return demoRequest<T>("POST", path);
  const response = await fetch(path, { method: "POST", body: file, credentials: "same-origin",
    headers: { ...LANGUAGE_HEADER, "X-OpenAmpere": "1", "Content-Type": "application/zip" } }).catch(() => {
    throw new Error(OFFLINE_MESSAGE);
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
  return data as T;
}

/** Live values older than three poll intervals (or while the inverter is unreachable) are not "live". */
export function useStale(snap: Snapshot | null, online: boolean, status: Status | null): boolean {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(timer);
  }, []);
  if (!snap) return false;
  const maxAge = Math.max(3 * (status?.poll_interval ?? 10), 30);
  return !online || status?.connected === false || now / 1000 - snap.timestamp > maxAge;
}

export type Device = {
  key: string; id: string | number; source: "openampere" | "evcc"; name: string;
  kind: "heating_rod" | "switch" | "wallbox" | "heat_pump"; enabled: boolean; power_w: number; active: boolean;
  on: boolean | null; temperature_c: number | null; target_c: number | null; status: string | null; error: string | null;
  override: { mode: "off" | "boost"; until: number | null } | null;
  connected: boolean | null; // wallbox: car plugged in
  soc?: number | null; range_km?: number | null; // wallbox: battery and range of the plugged-in car
};
export type DevicesView = { devices: Device[]; today_wh: Record<string, number>;
  evcc: { configured: boolean; error: string | null }; priority: string };
export type DeviceSeries = { devices: { key: string; name: string; kind: Device["kind"] }[];
  entries: { ts: number; values: (number | null)[] }[]; totals_wh?: number[] };
