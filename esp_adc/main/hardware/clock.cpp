#include "hardware/clock.h"
#include "driver/gpio.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/task.h"

namespace hardware {
static void mode(int pin, bool output) {
    gpio_set_direction(static_cast<gpio_num_t>(pin), output ? GPIO_MODE_OUTPUT : GPIO_MODE_INPUT);
    gpio_set_pull_mode(static_cast<gpio_num_t>(pin), GPIO_PULLDOWN_ONLY);
}
static void write(int pin, bool value) { gpio_set_level(static_cast<gpio_num_t>(pin), value); }
static int read(int pin) { return gpio_get_level(static_cast<gpio_num_t>(pin)); }
uint64_t Clock::monotonic_ms() const { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }
Clock::Clock() : device_(mode, write, read, esp_rom_delay_us), clock_(device_) {}
void Clock::initialize() {
    device_.begin();
    clock_.refresh(monotonic_ms());
    configASSERT(xTaskCreatePinnedToCore(poll, "rtc", 3072, this, 1, nullptr, 1) == pdPASS);
}
void Clock::poll(void *context) {
    auto &self = *static_cast<Clock *>(context);
    while (true) {
        { platform::Lock lock(self.mutex_); self.clock_.refresh(self.monotonic_ms()); }
        vTaskDelay(pdMS_TO_TICKS(self.poll_interval_ms_.load()));
    }
}
uint64_t Clock::timestamp_ms() {
    platform::Lock lock(mutex_);
    return clock_.timestamp(monotonic_ms());
}
daq::Result Clock::set_utc(const std::string &utc) {
    rtc::Calendar value;
    if (!rtc::parse(utc.c_str(), value)) return daq::failure(daq::Error::InvalidArgument, "UTC must be YYYY-MM-DDTHH:MM:SS, years 2000..2099");
    platform::Lock lock(mutex_);
    if (!clock_.set(value, monotonic_ms())) return daq::failure(daq::Error::Io, "DS1302 write verification failed");
    return {};
}
}
