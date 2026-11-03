#include "extio_device.hpp"

#include <cmath>
#include <memory>

namespace {

// Supported sample rates are the ADC rate decimated by powers of two (the index Start() takes):
// 0..4 -> 2, 4, 8, 16, 32 MHz at 64 MHz, plus one more octave when the ADC runs above N2_BANDSWITCH.
int rate_count() { return adcnominalfreq > N2_BANDSWITCH ? 6 : 5; }

double rate_at(int index) {
    double base = adcnominalfreq / 64.0;
    if (adcnominalfreq > N2_BANDSWITCH) base /= 2;
    return base * 2.0 * (1 << index);
}

using StepsFn = int (RadioHandlerClass::*)(const float**) const;

// Index of the step closest to `gain_db`, or -1 if there are no steps
int nearest_step(const RadioHandlerClass& h, StepsFn steps_of, double gain_db) {
    const float* steps = nullptr;
    const int count = (h.*steps_of)(&steps);
    int best = -1;
    for (int i = 0; i < count; i++) {
        if (best < 0 || std::abs(steps[i] - gain_db) < std::abs(steps[best] - gain_db)) best = i;
    }
    return best;
}

float step_value(const RadioHandlerClass& h, StepsFn steps_of, int index) {
    const float* steps = nullptr;
    (h.*steps_of)(&steps);
    return steps[index];
}

}  // namespace

ExtIODevice::~ExtIODevice() {
    try { close(); } catch (...) {}
}

HardwareError ExtIODevice::not_open_error() const {
    return HardwareError(EXTIO_ERROR_NOT_OPEN, "RX888 device is not open");
}

void ExtIODevice::open() {
    close();

    std::unique_ptr<fx3class> fx3(CreateUsbHandler());
    if (!fx3 || !fx3->Open()) throw HardwareError(EXTIO_ERROR_NOT_FOUND, "No SDDC device found");

    auto handler = std::make_unique<RadioHandlerClass>();
    handler->Init(fx3.get(), &ExtIODevice::rx_trampoline, nullptr, this);
    if (handler->getModel() == NORADIO) {
        handler->Close();   // before fx3: the hardware powers itself down through it
        throw HardwareError(EXTIO_ERROR_NOT_FOUND, "Connected SDDC device is not a supported radio");
    }

    fx3_ = fx3.release();
    handle_ = handler.release();
    rate_index_ = 0;
}

void ExtIODevice::close() {
    if (!handle_) return;
    RadioHandlerClass* handler = handle_;
    handle_ = nullptr;
    handler->Stop();    // joins the streaming threads, so nothing can call rx_callback_ after this
    handler->Close();   // powers the hardware down; needs fx3_ still alive
    delete handler;
    delete fx3_;
    fx3_ = nullptr;
    streaming_ = false;
    rx_callback_ = nullptr;
    rf_gain_.reset();
    if_gain_.reset();
}

void ExtIODevice::rx_trampoline(void* context, const float* data, uint32_t length) {
    const size_t bytes = static_cast<size_t>(length) * 2 * sizeof(float);   // `length` counts I/Q pairs
    static_cast<ExtIODevice*>(context)->dispatch_rx(data, bytes, bytes);
}

void ExtIODevice::start_rx() {
    require()->Start(rate_index_);   // stops a running stream first
    streaming_ = true;
}

void ExtIODevice::stop_rx() {
    require()->Stop();
    streaming_ = false;
}

void ExtIODevice::set_freq(uint64_t freq_hz) {
    RadioHandlerClass* h = require();
    const rf_mode mode = h->PrepareLo(freq_hz);
    if (mode == NOMODE) throw HardwareError(EXTIO_ERROR_INVALID_PARAM, "Frequency is outside the device's range");
    if (mode != h->GetmodeRF()) {
        h->UpdatemodeRF(mode);
        apply_gains();
    }
    h->TuneLO(freq_hz);
}

double ExtIODevice::set_sample_rate(double rate_hz) {
    RadioHandlerClass* h = require();
    int best = 0;
    for (int i = 1; i < rate_count(); i++) {
        if (std::abs(rate_at(i) - rate_hz) < std::abs(rate_at(best) - rate_hz)) best = i;
    }
    rate_index_ = best;
    if (streaming_) h->Start(rate_index_);
    return rate_at(best);
}

std::vector<double> ExtIODevice::sample_rates() const {
    std::vector<double> rates;
    for (int i = 0; i < rate_count(); i++) rates.push_back(rate_at(i));
    return rates;
}

std::optional<double> ExtIODevice::set_rx_rf_gain(double gain_db) {
    RadioHandlerClass* h = require();
    rf_gain_ = gain_db;
    const int index = nearest_step(*h, &RadioHandlerClass::GetRFAttSteps, gain_db);
    if (index < 0) return std::nullopt;
    h->UpdateattRF(index);
    return step_value(*h, &RadioHandlerClass::GetRFAttSteps, index);
}

std::optional<double> ExtIODevice::set_rx_if_gain(double gain_db) {
    RadioHandlerClass* h = require();
    if_gain_ = gain_db;
    const int index = nearest_step(*h, &RadioHandlerClass::GetIFGainSteps, gain_db);
    if (index < 0) return std::nullopt;
    h->UpdateIFGain(index);
    return step_value(*h, &RadioHandlerClass::GetIFGainSteps, index);
}

// Switching inputs resets the radio's gain stages; restore what was asked for
void ExtIODevice::apply_gains() {
    if (rf_gain_) set_rx_rf_gain(*rf_gain_);
    if (if_gain_) set_rx_if_gain(*if_gain_);
}

void ExtIODevice::set_bias_t(bool enable) {
    RadioHandlerClass* h = require();
    if (h->GetmodeRF() == VHFMODE) h->UpdBiasT_VHF(enable);
    else h->UpdBiasT_HF(enable);
}

void ExtIODevice::set_dither(bool enable) { require()->UptDither(enable); }
void ExtIODevice::set_random(bool enable) { require()->UptRand(enable); }
void ExtIODevice::set_pga(bool enable) { require()->UptPga(enable); }

std::string ExtIODevice::name() const { return require()->getName(); }
uint16_t ExtIODevice::firmware() const { return require()->GetFirmware(); }
bool ExtIODevice::is_vhf() const { return require()->GetmodeRF() == VHFMODE; }
