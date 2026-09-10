"""Run trusted local Python in a disposable, resource-bounded process."""
import contextlib
import io
import json
import sys
import traceback

try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
except (ImportError, ValueError):
    pass

from engine import execute


class LimitedLog(io.StringIO):
    def write(self, value):
        remaining = 20000 - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        return len(value)


log = LimitedLog()
try:
    source = json.load(sys.stdin)['source']
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        result = execute(source)
    result['console'] = log.getvalue()
    response = dict(ok=True, result=result)
except BaseException as e:
    response = dict(ok=False, error=str(e) or type(e).__name__, error_message=getattr(e, 'message', None), traceback=traceback.format_exc(), console=log.getvalue())
sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False))
