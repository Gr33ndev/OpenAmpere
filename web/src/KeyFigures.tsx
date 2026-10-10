// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { Summary } from "./api";
import { energyKwh, percent } from "./format";
import { LOCALE, t, tx } from "./i18n";

const euro = (v: number | null | undefined) =>
  v == null ? "–" : v.toLocaleString(LOCALE, { style: "currency", currency: "EUR" });

/** The few numbers that matter for a period, details on request. Used on the start page and in the analysis. */
export function KeyFigures({ summary, title }: { summary: Summary | null; title?: string }) {
  const e = summary?.energy_wh;
  return (
    <div className="card key-figures">
      {title && <div className="key-title">{title}</div>}
      <div className="key-row">
        <div><span className="key-label"><i className="dot" style={{ background: "var(--pv)" }} />{t("common.generated")}</span><strong>{energyKwh(e?.pv)}</strong></div>
        <div><span className="key-label"><i className="dot" style={{ background: "var(--house)" }} />{t("common.consumed")}</span><strong>{energyKwh(e?.load)}</strong></div>
        <div><span className="key-label"><i className="dot" style={{ background: "var(--battery)" }} />{t("common.selfSufficient")}</span>
          <strong>{percent(summary?.autarky, true)}</strong></div>
      </div>
      {summary?.money && (
        <p className="key-money">{tx("report.keyFigures.savings", { amount: <strong>{euro(summary.money.savings_eur)}</strong> })}</p>
      )}
      <details className="key-details">
        <summary>{t("report.keyFigures.allValues")}</summary>
        <dl className="facts">
          <dt>{t("report.keyFigures.toGrid")}</dt><dd>{energyKwh(e?.grid_export)}</dd>
          <dt>{t("report.keyFigures.fromGrid")}</dt><dd>{energyKwh(e?.grid_import)}</dd>
          <dt>{t("report.keyFigures.intoBattery")}</dt><dd>{energyKwh(e?.battery_charge)}</dd>
          <dt>{t("report.keyFigures.fromBattery")}</dt><dd>{energyKwh(e?.battery_discharge)}</dd>
          <dt>{t("report.keyFigures.selfConsumed")}</dt><dd>{percent(summary?.self_consumption, true)}</dd>
          {!!summary?.conversion_loss_wh && summary.conversion_loss_wh >= 50 && <>
            <dt>{t("report.keyFigures.inverterLosses")}</dt><dd>{energyKwh(summary.conversion_loss_wh)}</dd></>}
          {summary?.money && <><dt>{t("report.keyFigures.feedInPayment")}</dt><dd>{euro(summary.money.feed_in_eur)}</dd></>}
          {summary?.money && <><dt>{t("report.keyFigures.costGridImport")}</dt><dd>{euro(summary.money.grid_cost_eur)}</dd></>}
          {!!summary?.money?.base_fee_eur && <><dt>{t("common.standingCharge")}</dt><dd>{euro(summary.money.base_fee_eur)}</dd></>}
          {summary?.money?.net_cost_eur != null && <><dt>{t("report.keyFigures.netElectricityCost")}</dt><dd>{euro(summary.money.net_cost_eur)}</dd></>}
        </dl>
        {!!summary?.conversion_loss_wh && summary.conversion_loss_wh >= 50 && <p className="hint">{t("report.keyFigures.lossesHint")}</p>}
        {summary?.money && <p className="hint">{t("report.keyFigures.savingsHint")}</p>}
      </details>
    </div>
  );
}
