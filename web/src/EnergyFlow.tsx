import { type ReactNode, type Ref, useLayoutEffect, useRef, useState } from "react";
import type { Device, Snapshot } from "./api";
import { DeviceIcon } from "./DevicesPage";
import { kw, num, percent } from "./format";
import { BatteryIcon, GridIcon, HouseIcon, SolarIcon } from "./icons";

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

function Node({ x, y, h, icon, children, small = false, iconRef }: {
  x: number; y: number; h: number; icon: ReactNode; children: ReactNode; small?: boolean; iconRef?: Ref<HTMLDivElement>;
}) {
  return (
    <div className={`flow-node ${small ? "small" : ""}`} style={{ left: `${x}%`, top: `${(y / h) * 100}%` }}>
      <div className="flow-icon" ref={iconRef}>{icon}</div>
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

function direction(power: number | null | undefined, positive: string, negative: string): string | null {
  if (power == null || Math.abs(power) <= IDLE_W) return null;
  return power > 0 ? positive : negative;
}

function deviceX(index: number, count: number): number {
  return count === 1 ? 50 : 14 + (72 / (count - 1)) * index;
}

export function EnergyFlow({ snap, stale = false, devices = [] }: { snap: Snapshot | null; stale?: boolean; devices?: Device[] }) {
  const shown = devices.slice(0, MAX_DEVICES);
  const h = shown.length ? DEVICES_H : BASE_H;
  const container = useRef<HTMLDivElement>(null);
  const houseIcon = useRef<HTMLDivElement>(null);
  const deviceIcons = useRef<(HTMLDivElement | null)[]>([]);
  const [boxes, setBoxes] = useState<{ house: Box; houseNode: Box; devices: Box[] } | null>(null);
  const deviceKeys = shown.map((d) => d.key).join(",");

  // the device lines run between the drawn corners, so they are measured from the rendered icons
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
  const battery = direction(snap?.battery_power, "entlädt", "lädt");
  const grid = direction(snap?.grid_power, "Bezug", "Einspeisung");
  const label = snap
    ? [`Solar ${kw(snap.pv_power)}`, `Haus ${kw(house)}`,
       `Speicher ${kw(snap.battery_power)}${battery ? ` ${battery}` : ""}, ${percent(snap.battery_soc)}`,
       `Netz ${kw(snap.grid_power)}${grid ? ` ${grid}` : ""}`,
       ...shown.map((d) => `${d.name} ${kw(d.power_w)}`)].join(", ") + (stale ? " (veraltet)" : "")
    : "Energiefluss, keine Daten";
  return (
    <div ref={container} className={`flow ${stale ? "stale" : ""}`} role="img" aria-label={label} style={{ aspectRatio: `100 / ${h}` }}>
      <svg className="lines" viewBox={`0 0 100 ${h}`} preserveAspectRatio="none">
        {/* PV -> house (vertical) */}
        <Link x1={50} y1={30} x2={50} y2={41} power={stale ? null : snap?.pv_power ?? null} />
        {/* battery <-> house: + = discharging towards the house */}
        <Link x1={23} y1={LINE_Y} x2={38} y2={LINE_Y} power={stale ? null : snap?.battery_power ?? null} />
        {/* grid <-> house: + = import towards the house (drawn from grid side) */}
        <Link x1={77} y1={LINE_Y} x2={62} y2={LINE_Y} power={stale ? null : snap?.grid_power ?? null} />
        {/* house -> devices */}
        {boxes && shown.map((d, i) => {
          const device = boxes.devices[i];
          if (!device) return null;
          const [[x1, y1], [x2, y2]] = deviceLine(boxes.house, boxes.houseNode, device);
          return <Link key={d.key} x1={x1} y1={y1} x2={x2} y2={y2} power={stale ? null : d.power_w} />;
        })}
      </svg>

      <Node x={50} y={16} h={h} icon={<SolarIcon />}>{kw(snap?.pv_power)}</Node>
      <Node x={HOUSE.x} y={HOUSE.y} h={h} icon={<HouseIcon size={72} />} iconRef={houseIcon}>{kw(house)}</Node>
      <Node x={12} y={HOUSE.y} h={h} icon={<BatteryIcon soc={snap?.battery_soc ?? null} />}>
        {kw(snap?.battery_power)}
        <div className="soc">{percent(snap?.battery_soc)}{battery ? ` · ${battery}` : ""}</div>
      </Node>
      <Node x={88} y={HOUSE.y} h={h} icon={<GridIcon />}>
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
    </div>
  );
}
