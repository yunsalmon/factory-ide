"use strict";

const internalPostMessage = self.postMessage.bind(self);
const internalFetch = self.fetch.bind(self);
const internalImportScripts = self.importScripts.bind(self);
const PYODIDE_VERSION = "0.27.7";
const PYODIDE_BASE = `/vendor/pyodide/${PYODIDE_VERSION}/`;
// Delivery alias is still WASM, never JavaScript. Only this pinned URL is mapped.
// Fetch SRI checks decoded bytes before Pyodide can instantiate the response.
const WASM_ALIAS = PYODIDE_BASE + "pyodide.asm.wasm.js";
const WASM_INTEGRITY = "sha256-pQ3RhD+AWgt8RbYQN+4Neybf6F7+DhjvlaNK0k5AH18=";
let rejectRuntimeAsset;
function runtimeFetch(input, options) {
  const url = new URL(input instanceof Request ? input.url : input, self.location.href);
  const method = options?.method || (input instanceof Request ? input.method : "GET");
  if (url.href === new URL(PYODIDE_BASE + "pyodide.asm.wasm", self.location.origin).href && method === "GET") {
    const request = input instanceof Request ? new Request(new URL(WASM_ALIAS, self.location.origin), input) : new URL(WASM_ALIAS, self.location.origin);
    return internalFetch(request, {...options, integrity: WASM_INTEGRITY}).then(response => {
      if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/wasm") {
        throw new TypeError("Pinned WASM delivery response is unavailable or has an invalid MIME type");
      }
      return response;
    }).catch(error => {
      // Pyodide 0.27.7 logs instantiateStreaming failures without rejecting its
      // bootstrap promise. Propagate delivery/integrity errors to our controller.
      rejectRuntimeAsset?.(error);
      throw error;
    });
  }
  return internalFetch(input, options);
}
let pyodide;
let runtime;

function send(message) {
  internalPostMessage(message);
}

async function initialize() {
  if (runtime) return runtime;
  self.fetch = runtimeFetch;
  internalImportScripts(PYODIDE_BASE + "pyodide.js");
  const assetFailure = new Promise((_, reject) => { rejectRuntimeAsset = reject; });
  try {
    pyodide = await Promise.race([
      loadPyodide({indexURL: new URL(PYODIDE_BASE, self.location.origin).href}), assetFailure,
    ]);
  } finally {
    rejectRuntimeAsset = null;
  }
  const [wheel, engine, model, messages, locales, orders, disruptions, metrics] = await Promise.all([
    internalFetch(PYODIDE_BASE + "simpy-4.1.1-py3-none-any.whl").then((response) => response.arrayBuffer()),
    internalFetch("/runtime/engine.py").then((response) => response.text()),
    internalFetch("/runtime/model.py").then((response) => response.text()),
    internalFetch("/runtime/messages.py").then((response) => response.text()),
    internalFetch("/locales.json").then((response) => response.text()),
    internalFetch("/runtime/orders.py").then((response) => response.text()),
    internalFetch("/runtime/disruptions.py").then((response) => response.text()),
    internalFetch("/runtime/operation_metrics.py").then((response) => response.text()),

  ]);
  pyodide.unpackArchive(wheel, "zip");
  pyodide.FS.mkdirTree("/factory_runtime/web");
  pyodide.FS.writeFile("/factory_runtime/engine.py", engine);
  pyodide.FS.writeFile("/factory_runtime/model.py", model);
  pyodide.FS.writeFile("/factory_runtime/messages.py", messages);
  pyodide.FS.writeFile("/factory_runtime/web/locales.json", locales);
  pyodide.FS.writeFile("/factory_runtime/orders.py", orders);
  pyodide.FS.writeFile("/factory_runtime/disruptions.py", disruptions);
  pyodide.FS.writeFile("/factory_runtime/operation_metrics.py", metrics);
  await pyodide.runPythonAsync(`
import builtins, contextlib, gc, io, json, platform, sys, traceback
sys.path.insert(0, "/factory_runtime")
import simpy
from engine import Factory
from messages import descriptor
from model import ModelError, parse, synchronize

_allowed_imports = {
    "bisect", "collections", "copy", "dataclasses", "decimal", "enum", "fractions",
    "functools", "heapq", "itertools", "math", "messages", "operator", "random", "re",
    "simpy", "statistics", "string", "typing"
}
_original_import = builtins.__import__

def _sandbox_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if level or root in _allowed_imports:
        return _original_import(name, globals, locals, fromlist, level)
    raise PermissionError(f"Import '{root}' is outside the supported public model modules")

def _denied(*args, **kwargs):
    raise PermissionError("open() is outside the supported public model contract")

_safe_builtins = dict(vars(builtins))
_safe_builtins.update({"__import__": _sandbox_import, "open": _denied, "input": _denied})

class _LimitedLog(io.StringIO):
    def write(self, value):
        remaining = 20000 - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        return len(value)

def _line_for(error):
    if isinstance(error, SyntaxError):
        return error.lineno
    for frame in reversed(traceback.extract_tb(error.__traceback__)):
        if frame.filename == "factory_model.py":
            return frame.lineno
    return None

def _reclaim_request():
    return gc.collect()

def _execute(source):
    model, _, _ = parse(source)
    namespace = {"__name__": "__factory_model__", "__builtins__": _safe_builtins}
    exec(compile(source, "factory_model.py", "exec"), namespace)
    if namespace.get("MODEL") != model:
        from messages import message
        raise ModelError(message("message_37"))
    return Factory(model, namespace.get("choose_candidate"), namespace.get("processing_time"), namespace.get("process_lot")).run()

def _handle(operation, source, seed=None, updated_model=None):
    log = _LimitedLog()
    try:
        if len(source.encode("utf-8")) > 1_000_000:
            raise ModelError("Source exceeds the 1 MB browser limit")
        if operation == "sync":
            source = synchronize(source, updated_model)
            if len(source.encode("utf-8")) > 1_000_000:
                raise ModelError("Source exceeds the 1 MB browser limit")
        if operation == "run_seed":
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
                raise ModelError("Invalid experiment seed")
            model, _, _ = parse(source)
            model["seed"] = seed
            source = synchronize(source, model)
            import random
            random.seed(seed)
        if operation in ("parse", "sync"):
            model, _, warnings = parse(source)
            payload = {"model": model, "warnings": warnings, "warning_messages": [descriptor(w) for w in warnings]}
            if operation == "sync":
                payload["source"] = source
        else:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                result = _execute(source)
            result["console"] = log.getvalue()
            payload = {"ok": True, "result": result}
            if operation == "run_seed":
                payload["source"] = source
                payload["input_model"] = model
                if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 5_000_000:
                    raise ModelError("Experiment trace exceeds 5 MB limit")
        return json.dumps({"ok": True, "payload": payload}, ensure_ascii=False, allow_nan=False)
    except BaseException as error:
        return json.dumps({
            "ok": False,
            "error": str(error) or type(error).__name__,
            "error_message": getattr(error, "message", None),
            "traceback": traceback.format_exc(),
            "console": log.getvalue(),
            "line": _line_for(error),
        }, ensure_ascii=False, allow_nan=False)
`);
  runtime = {
    factory_sha256: [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify([engine, model, messages, orders, disruptions, metrics, locales]))))].map(value => value.toString(16).padStart(2, "0")).join(""),
    pyodide: PYODIDE_VERSION,
    python: pyodide.runPython("platform.python_version()"),
    simpy: pyodide.runPython("simpy.__version__"),
  };
  // These guards catch accidental use outside the model contract. The disposable
  // Worker, document isolation and CSP are the host/network security boundaries.
  self.fetch = () => Promise.reject(new TypeError("fetch() is outside the supported public model contract"));
  self.XMLHttpRequest = undefined;
  self.WebSocket = undefined;
  self.EventSource = undefined;
  self.importScripts = () => { throw new TypeError("Dynamic scripts are unavailable in the public browser sandbox"); };
  self.postMessage = () => { throw new TypeError("Direct worker messaging is unavailable in the public browser sandbox"); };
  return runtime;
}

