#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || {
    echo 'Python venv가 필요합니다. Debian/Ubuntu: sudo apt install python3-venv' >&2
    exit 1
  }
fi
if ! .venv/bin/python -c 'import simpy; assert simpy.__version__ == "4.1.1"' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python server.py "$@"
