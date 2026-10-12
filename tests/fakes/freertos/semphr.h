#pragma once
#include <mutex>
#include "freertos/FreeRTOS.h"
using SemaphoreHandle_t = std::mutex *;
inline SemaphoreHandle_t xSemaphoreCreateMutex() { return new std::mutex; }
inline int xSemaphoreTake(SemaphoreHandle_t handle, uint32_t) { handle->lock(); return pdTRUE; }
inline void xSemaphoreGive(SemaphoreHandle_t handle) { handle->unlock(); }
inline void vSemaphoreDelete(SemaphoreHandle_t handle) { delete handle; }
