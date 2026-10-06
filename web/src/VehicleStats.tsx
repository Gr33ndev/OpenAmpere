import { useState } from "react";
import { useResource } from "./api";
import { num } from "./format";
import { LOCALE, t, tx } from "./i18n";
import { Segmented } from "./ui";
import type { EvccSession, EvccView } from "./WallboxPage";

export type VehicleStat = {
  vehicle: string; sessions: number; energy_kwh: number; solar_pct: number | null; cost_eur: number | null;
  km: number | null; consumption_kwh_100km: number | null; solar_km: number | null;
  cost_100km_eur: number | null; grid_cost_100km_eur: number | null;
};

const time = (s: EvccSession) => Date.parse(s.created ?? "");
const sum = (list: EvccSession[], f: (s: EvccSession) => number) => list.reduce((a, s) => a + f(s), 0);
const solarKwh = (s: EvccSession) => (s.energy_kwh ?? 0) * (s.solar_pct ?? 0) / 100;

/**
 * Charging sessions summed up per vehicle. The distance comes from the odometer that evcc stores with each
 * session: what is charged at a session replaces what was driven since the session before.
 */
export function vehicleStats(sessions: EvccSession[], since: number): VehicleStat[] {
  const groups = new Map<string, EvccSession[]>();
  for (const s of sessions) {
    if (!s.energy_kwh || !(time(s) >= since)) continue;
    groups.set(s.vehicle ?? "", [...(groups.get(s.vehicle ?? "") ?? []), s]);
  }
  return [...groups].map(([vehicle, list]) => {
    list.sort((a, b) => time(a) - time(b));
    const energy = sum(list, (s) => s.energy_kwh ?? 0);
    const withKm = list.filter((s) => s.odometer_km != null);
    const stat: VehicleStat = {
      vehicle, sessions: list.length, energy_kwh: energy,
      solar_pct: list.some((s) => s.solar_pct != null) ? (sum(list, solarKwh) / energy) * 100 : null,
      cost_eur: list.every((s) => s.cost_eur != null) ? sum(list, (s) => s.cost_eur ?? 0) : null,
      km: null, consumption_kwh_100km: null, solar_km: null, cost_100km_eur: null, grid_cost_100km_eur: null,
    };
    if (withKm.length >= 2) {
      const first = withKm[0], last = withKm[withKm.length - 1];
      const km = last.odometer_km! - first.odometer_km!;
      const driven = list.filter((s) => time(s) > time(first) && time(s) <= time(last));
      const used = sum(driven, (s) => s.energy_kwh ?? 0);
      if (km > 0 && used > 0) {
        stat.km = km;
        stat.consumption_kwh_100km = (used / km) * 100;
        stat.solar_km = km * (sum(driven, solarKwh) / used);
        if (driven.every((s) => s.cost_eur != null && s.grid_cost_eur != null)) {
          stat.cost_100km_eur = (sum(driven, (s) => s.cost_eur ?? 0) / km) * 100;
          stat.grid_cost_100km_eur = (sum(driven, (s) => s.grid_cost_eur ?? 0) / km) * 100;
        }
      }
    }
    return stat;
  }).sort((a, b) => b.energy_kwh - a.energy_kwh);
}

const euro = (v: number) => v.toLocaleString(LOCALE, { style: "currency", currency: "EUR" });
const PERIODS: ["30" | "365" | "all", string][] = [["30", t("report.periods.thirtyDays")], ["365", t("report.periods.twelveMonths")], ["all", t("report.periods.allTime")]];

/** Driving with the sun: distance, consumption and cost per 100 km from the evcc sessions, for the analysis page. */
export function VehicleStats() {
  const { data: view } = useResource<EvccView>("/api/evcc");
  const { data } = useResource<{ sessions: EvccSession[] }>(view?.state ? "/api/evcc/sessions?limit=500" : null);
  const [period, setPeriod] = useState<"30" | "365" | "all">("30");
  if (!data?.sessions.length) return null;
  const stats = vehicleStats(data.sessions, period === "all" ? 0 : Date.now() - Number(period) * 86_400_000);
  return (
    <>
      <div className="section-title">{t("report.vehicleStats.title")}</div>
      <Segmented value={period} onChange={setPeriod} options={PERIODS} />
      {!stats.length && <p className="hint">{t("report.vehicleStats.emptyState")}</p>}
      {stats.map((st) => (
        <div className="card key-figures vehicle-stats" key={st.vehicle}>
          <div className="key-title">{st.vehicle || t("report.vehicleStats.unknownVehicle")}</div>
          {st.km != null ? (
            <div className="key-row">
              <div><span className="key-label">{t("report.vehicleStats.driven")}</span><strong>{num(st.km, 0)} km</strong></div>
              <div><span className="key-label"><i className="dot" style={{ background: "var(--pv)" }} />{t("report.vehicleStats.solarDistance")}</span>
                <strong>{num(st.solar_km, 0)} km</strong></div>
              <div><span className="key-label">{t("common.consumption")}</span><strong>{num(st.consumption_kwh_100km, 1)}</strong>
                <span className="key-unit"> kWh/100 km</span></div>
            </div>
          ) : (
            <div className="key-row">
              <div><span className="key-label">{t("common.charged")}</span><strong>{num(st.energy_kwh, 0)} kWh</strong></div>
              {st.solar_pct != null && <div><span className="key-label"><i className="dot" style={{ background: "var(--pv)" }} />{t("report.vehicleStats.solarShare")}</span>
                <strong>{num(st.solar_pct, 0)} %</strong></div>}
            </div>
          )}
          {st.cost_100km_eur != null && st.grid_cost_100km_eur != null && (
            <p className="key-money">{tx("report.vehicleStats.costPer100Km",
              { cost: euro(st.cost_100km_eur), gridCost: <strong>{euro(st.grid_cost_100km_eur)}</strong> })}</p>
          )}
          <dl className="facts">
            <dt>{t("common.charged")}</dt><dd>{t("report.vehicleStats.energySessions", { energy: num(st.energy_kwh, 1), count: st.sessions })}</dd>
            {st.solar_pct != null && <><dt>{t("report.vehicleStats.ofWhichSolar")}</dt><dd>{num(st.solar_pct, 0)} %</dd></>}
            {st.cost_eur != null && <><dt>{t("report.vehicleStats.cost")}</dt><dd>{euro(st.cost_eur)}</dd></>}
          </dl>
          {st.km == null && <p className="hint">{t("report.vehicleStats.distanceHint")}</p>}
        </div>
      ))}
      <p className="hint">{t("report.vehicleStats.hint")}</p>
    </>
  );
}
