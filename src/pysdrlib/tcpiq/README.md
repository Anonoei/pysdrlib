# TCP IQ stream

Samples from a TCP server that sends raw interleaved I/Q: an `rtl_tcp`-style source, a GNU Radio TCP sink, a radio
on another machine that pysdrlib has no driver for. It is receive-only, and also the smallest complete Python driver:
read it before writing one (`pysdrlib.contributing`).

    sdr = pysdrlib.open("tcpiq", host="10.0.0.5", port=1234)
    sdr.initialize(cf=100e6, Fs=2e6)     # says what the stream is; a stream can't be retuned from here
    sdr.start_rx()
    samples = sdr.read(65536, timeout=1.0)

## Specs
- Frequency and sample rate: whatever the source sends. `cf` and `Fs` are what you declare it to be.
- Samples: any `pysdrlib.base.convert` layout, `TcpIq(sample_format=convert.INT16)` (default: float32 I, Q, which is
  GNU Radio's `complex64`). The stream is little-endian unless the format says otherwise. Converted to complex64.
- Gain: none (`rx_gain_stages()` is empty).

## Notes
- It is not listed by `pysdrlib.find()`: there is nothing to enumerate.
- A stream that ends (the server closed) stops the receive thread; `read` then times out. `sdr.state` shows `eof`.
