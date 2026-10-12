import platform
import logging
from typing import Union

from PyQt5.QtCore import QSettings

from api.constants import HTTP, DEVICE_MAC, WIFI_TYPES, WIFI, GAIN_TYPES

logger = logging.getLogger(__name__)


class State:
    if platform.system() == "Darwin":  # macOS
        # будет хранить настройки в ~/Library/Preferences/ASC.EspAdc.plist
        settings = QSettings("ASC", "EspAdc")
    else:
        settings = QSettings("settings.ini", QSettings.Format.IniFormat)

    adapter: str = HTTP
    host: str = settings.value("Init/host", "192.168.4.1")
    port: Union[int, str] = settings.value("Init/port", 80)
    if not str(port).isdigit():
        port = 80
    mac: str = settings.value("Init/mac", DEVICE_MAC)
    if mac.lower() in ("10:06:1c:a6:b1:94", "7c:b0:c2:f1:06:8f", "7e:b0:c2:f1:06:8f"):
        mac = DEVICE_MAC

    wifi: WIFI_TYPES = settings.value("WIFI/wifi", WIFI[0])
    ssid: str = settings.value("WIFI/ssid", "esp")
    pwd: str = settings.value("WIFI/pwd", "12345678")

    gain: GAIN_TYPES = int(settings.value("Config/gain", 0))

    is_measuring: bool = False
    duration: int = int(settings.value("Measure/duration", 60))
    is_plot_data: bool = settings.value("Measure/is_plot_data", "true") == "true"
    plot_window: int = int(settings.value("Measure/plot_window", 20))
    rps: int = int(settings.value("Measure/rps", 5))

    @classmethod
    def store_state(cls):
        cls.settings.setValue("Init/adapter", cls.adapter)
        cls.settings.setValue("Init/host", cls.host)
        cls.settings.setValue("Init/port", cls.port)
        cls.settings.setValue("Init/mac", cls.mac)

        cls.settings.setValue("WIFI/wifi", cls.wifi)
        cls.settings.setValue("WIFI/ssid", cls.ssid)
        cls.settings.setValue("WIFI/pwd", cls.pwd)

        cls.settings.setValue("Config/gain", cls.gain)

        cls.settings.setValue("Measure/duration", cls.duration)
        cls.settings.setValue("Measure/is_plot_data", cls.is_plot_data)
        cls.settings.setValue("Measure/plot_window", cls.plot_window)
        cls.settings.remove("Measure/store_data")
        cls.settings.setValue("Measure/rps", cls.rps)

        cls.settings.sync()
