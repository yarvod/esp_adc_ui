#pragma once

#include "domain/models.h"
#include "domain/acquisition_config.h"
#include <memory>
#include <vector>

namespace daq {
class AdcPort {
public:
    virtual ~AdcPort() = default;
    virtual bool ready() const = 0;
    virtual int gain() const = 0;
    virtual Result set_gain(int value) = 0;
    virtual Result read(std::array<float, 3> &values) = 0;
};
class ClockPort {
public:
    virtual ~ClockPort() = default;
    virtual uint64_t timestamp_ms() = 0;
    virtual Result set_utc(const std::string &utc) = 0;
    virtual uint64_t monotonic_ms() const { return 0; }
    virtual void set_poll_interval(uint32_t) {}
};
class ConfigStorePort {
public:
    virtual ~ConfigStorePort() = default;
    virtual Result load(AcquisitionConfig &config) = 0;
    virtual Result save(const AcquisitionConfig &config) = 0;
};
class FileReader {
public:
    virtual ~FileReader() = default;
    virtual uint64_t size() const = 0;
    virtual Result read(char *buffer, size_t capacity, size_t &bytes) = 0;
};
class StoragePort {
public:
    virtual ~StoragePort() = default;
    virtual bool mounted() const = 0;
    virtual Result mount() = 0;
    virtual Result unmount() = 0;
    virtual Result create(const std::string &filename) = 0;
    virtual Result append(const std::string &filename, const Sample *samples, size_t count) = 0;
    virtual Result list(std::vector<FileInfo> &files) = 0;
    virtual Result remove(const std::string &filename) = 0;
    virtual Result open(const std::string &filename, std::unique_ptr<FileReader> &reader) = 0;
};
class NetworkPort {
public:
    virtual ~NetworkPort() = default;
    virtual std::string ip() const = 0;
    virtual WifiSettings settings() const = 0;
    virtual NetworkInfo info() const { return {}; }
    virtual Result configure(const WifiSettings &settings) = 0;
};
}
