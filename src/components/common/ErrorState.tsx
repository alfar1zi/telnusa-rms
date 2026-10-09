import { TextureButton } from "@/components/ui/texture-button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertCircle, RotateCcw } from "lucide-react";
import { useI18n } from "@/i18n/I18nContext";

interface ErrorStateProps {
  title?: string;
  message: string;
  // Page-level failures pass the standard admin escalation line; inline failures
  // (a single chart card) leave it out so the hint is not repeated per card.
  hint?: string;
  onRetry?: () => void;
}

export default function ErrorState({ title, message, hint, onRetry }: ErrorStateProps) {
  const { t } = useI18n();
  return (
    <Alert variant="destructive">
      <AlertCircle className="h-4 w-4" />
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription className="flex items-start justify-between gap-4">
        <span>
          {message}
          {hint ? <span className="mt-1 block text-xs opacity-80">{hint}</span> : null}
        </span>
        {onRetry && (
          <TextureButton
            variant="destructive"
            size="sm"
            onClick={onRetry}
            className="h-7 shrink-0"
          >
            <RotateCcw className="h-3 w-3" />
            {t("common.retry")}
          </TextureButton>
        )}
      </AlertDescription>
    </Alert>
  );
}
