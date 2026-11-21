# TCP IQ stream

Raw interleaved I/Q over TCP, at either end of the connection. pysdrlib can be the client of a server that sends
samples (an `rtl_tcp`-style source, a GNU Radio TCP sink in server mode, a radio on another machine that pysdrlib has no
driver for). It can also be the server that a sender connects to (a GNU Radio TCP sink in client mode, `nc`, a script).
It is receive-only.

    # client: connect to a server that sends samples
    sdr = pysdrlib.open("tcpiq", host="10.0.0.5", port=1234)

    # server: listen for a client that sends samples (every interface by default)
    sdr = pysdrlib.open("tcpiq", listen=True, port=1234)

    sdr.initialize(cf=100e6, Fs=2e6)     # says what the stream is; a stream can't be retuned from here
    sdr.start_rx()
    samples = sdr.read(65536, timeout=1.0)

## Receiving
The socket is read on a thread of the extension's own, in C++ and without the GIL, like the radios' streaming threads.
That thread converts the samples to complex64 and queues them. It reads as fast as the network delivers, so a busy
Python process never slows the sender down. If Python doesn't read in time, the queue (0.5 s of the declared rate, at
most 2^24 samples) drops the oldest samples, and this is reported like any native device's drops (`on_drop`, default:
the `Dropped` warning). There is no stall watchdog, because the sender decides when to send.

## Server
- `listen=True` binds to `host` (default `0.0.0.0`, every interface; pass `host="127.0.0.1"` to keep it local) and
  `port` (`0`: any free port, see `sdr.port`). A port that is in use raises `err.NoDevice`.
- It listens from `open` to `close` and serves one client at a time. When that client disconnects, it accepts the next
  one; a client that connects in the meantime waits.
- Samples that arrive while not receiving (before `start_rx`, after `stop_rx`) are read and discarded, so a sender is
  never blocked.
- `sdr.peer` is the connected client (`"host:port"`, or None), and `sdr.connections` counts the clients accepted.

## Specs
- Frequency and sample rate: whatever the source sends. `cf` and `Fs` are what you declare it to be.
- Samples: `open(..., sample_format=...)` with a SigMF-style name (`"cf32"`, the default: float32 I, Q, GNU Radio's
  `complex64`; `"ci16"`, `"ci8"`, `"cu8"` for `rtl_tcp`-style unsigned bytes, `"cu16"`, `"ci32"`, `"cf64"`, optionally
  with `_be`) or any `pysdrlib.base.convert.SampleFormat`. The stream is little-endian unless the format says otherwise. A sample split across two
  reads is put back together.
- Gain: none (`rx_gain_stages()` is empty).

## Notes
- It is not listed by `pysdrlib.find()`: there is nothing to enumerate.
- Client: a stream that ends (the server closed it) stops the receive thread, and `read` then times out. `sdr.eof` says so.
- The extension uses only the operating system's sockets, so there is no vendor library to build.
