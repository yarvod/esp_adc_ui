import logging
import socket
import time
from typing import Union

from api.base import AdapterInterface
from api.exceptions import DeviceConnectionError

logger = logging.getLogger(__name__)


class SocketAdapter(AdapterInterface):
    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 10,
        delay: float = 0.05,
        *args,
        **kwargs,
    ):
        self.socket = None
        self.host = host
        try:
            self.port = int(port)
        except ValueError:
            raise DeviceConnectionError("Incorrect port")
        self.timeout = 0
        self.delay = delay
        self.init(timeout)

    def init(self, timeout: float = 2):
        if self.socket is None:
            logger.debug(f"[{self.__class__.__name__}.init]Socket is None, creating ...")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        else:
            logger.debug(f"[{self.__class__.__name__}.init]Socket is already existed, connecting ...")
        self.connect(timeout)

    def connect(self, timeout: float = 2):
        self.set_timeout(timeout)
        try:
            self.socket.connect((self.host, self.port))
            logger.debug(f"[{self.__class__.__name__}.connect]Socket has been connected {self.socket}.")
        except (OSError, TimeoutError) as e:
            logger.debug(f"[{self.__class__.__name__}.connect] Error: {e}")
            raise DeviceConnectionError("Unable to connect socket")

    def is_socket_closed(self) -> Union[bool, None]:
        try:
            # this will try to read bytes without blocking and also without removing them from buffer (peek only)
            data = self.socket.recv(16)
            if len(data) == 0:
                logger.debug(f"[{self.__class__.__name__}.is_socket_closed] Socket is closed")
                return True
        except BlockingIOError:
            logger.debug(f"[{self.__class__.__name__}.is_socket_closed] BlockingIOError, socket is opened")
            return False  # socket is open and reading from it would block
        except ConnectionResetError:
            logger.debug(f"[{self.__class__.__name__}.is_socket_closed] ConnectionResetError, socket is closed")
            return True  # socket was closed for some other reason
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}.is_socket_closed] Unexpected exception '{e}', socket status is undefined"
            )
            return None
        logger.debug(f"[{self.__class__.__name__}.is_socket_closed] Socket is opened")
        return False

    def close(self):
        if self.socket is None:
            logger.warning(f"[{self.__class__.__name__}.close] Socket is None")
            return
        self.socket.close()
        logger.debug(f"[{self.__class__.__name__}.close] Socket has been closed.")

    def write(self, command: str, **kwargs):
        self._send(command)

    def read(self, num_bytes=1024, **kwargs):
        return self._recv(num_bytes)

    def query(self, command: str, buffer_size=1024 * 1024, delay: float = 0, **kwargs):
        self.write(command, **kwargs)
        if delay:
            time.sleep(delay)
        elif self.delay:
            time.sleep(self.delay)
        return self.read(num_bytes=buffer_size)

    def set_timeout(self, timeout):
        if timeout < 1e-3 or timeout > 20:
            raise ValueError("Timeout must be >= 1e-3 (1ms) and <= 3 (3s)")

        self.timeout = timeout
        self.socket.settimeout(self.timeout)

    def _send(self, value):
        encoded_value = ("%s\n" % value).encode("ascii")
        self.socket.sendall(encoded_value)

    def _recv(self, byte_num):
        value = self.socket.recv(byte_num)
        return value.decode("ascii").rstrip().replace("\n", "")


if __name__ == "__main__":
    print("IP address:")
    h = input()
    print("Port:")
    p = int(input())
    print("command:")
    command = input()
    sock = SocketAdapter(host=h, port=p)
    sock.connect()
    print(sock.query(command))
