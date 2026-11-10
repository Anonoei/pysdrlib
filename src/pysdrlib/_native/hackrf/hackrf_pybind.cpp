#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>

#include "hackrf_device.hpp"
#include "hardware_pybind.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_hackrf, m) {
    m.doc() = "HackRF bindings (pybind11)";

    // libhackrf errors -> classes in pysdrlib.hackrf.lib.err
    hw::register_errors(m, "HackRFError", {
        {HACKRF_ERROR_INVALID_PARAM, "INVALID_PARAM", ""},
        {HACKRF_ERROR_NOT_FOUND, "NOT_FOUND", ""},
        {HACKRF_ERROR_BUSY, "BUSY", ""},
        {HACKRF_ERROR_NO_MEM, "NO_MEM", ""},
        {HACKRF_ERROR_LIBUSB, "LIBUSB", ""},
        {HACKRF_ERROR_THREAD, "THREAD", ""},
        {HACKRF_ERROR_STREAMING_THREAD_ERR, "STREAMING_THREAD_ERR", ""},
        {HACKRF_ERROR_STREAMING_STOPPED, "STREAMING_STOPPED", ""},
        {HACKRF_ERROR_STREAMING_EXIT_CALLED, "STREAMING_EXIT_CALLED", ""},
        {HACKRF_ERROR_USB_API_VERSION, "USB_API_VERSION", ""},
        {HACKRF_ERROR_NOT_LAST_DEVICE, "NOT_LAST_DEVICE", ""},
        {HACKRF_ERROR_OTHER, "OTHER", ""},
    });

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
    auto device = hw::bind_device<HackRFDevice>(m, "device");
    device
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
        .def("board_partid_serialno_read", &HackRFDevice::board_partid_serialno_read);
    hw::bind_tx(device);   // transmit: complex64 in, the libhackrf transmit callback encodes it to int8

    // ---- module functions ---- //
    m.def("init", &hackrf_init_checked);   // idempotent; also done on demand
    m.def("exit", &hackrf_exit_checked);

    m.def("open", [] {
        return hw::open_device<HackRFDevice>([](HackRFDevice& d) { d.open(); });
    });
    m.def("open_by_serial", [](const std::string& serial) {
        return hw::open_device<HackRFDevice>([&](HackRFDevice& d) { d.open_by_serial(serial); });
    });
    m.def("device_list_open", [](HackRFDeviceList& list, int idx) {
        return hw::open_device<HackRFDevice>([&](HackRFDevice& d) { d.open_from_list(list.get(), idx); });
    });

    m.def("library_version", [] { return std::string(hackrf_library_version()); });
    m.def("library_release", [] { return std::string(hackrf_library_release()); });
    m.def("board_id_read", [](HackRFDevice& d) { return d.board_id_read(); });
    m.def("version_string_read", [](HackRFDevice& d) { return d.version_string_read(); });
    m.def("usb_api_version_read", [](HackRFDevice& d) { return d.usb_api_version_read(); });
    m.def("board_partid_serialno_read", [](HackRFDevice& d) { return d.board_partid_serialno_read(); });
}
