#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "board_config.h"

// DS1302 driver for the ESP-IDF firmware. RTC calendar is always UTC.
namespace rtc {
constexpr int RST_PIN = board::RTC_RST;
constexpr int DAT_PIN = board::RTC_DAT;
constexpr int CLK_PIN = board::RTC_CLK;

struct Calendar {
    int year, month, day, hour, minute, second;
};

inline int days_in_month(int year, int month) {
    static const int days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (month < 1 || month > 12) return 0;
    return days[month - 1] + (month == 2 && year % 4 == 0);
}

inline bool valid(const Calendar &c) {
    return c.year >= 2000 && c.year <= 2099 && c.month >= 1 && c.month <= 12 &&
           c.day >= 1 && c.day <= days_in_month(c.year, c.month) &&
           c.hour >= 0 && c.hour <= 23 && c.minute >= 0 && c.minute <= 59 &&
           c.second >= 0 && c.second <= 59;
}

inline bool parse(const char *text, Calendar &c) {
    if (strlen(text) != 19) return false;
    for (int i = 0; i < 19; ++i) {
        const char separator = i == 4 || i == 7 ? '-' : i == 10 ? 'T' : i == 13 || i == 16 ? ':' : 0;
        if (separator ? text[i] != separator : text[i] < '0' || text[i] > '9') return false;
    }
    return sscanf(text, "%4d-%2d-%2dT%2d:%2d:%2d", &c.year, &c.month, &c.day,
                  &c.hour, &c.minute, &c.second) == 6 && valid(c);
}

inline uint64_t epoch_seconds(const Calendar &c) {
    uint64_t days = 10957; // 1970-01-01 to 2000-01-01
    for (int y = 2000; y < c.year; ++y) days += y % 4 == 0 ? 366 : 365;
    for (int m = 1; m < c.month; ++m) days += days_in_month(c.year, m);
    return ((days + c.day - 1) * 24 + c.hour) * 3600 + c.minute * 60 + c.second;
}

inline Calendar calendar(uint64_t seconds) {
    Calendar c{2000, 1, 1, 0, 0, 0};
    uint64_t days = seconds / 86400 - 10957;
    while (days >= static_cast<uint64_t>(c.year % 4 == 0 ? 366 : 365)) {
        days -= c.year % 4 == 0 ? 366 : 365;
        ++c.year;
    }
    while (days >= static_cast<uint64_t>(days_in_month(c.year, c.month))) {
        days -= days_in_month(c.year, c.month);
        ++c.month;
    }
    c.day = static_cast<int>(days) + 1;
    c.hour = (seconds / 3600) % 24;
    c.minute = (seconds / 60) % 60;
    c.second = seconds % 60;
    return c;
}

// GPIO callbacks keep this driver independent of Arduino/ESP-IDF.
class Ds1302 {
public:
    Ds1302(void (*mode)(int, bool), void (*write)(int, bool), int (*read)(int), void (*delay)(uint32_t))
        : mode_(mode), write_(write), read_(read), delay_(delay) {}

    void begin() {
        write_(RST_PIN, false);
        write_(CLK_PIN, false);
        mode_(RST_PIN, true);
        mode_(CLK_PIN, true);
        mode_(DAT_PIN, false);
        // CR2032 is not rechargeable. Explicitly disable the trickle charger.
        write_register(0x8e, 0);
        write_register(0x90, 0);
        write_register(0x8e, 0x80);
    }

    bool read(Calendar &c) {
        uint8_t bytes[8];
        start();
        send(0xbf, true);
        for (auto &byte : bytes) byte = receive();
        stop();
        if ((bytes[0] & 0x80) || (bytes[1] & 0x80) || (bytes[2] & 0x40) ||
            (bytes[3] & 0xc0) || (bytes[4] & 0xe0) || bytes[5] < 1 || bytes[5] > 7 ||
            (bytes[7] & 0x7f)) return false;
        c = {decode(bytes[6]), decode(bytes[4]), decode(bytes[3]),
             decode(bytes[2] & (bytes[2] & 0x80 ? 0x1f : 0x3f)),
             decode(bytes[1]), decode(bytes[0])};
        c.year += 2000;
        if (bytes[2] & 0x80) {
            if (c.hour < 1 || c.hour > 12) return false;
            c.hour = c.hour % 12 + (bytes[2] & 0x20 ? 12 : 0);
        }
        return valid(c);
    }

