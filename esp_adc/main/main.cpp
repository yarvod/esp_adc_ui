#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <cerrno>
#include <dirent.h>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <sys/unistd.h>
#include <vector>

extern "C" {
#include "esp_err.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_ip_addr.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "driver/uart.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "sdmmc_cmd.h"
#include "esp_rom_sys.h"
}

static const char *TAG = "esp_adc";

// --- Configuration constants ---
constexpr char MAC_ADDRESS[] = "10:06:1c:a6:b1:94";
constexpr char WIFI_SSID_DEFAULT[] = "esp";
constexpr char WIFI_PASSWORD_DEFAULT[] = "12345678";
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 30000;
constexpr uint16_t SERVER_PORT = 80;
constexpr int SD_CS_PIN = 5;
constexpr int SD_MOSI_PIN = 23;
constexpr int SD_MISO_PIN = 19;
constexpr int SD_SCLK_PIN = 18;
constexpr int I2C_SDA_PIN = 21;
constexpr int I2C_SCL_PIN = 22;
constexpr i2c_port_t I2C_PORT = I2C_NUM_0;
constexpr uint32_t I2C_FREQ_HZ = 100000;
constexpr uint8_t ADS_I2C_ADDR = 0x48;
constexpr auto MOUNT_POINT = "/sdcard";
constexpr size_t MAX_FILENAME_LEN = 32;

constexpr int SD_BUFFER_SIZE = 860;
constexpr int RT_BUFFER_SIZE = 256;
constexpr int CHUNK_SIZE = 4096;
constexpr int OUTPUT_HZ = 100;
constexpr int OVERSAMPLE = 1;
constexpr float EMA_ALPHA = 0.25f;

// --- ADS1115 enums/consts ---
enum class AdsGain {
    GAIN_TWOTHIRDS = 0,
    GAIN_ONE,
    GAIN_TWO,
    GAIN_FOUR,
    GAIN_EIGHT,
    GAIN_SIXTEEN,
};

enum class AdsDataRate {
    RATE_8SPS = 0,
    RATE_16SPS,
    RATE_32SPS,
    RATE_64SPS,
    RATE_128SPS,
    RATE_250SPS,
    RATE_475SPS,
    RATE_860SPS,
};

struct Ads1115 {
    i2c_port_t port;
    uint8_t address;
    AdsGain gain;
    AdsDataRate data_rate;
};

// --- Runtime state ---
struct DataPoint {
    uint32_t timestamp_ms;
    float adc0;
    float adc1;
    float adc2;
};

static Ads1115 g_ads{I2C_PORT, ADS_I2C_ADDR, AdsGain::GAIN_ONE, AdsDataRate::RATE_860SPS};
static volatile bool sampling_enabled = false;
static volatile bool is_recording = false;
static volatile bool ads_ready = false;
static bool ads_error_logged = false;
static bool sd_mounted = false;
static std::string current_file_name;
static sdmmc_card_t *sd_card = nullptr;
static bool spi_bus_initialized = false;
static SemaphoreHandle_t sd_mutex = nullptr;
static SemaphoreHandle_t i2c_mutex = nullptr;

static DataPoint rt_buffer[RT_BUFFER_SIZE];
static volatile int rt_head = 0;
static volatile bool rt_has_data = false;
static portMUX_TYPE rt_mux = portMUX_INITIALIZER_UNLOCKED;

static DataPoint sd_buffer[SD_BUFFER_SIZE];
static int sd_buffer_index = 0;

static EventGroupHandle_t wifi_event_group;
constexpr EventBits_t WIFI_CONNECTED_BIT = BIT0;
constexpr EventBits_t WIFI_READY_BIT = BIT1;
static esp_netif_t *ap_netif = nullptr;
static esp_netif_t *sta_netif = nullptr;
static std::string wifi_mode = "own";

// --- Utility helpers ---
static std::string trim(const std::string &s) {
    const auto start = s.find_first_not_of(" \r\n\t");
    if (start == std::string::npos) return {};
    const auto end = s.find_last_not_of(" \r\n\t");
    return s.substr(start, end - start + 1);
}

static std::string default_recording_name() {
    time_t now = time(nullptr);
    struct tm tm_info{};
    char buf[64];
    if (now > 0 && localtime_r(&now, &tm_info)) {
        strftime(buf, sizeof(buf), "data_%Y%m%d_%H%M%S.txt", &tm_info);
        return std::string(buf);
    }
    uint64_t ms = static_cast<uint64_t>(esp_timer_get_time() / 1000ULL);
    snprintf(buf, sizeof(buf), "data_%llu.txt", static_cast<unsigned long long>(ms));
    return std::string(buf);
}

static std::string sanitize_filename(const std::string &in) {
    std::string out;
    out.reserve(in.size());
    for (char c : in) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.') {
            out.push_back(c);
        }
    }
    if (out.empty()) out = default_recording_name();
    if (out.size() > MAX_FILENAME_LEN) out.resize(MAX_FILENAME_LEN);
    return out;
}

