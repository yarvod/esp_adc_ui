import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from application.widgets.sd_data import SdData


class SdDataUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.widget = SdData(None)
        self.widget.show()
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)

    def visible_names(self):
        return [name for name, widgets in self.widget.file_rows if not widgets[0].isHidden()]

    def test_recent_timestamp_names_come_first_and_input_is_unchanged(self):
        files = [
            {"name": name, "size": 10}
            for name in (
                "data_20261011_235959_999.txt",
                "data_20261012_120000_001.txt",
                "data_20261012_120000_010.txt",
            )
        ]
        original = list(files)
        self.widget.set_files_list(files)
        self.assertEqual(self.visible_names(), [item["name"] for item in reversed(files)])
        self.assertEqual(files, original)

    def test_filter_is_case_insensitive_local_and_preserved_on_refresh(self):
        self.widget.set_files_list(["data_20261012_120000.txt", "DATA_20261011_090000.TXT", "notes.txt"])
        with patch("application.widgets.sd_data.EspAdc", side_effect=AssertionError("filter accessed device")):
            self.widget.file_filter.setText("  DATA_20261011  ")
            self.assertEqual(self.visible_names(), ["DATA_20261011_090000.TXT"])
            self.widget.set_files_list(["data_20261012_120000.txt", "data_20261011_090000.txt"])
            self.assertEqual(self.visible_names(), ["data_20261011_090000.txt"])
            self.widget.file_filter.clear()
        self.assertEqual(len(self.visible_names()), 2)

    def test_filter_preserves_buttons_and_correct_download_target(self):
        self.widget.set_files_list(["data_20261011_090000.txt", "data_20261012_120000.txt"])
        newest, buttons = self.widget.file_rows[0]
        download = buttons[1]
        download.setEnabled(False)
        self.widget.file_filter.setText("no-match")
        self.assertEqual(self.visible_names(), [])
        self.widget.file_filter.clear()
        self.assertIs(self.widget.file_rows[0][1][1], download)
        self.assertFalse(download.isEnabled())
        download.setEnabled(True)
        calls = []

        def start(file, index):
            calls.append((file, index))

        with patch.object(self.widget, "download_file", new=start):
            # Rebuild connects the mocked slot, preserving file identity after sorting.
            self.widget.set_files_list(["data_20261011_090000.txt", newest])
            self.widget.file_rows[0][1][1].click()
        self.assertEqual(calls, [(newest, 0)])

    def test_system_files_stay_hidden_and_empty_list_is_supported(self):
        self.widget.set_files_list([".hidden", "System Volume Information", "SYSTEM~1", "data_20261012_120000.txt"])
        self.assertEqual(self.visible_names(), ["data_20261012_120000.txt"])
        self.widget.set_files_list([])
        self.assertEqual(self.visible_names(), [])


if __name__ == "__main__":
    unittest.main()
