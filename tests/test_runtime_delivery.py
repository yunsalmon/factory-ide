import base64
import copy
import importlib.util
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('delivery',ROOT/'scripts/measure_runtime_delivery.py')
delivery=importlib.util.module_from_spec(spec);spec.loader.exec_module(delivery)

class RuntimeDeliveryTests(unittest.TestCase):
    def rows(self):
        return [dict(dashboard_ms=1000,edit_ready_ms=6000,feedback_ms=25,run_ms=300,validation_ms=[10]*5,
            delivery={'wasm_path':'/vendor/pyodide/0.27.7/pyodide.asm.wasm.js'},wasm_gets=[{
                'url':'https://example.invalid/vendor/pyodide/0.27.7/pyodide.asm.wasm.js',
                'disk_cache':False,'headers':{'cf-cache-status':'HIT'}}]) for _ in range(5)]

    def test_pin_sri_matches_existing_frozen_artifact(self):
        source=(ROOT/'web/browser-worker.js').read_text()
        sri=re.search(r'const WASM_INTEGRITY = "sha256-([^"]+)"',source)[1]
        self.assertEqual(base64.b64decode(sri).hex(),'a50dd1843f805a0b7c45b61037ee0d7b26dfe85efe0e18ef95a34ad24e401f5f')

    def test_gate_passes_five_measured_edge_hits(self):
        self.assertEqual(delivery.verdict(self.rows(),'broadband',True)[0],[])

    def test_cached_header_is_not_actual_edge_hit(self):
        rows=self.rows()
        for row in rows:row['wasm_gets'][0]['disk_cache']=True
        self.assertTrue(delivery.verdict(rows,'broadband',True)[0])

    def test_one_edge_hit_is_not_repeated_edge_evidence(self):
        rows=self.rows()
        for row in rows[1:]:row['wasm_gets'][0]['headers']['cf-cache-status']='DYNAMIC'
        self.assertTrue(delivery.verdict(rows,'broadband',True)[0])

    def test_missing_failed_or_insufficient_samples_never_pass(self):
        self.assertTrue(delivery.verdict(self.rows()[:4],'broadband',False)[0])
        rows=self.rows();rows[0]={'error':'timeout'}
        self.assertTrue(delivery.verdict(rows,'broadband',False)[0])
        rows=self.rows();rows[0]['wasm_gets']=[]
        self.assertTrue(delivery.verdict(rows,'broadband',False)[0])

    def test_latency_and_warm_validation_breaches_fail(self):
        for field,value in [('edit_ready_ms',16000),('feedback_ms',101),('run_ms',2001),('validation_ms',[501]*5)]:
            rows=self.rows()
            for row in rows:row[field]=value
            self.assertTrue(delivery.verdict(rows,'broadband',False)[0],field)
        rows=self.rows();rows[0]['edit_ready_ms']=45001
        self.assertTrue(delivery.verdict(rows,'slow4g',False)[0])

if __name__=='__main__':unittest.main()
