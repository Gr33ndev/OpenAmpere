// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { csv } from "./csv";
import { kw, num, percent, timeZone } from "./format";
import { LOCALE, list, t } from "./i18n";

/** One entry of /api/control/log. `details` differs per action and was not always an object; `result` is the stored
 * German text, `note` the reason or error from it in the app's language (#153). */
export type LogEntry = { ts: number; action: string; details: unknown; dry_run: boolean; result: string; note?: string | null };

export type LogStatus = "ok" | "test" | "warning" | "error";

/** An entry in words: what happened, who or what caused it, how it ended and the server's reason, if any. */
export type DescribedEntry = { text: string; by: string; status: LogStatus; note: string | null };

type Values = Record<string, unknown>;
const obj = (v: unknown): Values => (v && typeof v === "object" && !Array.isArray(v) ? v as Values : {});
const str = (v: unknown): string => (v == null ? "" : String(v));
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const unknown = () => t("settings.controlLog.unknownValue");
const pct = (v: unknown) => (isNum(v) ? percent(v) : unknown());
const watts = (v: unknown) => (isNum(v) ? `${v.toLocaleString(LOCALE)} W` : v === null ? t("common.noLimitValue") : unknown());
const kwp = (v: unknown) => (isNum(v) ? `${v.toLocaleString(LOCALE, { maximumFractionDigits: 2 })} kWp` : unknown());

function workMode(v: unknown): string {
  switch (v) {
    case "self_use": return t("common.selfConsumption");
    case "feed_in_first": return t("common.preferFeedIn");
    case "backup": return t("common.backupReserve");
    case "peak_shaving": return t("settings.controlLog.peakShaving");
    default: return str(v) || unknown();
  }
}

function feedInRule(v: unknown): string {
  switch (v) {
    case "limit_60": return "60 %";
    case "limit_70": return "70 %";
    case "operator": return t("settings.controlLog.operatorValue");
    case "none": return t("common.noLimitValue");
    default: return unknown();
  }
}

/** Name of a battery setting, e.g. "Notstrom-Reserve" for min_soc_on_grid. */
export function batterySettingName(key: string): string {
  return ({
    min_soc: t("settings.controlLog.backupLowerLimit"), min_soc_on_grid: t("settings.controlLog.backupReserve"),
    max_soc: t("settings.controlLog.chargeLimit"), work_mode: t("common.operatingMode"),
  } as Record<string, string>)[key] ?? key;
}

/** Value of a battery setting with its unit, e.g. "20 %" or "Eigenverbrauch". */
export const batterySettingValue = (key: string, v: unknown) => (key === "work_mode" ? workMode(v) : pct(v));

/** Battery settings: "Notstrom-Reserve von 10 % auf 20 %" for every changed value. */
function batteryChanges(from: Values, to: Values, overwritten: boolean): string {
  return list(Object.keys(to).map((key) => {
    const values = { name: batterySettingName(key), from: batterySettingValue(key, from[key]), to: batterySettingValue(key, to[key]) };
    return overwritten ? t("settings.controlLog.changedTo", values) : t("settings.controlLog.change", values);
  }));
}

function hours(v: unknown): string | null {
  return isNum(v) || (typeof v === "string" && v !== "" && Number.isFinite(Number(v)))
    ? t("settings.controlLog.hours", { count: Number(v), hours: num(Number(v), Number(v) % 1 ? 1 : 0) }) : null;
}

/** A device's own operating mode: automatic, on or off, possibly for some hours. */
function modeText(name: string, mode: unknown, duration: unknown): string {
  const h = hours(duration);
  if (mode === "boost") return h ? t("settings.controlLog.modeOnFor", { name, hours: h }) : t("settings.controlLog.modeOn", { name });
  if (mode === "off") return h ? t("settings.controlLog.modeOffFor", { name, hours: h }) : t("settings.controlLog.modeOff", { name });
  return t("settings.controlLog.modeAuto", { name });
}

