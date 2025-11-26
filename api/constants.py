from typing import Literal


SOCKET = "Socket"
SERIAL = "Serial"  # left for backward compatibility
ADAPTERS = {
    SOCKET: "api.SocketAdapter",
}

GAIN_TYPES = Literal[0, 1, 2, 3, 4, 5]
GAINS = {
    0: "+/- 6.144 V",
    1: "+/- 4.096 V",
    2: "+/- 2.048 V",
    3: "+/- 1.024 V",
    4: "+/- 0.512 V",
    5: "+/- 0.256 V",
}

WIFI_TYPES = Literal["own", "other"]
WIFI = ["own", "other"]
