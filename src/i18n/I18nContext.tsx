import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { LANGS, type Lang } from "./types.ts";
import { getStoredLang, storeLang } from "./lang.ts";
import { setActiveLang, translate, type Translate, type TranslateVars } from "./translate.ts";

/**
 * Console language.
 *
 * Defaults to English, persists the operator's choice, mirrors it onto
 * `<html lang>` (browser, screen readers, spell check) and keeps the module
 * translator in step so pure helpers speak the same language. A key missing in
 * the active language falls back to English; an unknown key renders as the key
 * itself, so a missing string shows up in review instead of vanishing.
 */
interface I18nValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(getStoredLang);

  useEffect(() => {
    setActiveLang(lang);
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    storeLang(next);
    setActiveLang(next);
  }, []);

  const t = useCallback<Translate>((key: string, vars?: TranslateVars) => translate(key, vars), [lang]);

  const value = useMemo<I18nValue>(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used within an I18nProvider");
  return value;
}

export { LANGS };
export type { Lang };
