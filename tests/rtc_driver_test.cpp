#include "ds1302.h"
#include <array>
#include <cassert>
#include <iostream>
#include <vector>

// Emulate GPIO edges, including the read turnaround after command bit 7.
static std::array<uint8_t, 8> registers{0x45, 0x30, 0x15, 0x12, 0x10, 0x02, 0x26, 0x80};
static bool selected = false, clock_high = false, output = false, data = false;
static uint8_t command = 0, byte = 0;
static int bits = 0, read_bit = -1;
static std::vector<uint8_t> written;
static int charger = -1;

static void mode(int pin, bool value) { if (pin == rtc::DAT_PIN) output = value; }
static void write_pin(int pin, bool value) {
    if (pin == rtc::DAT_PIN) { data = value; return; }
    if (pin == rtc::RST_PIN) {
        if (value) {
            assert(!clock_high);
            selected = true;
            command = byte = 0;
            bits = 0;
            read_bit = -1;
            written.clear();
        } else if (selected) {
            if (command == 0x8e && written.size() == 1) registers[7] = written[0];
            if (command == 0x90 && written.size() == 1) charger = written[0];
            if (command == 0xbe && written.size() == 8 && !(registers[7] & 0x80)) {
                for (int i = 0; i < 8; ++i) registers[i] = written[i];
            }
            selected = false;
        }
        return;
    }
    if (pin != rtc::CLK_PIN) return;
    const bool rising = !clock_high && value, falling = clock_high && !value;
    clock_high = value;
    if (!selected) return;
    if (rising && (!command || !(command & 1))) {
        assert(output);
        byte |= static_cast<uint8_t>(data) << bits;
        if (++bits == 8) {
            if (!command) command = byte;
            else written.push_back(byte);
            bits = 0;
            byte = 0;
        }
    }
    if (falling && command == 0xbf) { assert(!output); ++read_bit; }
}
static int read_pin(int pin) {
    assert(pin == rtc::DAT_PIN && !output && selected && read_bit >= 0);
    return (registers[(read_bit / 8) % 8] >> (read_bit % 8)) & 1;
}
static void delay_us(uint32_t) {}

int main() {
    rtc::Calendar c;
    assert(rtc::parse("2024-02-29T23:59:59", c));
    assert(!rtc::parse("2023-02-29T23:59:59", c));
    assert(!rtc::parse("2026-10-12T24:00:00", c));
    assert(!rtc::parse("2100-01-01T00:00:00", c));
    assert(!rtc::parse("2026-10-12T12:00:00junk", c));
    assert(!rtc::parse("2026-10-12 12:00:00", c));
    assert(!rtc::parse("2026-10-12T-1:00:00", c));
    for (int year = 2000; year <= 2099; ++year) {
        for (int month = 1; month <= 12; ++month) {
            for (int day = 1; day <= rtc::days_in_month(year, month); ++day) {
                rtc::Calendar expected{year, month, day, 23, 59, 59};
                auto actual = rtc::calendar(rtc::epoch_seconds(expected));
                assert(actual.year == year && actual.month == month && actual.day == day);
                assert(actual.hour == 23 && actual.minute == 59 && actual.second == 59);
            }
        }
    }
    assert(rtc::epoch_seconds({2000, 1, 1, 0, 0, 0}) == 946684800ULL);
    assert(rtc::epoch_seconds({2040, 2, 29, 12, 0, 0}) == 2214129600ULL);
    rtc::Ds1302 device(mode, write_pin, read_pin, delay_us);
    device.begin();
    assert(charger == 0 && registers[7] == 0x80);
    assert(device.read(c) && c.year == 2026 && c.hour == 15 && c.second == 45);
    registers[2] = 0x92; // midnight in 12-hour format
    assert(device.read(c) && c.hour == 0);
    registers[2] = 0xb2; // noon
    assert(device.read(c) && c.hour == 12);
    registers[0] = 0x80;
    assert(!device.read(c)); // halted oscillator
    registers.fill(0xff);
    assert(!device.read(c)); // disconnected data line
    assert(device.set({2040, 2, 29, 12, 0, 0}));
    assert(charger == 0 && registers[7] == 0x80 && registers[2] == 0x12);
    rtc::Clock clock(device);
    clock.refresh(100);
    const uint64_t base = 2214129600000ULL;
    assert(clock.timestamp(100) == base);
    assert(clock.timestamp(125) == base + 25);
    clock.refresh(1099);
    assert(clock.timestamp(1099) == base + 999);
    registers[0] = 1;
    clock.refresh(1110);
    assert(clock.timestamp(1110) == base + 1000);
    assert(clock.timestamp(1120) == base + 1010);
    clock.refresh(2700); // valid registers but clock stopped ticking
    assert(clock.timestamp(2700) == 0);
    registers.fill(0xff);
    clock.refresh(2800);
    assert(clock.timestamp(2800) == 0);
    assert(clock.set({2026, 10, 12, 15, 30, 45}, 3000));
    assert(clock.timestamp(3000) == rtc::epoch_seconds({2026, 10, 12, 15, 30, 45}) * 1000);
    assert(clock.timestamp(4600) == 0); // no refresh: stale clock
    std::cout << "RTC driver and calendar checks passed\n";
}
