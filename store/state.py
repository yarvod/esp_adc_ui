import platform
import logging
from typing import Union

from PyQt5.QtCore import QSettings

from api.constants import SOCKET, WIFI_TYPES, WIFI, GAIN_TYPES

logger = logging.getLogger(__name__)


class State:
    if platform.system() == "Darwin":  # macOS
        # будет хранить настройки в ~/Library/Preferences/ASC.EspAdc.plist
        settings = QSettings("ASC", "EspAdc")
    else:
        settings = QSettings("settings.ini", QSettings.Format.IniFormat)

    adapter: str = settings.value("Init/adapter", SOCKET)
    host: str = settings.value("Init/host", "")
    port: Union[int, str] = settings.value("Init/port", "COM9")
    mac: str = settings.value("Init/mac", "10:06:1c:a6:b1:94")

    wifi: WIFI_TYPES = settings.value("WIFI/wifi", WIFI[0])
    ssid: str = settings.value("WIFI/ssid", "esp")
    pwd: str = settings.value("WIFI/pwd", "12345678")

    gain: GAIN_TYPES = int(settings.value("Config/gain", 0))

    is_measuring: bool = False
    duration: int = int(settings.value("Measure/duration", 60))
    is_plot_data: bool = settings.value("Measure/is_plot_data", "true") == "true"
    plot_window: int = int(settings.value("Measure/plot_window", 20))
    store_data: bool = settings.value("Measure/store_data", "true") == "true"
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
        cls.settings.setValue("Measure/store_data", cls.store_data)
        cls.settings.setValue("Measure/rps", cls.rps)

        cls.settings.sync()
