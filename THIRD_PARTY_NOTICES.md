# Third-party software

## SimPy 4.1.1

Runtime dependency installed through `requirements.txt` and bundled as a checksum-pinned wheel in the public build. MIT license.

- https://simpy.readthedocs.io/
- https://gitlab.com/team-simpy/simpy
- Installed package contains its copyright and license notice.

## Pyodide 0.27.7

The public build bundles Pyodide's JavaScript/WebAssembly CPython distribution under
the Mozilla Public License 2.0. The verified payload includes `PYODIDE-LICENSE`.

- https://pyodide.org/
- https://github.com/pyodide/pyodide/tree/0.27.7

## CodeMirror 5.65.16

The JavaScript editor, Python mode, and edit/search/hint/dialog add-ons are bundled
in `web/vendor/codemirror/` so the application does not require a CDN at runtime.
Distributed under the MIT license; the original license is included at
`web/vendor/codemirror/LICENSE`. One trailing whitespace in the upstream CSS was removed; editor behavior is unchanged.

- https://github.com/codemirror/codemirror5
- Distribution source: https://cdn.jsdelivr.net/npm/codemirror@5.65.16/

## Playwright for Python 1.62.0

Optional test dependency, Apache License 2.0.

- https://github.com/microsoft/playwright-python

Jaspera and FactorySimPy are design references, not bundled dependencies.
