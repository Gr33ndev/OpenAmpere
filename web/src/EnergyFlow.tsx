import { type ReactNode, type Ref, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Device, Snapshot } from "./api";
import { DeviceIcon } from "./DevicesPage";
import { kw, num, percent } from "./format";
import { t } from "./i18n";
import { BatteryIcon, GridIcon, HouseIcon, SolarIcon } from "./icons";
import { chiptune, EASTER_EGG_CODE, type EasterEggKey, useEasterEgg } from "./easterEgg";

const IDLE_W = 50; // below this the value shows as 0,0 kW, so no flow or direction either
// Layout in a 100 x H coordinate system (matches the container's aspect ratio)
const BASE_H = 80;
const DEVICES_H = 124; // extra row below the house for wallbox, heating rod, ...
const HOUSE = { x: 50, y: 56 };
const LINE_Y = 52; // vertical centre of the side icons
const DEVICE_Y = 95; // centre of the device icons
const MAX_DEVICES = 4;

/** Connection; dashes travel from (x1, y1) to (x2, y2) when power > 0 and backwards when < 0. */
function Link({ x1, y1, x2, y2, power }: { x1: number; y1: number; x2: number; y2: number; power: number | null }) {
  const active = power != null && Math.abs(power) > IDLE_W;
  return (
    <g>
      <line x1={x1} y1={y1} x2={x2} y2={y2} className="flow-line" vectorEffect="non-scaling-stroke" />
      {active && (
        <line x1={x1} y1={y1} x2={x2} y2={y2} vectorEffect="non-scaling-stroke"
          className={`flow-dash ${power! > 0 ? "fwd" : "rev"}`} />
      )}
    </g>
  );
}

function Node({ x, y, h, icon, children, small = false, iconRef, name, onTap }: {
  x: number; y: number; h: number; icon: ReactNode; children: ReactNode; small?: boolean; iconRef?: Ref<HTMLDivElement>;
  name?: string; onTap?: () => void;
}) {
  return (
    <div className={`flow-node ${small ? "small" : ""} ${name ?? ""}`} style={{ left: `${x}%`, top: `${(y / h) * 100}%` }}>
      {/* biome-ignore lint/a11y/noStaticElementInteractions lint/a11y/useKeyWithClickEvents: a hidden easter egg for pointer users, no function needs it */}
      <div className="flow-icon" ref={iconRef} onClick={onTap}>{icon}</div>
      <div className="value">{children}</div>
    </div>
  );
}

type Box = { x: number; y: number; w: number; h: number }; // in the 100 x H coordinates of the lines
type Point = [number, number];

// corners in the 64 x 64 icon coordinates, mirrored for the other side: bottom right of the house walls and
// top right of the devices (the same point for every device, so the picture stays symmetric)
const HOUSE_CORNER: Point = [51, 54];
const DEVICE_CORNER: Point = [45, 14];
const GAP = 2.5; // free space at both ends of a line, like between battery and house

const at = (box: Box, [cx, cy]: Point, mirror = false): Point =>
  [box.x + ((mirror ? 64 - cx : cx) / 64) * box.w, box.y + (cy / 64) * box.h];

/** House corner to the facing device corner, shortened by GAP at both ends. A device right below the house
 * gets a straight line from below the house value instead. */
function deviceLine(house: Box, houseNode: Box, device: Box): [Point, Point] {
  const centre = device.x + device.w / 2;
  if (Math.abs(centre - (house.x + house.w / 2)) < 5) {
    return [[centre, houseNode.y + houseNode.h + GAP], [centre, at(device, DEVICE_CORNER)[1] - GAP]];
  }
  const left = centre < house.x + house.w / 2;
  const [x1, y1] = at(house, HOUSE_CORNER, left);
  const [x2, y2] = at(device, DEVICE_CORNER, !left);
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  const dx = ((x2 - x1) / len) * GAP, dy = ((y2 - y1) / len) * GAP;
  return [[x1 + dx, y1 + dy], [x2 - dx, y2 - dy]];
}

/** Where the battery's charge comes from: the sun, or the grid (cheap power, or more than half from the grid). */
export function chargeSource(snap: Snapshot | null, gridCharging: boolean): "sun" | "grid" | null {
  const charge = -(snap?.battery_power ?? 0);
  if (!snap || charge <= IDLE_W) return null;
  if (gridCharging) return "grid";
  return (snap.grid_power ?? 0) > IDLE_W && (snap.grid_power ?? 0) >= charge / 2 ? "grid" : "sun";
}

function direction(power: number | null | undefined, positive: string, negative: string): string | null {
  if (power == null || Math.abs(power) <= IDLE_W) return null;
  return power > 0 ? positive : negative;
}

function deviceX(index: number, count: number): number {
  return count === 1 ? 50 : 14 + (72 / (count - 1)) * index;
}

let retroOn = false; // survives switching pages, not a reload

/** The easter egg code switches to a retro game look. The icons form a D-pad (sun up, house down, battery left,
 * grid right); B and A appear as buttons once the arrows are entered. */
