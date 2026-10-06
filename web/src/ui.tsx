import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { t } from "./i18n";
import { Chevron } from "./icons";

export function Segmented<T extends string>({ value, options, onChange, disabled }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; disabled?: boolean;
}) {
  return (
    <div className="segmented">
      {options.map(([key, label]) => (
        <button key={key} type="button" className={value === key ? "active" : ""} disabled={disabled}
          aria-pressed={value === key} onClick={() => onChange(key)}>{label}</button>
      ))}
    </div>
  );
}

export function SubPage({ title, onBack, children }: { title: string; onBack: () => void; children: ReactNode }) {
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);
  return (
    <div className="page">
      <button className="back" onClick={onBack} aria-label={t("shell.subPage.back")}><Chevron dir="left" /></button>
      <div className="page-head"><h1>{title}</h1></div>
      {children}
    </div>
  );
}

export function MenuRow({ label, hint, onClick }: { label: string; hint?: string; onClick: () => void }) {
  return (
    <button className="menu-row" onClick={onClick}>
      <span>
        {label}
        {hint && <span className="menu-hint">{hint}</span>}
      </span>
      <Chevron />
    </button>
  );
}

export function Field({ label, hint, locked, children }: {
  label: string; hint?: string; locked?: boolean; children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}{locked && <span className="lock">{t("common.fixedSetting")}</span>}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Switch({ checked, onChange, disabled, label }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string;
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      className={`switch ${checked ? "on" : ""}`} onClick={() => onChange(!checked)}>
      <span />
    </button>
  );
}

export function SwitchRow({ label, hint, checked, onChange, disabled }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <div className="switch-row">
      <div>
        <div>{label}</div>
        {hint && <div className="field-hint">{hint}</div>}
      </div>
      <Switch checked={checked} onChange={onChange} disabled={disabled} label={label} />
    </div>
  );
}

export function Checkbox({ checked, onChange, children, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; children: ReactNode; disabled?: boolean;
}) {
  return (
    <label className={`checkbox ${disabled ? "disabled" : ""}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="checkmark" aria-hidden />
      <span>{children}</span>
    </label>
  );
}

export function Slider({ value, min, max, step = 1, unit, onChange, onCommit, disabled }: {
  value: number; min: number; max: number; step?: number; unit: string; onChange: (v: number) => void;
  /** called once when the user lets go (for settings that are saved right away) */
  onCommit?: (v: number) => void; disabled?: boolean;
}) {
  const commit = (e: React.SyntheticEvent<HTMLInputElement>) => onCommit?.(Number(e.currentTarget.value));
  return (
    <div className="slider">
      <input type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit}
        style={{ "--pct": `${((value - min) / (max - min)) * 100}%` } as React.CSSProperties} />
      <span className="slider-value">{value} {unit}</span>
    </div>
  );
}

export function Button({ children, onClick, variant = "primary", disabled, busy, type = "button" }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "danger";
  disabled?: boolean; busy?: boolean; type?: "button" | "submit";
}) {
  return (
    <button type={type} className={`btn ${variant}`} onClick={onClick} disabled={disabled || busy}>
      {busy ? t("shell.button.pleaseWait") : children}
    </button>
  );
}

/** Loading / error placeholder with a retry button. */
export function LoadState({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  if (!error) return <p className="hint">{t("common.loading")}</p>;
  return (
    <Notice kind="error">
      <div>{error}</div>
      <div className="hint">{t("shell.loadState.retrying")}</div>
      {onRetry && <button className="link" onClick={onRetry}>{t("common.tryAgainNow")}</button>}
    </Notice>
  );
}

/** Field for a decimal amount with a comma, e.g. a price in ct. Keeps the text while typing (so "20," stays) and
 * shows the value formatted (20,50) when the field is left. Empty means null. */
export function AmountInput({ value, onChange, format }: {
  value: number | null; onChange: (value: number | null) => void; format: (value: number) => string;
}) {
  const shown = value == null ? "" : format(value);
  const [text, setText] = useState(shown);
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (!editing) setText(shown); }, [shown, editing]);
  return (
    <input className="input" inputMode="decimal" value={text} onFocus={() => setEditing(true)} onBlur={() => setEditing(false)}
      onChange={(e) => {
        setText(e.target.value);
        const raw = e.target.value.trim();
        const parsed = raw === "" ? null : Number(raw.replace(",", "."));
        if (parsed === null || Number.isFinite(parsed)) onChange(parsed);
      }} />
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "warn" | "ok" | "error"; children: ReactNode }) {
  return <div className={`notice ${kind}`}>{children}</div>;
}

/** Keeps keyboard focus inside a modal, closes it with Escape and gives focus back afterwards. */
export function useModal(ref: React.RefObject<HTMLElement | null>, onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const focusable = () => Array.from(el?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select, textarea, a[href], [tabindex]:not([tabindex="-1"])') ?? []);
    (focusable().find((f) => f.tagName === "INPUT") ?? focusable()[0] ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close.current(); return; }
      if (e.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [ref]);
}

export function Dialog({ title, children, confirm, cancel = t("common.cancel"), danger, disabled, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirm: string; cancel?: string; danger?: boolean; disabled?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useModal(ref, onCancel);
  return (
    <div className="overlay" onClick={onCancel}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} tabIndex={-1}
        onClick={(e) => e.stopPropagation()}>
        <h2 id={titleId}>{title}</h2>
        <div className="dialog-body">{children}</div>
        <div className="dialog-actions">
          <Button variant={danger ? "danger" : "primary"} disabled={disabled} onClick={onConfirm}>{confirm}</Button>
          <Button variant="secondary" onClick={onCancel}>{cancel}</Button>
        </div>
      </div>
    </div>
  );
}

/** Small transient message at the bottom ("snackbar"). */
let pushToast: ((text: string, kind?: "ok" | "error") => void) | null = null;
/**
 * Copies text to the clipboard. The Clipboard API exists only on HTTPS, but OpenAmpere runs on plain HTTP in the
 * home network: then a selected, invisible text field and execCommand do it (also on the iPhone). Call it
 * directly from a tap, the browser allows copying only right after a user action.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall back below */
    }
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  // in view and 16 px, otherwise iOS scrolls or zooms; invisible anyway
  Object.assign(field.style, { position: "fixed", top: "0", left: "0", opacity: "0", fontSize: "16px" });
  document.body.appendChild(field);
  field.focus();
  field.select();
  field.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  field.remove();
  return ok;
}

export function toast(text: string, kind: "ok" | "error" = "ok") {
  pushToast?.(text, kind);
}

export function ToastHost() {
  const [message, setMessage] = useState<{ text: string; kind: string } | null>(null);
  useEffect(() => {
    let timer: number | undefined;
    pushToast = (text, kind = "ok") => {
      setMessage({ text, kind });
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setMessage(null), 3500);
    };
    return () => {
      pushToast = null;
      window.clearTimeout(timer);
    };
  }, []);
  return message ? <div className={`toast ${message.kind}`} role="status">{message.text}</div> : null;
}

/** Background information that most people do not need every time: folded away. */
export function LearnMore({ summary = t("shell.learnMore.learnMore"), children }: { summary?: string; children: ReactNode }) {
  return (
    <details className="learn">
      <summary>{summary}</summary>
      <div className="learn-body">{children}</div>
    </details>
  );
}

/** Shown next to a save button while the form differs from what is stored. */
export function Unsaved({ show }: { show: boolean }) {
  return show ? <p className="hint unsaved" role="status">{t("shell.unsaved.notSavedYet")}</p> : null;
}