static std::string ip_to_string(const esp_netif_ip_info_t &ip) {
    char buf[16];
    snprintf(buf, sizeof(buf), IPSTR, IP2STR(&ip.ip));
    return std::string(buf);
}

// ======================= NVS (preferences) ============================
static esp_err_t init_nvs() {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    return err;
}

struct WifiSettings {
    std::string mode;
    std::string ssid;
    std::string pwd;
};

static std::string nvs_get_string(nvs_handle_t handle, const char *key, const std::string &fallback) {
    size_t required = 0;
    esp_err_t err = nvs_get_str(handle, key, nullptr, &required);
    if (err != ESP_OK || required == 0) return fallback;
    std::string value(required, '\0');
    if (nvs_get_str(handle, key, value.data(), &required) != ESP_OK) return fallback;
    value.resize(strlen(value.c_str()));
    return value;
}

static WifiSettings load_wifi_settings() {
    WifiSettings settings{wifi_mode, WIFI_SSID_DEFAULT, WIFI_PASSWORD_DEFAULT};
    nvs_handle_t handle;
    if (nvs_open("wifi-settings", NVS_READONLY, &handle) == ESP_OK) {
        settings.mode = nvs_get_string(handle, "wifi", settings.mode);
        settings.ssid = nvs_get_string(handle, "ssid", settings.ssid);
        settings.pwd = nvs_get_string(handle, "pwd", settings.pwd);
        nvs_close(handle);
    }
    wifi_mode = settings.mode;
    return settings;
}

static void save_wifi_settings(const WifiSettings &settings) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wifi-settings", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to open NVS: %s", esp_err_to_name(err));
        return;
    }
    nvs_set_str(handle, "wifi", settings.mode.c_str());
    nvs_set_str(handle, "ssid", settings.ssid.c_str());
    nvs_set_str(handle, "pwd", settings.pwd.c_str());
    nvs_commit(handle);
    nvs_close(handle);
}

// ======================= ADS1115 helpers =============================
static float ads_gain_lsb_mv(AdsGain g) {
    switch (g) {
        case AdsGain::GAIN_TWOTHIRDS: return 6.144f / 32768.0f * 1000.0f;
        case AdsGain::GAIN_ONE:       return 4.096f / 32768.0f * 1000.0f;
        case AdsGain::GAIN_TWO:       return 2.048f / 32768.0f * 1000.0f;
        case AdsGain::GAIN_FOUR:      return 1.024f / 32768.0f * 1000.0f;
        case AdsGain::GAIN_EIGHT:     return 0.512f / 32768.0f * 1000.0f;
        case AdsGain::GAIN_SIXTEEN:   return 0.256f / 32768.0f * 1000.0f;
    }
    return 4.096f / 32768.0f * 1000.0f;
}

static const char *ads_gain_range_str(AdsGain g) {
    switch (g) {
        case AdsGain::GAIN_TWOTHIRDS: return "±6.144V";
        case AdsGain::GAIN_ONE:       return "±4.096V";
        case AdsGain::GAIN_TWO:       return "±2.048V";
        case AdsGain::GAIN_FOUR:      return "±1.024V";
        case AdsGain::GAIN_EIGHT:     return "±0.512V";
        case AdsGain::GAIN_SIXTEEN:   return "±0.256V";
    }
    return "unknown";
}

static int ads_gain_to_index(AdsGain g) {
    switch (g) {
        case AdsGain::GAIN_TWOTHIRDS: return 0;
        case AdsGain::GAIN_ONE:       return 1;
        case AdsGain::GAIN_TWO:       return 2;
        case AdsGain::GAIN_FOUR:      return 3;
        case AdsGain::GAIN_EIGHT:     return 4;
        case AdsGain::GAIN_SIXTEEN:   return 5;
    }
    return 1;
}

static bool index_to_ads_gain(int idx, AdsGain &gain) {
    switch (idx) {
        case 0: gain = AdsGain::GAIN_TWOTHIRDS; return true;
        case 1: gain = AdsGain::GAIN_ONE;       return true;
        case 2: gain = AdsGain::GAIN_TWO;       return true;
        case 3: gain = AdsGain::GAIN_FOUR;      return true;
        case 4: gain = AdsGain::GAIN_EIGHT;     return true;
        case 5: gain = AdsGain::GAIN_SIXTEEN;   return true;
        default: return false;
    }
}

static bool parse_ads_gain(const std::string &s, AdsGain &gain) {
    const std::string v = trim(s);
    if (v == "2/3" || v == "0.666" || v == "0.667") { gain = AdsGain::GAIN_TWOTHIRDS; return true; }
    if (v == "1"   || v == "1x"   || v == "4.096") { gain = AdsGain::GAIN_ONE; return true; }
    if (v == "2"   || v == "2x"   || v == "2.048") { gain = AdsGain::GAIN_TWO; return true; }
    if (v == "4"   || v == "4x"   || v == "1.024") { gain = AdsGain::GAIN_FOUR; return true; }
    if (v == "8"   || v == "8x"   || v == "0.512") { gain = AdsGain::GAIN_EIGHT; return true; }
    if (v == "16"  || v == "16x"  || v == "0.256") { gain = AdsGain::GAIN_SIXTEEN; return true; }
    return false;
}

