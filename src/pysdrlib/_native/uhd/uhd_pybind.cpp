#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <memory>

#include "hardware_pybind.hpp"
#include "uhd_device.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_uhd, m) {
    m.doc() = "UHD (USRP) bindings (pybind11)";

    hw::register_errors(m, "UHDError", {
        {1, "INVALID_DEVICE", ""},
        {10, "INDEX", ""},
        {11, "KEY", ""},
        {20, "NOT_IMPLEMENTED", ""},
        {21, "USB", ""},
        {30, "IO", ""},
        {31, "OS", ""},
        {40, "ASSERTION", ""},
        {41, "LOOKUP", ""},
        {42, "TYPE", ""},
        {43, "VALUE", ""},
        {44, "RUNTIME", ""},
        {45, "ENVIRONMENT", ""},
        {46, "SYSTEM", ""},
        {USHIM_RX_TIMEOUT, "TIMEOUT", ""},
        {USHIM_RX_OVERFLOW, "OVERFLOW", ""},
        {UHD_PY_NOT_FOUND, "NOT_FOUND", "No USRP matched (none attached, or it could not be found at the address)"},
        {UHD_PY_NOT_OPEN, "NOT_OPEN", ""},
        {UHD_PY_INVALID, "VALUE", ""},
        {-1, "OTHER", ""},
    });

    py::enum_<Dir>(m, "DIR").value("RX", Dir::RX).value("TX", Dir::TX);

    const auto nogil = py::call_guard<py::gil_scoped_release>();

    auto device = hw::bind_device<UHDDevice>(m, "device");
    device
        // device-wide (channel 0, both directions): what the generic API uses
        .def("set_freq", [](UHDDevice& d, double v) { d.set_freq(Dir::RX, v, 0); d.set_freq(Dir::TX, v, 0); }, py::arg("freq"), nogil)
        .def("set_sample_rate", [](UHDDevice& d, double v) { d.set_rate(Dir::RX, v, 0); d.set_rate(Dir::TX, v, 0); },
             py::arg("rate"), nogil)   // the AD9361 runs both directions from one clock

        // tuning, per channel: every call takes the channel
        .def("set_rx_rate", [](UHDDevice& d, double v, size_t c) { d.set_rate(Dir::RX, v, c); }, py::arg("rate"), py::arg("channel") = 0, nogil)
        .def("set_tx_rate", [](UHDDevice& d, double v, size_t c) { d.set_rate(Dir::TX, v, c); }, py::arg("rate"), py::arg("channel") = 0, nogil)
        .def("get_rx_rate", [](UHDDevice& d, size_t c) { return d.rate(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_rate", [](UHDDevice& d, size_t c) { return d.rate(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rate_ranges", [](UHDDevice& d, size_t c) { return d.rate_ranges(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_freq", [](UHDDevice& d, double v, size_t c) { d.set_freq(Dir::RX, v, c); }, py::arg("freq"), py::arg("channel") = 0, nogil)
        .def("set_tx_freq", [](UHDDevice& d, double v, size_t c) { d.set_freq(Dir::TX, v, c); }, py::arg("freq"), py::arg("channel") = 0, nogil)
        .def("get_rx_freq", [](UHDDevice& d, size_t c) { return d.freq(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_freq", [](UHDDevice& d, size_t c) { return d.freq(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_freq_range", [](UHDDevice& d, size_t c) { return d.freq_range(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_freq_range", [](UHDDevice& d, size_t c) { return d.freq_range(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_gain", [](UHDDevice& d, double v, size_t c) { d.set_gain(Dir::RX, v, c); }, py::arg("gain"), py::arg("channel") = 0, nogil)
        .def("set_tx_gain", [](UHDDevice& d, double v, size_t c) { d.set_gain(Dir::TX, v, c); }, py::arg("gain"), py::arg("channel") = 0, nogil)
        .def("get_rx_gain", [](UHDDevice& d, size_t c) { return d.gain(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_gain", [](UHDDevice& d, size_t c) { return d.gain(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_gain_range", [](UHDDevice& d, size_t c) { return d.gain_range(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_gain_range", [](UHDDevice& d, size_t c) { return d.gain_range(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_agc", &UHDDevice::set_agc, py::arg("enable"), py::arg("channel") = 0, nogil)
        .def("set_rx_bandwidth", [](UHDDevice& d, double v, size_t c) { d.set_bandwidth(Dir::RX, v, c); return d.bandwidth(Dir::RX, c); }, py::arg("bandwidth"), py::arg("channel") = 0, nogil)
        .def("set_tx_bandwidth", [](UHDDevice& d, double v, size_t c) { d.set_bandwidth(Dir::TX, v, c); return d.bandwidth(Dir::TX, c); }, py::arg("bandwidth"), py::arg("channel") = 0, nogil)
        .def("get_rx_bandwidth", [](UHDDevice& d, size_t c) { return d.bandwidth(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_bandwidth", [](UHDDevice& d, size_t c) { return d.bandwidth(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_bandwidth_range", [](UHDDevice& d, size_t c) { return d.bandwidth_range(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_bandwidth_range", [](UHDDevice& d, size_t c) { return d.bandwidth_range(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_antenna", [](UHDDevice& d, const std::string& n, size_t c) { d.set_antenna(Dir::RX, n, c); }, py::arg("name"), py::arg("channel") = 0, nogil)
        .def("set_tx_antenna", [](UHDDevice& d, const std::string& n, size_t c) { d.set_antenna(Dir::TX, n, c); }, py::arg("name"), py::arg("channel") = 0, nogil)
        .def("get_rx_antenna", [](UHDDevice& d, size_t c) { return d.antenna(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_antenna", [](UHDDevice& d, size_t c) { return d.antenna(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_antennas", [](UHDDevice& d, size_t c) { return d.antennas(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_antennas", [](UHDDevice& d, size_t c) { return d.antennas(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_dc_offset", &UHDDevice::set_dc_offset, py::arg("enable"), py::arg("channel") = 0, nogil)
        .def("set_rx_iq_balance", &UHDDevice::set_iq_balance, py::arg("enable"), py::arg("channel") = 0, nogil)
        .def("num_rx_channels", [](UHDDevice& d) { return d.num_channels(Dir::RX); }, nogil)
        .def("num_tx_channels", [](UHDDevice& d) { return d.num_channels(Dir::TX); }, nogil)
        .def("rx_sensors", [](UHDDevice& d, size_t c) { return d.sensors(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_sensors", [](UHDDevice& d, size_t c) { return d.sensors(Dir::TX, c); }, py::arg("channel") = 0, nogil)

        // motherboard
        .def("set_master_clock_rate", &UHDDevice::set_master_clock_rate, nogil)
        .def("get_master_clock_rate", &UHDDevice::master_clock_rate, nogil)
        .def("set_clock_source", &UHDDevice::set_clock_source, nogil)
        .def("get_clock_source", &UHDDevice::clock_source, nogil)
        .def("clock_sources", &UHDDevice::clock_sources, nogil)
        .def("set_time_source", &UHDDevice::set_time_source, nogil)
        .def("get_time_source", &UHDDevice::time_source, nogil)
        .def("time_sources", &UHDDevice::time_sources, nogil)
        .def("time_now", &UHDDevice::time_now, nogil)
        .def("set_time_now", &UHDDevice::set_time_now, nogil)
        .def("set_time_next_pps", &UHDDevice::set_time_next_pps, nogil)
        .def("mboard_sensors", &UHDDevice::mboard_sensors, nogil)
        .def("info", &UHDDevice::info, nogil)

        // which channels stream (the k-th listed goes to queue slot k)
        .def("set_rx_channels", &UHDDevice::set_rx_channels, py::arg("channels"))
        .def("get_rx_channels", &UHDDevice::rx_channels)
        .def("set_tx_channels", &UHDDevice::set_tx_channels, py::arg("channels"))
        .def("get_tx_channels", &UHDDevice::tx_channels)

        // stream health
        .def("rx_overflows", &UHDDevice::rx_overflows)
        .def("rx_lost", &UHDDevice::rx_lost)
        .def("rx_error", &UHDDevice::rx_error);
    hw::bind_tx(device);   // transmit: complex64 in, the transmit thread encodes it


    // ---- module functions ---- //
    m.def("init", [] {});   // nothing to initialise: UHD needs no global setup (kept so devices call init() like the others)
    m.def("open", [](const std::string& args) {
        return hw::open_device<UHDDevice>([&](UHDDevice& d) { d.open(args); });
    }, py::arg("args") = "");
    m.def("find", [](const std::string& args) {
        py::gil_scoped_release release;
        return uhd_find(args);
    }, py::arg("args") = "");
}
