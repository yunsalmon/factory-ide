"use strict";

// Resource accounting uses roles rather than URLs because the interactive
// editor and experiment lanes intentionally share the same Worker program.
// Audits should pair this ledger with browser Worker-target counts.
const FACTORY_WORKER_RESOURCE_CONTRACT = Object.freeze({
  version: 1,
  interactive: Object.freeze({maximum: 1, settled: 1, workerName: "factory-interactive-runtime"}),
  experiment: Object.freeze({maximum: 2, settled: 0, workerName: "factory-experiment-runtime"}),
  data: Object.freeze({maximum: 1, settled: 0, workerName: "factory-data-import"}),
});
const factoryWorkerResources = (() => {
  const live = {interactive: new Set(), experiment: new Set(), data: new Set()};
  let serial = 0;
  const snapshot = () => Object.freeze({
    contractVersion: FACTORY_WORKER_RESOURCE_CONTRACT.version,
    interactive: live.interactive.size,
    experiment: live.experiment.size,
    data: live.data.size,
    total: live.interactive.size + live.experiment.size + live.data.size,
  });
  return Object.freeze({
    allocate(runtime) {
      const rule = FACTORY_WORKER_RESOURCE_CONTRACT[runtime.role];
      if (!rule || live[runtime.role].has(runtime) || live[runtime.role].size >= rule.maximum) {
        throw Object.assign(new Error(`Worker resource limit exceeded for ${runtime.role}`), {code: "browser_resource_limit"});
      }
      runtime.resourceId = ++serial;
      live[runtime.role].add(runtime);
    },
    release(runtime) {
      if (!runtime || !Object.hasOwn(live, runtime.role)) return false;
      return live[runtime.role].delete(runtime);
    },
    snapshot,
  });
})();

class BrowserPythonRuntime {
  constructor({workerURL = "/browser-worker.js", initTimeout = 180_000, runTimeout = 8_000, onState = () => {}, role = "interactive"} = {}) {
    if (!Object.hasOwn(FACTORY_WORKER_RESOURCE_CONTRACT, role)) throw new TypeError(`Unknown browser Worker role: ${role}`);
    this.workerURL = workerURL;
    this.initTimeout = initTimeout;
    this.runTimeout = runTimeout;
    this.onState = onState;
    this.role = role;
    this.worker = null;
    this.ready = null;
    this.pending = new Map();
    this.serial = 0;
    this.state = "idle";
    this.runtime = null;
    this.resourceId = null;
    this.idleReclaims = 0;
    this.lastIdleReclaim = null;
  }

  setState(state) {
    this.state = state;
    this.onState(state, this.runtime);
  }

  createWorker() {
    // One runtime owns at most one physical Worker. Returning the existing
    // initialization promise keeps direct/reentrant callers on that owner and
    // prevents a Worker reference from being overwritten outside the ledger.
    if (this.worker) return this.ready;
    factoryWorkerResources.allocate(this);
    const rule = FACTORY_WORKER_RESOURCE_CONTRACT[this.role];
    let worker;
    try {
      worker = new Worker(this.workerURL, {name: `${rule.workerName}-${this.resourceId}`});
      this.worker = worker;
    } catch (error) {
      factoryWorkerResources.release(this);
      this.resourceId = null;
      throw error;
    }
    this.worker.addEventListener("message", (event) => this.receive(event.data));
    this.worker.addEventListener("error", (event) => {
      if (this.worker !== worker) return;
      const initializing = this.state === "loading";
      const error = new Error(event.message || "Browser worker failed");
      if (initializing) error.code = "browser_init_error";
      this.terminate(error);
      this.setState(initializing ? "init_error" : "stopped");
    });
    this.ready = this.request("init", {}, this.initTimeout).then((payload) => {
      if (this.worker !== worker) throw Object.assign(new Error("Browser runtime stopped"), {code: "browser_stopped"});
      this.runtime = payload.runtime;
      this.setState("ready");
      return payload;
    }).catch(error => {
      if (this.worker === worker) {
        this.terminate(error);
        this.setState("init_error");
      }
      throw error;
    });
    return this.ready;
  }

  receive(message) {
    const pending = this.pending.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(message.id);
    if (message.resources?.idle_reclaimed) {
      this.idleReclaims += 1;
      this.lastIdleReclaim = Object.freeze({...message.resources});
    }
    if (message.type === "error") {
      const error = Object.assign(new Error(message.error || "Browser runtime error"), message);
      pending.reject(error);
    } else {
      pending.resolve(message);
    }
  }

  request(type, body = {}, timeout = this.runTimeout) {
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const error = Object.assign(new Error("Browser runtime wall-time limit exceeded"), {code: type === "init" ? "browser_init_timeout" : "browser_timeout"});
        this.terminate(error);
        this.setState(type === "init" ? "init_error" : "stopped");
        reject(error);
      }, timeout);
      this.pending.set(id, {resolve, reject, timer});
      this.worker.postMessage({type, id, ...body});
    });
  }

  async ensureReady() {
    if (!this.worker) {
      this.setState("loading");
      return this.createWorker();
    }
    return this.ready;
  }

  async parse(source) {
    await this.ensureReady();
    this.setState("checking");
    try {
      const message = await this.request("parse", {source});
      return message.payload;
    } finally {
      if (this.worker) this.setState("ready");
    }
  }

  async sync(source, model) {
    await this.ensureReady();
    this.setState("checking");
    try {
      const message = await this.request("sync", {source, model});
      return message.payload;
    } finally {
      if (this.worker) this.setState("ready");
    }
  }

  async run(source) {
    await this.ensureReady();
    this.setState("running");
    try {
      const message = await this.request("run", {source});
      return message.payload;
    } finally {
      if (this.worker) this.setState("ready");
    }
  }

  async runSeed(source, seed) {
    await this.ensureReady();
    this.setState("running");
    try {
      const message = await this.request("run_seed", {source, seed});
      return message.payload;
    } finally {
      if (this.worker) this.setState("ready");
    }
  }

  stop() {
    if (!this.worker) return false;
    this.terminate(Object.assign(new Error("Browser runtime stopped"), {code: "browser_stopped"}));
    this.setState("stopped");
    return true;
  }

  terminate(error) {
    const worker = this.worker;
    this.worker = null;
    this.ready = null;
    this.runtime = null;
    factoryWorkerResources.release(this);
    this.resourceId = null;
    worker?.terminate();
    this.failAll(error);
  }

  resourceSnapshot() {
    return Object.freeze({
      ...factoryWorkerResources.snapshot(),
      role: this.role,
      state: this.state,
      idleReclaims: this.idleReclaims,
      lastIdleReclaim: this.lastIdleReclaim,
    });
  }

  failAll(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}

window.BrowserPythonRuntime = BrowserPythonRuntime;
window.FACTORY_WORKER_RESOURCE_CONTRACT = FACTORY_WORKER_RESOURCE_CONTRACT;
window.factoryWorkerResources = factoryWorkerResources;
