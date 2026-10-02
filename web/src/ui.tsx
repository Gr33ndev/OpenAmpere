import { useEffect, useState, type ReactNode } from "react";
import { Chevron } from "./icons";

export function Segmented<T extends string>({ value, options, onChange, disabled }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; disabled?: boolean;
}) {
  return (
    <div className="segmented">
      {options.map(([key, label]) => (
        <button key={key} type="button" className={value === key ? "active" : ""} disabled={disabled}
          onClick={() => onChange(key)}>{label}</button>
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
      <button className="back" onClick={onBack} aria-label="Zurück"><Chevron dir="left" /></button>
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
      <span className="field-label">{label}{locked && <span className="lock">fest eingestellt</span>}</span>
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

export function Slider({ value, min, max, step = 1, unit, onChange, disabled }: {
  value: number; min: number; max: number; step?: number; unit: string; onChange: (v: number) => void; disabled?: boolean;
}) {
  return (
    <div className="slider">
      <input type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
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
      {busy ? "Bitte warten …" : children}
    </button>
  );
}

/** Loading / error placeholder with a retry button. */
export function LoadState({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  if (!error) return <p className="hint">Lade …</p>;
  return (
    <Notice kind="error">
      <div>{error}</div>
      <div className="hint">Neuer Versuch läuft automatisch.</div>
      {onRetry && <button className="link" onClick={onRetry}>Jetzt erneut versuchen</button>}
    </Notice>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "warn" | "ok" | "error"; children: ReactNode }) {
  return <div className={`notice ${kind}`}>{children}</div>;
}

export function Dialog({ title, children, confirm, cancel = "Abbrechen", danger, disabled, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirm: string; cancel?: string; danger?: boolean; disabled?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <div className="overlay" onClick={onCancel}>
      <div className="dialog" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
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
