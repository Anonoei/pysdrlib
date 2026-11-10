#ifndef PYSDRLIB_UHD_SHIM_H
#define PYSDRLIB_UHD_SHIM_H

/* A small, plain-C face on UHD's C API (uhd.h).
 *
 * uhd.h pulls in UHD's C++ headers (and so Boost) when it is included from C++. The shim is compiled as C, so
 * the extension needs only UHD's headers, never Boost, and the C++ side (uhd_device.cpp) sees none of UHD.
 *
 * Functions return 0 on success, or UHD's uhd_error code (non-zero) with the message in `ushim_error`.
 * Lists come back as one string, items separated by '\n'. */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ushim ushim;

/* error codes beyond UHD's (all positive; UHD's own are small) */
#define USHIM_RX_TIMEOUT   1001
#define USHIM_RX_OVERFLOW  1002
#define USHIM_RX_OTHER     1003
#define USHIM_TX_UNDERFLOW 1

int ushim_find(const char* args, char* out, size_t cap);   /* device addresses, one per line */
int ushim_open(const char* args, ushim** out, char* err, size_t err_cap);
void ushim_close(ushim* s);
const char* ushim_error(const ushim* s);

/* motherboard */
int ushim_info(ushim* s, char* out, size_t cap);   /* "key=value" per line: mboard_id, mboard_name, mboard_serial, rx_id, rx_subdev_name... */
int ushim_mboard_name(ushim* s, char* out, size_t cap);
int ushim_set_master_clock_rate(ushim* s, double rate);
int ushim_master_clock_rate(ushim* s, double* rate);
int ushim_set_clock_source(ushim* s, const char* name);
int ushim_clock_source(ushim* s, char* out, size_t cap);
int ushim_clock_sources(ushim* s, char* out, size_t cap);
int ushim_set_time_source(ushim* s, const char* name);
int ushim_time_source(ushim* s, char* out, size_t cap);
int ushim_time_sources(ushim* s, char* out, size_t cap);
int ushim_time_now(ushim* s, double* seconds);
int ushim_set_time_now(ushim* s, double seconds);
int ushim_set_time_next_pps(ushim* s, double seconds);
int ushim_mboard_sensors(ushim* s, char* out, size_t cap);   /* "name=value unit" per line */

/* channels: dir 0 = RX, 1 = TX */
int ushim_num_channels(ushim* s, int dir, size_t* n);
int ushim_set_rate(ushim* s, int dir, double rate, size_t chan);
int ushim_rate(ushim* s, int dir, size_t chan, double* rate);
int ushim_rate_ranges(ushim* s, int dir, size_t chan, double* out, size_t max_ranges, size_t* n);   /* lo, hi, step ... */
int ushim_set_freq(ushim* s, int dir, double freq, size_t chan);
int ushim_freq(ushim* s, int dir, size_t chan, double* freq);
int ushim_freq_range(ushim* s, int dir, size_t chan, double* lo, double* hi);
int ushim_set_gain(ushim* s, int dir, double gain, size_t chan);
int ushim_gain(ushim* s, int dir, size_t chan, double* gain);
int ushim_gain_range(ushim* s, int dir, size_t chan, double* lo, double* hi, double* step);
int ushim_set_agc(ushim* s, int enable, size_t chan);
int ushim_set_bandwidth(ushim* s, int dir, double bw, size_t chan);
int ushim_bandwidth(ushim* s, int dir, size_t chan, double* bw);
int ushim_bandwidth_range(ushim* s, int dir, size_t chan, double* lo, double* hi);
int ushim_set_antenna(ushim* s, int dir, const char* name, size_t chan);
int ushim_antenna(ushim* s, int dir, size_t chan, char* out, size_t cap);
int ushim_antennas(ushim* s, int dir, size_t chan, char* out, size_t cap);
int ushim_set_dc_offset(ushim* s, int enable, size_t chan);
int ushim_set_iq_balance(ushim* s, int enable, size_t chan);
int ushim_channel_sensors(ushim* s, int dir, size_t chan, char* out, size_t cap);

/* streaming. `channels`: the USRP channel numbers one streamer carries. Format is sc16 on the wire and in memory. */
int ushim_rx_open(ushim* s, const size_t* channels, size_t n, size_t* max_samps);
void ushim_rx_close(ushim* s);
int ushim_rx_start(ushim* s);
int ushim_rx_stop(ushim* s);
/* one call; `bufs`: one int16 buffer (interleaved I/Q) per channel. Returns 0, USHIM_RX_TIMEOUT, USHIM_RX_OVERFLOW
   (samples were lost: the next call continues) or USHIM_RX_OTHER / a UHD error (message in ushim_error). */
int ushim_rx_recv(ushim* s, void** bufs, size_t samps, double timeout, size_t* got);
/* device time of the first sample of the last ushim_rx_recv: 0 and *seconds, or 1 if it had none */
int ushim_rx_time(ushim* s, double* seconds);

int ushim_tx_open(ushim* s, const size_t* channels, size_t n, size_t* max_samps);
void ushim_tx_close(ushim* s);
int ushim_tx_send(ushim* s, const void** bufs, size_t samps, double timeout, int start_of_burst, int end_of_burst, size_t* sent);
/* drain one async message: 0 none, USHIM_TX_UNDERFLOW if an underflow/sequence error was reported, -1 other */
int ushim_tx_async(ushim* s);

#ifdef __cplusplus
}
#endif
#endif
