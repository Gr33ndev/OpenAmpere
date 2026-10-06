import { correction, generate } from "lean-qr/nano";

const QUIET = 4; // white border in modules, needed by most scanners

/** A QR code as inline SVG (no canvas, no requests). Always dark on white, also in dark mode: many scanners cannot
 * read light-on-dark codes. Each row of dark modules is one path segment, which keeps the markup small. */
export function QrCode({ text, label }: { text: string; label: string }) {
  const code = generate(text, { minCorrectionLevel: correction.M });
  const size = code.size + 2 * QUIET;
  let path = "";
  for (let y = 0; y < code.size; y++) {
    for (let x = 0; x < code.size; x++) {
      if (!code.get(x, y)) continue;
      let run = 1;
      while (x + run < code.size && code.get(x + run, y)) run++;
      path += `M${x + QUIET} ${y + QUIET}h${run}v1h-${run}z`;
      x += run - 1;
    }
  }
  return (
    <svg className="qr" viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label} shapeRendering="crispEdges">
      <rect width={size} height={size} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}
