import logging
import re
import socket
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class EspAdc:
    """
    A class to interface with the ESP ADC data acquisition system.

    Attributes:
        dll_path (str): The path to the DAQ122 DLL (SO).
    """

    def __init__(self, host: str, port: int):
        """
        Initializes the ESP ADC device interface.
        """
        self.socket = None
        self.host = host
        self.port = port

    def _setup_socket(self):
        self.socket = socket.socket()
        try:
            self.socket.connect((self.host, self.port))
        except Exception as e:
            logger.debug(f"Exception: {e}")

    def close(self):
        if not self.socket:
            return
        try:
            self.socket.close()
        except Exception as e:
            logger.debug(f"Exception: {e}")

    def __enter__(self) -> "EspAdc":
        self._setup_socket()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.close()
            del self
        except (OSError,) as e:
            raise DeviceCloseError(str(e))

    def read(self):
        data = self.socket.recv(1024)
        data = data.decode().rstrip()
        return data.replace("\n", "")

    def write(self, cmd: str):
        self.socket.sendall(f"{cmd}\n".encode())

    def read_data(self) -> Optional[Tuple[float, float]]:
        self.write("adc")
        response = self.read()
        reg = re.compile(f"ADC01:\s*([\d.-]+)\s*mV;ADC23:\s*([\d.-]+)\s*mV;")
        try:
            parsed = re.findall(reg, response)[0]
            return float(parsed[0]), float(parsed[1])
        except (IndexError, ValueError):
            return None
