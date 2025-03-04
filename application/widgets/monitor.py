from typing import List, Dict

from PyQt5 import QtWidgets, QtCore


class MonitorGroup(QtWidgets.QGroupBox):
    def __init__(self, parent):
        super().__init__(parent)
        self.setTitle("Monitor")

        vlayout = QtWidgets.QVBoxLayout()
        glayout = QtWidgets.QGridLayout()
        flayout = QtWidgets.QFormLayout()

        for i in range(2):
            glayout.addWidget(QtWidgets.QLabel(f"AI{i+1}", self), 0, i)
            ai = QtWidgets.QLabel("", self)
            setattr(self, f"ai{i+1}", ai)
            glayout.addWidget(ai, 1, i)

        self.timer = QtWidgets.QLabel("", self)
        flayout.addRow("Timer:", self.timer)
        flayout.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        vlayout.addLayout(glayout)
        vlayout.addLayout(flayout)

        self.setLayout(vlayout)

    def add_data(self, data: List[Dict]):
        for dat in data:
            ai = getattr(self, f"ai{dat['channel']}")
            ai.setText(f"{dat['voltage']:.5f}")

        self.timer.setText(f"{data[0]['time']:.3f}")

    def reset_values(self):
        for i in range(1, 3):
            ai = getattr(self, f"ai{i}")
            ai.setText("")

        self.timer.setText("")
