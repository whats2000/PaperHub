// Hand-rolled SVG marks for the Feedback page (no chart library in the app).
// Series colours come from the dataviz-validated --viz-* tokens; text never
// wears a series colour.

const GAP_PX = 2;
const END_RADIUS = 4;

export interface BarSegment {
  key: string;
  value: number;
  color: string;
}

/**
 * A horizontal stacked bar. Each segment's width is `value / max` of the
 * track (as a percentage attribute), the next one starts after a 2 px gap, and
 * only the far data-end is rounded. The track keeps 2 px of right padding so
 * the gap never pushes the longest bar out of its box.
 */
export function StackedBar({
  segments,
  max,
  height = 14,
}: {
  segments: BarSegment[];
  max: number;
  height?: number;
}) {
  const drawn = segments.filter((s) => s.value > 0);
  const pcts = drawn.map((s) => (max > 0 ? (s.value / max) * 100 : 0));
  const starts = pcts.map((_, i) => pcts.slice(0, i).reduce((a, b) => a + b, 0));
  return (
    <div className="pr-0.5">
      <svg width="100%" height={height} className="block overflow-visible" aria-hidden="true">
        {drawn.map((s, i) => {
          const x = `${starts[i]}%`;
          const last = i === drawn.length - 1;
          return (
            <g key={s.key} transform={`translate(${i * GAP_PX} 0)`}>
              {/* A nested viewport per segment clips its shapes to the
                  segment's own width, so the square-off never overdraws a
                  segment narrower than the corner radius. */}
              <svg data-seg={s.key} x={x} width={`${pcts[i]}%`} height={height}>
                <rect width="100%" height={height} rx={last ? END_RADIUS : 0} fill={s.color} />
                {/* Square off the baseline side of the rounded end segment. */}
                {last && <rect width={END_RADIUS} height={height} fill={s.color} />}
              </svg>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** A 4-point trend line with an emphasised last value, in text ink. */
export function Sparkline({
  values,
  width = 60,
  height = 18,
}: {
  values: readonly number[];
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return null;
  const mx = Math.max(...values);
  const mn = Math.min(...values);
  const range = Math.max(1, mx - mn);
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * (width - 6) + 3,
    height - 3 - ((v - mn) / range) * (height - 6),
  ]);
  const last = pts[pts.length - 1]!;
  return (
    <svg width={width} height={height} className="block text-muted-foreground" aria-hidden="true">
      <polyline
        points={pts.map(([x, y]) => `${x!.toFixed(1)},${y!.toFixed(1)}`).join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last[0]} cy={last[1]} r={2.5} className="fill-foreground" />
    </svg>
  );
}

/** A legend key: a small swatch in the series colour. */
export function Swatch({ color }: { color: string }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block size-2.5 shrink-0 rounded-[2px]"
      style={{ background: color }}
    />
  );
}

export const VIZ_EXPLICIT = "var(--viz-explicit)";
export const VIZ_IMPLICIT = "var(--viz-implicit)";
