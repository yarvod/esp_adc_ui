#pragma once

#include <stdint.h>

namespace board {
// ESP32-WROOM board connections. Keep all wiring and acquisition settings here.
constexpr int ADC_SDA = 21;
constexpr int ADC_SCL = 22;
constexpr uint8_t ADC_ADDRESS = 0x48;
constexpr uint32_t I2C_FREQUENCY = 100000;
constexpr int RTC_RST = 27;
constexpr int RTC_DAT = 26;
constexpr int RTC_CLK = 25;
constexpr int SD_CS = 5;
constexpr int SD_MOSI = 23;
constexpr int SD_MISO = 19;
constexpr int SD_CLK = 18;
constexpr int SD_FREQUENCY_KHZ = 4000;
constexpr char SD_MOUNT[] = "/sdcard";
// Runtime acquisition defaults and bounds live in domain/acquisition_config.h.
constexpr int RECORD_BUFFER_SIZE = 860;
constexpr int HTTP_PORT = 80;
constexpr int HTTP_WORKERS = 4;
constexpr int HTTP_MAX_CONNECTIONS = 6;
constexpr int HTTP_QUEUE_LENGTH = 8;
constexpr int HTTP_TIMEOUT_SECONDS = 3;
constexpr int HTTP_TASK_STACK_SIZE = 6144;
constexpr int MAX_DOWNLOADS = 2;
static_assert(MAX_DOWNLOADS > 0 && MAX_DOWNLOADS < HTTP_WORKERS, "Reserve HTTP workers for monitoring");
constexpr int DOWNLOAD_CHUNK = 4096;
constexpr int HTTP_MAX_BODY = 1024;
constexpr int WIFI_TIMEOUT_MS = 30000;
// Native identities of the ESP32 attached at /dev/cu.usbserial-0001.
// STA read from eFuse; AP is the ESP-IDF four-address factory identity.
constexpr uint8_t WIFI_STA_MAC[6] = {0x68, 0x09, 0x47, 0x9f, 0x21, 0x2c};
constexpr uint8_t WIFI_AP_MAC[6] = {0x68, 0x09, 0x47, 0x9f, 0x21, 0x2d};
static_assert((WIFI_STA_MAC[0] & 1) == 0 && (WIFI_AP_MAC[0] & 1) == 0, "Wi-Fi MAC must be unicast");
constexpr char WIFI_AP_SSID[] = "esp";
constexpr char WIFI_AP_PASSWORD[] = "12345678";
}
