import logging
import re
import socket
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

    def start_record(self, file: str = ""):
        # Если имя не задано — формируем сами, чтобы не полагаться на прошивку
        name = file or self._default_filename()
        if not name.startswith("/"):
            name = "/" + name
        return self.query(f"start={name}")

    @staticmethod
    def _default_filename() -> str:
        from datetime import datetime

        return datetime.now().strftime("data_%Y%m%d_%H%M%S.txt")

    def stop_record(self):
        return self.query("stop")

    def check_recording_status(self):
        return self.query("checkRecording")

    def get_files(self):
        response = self.query("files")
        files = []
        for item in response.split(";"):
            if not item:
                continue
            if ":" in item:
                name, size = item.split(":", 1)
                try:
                    size_int = int(size)
                except ValueError:
                    size_int = -1
                files.append({"name": name, "size": size_int})
            else:
                files.append({"name": item, "size": -1})
        return files

    def delete_file(self, file: str):
        return self.query(f"delete={file}")

    def download_file(self, file: str, on_progress=None, chunk_size: int = 256 * 1024, dest_path: str = None):
        """Скачать файл по TCP с прогрессом-колбэком (байты_скачано, всего_байт). Возвращает (ok, msg)."""

        self.write(f"hostFile=/{file}")
        # ускоряем передачу: отключаем Nagle и увеличиваем буфер приёма, если возможно
        try:
            self.adapter.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.adapter.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 512 * 1024)
        except OSError:
            pass

        def _recv_line(sock) -> str:
            buf = bytearray()
            while True:
                ch = sock.recv(1)
                if not ch:
                    break
                if ch == b"\n":
                    break
                buf.extend(ch)
            return buf.decode("ascii", errors="ignore")

        header = _recv_line(self.adapter.socket)
        if header.startswith("Error") or not header.startswith("SIZE "):
            return False, (header or "Error: no header")

        try:
            total_size = int(header.split()[1])
        except (IndexError, ValueError):
            return False, f"Invalid header: {header}"

        downloaded = 0
        target = dest_path or file
        with open(target, "wb") as f_out:
            while downloaded < total_size:
                chunk = self.adapter.socket.recv(min(chunk_size, total_size - downloaded))
                if not chunk:
                    break
                f_out.write(chunk)
                downloaded += len(chunk)
                if callable(on_progress):
                    on_progress(downloaded, total_size)

        if downloaded != total_size:
            return False, f"Download incomplete: {downloaded}/{total_size} bytes"
        return True, f"File {file} downloaded ({downloaded} bytes)"

    def init_sd(self):
        return self.query("initSD")

    def deinit_sd(self):
        return self.query("deinitSD")
