import math
import re
from datetime import datetime, timezone
from urllib.parse import quote

from api import routes
from api.base import BaseInstrument
from api.constants import HTTP, GAIN_TYPES, WIFI_TYPES
from api.exceptions import DeviceApiError


class EspAdc(BaseInstrument):
    """Device operations, independent of UI and HTTP connection lifetime."""

    def __init__(self, host="192.168.4.1", port=80, adapter=HTTP, **kwargs):
        super().__init__(host=host, port=port, adapter=adapter, **kwargs)

    def _request(self, method, path, body=None):
        if self.adapter is None:
            self._set_adapter()
        return self.adapter.request(method, path, body)

    def get_status(self):
        result = self._request("GET", routes.STATUS)
        if result.get("device") != "esp_adc" or result.get("api_version") != 1:
            raise DeviceApiError("Устройство не поддерживает HTTP API v1. Обновите прошивку ESP32.")
        return result

    def read_sample(self):
        result = self._request("GET", routes.ADC)
        try:
            timestamp_ms = int(result["timestamp_ms"])
            values = tuple(float(result[f"adc{i}_mv"]) for i in range(3))
        except (KeyError, TypeError, ValueError) as exc:
            raise DeviceApiError("Некорректный формат измерения ESP32") from exc
        if timestamp_ms <= 0 or not all(math.isfinite(value) for value in values):
            raise DeviceApiError("Часы RTC недоступны или измерение некорректно")
        return timestamp_ms, values

    def read_data(self):
        return self.read_sample()[1]

    def get_gain(self):
        return self._request("GET", routes.GAIN)["gain"]

    def set_gain(self, gain: GAIN_TYPES):
        return self._request("PUT", routes.GAIN, {"gain": gain})["gain"]

    def get_rtc_time(self) -> datetime:
        result = self._request("GET", routes.RTC)
        value = datetime.fromtimestamp(int(result["timestamp_ms"]) / 1000, timezone.utc)
        if not 2000 <= value.year <= 2099:
            raise DeviceApiError("Некорректная дата RTC")
        return value

    def set_rtc_time(self, value: datetime) -> None:
        if value.tzinfo is None:
            value = value.astimezone()
        value = value.astimezone(timezone.utc)
        if not 2000 <= value.year <= 2099:
            raise ValueError("DS1302 поддерживает даты с 2000 по 2099 год")
        self._request("PUT", routes.RTC, {"utc": value.strftime("%Y-%m-%dT%H:%M:%S")})

    def get_wifi(self):
        return self._request("GET", routes.WIFI)

    def set_wifi(self, wifi: WIFI_TYPES, ssid: str, pwd: str):
        return self._request("PUT", routes.WIFI, {"mode": wifi, "ssid": ssid, "password": pwd})["message"]

    def get_ip(self):
        return self._request("GET", routes.IP)["ip"]

    def get_config(self):
        return self._request("GET", routes.CONFIG)

    def set_config(self, config: dict):
        """Replace and persist the full configuration returned by get_config()."""
        return self._request("PUT", routes.CONFIG, config)

    @staticmethod
    def _filename(file):
        name = file.lstrip("/\\")
        if len(name) > 32 or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", name):
            raise ValueError("Некорректное имя файла SD")
        return name

    def start_record(self, file: str = ""):
        filename = self._filename(file) if file else ""
        result = self._request("POST", routes.START, {"filename": filename})
        return f"Recording started in {result['filename']}"

    def stop_record(self):
        result = self._request("POST", routes.STOP, {})
        return f"Recording stopped in {result['filename']}"

    def check_recording_status(self):
        result = self._request("GET", routes.RECORDING)
        if result.get("error"):
            return f"Error: {result['error']}"
        return f"Recording to {result['filename']}" if result["recording"] else "Not recording"

    def get_files(self):
        return self._request("GET", routes.FILES)["files"]

    def delete_file(self, file: str):
        name = self._filename(file)
        return self._request("DELETE", f"{routes.FILES}/{quote(name, safe='')}")["message"]

    def download_file(self, file: str, on_progress=None, chunk_size=65536, dest_path=None):
        name = self._filename(file)
        if self.adapter is None:
            self._set_adapter()
        return self.adapter.download(
            f"{routes.FILES}/{quote(name, safe='')}",
            dest_path or name,
            on_progress=on_progress,
            chunk_size=chunk_size,
        )

    def init_sd(self):
        return self._request("POST", routes.MOUNT, {})["message"]

    def deinit_sd(self):
        return self._request("POST", routes.UNMOUNT, {})["message"]
