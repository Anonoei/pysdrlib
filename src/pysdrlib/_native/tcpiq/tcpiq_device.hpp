#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "hardware_wrapper.hpp"

enum TcpIqErrorCode {
    TCPIQ_ERROR_FAILED = -1001,    // a socket call failed; the message says which
    TCPIQ_ERROR_CONNECT = -1002,   // could not connect to the server
    TCPIQ_ERROR_BIND = -1003,      // could not listen on the address (in use, no permission, bad address)
    TCPIQ_ERROR_NOT_OPEN = -1004,
    TCPIQ_ERROR_BUSY = -1005,
};

// The sockets of an open stream (opaque outside the .cpp: the platform's socket type lives there)
struct TcpIqConn;

// Raw interleaved I/Q over TCP, either end of the connection:
//   connect: pysdrlib is the client of a server that sends samples
//   listen:  pysdrlib is the server, and a client connects and sends samples
// Either way a thread of the device's own reads the socket and hands whole samples to the receive path
// (hardware_rx.hpp: complex64 conversion and the ring), never touching Python. The socket is drained as fast
// as the network delivers: if Python reads too slowly the ring drops the oldest samples, the sender is never held up.
//
// Listening, the thread runs from open to close: it serves one client at a time, and reads (and discards) what arrives
// while not receiving. When the client disconnects it waits for the next (or the same one again) and streams on; a
// client that connects while one is connected takes over (a sender reconnecting after its connection died), and TCP
// keepalive notices a client that vanished without closing. Connected, it reads while receiving.
class TcpIqDevice : public HardwareDevice<TcpIqConn*> {
public:
    TcpIqDevice() = default;
    ~TcpIqDevice() override;

    // Connect to `host`:`port`, waiting up to `timeout` seconds
    void open_connect(const std::string& host, uint16_t port, double timeout, const SampleFormat& format);
    // Listen on `host`:`port` (port 0: any free one, see port())
    void open_listen(const std::string& host, uint16_t port, const SampleFormat& format);
    void close() override;

    SampleFormat rx_sample_format() const override { return format_; }

    void start_rx() override;
    void stop_rx() override;
    bool is_streaming() const override { return streaming_; }

    bool listening() const { return listening_; }
    bool eof() const { return eof_; }                       // connected: the server closed the stream
    std::string peer() const;                               // "host:port" of the other end; "" when none
    uint64_t connections() const { return connections_; }  // clients accepted since open (listening)
    uint64_t replaced() const { return replaced_; }        // ... of which took over from a connected one (reconnects)
    double disconnected_for() const;   // listening: seconds since the client dropped, with none since; -1 otherwise
    uint16_t port() const { return port_; }                 // the local port
    // The rtl_tcp header the stream began with ("RTL0", tuner type, gain count; skipped, not samples), if it did
    bool rtl_header() const { return rtl_seen_; }
    uint32_t rtl_tuner() const { return rtl_tuner_; }
    uint32_t rtl_gain_count() const { return rtl_gains_; }
    // How fast samples arrive (per second, over the latest window of about a second), whether receiving or not; 0
    // until a window is in, and when nothing has arrived for a while. Only a sender paced in real time (a radio)
    // arrives at its sample rate, so it is a check on the declared rate, not a source of it
    double rx_rate() const;
    uint64_t rate_windows() const { return rate_windows_; }   // windows measured since open

protected:
    HardwareError not_open_error() const override;

private:
    void io_loop();
    void stop_thread();
    void drop_client();
    void receive(size_t got);
    void new_stream();
    bool accept_client(TcpIqConn* c);
    size_t sniff_header(size_t got);
    void rate_reset();
    void rate_count(size_t bytes);

    SampleFormat format_{SampleKind::Float, 32, 32, false};
    std::thread io_;
    std::mutex stream_mutex_;          // start/stop/close
    mutable std::mutex peer_mutex_;
    std::string peer_;
    std::atomic<bool> stop_{false};
    std::atomic<bool> streaming_{false};
    std::atomic<bool> eof_{false};
    std::atomic<uint64_t> connections_{0};
    std::atomic<uint64_t> replaced_{0};
    std::atomic<int64_t> dropped_at_{0};   // steady-clock ns the client dropped (0: connected, or none yet)
    bool listening_ = false;
    uint16_t port_ = 0;
    std::vector<unsigned char> buf_;   // a read, after `carry_` bytes of a sample split by the previous one
    size_t carry_ = 0;
    // The start of a connection's stream, held back until it shows whether it is an rtl_tcp header (12 bytes)
    static constexpr size_t RTL_HEADER = 12;
    unsigned char head_[RTL_HEADER] = {};
    size_t head_len_ = 0;
    bool sniffing_ = false;
    std::atomic<bool> rtl_seen_{false};
    std::atomic<uint32_t> rtl_tuner_{0};
    std::atomic<uint32_t> rtl_gains_{0};
    // The arrival rate (receive thread only, but for the atomics). A stream's first bytes are a burst (what the sender
    // and the kernel buffered), so windows start after a warm-up, and again after a pause
    using clock = std::chrono::steady_clock;
    clock::time_point rate_t0_{};      // the window's start (or the warm-up's); none: no bytes yet
    bool rate_warm_ = false;
    clock::time_point rate_last_{};    // the latest bytes
    uint64_t rate_bytes_ = 0;          // bytes since rate_t0_
    std::atomic<double> rx_rate_{0.0};
    std::atomic<int64_t> rate_last_ns_{0};
    std::atomic<uint64_t> rate_windows_{0};
};
