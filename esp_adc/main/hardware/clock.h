#pragma once
#include "domain/ports.h"
#include "platform/mutex.h"
#include "ds1302.h"
#include <atomic>

namespace hardware {
class Clock : public daq::ClockPort {
public:
    Clock();
    void initialize();
    uint64_t timestamp_ms() override;
    daq::Result set_utc(const std::string &utc) override;
    uint64_t monotonic_ms() const override;
    void set_poll_interval(uint32_t value) override { poll_interval_ms_ = value; }
private:
    platform::Mutex mutex_;
    rtc::Ds1302 device_;
    rtc::Clock clock_;
    std::atomic<uint32_t> poll_interval_ms_{20};
    static void poll(void *context);
};
}
