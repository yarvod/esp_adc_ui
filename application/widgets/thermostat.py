import logging
import threading
from collections import deque
from pathlib import Path

import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter
from PyQt5 import QtCore, QtWidgets
from serial.tools import list_ports

from api.thermostat import ThermostatConfig, ThermostatSession


logger = logging.getLogger(__name__)


class TemperatureThread(QtCore.QThread):
    sample = QtCore.pyqtSignal(object)
    status = QtCore.pyqtSignal(str, bool)
    message = QtCore.pyqtSignal(str)
    saved = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.stop_event = threading.Event()
        self.session = ThermostatSession(config, self.stop_event)

    def run(self):
        try:
            result = self.session.run(self.sample.emit, self.status.emit, self.message.emit)
            self.saved.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))

    def stop(self):
        # A queued slot cannot interrupt a blocking acquisition loop.
        self.stop_event.set()


class PortScanThread(QtCore.QThread):
    ports = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)

    def run(self):
        try:
            self.ports.emit([(p.device, p.description) for p in list_ports.comports()])
        except Exception as exc:
            self.error.emit(str(exc))


class GraphSaveThread(QtCore.QThread):
    error = QtCore.pyqtSignal(str)

    def __init__(self, image, path, parent=None):
        super().__init__(parent)
        self.image = image
        self.path = path

    def run(self):
        if not self.image.save(self.path, "PNG"):
            self.error.emit(f"Не удалось сохранить график: {self.path}")


