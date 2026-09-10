import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fetch_browser_runtime', ROOT / 'scripts' / 'fetch_browser_runtime.py')
runtime_fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_fetch)


class Response:
    def __init__(self, value):
        self.value = value
        self.offset = 0

    def read(self, size):
        value = self.value[self.offset:self.offset + size]
        self.offset += len(value)
        return value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class BrowserRuntimeTests(unittest.TestCase):
    def test_artifact_versions_and_hashes_are_frozen(self):
        self.assertEqual(runtime_fetch.PYODIDE_VERSION, '0.27.7')
        self.assertEqual(runtime_fetch.SIMPY_VERSION, '4.1.1')
        self.assertEqual(len(runtime_fetch.ARTIFACTS), 6)
        self.assertTrue(all(len(expected) == 64 for _, expected in runtime_fetch.ARTIFACTS.values()))

    def test_fetch_rejects_artifact_that_does_not_match_frozen_hash(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime_fetch, 'ARTIFACTS', {'runtime.bin': ('https://invalid.test/runtime', '0' * 64)}), patch.object(runtime_fetch, 'urlopen', return_value=Response(b'tampered')):
            with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
                runtime_fetch.fetch(Path(directory))
            self.assertFalse((Path(directory) / 'runtime.bin').exists())

    def test_worker_uses_static_runtime_and_no_execution_api(self):
        worker = (ROOT / 'web' / 'browser-worker.js').read_text()
        controller = (ROOT / 'web' / 'browser-runtime.js').read_text()
        self.assertIn('Factory(model', worker)
        self.assertIn('self.fetch = () => Promise.reject', worker)
        self.assertIn('self.postMessage = () =>', worker)
        self.assertNotIn('/api/', worker + controller)
        self.assertIn('worker?.terminate()', controller)

    def test_runtime_manifest_records_every_download(self):
        cache = ROOT / '.cache' / 'browser-runtime' / 'runtime-manifest.json'
        if not cache.exists():
            self.skipTest('browser runtime cache has not been fetched')
        manifest = json.loads(cache.read_text())
        self.assertEqual(manifest['python'], '3.12.7')
        for name, expected in manifest['artifacts'].items():
            self.assertEqual(hashlib.sha256((cache.parent / name).read_bytes()).hexdigest(), expected)


if __name__ == '__main__':
    unittest.main()
