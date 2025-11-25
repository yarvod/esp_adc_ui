import logging
from datetime import datetime

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


class InitSdThread(QThread):
    log = pyqtSignal(dict)

    def __init__(self, parent, init: bool):
        super().__init__(parent)
        self.init = init

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                if self.init:
                    response = daq.init_sd()
                else:
                    response = daq.deinit_sd()
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

        hlayout_buttons = QtWidgets.QHBoxLayout()
        hlayout_buttons_sd = QtWidgets.QHBoxLayout()

        self.btn_start = QtWidgets.QPushButton("Start", self)
        self.btn_start.clicked.connect(self.start_measure)

        self.btn_stop = QtWidgets.QPushButton("Stop", self)
        self.btn_stop.clicked.connect(self.stop_measure)

        self.btn_check_status = QtWidgets.QPushButton("Check status", self)
        self.btn_check_status.clicked.connect(self.check_status)

        self.btn_init_sd = QtWidgets.QPushButton("Init SD", self)
        self.btn_init_sd.clicked.connect(lambda: self.init_sd(True))

        self.btn_deinit_sd = QtWidgets.QPushButton("Deinit SD", self)
        self.btn_deinit_sd.clicked.connect(lambda: self.init_sd(False))

        hlayout_buttons.addWidget(self.btn_start)
        hlayout_buttons.addWidget(self.btn_stop)
        hlayout_buttons.addWidget(self.btn_check_status)
        hlayout_buttons_sd.addWidget(self.btn_init_sd)
        hlayout_buttons_sd.addWidget(self.btn_deinit_sd)

        layout.addLayout(hlayout_buttons)
        layout.addLayout(hlayout_buttons_sd)
        layout.addStretch()

        self.setLayout(layout)

    def start_measure(self):
        filename = datetime.now().strftime("data_%Y%m%d_%H%M%S.txt")
        self.thread_start = StartThread(
            parent=self,
            file=filename,
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

    def init_sd(self, init: bool):
        self.thread_init_sd = InitSdThread(
            parent=self,
            init=init,
        )
        if init:
            self.thread_init_sd.finished.connect(lambda: self.btn_init_sd.setEnabled(True))
        else:
            self.thread_init_sd.finished.connect(lambda: self.btn_deinit_sd.setEnabled(True))
        self.thread_init_sd.log.connect(self.set_log)
        self.thread_init_sd.start()
        if init:
            self.btn_init_sd.setEnabled(False)
        else:
            self.btn_deinit_sd.setEnabled(False)
