from functools import partial
import logging
import os
from typing import List

from PyQt5 import QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal

from api import EspAdc
from api.constants import SOCKET
from application.mixins.log_mixin import LogMixin
from store.state import State

logger = logging.getLogger(__name__)


class DeleteThread(QThread):
    log = pyqtSignal(dict)

    def __init__(self, file: str, parent):
        self.file = file
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                if not self.file.startswith("/"):
                    self.file = "/" + self.file
                response = daq.delete_file(self.file)
                log_type = "error" if "Error" in response else "info"
                self.log.emit({"type": log_type, "msg": response})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class DownloadThread(QThread):
    log = pyqtSignal(dict)
    progress = pyqtSignal(int, int)  # downloaded, total

    def __init__(self, file: str, target_dir: str, parent):
        self.file = file
        self.target_dir = target_dir
        super().__init__(parent)

    def run(self):
        try:
            assert State.adapter == SOCKET, "Download use only Socket"
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                target_path = os.path.join(self.target_dir, os.path.basename(self.file))
                self.log.emit({"type": "info", "msg": f"Downloading to {target_path}"})
                ok, response = daq.download_file(
                    self.file,
                    on_progress=self._emit_progress,
                    chunk_size=128 * 1024,
                    dest_path=target_path,
                )
                log_type = "error" if not ok else "info"
                self.log.emit({"type": log_type, "msg": response})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()

    def _emit_progress(self, downloaded: int, total: int):
        self.progress.emit(downloaded, total)


class GetFilesThread(QThread):
    log = pyqtSignal(dict)
    files = pyqtSignal(list)

    def __init__(self, parent):
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                response = daq.get_files()
                log_type = "error" if "Error" in response else "info"
                self.log.emit({"type": log_type, "msg": response})
                self.files.emit(response)
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class SdData(QtWidgets.QWidget, LogMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.logger = logger
        layout = QtWidgets.QVBoxLayout()

        hlayout_buttons = QtWidgets.QHBoxLayout()

        self.scroll_files = QtWidgets.QScrollArea(self)
        self.scroll_files.setWidgetResizable(True)
        self.scroll_files_content = QtWidgets.QWidget()
        self.glayout_files = QtWidgets.QGridLayout(self.scroll_files_content)
        self.scroll_files.setWidget(self.scroll_files_content)

        self.btn_get_files = QtWidgets.QPushButton("Get files list", self)
        self.btn_get_files.clicked.connect(self.get_files)

        hlayout_buttons.addWidget(self.btn_get_files)

        layout.addLayout(hlayout_buttons)
        layout.addWidget(self.scroll_files)

        self.setLayout(layout)

    def get_files(self):
        self.thread_get_files = GetFilesThread(
            parent=self,
        )
        self.thread_get_files.finished.connect(lambda: self.btn_get_files.setEnabled(True))
        self.thread_get_files.log.connect(self.set_log)
        self.thread_get_files.files.connect(self.set_files_list)
        self.thread_get_files.start()
        self.btn_get_files.setEnabled(False)

    def set_files_list(self, files: List[dict]):
        # Очистка макета перед добавлением новых элементов
        while self.glayout_files.count():
            child = self.glayout_files.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        skip_prefixes = ("SYSTEM~", "FSEVE~", "SPOTL~", "TRASH~")
        for i, file_info in enumerate(files):
            file = file_info.get("name") if isinstance(file_info, dict) else file_info
            size = file_info.get("size", -1) if isinstance(file_info, dict) else -1
            if (
                not file
                or file.startswith(".")
                or file == "System Volume Information"
                or file.startswith(skip_prefixes)
            ):
                continue
            btn_download = QtWidgets.QPushButton("Download", self)
            btn_download.clicked.connect(partial(self.download_file, file, i))
            setattr(self, f"btn_download_{i}", btn_download)
            btn_delete = QtWidgets.QPushButton("Delete", self)
            btn_delete.clicked.connect(partial(self.delete_file, file, i))
            setattr(self, f"btn_delete_{i}", btn_delete)
            size_mb = f"{size / (1024 * 1024):.2f} MB" if size and size > 0 else "N/A"
            self.glayout_files.addWidget(QtWidgets.QLabel(f"{file} ({size_mb})", self), i, 0)
            self.glayout_files.addWidget(btn_download, i, 1)
            self.glayout_files.addWidget(btn_delete, i, 2)

    def download_file(self, file: str, ind: int):
        target_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder to save")
        if not target_dir:
            return
        self.thread_download = DownloadThread(parent=self, file=file, target_dir=target_dir)
        btn_download = getattr(self, f"btn_download_{ind}")
        progress_dialog = QtWidgets.QProgressDialog(f"Downloading {file}...", "Cancel", 0, 100, self)
        progress_dialog.setWindowTitle("Download")
        progress_dialog.setAutoClose(True)
        progress_dialog.setAutoReset(True)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)

        def on_progress(downloaded: int, total: int):
            if total <= 0:
                progress_dialog.setLabelText(f"Downloading {file}: {downloaded} bytes")
                return
            percent = int(downloaded * 100 / total)
            mb_done = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            progress_dialog.setValue(percent)
            progress_dialog.setLabelText(f"{file}: {percent}% ({mb_done:.2f}/{mb_total:.2f} MB)")
            if progress_dialog.wasCanceled():
                self.thread_download.terminate()
                progress_dialog.close()

        self.thread_download.progress.connect(on_progress)
        self.thread_download.finished.connect(progress_dialog.close)
        self.thread_download.finished.connect(lambda: btn_download.setEnabled(True))
        self.thread_download.log.connect(self.set_log)
        self.thread_download.start()
        btn_download.setEnabled(False)

    def delete_file(self, file: str, ind: int):
        dlg = QtWidgets.QMessageBox(self)
        dlg.setWindowTitle("Deleting file")
        dlg.setText(f"Аre you sure you want to delete file {file}")
        dlg.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        dlg.setIcon(QtWidgets.QMessageBox.Icon.Question)
        button = dlg.exec()

        if button == QtWidgets.QMessageBox.StandardButton.Yes:
            self.thread_delete = DeleteThread(parent=self, file=file)
            btn_delete = getattr(self, f"btn_delete_{ind}")
            self.thread_delete.finished.connect(lambda: btn_delete.setEnabled(True))
            self.thread_delete.finished.connect(self.get_files)
            self.thread_delete.log.connect(self.set_log)
            self.thread_delete.start()
            btn_delete.setEnabled(False)
        else:
            return
