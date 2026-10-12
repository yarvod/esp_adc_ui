#pragma once
#include "domain/ports.h"
#include "platform/mutex.h"
#include "sdmmc_cmd.h"
#include <atomic>
#include <map>

namespace hardware {
class Storage : public daq::StoragePort {
public:
    bool mounted() const override { return mounted_; }
    daq::Result mount() override;
    daq::Result unmount() override;
    daq::Result create(const std::string &filename) override;
    daq::Result append(const std::string &filename, const daq::Sample *samples, size_t count) override;
    daq::Result list(std::vector<daq::FileInfo> &files) override;
    daq::Result remove(const std::string &filename) override;
    daq::Result open(const std::string &filename, std::unique_ptr<daq::FileReader> &reader) override;
private:
    class Reader;
    platform::Mutex mutex_;
    std::atomic<bool> mounted_{false};
    bool bus_initialized_ = false;
    sdmmc_card_t *card_ = nullptr;
    std::map<std::string, int> readers_;
};
}