class ThermostatWidget(QtWidgets.QWidget):
    stopped = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = self.scan_thread = self.save_thread = None
        self.export_graph = None
        self.closing = False
        self.result = None
        self.failed = False
        self.controlling_heater = True
        self.dirty = False
        self.times, self.temps_t, self.temps_b = (deque(maxlen=500) for _ in range(3))

        layout = QtWidgets.QHBoxLayout(self)
        readings = QtWidgets.QVBoxLayout()
        self.status = QtWidgets.QLabel("Остановлено", self)
        self.values = QtWidgets.QLabel("T: — °C    B: — °C    Δ: — °C", self)
        self.values.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.summary = QtWidgets.QLabel("Точек: 0 · Нагрев выключен", self)
        readings.addWidget(self.status)
        readings.addWidget(self.values)
        readings.addWidget(self.summary)
        self.graphs = pg.GraphicsLayoutWidget(self)
        self.graphs.setBackground("w")
        self.plot_t = self.graphs.addPlot(row=0, col=0, title="Температура T")
        self.plot_b = self.graphs.addPlot(row=1, col=0, title="Температура B")
        self.curve_t = self._prepare_plot(self.plot_t, "#1f77b4")
        self.curve_b = self._prepare_plot(self.plot_b, "#2ca02c")
        self.plot_b.setXLink(self.plot_t)
        self.graphs.setMinimumHeight(300)
        readings.addWidget(self.graphs, 1)
        self.statistics = QtWidgets.QTableWidget(3, 4, self)
        self.statistics.setHorizontalHeaderLabels(["Мин", "Макс", "Среднее", "σ"])
        self.statistics.setVerticalHeaderLabels(["T", "B", "Δ"])
        self.statistics.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.statistics.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.statistics.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.statistics.setMaximumHeight(130)
        readings.addWidget(self.statistics)
        layout.addLayout(readings, 1)

        panel = QtWidgets.QGroupBox("Термостат / Marlin", self)
        panel.setMaximumWidth(300)
        controls = QtWidgets.QVBoxLayout(panel)
        form = QtWidgets.QFormLayout()
        self.port = QtWidgets.QComboBox(self)
        self.port.setEditable(True)
        self.port.addItem("Авто", "")
        self.port.setToolTip("Автопоиск или имя порта, например COM3 / /dev/cu.usbserial-…")
        self.refresh = QtWidgets.QPushButton("Обновить порты", self)
        self.refresh.clicked.connect(self.refresh_ports)
        self.baudrate = QtWidgets.QComboBox(self)
        self.baudrate.addItems(["115200", "250000", "57600", "9600"])
        self.heat_on = self._temperature(29.5)
        self.heat_off = self._temperature(31.0)
        self.target = self._temperature(30.0)
        self.heater = QtWidgets.QCheckBox("Управлять нагревом", self)
        self.heater.setChecked(True)
        self.heater.setToolTip("При запуске включает управление нагревателем T и вентилятор (M106 S255)")
        self.window = QtWidgets.QSpinBox(self)
        self.window.setRange(50, 10000)
        self.window.setValue(500)
        self.directory = QtWidgets.QLineEdit(str(ThermostatConfig().data_dir), self)
        self.directory.setToolTip("CSV, статистика и полный график PNG сохраняются в эту папку")
        browse = QtWidgets.QPushButton("Выбрать папку…", self)
        browse.clicked.connect(self.choose_directory)
        form.addRow("Порт:", self.port)
        form.addRow(self.refresh)
        form.addRow("Скорость:", self.baudrate)
        form.addRow("Включение, °C:", self.heat_on)
        form.addRow("Выключение, °C:", self.heat_off)
        form.addRow("Цель, °C:", self.target)
        form.addRow(self.heater)
        form.addRow("Точек на графике:", self.window)
        form.addRow("Папка сессий:", self.directory)
        form.addRow(browse)
        controls.addLayout(form)
        self.start_button = QtWidgets.QPushButton("Запустить", self)
        self.stop_button = QtWidgets.QPushButton("Остановить", self)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        note = QtWidgets.QLabel(
            "T — нагреватель, B — стол.\n"
            "Гистерезис работает по T.\n"
            "CSV содержит всю сессию;\nграфики показывают последние точки.",
            self,
        )
        note.setWordWrap(True)
        controls.addWidget(note)
        self.files = QtWidgets.QLabel("", self)
        self.files.setWordWrap(True)
        self.files.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        controls.addWidget(self.files)
        controls.addStretch()
        layout.addWidget(panel, 0, QtCore.Qt.AlignTop)
        self.controls = [
            self.port,
            self.refresh,
            self.baudrate,
            self.heat_on,
            self.heat_off,
            self.target,
            self.heater,
            self.window,
            self.directory,
            browse,
        ]
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.redraw)

    @staticmethod
    def _prepare_plot(plot, color):
        plot.setLabel("left", "Температура", units="°C")
        plot.setLabel("bottom", "Время", units="s")
        # Fixed axis gutters also keep labels aligned in an offscreen PNG export.
        plot.getAxis("left").setWidth(75)
        plot.getAxis("bottom").setHeight(50)
        for axis in ("left", "bottom"):
            plot.getAxis(axis).setTextPen("#413C58")
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.setClipToView(True)
        plot.setDownsampling(auto=True, mode="peak")
        return plot.plot(pen=pg.mkPen(color, width=2))

    def _temperature(self, value):
        field = QtWidgets.QDoubleSpinBox(self)
        field.setRange(0, 300)
        field.setDecimals(2)
        field.setSingleStep(0.1)
        field.setValue(value)
        return field

    def choose_directory(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Папка сессий", self.directory.text())
        if path:
            self.directory.setText(path)

    def refresh_ports(self):
        if not self._is_idle() or self.closing:
            return
        self.refresh.setEnabled(False)
        self.scan_thread = PortScanThread(self)
        self.scan_thread.ports.connect(self._ports_ready)
        self.scan_thread.error.connect(self._error)
        self.scan_thread.finished.connect(self._scan_finished)
        self.scan_thread.start()

    @QtCore.pyqtSlot(object)
    def _ports_ready(self, ports):
        if self.closing or self.thread is not None:
            return
        selected = self.port.currentData() or self.port.currentText()
        self.port.clear()
        self.port.addItem("Авто", "")
        for device, description in ports:
            self.port.addItem(f"{device} ({description})", device)
        index = self.port.findData(selected)
        if index >= 0:
            self.port.setCurrentIndex(index)
        elif selected != "Авто":
            self.port.setEditText(selected)

    @QtCore.pyqtSlot()
    def _scan_finished(self):
        self.scan_thread.deleteLater()
        self.scan_thread = None
        self.refresh.setEnabled(self.thread is None and self.save_thread is None and not self.closing)
        self._notify_stopped()

    def start(self):
        if self.thread is not None or self.save_thread is not None or self.export_graph is not None or self.closing:
            return
        text = self.port.currentText().strip()
        port = self.port.currentData() if text == self.port.itemText(self.port.currentIndex()) else text
        try:
            if not self.directory.text().strip():
                raise ValueError("Выберите папку сессий")
            config = ThermostatConfig(
                port=port or "",
                baudrate=int(self.baudrate.currentText()),
                heat_on=self.heat_on.value(),
                heat_off=self.heat_off.value(),
                target=self.target.value(),
                control_heater=self.heater.isChecked(),
                data_dir=Path(self.directory.text()).expanduser(),
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Параметры термостата", str(exc))
            return
        self.times, self.temps_t, self.temps_b = (deque(maxlen=self.window.value()) for _ in range(3))
        self.curve_t.setData([], [])
        self.curve_b.setData([], [])
        self.statistics.clearContents()
        self.values.setText("T: — °C    B: — °C    Δ: — °C")
        self.summary.setText("Точек: 0 · Нагрев выключен")
        self.files.clear()
        self.result = None
        self.failed = False
        self.controlling_heater = config.control_heater
        self._set_running(True)
        self.status.setText("Поиск устройства…")
        self.thread = TemperatureThread(config, self)
        self.thread.sample.connect(self._sample)
        self.thread.status.connect(self._status)
        self.thread.message.connect(self._message)
        self.thread.saved.connect(self._saved)
        self.thread.error.connect(self._error)
        self.thread.finished.connect(self._finished)
        self.timer.start()
        self.thread.start()

    def _set_running(self, running):
        for control in self.controls:
            control.setEnabled(not running and not self.closing)
        self.refresh.setEnabled(not running and self.scan_thread is None and not self.closing)
        self.start_button.setEnabled(not running and not self.closing)
        self.stop_button.setEnabled(running and not self.closing)

    def stop(self):
        if self.thread is not None:
            self.thread.stop()
            self.stop_button.setEnabled(False)
            self.status.setText("Остановка и сохранение…")

    @QtCore.pyqtSlot(object)
    def _sample(self, sample):
        self.times.append(sample["elapsed"])
        self.temps_t.append(sample["T"])
        self.temps_b.append(sample["B"])
        self.dirty = True
        t, b = sample["T"], sample["B"]
        self.values.setText(f"T: {t:.2f} °C    B: {b:.2f} °C    Δ: {t - b:+.2f} °C")
        heating = "Нагрев включён" if sample["heating"] else "Нагрев выключен"
        if not self.controlling_heater:
            heating = "Только мониторинг"
        self.summary.setText(f"Точек: {sample['count']} · {sample['elapsed']:.0f} с · {heating}")
        for row, name in enumerate(("T", "B", "delta")):
            for col, key in enumerate(("min", "max", "mean", "std")):
                self.statistics.setItem(row, col, QtWidgets.QTableWidgetItem(f"{sample['stats'][name][key]:.3f}"))

    @QtCore.pyqtSlot()
    def redraw(self):
        if self.dirty:
            self.curve_t.setData(list(self.times), list(self.temps_t))
            self.curve_b.setData(list(self.times), list(self.temps_b))
            self.dirty = False

    @QtCore.pyqtSlot(str, bool)
    def _status(self, message, heating):
        if self.thread is not None and not self.thread.stop_event.is_set():
            self.status.setText(message)
            if self.controlling_heater:
                state = "Нагрев выключен" if message.startswith("Подключено:") else "Нагрев: нет связи"
                self.summary.setText(self.summary.text().rsplit(" · ", 1)[0] + " · " + state)

    @QtCore.pyqtSlot(str)
    def _message(self, message):
        logger.info(message)

    @QtCore.pyqtSlot(str)
    def _error(self, message):
        self.failed = True
        self.status.setText(f"Ошибка: {message}")
        logger.error(message)

    @QtCore.pyqtSlot(object)
    def _saved(self, result):
        self.result = result
        if result["error"]:
            self._error(result["error"])
        self.files.setText(f"CSV: {result['csv']}\nСтатистика: {result['stats']}")

    @QtCore.pyqtSlot()
    def _finished(self):
        self.timer.stop()
        self.redraw()
        self.thread.deleteLater()
        self.thread = None
        if self.result and self.result["times"]:
            graph = None
            try:
                # Qt scenes are rendered only in the GUI thread; PNG encoding runs separately.
                graph = pg.GraphicsLayoutWidget()
                graph.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
                graph.setBackground("w")
                graph.resize(1200, 720)
                for row, name, color in ((0, "T", "#1f77b4"), (1, "B", "#2ca02c")):
                    plot = graph.addPlot(row=row, col=0, title=f"Температура {name} — вся сессия")
                    self._prepare_plot(plot, color).setData(self.result["times"], self.result[name])
                    plot.autoRange()
                self.export_graph = graph
                graph.show()
                self.stop_button.setEnabled(False)
                if not self.failed:
                    self.status.setText("Сохранение полного графика…")
                # Let Qt settle text and layout without pumping events inside this slot.
                QtCore.QTimer.singleShot(10, self._save_graph)
                return
            except Exception as exc:
                self._error(f"Не удалось построить PNG: {exc}")
                if graph is not None:
                    graph.close()
                    graph.deleteLater()
                self.export_graph = None
        self._complete()

    @QtCore.pyqtSlot()
    def _save_graph(self):
        try:
            # Device caches from the hidden view cannot be reused by the export painter.
            for item in self.export_graph.scene().items():
                item.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
            exporter = ImageExporter(self.export_graph.ci)
            exporter.parameters()["width"] = 1200
            image = exporter.export(toBytes=True)
            self.save_thread = GraphSaveThread(image, self.result["png"], self)
            self.save_thread.error.connect(self._error)
            self.save_thread.finished.connect(self._graph_saved)
            self.save_thread.start()
        except Exception as exc:
            self._error(f"Не удалось построить PNG: {exc}")
        finally:
            self.export_graph.close()
            self.export_graph.deleteLater()
            self.export_graph = None
        if self.save_thread is None:
            self._complete()

    @QtCore.pyqtSlot()
    def _graph_saved(self):
        if not self.failed:
            self.files.setText(self.files.text() + f"\nГрафик: {self.result['png']}")
            logger.info("График сессии: %s", self.result["png"])
        self.save_thread.deleteLater()
        self.save_thread = None
        self._complete()

    def _complete(self):
        self._set_running(False)
        state = "Управление нагревом завершено" if self.controlling_heater else "Только мониторинг"
        self.summary.setText(self.summary.text().rsplit(" · ", 1)[0] + " · " + state)
        if not self.failed:
            self.status.setText("Остановлено · сессия сохранена")
        # Release complete-session arrays after exporting the image.
        if self.result:
            for key in ("times", "T", "B"):
                self.result[key] = []
        self._notify_stopped()

    def _notify_stopped(self):
        if self._is_idle():
            self.stopped.emit()

    def _is_idle(self):
        return all(worker is None for worker in (self.thread, self.scan_thread, self.save_thread, self.export_graph))

    def shutdown(self):
        self.closing = True
        self._set_running(self.thread is not None)
        self.stop()
        return self._is_idle()
