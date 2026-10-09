import type { Lang } from "./types.ts";

/**
 * Language persistence.
 *
 * Kept apart from the provider so pure modules (state semantics, labels) can
 * read the stored choice without importing React.
 */
const STORAGE_KEY = "telnusa-lang";

export function getStoredLang(): Lang {
  if (typeof localStorage === "undefined") return "en";
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "id" || stored === "en" ? stored : "en";
}

export function storeLang(lang: Lang): void {
  if (typeof localStorage !== "undefined") localStorage.setItem(STORAGE_KEY, lang);
  if (typeof document !== "undefined") document.documentElement.lang = lang;
}
