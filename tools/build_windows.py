"""Build a portable application and standalone helper with the active Python."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from application.updates import asset_name, current_arch, executable_arch, version_key
from application.version import REPO_SLUG, __version__


def make_package(directory, tag, arch):
    for name in ("EspAdc.exe", "updater.exe"):
        if executable_arch(directory / name) != arch:
            raise ValueError(f"Wrong architecture: {name}")
    manifest_path = directory / "version.json"
    files = sorted(
        {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} | {"version.json"}
    )
    manifest_path.write_text(
        json.dumps({"schema": 1, "version": tag, "arch": arch, "files": files}, indent=2), encoding="utf-8"
    )
    archive = directory.parent / asset_name(tag, arch)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for name in files:
            package.write(directory / name, f"EspAdc/{name}")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    archive.with_name(archive.name + ".sha256").write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="ascii")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--arch", choices=["x86", "x64"], default=current_arch())
    args = parser.parse_args()
    version_key(args.version)
    if sys.platform != "win32":
        parser.error("Windows binaries must be built on Windows.")
    if args.arch != current_arch():
        parser.error(f"Use {args.arch} Python; the current interpreter is {current_arch()}.")
    version_file = ROOT / "application" / "version.py"
    original = version_file.read_bytes()
    try:
        version_file.write_text(
            f'"""Release identity stamped by the build."""\n\n__version__ = {args.version!r}\nREPO_SLUG = {REPO_SLUG!r}\n',
            encoding="utf-8",
        )
        # Avoid stale bytecode when version tags have the same length within one second.
        for cached in (ROOT / "application" / "__pycache__").glob("version.*.pyc"):
            cached.unlink()
        common = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--noupx", "--noconsole"]
        subprocess.run(
            common
            + [
                "--name",
                "updater",
                "--onefile",
                "--exclude-module",
                "PyQt5",
                "--exclude-module",
                "application.app",
                "--exclude-module",
                "numpy",
                "updater.py",
            ],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            common
            + [
                "--name",
                "EspAdc",
                "--onedir",
                "--icon",
                "assets/volt64.png",
                "--add-data",
                "assets;assets",
                "main.py",
            ],
            cwd=ROOT,
            check=True,
        )
        directory = ROOT / "dist" / "EspAdc"
        shutil.copy2(ROOT / "dist" / "updater.exe", directory / "updater.exe")
        shutil.copytree(ROOT / "assets", directory / "assets", dirs_exist_ok=True)
        print(f"Built {make_package(directory, args.version, args.arch)}")
    finally:
        version_file.write_bytes(original)


if __name__ == "__main__":
    main()
