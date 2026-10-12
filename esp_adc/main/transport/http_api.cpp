#include "transport/http_api.h"
#include "transport/http_protocol.h"
#include "board_config.h"
#include "ds1302.h"
#include "cJSON.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include <cmath>
#include <memory>
#include <new>

namespace transport {
using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
static Json object() { return Json(cJSON_CreateObject(), cJSON_Delete); }
static void headers(httpd_req_t *request) {
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    httpd_resp_set_hdr(request, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(request, "Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS");
    httpd_resp_set_hdr(request, "Access-Control-Allow-Headers", "Content-Type");
}
static esp_err_t send_json(httpd_req_t *request, const cJSON *body, const char *status = "200 OK") {
    headers(request);
    httpd_resp_set_status(request, status);
    char *text = cJSON_PrintUnformatted(body);
    if (!text) return ESP_FAIL;
    const auto result = httpd_resp_send(request, text, HTTPD_RESP_USE_STRLEN);
    cJSON_free(text);
    return result;
}
static void error(httpd_req_t *request, const char *status, const char *code, const std::string &message) {
    auto body = object();
    auto *detail = cJSON_AddObjectToObject(body.get(), "error");
    cJSON_AddStringToObject(detail, "code", code);
    cJSON_AddStringToObject(detail, "message", message.c_str());
    send_json(request, body.get(), status);
}
static bool checked(httpd_req_t *request, const daq::Result &result) {
    if (result.ok()) return true;
    switch (result.error) {
        case daq::Error::InvalidArgument: error(request, "400 Bad Request", "invalid_argument", result.message); break;
        case daq::Error::NotReady: error(request, "503 Service Unavailable", "not_ready", result.message); break;
        case daq::Error::Conflict: error(request, "409 Conflict", "conflict", result.message); break;
        case daq::Error::NotFound: error(request, "404 Not Found", "not_found", result.message); break;
        default: error(request, "500 Internal Server Error", "io_error", result.message); break;
    }
    return false;
}
static cJSON *recording_json(const daq::RecordingStatus &status) {
    auto *result = cJSON_CreateObject();
    cJSON_AddBoolToObject(result, "recording", status.active);
    cJSON_AddStringToObject(result, "filename", status.filename.c_str());
    cJSON_AddStringToObject(result, "error", status.error.c_str());
    return result;
}
static bool string_field(const cJSON *body, const char *key, std::string &value, bool optional = false) {
    auto *item = cJSON_GetObjectItemCaseSensitive(body, key);
    if (!item && optional) { value.clear(); return true; }
    if (!cJSON_IsString(item)) return false;
    value = item->valuestring;
    return true;
}
static int closed_session_recv(httpd_handle_t, int, char *, size_t, int) {
    // Report intentional EOF to HTTPD when it reclaims a completed, shut-down
    // session. Calling lwIP recv instead would log ENOTCONN for every response.
    return 0;
}
static cJSON *config_json(const daq::AcquisitionConfig &config) {
    auto *body = cJSON_CreateObject();
    cJSON_AddNumberToObject(body, "sample_interval_ms", config.sample_interval_ms);
    cJSON_AddNumberToObject(body, "average_samples", config.average_samples);
    cJSON_AddNumberToObject(body, "ema_alpha", config.ema_alpha);
    cJSON_AddNumberToObject(body, "record_every", config.record_every);
    cJSON_AddNumberToObject(body, "flush_samples", config.flush_samples);
    cJSON_AddNumberToObject(body, "flush_interval_ms", config.flush_interval_ms);
    cJSON_AddNumberToObject(body, "rtc_poll_interval_ms", config.rtc_poll_interval_ms);
    cJSON_AddNumberToObject(body, "gain", config.gain);
    return body;
}
static daq::Result parse_config(const cJSON *body, daq::AcquisitionConfig &config) {
    const char *keys[] = {"sample_interval_ms", "average_samples", "ema_alpha", "record_every",
                          "flush_samples", "flush_interval_ms", "rtc_poll_interval_ms", "gain"};
    if (cJSON_GetArraySize(body) != 8)
        return daq::failure(daq::Error::InvalidArgument, "Supply all 8 config fields; read GET /api/v1/config first");
    double values[8];
    for (int i = 0; i < 8; ++i) {
        const auto *item = cJSON_GetObjectItemCaseSensitive(body, keys[i]);
        if (!cJSON_IsNumber(item) || !std::isfinite(item->valuedouble) || item->valuedouble < 0 ||
            item->valuedouble > 60000 || (i != 2 && item->valuedouble != std::floor(item->valuedouble)))
            return daq::failure(daq::Error::InvalidArgument, std::string("Invalid config field: ") + keys[i]);
        values[i] = item->valuedouble;
    }
    config = {static_cast<uint32_t>(values[0]), static_cast<uint32_t>(values[1]), static_cast<float>(values[2]),
              static_cast<uint32_t>(values[3]), static_cast<uint32_t>(values[4]), static_cast<uint32_t>(values[5]),
              static_cast<uint32_t>(values[6]), static_cast<int32_t>(values[7])};
    return daq::validate_config(config);
}

struct HttpApi::Work {
    httpd_req_t *request = nullptr;
    HttpApi *api = nullptr;
    std::string path, body;
};

esp_err_t HttpApi::accept(httpd_req_t *request) {
    auto &self = *static_cast<HttpApi *>(request->user_ctx);
    if (request->content_len > board::HTTP_MAX_BODY) {
        error(request, "413 Content Too Large", "body_too_large", "Maximum JSON body is 1024 bytes");
        return ESP_FAIL;
    }
    std::unique_ptr<Work> work(new (std::nothrow) Work);
    if (!work) return ESP_FAIL;
    work->api = &self;
    work->path = request->uri;
    const auto query = work->path.find('?');
    if (query != std::string::npos) work->path.resize(query);
    if (httpd_req_async_handler_begin(request, &work->request) != ESP_OK) return ESP_FAIL;
    Work *pointer = work.get();
    if (xQueueSend(self.queue_, &pointer, 0) != pdTRUE) {
        error(work->request, "503 Service Unavailable", "busy", "HTTP work queue full; retry later");
        httpd_req_async_handler_complete(work->request);
        return ESP_FAIL;
    }
    work.release();
    return ESP_OK;
}
void HttpApi::worker(void *context) {
    auto &self = *static_cast<HttpApi *>(context);
    UBaseType_t reported_free = board::HTTP_TASK_STACK_SIZE;
    while (true) {
        Work *pointer;
        if (xQueueReceive(self.queue_, &pointer, portMAX_DELAY) != pdTRUE) continue;
        std::unique_ptr<Work> work(pointer);
        const int socket = httpd_req_to_sockfd(work->request);
        self.dispatch(*work);
        // Every asynchronous request must be completed, including send/read failures.
        // Shut down while this async request still owns the session; the HTTP task
        // releases it after completion. No delayed close can target a reused fd.
        httpd_sess_set_recv_override(work->request->handle, socket, closed_session_recv);
        shutdown(socket, SHUT_RDWR);
        httpd_req_async_handler_complete(work->request);
        const UBaseType_t free_stack = uxTaskGetStackHighWaterMark(nullptr);
        if (free_stack + 128 < reported_free) {
            ESP_LOGI("http", "Worker stack minimum free: %u bytes", static_cast<unsigned>(free_stack));
            reported_free = free_stack;
        }
    }
}
void HttpApi::start() {
    queue_ = xQueueCreate(board::HTTP_QUEUE_LENGTH, sizeof(Work *));
    configASSERT(queue_);
    for (int i = 0; i < board::HTTP_WORKERS; ++i)
        configASSERT(xTaskCreate(worker, "http_worker", board::HTTP_TASK_STACK_SIZE, this, 2, nullptr) == pdPASS);
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = board::HTTP_PORT;
    config.max_open_sockets = board::HTTP_MAX_CONNECTIONS;
    config.max_uri_handlers = 5;
    config.max_resp_headers = 10;
    config.stack_size = board::HTTP_TASK_STACK_SIZE;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = board::HTTP_TIMEOUT_SECONDS;
    config.send_wait_timeout = board::HTTP_TIMEOUT_SECONDS;
    config.uri_match_fn = httpd_uri_match_wildcard;
    ESP_ERROR_CHECK(httpd_start(&server_, &config));
    for (auto method : {HTTP_GET, HTTP_PUT, HTTP_POST, HTTP_DELETE, HTTP_OPTIONS}) {
        httpd_uri_t route{};
        route.uri = "/*";
        route.method = method;
        route.handler = accept;
        route.user_ctx = this;
        ESP_ERROR_CHECK(httpd_register_uri_handler(server_, &route));
    }
    ESP_LOGI("http", "HTTP API listening on port %d with %d workers", board::HTTP_PORT, board::HTTP_WORKERS);
}

void HttpApi::dispatch(Work &work) {
    auto *request = work.request;
    // Receiving a slow request body also belongs to a worker, never the HTTP accept task.
    work.body.resize(request->content_len);
    const int64_t deadline = esp_timer_get_time() + board::HTTP_TIMEOUT_SECONDS * 1000000LL;
    size_t offset = 0;
    while (offset < work.body.size()) {
        if (esp_timer_get_time() >= deadline) {
            error(request, "408 Request Timeout", "request_timeout", "Incomplete JSON request body"); return;
        }
        const int received = httpd_req_recv(request, &work.body[offset], work.body.size() - offset);
        if (received <= 0) {
            error(request, "408 Request Timeout", "request_timeout", "Incomplete JSON request body"); return;
        }
        offset += received;
    }
    const auto route = protocol::route(work.path);
    if (route == protocol::Route::Unknown) {
        error(request, "404 Not Found", "not_found", "Unknown API endpoint");
        return;
    }
    if (request->method == HTTP_OPTIONS) {
        headers(request);
        httpd_resp_set_status(request, "204 No Content");
        httpd_resp_send(request, nullptr, 0);
        return;
    }
    const bool read = request->method == HTTP_GET;
    const bool put = request->method == HTTP_PUT;
    const bool post = request->method == HTTP_POST;
    const bool remove = request->method == HTTP_DELETE;
    using R = protocol::Route;
    const bool permitted =
        (read && route != R::Start && route != R::Stop && route != R::Mount && route != R::Unmount) ||
        (put && (route == R::Config || route == R::Gain || route == R::Rtc || route == R::Wifi)) ||
        (post && (route == R::Start || route == R::Stop || route == R::Mount || route == R::Unmount)) ||
        (remove && route == R::File);
    if (!permitted) {
        httpd_resp_set_hdr(request, "Allow", protocol::allowed(route));
        error(request, "405 Method Not Allowed", "method_not_allowed", "Method not allowed for this endpoint");
        return;
    }
    Json payload(nullptr, cJSON_Delete);
    if (put || post) {
        if (work.body.empty()) work.body = "{}";
        if (work.body.find('\0') == std::string::npos)
            payload.reset(cJSON_ParseWithLengthOpts(work.body.c_str(), work.body.size() + 1, nullptr, true));
        if (!cJSON_IsObject(payload.get())) {
            error(request, "400 Bad Request", "invalid_json", "Body must be a JSON object");
            return;
        }
    }
    auto response = object();
    switch (route) {
        case R::Status: {
            auto status = service_.status();
            cJSON_AddStringToObject(response.get(), "device", "esp_adc");
            cJSON_AddNumberToObject(response.get(), "api_version", protocol::API_VERSION);
            cJSON_AddStringToObject(response.get(), "ip", status.ip.c_str());
            cJSON_AddBoolToObject(response.get(), "adc_ready", status.adc_ready);
            cJSON_AddBoolToObject(response.get(), "rtc_ready", status.rtc_ready);
            cJSON_AddBoolToObject(response.get(), "sd_mounted", status.sd_mounted);
            cJSON_AddNumberToObject(response.get(), "gain", status.gain);
            const auto config = service_.config();
            cJSON_AddNumberToObject(response.get(), "sample_hz", 1000.0 / config.sample_interval_ms);
            cJSON_AddNumberToObject(response.get(), "output_hz", 1000.0 / config.sample_interval_ms / config.average_samples);
            cJSON_AddItemToObject(response.get(), "recording", recording_json(status.recording));
            break;
        }
        case R::Config: {
            if (put) {
                daq::AcquisitionConfig config;
                if (!checked(request, parse_config(payload.get(), config)) || !checked(request, service_.configure(config))) return;
            }
            response.reset(config_json(service_.config()));
            break;
        }
        case R::Ip:
            cJSON_AddStringToObject(response.get(), "ip", service_.status().ip.c_str());
            break;
        case R::Adc: {
            daq::Sample sample;
            if (!checked(request, service_.latest(sample))) return;
            cJSON_AddNumberToObject(response.get(), "timestamp_ms", sample.timestamp_ms);
            cJSON_AddNumberToObject(response.get(), "sequence", sample.sequence);
            for (int i = 0; i < 3; ++i) {
                char key[16];
                snprintf(key, sizeof(key), "adc%d_mv", i);
                cJSON_AddNumberToObject(response.get(), key, sample.millivolts[i]);
            }
            break;
        }
        case R::Gain:
            if (put) {
                auto *value = cJSON_GetObjectItemCaseSensitive(payload.get(), "gain");
                if (!cJSON_IsNumber(value) || value->valuedouble < 0 || value->valuedouble > 5 ||
                    value->valuedouble != std::floor(value->valuedouble)) {
                    error(request, "400 Bad Request", "invalid_argument", "gain must be an integer 0..5");
                    return;
                }
                if (!checked(request, service_.set_gain(static_cast<int>(value->valuedouble)))) return;
            }
            cJSON_AddNumberToObject(response.get(), "gain", service_.gain());
            break;
        case R::Rtc: {
            if (put) {
                std::string utc;
                if (!string_field(payload.get(), "utc", utc)) {
                    error(request, "400 Bad Request", "invalid_argument", "utc must be a string"); return;
                }
                if (!checked(request, service_.set_time(utc))) return;
            }
            const uint64_t now = service_.timestamp_ms();
            if (!checked(request, now ? daq::Result{} : daq::failure(daq::Error::NotReady, "RTC unavailable or not set"))) return;
            auto c = rtc::calendar(now / 1000);
            char utc[24];
            snprintf(utc, sizeof(utc), "%04d-%02d-%02dT%02d:%02d:%02dZ", c.year, c.month, c.day, c.hour, c.minute, c.second);
            cJSON_AddStringToObject(response.get(), "utc", utc);
            cJSON_AddNumberToObject(response.get(), "timestamp_ms", now);
            break;
        }
        case R::Wifi: {
            if (put) {
                daq::WifiSettings settings;
                if (!string_field(payload.get(), "mode", settings.mode) || !string_field(payload.get(), "ssid", settings.ssid) ||
                    !string_field(payload.get(), "password", settings.password)) {
                    error(request, "400 Bad Request", "invalid_argument", "mode, ssid and password must be strings"); return;
                }
                if (!checked(request, service_.configure_wifi(settings))) return;
                cJSON_AddStringToObject(response.get(), "message", "Wi-Fi settings saved; restarting in 1 second");
            } else {
                auto settings = service_.wifi();
                cJSON_AddStringToObject(response.get(), "mode", settings.mode.c_str());
                cJSON_AddStringToObject(response.get(), "ssid", settings.ssid.c_str());
                cJSON_AddStringToObject(response.get(), "ip", service_.status().ip.c_str());
                const auto network = service_.network_info();
                auto *station = cJSON_AddObjectToObject(response.get(), "station");
                cJSON_AddBoolToObject(station, "enabled", network.sta_enabled);
                cJSON_AddBoolToObject(station, "connected", network.sta_connected);
                cJSON_AddStringToObject(station, "ip", network.sta_ip.c_str());
                cJSON_AddStringToObject(station, "mac", network.sta_mac.c_str());
                auto *ap = cJSON_AddObjectToObject(response.get(), "access_point");
                cJSON_AddBoolToObject(ap, "enabled", network.ap_enabled);
                cJSON_AddStringToObject(ap, "ip", network.ap_ip.c_str());
                cJSON_AddStringToObject(ap, "mac", network.ap_mac.c_str());
                cJSON_AddStringToObject(ap, "ssid", network.ap_ssid.c_str());
            }
            break;
        }
        case R::Start: {
            std::string filename;
            if (!string_field(payload.get(), "filename", filename, true)) {
                error(request, "400 Bad Request", "invalid_argument", "filename must be a string"); return;
            }
            if (!checked(request, service_.start_recording(filename))) return;
            response.reset(recording_json(service_.recording()));
            cJSON_AddStringToObject(response.get(), "message", "Recording started");
            break;
        }
        case R::Stop:
            if (!checked(request, service_.stop_recording())) return;
            response.reset(recording_json(service_.recording()));
            cJSON_AddStringToObject(response.get(), "message", "Recording stopped");
            break;
        case R::Recording:
            response.reset(recording_json(service_.recording()));
            break;
        case R::Mount: case R::Unmount:
            if (!checked(request, route == R::Mount ? service_.mount_sd() : service_.unmount_sd())) return;
            cJSON_AddStringToObject(response.get(), "message", route == R::Mount ? "SD card mounted" : "SD card unmounted; safe to remove");
            break;
        case R::Files: {
            std::vector<daq::FileInfo> files;
            if (!checked(request, service_.files(files))) return;
            auto *array = cJSON_AddArrayToObject(response.get(), "files");
            for (const auto &file : files) {
                auto *item = cJSON_CreateObject();
                cJSON_AddStringToObject(item, "name", file.name.c_str());
                cJSON_AddNumberToObject(item, "size", file.size);
                cJSON_AddItemToArray(array, item);
            }
            break;
        }
        case R::File: {
            const std::string filename = work.path.substr(14);
            if (remove) {
                if (!checked(request, service_.delete_file(filename))) return;
                cJSON_AddStringToObject(response.get(), "message", "File deleted");
                break;
            }
            std::unique_ptr<daq::FileReader> reader;
            if (!checked(request, service_.download(filename, reader))) return;
            // A local array here enlarged the frame of EVERY dispatch, including
            // status requests, and overflowed the worker's 6 KiB stack.
            std::unique_ptr<char[]> buffer(new (std::nothrow) char[board::DOWNLOAD_CHUNK]);
            if (!buffer) {
                error(request, "503 Service Unavailable", "busy", "Download buffer unavailable; retry later");
                return;
            }
            headers(request);
            httpd_resp_set_type(request, "application/octet-stream");
            const std::string size = std::to_string(reader->size());
            const std::string disposition = "attachment; filename=\"" + filename + "\"";
            httpd_resp_set_hdr(request, "Content-Disposition", disposition.c_str());
            httpd_resp_set_hdr(request, "X-File-Size", size.c_str());
            httpd_resp_set_hdr(request, "Access-Control-Expose-Headers", "X-File-Size");
            uint64_t remaining = reader->size();
            while (remaining) {
                size_t bytes = 0;
                auto result = reader->read(buffer.get(), board::DOWNLOAD_CHUNK, bytes);
                if (!result.ok() || !bytes || httpd_resp_send_chunk(request, buffer.get(), bytes) != ESP_OK) return;
                remaining -= bytes;
                taskYIELD();
            }
            httpd_resp_send_chunk(request, nullptr, 0);
            return;
        }
        default: return;
    }
    send_json(request, response.get());
}
}
