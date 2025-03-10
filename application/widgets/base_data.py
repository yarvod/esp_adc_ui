from PyQt5 import QtWidgets

from application.widgets.data_table import DataTable
from application.widgets.sd_data import SdData


class BaseData(QtWidgets.QTabWidget):
    def __init__(self, parent):
        super().__init__(parent)

        self.addTab(DataTable(self), "Local Data")
        self.addTab(SdData(self), "SD Data")
