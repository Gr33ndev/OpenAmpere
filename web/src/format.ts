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

export function time(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** "Heute, 16:32:39" / "Gestern, …" / "01.10.2026, …" */
export function updatedLabel(ts: number): string {
  const d = new Date(ts * 1000);
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86_400_000);
  const day = days === 0 ? "Heute" : days === 1 ? "Gestern" : d.toLocaleDateString("de-DE");
  return `${day}, ${time(ts)}`;
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
