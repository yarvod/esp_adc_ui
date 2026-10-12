#include "application/daq_service.h"
#include "transport/http_protocol.h"
#include "ds1302.h"
#include <cassert>
#include <atomic>
#include <functional>
#include <future>
#include <iostream>
#include <map>
#include <limits>
#include <thread>

using namespace daq;
struct FakeAdc : AdcPort {
    bool available = true;
    int value = 1;
    std::array<float, 3> raw{100, 200, 300};
    std::function<void()> on_read;
    bool ready() const override { return available; }
    int gain() const override { return value; }
    Result set_gain(int gain) override { value = gain; return {}; }
    Result read(std::array<float, 3> &values) override {
        if (on_read) on_read();
        values = raw;
        return available ? Result{} : failure(Error::NotReady, "ADC disconnected");
    }
};
struct FakeClock : ClockPort {
    std::atomic<uint64_t> now{1791819045000ULL};
    uint64_t ticks = 0;
    uint32_t poll_ms = 20;
    uint64_t monotonic_ms() const override { return ticks; }
    void set_poll_interval(uint32_t value) override { poll_ms = value; }
    uint64_t timestamp_ms() override { return now; }
    Result set_utc(const std::string &utc) override {
        rtc::Calendar c;
        if (!rtc::parse(utc.c_str(), c)) return failure(Error::InvalidArgument, "Invalid UTC");
        now = rtc::epoch_seconds(c) * 1000;
        return {};
    }
};
struct FakeStorage : StoragePort {
    bool active = true, fail_write = false, partial_write = false;
    int append_calls = 0;
    int readers = 0;
    std::map<std::string, std::vector<Sample>> data;
    struct Reader : FileReader {
        FakeStorage &owner;
        explicit Reader(FakeStorage &owner) : owner(owner) { ++owner.readers; }
        ~Reader() override { --owner.readers; }
        uint64_t size() const override { return 0; }
        Result read(char *, size_t, size_t &bytes) override { bytes = 0; return {}; }
    };
    bool mounted() const override { return active; }
    Result mount() override { active = true; return {}; }
    Result unmount() override {
        if (readers) return failure(Error::Conflict, "Downloading");
        active = false;
        return {};
    }
    Result create(const std::string &name) override {
        if (!active) return failure(Error::NotReady, "SD removed");
        if (data.count(name)) return failure(Error::Conflict, "Exists");
        data[name] = {};
        return {};
    }
    Result append(const std::string &name, const Sample *samples, size_t count) override {
        ++append_calls;
        if (partial_write) return failure(Error::PartialWrite, "Unsafe partial write");
        if (fail_write) return failure(Error::Io, "Write failed");
        data[name].insert(data[name].end(), samples, samples + count);
        return {};
    }
    Result list(std::vector<FileInfo> &files) override { files.clear(); return {}; }
    Result remove(const std::string &name) override {
        if (readers) return failure(Error::Conflict, "Downloading");
        data.erase(name);
        return {};
    }
    Result open(const std::string &, std::unique_ptr<FileReader> &reader) override {
        reader.reset(new Reader(*this));
        return {};
    }
};
struct FakeNetwork : NetworkPort {
    std::string ip() const override { return "192.168.4.1"; }
    WifiSettings settings() const override { return {"own", "esp", "12345678"}; }
    Result configure(const WifiSettings &) override { return {}; }
};
struct FakeConfigStore : ConfigStorePort {
    AcquisitionConfig saved;
    bool exists = false, fail_save = false;
    int writes = 0;
    Result load(AcquisitionConfig &config) override {
        if (!exists) return failure(Error::NotFound, "No config");
        config = saved;
        return {};
    }
    Result save(const AcquisitionConfig &config) override {
        ++writes;
        if (fail_save) return failure(Error::Io, "NVS failed");
        saved = config;
        exists = true;
        return {};
    }
};

