import logging
import textwrap
from typing import Union

from PyQt5 import QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal

from api import EspAdc
from api.constants import ADAPTERS, SERIAL

from store.state import State

logger = logging.getLogger(__name__)


class InitializeThread(QThread):
    status = pyqtSignal(str)
    log = pyqtSignal(dict)

    def __init__(self, adapter: str, host: str, port: Union[str, int], parent):
        self.adapter = adapter
        self.host = host
        self.port = port
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=self.host, port=self.port, adapter=self.adapter) as daq:
                self.status.emit("Success Connected!")
                State.adapter = self.adapter
                State.host = self.host
                State.port = self.port
        except Exception as e:
            self.status.emit(textwrap.shorten(str(e), width=50))
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class InitializeGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super(InitializeGroup, self).__init__(parent)

        self.thread_initialize = None

        self.setTitle("Device init")

        layout = QtWidgets.QFormLayout()

        self.host = QtWidgets.QLineEdit(self)
        self.host.setText(State.host)

        self.adapter = QtWidgets.QComboBox(self)
        self.adapter.addItems(ADAPTERS.keys())
        self.adapter.currentTextChanged.connect(self.adapter_changed)
        self.adapter.setCurrentText(State.adapter)

        self.port = QtWidgets.QLineEdit(self)
        self.port.setText(f"{State.port}")

        self.status = QtWidgets.QLabel("Not initialized", self)
        self.btnInitialize = QtWidgets.QPushButton("Initialize", self)
        self.btnInitialize.clicked.connect(self.initialize)

        layout.addRow("Device:", QtWidgets.QLabel("ESP32 ADC", self))
        layout.addRow("Adapter:", self.adapter)
        layout.addRow("Host:", self.host)
        layout.addRow("Port:", self.port)
        layout.addRow("Status:", self.status)
        layout.addRow(self.btnInitialize)

        self.setLayout(layout)
        self.adapter_changed(State.adapter)

    def initialize(self):
        self.thread_initialize = InitializeThread(
            parent=self,
            adapter=self.adapter.currentText(),
            host=self.host.text(),
            port=self.port.text(),
        )
        self.thread_initialize.finished.connect(lambda: self.btnInitialize.setEnabled(True))
        self.thread_initialize.status.connect(self.status.setText)
        self.thread_initialize.log.connect(self.set_log)
        self.thread_initialize.start()
        self.btnInitialize.setEnabled(False)

    def adapter_changed(self, text: str):
        self.host.setEnabled(text != SERIAL)

    @staticmethod
    def set_log(log: dict):
        log_type = log.get("type")
        if not log_type:
            return
        method = getattr(logger, log_type, None)
        if not method:
            return
        method(log.get("msg"))
