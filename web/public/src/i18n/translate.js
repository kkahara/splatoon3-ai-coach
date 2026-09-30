/** Static UI strings. Pure functions so they can be tested without a browser. */

export const LOCALES = ["en", "ja"];
export const DEFAULT_LOCALE = "en";

export function normalizeLocale(value) {
  return LOCALES.includes(value) ? value : DEFAULT_LOCALE;
}

/**
 * Resolve `key` for `locale`. Fallback order is deterministic:
 * requested locale, then English, then the key itself.
 */
export function translate(resources, locale, key, params) {
  const table = resources[normalizeLocale(locale)] || {};
  const english = resources[DEFAULT_LOCALE] || {};
  const template = table[key] ?? english[key] ?? key;
  if (!params) {
    return template;
  }
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.hasOwn(params, name) ? String(params[name]) : match,
  );
}