static int ads_data_rate_to_sps(AdsDataRate r) {
    switch (r) {
        case AdsDataRate::RATE_8SPS:   return 8;
        case AdsDataRate::RATE_16SPS:  return 16;
        case AdsDataRate::RATE_32SPS:  return 32;
        case AdsDataRate::RATE_64SPS:  return 64;
        case AdsDataRate::RATE_128SPS: return 128;
        case AdsDataRate::RATE_250SPS: return 250;
        case AdsDataRate::RATE_475SPS: return 475;
        case AdsDataRate::RATE_860SPS: return 860;
    }
    return -1;
}

static esp_err_t ads_bus_recover() {
    if (i2c_mutex && xSemaphoreTake(i2c_mutex, pdMS_TO_TICKS(50)) != pdTRUE) return ESP_ERR_TIMEOUT;
    i2c_driver_delete(I2C_PORT);
    esp_err_t err = ESP_FAIL;
    // reapply I2C config locally to avoid forward declaration issues
    i2c_config_t conf = {};
    conf.mode = I2C_MODE_MASTER;
    conf.sda_io_num = I2C_SDA_PIN;
    conf.scl_io_num = I2C_SCL_PIN;
    conf.sda_pullup_en = GPIO_PULLUP_ENABLE;
    conf.scl_pullup_en = GPIO_PULLUP_ENABLE;
    conf.master.clk_speed = I2C_FREQ_HZ;
    conf.clk_flags = 0;
    if (i2c_param_config(I2C_PORT, &conf) == ESP_OK &&
        i2c_driver_install(I2C_PORT, conf.mode, 0, 0, 0) == ESP_OK) {
        i2c_set_timeout(I2C_PORT, 0xFFFF);
        err = ESP_OK;
    }
    if (i2c_mutex) xSemaphoreGive(i2c_mutex);
    return err;
}

static esp_err_t ads_write_reg(uint8_t reg, uint16_t value) {
    if (!ads_ready) return ESP_ERR_INVALID_STATE;
    if (i2c_mutex && xSemaphoreTake(i2c_mutex, pdMS_TO_TICKS(20)) != pdTRUE) return ESP_ERR_TIMEOUT;
    uint8_t data[3];
    data[0] = reg;
    data[1] = (value >> 8) & 0xFF;
    data[2] = value & 0xFF;
    esp_err_t err = i2c_master_write_to_device(g_ads.port, g_ads.address, data, sizeof(data), pdMS_TO_TICKS(20));
    if (i2c_mutex) xSemaphoreGive(i2c_mutex);
    if (err != ESP_OK) {
        ads_ready = false;
        ads_bus_recover();
    }
    return err;
}

static esp_err_t ads_read_reg(uint8_t reg, uint16_t &value) {
    if (!ads_ready) return ESP_ERR_INVALID_STATE;
    uint8_t data[2] = {};
    esp_err_t last_err = ESP_FAIL;
    for (int attempt = 0; attempt < 2; ++attempt) {
        if (i2c_mutex && xSemaphoreTake(i2c_mutex, pdMS_TO_TICKS(20)) != pdTRUE) return ESP_ERR_TIMEOUT;
        esp_err_t err = i2c_master_write_read_device(g_ads.port, g_ads.address, &reg, 1, data, sizeof(data), pdMS_TO_TICKS(20));
        if (i2c_mutex) xSemaphoreGive(i2c_mutex);
        if (err == ESP_OK) {
            value = (static_cast<uint16_t>(data[0]) << 8) | data[1];
            return ESP_OK;
        }
        last_err = err;
        ads_ready = false;
        ads_bus_recover();
    }
    return last_err;
}

static uint16_t ads_build_config(uint8_t channel) {
    const uint16_t mux = static_cast<uint16_t>(0x04 + channel) << 12; // single-ended AINx vs GND
    const uint16_t pga = static_cast<uint16_t>(g_ads.gain) << 9;
    const uint16_t mode_single = 1 << 8;
    const uint16_t data_rate = static_cast<uint16_t>(g_ads.data_rate) << 5;
    const uint16_t comparator_disabled = 0x0003;
    return 0x8000 | mux | pga | mode_single | data_rate | comparator_disabled;
}

static esp_err_t ads_read_raw(uint8_t channel, int16_t &raw) {
    const uint16_t config = ads_build_config(channel);
    esp_err_t err = ads_write_reg(0x01, config);
    if (err != ESP_OK) return err;

    const int max_wait_us = 20000;
    int waited_us = 0;
    while (waited_us < max_wait_us) {
        uint16_t status = 0;
        err = ads_read_reg(0x01, status);
        if (err != ESP_OK) return err;
        if (status & 0x8000) break;
        esp_rom_delay_us(1000);
        waited_us += 1000;
    }

    uint16_t data = 0;
    err = ads_read_reg(0x00, data);
    raw = static_cast<int16_t>(data);
    return err;
}

