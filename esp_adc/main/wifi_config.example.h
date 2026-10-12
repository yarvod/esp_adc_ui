#pragma once
#include <stdint.h>

// Copy to wifi_config.local.h (ignored by Git), then rebuild and flash.
// Change REVISION whenever this file should replace the saved Wi-Fi settings.
// Applied once per revision; later changes from the UI/API remain effective.
namespace local_wifi {
constexpr uint32_t REVISION = 1;
constexpr char SSID[] = "your-router";
constexpr char PASSWORD[] = "your-password";
}
