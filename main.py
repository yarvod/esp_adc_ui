import sys
import logging

from PyQt5.QtWidgets import QApplication

from application import App

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
        ],
    )
    app = QApplication(sys.argv)
    ex = App()
    sys.exit(app.exec())