static float ads_read_mv(uint8_t channel) {
    if (!ads_ready) return 0.0f;
    long sum = 0;
    for (int i = 0; i < OVERSAMPLE; ++i) {
        int16_t raw = 0;
        esp_err_t err = ads_read_raw(channel, raw);
        if (err != ESP_OK) {
            if (!ads_error_logged) {
                ESP_LOGW(TAG, "ADS1115 read failed: %s", esp_err_to_name(err));
                ads_error_logged = true;
            }
            ads_ready = false;
            ads_bus_recover();
            return 0.0f;
        }
        ads_error_logged = false;
        sum += raw;
    }
    const float avg_raw = static_cast<float>(sum) / static_cast<float>(std::max(OVERSAMPLE, 1));
    return avg_raw * ads_gain_lsb_mv(g_ads.gain);
}

static void read_adc(float *result) {
    static bool ema_init = false;
    static float e0 = 0, e1 = 0, e2 = 0;

    const float x0 = ads_read_mv(0);
    const float x1 = ads_read_mv(1);
    const float x2 = ads_read_mv(2);

    if (!ema_init) {
        e0 = x0; e1 = x1; e2 = x2; ema_init = true;
    } else {
        e0 += EMA_ALPHA * (x0 - e0);
        e1 += EMA_ALPHA * (x1 - e1);
        e2 += EMA_ALPHA * (x2 - e2);
    }

    result[0] = e0;
    result[1] = e1;
    result[2] = e2;
}

// ======================= Buffers =====================================
static inline void start_sampling() { sampling_enabled = true; }

static void rt_push_sample(const DataPoint &dp) {
    portENTER_CRITICAL(&rt_mux);
    rt_buffer[rt_head] = dp;
    rt_head = (rt_head + 1) % RT_BUFFER_SIZE;
    rt_has_data = true;
    portEXIT_CRITICAL(&rt_mux);
}

static bool rt_get_latest(DataPoint &out) {
    if (!rt_has_data) return false;
    portENTER_CRITICAL(&rt_mux);
    int last = (rt_head - 1 + RT_BUFFER_SIZE) % RT_BUFFER_SIZE;
    out = rt_buffer[last];
    portEXIT_CRITICAL(&rt_mux);
    return true;
}

static std::string read_adc_pretty() {
    if (!ads_ready) return "ADS1115 not ready";
    if (!sampling_enabled) start_sampling();
    DataPoint dp;
    if (!rt_get_latest(dp)) {
        float v[3];
        read_adc(v);
        dp = {static_cast<uint32_t>(esp_timer_get_time() / 1000), v[0], v[1], v[2]};
        rt_push_sample(dp);
    }
    char out[96];
    snprintf(out, sizeof(out), "ADC0: %.1f mV; ADC1: %.1f mV; ADC2: %.1f mV;", dp.adc0, dp.adc1, dp.adc2);
    return std::string(out);
}

// ======================= SD handling =================================
static esp_err_t init_sd_card() {
    if (sd_mounted) return ESP_OK;

    spi_bus_config_t bus_cfg = {};
    bus_cfg.mosi_io_num = SD_MOSI_PIN;
    bus_cfg.miso_io_num = SD_MISO_PIN;
    bus_cfg.sclk_io_num = SD_SCLK_PIN;
    bus_cfg.quadwp_io_num = -1;
    bus_cfg.quadhd_io_num = -1;
    bus_cfg.data4_io_num = -1;
    bus_cfg.data5_io_num = -1;
    bus_cfg.data6_io_num = -1;
    bus_cfg.data7_io_num = -1;
    bus_cfg.max_transfer_sz = 4000;
    bus_cfg.flags = SPICOMMON_BUSFLAG_MASTER;
    bus_cfg.intr_flags = 0;

    if (!spi_bus_initialized) {
        esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to init SPI bus for SD: %s", esp_err_to_name(err));
            return err;
        }
        spi_bus_initialized = true;
    }

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SPI2_HOST;

    sdspi_device_config_t slot_config = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_config.gpio_cs = static_cast<gpio_num_t>(SD_CS_PIN);
    slot_config.host_id = static_cast<spi_host_device_t>(host.slot);

    esp_vfs_fat_sdmmc_mount_config_t mount_config = {};
    mount_config.format_if_mount_failed = false;
    mount_config.max_files = 5;
    mount_config.allocation_unit_size = 0;

    host.max_freq_khz = 4000; // фиксированная частота 4 МГц для стабильности
    esp_err_t err = esp_vfs_fat_sdspi_mount(MOUNT_POINT, &host, &slot_config, &mount_config, &sd_card);
    if (err == ESP_OK) {
        sd_mounted = true;
        ESP_LOGI(TAG, "SD card mounted at %s (freq %d kHz)", MOUNT_POINT, host.max_freq_khz);
        return ESP_OK;
    }
    ESP_LOGE(TAG, "Failed to mount SD card: %s", esp_err_to_name(err));
    return err;
}

