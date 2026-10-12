#include "hardware/config_store.h"
#include "nvs.h"
#include <type_traits>

namespace hardware {
namespace {
// Single versioned NVS value: no partially updated collection of keys.
struct StoredConfig { uint32_t version; daq::AcquisitionConfig config; };
static_assert(std::is_trivially_copyable<StoredConfig>::value, "NVS config must be plain data");
constexpr uint32_t VERSION = 1;
}
daq::Result ConfigStore::load(daq::AcquisitionConfig &config) {
    nvs_handle_t handle;
    auto err = nvs_open("acquisition", NVS_READONLY, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return daq::failure(daq::Error::NotFound, "No saved acquisition config");
    if (err != ESP_OK) return daq::failure(daq::Error::Io, "Cannot open acquisition config");
    StoredConfig stored{};
    size_t size = sizeof(stored);
    err = nvs_get_blob(handle, "config", &stored, &size);
    nvs_close(handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return daq::failure(daq::Error::NotFound, "No saved acquisition config");
    if (err != ESP_OK || size != sizeof(stored) || stored.version != VERSION || !daq::validate_config(stored.config).ok())
        return daq::failure(daq::Error::Io, "Invalid saved acquisition config; using defaults");
    config = stored.config;
    return {};
}
daq::Result ConfigStore::save(const daq::AcquisitionConfig &config) {
    const auto valid = daq::validate_config(config);
    if (!valid.ok()) return valid;
    nvs_handle_t handle;
    auto err = nvs_open("acquisition", NVS_READWRITE, &handle);
    if (err != ESP_OK) return daq::failure(daq::Error::Io, "Cannot open acquisition config");
    const StoredConfig stored{VERSION, config};
    err = nvs_set_blob(handle, "config", &stored, sizeof(stored));
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    return err == ESP_OK ? daq::Result{} : daq::failure(daq::Error::Io, "Cannot save acquisition config to NVS");
}
}
