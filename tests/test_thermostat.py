import csv
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import serial

from api.thermostat import RunningStats, ThermostatConfig, ThermostatSession, parse_temperature


class FakeSerial:
    def __init__(self, port, baudrate, timeout, write_timeout, readings=(), probe=True, disconnect=False):
        self.port = port
        self.commands = []
        self.pending = bytearray()
        self.readings = readings
        self.probe = probe
        self.disconnect = disconnect
        self.reporting = False
        self.closed = False
        self.owner = threading.get_ident()

    @property
    def in_waiting(self):
        return len(self.pending)

    def reset_input_buffer(self):
        self.pending.clear()

    def _report(self, temperature):
        self.pending.extend(f"ok T:{temperature} /30 B:25.0 /0 @:0\n".encode())

    def write(self, payload):
        self.commands.append(payload)
        if payload == b"M105\n" and self.probe:
            self._report(20)
        if payload == b"M155 S1\n":
            self.reporting = True
            for temperature in self.readings:
                self._report(temperature)
        return len(payload)

    def read(self, size):
        if self.reporting and self.disconnect:
            raise serial.SerialException("disconnected")
        if not self.pending:
            time.sleep(0.002)
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data

    def close(self):
        self.closed = True


class ThermostatTests(unittest.TestCase):
    def test_parser_accepts_marlin_targets_signed_values_and_t0(self):
        self.assertEqual(parse_temperature("ok T:30.25 /30 B:24.50 /0 @:0"), (30.25, 24.5))
        self.assertEqual(parse_temperature("T0: -.5 B: +2"), (-0.5, 2))
        for line in ("ok", "T:nan B:20", "T:20", "T:1.2.3 B:20", "T:20 B:2abc"):
            with self.subTest(line=line):
                self.assertIsNone(parse_temperature(line))

    def test_stable_sample_statistics(self):
        stats = RunningStats()
        for value in (1e9 + 1, 1e9 + 2, 1e9 + 3):
            stats.add(value)
        self.assertEqual(stats.snapshot(), {"mean": 1e9 + 2, "min": 1e9 + 1, "max": 1e9 + 3, "std": 1.0})

    def test_invalid_thresholds_are_rejected(self):
        for changes in ({"heat_on": 32}, {"heat_off": 29}, {"target": float("nan")}, {"baudrate": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ThermostatConfig(**changes)

    def run_session(self, directory, factory, count=5, **options):
        samples, messages = [], []
        session = ThermostatSession(ThermostatConfig(port="fake", data_dir=Path(directory), **options))
        session.BOOT_DELAY = session.RECONNECT_DELAY = 0
        session.PROBE_TIMEOUT = 0.02

        def sample(data):
            samples.append(data)
            if len(samples) >= count:
                session.stop_event.set()

        with patch("api.thermostat.serial.Serial", side_effect=factory):
            result = session.run(sample, lambda *args: None, messages.append)
        return result, samples, messages

    def test_hysteresis_csv_statistics_and_shutdown(self):
        devices = []

        def factory(*args, **kwargs):
            device = FakeSerial(*args, **kwargs, readings=(31.5, 30, 29, 30))
            devices.append(device)
            return device

        with tempfile.TemporaryDirectory() as directory:
            result, samples, _ = self.run_session(directory, factory)
            self.assertIsNone(result["error"])
            self.assertEqual([sample["heating"] for sample in samples], [True, False, False, True, True])
            heater_commands = [c for c in devices[0].commands if c.startswith(b"M104")]
            self.assertEqual(heater_commands, [b"M104 S30\n", b"M104 S0\n", b"M104 S30\n", b"M104 S0\n"])
            self.assertEqual(devices[0].commands[-1], b"M155 S0\n")
            self.assertTrue(devices[0].closed)
            with open(result["csv"], newline="") as stream:
                self.assertEqual(len(list(csv.reader(stream))), 6)
            self.assertEqual(result["T"], [20, 31.5, 30, 29, 30])
            self.assertIn("Всего точек: 5", Path(result["stats"]).read_text())

    def test_monitor_only_does_not_control_heater_or_fan(self):
        device = FakeSerial("fake", 115200, 0.2, 0.5, readings=(31.5, 30, 29, 30))
        with tempfile.TemporaryDirectory() as directory:
            self.run_session(directory, lambda *a, **kw: device, control_heater=False)
        self.assertFalse(any(c.startswith((b"M104", b"M106")) for c in device.commands))
        self.assertTrue(device.closed)

    def test_reconnect_preserves_session_and_statistics(self):
        devices = []

        def factory(*args, **kwargs):
            device = FakeSerial(*args, **kwargs, readings=() if not devices else (31,), disconnect=not devices)
            devices.append(device)
            return device

        with tempfile.TemporaryDirectory() as directory:
            result, samples, messages = self.run_session(directory, factory, count=3)
        self.assertEqual(len(devices), 2)
        self.assertEqual(result["count"], 3)
        self.assertEqual([s["count"] for s in samples], [1, 2, 3])
        self.assertTrue(all(d.closed for d in devices))
        self.assertTrue(any("Потеря связи" in message for message in messages))

    def test_autodiscovery_never_heats_an_unverified_device(self):
        stop = threading.Event()
        devices = []

        def factory(*args, **kwargs):
            device = FakeSerial(*args, **kwargs, probe=args[0] == "marlin")
            devices.append(device)
            return device

        with tempfile.TemporaryDirectory() as directory:
            session = ThermostatSession(ThermostatConfig(data_dir=Path(directory)), stop)
            session.BOOT_DELAY = 0
            session.PROBE_TIMEOUT = 0.02
            ports = [SimpleNamespace(device=name, description="CH340") for name in ("other", "marlin")]
            with patch("api.thermostat.list_ports.comports", return_value=ports), patch(
                "api.thermostat.serial.Serial", side_effect=factory
            ):
                result = session.run(lambda sample: stop.set(), lambda *args: None, lambda msg: None)
        self.assertEqual(result["count"], 1)
        self.assertEqual(devices[0].commands, [b"M105\n"])
        self.assertTrue(all(d.closed for d in devices))

    def test_stop_interrupts_reconnect_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = threading.Event()
            session = ThermostatSession(ThermostatConfig(data_dir=Path(directory)), stop)

            def status(message, heating):
                stop.set()

            started = time.monotonic()
            with patch("api.thermostat.list_ports.comports", return_value=[]):
                result = session.run(lambda *a: None, status, lambda *a: None)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
