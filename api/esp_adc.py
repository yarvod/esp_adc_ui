import logging
import re
from typing import Tuple, Optional

from api.base import BaseInstrument
from api.constants import GAIN_TYPES, WIFI_TYPES

logger = logging.getLogger(__name__)


class EspAdc(BaseInstrument):
    """
    A class to interface with the ESP ADC data acquisition system.
    """

    def read_data(self) -> Optional[Tuple[float, float, float]]:
        try:
            response = self.query("adc")
        except UnicodeDecodeError:
            return None
        reg = re.compile(f"ADC0:\s*([\d.-]+)\s*mV;\s*ADC1:\s*([\d.-]+)\s*mV;\s*ADC2:\s*([\d.-]+)\s*mV;")
        try:
            parsed = re.findall(reg, response)[0]
            return float(parsed[0]), float(parsed[1]), float(parsed[2])
        except (IndexError, ValueError):
            return None

    def set_gain(self, gain: GAIN_TYPES):
        self.write(f"setGain={gain}")

    def set_wifi(
        self,
        wifi: WIFI_TYPES,
        ssid: str,
        pwd: str,
    ):
        self.write(f"wifi={wifi};ssid={ssid};pwd={pwd}")

    def get_ip(self):
        try:
            return self.query("ip")
        except UnicodeDecodeError:
            return "Unknown IP"

    def start_record(self, file: str):
        return self.query(f"start={file}")

    def stop_record(self):
        return self.query("stop")

    def check_recording_status(self):
        return self.query("checkRecording")

    def get_files(self):
        response = self.query("files")
        return response.split(";")

    def delete_file(self, file: str):
        return self.query(f"delete={file}")

    def download_file(self, file: str):
        self.write(f"hostFile=/{file}")
        with open(file, "wb") as file:
            while True:
                data = self.adapter.socket.recv(4096)
                if not data:
                    return f"File {file} downloaded"
                file.write(data)

    def init_sd(self):
        return self.query("initSD")

    def deinit_sd(self):
        return self.query("deinitSD")
