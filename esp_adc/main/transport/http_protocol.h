#pragma once
#include <string>

namespace protocol {
constexpr char API_PREFIX[] = "/api/v1";
constexpr int API_VERSION = 1;
enum class Route { Status, Config, Ip, Adc, Gain, Rtc, Wifi, Recording, Start, Stop, Files, File, Mount, Unmount, Unknown };
inline Route route(const std::string &path) {
    if (path == "/" || path == "/api/v1/status") return Route::Status;
    if (path == "/api/v1/config") return Route::Config;
    if (path == "/api/v1/ip") return Route::Ip;
    if (path == "/api/v1/adc") return Route::Adc;
    if (path == "/api/v1/adc/gain") return Route::Gain;
    if (path == "/api/v1/rtc") return Route::Rtc;
    if (path == "/api/v1/wifi") return Route::Wifi;
    if (path == "/api/v1/recording") return Route::Recording;
    if (path == "/api/v1/recording/start") return Route::Start;
    if (path == "/api/v1/recording/stop") return Route::Stop;
    if (path == "/api/v1/files") return Route::Files;
    if (path.compare(0, 14, "/api/v1/files/") == 0 && path.size() > 14) return Route::File;
    if (path == "/api/v1/sd/mount") return Route::Mount;
    if (path == "/api/v1/sd/unmount") return Route::Unmount;
    return Route::Unknown;
}
inline const char *allowed(Route route) {
    switch (route) {
        case Route::Config: case Route::Gain: case Route::Rtc: case Route::Wifi: return "GET, PUT, OPTIONS";
        case Route::Start: case Route::Stop: case Route::Mount: case Route::Unmount: return "POST, OPTIONS";
        case Route::File: return "GET, DELETE, OPTIONS";
        case Route::Unknown: return "OPTIONS";
        default: return "GET, OPTIONS";
    }
}
}
