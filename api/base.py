import logging
from typing import Union

from api.constants import ADAPTERS
from api.exceptions import DeviceConnectionError, DeviceCloseError
from api.utils import import_class


logger = logging.getLogger(__name__)


class AdapterInterface:
    """Transport boundary for structured device operations."""

    def request(self, method, path, body=None):
        raise NotImplementedError

    def download(self, path, target, **kwargs):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class BaseInstrument:
    def __init__(
        self,
        host: str,
        port: Union[str, int],
        adapter: str,
        *args,
        **kwargs,
    ):
        self.host = host
        self.port = port
        self.adapter_name = adapter
        self.adapter = None
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        if self.adapter is None:
            self._set_adapter()
        return self

    def close(self):
        if self.adapter:
            self.adapter.close()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.close()
            del self
        except (OSError,) as e:
            raise DeviceCloseError(str(e))

    def _set_adapter(self) -> None:
        adapter_path = ADAPTERS.get(self.adapter_name)
        if not adapter_path:
            raise DeviceConnectionError("Используйте HTTP: старый TCP-протокол заменён HTTP API v1")
        try:
            adapter_class = import_class(adapter_path)
            self.adapter: AdapterInterface = adapter_class(host=self.host, port=self.port, *self.args, **self.kwargs)
        except (ImportError, ImportWarning) as e:
            logger.debug(f"[{self.__class__.__name__}._set_adapter] {e}")
            raise DeviceConnectionError
