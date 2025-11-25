import logging
import time
from typing import Dict

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import pyqtSignal

from api import EspAdc
from store.state import State

logger = logging.getLogger(__name__)


class MeasureThread(QtCore.QThread):
    finished = pyqtSignal(int)
    data_plot = pyqtSignal(list)
    log = pyqtSignal(dict)

    def __init__(self, parent, rps: int):
        super().__init__(parent)
        self.duration = State.duration
        self.rps = rps

    def run(self) -> None:
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                self.log.emit({"type": "info", "msg": "Device Connected!"})
                start = time.time()
                while State.is_measuring:
                    data_plot = []
                    time.sleep(1 / self.rps)
                    data = daq.read_data()
                    if data:
                        duration = time.time() - start
                        a0, a1, a2 = data
                        data_plot.append({"channel": 1, "voltage": a0, "time": duration})
                        data_plot.append({"channel": 2, "voltage": a1, "time": duration})
                        data_plot.append({"channel": 3, "voltage": a2, "time": duration})
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
        self.finished.emit(code)


class MeasureGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__(parent)
        self.thread_measure = None
        self.setTitle("Monitor")

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

        flayout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        flayout.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        flayout.addRow("Measuring Time, s:", self.duration)
        flayout.addRow("RpS:", self.rps)
        flayout.addRow(self.is_plot_data, self.plot_window)

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
        parent = self.parent()
        if hasattr(parent, "plot_widget"):
            parent.plot_widget.clear()
        if hasattr(parent, "monitor_widget"):
            parent.monitor_widget.reset_values()
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
        parent = self.parent()
        if self.is_plot_data.isChecked() and hasattr(parent, "plot_widget"):
            parent.plot_widget.add_plots(data)
        if hasattr(parent, "monitor_widget"):
            parent.monitor_widget.add_data(data)

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
    def set_log(log: Dict):
        log_type = log.get("type")
        if not log_type:
            return
        method = getattr(logger, log_type, None)
        if not method:
            return
        method(log.get("msg"))
