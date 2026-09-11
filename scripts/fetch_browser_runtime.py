"""Fetch the exact browser Python runtime artifacts into a reusable build cache."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen


PYODIDE_VERSION = "0.27.7"
SIMPY_VERSION = "4.1.1"
PYODIDE_BASE = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
ARTIFACTS = {
    "PYODIDE-LICENSE": ("https://raw.githubusercontent.com/pyodide/pyodide/0.27.7/LICENSE", "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5"),
    "pyodide.js": (PYODIDE_BASE + "pyodide.js", "b4cb23a53aba19c221659b9fb40a2f18281d685691dc06647fb0afd0681baf98"),
    "pyodide.asm.js": (PYODIDE_BASE + "pyodide.asm.js", "6b4c90de5b7172873f04f21884d0e9d2274e305fe32116558fe3e4fbe3618d51"),
    "pyodide.asm.wasm": (PYODIDE_BASE + "pyodide.asm.wasm", "a50dd1843f805a0b7c45b61037ee0d7b26dfe85efe0e18ef95a34ad24e401f5f"),
    "python_stdlib.zip": (PYODIDE_BASE + "python_stdlib.zip", "16611534726e5d8ac2bd8f926410b2dcb8d6f49aa24913463533b457a2115c16"),
    "pyodide-lock.json": (PYODIDE_BASE + "pyodide-lock.json", "9c45b916001a750f4102fc287494f3eab215909c7535c626a20db80fd6333e2c"),
    "simpy-4.1.1-py3-none-any.whl": (
        "https://files.pythonhosted.org/packages/48/72/920ed1224c94a8a5a69e6c1275ac7fe4eb911ba8feffddf469f1629d47f3/simpy-4.1.1-py3-none-any.whl",
        "7c5ae380240fd2238671160e4830956f8055830a8317edf5c05e495b3823cd88",
    ),
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def fetch(output):
    output.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in ARTIFACTS.items():
        target = output / name
        if target.is_file() and digest(target) == expected:
            target.chmod(0o644)
            continue
        with urlopen(url, timeout=120) as response, tempfile.NamedTemporaryFile(dir=output, delete=False) as temporary:
            while block := response.read(1024 * 1024):
                temporary.write(block)
            downloaded = Path(temporary.name)
        actual = digest(downloaded)
        if actual != expected:
            downloaded.unlink(missing_ok=True)
            raise RuntimeError(f"Checksum mismatch for {name}: expected {expected}, received {actual}")
        downloaded.replace(target)
        target.chmod(0o644)
    manifest = {
        "pyodide": PYODIDE_VERSION,
        "python": "3.12.7",
        "simpy": SIMPY_VERSION,
        "artifacts": {name: sha for name, (_, sha) in ARTIFACTS.items()},
    }
    (output / "runtime-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".cache/browser-runtime"))
    args = parser.parse_args()
    print(json.dumps(fetch(args.output), sort_keys=True))
