import logging

from PyQt5 import QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal

from api import EspAdc
from application.mixins.log_mixin import LogMixin
from store.state import State


logger = logging.getLogger(__name__)


class StartThread(QThread):
    log = pyqtSignal(dict)

    def __init__(self, file: str, parent):
        self.file = file
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                response = daq.start_record(self.file)
                log_type = "error" if "Error" in response else "info"
                self.log.emit({"type": log_type, "msg": response})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class StopThread(QThread):
    log = pyqtSignal(dict)

    def __init__(self, parent):
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                response = daq.stop_record()
                log_type = "error" if "Error" in response else "info"
                self.log.emit({"type": log_type, "msg": response})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class CheckStatusThread(QThread):
    log = pyqtSignal(dict)

    def __init__(self, parent):
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                response = daq.check_recording_status()
                log_type = "error" if "Error" in response else "info"
                self.log.emit({"type": log_type, "msg": response})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class SdMeasureGroup(QtWidgets.QGroupBox, LogMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.logger = logger
        self.setTitle("SD Measure")
        layout = QtWidgets.QVBoxLayout()

        flayout_file = QtWidgets.QFormLayout()
        hlayout_buttons = QtWidgets.QHBoxLayout()

        self.file_label = QtWidgets.QLabel("File:", self)

        self.file = QtWidgets.QLineEdit(self)

        self.btn_start = QtWidgets.QPushButton("Start", self)
        self.btn_start.clicked.connect(self.start_measure)

        self.btn_stop = QtWidgets.QPushButton("Stop", self)
        self.btn_stop.clicked.connect(self.stop_measure)

        self.btn_check_status = QtWidgets.QPushButton("Check status", self)
        self.btn_check_status.clicked.connect(self.check_status)

        flayout_file.addRow(self.file_label, self.file)
        hlayout_buttons.addWidget(self.btn_start)
        hlayout_buttons.addWidget(self.btn_stop)
        hlayout_buttons.addWidget(self.btn_check_status)

        layout.addLayout(flayout_file)
        layout.addLayout(hlayout_buttons)
        layout.addStretch()

        self.setLayout(layout)

    def start_measure(self):
        self.thread_start = StartThread(
            parent=self,
            file=self.file.text(),
        )
        self.thread_start.finished.connect(lambda: self.btn_start.setEnabled(True))
        self.thread_start.log.connect(self.set_log)
        self.thread_start.start()
        self.btn_start.setEnabled(False)

    def stop_measure(self):
        self.thread_stop = StopThread(
            parent=self,
        )
        self.thread_stop.finished.connect(lambda: self.btn_stop.setEnabled(True))
        self.thread_stop.log.connect(self.set_log)
        self.thread_stop.start()
        self.btn_stop.setEnabled(False)

    def check_status(self):
        self.thread_check_status = CheckStatusThread(
            parent=self,
        )
        self.thread_check_status.finished.connect(lambda: self.btn_check_status.setEnabled(True))
        self.thread_check_status.log.connect(self.set_log)
        self.thread_check_status.start()
        self.btn_check_status.setEnabled(False)
