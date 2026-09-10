"""Local-only HTTP application. No build step or external frontend CDN."""
import argparse
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
        payload = json.loads(stdout) if stdout else {'ok': False, 'error': '실행 프로세스가 종료되었습니다. 시간·메모리 한도 또는 사용자 코드 오류를 확인하세요.', 'traceback': stderr[-4000:]}
    except subprocess.TimeoutExpired:
        stop_process(job['process'])
        job['process'].communicate()
        payload = {'ok': False, 'error': '실행 제한 시간(20초)을 초과했습니다.'}
    except Exception as e:
        payload = {'ok': False, 'error': str(e)}
    with LOCK:
        if job['status'] != 'cancelled':
            job.update(status='done', payload=payload)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, payload, content_type='application/json; charset=utf-8'):
        data = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, (dict, list)) else payload
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (not origin or origin in {f'http://{host}' for host in hosts})

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': '로컬 동일 출처 요청만 허용됩니다.'})
        path = urlparse(self.path).path
        if path == '/api/bootstrap':
            return self.send(200, {'token': TOKEN, 'source': (ROOT / 'examples/demo.py').read_text(), 'version': '1.0.0'})
        if path.startswith('/api/jobs/'):
            if self.headers.get('X-Factory-Token') != TOKEN:
                return self.send(403, {'error': '잘못된 실행 토큰'})
            with LOCK:
                job = JOBS.get(path.rsplit('/', 1)[1])
                result = {k: v for k, v in job.items() if k in ('status', 'payload')} if job else None
            return self.send(200 if result else 404, result or {'error': '실행을 찾을 수 없습니다.'})
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
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
            return self.send(403, {'error': '로컬 동일 출처 요청과 실행 토큰이 필요합니다.'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 1_000_000:
                return self.send(413, {'error': '요청 크기 한도는 1 MB입니다.'})
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
                        return self.send(409, {'error': '다른 실행이 진행 중입니다. 완료 또는 중지 후 실행하세요.'})
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
            return self.send(400, {'error': str(e)})
        except Exception as e:
            return self.send(500, {'error': f'서버 오류: {e}'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Factory Studio → http://127.0.0.1:{server.server_port}', flush=True)
    print('로컬 Python 코드를 현재 사용자 권한으로 실행합니다. 신뢰하는 프로젝트를 여세요.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for job in JOBS.values():
            stop_process(job['process'])
        server.server_close()
