#include "application/daq_service.h"
#include "hardware/adc.h"
#include "hardware/clock.h"
#include "hardware/network.h"
#include "hardware/storage.h"
#include "hardware/config_store.h"
#include "transport/http_api.h"
#include "esp_log.h"

#ifdef DAQ_HTTP_SMOKE_TEST
void start_http_smoke_test(const char *host);
#endif

extern "C" void app_main() {
    // Composition root: hardware adapters -> application service -> HTTP transport.
    static hardware::Network network;
    static hardware::Adc adc;
    static hardware::Clock clock;
    static hardware::Storage storage;
    static hardware::ConfigStore config_store;
    static application::DaqService service(adc, clock, storage, network, &config_store);
    static transport::HttpApi http(service);

    clock.initialize();
    adc.initialize();
    auto mounted = storage.mount();
    if (!mounted.ok()) ESP_LOGW("main", "%s", mounted.message.c_str());
    network.initialize();
    const auto configured = service.initialize_config(); // Network initializes shared NVS first.
    if (!configured.ok() && configured.error != daq::Error::NotFound)
        ESP_LOGW("main", "%s", configured.message.c_str());
    const auto config = service.config();
    ESP_LOGI("main", "Config: sample=%lu ms average=%lu EMA=%.2f record_every=%lu flush=%lu/%lu ms RTC=%lu ms gain=%ld",
             static_cast<unsigned long>(config.sample_interval_ms), static_cast<unsigned long>(config.average_samples),
             config.ema_alpha, static_cast<unsigned long>(config.record_every), static_cast<unsigned long>(config.flush_samples),
             static_cast<unsigned long>(config.flush_interval_ms), static_cast<unsigned long>(config.rtc_poll_interval_ms),
             static_cast<long>(config.gain));
    application::start_sampling(service);
    http.start();
#ifdef DAQ_HTTP_SMOKE_TEST
    start_http_smoke_test(network.ip().c_str());
#endif
}
