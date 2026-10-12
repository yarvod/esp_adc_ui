#pragma once
#include "domain/models.h"
#include <cmath>

namespace daq {
constexpr uint32_t MAX_FLUSH_SAMPLES = 860;
struct AcquisitionConfig {
    uint32_t sample_interval_ms = 10;
    uint32_t average_samples = 1;
    float ema_alpha = 0.25f; // 1 disables exponential smoothing.
    uint32_t record_every = 1;
    uint32_t flush_samples = MAX_FLUSH_SAMPLES;
    uint32_t flush_interval_ms = 1000;
    uint32_t rtc_poll_interval_ms = 20;
    int32_t gain = 1;
};
inline bool operator==(const AcquisitionConfig &a, const AcquisitionConfig &b) {
    return a.sample_interval_ms == b.sample_interval_ms && a.average_samples == b.average_samples &&
        a.ema_alpha == b.ema_alpha && a.record_every == b.record_every && a.flush_samples == b.flush_samples &&
        a.flush_interval_ms == b.flush_interval_ms && a.rtc_poll_interval_ms == b.rtc_poll_interval_ms && a.gain == b.gain;
}
inline Result validate_config(const AcquisitionConfig &c) {
    if (c.sample_interval_ms < 10 || c.sample_interval_ms > 60000 || c.sample_interval_ms % 10)
        return failure(Error::InvalidArgument, "sample_interval_ms: 10..60000, multiple of 10");
    if (c.average_samples < 1 || c.average_samples > 1000)
        return failure(Error::InvalidArgument, "average_samples: 1..1000");
    if (!std::isfinite(c.ema_alpha) || c.ema_alpha < 0.01f || c.ema_alpha > 1)
        return failure(Error::InvalidArgument, "ema_alpha: 0.01..1 (1 disables EMA)");
    if (c.record_every < 1 || c.record_every > 10000)
        return failure(Error::InvalidArgument, "record_every: 1..10000");
    if (c.flush_samples < 1 || c.flush_samples > MAX_FLUSH_SAMPLES)
        return failure(Error::InvalidArgument, "flush_samples: 1..860");
    if (c.flush_interval_ms < 100 || c.flush_interval_ms > 60000 || c.flush_interval_ms % 10)
        return failure(Error::InvalidArgument, "flush_interval_ms: 100..60000, multiple of 10");
    if (c.rtc_poll_interval_ms < 20 || c.rtc_poll_interval_ms > 200 || c.rtc_poll_interval_ms % 10)
        return failure(Error::InvalidArgument, "rtc_poll_interval_ms: 20..200, multiple of 10");
    if (c.gain < 0 || c.gain > 5) return failure(Error::InvalidArgument, "gain: 0..5");
    return {};
}
}
