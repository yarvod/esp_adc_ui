from PyQt5 import QtWidgets

from store.state import State


class ConfigGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__(parent)

        self.setTitle("Config")

        hlayout = QtWidgets.QHBoxLayout()
        form_layout = QtWidgets.QFormLayout()

        self.host = QtWidgets.QLineEdit(self)
        self.host.setText(State.host)
        self.host.valueChanged.connect(self.set_host)

        self.port = QtWidgets.QSpinBox(self)
        self.port.setRange(1, 999999)
        self.port.setValue(State.port)
        self.port.valueChanged.connect(self.set_port)

        form_layout.addRow("Host:", self.host)
        form_layout.addRow("Port:", self.port)

        hlayout.addLayout(form_layout)

        self.setLayout(hlayout)

    @staticmethod
    def set_host(value: str):
        State.host = value

    @staticmethod
    def set_port(value: int):
        State.port = value
