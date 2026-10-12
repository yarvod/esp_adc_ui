"""Marlin temperature acquisition; no Qt widgets or plotting in this module."""

import csv
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
TEMPERATURE = re.compile(rf"(?:^|\s)T(?:0)?:\s*({NUMBER})(?![\w.]).*?\bB:\s*({NUMBER})(?![\w.])")


def parse_temperature(line):
    match = TEMPERATURE.search(line)
    if not match:
        return None
    values = tuple(float(value) for value in match.groups())
    return values if all(math.isfinite(value) for value in values) else None


@dataclass(frozen=True)
class ThermostatConfig:
    port: str = ""
    baudrate: int = 115200
    heat_on: float = 29.5
    heat_off: float = 31.0
    target: float = 30.0
    control_heater: bool = True
    data_dir: Path = Path.home() / "termostat" / "data"

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.heat_on, self.heat_off, self.target)):
            raise ValueError("Температуры должны быть конечными числами")
        if not 0 <= self.heat_on < self.target <= self.heat_off:
            raise ValueError("Нужны пороги: 0 ≤ включение < цель ≤ выключение")
        if self.baudrate <= 0:
            raise ValueError("Скорость порта должна быть положительной")


class RunningStats:
    """Welford's algorithm keeps variance stable in long sessions."""

    def __init__(self):
        self.count = 0
        self.mean = self.m2 = 0.0
        self.minimum = math.inf
        self.maximum = -math.inf

    def add(self, value):
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)

    def snapshot(self):
        return {
            "mean": self.mean,
            "min": self.minimum if self.count else None,
            "max": self.maximum if self.count else None,
            "std": math.sqrt(max(0.0, self.m2 / (self.count - 1))) if self.count > 1 else 0.0,
        }


