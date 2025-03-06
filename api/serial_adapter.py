import serial
import time

from api.base import AdapterInterface


class SerialAdapter(AdapterInterface):
    def __init__(
        self,
        port: str,
        timeout: float = 0,
        delay: float = 0,
        *args,
        **kwargs,
    ):
        self.timeout = timeout
        self.delay = delay
        self.serial = serial.Serial(port=port, timeout=timeout)

    def _send(self, value: str) -> None:
        encoded_cmd = ("%s\n" % value).encode()
        self.serial.write(encoded_cmd)

    def _recv(self, byte_num) -> str:
        value = self.serial.read(byte_num)
        return value.decode().rstrip("\n\x00")

    def read(self, num_bytes=1024, **kwargs) -> str:
        return self._recv(num_bytes)

    def write(self, cmd: str) -> None:
        self._send(cmd)

    def query(self, cmd: str, buffer_size=1024, **kwargs) -> str:
        self.write(cmd)
        if self.delay:
            time.sleep(self.delay)
        return self.read(num_bytes=buffer_size)

    def close(self):
        if self.serial and self.serial.isOpen():
            self.serial.close()
