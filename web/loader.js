"use strict";
// Loading each dependency in its own task keeps 4x-throttled startup
// responsive. The ordered chain preserves the same global-script dependency
// contract as `defer` while yielding between files.
const editorScripts = [
  "/vendor/codemirror/codemirror.js",
  "/vendor/codemirror/python.js",
  "/vendor/codemirror/matchbrackets.js",
  "/vendor/codemirror/comment.js",
  "/vendor/codemirror/searchcursor.js",
  "/vendor/codemirror/dialog.js",
  "/vendor/codemirror/search.js",
  "/vendor/codemirror/show-hint.js",
];
const factoryScripts = [
  "/i18n.js", "/allocation-results.js",
  "/inventory-projection.js", "/inventory-ui.js", "/browser-runtime.js",
  "/order-projection.js", "/order-ui.js", "/operations.js", "/graph-state.js",
  "/scenario-comparison.js", "/scenario-ui.js", "/data-core.js", "/data-ui.js",
  "/experiment-core.js", "/experiment-ui.js", "/app.js",
];
function loadScriptSequence(scripts, index = 0, complete = () => {}, optional = false) {
  if (index === scripts.length) { complete(); return; }
  const script = document.createElement("script");
  script.src = scripts[index];
  script.onload = () => loadScriptSequence(scripts, index + 1, complete, optional);
  script.onerror = () => {
    if (optional) complete();
    else window.dispatchEvent(new ErrorEvent("error", {message: `Failed to load ${scripts[index]}`}));
  };
  document.head.append(script);
}
let editorPromise = null;
window.loadEnhancedEditor = () => editorPromise ||= new Promise(resolve => {
  loadScriptSequence(editorScripts, 0, () => { window.enhanceEditor?.(); resolve(); }, true);
});
window.translations = {};
window.loadLocale = language => {
  if (Object.keys(window.translations[language] || {}).length) return Promise.resolve();
  if (document.documentElement.dataset.mode === "public") {
    const messages = {};
    return [0, 1, 2, 3].reduce((promise, index) => promise.then(() =>
      fetch(`/locales-${language}-${index}.json`).then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      }).then(value => Object.assign(messages, value))
    ), Promise.resolve()).then(() => { window.translations[language] = messages; });
  }
  return fetch("/locales.json").then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }).then(value => { window.translations = value; });
};
const supportedLocales = ["ko", "en", "ja"];
let initialLocale = (navigator.languages?.[0] || navigator.language || "en").toLowerCase().split("-")[0];
try { initialLocale = localStorage.getItem("factory-studio.locale.v1") || initialLocale; } catch {}
if (!supportedLocales.includes(initialLocale)) initialLocale = "en";
window.translations.en = {};
window.translations[initialLocale] ||= {};
let resolveInitialLocale;
window.initialLocaleReady = new Promise(resolve => { resolveInitialLocale = resolve; });
loadScriptSequence(factoryScripts, 0, () => {
  window.loadLocale("en")
    .then(() => initialLocale === "en" ? null : window.loadLocale(initialLocale))
    .then(() => { resolveInitialLocale();
    for (const language of supportedLocales) if (!window.translations[language]) setTimeout(() => window.loadLocale(language), 0);
    })
    .catch(error => window.dispatchEvent(new ErrorEvent("error", {message: `Failed to load locales: ${error.message}`})));
});
