import type { ElementType } from "react";
import type { DataAge } from "@/types/device";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface TelemetryCardProps {
  label: string;
  value: number | null;
  unit: string;
  decimals?: number;
  icon?: ElementType;
  loading?: boolean;
  // C3: server-authoritative freshness. Anything but "fresh" renders the
  // explicit staleness marker, never the value as if it were live. Null age
  // (older payloads) leaves rendering unchanged so mock paths keep working.
  dataAge?: DataAge | null;
}

export default function TelemetryCard({
  label,
  value,
  unit,
  decimals = 1,
  icon: Icon,
  loading,
  dataAge,
}: TelemetryCardProps) {
  // A non-fresh reading is never rendered as a live value. Null stays "NO DATA".
  // (unknown); a present-but-stale value keeps its number but is marked stale.
  const stale = dataAge != null && dataAge !== "fresh";
  return (
    <Card className="flex flex-col justify-between">
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">{label}</p>
          {Icon && <Icon className="size-4 text-muted-foreground" />}
        </div>
        {loading ? (
          <Skeleton className="h-7 w-20 mt-2" />
        ) : (
          <div className="mt-2 flex items-baseline gap-1.5">
            <span className="text-xl sm:text-2xl font-bold tabular-nums tracking-tight text-foreground">
              {value === null ? "NO DATA" : value.toFixed(decimals)}
            </span>
            <span className="text-xs font-semibold text-muted-foreground uppercase">{unit}</span>
            {stale && (
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {dataAge === "offline" ? "OFFLINE" : dataAge === "no_data" ? "NO DATA" : "STALE"}
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
