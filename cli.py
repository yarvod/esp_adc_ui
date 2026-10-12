"""ESP ADC HTTP CLI: live readings and text-file recording on the device SD card."""

import argparse
import sys
import time

from api import EspAdc
from api.exceptions import DeviceApiError


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("--port", type=int, default=80)
    commands = parser.add_subparsers(dest="command", required=True)
    monitor = commands.add_parser("monitor", help="Print live readings without saving locally")
    monitor.add_argument("--duration", type=float, default=60)
    monitor.add_argument("--rps", type=float, default=5)
    start = commands.add_parser("start", help="Start recording a text file on the ESP SD card")
    start.add_argument("--filename", default="", help="Optional SD filename; empty means automatic naming")
    commands.add_parser("stop", help="Stop recording on the ESP SD card")
    commands.add_parser("status", help="Show SD recording status")
    commands.add_parser("files", help="List files on the ESP SD card")
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.command == "monitor" and not (args.duration > 0 and 0 < args.rps <= 100):
        parser.error("duration must be positive and rps must be in (0, 100]")
    try:
        with EspAdc(host=args.host, port=args.port) as device:
            if args.command == "start":
                print(device.start_record(args.filename))
            elif args.command == "stop":
                print(device.stop_record())
            elif args.command == "status":
                print(device.check_recording_status())
            elif args.command == "files":
                for item in device.get_files():
                    print(f"{item['name']}  {item['size']} bytes")
            else:
                deadline = time.monotonic() + args.duration
                print("timestamp_ms  AI1_mV  AI2_mV  AI3_mV")
                while time.monotonic() < deadline:
                    try:
                        timestamp, values = device.read_sample()
                        print(f"{timestamp}  " + "  ".join(f"{value:.6f}" for value in values))
                    except DeviceApiError as error:
                        if error.status != 503 or error.code != "not_ready":
                            raise
                    time.sleep(1 / args.rps)
        return 0
    except KeyboardInterrupt:
        return 0
    except (DeviceApiError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
