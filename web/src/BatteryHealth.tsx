import { useState } from "react";
import type { Status } from "./api";
import { putJson, useResource } from "./api";
import { num, timeZone } from "./format";
import { Button, Field, Notice, toast } from "./ui";

type Extreme = { value: number; ts: number } | null;
export type BatteryHealth = {
  capacity_kwh: number | null; charged_kwh: number | null; discharged_kwh: number | null; cycles: number | null;
  efficiency_pct: number | null; soh_pct: number | null; cell_max_now_c: number | null; cell_min_now_c: number | null;
  spread_now_c: number | null; days: number; warning: string | null;
  extremes: { cell_max: Extreme; cell_min: Extreme; spread: Extreme; inverter: Extreme; battery: Extreme };
};

const when = (ts: number) => new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit",
  minute: "2-digit", timeZone: timeZone() });
const deg = (v: number | null | undefined) => (v == null ? "–" : `${num(v, 1)} °C`);

function CapacityForm({ current, onSaved }: { current: number | null; onSaved: () => void }) {
  const [value, setValue] = useState(current ? String(current).replace(".", ",") : "");
  const [busy, setBusy] = useState(false);
  const kwh = Number(value.replace(",", "."));
  const save = async () => {
    setBusy(true);
    try {
      await putJson("/api/settings", { "battery.capacity_kwh": kwh });
      toast("Gespeichert");
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="form capacity-form">
      <Field label="Nutzbare Kapazität" hint="Steht im Datenblatt oder auf dem Typenschild des Speichers.">
        <div className="input-unit"><input className="input" inputMode="decimal" value={value} placeholder="z. B. 10,4"
          onChange={(e) => setValue(e.target.value)} /><span>kWh</span></div>
      </Field>
      <Button variant="secondary" busy={busy} disabled={!(kwh > 0 && kwh <= 200) || kwh === current} onClick={() => void save()}>Speichern</Button>
    </div>
  );
}

/** Full cycles, efficiency and cell temperatures of the home battery, for "Meine Anlage". */
export function BatteryHealthCard() {
  const { data, reload } = useResource<BatteryHealth>("/api/battery/health", 60_000);
  const [edit, setEdit] = useState(false);
  if (!data) return null;
  const x = data.extremes;
  return (
    <div className="card">
      <strong>Speicher-Gesundheit</strong>
      {data.warning && <Notice kind="warn">{data.warning}</Notice>}
      <dl className="facts">
        <dt>Vollzyklen</dt>
        <dd>{data.cycles != null ? `etwa ${num(data.cycles, 0)}` : "–"}</dd>
        <dt>Wirkungsgrad</dt><dd>{data.efficiency_pct != null ? `${num(data.efficiency_pct, 0)} %` : "–"}</dd>
        {data.cell_max_now_c != null && <><dt>Zellen jetzt</dt>
          <dd>{num(data.cell_min_now_c, 1)} bis {deg(data.cell_max_now_c)}</dd></>}
        {x.cell_max && <><dt>Wärmste Zelle ({data.days <= 1 ? "bisher" : `${data.days} Tage`})</dt><dd>{deg(x.cell_max.value)} · {when(x.cell_max.ts)}</dd></>}
        {x.spread && <><dt>Größter Unterschied</dt><dd>{deg(x.spread.value)} · {when(x.spread.ts)}</dd></>}
        {x.inverter && <><dt>Wechselrichter max.</dt><dd>{deg(x.inverter.value)} · {when(x.inverter.ts)}</dd></>}
      </dl>
      {(edit || !data.capacity_kwh) ? (
        <CapacityForm current={data.capacity_kwh} onSaved={() => { setEdit(false); reload(); }} />
      ) : (
        <button className="link" onClick={() => setEdit(true)}>Kapazität ändern ({num(data.capacity_kwh, 1)} kWh)</button>
      )}
      <p className="hint">Ein Vollzyklus heißt: einmal die ganze Kapazität entladen. Viele Speicher sind für 6000 Zyklen
        ausgelegt. Der Wirkungsgrad ist entladene geteilt durch geladene Energie seit Inbetriebnahme, typisch sind 85 bis
        95 %. Die Zellen eines gesunden Speichers sind höchstens wenige Grad unterschiedlich warm.</p>
    </div>
  );
}

/** When the inverter firmware changed (an update can change registers and values). */
export function FirmwareFacts({ firmware }: { firmware: Status["firmware"] }) {
  const history = firmware?.history ?? [];
  if (!firmware?.since && !history.length) return null;
  return (
    <>
      {firmware?.since && history.length > 0 && <p className="hint">Diese Firmware meldet der Wechselrichter seit {new Date(firmware.since * 1000)
        .toLocaleDateString("de-DE", { day: "numeric", month: "long", year: "numeric", timeZone: timeZone() })}.</p>}
      {history.length > 0 && (
        <details className="advanced">
          <summary>Firmware-Änderungen ({history.length})</summary>
          <ul className="sessions">
            {[...history].reverse().map((h) => (
              <li key={h.ts}><span>{new Date(h.ts * 1000).toLocaleDateString("de-DE", { timeZone: timeZone() })}</span>
                <span>{h.old} → {h.new}</span></li>
            ))}
          </ul>
          <p className="hint">Nach einem Update einmal die Diagnose ausführen (Mehr → Diagnose). Ein Update kann Register
            ändern, dann stimmen einzelne Werte nicht mehr.</p>
        </details>
      )}
    </>
  );
}
