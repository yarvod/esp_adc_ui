import time

from api import get_daq_class
from api.structures import DAQVoltage, DAQSampleRate, DAQADCChannel

# Example usage with context management
if __name__ == "__main__":
    try:
        DAQ122 = get_daq_class()
        with DAQ122() as daq:
            if daq.is_connected():
                print("Device is connected")

            if daq.configure_sampling_parameters(DAQVoltage.Voltage5V, DAQSampleRate.SampleRate500):
                print("Sampling parameters configured")

            if daq.config_adc_channel(DAQADCChannel.AIN_ALL):
                daq.start_collection()
                time.sleep(1)  # Wait for data to accumulate

                count = 0
                start = time.time()

                while True:

                    success, data = daq.read_data(read_elements_count=500, channel_number=0, timeout=5000)
                    if success:
                        read_data = list(data)
                        pressure = read_data[0]

                        count += 1
                        duration = time.time() - start
                        print(f"\r {duration:5.3f}: {pressure:8.6f}  {count} ", end="")
                        if duration > 360:
                            break

    except (Exception, KeyboardInterrupt) as e:
        print(f"Exception: {e}")
