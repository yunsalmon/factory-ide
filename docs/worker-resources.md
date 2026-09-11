# Browser Worker resource contract

The public editor deliberately retains one initialized Python Worker while its
page is open. Reusing that Worker keeps warm validation below 500 ms; a new
Pyodide runtime takes seconds even when its files are cached. The resource
contract therefore distinguishes the interactive runtime from bounded task
Workers instead of promising zero total Workers after every action.

`FACTORY_WORKER_RESOURCE_CONTRACT` version 1 is the executable definition:

| Role | Maximum while active | Expected after the work settles | Lifetime |
| --- | ---: | ---: | --- |
| `interactive` | 1 | 1 after first initialization; 0 before use or after Stop/Reset/Worker failure/timeout/pagehide | Reused by edit validation, synchronization, and Run |
| `experiment` | 2 | 0 | One isolated Python runtime per experiment lane |
| data import | 1 | 0 | One `/data-worker.js` owned by one import preview/apply operation |

The settled total after an experiment is consequently one Worker target: the
interactive runtime. That target is not an experiment leak. A completed or
cancelled experiment must have `ExperimentRunner.workers.size === 0`, ledger
`experiment === 0`, and no extra browser Worker target. An explicit interactive
Stop must reduce both the ledger and the browser target count to zero, and the
next validation may create one fresh interactive runtime.

The interactive target is count-bounded and request memory is reclaimed while
the warm runtime stays resident. Before every non-initialization reply,
`browser-worker.js` deletes the transient operation, source, seed, and model
bridge globals and runs Python garbage collection. The reply carries
`resources.idle_reclaimed`; `BrowserPythonRuntime.resourceSnapshot()` records
the acknowledgement count and last reclamation. Handled Python parse, model,
or execution errors reclaim request memory and retain the ready interactive
target for a warm correction and retry. Stop, Reset, native Worker failure,
initialization/execution timeout, and pagehide terminate the target and clear
pending requests. Page teardown also invalidates an experiment that is still
starting and cancels all active experiment lanes and data import work.

## Automated measurement

Run the contract gate against a freshly built static image:

```sh
docker build -f deploy/Dockerfile --build-arg SOURCE_REVISION="$SHA" -t factory:test .
docker run --rm -d --name factory-test -p 127.0.0.1:13333:8080 factory:test
python tests/browser_worker_resources.py http://127.0.0.1:13333 artifacts/worker-resources.json
```

The gate uses three independent views together:

- the versioned product ledger distinguishes interactive and experiment roles;
- constructor/terminate instrumentation records actual Worker names and the
  concurrent experiment peak;
- Chrome DevTools Protocol counts all dedicated Worker targets, so an
  unregistered `/browser-worker.js` or `/data-worker.js` still fails.

It runs five four-seed/max-two experiments. After forced page garbage
collection and a short settling interval, every repetition must return to one
total target and zero experiment Workers. From the first completed repetition
to the fifth, final page used heap and summed Chrome RSS must be no more than
120% of the first checkpoint (with a 1 MiB allowance for the small page heap).
RSS is a summed process measurement and may include shared browser memory; it
is a regression signal, not an isolated Wasm-heap measurement. Target counts
are the leak boundary.

The same run requires five warm validations with p95 at most 500 ms, an idle
reclamation acknowledgement for every validation, cancellation to leave zero
experiment Workers, a successful recovery experiment, an explicit interactive
Stop to leave zero total targets, and a successful fresh interactive recovery.
It also rejects any `/api/` request and checks that experiment concurrency never
exceeds two. Synthetic bundled model data is used throughout.

Data import Worker cancellation and normal completion are enforced separately
by `browser_data.py`; the combined browser gate still counts any unexpected
remaining data Worker target. Public Worker fetch/CSP/API isolation remains
covered by `static_delivery.py`, `browser_runtime_delivery.py`, and
`browser_public.py`.
