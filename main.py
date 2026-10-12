import sys
import logging
import os
from pathlib import Path

# Portable builds keep assets and settings beside the executable, regardless of
# the shortcut's working directory. Set this before importing QSettings consumers.
if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).resolve().parent)

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
    if os.environ.get("ESP_ADC_SMOKE_TEST") == "1":
        # CI starts the frozen runtime from a different working directory.
        from PyQt5.QtCore import QTimer
        from application.version import __version__
        from application.updates import current_arch

        assert __version__ == os.environ["ESP_ADC_EXPECTED_VERSION"]
        assert current_arch() == os.environ["ESP_ADC_EXPECTED_ARCH"]
        assert not ex.windowIcon().isNull()
        QTimer.singleShot(500, ex.close)
    sys.exit(app.exec())
