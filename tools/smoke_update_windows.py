"""Exercise the actual frozen Windows updater in a disposable portable install."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from application.updates import current_arch, read_manifest


def main():
    if sys.platform != "win32":
        raise RuntimeError("Windows-only smoke test")
    distribution = ROOT / "dist" / "EspAdc"
    version = read_manifest(distribution)["version"]
    with tempfile.TemporaryDirectory(prefix="EspAdc-update-smoke-") as folder:
        root = Path(folder)
        source = root / "unpacked" / "EspAdc"
        install = root / "portable app"
        shutil.copytree(distribution, source)
        shutil.copytree(distribution, install)
        marker = install / "_internal" / "obsolete-test.txt"
        marker.write_text("old runtime")
        manifest_path = install / "version.json"
        old_manifest = read_manifest(install)
        old_manifest["version"] = "v0.9.0"
        old_manifest["files"].append("_internal/obsolete-test.txt")
        manifest_path.write_text(json.dumps(old_manifest), encoding="utf-8")
        settings = install / "settings.ini"
        settings.write_text("[UpdateSmoke]\nmarker=keep-me\n", encoding="utf-8")
        user_file = install / "downloaded-sd.txt"
        user_file.write_text("user measurement")
        helper = root / "updater.exe"
        shutil.copy2(distribution / "updater.exe", helper)
        env = dict(
            os.environ, ESP_ADC_SMOKE_TEST="1", ESP_ADC_EXPECTED_VERSION=version, ESP_ADC_EXPECTED_ARCH=current_arch()
        )
        # The updater must wait for a live process instead of terminating it.
        with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"]) as parent:
            process = subprocess.Popen(
                [
                    str(helper),
                    "--pid",
                    str(parent.pid),
                    "--src",
                    str(source),
                    "--dst",
                    str(install),
                    "--cleanup",
                    str(root),
                ],
                env=env,
                cwd=root,
            )
            try:
                if process.wait(timeout=90) != 0:
                    raise RuntimeError("Frozen updater failed; see %TEMP%/EspAdc-updater.log")
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
            assert parent.wait(timeout=5) == 0
        assert read_manifest(install)["version"] == version
        assert "keep-me" in settings.read_text()
        assert user_file.read_text() == "user measurement"
        assert not marker.exists()
        # Verify the replaced runtime itself can start, including paths with spaces.
        process = subprocess.Popen([str(install / "EspAdc.exe")], env=env, cwd=root)
        try:
            assert process.wait(timeout=60) == 0
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        # The automatically restarted instance exits after its own smoke check.
        # Wait for file locks to clear before TemporaryDirectory removes the install.
        import time

        time.sleep(2)
    print("Frozen updater: process wait, replacement, settings, cleanup and restarted runtime OK")


if __name__ == "__main__":
    main()
