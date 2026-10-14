import { useState } from "react";
import { useLocation } from "react-router-dom";
import { Sun, Moon, LogOut, ChevronDown, Globe } from "lucide-react";
import { TextureButton } from "@/components/ui/texture-button";
import {
  DropdownMenu, DropdownMenuContent,
  DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { type Theme, applyTheme, getStoredTheme, storeTheme } from "@/lib/theme";
import { useAuth } from "@/context/AuthContext";
import { IS_MOCK } from "@/services/dataMode";
import { useI18n } from "@/i18n/I18nContext";
import { LANGS } from "@/i18n/types";

const PAGE_TITLE_KEYS: Record<string, string> = {
  "/dashboard":  "header.title.dashboard",
  "/devices":    "header.title.devices",
  "/monitoring": "header.title.monitoring",
  "/alarms":     "header.title.alarms",
  "/reports":    "header.title.reports",
  "/settings":   "header.title.settings",
};

export default function TopHeader() {
  const { pathname }      = useLocation();
  const [theme, setTheme] = useState<Theme>(getStoredTheme);
  const { user, logout }  = useAuth();
  const { lang, setLang, t } = useI18n();

  const title = pathname.startsWith("/devices/")
    ? t("header.title.deviceDetail")
    : pathname.startsWith("/remote/")
    ? t("header.title.remote")
    : (PAGE_TITLE_KEYS[pathname] ? t(PAGE_TITLE_KEYS[pathname]) : "");

  const resolved: "light" | "dark" =
    theme === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
      : theme;

  function handleTheme(val: Theme) {
    applyTheme(val);
    storeTheme(val);
    setTheme(val);
  }

  // Get initials for avatar
  const initials = user?.name
    ? user.name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase()
    : "OP";

  return (
    <>
      {/* Mock mode indicator: visible in dev only, never shown in real mode */}
      {IS_MOCK && (
        <div
          role="status"
          aria-label="Mock data mode active"
          className="flex items-center justify-center gap-2 bg-secondary/50 border-b border-border/70 px-4 py-1"
        >
          <span className="text-[11px] font-medium text-[var(--chart-1)] tracking-wide">
            MOCK MODE ACTIVE: Development Harness Only
          </span>
        </div>
      )}

      <header className="h-14 border-b border-border/70 flex items-center px-4 gap-3 bg-card/80 backdrop-blur-xs shrink-0 sticky top-0 z-20">
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold tracking-tight text-foreground truncate font-sans">
            {title}
          </h2>
        </div>

        {/* Language */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <TextureButton
              variant="icon"
              size="icon"
              className="size-8 text-muted-foreground hover:text-foreground"
              aria-label={t("header.language")}
            >
              <Globe className="size-4" />
            </TextureButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="text-xs">
            {LANGS.map((option) => (
              <DropdownMenuItem
                key={option.id}
                onClick={() => setLang(option.id)}
                className={option.id === lang ? "font-semibold" : undefined}
              >
                {option.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Theme toggle */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <TextureButton
              variant="icon"
              size="icon"
              className="size-8 text-muted-foreground hover:text-foreground"
              aria-label={t("header.toggleTheme")}
            >
              {resolved === "light"
                ? <Sun className="size-4" />
                : <Moon className="size-4" />}
            </TextureButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="text-xs">
            <DropdownMenuItem onClick={() => handleTheme("light")}>{t("header.theme.light")}</DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleTheme("dark")}>{t("header.theme.dark")}</DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleTheme("system")}>{t("header.theme.system")}</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* User menu with Base UI / Radix Avatar (no round badge) */}
        {user && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <TextureButton
                variant="ghost"
                className="flex items-center gap-2 rounded-lg p-1 pl-2 hover:bg-muted/50 transition-colors focus-visible:outline-hidden"
                aria-label={t("header.userMenu")}
              >
                <div className="hidden sm:flex flex-col text-right leading-none">
                  <span className="text-xs font-semibold text-foreground max-w-28 truncate">{user.name}</span>
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider mt-0.5">
                    {user.role}
                  </span>
                </div>

                <Avatar size="sm" className="size-7 border border-border">
                  <AvatarFallback className="text-[11px] font-bold bg-muted text-foreground">
                    {initials}
                  </AvatarFallback>
                </Avatar>

                <ChevronDown className="size-3 text-muted-foreground hidden sm:inline" />
              </TextureButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-48 text-xs">
              <div className="px-3 py-2">
                <p className="font-semibold text-foreground truncate">{user.name}</p>
                <p className="text-muted-foreground text-[11px] truncate mt-0.5">{user.email}</p>
              </div>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => void logout()}
                className="text-[var(--destructive)] focus:text-[var(--destructive)] gap-2 cursor-pointer"
              >
                <LogOut className="size-3.5" />
                {t("header.signOut")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </header>
    </>
  );
}
