#pragma once
#include "domain/ports.h"
#include "platform/mutex.h"
#include <atomic>

namespace hardware {
class Adc : public daq::AdcPort {
public:
    void initialize();
    bool ready() const override { return ready_; }
    int gain() const override { return gain_; }
    daq::Result set_gain(int value) override;
    daq::Result read(std::array<float, 3> &values) override;
private:
    platform::Mutex mutex_;
    std::atomic<bool> ready_{false};
    std::atomic<int> gain_{1};
    bool bus_ready_ = false;
    int64_t next_probe_us_ = 0;
    bool configure_bus();
    bool probe();
    bool read_register(uint8_t address, uint16_t &value);
    bool read_channel(int channel, int16_t &value);
};
}
