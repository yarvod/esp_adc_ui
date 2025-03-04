import argparse
import time
import numpy as np
import h5py
import multiprocessing
from tabulate import tabulate
from api import get_daq_class
from api.exceptions import DeviceError
from api.structures import DAQSampleRate, DAQVoltage, DAQADCChannel
import curses


def save_to_hdf5(filename, data, channels, sample_rate, voltage, duration, averaging, epr):
    with h5py.File(filename, "w") as file:
        data_group = file.create_group("data")
        for i, channel in enumerate(channels):
            data_group.create_dataset(f"channel_{channel}", data=data[i])

        params_group = file.create_group("parameters")
        params_group.attrs["sample_rate"] = sample_rate
        params_group.attrs["voltage"] = voltage
        params_group.attrs["duration"] = duration
        params_group.attrs["averaging"] = averaging
        params_group.attrs["epr"] = epr


def display_table(queue, channels):
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    stdscr.nodelay(True)

    try:
        while True:
            data = queue.get()
            if data is None:
                break

            stdscr.clear()
            table_data = [["Channel", "Time", "Average Data", "Count"]]
            for channel, (duration, average_data, count) in zip(channels, data):
                table_data.append([channel, f"{duration:5.3f}", f"{average_data:8.6f}", count])

            table_str = tabulate(table_data, headers="firstrow", tablefmt="grid")
            stdscr.addstr(0, 0, table_str)
            stdscr.refresh()
            time.sleep(0.1)  # Пауза для обновления консоли
    finally:
        curses.nocbreak()
        stdscr.keypad(False)
        curses.echo()
        curses.endwin()


def main():
    parser = argparse.ArgumentParser(prog="DAQ122 CLI", description="Measuring via DAQ122 ADC", epilog="LOL")
    parser.add_argument(
        "-s",
        "--sample-rate",
        action="store",
        default=DAQSampleRate.SampleRate500.value,
        choices=[sr.value for sr in DAQSampleRate],
        type=int,
    )
    parser.add_argument("-e", "--epr", action="store", default=100, type=int)
    parser.add_argument("-c", "--channel", action="append", choices=list(range(1, 9)), type=int)
    parser.add_argument(
        "-v", "--voltage", action="store", default="Voltage5V", choices=[vt.name for vt in DAQVoltage], type=str
    )
    parser.add_argument("-a", "--average", action="store_true")
    parser.add_argument("-d", "--duration", default=60, type=int)
    parser.add_argument("-o", "--output", default="data.h5", type=str, help="Output HDF5 file")

    args = parser.parse_args()

    voltage = DAQVoltage[args.voltage]
    sample_rate = DAQSampleRate.get_by_value(args.sample_rate)

    data_to_save = [[] for _ in args.channel]

    DAQ122 = get_daq_class()

    queue = multiprocessing.Queue()
    display_process = multiprocessing.Process(target=display_table, args=(queue, args.channel))
    display_process.start()

    try:
        with DAQ122() as daq:
            if daq.is_connected():
                print("Device is connected")

            if daq.configure_sampling_parameters(voltage, sample_rate):
                print("Sampling parameters configured")

            if daq.config_adc_channel(DAQADCChannel.AIN_ALL):
                daq.start_collection()

                count = 0
                start = time.time()

                while True:
                    time.sleep(args.epr / sample_rate / 2)
                    channel_data = []
                    for channel_index, channel in enumerate(args.channel):
                        success, data = daq.read_data(
                            read_elements_count=args.epr, channel_number=channel - 1, timeout=5000
                        )
                        if success:
                            duration = time.time() - start
                            read_data = data[: args.epr]
                            average_data = np.mean(read_data)
                            if args.average:
                                data_to_save[channel_index].append(average_data)
                            else:
                                data_to_save[channel_index].extend(read_data)

                            count += 1
                            channel_data.append((duration, average_data, count))

                            if duration > args.duration:
                                break

                    queue.put(channel_data)
                    if duration > args.duration:
                        break

    except (DeviceError, KeyboardInterrupt) as e:
        print(f"Error: {e}")
    finally:
        queue.put(None)
        display_process.join()
        data_to_save = [np.array(channel_data) for channel_data in data_to_save]
        save_to_hdf5(
            args.output,
            data_to_save,
            args.channel,
            sample_rate.value,
            voltage.name,
            args.duration,
            args.average,
            args.epr,
        )
        print(f"\nData saved to {args.output}")


if __name__ == "__main__":
    main()
