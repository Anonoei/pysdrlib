// Works around three upstream bugs in Core's Linux FX3 handler (fx3handler::Open), which would
// otherwise crash the interpreter. Applied with the linker's --wrap (see CMakeLists.txt).
//
//  - It never initializes `devidx`, only fx3handler::Enumerate() does and that dereferences a null
//    list when the device can't be opened. Only one device is supported, so always open the first.
//  - When the open fails (no device, or no permission) it still sends the device a command, through
//    a null handle. Make that a failed command, so Open() just returns false.
//  - Its USB event thread calls libusb_handle_events_completed(), which can block for up to 60 s when no
//    transfer is pending (as after the stream died and every transfer timed out). stop_rx()/close() join that
//    thread, so they hung that long, and with them whatever called them. Wait in short slices instead.
//  - streaming_stop() (streaming.c) waits for every bulk transfer in flight to finish BEFORE cancelling them, so on a
//    radio that sends nothing each runs out its 5 s timeout first. Cancel first, then wait for them to come back.
//    streaming_close() frees transfers whether or not they came back: one still in flight is left alone (leaked).
//    Both also dereference a stream that failed to open (null).
//  - Stopping the FX3 (STOPFX3) on a radio that no longer answers took the 5 s every command may: give it 0.5 s.
//
// The streaming wrappers mirror `struct streaming`, which is private to streaming.c. It is from the snapshot named in
// src/pysdrlib/extio/vendor/PROVENANCE: when bumping ExtIO_sddc, check that struct is still laid out as below.
#include <chrono>
#include <cstdio>

#include "ExtIO_sddc/Core/arch/linux/usb_device.h"
#include "ExtIO_sddc/Core/arch/linux/usb_device_internals.h"

namespace {

// ExtIO_sddc/Interface.h's FX3Command values the guards care about
constexpr uint8_t STOPFX3 = 0xAB;
constexpr unsigned SHORT_COMMAND_MS = 500;
constexpr auto STOP_DRAIN = std::chrono::milliseconds(1000);   // cancelled transfers come back in well under this

// streaming.c's `enum StreamingStatus` and `struct streaming` (see above)
enum MirrorStatus { STATUS_OFF, STATUS_READY, STATUS_STREAMING, STATUS_CANCELLED, STATUS_FAILED = 0xff };
struct streaming_mirror {
    MirrorStatus status;
    int random;
    usb_device_t *usb_device;
    uint32_t sample_rate;
    uint32_t frame_size;
    uint32_t num_frames;
    void (*callback)(uint32_t, uint8_t *, void *);
    void *callback_context;
    uint8_t **frames;
    struct libusb_transfer **transfers;
    int active_transfers;   // C11 atomic_int: same size and alignment as int; only accessed with __atomic builtins
};

int active(const streaming_mirror *s) { return __atomic_load_n(&s->active_transfers, __ATOMIC_SEQ_CST); }

}  // namespace

extern "C" {

usb_device_t *__real_usb_device_open(int index, const char *image, uint32_t size);
int __real_usb_device_control(usb_device_t *t, uint8_t request, uint16_t value,
                              uint16_t index, uint8_t *data, uint16_t length, int read);
int __real_streaming_stop(void *s);
void __real_streaming_close(void *s);

usb_device_t *__wrap_usb_device_open(int, const char *image, uint32_t size) {
    return __real_usb_device_open(0, image, size);
}

int __wrap_usb_device_control(usb_device_t *t, uint8_t request, uint16_t value,
                              uint16_t index, uint8_t *data, uint16_t length, int read) {
    if (t == nullptr) return -1;
    if (request == STOPFX3 && !read) {
        const uint8_t type = LIBUSB_ENDPOINT_OUT | LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE;
        return libusb_control_transfer(t->dev_handle, type, request, value, index, data, length, SHORT_COMMAND_MS) < 0 ? -1 : 0;
    }
    return __real_usb_device_control(t, request, value, index, data, length, read);
}

int __wrap_usb_device_handle_events(usb_device_t *t) {
    struct timeval slice = {0, 100000};
    return libusb_handle_events_timeout_completed(t->context, &slice, &t->completed);
}

int __wrap_streaming_stop(void *stream) {
    auto *s = static_cast<streaming_mirror *>(stream);
    if (s == nullptr) return -1;
    if (s->callback == nullptr) return __real_streaming_stop(stream);   // synchronous: nothing in flight
    s->status = STATUS_CANCELLED;   // the callback stops resubmitting
    for (uint32_t i = 0; i < s->num_frames; ++i) {
        libusb_cancel_transfer(s->transfers[i]);   // LIBUSB_ERROR_NOT_FOUND: already back
    }
    // Callers have joined the event thread: deliver the cancellations here
    const auto deadline = std::chrono::steady_clock::now() + STOP_DRAIN;
    while (active(s) > 0 && std::chrono::steady_clock::now() < deadline) {
        struct timeval slice = {0, 10000};
        libusb_handle_events_timeout_completed(s->usb_device->context, &slice, nullptr);
    }
    if (active(s) > 0) {
        fprintf(stderr, "ERROR - %d USB transfers did not come back after being cancelled\n", active(s));
        s->status = STATUS_FAILED;
    }
    return 0;
}

void __wrap_streaming_close(void *stream) {
    auto *s = static_cast<streaming_mirror *>(stream);
    if (s == nullptr) return;
    if (s->callback != nullptr && active(s) > 0) return;   // libusb still owns a transfer: freeing it would corrupt memory
    __real_streaming_close(stream);
}

}
