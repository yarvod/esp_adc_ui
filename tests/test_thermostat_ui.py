import csv
import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtGui, QtWidgets

from application.app import App
from application.widgets.thermostat import ThermostatWidget
from test_thermostat import FakeSerial


class ThermostatUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def pump_until(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(condition(), "Qt worker did not finish in time")

    def test_threaded_session_exports_full_graph_and_keeps_display_bounded(self):
        main_thread = threading.get_ident()
        devices = []

        def factory(*args, **kwargs):
            device = FakeSerial(*args, **kwargs, readings=[29 + i / 1000 for i in range(620)])
            devices.append(device)
            return device

        with tempfile.TemporaryDirectory() as directory, patch(
            "api.thermostat.serial.Serial", side_effect=factory
        ), patch("api.thermostat.ThermostatSession.BOOT_DELAY", 0):
            widget = ThermostatWidget()
            try:
                widget.resize(1050, 620)
                widget.port.setEditText("fake")
                widget.directory.setText(directory)
                widget.start()
                received_threads = []

                def sample(data):
                    received_threads.append(threading.get_ident())
                    if data["count"] >= 621:
                        widget.stop()

                widget.thread.sample.connect(sample)
                self.pump_until(widget._is_idle)
                self.assertFalse(widget.failed, widget.status.text())
                self.assertEqual(set(received_threads), {main_thread})
                self.assertNotEqual(devices[0].owner, main_thread)
                self.assertEqual(len(widget.times), 500)
                self.assertEqual(len(widget.curve_t.xData), 500)
                self.assertEqual(widget.result["count"], 621)
                with open(widget.result["csv"], newline="") as stream:
                    self.assertEqual(len(list(csv.reader(stream))), 622)
                image = QtGui.QImage(widget.result["png"])
                self.assertFalse(image.isNull())
                self.assertEqual(image.width(), 1200)
                self.assertGreater(image.height(), 500)
                # Exported labels must survive the hidden view's paint cache.
                self.assertTrue(
                    any(image.pixelColor(x, y).lightness() < 200 for x in range(300, 1000) for y in range(10, 35))
                )
                self.assertTrue(widget.start_button.isEnabled())
                self.assertIn("Управление нагревом завершено", widget.summary.text())
            finally:
                widget.shutdown()
                self.pump_until(widget._is_idle)
                widget.deleteLater()
                self.app.processEvents()

    def test_window_close_waits_for_serial_cleanup_without_blocking_ui(self):
        devices = []

        def factory(*args, **kwargs):
            device = FakeSerial(*args, **kwargs)
            devices.append(device)
            return device

        handlers = list(logging.getLogger().handlers)
        with tempfile.TemporaryDirectory() as directory, patch(
            "api.thermostat.serial.Serial", side_effect=factory
        ), patch("api.thermostat.ThermostatSession.BOOT_DELAY", 0), patch(
            "application.app.State.store_state"
        ) as store:
            window = App()
            widget = window.centralWidget().thermostat_widget
            try:
                window.centralWidget().tabs.setCurrentIndex(1)
                widget.port.setEditText("fake")
                widget.directory.setText(directory)
                widget.start()
                self.pump_until(lambda: len(widget.times) > 0)
                started = time.monotonic()
                window.close()
                self.assertLess(time.monotonic() - started, 0.1)
                self.pump_until(lambda: not window.isVisible())
                self.assertTrue(devices[0].closed)
                self.assertEqual(devices[0].commands[-2:], [b"M104 S0\n", b"M155 S0\n"])
                self.assertIsNone(widget.thread)
                self.assertIsNone(widget.save_thread)
                self.assertTrue(Path(widget.result["png"]).exists())
                store.assert_called_once()
            finally:
                widget.shutdown()
                self.pump_until(widget._is_idle)
                logging.getLogger().handlers[:] = handlers
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
