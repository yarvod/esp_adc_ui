from typing import List, Dict
from datetime import datetime, timezone

from PyQt5 import QtWidgets, QtCore


class MonitorGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__(parent)
        self.setTitle("Monitor")

        hlayout = QtWidgets.QHBoxLayout()
        glayout_ai1 = QtWidgets.QGridLayout()
        glayout_ai2 = QtWidgets.QGridLayout()
        glayout_ai3 = QtWidgets.QGridLayout()
        glayout_timer = QtWidgets.QGridLayout()

        self.ai1_label = QtWidgets.QLabel("<h3>AI1, mV</h3>", self)
        self.ai1 = QtWidgets.QLabel("<h3>N\A</h3>", self)
        glayout_ai1.addWidget(self.ai1_label, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        glayout_ai1.addWidget(self.ai1, 1, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        self.ai2_label = QtWidgets.QLabel("<h3>AI2, mV</h3>", self)
        self.ai2 = QtWidgets.QLabel("<h3>N\A</h3>", self)
        glayout_ai2.addWidget(self.ai2_label, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        glayout_ai2.addWidget(self.ai2, 1, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        self.ai3_label = QtWidgets.QLabel("<h3>AI3, mV</h3>", self)
        self.ai3 = QtWidgets.QLabel("<h3>N\A</h3>", self)
        glayout_ai3.addWidget(self.ai3_label, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        glayout_ai3.addWidget(self.ai3, 1, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        self.timer_label = QtWidgets.QLabel("<h3>Timer, s</h3>", self)
        self.timer = QtWidgets.QLabel("<h3>N\A</h3>", self)
        glayout_timer.addWidget(self.timer_label, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        glayout_timer.addWidget(self.timer, 1, 0, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        hlayout.addLayout(glayout_ai1)
        hlayout.addSpacing(20)
        hlayout.addLayout(glayout_ai2)
        hlayout.addSpacing(20)
        hlayout.addLayout(glayout_ai3)
        hlayout.addSpacing(20)
        hlayout.addLayout(glayout_timer)
        hlayout.addStretch()

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(hlayout)
        self.rtc_time = QtWidgets.QLabel("Время измерения RTC: —", self)
        layout.addWidget(self.rtc_time)
        self.setLayout(layout)

    def add_data(self, data: List[Dict]):
        for dat in data:
            ai = getattr(self, f"ai{dat['channel']}")
            ai.setText(f"<h3>{dat['voltage']:.2f}</h3>")

        self.timer.setText(f"<h3>{data[0]['time']:.2f}</h3>")
        timestamp_ms = data[0].get("timestamp_ms")
        if timestamp_ms:
            local = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).astimezone()
            self.rtc_time.setText(f"Время измерения RTC: {local.strftime('%d.%m.%Y %H:%M:%S %Z')}")

    def reset_values(self):
        for i in range(1, 4):
            ai = getattr(self, f"ai{i}")
            ai.setText("<h3>N\A</h3>")

        self.timer.setText("<h3>N\A</h3>")
        self.rtc_time.setText("Время измерения RTC: —")
