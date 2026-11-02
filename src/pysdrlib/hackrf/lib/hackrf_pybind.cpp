#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <cstring>
#include <memory>
#include <thread>

#include "hackrf_device.hpp"
#include "hardware_numpy.hpp"

namespace py = pybind11;

namespace {

// Dropping the Python callback needs the GIL, which the destructor path may not hold.
struct PyCallback {
    explicit PyCallback(py::function fn) : fn_(new py::function(std::move(fn)), [](py::function* p) {
        py::gil_scoped_acquire gil;
        delete p;
    }) {}
    std::shared_ptr<py::function> fn_;
};

// If a device is garbage-collected while streaming, close() joins the streaming
// thread, which may be waiting for the GIL inside the Python callback. Release it.
class PyHackRFDevice : public HackRFDevice {
public:
    ~PyHackRFDevice() override {
        if (PyGILState_Check()) {
            py::gil_scoped_release release;
            try { close(); } catch (...) {}
        }
    }
};

const char* error_class_name(int code) {
    switch (code) {
        case HACKRF_ERROR_INVALID_PARAM:        return "INVALID_PARAM";
        case HACKRF_ERROR_NOT_FOUND:            return "NOT_FOUND";
        case HACKRF_ERROR_BUSY:                 return "BUSY";
        case HACKRF_ERROR_NO_MEM:               return "NO_MEM";
        case HACKRF_ERROR_LIBUSB:               return "LIBUSB";
        case HACKRF_ERROR_THREAD:               return "THREAD";
        case HACKRF_ERROR_STREAMING_THREAD_ERR: return "STREAMING_THREAD_ERR";
        case HACKRF_ERROR_STREAMING_STOPPED:    return "STREAMING_STOPPED";
        case HACKRF_ERROR_STREAMING_EXIT_CALLED:return "STREAMING_EXIT_CALLED";
        case HACKRF_ERROR_USB_API_VERSION:      return "USB_API_VERSION";
        case HACKRF_ERROR_NOT_LAST_DEVICE:      return "NOT_LAST_DEVICE";
        default:                                return "OTHER";
    }
}

std::unique_ptr<HackRFDevice> make_device() { return std::make_unique<PyHackRFDevice>(); }

}  // namespace

