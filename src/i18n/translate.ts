import type { Lang } from "./types.ts";
import { lookup, lookupFallback } from "./dict.ts";
import { getStoredLang } from "./lang.ts";

/**
 * Translation for code that is not a React component.
 *
 * Pure formatting modules (state semantics, labels) cannot call a hook, so they
 * read the console language through this accessor. `I18nProvider` keeps it in
 * step with the operator's choice, and both paths read the same dictionary, so a
 * label can never disagree with the rest of the screen.
 */
let activeLang: Lang = getStoredLang();

export function setActiveLang(lang: Lang): void {
  activeLang = lang;
}

export function getActiveLang(): Lang {
  return activeLang;
}

export type TranslateVars = Record<string, string | number>;
export type Translate = (key: string, vars?: TranslateVars) => string;

function interpolate(template: string, vars?: TranslateVars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

export function translate(key: string, vars?: TranslateVars): string {
  return interpolate(lookup(activeLang, key) ?? lookupFallback(key) ?? key, vars);
}
