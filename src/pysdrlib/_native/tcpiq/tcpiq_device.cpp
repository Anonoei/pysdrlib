#include "tcpiq_device.hpp"

#include <chrono>
#include <cmath>
#include <algorithm>
#include <cstring>

// ---- sockets, the same on every platform from here on ---- //
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
using sock_t = SOCKET;
static const sock_t NO_SOCKET = INVALID_SOCKET;
static int sock_close(sock_t s) { return closesocket(s); }
static int sock_poll(pollfd* p, unsigned long n, int ms) { return WSAPoll(p, n, ms); }
static int sock_errno() { return WSAGetLastError(); }
static bool interrupted(int e) { return e == WSAEINTR || e == WSAEWOULDBLOCK; }
static bool in_progress(int e) { return e == WSAEWOULDBLOCK || e == WSAEINPROGRESS; }
static void set_blocking(sock_t s, bool blocking) {
    u_long nb = blocking ? 0 : 1;
    ioctlsocket(s, FIONBIO, &nb);
}
static std::string sock_strerror(int e) { return "error " + std::to_string(e); }
static void sockets_init() {
    static std::once_flag once;
    std::call_once(once, [] {
        WSADATA data;
        if (WSAStartup(MAKEWORD(2, 2), &data) != 0) throw HardwareError(TCPIQ_ERROR_FAILED, "WSAStartup failed");
    });
}
#else
#include <cerrno>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
using sock_t = int;
static const sock_t NO_SOCKET = -1;
static int sock_close(sock_t s) { return ::close(s); }
static int sock_poll(pollfd* p, nfds_t n, int ms) { return ::poll(p, n, ms); }
static int sock_errno() { return errno; }
static bool interrupted(int e) { return e == EINTR || e == EAGAIN || e == EWOULDBLOCK; }
static bool in_progress(int e) { return e == EINPROGRESS; }
static void set_blocking(sock_t s, bool blocking) {
    int flags = fcntl(s, F_GETFL, 0);
    fcntl(s, F_SETFL, blocking ? (flags & ~O_NONBLOCK) : (flags | O_NONBLOCK));
}
static std::string sock_strerror(int e) { return std::strerror(e); }
static void sockets_init() {}
#endif

struct TcpIqConn {
    sock_t listener = NO_SOCKET;   // listening
    sock_t client = NO_SOCKET;     // the connection samples arrive on
};

