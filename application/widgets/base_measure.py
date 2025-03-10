from PyQt5 import QtWidgets

from application.widgets.measure_group import MeasureGroup
from application.widgets.sd_measure_group import SdMeasureGroup


class BaseMeasure(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)

        layout = QtWidgets.QHBoxLayout()

        layout.addWidget(MeasureGroup(self))
        layout.addWidget(SdMeasureGroup(self))

        self.setLayout(layout)
