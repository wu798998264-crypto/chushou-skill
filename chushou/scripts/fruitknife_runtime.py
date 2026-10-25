#!/usr/bin/env python3
"""Install/check the portable short-drama runtime inside this skill only."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import uuid


SKILL = Path(__file__).resolve().parent.parent
REQUIREMENTS = SKILL / "references" / "fruitknife-requirements.txt"
TAG = "-".join((sys.implementation.cache_tag or "python", sys.platform, platform.machine().lower()))
PACKAGES = SKILL / "runtime" / "fruitknife" / TAG / "packages"
_READY = None


def activate_packages():
    if PACKAGES.is_dir() and str(PACKAGES) not in sys.path:
        sys.path.insert(0, str(PACKAGES))


def probe(packages):
    # A fresh -I interpreter cannot borrow the author's PYTHONPATH or user site.
    program = '''
import json, os, pathlib, subprocess, sys
packages, vendor = sys.argv[1:3]
sys.path[:0] = [packages, vendor]
import requests, betterproto, gmssl, imageio_ffmpeg
from Crypto.Cipher import AES
from PIL import Image
from flurl.core import core_sixgod
modules = [requests, betterproto, gmssl, imageio_ffmpeg, AES, Image]
root = pathlib.Path(packages).resolve()
for module in modules:
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root), 'Dependency outside the skill runtime'
os.environ.pop('IMAGEIO_FFMPEG_EXE', None)
binary = imageio_ffmpeg.get_ffmpeg_exe()
assert pathlib.Path(binary).resolve().is_relative_to(root), 'Bundled FFmpeg unavailable for this platform'
result = subprocess.run([binary, '-version'], capture_output=True, text=True, timeout=20)
assert result.returncode == 0, 'Bundled FFmpeg failed to run'
print(json.dumps({'status':'ready','python':sys.version.split()[0], 'packages':str(root),
                  'ffmpeg':binary,'ffmpeg_version':result.stdout.splitlines()[0]}))
'''
    completed = subprocess.run([sys.executable, "-I", "-c", program, str(packages),
                                str(SKILL / "scripts" / "fruitknife_vendor")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    if completed.returncode:
        raise ValueError("Short-drama runtime check failed: " + (completed.stderr.strip().splitlines() or ["unknown error"])[-1])
    return json.loads(completed.stdout)


def ensure_runtime(install=True):
    global _READY
    if _READY:
        activate_packages()
        return _READY
    if sys.version_info < (3, 11):
        raise ValueError("Short-drama preparation requires Python 3.11+ with pip")
    expected = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    receipt = PACKAGES.parent / "ready.json"
    try:
        if json.loads(receipt.read_text(encoding="utf-8"))["requirements_sha256"] == expected:
            _READY = probe(PACKAGES)
            activate_packages()
            return _READY
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass
    if not install:
        raise ValueError("Runtime needs setup; run python scripts/fruitknife_runtime.py --install")
    print("Preparing the skill's isolated short-drama dependencies and FFmpeg...", file=sys.stderr)
    PACKAGES.parent.mkdir(parents=True, exist_ok=True)
    lock = PACKAGES.parent / ".installing"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("Short-drama setup is already running; retry after it finishes") from exc
    try:
        with tempfile.TemporaryDirectory(prefix="install-", dir=PACKAGES.parent) as temporary:
            staging = Path(temporary).resolve()
            if not staging.is_relative_to(PACKAGES.parent.resolve()):
                raise ValueError("Runtime staging path escaped its skill directory")
            command = [sys.executable, "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                       "--no-input", "--index-url", "https://pypi.org/simple",
                       "--only-binary=imageio-ffmpeg,pycryptodome,pillow", "--target", str(staging / "packages"),
                       "-r", str(REQUIREMENTS)]
            result = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, timeout=600, shell=False)
            if result.returncode:
                raise ValueError("Dependency installation failed; Python needs pip and access to PyPI. Retry the same setup command")
            probe(staging / "packages")
            if PACKAGES.exists():
                PACKAGES.rename(PACKAGES.with_name("packages-before-" + uuid.uuid4().hex[:8]))
            (staging / "packages").rename(PACKAGES)
        _READY = probe(PACKAGES)
        receipt.write_text(json.dumps({**_READY, "requirements_sha256": expected}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        lock.rmdir()
    activate_packages()
    return _READY


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check without installing or contacting the provider")
    mode.add_argument("--install", action="store_true", help="Prepare the runtime, including bundled FFmpeg")
    args = parser.parse_args()
    try:
        print(json.dumps(ensure_runtime(install=not args.check), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "setup_failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
