const nf1 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

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
  return new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: zone });
}

/** YYYY-MM-DD of a moment in the plant's time zone. */
export function dayOf(ts: number): string {
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: zone })
    .format(new Date(ts * 1000));
}

/** "Heute, 16:32:39" / "Gestern, …" / "01.10.2026, …" */
export function updatedLabel(ts: number): string {
  const now = Date.now() / 1000;
  const day = dayOf(ts) === dayOf(now) ? "Heute" : dayOf(ts) === dayOf(now - 86_400) ? "Gestern"
    : new Date(ts * 1000).toLocaleDateString("de-DE", { timeZone: zone });
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

/** Number with fixed decimals in German notation, e.g. 2,6 */
export function num(value: number | null | undefined, digits = 1): string {
  if (value == null) return "–";
  return value.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
