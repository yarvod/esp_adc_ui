from PyQt5 import QtWidgets

from application.widgets.initialize_group import InitializeGroup
from application.widgets.setup_wifi_group import SetUpWifiGroup


class BaseInit(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)

        layout = QtWidgets.QHBoxLayout()

        layout.addWidget(InitializeGroup(self))
        layout.addWidget(SetUpWifiGroup(self))

        self.setLayout(layout)
