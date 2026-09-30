import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import en from "./en.json";
import ja from "./ja.json";
import { normalizeLocale, translate } from "./translate.js";

const RESOURCES = { en, ja };
const STORAGE_KEY = "s3-locale";
const LocaleContext = createContext({ locale: "en", setLocale: () => {} });

function initialLocale() {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return normalizeLocale(saved);
    }
  } catch {
    // Storage can be blocked; fall through to the browser language.
  }
  const tag = String(navigator.language || "").toLowerCase();
  return tag.startsWith("ja") ? "ja" : "en";
}

export function LocaleProvider({ children }) {
  const [locale, setLocaleState] = useState(initialLocale);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next) => {
    const value = normalizeLocale(next);
    setLocaleState(value);
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // Selection still applies for this page view.
    }
  }, []);

  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  return useContext(LocaleContext);
}

export function useT() {
  const { locale } = useLocale();
  return useCallback((key, params) => translate(RESOURCES, locale, key, params), [locale]);
}
