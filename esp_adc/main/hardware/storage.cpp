#include "hardware/storage.h"
#include "board_config.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "esp_vfs_fat.h"
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace hardware {
static std::string path(const std::string &name) { return std::string(board::SD_MOUNT) + "/" + name; }
static daq::Result unavailable() { return daq::failure(daq::Error::NotReady, "SD card not mounted"); }
static daq::Result invalid_name() { return daq::failure(daq::Error::InvalidArgument, "Invalid filename"); }

class Storage::Reader : public daq::FileReader {
public:
    Reader(Storage &storage, std::string name, FILE *file, uint64_t size)
        : storage_(storage), name_(std::move(name)), file_(file), size_(size), remaining_(size) {}
    ~Reader() override {
        platform::Lock lock(storage_.mutex_);
        fclose(file_);
        if (--storage_.readers_[name_] == 0) storage_.readers_.erase(name_);
    }
    uint64_t size() const override { return size_; }
    daq::Result read(char *buffer, size_t capacity, size_t &bytes) override {
        platform::Lock lock(storage_.mutex_);
        bytes = fread(buffer, 1, std::min<uint64_t>(capacity, remaining_), file_);
        if (ferror(file_) || (!bytes && remaining_)) return daq::failure(daq::Error::Io, "SD read failed");
        remaining_ -= bytes;
        return {};
    }
private:
    Storage &storage_;
    std::string name_;
    FILE *file_;
    uint64_t size_, remaining_;
};

daq::Result Storage::mount() {
    platform::Lock lock(mutex_);
    if (mounted_) return {};
    spi_bus_config_t bus{};
    bus.mosi_io_num = board::SD_MOSI;
    bus.miso_io_num = board::SD_MISO;
    bus.sclk_io_num = board::SD_CLK;
    bus.quadwp_io_num = bus.quadhd_io_num = -1;
    bus.data4_io_num = bus.data5_io_num = bus.data6_io_num = bus.data7_io_num = -1;
    bus.max_transfer_sz = 4096;
    if (!bus_initialized_) {
        if (spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO) != ESP_OK)
            return daq::failure(daq::Error::Io, "SD SPI initialization failed");
        bus_initialized_ = true;
    }
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SPI2_HOST;
    host.max_freq_khz = board::SD_FREQUENCY_KHZ;
    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.gpio_cs = static_cast<gpio_num_t>(board::SD_CS);
    slot.host_id = SPI2_HOST;
    esp_vfs_fat_sdmmc_mount_config_t config{};
    config.format_if_mount_failed = false;
    config.max_files = 5;
    if (esp_vfs_fat_sdspi_mount(board::SD_MOUNT, &host, &slot, &config, &card_) != ESP_OK)
        return daq::failure(daq::Error::Io, "SD mount failed; check card and wiring");
    mounted_ = true;
    return {};
}
daq::Result Storage::unmount() {
    platform::Lock lock(mutex_);
    if (!readers_.empty()) return daq::failure(daq::Error::Conflict, "Wait for active downloads before removing SD");
    if (mounted_) {
        if (esp_vfs_fat_sdcard_unmount(board::SD_MOUNT, card_) != ESP_OK)
            return daq::failure(daq::Error::Io, "SD unmount failed");
        mounted_ = false;
        card_ = nullptr;
    }
    if (bus_initialized_) {
        if (spi_bus_free(SPI2_HOST) != ESP_OK) return daq::failure(daq::Error::Io, "SD SPI release failed");
        bus_initialized_ = false;
    }
    return {};
}
daq::Result Storage::create(const std::string &filename) {
    if (!daq::valid_filename(filename)) return invalid_name();
    platform::Lock lock(mutex_);
    if (!mounted_) return unavailable();
    int fd = ::open(path(filename).c_str(), O_CREAT | O_EXCL | O_WRONLY, 0666);
    if (fd < 0) return daq::failure(errno == EEXIST ? daq::Error::Conflict : daq::Error::Io,
                                  errno == EEXIST ? "File already exists; choose another name" : "Cannot create recording file");
    if (close(fd) != 0) return daq::failure(daq::Error::Io, "SD file close failed");
    return {};
}
daq::Result Storage::append(const std::string &filename, const daq::Sample *samples, size_t count) {
    if (!daq::valid_filename(filename)) return invalid_name();
    platform::Lock lock(mutex_);
    if (!mounted_) return unavailable();
    FILE *file = fopen(path(filename).c_str(), "a");
    if (!file) return daq::failure(daq::Error::Io, "Cannot open recording file");
    struct stat before{};
    if (fstat(fileno(file), &before) != 0) {
        fclose(file);
        return daq::failure(daq::Error::Io, "Cannot checkpoint recording size");
    }
    bool ok = true;
    for (size_t i = 0; i < count && ok; ++i) {
        const auto &sample = samples[i];
        ok = fprintf(file, "%llu; %.1f; %.1f; %.1f\n", static_cast<unsigned long long>(sample.timestamp_ms),
                     sample.millivolts[0], sample.millivolts[1], sample.millivolts[2]) > 0;
    }
    if (fflush(file) != 0) ok = false;
    if (fclose(file) != 0) ok = false;
    if (ok) return {};
    // A failed batch must not be appended again on top of a partially written batch.
    const int fd = ::open(path(filename).c_str(), O_WRONLY);
    bool rolled_back = fd >= 0 && ftruncate(fd, before.st_size) == 0;
    if (fd >= 0 && close(fd) != 0) rolled_back = false;
    return daq::failure(rolled_back ? daq::Error::Io : daq::Error::PartialWrite,
        rolled_back ? "SD write failed; batch rolled back, retry stop after fixing SD" :
                      "Partial SD write could not be rolled back; inspect file before restarting device");
}
daq::Result Storage::list(std::vector<daq::FileInfo> &files) {
    platform::Lock lock(mutex_);
    if (!mounted_) return unavailable();
    DIR *directory = opendir(board::SD_MOUNT);
    if (!directory) return daq::failure(daq::Error::Io, "Cannot list SD directory");
    files.clear();
    while (auto *entry = readdir(directory)) {
        std::string name(entry->d_name);
        if (!daq::valid_filename(name)) continue;
        bool system = false;
        for (auto prefix : {"SYSTEM~", "FSEVE~", "SPOTL~", "TRASH~"})
            if (name.compare(0, strlen(prefix), prefix) == 0) system = true;
        struct stat info{};
        if (!system && stat(path(name).c_str(), &info) == 0 && S_ISREG(info.st_mode))
            files.push_back({name, static_cast<uint64_t>(info.st_size)});
    }
    closedir(directory);
    return {};
}
daq::Result Storage::remove(const std::string &filename) {
    if (!daq::valid_filename(filename)) return invalid_name();
    platform::Lock lock(mutex_);
    if (!mounted_) return unavailable();
    if (readers_.count(filename)) return daq::failure(daq::Error::Conflict, "File is being downloaded");
    if (unlink(path(filename).c_str()) == 0) return {};
    return daq::failure(errno == ENOENT ? daq::Error::NotFound : daq::Error::Io, "Cannot delete file");
}
daq::Result Storage::open(const std::string &filename, std::unique_ptr<daq::FileReader> &reader) {
    if (!daq::valid_filename(filename)) return invalid_name();
    reader.reset(); // Release an earlier lease before acquiring the storage mutex.
    platform::Lock lock(mutex_);
    if (!mounted_) return unavailable();
    int count = 0;
    for (const auto &entry : readers_) count += entry.second;
    if (count >= board::MAX_DOWNLOADS) return daq::failure(daq::Error::Conflict, "Too many active downloads");
    FILE *file = fopen(path(filename).c_str(), "rb");
    if (!file) return daq::failure(errno == ENOENT ? daq::Error::NotFound : daq::Error::Io, "Cannot open file");
    struct stat info{};
    if (fstat(fileno(file), &info) != 0 || !S_ISREG(info.st_mode) || info.st_size < 0) {
        fclose(file);
        return daq::failure(daq::Error::Io, "Cannot read file size");
    }
    ++readers_[filename];
    reader.reset(new Reader(*this, filename, file, static_cast<uint64_t>(info.st_size)));
    return {};
}
}