static void deinit_sd_card() {
    if (sd_mounted) {
        esp_vfs_fat_sdcard_unmount(MOUNT_POINT, sd_card);
        sd_card = nullptr;
        sd_mounted = false;
        ESP_LOGI(TAG, "SD card unmounted");
    }
    if (spi_bus_initialized) {
        spi_bus_free(SPI2_HOST);
        spi_bus_initialized = false;
    }
}

static void flush_buffer_to_sd() {
    if (!sd_mounted || current_file_name.empty()) return;
    if (xSemaphoreTake(sd_mutex, pdMS_TO_TICKS(100)) != pdTRUE) return;
    const int count = sd_buffer_index;
    if (count > 0) {
        const std::string path = std::string(MOUNT_POINT) + "/" + current_file_name;
        FILE *f = fopen(path.c_str(), "a");
        if (!f) {
            is_recording = false;
            ESP_LOGE(TAG, "Failed to open %s for writing (errno=%d: %s)", path.c_str(), errno, strerror(errno));
            sd_buffer_index = 0;
            xSemaphoreGive(sd_mutex);
            return;
        }
        for (int i = 0; i < count; ++i) {
            fprintf(f, "%lu; %.1f; %.1f; %.1f\n",
                    static_cast<unsigned long>(sd_buffer[i].timestamp_ms),
                    sd_buffer[i].adc0, sd_buffer[i].adc1, sd_buffer[i].adc2);
        }
        fclose(f);
        sd_buffer_index = 0;
    }
    xSemaphoreGive(sd_mutex);
}

static std::string list_files() {
    if (!sd_mounted) return "Error: SD card not initialized";
    DIR *dir = opendir(MOUNT_POINT);
    if (!dir) return "Error: Failed to open directory";
    std::string result;
    // фильтруем типичные системные/longname сервисные файлы
    const char *skip_prefixes[] = {"System Volume Information", "SYSTEM~", "FSEVE~", "SPOTL~", "TRASH~"};
    struct dirent *entry;
    while ((entry = readdir(dir)) != nullptr) {
        if (entry->d_name[0] == '.') continue;
        bool skip = false;
        for (auto p : skip_prefixes) {
            if (strncmp(entry->d_name, p, strlen(p)) == 0) { skip = true; break; }
        }
        if (skip) continue;
        std::string fname = entry->d_name;
        std::string path = std::string(MOUNT_POINT) + "/" + fname;
        struct stat st{};
        if (stat(path.c_str(), &st) == 0) {
            char buf[128];
            // формат: имя:байты;
            snprintf(buf, sizeof(buf), "%s:%lld;", fname.c_str(), static_cast<long long>(st.st_size));
            result += buf;
        } else {
            result += fname;
            result.push_back(';');
        }
    }
    closedir(dir);
    return result;
}

static std::string delete_file(const std::string &file_name) {
    if (!sd_mounted) return "Error: SD card not initialized.";
    if (file_name.empty()) return "Error: Empty file name";
    if (is_recording && current_file_name == file_name) return "Error: Unable delete current recording file!";
    const std::string path = std::string(MOUNT_POINT) + "/" + file_name;
    if (access(path.c_str(), F_OK) == 0) {
        if (unlink(path.c_str()) == 0) return "File " + file_name + " deleted";
        return "Error: Failed to delete " + file_name;
    }
    return "Error: File " + file_name + " not found";
}

static void host_file(int client_sock, const std::string &file_name) {
    if (!sd_mounted) {
        const char *msg = "Error: SD not mounted\n";
        send(client_sock, msg, strlen(msg), 0);
        return;
    }
    // Сброс буфера, чтобы файл содержал свежие данные записи
    if (is_recording && current_file_name == file_name) {
        flush_buffer_to_sd();
    }
    const std::string path = std::string(MOUNT_POINT) + "/" + file_name;
    struct stat st = {};
    if (stat(path.c_str(), &st) != 0 || st.st_size < 0) {
        const std::string msg = "Error: File not found\n";
        send(client_sock, msg.c_str(), msg.size(), 0);
        return;
    }
    FILE *f = nullptr;
    if (xSemaphoreTake(sd_mutex, pdMS_TO_TICKS(500)) == pdTRUE) {
        f = fopen(path.c_str(), "rb");
    } else {
        const char *msg = "Error: SD busy\n";
        send(client_sock, msg, strlen(msg), 0);
        return;
    }
    if (!f) {
        const std::string msg = "Error: Failed to open file " + file_name + "\n";
        send(client_sock, msg.c_str(), msg.size(), 0);
        xSemaphoreGive(sd_mutex);
        return;
    }
    // Отправляем размер, чтобы клиент мог показать прогресс
    {
        char header[64];
        int hdr_len = snprintf(header, sizeof(header), "SIZE %lld\n", static_cast<long long>(st.st_size));
        send(client_sock, header, hdr_len, 0);
    }
    std::vector<uint8_t> buf(CHUNK_SIZE);
    size_t read_bytes = 0;
    while ((read_bytes = fread(buf.data(), 1, buf.size(), f)) > 0) {
        size_t sent = 0;
        while (sent < read_bytes) {
            int n = send(client_sock, buf.data() + sent, read_bytes - sent, 0);
            if (n <= 0) break;
            sent += static_cast<size_t>(n);
        }
        // Небольшая уступка планировщику, чтобы не душить другие задачи
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    fclose(f);
    xSemaphoreGive(sd_mutex);
}

// ======================= Wi-Fi =======================================
static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        auto *event = static_cast<ip_event_got_ip_t *>(event_data);
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT | WIFI_READY_BIT);
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_START) {
        xEventGroupSetBits(wifi_event_group, WIFI_READY_BIT);
    }
}

