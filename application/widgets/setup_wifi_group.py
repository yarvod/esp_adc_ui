import logging
import time

from PyQt5 import QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal

from api import EspAdc
from api.constants import WIFI_TYPES, WIFI

from store.state import State

logger = logging.getLogger(__name__)


class SetUpWifiThread(QThread):
    log = pyqtSignal(dict)

    def __init__(self, wifi: WIFI_TYPES, ssid: str, pwd: str, parent):
        self.wifi = wifi
        self.ssid = ssid
        self.pwd = pwd
        super().__init__(parent)

    def run(self):
        try:
            with EspAdc(host=State.host, port=State.port, adapter=State.adapter) as daq:
                daq.set_wifi(self.wifi, self.ssid, self.pwd)
                self.log.emit({"type": "info", "msg": "Setting up wifi ..."})
                time.sleep(5)
                State.wifi = self.wifi
                State.ssid = self.ssid
                State.pwd = self.pwd
                self.log.emit({"type": "info", "msg": f"Wifi {self.wifi} {self.ssid} is Set up"})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class CheckIPThread(QThread):
    ip = pyqtSignal(str)
    log = pyqtSignal(dict)

    def __init__(self, parent, mac: str):
        super().__init__(parent)
        self.mac = mac

    def run(self):
        try:
            from api.utils import find_ip_by_mac

            ip = find_ip_by_mac(self.mac)
            if not ip:
                self.ip.emit("Undefined")
                self.log.emit({"type": "error", "msg": f"IP not found for MAC {self.mac}"})
            else:
                self.ip.emit(ip)
                self.log.emit({"type": "info", "msg": f"IP is {ip}"})
        except Exception as e:
            self.log.emit({"type": "error", "msg": str(e)})
        self.finished.emit()


class SetUpWifiGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__(parent)

        layout = QtWidgets.QFormLayout()

        self.thread_setup_wifi = None

        self.setTitle("SetUp WiFi")

        layout = QtWidgets.QFormLayout()

        self.wifi = QtWidgets.QComboBox(self)
        self.wifi.addItems(WIFI)
        self.wifi.setCurrentText(State.wifi)

        self.ssid = QtWidgets.QLineEdit(self)
        self.ssid.setText(State.ssid)

        self.pwd = QtWidgets.QLineEdit(self)
        self.pwd.setText(f"{State.pwd}")

        self.mac = QtWidgets.QLineEdit(self)
        self.mac.setText(State.mac)

        self.ip = QtWidgets.QLabel("Undefined", self)

        self.btn_setup = QtWidgets.QPushButton("Set Up WiFi", self)
        self.btn_setup.clicked.connect(self.setup_wifi)

        self.btn_check_ip = QtWidgets.QPushButton("Check IP", self)
        self.btn_check_ip.clicked.connect(self.check_ip)

        layout.addRow("wifi:", self.wifi)
        layout.addRow("ssid:", self.ssid)
        layout.addRow("pwd:", self.pwd)
        layout.addRow("mac:", self.mac)
        layout.addRow("IP:", self.ip)
        layout.addRow(self.btn_setup)
        layout.addRow(self.btn_check_ip)

        self.setLayout(layout)

    def setup_wifi(self):
        self.thread_setup_wifi = SetUpWifiThread(
            parent=self,
            wifi=self.wifi.currentText(),
            ssid=self.ssid.text(),
            pwd=self.pwd.text(),
        )
        self.thread_setup_wifi.finished.connect(lambda: self.btn_setup.setEnabled(True))
        self.thread_setup_wifi.log.connect(self.set_log)
        self.thread_setup_wifi.start()
        self.btn_setup.setEnabled(False)

    def check_ip(self):
        State.mac = self.mac.text()
        self.thread_check_ip = CheckIPThread(parent=self, mac=State.mac)
        self.thread_check_ip.finished.connect(lambda: self.btn_check_ip.setEnabled(True))
        self.thread_check_ip.ip.connect(self.ip.setText)
        self.thread_check_ip.log.connect(self.set_log)
        self.thread_check_ip.start()
        self.btn_check_ip.setEnabled(False)

    @staticmethod
    def set_log(log: dict):
        log_type = log.get("type")
        if not log_type:
            return
        method = getattr(logger, log_type, None)
        if not method:
            return
        method(log.get("msg"))
