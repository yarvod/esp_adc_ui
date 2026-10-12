import argparse
import time
from datetime import datetime, timezone

from api import EspAdc


def main():
    parser = argparse.ArgumentParser(description="Monitor ESP32 over HTTP")
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--rps", type=float, default=5)
    args = parser.parse_args()
    if args.rps <= 0:
        parser.error("--rps must be positive")
    with EspAdc(host=args.host, port=args.port) as device:
        device.get_status()
        try:
            while True:
                timestamp_ms, values = device.read_sample()
                local = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).astimezone()
                print(
                    f"{local.isoformat(timespec='milliseconds')}: "
                    + "; ".join(f"ADC{i}: {value:.1f} mV" for i, value in enumerate(values))
                )
                time.sleep(1 / args.rps)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
