"use strict";

class BrowserPythonRuntime {
  constructor({workerURL = "/browser-worker.js", initTimeout = 60_000, runTimeout = 8_000, onState = () => {}} = {}) {
    this.workerURL = workerURL;
    this.initTimeout = initTimeout;
    this.runTimeout = runTimeout;
    this.onState = onState;
    this.worker = null;
    this.ready = null;
    this.pending = new Map();
    this.serial = 0;
    this.state = "idle";
    this.runtime = null;
  }

  setState(state) {
    this.state = state;
    this.onState(state, this.runtime);
  }

  createWorker() {
    this.worker = new Worker(this.workerURL);
    this.worker.addEventListener("message", (event) => this.receive(event.data));
    this.worker.addEventListener("error", (event) => {
      this.terminate(new Error(event.message || "Browser worker failed"));
      this.setState("stopped");
    });
    this.ready = this.request("init", {}, this.initTimeout).then((payload) => {
      this.runtime = payload.runtime;
      this.setState("ready");
      return payload;
    });
    return this.ready;
  }

  receive(message) {
    const pending = this.pending.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(message.id);
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
        const error = Object.assign(new Error("Browser runtime wall-time limit exceeded"), {code: "browser_timeout"});
        this.terminate(error);
        this.setState("stopped");
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
    worker?.terminate();
    this.failAll(error);
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
