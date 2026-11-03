#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "RadioHandler.h"
#include "hardware_wrapper.hpp"

enum ExtIOErrorCode {
    EXTIO_ERROR_INVALID_PARAM = -2,
    EXTIO_ERROR_NOT_FOUND = -5,
    EXTIO_ERROR_NOT_OPEN = -6,
};

// Core's USB enumeration: number of SDDC devices (bootloader or running) attached; -1 if USB failed.
// Needs no permissions to the device.
extern "C" int usb_device_count_devices();

// Wraps ExtIO_sddc's RadioHandlerClass (BBRF103, RX888 and its mkII/mkIII, RX999, HF103...).
// The handler does the USB streaming and the real->IQ DDC itself, and delivers float I/Q.
class ExtIODevice : public HardwareDevice<RadioHandlerClass*> {
public:
    ~ExtIODevice() override;

    // Finds the device, uploads its firmware and initializes the radio
    void open();
    void close() override;

    // The DDC delivers interleaved float32 I/Q, already scaled
    SampleFormat rx_sample_format() const override { return {SampleKind::Float, 32, 32, false}; }

    // Streaming. The callback runs on the handler's thread; its return value is ignored (stop with stop_rx()).
    void start_rx() override;
    void stop_rx() override;
    bool is_streaming() const override { return streaming_; }

    // Tuning. Frequencies are for the active input, chosen from the frequency: HF direct sampling below
    // half the ADC rate, otherwise the VHF/UHF tuner.
    void set_freq(uint64_t freq_hz);
    // Selects the supported rate nearest `rate_hz` (restarting the stream if running); returns it.
    double set_sample_rate(double rate_hz);
    std::vector<double> sample_rates() const;

    // Gain stages, in dB, snapped to the nearest step of the active input. nullopt: the stage doesn't
    // exist on this model/input. Re-applied when a frequency change switches inputs.
    std::optional<double> set_rx_rf_gain(double gain_db);   // attenuator (HF) / tuner RF gain (VHF)
    std::optional<double> set_rx_if_gain(double gain_db);   // VGA / tuner IF gain

    void set_bias_t(bool enable);   // on the active input
    void set_dither(bool enable);
    void set_random(bool enable);
    void set_pga(bool enable);

    std::string name() const;
    uint16_t firmware() const;
    bool is_vhf() const;

protected:
    HardwareError not_open_error() const override;

private:
    static void rx_trampoline(void* context, const float* data, uint32_t length);
    void apply_gains();

    fx3class* fx3_ = nullptr;
    int rate_index_ = 0;
    bool streaming_ = false;
    std::optional<double> rf_gain_, if_gain_;
};
