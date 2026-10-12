from PyQt5 import QtWidgets

from application.widgets.acquisition_group import AcquisitionGroup
from application.widgets.rtc_group import RtcGroup


class ConfigGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__("Настройки устройства", parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget(self)
        self.acquisition = AcquisitionGroup(self.tabs)
        self.rtc_group = RtcGroup(self.tabs)
        self.tabs.addTab(self.acquisition, "Сбор и запись")
        self.tabs.addTab(self.rtc_group, "Дата и время")
        layout.addWidget(self.tabs)