PYBIND11_MODULE(_hackrf, m) {
    m.doc() = "HackRF bindings (pybind11)";

    // libhackrf errors -> the pysdrlib.hackrf.lib.err hierarchy
    py::register_exception_translator([](std::exception_ptr p) {
        try {
            if (p) std::rethrow_exception(p);
        } catch (const HardwareError& e) {
            py::object cls = py::module_::import("pysdrlib.hackrf.lib.err").attr(error_class_name(e.code()));
            PyErr_SetString(cls.ptr(), e.what());
        }
    });

    py::enum_<hackrf_error>(m, "ERR")
        .value("SUCCESS", HACKRF_SUCCESS)
        .value("TRUE", HACKRF_TRUE)
        .value("INVALID_PARAM", HACKRF_ERROR_INVALID_PARAM)
        .value("NOT_FOUND", HACKRF_ERROR_NOT_FOUND)
        .value("BUSY", HACKRF_ERROR_BUSY)
        .value("NO_MEM", HACKRF_ERROR_NO_MEM)
        .value("LIBUSB", HACKRF_ERROR_LIBUSB)
        .value("THREAD", HACKRF_ERROR_THREAD)
        .value("STREAMING_THREAD_ERR", HACKRF_ERROR_STREAMING_THREAD_ERR)
        .value("STREAMING_STOPPED", HACKRF_ERROR_STREAMING_STOPPED)
        .value("STREAMING_EXIT_CALLED", HACKRF_ERROR_STREAMING_EXIT_CALLED)
        .value("USB_API_VERSION", HACKRF_ERROR_USB_API_VERSION)
        .value("NOT_LAST_DEVICE", HACKRF_ERROR_NOT_LAST_DEVICE)
        .value("OTHER", HACKRF_ERROR_OTHER);

    py::enum_<rf_path_filter>(m, "RF_PATH_FILTER")
        .value("BYPASS", RF_PATH_FILTER_BYPASS)
        .value("LOW_PASS", RF_PATH_FILTER_LOW_PASS)
        .value("HIGH_PASS", RF_PATH_FILTER_HIGH_PASS);

    m.attr("SAMPLES_PER_BLOCK") = SAMPLES_PER_BLOCK;
    m.attr("BYTES_PER_BLOCK") = BYTES_PER_BLOCK;
    m.attr("MAX_SWEEP_RANGES") = MAX_SWEEP_RANGES;

    // ---- device list ---- //
    py::class_<HackRFDeviceList>(m, "device_list")
        .def(py::init<>())
        .def("close", &HackRFDeviceList::close)
        .def("__len__", &HackRFDeviceList::device_count)
        .def_property_readonly("device_count", &HackRFDeviceList::device_count)
        .def_property_readonly("serial_numbers", &HackRFDeviceList::serial_numbers)
        .def_property_readonly("usb_board_ids", &HackRFDeviceList::usb_board_ids);

    // ---- device ---- //
    const auto nogil = py::call_guard<py::gil_scoped_release>();

    py::class_<HackRFDevice>(m, "device")
        .def(py::init(&make_device), "A closed device; use open()/open_by_serial() to get an open one.")
        .def("close", &HackRFDevice::close, nogil)
        .def_property_readonly("is_open", &HackRFDevice::is_open)

        // streaming. The callback gets (array of the device's raw samples in its native dtype,
        // valid_bytes, buffer_bytes) and returns 0 to keep streaming. Set it before start_rx().
        .def("set_rx_callback", [](HackRFDevice& d, py::function fn) {
            PyCallback holder(std::move(fn));
            const SampleFormat format = d.rx_sample_format();
            d.set_rx_callback([holder, format](const void* data, size_t valid, size_t buflen) -> int {
                py::gil_scoped_acquire gil;
                try {
                    py::object r = (*holder.fn_)(hw::make_rx_array(format, data, valid), valid, buflen);
                    return r.is_none() ? 0 : r.cast<int>();
                } catch (py::error_already_set& e) {
                    e.discard_as_unraisable("pysdrlib.hackrf rx_callback");
                    return -1;
                }
            });
        })
        .def("start_rx", &HackRFDevice::start_rx, nogil)
        .def("stop_rx", &HackRFDevice::stop_rx, nogil)
        .def("is_streaming", &HackRFDevice::is_streaming)
        // (kind, significant bits, storage bits, packed): see pysdrlib.base.convert.SampleFormat
        .def("rx_sample_format", [](const HackRFDevice& d) {
            SampleFormat f = d.rx_sample_format();
            return py::make_tuple(hw::kind_name(f.kind), f.bits, f.storage_bits, f.packed);
        })

        // tuning
        .def("set_freq", &HackRFDevice::set_freq)
        .def("set_sample_rate", &HackRFDevice::set_sample_rate)
        .def("set_baseband_filter_bandwidth", &HackRFDevice::set_baseband_filter_bandwidth)
        .def("set_hw_sync_mode", &HackRFDevice::set_hw_sync_mode)

        // device-specific gain stages
        .def("set_rx_rf_gain", &HackRFDevice::set_rx_rf_gain)
        .def("set_rx_if_gain", &HackRFDevice::set_rx_if_gain)
        .def("set_rx_bb_gain", &HackRFDevice::set_rx_bb_gain)
        .def("set_tx_rf_gain", &HackRFDevice::set_tx_rf_gain)
        .def("set_tx_if_gain", &HackRFDevice::set_tx_if_gain)
        // libhackrf-named aliases
        .def("set_amp_enable", &HackRFDevice::set_amp_enable)
        .def("set_lna_gain", &HackRFDevice::set_lna_gain)
        .def("set_vga_gain", &HackRFDevice::set_vga_gain)
        .def("set_txvga_gain", &HackRFDevice::set_txvga_gain)
        .def("set_antenna_enable", &HackRFDevice::set_antenna_enable)

        // queries
        .def("board_id_read", &HackRFDevice::board_id_read)
        .def("version_string_read", &HackRFDevice::version_string_read)
        .def("usb_api_version_read", &HackRFDevice::usb_api_version_read)
        .def("board_partid_serialno_read", &HackRFDevice::board_partid_serialno_read)

        // test hook: drive the real streaming-thread path from a native thread, no hardware
        .def("_simulate_rx", [](HackRFDevice& d, py::bytes data, int valid, int buflen) {
            std::string buf = data;
            int result = 0;
            {
                py::gil_scoped_release release;
                std::thread t([&] {
                    result = d.simulate_rx(reinterpret_cast<const uint8_t*>(buf.data()), valid, buflen);
                });
                t.join();
            }
            return result;
        });

    // ---- module functions ---- //
    m.def("init", &hackrf_init_checked);   // idempotent; also done on demand
    m.def("exit", &hackrf_exit_checked);

    m.def("open", [] {
        auto d = make_device();
        d->open();
        return d;
    });
    m.def("open_by_serial", [](const std::string& serial) {
        auto d = make_device();
        d->open_by_serial(serial);
        return d;
    });
    m.def("device_list_open", [](HackRFDeviceList& list, int idx) {
        auto d = make_device();
        d->open_from_list(list.get(), idx);
        return d;
    });

    m.def("library_version", [] { return std::string(hackrf_library_version()); });
    m.def("library_release", [] { return std::string(hackrf_library_release()); });
    m.def("board_id_read", [](HackRFDevice& d) { return d.board_id_read(); });
    m.def("version_string_read", [](HackRFDevice& d) { return d.version_string_read(); });
    m.def("usb_api_version_read", [](HackRFDevice& d) { return d.usb_api_version_read(); });
    m.def("board_partid_serialno_read", [](HackRFDevice& d) { return d.board_partid_serialno_read(); });
}
