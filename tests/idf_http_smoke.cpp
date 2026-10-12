// Opt-in physical-board regression test. Calls the real HTTP server through lwIP.
// Default: no writes. DAQ_CONFIG_SMOKE_TEST additionally writes acquisition config,
// verifies it across reboot and restores the original config; never modifies SD files.
#include "board_config.h"
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include <atomic>
#include <cstdio>
#include <cstring>
#include <string>
#ifdef DAQ_CONFIG_SMOKE_TEST
#include "nvs.h"
#include "esp_system.h"
#endif

namespace {
constexpr int CLIENTS = 3; // 3 server sockets + 3 clients + HTTP control/listener <= 10.
constexpr int REQUESTS = 200;
std::string host;
std::atomic<int> finished{0}, failures{0}, successes{0};
struct Case { const char *method, *path, *body; int expected, alternate; };
const Case cases[] = {
    {"GET", "/api/v1/status", "", 200, 200},
    {"GET", "/api/v1/config", "", 200, 200},
    {"PUT", "/api/v1/config", "{\"sample_interval_ms\":0}", 400, 400},
    {"GET", "/api/v1/wifi", "", 200, 200},
    {"GET", "/api/v1/ip", "", 200, 200},
    {"GET", "/api/v1/adc/gain", "", 200, 200},
    {"GET", "/api/v1/rtc", "", 200, 503},
    {"GET", "/api/v1/adc", "", 200, 503},
    {"GET", "/api/v1/files", "", 200, 503},
    {"GET", "/api/v1/recording", "", 200, 200},
    {"PUT", "/api/v1/adc/gain", "{\"gain\":99}", 400, 400},
    {"PUT", "/api/v1/rtc", "{", 400, 400},
    {"POST", "/api/v1/status", "{}", 405, 405},
    {"GET", "/api/v1/missing", "", 404, 404},
    {"OPTIONS", "/api/v1/status", "", 204, 204},
};

bool request(const Case &test, std::string *out_body = nullptr) {
    const int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) return false;
    timeval timeout{5, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(board::HTTP_PORT);
    inet_pton(AF_INET, host.c_str(), &address.sin_addr);
    if (connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0) {
        close(fd);
        return false;
    }
    std::string wire = std::string(test.method) + " " + test.path +
        " HTTP/1.1\r\nHost: " + host + "\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: " +
        std::to_string(strlen(test.body)) + "\r\n\r\n" + test.body;
    size_t sent = 0;
    while (sent < wire.size()) {
        int count = send(fd, wire.data() + sent, wire.size() - sent, 0);
        if (count <= 0) { close(fd); return false; }
        sent += count;
    }
    std::string response;
    char chunk[512];
    int count;
    while ((count = recv(fd, chunk, sizeof(chunk), 0)) > 0 && response.size() < 8192)
        response.append(chunk, count);
    close(fd);
    int status = 0;
    if (count < 0 || sscanf(response.c_str(), "HTTP/1.1 %d", &status) != 1 ||
        (status != test.expected && status != test.alternate)) return false;
    const auto separator = response.find("\r\n\r\n");
    if (separator == std::string::npos) return false;
    const std::string body = response.substr(separator + 4);
    const auto length_header = response.find("Content-Length: ");
    unsigned length = 0;
    if (length_header == std::string::npos ||
        sscanf(response.c_str() + length_header, "Content-Length: %u", &length) != 1 || length != body.size()) return false;
    if (status == 204) return body.empty();
    cJSON *json = cJSON_Parse(body.c_str());
    bool valid = cJSON_IsObject(json);
    if (valid && status >= 400) valid = cJSON_IsObject(cJSON_GetObjectItemCaseSensitive(json, "error"));
    if (valid && !strcmp(test.path, "/api/v1/status") && status == 200)
        valid = cJSON_IsNumber(cJSON_GetObjectItemCaseSensitive(json, "api_version"));
    cJSON_Delete(json);
    if (valid && out_body) *out_body = body;
    return valid;
}

void client(void *arg) {
    const int id = static_cast<int>(reinterpret_cast<intptr_t>(arg));
    for (int i = 0; i < REQUESTS; ++i) {
        const auto &test = cases[(i + id) % (sizeof(cases) / sizeof(cases[0]))];
        if (request(test)) ++successes;
        else {
            ++failures;
            ESP_LOGE("http_test", "Client %d request %d failed: %s %s", id, i, test.method, test.path);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    ESP_LOGI("http_test", "Client %d finished; stack free %u", id,
             static_cast<unsigned>(uxTaskGetStackHighWaterMark(nullptr)));
    if (++finished == CLIENTS) {
        ESP_LOGI("http_test", "RESULT: %d passed, %d failed; internal heap free=%u minimum=%u",
                 successes.load(), failures.load(),
                 static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
                 static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)));
    }
    vTaskDelete(nullptr);
}

#ifdef DAQ_CONFIG_SMOKE_TEST
std::string saved_text(nvs_handle_t handle, const char *key) {
    size_t size = 0;
    ESP_ERROR_CHECK(nvs_get_str(handle, key, nullptr, &size));
    std::string value(size, '\0');
    ESP_ERROR_CHECK(nvs_get_str(handle, key, &value[0], &size));
    value.resize(size - 1);
    return value;
}
bool same_json(const std::string &a, const std::string &b) {
    auto *left = cJSON_Parse(a.c_str());
    auto *right = cJSON_Parse(b.c_str());
    const bool equal = left && right && cJSON_Compare(left, right, true);
    cJSON_Delete(left);
    cJSON_Delete(right);
    return equal;
}
void config_test(void *) {
    nvs_handle_t handle;
    ESP_ERROR_CHECK(nvs_open("cfg-selftest", NVS_READWRITE, &handle));
    uint8_t phase = 0;
    nvs_get_u8(handle, "phase-v2", &phase);
    std::string current;
    configASSERT(request({"GET", "/api/v1/config", "", 200, 200}, &current));
    if (phase == 0) {
        ESP_ERROR_CHECK(nvs_set_str(handle, "original", current.c_str()));
        auto *modified = cJSON_Parse(current.c_str());
        const char *keys[] = {"sample_interval_ms", "average_samples", "ema_alpha", "record_every",
                             "flush_samples", "flush_interval_ms", "rtc_poll_interval_ms", "gain"};
        const double values[] = {20, 4, 1, 2, 16, 500, 50, 2};
        for (int i = 0; i < 8; ++i) cJSON_SetNumberValue(cJSON_GetObjectItemCaseSensitive(modified, keys[i]), values[i]);
        char *encoded = cJSON_PrintUnformatted(modified);
        const std::string expected(encoded);
        cJSON_free(encoded);
        cJSON_Delete(modified);
        ESP_ERROR_CHECK(nvs_set_str(handle, "expected", expected.c_str()));
        ESP_ERROR_CHECK(nvs_set_u8(handle, "phase-v2", 1));
        ESP_ERROR_CHECK(nvs_commit(handle));
        nvs_close(handle);
        configASSERT(request({"PUT", "/api/v1/config", expected.c_str(), 200, 200}, &current));
        configASSERT(same_json(current, expected));
        // Invalid complete configs must not replace the successfully persisted one.
        const char *invalid = "{\"sample_interval_ms\":0,\"average_samples\":4,\"ema_alpha\":1,\"record_every\":2,\"flush_samples\":16,\"flush_interval_ms\":500,\"rtc_poll_interval_ms\":50,\"gain\":2}";
        configASSERT(request({"PUT", "/api/v1/config", invalid, 400, 400}));
        ESP_LOGI("config_test", "Config saved through HTTP; restarting to verify NVS");
        vTaskDelay(pdMS_TO_TICKS(100));
        esp_restart();
    } else if (phase == 1) {
        const auto expected = saved_text(handle, "expected");
        const auto original = saved_text(handle, "original");
        const bool survived = same_json(current, expected);
        configASSERT(request({"PUT", "/api/v1/config", original.c_str(), 200, 200}, &current));
        configASSERT(same_json(current, original));
        configASSERT(survived);
        ESP_ERROR_CHECK(nvs_set_u8(handle, "phase-v2", 2));
        ESP_ERROR_CHECK(nvs_erase_key(handle, "expected"));
        ESP_ERROR_CHECK(nvs_erase_key(handle, "original"));
        ESP_ERROR_CHECK(nvs_commit(handle));
        nvs_close(handle);
        ESP_LOGI("config_test", "PASS: all 8 fields survived reboot; original config restored");
    } else {
        nvs_close(handle);
        ESP_LOGI("config_test", "NVS reboot test already completed");
    }
    for (int i = 0; i < CLIENTS; ++i)
        configASSERT(xTaskCreate(client, "http_test", 4096, reinterpret_cast<void *>(i), 1, nullptr) == pdPASS);
    vTaskDelete(nullptr);
}
#endif
}

void start_http_smoke_test(const char *ip) {
    host = ip;
    ESP_LOGI("http_test", "Starting %d clients, %d requests each on %s", CLIENTS, REQUESTS, host.c_str());
#ifdef DAQ_CONFIG_SMOKE_TEST
    configASSERT(xTaskCreate(config_test, "config_test", 6144, nullptr, 1, nullptr) == pdPASS);
#else
    for (int i = 0; i < CLIENTS; ++i)
        configASSERT(xTaskCreate(client, "http_test", 4096, reinterpret_cast<void *>(i), 1, nullptr) == pdPASS);
#endif
}
