import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useRetroLook } from "./easterEgg";
import { ct, num, timeZone } from "./format";
import { LOCALE, t } from "./i18n";

const AXIS_FONT = `12px "DM Sans Variable", system-ui, sans-serif`;
const RETRO_FONT = "12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

const H = 3600, D = 24 * H, MO = 30 * D;
/** Tick steps for the time axis; uPlot handles the month steps as calendar months. */
const TIME_INCRS = [900, 1800, H, 2 * H, 3 * H, 4 * H, 6 * H, 12 * H, D, 2 * D, 3 * D, 7 * D, 14 * D, MO, 2 * MO, 3 * MO, 6 * MO, 365 * D];

/** Smallest gap between two data points = width of one bucket (hour, day, month). */
function bucketWidth(x: number[]): number {
  let width = Infinity;
  for (let i = 1; i < x.length; i++) width = Math.min(width, x[i] - x[i - 1]);
  return Number.isFinite(width) && width > 0 ? width : D;
}

let measureCtx: CanvasRenderingContext2D | null = null;
/** Width of the y axis, so long labels like "1.750 kWh" are not cut off. */
function axisSize(labels: string[] | null, font: string): number {
  if (!labels?.length) return 62;
  measureCtx ??= document.createElement("canvas").getContext("2d");
  if (!measureCtx) return 62;
  measureCtx.font = font;
  return Math.max(40, Math.ceil(Math.max(...labels.map((l) => measureCtx!.measureText(l).width))) + 18);
}

export type Series = {
  label: string;
  color: string;
  values: (number | null)[];
  unit: string;
  scale?: string;
  fill?: boolean;
  /** Lines only: dashed (e.g. devices on top of the main curves) */
  dash?: boolean;
  /** Bars only: -1 = left of the tick, 1 = right of the tick (two series side by side), 0 = centred (stacked) */
  barAlign?: -1 | 0 | 1;
  /** Decimals of kW/kWh values in the readout (default 2); counter-based energy uses energyDigits() (#194) */
  digits?: number;
};

/** Easter egg (retro look): bars filled with square pixels and a gap between them, in canvas pixels, so the grid
 * lines up across all bars like on an old screen. */
function pixelFill(color: string): uPlot.Series.Fill {
  const cache = new WeakMap<CanvasRenderingContext2D, CanvasPattern | string>();
  return (u) => {
    let fill = cache.get(u.ctx);
    if (!fill) {
      const gap = Math.max(1, Math.round(devicePixelRatio));
      const size = 4 * gap;
      const tile = document.createElement("canvas");
      tile.width = tile.height = size;
      const ctx = tile.getContext("2d");
      if (ctx) {
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, size - gap, size - gap);
      }
      fill = (ctx && u.ctx.createPattern(tile, "repeat")) || color;
      cache.set(u.ctx, fill);
    }
    return fill;
  };
}

/** Thin uPlot wrapper: lines for power, side-by-side bar pairs for energy. */
export function Chart({ x, series, bars = false, xFormat, height = 220, onHover, label }: {
  x: number[];
  series: Series[];
  bars?: boolean;
  xFormat: (ts: number) => string;
  height?: number;
  /** index of the touched / hovered data point, null when the cursor leaves the chart */
  onHover?: (index: number | null) => void;
  /** text alternative for screen readers */
  label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const hover = useRef(onHover);
  hover.current = onHover;
  const retro = useRetroLook();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const style = getComputedStyle(document.documentElement);
    const cssVar = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
    const resolve = (c: string) => (c.startsWith("var(") ? cssVar(c.slice(4, -1), "#888") : c);
    const unit = series.find((s) => s.scale !== "soc")?.unit ?? "";
    const axisColor = cssVar("--text-dim", "#888");
    const gridColor = cssVar("--line", "#333");
    // bars: half a bucket of room at both ends so the outer bars are not cut off, ticks no finer than one bucket
    // (otherwise one day gets several "05.10." labels) and bars that grow from zero
    const bucket = bucketWidth(x);
    const font = retro ? RETRO_FONT : AXIS_FONT;
    const grid = { stroke: gridColor, width: 1, ...(retro ? { dash: [2, 4] } : {}) };

    const opts: uPlot.Options = {
      width: el.clientWidth,
      height,
      cursor: { drag: { x: false, y: false } },
      // ticks on the hours and days of the plant's time zone, like the labels (also on a phone in another zone)
      ...(timeZone() ? { tzDate: (ts: number) => uPlot.tzDate(new Date(ts * 1000), timeZone() as string) } : {}),
      legend: { show: false },
      hooks: { setCursor: [(u) => hover.current?.(u.cursor.idx ?? null)] },
      scales: {
        x: bars ? { time: true, range: (_u, min, max) => [min - bucket / 2, max + bucket / 2] } : { time: true },
        ...(bars ? { y: { range: (_u, min, max) => uPlot.rangeNum(Math.min(0, min), Math.max(0, max), 0.1, true) } } : {}),
        soc: { range: [0, 100] },
      },
      axes: [
        { font, stroke: axisColor, grid, values: (_u, ticks) => ticks.map(xFormat),
          ...(bars ? { incrs: TIME_INCRS.filter((incr) => incr >= bucket * 0.9) } : {}) },
        { scale: "y", font, stroke: axisColor, grid, size: (_u, labels) => axisSize(labels, font),
          values: (_u, ticks) => ticks.map((v) => `${v.toLocaleString(LOCALE)} ${unit}`) },
        ...(series.some((s) => s.scale === "soc")
          ? [{ scale: "soc", side: 1, stroke: axisColor, grid: { show: false }, size: 40 } as uPlot.Axis]
          : []),
      ],
      series: [
        { label: t("report.chart.time"), value: (_u, v) => (v == null ? "–" : xFormat(v)) },
        ...series.map((s) => {
          const isBar = bars && s.scale !== "soc";
          const color = resolve(s.color);
          return {
            label: s.label,
            stroke: color,
            width: isBar ? 0 : retro ? 3 : 2,
            dash: !isBar && s.dash ? [6, 4] : undefined,
            scale: s.scale ?? "y",
            fill: isBar ? (retro ? pixelFill(color) : color) : s.fill ? `${color}33` : undefined,
            // retro: lines in steps, like a curve drawn on a coarse grid
            paths: isBar ? uPlot.paths.bars!({ size: s.barAlign === 0 ? [0.7, 60] : [0.46, 48], align: s.barAlign ?? 1 })
              : retro ? uPlot.paths.stepped!({ align: 1 }) : undefined,
            points: { show: false },
            value: (_u: uPlot, v: number | null) =>
              v == null ? "–" : `${s.unit === "ct" ? ct(v) : (s.unit === "kW" || s.unit === "kWh" ? num(v, s.digits ?? 2) : v.toLocaleString(LOCALE, { maximumFractionDigits: 1 }))} ${s.unit}`,
          };
        }),
      ],
    };
    const plot = new uPlot(opts, [x, ...series.map((s) => s.values)] as uPlot.AlignedData, el);
    const observer = new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height }));
    observer.observe(el);
    return () => {
      observer.disconnect();
      plot.destroy();
    };
  }, [x, series, bars, xFormat, height, retro]);

  return <div ref={ref} className="chart" role="img" aria-label={label ?? t("report.chart.defaultLabel")} />;
}
