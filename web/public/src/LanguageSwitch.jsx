import { useLocale, useT } from "./i18n/LocaleContext.jsx";

const CHOICES = [
  ["en", "English"],
  ["ja", "日本語"],
];

export function LanguageSwitch() {
  const { locale, setLocale } = useLocale();
  const t = useT();
  return (
    <nav className="lang-switch" aria-label={t("nav.site_language")} title={t("nav.site_language")}>
      {CHOICES.map(([value, label], index) => (
        <span key={value}>
          {index > 0 ? <span className="lang-sep"> | </span> : null}
          <button
            type="button"
            className={value === locale ? "linkish lang-current" : "linkish"}
            aria-pressed={value === locale}
            lang={value}
            onClick={() => setLocale(value)}
          >
            {label}
          </button>
        </span>
      ))}
    </nav>
  );
}
