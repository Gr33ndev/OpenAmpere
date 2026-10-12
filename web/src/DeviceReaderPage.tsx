// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { Fragment, useState } from "react";
import { postJson } from "./api";
import { t } from "./i18n";
import type { PageProps } from "./SettingsPages";
import { Button, Field, LearnMore, Notice, Segmented, SubPage, toast } from "./ui";

/** Reading any Modbus device in the home network (#324): only reads, the result goes into the diagnostics report. */

type Block = { start: number; count: number; ok: boolean; words: (number | null)[] | null;
  error: "rejected" | "no_answer" | null; exception: number | null; ms: number | null;
  text?: { address: number; text: string }[] };
type ReadView = { host: string; port: number; unit: number; function: number; shared_connection: boolean; blocks: Block[] };
type Identified = { host: string; open_ports: number[]; title: string | null; mac_vendor: string | null; mac_available: boolean };
type Found = { service: string; name: string; host: string; port: number | null };
type Search = { available: boolean; devices: Found[] };

const MAX_REGISTERS = 600;
const BLOCK = 60;
const EXCEPTIONS: Record<number, string> = {
  1: t("settings.deviceReader.exception1"), 2: t("settings.deviceReader.exception2"),
  3: t("settings.deviceReader.exception3"), 4: t("settings.deviceReader.exception4"),
  6: t("settings.deviceReader.exception6"), 11: t("settings.deviceReader.exception11"),
};

/** "4096" or "0x1000" -> 4096; anything else -> null. */
export function parseNumber(text: string): number | null {
  const value = text.trim();
  if (/^0x[0-9a-f]+$/i.test(value)) return Number.parseInt(value.slice(2), 16);
  if (/^\d+$/.test(value)) return Number.parseInt(value, 10);
  return null;
}

const hex = (n: number) => `0x${n.toString(16).toUpperCase().padStart(4, "0")}`;
const signed = (n: number) => (n & 0x8000 ? n - 0x10000 : n);

function blockState(b: Block): string {
  if (b.ok) return t("settings.deviceReader.blockOk", { ms: b.ms ?? 0 });
  const reason = b.exception == null ? null
    : (EXCEPTIONS[b.exception] ?? t("settings.deviceReader.exceptionOther", { code: b.exception }));
  if (b.error === "rejected") return t("settings.deviceReader.blockRejected", { reason: reason ?? "" });
  return reason ? t("settings.deviceReader.blockNoAnswerReason", { reason }) : t("settings.deviceReader.blockNoAnswer");
}

