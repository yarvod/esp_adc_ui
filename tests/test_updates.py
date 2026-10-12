import hashlib
import io
import json
import struct
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import updater
from application import updates
from tools.build_windows import make_package


def pe_bytes(arch="x64"):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, {"x64": 0x8664, "x86": 0x014C}[arch])
    return bytes(data)


def write_package(directory, version="v1.1.0", arch="x64", extra=None):
    directory.mkdir(parents=True, exist_ok=True)
    contents = {"EspAdc.exe": pe_bytes(arch), "updater.exe": pe_bytes(arch), "_internal/runtime.dll": b"new"}
    contents.update(extra or {})
    for name, data in contents.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {"schema": 1, "version": version, "arch": arch, "files": sorted([*contents, "version.json"])}
    (directory / "version.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ReleaseTests(unittest.TestCase):
    def test_version_order_is_numeric_and_stable_follows_prerelease(self):
        tags = ["v1.2.3-alpha.1", "v1.2.3-beta.2", "v1.2.3-beta.10", "v1.2.3-rc.1", "v1.2.3", "v1.10.0", "v2.0.0"]
        self.assertEqual(sorted(reversed(tags), key=updates.version_key), tags)
        for tag in ("", "1.0.0", "v01.0.0", "v1.0.0-beta", "v1.0.0-beta.0", "v1.0.0\n", "v1.0.0+build"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                updates.version_key(tag)

    def test_architecture_uses_process_pointer_width(self):
        with patch("application.updates.struct.calcsize", return_value=4):
            self.assertEqual(updates.current_arch(), "x86")
        with patch("application.updates.struct.calcsize", return_value=8):
            self.assertEqual(updates.current_arch(), "x64")

    def test_listing_paginates_filters_drafts_and_selects_exact_arch_asset(self):
        def release(tag, draft=False, prerelease=False):
            return {
                "tag_name": tag,
                "draft": draft,
                "prerelease": prerelease,
                "assets": [
                    {
                        "name": updates.asset_name(tag, arch) + suffix,
                        "browser_download_url": f"https://github.com/test/{arch}{suffix}",
                    }
                    for arch in ("x64", "x86")
                    for suffix in ("", ".sha256")
                ],
            }

        pages = [
            [release("v1.2.0")] * 98 + [release("v1.20.0", draft=True), release("v1.3.0-beta.1", prerelease=True)],
            [release("v1.10.0")],
        ]
        calls = []

        def fetch(url):
            calls.append(url)
            return io.BytesIO(json.dumps(pages[len(calls) - 1]).encode())

        with patch("application.updates._open", side_effect=fetch):
            releases = updates.list_releases("x86")
        self.assertEqual(releases[0].tag, "v1.10.0")
        self.assertNotIn("v1.20.0", [item.tag for item in releases])
        self.assertEqual(releases[0].download_url, "https://github.com/test/x86")
        self.assertTrue(releases[0].installable)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any(item.prerelease for item in releases))

    def test_legacy_and_missing_checksum_releases_remain_visible(self):
        data = [
            {"tag_name": "10", "assets": []},
            {
                "tag_name": "v1.2.0",
                "assets": [
                    {
                        "name": "EspAdc_Windows_x64_v1.2.0.zip",
                        "browser_download_url": "https://github.com/test/package",
                    }
                ],
            },
        ]
        with patch("application.updates._open", return_value=io.BytesIO(json.dumps(data).encode())):
            releases = updates.list_releases("x64")
        self.assertEqual([r.tag for r in releases], ["v1.2.0", "10"])
        self.assertTrue(all(not r.installable for r in releases))

    def test_api_errors_are_visible_and_invalid_urls_are_rejected(self):
        with patch("application.updates._open", return_value=io.BytesIO(b'{"message":"bad"}')):
            with self.assertRaises(updates.UpdateError):
                updates.list_releases()
        for url in ("http://github.com/file", "https://evil.example/file", "file:///tmp/file"):
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                updates._check_url(url)


class PackageTests(unittest.TestCase):
    def package_bytes(self, change=None, extra=None):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "EspAdc"
            write_package(source)
            if change:
                change(source)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as package:
                for path in source.rglob("*"):
                    if path.is_file():
                        package.write(path, "EspAdc/" + path.relative_to(source).as_posix())
                for name, data in (extra or {}).items():
                    package.writestr(name, data)
            return stream.getvalue()

    def download(self, data, checksum=None, size=None, cancel=None):
        release = updates.Release(
            "v1.1.0",
            "",
            "",
            False,
            "https://github.com/test/release",
            "https://github.com/test/package",
            "https://github.com/test/checksum",
            len(data) if size is None else size,
        )
        digest = checksum or hashlib.sha256(data).hexdigest()

        def fetch(url):
            return io.BytesIO(
                (digest + "  EspAdc_Windows_x64_v1.1.0.zip\n").encode() if url == release.checksum_url else data
            )

        with patch("application.updates.current_arch", return_value="x64"), patch(
            "application.updates._open", side_effect=fetch
        ):
            return updates.prepare_update(release, cancel=cancel)

    def test_verified_package_is_extracted_and_owned_by_caller(self):
        prepared = self.download(self.package_bytes())
        try:
            self.assertEqual(updates.read_manifest(prepared.source)["version"], "v1.1.0")
            self.assertTrue((prepared.source / "updater.exe").exists())
        finally:
            prepared.cleanup()
        self.assertFalse(prepared.directory.exists())

    def test_failed_download_and_cancel_clean_the_temporary_directory(self):
        data = self.package_bytes()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "download"
            target.mkdir()
            with patch("application.updates.tempfile.mkdtemp", return_value=str(target)):
                with self.assertRaisesRegex(updates.UpdateError, "сумма"):
                    self.download(data, checksum="0" * 64)
            self.assertFalse(target.exists())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(updates.UpdateCancelled):
            self.download(self.package_bytes(), cancel=cancel)

    def test_truncated_download_is_rejected(self):
        data = self.package_bytes()
        with self.assertRaisesRegex(updates.UpdateError, "не полностью"):
            self.download(data, size=len(data) + 1)

    def test_wrong_manifest_version_and_arch_are_rejected(self):
        for changes in ({"version": "v9.0.0"}, {"arch": "x86"}, {"schema": 2}):

            def modify(source):
                path = source / "version.json"
                manifest = json.loads(path.read_text())
                manifest.update(changes)
                path.write_text(json.dumps(manifest))

            with self.subTest(changes=changes), self.assertRaises(updates.UpdateError):
                self.download(self.package_bytes(change=modify))

    def test_unsafe_and_extra_archive_paths_are_rejected(self):
        for name in (
            "EspAdc/../../escape.txt",
            "C:/escape",
            "EspAdc/settings.ini",
            "EspAdc/_internal/CON.txt",
            "EspAdc/_internal/x.dll:evil",
            "EspAdc/_internal\\evil.dll",
            "EspAdc/EspAdc.exe/evil",
        ):
            with self.subTest(name=name), self.assertRaises(updates.UpdateError):
                self.download(self.package_bytes(extra={name: b"bad"}))

    def test_missing_files_and_mislabeled_executable_are_rejected(self):
        for change in (
            lambda source: (source / "updater.exe").unlink(),
            lambda source: (source / "EspAdc.exe").write_bytes(pe_bytes("x86")),
        ):
            with self.subTest(change=change), self.assertRaises(updates.UpdateError):
                self.download(self.package_bytes(change=change))

    def test_build_package_round_trip_and_checksum(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "EspAdc"
            write_package(directory)
            archive = make_package(directory, "v1.1.0", "x64")
            checksum = archive.with_name(archive.name + ".sha256").read_text().split()[0]
            self.assertEqual(checksum, hashlib.sha256(archive.read_bytes()).hexdigest())
            prepared = self.download(archive.read_bytes())
            prepared.cleanup()
            with self.assertRaises(ValueError):
                make_package(directory, "v1.1.0", "x86")


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "source"
        self.install = root / "portable"
        write_package(self.source)
        write_package(self.install, "v1.0.0", extra={"_internal/obsolete.dll": b"old"})
        (self.install / "settings.ini").write_text("my settings")
        (self.install / "measure.txt").write_text("downloaded SD text")

    def test_swap_preserves_user_files_removes_stale_runtime_and_restarts(self):
        restarted = []
        updater.install_update(self.source, self.install, restarted.append)
        self.assertEqual(updates.read_manifest(self.install)["version"], "v1.1.0")
        self.assertEqual((self.install / "settings.ini").read_text(), "my settings")
        self.assertEqual((self.install / "measure.txt").read_text(), "downloaded SD text")
        self.assertFalse((self.install / "_internal/obsolete.dll").exists())
        self.assertEqual(restarted, [self.install])
        self.assertEqual(sorted(p.name for p in self.install.parent.iterdir()), ["portable", "source"])

    def test_copy_failure_preserves_old_installation(self):
        with patch("updater.shutil.copy2", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                updater.install_update(self.source, self.install, lambda path: None)
        self.assertEqual(updates.read_manifest(self.install)["version"], "v1.0.0")

    def test_restart_failure_rolls_back(self):
        def fail(path):
            raise OSError("cannot start")

        with self.assertRaises(OSError):
            updater.install_update(self.source, self.install, fail)
        self.assertEqual(updates.read_manifest(self.install)["version"], "v1.0.0")
        self.assertTrue((self.install / "_internal/obsolete.dll").exists())
        self.assertEqual((self.install / "settings.ini").read_text(), "my settings")

    def test_second_rename_failure_restores_original_directory(self):
        original = updater._rename_with_retry
        calls = []

        def rename(source, destination):
            calls.append((source, destination))
            if len(calls) == 2:
                raise PermissionError("locked")
            return original(source, destination)

        with patch("updater._rename_with_retry", side_effect=rename):
            with self.assertRaises(PermissionError):
                updater.install_update(self.source, self.install, lambda path: None)
        self.assertEqual(updates.read_manifest(self.install)["version"], "v1.0.0")

    def test_helper_is_copied_outside_installation(self):
        prepared = updates.PreparedUpdate(self.source.parent / "download", self.source)
        prepared.directory.mkdir()
        with patch("application.updates.subprocess.Popen") as launch:
            updates.launch_updater(prepared, self.install, 123)
        self.assertEqual(launch.call_args.args[0][0], str(prepared.directory / "updater.exe"))
        self.assertEqual(launch.call_args.kwargs["cwd"], str(prepared.directory))
        self.assertTrue((prepared.directory / "updater.exe").exists())

    def test_architecture_change_is_rejected(self):
        write_package(self.source, arch="x86")
        with self.assertRaises(updates.UpdateError):
            updater.install_update(self.source, self.install, lambda path: None)
        self.assertEqual(updates.read_manifest(self.install)["version"], "v1.0.0")


if __name__ == "__main__":
    unittest.main()
