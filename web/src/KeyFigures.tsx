import type { Summary } from "./api";
import { kwh, percent } from "./format";

const euro = (v: number | null | undefined) =>
  v == null ? "–" : v.toLocaleString("de-DE", { style: "currency", currency: "EUR" });

/** The few numbers that matter for a period, details on request. Used on the start page and in the analysis. */
export function KeyFigures({ summary, title, open = false }: { summary: Summary | null; title?: string; open?: boolean }) {
  const e = summary?.energy_wh;
  return (
    <div className="card key-figures">
      {title && <div className="key-title">{title}</div>}
      <div className="key-row">
        <div><span className="key-label"><i className="dot" style={{ background: "var(--pv)" }} />Erzeugt</span><strong>{kwh(e?.pv)}</strong></div>
        <div><span className="key-label"><i className="dot" style={{ background: "var(--house)" }} />Verbraucht</span><strong>{kwh(e?.load)}</strong></div>
        <div><span className="key-label"><i className="dot" style={{ background: "var(--battery)" }} />Autark</span>
          <strong>{percent(summary?.autarky, true)}</strong></div>
      </div>
      {summary?.money && (
        <p className="key-money">Ersparnis etwa <strong>{euro(summary.money.savings_eur)}</strong></p>
      )}
      <details className="key-details" open={open}>
        <summary>Alle Werte</summary>
        <dl className="facts">
          <dt>Ins Netz eingespeist</dt><dd>{kwh(e?.grid_export)}</dd>
          <dt>Aus dem Netz bezogen</dt><dd>{kwh(e?.grid_import)}</dd>
          <dt>In den Speicher geladen</dt><dd>{kwh(e?.battery_charge)}</dd>
          <dt>Aus dem Speicher genutzt</dt><dd>{kwh(e?.battery_discharge)}</dd>
          <dt>Selbst genutzter Solarstrom</dt><dd>{percent(summary?.self_consumption, true)}</dd>
          {summary?.money && <><dt>Einspeisevergütung</dt><dd>{euro(summary.money.feed_in_eur)}</dd></>}
          {summary?.money && <><dt>Kosten Netzbezug</dt><dd>{euro(summary.money.grid_cost_eur)}</dd></>}
        </dl>
        {summary?.money && <p className="hint">Die Ersparnis ist eine Schätzung mit deinem Stromtarif (Mehr → Stromtarif):
          selbst genutzter Solarstrom zum Strompreis plus Einspeisevergütung.</p>}
      </details>
    </div>
  );
}
