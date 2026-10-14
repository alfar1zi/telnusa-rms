import { cn } from "@/lib/utils";

/**
 * The dashboard's single visual medium: a zero-based horizontal bar.
 *
 * Why one medium, not several: a dashboard is read by the same perceptual
 * strategy every time (Few, "Common Pitfalls", #6 meaningless variety), so every
 * quantity that can be seen as a proportion is shown as length here. Length is
 * read faster and more accurately than a number, and compared without arithmetic.
 *
 * `max` is the shared scale for a group of bars; when omitted the segments are
 * read as parts of their own sum (a distribution). Colour is never decorative:
 * callers pass a semantic token only when the segment's state means something.
 */
export interface BarSegment {
  value: number;
  className?: string;
  label?: string;
}

interface BarProps {
  segments: BarSegment[];
  max?: number;
  height?: number;
  className?: string;
}

export default function Bar({ segments, max, height = 6, className }: BarProps) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  const scale = max !== undefined ? max : total;
  if (!Number.isFinite(scale) || scale <= 0 || total <= 0) {
    // An empty bar is the honest picture of "nothing to show", not a hidden one.
    return <div className={cn("w-full rounded-full bg-muted/50", className)} style={{ height }} aria-hidden="true" />;
  }
  return (
    <div
      className={cn("flex w-full overflow-hidden rounded-full bg-muted/50", className)}
      style={{ height }}
      role="img"
      aria-label={segments.filter((s) => s.label).map((s) => `${s.label}: ${s.value}`).join(", ")}
    >
      {segments.map((segment, index) => {
        const width = (Math.max(0, segment.value) / scale) * 100;
        if (width <= 0) return null;
        return <div key={index} className={cn("h-full", segment.className)} style={{ width: `${width}%` }} />;
      })}
    </div>
  );
}

/**
 * A single featured measure over a shared scale with an optional comparative
 * marker: the bullet-graph idea in its smallest form (Few, "Bullet Graph Design
 * Spec"). Used where the point of the picture is "how much of that does this
 * amount to", e.g. how much of the array's power reaches the MPPT output.
 */
export function BarWithMarker({
  value,
  max,
  marker,
  markerLabel,
  className,
  markerClassName,
}: {
  value: number;
  max: number;
  marker?: number | null;
  markerLabel?: string;
  className?: string;
  markerClassName?: string;
}) {
  const safeMax = max > 0 ? max : 1;
  const width = Math.min(100, (Math.max(0, value) / safeMax) * 100);
  const markerLeft = marker != null && marker > 0 ? Math.min(100, (marker / safeMax) * 100) : null;
  return (
    <div
      className="relative w-full"
      role="img"
      aria-label={markerLeft != null && markerLabel ? `${markerLabel}: ${marker}` : undefined}
    >
      <div className={cn("h-1.5 w-full rounded-full bg-muted/50 overflow-hidden", className)}>
        <div className="h-full rounded-full bg-foreground/70" style={{ width: `${width}%` }} />
      </div>
      {markerLeft != null && (
        <span
          className={cn("absolute -top-0.5 h-2.5 w-0.5 rounded-full bg-muted-foreground", markerClassName)}
          style={{ left: `calc(${markerLeft}% - 1px)` }}
          aria-hidden="true"
        />
      )}
    </div>
  );
}