function useRetro(): { retro: boolean; justUnlocked: boolean; progress: number; press: (key: EasterEggKey) => void } {
  const [retro, setRetro] = useState(retroOn);
  const [justUnlocked, setJustUnlocked] = useState(false);
  const { progress, press } = useEasterEgg(() => {
    retroOn = !retroOn;
    setRetro(retroOn);
    setJustUnlocked(retroOn);
    chiptune(retroOn ? [523, 659, 784, 1047, 784, 1047] : [784, 659, 523, 392]);
  });
  useEffect(() => {
    if (!justUnlocked) return;
    const timer = setTimeout(() => setJustUnlocked(false), 3000);
    return () => clearTimeout(timer);
  }, [justUnlocked]);
  return { retro, justUnlocked, progress, press };
}

export function EnergyFlow({ snap, stale = false, devices = [], gridCharging = false }: {
  snap: Snapshot | null; stale?: boolean; devices?: Device[]; gridCharging?: boolean;
}) {
  const shown = devices.slice(0, MAX_DEVICES);
  const h = shown.length ? DEVICES_H : BASE_H;
  const container = useRef<HTMLDivElement>(null);
  const houseIcon = useRef<HTMLDivElement>(null);
  const deviceIcons = useRef<(HTMLDivElement | null)[]>([]);
  const [boxes, setBoxes] = useState<{ house: Box; houseNode: Box; devices: Box[] } | null>(null);
  const deviceKeys = shown.map((d) => d.key).join(",");
  const { retro, justUnlocked, progress, press } = useRetro();

  // the device lines run between the drawn corners, so they are measured from the rendered icons
  // biome-ignore lint/correctness/useExhaustiveDependencies: deviceKeys stands for the shown devices, so a new array on every render does not measure again
  useLayoutEffect(() => {
    const el = container.current;
    if (!el || !shown.length) return;
    const measure = () => {
      const c = el.getBoundingClientRect();
      if (!c.width || !houseIcon.current) return;
      const box = (node: HTMLElement): Box => {
        const r = node.getBoundingClientRect();
        return { x: ((r.left - c.left) / c.width) * 100, y: ((r.top - c.top) / c.height) * h,
          w: (r.width / c.width) * 100, h: (r.height / c.height) * h };
      };
      setBoxes({ house: box(houseIcon.current), houseNode: box(houseIcon.current.parentElement!), devices: deviceIcons.current.slice(0, shown.length).map((d) => (d ? box(d) : null!)) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [deviceKeys, h]);
  const devicePower = shown.reduce((sum, d) => sum + (d.power_w || 0), 0);
  // the inverter measures the whole consumption: the household is what the devices do not use
  const house = snap?.house_power != null ? Math.max(0, snap.house_power - (stale ? 0 : devicePower)) : null;
  // battery: + = discharging towards the house; grid: + = import from the grid
  const source = stale ? null : chargeSource(snap, gridCharging);
  const battery = direction(snap?.battery_power, t("overview.energyFlow.discharging"), t("overview.energyFlow.charging")); // the badge shows sun or grid
  const grid = snap?.off_grid ? t("common.disconnected") : direction(snap?.grid_power, t("common.import"), t("common.feedIn"));
  const label = snap
    ? [t("overview.energyFlow.solarPower", { power: kw(snap.pv_power) }), t("overview.energyFlow.housePower", { power: kw(house) }),
       `${t("overview.energyFlow.batteryPower", { power: kw(snap.battery_power) })}${battery ? ` ${battery}` : ""}, ${percent(snap.battery_soc)}`,
       `${t("overview.energyFlow.gridPower", { power: kw(snap.grid_power) })}${grid ? ` ${grid}` : ""}`,
       ...shown.map((d) => `${d.name} ${kw(d.power_w)}`)].join(", ") + (stale ? ` (${t("overview.energyFlow.outdated")})` : "")
    : t("overview.energyFlow.noData");
  return (
    <div ref={container} className={`flow ${stale ? "stale" : ""} ${retro ? "retro" : ""}`} role="img" aria-label={label} style={{ aspectRatio: `100 / ${h}` }}>
      <svg className="lines" viewBox={`0 0 100 ${h}`} preserveAspectRatio="none" aria-hidden="true">
        {/* PV -> house (vertical) */}
        <Link x1={50} y1={30} x2={50} y2={41} power={stale ? null : snap?.pv_power ?? null} />
        {/* battery <-> house: + = discharging towards the house */}
        <Link x1={23} y1={LINE_Y} x2={38} y2={LINE_Y} power={stale ? null : snap?.battery_power ?? null} />
        {/* grid <-> house: + = import towards the house (drawn from grid side) */}
        <Link x1={77} y1={LINE_Y} x2={62} y2={LINE_Y} power={stale || snap?.off_grid ? null : snap?.grid_power ?? null} />
        {/* house -> devices */}
        {boxes && shown.map((d, i) => {
          const device = boxes.devices[i];
          if (!device) return null;
          const [[x1, y1], [x2, y2]] = deviceLine(boxes.house, boxes.houseNode, device);
          return <Link key={d.key} x1={x1} y1={y1} x2={x2} y2={y2} power={stale ? null : d.power_w} />;
        })}
      </svg>

      {retro && <svg className="retro-defs" aria-hidden><RetroPixels /></svg>}
      <Node x={50} y={16} h={h} icon={<SolarIcon />} name="sun" onTap={() => press("up")}>{kw(snap?.pv_power)}</Node>
      <Node x={HOUSE.x} y={HOUSE.y} h={h} icon={<HouseIcon size={72} />} iconRef={houseIcon} name="house" onTap={() => press("down")}>{kw(house)}</Node>
      <Node x={12} y={HOUSE.y} h={h} icon={
        <span className="battery-with-source">
          <BatteryIcon soc={snap?.battery_soc ?? null} />
          {source && <span className={`charge-source ${source}`} title={source === "sun" ? t("overview.energyFlow.chargingFromSolar") : t("overview.energyFlow.chargingFromGrid")}>
            {source === "sun" ? <SunGlyph /> : "€"}</span>}
        </span>} name="battery" onTap={() => press("left")}>
        {kw(snap?.battery_power)}
        <div className="soc">{percent(snap?.battery_soc)}{battery ? ` · ${battery}` : ""}</div>
        {retro && <div className="soc lives">{t("overview.retro.lives")}</div>}
      </Node>
      <Node x={88} y={HOUSE.y} h={h} icon={<span className="grid-with-coins"><GridIcon />
        {retro && !stale && !snap?.off_grid && (snap?.grid_power ?? 0) < -IDLE_W && <CoinSprite />}</span>}
        name="grid" onTap={() => press("right")}>
        {kw(snap?.grid_power)}
        {grid && <div className="soc">{grid}</div>}
      </Node>
      {shown.map((d, i) => (
        <Node key={d.key} x={deviceX(i, shown.length)} y={DEVICE_Y} h={h} small icon={<DeviceIcon kind={d.kind} size={64} />}
          iconRef={(el) => { deviceIcons.current[i] = el; }}>
          {kw(d.power_w)}
          <div className="soc">{d.name}{d.temperature_c != null && d.kind === "heating_rod" ? ` · ${num(d.temperature_c, 0)} °C` : ""}</div>
          {d.soc != null && <div className="soc">{num(d.soc, 0)} %{d.range_km != null ? ` · ${num(d.range_km, 0)} km` : ""}</div>}
        </Node>
      ))}
      {progress >= EASTER_EGG_CODE.length - 2 && (
        <div className="retro-pad">
          <button type="button" tabIndex={-1} className="b" onClick={() => press("b")}>{t("overview.retro.buttonB")}</button>
          <button type="button" tabIndex={-1} className="a" onClick={() => press("a")}>{t("overview.retro.buttonA")}</button>
        </div>
      )}
      {justUnlocked && <div className="retro-banner">{t("overview.retro.unlocked")}</div>}
    </div>
  );
}

/** SVG filter that draws the icons in coarse pixels. It is applied to the shapes inside the icons, in their
 * 64 x 64 coordinates: on HTML elements Safari places the sample point outside the icon and draws nothing. */
function RetroPixels() {
  return (
    <filter id="retro-pixels" filterUnits="userSpaceOnUse" x="-8" y="-8" width="80" height="80">
      <feFlood x="1" y="1" width="1" height="1" />
      <feComposite x="0" y="0" width="3" height="3" />
      <feTile result="grid" />
      <feComposite in="SourceGraphic" in2="grid" operator="in" />
      <feMorphology operator="dilate" radius="1" />
    </filter>
  );
}

// pixel art for the retro look: a coin and "+1", one string per row (o outline, y gold, w shine, t text, which gets
// an outline around it)
const COIN = [
  "..ooo..............",
  ".oyyyo.............",
  "oywyyyo.........t..",
  "oywoyyo...t....tt..",
  "oyyoyyo..ttt....t..",
  "oyyoyyo...t.....t..",
  "oyyoyyo.........t..",
  "oyyyyyo........ttt.",
  ".oyyyo.............",
  "..ooo..............",
];
const COIN_COLOURS: Record<string, string> = { o: "#7a5600", y: "#f5b301", w: "#ffe27a", t: "#f5b301" };
const isText = (x: number, y: number) => COIN[y]?.[x] === "t";

function CoinSprite() {
  const pixels = COIN.flatMap((row, y) => [...row].map((c, x) => {
    const outline = c === "." && [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]].some(([nx, ny]) => isText(nx, ny));
    const fill = outline ? COIN_COLOURS.o : COIN_COLOURS[c];
    return fill ? <rect key={`${x},${y}`} x={x} y={y} width={1} height={1} fill={fill} /> : null;
  }));
  return <svg className="coin" width={57} height={30} viewBox="0 0 19 10" shapeRendering="crispEdges" aria-hidden>{pixels}</svg>;
}

function SunGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="4.5" fill="currentColor" stroke="none" />
      <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" />
    </svg>
  );
}