namespace {

constexpr size_t CHUNK_BYTES = 1 << 18;     // one read
constexpr int RCVBUF_BYTES = 4 << 20;       // the kernel's buffer: room for a burst while the thread converts
constexpr int POLL_MS = 100;                // how soon the thread notices a stop

struct AddrInfo {
    addrinfo* list = nullptr;
    ~AddrInfo() { if (list) freeaddrinfo(list); }
};

void resolve(AddrInfo& out, const std::string& host, uint16_t port, bool passive, int error_code) {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;
    if (passive) hints.ai_flags = AI_PASSIVE;
    const std::string service = std::to_string(port);
    int r = getaddrinfo(host.empty() ? nullptr : host.c_str(), service.c_str(), &hints, &out.list);
    if (r != 0 || !out.list)
        throw HardwareError(error_code, "cannot resolve '" + host + "': " + gai_strerror(r));
}

void set_rcvbuf(sock_t s) {
    int size = RCVBUF_BYTES;
    setsockopt(s, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<const char*>(&size), sizeof(size));
}

// A client that vanishes without closing (crashed, its network dropped) is noticed within ~KEEPALIVE_IDLE + 3 *
// KEEPALIVE_INTERVAL seconds, so a listening device goes back to waiting for one, instead of on a dead connection
constexpr int KEEPALIVE_IDLE = 5, KEEPALIVE_INTERVAL = 2, KEEPALIVE_COUNT = 3;

void set_keepalive(sock_t s) {
    int on = 1;
    setsockopt(s, SOL_SOCKET, SO_KEEPALIVE, reinterpret_cast<const char*>(&on), sizeof(on));
#if defined(TCP_KEEPIDLE)
    int idle = KEEPALIVE_IDLE;
    setsockopt(s, IPPROTO_TCP, TCP_KEEPIDLE, reinterpret_cast<const char*>(&idle), sizeof(idle));
#elif defined(TCP_KEEPALIVE)   // macOS
    int idle = KEEPALIVE_IDLE;
    setsockopt(s, IPPROTO_TCP, TCP_KEEPALIVE, reinterpret_cast<const char*>(&idle), sizeof(idle));
#endif
#if defined(TCP_KEEPINTVL)
    int interval = KEEPALIVE_INTERVAL;
    setsockopt(s, IPPROTO_TCP, TCP_KEEPINTVL, reinterpret_cast<const char*>(&interval), sizeof(interval));
#endif
#if defined(TCP_KEEPCNT)
    int count = KEEPALIVE_COUNT;
    setsockopt(s, IPPROTO_TCP, TCP_KEEPCNT, reinterpret_cast<const char*>(&count), sizeof(count));
#endif
}

std::string addr_text(const sockaddr* sa, socklen_t len) {
    char host[NI_MAXHOST] = {0}, serv[NI_MAXSERV] = {0};
    if (getnameinfo(sa, len, host, sizeof(host), serv, sizeof(serv), NI_NUMERICHOST | NI_NUMERICSERV) != 0) return "?";
    std::string h = host;
    if (h.find(':') != std::string::npos) h = "[" + h + "]";   // IPv6
    return h + ":" + serv;
}

uint16_t local_port(sock_t s) {
    sockaddr_storage local{};
    socklen_t len = sizeof(local);
    if (getsockname(s, reinterpret_cast<sockaddr*>(&local), &len) != 0) return 0;
    return ntohs(local.ss_family == AF_INET6 ? reinterpret_cast<sockaddr_in6*>(&local)->sin6_port
                                             : reinterpret_cast<sockaddr_in*>(&local)->sin_port);
}

// Connect `s` to `ai` within `timeout` seconds (negative: the system's own); returns 0 or the socket error
int connect_within(sock_t s, const addrinfo* ai, double timeout) {
    set_blocking(s, false);
    int err = 0;
    if (connect(s, ai->ai_addr, static_cast<socklen_t>(ai->ai_addrlen)) != 0) {
        err = sock_errno();
        if (in_progress(err)) {
            pollfd p{};
            p.fd = s;
            p.events = POLLOUT;
            const int ms = timeout < 0 ? -1 : static_cast<int>(std::ceil(timeout * 1000));
            int r = sock_poll(&p, 1, ms);
            if (r == 0) {
                err = -1;   // timed out
            } else if (r < 0) {
                err = sock_errno();
            } else {
                socklen_t len = sizeof(err);
                getsockopt(s, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&err), &len);
            }
        }
    }
    set_blocking(s, true);
    return err;
}

}  // namespace

// --- TcpIqDevice ------------------------------------------------------------ //

TcpIqDevice::~TcpIqDevice() {
    try { close(); } catch (...) {}
}

HardwareError TcpIqDevice::not_open_error() const {
    return HardwareError(TCPIQ_ERROR_NOT_OPEN, "the TCP IQ stream is not open");
}

void TcpIqDevice::open_connect(const std::string& host, uint16_t port, double timeout, const SampleFormat& format) {
    close();
    hw::check_format(format);
    sockets_init();
    AddrInfo addrs;
    resolve(addrs, host, port, false, TCPIQ_ERROR_CONNECT);
    std::string why = "no address";
    for (addrinfo* ai = addrs.list; ai; ai = ai->ai_next) {
        sock_t s = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (s == NO_SOCKET) {
            why = sock_strerror(sock_errno());
            continue;
        }
        set_rcvbuf(s);
        int err = connect_within(s, ai, timeout);
        if (err == 0) {
            auto* c = new TcpIqConn;
            c->client = s;
            port_ = local_port(s);
            {
                std::lock_guard<std::mutex> lock(peer_mutex_);
                peer_ = addr_text(ai->ai_addr, static_cast<socklen_t>(ai->ai_addrlen));
            }
            format_ = format;
            listening_ = false;
            eof_ = false;
            buf_.assign(CHUNK_BYTES + format.bytes_per_sample() + RTL_HEADER, 0);
            new_stream();
            handle_ = c;
            return;
        }
        why = err == -1 ? "timed out" : sock_strerror(err);
        sock_close(s);
    }
    throw HardwareError(TCPIQ_ERROR_CONNECT, "cannot connect to " + host + ":" + std::to_string(port) + ": " + why);
}

