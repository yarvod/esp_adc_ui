#pragma once
#include "domain/ports.h"
#include "platform/mutex.h"

namespace hardware {
class Network : public daq::NetworkPort {
public:
    void initialize();
    std::string ip() const override;
    daq::WifiSettings settings() const override;
    daq::NetworkInfo info() const override;
    daq::Result configure(const daq::WifiSettings &settings) override;
private:
    mutable platform::Mutex mutex_;
    daq::WifiSettings settings_;
    bool restarting_ = false;
};
}