static esp_err_t start_wifi(const WifiSettings &settings) {
    wifi_mode = settings.mode;
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    wifi_mode_t mode = (settings.mode == "other") ? WIFI_MODE_STA : WIFI_MODE_AP;
    if (mode == WIFI_MODE_STA) {
        sta_netif = esp_netif_create_default_wifi_sta();
    } else {
        ap_netif = esp_netif_create_default_wifi_ap();
    }

    wifi_config_t wifi_config = {};
    if (mode == WIFI_MODE_STA) {
        strncpy(reinterpret_cast<char *>(wifi_config.sta.ssid), settings.ssid.c_str(), sizeof(wifi_config.sta.ssid) - 1);
        strncpy(reinterpret_cast<char *>(wifi_config.sta.password), settings.pwd.c_str(), sizeof(wifi_config.sta.password) - 1);
        wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
        wifi_config.sta.pmf_cfg = { .capable = true, .required = false };
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    } else {
        strncpy(reinterpret_cast<char *>(wifi_config.ap.ssid), settings.ssid.c_str(), sizeof(wifi_config.ap.ssid) - 1);
        wifi_config.ap.ssid_len = static_cast<uint8_t>(strlen(reinterpret_cast<const char *>(wifi_config.ap.ssid)));
        if (settings.pwd.size() < 8) {
            wifi_config.ap.authmode = WIFI_AUTH_OPEN;
            wifi_config.ap.password[0] = '\0';
        } else {
            strncpy(reinterpret_cast<char *>(wifi_config.ap.password), settings.pwd.c_str(), sizeof(wifi_config.ap.password) - 1);
            wifi_config.ap.authmode = WIFI_AUTH_WPA_WPA2_PSK;
        }
        wifi_config.ap.max_connection = 4;
        wifi_config.ap.channel = 1;
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    }

    ESP_ERROR_CHECK(esp_wifi_start());

    if (mode == WIFI_MODE_STA) {
        EventBits_t bits = xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, pdMS_TO_TICKS(WIFI_CONNECT_TIMEOUT_MS));
        if (!(bits & WIFI_CONNECTED_BIT)) {
            ESP_LOGW(TAG, "WiFi STA connection timeout");
        }
    } else {
        xEventGroupWaitBits(wifi_event_group, WIFI_READY_BIT, pdFALSE, pdFALSE, pdMS_TO_TICKS(2000));
    }

    return ESP_OK;
}

static std::string get_ip() {
    esp_netif_ip_info_t ip_info = {};
    if (wifi_mode == "own") {
        if (ap_netif && esp_netif_get_ip_info(ap_netif, &ip_info) == ESP_OK) {
            return ip_to_string(ip_info);
        }
        return "192.168.4.1";
    }
    if (sta_netif && esp_netif_get_ip_info(sta_netif, &ip_info) == ESP_OK) {
        return ip_to_string(ip_info);
    }
    return "0.0.0.0";
}

static void configure_wifi(const std::string &wifi, const std::string &ssid, const std::string &pwd) {
    WifiSettings new_settings{wifi, ssid, pwd};
    save_wifi_settings(new_settings);
    ESP_LOGI(TAG, "WiFi settings saved. Restarting...");
    vTaskDelay(pdMS_TO_TICKS(200));
    esp_restart();
}

// ======================= Commands ====================================
static std::string check_recording_status() {
    return is_recording ? ("Recording to " + current_file_name) : "Not recording";
}