void TcpIqDevice::open_listen(const std::string& host, uint16_t port, const SampleFormat& format) {
    close();
    hw::check_format(format);
    sockets_init();
    AddrInfo addrs;
    resolve(addrs, host, port, true, TCPIQ_ERROR_BIND);
    std::string why = "no address";
    for (addrinfo* ai = addrs.list; ai; ai = ai->ai_next) {
        sock_t s = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (s == NO_SOCKET) {
            why = sock_strerror(sock_errno());
            continue;
        }
        int on = 1;
#ifdef _WIN32   // Windows' SO_REUSEADDR would let us take a port another program listens on
        setsockopt(s, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char*>(&on), sizeof(on));
#else           // rebinding right after a previous server closed (TIME_WAIT)
        setsockopt(s, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&on), sizeof(on));
#endif
        set_rcvbuf(s);   // inherited by accepted sockets, before the handshake sizes the window
        if (bind(s, ai->ai_addr, static_cast<socklen_t>(ai->ai_addrlen)) != 0 || listen(s, 1) != 0) {
            why = sock_strerror(sock_errno());
            sock_close(s);
            continue;
        }
        port_ = local_port(s);
        auto* c = new TcpIqConn;
        c->listener = s;
        format_ = format;
        listening_ = true;
        eof_ = false;
        connections_ = 0;
        replaced_ = 0;
        buf_.assign(CHUNK_BYTES + format.bytes_per_sample() + RTL_HEADER, 0);
        new_stream();
        handle_ = c;
        stop_ = false;
        io_ = std::thread([this] { io_loop(); });   // accepts and drains from now until close()
        return;
    }
    throw HardwareError(TCPIQ_ERROR_BIND, "cannot listen on " + (host.empty() ? "*" : host) + ":" + std::to_string(port) + ": " + why);
}

void TcpIqDevice::close() {
    std::lock_guard<std::mutex> lock(stream_mutex_);
    if (!handle_) return;
    stop_thread();
    TcpIqConn* c = handle_;
    handle_ = nullptr;
    if (c->client != NO_SOCKET) sock_close(c->client);
    if (c->listener != NO_SOCKET) sock_close(c->listener);
    delete c;
    streaming_ = false;
    std::lock_guard<std::mutex> plock(peer_mutex_);
    peer_.clear();
}

void TcpIqDevice::start_rx() {
    require();
    std::lock_guard<std::mutex> lock(stream_mutex_);
    if (streaming_) throw HardwareError(TCPIQ_ERROR_BUSY, "the TCP IQ stream is already receiving");
    streaming_ = true;
    if (listening_) return;   // the thread is already reading
    if (io_.joinable()) io_.join();   // a stream that ended by itself
    if (eof_) return;
    stop_ = false;
    io_ = std::thread([this] { io_loop(); });
}

void TcpIqDevice::stop_rx() {
    require();
    std::lock_guard<std::mutex> lock(stream_mutex_);
    streaming_ = false;
    if (!listening_) stop_thread();   // listening, the thread keeps draining the client
}

void TcpIqDevice::stop_thread() {
    if (!io_.joinable()) return;
    if (io_.get_id() == std::this_thread::get_id())
        throw HardwareError(TCPIQ_ERROR_BUSY, "stop_rx cannot be called from the receive callback; return non-zero instead");
    stop_ = true;
    io_.join();
    stop_ = false;
}

std::string TcpIqDevice::peer() const {
    std::lock_guard<std::mutex> lock(peer_mutex_);
    return peer_;
}

void TcpIqDevice::drop_client() {
    sock_close(handle_->client);
    handle_->client = NO_SOCKET;
    new_stream();
    std::lock_guard<std::mutex> lock(peer_mutex_);
    peer_.clear();
}

// A connection's stream starts: nothing carried, and its first bytes are checked for an rtl_tcp header
void TcpIqDevice::new_stream() {
    carry_ = 0;
    head_len_ = 0;
    sniffing_ = true;
    rtl_seen_ = false;
}

