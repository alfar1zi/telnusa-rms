import { NavLink, useLocation } from "react-router-dom";
import { useEffect, useRef } from "react";
import {
  LayoutDashboard,
  Server,
  Activity,
  AlertTriangle,
  FileText,
  Settings2,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/i18n/I18nContext";

const NAV_ITEMS = [
  { to: "/dashboard",  labelKey: "nav.dashboard",  Icon: LayoutDashboard },
  { to: "/devices",    labelKey: "nav.devices",    Icon: Server          },
  { to: "/monitoring", labelKey: "nav.monitoring", Icon: Activity        },
  { to: "/alarms",     labelKey: "nav.alarms",     Icon: AlertTriangle   },
  { to: "/reports",    labelKey: "nav.reports",    Icon: FileText        },
  { to: "/settings",   labelKey: "nav.settings",   Icon: Settings2       },
];

export default function AppSidebar() {
  const location = useLocation();
  const { t } = useI18n();
  const { setOpen, isMobile } = useSidebar();
  // Desktop: the rail stays collapsed and opens while the pointer or the
  // keyboard focus is inside it, then closes again. Focus matters because a
  // pointer-only reveal would leave keyboard users with an icon rail they
  // cannot read; mobile keeps the sheet, which has its own open/close.
  const pointerInside = useRef(false);
  // A pointer crossing the rail on its way to the content must not open the sidebar,
  // and a pointer that leaves and returns a moment later must not make it flicker.
  // Keyboard focus is a deliberate act and opens immediately.
  const OPEN_DELAY_MS = 180;
  const CLOSE_GRACE_MS = 220;
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearTimers = () => {
    if (openTimer.current) { clearTimeout(openTimer.current); openTimer.current = null; }
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
  };
  useEffect(() => clearTimers, []);
  const hoverProps = isMobile
    ? {}
    : {
        onMouseEnter: () => {
          pointerInside.current = true;
          if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
          if (!openTimer.current) {
            openTimer.current = setTimeout(() => { openTimer.current = null; setOpen(true); }, OPEN_DELAY_MS);
          }
        },
        onMouseLeave: () => {
          pointerInside.current = false;
          if (openTimer.current) { clearTimeout(openTimer.current); openTimer.current = null; }
          if (!closeTimer.current) {
            closeTimer.current = setTimeout(() => { closeTimer.current = null; setOpen(false); }, CLOSE_GRACE_MS);
          }
        },
        onFocusCapture: () => { clearTimers(); setOpen(true); },
        onBlurCapture: (event: React.FocusEvent<HTMLDivElement>) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) { clearTimers(); setOpen(false); }
        },
      };

  return (
    <Sidebar collapsible="icon" variant="sidebar" {...hoverProps}>
      <SidebarHeader className="border-b border-sidebar-border/70 p-3">
        <div className="flex flex-col px-2 py-1 group-data-[collapsible=icon]:hidden">
          <span className="font-bold text-sm tracking-tight text-sidebar-foreground">
            {t("brand.name")}
          </span>
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
            {t("brand.tagline")}
          </span>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="text-[11px] uppercase tracking-wider text-muted-foreground px-2">
            {t("nav.navigation")}
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV_ITEMS.map(({ to, labelKey, Icon }) => {
                const label = t(labelKey);
                const isActive = location.pathname === to || (to !== "/dashboard" && location.pathname.startsWith(to));
                return (
                  <SidebarMenuItem key={to}>
                    <SidebarMenuButton
                      asChild
                      isActive={isActive}
                      tooltip={label}
                      className={
                        isActive
                          ? "bg-primary text-primary-foreground font-semibold hover:bg-primary hover:text-primary-foreground"
                          : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                      }
                    >
                      <NavLink to={to}>
                        <Icon className="size-4 shrink-0" />
                        <span>{label}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarRail />
    </Sidebar>
  );
}
