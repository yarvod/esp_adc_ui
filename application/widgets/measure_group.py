import logging
import time
from typing import Dict

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import pyqtSignal

from api import EspAdc
from api.exceptions import DeviceApiError
from store.state import State

logger = logging.getLogger(__name__)


class MeasureThread(QtCore.QThread):
    finished = pyqtSignal(int)
    data_plot = pyqtSignal(list)
    log = pyqtSignal(dict)
    clock_changed = pyqtSignal()

    def __init__(self, parent, rps: int):
        super().__init__(parent)
        self.duration = State.duration
        self.rps = rps

    def run(self) -> None:
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                self.log.emit({"type": "info", "msg": "Device Connected!"})
                first_timestamp = None
                started = time.monotonic()
                previous_timestamp = None
                previous_read = None
                unavailable_since = None
                while State.is_measuring:
                    if time.monotonic() - started >= self.duration:
                        State.is_measuring = False
                        break
                    data_plot = []
                    time.sleep(1 / self.rps)
                    try:
                        timestamp_ms, data = daq.read_sample()
                    except DeviceApiError as exc:
                        if exc.status != 503 or exc.code != "not_ready":
                            raise
                        if unavailable_since is None:
                            unavailable_since = time.monotonic()
                        if time.monotonic() - unavailable_since >= 2:
                            status = daq.get_status()
                            if not status["adc_ready"] or not status["rtc_ready"]:
                                raise
                            # A long averaging window legitimately has no completed point yet.
                            unavailable_since = time.monotonic()
                        continue
                    unavailable_since = None
                    read_at = time.monotonic()
                    if timestamp_ms == previous_timestamp:
                        continue  # Polling can be faster than acquisition; only plot new points.
                    if first_timestamp is None:
                        first_timestamp = timestamp_ms
                    elif (
                        timestamp_ms < previous_timestamp
                        or abs((timestamp_ms - previous_timestamp) / 1000 - (read_at - previous_read)) > 2
                    ):
                        first_timestamp = timestamp_ms
                        self.clock_changed.emit()
                        self.log.emit({"type": "info", "msg": "RTC time updated; plot timer restarted"})
                    previous_timestamp = timestamp_ms
                    previous_read = read_at
                    duration = (timestamp_ms - first_timestamp) / 1000
                    for channel, voltage in enumerate(data, 1):
                        data_plot.append(
                            {"channel": channel, "voltage": voltage, "time": duration, "timestamp_ms": timestamp_ms}
                        )
                    if time.monotonic() - started >= self.duration:
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
    def __init__(self, parent, plot_widget=None, monitor_widget=None):
        super().__init__(parent)
        self.thread_measure = None
        self.plot_widget = plot_widget
        self.monitor_widget = monitor_widget
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
        flayout.addRow("Monitor Time, s:", self.duration)
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
        if self.plot_widget is not None:
            self.plot_widget.clear()
        if self.monitor_widget is not None:
            self.monitor_widget.reset_values()
        self.thread_measure = MeasureThread(self, rps=self.rps.value())
        self.thread_measure.data_plot.connect(self.plot_data)
        self.thread_measure.log.connect(self.set_log)
        if self.plot_widget is not None:
            self.thread_measure.clock_changed.connect(self.plot_widget.clear)
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
        State.is_measuring = False
        self.btn_start.setEnabled(True)
        if code == 0:
            logger.info("Measure finished successfully!")
        else:
            logger.error("Measure finished due to Error!")

    def plot_data(self, data: list):
        if self.is_plot_data.isChecked() and self.plot_widget is not None:
            self.plot_widget.add_plots(data)
        if self.monitor_widget is not None:
            self.monitor_widget.add_data(data)

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
