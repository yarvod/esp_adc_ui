#pragma once
#include "application/daq_service.h"
#include "esp_http_server.h"
#include "freertos/queue.h"

namespace transport {
class HttpApi {
public:
    explicit HttpApi(application::DaqService &service) : service_(service) {}
    void start();
private:
    struct Work;
    application::DaqService &service_;
    httpd_handle_t server_ = nullptr;
    QueueHandle_t queue_ = nullptr;
    static esp_err_t accept(httpd_req_t *request);
    static void worker(void *context);
    void dispatch(Work &work);
};
}
