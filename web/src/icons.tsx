// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/* Own line-art icons (no third-party artwork). Colours come from CSS variables. */

type P = { size?: number };

export function SolarIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <path d="M10 14h44l-6 26H4z" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinejoin="round" />
      <path d="M8.5 27h41M29 14l-3 26M41.5 14l-4.5 26M18 14l-5 26" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <path d="M26 40v12M18 52h16" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="26" cy="54" r="2.5" fill="var(--brand)" />
    </svg>
  );
}

export function HouseIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <path d="M8 30 32 10l24 20" stroke="var(--icon-stroke)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 26v28h38V26" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinejoin="round" />
      <rect x="27" y="38" width="10" height="16" rx="1" stroke="var(--icon-stroke)" strokeWidth="2" />
      <rect x="18" y="32" width="6" height="6" rx="1" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <rect x="40" y="32" width="6" height="6" rx="1" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <path d="M26 24l6-6 6 6" stroke="var(--brand)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function BatteryIcon({ size = 64, soc }: P & { soc: number | null }) {
  const level = Math.max(0, Math.min(100, soc ?? 0));
  const h = (level / 100) * 34;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <rect x="16" y="6" width="32" height="52" rx="3" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" />
      <circle cx="32" cy="13" r="2.5" fill="var(--brand)" />
      <rect x="27" y="19" width="10" height="36" rx="2" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <rect x="28" y={54 - h} width="8" height={h} rx="1.5" fill="var(--sky)" />
      <path d="M20 58v3M44 58v3" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function GridIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <path d="M24 58 30 8h4l6 50M27 34h10M25.5 46h13M28.5 20h7" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinejoin="round" />
      <path d="M14 16h36M18 26h28" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinecap="round" />
      <path d="M26 46l12-12M38 46 26 34" stroke="var(--icon-stroke)" strokeWidth="1.2" />
      <path d="M14 16v4M50 16v4M18 26v4M46 26v4" stroke="var(--icon-stroke)" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function InverterIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <rect x="12" y="8" width="40" height="48" rx="4" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" />
      <rect x="20" y="16" width="24" height="14" rx="2" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <path d="M23 23c2.5-5 5-5 7.5 0s5 5 7.5 0" stroke="var(--brand)" strokeWidth="2" strokeLinecap="round" />
      <path d="M20 40h24M20 46h24" stroke="var(--icon-stroke)" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M22 56v4M42 56v4" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function NavHome() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" fill="currentColor" aria-hidden>
      <path d="M14 3.5 3.5 12.2V24h7.5v-7h6v7h7.5V12.2z" />
    </svg>
  );
}

export function NavReport() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="5" y="15" width="4" height="8" rx="1" />
      <rect x="12" y="12" width="4" height="11" rx="1" />
      <rect x="19" y="13" width="4" height="10" rx="1" />
      <path d="M4 11l7-5 5 3 8-5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 25.5h22" strokeLinecap="round" />
    </svg>
  );
}

export function NavMore() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" fill="currentColor" aria-hidden>
      <circle cx="6" cy="14" r="2.2" />
      <circle cx="14" cy="14" r="2.2" />
      <circle cx="22" cy="14" r="2.2" />
    </svg>
  );
}

export function Chevron({ dir = "right" }: { dir?: "left" | "right" }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden style={dir === "left" ? { transform: "scaleX(-1)" } : undefined}>
      <path d="m9 5 7 7-7 7" />
    </svg>
  );
}

export function CalendarIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
      <path d="M3.5 10h17M8 3v4M16 3v4" strokeLinecap="round" />
      <path d="M7.5 13.5h2M11 13.5h2M14.5 13.5h2M7.5 16.5h2M11 16.5h2" strokeLinecap="round" />
    </svg>
  );
}

export function CheckCircle() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
      <circle cx="10" cy="10" r="10" fill="var(--success)" />
      <path d="m5.8 10.2 2.8 2.8 5.6-5.6" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function WarnCircle() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
      <circle cx="10" cy="10" r="10" fill="var(--delight)" />
      <path d="M10 5v6M10 14.5v.5" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

export function CarIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <path d="M17 31l5-12c.8-2 2.6-3 4.6-3h10.8c2 0 3.8 1 4.6 3l5 12" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinejoin="round" />
      <path d="M22 30l3.5-8.5h13L42 30" stroke="var(--icon-stroke)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M13 48v6h8v-6M43 48v6h8v-6" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinejoin="round" />
      <rect x="8" y="30" width="48" height="18" rx="5" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" />
      <circle cx="16.5" cy="38.5" r="3" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <circle cx="47.5" cy="38.5" r="3" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <path d="M33.5 33l-3.5 6h5l-3.5 6" stroke="var(--brand)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function HeaterIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <rect x="16" y="6" width="32" height="52" rx="12" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" />
      <path d="M20 25c2.4-1.6 4.4-1.6 6 0s3.6 1.6 6 0 3.6-1.6 6 0 3.6 1.6 6 0" stroke="var(--icon-stroke)" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M27 52V34a5 5 0 0 1 10 0v18" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinecap="round" />
      <path d="M26 12c-1.5 1.6 1.5 2.4 0 4M32 11c-1.5 1.6 1.5 2.4 0 4M38 12c-1.5 1.6 1.5 2.4 0 4" stroke="var(--brand)" strokeWidth="2" strokeLinecap="round" />
      <path d="M22 58v3M42 58v3" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function PlugIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <rect x="14" y="14" width="36" height="36" rx="10" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" />
      <circle cx="32" cy="32" r="10" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <circle cx="28" cy="32" r="1.8" fill="var(--icon-stroke)" />
      <circle cx="36" cy="32" r="1.8" fill="var(--icon-stroke)" />
      <path d="M32 20v4" stroke="var(--brand)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function HeatPumpIcon({ size = 64 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <rect x="8" y="16" width="48" height="34" rx="4" fill="var(--icon-fill)" stroke="var(--icon-stroke)" strokeWidth="2" />
      <circle cx="24" cy="33" r="10" stroke="var(--icon-stroke)" strokeWidth="1.5" />
      <path d="M24 23v20M14 33h20M17 26l14 14M31 26 17 40" stroke="var(--icon-stroke)" strokeWidth="1" />
      <path d="M40 26h10M40 32h10M40 38h10" stroke="var(--icon-stroke)" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M14 50v4M50 50v4" stroke="var(--icon-stroke)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="45" cy="44" r="2" fill="var(--brand)" />
    </svg>
  );
}

export function NavDevices() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M10 3v5M18 3v5" strokeLinecap="round" />
      <path d="M7 8h14v5a7 7 0 0 1-14 0z" strokeLinejoin="round" />
      <path d="M14 20v5" strokeLinecap="round" />
      <path d="M15 11.5l-2 3h3l-2 3" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