class ThermostatSession:
    BOOT_DELAY = 2.0
    PROBE_TIMEOUT = 3.0
    RECONNECT_DELAY = 5.0
    STALE_TIMEOUT = 6.0

    def __init__(self, config, stop_event=None):
        self.config = config
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.connection = None
        self.heating = False
        self.configured = False
        self.pending = b""
        self.stats = {name: RunningStats() for name in ("T", "B", "delta")}

    def _write(self, command):
        payload = (command + "\n").encode("ascii")
        if self.connection.write(payload) != len(payload):
            raise serial.SerialTimeoutException("Команда передана не полностью")

    def _read_lines(self):
        chunk = self.connection.read(min(self.connection.in_waiting or 1, 4096))
        self.pending += chunk
        lines = self.pending.split(b"\n")
        self.pending = lines.pop()
        if len(self.pending) > 8192:
            self.pending = b""
        return [line.decode("utf-8", errors="replace").strip() for line in lines]

    def _disconnect(self, on_log):
        if self.connection is None:
            return
        try:
            if self.configured:
                commands = ["M104 S0"] if self.config.control_heater else []
                commands.append("M155 S0")
                for command in commands:
                    try:
                        self._write(command)
                    except (serial.SerialException, OSError) as exc:
                        on_log(f"Не удалось отправить {command}: {exc}")
        finally:
            try:
                self.connection.close()
            except (serial.SerialException, OSError) as exc:
                on_log(f"Ошибка закрытия порта: {exc}")
            self.connection = None
            self.pending = b""
            self.heating = self.configured = False

    def _connect(self, on_status, on_log):
        if self.config.port:
            ports = [self.config.port]
        else:
            candidates = list(list_ports.comports())
            candidates.sort(
                key=lambda p: not any(hint in (p.description or "").lower() for hint in ("ch340", "arduino", "marlin"))
            )
            ports = [p.device for p in candidates]
        for port in ports:
            if self.stop_event.is_set():
                break
            on_status(f"Проверка {port}…", False)
            try:
                self.connection = serial.Serial(port, self.config.baudrate, timeout=0.2, write_timeout=0.5)
                if self.stop_event.wait(self.BOOT_DELAY):
                    self._disconnect(on_log)
                    return None
                self.connection.reset_input_buffer()
                self._write("M105")
                deadline = time.monotonic() + self.PROBE_TIMEOUT
                while not self.stop_event.is_set() and time.monotonic() < deadline:
                    for line in self._read_lines():
                        reading = parse_temperature(line)
                        if reading is not None:
                            # Mark ownership before commands so partial setup is also cleaned up.
                            self.configured = True
                            self._write("M155 S1")
                            if self.config.control_heater:
                                self._write("M106 S255")
                            on_log(f"Термостат подключён: {port}")
                            on_status(f"Подключено: {port}", False)
                            return reading
                on_log(f"{port}: нет ответа с температурами T и B")
            except (serial.SerialException, OSError) as exc:
                on_log(f"{port}: {exc}")
            self._disconnect(on_log)
        return None

    def _sample(self, reading, writer, stream, started, on_sample):
        t, b = reading
        if self.config.control_heater:
            if t >= self.config.heat_off and self.heating:
                self._write("M104 S0")
                self.heating = False
            elif t <= self.config.heat_on and not self.heating:
                self._write(f"M104 S{self.config.target:g}")
                self.heating = True
        elapsed = time.monotonic() - started
        try:
            writer.writerow((datetime.now().isoformat(), f"{t:.3f}", f"{b:.3f}"))
            stream.flush()
        except OSError as exc:
            # Disk failures end the session, rather than being mistaken for a lost port.
            raise RuntimeError(f"Не удалось записать CSV: {exc}") from exc
        for name, value in (("T", t), ("B", b), ("delta", t - b)):
            self.stats[name].add(value)
        on_sample(
            {
                "elapsed": elapsed,
                "T": t,
                "B": b,
                "heating": self.heating,
                "count": self.stats["T"].count,
                "stats": {name: stat.snapshot() for name, stat in self.stats.items()},
            }
        )

    def run(self, on_sample, on_status, on_log):
        directory = Path(self.config.data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        csv_path = directory / f"data_{session_id}.csv"
        stats_path = directory / f"stats_{session_id}.txt"
        started = time.monotonic()
        error = None
        try:
            with csv_path.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("timestamp", "temp_T", "temp_B"))
                stream.flush()
                on_log(f"CSV сессии: {csv_path}")
                while not self.stop_event.is_set():
                    reading = self._connect(on_status, on_log)
                    if reading is None:
                        on_status("Ожидание устройства; повтор через 5 с", False)
                        self.stop_event.wait(self.RECONNECT_DELAY)
                        continue
                    try:
                        self._sample(reading, writer, stream, started, on_sample)
                        last_sample = last_poll = time.monotonic()
                        while not self.stop_event.is_set():
                            for line in self._read_lines():
                                reading = parse_temperature(line)
                                if reading is not None:
                                    self._sample(reading, writer, stream, started, on_sample)
                                    last_sample = time.monotonic()
                            now = time.monotonic()
                            # M105 also supports firmware without M155 auto reporting.
                            if now - last_sample >= 1.5 and now - last_poll >= 1.0:
                                self._write("M105")
                                last_poll = now
                            if now - last_sample > self.STALE_TIMEOUT:
                                raise serial.SerialException("Устройство перестало передавать температуру")
                    except (serial.SerialException, OSError) as exc:
                        on_log(f"Потеря связи: {exc}")
                        self._disconnect(on_log)
                        on_status("Потеря связи; переподключение…", False)
                        self.stop_event.wait(self.RECONNECT_DELAY)
        except Exception as exc:
            error = str(exc)
            on_log(f"Ошибка сессии: {exc}")
        finally:
            self._disconnect(on_log)
        count = self.stats["T"].count
        duration = time.monotonic() - started
        try:
            with stats_path.open("w", encoding="utf-8") as stream:
                stream.write(f"Сессия: {session_id}\nВсего точек: {count}\nДлительность: {duration:.1f} сек\n")
                for name, stat in self.stats.items():
                    if stat.count:
                        s = stat.snapshot()
                        stream.write(
                            f"{name}: мин={s['min']:.3f} макс={s['max']:.3f} " f"ср={s['mean']:.3f} σ={s['std']:.4f}\n"
                        )
                if error:
                    stream.write(f"Ошибка: {error}\n")
            # Read history off the GUI thread; the display window does not limit export.
            times, tt, tb = [], [], []
            with csv_path.open(encoding="utf-8", newline="") as stream:
                rows = csv.reader(stream)
                next(rows, None)
                first = None
                for row in rows:
                    stamp = datetime.fromisoformat(row[0])
                    if first is None:
                        first = stamp
                    times.append((stamp - first).total_seconds())
                    tt.append(float(row[1]))
                    tb.append(float(row[2]))
        except (OSError, ValueError) as exc:
            on_log(f"Ошибка сохранения результатов: {exc}")
            error = error or str(exc)
            times, tt, tb = [], [], []
        return {
            "csv": str(csv_path),
            "stats": str(stats_path),
            "png": str(directory / f"full_graph_{session_id}.png"),
            "times": times,
            "T": tt,
            "B": tb,
            "count": count,
            "error": error,
        }
