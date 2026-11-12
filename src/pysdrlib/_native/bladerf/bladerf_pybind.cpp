#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>

#include "bladerf_device.hpp"
#include "hardware_pybind.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_bladerf, m) {
    m.doc() = "bladeRF 2.0 micro bindings (pybind11)";

    // libbladeRF errors -> classes in pysdrlib.bladerf.lib.err
    hw::register_errors(m, "BladeRFError", {
        {BLADERF_ERR_UNEXPECTED, "UNEXPECTED", ""},
        {BLADERF_ERR_RANGE, "RANGE", ""},
        {BLADERF_ERR_INVAL, "INVAL", ""},
        {BLADERF_ERR_MEM, "MEM", ""},
        {BLADERF_ERR_IO, "IO", ""},
        {BLADERF_ERR_TIMEOUT, "TIMEOUT", ""},
        {BLADERF_ERR_NODEV, "NODEV", ""},
        {BLADERF_ERR_UNSUPPORTED, "UNSUPPORTED", ""},
        {BLADERF_ERR_MISALIGNED, "MISALIGNED", ""},
        {BLADERF_ERR_CHECKSUM, "CHECKSUM", ""},
        {BLADERF_ERR_NO_FILE, "NO_FILE", ""},
        {BLADERF_ERR_UPDATE_FPGA, "UPDATE_FPGA", "The FPGA image is too old for this libbladeRF"},
        {BLADERF_ERR_UPDATE_FW, "UPDATE_FW", "The firmware is too old for this libbladeRF"},
        {BLADERF_ERR_TIME_PAST, "TIME_PAST", ""},
        {BLADERF_ERR_QUEUE_FULL, "QUEUE_FULL", ""},
        {BLADERF_ERR_FPGA_OP, "FPGA_OP", ""},
        {BLADERF_ERR_PERMISSION, "PERMISSION", "No permission to open the device (udev rules?)"},
        {BLADERF_ERR_WOULD_BLOCK, "WOULD_BLOCK", ""},
        {BLADERF_ERR_NOT_INIT, "NOT_INIT", ""},
        {BRF_PY_NOT_FOUND, "NOT_FOUND", "No bladeRF matched (none attached, or it could not be found at the address)"},
        {BRF_PY_NOT_OPEN, "NOT_OPEN", ""},
        {BRF_PY_INVALID, "VALUE", ""},
        {BRF_PY_UNSUPPORTED_BOARD, "UNSUPPORTED_BOARD", "A bladeRF, but not a 2.0 micro: only that one is supported"},
    });

    py::enum_<Dir>(m, "DIR").value("RX", Dir::RX).value("TX", Dir::TX);
    py::enum_<bladerf_gain_mode>(m, "GAIN_MODE")
        .value("DEFAULT", BLADERF_GAIN_DEFAULT)
        .value("MGC", BLADERF_GAIN_MGC)
        .value("FASTATTACK_AGC", BLADERF_GAIN_FASTATTACK_AGC)
        .value("SLOWATTACK_AGC", BLADERF_GAIN_SLOWATTACK_AGC)
        .value("HYBRID_AGC", BLADERF_GAIN_HYBRID_AGC);

    const auto nogil = py::call_guard<py::gil_scoped_release>();

    auto device = hw::bind_device<BladeRFDevice>(m, "device");
    device
        // tuning, per channel: every call takes the channel
        .def("set_rx_rate", [](BladeRFDevice& d, double v, size_t c) { return d.set_rate(Dir::RX, v, c); }, py::arg("rate"), py::arg("channel") = 0, nogil)
        .def("set_tx_rate", [](BladeRFDevice& d, double v, size_t c) { return d.set_rate(Dir::TX, v, c); }, py::arg("rate"), py::arg("channel") = 0, nogil)
        .def("get_rx_rate", [](BladeRFDevice& d, size_t c) { return d.rate(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_rate", [](BladeRFDevice& d, size_t c) { return d.rate(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_rate_range", [](BladeRFDevice& d, size_t c) { return d.rate_range(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_rate_range", [](BladeRFDevice& d, size_t c) { return d.rate_range(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_freq", [](BladeRFDevice& d, uint64_t v, size_t c) { d.set_freq(Dir::RX, v, c); }, py::arg("freq"), py::arg("channel") = 0, nogil)
        .def("set_tx_freq", [](BladeRFDevice& d, uint64_t v, size_t c) { d.set_freq(Dir::TX, v, c); }, py::arg("freq"), py::arg("channel") = 0, nogil)
        .def("get_rx_freq", [](BladeRFDevice& d, size_t c) { return d.freq(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_freq", [](BladeRFDevice& d, size_t c) { return d.freq(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_freq_range", [](BladeRFDevice& d, size_t c) { return d.freq_range(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_freq_range", [](BladeRFDevice& d, size_t c) { return d.freq_range(Dir::TX, c); }, py::arg("channel") = 0, nogil)

        // gain: stages by libbladeRF's names ("full" receive, "dsa" transmit), whole dB
        .def("set_rx_gain_stage", [](BladeRFDevice& d, const std::string& s, int v, size_t c) { d.set_gain_stage(Dir::RX, s, v, c); }, py::arg("stage"), py::arg("gain"), py::arg("channel") = 0, nogil)
        .def("set_tx_gain_stage", [](BladeRFDevice& d, const std::string& s, int v, size_t c) { d.set_gain_stage(Dir::TX, s, v, c); }, py::arg("stage"), py::arg("gain"), py::arg("channel") = 0, nogil)
        .def("get_rx_gain_stage", [](BladeRFDevice& d, const std::string& s, size_t c) { return d.gain_stage(Dir::RX, s, c); }, py::arg("stage"), py::arg("channel") = 0, nogil)
        .def("get_tx_gain_stage", [](BladeRFDevice& d, const std::string& s, size_t c) { return d.gain_stage(Dir::TX, s, c); }, py::arg("stage"), py::arg("channel") = 0, nogil)
        .def("rx_gain_stage_range", [](BladeRFDevice& d, const std::string& s, size_t c) { return d.gain_stage_range(Dir::RX, s, c); }, py::arg("stage"), py::arg("channel") = 0, nogil)
        .def("tx_gain_stage_range", [](BladeRFDevice& d, const std::string& s, size_t c) { return d.gain_stage_range(Dir::TX, s, c); }, py::arg("stage"), py::arg("channel") = 0, nogil)
        .def("rx_gain_stages", [](BladeRFDevice& d, size_t c) { return d.gain_stages(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_gain_stages", [](BladeRFDevice& d, size_t c) { return d.gain_stages(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_gain_mode", [](BladeRFDevice& d, bladerf_gain_mode mode, size_t c) { d.set_gain_mode(Dir::RX, mode, c); }, py::arg("mode"), py::arg("channel") = 0, nogil)
        .def("get_rx_gain_mode", [](BladeRFDevice& d, size_t c) { return static_cast<bladerf_gain_mode>(d.gain_mode(Dir::RX, c)); }, py::arg("channel") = 0, nogil)

        .def("set_rx_bandwidth", [](BladeRFDevice& d, double v, size_t c) { return d.set_bandwidth(Dir::RX, v, c); }, py::arg("bandwidth"), py::arg("channel") = 0, nogil)
        .def("set_tx_bandwidth", [](BladeRFDevice& d, double v, size_t c) { return d.set_bandwidth(Dir::TX, v, c); }, py::arg("bandwidth"), py::arg("channel") = 0, nogil)
        .def("get_rx_bandwidth", [](BladeRFDevice& d, size_t c) { return d.bandwidth(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_bandwidth", [](BladeRFDevice& d, size_t c) { return d.bandwidth(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_bandwidth_range", [](BladeRFDevice& d, size_t c) { return d.bandwidth_range(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_bandwidth_range", [](BladeRFDevice& d, size_t c) { return d.bandwidth_range(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_bias_tee", [](BladeRFDevice& d, bool v, size_t c) { d.set_bias_tee(Dir::RX, v, c); }, py::arg("enable"), py::arg("channel") = 0, nogil)
        .def("get_rx_bias_tee", [](BladeRFDevice& d, size_t c) { return d.bias_tee(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("set_tx_bias_tee", [](BladeRFDevice& d, bool v, size_t c) { d.set_bias_tee(Dir::TX, v, c); }, py::arg("enable"), py::arg("channel") = 0, nogil)
        .def("get_tx_bias_tee", [](BladeRFDevice& d, size_t c) { return d.bias_tee(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("num_rx_channels", [](BladeRFDevice& d) { return d.num_channels(Dir::RX); }, nogil)
        .def("num_tx_channels", [](BladeRFDevice& d) { return d.num_channels(Dir::TX); }, nogil)
        .def("get_rx_rf_port", [](BladeRFDevice& d, size_t c) { return d.rf_port(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("get_tx_rf_port", [](BladeRFDevice& d, size_t c) { return d.rf_port(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("rx_rf_ports", [](BladeRFDevice& d, size_t c) { return d.rf_ports(Dir::RX, c); }, py::arg("channel") = 0, nogil)
        .def("tx_rf_ports", [](BladeRFDevice& d, size_t c) { return d.rf_ports(Dir::TX, c); }, py::arg("channel") = 0, nogil)
        .def("set_rx_rf_port", [](BladeRFDevice& d, const std::string& p, size_t c) { d.set_rf_port(Dir::RX, p, c); }, py::arg("port"), py::arg("channel") = 0, nogil)
        .def("set_tx_rf_port", [](BladeRFDevice& d, const std::string& p, size_t c) { d.set_rf_port(Dir::TX, p, c); }, py::arg("port"), py::arg("channel") = 0, nogil)

        // board
        .def("set_clock_select", &BladeRFDevice::set_clock_select, nogil)
        .def("get_clock_select", &BladeRFDevice::clock_select, nogil)
        .def("set_clock_output", &BladeRFDevice::set_clock_output, nogil)
        .def("get_clock_output", &BladeRFDevice::clock_output, nogil)
        .def("sensors", &BladeRFDevice::sensors, nogil)
        .def("info", &BladeRFDevice::info, nogil)

        // which channels stream (the k-th listed goes to queue slot k)
        .def("set_rx_channels", &BladeRFDevice::set_rx_channels, py::arg("channels"))
        .def("get_rx_channels", &BladeRFDevice::rx_channels)
        .def("set_tx_channels", &BladeRFDevice::set_tx_channels, py::arg("channels"))
        .def("get_tx_channels", &BladeRFDevice::tx_channels)

        // stream health
        .def("rx_overflows", &BladeRFDevice::rx_overflows)
        .def("rx_lost", &BladeRFDevice::rx_lost)
        .def("rx_error", &BladeRFDevice::rx_error);
    hw::bind_tx(device);   // transmit: complex64 in, the transmit thread encodes it

    // ---- module functions ---- //
    m.def("init", [] {});   // nothing to initialise: libbladeRF needs no global setup (kept so devices call init() like the others)
    m.def("open", [](const std::string& identifier) {
        return hw::open_device<BladeRFDevice>([&](BladeRFDevice& d) { d.open(identifier); });
    }, py::arg("identifier") = "");
    m.def("find", [] {
        py::gil_scoped_release release;
        return bladerf_find();
    });
    m.def("library_version", [] {
        struct bladerf_version v;
        bladerf_version(&v);
        return std::string(v.describe ? v.describe : "");
    });
    m.def("set_log_level", [](const std::string& level) {   // libbladeRF logs to stderr; quiet by default
        static const std::pair<const char*, bladerf_log_level> levels[] = {
            {"verbose", BLADERF_LOG_LEVEL_VERBOSE}, {"debug", BLADERF_LOG_LEVEL_DEBUG}, {"info", BLADERF_LOG_LEVEL_INFO},
            {"warning", BLADERF_LOG_LEVEL_WARNING}, {"error", BLADERF_LOG_LEVEL_ERROR}, {"critical", BLADERF_LOG_LEVEL_CRITICAL},
            {"silent", BLADERF_LOG_LEVEL_SILENT}};
        for (const auto& l : levels)
            if (level == l.first) { bladerf_log_set_verbosity(l.second); return; }
        throw std::invalid_argument("log level: verbose, debug, info, warning, error, critical or silent");
    }, py::arg("level"));
}
