import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from application.updates import PreparedUpdate, Release
from application.version import __version__
from application.widgets.dialogs.update_dialog import UpdateDialog


class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.dialog = UpdateDialog()
        self.dialog.show()
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.close)

    def pump_until_idle(self):
        deadline = time.monotonic() + 5
        while self.dialog.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertIsNone(self.dialog.worker)

    def release(self, tag="v2.0.0", available=True):
        return Release(
            tag,
            "Описание изменений",
            "2026-10-12",
            False,
            "https://github.com/test/release",
            "https://github.com/test/file" if available else "",
            "https://github.com/test/checksum" if available else "",
        )

    def test_current_release_cannot_install_and_older_version_can(self):
        with patch("application.widgets.dialogs.update_dialog.can_install", return_value=True):
            self.dialog.populate([self.release(__version__), self.release("v0.9.0"), self.release("v0.8.0", False)])
            self.assertFalse(self.dialog.install.isEnabled())
            self.dialog.list.setCurrentRow(1)
            self.assertTrue(self.dialog.install.isEnabled())
            self.assertEqual(self.dialog.description.toPlainText(), "Описание изменений")
            self.dialog.list.setCurrentRow(2)
            self.assertFalse(self.dialog.install.isEnabled())
            self.assertTrue(self.dialog.manual.isEnabled())

    def test_source_mode_allows_manual_release_link(self):
        with patch("application.widgets.dialogs.update_dialog.can_install", return_value=False):
            self.dialog.populate([self.release()])
        self.assertFalse(self.dialog.install.isEnabled())
        with patch("application.widgets.dialogs.update_dialog.QtGui.QDesktopServices.openUrl") as open_url:
            self.dialog.manual.click()
        self.assertEqual(open_url.call_args.args[0].toString(), "https://github.com/test/release")

    def test_loading_and_error_are_asynchronous_and_retryable(self):
        with patch(
            "application.widgets.dialogs.update_dialog.updates.list_releases",
            side_effect=RuntimeError("Network unavailable"),
        ):
            self.dialog.load_releases()
            self.assertFalse(self.dialog.refresh.isEnabled())
            self.pump_until_idle()
        self.assertIn("Network unavailable", self.dialog.status.text())
        self.assertTrue(self.dialog.refresh.isEnabled())
        with patch("application.widgets.dialogs.update_dialog.updates.list_releases", return_value=[self.release()]):
            self.dialog.refresh.click()
            self.pump_until_idle()
        self.assertEqual(self.dialog.list.count(), 1)

    def test_close_during_download_waits_for_worker_and_cleans_result(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "update"
            target.mkdir()
            prepared = PreparedUpdate(target, target / "EspAdc")

            def download(release, progress, cancel):
                cancel.wait(3)
                return prepared

            with patch("application.widgets.dialogs.update_dialog.updates.prepare_update", side_effect=download):
                self.dialog._start_worker(self.release())
                self.dialog.reject()
                self.pump_until_idle()
            self.assertFalse(target.exists())
            self.assertFalse(self.dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
