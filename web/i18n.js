"use strict";
const localeKey = "factory-studio.locale.v1";
const browserLocale = (navigator.languages?.[0] || navigator.language || "en").toLowerCase().split("-")[0];
let locale = Object.hasOwn(translations, browserLocale) ? browserLocale : "en";
try {
  const saved = localStorage.getItem(localeKey);
  if (Object.hasOwn(translations, saved)) locale = saved;
} catch {}
function tr(key, args = []) {
  const template = translations[locale]?.[key] ?? translations.en[key] ?? "Translation unavailable";
  return template.replace(/\{(\d+)\}/g, (_, i) => {
    const value = args[Number(i)];
    return value?.code ? tr(value.code, value.args) : String(value ?? "");
  });
}
function localized(value, detail) { return detail?.code ? tr(detail.code, detail.args) : value; }
function translateStatic() {
  document.documentElement.lang = locale;
  document.querySelectorAll('[data-i18n]').forEach(el => el.textContent = tr(el.dataset.i18n));
  for (const attr of ['title', 'aria-label']) document.querySelectorAll('[data-i18n-' + attr + ']').forEach(el => el.setAttribute(attr, tr(el.getAttribute('data-i18n-' + attr))));
  document.querySelector('#language').value = locale;
}
