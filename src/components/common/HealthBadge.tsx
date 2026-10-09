import type { HealthState } from "@/types/device";
import { healthSemantic } from "@/lib/deviceSemantics";

export default function HealthBadge({ health }: { health: HealthState }) {
  const { label, color } = healthSemantic(health);
  return (
    <span
      style={{ color }}
      className="text-xs font-medium"
    >
      {label}
    </span>
  );
}
