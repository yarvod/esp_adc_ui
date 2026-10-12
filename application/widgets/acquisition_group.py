import logging

from PyQt5 import QtCore, QtWidgets

from api import EspAdc
from api.constants import GAINS
from store.state import State

logger = logging.getLogger(__name__)


class ConfigThread(QtCore.QThread):
    result = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)

    def __init__(self, parent, value=None):
        super().__init__(parent)
        self.value = value
        self.connection = dict(host=State.host, port=State.port, adapter=State.adapter)

    def run(self):
        try:
            with EspAdc(**self.connection) as device:
                config = device.get_config() if self.value is None else device.set_config(self.value)
                self.result.emit(config)
        except Exception as exc:
            self.error.emit(str(exc))


class AcquisitionGroup(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None
        self.loaded = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(2)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.fields = {}
        specifications = [
            ("sample_interval_ms", "Опрос трёх каналов, мс", 10, 60000, 10, 10),
            ("average_samples", "Измерений в среднем", 1, 1000, 1, 1),
            ("record_every", "Записывать каждую N-ю точку", 1, 10000, 1, 1),
            ("flush_samples", "Сброс на SD после N точек", 1, 860, 1, 860),
            ("flush_interval_ms", "Или через время, мс", 100, 60000, 10, 1000),
            ("rtc_poll_interval_ms", "Чтение часов, мс", 20, 200, 10, 20),
        ]
        for key, label, low, high, step, default in specifications:
            field = QtWidgets.QSpinBox(self)
            field.setRange(low, high)
            field.setSingleStep(step)
            field.setValue(default)
            self.fields[key] = field
            form.addRow(label, field)
            field.valueChanged.connect(self.update_summary)
        alpha = QtWidgets.QDoubleSpinBox(self)
        alpha.setRange(0.01, 1.0)
        alpha.setDecimals(2)
        alpha.setSingleStep(0.05)
        alpha.setValue(0.25)
        alpha.setToolTip("После среднего по N измерениям: меньше α — сильнее сглаживание; 1 — без EMA.")
        alpha.valueChanged.connect(self.update_summary)
        self.fields["ema_alpha"] = alpha
        form.insertRow(2, "Сглаживание EMA, α (1 = выкл.)", alpha)
        self.gain = QtWidgets.QComboBox(self)
        self.gain.addItems(GAINS.values())
        self.gain.setCurrentIndex(1)
        form.insertRow(0, "Диапазон АЦП", self.gain)
        layout.addLayout(form)
        self.summary = QtWidgets.QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.status = QtWidgets.QLabel("Сначала прочитайте настройки ESP.", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QtWidgets.QHBoxLayout()
        self.btn_read = QtWidgets.QPushButton("Прочитать с ESP", self)
        self.btn_apply = QtWidgets.QPushButton("Применить и сохранить", self)
        self.btn_read.clicked.connect(lambda: self.execute(False))
        self.btn_apply.clicked.connect(lambda: self.execute(True))
        self.btn_apply.setEnabled(False)
        row.addWidget(self.btn_read)
        row.addWidget(self.btn_apply)
        layout.addLayout(row)
        note = QtWidgets.QLabel(
            "Настройки хранятся во внутренней флеш-памяти ESP. Перед изменением остановите запись на SD.", self
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.update_summary()

    def values(self):
        return {**{key: field.value() for key, field in self.fields.items()}, "gain": self.gain.currentIndex()}

    def update_summary(self):
        if not hasattr(self, "summary"):
            return
        values = self.values()
        period = values["sample_interval_ms"] * values["average_samples"] / 1000
        recorded = period * values["record_every"]
        self.summary.setText(
            f"Расчётно: усреднённая точка каждые {period:g} с; запись каждые {recorded:g} с. "
            "Сброс на SD — по числу точек или времени, что раньше."
        )
        self.summary.setToolTip("Метка времени усреднённой точки — время последнего измерения в окне.")

    def execute(self, save):
        if self.thread and self.thread.isRunning():
            return
        self.thread = ConfigThread(self, self.values() if save else None)
        self.thread.result.connect(lambda config: self.show_config(config, save))
        self.thread.error.connect(self.show_error)
        self.thread.finished.connect(lambda: self.set_busy(False))
        self.set_busy(True)
        self.thread.start()

    def set_busy(self, busy):
        self.btn_read.setEnabled(not busy)
        self.btn_apply.setEnabled(not busy and self.loaded)
        self.gain.setEnabled(not busy)
        for field in self.fields.values():
            field.setEnabled(not busy)

    def show_config(self, config, saved=False):
        for key, field in self.fields.items():
            field.setValue(config[key])
        self.gain.setCurrentIndex(config["gain"])
        State.gain = config["gain"]
        self.loaded = True
        self.status.setText("Применено и сохранено в ESP." if saved else "Прочитаны действующие настройки ESP.")
        self.update_summary()
        logger.info(self.status.text())

    def show_error(self, message):
        self.status.setText(message)
        logger.error("Настройки ESP: %s", message)
