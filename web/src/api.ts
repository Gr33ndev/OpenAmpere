// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
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
  consumption?: number | null; // live counters: from the balance of the others, "load" is the inverter's counter (#346)
  pv_external?: number | null; // stored energy: the part of pv from a second PV inverter (#419); null without one
};

export type Snapshot = {
  timestamp: number;
  pv_power: number | null; // with a counted second PV inverter
  pv_external_power?: number | null; // second PV inverter the inverter measures (SAJ Meter B, #419), when counted
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
  /** what the inverter's meter for a second PV inverter reads (SAJ Meter B, #419), also before it is counted */
  external_pv_w?: number | null;
  configured: boolean;
  connected: boolean;
  last_error: string | null;
  last_update: number | null;
  stale: boolean;
  poll_interval: number;
  relocated: { from: string; to: string; ts: number } | null;
  /** damaged: the database could not be read at the start and was replaced by an empty one (#165);
   *  restored: the last backup restored in the app, with the file of the database it replaced;
   *  rollback: this older version found a newer version's database and uses the copy from before the upgrade (#166);
   *  ha_backup: Home Assistant restored a backup of the app, with the password and app accesses of then (#350).
   *  Without login only whether it happened (true/false), not when or which file (#353). */
  database: { damaged: { ts: number; file: string } | boolean | null; restored: { ts: number; kept: string } | boolean | null;
    rollback: { ts: number; version: number; kept: string } | boolean | null; ha_backup: { ts: number } | boolean | null };
  timezone: string;
  web_build: string | null;
  clock_wrong: boolean;
  devices: { grid_charging: boolean; items: Device[] };
  device: { manufacturer: string; model: string; serial: string | null; firmware: string | null; register_map: string | null;
    driver: string | null; unit: number | null; rated_power_w: number | null; supports_control: boolean;
    energy_step_wh?: number | null;
    /** the work modes the device offers (SAJ: self_use, backup); null: all */
    work_modes?: string[] | null;
    /** grid charging through a time slot of the device (SAJ, #377): it keeps charging this long after OpenAmpere stops */
    charging_continues_min?: number | null;
    /** the backup work mode keeps its own reserve (SAJ, #380) */
    backup_reserve?: boolean } | null;
  /** step of the inverter's energy counters in Wh (#194): 100 = 0.1 kWh; missing on servers before this field */
  energy_step_wh?: number;
  control: { enabled: boolean; dry_run: boolean };
  /** only in the Home Assistant app (#300): ports on the host for direct access (null: turned off) and the address,
   *  if the Supervisor knows the IP of the host */
  home_assistant?: { direct_url: string | null; web_port?: number | null; https_port?: number | null } | null;
  // whether readings get stored (#170); missing on servers before this field
  storage?: { failing_since: number | null; error: "full" | "read_only" | "other" | null; free_bytes: number | null;
    low_space: boolean };
  firmware?: { serial?: string | null; firmware?: string; since?: number; history: { ts: number; old: string; new: string }[] };
  /** another device (e.g. the old smartbox) set these settings back after a change by OpenAmpere */
  overwritten?: { ts: number; settings: string[] } | null;
};

export type Period = "day" | "week" | "month" | "year";

export type Summary = {
  period: Period;
  from: number;
  to: number;
  energy_wh: Counters;
  partial_since?: number | null;
  recorded_since?: number | null; // first day: the chart starts here, the totals come from the inverter
  money?: { savings_eur: number; feed_in_eur: number; grid_cost_eur: number; base_fee_eur?: number; net_cost_eur?: number };
  autarky: number | null;
  self_consumption: number | null;
};