function reclaimRequestMemory() {
  const names = ["__factory_operation", "__factory_source", "__factory_seed", "__factory_model_json"];
  for (const name of names) pyodide.globals.delete(name);
  return {
    idle_reclaimed: true,
    globals_cleared: names.length,
    python_objects_collected: pyodide.runPython("_reclaim_request()"),
  };
}

async function handle(message) {
  let outgoing;
  let resources;
  try {
    const details = await initialize();
    if (message.type === "init") {
      send({type: "ready", id: message.id, runtime: details});
      return;
    }
    if (!["parse", "run", "run_seed", "sync"].includes(message.type) || typeof message.source !== "string") {
      throw new TypeError("Invalid browser worker request");
    }
    pyodide.globals.set("__factory_operation", message.type);
    pyodide.globals.set("__factory_source", message.source);
    pyodide.globals.set("__factory_seed", message.seed ?? null);
    pyodide.globals.set("__factory_model_json", JSON.stringify(message.model ?? null));
    const response = JSON.parse(await pyodide.runPythonAsync("_handle(__factory_operation, __factory_source, __factory_seed, json.loads(__factory_model_json))"));
    if (!response.ok) {
      outgoing = {type: "error", id: message.id, ...response};
    } else {
      outgoing = {type: "result", id: message.id, payload: response.payload};
    }
  } catch (error) {
    outgoing = {type: "error", id: message.id, ...(!runtime ? {code: "browser_init_error"} : {}), error: error?.message || String(error), traceback: error?.stack || ""};
  } finally {
    if (message.type !== "init" && pyodide) {
      try {
        resources = reclaimRequestMemory();
      } catch (error) {
        outgoing = {type: "error", id: message.id, code: "browser_reclaim_error", error: error?.message || String(error), traceback: error?.stack || ""};
      }
    }
  }
  send({...outgoing, ...(resources ? {resources} : {})});
}

self.addEventListener("message", (event) => handle(event.data));
