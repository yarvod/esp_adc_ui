from typing import List, Dict

from PyQt5 import QtWidgets
import pyqtgraph as pg

from store.state import State


class PlotWidget(QtWidgets.QWidget):
    colors = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
    ]

    def __init__(self, parent):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        self.plot = pg.PlotWidget(self)
        self.prepare_plot()

        layout.addWidget(self.plot)
        self.setLayout(layout)

    def prepare_plot(self):
        x_label = "Time, s"
        y_label = "Voltage, mV"
        self.plot.setBackground("w")
        styles = {"color": "#413C58", "font-size": "15px"}
        self.plot.setLabel("left", y_label, **styles)
        self.plot.setLabel("bottom", x_label, **styles)
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True)

    def clear(self):
        self.plot.clear()

    def get_plot_items(self):
        plot_item = self.plot.getPlotItem()
        return {item.name(): item for item in plot_item.items}

    def add_plots(self, data: List[Dict]):
        items = self.get_plot_items()
        for dat in data:
            graph_id = f"AI{dat['channel']}"
            if items.get(graph_id):
                item = items.get(graph_id)
                x_data = list(item.xData)
                x_data.append(dat["time"])
                y_data = list(item.yData)
                y_data.append(dat["voltage"])
                if len(x_data) > State.plot_window:
                    del x_data[0]
                    del y_data[0]
                item.setData(x_data, y_data)
                continue

            pen = pg.mkPen(color=self.colors[dat["channel"] - 1], width=2)
            self.plot.plot(
                [dat["time"]], [dat["voltage"]], name=f"{graph_id}", pen=pen, symbolSize=6, symbolBrush=pen.color()
            )
