#pragma once
#include "domain/ports.h"
#include "platform/mutex.h"
#include "board_config.h"
#include <array>
#include <atomic>

namespace application {
class DaqService {
public:
    DaqService(daq::AdcPort &adc, daq::ClockPort &clock, daq::StoragePort &storage, daq::NetworkPort &network,
               daq::ConfigStorePort *config_store = nullptr)
        : adc_(adc), clock_(clock), storage_(storage), network_(network), config_store_(config_store) {}
    daq::Result initialize_config();
    daq::AcquisitionConfig config() const;
    daq::Result configure(const daq::AcquisitionConfig &config);
    uint32_t sample_interval_ms() const { return sample_interval_ms_.load(); }
    void flush_due();
    void collect_sample();
    daq::Result latest(daq::Sample &sample) const;
    daq::Status status();
    daq::RecordingStatus recording() const;
    daq::Result start_recording(const std::string &filename);
    daq::Result stop_recording();
    daq::Result set_time(const std::string &utc);
    uint64_t timestamp_ms() { return clock_.timestamp_ms(); }
    int gain() const { return adc_.gain(); }
    daq::Result set_gain(int value);
    daq::WifiSettings wifi() const { return network_.settings(); }
    daq::NetworkInfo network_info() const { return network_.info(); }
    daq::Result configure_wifi(const daq::WifiSettings &settings);
    daq::Result mount_sd();
    daq::Result unmount_sd();
    daq::Result files(std::vector<daq::FileInfo> &files);
    daq::Result delete_file(const std::string &filename);
    daq::Result download(const std::string &filename, std::unique_ptr<daq::FileReader> &reader);
private:
    daq::AdcPort &adc_;
    daq::ClockPort &clock_;
    daq::StoragePort &storage_;
    daq::NetworkPort &network_;
    daq::ConfigStorePort *config_store_;
    daq::AcquisitionConfig config_;
    bool config_saved_ = false;
    std::atomic<uint32_t> sample_interval_ms_{10};
    std::array<double, 3> sums_{};
    std::array<float, 3> filtered_{};
    uint32_t averaged_ = 0, record_counter_ = 0;
    bool filter_ready_ = false;
    uint64_t buffered_since_ms_ = 0;
    // Lock order: state -> clock/storage/network. Snapshot is never held across I/O.
    mutable platform::Mutex state_mutex_, snapshot_mutex_;
    daq::RecordingStatus recording_;
    std::array<daq::Sample, board::RECORD_BUFFER_SIZE> buffer_{};
    size_t buffered_ = 0;
    daq::Sample latest_;
    bool sample_ready_ = false;
    uint64_t sequence_ = 0;
    std::atomic<uint64_t> generation_{0};
    bool restart_pending_ = false;
    bool unsafe_write_ = false;
    daq::Result flush_locked();
    daq::Result configure_locked(const daq::AcquisitionConfig &config, bool persist);
    void reset_pipeline_locked();
};
void start_sampling(DaqService &service);
}
