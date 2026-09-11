"use strict";

// Public mode starts with the native textarea. The optional advanced editor is
// downloaded only after an explicit request, and only executed after every
// CodeMirror asset has passed an HTTP preflight.
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
const loaderMessages = {
  ko: {locale: "언어 파일을 불러오지 못했습니다. 기본 언어로 계속합니다.", module: "일부 화면을 불러오지 못했습니다. 다시 시도할 수 있습니다.", editor: "고급 편집기를 불러오지 못했습니다. 기본 편집기를 계속 사용할 수 있습니다.", retry: "다시 시도"},
  en: {locale: "The language file could not be loaded. Continuing with the fallback language.", module: "Part of the interface could not be loaded. You can retry.", editor: "The advanced editor could not be loaded. The basic editor is still available.", retry: "Retry"},
  ja: {locale: "言語ファイルを読み込めませんでした。代替言語で続行します。", module: "画面の一部を読み込めませんでした。再試行できます。", editor: "高機能エディターを読み込めませんでした。基本エディターは引き続き使用できます。", retry: "再試行"},
};

function loaderLanguage() {
  const selected = document.querySelector("#language")?.value;
  const language = document.documentElement.dataset.uiReady
    ? selected || document.documentElement.lang || initialLocale || "en"
    : initialLocale || "en";
  return Object.hasOwn(loaderMessages, language) ? language : "en";
}

window.showFactoryLoadFailure = (kind, detail = "") => {
  document.querySelectorAll("[data-boot-stage]").forEach(element => element.removeAttribute("data-boot-stage"));
  const output = document.querySelector("#output");
  if (output && !output.textContent.trim()) output.textContent = loaderMessages[loaderLanguage()][kind] || loaderMessages.en.module;
  let alert = document.querySelector("#loader-error");
  if (!alert) {
    alert = document.createElement("div");
    alert.id = "loader-error";
    alert.className = "loader-error";
    alert.setAttribute("role", "alert");
    const main = document.querySelector("main") || document.body;
    main.prepend(alert);
  }
  const language = loaderLanguage();
  const message = loaderMessages[language][kind] || loaderMessages[language].module;
  alert.replaceChildren();
  const copy = document.createElement("span");
  copy.textContent = detail ? `${message} (${detail})` : message;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = loaderMessages[language].retry;
  retry.addEventListener("click", () => location.reload());
  alert.append(copy, retry);
  document.documentElement.dataset.uiReady ||= "error";
};

const loadedScripts = new Set();
function loadScript(path) {
  if (loadedScripts.has(path)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = path;
    script.onload = () => { loadedScripts.add(path); resolve(); };
    script.onerror = () => { script.remove(); reject(new Error(`Failed to load ${path}`)); };
    document.head.append(script);
  });
}

async function loadScripts(scripts, continueOnError = false) {
  const errors = [];
  for (const path of scripts) {
    try { await loadScript(path); }
    catch (error) {
      errors.push({path, error});
      if (!continueOnError) throw error;
    }
  }
  return errors;
}

let editorAssetsPromise = null;
window.loadEnhancedEditor = selection => {
  if (!editorAssetsPromise) {
    editorAssetsPromise = Promise.all(editorScripts.map(async path => {
      const response = await fetch(path, {cache: "force-cache"});
      if (!response.ok) throw new Error(`Failed to load ${path} (HTTP ${response.status})`);
    })).then(() => loadScripts(editorScripts)).catch(error => {
      editorAssetsPromise = null;
      throw error;
    });
  }
  return editorAssetsPromise.then(() => window.enhanceEditor?.(selection));
};

window.translations = {};
const localeLoads = new Map();
async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Failed to load ${path} (HTTP ${response.status})`);
  return response.json();
}

window.loadLocale = language => {
  if (Object.keys(window.translations[language] || {}).length) return Promise.resolve();
  if (localeLoads.has(language)) return localeLoads.get(language);
  let promise;
  if (document.documentElement.dataset.mode === "public") {
    promise = Promise.all([0, 1, 2, 3].map(index => fetchJson(`/locales-${language}-${index}.json`)))
      .then(chunks => { window.translations[language] = Object.assign({}, ...chunks); })
      .catch(async () => {
        const all = await fetchJson("/locales.json");
        window.translations = all;
        if (!Object.keys(window.translations[language] || {}).length) throw new Error(`Missing locale ${language}`);
      });
  } else {
    promise = fetchJson("/locales.json").then(value => { window.translations = value; });
  }
  promise = promise.catch(error => { localeLoads.delete(language); throw error; });
  localeLoads.set(language, promise);
  return promise;
};

const supportedLocales = ["ko", "en", "ja"];
let initialLocale = (navigator.languages?.[0] || navigator.language || "en").toLowerCase().split("-")[0];
try { initialLocale = localStorage.getItem("factory-studio.locale.v1") || initialLocale; } catch {}
if (!supportedLocales.includes(initialLocale)) initialLocale = "en";
window.translations.en = {};
window.translations[initialLocale] ||= {};
let resolveInitialLocale;
window.initialLocaleReady = new Promise(resolve => { resolveInitialLocale = resolve; });

loadScripts(factoryScripts, true).then(async errors => {
  const dataUiFailure = errors.find(({path}) => path === "/data-ui.js");
  if (dataUiFailure) {
    window.invalidateDataWork ||= () => {};
    window.dataWorkerStop ||= () => {};
    window.renderDataPanel ||= () => window.showFactoryLoadFailure("module", "data-ui.js");
    const dataTab = document.querySelector('[data-tab="data"]');
    if (dataTab) dataTab.disabled = true;
  }
  if (errors.length) window.showFactoryLoadFailure("module", errors.map(({path}) => path.split("/").pop()).join(", "));
  try {
    await window.loadLocale("en");
    if (initialLocale !== "en") await window.loadLocale(initialLocale);
  } catch (error) {
    window.showFactoryLoadFailure("locale", error.message);
  } finally {
    resolveInitialLocale();
  }
  for (const language of supportedLocales) {
    if (!Object.keys(window.translations[language] || {}).length) setTimeout(() => window.loadLocale(language).catch(() => {}), 0);
  }
});
