import { useState } from "react";
import { deleteJson, useResource } from "./api";
import { num, timeZone } from "./format";
import { LOCALE, t } from "./i18n";
import { Dialog, toast } from "./ui";

type Outage = {
  start: number; end: number; duration_s: number; soc_start: number | null; soc_end: number | null; soc_min: number | null;
  load_kwh: number | null; solar_kwh: number | null; battery_kwh: number | null; dark_since: number | null;
  gap_reason?: "battery_empty" | "no_data" | "inverter_off" | null;
};
export type OutagesView = {
  current: { start: number; soc_start: number | null; soc_last: number | null } | null;
  outages: Outage[]; count: number; total_s: number;
};

const day = (ts: number) => new Date(ts * 1000).toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric",
  timeZone: timeZone() });
const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone: timeZone() });
const duration = (s: number) => {
  const total = Math.round(s / 60), h = Math.floor(total / 60), m = total % 60; // never "1 h 60 min"
  return h ? t("report.duration.hoursMinutes", { h, m }) : t("report.duration.minutes", { m: Math.max(1, m) });
};
const pct = (v: number | null) => (v == null ? "–" : `${num(v, 0)} %`);
// recorded before #89 without a reason: a battery that never went below 15 % was not empty
const batteryEmpty = (o: Outage) => (o.gap_reason ? o.gap_reason === "battery_empty" : (o.soc_min ?? 0) <= 15);

/** Power cuts recorded from the inverter's off-grid mode: how often, how long, how far battery and sun carried (#51). */
export function OutagesSection() {
  const { data, setData } = useResource<OutagesView>("/api/outages", 60_000);
  const [removing, setRemoving] = useState<Outage | null>(null);
  if (!data || (!data.count && !data.current)) return null;
  const remove = async () => {
    if (!removing) return;
    try {
      setData(await deleteJson<OutagesView>(`/api/outages/${removing.start}`));
      toast(t("report.outagesSection.entryRemoved"));
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setRemoving(null);
  };
  return (
    <>
      <div className="section-title">{t("report.outagesSection.title")}</div>
      <div className="card">
        <p className="outage-summary">
          {t("report.outagesSection.powerCutCount", { count: data.count })}
          {data.count > 0 && <>, {t("report.outagesSection.totalDuration", { duration: duration(data.total_s) })}</>}
          {data.outages.length > 0 && <span className="meta"> {t("report.outagesSection.since", { date: day(data.outages[data.outages.length - 1].start) })}</span>}
        </p>
        {data.current && (
          <p className="hint">{t("report.outagesSection.ongoing", {
            time: time(data.current.start), from: pct(data.current.soc_start), to: pct(data.current.soc_last) })}</p>
        )}
        <ul className="sessions outages">
          {data.outages.map((o) => (
            <li key={o.start}>
              <span><strong>{t("report.outagesSection.dateTime", { date: day(o.start), time: time(o.start) })}</strong> · {duration(o.duration_s)}</span>
              <span className="meta">
                {t("report.outagesSection.battery", { from: pct(o.soc_start), to: pct(o.soc_end) })}
                {o.soc_min != null && o.soc_min < (o.soc_end ?? 101) ? ` ${t("report.outagesSection.lowestSoc", { soc: pct(o.soc_min) })}` : ""}
                {o.load_kwh != null && <> · {t("report.outagesSection.house", { energy: `${num(o.load_kwh, 2)} kWh` })}
                  {o.solar_kwh != null ? `, ${t("report.outagesSection.solarShare", { energy: `${num(Math.min(o.solar_kwh, o.load_kwh), 2)} kWh` })}` : ""}</>}
              </span>
              {o.dark_since && (o.gap_reason === "inverter_off"
                ? <span className="meta">{t("report.outagesSection.inverterOff", { time: time(o.dark_since) })}</span>
                : batteryEmpty(o)
                ? <span className="meta warn-text">{t("report.outagesSection.batteryEmpty", { time: time(o.dark_since) })}</span>
                : <span className="meta">{t("report.outagesSection.noReadings", { time: time(o.dark_since) })}</span>)}
              <button type="button" className="link" onClick={() => setRemoving(o)}>{t("report.outagesSection.notAPowerCut")}</button>
            </li>
          ))}
        </ul>
      </div>
      {removing && (
        <Dialog title={t("report.outagesSection.removeTitle")} confirm={t("common.remove")} onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
          <p>{t("report.outagesSection.removeText", {
            date: day(removing.start), time: time(removing.start) })}</p>
        </Dialog>
      )}
    </>
  );
}
