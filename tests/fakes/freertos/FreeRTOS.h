#pragma once
#include <cassert>
#include <cstdint>
using TickType_t = uint32_t;
constexpr int pdTRUE = 1, pdPASS = 1;
constexpr uint32_t portMAX_DELAY = UINT32_MAX;
#define configASSERT(expression) assert(expression)
#define pdMS_TO_TICKS(value) (value)
