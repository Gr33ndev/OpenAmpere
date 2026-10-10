// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useEffect, useState } from "react";
import { putJson, useResource } from "./api";
import { num, todayIso } from "./format";
import { list, LOCALE, t } from "./i18n";
import { navigate } from "./route";
import type { PageProps } from "./SettingsPages";
import { AmountInput, Button, Field, LoadState, SubPage, toast, Unsaved } from "./ui";

type Kind = "import" | "export";
type Payment = { from: string; eur: number };
export type BillingSettings = Record<Kind, { start_month: number; payments: Payment[] }>;
export type BillingYear = {
  from: string; to: string; months_paid: number; paid_eur: number; yearly_payments_eur: number;
  so_far_kwh: number; so_far_eur: number; estimated_before: string | null; projected_kwh: number; projected_eur: number;
  method: "last_year" | "typical" | "none"; balance_now_eur: number; balance_end_eur: number;
  fitting_payment_eur: number; incomplete: boolean;
  paid_to_date_eur: number; balance_today_eur: number; missing_days: number;
  /** the grid operator's meter values replace the inverter's for these days (#60) */
  meter: { source: string; from: string; until: string; kwh: number; deviation_percent: number | null } | null;
};
export type Billing = { settings: BillingSettings; status: Record<Kind, BillingYear | null> };

const MONTHS = [t("report.months.january"), t("report.months.february"), t("report.months.march"), t("report.months.april"), t("report.months.may"), t("report.months.june"), t("report.months.july"), t("report.months.august"), t("report.months.september"),
  t("report.months.october"), t("report.months.november"), t("report.months.december")];
const TEXT: Record<Kind, { title: string; hint: string }> = {
  import: { title: t("report.text.gridImport"), hint: t("report.text.importHint") },
  export: { title: t("common.feedIn"), hint: t("report.text.feedInHint") },
};
const euro = (v: number) => v.toLocaleString(LOCALE, { style: "currency", currency: "EUR" });
const dateLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric" });
const thisMonth = () => todayIso().slice(0, 7); // in the plant's time zone, not UTC

/** Settings: the monthly prepayments and when the billing year starts, for grid power and feed-in. */
export function BillingPage({ onBack }: PageProps) {
  const { data, error, reload, setData } = useResource<Billing>("/api/billing");
  const [form, setForm] = useState<BillingSettings | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setForm(data.settings); }, [data]);
  const dirty = !!data && !!form && JSON.stringify(form) !== JSON.stringify(data.settings);
  const update = (kind: Kind, patch: Partial<BillingSettings[Kind]>) => setForm((f) => f && { ...f, [kind]: { ...f[kind], ...patch } });

  const save = async () => {
    setBusy(true);
    try {
      setData(await putJson<Billing>("/api/billing", { settings: form }));
      toast(t("common.saved"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title={t("common.advancePayments")} onBack={onBack}>
      <p className="hint">{t("report.billingPage.intro")}</p>
      {!form && <LoadState error={error} onRetry={reload} />}
      {form && (["import", "export"] as Kind[]).map((kind) => (
        <div key={kind}>
          <div className="section-title">{TEXT[kind].title}</div>
          <div className="card form">
            <p className="hint">{TEXT[kind].hint}</p>
            <Field label={t("report.billingPage.billingYearStarts")}>
              <select className="input" value={form[kind].start_month} onChange={(e) => update(kind, { start_month: Number(e.target.value) })}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </Field>
            {form[kind].payments.map((p, i) => (
              <div className="field-row" key={i}>
                <Field label={t("report.billingPage.fromMonth")}><input className="input" type="month" value={p.from}
                  onChange={(e) => update(kind, { payments: form[kind].payments.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)) })} /></Field>
                <Field label={t("report.billingPage.perMonth")}><div className="input-unit"><AmountInput value={p.eur}
                  format={(v) => v.toLocaleString(LOCALE, { useGrouping: false, maximumFractionDigits: 20 })}
                  onChange={(v) => update(kind, { payments: form[kind].payments.map((x, j) => (j === i
                    ? { ...x, eur: v ?? 0 } : x)) })} /><span>€</span></div></Field>
                <button type="button" className="link danger-link" aria-label={t("report.billingPage.removeAdvancePayment")}
                  onClick={() => update(kind, { payments: form[kind].payments.filter((_, j) => j !== i) })}>{t("common.remove")}</button>
              </div>
            ))}
            <button type="button" className="link" onClick={() => update(kind, { payments: [...form[kind].payments,
              { from: thisMonth(), eur: form[kind].payments[form[kind].payments.length - 1]?.eur ?? 0 }] })}>
              {form[kind].payments.length ? t("report.billingPage.addChange") : t("report.billingPage.enterAdvancePayment")}</button>
          </div>
        </div>
      ))}
      {form && <Button busy={busy} disabled={!dirty} onClick={save}>{t("common.save")}</Button>}
      <Unsaved show={dirty} />
      <p className="hint">{t("report.billingPage.changeHint")}</p>
    </SubPage>
  );
}

const lastDay = (to: string) => new Date(new Date(`${to}T12:00:00`).getTime() - 86_400_000).toISOString().slice(0, 10);

