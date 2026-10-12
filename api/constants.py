from typing import Literal


HTTP = "HTTP"
DEVICE_MAC = "68:09:47:9f:21:2c"
DEVICE_AP_MAC = "68:09:47:9f:21:2d"
ADAPTERS = {
    HTTP: "api.HttpAdapter",
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

WIFI_TYPES = Literal["own", "other", "both"]
WIFI = ["own", "other", "both"]
WIFI_LABELS = {
    "own": "Собственная сеть",
    "other": "Подключение к роутеру",
    "both": "Сеть ESP + роутер",
}
