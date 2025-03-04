import logging

from PyQt5 import QtWidgets

from api import EspAdc

from store.state import State

logger = logging.getLogger(__name__)


class InitializeGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super(InitializeGroup, self).__init__(parent)

        self.setTitle("Device init")

        layout = QtWidgets.QFormLayout()

        self.host = QtWidgets.QLineEdit(self)
        self.host.setText(State.host)

        self.port = QtWidgets.QSpinBox(self)
        self.port.setRange(1, 999999)
        self.port.setValue(State.port)

        self.status = QtWidgets.QLabel("Not initialized", self)
        self.btnInitialize = QtWidgets.QPushButton("Initialize", self)
        self.btnInitialize.clicked.connect(self.initialize)

        layout.addRow("Device:", QtWidgets.QLabel("ESP32 ADC", self))
        layout.addRow("Host:", self.host)
        layout.addRow("Port:", self.port)
        layout.addRow("Status:", self.status)
        layout.addRow(self.btnInitialize)

        self.setLayout(layout)

    def initialize(self):
        try:
            with EspAdc(host=self.host.text(), port=self.port.value()) as daq:
                if daq.read_data:
                    self.status.setText("Success Connected!")
                    State.host = self.host.text()
                    State.port = self.port.value()
        except Exception as e:
            self.status.setText(str(e))
            logger.error(str(e))
