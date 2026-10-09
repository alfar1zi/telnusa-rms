import type { ElementType, ReactNode } from "react";
import { Inbox } from "lucide-react";

interface EmptyStateProps {
  icon?: ElementType;
  message: string;
  // explicit state chip, e.g. NOT INTEGRATED. Rendered above the message
  // so the machine-readable state is visible, not only prose.
  label?: string;
  action?: ReactNode;
}

export default function EmptyState({ icon: Icon = Inbox, message, label, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12">
      <Icon className="size-8 text-muted-foreground" />
      {label && (
        <span className="mt-2 rounded border border-border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      )}
      <p className="text-sm text-muted-foreground mt-2">{message}</p>
      {action}
    </div>
  );
}