export type EnergyEntry = Counters & { ts: number; soc: number | null };
export type PowerEntry = { ts: number; pv: number | null; house: number | null; grid: number | null; battery: number | null; soc: number | null;
  pv_external?: number | null };

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
    "pv.external": boolean;
    "pv.external_name": string;
    "battery.capacity_kwh": number;
    "battery.max_charge_kw": number;
    "updates.check": boolean;
    "updates.auto": boolean;
    "grid.feed_in_rule": FeedInRule;
    "notify.ntfy_url": string | null; // null without login (see hidden)
    "notify.on_unreachable": boolean;
    "notify.on_alarm": boolean;
    "notify.on_overwritten": boolean;
    "notify.on_battery_full": boolean;
    "notify.on_cheap_power": boolean;
    "notify.on_firmware": boolean;
    "notify.on_battery_health": boolean;
    "notify.on_off_grid": boolean;
    "notify.on_storage": boolean;
    "notify.on_wallbox": boolean;
    "evcc.url": string;
    "evcc.priority": "wallbox_first" | "devices_first";
    "meter.provider": string;
    "meter.username": string | null; // null without login (see hidden)
    "meter.meter_ids": string[] | null; // null without login (see hidden)
    "log.level": "debug" | "info" | "warning";
    "server.allowed_hosts": string[] | null; // null without login (see hidden)
  };
  secrets: Record<SecretKey, { set: boolean; hint: string | null }>;
  locked: string[];
  /** Values that identify the owner or give access to data: null until logged in (#164). */
  hidden: SettingKey[];
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
  /** resolution in W (SAJ: per mille of the rated power); a limit is written rounded down to it */
  step_w?: number;
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
  /** The day the state-of-charge step imports next: days after it have their state of charge already (#476). */
  soc_cursor?: string;
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
  /** the reserve of the backup work mode, only on a device that keeps its own (SAJ, #380) */
  backup_soc?: number | null;
};

export type BatteryState = BatterySettings & {
  unreadable: string[];
  external_change: { expected: Record<string, unknown>; found: Record<string, unknown> } | null;
};

export const OFFLINE_MESSAGE = t("shell.offlineMessage.noConnection");
/** Asks the server for error messages in the language of the app (#104); German needs no header. */
const LANGUAGE_HEADER: Record<string, string> = lang() === "de" ? {} : { "X-OpenAmpere-Lang": lang() };

/** Server paths ("/api/…") relative to the page, so the app also works under a path prefix such as the
 *  ingress address of the Home Assistant app (#300). The page is always served from a folder ending in "/". */
