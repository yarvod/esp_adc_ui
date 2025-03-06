from typing import Union

from PyQt5.QtCore import QSettings

from api.constants import SERIAL


class State:
    settings = QSettings("settings.ini", QSettings.IniFormat)
    adapter: str = settings.value("State/adapter", SERIAL)
    host: str = settings.value("State/host", "")
    port: Union[int, str] = settings.value("State/port", "COM9")
    is_measuring: bool = False
    plot_window: int = 20
    duration: int = 60
    is_plot_data: bool = False
    store_data: bool = True

    @classmethod
    def store_state(cls):
        cls.settings.setValue("State/adapter", cls.adapter)
        cls.settings.setValue("State/host", cls.host)
        cls.settings.setValue("State/port", cls.port)
        cls.settings.sync()