/** One kind of the billing as it stands today: what was used or earned so far against the prepayments up to now (#59). */
function TodayRows({ kind, year }: { kind: Kind; year: BillingYear }) {
  const diff = year.balance_today_eur;
  return (
    <>
      <dt className="billing-group">{TEXT[kind].title} <span className="meta">{t("report.todayRows.since", { date: dateLabel(year.from) })}</span></dt><dd />
      {kind === "import" ? <>
        <dt>{t("report.todayRows.paidSoFar")}</dt><dd>{euro(year.paid_to_date_eur)}</dd>
        <dt>{t("report.todayRows.cost", { energy: `${num(year.so_far_kwh, 2)}\u00a0kWh` })}</dt><dd>− {euro(year.so_far_eur)}</dd>
      </> : <>
        <dt>{t("report.todayRows.payment", { energy: `${num(year.so_far_kwh, 2)}\u00a0kWh` })}</dt><dd>{euro(year.so_far_eur)}</dd>
        <dt>{t("report.todayRows.paidSoFar")}</dt><dd>− {euro(year.paid_to_date_eur)}</dd>
      </>}
      <dt className="sub">{t("report.todayRows.difference")}</dt><dd className={`sub ${diff >= 0 ? "good-text" : "bad-text"}`}>{diff >= 0 ? "+" : "−"} {euro(Math.abs(diff))}</dd>
    </>
  );
}

/** The billing as it stands today, one block for grid power and feed-in, without a forecast (#59). */
export function BillingSection() {
  const { data } = useResource<Billing>("/api/billing");
  if (!data) return null;
  const kinds = (["import", "export"] as Kind[]).filter((k) => data.status[k]);
  if (!kinds.length) return (
    <>
      <div className="section-title">{t("common.advancePayments")}</div>
      <div className="card">
        <p>{t("report.billingSection.emptyState")}</p>
        <button type="button" className="link" onClick={() => navigate("more/billing")}>{t("report.billingSection.enterAdvancePayments")}</button>
      </div>
    </>
  );
  const years = kinds.map((k) => [k, data.status[k]!] as const);
  const total = years.reduce((sum, [, y]) => sum + y.balance_today_eur, 0);
  const even = Math.abs(total) < 5;
  const missing = Math.max(...years.map(([, y]) => y.missing_days));
  const estimated = years.map(([, y]) => y.estimated_before).filter(Boolean).sort()[0];
  const metered = years.flatMap(([k, y]) => (y.meter ? [[k, y.meter] as const] : []));
  const deviations = metered.filter(([, m]) => m.deviation_percent != null && Math.abs(m.deviation_percent) >= 0.5)
    .map(([k, m]) => {
      const percent = num(Math.abs(m.deviation_percent!), 1);
      if (k === "import") return m.deviation_percent! < 0 ? t("report.billingSection.importLess", { percent }) : t("report.billingSection.importMore", { percent });
      return m.deviation_percent! < 0 ? t("report.billingSection.feedInLess", { percent }) : t("report.billingSection.feedInMore", { percent });
    });
  return (
    <>
      <div className="section-title">{t("common.advancePayments")}</div>
      <div className="card key-figures billing-card">
        <div>
          <span className="key-label">{t("report.billingSection.asOfToday")}</span>
          <strong className={`billing-headline ${even ? "" : total > 0 ? "good" : "bad"}`}>
            {even ? t("report.billingSection.advancePaymentsFit") : total > 0 ? t("report.billingSection.amountCredit", { amount: euro(total) })
              : t("report.billingSection.amountOwed", { amount: euro(-total) })}</strong>
        </div>
        <dl className="facts billing-sum">
          {years.map(([k, y]) => <TodayRows key={k} kind={k} year={y} />)}
          {years.length > 1 && <><dt className="sum">{t("report.billingSection.total")}</dt><dd className="sum">{total >= 0 ? "+" : "−"} {euro(Math.abs(total))}</dd></>}
        </dl>
        <p className="hint">{kinds.includes("export")
          ? t("report.billingSection.explanation")
          : t("report.billingSection.explanationImportOnly")}</p>
        {missing > 0 && <p className="hint warn-text">{t("report.billingSection.missingDays", { count: missing })}</p>}
        {estimated && <p className="hint">{t("report.billingSection.estimatedBefore", { date: dateLabel(estimated) })}</p>}
        {metered.length > 0 && (
          <p className="hint">{t("report.billingSection.meterSource", {
            date: dateLabel(metered.map(([, m]) => m.until).sort()[0]), source: metered[0][1].source })}
            {deviations.length > 0 && ` ${t("report.billingSection.deviations", { deviations: list(deviations) })}`}</p>
        )}
        <p className="hint">{metered.length ? "" : `${t("report.billingSection.meterDisclaimer")} `}
          {t("report.billingSection.yearEnds", { date: dateLabel(lastDay(years[0][1].to)) })}</p>
        {!metered.length && (
          <button type="button" className="link" onClick={() => navigate("more/gridmeter")}>{t("report.billingSection.fetchMeterReadings")}</button>
        )}
      </div>
    </>
  );
}
