#pragma once

#include <array>
#include <algorithm>
#include <cstdint>
#include <string>

namespace daq {
enum class Error { None, InvalidArgument, NotReady, Conflict, NotFound, Io, PartialWrite };
struct Result {
    Error error = Error::None;
    std::string message;
    bool ok() const { return error == Error::None; }
};
inline Result failure(Error error, const std::string &message) { return {error, message}; }
struct Sample {
    uint64_t timestamp_ms = 0;
    uint64_t sequence = 0;
    std::array<float, 3> millivolts{};
};
struct FileInfo {
    std::string name;
    uint64_t size = 0;
};
struct WifiSettings {
    std::string mode, ssid, password;
};
inline bool wifi_station_enabled(const std::string &mode) { return mode == "other" || mode == "both"; }
inline bool wifi_ap_enabled(const std::string &mode) { return mode == "own" || mode == "both"; }
inline Result validate_wifi(const WifiSettings &settings) {
    if ((settings.mode != "own" && settings.mode != "other" && settings.mode != "both") ||
        settings.ssid.empty() || settings.ssid.size() > 32 ||
        (!settings.password.empty() && (settings.password.size() < 8 || settings.password.size() > 63)))
        return failure(Error::InvalidArgument, "Wi-Fi mode must be own/other/both; SSID 1..32 bytes; password empty or 8..63 bytes");
    return {};
}
struct NetworkInfo {
    bool sta_enabled = false, sta_connected = false, ap_enabled = false;
    std::string sta_ip, ap_ip, sta_mac, ap_mac, ap_ssid;
};
struct RecordingStatus {
    bool active = false;
    std::string filename;
    std::string error;
};
struct Status {
    std::string ip;
    bool adc_ready = false, rtc_ready = false, sd_mounted = false;
    int gain = 1;
    RecordingStatus recording;
};
inline bool valid_filename(const std::string &name) {
    return !name.empty() && name.size() <= 32 && name[0] != '.' &&
           std::all_of(name.begin(), name.end(), [](char c) {
               return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                      (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.';
           });
}
}