static std::string process_request(const std::string &raw_command) {
    const std::string command = trim(raw_command);
    if (command == "adc") {
        return read_adc_pretty();
    } else if (command == "ip") {
        return get_ip();
    } else if (command == "adsGain") {
        if (!ads_ready) return "ADS1115 not ready";
        return std::to_string(ads_gain_to_index(g_ads.gain));
    } else if (command.rfind("adsGain=", 0) == 0) {
        if (!ads_ready) return "ADS1115 not ready";
        std::string val = command.substr(8);
        AdsGain gain{};
        bool ok = false;
        const bool is_number = !val.empty() && std::all_of(val.begin(), val.end(), [](char c){ return (c >= '0' && c <= '9'); });
        if (is_number) {
            ok = index_to_ads_gain(std::atoi(val.c_str()), gain);
        } else {
            ok = parse_ads_gain(val, gain);
        }
        if (!ok) return "Error: Invalid gain value '" + val + "'. Use index 0..5 or 2/3,1,2,4,8,16";
        const bool was_sampling = sampling_enabled;
        sampling_enabled = false;
        vTaskDelay(pdMS_TO_TICKS(2));
        const int from_idx = ads_gain_to_index(g_ads.gain);
        g_ads.gain = gain;
        const int to_idx = ads_gain_to_index(g_ads.gain);
        sampling_enabled = was_sampling;
        ESP_LOGI(TAG, "ADS1115 gain changed %d -> %d (range %s)", from_idx, to_idx, ads_gain_range_str(g_ads.gain));
        return std::to_string(to_idx);

    } else if (command.rfind("wifi=", 0) == 0) {
        if (is_recording) return "Error: Unable setup wifi during recording!";
        const size_t sep1 = command.find(';');
        const size_t sep2 = command.rfind(';');
        if (sep1 == std::string::npos || sep2 == std::string::npos || sep2 <= sep1) return "Error: Invalid wifi command";
        const std::string wifi = command.substr(5, sep1 - 5);
        const std::string ssid = command.substr(sep1 + 6, sep2 - (sep1 + 6));
        const std::string pwd = command.substr(sep2 + 5);
        configure_wifi(wifi, ssid, pwd);
        return "Restarting to apply WiFi settings";

    } else if (command.rfind("start=", 0) == 0) {
        if (!sd_mounted) return "Error: SD card not initialized.";
        if (is_recording) return "Error: Unable to start new recording due to " + current_file_name;
        std::string name = trim(command.substr(6));
        if (name.empty() || name == "/") name = default_recording_name();
        // оставляем только базовое имя без путей
        const size_t slash = name.find_last_of('/');
        if (slash != std::string::npos) name = name.substr(slash + 1);
        name = sanitize_filename(name);
        current_file_name = name;
        is_recording = true;
        start_sampling();
        return "Recording started in " + current_file_name;

    } else if (command == "stop") {
        is_recording = false;
        flush_buffer_to_sd();
        std::string response = "Recording stopped in " + current_file_name;
        current_file_name.clear();
        return response;

    } else if (command.rfind("delete=", 0) == 0) {
        return delete_file(command.substr(7));

    } else if (command == "files") {
        return list_files();

    } else if (command == "checkRecording") {
        return check_recording_status();

    } else if (command == "deinitSD") {
        if (sd_mounted) {
            if (is_recording) {
                is_recording = false;
                flush_buffer_to_sd();
            }
            deinit_sd_card();
            return "SD card deinitialized. Safe to remove.";
        }
        return "SD card is already deinitialized.";

    } else if (command == "initSD") {
        if (!sd_mounted) {
            if (init_sd_card() == ESP_OK) return "SD card initialized.";
            return "Failed to initialize SD card.";
        }
        return "SD card is already initialized.";
    }

    return "command not found";
}

// ======================= Tasks ======================================
static void data_collection_task(void *param) {
    const TickType_t period = pdMS_TO_TICKS(1000 / OUTPUT_HZ);
    TickType_t last_wake = xTaskGetTickCount();
    sampling_enabled = true;
    while (true) {
        if (sampling_enabled && ads_ready) {
            float v[3];
            read_adc(v);
            DataPoint dp{static_cast<uint32_t>(esp_timer_get_time() / 1000), v[0], v[1], v[2]};
            rt_push_sample(dp);
            if (is_recording && sd_mounted) {
                if (xSemaphoreTake(sd_mutex, pdMS_TO_TICKS(2)) == pdTRUE) {
                    bool full = false;
                    if (sd_buffer_index < SD_BUFFER_SIZE) {
                        sd_buffer[sd_buffer_index] = dp;
                        full = (++sd_buffer_index >= SD_BUFFER_SIZE);
                    } else {
                        full = true;
                    }
                    xSemaphoreGive(sd_mutex);
                    if (full) flush_buffer_to_sd();
                }
            }
        }
        vTaskDelayUntil(&last_wake, period);
    }
}

static bool read_serial_line(std::string &out) {
    uint8_t ch;
    out.clear();
    while (true) {
        const int len = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(10));
        if (len <= 0) return false;
        if (ch == '\n') break;
        if (ch != '\r') out.push_back(static_cast<char>(ch));
        if (out.size() > 256) out.resize(256);
    }
    return true;
}

