import logging

from PyQt5 import QtWidgets
from PyQt5.QtGui import QIcon

from application.widgets import PlotWidget, SdMeasureGroup, SdData, MonitorGroup, MeasureGroup
from application.widgets.base_init import BaseInit
from application.widgets.config_group import ConfigGroup
from application.widgets.log import LogWidget, LogHandler
from application.widgets.monitor import MonitorGroup
from store.state import State


class MainWidget(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)

        hlayout = QtWidgets.QHBoxLayout()
        left_vlayout = QtWidgets.QVBoxLayout()
        right_vlayout = QtWidgets.QVBoxLayout()

        self.monitor_widget = MonitorGroup(self)
        left_vlayout.addWidget(self.monitor_widget)

        self.plot_widget = PlotWidget(self)
        left_vlayout.addWidget(self.plot_widget)

        self.log_widget = LogWidget(self)
        left_vlayout.addWidget(self.log_widget)

        right_vlayout.addWidget(BaseInit(self))

        self.config_group = ConfigGroup(self)
        right_vlayout.addWidget(self.config_group)

        hlayout_measure = QtWidgets.QHBoxLayout()
        self.measure_group = MeasureGroup(self)
        self.sd_measure_group = SdMeasureGroup(self)
        # чтобы иметь доступ из дочерних групп
        self.measure_group.plot_widget = self.plot_widget
        self.measure_group.monitor_widget = self.monitor_widget
        hlayout_measure.addWidget(self.measure_group)
        hlayout_measure.addWidget(self.sd_measure_group)
        right_vlayout.addLayout(hlayout_measure)

        right_vlayout.addWidget(SdData(self))

        hlayout.addLayout(left_vlayout)
        hlayout.addLayout(right_vlayout)

        self.setLayout(hlayout)

        # logging config
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        log_widget_handler = LogHandler(self.log_widget)
        stream_handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
        log_widget_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)

        logger.addHandler(log_widget_handler)
        logger.addHandler(stream_handler)

        # sys.stdout = StdoutRedirector(self.log_widget)


class App(QtWidgets.QMainWindow):
    def __init__(
        self,
        title: str = "ESP ADC",
    ):
        super().__init__()
        self.left = 0
        self.top = 0
        self.width = 1200
        self.height = 700
        self.setGeometry(self.left, self.top, self.width, self.height)
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon("assets/volt16.png"))
        self.setCentralWidget(MainWidget(self))
        self.show()

    def closeEvent(self, event):
        State.store_state()
        event.accept()
