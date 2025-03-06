import logging
import re
from typing import Tuple, Optional

from api.base import BaseInstrument

logger = logging.getLogger(__name__)


class EspAdc(BaseInstrument):
    """
    A class to interface with the ESP ADC data acquisition system.
    """

    def read_data(self) -> Optional[Tuple[float, float]]:
        response = self.query("adc")
        reg = re.compile(f"ADC01:\s*([\d.-]+)\s*mV;ADC23:\s*([\d.-]+)\s*mV;")
        try:
            parsed = re.findall(reg, response)[0]
            return float(parsed[0]), float(parsed[1])
        except (IndexError, ValueError):
            return None
