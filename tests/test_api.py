import json
from pathlib import Path
import threading
import time
import unittest
import urllib.error
import urllib.request

import server

DEMO = (Path(__file__).resolve().parents[1] / 'examples/demo.py').read_text()


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        for job in server.JOBS.values():
            server.stop_process(job['process'])
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_versioned_experiment_runtime_route(self):
        from scripts.fetch_browser_runtime import PYODIDE_VERSION
        cached = Path(__file__).resolve().parents[1] / '.cache/browser-runtime/pyodide.js'
        if not cached.is_file():
            self.skipTest('runtime cache unavailable')
        with urllib.request.urlopen(self.base + f'/vendor/pyodide/{PYODIDE_VERSION}/pyodide.js') as response:
            self.assertEqual(response.read(), cached.read_bytes())
        for path in ['/vendor/pyodide/0.0.0/pyodide.js', '/vendor/pyodide/0.27.7/../server.py']:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + path)
            self.assertEqual(error.exception.code, 404)

    def test_worker_only_wasm_policy_and_runtime_allowlist(self):
        for path in ['/', '/app.js']:
            with urllib.request.urlopen(self.base + path) as response:
                self.assertNotIn("unsafe-eval", response.headers['Content-Security-Policy'])
        with urllib.request.urlopen(self.base + '/browser-worker.js') as response:
            self.assertIn("wasm-unsafe-eval", response.headers['Content-Security-Policy'])
        with urllib.request.urlopen(self.base + '/runtime/model.py') as response:
            self.assertIn(b'def synchronize', response.read())
        for path in ['/runtime/server.py','/vendor/pyodide/../../server.py']:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + path)
            self.assertEqual(error.exception.code,404)

    def request(self, path, body=None, headers=None, token=True):
        request_headers = {'Content-Type': 'application/json'}
        if token:
            request_headers['X-Factory-Token'] = server.TOKEN
        request_headers.update(headers or {})
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def wait_job(self, job_id):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            _, job = self.request('/api/jobs/' + job_id)
            if job['status'] != 'running':
                return job
            time.sleep(.02)
        self.fail('Worker did not finish')

    def test_bootstrap(self):
        code, data = self.request('/api/bootstrap')
        self.assertEqual(code, 200)
        self.assertEqual(data['source'], DEMO)

    def test_cross_origin_rejected(self):
        code, _ = self.request('/api/run', {'source': DEMO}, {'Origin': 'https://example.com'})
        self.assertEqual(code, 403)

    def test_untrusted_host_rejected(self):
        code, _ = self.request('/api/bootstrap', headers={'Host': 'evil.example'})
        self.assertEqual(code, 403)

    def test_missing_token_rejected(self):
        self.assertEqual(self.request('/api/run', {'source': DEMO}, token=False)[0], 403)

    def test_invalid_source_has_helpful_error(self):
        code, data = self.request('/api/parse', {'source': 'MODEL = {'})
        self.assertEqual(code, 400)
        self.assertIn('1행:', data['error'])

    def test_worker_result(self):
        code, data = self.request('/api/run', {'source': DEMO})
        self.assertEqual(code, 202)
        job = self.wait_job(data['job_id'])
        self.assertTrue(job['payload']['ok'])
        self.assertEqual(job['payload']['result']['summary']['completed'], 30)

    def test_runtime_errors_are_reported(self):
        _, data = self.request('/api/run', {'source': DEMO + '\nraise ValueError("custom failure")'})
        payload = self.wait_job(data['job_id'])['payload']
        self.assertFalse(payload['ok'])
        self.assertIn('custom failure', payload['traceback'])

    def test_cancel_and_concurrent_run_guard(self):
        _, data = self.request('/api/run', {'source': DEMO + '\nwhile True: pass'})
        self.assertEqual(self.request('/api/run', {'source': DEMO})[0], 409)
        self.request('/api/cancel', {'job_id': data['job_id']})
        self.assertEqual(self.wait_job(data['job_id'])['status'], 'cancelled')
        server.JOBS[data['job_id']]['process'].wait(timeout=3)

    def test_print_output_is_captured(self):
        _, data = self.request('/api/run', {'source': DEMO + '\nprint("factory log")'})
        self.assertIn('factory log', self.wait_job(data['job_id'])['payload']['result']['console'])
