#include "hardware/adc.h"
#include "board_config.h"
#include "driver/i2c.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "esp_log.h"

namespace hardware {
bool Adc::configure_bus() {
    i2c_config_t config{};
    config.mode = I2C_MODE_MASTER;
    config.sda_io_num = board::ADC_SDA;
    config.scl_io_num = board::ADC_SCL;
    config.sda_pullup_en = GPIO_PULLUP_ENABLE;
    config.scl_pullup_en = GPIO_PULLUP_ENABLE;
    config.master.clk_speed = board::I2C_FREQUENCY;
    bus_ready_ = i2c_param_config(I2C_NUM_0, &config) == ESP_OK &&
                 i2c_driver_install(I2C_NUM_0, config.mode, 0, 0, 0) == ESP_OK;
    if (bus_ready_) i2c_set_timeout(I2C_NUM_0, 0xffff);
    return bus_ready_;
}
bool Adc::read_register(uint8_t address, uint16_t &value) {
    uint8_t data[2];
    if (i2c_master_write_read_device(I2C_NUM_0, board::ADC_ADDRESS, &address, 1,
                                    data, sizeof(data), pdMS_TO_TICKS(20)) != ESP_OK) return false;
    value = (static_cast<uint16_t>(data[0]) << 8) | data[1];
    return true;
}
bool Adc::probe() {
    uint16_t value;
    ready_ = bus_ready_ && read_register(0, value);
    return ready_;
}
void Adc::initialize() {
    platform::Lock lock(mutex_);
    configure_bus();
    probe();
    ESP_LOGI("adc", "ADS1115 %s (SDA=%d, SCL=%d)", ready_ ? "ready" : "unavailable", board::ADC_SDA, board::ADC_SCL);
}
daq::Result Adc::set_gain(int value) {
    if (value < 0 || value > 5) return daq::failure(daq::Error::InvalidArgument, "Gain must be an integer 0..5");
    platform::Lock lock(mutex_);
    gain_ = value;
    return {};
}
bool Adc::read_channel(int channel, int16_t &value) {
    const uint16_t config = 0x8000 | ((0x04 + channel) << 12) | (gain_.load() << 9) | 0x0100 | (7 << 5) | 3;
    const uint8_t data[] = {1, static_cast<uint8_t>(config >> 8), static_cast<uint8_t>(config)};
    if (i2c_master_write_to_device(I2C_NUM_0, board::ADC_ADDRESS, data, sizeof(data), pdMS_TO_TICKS(20)) != ESP_OK) return false;
    const int64_t deadline = esp_timer_get_time() + 20000;
    do {
        uint16_t state;
        if (!read_register(1, state)) return false;
        if (state & 0x8000) {
            uint16_t raw;
            if (!read_register(0, raw)) return false;
            value = static_cast<int16_t>(raw);
            return true;
        }
        esp_rom_delay_us(1000);
    } while (esp_timer_get_time() < deadline);
    return false;
}
daq::Result Adc::read(std::array<float, 3> &values) {
    // Lock the entire three-channel conversion: a gain update cannot split a sample.
    platform::Lock lock(mutex_);
    if (!ready_) {
        if (esp_timer_get_time() < next_probe_us_) return daq::failure(daq::Error::NotReady, "ADS1115 unavailable");
        next_probe_us_ = esp_timer_get_time() + 1000000;
        if (bus_ready_) { i2c_driver_delete(I2C_NUM_0); bus_ready_ = false; }
        if (!configure_bus() || !probe()) return daq::failure(daq::Error::NotReady, "ADS1115 unavailable");
    }
    static const float ranges[] = {6144, 4096, 2048, 1024, 512, 256};
    for (int channel = 0; channel < 3; ++channel) {
        int16_t raw;
        if (!read_channel(channel, raw)) {
            ready_ = false;
            next_probe_us_ = esp_timer_get_time() + 1000000;
            return daq::failure(daq::Error::Io, "ADS1115 conversion failed");
        }
        values[channel] = raw * ranges[gain_] / 32768.0f;
    }
    return {};
}
}
