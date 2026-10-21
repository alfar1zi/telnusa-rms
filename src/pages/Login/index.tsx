/**
 * Login page: NOC-style, minimal.
 *
 * This component renders the login UI only.
 * It does NOT implement username/password validation in React.
 *
 * In production, authentication is handled by:
 *   1. Cloudflare Access (edge identity layer): if configured; OR
 *   2. POST /api/auth/login -> server validates credentials, issues HttpOnly session cookie
 *
 * The form submits to the backend. On success the backend redirects or returns
 * the session cookie; this page then re-validates via /api/auth/me.
 *
 * In mock mode: clicking "Sign In" directly sets the mock session via getAuthUser().
 */

import { useState, type FormEvent } from "react";
import { useNavigate, useLocation, Navigate } from "react-router-dom";
import { Eye, EyeOff } from "lucide-react";
import { TextureButton } from "@/components/ui/texture-button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/I18nContext";
import { IS_MOCK, API_BASE } from "@/services/dataMode";

interface LocationState {
  from?: { pathname: string };
}

export default function Login() {
  const { user, loading, refresh } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as LocationState | null)?.from?.pathname ?? "/dashboard";

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Server-side failure (not a credential problem): offer the escalation path.
  const [serverError, setServerError] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Already authenticated: skip login page
  if (!loading && user) return <Navigate to={from} replace />;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setServerError(false);
    setSubmitting(true);

    if (IS_MOCK) {
      // Mock mode: no real credential check, refresh mock auth state
      await refresh();
      navigate(from, { replace: true });
      setSubmitting(false);
      return;
    }

    // Real mode: POST credentials to backend; server issues HttpOnly cookie on success.
    // Credentials are never validated in the browser.
    const form = e.currentTarget;
    const data = new FormData(form);
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: data.get("email"),
          password: data.get("password"),
        }),
      });

      if (res.status === 401 || res.status === 403) {
        setError(t("auth.invalidCredentials"));
        return;
      }
      if (res.status === 429) {
        setError(t("auth.tooManyAttempts"));
        return;
      }
      if (!res.ok) {
        setError(t("auth.signInFailed"));
        setServerError(true);
        return;
      }

      // Session cookie issued by server, re-validate identity
      await refresh();
      navigate(from, { replace: true });
    } catch {
      setError(t("auth.serverUnreachable"));
      setServerError(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen w-full flex flex-col md:flex-row bg-background text-foreground">
      {/* Mobile Top Visual Banner */}
      <div className="relative md:hidden w-full h-44 flex flex-col justify-end p-6 overflow-hidden bg-black select-none">
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{
            backgroundImage: "url('/login-bg.webp')",
            filter: "blur(1px) brightness(0.50)",
          }}
          aria-hidden="true"
        />
        <div className="absolute inset-0 bg-black/20" aria-hidden="true" />
        <div className="relative z-10">
          <h1 className="text-2xl font-semibold tracking-tight text-white leading-tight">
            {t("login.brand")}
          </h1>
          <p className="text-xs text-white/80 font-normal mt-1">
            {t("login.organizedBy")}
          </p>
        </div>
      </div>

      {/* Desktop Left Visual Panel: approx 68% width */}
      <div className="relative hidden md:flex md:w-[65%] lg:w-[68%] flex-col justify-end p-12 lg:p-16 overflow-hidden bg-black select-none">
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{
            backgroundImage: "url('/login-bg.webp')",
            filter: "blur(1px) brightness(0.50)",
          }}
          aria-hidden="true"
        />
        <div className="absolute inset-0 bg-black/20" aria-hidden="true" />
        <div className="relative z-10 max-w-2xl">
          <h1 className="text-4xl lg:text-5xl font-semibold tracking-tight text-white leading-none">
            {t("login.brand")}
          </h1>
          <p className="text-sm lg:text-base text-white/80 font-normal mt-3">
            {t("login.organizedBy")}
          </p>
        </div>
      </div>

      {/* Right Login Panel: approx 32% width */}
      <div className="flex-1 md:w-[35%] lg:w-[32%] flex flex-col justify-center px-6 py-10 sm:px-10 lg:px-12 bg-background md:border-l border-border">
        <div className="w-full max-w-[380px] mx-auto">
          <div className="mb-8">
            <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase mb-2">
              {t("login.product")}
            </p>
            <h2 className="text-2xl lg:text-3xl font-semibold tracking-tight text-foreground leading-tight mb-2">
              {t("login.welcome")}
            </h2>
            <p className="text-sm text-muted-foreground leading-relaxed">
              {t("login.subtitle")}
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4" autoComplete="on">
            <div>
              <label
                htmlFor="login-email"
                className="block text-xs font-medium text-foreground mb-1.5"
              >
                {t("login.email")}
              </label>
              <Input
                id="login-email"
                name="email"
                type="email"
                autoComplete="email"
                required
                disabled={submitting}
                placeholder={IS_MOCK ? t("auth.emailPlaceholderMock") : t("auth.emailPlaceholder")}
              />
            </div>

            <div>
              <label
                htmlFor="login-password"
                className="block text-xs font-medium text-foreground mb-1.5"
              >
                {t("login.password")}
              </label>
              <div className="relative">
                <Input
                  id="login-password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  disabled={submitting}
                  placeholder="--------"
                  className="pr-10"
                />
                <TextureButton
                  type="button"
                  variant="icon"
                  size="icon"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 size-7 text-muted-foreground hover:text-foreground"
                  aria-label={showPassword ? t("auth.hidePassword") : t("auth.showPassword")}
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </TextureButton>
              </div>
            </div>

            {error && (
              <div
                role="alert"
                className="text-xs text-destructive border border-destructive/30 rounded-md p-3"
              >
                {error}
                {serverError && (
                  <span className="mt-1 block opacity-80">{t("common.contactAdmin")}</span>
                )}
              </div>
            )}

            <TextureButton
              type="submit"
              size="lg"
              className="w-full h-10 mt-2"
              disabled={submitting || loading}
            >
              {submitting ? t("login.submitting") : IS_MOCK ? t("auth.submitMock") : t("login.submit")}
            </TextureButton>
          </form>

          {IS_MOCK && (
            <p className="mt-4 text-center text-xs text-muted-foreground">
              {t("auth.mockModeNotice")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
