idf_component_register(
    SRCS "main.cpp" "hardware/adc.cpp" "hardware/clock.cpp" "hardware/storage.cpp"
         "hardware/network.cpp" "hardware/config_store.cpp" "application/daq_service.cpp" "transport/http_api.cpp"
    PRIV_REQUIRES nvs_flash esp_wifi esp_event esp_netif driver sdmmc esp_timer fatfs vfs esp_http_server json
    INCLUDE_DIRS "."
)

# Keep room for ESP-IDF, filesystem and JSON calls on the FreeRTOS task stacks.
target_compile_options(${COMPONENT_LIB} PRIVATE -Werror=frame-larger-than=2048)

option(DAQ_HTTP_SMOKE_TEST "Run non-mutating HTTP stress test on the physical ESP32" OFF)
option(DAQ_CONFIG_SMOKE_TEST "Also test config writes across reboot, then restore original config" OFF)
if(DAQ_HTTP_SMOKE_TEST)
    target_sources(${COMPONENT_LIB} PRIVATE "../../tests/idf_http_smoke.cpp")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE DAQ_HTTP_SMOKE_TEST=1)
    if(DAQ_CONFIG_SMOKE_TEST)
        target_compile_definitions(${COMPONENT_LIB} PRIVATE DAQ_CONFIG_SMOKE_TEST=1)
    endif()
endif()
