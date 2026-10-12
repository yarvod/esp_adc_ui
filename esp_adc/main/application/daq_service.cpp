#include "application/daq_service.h"
#include "ds1302.h"
#include "freertos/task.h"
#include <cstdio>

namespace application {
static_assert(board::RECORD_BUFFER_SIZE == daq::MAX_FLUSH_SAMPLES, "Config limit must fit recording buffer");
void DaqService::reset_pipeline_locked() {
    sums_.fill(0);
    averaged_ = record_counter_ = 0;
    filter_ready_ = false;
    ++generation_;
    platform::Lock snapshot(snapshot_mutex_);
    sample_ready_ = false;
}
daq::AcquisitionConfig DaqService::config() const {
    platform::Lock lock(state_mutex_);
    return config_;
}
daq::Result DaqService::initialize_config() {
    platform::Lock lock(state_mutex_);
    daq::AcquisitionConfig loaded;
    const auto result = config_store_ ? config_store_->load(loaded) : daq::Result{};
    const auto applied = configure_locked(loaded, false);
    config_saved_ = result.ok() && config_store_;
    return applied.ok() ? result : applied;
}
daq::Result DaqService::configure_locked(const daq::AcquisitionConfig &value, bool persist) {
    const auto valid = daq::validate_config(value);
    if (!valid.ok()) return valid;
    if (recording_.active || buffered_) return daq::failure(daq::Error::Conflict, "Stop and flush recording before changing acquisition config");
    if (restart_pending_) return daq::failure(daq::Error::Conflict, "Wi-Fi restart pending");
    if (persist && config_saved_ && value == config_) return {};
    const int old_gain = adc_.gain();
    const auto gain = adc_.set_gain(value.gain);
    if (!gain.ok()) return gain;
    if (persist && config_store_) {
        const auto saved = config_store_->save(value);
        if (!saved.ok()) {
            adc_.set_gain(old_gain);
            reset_pipeline_locked();
            return saved;
        }
        config_saved_ = true;
    }
    config_ = value;
    sample_interval_ms_ = value.sample_interval_ms;
    clock_.set_poll_interval(value.rtc_poll_interval_ms);
    reset_pipeline_locked();
    return {};
}
daq::Result DaqService::configure(const daq::AcquisitionConfig &value) {
    platform::Lock lock(state_mutex_);
    return configure_locked(value, true);
}
void DaqService::flush_due() {
    platform::Lock lock(state_mutex_);
    if (recording_.active && buffered_ && clock_.monotonic_ms() - buffered_since_ms_ >= config_.flush_interval_ms)
        flush_locked();
}
daq::Result DaqService::flush_locked() {
    if (!buffered_) return {};
    if (unsafe_write_) return daq::failure(daq::Error::PartialWrite, recording_.error);
    const auto result = storage_.append(recording_.filename, buffer_.data(), buffered_);
    if (result.ok()) { buffered_ = 0; recording_.error.clear(); }
    else {
        recording_.active = false;
        recording_.error = result.message;
        unsafe_write_ = result.error == daq::Error::PartialWrite;
    }
    return result;
}
void DaqService::collect_sample() {
    daq::Sample sample;
    const uint64_t generation = generation_.load();
    const auto read = adc_.read(sample.millivolts);
    sample.timestamp_ms = clock_.timestamp_ms();
    platform::Lock lock(state_mutex_);
    if (generation != generation_) return;
    if (!read.ok()) {
        reset_pipeline_locked();
        if (recording_.active) {
            recording_.active = false;
            if (flush_locked().ok()) recording_.error = read.message;
        }
        return;
    }
    if (!sample.timestamp_ms) {
        reset_pipeline_locked();
        if (recording_.active) {
            recording_.active = false;
            if (flush_locked().ok()) recording_.error = "Recording stopped: RTC unavailable";
        }
        return;
    }
    for (int i = 0; i < 3; ++i) sums_[i] += sample.millivolts[i];
    if (++averaged_ < config_.average_samples) return;
    for (int i = 0; i < 3; ++i) {
        const float mean = sums_[i] / averaged_;
        filtered_[i] = filter_ready_ ? filtered_[i] + config_.ema_alpha * (mean - filtered_[i]) : mean;
        sample.millivolts[i] = filtered_[i];
    }
    sums_.fill(0);
    averaged_ = 0;
    filter_ready_ = true;
    sample.sequence = ++sequence_;
    {
        platform::Lock snapshot(snapshot_mutex_);
        latest_ = sample;
        sample_ready_ = true;
    }
    if (!recording_.active || record_counter_++ % config_.record_every != 0) return;
    if (!buffered_) buffered_since_ms_ = clock_.monotonic_ms();
    buffer_[buffered_++] = sample;
    if (buffered_ >= config_.flush_samples) flush_locked();
}
daq::Result DaqService::latest(daq::Sample &sample) const {
    platform::Lock lock(snapshot_mutex_);
    if (!sample_ready_) return daq::failure(daq::Error::NotReady, "ADS1115 sample unavailable");
    if (!latest_.timestamp_ms) return daq::failure(daq::Error::NotReady, "RTC unavailable or not set");
    sample = latest_;
    return {};
}
daq::RecordingStatus DaqService::recording() const {
    platform::Lock lock(state_mutex_);
    return recording_;
}
daq::Status DaqService::status() {
    daq::Status result;
    result.ip = network_.ip();
    result.adc_ready = adc_.ready();
    result.rtc_ready = clock_.timestamp_ms() != 0;
    result.sd_mounted = storage_.mounted();
    result.gain = adc_.gain();
    result.recording = recording();
    return result;
}
daq::Result DaqService::start_recording(const std::string &filename) {
    platform::Lock lock(state_mutex_);
    if (recording_.active) return daq::failure(daq::Error::Conflict, "Recording already active");
    if (restart_pending_) return daq::failure(daq::Error::Conflict, "Wi-Fi restart pending");
    if (buffered_) return daq::failure(daq::Error::Conflict, "Previous recording has unsaved samples; retry stop before starting");
    if (!adc_.ready()) return daq::failure(daq::Error::NotReady, "ADS1115 unavailable");
    const uint64_t now = clock_.timestamp_ms();
    if (!now) return daq::failure(daq::Error::NotReady, "Set RTC time before recording");
    std::string name = filename;
    if (name.empty()) {
        const auto c = rtc::calendar(now / 1000);
        char text[40];
        snprintf(text, sizeof(text), "data_%04d%02d%02d_%02d%02d%02d_%03d.txt", c.year, c.month, c.day,
                 c.hour, c.minute, c.second, static_cast<int>(now % 1000));
        name = text;
    }
    if (!daq::valid_filename(name)) return daq::failure(daq::Error::InvalidArgument, "Invalid filename");
    const auto created = storage_.create(name);
    if (!created.ok()) return created;
    recording_ = {true, name, ""};
    reset_pipeline_locked();
    return {};
}
daq::Result DaqService::stop_recording() {
    platform::Lock lock(state_mutex_);
    recording_.active = false;
    return flush_locked();
}
daq::Result DaqService::set_time(const std::string &utc) {
    platform::Lock lock(state_mutex_);
    if (recording_.active) return daq::failure(daq::Error::Conflict, "Stop recording before setting RTC");
    const auto result = clock_.set_utc(utc);
    if (result.ok()) reset_pipeline_locked();
    return result;
}
daq::Result DaqService::set_gain(int value) {
    platform::Lock lock(state_mutex_);
    auto updated = config_;
    updated.gain = value;
    return configure_locked(updated, true);
}
daq::Result DaqService::configure_wifi(const daq::WifiSettings &settings) {
    platform::Lock lock(state_mutex_);
    if (recording_.active || buffered_) return daq::failure(daq::Error::Conflict, "Stop and flush recording before changing Wi-Fi");
    const auto result = network_.configure(settings);
    if (result.ok()) restart_pending_ = true;
    return result;
}
daq::Result DaqService::mount_sd() {
    platform::Lock lock(state_mutex_);
    return storage_.mount();
}
daq::Result DaqService::unmount_sd() {
    platform::Lock lock(state_mutex_);
    if (recording_.active || buffered_) return daq::failure(daq::Error::Conflict, "Stop and flush recording before removing SD");
    return storage_.unmount();
}
daq::Result DaqService::files(std::vector<daq::FileInfo> &files) { return storage_.list(files); }
daq::Result DaqService::delete_file(const std::string &filename) {
    platform::Lock lock(state_mutex_);
    if ((recording_.active || buffered_) && filename == recording_.filename)
        return daq::failure(daq::Error::Conflict, "Cannot delete active recording");
    return storage_.remove(filename);
}
daq::Result DaqService::download(const std::string &filename, std::unique_ptr<daq::FileReader> &reader) {
    platform::Lock lock(state_mutex_);
    if (filename == recording_.filename) {
        auto flushed = flush_locked();
        if (!flushed.ok()) return flushed;
    }
    // Reader pins the card and file, but does not keep any mutex locked during transfer.
    return storage_.open(filename, reader);
}
static void sampling_task(void *context) {
    auto &service = *static_cast<DaqService *>(context);
    TickType_t wake = xTaskGetTickCount();
    TickType_t last_sample = wake;
    uint32_t last_interval = 0;
    while (true) {
        const TickType_t now = xTaskGetTickCount();
        const uint32_t interval = service.sample_interval_ms();
        if (interval != last_interval || now - last_sample >= pdMS_TO_TICKS(interval)) {
            last_sample = now;
            last_interval = interval;
            service.collect_sample();
        }
        service.flush_due();
        // Check configuration and flush deadlines even with a long sample interval.
        // Resynchronize after slow I/O instead of running a burst of overdue samples.
        if (xTaskGetTickCount() - wake > pdMS_TO_TICKS(10)) wake = xTaskGetTickCount();
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(10));
    }
}
void start_sampling(DaqService &service) {
    configASSERT(xTaskCreatePinnedToCore(sampling_task, "sampling", 4096, &service, 1, nullptr, 0) == pdPASS);
}
}
