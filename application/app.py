import logging

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtGui import QIcon

from application.widgets import PlotWidget, SdMeasureGroup, SdData, MonitorGroup, MeasureGroup
from application.widgets.base_init import BaseInit
from application.widgets.config_group import ConfigGroup
from application.widgets.log import LogWidget, LogHandler
from application.widgets.thermostat import ThermostatWidget
from application.widgets.dialogs.update_dialog import UpdateController
from application.version import __version__
from store.state import State


class MainWidget(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setTabPosition(QtWidgets.QTabWidget.North)
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs)

        # Widgets are created once: switching tabs preserves acquisition and state.
        measurements = QtWidgets.QWidget()
        measurements_layout = QtWidgets.QHBoxLayout(measurements)
        measurements_layout.setContentsMargins(8, 12, 8, 8)
        readings = QtWidgets.QWidget(measurements)
        readings_layout = QtWidgets.QVBoxLayout(readings)
        readings_layout.setContentsMargins(0, 0, 0, 0)
        self.monitor_widget = MonitorGroup(readings)
        self.monitor_widget.setTitle("Текущие измерения")
        self.plot_widget = PlotWidget(readings)
        self.plot_widget.layout().setContentsMargins(0, 0, 0, 0)
        readings_layout.addWidget(self.monitor_widget)
        readings_layout.addWidget(self.plot_widget, 1)
        measurements_layout.addWidget(readings, 1)

        self.measure_group = MeasureGroup(
            measurements, plot_widget=self.plot_widget, monitor_widget=self.monitor_widget
        )
        self.measure_group.setTitle("Параметры мониторинга")
        self.measure_group.setMaximumWidth(290)
        measurements_layout.addWidget(self.measure_group, 0, QtCore.Qt.AlignTop)
        self._add_tab(measurements, "Измерения")

        self.thermostat_widget = ThermostatWidget(self.tabs)
        self._add_tab(self.thermostat_widget, "Термостат")

        connection = QtWidgets.QWidget()
        connection_layout = QtWidgets.QVBoxLayout(connection)
        self.connection_group = BaseInit(connection)
        connection_layout.addWidget(self.connection_group)
        connection_layout.addStretch()
        self._add_tab(connection, "Подключение")

        settings = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings)
        self.config_group = ConfigGroup(settings)
        self.config_group.setTitle("Настройки ESP")
        settings_layout.addWidget(self.config_group)
        settings_layout.addStretch()
        self._add_tab(settings, "Настройки")

        sd_page = QtWidgets.QWidget()
        sd_layout = QtWidgets.QVBoxLayout(sd_page)
        self.sd_measure_group = SdMeasureGroup(sd_page)
        self.sd_measure_group.setTitle("Запись на SD-карту")
        self.sd_data = SdData(sd_page)
        sd_layout.addWidget(self.sd_measure_group)
        sd_layout.addWidget(self.sd_data, 1)
        self._add_tab(sd_page, "SD-карта")

        self.log_widget = LogWidget(self.tabs)
        self.log_widget.setTitle("События и ошибки")
        self.tabs.addTab(self.log_widget, "Журнал")

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

    def _add_tab(self, content, title):
        # Scroll only when needed on small screens; tab contents cannot enlarge the window.
        scroll = QtWidgets.QScrollArea(self.tabs)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)


class App(QtWidgets.QMainWindow):
    def __init__(
        self,
        title: str = "ESP ADC",
    ):
        super().__init__()
        self.setWindowTitle(f"{title} — {__version__}")
        self.setWindowIcon(QIcon("assets/volt16.png"))
        self.setCentralWidget(MainWidget(self))
        self.update_controller = UpdateController(self)
        self._close_timer = QtCore.QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close)
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(1050, available.width() - 48), min(680, available.height() - 64))
            self.move(available.center() - self.rect().center())
        else:
            self.resize(1050, 680)
        self.show()

    def closeEvent(self, event):
        State.is_measuring = False
        thermostat_ready = self.centralWidget().thermostat_widget.shutdown()
        # Keep the event loop alive until serial cleanup, exports and other workers finish.
        if not thermostat_ready or any(thread.isRunning() for thread in self.findChildren(QtCore.QThread)):
            event.ignore()
            self._close_timer.start(100)
            return
        State.store_state()
        if not self.update_controller.launch_pending():
            event.ignore()
            return
        event.accept()
