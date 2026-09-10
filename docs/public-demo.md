# Public demo operations

The proposed address is `https://factory.yshnote.com`; it is **not configured or accepted by this change**. Record the actual accepted URL, commit, image ID, trace hash, UTC time and external browser evidence in the release record after publication. Local container checks do not satisfy external HTTPS acceptance.

The public artifact contains HTML/JS/CSS, checksum-pinned Pyodide 0.27.7 and SimPy 4.1.1, and a precomputed schema-v2 fallback. It never starts `server.py` or `worker.py`; Nginx rejects `/api/*` and its image has no native Python executable. Python runs in a dedicated browser Web Worker and returns the existing result contract. Stop or the eight-second limit destroys that worker, so the next run starts a fresh interpreter. File and JS/network imports are denied to user code, the worker has no DOM, and its virtual filesystem disappears with the worker. Reload restores matching browser-local source/results; **Reset to example** clears them and restores the fallback.

## Reproducible build and isolated validation

Use a clean checkout of the exact reviewed commit; do not tag modified working trees as that commit. Base images are pinned by digest; record final image IDs for exact rollback. `scripts/fetch_browser_runtime.py` pins and verifies every browser runtime URL and SHA-256 in a reusable build cache.

```sh
git status --porcelain
FACTORY_REVISION=$(git rev-parse HEAD)
docker build -f deploy/Dockerfile --build-arg SOURCE_REVISION="$FACTORY_REVISION" -t "factory-demo:$FACTORY_REVISION" .
docker image inspect "factory-demo:$FACTORY_REVISION" --format '{{.Id}}'
docker run -d --name factory-demo-check --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m --cap-drop ALL --security-opt no-new-privileges:true -p 127.0.0.1:13084:8080 "factory-demo:$FACTORY_REVISION"
curl -fsS http://127.0.0.1:13084/healthz
curl -fsS http://127.0.0.1:13084/version.json
python tests/browser_public.py http://127.0.0.1:13084
docker inspect factory-demo-check --format '{{.State.Health.Status}}'
docker exec factory-demo-check sh -c 'command -v python || command -v python3 || true'
docker logs --tail 100 factory-demo-check
docker restart factory-demo-check
curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:13084/version.json
docker rm -f factory-demo-check
```

Install requirements-dev.txt and `python -m playwright install chromium` in the build/test environment. Build/test Python is never included in the runtime image. For other static hosts, `python scripts/build_public.py --output dist` creates the root document; configure GET/HEAD only, no API/function proxies, root asset paths, JSON/JS/WASM MIME types and no stale HTML caching. Preserve the Nginx CSP split: generated-code permission is scoped to `/browser-worker.js`. `/`, `/?demo=1#replay`, `/install.html` and `/version.json` are supported direct links; unknown paths return 404.

`version.json` and the on-screen version identify the source commit, source/schema/trace hashes, runtime versions and every runtime artifact hash. Custom result JSON records its executed source hash, source revision and actual worker versions. All assets are replaced together through an immutable image.

## Proposed ingress connection (infra/service coordination required)

Do not alter existing blog/theme services, ports 3001/3005 or their networks. Confirm DNS record absence/conflicts and ownership of the candidate hostname first. Use a dedicated Docker network so no host service needs exposure:

```sh
docker network create factory-public
export FACTORY_REVISION=<reviewed-full-commit-sha>
docker compose -f deploy/compose.yml up -d
docker network connect factory-public cloudflared
```

The last command assumes the existing tunnel container is named `cloudflared`; substitute its inspected actual name. Persist that additional network in the tunnel's owning service definition before its next recreation. The new service listens only on loopback port 3084 for operator diagnostics; tunnel traffic reaches container port 8080 on `factory-public`.

Add this entry to `/home/yun/.cloudflared/config.yml` **before the existing catch-all**, leaving all existing entries intact:

```yaml
  - hostname: factory.yshnote.com
    service: http://factory-demo:8080
```

The tunnel owner then validates ingress with its existing credentials/config and creates a proxied CNAME for `factory.yshnote.com` to the same tunnel's `<tunnel-id>.cfargotunnel.com` target (or uses `cloudflared tunnel route dns <tunnel-id> factory.yshnote.com`). Do not copy tunnel credentials into this repository, image, logs or change report. Restart/reload the tunnel through its owner-controlled service only after validating the complete ingress configuration. Cloudflare terminates HTTPS; the isolated origin is static and contains no authenticated API, sessions or permissive CORS configuration. Host and Origin are not used to authorize computation, because none exists. Both expected Host and hostile Origin must still receive only static responses or API rejection.

## External acceptance and release record

From outside the host/network, run:

```sh
curl -fsS https://factory.yshnote.com/healthz
curl -fsS https://factory.yshnote.com/version.json
python tests/browser_public.py https://factory.yshnote.com
```

Confirm TLS certificate validity, HTTP to HTTPS policy, root assets/MIME types, direct links and reload. The browser test checks all three locales, playback, allocation before/after details and final results, local install navigation, no browser API traffic, and direct malicious POST rejection with a foreign Origin. Save the output and browser screenshots with the actual URL/commit as release evidence. Also check original blog hosts remain healthy after tunnel changes. Until these checks pass, label the release **local validation only; external acceptance pending**.

## Health, logs, restart, update and rollback

Nginx health probes run every 30 seconds. `/healthz` confirms static service readiness; `version.json` confirms artifact identity. Docker restart policy handles process exit and host reboot; an unhealthy-but-running container requires operator investigation/restart (Docker alone does not restart unhealthy containers). Logs are stdout/stderr with 10 MB × 3 rotation in Compose.

```sh
docker compose -f deploy/compose.yml ps
docker logs --tail 100 factory-demo
docker inspect factory-demo --format '{{.State.Health.Status}}'
docker compose -f deploy/compose.yml restart factory-demo
```

For an update, retain the previous full SHA and image ID, build the new clean commit, pass isolated tests, then set `FACTORY_REVISION` to the new SHA and `docker compose -f deploy/compose.yml up -d --no-build`. Check health/version and external browser acceptance again. For rollback, set `FACTORY_REVISION` to the retained previous SHA and run the same command; verify the restored version externally. Never prune the previous image until the new release is accepted. No persistent application data or schema migration exists. To remove publication, the tunnel owner removes only this ingress/DNS entry and the dedicated network attachment, then stops the demo Compose service. Existing blog routing stays intact.
