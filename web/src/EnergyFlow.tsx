import type { ReactNode } from "react";
import type { Snapshot } from "./api";
import { kw, percent } from "./format";
import { BatteryIcon, GridIcon, HouseIcon, SolarIcon } from "./icons";

const IDLE_W = 30;
// Layout in a 100 x 80 coordinate system (matches the container's aspect ratio)
const H = 80;
const HOUSE = { x: 50, y: 56 };
const LINE_Y = 52; // vertical centre of the side icons

/** Connection to the house; dashes travel towards the house when power > 0, away from it when < 0. */
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

function Node({ x, y, icon, children }: { x: number; y: number; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flow-node" style={{ left: `${x}%`, top: `${(y / H) * 100}%` }}>
      {icon}
      <div className="value">{children}</div>
    </div>
  );
}

export function EnergyFlow({ snap }: { snap: Snapshot | null }) {
  const house = snap?.house_power ?? null;
  return (
    <div className="flow" role="img" aria-label="Energiefluss">
      <svg className="lines" viewBox={`0 0 100 ${H}`} preserveAspectRatio="none">
        {/* PV -> house (vertical) */}
        <Link x1={50} y1={30} x2={50} y2={41} power={snap?.pv_power ?? null} />
        {/* battery <-> house: + = discharging towards the house */}
        <Link x1={23} y1={LINE_Y} x2={38} y2={LINE_Y} power={snap?.battery_power ?? null} />
        {/* grid <-> house: + = import towards the house (drawn from grid side) */}
        <Link x1={77} y1={LINE_Y} x2={62} y2={LINE_Y} power={snap?.grid_power ?? null} />
      </svg>

      <Node x={50} y={16} icon={<SolarIcon />}>{kw(snap?.pv_power)}</Node>
      <Node x={HOUSE.x} y={HOUSE.y} icon={<HouseIcon size={72} />}>{kw(house)}</Node>
      <Node x={12} y={HOUSE.y} icon={<BatteryIcon soc={snap?.battery_soc ?? null} />}>
        {kw(snap?.battery_power)}
        <div className="soc">{percent(snap?.battery_soc)}</div>
      </Node>
      <Node x={88} y={HOUSE.y} icon={<GridIcon />}>{kw(snap?.grid_power)}</Node>
    </div>
  );
}
