idf_component_register(
    SRCS "main.cpp"
    PRIV_REQUIRES spi_flash nvs_flash esp_wifi esp_event esp_netif driver sdmmc esp_timer fatfs vfs
    INCLUDE_DIRS ""
)