static void serial_command_task(void *param) {
    while (true) {
        std::string command;
        if (read_serial_line(command)) {
            const std::string response = process_request(command);
            printf("%s\n", response.c_str());
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

static bool recv_line(int sock, std::string &out) {
    char c;
    out.clear();
    while (true) {
        const int ret = recv(sock, &c, 1, 0);
        if (ret <= 0) return false;
        if (c == '\n') break;
        if (c != '\r') out.push_back(c);
        if (out.size() > 256) out.resize(256);
    }
    return true;
}

static void wifi_command_task(void *param) {
    // Wait until Wi-Fi is ready (AP started or STA got IP)
    xEventGroupWaitBits(wifi_event_group, WIFI_READY_BIT, pdFALSE, pdFALSE, portMAX_DELAY);

    const int listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (listen_sock < 0) {
        ESP_LOGE(TAG, "Unable to create socket");
        vTaskDelete(nullptr);
        return;
    }

    int opt = 1;
    setsockopt(listen_sock, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in server_addr{};
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(SERVER_PORT);
    server_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(listen_sock, reinterpret_cast<sockaddr *>(&server_addr), sizeof(server_addr)) != 0) {
        ESP_LOGE(TAG, "Socket bind failed");
        close(listen_sock);
        vTaskDelete(nullptr);
        return;
    }

    if (listen(listen_sock, 1) != 0) {
        ESP_LOGE(TAG, "Socket listen failed");
        close(listen_sock);
        vTaskDelete(nullptr);
        return;
    }

    ESP_LOGI(TAG, "Command server listening on port %d", SERVER_PORT);

    while (true) {
        sockaddr_in6 client_addr{};
        socklen_t addr_len = sizeof(client_addr);
        const int client_sock = accept(listen_sock, reinterpret_cast<sockaddr *>(&client_addr), &addr_len);
        if (client_sock < 0) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        ESP_LOGI(TAG, "Client connected");
        while (true) {
            std::string request;
            if (!recv_line(client_sock, request)) break;
            if (request.rfind("hostFile=", 0) == 0) {
                host_file(client_sock, request.substr(9));
                break; // close after sending file
            } else {
                const std::string response = process_request(request) + "\n";
                send(client_sock, response.c_str(), response.size(), 0);
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        shutdown(client_sock, 0);
        close(client_sock);
        ESP_LOGI(TAG, "Client disconnected");
    }
}

// ======================= Init ========================================
static void init_uart_console() {
    if (!uart_is_driver_installed(UART_NUM_0)) {
        uart_config_t cfg = {};
        cfg.baud_rate = 115200;
        cfg.data_bits = UART_DATA_8_BITS;
        cfg.parity = UART_PARITY_DISABLE;
        cfg.stop_bits = UART_STOP_BITS_1;
        cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
        cfg.rx_flow_ctrl_thresh = 0;
        cfg.source_clk = UART_SCLK_APB;
        uart_driver_install(UART_NUM_0, 2048, 0, 0, nullptr, 0);
        uart_param_config(UART_NUM_0, &cfg);
        uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    }
}

static esp_err_t init_i2c() {
    i2c_config_t conf = {};
    conf.mode = I2C_MODE_MASTER;
    conf.sda_io_num = I2C_SDA_PIN;
    conf.scl_io_num = I2C_SCL_PIN;
    conf.sda_pullup_en = GPIO_PULLUP_ENABLE;
    conf.scl_pullup_en = GPIO_PULLUP_ENABLE;
    conf.master.clk_speed = I2C_FREQ_HZ;
    conf.clk_flags = 0;
    ESP_ERROR_CHECK(i2c_param_config(I2C_PORT, &conf));
    ESP_ERROR_CHECK(i2c_driver_install(I2C_PORT, conf.mode, 0, 0, 0));
    // Увеличим таймаут битбенга на шине на всякий случай
    i2c_set_timeout(I2C_PORT, 0xFFFF);
    return ESP_OK;
}

extern "C" void app_main(void) {
    ESP_ERROR_CHECK(init_nvs());
    init_uart_console();
    wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(init_i2c());
    i2c_mutex = xSemaphoreCreateMutex();

    // ADS1115 init (check presence)
    ads_ready = true;
    uint16_t dummy = 0;
    esp_err_t ads_ok = ads_read_reg(0x00, dummy);
    if (ads_ok != ESP_OK) {
        ads_ready = false;
        ESP_LOGE(TAG, "Failed to initialize ADS1115 at 0x%02X: %s", g_ads.address, esp_err_to_name(ads_ok));
    } else {
        ESP_LOGI(TAG, "ADS1115 initialized at 0x%02X", g_ads.address);
        ESP_LOGI(TAG, "ADS1115 gain index %d (range %s)", ads_gain_to_index(g_ads.gain), ads_gain_range_str(g_ads.gain));
        ESP_LOGI(TAG, "ADS1115 data rate %d SPS", ads_data_rate_to_sps(g_ads.data_rate));
        ESP_LOGI(TAG, "Acquisition: OUTPUT_HZ=%d Hz, OVERSAMPLE=%d, EMA_ALPHA=%.3f", OUTPUT_HZ, OVERSAMPLE, EMA_ALPHA);
    }

    WifiSettings settings = load_wifi_settings();
    esp_event_handler_instance_t wifi_any_id;
    esp_event_handler_instance_t ip_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, nullptr, &wifi_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, nullptr, &ip_got_ip));
    start_wifi(settings);
    ESP_LOGI(TAG, "IP address: %s", get_ip().c_str());

    sd_mutex = xSemaphoreCreateMutex();
    init_sd_card();

    xTaskCreatePinnedToCore(serial_command_task, "serial_cmd", 4096, nullptr, 1, nullptr, 1);
    xTaskCreatePinnedToCore(wifi_command_task, "wifi_cmd", 4096, nullptr, 1, nullptr, 1);
    xTaskCreatePinnedToCore(data_collection_task, "data_collect", 4096, nullptr, 1, nullptr, 0);
}
