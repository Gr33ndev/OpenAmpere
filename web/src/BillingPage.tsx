import { useEffect, useState } from "react";
import { putJson, useResource } from "./api";
import { num } from "./format";
import { navigate } from "./route";
import type { PageProps } from "./SettingsPages";
import { Button, Field, LoadState, SubPage, toast, Unsaved } from "./ui";

type Kind = "import" | "export";
type Payment = { from: string; eur: number };
export type BillingSettings = Record<Kind, { start_month: number; payments: Payment[] }>;
export type BillingYear = {
  from: string; to: string; months_paid: number; paid_eur: number; yearly_payments_eur: number;
  so_far_kwh: number; so_far_eur: number; estimated_before: string | null; projected_kwh: number; projected_eur: number;
  method: "last_year" | "typical" | "none"; balance_now_eur: number; balance_end_eur: number;
  fitting_payment_eur: number; incomplete: boolean;
};
export type Billing = { settings: BillingSettings; status: Record<Kind, BillingYear | null> };

const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const TEXT: Record<Kind, { title: string; who: string; hint: string }> = {
  import: { title: "Strombezug", who: "an deinen Stromanbieter",
    hint: "Der monatliche Abschlag an deinen Stromanbieter. Steht auf der letzten Jahresrechnung oder im Kundenportal." },
  export: { title: "Einspeisung", who: "vom Netzbetreiber",
    hint: "Die monatliche Abschlagszahlung für deine Einspeisevergütung. Steht in der Abrechnung des Netzbetreibers." },
};
const euro = (v: number) => v.toLocaleString("de-DE", { style: "currency", currency: "EUR" });
const dateLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("de-DE", { day: "numeric", month: "long", year: "numeric" });
const thisMonth = () => new Date().toISOString().slice(0, 7);

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
      toast("Gespeichert");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SubPage title="Abschläge" onBack={onBack}>
      <p className="hint">Trag deine monatlichen Abschläge ein. OpenAmpere vergleicht sie mit deinem Verbrauch und deiner
        Einspeisung und rechnet bis zur Jahresabrechnung hoch. Das Ergebnis steht in der Auswertung.</p>
      {!form && <LoadState error={error} onRetry={reload} />}
      {form && (["import", "export"] as Kind[]).map((kind) => (
        <div key={kind}>
          <div className="section-title">{TEXT[kind].title}</div>
          <div className="card form">
            <p className="hint">{TEXT[kind].hint}</p>
            <Field label="Abrechnungsjahr beginnt im">
              <select className="input" value={form[kind].start_month} onChange={(e) => update(kind, { start_month: Number(e.target.value) })}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </Field>
            {form[kind].payments.map((p, i) => (
              <div className="field-row" key={i}>
                <Field label="Ab Monat"><input className="input" type="month" value={p.from}
                  onChange={(e) => update(kind, { payments: form[kind].payments.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)) })} /></Field>
                <Field label="Pro Monat"><div className="input-unit"><input className="input" inputMode="decimal"
                  value={String(p.eur).replace(".", ",")}
                  onChange={(e) => update(kind, { payments: form[kind].payments.map((x, j) => (j === i
                    ? { ...x, eur: Number(e.target.value.replace(",", ".")) || 0 } : x)) })} /><span>€</span></div></Field>
                <button className="link danger-link" aria-label="Abschlag entfernen"
                  onClick={() => update(kind, { payments: form[kind].payments.filter((_, j) => j !== i) })}>Entfernen</button>
              </div>
            ))}
            <button className="link" onClick={() => update(kind, { payments: [...form[kind].payments,
              { from: thisMonth(), eur: form[kind].payments[form[kind].payments.length - 1]?.eur ?? 0 }] })}>
              {form[kind].payments.length ? "Geänderten Abschlag hinzufügen" : "Abschlag eintragen"}</button>
          </div>
        </div>
      ))}
      {form && <Button busy={busy} disabled={!dirty} onClick={save}>Speichern</Button>}
      <Unsaved show={dirty} />
      <p className="hint">Ändert sich ein Abschlag, trag den neuen mit dem Monat ein, ab dem er gilt. Die Preise kommen aus
        deinem Stromtarif (Mehr → Stromtarif), auch der Grundpreis.</p>
    </SubPage>
  );
}

function YearCard({ kind, year }: { kind: Kind; year: BillingYear }) {
  const end = year.balance_end_eur;
  const back = end >= 0;
  const headline = Math.abs(end) < 5 ? "Abschläge passen" : kind === "import"
    ? (back ? `${euro(end)} Guthaben` : `${euro(-end)} Nachzahlung`)
    : (back ? `${euro(end)} Nachzahlung an dich` : `${euro(-end)} zu viel ausgezahlt`);
  const lastDay = new Date(new Date(`${year.to}T12:00:00`).getTime() - 86_400_000).toISOString().slice(0, 10);
  return (
    <div className="card key-figures billing-card">
      <div className="key-title">{TEXT[kind].title} · {dateLabel(year.from)} bis {dateLabel(lastDay)}</div>
      <div>
        <span className="key-label">Voraussichtlich bei der Abrechnung</span>
        <strong className={`billing-headline ${Math.abs(end) < 5 ? "" : back ? "good" : "bad"}`}>{headline}</strong>
      </div>
      <dl className="facts">
        <dt>{kind === "import" ? "Bisher verbraucht" : "Bisher eingespeist"}</dt>
        <dd>{num(year.so_far_kwh, 0)} kWh · {euro(year.so_far_eur)}</dd>
        <dt>Abschläge bisher</dt><dd>{euro(year.paid_eur)} ({year.months_paid} {year.months_paid === 1 ? "Monat" : "Monate"})</dd>
        <dt>Stand heute</dt>
        <dd>{year.balance_now_eur >= 0 ? `${euro(year.balance_now_eur)} im Plus` : `${euro(-year.balance_now_eur)} im Minus`}</dd>
        <dt>Hochrechnung Jahr</dt><dd>{num(year.projected_kwh, 0)} kWh · {euro(year.projected_eur)}</dd>
        <dt>Abschläge im Jahr</dt><dd>{euro(year.yearly_payments_eur)}</dd>
        <dt>Passender Abschlag</dt><dd>etwa {euro(year.fitting_payment_eur)} pro Monat</dd>
      </dl>
      <p className="hint">
        {year.method === "last_year" ? "Die restlichen Monate sind mit deinen Werten aus dem Vorjahr gerechnet. "
          : "Die restlichen Monate sind nach dem typischen Jahresverlauf geschätzt, ab nächstem Jahr mit deinen eigenen Werten. "}
        {year.estimated_before ? `Vor dem ${dateLabel(year.estimated_before)} hat OpenAmpere noch nicht gemessen, diese Zeit ist geschätzt. ` : ""}
        {kind === "import" ? "Enthalten ist der Grundpreis aus deinem Stromtarif." : ""}
      </p>
    </div>
  );
}

/** Forecast of the annual bills, for the analysis page. */
export function BillingSection() {
  const { data } = useResource<Billing>("/api/billing");
  if (!data) return null;
  const years = (["import", "export"] as Kind[]).filter((k) => data.status[k]);
  return (
    <>
      <div className="section-title">Jahresabrechnung</div>
      {years.length ? years.map((k) => <YearCard key={k} kind={k} year={data.status[k]!} />) : (
        <div className="card">
          <p>Trag deine monatlichen Abschläge ein, dann sagt OpenAmpere voraus, ob bei der Jahresabrechnung Geld zurückkommt.</p>
          <button className="link" onClick={() => navigate("more/billing")}>Abschläge eintragen</button>
        </div>
      )}
    </>
  );
}
