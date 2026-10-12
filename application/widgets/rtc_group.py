import logging
from datetime import datetime, timezone

from PyQt5 import QtCore, QtWidgets

from api import EspAdc
from store.state import State

logger = logging.getLogger(__name__)


class RtcThread(QtCore.QThread):
    result = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)

    def __init__(self, parent, action, value=None):
        super().__init__(parent)
        self.action = action
        self.value = value
        self.connection = dict(host=State.host, port=State.port, adapter=State.adapter)

    def run(self):
        try:
            with EspAdc(**self.connection) as daq:
                if self.action == "computer":
                    daq.set_rtc_time(datetime.now(timezone.utc))
                elif self.action == "set":
                    daq.set_rtc_time(self.value)
                self.result.emit(daq.get_rtc_time())
        except Exception as exc:
            self.error.emit(str(exc))


class RtcGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__("Часы DS1302", parent)
        self.thread = None
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel("Время часов ещё не прочитано", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QtWidgets.QHBoxLayout()
        self.date_time = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime(), self)
        self.date_time.setDisplayFormat("dd.MM.yyyy HH:mm:ss")
        self.date_time.setCalendarPopup(True)
        self.date_time.setMinimumDate(QtCore.QDate(2000, 1, 1))
        self.date_time.setMaximumDate(QtCore.QDate(2099, 12, 31))
        row.addWidget(self.date_time)
        self.btn_set = QtWidgets.QPushButton("Установить", self)
        self.btn_set.clicked.connect(lambda: self.execute("set"))
        row.addWidget(self.btn_set)
        layout.addLayout(row)

        buttons = QtWidgets.QHBoxLayout()
        self.btn_read = QtWidgets.QPushButton("Прочитать часы", self)
        self.btn_read.clicked.connect(lambda: self.execute("read"))
        self.btn_computer = QtWidgets.QPushButton("Время компьютера", self)
        self.btn_computer.clicked.connect(lambda: self.execute("computer"))
        buttons.addWidget(self.btn_read)
        buttons.addWidget(self.btn_computer)
        layout.addLayout(buttons)
        self.note = QtWidgets.QLabel(
            "Ввод и отображение — в часовом поясе компьютера. Перед настройкой остановите запись на SD.", self
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def execute(self, action):
        value = self.date_time.dateTime().toPyDateTime()
        self.thread = RtcThread(self, action, value)
        self.thread.result.connect(self.show_time)
        self.thread.error.connect(self.show_error)
        self.thread.finished.connect(lambda: self.set_busy(False))
        self.set_busy(True)
        self.thread.start()

    def set_busy(self, busy):
        for button in (self.btn_read, self.btn_set, self.btn_computer):
            button.setEnabled(not busy)

    def show_time(self, value):
        local = value.astimezone()
        self.date_time.setDateTime(QtCore.QDateTime(local.replace(tzinfo=None)))
        self.status.setText(f"Последнее чтение RTC: {local.strftime('%d.%m.%Y %H:%M:%S %Z')}")
        logger.info("Время RTC: %s", local.isoformat(timespec="seconds"))

    def show_error(self, message):
        self.status.setText(message)
        logger.error("RTC: %s", message)
