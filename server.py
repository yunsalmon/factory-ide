"""Local-only HTTP application. No build step or external frontend CDN."""
from messages import message, descriptor
import platform
import simpy
import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from model import ModelError, parse, synchronize

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.Lock()


def stop_process(process):
    if process.poll() is None:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()


def run_job(job, source):
    try:
        stdout, stderr = job['process'].communicate(json.dumps({'source': source}), timeout=20)
        payload = json.loads(stdout) if stdout else {'ok': False, 'error': message('message_38'), 'traceback': stderr[-4000:]}
    except subprocess.TimeoutExpired:
        stop_process(job['process'])
        job['process'].communicate()
        payload = {'ok': False, 'error': message('message_39')}
    except Exception as e:
        payload = {'ok': False, 'error': str(e)}
    with LOCK:
        if job['status'] != 'cancelled':
            job.update(status='done', payload=payload)
            if descriptor(payload.get('error')):
                payload['error_message'] = descriptor(payload['error'])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, payload, content_type='application/json; charset=utf-8'):
        if isinstance(payload, dict):
            payload = dict(payload)
            if descriptor(payload.get('error')):
                payload['error_message'] = descriptor(payload['error'])
            if 'warnings' in payload:
                payload['warning_messages'] = [descriptor(w) for w in payload['warnings']]
        data = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, (dict, list)) else payload
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if urlparse(self.path).path == "/browser-worker.js":
            policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; connect-src 'self'; object-src 'none'; base-uri 'none'"
        self.send_header('Content-Security-Policy', policy)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self):
        port = self.server.server_port
        bind_host = self.server.server_address[0]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}', f'{bind_host}:{port}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (not origin or origin in {f'http://{host}' for host in hosts})

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': message('message_40')})
        path = urlparse(self.path).path
        if path == '/locales.js':
            catalogue = (ROOT / 'web/locales.json').read_text()
            return self.send(200, ('const translations = ' + catalogue + ';').encode(), 'text/javascript; charset=utf-8')
        if path == '/api/bootstrap':
            return self.send(200, {'token': TOKEN, 'source': (ROOT / 'examples/demo.py').read_text(), 'version': '1.0.0', 'runtime': {'kind': 'local', 'application': '1.0.0', 'runtime': {'python': platform.python_version(), 'simpy': simpy.__version__}}})
        if path.startswith('/api/jobs/'):
            if self.headers.get('X-Factory-Token') != TOKEN:
                return self.send(403, {'error': message('message_41')})
            with LOCK:
                job = JOBS.get(path.rsplit('/', 1)[1])
                result = {k: v for k, v in job.items() if k in ('status', 'payload')} if job else None
            return self.send(200 if result else 404, result or {'error': message('message_42')})
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/i18n.js': ('i18n.js', 'text/javascript'), '/allocation-results.js': ('allocation-results.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
        assets.update({f'/{name}': (name, 'text/javascript') for name in ('inventory-projection.js', 'inventory-ui.js', 'scenario-comparison.js', 'scenario-ui.js')})
        assets.update({f'/{name}': (name, 'text/javascript') for name in ('order-projection.js', 'order-ui.js')})
        assets.update({f'/{name}': (name, 'text/javascript') for name in ('browser-runtime.js','browser-worker.js','experiment-core.js','experiment-ui.js')})
        if path.startswith('/runtime/') and path.removeprefix('/runtime/') in ('engine.py','model.py','messages.py','orders.py','disruptions.py','operation_metrics.py'):
            return self.send(200, (ROOT / path.removeprefix('/runtime/')).read_bytes(), 'text/plain; charset=utf-8')
        if path.startswith('/vendor/pyodide/'):
            name = path.removeprefix('/vendor/pyodide/')
            target = ROOT / '.cache/browser-runtime' / name
            if '/' not in name and name in ('pyodide.js','pyodide.asm.js','pyodide.asm.wasm','python_stdlib.zip','pyodide-lock.json','simpy-4.1.1-py3-none-any.whl') and target.is_file():
                return self.send(200, target.read_bytes(), 'application/wasm' if name.endswith('.wasm') else 'text/javascript' if name.endswith('.js') else 'application/octet-stream')
        assets['/locales.json'] = ('locales.json', 'application/json')
        assets['/operations.js'] = ('operations.js', 'text/javascript')
        if path.startswith('/vendor/codemirror/'):
            name = path.removeprefix('/vendor/codemirror/')
            if '/' not in name and name.endswith(('.js', '.css')) and (ROOT / 'web/vendor/codemirror' / name).is_file():
                mime = 'text/javascript' if name.endswith('.js') else 'text/css'
                return self.send(200, (ROOT / 'web/vendor/codemirror' / name).read_bytes(), mime + '; charset=utf-8')
        if path not in assets:
            return self.send(404, {'error': 'Not found'})
        name, mime = assets[path]
        return self.send(200, (ROOT / 'web' / name).read_bytes(), mime + '; charset=utf-8')

    def do_POST(self):
        if not self.allowed() or self.headers.get('X-Factory-Token') != TOKEN:
            return self.send(403, {'error': message('message_43')})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 1_000_000:
                return self.send(413, {'error': message('message_44')})
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/parse':
                model, _, warnings = parse(body['source'])
                return self.send(200, {'model': model, 'warnings': warnings})
            if self.path == '/api/sync':
                source = synchronize(body['source'], body['model'])
                model, _, warnings = parse(source)
                return self.send(200, {'source': source, 'model': model, 'warnings': warnings})
            if self.path == '/api/run':
                parse(body['source'])
                with LOCK:
                    if any(j['status'] == 'running' for j in JOBS.values()):
                        return self.send(409, {'error': message('message_45')})
                    # Keep the latest results; avoid accumulating complete traces in memory.
                    if len(JOBS) >= 3:
                        JOBS.pop(next(iter(JOBS)))
                    process = subprocess.Popen([sys.executable, str(ROOT / 'worker.py')], stdin=subprocess.PIPE,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                               cwd=ROOT, start_new_session=(os.name == 'posix'))
                    job_id = secrets.token_hex(12)
                    job = {'process': process, 'status': 'running', 'created': time.time()}
                    JOBS[job_id] = job
                threading.Thread(target=run_job, args=(job, body['source']), daemon=True).start()
                return self.send(202, {'job_id': job_id})
            if self.path == '/api/cancel':
                with LOCK:
                    job = JOBS.get(body['job_id'])
                    if job and job['status'] == 'running':
                        job['status'] = 'cancelled'
                        stop_process(job['process'])
                return self.send(200, {'status': 'cancelled'})
            return self.send(404, {'error': 'Not found'})
        except (ModelError, ValueError, KeyError, TypeError, AttributeError) as e:
            return self.send(400, {'error': str(e), 'error_message': getattr(e, 'message', None)})
        except Exception as e:
            return self.send(500, {'error': message('message_46' ,e)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1', help=message('message_47'))
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    try:
        address = ipaddress.IPv4Address(args.host)
        if address.is_unspecified:
            parser.error(message('message_48'))
    except ipaddress.AddressValueError:
        parser.error(message('message_49'))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'Factory Studio → http://{args.host}:{server.server_port}', flush=True)
    print(message('message_50'), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for job in JOBS.values():
            stop_process(job['process'])
        server.server_close()
