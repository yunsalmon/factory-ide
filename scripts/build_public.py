"""Build trusted, precomputed assets. Never run against visitor-supplied source."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import execute


def build(output, revision):
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'web', output, dirs_exist_ok=True)
    source = (ROOT / 'examples/public_demo.py').read_text()
    result = execute(source)
    trace = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    version = dict(source_revision=revision, trace_schema=result['schema_version'],
                   source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                   trace_sha256=hashlib.sha256(trace.encode()).hexdigest())
    (output / 'demo.json').write_text(json.dumps(dict(source=source, result=result, version=version), ensure_ascii=False))
    (output / 'version.json').write_text(json.dumps(version))
    (output / 'healthz').write_text('ok\n')
    (output / 'locales.js').write_text('const translations = ' + (ROOT / 'web/locales.json').read_text() + ';\n')
    html = (output / 'index.html').read_text().replace('<html lang="ko">', '<html lang="ko" data-mode="public">')
    for before, after in [('local','public_title'),('ui_211','public_title'),('ui_212','public_trace'),('ui_241','public_scope'),('live_trace','public_trace'),('ui_231','public_help'),('ui_12','public_source'),('ui_227','public_source')]:
        html = html.replace(f'data-i18n="{before}"', f'data-i18n="{after}"')
    start, end = html.index('      <p>', html.index('<dialog')), html.index('    </dialog>')
    html = html[:start] + '<p data-i18n="public_scope"></p><p data-i18n="public_help"></p><a href="/install.html" data-i18n="public_install"></a>\n' + html[end:]
    banner = '<section class="public-notice"><b data-i18n="public_title"></b><p data-i18n="public_scope"></p><a href="/install.html" data-i18n="public_install"></a><details><summary>Build / trace</summary><code id="demo-version"></code></details></section>'
    html = html.replace('<main>', '<main>' + banner)
    (output / 'index.html').write_text(html)
    with (output / 'style.css').open('a') as f:
        f.write('\nhtml[data-mode="public"] :is(#run-button,#import-button,#apply-code,#add-process,#add-machine,#add-route,#settings-button){display:none!important}\n.public-notice{padding:12px 18px;border:1px solid #618572;border-radius:8px;margin-bottom:16px}.public-notice p{margin:6px 0}.public-notice code{overflow-wrap:anywhere}html[data-mode="public"] .statusbar #runtime-status{display:none}\n')
    shutil.copy(ROOT / 'deploy/install.html', output)
    return version

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--revision', default=None)
    args = parser.parse_args()
    revision = args.revision or subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    print(json.dumps(build(args.output, revision)))