function wallboxText(name: string, action: unknown, value: unknown): string {
  switch (action) {
    case "mode": {
      const modes: Record<string, string> = { off: t("common.off"), pv: t("devices.modes.solar"), minpv: t("devices.modes.minSolar"),
        now: t("devices.modes.fast") };
      const mode = modes[str(value)] ?? str(value);
      return t("settings.controlLog.wallboxMode", { name, mode });
    }
    case "limit_soc": return t("settings.controlLog.wallboxLimit", { name, soc: pct(value) });
    case "min_soc": return t("settings.controlLog.wallboxMinSoc", { name, soc: pct(value) });
    case "plan_delete": return t("settings.controlLog.wallboxPlanDeleted", { name });
    case "priority_soc": return t("settings.controlLog.wallboxBatteryPriority", { name, soc: pct(value) });
    case "plan": {
      const plan = obj(value);
      const when = isNum(plan.time) ? new Date(plan.time * 1000).toLocaleString(LOCALE, {
        weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: timeZone() }) : unknown();
      return t("settings.controlLog.wallboxPlan", { name, soc: pct(plan.soc), time: when });
    }
    default: return t("settings.controlLog.wallboxOther", { name });
  }
}

/** The update entry stored the versions as plain strings before #153, now as {version}. */
const version = (v: unknown) => str(typeof v === "string" ? v : obj(v).version) || unknown();

function sentence(e: LogEntry, details: Values, from: Values, to: Values): string {
  const name = str(to.consumer ?? from.consumer) || t("common.device");
  switch (e.action) {
    case "control_switches":
      return Object.keys(to).map((key) => {
        if (key === "control.enabled") return to[key] ? t("settings.controlLog.controlOn") : t("settings.controlLog.controlOff");
        if (key === "control.dry_run") return to[key] ? t("settings.controlLog.testModeOn") : t("settings.controlLog.testModeOff");
        if (key === "grid.feed_in_rule") return t("settings.controlLog.feedInRule", { from: feedInRule(from[key]), to: feedInRule(to[key]) });
        if (key === "pv.installed_kwp") return t("settings.controlLog.pvCapacity", { from: kwp(from[key]), to: kwp(to[key]) });
        return t("settings.controlLog.setting", { name: key, from: str(from[key]) || unknown(), to: str(to[key]) || unknown() });
      }).join(" ") || t("settings.controlLog.unknown", { action: e.action });
    case "battery_settings":
      return t("settings.controlLog.batterySettings", { changes: batteryChanges(from, to, false) });
    case "battery_settings_check":
      return t("settings.controlLog.batteryOverwritten", { changes: batteryChanges(from, to, true) });
    case "export_limit":
      return t("settings.controlLog.exportLimit", { from: watts(from.export_limit_w), to: watts(to.export_limit_w) });
    case "export_limit_check":
      return t("settings.controlLog.exportLimitOverwritten", { from: watts(from.export_limit_w), to: watts(to.export_limit_w) });
    case "grid_charging_switch":
      return to.enabled ? t("settings.controlLog.gridChargingOn") : t("settings.controlLog.gridChargingOff");
    case "grid_charging": {
      if (details.source != null) { // changed by an app through the app API
        const changes = [
          ...("enabled" in to ? [to.enabled ? t("settings.controlLog.switchedOn") : t("settings.controlLog.switchedOff")] : []),
          ...("target_soc" in to ? [t("settings.controlLog.chargeTarget", { soc: pct(to.target_soc) })] : []),
        ];
        return t("settings.controlLog.gridChargingSet", { changes: list(changes) || unknown() });
      }
      if ("power_w" in to) {
        return "target_soc" in to
          ? t("settings.controlLog.gridChargingStarted", { power: kw(Number(to.power_w)), soc: pct(to.target_soc) })
          : t("settings.controlLog.gridChargingStartedNoTarget", { power: kw(Number(to.power_w)) });
      }
      if (e.result.startsWith("Fernsteuerung")) return t("settings.controlLog.remoteReleased");
      if (e.result.startsWith("Beenden fehlgeschlagen")) return t("settings.controlLog.gridChargingStopFailed");
      return isNum(from.soc) && isNum(to.soc)
        ? t("settings.controlLog.gridChargingStopped", { from: pct(from.soc), to: pct(to.soc) })
        : t("settings.controlLog.gridChargingStoppedPlain");
    }
    case "consumer":
      if ("on" in to) return to.on ? t("settings.controlLog.consumerOn", { name }) : t("settings.controlLog.consumerOff", { name });
      return isNum(to.power_w) && to.power_w > 0
        ? t("settings.controlLog.consumerPower", { name, power: kw(to.power_w) })
        : t("settings.controlLog.consumerOff", { name });
    case "consumer_mode":
      return modeText(name, to.mode, to.hours);
    case "device_mode":
      return modeText(str(details.device) || t("common.device"), to.mode, to.hours);
    case "evcc":
      return wallboxText(str(from.loadpoint) || t("settings.controlLog.wallbox"), to.action, to.value);
    case "remote_access":
      return ({
        login: t("settings.controlLog.remoteLogin"), logout: t("settings.controlLog.remoteLogout"),
        https_on: t("settings.controlLog.remoteHttpsOn"), https_off: t("settings.controlLog.remoteHttpsOff"),
      } as Record<string, string>)[str(to.remote_access)] ?? t("settings.controlLog.unknown", { action: e.action });
    case "token_created": {
      const token = { ...details, ...to };
      return token.scope === "control" ? t("settings.controlLog.tokenCreatedControl", { name: str(token.name) })
        : t("settings.controlLog.tokenCreatedRead", { name: str(token.name) });
    }
    case "token_revoked":
      return t("settings.controlLog.tokenRevoked", { name: str(details.name ?? from.name) });
    case "outage_removed":
      return t("settings.controlLog.outageRemoved", { when: str(from.outage) || unknown() });
    case "update":
      return t("settings.controlLog.update", { from: version(details.from), to: version(details.to) });
    default:
      return t("settings.controlLog.unknown", { action: e.action });
  }
}