function BlockResult({ block }: { block: Block }) {
  const end = block.start + block.count - 1;
  return (
    <div className="card">
      <p className="hint flush"><strong>{block.start}–{end}</strong> ({hex(block.start)}–{hex(end)}) · {blockState(block)}</p>
      {!!block.text?.length && (
        <dl className="facts reader-text">
          {block.text.map((x) => <Fragment key={x.address}><dt>{x.address}</dt><dd>„{x.text}“</dd></Fragment>)}
        </dl>
      )}
      {block.words && (
        <div className="table-scroll">
          <table className="register-table">
            <thead><tr><th>{t("settings.deviceReader.colAddress")}</th><th>{t("settings.deviceReader.colHex")}</th>
              <th>{t("settings.deviceReader.colValue")}</th><th>{t("settings.deviceReader.colSigned")}</th></tr></thead>
            <tbody>
              {block.words.map((w, i) => (
                <tr key={block.start + i}><td>{block.start + i}</td><td>{hex(block.start + i)}</td>
                  <td>{w == null ? "–" : w}</td><td>{w == null ? "–" : signed(w)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function DeviceReaderPage({ onBack, onNavigate }: PageProps) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("502");
  const [unit, setUnit] = useState("1");
  const [fn, setFn] = useState<"3" | "4">("3");
  const [start, setStart] = useState("");
  const [count, setCount] = useState("10");
  const [busy, setBusy] = useState<"" | "search" | "identify" | "read">("");
  const [found, setFound] = useState<Search | null>(null);
  const [identified, setIdentified] = useState<Identified | null>(null);
  const [result, setResult] = useState<ReadView | null>(null);

  const call = async <T,>(what: "search" | "identify" | "read", path: string, body: unknown, done: (r: T) => void) => {
    setBusy(what);
    try {
      done(await postJson<T>(path, body));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy("");
    }
  };
  const read = () => {
    const values = [parseNumber(port), parseNumber(unit), parseNumber(start), parseNumber(count)];
    if (values.some((v) => v == null)) {
      toast(t("settings.deviceReader.invalidNumbers"), "error");
      return;
    }
    const [p, u, s, c] = values as number[];
    void call<{ read: ReadView }>("read", "/api/diagnostics/read",
      { host: host.trim(), port: p, unit: u, function: Number(fn), start: s, count: Math.min(c, MAX_REGISTERS) },
      (r) => setResult(r.read));
  };
  const use = (d: Found) => {
    setHost(d.host);
    if (d.port) setPort(String(d.port));
  };

  return (
    <SubPage title={t("settings.deviceReader.title")} onBack={onBack}>
      <p className="hint">{t("settings.deviceReader.intro")} <strong>{t("settings.deviceReader.readOnly")}</strong></p>
      <LearnMore>
        <p className="hint">{t("settings.deviceReader.explanation")}</p>
      </LearnMore>
      <Notice kind="warn">{t("settings.deviceReader.oneClient")}</Notice>

      <div className="section-title">{t("settings.deviceReader.findTitle")}</div>
      <div className="card form">
        <p className="hint flush">{t("settings.deviceReader.searchHint")}</p>
        <Button variant="secondary" busy={busy === "search"} disabled={!!busy}
          onClick={() => void call<Search>("search", "/api/diagnostics/search", {}, setFound)}>
          {t("settings.deviceReader.searchButton")}</Button>
        {found && !found.available && <Notice kind="info">{t("settings.deviceReader.searchUnavailable")}</Notice>}
        {found?.available && !found.devices.length && <p className="hint flush">{t("settings.deviceReader.nothingFound")}</p>}
        {!!found?.devices.length && (
          <ul className="plain-list found-list">
            {found.devices.map((d) => (
              <li key={`${d.host}-${d.name}`}>
                <span><strong>{d.name}</strong> <span className="meta">{d.host}{d.port ? `:${d.port}` : ""}</span></span>
                <button type="button" className="link" onClick={() => use(d)}>{t("settings.deviceReader.use")}</button>
              </li>
            ))}
          </ul>
        )}
        <Field label={t("settings.deviceReader.address")} hint={t("settings.deviceReader.addressHint", { example: "192.168.178.50" })}>
          <input className="input" value={host} inputMode="decimal" autoComplete="off" onChange={(e) => setHost(e.target.value)} />
        </Field>
        <Button variant="secondary" busy={busy === "identify"} disabled={!!busy || !host.trim()}
          onClick={() => void call<Identified>("identify", "/api/diagnostics/identify", { host: host.trim() }, setIdentified)}>
          {t("settings.deviceReader.identifyButton")}</Button>
        {identified && (
          <dl className="facts">
            <dt>{t("settings.deviceReader.openPorts")}</dt>
            <dd>{identified.open_ports.join(", ") || t("settings.deviceReader.none")}</dd>
            <dt>{t("settings.deviceReader.pageTitle")}</dt><dd>{identified.title ?? "–"}</dd>
            <dt>{t("settings.deviceReader.macVendor")}</dt>
            <dd>{identified.mac_vendor ?? (identified.mac_available ? t("settings.deviceReader.unknown") : t("settings.deviceReader.notInApp"))}</dd>
          </dl>
        )}
        {identified && <p className="hint flush">{t("settings.deviceReader.portsHint")}</p>}
      </div>

      <div className="section-title">{t("settings.deviceReader.readTitle")}</div>
      <div className="card form">
        <Field label={t("settings.deviceReader.port")}>
          <input className="input" value={port} inputMode="numeric" onChange={(e) => setPort(e.target.value)} />
        </Field>
        <Field label={t("settings.deviceReader.unit")} hint={t("settings.deviceReader.unitHint")}>
          <input className="input" value={unit} inputMode="numeric" onChange={(e) => setUnit(e.target.value)} />
        </Field>
        <Field label={t("settings.deviceReader.function")} hint={t("settings.deviceReader.functionHint")}>
          <Segmented value={fn} onChange={setFn} options={[["3", t("settings.deviceReader.fc3")], ["4", t("settings.deviceReader.fc4")]]} />
        </Field>
        <Field label={t("settings.deviceReader.start")} hint={t("settings.deviceReader.startHint")}>
          <input className="input" value={start} autoComplete="off" placeholder="0x1000" onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label={t("settings.deviceReader.count")}
          hint={t("settings.deviceReader.countHint", { max: MAX_REGISTERS, block: BLOCK })}>
          <input className="input" value={count} inputMode="numeric" onChange={(e) => setCount(e.target.value)} />
        </Field>
        <Button busy={busy === "read"} disabled={!!busy || !host.trim() || !start.trim()} onClick={read}>
          {t("settings.deviceReader.readButton")}</Button>
      </div>

      {result && (
        <>
          <div className="section-title">{t("settings.deviceReader.result")}</div>
          {result.shared_connection && <Notice kind="info">{t("settings.deviceReader.sharedConnection", { unit: result.unit })}</Notice>}
          {result.blocks.map((b) => <BlockResult key={b.start} block={b} />)}
          <div className="card form">
            <p className="hint flush">{t("settings.deviceReader.reportHint")}</p>
            {onNavigate && <Button variant="secondary" onClick={() => onNavigate("diagnostics")}>{t("settings.deviceReader.toDiagnostics")}</Button>}
          </div>
        </>
      )}
    </SubPage>
  );
}
