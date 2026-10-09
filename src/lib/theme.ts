export type Theme = "light" | "dark" | "system";

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  const resolved =
    theme === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
      : theme;
  root.classList.remove("light", "dark");
  root.classList.add(resolved);
}
export function getStoredTheme(): Theme {
  return (localStorage.getItem("telnusa-theme") as Theme) ?? "system";
}
export function storeTheme(theme: Theme): void {
  localStorage.setItem("telnusa-theme", theme);
}
