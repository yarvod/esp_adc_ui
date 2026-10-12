import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from api import EspAdc
from api import routes
from api.exceptions import DeviceApiError, DownloadCancelled


class DeviceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls = []
    config = {
        "sample_interval_ms": 10,
        "average_samples": 1,
        "ema_alpha": 0.25,
        "record_every": 1,
        "flush_samples": 860,
        "flush_interval_ms": 1000,
        "rtc_poll_interval_ms": 20,
        "gain": 1,
    }
    download_started = threading.Event()
    download_release = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_api()

    do_PUT = do_POST = do_DELETE = do_GET

    def handle_api(self):
        size = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(size)) if size else None
        self.calls.append((self.command, self.path, body))
        if self.path.startswith(routes.FILES + "/") and self.command == "GET":
            content = b"1791819045123; 1.0; 2.0; 3.0\n" * 16
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("X-File-Size", str(len(content) + (10 if "broken" in self.path else 0)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.download_started.set()
            if "slow" in self.path:
                self.download_release.wait(5)
            for offset in range(0, len(content), 31):
                chunk = content[offset : offset + 31]
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            self.close_connection = True
            return
        status = 200
        if self.path == routes.STATUS:
            result = {"device": "esp_adc", "api_version": 1, "ip": "192.168.4.1"}
        elif self.path == routes.CONFIG:
            if body is not None:
                type(self).config = body
            result = self.config
        elif self.path == routes.ADC:
            result = {"timestamp_ms": 1791819045123, "sequence": 23, "adc0_mv": 1, "adc1_mv": 2, "adc2_mv": 3}
        elif self.path == routes.RTC:
            result = {"timestamp_ms": 1791819045123, "utc": "2026-10-12T15:30:45Z"}
        elif self.path == routes.GAIN:
            result = {"gain": body["gain"] if body else 1}
        elif self.path == routes.IP:
            result = {"ip": "192.168.4.1"}
        elif self.path == routes.WIFI:
            result = {"mode": "own", "ssid": "esp", "message": "Wi-Fi saved"}
        elif self.path == routes.START:
            status = 409
            result = {"error": {"code": "conflict", "message": "Recording already active"}}
        elif self.path in (routes.STOP, routes.RECORDING):
            result = {"recording": False, "filename": "last.txt", "error": ""}
        elif self.path == routes.FILES:
            result = {"files": [{"name": "last.txt", "size": 123}]}
        elif self.path.startswith(routes.FILES + "/"):
            result = {"message": "File deleted"}
        elif self.path in (routes.MOUNT, routes.UNMOUNT):
            result = {"message": "SD ready"}
        else:
            status = 404
            result = {"error": {"code": "not_found", "message": "Missing"}}
        encoded = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True


class HttpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DeviceHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def device(self):
        return EspAdc(host="127.0.0.1", port=self.server.server_port)

    def test_typed_methods_cover_old_socket_operations(self):
        with self.device() as device:
            self.assertEqual(device.get_status()["api_version"], 1)
            self.assertEqual(device.get_ip(), "192.168.4.1")
            self.assertEqual(device.get_gain(), 1)
            self.assertEqual(device.set_gain(2), 2)
            self.assertEqual(device.get_wifi()["ssid"], "esp")
            self.assertEqual(device.set_wifi("own", "esp", "12345678"), "Wi-Fi saved")
            self.assertEqual(device.set_wifi("both", "router", "12345678"), "Wi-Fi saved")
            self.assertEqual(device.check_recording_status(), "Not recording")
            self.assertIn("last.txt", device.stop_record())
            self.assertEqual(device.get_files()[0]["size"], 123)
            self.assertEqual(device.delete_file("last.txt"), "File deleted")
            self.assertEqual(device.init_sd(), "SD ready")
            self.assertEqual(device.deinit_sd(), "SD ready")
        self.assertIn(("PUT", routes.GAIN, {"gain": 2}), DeviceHandler.calls)
        self.assertIn(
            ("PUT", routes.WIFI, {"mode": "own", "ssid": "esp", "password": "12345678"}), DeviceHandler.calls
        )
        self.assertIn(
            ("PUT", routes.WIFI, {"mode": "both", "ssid": "router", "password": "12345678"}), DeviceHandler.calls
        )

    def test_device_conflict_is_visible_to_caller(self):
        with self.device() as device, self.assertRaises(DeviceApiError) as error:
            device.start_record()
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(error.exception.code, "conflict")

    def test_configuration_round_trip(self):
        with self.device() as device:
            config = device.get_config()
            config.update(sample_interval_ms=20, average_samples=10, record_every=2)
            self.assertEqual(device.set_config(config), config)
            self.assertEqual(device.get_config(), config)
        self.assertIn(("PUT", routes.CONFIG, config), DeviceHandler.calls)

    def test_several_clients_can_poll_while_download_is_waiting(self):
        DeviceHandler.download_started.clear()
        DeviceHandler.download_release.clear()
        try:
            with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=5) as pool:
                target = Path(directory) / "slow.txt"

                def download():
                    with self.device() as device:
                        return device.download_file("slow.txt", dest_path=target)

                transfer = pool.submit(download)
                self.assertTrue(DeviceHandler.download_started.wait(2))

                def monitor():
                    with self.device() as device:
                        return device.read_sample(), device.get_rtc_time(), device.get_status()

                reads = [pool.submit(monitor) for _ in range(4)]
                for read in reads:
                    self.assertEqual(read.result(timeout=2)[0][1], (1, 2, 3))
                self.assertFalse(transfer.done())
                DeviceHandler.download_release.set()
                self.assertTrue(transfer.result(timeout=2)[0])
                self.assertTrue(target.read_bytes().startswith(b"1791819045123;"))
        finally:
            DeviceHandler.download_release.set()

    def test_incomplete_download_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as directory, self.device() as device:
            target = Path(directory) / "saved.txt"
            target.write_bytes(b"existing recording")
            with self.assertRaises(DeviceApiError):
                device.download_file("broken.txt", dest_path=target)
            self.assertEqual(target.read_bytes(), b"existing recording")
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_invalid_filename_never_reaches_device(self):
        with self.device() as device:
            for name in ("../test.txt", "folder/test.txt", ".hidden", "a" * 33):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    device.delete_file(name)

    def test_cancelled_download_cleans_up_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory, self.device() as device:
            target = Path(directory) / "saved.txt"
            target.write_bytes(b"existing recording")

            def cancel(downloaded, total):
                raise DownloadCancelled

            with self.assertRaises(DownloadCancelled):
                device.download_file("last.txt", dest_path=target, on_progress=cancel, chunk_size=128)
            self.assertEqual(target.read_bytes(), b"existing recording")
            self.assertEqual(list(Path(directory).glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