int main() {
    assert(wifi_station_enabled("both") && wifi_ap_enabled("both"));
    assert(wifi_station_enabled("other") && !wifi_ap_enabled("other"));
    assert(!wifi_station_enabled("own") && wifi_ap_enabled("own"));
    assert(validate_wifi({"both", "router", "12345678"}).ok());
    assert(!validate_wifi({"invalid", "router", "12345678"}).ok());
    assert(!validate_wifi({"both", "router", "123"}).ok());
    FakeAdc adc;
    FakeClock clock;
    FakeStorage storage;
    FakeNetwork network;
    application::DaqService service(adc, clock, storage, network);
    assert(protocol::route("/api/v1/files/test.txt") == protocol::Route::File);
    assert(protocol::route("/api/v1/filesOther") == protocol::Route::Unknown);
    assert(!valid_filename("../bad") && !valid_filename(".."));
    assert(!valid_filename("a/b.txt") && !valid_filename(".hidden"));
    assert(valid_filename("data_20261012_153045_123.txt"));
    Sample sample;
    assert(service.latest(sample).error == Error::NotReady);
    service.collect_sample();
    assert(service.latest(sample).ok() && sample.timestamp_ms == clock.now && sample.sequence == 1);

    // A device has one recording, with concurrent callers receiving explicit conflicts.
    std::atomic<int> accepted{0};
    std::vector<std::thread> clients;
    for (int i = 0; i < 8; ++i) clients.emplace_back([&, i] {
        auto result = service.start_recording("client_" + std::to_string(i) + ".txt");
        if (result.ok()) ++accepted;
        else assert(result.error == Error::Conflict);
    });
    for (auto &thread : clients) thread.join();
    assert(accepted == 1 && storage.data.size() == 1);
    const auto filename = service.recording().filename;
    assert(service.set_time("2026-10-12T16:00:00").error == Error::Conflict);
    assert(service.set_gain(2).error == Error::Conflict);
    assert(service.configure_wifi({"own", "esp", "12345678"}).error == Error::Conflict);
    assert(service.unmount_sd().error == Error::Conflict);
    assert(service.delete_file(filename).error == Error::Conflict);
    for (int i = 0; i < 5; ++i) { clock.now += 10; service.collect_sample(); }
    assert(service.stop_recording().ok() && storage.data[filename].size() == 5);

    std::unique_ptr<FileReader> reader;
    assert(service.download(filename, reader).ok());
    assert(service.unmount_sd().error == Error::Conflict);
    assert(service.delete_file(filename).error == Error::Conflict);
    service.collect_sample();
    assert(service.latest(sample).ok()); // Reader does not monopolize snapshots.
    reader.reset();
    assert(service.unmount_sd().ok());
    assert(service.start_recording("no_card.txt").error == Error::NotReady);
    assert(service.mount_sd().ok());

    // Loss of RTC stops recording, preserving the last valid points.
    assert(service.start_recording("clock_loss.txt").ok());
    service.collect_sample();
    clock.now = 0;
    service.collect_sample();
    assert(!service.recording().active && !service.recording().error.empty());
    assert(storage.data["clock_loss.txt"].size() == 1);
    assert(service.latest(sample).error == Error::NotReady);
    assert(service.set_time("2026-10-12T16:00:00").ok());

    // Failed writes keep the pending buffer; retries cannot start another file.
    assert(service.start_recording("retry.txt").ok());
    service.collect_sample();
    storage.fail_write = true;
    assert(service.stop_recording().error == Error::Io);
    assert(service.start_recording("new.txt").error == Error::Conflict);
    assert(service.unmount_sd().error == Error::Conflict);
    storage.fail_write = false;
    assert(service.stop_recording().ok());
    assert(storage.data["retry.txt"].size() == 1);
    assert(service.recording().error.empty());

    // A conversion started before a clock change cannot enter the new recording.
    std::promise<void> began, resume;
    auto gate = resume.get_future();
    adc.on_read = [&] { began.set_value(); gate.wait(); };
    std::thread conversion([&] { service.collect_sample(); });
    began.get_future().wait();
    assert(service.set_time("2026-10-12T17:00:00").ok());
    assert(service.start_recording("new_epoch.txt").ok());
    resume.set_value();
    conversion.join();
    adc.on_read = nullptr;
    assert(service.latest(sample).error == Error::NotReady);
    assert(service.stop_recording().ok() && storage.data["new_epoch.txt"].empty());
    assert(service.configure_wifi({"own", "esp", "12345678"}).ok());
    assert(service.start_recording("before_restart.txt").error == Error::Conflict);
    {
        FakeStorage failed_storage;
        application::DaqService failed_service(adc, clock, failed_storage, network);
        assert(failed_service.start_recording("partial.txt").ok());
        failed_service.collect_sample();
        failed_storage.partial_write = true;
        assert(failed_service.stop_recording().error == Error::PartialWrite);
        assert(failed_service.stop_recording().error == Error::PartialWrite);
        assert(failed_storage.append_calls == 1); // Retrying cannot duplicate uncertain rows.
    }
    {
        FakeAdc sensor;
        FakeClock time;
        FakeStorage card;
        FakeConfigStore store;
        application::DaqService configured(sensor, time, card, network, &store);
        assert(configured.initialize_config().error == Error::NotFound);
        auto config = configured.config();
        config.average_samples = 3;
        config.ema_alpha = 1;
        config.record_every = 2;
        config.flush_samples = 2;
        config.rtc_poll_interval_ms = 100;
        assert(configured.configure(config).ok());
        assert(store.writes == 1 && time.poll_ms == 100);
        assert(configured.configure(config).ok() && store.writes == 1); // No flash wear on unchanged PUT.
        assert(configured.start_recording("average.txt").ok());
        assert(configured.configure(config).error == Error::Conflict);
        for (int i = 1; i <= 9; ++i) {
            sensor.raw = {float(i), float(i * 2), float(i * 3)};
            time.now += 10;
            time.ticks += 10;
            configured.collect_sample();
            if (i < 3) assert(configured.latest(sample).error == Error::NotReady);
            if (i < 9) assert(card.data["average.txt"].empty());
        }
        const auto &rows = card.data["average.txt"];
        assert(rows.size() == 2 && rows[0].millivolts[0] == 2 && rows[1].millivolts[0] == 8);
        assert(rows[1].timestamp_ms == time.now && rows[1].sequence == 3);
        assert(configured.latest(sample).ok() && sample.millivolts[2] == 24);
        assert(configured.stop_recording().ok());

        // EMA is applied once per completed averaging window.
        config.ema_alpha = 0.5f;
        assert(configured.configure(config).ok());
        sensor.raw = {10, 10, 10};
        for (int i = 0; i < 3; ++i) configured.collect_sample();
        sensor.raw = {20, 20, 20};
        for (int i = 0; i < 3; ++i) configured.collect_sample();
        assert(configured.latest(sample).ok() && sample.millivolts[0] == 15);

        // Deadline flush does not require another ADC point or advancing RTC.
        config.average_samples = config.record_every = 1;
        config.flush_samples = 860;
        config.flush_interval_ms = 100;
        config.sample_interval_ms = 60000;
        assert(configured.configure(config).ok());
        assert(configured.start_recording("deadline.txt").ok());
        configured.collect_sample();
        time.ticks += 99;
        configured.flush_due();
        assert(card.data["deadline.txt"].empty());
        ++time.ticks;
        configured.flush_due();
        assert(card.data["deadline.txt"].size() == 1);
        assert(configured.stop_recording().ok());

        // Both config and the compatibility gain endpoint survive a simulated reboot.
        assert(configured.set_gain(3).ok() && store.saved.gain == 3);
        application::DaqService rebooted(sensor, time, card, network, &store);
        assert(rebooted.initialize_config().ok());
        assert(rebooted.config() == configured.config() && rebooted.sample_interval_ms() == 60000);
        auto invalid = configured.config();
        const int writes = store.writes;
        invalid.sample_interval_ms = 11;
        assert(!configured.configure(invalid).ok() && store.writes == writes);
        invalid = configured.config();
        invalid.ema_alpha = std::numeric_limits<float>::quiet_NaN();
        assert(!configured.configure(invalid).ok());
        store.fail_save = true;
        auto changed = configured.config();
        changed.gain = 5;
        assert(configured.configure(changed).error == Error::Io);
        assert(configured.config().gain == 3 && sensor.gain() == 3 && store.saved.gain == 3);
    }
    std::cout << "Acquisition config, averaging, persistence, concurrency and recording checks passed\n";
}