    bool set(const Calendar &c) {
        if (!valid(c)) return false;
        write_register(0x8e, 0);
        write_register(0x90, 0);
        const uint8_t bytes[] = {encode(c.second), encode(c.minute), encode(c.hour),
            encode(c.day), encode(c.month), static_cast<uint8_t>((epoch_seconds(c) / 86400 + 4) % 7 + 1),
            encode(c.year - 2000), 0x80};
        start();
        send(0xbe);
        for (auto byte : bytes) send(byte);
        stop();
        Calendar actual;
        if (!read(actual)) return false;
        const uint64_t expected = epoch_seconds(c), received = epoch_seconds(actual);
        return received == expected || received == expected + 1;
    }

private:
    void (*mode_)(int, bool);
    void (*write_)(int, bool);
    int (*read_)(int);
    void (*delay_)(uint32_t);
    static uint8_t encode(int n) { return static_cast<uint8_t>((n / 10) * 16 + n % 10); }
    static int decode(uint8_t n) { return (n & 15) <= 9 && (n >> 4) <= 9 ? (n >> 4) * 10 + (n & 15) : -1; }
    void start() {
        write_(CLK_PIN, false);
        mode_(DAT_PIN, true);
        write_(RST_PIN, true);
        delay_(4);
    }
    void stop() { write_(RST_PIN, false); mode_(DAT_PIN, false); delay_(4); }
    void send(uint8_t byte, bool reading = false) {
        for (int bit = 0; bit < 8; ++bit) {
            write_(DAT_PIN, (byte >> bit) & 1);
            delay_(4);
            write_(CLK_PIN, true);
            delay_(4);
            // Release I/O before the falling edge that outputs the first read bit.
            if (reading && bit == 7) mode_(DAT_PIN, false);
            write_(CLK_PIN, false);
        }
    }
    uint8_t receive() {
        uint8_t byte = 0;
        for (int bit = 0; bit < 8; ++bit) {
            delay_(4);
            byte |= read_(DAT_PIN) << bit;
            write_(CLK_PIN, true);
            delay_(4);
            write_(CLK_PIN, false);
        }
        return byte;
    }
    void write_register(uint8_t address, uint8_t byte) { start(); send(address); send(byte); stop(); }
};

// Call under the firmware's RTC mutex. Poll every 20 ms to observe second edges.
class Clock {
public:
    explicit Clock(Ds1302 &device) : device_(device) {}
    void refresh(uint64_t monotonic_ms) {
        Calendar c;
        if (!device_.read(c)) { ready_ = false; return; }
        const uint64_t seconds = epoch_seconds(c);
        if (!ready_ || seconds != seconds_) {
            anchor_ms_ = monotonic_ms;
            seconds_ = seconds;
            if (!ready_) last_timestamp_ = 0;
            ready_ = true;
        }
        read_ms_ = monotonic_ms;
    }
    uint64_t timestamp(uint64_t monotonic_ms) {
        if (!ready_ || monotonic_ms - read_ms_ > 1500 || monotonic_ms - anchor_ms_ > 1500) return 0;
        uint64_t fraction = monotonic_ms - anchor_ms_;
        if (fraction > 999) fraction = 999;
        const uint64_t candidate = seconds_ * 1000 + fraction;
        if (candidate > last_timestamp_) last_timestamp_ = candidate;
        return last_timestamp_;
    }
    bool set(const Calendar &c, uint64_t monotonic_ms) {
        ready_ = false;
        last_timestamp_ = 0;
        if (!device_.set(c)) return false;
        refresh(monotonic_ms);
        return ready_;
    }
private:
    Ds1302 &device_;
    bool ready_ = false;
    uint64_t seconds_ = 0, anchor_ms_ = 0, read_ms_ = 0, last_timestamp_ = 0;
};
} // namespace rtc
