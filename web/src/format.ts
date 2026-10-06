import { LOCALE, t } from "./i18n";

const nf1 = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });

export function kw(watts: number | null | undefined): string {
  if (watts == null) return "–";
  return `${nf1.format(Math.abs(watts) / 1000)} kW`;
}

export function kwh(wh: number | null | undefined): string {
  if (wh == null) return "–";
  return `${nf1.format(wh / 1000)} kWh`;
}

export function percent(value: number | null | undefined, ratio = false): string {
  if (value == null) return "–";
  return `${nf0.format(ratio ? value * 100 : value)} %`;
}

/** Times are shown in the time zone of the plant (server setting), not of the phone – e.g. while travelling. */
let zone: string | undefined;
export function setTimeZone(value: string | undefined): void {
  zone = value;
}
export const timeZone = () => zone;

export function time(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: zone });
}

/** YYYY-MM-DD of a moment in the plant's time zone. */
export function dayOf(ts: number): string {
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: zone })
    .format(new Date(ts * 1000));
}

/** "Heute, 16:32:39" / "Gestern, …" / "01.10.2026, …" */
export function updatedLabel(ts: number): string {
  const now = Date.now() / 1000;
  const day = dayOf(ts) === dayOf(now) ? t("common.today") : dayOf(ts) === dayOf(now - 86_400) ? t("shell.updatedLabel.yesterday")
    : new Date(ts * 1000).toLocaleDateString(LOCALE, { timeZone: zone });
  return `${day}, ${time(ts)}`;
}

/** Today in the plant's time zone. */
export function todayIso(): string {
  return dayOf(Date.now() / 1000);
}

export function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Cent amounts, always with two decimals (#87), e.g. 32,50 ct */
export function ct(value: number | null | undefined): string {
  return num(value, 2);
}

/** A cent or euro amount as the text of an input field: two decimals, more only if it was entered that way
 * (e.g. an EEG rate of 8,032 ct), no thousands separator. */
export function amountInput(value: number): string {
  return value.toLocaleString(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 4, useGrouping: false });
}

/** Number with fixed decimals in the app's language, e.g. 2,6 (German) or 2.6 (English) */
export function num(value: number | null | undefined, digits = 1): string {
  if (value == null) return "–";
  return value.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
