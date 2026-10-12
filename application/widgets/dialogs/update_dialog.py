import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from application import updates
from application.version import __version__

logger = logging.getLogger(__name__)


def can_install():
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


class UpdateWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int)

    def __init__(self, release=None, parent=None):
        super().__init__(parent)
        self.release = release
        self.cancel = threading.Event()
        self.result = None
        self.error = ""

    def run(self):
        try:
            if self.release is None:
                self.result = updates.list_releases()
            else:
                self.result = updates.prepare_update(self.release, self.progress.emit, self.cancel)
        except updates.UpdateCancelled:
            pass
        except Exception as error:
            self.error = str(error)


class UpdateDialog(QtWidgets.QDialog):
    prepared = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Версии и обновление ESP ADC")
        self.resize(700, 560)
        self.worker = None
        self.closing = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(f"Установлена: {__version__} · Windows {updates.current_arch()}"))
        self.list = QtWidgets.QListWidget()
        self.list.currentItemChanged.connect(self.selection_changed)
        layout.addWidget(self.list, 1)
        self.description = QtWidgets.QTextEdit()
        self.description.setReadOnly(True)
        layout.addWidget(self.description, 1)
        self.status = QtWidgets.QLabel("Загрузка списка версий…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        buttons = QtWidgets.QDialogButtonBox()
        self.refresh = buttons.addButton("Обновить список", QtWidgets.QDialogButtonBox.ActionRole)
        self.manual = buttons.addButton("Открыть релиз", QtWidgets.QDialogButtonBox.ActionRole)
        self.install = buttons.addButton("Установить выбранную версию", QtWidgets.QDialogButtonBox.ActionRole)
        self.cancel = buttons.addButton("Закрыть", QtWidgets.QDialogButtonBox.RejectRole)
        self.refresh.clicked.connect(self.load_releases)
        self.manual.clicked.connect(self.open_release)
        self.install.clicked.connect(self.install_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.selection_changed()

    def selected_release(self):
        item = self.list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else None

    def selection_changed(self, *args):
        release = self.selected_release()
        busy = self.worker is not None
        self.install.setEnabled(
            bool(release and release.installable and release.tag != __version__ and can_install() and not busy)
        )
        self.manual.setEnabled(bool(release) and not busy)
        self.refresh.setEnabled(not busy)
        self.list.setEnabled(not busy)
        self.description.setPlainText(release.body if release else "")
        if release and not busy:
            if not can_install():
                self.status.setText(
                    "Автоустановка доступна в собранном приложении для Windows. Релиз можно открыть в браузере."
                )
            elif release.tag == __version__:
                self.status.setText("Эта версия уже установлена.")
            elif not release.installable:
                self.status.setText("В релизе нет ZIP и SHA-256 для вашей разрядности. Доступно открытие релиза.")
            else:
                self.status.setText(
                    "Можно установить новую или предыдущую версию. Настройки и файлы измерений сохранятся."
                )

    def _start_worker(self, release=None):
        self.worker = UpdateWorker(release, self)
        self.worker.progress.connect(self.download_progress)
        self.worker.finished.connect(self.worker_finished)
        self.selection_changed()
        self.worker.start()

    def load_releases(self):
        if self.worker is not None:
            return
        self.status.setText("Загрузка списка версий с GitHub…")
        self._start_worker()

    def populate(self, releases):
        self.list.clear()
        for release in releases:
            label = release.tag
            if release.prerelease:
                label += " · предварительная"
            if release.tag == __version__:
                label += " · установлена"
            if not release.installable:
                label += " · ручная загрузка"
            if release.published_at:
                label += f" · {release.published_at[:10]}"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, release)
            self.list.addItem(item)
        if releases:
            self.list.setCurrentRow(0)
        else:
            self.status.setText("Опубликованных релизов пока нет.")

    def open_release(self):
        release = self.selected_release()
        if release:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(release.page_url))

    def install_selected(self):
        release = self.selected_release()
        if not release or not self.install.isEnabled():
            return
        if (
            QtWidgets.QMessageBox.question(
                self,
                "Установка версии",
                f"Установить {release.tag} вместо {__version__}?\n\n"
                "Измерения остановятся, программа закроется и запустится снова. Настройки и сохранённые данные останутся.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            != QtWidgets.QMessageBox.Yes
        ):
            return
        try:
            # Replacing a portable directory requires write access to its parent, too.
            install = Path(sys.executable).resolve().parent
            if not (install / "updater.exe").is_file():
                raise updates.UpdateError("Не найден updater.exe. Распакуйте полный дистрибутив.")
            with tempfile.TemporaryDirectory(prefix=".EspAdc-write-check-", dir=install.parent):
                pass
        except OSError:
            self.status.setText(
                "Нет доступа к каталогу установки. Перенесите папку ESP ADC в доступный для записи каталог."
            )
            return
        except updates.UpdateError as error:
            self.status.setText(str(error))
            return
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel.setText("Отменить загрузку")
        self.status.setText(f"Загрузка и проверка {release.tag}…")
        self._start_worker(release)

    def download_progress(self, downloaded, total):
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(downloaded * 100 / total)))

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        self.progress.hide()
        self.cancel.setText("Закрыть")
        self.selection_changed()
        if self.closing or worker.cancel.is_set():
            if isinstance(worker.result, updates.PreparedUpdate):
                worker.result.cleanup()
            worker.deleteLater()
            super().reject()
            return
        if worker.error:
            self.status.setText(worker.error)
        elif worker.release is None:
            self.populate(worker.result or [])
        elif worker.result:
            self.prepared.emit(worker.result)
            self.accept()
        worker.deleteLater()

    def reject(self):
        if self.worker is not None:
            self.closing = True
            self.worker.cancel.set()
            self.cancel.setEnabled(False)
            self.status.setText("Отмена… Ожидание завершения сетевого запроса.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)


class UpdateController(QtCore.QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = None
        self.pending = None
        self.check_worker = None
        menu = window.menuBar().addMenu("Программа")
        self.action = menu.addAction("Версии и обновление…", self.open_dialog)
        menu.addSeparator()
        menu.addAction(f"Версия: {__version__}").setEnabled(False)
        self.auto = menu.addAction("Проверять обновления при запуске")
        self.auto.setCheckable(True)
        self.auto.setChecked(window_settings().value("Updates/check_on_startup", True, type=bool))
        self.auto.toggled.connect(self.save_preference)
        if can_install() and self.auto.isChecked() and os.environ.get("ESP_ADC_SMOKE_TEST") != "1":
            QtCore.QTimer.singleShot(2000, self.check_on_startup)

    def save_preference(self, checked):
        window_settings().setValue("Updates/check_on_startup", checked)

    def open_dialog(self):
        if self.pending:
            return
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        self.dialog = UpdateDialog(self.window)
        self.dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.dialog.prepared.connect(self.queue_update)
        self.dialog.finished.connect(self.dialog_closed)
        self.dialog.setWindowModality(QtCore.Qt.WindowModal)
        self.dialog.show()
        self.dialog.load_releases()

    def dialog_closed(self):
        self.dialog = None

    def queue_update(self, prepared):
        self.pending = prepared
        self.window.setEnabled(False)
        self.window.statusBar().showMessage("Обновление подготовлено. Остановка измерений и сохранение настроек…")
        QtCore.QTimer.singleShot(0, self.window.close)

    def check_on_startup(self):
        if self.pending or not self.auto.isChecked():
            return
        self.check_worker = UpdateWorker(parent=self.window)
        self.check_worker.finished.connect(self.startup_finished)
        self.check_worker.start()

    def startup_finished(self):
        worker = self.check_worker
        self.check_worker = None
        if worker.error:
            logger.info("Проверка обновлений: %s", worker.error)
        else:
            for release in worker.result or []:
                try:
                    newer = updates.version_key(release.tag) > updates.version_key(__version__)
                except ValueError:
                    continue
                if newer and not release.prerelease and release.installable:
                    self.window.statusBar().showMessage(
                        f"Доступна {release.tag}. Откройте «Программа → Версии и обновление…»."
                    )
                    break
        worker.deleteLater()

    def launch_pending(self):
        if not self.pending:
            return True
        try:
            updates.launch_updater(self.pending, Path(sys.executable).resolve().parent, os.getpid())
        except Exception as error:
            self.pending.cleanup()
            self.pending = None
            self.window.setEnabled(True)
            thermostat = self.window.centralWidget().thermostat_widget
            thermostat.closing = False
            thermostat._set_running(False)
            QtWidgets.QMessageBox.critical(self.window, "Обновление ESP ADC", str(error))
            return False
        self.pending = None  # Helper now owns the downloaded files.
        return True


def window_settings():
    from store.state import State

    return State.settings
