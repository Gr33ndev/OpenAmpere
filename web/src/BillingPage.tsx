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
  paid_to_date_eur: number; balance_today_eur: number; missing_days: number;
  /** the grid operator's meter values replace the inverter's for these days (#60) */
  meter: { source: string; from: string; until: string; kwh: number; deviation_percent: number | null } | null;
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
        Einspeisung bis heute. Das Ergebnis steht in der Auswertung.</p>
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

const lastDay = (to: string) => new Date(new Date(`${to}T12:00:00`).getTime() - 86_400_000).toISOString().slice(0, 10);

/** One kind of the billing as it stands today: what was used or earned so far against the prepayments up to now (#59). */
function TodayRows({ kind, year }: { kind: Kind; year: BillingYear }) {
  const diff = year.balance_today_eur;
  return (
    <>
      <dt className="billing-group">{TEXT[kind].title} <span className="meta">seit {dateLabel(year.from)}</span></dt><dd />
      {kind === "import" ? <>
        <dt>Abschläge bis heute</dt><dd>{euro(year.paid_to_date_eur)}</dd>
        <dt>Kosten für {num(year.so_far_kwh, 0)}&nbsp;kWh</dt><dd>− {euro(year.so_far_eur)}</dd>
      </> : <>
        <dt>Vergütung für {num(year.so_far_kwh, 0)}&nbsp;kWh</dt><dd>{euro(year.so_far_eur)}</dd>
        <dt>Abschläge bis heute</dt><dd>− {euro(year.paid_to_date_eur)}</dd>
      </>}
      <dt className="sub">Differenz</dt><dd className={`sub ${diff >= 0 ? "good-text" : "bad-text"}`}>{diff >= 0 ? "+" : "−"} {euro(Math.abs(diff))}</dd>
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
      <div className="section-title">Abschläge</div>
      <div className="card">
        <p>Trag deine monatlichen Abschläge ein, dann zeigt OpenAmpere, ob sie zu deinem Verbrauch und deiner Einspeisung passen.</p>
        <button className="link" onClick={() => navigate("more/billing")}>Abschläge eintragen</button>
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
    .map(([k, m]) => `${k === "import" ? "beim Bezug" : "bei der Einspeisung"} ${num(Math.abs(m.deviation_percent!), 1)} % ${m.deviation_percent! < 0 ? "weniger" : "mehr"}`);
  return (
    <>
      <div className="section-title">Abschläge</div>
      <div className="card key-figures billing-card">
        <div>
          <span className="key-label">Stand heute</span>
          <strong className={`billing-headline ${even ? "" : total > 0 ? "good" : "bad"}`}>
            {even ? "Abschläge passen" : total > 0 ? `${euro(total)} im Plus` : `${euro(-total)} im Minus`}</strong>
        </div>
        <dl className="facts billing-sum">
          {years.map(([k, y]) => <TodayRows key={k} kind={k} year={y} />)}
          {years.length > 1 && <><dt className="sum">Zusammen</dt><dd className="sum">{total >= 0 ? "+" : "−"} {euro(Math.abs(total))}</dd></>}
        </dl>
        <p className="hint">Plus heißt: Bis heute hast du mehr Abschlag gezahlt als verbraucht{kinds.includes("export")
          ? " oder mehr eingespeist als ausgezahlt wurde" : ""}. Der laufende Monat zählt anteilig bis heute, die Kosten
          enthalten den Grundpreis aus deinem Stromtarif.</p>
        {missing > 0 && <p className="hint warn-text">An {missing} {missing === 1 ? "Tag" : "Tagen"} hat OpenAmpere keine Messwerte,
          etwa weil die Verbindung gestört war. Verbrauch und Einspeisung sind deshalb etwas zu niedrig.</p>}
        {estimated && <p className="hint">Vor dem {dateLabel(estimated)} hat OpenAmpere noch nicht gemessen, diese Zeit ist geschätzt.</p>}
        {metered.length > 0 && (
          <p className="hint">Bis {dateLabel(metered.map(([, m]) => m.until).sort()[0])} rechnet OpenAmpere mit den Zählerwerten
            von {metered[0][1].source}, danach mit denen des Wechselrichters.{deviations.length > 0 && ` Der Wechselrichter misst ${deviations.join(" und ")}.`}</p>
        )}
        <p className="hint">{metered.length ? "" : "Abgerechnet wird nach den Zählern des Netzbetreibers. Die Werte hier sind eine Orientierung. "}
          Das Abrechnungsjahr endet am {dateLabel(lastDay(years[0][1].to))}.</p>
        {!metered.length && (
          <button className="link" onClick={() => navigate("more/gridmeter")}>Zählerwerte vom Netzbetreiber abrufen</button>
        )}
      </div>
    </>
  );
}
