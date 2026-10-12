import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from api.esp_adc import EspAdc
from api.exceptions import DeviceApiError


class RtcApiTests(unittest.TestCase):
    def setUp(self):
        self.device = EspAdc(host="test", port=80, adapter="HTTP")
        self.device.adapter = Mock()

    def test_manual_local_time_is_sent_as_utc(self):
        self.device.adapter.request.return_value = {"timestamp_ms": 1791819045000}
        value = datetime(2026, 10, 12, 18, 30, 45, tzinfo=timezone(timedelta(hours=3)))
        self.device.set_rtc_time(value)
        self.device.adapter.request.assert_called_once_with("PUT", "/api/v1/rtc", {"utc": "2026-10-12T15:30:45"})

    def test_read_timestamp_beyond_32_bits(self):
        value = datetime(2040, 2, 29, 12, 0, tzinfo=timezone.utc)
        milliseconds = int(value.timestamp() * 1000)
        self.device.adapter.request.return_value = {"timestamp_ms": milliseconds}
        self.assertEqual(self.device.get_rtc_time(), value)

    def test_acquisition_uses_timestamp_from_same_response(self):
        self.device.adapter.request.return_value = {
            "adc0_mv": 1.2,
            "adc1_mv": -3.4,
            "adc2_mv": 5.6,
            "timestamp_ms": 1791817200123,
        }
        self.assertEqual(self.device.read_sample(), (1791817200123, (1.2, -3.4, 5.6)))
        self.device.adapter.request.assert_called_once_with("GET", "/api/v1/adc", None)

    def test_voltage_reader_still_works(self):
        self.device.adapter.request.return_value = {
            "adc0_mv": 1.2,
            "adc1_mv": -3.4,
            "adc2_mv": 5.6,
            "timestamp_ms": 1791817200123,
        }
        self.assertEqual(self.device.read_data(), (1.2, -3.4, 5.6))

    def test_missing_or_unset_clock_does_not_use_computer_time(self):
        for response in ({}, {"adc0_mv": 1, "adc1_mv": 2, "adc2_mv": 3, "timestamp_ms": 0}):
            self.device.adapter.request.return_value = response
            with self.subTest(response=response), self.assertRaises(DeviceApiError):
                self.device.read_sample()

    def test_clock_errors_propagate(self):
        self.device.adapter.request.side_effect = DeviceApiError("RTC unavailable", 503, "not_ready")
        with self.assertRaisesRegex(DeviceApiError, "RTC unavailable"):
            self.device.get_rtc_time()
        with self.assertRaisesRegex(DeviceApiError, "RTC unavailable"):
            self.device.set_rtc_time(datetime(2026, 10, 12, tzinfo=timezone.utc))

    def test_recording_name_is_generated_by_firmware(self):
        self.device.adapter.request.return_value = {"filename": "rtc.txt"}
        self.device.start_record()
        self.device.adapter.request.assert_called_once_with("POST", "/api/v1/recording/start", {"filename": ""})

    def test_unsupported_year_is_rejected_before_sending(self):
        with self.assertRaises(ValueError):
            self.device.set_rtc_time(datetime(2100, 1, 1, tzinfo=timezone.utc))
        self.device.adapter.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