export const appUrl = (path: string) => (path.startsWith("/") ? `.${path}` : path);

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (DEMO && path.startsWith("/api/")) return demoRequest<T>(method, path, body);
  let response: Response;
  const headers: Record<string, string> = { ...LANGUAGE_HEADER };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-OpenAmpere"] = "1"; // required by the server for every change (CSRF protection)
  try {
    response = await fetch(appUrl(path), {
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
/** Failed attempts of the live connection in a row before the app says why and reads the values by polling (#460). */
const LIVE_FAILURES = 3;
const LIVE_POLL_MS = 5000;

/** The live values over the WebSocket. If it does not open a few times in a row while the server answers (typically a
 *  reverse proxy without WebSocket support or without the original host name, #460), `blocked` says so and the values
 *  are read from GET /api/live every few seconds instead. */
export function useLive(): { snap: Snapshot | null; online: boolean; blocked: boolean } {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [online, setOnline] = useState(false);
  const [blocked, setBlocked] = useState(false);

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
    let poll: number | undefined;
    let closed = false;
    let failures = 0;

    // only while the WebSocket fails: the server answers a normal request, so the cause is the way to it
    const read = async () => {
      try {
        const res = await fetch(appUrl("/api/live"), { credentials: "same-origin" });
        if (closed || failures < LIVE_FAILURES) return;
        if (res.ok) setSnap(await res.json());
        setOnline(res.ok); // while polling, the values are as live as the poll
        setBlocked(res.ok || res.status === 503); // 503: the server answers, it has no readings yet
      } catch {
        if (closed || failures < LIVE_FAILURES) return; // the socket opened meanwhile: it says what is live
        // the server itself is not reachable (e.g. a restart for an update): no proxy hint, the overview says it is not
        // connected. Polling stops and the count starts again, so the hint needs new failed attempts of the socket
        // while the server answers, and does not flash up when the server is back before the socket
        setOnline(false);
        setBlocked(false);
        window.clearInterval(poll);
        poll = undefined;
        failures = 0;
      }
    };
    const connect = () => {
      const url = new URL(appUrl("/api/live/ws"), location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      let opened = false;
      ws = new WebSocket(url);
      ws.onopen = () => {
        opened = true;
        failures = 0;
        window.clearInterval(poll);
        poll = undefined;
        setBlocked(false);
        setOnline(true);
      };
      ws.onmessage = (event) => setSnap(JSON.parse(event.data));
      ws.onclose = () => {
        if (poll === undefined) setOnline(false); // while polling, read() says whether values arrive
        if (!opened && ++failures >= LIVE_FAILURES && poll === undefined && !closed) {
          void read();
          poll = window.setInterval(() => void read(), LIVE_POLL_MS);
        }
        if (!closed) retry = window.setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(poll);
      ws?.close();
    };
  }, []);

  return { snap, online, blocked };
}

const RETRY_MS = 5000;
const REFRESH_EVENT = "openampere:refresh";

/** Reload every resource on screen, e.g. after a setting changed that several pages show. */
export function refreshAll(): void {
  invalidate();
  window.dispatchEvent(new Event(REFRESH_EVENT));
}

/** Calls apply when values from the server really changed – not on every poll or reload with the same content –
 *  so a form keeps what is being typed (#221). */
export function useOnServerChange<T>(value: T | null | undefined, apply: (value: T) => void): void {
  const last = useRef<string | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: apply is recreated on every render, only new values count
  useEffect(() => {
    if (value == null) return;
    const json = JSON.stringify(value);
    if (json === last.current) return;
    last.current = json;
    apply(value);
  }, [value]);
}

/** Re-fetches a JSON resource whenever the path changes and every refreshMs; path null = nothing to load.
 *  Failed loads are retried automatically every few seconds. */
export function useResource<T>(path: string | null, refreshMs = 0): {
  data: T | null; error: string | null; reload: () => void; setData: (value: T) => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const shown = useRef<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: nonce is the trigger of reload()
  useEffect(() => {
    if (shown.current !== path) {  // another resource: nothing old on screen; reload(): keep it until the new data is there
      shown.current = path;
      setData(null);
      setError(null);
    }
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

/** Home Assistant app (#300): the address for direct access by IP, with a placeholder for the IP if the Supervisor
 *  does not know it; null if the owner turned direct access off. */
export function directAddress(app: NonNullable<Status["home_assistant"]>): string | null {
  if (app.direct_url) return app.direct_url;
  return app.web_port ? `http://${t("shell.homeAssistant.hostPlaceholder")}:${app.web_port}` : null;
}

/** home_assistant: only in the Home Assistant app (#300), "ingress" when opened in its sidebar */
export type AuthStatus = {
  configured: boolean; authenticated: boolean; home_assistant?: "ingress" | "direct";
  locked?: boolean; // a damaged database had a password that could not be taken over: reset-password on the server first
};

/** POST with a raw body (file upload) – same headers as JSON requests. */
export async function postFile<T>(path: string, file: Blob, type = "application/zip"): Promise<T> {
  if (DEMO) return demoRequest<T>("POST", path);
  const response = await fetch(appUrl(path), { method: "POST", body: file, credentials: "same-origin",
    headers: { ...LANGUAGE_HEADER, "X-OpenAmpere": "1", "Content-Type": type } }).catch(() => {
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
  key: string; id: string | number; source: "openampere" | "evcc" | "wallbox"; name: string;
  kind: "heating_rod" | "switch" | "wallbox" | "heat_pump"; enabled: boolean; power_w: number; active: boolean;
  on: boolean | null; temperature_c: number | null; target_c: number | null; status: string | null; error: string | null;
  override: { mode: "off" | "boost"; until: number | null } | null;
  connected: boolean | null; // wallbox: car plugged in
  soc?: number | null; range_km?: number | null; // wallbox: battery and range of the plugged-in car
  soc_ts?: number | null; // wallbox OpenAmpere controls: when the car's manufacturer delivered the state of charge (#401)
  addresses_lost?: boolean; // own switch addresses that cannot be read (#392): not switched until entered again
};
export type DevicesView = { devices: Device[]; today_wh: Record<string, number>;
  evcc: { configured: boolean; error: string | null }; priority: string };
export type DeviceSeries = { devices: { key: string; name: string; kind: Device["kind"] }[];
  entries: { ts: number; values: (number | null)[] }[]; totals_wh?: number[] };
