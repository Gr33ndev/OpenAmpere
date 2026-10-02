import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

const AXIS_FONT = `12px "DM Sans Variable", system-ui, sans-serif`;

export type Series = {
  label: string;
  color: string;
  values: (number | null)[];
  unit: string;
  scale?: string;
  fill?: boolean;
  /** Bars only: -1 = left of the tick, 1 = right of the tick (two series side by side), 0 = centred (stacked) */
  barAlign?: -1 | 0 | 1;
};

/** Thin uPlot wrapper: lines for power, side-by-side bar pairs for energy. */
export function Chart({ x, series, bars = false, xFormat, height = 220 }: {
  x: number[];
  series: Series[];
  bars?: boolean;
  xFormat: (ts: number) => string;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const style = getComputedStyle(document.documentElement);
    const cssVar = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
    const resolve = (c: string) => (c.startsWith("var(") ? cssVar(c.slice(4, -1), "#888") : c);
    const unit = series.find((s) => s.scale !== "soc")?.unit ?? "";
    const axisColor = cssVar("--text-dim", "#888");
    const gridColor = cssVar("--line", "#333");

    const opts: uPlot.Options = {
      width: el.clientWidth,
      height,
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: true }, soc: { range: [0, 100] } },
      axes: [
        { font: AXIS_FONT, stroke: axisColor, grid: { stroke: gridColor, width: 1 }, values: (_u, ticks) => ticks.map(xFormat) },
        { scale: "y", font: AXIS_FONT, stroke: axisColor, grid: { stroke: gridColor, width: 1 }, size: 62,
          values: (_u, ticks) => ticks.map((v) => `${v.toLocaleString("de-DE")} ${unit}`) },
        ...(series.some((s) => s.scale === "soc")
          ? [{ scale: "soc", side: 1, stroke: axisColor, grid: { show: false }, size: 40 } as uPlot.Axis]
          : []),
      ],
      series: [
        { label: "Zeit", value: (_u, v) => (v == null ? "–" : xFormat(v)) },
        ...series.map((s) => {
          const isBar = bars && s.scale !== "soc";
          const color = resolve(s.color);
          return {
            label: s.label,
            stroke: color,
            width: isBar ? 0 : 2,
            scale: s.scale ?? "y",
            fill: isBar ? color : s.fill ? color + "33" : undefined,
            paths: isBar ? uPlot.paths.bars!({ size: s.barAlign === 0 ? [0.7, 60] : [0.46, 48], align: s.barAlign ?? 1 }) : undefined,
            points: { show: false },
            value: (_u: uPlot, v: number | null) =>
              v == null ? "–" : `${v.toLocaleString("de-DE", { maximumFractionDigits: 1 })} ${s.unit}`,
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
  }, [x, series, bars, xFormat, height]);

  return <div ref={ref} className="chart" />;
}
