import logging

from PyQt5 import QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal

from api.constants import GAINS, GAIN_TYPES
from api import EspAdc
from store.state import State


logger = logging.getLogger(__name__)


class SetGainThread(QThread):
    log = pyqtSignal(dict)

    def __init__(self, gain: GAIN_TYPES, parent):
        self.gain = gain
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                daq.set_gain(self.gain)
                State.gain = self.gain
                self.log.emit({"type": "info", "msg": f"Voltage Range is {GAINS[self.gain]}"})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class ConfigGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__(parent)

        self.setTitle("Config")

        vlauout = QtWidgets.QVBoxLayout()
        hlayout_gain = QtWidgets.QHBoxLayout()

        self.gain_label = QtWidgets.QLabel("Voltage Range:", self)
        self.gain = QtWidgets.QComboBox(self)

        self.gain.addItems(GAINS.values())
        self.gain.setCurrentIndex(State.gain)

        self.btn_set_gain = QtWidgets.QPushButton("Set", self)
        self.btn_set_gain.clicked.connect(self.set_gain)

        hlayout_gain.addWidget(self.gain_label)
        hlayout_gain.addWidget(self.gain)
        hlayout_gain.addWidget(self.btn_set_gain)
        hlayout_gain.addStretch()

        vlauout.addLayout(hlayout_gain)

        self.setLayout(vlauout)

    def set_gain(self):
        self.thread_set_gain = SetGainThread(
            parent=self,
            gain=self.gain.currentIndex(),
        )
        self.thread_set_gain.finished.connect(lambda: self.btn_set_gain.setEnabled(True))
        self.thread_set_gain.log.connect(self.set_log)
        self.thread_set_gain.start()
        self.btn_set_gain.setEnabled(False)

    @staticmethod
    def set_log(log: dict):
        log_type = log.get("type")
        if not log_type:
            return
        method = getattr(logger, log_type, None)
        if not method:
            return
        method(log.get("msg"))
