from api import EspAdc


def download_file_from_esp(host, port, file_name, output_file):
    with EspAdc(host=host, port=port) as device:
        ok, message = device.download_file(file_name, dest_path=output_file)
        print(message)
        return ok


if __name__ == "__main__":
    download_file_from_esp("192.168.4.1", 80, "test.txt", "test.txt")
