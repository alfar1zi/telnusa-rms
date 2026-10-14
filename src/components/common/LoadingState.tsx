import { Skeleton } from "@/components/ui/skeleton";

interface LoadingStateProps {
  className?: string;
}

export default function LoadingState({ className }: LoadingStateProps) {
  return (
    <div className={`space-y-3 p-4 ${className ?? ""}`}>
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
    </div>
  );
}
