#include "hardware/network.h"
#include "board_config.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include <cstring>
#include <atomic>
#if __has_include("wifi_config.local.h")
#include "wifi_config.local.h"
#define DAQ_LOCAL_WIFI_CONFIG 1
#endif

namespace hardware {
using WifiSettings = daq::WifiSettings;
static const char *TAG = "network";
static EventGroupHandle_t wifi_event_group;
constexpr EventBits_t WIFI_CONNECTED_BIT = BIT0;
constexpr EventBits_t WIFI_AP_READY_BIT = BIT1;
static std::atomic<bool> station_enabled{false};
static esp_netif_t *ap_netif = nullptr;
static esp_netif_t *sta_netif = nullptr;
static std::string wifi_mode = "own";
static std::string ip_to_string(const esp_netif_ip_info_t &ip) {
    char text[16];
    snprintf(text, sizeof(text), IPSTR, IP2STR(&ip.ip));
    return text;
}
static std::string preference(nvs_handle_t handle, const char *key, const std::string &fallback) {
    size_t size = 0;
    if (nvs_get_str(handle, key, nullptr, &size) != ESP_OK || !size) return fallback;
    std::string result(size, '\0');
    if (nvs_get_str(handle, key, &result[0], &size) != ESP_OK) return fallback;
    result.resize(strlen(result.c_str()));
    return result;
}
static esp_err_t save_settings(nvs_handle_t handle, const WifiSettings &settings) {
    esp_err_t err = nvs_set_str(handle, "wifi", settings.mode.c_str());
    if (err == ESP_OK) err = nvs_set_str(handle, "ssid", settings.ssid.c_str());
    if (err == ESP_OK) err = nvs_set_str(handle, "pwd", settings.password.c_str());
    return err;
}
#ifdef DAQ_LOCAL_WIFI_CONFIG
static void apply_local_settings(WifiSettings &settings) {
    const WifiSettings configured{"both", local_wifi::SSID, local_wifi::PASSWORD};
    if (!daq::validate_wifi(configured).ok()) {
        ESP_LOGE(TAG, "Invalid local Wi-Fi config; keeping saved settings");
        return;
    }
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wifi-settings", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Cannot open NVS for local Wi-Fi config");
        return;
    }
    uint32_t revision = 0;
    const bool applied = nvs_get_u32(handle, "local-revision", &revision) == ESP_OK && revision == local_wifi::REVISION;
    if (!applied) {
        err = save_settings(handle, configured);
        if (err == ESP_OK) err = nvs_set_u32(handle, "local-revision", local_wifi::REVISION);
        if (err == ESP_OK) err = nvs_commit(handle);
        if (err == ESP_OK) {
            settings = configured;
            ESP_LOGI(TAG, "Applied local Wi-Fi config: AP+STA, router '%s'", settings.ssid.c_str());
        } else ESP_LOGE(TAG, "Cannot save local Wi-Fi config: %s", esp_err_to_name(err));
    }
    nvs_close(handle);
}
#endif
static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        if (station_enabled) esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        auto *event = static_cast<wifi_event_sta_disconnected_t *>(event_data);
        ESP_LOGW(TAG, "Router disconnected: reason %u", static_cast<unsigned>(event->reason));
        xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
        if (station_enabled) esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        auto *event = static_cast<ip_event_got_ip_t *>(event_data);
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_START) {
        xEventGroupSetBits(wifi_event_group, WIFI_AP_READY_BIT);
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_STOP) {
        xEventGroupClearBits(wifi_event_group, WIFI_AP_READY_BIT);
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_LOST_IP) {
        xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void log_mac(const char *label, const uint8_t *mac) {
    ESP_LOGI(TAG, "%s: %02x:%02x:%02x:%02x:%02x:%02x", label, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}
static void set_wifi_identity(wifi_mode_t active_mode) {
    // Both interfaces retain their own native address in every operating mode.
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, board::WIFI_STA_MAC));
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_AP, board::WIFI_AP_MAC));
    ESP_ERROR_CHECK(esp_wifi_set_mode(active_mode));
    uint8_t actual[6];
    if (active_mode != WIFI_MODE_AP) {
        ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, actual));
        configASSERT(memcmp(actual, board::WIFI_STA_MAC, sizeof(actual)) == 0);
        log_mac("Active STA MAC", actual);
    }
    if (active_mode != WIFI_MODE_STA) {
        ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_AP, actual));
        configASSERT(memcmp(actual, board::WIFI_AP_MAC, sizeof(actual)) == 0);
        log_mac("Active AP MAC", actual);
    }
}

