import logging
import time
from typing import Dict

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import pyqtSignal

from api import EspAdc
from store.data import MeasureManager
from store.state import State


logger = logging.getLogger(__name__)


class MeasureThread(QtCore.QThread):
    finished = pyqtSignal(int)
    data_plot = pyqtSignal(list)
    log = pyqtSignal(dict)

    def __init__(self, parent, rps: int):
        super().__init__(parent)
        self.store_data = State.store_data
        self.duration = State.duration
        self.measure = None
        self.rps = rps

    def create_measure(self):
        if self.store_data:
            self.measure = MeasureManager.create(
                data={
                    "rps": self.rps,
                    "data": {channel: [] for channel in range(1, 3)},
                    "time": [],
                }
            )
            self.measure.save(finish=False)

    def run(self) -> None:
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                self.log.emit({"type": "info", "msg": "Device Connected!"})

                self.create_measure()

                start = time.time()
                while State.is_measuring:
                    data_plot = []
                    time.sleep(1 / self.rps)
                    data = daq.read_data()
                    if data:
                        duration = time.time() - start
                        a0_1, a2_3 = data
                        if self.store_data and self.measure:
                            self.measure.data["data"][1].append(a0_1)
                            self.measure.data["data"][2].append(a2_3)
                            self.measure.data["time"].append(duration)

                        data_plot.append({"channel": 1, "voltage": a0_1, "time": duration})
                        data_plot.append({"channel": 2, "voltage": a2_3, "time": duration})

                        if duration > self.duration:
                            State.is_measuring = False
                    if data_plot:
                        self.data_plot.emit(data_plot)
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
            self.finish(1)
            return

        self.finish(0)

    def finish(self, code: int = 0):
        if self.store_data and self.measure:
            self.measure.save(finish=True)
        self.finished.emit(code)


class MeasureGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__(parent)
        self.thread_measure = None
        self.setTitle("Local Measure")

        vlayout = QtWidgets.QVBoxLayout()
        hlayout = QtWidgets.QHBoxLayout()
        flayout = QtWidgets.QFormLayout()

        self.duration = QtWidgets.QSpinBox(self)
        self.duration.setRange(1, 100000)
        self.duration.setValue(State.duration)
        self.duration.valueChanged.connect(self.set_duration)

        self.is_plot_data = QtWidgets.QCheckBox(self)
        self.is_plot_data.setText("Plot data")
        self.is_plot_data.setToolTip("Plotting might take a lot CPU resources!")
        self.is_plot_data.setChecked(State.is_plot_data)
        self.is_plot_data.stateChanged.connect(self.set_is_plot_data)

        self.plot_window = QtWidgets.QSpinBox(self)
        self.plot_window.setRange(1, 10000)
        self.plot_window.setValue(State.plot_window)
        self.plot_window.valueChanged.connect(self.set_plot_window)

        self.rps = QtWidgets.QSpinBox(self)
        self.rps.setToolTip("Requests per Second")
        self.rps.setRange(1, 100)
        self.rps.setValue(State.rps)
        self.rps.valueChanged.connect(self.set_rps)

        self.store_data = QtWidgets.QCheckBox(self)
        self.store_data.setText("Store Data")
        self.store_data.setToolTip("Storing data to data table below")
        self.store_data.setChecked(State.store_data)
        self.store_data.stateChanged.connect(self.set_store_data)

        flayout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        flayout.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        flayout.addRow("Measuring Time, s:", self.duration)
        flayout.addRow("RpS:", self.rps)
        flayout.addRow(self.is_plot_data, self.plot_window)
        flayout.addRow(self.store_data)

        self.btn_start = QtWidgets.QPushButton("Start", self)
        self.btn_start.clicked.connect(self.start_measure)
        self.btn_stop = QtWidgets.QPushButton("Stop", self)
        self.btn_stop.clicked.connect(self.stop_measure)
        hlayout.addWidget(self.btn_start)
        hlayout.addWidget(self.btn_stop)

        vlayout.addLayout(flayout)
        vlayout.addLayout(hlayout)

        self.setLayout(vlayout)

    def start_measure(self):
        self.parent().parent().plot_widget.clear()
        self.parent().parent().monitor_widget.reset_values()
        self.thread_measure = MeasureThread(self, rps=self.rps.value())
        self.thread_measure.data_plot.connect(self.plot_data)
        self.thread_measure.log.connect(self.set_log)
        self.btn_start.setEnabled(False)
        self.thread_measure.finished.connect(self.finish_measure)
        State.is_measuring = True
        self.thread_measure.start()

    @staticmethod
    def stop_measure():
        if State.is_measuring:
            logger.info("Wait for finishing measurement...")
        State.is_measuring = False

    def finish_measure(self, code: int = 0):
        self.btn_start.setEnabled(True)
        if code == 0:
            logger.info("Measure finished successfully!")
        else:
            logger.error("Measure finished due to Error!")

    def plot_data(self, data: list):
        if self.is_plot_data.isChecked():
            self.parent().parent().plot_widget.add_plots(data)
        self.parent().parent().monitor_widget.add_data(data)

    @staticmethod
    def set_duration(value):
        State.duration = int(value)

    @staticmethod
    def set_rps(value):
        State.rps = int(value)

    def set_is_plot_data(self, state):
        if state == QtCore.Qt.CheckState.Checked:
            State.is_plot_data = True
            return
        State.is_plot_data = False

    @staticmethod
    def set_plot_window(value):
        State.plot_window = int(value)

    @staticmethod
    def set_read_elements(value):
        State.read_elements_count.value = int(value)

    @staticmethod
    def set_average(state):
        value = state == QtCore.Qt.CheckState.Checked
        State.is_average = value

    @staticmethod
    def set_store_data(state):
        value = state == QtCore.Qt.CheckState.Checked
        State.store_data = value

    @staticmethod
    def set_log(log: Dict):
        log_type = log.get("type")
        if not log_type:
            return
        method = getattr(logger, log_type, None)
        if not method:
            return
        method(log.get("msg"))
