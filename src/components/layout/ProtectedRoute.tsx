/**
 * ProtectedRoute: redirects unauthenticated users to /login.
 *
 * Works with AuthContext: while session is loading, renders nothing
 * (avoids flash of protected content). Once resolved:
 * - user present → renders children
 * - user null    → redirects to /login
 *
 * Authorization (role checks) is enforced server-side on every API call.
 * This route guard is a UX convenience, not a security boundary.
 */

import { Navigate, Outlet, useLocation } from "react-router-dom";
import { AlertCircle, RotateCcw }         from "lucide-react";
import { useAuth }                        from "@/context/AuthContext";
import { useI18n }                        from "@/i18n/I18nContext";
import { TextureButton }                  from "@/components/ui/texture-button";
import ForcePasswordChange                from "@/components/auth/ForcePasswordChange";

export default function ProtectedRoute() {
  const { user, loading, sessionError, refresh } = useAuth();
  const { t } = useI18n();
  const location          = useLocation();

  // While validating session: render nothing to avoid content flash
  if (loading) return null;

  // Backend unreachable: the session is neither confirmed nor rejected, so the
  // operator gets an explicit outage with a retry and an escalation path. Sending
  // them to the login form would invite credentials into a dead service.
  if (!user && sessionError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="flex w-full max-w-md flex-col items-center gap-3 rounded-xl border border-border bg-card p-8 text-center shadow-sm">
          <AlertCircle className="size-8 text-[var(--destructive)]" />
          <h1 className="text-lg font-semibold text-foreground">{t("outage.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("outage.message")}</p>
          <p className="text-xs text-muted-foreground">{t("common.contactAdmin")}</p>
          <TextureButton variant="secondary" size="sm" onClick={() => void refresh()} className="mt-1">
            <RotateCcw className="size-3.5" />
            {t("common.retry")}
          </TextureButton>
        </div>
      </div>
    );
  }

  // Not authenticated: send to login, preserve intended destination
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Forced password change gate
  if (user.mustChangePassword) {
    return <ForcePasswordChange />;
  }

  return <Outlet />;
}
