"""Standalone Windows helper: wait, replace a portable installation, restart."""

import argparse
import ctypes
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from application.updates import UpdateError, executable_arch, read_manifest, runtime_path


def wait_for_process(pid, timeout=120):
    if sys.platform != "win32":
        raise UpdateError("Автоматическая установка поддерживается только в Windows.")
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE; never terminate the process
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: process already exited
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, timeout * 1000) != 0:
            raise UpdateError("Приложение не завершилось. Закройте ESP ADC и повторите обновление.")
    finally:
        kernel.CloseHandle(handle)


def _rename_with_retry(source, destination):
    for attempt in range(20):
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def install_update(source, destination, restart=None):
    """Prepare beside the installation, then swap with rollback on copy/swap/start failure."""
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.is_symlink() or not (destination / "EspAdc.exe").is_file():
        raise UpdateError("Не найден каталог установленного ESP ADC.")
    new_manifest = read_manifest(source)
    if not (source / "EspAdc.exe").is_file() or not (source / "updater.exe").is_file():
        raise UpdateError("Неполный пакет обновления.")
    if any(executable_arch(source / name) != new_manifest["arch"] for name in ("EspAdc.exe", "updater.exe")):
        raise UpdateError("Разрядность EXE отличается от манифеста.")
    if (destination / "version.json").exists():
        old_manifest = read_manifest(destination)
        if old_manifest["arch"] != new_manifest["arch"]:
            raise UpdateError("Разрядность обновления отличается от установленной программы.")
        old_files = old_manifest["files"]
    else:
        # First supported update from a portable build without a manifest.
        old_files = []
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-new-", dir=destination.parent))
    backup = Path(tempfile.mkdtemp(prefix=f".{destination.name}-backup-", dir=destination.parent))
    backup.rmdir()
    swapped = False
    try:
        # Preserve every user file, including settings.ini and thermostat exports.
        shutil.copytree(destination, stage, dirs_exist_ok=True, symlinks=True)
        for name in old_files:
            target = stage / str(runtime_path(name))
            for parent in (target, *target.parents):
                if parent == stage.parent:
                    break
                if parent.is_symlink():
                    raise UpdateError("В каталоге программы обнаружена ссылка вместо файла сборки.")
            if target.is_file():
                target.unlink()
        for name in new_manifest["files"]:
            relative = str(runtime_path(name))
            target = stage / relative
            for parent in (target, *target.parents):
                if parent == stage.parent:
                    break
                if parent.is_symlink():
                    raise UpdateError("В каталоге программы обнаружена ссылка вместо файла сборки.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        _rename_with_retry(destination, backup)
        try:
            _rename_with_retry(stage, destination)
        except Exception:
            _rename_with_retry(backup, destination)
            raise
        swapped = True
        if restart is None:
            subprocess.Popen([str(destination / "EspAdc.exe")], cwd=str(destination))
        else:
            restart(destination)
    except Exception:
        if swapped:
            # Keep the new files in stage while restoring the previous working installation.
            _rename_with_retry(destination, stage)
            _rename_with_retry(backup, destination)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="ESP ADC updater")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--dst", type=Path, required=True)
    parser.add_argument("--cleanup", type=Path, required=True)
    args = parser.parse_args()
    log = Path(tempfile.gettempdir()) / "EspAdc-updater.log"
    logging.basicConfig(
        filename=log, encoding="utf-8", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        wait_for_process(args.pid)
        install_update(args.src, args.dst)
        logging.info("Installed update in %s", args.dst)
        # The running onefile helper cannot remove itself on Windows; clean package data only.
        cleanup = args.cleanup.resolve()
        if cleanup.name.startswith("EspAdc-update-") and args.src.resolve() == cleanup / "unpacked" / "EspAdc":
            shutil.rmtree(cleanup / "unpacked", ignore_errors=True)
            (cleanup / "package.zip").unlink(missing_ok=True)
        return 0
    except Exception as error:
        logging.exception("Update failed")
        message = f"Не удалось обновить ESP ADC: {error}\n\nПредыдущая версия сохранена.\nЖурнал: {log}"
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, message, "Обновление ESP ADC", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