static void configure_ap(const std::string &ssid, const std::string &password) {
    wifi_config_t config{};
    memcpy(config.ap.ssid, ssid.data(), ssid.size());
    config.ap.ssid_len = static_cast<uint8_t>(ssid.size());
    if (password.empty()) config.ap.authmode = WIFI_AUTH_OPEN;
    else {
        memcpy(config.ap.password, password.data(), password.size());
        config.ap.authmode = WIFI_AUTH_WPA_WPA2_PSK;
    }
    config.ap.max_connection = 4;
    config.ap.channel = 1; // In AP+STA the driver follows the router's channel.
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &config));
    ESP_LOGI(TAG, "AP SSID: %s", ssid.c_str());
}
static esp_err_t start_wifi(const WifiSettings &settings) {
    wifi_mode = settings.mode;
    const bool use_sta = daq::wifi_station_enabled(settings.mode);
    const bool use_ap = daq::wifi_ap_enabled(settings.mode);
    station_enabled = use_sta;
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    if (use_sta) sta_netif = esp_netif_create_default_wifi_sta();
    if (use_ap) ap_netif = esp_netif_create_default_wifi_ap();
    const wifi_mode_t mode = use_sta && use_ap ? WIFI_MODE_APSTA : use_sta ? WIFI_MODE_STA : WIFI_MODE_AP;
    set_wifi_identity(mode);
    if (use_sta) {
        wifi_config_t config{};
        memcpy(config.sta.ssid, settings.ssid.data(), settings.ssid.size());
        memcpy(config.sta.password, settings.password.data(), settings.password.size());
        config.sta.threshold.authmode = settings.password.empty() ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
        config.sta.pmf_cfg = { .capable = true, .required = false };
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    }
    if (use_ap) {
        configure_ap(settings.mode == "both" ? board::WIFI_AP_SSID : settings.ssid,
                     settings.mode == "both" ? board::WIFI_AP_PASSWORD : settings.password);
    }
    ESP_ERROR_CHECK(esp_wifi_start());
    if (use_ap) {
        // AP+STA becomes accessible through AP immediately; STA connects in background.
        xEventGroupWaitBits(wifi_event_group, WIFI_AP_READY_BIT, pdFALSE, pdFALSE, pdMS_TO_TICKS(2000));
    } else {
        EventBits_t bits = xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE,
                                              pdMS_TO_TICKS(board::WIFI_TIMEOUT_MS));
        if (!(bits & WIFI_CONNECTED_BIT)) {
            ESP_LOGW(TAG, "STA unavailable; starting fallback AP '%s'", board::WIFI_AP_SSID);
            station_enabled = false;
            ESP_ERROR_CHECK(esp_wifi_stop());
            wifi_mode = "own";
            if (!ap_netif) ap_netif = esp_netif_create_default_wifi_ap();
            set_wifi_identity(WIFI_MODE_AP);
            configure_ap(board::WIFI_AP_SSID, board::WIFI_AP_PASSWORD);
            ESP_ERROR_CHECK(esp_wifi_start());
            xEventGroupWaitBits(wifi_event_group, WIFI_AP_READY_BIT, pdFALSE, pdFALSE, pdMS_TO_TICKS(2000));
        }
    }
    return ESP_OK;
}
static std::string interface_ip(esp_netif_t *interface) {
    esp_netif_ip_info_t info{};
    if (interface && esp_netif_get_ip_info(interface, &info) == ESP_OK && info.ip.addr)
        return ip_to_string(info);
    return "";
}
static std::string interface_mac(wifi_interface_t interface) {
    uint8_t mac[6];
    if (esp_wifi_get_mac(interface, mac) != ESP_OK) return "";
    char text[18];
    snprintf(text, sizeof(text), "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return text;
}


void Network::initialize() {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    uint8_t native_sta[6], native_ap[6];
    ESP_ERROR_CHECK(esp_read_mac(native_sta, ESP_MAC_WIFI_STA));
    ESP_ERROR_CHECK(esp_read_mac(native_ap, ESP_MAC_WIFI_SOFTAP));
    log_mac("Factory STA MAC", native_sta);
    log_mac("Factory AP MAC", native_ap);
    settings_ = {"own", board::WIFI_AP_SSID, board::WIFI_AP_PASSWORD};
    nvs_handle_t handle;
    if (nvs_open("wifi-settings", NVS_READONLY, &handle) == ESP_OK) {
        settings_.mode = preference(handle, "wifi", settings_.mode);
        settings_.ssid = preference(handle, "ssid", settings_.ssid);
        settings_.password = preference(handle, "pwd", settings_.password);
        nvs_close(handle);
    }
#ifdef DAQ_LOCAL_WIFI_CONFIG
    apply_local_settings(settings_);
#endif
    wifi_event_group = xEventGroupCreate();
    configASSERT(wifi_event_group);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, nullptr));
    if (!daq::validate_wifi(settings_).ok()) settings_ = {"own", board::WIFI_AP_SSID, board::WIFI_AP_PASSWORD};
    ESP_ERROR_CHECK(start_wifi(settings_));
    if (settings_.mode != wifi_mode) settings_ = {"own", board::WIFI_AP_SSID, board::WIFI_AP_PASSWORD};
    ESP_LOGI(TAG, "IP: %s", ip().c_str());
}
std::string Network::ip() const {
    const auto network = info();
    if (!network.sta_ip.empty()) return network.sta_ip;
    if (!network.ap_ip.empty()) return network.ap_ip;
    return "0.0.0.0";
}
daq::NetworkInfo Network::info() const {
    daq::NetworkInfo result;
    result.sta_enabled = daq::wifi_station_enabled(wifi_mode);
    result.ap_enabled = daq::wifi_ap_enabled(wifi_mode);
    result.sta_connected = result.sta_enabled && (xEventGroupGetBits(wifi_event_group) & WIFI_CONNECTED_BIT);
    if (result.sta_enabled) {
        result.sta_mac = interface_mac(WIFI_IF_STA);
        if (result.sta_connected) result.sta_ip = interface_ip(sta_netif);
    }
    if (result.ap_enabled) {
        result.ap_mac = interface_mac(WIFI_IF_AP);
        result.ap_ip = interface_ip(ap_netif);
        result.ap_ssid = wifi_mode == "both" ? board::WIFI_AP_SSID : settings().ssid;
    }
    return result;
}
daq::WifiSettings Network::settings() const {
    platform::Lock lock(mutex_);
    return settings_;
}
static void restart_task(void *) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
}
daq::Result Network::configure(const WifiSettings &settings) {
    const auto valid = daq::validate_wifi(settings);
    if (!valid.ok()) return valid;
    platform::Lock lock(mutex_);
    if (restarting_) return daq::failure(daq::Error::Conflict, "Wi-Fi restart already pending");
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wifi-settings", NVS_READWRITE, &handle);
    if (err == ESP_OK) {
        err = save_settings(handle, settings);
        if (err == ESP_OK) err = nvs_commit(handle);
        nvs_close(handle);
    }
    if (err != ESP_OK) return daq::failure(daq::Error::Io, "Cannot save Wi-Fi settings");
    if (xTaskCreate(restart_task, "wifi_restart", 2048, nullptr, 1, nullptr) != pdPASS)
        return daq::failure(daq::Error::Io, "Settings saved but restart could not be scheduled");
    restarting_ = true;
    return {};
}
}
