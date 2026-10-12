from api import EspAdc


if __name__ == "__main__":
    with EspAdc(host="192.168.4.1", port=80) as device:
        print(device.start_record())
