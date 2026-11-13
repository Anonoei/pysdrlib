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
#include "ExtIO_sddc/Core/arch/linux/usb_device.h"
#include "ExtIO_sddc/Core/arch/linux/usb_device_internals.h"

extern "C" {

usb_device_t *__real_usb_device_open(int index, const char *image, uint32_t size);
int __real_usb_device_control(usb_device_t *t, uint8_t request, uint16_t value,
                              uint16_t index, uint8_t *data, uint16_t length, int read);

usb_device_t *__wrap_usb_device_open(int, const char *image, uint32_t size) {
    return __real_usb_device_open(0, image, size);
}

int __wrap_usb_device_control(usb_device_t *t, uint8_t request, uint16_t value,
                              uint16_t index, uint8_t *data, uint16_t length, int read) {
    if (t == nullptr) return -1;
    return __real_usb_device_control(t, request, value, index, data, length, read);
}

int __wrap_usb_device_handle_events(usb_device_t *t) {
    struct timeval slice = {0, 100000};
    return libusb_handle_events_timeout_completed(t->context, &slice, &t->completed);
}

}
