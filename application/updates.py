"""GitHub Releases and verified portable Windows update packages (no Qt dependency)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from application.version import REPO_SLUG

TAG_PATTERN = r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.([1-9]\d*))?"
MAX_ARCHIVE_SIZE = 1024 * 1024 * 1024
MAX_UNPACKED_SIZE = 2 * MAX_ARCHIVE_SIZE


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


def version_key(tag):
    match = re.fullmatch(TAG_PATTERN, tag)
    if not match:
        raise ValueError(f"Версия должна иметь вид v1.2.3 или v1.2.3-beta.1: {tag}")
    major, minor, patch, channel, number = match.groups()
    return int(major), int(minor), int(patch), {"alpha": 0, "beta": 1, "rc": 2, None: 3}[channel], int(number or 0)


def current_arch():
    # A 32-bit process on 64-bit Windows must receive the x86 package.
    return "x64" if struct.calcsize("P") == 8 else "x86"


def executable_arch(path):
    try:
        with Path(path).open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError()
            stream.seek(0x3C)
            offset = struct.unpack("<I", stream.read(4))[0]
            stream.seek(offset)
            if stream.read(4) != b"PE\0\0":
                raise ValueError()
            machine = struct.unpack("<H", stream.read(2))[0]
            return {0x014C: "x86", 0x8664: "x64"}[machine]
    except (OSError, ValueError, KeyError, struct.error) as error:
        raise UpdateError(f"Некорректный Windows EXE: {Path(path).name}") from error


def asset_name(tag, arch):
    return f"EspAdc_Windows_{arch}_{tag}.zip"


def _check_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "api.github.com"}:
        raise UpdateError("Некорректная ссылка на GitHub.")


def _open(url):
    _check_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/octet-stream",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "EspAdc-Updater",
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise UpdateError("GitHub ограничил число запросов или доступ к релизам. Повторите позже.") from error
        if error.code == 404:
            raise UpdateError("Релиз или файл не найден. Для обновления нужен публичный GitHub Releases.") from error
        raise UpdateError(f"GitHub вернул HTTP {error.code}.") from error
    except (OSError, urllib.error.URLError) as error:
        raise UpdateError(f"Не удалось подключиться к GitHub: {error}") from error


@dataclass(frozen=True)
class Release:
    tag: str
    body: str
    published_at: str
    prerelease: bool
    page_url: str
    download_url: str = ""
    checksum_url: str = ""
    size: int = 0

    @property
    def installable(self):
        return bool(self.download_url and self.checksum_url)


def list_releases(arch=None):
    arch = arch or current_arch()
    releases = []
    # Include older versions, drafts excluded; prereleases remain selectable.
    for page in range(1, 11):
        url = f"https://api.github.com/repos/{REPO_SLUG}/releases?per_page=100&page={page}"
        try:
            with _open(url) as response:
                data = json.loads(response.read(8 * 1024 * 1024))
        except (ValueError, OSError) as error:
            raise UpdateError("Не удалось прочитать список релизов GitHub.") from error
        if not isinstance(data, list):
            raise UpdateError("GitHub вернул некорректный список релизов.")
        for item in data:
            if item.get("draft") or not item.get("tag_name"):
                continue
            tag = item["tag_name"]
            name = asset_name(tag, arch)
            assets = {asset["name"]: asset for asset in item.get("assets", [])}
            asset = assets.get(name, {})
            checksum = assets.get(name + ".sha256", {})
            releases.append(
                Release(
                    tag,
                    item.get("body") or "Описание отсутствует.",
                    item.get("published_at") or "",
                    bool(item.get("prerelease")),
                    item.get("html_url") or f"https://github.com/{REPO_SLUG}/releases",
                    asset.get("browser_download_url", ""),
                    checksum.get("browser_download_url", ""),
                    asset.get("size", 0),
                )
            )
        if len(data) < 100:
            break

    # Semver versions first. Legacy tags are still visible for manual download.
    def order(release):
        try:
            return (1, version_key(release.tag))
        except ValueError:
            return (0, (release.published_at,))

    return sorted(releases, key=order, reverse=True)


def safe_relative_path(name):
    """Restrict ZIP/manifest paths to portable runtime files, including on Windows."""
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or "\\" in name
        or ":" in name
        or any(
            part in {".", ".."}
            or part.endswith((".", " "))
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
            for part in name.split("/")
        )
    ):
        raise UpdateError(f"Недопустимый путь в пакете: {name}")
    return path


def runtime_path(name):
    path = safe_relative_path(name)
    if path.parts[0] not in {"EspAdc.exe", "updater.exe", "version.json", "_internal", "assets"}:
        raise UpdateError(f"Пакет содержит посторонний файл: {name}")
    if path.parts[0] not in {"_internal", "assets"} and len(path.parts) != 1:
        raise UpdateError(f"Недопустимый путь файла программы: {name}")
    return path


def read_manifest(directory, tag=None, arch=None):
    try:
        manifest = json.loads((Path(directory) / "version.json").read_text(encoding="utf-8"))
        version_key(manifest["version"])
        files = manifest["files"]
        if manifest["schema"] != 1 or manifest["arch"] not in {"x86", "x64"} or not isinstance(files, list):
            raise ValueError()
        if tag is not None and manifest["version"] != tag:
            raise ValueError()
        if arch is not None and manifest["arch"] != arch:
            raise ValueError()
        if not all(isinstance(name, str) for name in files) or len(set(name.casefold() for name in files)) != len(
            files
        ):
            raise ValueError()
        for name in files:
            runtime_path(name)
        if not {"EspAdc.exe", "updater.exe", "version.json"}.issubset(files):
            raise ValueError()
        return manifest
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise UpdateError("Некорректный манифест версии или разрядности пакета.") from error


@dataclass
class PreparedUpdate:
    directory: Path
    source: Path

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def prepare_update(release, progress=lambda done, total: None, cancel=None):
    cancel = cancel or threading.Event()
    if not release.installable:
        raise UpdateError("Для этой версии нет проверяемой сборки вашей разрядности.")
    if release.size > MAX_ARCHIVE_SIZE:
        raise UpdateError("Архив обновления слишком большой.")
    directory = Path(tempfile.mkdtemp(prefix="EspAdc-update-"))
    try:
        with _open(release.checksum_url) as response:
            checksum_text = response.read(4096).decode("ascii").strip()
        fields = checksum_text.split()
        if (
            len(fields) != 2
            or not re.fullmatch("[0-9a-fA-F]{64}", fields[0])
            or fields[1] != asset_name(release.tag, current_arch())
        ):
            raise UpdateError("Некорректная контрольная сумма релиза.")
        archive = directory / "package.zip"
        digest = hashlib.sha256()
        downloaded = 0
        with _open(release.download_url) as response, archive.open("wb") as output:
            while True:
                if cancel.is_set():
                    raise UpdateCancelled("Загрузка отменена.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_ARCHIVE_SIZE:
                    raise UpdateError("Архив обновления слишком большой.")
                output.write(chunk)
                digest.update(chunk)
                progress(downloaded, release.size)
        if release.size and downloaded != release.size:
            raise UpdateError("Архив скачан не полностью.")
        if digest.hexdigest() != fields[0].lower():
            raise UpdateError("Контрольная сумма не совпала. Повторите загрузку.")
        unpack = directory / "unpacked"
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if len(members) > 30000 or sum(info.file_size for info in members) > MAX_UNPACKED_SIZE:
                raise UpdateError("Распакованный архив слишком большой.")
            seen = set()
            for info in members:
                path = safe_relative_path(info.filename.rstrip("/"))
                if path.parts[0] != "EspAdc" or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise UpdateError("Некорректная структура архива обновления.")
                if not info.is_dir():
                    runtime_path(str(PurePosixPath(*path.parts[1:])))
                if str(path).casefold() in seen:
                    raise UpdateError("Архив содержит повторяющиеся пути.")
                seen.add(str(path).casefold())
            for info in members:
                if cancel.is_set():
                    raise UpdateCancelled("Загрузка отменена.")
                package.extract(info, unpack)
        source = unpack / "EspAdc"
        manifest = read_manifest(source, release.tag, current_arch())
        actual = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
        if actual != set(manifest["files"]):
            raise UpdateError("Состав пакета не соответствует манифесту.")
        for name in ("EspAdc.exe", "updater.exe"):
            if executable_arch(source / name) != current_arch():
                raise UpdateError("EXE в пакете имеет неверную разрядность.")
        return PreparedUpdate(directory, source)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def launch_updater(prepared, install_dir, pid):
    # Run a standalone helper outside the installation so its executable can be replaced.
    helper = prepared.directory / "updater.exe"
    installed_helper = Path(install_dir) / "updater.exe"
    if not installed_helper.is_file():
        raise UpdateError("Не найден updater.exe. Распакуйте полный дистрибутив приложения.")
    shutil.copy2(installed_helper, helper)
    return subprocess.Popen(
        [
            str(helper),
            "--pid",
            str(pid),
            "--src",
            str(prepared.source),
            "--dst",
            str(install_dir),
            "--cleanup",
            str(prepared.directory),
        ],
        cwd=str(prepared.directory),
    )
