import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

import cli


class CliTests(unittest.TestCase):
    def test_start_records_on_device_without_local_output(self):
        device = MagicMock()
        device.start_record.return_value = "Recording started in measurements.txt"
        with patch("cli.EspAdc") as factory, contextlib.redirect_stdout(io.StringIO()) as output:
            factory.return_value.__enter__.return_value = device
            self.assertEqual(cli.main(["start", "--filename", "measurements.txt"]), 0)
        device.start_record.assert_called_once_with("measurements.txt")
        self.assertIn("measurements.txt", output.getvalue())

    def test_monitor_prints_readings_without_writing_files(self):
        device = MagicMock()
        device.read_sample.return_value = (1700000000000, (1, 2, 3))
        with patch("cli.EspAdc") as factory, patch("cli.time.monotonic", side_effect=[0, 0, 2]), patch(
            "cli.time.sleep"
        ), patch("builtins.open", side_effect=AssertionError("local storage used")), contextlib.redirect_stdout(
            io.StringIO()
        ) as output:
            factory.return_value.__enter__.return_value = device
            self.assertEqual(cli.main(["monitor", "--duration", "1"]), 0)
        self.assertIn("1700000000000", output.getvalue())


if __name__ == "__main__":
    unittest.main()
