import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from api.exceptions import DeviceApiError, DeviceConnectionError


class HttpAdapter:
    """Independent, bounded HTTP requests; no exclusive device session."""

    def __init__(self, host, port=80, timeout=10, **kwargs):
        raw = host.strip()
        parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise DeviceConnectionError("Укажите IP/имя ESP32 или http://адрес без пути API")
        if parsed.username or parsed.password:
            raise DeviceConnectionError("Адрес устройства не должен содержать учётные данные")
        try:
            number = parsed.port or int(port)
        except (TypeError, ValueError) as exc:
            raise DeviceConnectionError("Некорректный HTTP-порт") from exc
        if not 1 <= number <= 65535:
            raise DeviceConnectionError("HTTP-порт должен быть от 1 до 65535")
        hostname = parsed.hostname
        if ":" in hostname:
            hostname = f"[{hostname}]"
        self.base_url = urlunsplit(("http", f"{hostname}:{number}", "", "", ""))
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))

    def close(self):
        pass

    def _open(self, method, path, body=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must be absolute on the device")
        data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {"Accept": "application/json", "Connection": "close"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            return self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            with exc:
                try:
                    detail = json.loads(exc.read(65536)).get("error", {})
                    message = detail.get("message", str(exc))
                    code = detail.get("code", "http_error")
                except (ValueError, AttributeError, TypeError):
                    message, code = str(exc), "http_error"
            raise DeviceApiError(message, exc.code, code) from exc
        except (URLError, OSError) as exc:
            raise DeviceConnectionError(f"Не удалось обратиться к ESP32 по HTTP: {exc}") from exc

    def request(self, method, path, body=None):
        with self._open(method, path, body) as response:
            if response.headers.get_content_type() != "application/json":
                raise DeviceApiError("Устройство вернуло не JSON. Обновите прошивку ESP32.")
            try:
                result = json.loads(response.read(65536))
            except (ValueError, UnicodeDecodeError) as exc:
                raise DeviceApiError("Некорректный JSON-ответ ESP32") from exc
            if not isinstance(result, dict):
                raise DeviceApiError("Некорректный формат ответа ESP32")
            return result

    def download(self, path, target, on_progress=None, chunk_size=65536):
        if chunk_size <= 0:
            raise ValueError("Download chunk size must be positive")
        destination = Path(target)
        temporary = None
        try:
            with self._open("GET", path) as response:
                if response.headers.get_content_type() != "application/octet-stream":
                    raise DeviceApiError("Ожидался файл с SD-карты")
                try:
                    total = int(response.headers["X-File-Size"])
                except (TypeError, ValueError) as exc:
                    raise DeviceApiError("Устройство не передало размер файла") from exc
                if total < 0:
                    raise DeviceApiError("Некорректный размер файла")
                downloaded = 0
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent, prefix=f".{destination.name}.", suffix=".part", delete=False
                ) as output:
                    temporary = output.name
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        if callable(on_progress):
                            on_progress(downloaded, total)
                if downloaded != total:
                    raise DeviceApiError(f"Файл скачан не полностью: {downloaded}/{total} байт")
            os.replace(temporary, destination)
            temporary = None
            return True, f"File {destination.name} downloaded ({downloaded} bytes)"
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