// rtl_tcp (librtlsdr's server) starts its stream with 12 bytes of "dongle info": "RTL0", then the tuner type and the
// number of gain steps (big-endian uint32). They are not samples: the first bytes of a stream are held back until they
// show whether they are that header (then dropped) or samples (then put back in front). `got` new bytes are at the
// start of buf_ (nothing is carried yet); returns how many bytes of samples are there now
size_t TcpIqDevice::sniff_header(size_t got) {
    static const char magic[4] = {'R', 'T', 'L', '0'};
    const size_t held = head_len_;
    const size_t take = std::min(RTL_HEADER - head_len_, got);
    std::memcpy(head_ + head_len_, buf_.data(), take);
    head_len_ += take;
    if (std::memcmp(head_, magic, std::min<size_t>(head_len_, 4)) != 0) {   // samples: put what was held in front
        sniffing_ = false;
        if (held) {
            std::memmove(buf_.data() + held, buf_.data(), got);
            std::memcpy(buf_.data(), head_, held);
        }
        return held + got;
    }
    if (head_len_ < RTL_HEADER) return 0;   // (so far) a header: wait for the rest of it
    sniffing_ = false;
    auto be32 = [this](size_t at) {
        return (uint32_t(head_[at]) << 24) | (uint32_t(head_[at + 1]) << 16) | (uint32_t(head_[at + 2]) << 8) | head_[at + 3];
    };
    rtl_tuner_ = be32(4);
    rtl_gains_ = be32(8);
    rtl_seen_ = true;
    if (take < got) std::memmove(buf_.data(), buf_.data() + take, got - take);
    return got - take;
}

// `got` new bytes are in buf_ after the carried ones: queue the whole samples (while receiving), keep a split one
void TcpIqDevice::receive(size_t got) {
    if (sniffing_) {
        got = sniff_header(got);
        if (!got) return;
    }
    const size_t bps = format_.bytes_per_sample();
    const size_t total = carry_ + got;
    const size_t whole = total - total % bps;
    if (whole && streaming_) {
        if (dispatch_rx(buf_.data(), whole) != 0) streaming_ = false;   // no queue to take it
    }
    carry_ = total - whole;
    if (carry_) std::memmove(buf_.data(), buf_.data() + whole, carry_);
}

// Listening: take a client waiting on the listener. One already connected is dropped for it: a sender that reconnects
// does so because its connection died, though this end may not have noticed yet
bool TcpIqDevice::accept_client(TcpIqConn* c) {
    sockaddr_storage from{};
    socklen_t len = sizeof(from);
    sock_t s = accept(c->listener, reinterpret_cast<sockaddr*>(&from), &len);
    if (s == NO_SOCKET) return false;
    if (c->client != NO_SOCKET) {
        drop_client();
        ++replaced_;
    }
    set_blocking(s, true);
    set_rcvbuf(s);
    set_keepalive(s);
    c->client = s;
    new_stream();
    {
        std::lock_guard<std::mutex> lock(peer_mutex_);
        peer_ = addr_text(reinterpret_cast<sockaddr*>(&from), len);
    }
    ++connections_;
    return true;
}

void TcpIqDevice::io_loop() {
    TcpIqConn* c = handle_;   // valid until close(), which joins this thread first
    while (!stop_) {
        if (c->client == NO_SOCKET) {   // listening, no client: wait for one
            pollfd p{};
            p.fd = c->listener;
            p.events = POLLIN;
            if (sock_poll(&p, 1, POLL_MS) > 0) accept_client(c);
            continue;
        }
        pollfd p[2]{};
        p[0].fd = c->client;
        p[0].events = POLLIN;
        unsigned n_fds = 1;
        if (listening_) {   // and for a client reconnecting
            p[1].fd = c->listener;
            p[1].events = POLLIN;
            n_fds = 2;
        }
        int r = sock_poll(p, n_fds, POLL_MS);
        if (r == 0) continue;
        if (r < 0 && interrupted(sock_errno())) continue;
        if (r > 0 && listening_ && (p[1].revents & POLLIN)) {
            accept_client(c);
            continue;
        }
        long n = -1;
        if (r > 0) {
            n = static_cast<long>(recv(c->client, reinterpret_cast<char*>(buf_.data() + carry_),
                                       static_cast<int>(CHUNK_BYTES), 0));
            if (n > 0) {
                receive(static_cast<size_t>(n));
                continue;
            }
            if (n < 0 && interrupted(sock_errno())) continue;
        }
        // the other end closed, or the connection failed (or keepalive found it dead)
        if (listening_) {
            drop_client();   // and wait for the next, or the same one reconnecting
            continue;
        }
        eof_ = true;
        return;
    }
}
