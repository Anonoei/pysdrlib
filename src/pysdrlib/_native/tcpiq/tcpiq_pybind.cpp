#include <pybind11/pybind11.h>

#include <memory>
#include <string>

#include "tcpiq_device.hpp"
#include "hardware_pybind.hpp"

namespace py = pybind11;

namespace {

// (kind, bits, storage, packed, big_endian), as pysdrlib.base.convert.SampleFormat has them
SampleFormat make_format(const std::string& kind, int bits, int storage, bool packed, bool big_endian) {
    if (kind != "int" && kind != "uint" && kind != "float")
        throw py::value_error("kind must be int, uint or float, not '" + kind + "'");
    SampleKind k = kind == "int" ? SampleKind::Int : kind == "uint" ? SampleKind::UInt : SampleKind::Float;
    return SampleFormat{k, bits, storage, packed, big_endian};
}

}  // namespace

PYBIND11_MODULE(_tcpiq, m) {
    m.doc() = "TCP I/Q stream bindings (pybind11): the socket is read on a thread of the device's own, without the GIL";

    hw::register_errors(m, "TcpIqError", {
        {TCPIQ_ERROR_FAILED, "FAILED", ""},
        {TCPIQ_ERROR_CONNECT, "CONNECT", "Could not connect to the server"},
        {TCPIQ_ERROR_BIND, "BIND", "Could not listen on the address: in use, not this machine's, or no permission"},
        {TCPIQ_ERROR_NOT_OPEN, "NOT_OPEN", ""},
        {TCPIQ_ERROR_BUSY, "BUSY", ""},
    });

    hw::bind_device<TcpIqDevice>(m, "device")
        .def_property_readonly("listening", &TcpIqDevice::listening)
        .def_property_readonly("eof", &TcpIqDevice::eof)
        .def_property_readonly("peer", &TcpIqDevice::peer)
        .def_property_readonly("connections", &TcpIqDevice::connections)
        .def_property_readonly("replaced", &TcpIqDevice::replaced)
        .def_property_readonly("port", &TcpIqDevice::port)
        .def_property_readonly("rtl_header", &TcpIqDevice::rtl_header)
        .def_property_readonly("rtl_tuner", &TcpIqDevice::rtl_tuner)
        .def_property_readonly("rtl_gain_count", &TcpIqDevice::rtl_gain_count)
        .def_property_readonly("rx_rate", &TcpIqDevice::rx_rate,
                               "Samples per second arriving (latest ~1 s window); 0.0 when not known")
        .def_property_readonly("rate_windows", &TcpIqDevice::rate_windows);

    m.def("connect", [](const std::string& host, uint16_t port, double timeout, const std::string& kind, int bits,
                        int storage, bool packed, bool big_endian) {
        const SampleFormat f = make_format(kind, bits, storage, packed, big_endian);
        return hw::open_device<TcpIqDevice>([&](TcpIqDevice& d) { d.open_connect(host, port, timeout, f); });
    }, py::arg("host"), py::arg("port"), py::arg("timeout"), py::arg("kind"), py::arg("bits"), py::arg("storage"),
       py::arg("packed") = false, py::arg("big_endian") = false,
       "Connect to a server that sends raw I/Q in the given layout");
    m.def("listen", [](const std::string& host, uint16_t port, const std::string& kind, int bits, int storage,
                       bool packed, bool big_endian) {
        const SampleFormat f = make_format(kind, bits, storage, packed, big_endian);
        return hw::open_device<TcpIqDevice>([&](TcpIqDevice& d) { d.open_listen(host, port, f); });
    }, py::arg("host"), py::arg("port"), py::arg("kind"), py::arg("bits"), py::arg("storage"),
       py::arg("packed") = false, py::arg("big_endian") = false,
       "Listen for a client that sends raw I/Q in the given layout (port 0: any free port, see device.port)");
}
