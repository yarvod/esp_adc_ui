#pragma once
#include "domain/ports.h"

namespace hardware {
class ConfigStore : public daq::ConfigStorePort {
public:
    daq::Result load(daq::AcquisitionConfig &config) override;
    daq::Result save(const daq::AcquisitionConfig &config) override;
};
}