function by(e: LogEntry, details: Values): string {
  if (typeof details.source === "string" && details.source) { // "Home Assistant (Zugang für Apps)", made by the server
    const app = /^(.*) \(Zugang für Apps\)$/.exec(details.source)?.[1];
    return app ? t("settings.controlLog.byAppAccess", { name: app }) : details.source;
  }
  switch (e.action) {
    case "update": return (details.by ?? e.result) === "auto" ? t("settings.controlLog.byNight") : t("settings.controlLog.byApp");
    case "grid_charging": return t("settings.controlLog.byAuto");
    case "consumer": return e.result.includes("von Hand") ? t("settings.controlLog.byApp") : t("settings.controlLog.byAuto");
    case "battery_settings_check": case "export_limit_check": return t("settings.controlLog.byOtherDevice");
    default: return t("settings.controlLog.byApp");
  }
}

function status(e: LogEntry): LogStatus {
  if (e.dry_run) return "test";
  if (/^Fehler|fehlgeschlagen/.test(e.result)) return "error";
  if (e.action.endsWith("_check") || e.result.startsWith("Rücklesen abweichend")) return "warning";
  return "ok";
}

/** Never throws: an entry of an unknown or broken shape becomes a general sentence (#153). */
export function describe(e: LogEntry): DescribedEntry {
  const entry = { ...e, action: str(e.action), result: str(e.result) };
  const details = obj(entry.details);
  try {
    return { text: sentence(entry, details, obj(details.from), obj(details.to)), by: by(entry, details), status: status(entry),
      note: e.note ?? null };
  } catch {
    return { text: t("settings.controlLog.unknown", { action: entry.action }), by: "", status: status(entry), note: e.note ?? null };
  }
}

export function statusLabel(s: LogStatus): string {
  return { ok: t("settings.controlLog.statusOk"), test: t("settings.controlLog.statusTest"),
    warning: t("settings.controlLog.statusWarning"), error: t("settings.controlLog.statusError") }[s];
}

/** The whole log as CSV for Excel or LibreOffice: ";" where the decimal separator is ",", with BOM for the umlauts. */
export function logCsv(entries: LogEntry[]): string {
  const separator = (1.5).toLocaleString(LOCALE).includes(",") ? ";" : ",";
  const day = (ts: number) => new Date(ts * 1000).toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit", year: "numeric", timeZone: timeZone() });
  const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: timeZone() });
  const head = [t("settings.controlLog.csvDate"), t("settings.controlLog.csvTime"), t("settings.controlLog.csvEvent"), t("settings.controlLog.csvBy"),
    t("settings.controlLog.csvResult"), t("settings.controlLog.csvNote"), t("settings.controlLog.technicalDetails")];
  const rows = entries.map((e) => {
    const d = describe(e);
    return [day(e.ts), clock(e.ts), d.text, d.by, statusLabel(d.status), d.note ?? "",
      `${e.action} ${JSON.stringify(e.details)} ${e.result}`.trim()];
  });
  return csv([head, ...rows], separator);
}
