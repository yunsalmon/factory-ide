"""Build trusted, precomputed assets. Never run against visitor-supplied source."""
import argparse
import hashlib
import gzip
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import execute
import simpy
from fetch_browser_runtime import fetch


def build(output, revision, runtime_dir=None):
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'web', output, dirs_exist_ok=True)
    runtime_dir = runtime_dir or ROOT / '.cache' / 'browser-runtime'
    runtime_manifest = fetch(runtime_dir)
    shutil.copytree(runtime_dir, output / 'vendor' / 'pyodide' / runtime_manifest['pyodide'], dirs_exist_ok=True)
    python_runtime = output / 'runtime'
    python_runtime.mkdir(exist_ok=True)
    for name in ('engine.py', 'model.py', 'messages.py', 'orders.py', 'disruptions.py', 'operation_metrics.py'):
        shutil.copy(ROOT / name, python_runtime / name)
    source = (ROOT / 'examples/public_demo.py').read_text()
    result = execute(source)
    source_sha256 = hashlib.sha256(source.encode()).hexdigest()
    result['execution'] = dict(
        kind='precomputed', source_sha256=source_sha256, source_revision=revision,
        runtime=dict(python=platform.python_version(), simpy=simpy.__version__),
    )
    trace = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    version = dict(source_revision=revision, trace_schema=result['schema_version'],
                   source_sha256=source_sha256,
                   trace_sha256=hashlib.sha256(trace.encode()).hexdigest(),
                   browser_runtime=runtime_manifest)
    (output / 'demo.json').write_text(json.dumps(dict(source=source, result=result, version=version), ensure_ascii=False))
    (output / 'version.json').write_text(json.dumps(version))
    (output / 'healthz').write_text('ok\n')
    (output / 'locales.js').write_text('const translations = ' + (ROOT / 'web/locales.json').read_text() + ';\n')
    html = (output / 'index.html').read_text().replace('<html lang="ko">', '<html lang="ko" data-mode="public">')
    for before, after in [('local','public_title'),('ui_211','public_runtime'),('ui_212','public_runtime_detail'),('ui_241','public_storage'),('live_trace','public_trace'),('ui_231','public_help'),('ui_12','public_source'),('ui_227','public_shortcut')]:
        html = html.replace(f'data-i18n="{before}"', f'data-i18n="{after}"')
    start, end = html.index('      <p>', html.index('<dialog')), html.index('    </dialog>')
    html = html[:start] + '<p data-i18n="public_scope"></p><p data-i18n="public_help"></p><a href="/install.html" data-i18n="public_install"></a>\n' + html[end:]
    banner = '<section class="public-notice"><b data-i18n="public_title"></b><p data-i18n="public_scope"></p><p data-i18n="public_limits"></p><a href="/install.html" data-i18n="public_install"></a><details><summary>Build / runtime / trace</summary><code id="demo-version"></code></details></section>'
    html = html.replace('<main>', '<main>' + banner)
    (output / 'index.html').write_text(html)
    with (output / 'style.css').open('a') as f:
        f.write('\nhtml[data-mode="public"] :is(#add-process,#add-machine,#add-route,#settings-button){display:none!important}\nhtml:not([data-mode="public"]) #reset-button{display:none!important}.public-notice{padding:12px 18px;border:1px solid #618572;border-radius:8px;margin-bottom:16px}.public-notice p{margin:6px 0}.public-notice code{overflow-wrap:anywhere}\n')
    shutil.copy(ROOT / 'deploy/install.html', output)
    # Deterministic precompression avoids repeated CPU work in the static server.
    # Leave archives alone unless compression actually reduces transfer size.
    for asset in output.rglob('*'):
        if not asset.is_file() or asset.suffix == '.gz':
            continue
        compressed_path = asset.with_name(asset.name + '.gz')
        compressed_path.unlink(missing_ok=True)
        raw = asset.read_bytes()
        if len(raw) < 1024:
            continue
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        if len(compressed) < len(raw) * .95:
            compressed_path.write_bytes(compressed)
    return version

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--revision', default=None)
    parser.add_argument('--runtime-dir', type=Path)
    args = parser.parse_args()
    revision = args.revision or subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    print(json.dumps(build(args.output, revision, args.runtime_dir)))
