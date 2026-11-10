import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def venv_python(folder):
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare_environment(name):
    folder = ROOT / f".venv-{name}"
    python = venv_python(folder)
    requirements = ROOT / f"requirements-{name}.txt"
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(folder)], check=True)

    stamp = folder / "requirements.sha256"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not stamp.is_file() or stamp.read_text(encoding="ascii").strip().lower() != fingerprint:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        stamp.write_text(fingerprint, encoding="ascii")
    return python


def parse_args():
    parser = argparse.ArgumentParser(description="Run the FSL-V2 webcam app.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to bind (default: 127.0.0.1; use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, default=5000, help="TCP port (default: 5000)")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def main():
    parse_args()
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        print("Run this launcher with 64-bit CPython 3.12.", file=sys.stderr)
        return 1
    try:
        python = prepare_environment("browser")
        prepare_environment("letters")
        return subprocess.run([str(python), str(ROOT / "combined_app.py"), *sys.argv[1:]], cwd=ROOT).returncode
    except subprocess.CalledProcessError as error:
        print(f"FSL-V2 setup failed (exit code {error.returncode}). See the output above.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
