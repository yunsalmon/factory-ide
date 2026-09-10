# Repeated experiments

Open **Repeated experiments**, enter a named definition, notes and an explicit list of distinct integer seeds. The replication count is the list length (1–30); seeds range from 0 to 4294967295. Choose one or two concurrent Workers and run. The current source/model is parsed and frozen at the start. Editing the IDE afterward does not change the experiment. All runs use isolated browser Python, including in the local IDE.

For local use, first fetch the pinned browser runtime with `python scripts/fetch_browser_runtime.py`; the local server then serves that cache and the same checked-in engine modules. Static builds already include these files. A missing/broken cache produces visible failed runs. The local document keeps its restrictive CSP; only `/browser-worker.js` receives the same Wasm/evaluation policy as the existing static sandbox. No experiment Python is sent to the native execution API.

Each replication synchronizes only `MODEL.seed` in the Python source, preserving hooks and comments. It resets Python's `random` module to that seed before executing a fresh model namespace. Engine `ctx['rng']` is also controlled by MODEL.seed. Use those generators for reproducible models. Hooks that intentionally depend on nondeterministic external state are not claimed reproducible. Runs record both the seeded input-model hash and migrated result-model hash, seeded source hash, trace hash and actual Python/Pyodide/SimPy versions and a checksum of the loaded factory runtime modules/catalog. Failed initialization has explicitly unavailable runtime metadata. The experiment definition records original source/model and hashes, name, notes, ordered seed list and concurrency. Completion order does not change record order or representative selection.

## Bounds and cancellation

At most two experiment Workers exist concurrently. Each has the existing 60-second initialization and 8-second execution deadlines; a timeout terminates its Worker. Runs obey the engine's 50,000 event limit. Source is bounded to 1 MB; each experiment response (including seeded source and trace) is checked against 5 MB inside Python before crossing the Worker boundary. At most 2,000 scalar KPI fields are retained per run.

Only compact metrics/status/hashes/runtime are retained for all runs, plus one representative trace: the lowest successful index in the explicit seed list. This is a deterministic example, not a claim that it is statistically typical. Cancel also invalidates the initiation token during parsing or either asynchronous definition digest, so a cancelled startup cannot create Workers afterward. Cancel terminates outstanding Workers, marks queued/running replications cancelled, retains completed measurements, and permits a clean next experiment. The application keeps the latest two finished experiments plus the active experiment in page memory; export before a third replaces the oldest. Reload clears experiment data. A versioned artifact is bounded to 20 MB and two experiments. These are data/Worker-count bounds, not a claim of a fixed browser heap size; each Python runtime also needs its own Wasm memory.

## Statistics and exclusions

Metrics reuse `scenarioContract` and `scenarioMetrics`: identical minute horizons/schema tuples, full machine/state measurement coverage, and complete finite-buffer interval evidence. Failed/cancelled runs are listed with status and failure text and excluded from **every** KPI. A successful run with an unavailable particular KPI is excluded from **that** KPI. Every summary row shows measured n and excluded count; failures and missing measurements are never replaced by zero. An all-failed experiment shows no measured samples and cannot claim a comparable successful contract.

Distribution values are available in sorted order, with minimum/maximum and linearly interpolated p50/p90: rank `(n−1)p`, interpolation between adjacent observations. The arithmetic mean uses measured observations only. The two-sided 95% interval for the mean is `mean ± t(0.975, n−1) × sample_sd / sqrt(n)` using Student-t critical values for 1–29 degrees of freedom, stored to at least nine significant digits. With fewer than two measurements the interval is unavailable. A complete constant sample has a genuine zero-width interval. This interval assumes independent seeds and sufficiently normal sample means; it is not a distribution/prediction interval or a universal guarantee for arbitrary skewed models.

Experiment comparison reports candidate minus baseline means and each measured n. Different seed lists/runtime metadata produce explicit warnings. This is an unpaired descriptive comparison, without a significance test or causal claim. Horizon/unit/schema mismatches are rejected. Representative replay loads the stored source/trace into existing replay views without executing it.

## Artifacts and interfaces

`factory-experiments` version 1 contains up to two experiment definitions, per-seed statuses/metrics/hashes/runtime, the representative source/trace, and a canonical SHA-256 content hash. Original objects are recursively checked for non-finite numbers before any JSON copy or canonical hash, including representative traces and runtime/definition extensions. NaN and either Infinity (including JSON numeric overflow such as `1e309`) are rejected; valid null measurements are preserved. Import verifies that hash, definition hashes, seed/status/count/metric bounds, and representative model/source/trace hashes, contract and computed metrics. Aggregates are recomputed from per-run measurements; cached means are not trusted. The content hash detects accidental modification, not authenticity from an untrusted producer. Nonrepresentative traces are intentionally not retained; per-run metrics and trace hashes remain reviewable, but those traces cannot be replayed without rerunning.

- `experimentDefinition`, `experimentSeeds`: bounded frozen input contract.
- `ExperimentRunner.run/cancel`: bounded Worker lifecycle and deterministic records.
- `experimentStats`, `experimentAggregate`, `experimentCompare`: pure numerical operations.
- `experimentExport/experimentRestore`: asynchronous hashed artifact boundary, no execution.
- `BrowserPythonRuntime.runSeed`: internal seeded Worker operation, separate from existing normal run.

Tests include independent percentile/Student-t fixtures, deterministic out-of-order scheduling and failures, cancellation/recovery, integrity/seed bounds, actual Python random reproducibility, locale export/import/replay, main-thread heartbeat and mobile overflow.
