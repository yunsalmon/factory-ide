# Cold runtime delivery and release gate

The issue33 baseline measured a3,137,600-byte gzip WASM response with
`CF-Cache-Status: DYNAMIC`:68,345ms body transfer versus556ms TTFB,
74,293ms edit→ready. Five small Python engine runs took approximately108–116ms.
The deployed broadband sample of three contexts had cold readiness p75=72,218ms.
These baseline samples identify network delivery as the primary problem; they
are not a five-sample acceptance result.

## Repository-owned change

The build copies the checksum-verified `pyodide.asm.wasm` byte-for-byte to
`/vendor/pyodide/0.27.7/pyodide.asm.wasm.js`. Cloudflare's documented default
eligibility uses the file extension rather than MIME, and includes JS:
[default cache behavior](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/).
This makes the large object eligible without a zone credential or a broad
cache-everything rule. Eligibility is not proof of a HIT: zone overrides,
cache-deception protection, eviction, and a genuinely cold edge can still miss.
The public release gate below must establish the actual result.

The suffix is a delivery convention, not JavaScript code. An exact Nginx
location serves `application/wasm`, inherits `nosniff` and the existing CSP,
and only allows GET/HEAD. The builder preserves the original file and
creates identical deterministic gzip bytes for both names. Both use the
existing bounded one-day cache policy. No HTML, application Python module,
API, or arbitrary directory becomes cacheable as part of this change.

During bootstrap only, the dedicated Worker maps exactly the same-origin,
query-free canonical WASM GET to the alias. All other fetches retain their
original behavior. Fetch SRI checks the original frozen SHA256 against the
decoded body before instantiation. The pinned upstream Pyodide distribution
is unchanged. MIME/status/SRI errors propagate to the controller immediately;
Pyodide0.27.7 otherwise logs a streaming-instantiation failure while leaving
bootstrap pending. Stop destroys the Worker and invalidates these pending
requests. The post-bootstrap model compatibility guards and the document/
Worker CSP isolation boundary remain intact. MIME/byte tampering never falls
back silently to an unverified object or third-party origin.

A `.wasm.js` script-element/importScripts request is rejected as a script by
its WASM MIME and `nosniff`. The browser regression checks this, plus corrupt
bytes, wrong MIME,404, localized initialization errors, cleanup, and a fresh
successful Worker afterward. `version.json.browser_runtime_delivery` records
the effective URL and decoded hash for reviewers and the measurement gate.

## Local checks

Build the static image using the exact committed revision, launch it on a
private loopback port, then run:

```sh
python -m unittest discover -s tests -v
python tests/static_delivery.py http://127.0.0.1:13333
python tests/browser_runtime_delivery.py http://127.0.0.1:13333
python tests/browser_initialization.py http://127.0.0.1:13333
python tests/browser_public.py http://127.0.0.1:13333
python scripts/measure_runtime_delivery.py http://127.0.0.1:13333 \
  --revision COMMITTED_SHA --profile broadband --output /tmp/local-broadband.json
python scripts/measure_runtime_delivery.py http://127.0.0.1:13333 \
  --revision COMMITTED_SHA --profile slow4g --output /tmp/local-slow4g.json
```

The gate defaults to five fresh browser contexts, rotating en/ko/ja. It clears
browser cache, serializes clients, does not prewarm/purge the CDN, and uses
only bundled synthetic source. CDP network emulation is installed on the page. Dedicated Worker Network
observation is enabled before it resumes; direct Worker-target emulation is
unsupported by Chromium (the negative prototype returned code-32000). Inspect
the actual Worker transfer timings to assess the effective profile; these
profiles are not a guaranteed hardware network shaper. Slow4G also uses4× page CPU
slowdown; this is not a claim that Worker CPU is emulated as a physical phone.
It records browser version, revision, status transitions, actual Worker GET
headers, transfer/decoded byte counts, resource timing, warm validation and
full small-demo run/result time. No tokens or user source are recorded.

## Public acceptance after an authorized deployment

Do not declare the local result to be a public latency improvement. After the
ordinary site deployment has been authorized and completed, run the same
commands against `https://factory.yshnote.com`, adding `--require-edge-hit`.
This requires actual network Worker GET HITs in at least two distinct fresh
contexts. A cached response retaining an old `CF-Cache-Status` header with
`fromDiskCache=true` does not count. Every sample, including an initial edge
MISS, remains in the timing distribution. Fewer than five complete samples,
errors, absent alias requests, broadband p75>15s, any Slow4G readiness>45s,
feedback>100ms, validation p95>500ms, or demo-result p95>2s fail the gate.

Compare before/after raw JSON with the same hardware, browser and profiles.
Separate WASM body download from TTFB, other runtime resources, the interval
between last resource and ready, validation and execution. Concurrent fetch
intervals must not be added as if they were serial CPU time. Inspect the
first MISS independently: a cache can improve p75 while its first-edge user
still exceeds the budget. Do not conceal this by priming an edge first.

If the alias stays DYNAMIC or misses budgets, this issue remains open. Inspect
zone cache overrides and cache-deception settings; an explicit narrowly
scoped cache rule may be necessary. Cloudflare documents individual-rule
creation/update APIs; avoid replacing a zone's full ruleset:
[Cache Rules API](https://developers.cloudflare.com/cache/how-to/cache-rules/create-api/).
Account values and credentials belong to deployment infrastructure, never the
repository. This change does not call an account API, mutate cache settings,
purge objects, or deploy the site.

## Upgrade and rollback

A runtime upgrade must change its version directory, pinned hashes, Worker
SRI, exact Nginx alias location and delivery metadata together. Never serve
different decoded bytes at the same versioned alias. The existing unit and
HTTP/browser tests detect hash/MIME mismatches. Retain old runtime paths
through the cache window during staged deployments. Deploy worker and assets
atomically; a worker paired with an older image missing the alias fails
visibly. Rolling back the complete image restores the canonical fetch path;
an old cached alias cannot affect an old Worker that does not request it.

Public edge/latency acceptance requires deployment and has not been asserted
from a local proxy or simulated HIT header.
