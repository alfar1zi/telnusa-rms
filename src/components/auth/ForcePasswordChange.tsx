import { useState, type FormEvent } from "react";
import { TextureButton } from "@/components/ui/texture-button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/I18nContext";
import { changePassword } from "@/services/api";

export default function ForcePasswordChange() {
  const { user, refresh, logout } = useAuth();
  const { t } = useI18n();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (newPassword.length < 8) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    if (newPassword === currentPassword) {
      setError(t("auth.passwordUnchanged"));
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(t("auth.passwordMismatch"));
      return;
    }

    setSubmitting(true);
    try {
      const res = await changePassword({ currentPassword, newPassword });
      if (res && res.ok) {
        setSuccess(true);
        await refresh();
      } else {
        setError(t("auth.passwordChangeFailed"));
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("auth.passwordChangeFailedGeneric");
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative min-h-screen w-full flex items-center justify-center p-4 overflow-hidden bg-background">
      {/* Background image layer */}
      <div
        className="absolute inset-0 bg-cover bg-center bg-no-repeat"
        style={{
          backgroundImage: "url('/password-change-bg.png')",
          filter: "blur(4px) brightness(0.50)",
          transform: "scale(1.03)",
        }}
        aria-hidden="true"
      />
      {/* Dark overlay layer */}
      <div className="absolute inset-0 bg-black/25" aria-hidden="true" />

      {/* Foreground form */}
      <Card className="relative z-10 w-full max-w-md border-border bg-card/95 backdrop-blur-sm shadow-xl">
        <CardHeader className="space-y-1">
          <div className="inline-flex items-center gap-2 text-[var(--chart-1)] font-medium text-xs uppercase tracking-wider">
            <span>{t("auth.securityRequirement")}</span>
          </div>
          <CardTitle className="text-xl font-semibold">{t("auth.changeRequiredTitle")}</CardTitle>
          <CardDescription>
            {t("auth.changeRequiredPrefix")}<strong>{user?.email}</strong>{t("auth.changeRequiredSuffix")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {success ? (
            <div className="space-y-4 text-center py-4">
              <p className="text-sm text-foreground font-medium">{t("auth.passwordUpdated")}</p>
              <p className="text-xs text-muted-foreground">{t("auth.redirecting")}</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {t("auth.currentPassword")}
                </label>
                <input
                  type="password"
                  required
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  disabled={submitting}
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                  autoComplete="current-password"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {t("auth.newPassword")}
                </label>
                <input
                  type="password"
                  required
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  disabled={submitting}
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                  autoComplete="new-password"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {t("auth.confirmPassword")}
                </label>
                <input
                  type="password"
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  disabled={submitting}
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                  autoComplete="new-password"
                />
              </div>

              {error && (
                <div role="alert" className="text-xs text-destructive border border-destructive/30 rounded px-3 py-2">
                  {error}
                </div>
              )}

              <div className="pt-2 flex flex-col gap-2">
                <TextureButton type="submit" className="w-full" disabled={submitting}>
                  {submitting ? t("auth.updatingPassword") : t("auth.updatePasswordAndContinue")}
                </TextureButton>
                <TextureButton
                  type="button"
                  variant="ghost"
                  className="w-full text-muted-foreground hover:text-foreground text-xs"
                  onClick={() => void logout()}
                  disabled={submitting}
                >
                  {t("auth.signOut")}
                </TextureButton>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
