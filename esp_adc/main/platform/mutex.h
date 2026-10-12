#pragma once

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace platform {
class Mutex {
public:
    Mutex() : handle_(xSemaphoreCreateMutex()) { configASSERT(handle_); }
    ~Mutex() { vSemaphoreDelete(handle_); }
    Mutex(const Mutex &) = delete;
    Mutex &operator=(const Mutex &) = delete;
    void lock() const { xSemaphoreTake(handle_, portMAX_DELAY); }
    void unlock() const { xSemaphoreGive(handle_); }
private:
    SemaphoreHandle_t handle_;
};
class Lock {
public:
    explicit Lock(const Mutex &mutex) : mutex_(mutex) { mutex_.lock(); }
    ~Lock() { mutex_.unlock(); }
    Lock(const Lock &) = delete;
    Lock &operator=(const Lock &) = delete;
private:
    const Mutex &mutex_;
};
}
