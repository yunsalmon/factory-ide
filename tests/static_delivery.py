"""Run against a locally built static container: python tests/static_delivery.py URL."""
import gzip
import hashlib
import json
import sys
import urllib.error
from urllib.request import Request, urlopen

base = sys.argv[1].rstrip('/')
def get(path, headers=None):
    try:
        response = urlopen(Request(base + path, headers=headers or {}), timeout=30)
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()
    with response:
        return response.status, response.headers, response.read()

_, _, version = get('/version.json')
version = json.loads(version)
runtime = version['browser_runtime']
for name in ['pyodide.asm.wasm', 'pyodide.asm.js', 'pyodide.js']:
    path = '/vendor/pyodide/' + runtime['pyodide'] + '/' + name
    status, raw_headers, raw = get(path, {'Accept-Encoding': 'identity'})
    assert status == 200 and raw_headers.get('Content-Encoding') is None
    status, headers, compressed = get(path, {'Accept-Encoding': 'gzip'})
    assert status == 200 and headers['Content-Encoding'] == 'gzip'
    assert 'Accept-Encoding' in headers['Vary']
    assert headers['Cache-Control'] == 'public, max-age=86400, must-revalidate'
    assert len(compressed) < len(raw) * .95
    assert gzip.decompress(compressed) == raw
    assert hashlib.sha256(raw).hexdigest() == runtime['artifacts'][name]
    if name.endswith('.wasm'):
        assert headers['Content-Type'] == 'application/wasm'
    status, _, body = get(path, {'Accept-Encoding': 'gzip', 'If-None-Match': headers['ETag']})
    assert status == 304 and not body
    print(f'PASS {name}: {len(raw):,} -> {len(compressed):,} bytes ({100*len(compressed)/len(raw):.1f}%)')

for path in ['/runtime/engine.py', '/browser-runtime.js', '/browser-worker.js']:
    status, headers, body = get(path, {'Accept-Encoding': 'gzip'})
    assert status == 200 and headers['Content-Encoding'] == 'gzip'
    assert headers['Cache-Control'] == 'public, max-age=0, must-revalidate'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    status, _, _ = get(path, {'If-None-Match': headers['ETag'], 'Accept-Encoding': 'gzip'})
    assert status == 304
_, worker, _ = get('/browser-worker.js')
_, document, _ = get('/')
assert "'unsafe-eval'" in worker['Content-Security-Policy']
assert "'unsafe-eval'" not in document['Content-Security-Policy']
assert "worker-src 'self'" in document['Content-Security-Policy']
assert document['Cache-Control'] == 'no-cache'
assert get('/api/run')[0] == 404
print('PASS runtime revalidation, document/worker CSP and API boundary')
