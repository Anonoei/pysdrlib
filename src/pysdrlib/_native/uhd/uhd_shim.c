#include "uhd_shim.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <uhd.h>

struct ushim {
    uhd_usrp_handle usrp;
    uhd_rx_streamer_handle rx;
    uhd_tx_streamer_handle tx;
    uhd_rx_metadata_handle rxmd;
    uhd_tx_metadata_handle txmd;
    uhd_async_metadata_handle am;
    size_t rx_channels;
    char err[512];
    char rx_err[512];
    char tx_err[512];
};

static int chk(ushim* s, uhd_error e) {
    if (e != UHD_ERROR_NONE) {
        s->err[0] = 0;
        uhd_usrp_last_error(s->usrp, s->err, sizeof s->err);
    }
    return (int)e;
}

static void copy_out(char* out, size_t cap, const char* text) {
    if (!cap) return;
    strncpy(out, text, cap - 1);
    out[cap - 1] = 0;
}

/* a uhd_string_vector as '\n'-separated text */
static int join(uhd_string_vector_handle v, char* out, size_t cap) {
    size_t n = 0, used = 0;
    char item[256];
    if (cap) out[0] = 0;
    uhd_string_vector_size(v, &n);
    for (size_t i = 0; i < n; i++) {
        item[0] = 0;
        uhd_string_vector_at(v, i, item, sizeof item);
        size_t len = strlen(item);
        if (used + len + 2 > cap) break;
        memcpy(out + used, item, len);
        used += len;
        out[used++] = '\n';
        out[used] = 0;
    }
    if (used && out[used - 1] == '\n') out[used - 1] = 0;
    return 0;
}

int ushim_find(const char* args, char* out, size_t cap) {
    uhd_string_vector_handle v = NULL;
    uhd_error e = uhd_string_vector_make(&v);
    if (e) return (int)e;
    e = uhd_usrp_find(args ? args : "", &v);
    if (!e) join(v, out, cap);
    uhd_string_vector_free(&v);
    return (int)e;
}

int ushim_open(const char* args, ushim** out, char* err, size_t err_cap) {
    ushim* s = (ushim*)calloc(1, sizeof *s);
    if (!s) return -1;
    uhd_error e = uhd_usrp_make(&s->usrp, args ? args : "");
    if (e) {
        if (s->usrp == NULL) {
            /* uhd_usrp_make does not give us a handle to ask: UHD keeps the message in a global */
            copy_out(err, err_cap, "uhd_usrp_make failed");
        }
        free(s);
        return (int)e;
    }
    uhd_rx_metadata_make(&s->rxmd);
    uhd_tx_metadata_make(&s->txmd, false, 0, 0.0, false, false);
    uhd_async_metadata_make(&s->am);
    *out = s;
    return 0;
}

void ushim_close(ushim* s) {
    if (!s) return;
    ushim_rx_close(s);
    ushim_tx_close(s);
    if (s->rxmd) uhd_rx_metadata_free(&s->rxmd);
    if (s->txmd) uhd_tx_metadata_free(&s->txmd);
    if (s->am) uhd_async_metadata_free(&s->am);
    if (s->usrp) uhd_usrp_free(&s->usrp);
    free(s);
}

const char* ushim_error(const ushim* s) { return s->err; }

/* ---- motherboard ---- */
int ushim_info(ushim* s, char* out, size_t cap) {
    uhd_usrp_rx_info_t info;
    memset(&info, 0, sizeof info);
    uhd_error e = uhd_usrp_get_rx_info(s->usrp, 0, &info);
    if (!e) {
        snprintf(out, cap, "mboard_id=%s\nmboard_name=%s\nmboard_serial=%s\nrx_id=%s\nrx_subdev_name=%s\nrx_subdev_spec=%s",
                 info.mboard_id ? info.mboard_id : "", info.mboard_name ? info.mboard_name : "",
                 info.mboard_serial ? info.mboard_serial : "", info.rx_id ? info.rx_id : "",
                 info.rx_subdev_name ? info.rx_subdev_name : "", info.rx_subdev_spec ? info.rx_subdev_spec : "");
        uhd_usrp_rx_info_free(&info);
    }
    return chk(s, e);
}
int ushim_mboard_name(ushim* s, char* out, size_t cap) { return chk(s, uhd_usrp_get_mboard_name(s->usrp, 0, out, cap)); }
int ushim_set_master_clock_rate(ushim* s, double r) { return chk(s, uhd_usrp_set_master_clock_rate(s->usrp, r, 0)); }
int ushim_master_clock_rate(ushim* s, double* r) { return chk(s, uhd_usrp_get_master_clock_rate(s->usrp, 0, r)); }
int ushim_set_clock_source(ushim* s, const char* n) { return chk(s, uhd_usrp_set_clock_source(s->usrp, n, 0)); }
int ushim_clock_source(ushim* s, char* out, size_t cap) { return chk(s, uhd_usrp_get_clock_source(s->usrp, 0, out, cap)); }
int ushim_set_time_source(ushim* s, const char* n) { return chk(s, uhd_usrp_set_time_source(s->usrp, n, 0)); }
int ushim_time_source(ushim* s, char* out, size_t cap) { return chk(s, uhd_usrp_get_time_source(s->usrp, 0, out, cap)); }

static int string_list(ushim* s, uhd_error (*get)(uhd_usrp_handle, size_t, uhd_string_vector_handle*), char* out, size_t cap) {
    uhd_string_vector_handle v = NULL;
    uhd_string_vector_make(&v);
    uhd_error e = get(s->usrp, 0, &v);
    if (!e) join(v, out, cap);
    uhd_string_vector_free(&v);
    return chk(s, e);
}
int ushim_clock_sources(ushim* s, char* out, size_t cap) { return string_list(s, uhd_usrp_get_clock_sources, out, cap); }
int ushim_time_sources(ushim* s, char* out, size_t cap) { return string_list(s, uhd_usrp_get_time_sources, out, cap); }

int ushim_time_now(ushim* s, double* seconds) {
    int64_t full = 0;
    double frac = 0;
    uhd_error e = uhd_usrp_get_time_now(s->usrp, 0, &full, &frac);
    *seconds = (double)full + frac;
    return chk(s, e);
}
int ushim_set_time_now(ushim* s, double t) {
    int64_t full = (int64_t)t;
    return chk(s, uhd_usrp_set_time_now(s->usrp, full, t - (double)full, 0));
}
int ushim_set_time_next_pps(ushim* s, double t) {
    int64_t full = (int64_t)t;
    return chk(s, uhd_usrp_set_time_next_pps(s->usrp, full, t - (double)full, 0));
}

/* "name=value unit" lines for the sensors named in `names` (read with `get`) */
static void sensor_lines(ushim* s, uhd_string_vector_handle names, int dir, size_t chan, int mboard, char* out, size_t cap) {
    size_t n = 0, used = 0;
    char name[128], value[160], unit[32];
    if (cap) out[0] = 0;
    uhd_string_vector_size(names, &n);
    for (size_t i = 0; i < n; i++) {
        uhd_sensor_value_handle sv = NULL;
        uhd_sensor_value_make(&sv);
        name[0] = 0;
        uhd_string_vector_at(names, i, name, sizeof name);
        uhd_error e;
        if (mboard) e = uhd_usrp_get_mboard_sensor(s->usrp, name, 0, &sv);
        else if (dir == 0) e = uhd_usrp_get_rx_sensor(s->usrp, name, chan, &sv);
        else e = uhd_usrp_get_tx_sensor(s->usrp, name, chan, &sv);
        if (!e) {
            value[0] = unit[0] = 0;
            uhd_sensor_value_value(sv, value, sizeof value);
            uhd_sensor_value_unit(sv, unit, sizeof unit);
            int len = snprintf(out + used, cap - used, "%s%s=%s%s%s", used ? "\n" : "", name, value, unit[0] ? " " : "", unit);
            if (len < 0 || (size_t)len >= cap - used) { uhd_sensor_value_free(&sv); break; }
            used += (size_t)len;
        }
        uhd_sensor_value_free(&sv);
    }
}

int ushim_mboard_sensors(ushim* s, char* out, size_t cap) {
    uhd_string_vector_handle v = NULL;
    uhd_string_vector_make(&v);
    uhd_error e = uhd_usrp_get_mboard_sensor_names(s->usrp, 0, &v);
    if (!e) sensor_lines(s, v, 0, 0, 1, out, cap);
    uhd_string_vector_free(&v);
    return chk(s, e);
}
int ushim_channel_sensors(ushim* s, int dir, size_t chan, char* out, size_t cap) {
    uhd_string_vector_handle v = NULL;
    uhd_string_vector_make(&v);
    uhd_error e = dir == 0 ? uhd_usrp_get_rx_sensor_names(s->usrp, chan, &v) : uhd_usrp_get_tx_sensor_names(s->usrp, chan, &v);
    if (!e) sensor_lines(s, v, dir, chan, 0, out, cap);
    uhd_string_vector_free(&v);
    return chk(s, e);
}

/* ---- channels ---- */
int ushim_num_channels(ushim* s, int dir, size_t* n) {
    return chk(s, dir == 0 ? uhd_usrp_get_rx_num_channels(s->usrp, n) : uhd_usrp_get_tx_num_channels(s->usrp, n));
}
int ushim_set_rate(ushim* s, int dir, double r, size_t c) {
    return chk(s, dir == 0 ? uhd_usrp_set_rx_rate(s->usrp, r, c) : uhd_usrp_set_tx_rate(s->usrp, r, c));
}
int ushim_rate(ushim* s, int dir, size_t c, double* r) {
    return chk(s, dir == 0 ? uhd_usrp_get_rx_rate(s->usrp, c, r) : uhd_usrp_get_tx_rate(s->usrp, c, r));
}

static int range_bounds(ushim* s, void* unused, uhd_meta_range_handle r, double* lo, double* hi, double* step) {
    (void)s;
    (void)unused;
    uhd_meta_range_start(r, lo);
    uhd_meta_range_stop(r, hi);
    if (step) uhd_meta_range_step(r, step);
    return 0;
}

int ushim_rate_ranges(ushim* s, int dir, size_t c, double* out, size_t max_ranges, size_t* n) {
    uhd_meta_range_handle r = NULL;
    uhd_meta_range_make(&r);
    uhd_error e = dir == 0 ? uhd_usrp_get_rx_rates(s->usrp, c, r) : uhd_usrp_get_tx_rates(s->usrp, c, r);
    *n = 0;
    if (!e) {
        size_t count = 0;
        uhd_meta_range_size(r, &count);
        for (size_t i = 0; i < count && i < max_ranges; i++) {
            uhd_range_t one;
            uhd_meta_range_at(r, i, &one);
            out[3 * i] = one.start;
            out[3 * i + 1] = one.stop;
            out[3 * i + 2] = one.step;
            *n = i + 1;
        }
    }
    uhd_meta_range_free(&r);
    return chk(s, e);
}

static int tune(ushim* s, int dir, double freq, size_t c) {
    uhd_tune_request_t req;
    memset(&req, 0, sizeof req);
    req.target_freq = freq;
    req.rf_freq_policy = UHD_TUNE_REQUEST_POLICY_AUTO;
    req.dsp_freq_policy = UHD_TUNE_REQUEST_POLICY_AUTO;
    uhd_tune_result_t result;
    memset(&result, 0, sizeof result);
    return chk(s, dir == 0 ? uhd_usrp_set_rx_freq(s->usrp, &req, c, &result) : uhd_usrp_set_tx_freq(s->usrp, &req, c, &result));
}
int ushim_set_freq(ushim* s, int dir, double f, size_t c) { return tune(s, dir, f, c); }
int ushim_freq(ushim* s, int dir, size_t c, double* f) {
    return chk(s, dir == 0 ? uhd_usrp_get_rx_freq(s->usrp, c, f) : uhd_usrp_get_tx_freq(s->usrp, c, f));
}
int ushim_freq_range(ushim* s, int dir, size_t c, double* lo, double* hi) {
    uhd_meta_range_handle r = NULL;
    uhd_meta_range_make(&r);
    uhd_error e = dir == 0 ? uhd_usrp_get_rx_freq_range(s->usrp, c, r) : uhd_usrp_get_tx_freq_range(s->usrp, c, r);
    if (!e) range_bounds(s, NULL, r, lo, hi, NULL);
    uhd_meta_range_free(&r);
    return chk(s, e);
}

int ushim_set_gain(ushim* s, int dir, double g, size_t c) {
    return chk(s, dir == 0 ? uhd_usrp_set_rx_gain(s->usrp, g, c, "") : uhd_usrp_set_tx_gain(s->usrp, g, c, ""));
}
int ushim_gain(ushim* s, int dir, size_t c, double* g) {
    return chk(s, dir == 0 ? uhd_usrp_get_rx_gain(s->usrp, c, "", g) : uhd_usrp_get_tx_gain(s->usrp, c, "", g));
}
int ushim_gain_range(ushim* s, int dir, size_t c, double* lo, double* hi, double* step) {
    uhd_meta_range_handle r = NULL;
    uhd_meta_range_make(&r);
    uhd_error e = dir == 0 ? uhd_usrp_get_rx_gain_range(s->usrp, "", c, r) : uhd_usrp_get_tx_gain_range(s->usrp, "", c, r);
    if (!e) range_bounds(s, NULL, r, lo, hi, step);
    uhd_meta_range_free(&r);
    return chk(s, e);
}
int ushim_set_agc(ushim* s, int enable, size_t c) { return chk(s, uhd_usrp_set_rx_agc(s->usrp, enable != 0, c)); }

int ushim_set_bandwidth(ushim* s, int dir, double bw, size_t c) {
    return chk(s, dir == 0 ? uhd_usrp_set_rx_bandwidth(s->usrp, bw, c) : uhd_usrp_set_tx_bandwidth(s->usrp, bw, c));
}
int ushim_bandwidth(ushim* s, int dir, size_t c, double* bw) {
    return chk(s, dir == 0 ? uhd_usrp_get_rx_bandwidth(s->usrp, c, bw) : uhd_usrp_get_tx_bandwidth(s->usrp, c, bw));
}
int ushim_bandwidth_range(ushim* s, int dir, size_t c, double* lo, double* hi) {
    uhd_meta_range_handle r = NULL;
    uhd_meta_range_make(&r);
    uhd_error e = dir == 0 ? uhd_usrp_get_rx_bandwidth_range(s->usrp, c, r) : uhd_usrp_get_tx_bandwidth_range(s->usrp, c, r);
    if (!e) range_bounds(s, NULL, r, lo, hi, NULL);
    uhd_meta_range_free(&r);
    return chk(s, e);
}

int ushim_set_antenna(ushim* s, int dir, const char* n, size_t c) {
    return chk(s, dir == 0 ? uhd_usrp_set_rx_antenna(s->usrp, n, c) : uhd_usrp_set_tx_antenna(s->usrp, n, c));
}
int ushim_antenna(ushim* s, int dir, size_t c, char* out, size_t cap) {
    return chk(s, dir == 0 ? uhd_usrp_get_rx_antenna(s->usrp, c, out, cap) : uhd_usrp_get_tx_antenna(s->usrp, c, out, cap));
}
int ushim_antennas(ushim* s, int dir, size_t c, char* out, size_t cap) {
    uhd_string_vector_handle v = NULL;
    uhd_string_vector_make(&v);
    uhd_error e = dir == 0 ? uhd_usrp_get_rx_antennas(s->usrp, c, &v) : uhd_usrp_get_tx_antennas(s->usrp, c, &v);
    if (!e) join(v, out, cap);
    uhd_string_vector_free(&v);
    return chk(s, e);
}
int ushim_set_dc_offset(ushim* s, int en, size_t c) { return chk(s, uhd_usrp_set_rx_dc_offset_enabled(s->usrp, en != 0, c)); }
int ushim_set_iq_balance(ushim* s, int en, size_t c) { return chk(s, uhd_usrp_set_rx_iq_balance_enabled(s->usrp, en != 0, c)); }

/* ---- streaming ---- */
static int open_streamer(ushim* s, int rx, const size_t* channels, size_t n, size_t* max_samps) {
    char cpu[] = "sc16", otw[] = "sc16", args[] = "";
    size_t* list = (size_t*)malloc(n * sizeof *list);
    if (!list) return -1;
    memcpy(list, channels, n * sizeof *list);
    uhd_stream_args_t sa;
    sa.cpu_format = cpu;
    sa.otw_format = otw;
    sa.args = args;
    sa.channel_list = list;
    sa.n_channels = (int)n;
    uhd_error e;
    if (rx) {
        e = uhd_rx_streamer_make(&s->rx);
        if (!e) e = uhd_usrp_get_rx_stream(s->usrp, &sa, s->rx);
        if (!e) e = uhd_rx_streamer_max_num_samps(s->rx, max_samps);
        if (e) uhd_rx_streamer_last_error(s->rx, s->err, sizeof s->err);
        else s->rx_channels = n;
    } else {
        e = uhd_tx_streamer_make(&s->tx);
        if (!e) e = uhd_usrp_get_tx_stream(s->usrp, &sa, s->tx);
        if (!e) e = uhd_tx_streamer_max_num_samps(s->tx, max_samps);
        if (e) uhd_tx_streamer_last_error(s->tx, s->err, sizeof s->err);
    }
    free(list);
    return (int)e;
}
int ushim_rx_open(ushim* s, const size_t* ch, size_t n, size_t* m) { ushim_rx_close(s); return open_streamer(s, 1, ch, n, m); }
int ushim_tx_open(ushim* s, const size_t* ch, size_t n, size_t* m) { ushim_tx_close(s); return open_streamer(s, 0, ch, n, m); }
void ushim_rx_close(ushim* s) { if (s->rx) uhd_rx_streamer_free(&s->rx); s->rx = NULL; }
void ushim_tx_close(ushim* s) { if (s->tx) uhd_tx_streamer_free(&s->tx); s->tx = NULL; }

static int stream_cmd(ushim* s, uhd_stream_mode_t mode) {
    if (!s->rx) return -1;
    uhd_stream_cmd_t cmd;
    memset(&cmd, 0, sizeof cmd);
    cmd.stream_mode = mode;
    cmd.stream_now = true;
    if (mode == UHD_STREAM_MODE_START_CONTINUOUS && s->rx_channels > 1) {
        /* Several channels in one streamer must start on the same sample: at a time on the device's clock, a little
           ahead so the command arrives first (UHD refuses "now" for them) */
        int64_t full = 0;
        double frac = 0;
        uhd_error e = uhd_usrp_get_time_now(s->usrp, 0, &full, &frac);
        if (e) return chk(s, e);
        frac += 0.05;
        if (frac >= 1.0) { full += 1; frac -= 1.0; }
        cmd.stream_now = false;
        cmd.time_spec_full_secs = full;
        cmd.time_spec_frac_secs = frac;
    }
    uhd_error e = uhd_rx_streamer_issue_stream_cmd(s->rx, &cmd);
    if (e) uhd_rx_streamer_last_error(s->rx, s->err, sizeof s->err);
    return (int)e;
}
int ushim_rx_start(ushim* s) { return stream_cmd(s, UHD_STREAM_MODE_START_CONTINUOUS); }
int ushim_rx_stop(ushim* s) { return stream_cmd(s, UHD_STREAM_MODE_STOP_CONTINUOUS); }

int ushim_rx_recv(ushim* s, void** bufs, size_t samps, double timeout, size_t* got) {
    *got = 0;
    uhd_error e = uhd_rx_streamer_recv(s->rx, bufs, samps, &s->rxmd, timeout, false, got);
    if (e) {
        uhd_rx_streamer_last_error(s->rx, s->rx_err, sizeof s->rx_err);
        memcpy(s->err, s->rx_err, sizeof s->err);
        return (int)e;
    }
    uhd_rx_metadata_error_code_t code = UHD_RX_METADATA_ERROR_CODE_NONE;
    uhd_rx_metadata_error_code(s->rxmd, &code);
    switch (code) {
        case UHD_RX_METADATA_ERROR_CODE_NONE: return 0;
        case UHD_RX_METADATA_ERROR_CODE_TIMEOUT: return USHIM_RX_TIMEOUT;
        case UHD_RX_METADATA_ERROR_CODE_OVERFLOW: return USHIM_RX_OVERFLOW;
        default:
            uhd_rx_metadata_strerror(s->rxmd, s->err, sizeof s->err);
            return USHIM_RX_OTHER;
    }
}

int ushim_rx_time(ushim* s, double* seconds) {
    bool has = false;
    int64_t full = 0;
    double frac = 0;
    if (uhd_rx_metadata_has_time_spec(s->rxmd, &has) || !has) return 1;
    if (uhd_rx_metadata_time_spec(s->rxmd, &full, &frac)) return 1;
    *seconds = (double)full + frac;
    return 0;
}

int ushim_tx_send(ushim* s, const void** bufs, size_t samps, double timeout, int sob, int eob, size_t* sent) {
    uhd_tx_metadata_handle md = NULL;
    *sent = 0;
    uhd_error e = uhd_tx_metadata_make(&md, false, 0, 0.0, sob != 0, eob != 0);
    if (!e) e = uhd_tx_streamer_send(s->tx, bufs, samps, &md, timeout, sent);
    if (e) uhd_tx_streamer_last_error(s->tx, s->err, sizeof s->err);
    if (md) uhd_tx_metadata_free(&md);
    return (int)e;
}

int ushim_tx_async(ushim* s) {
    bool valid = false;
    if (uhd_tx_streamer_recv_async_msg(s->tx, &s->am, 0.0, &valid) != UHD_ERROR_NONE || !valid) return 0;
    uhd_async_metadata_event_code_t code = UHD_ASYNC_METADATA_EVENT_CODE_BURST_ACK;
    uhd_async_metadata_event_code(s->am, &code);
    switch (code) {
        case UHD_ASYNC_METADATA_EVENT_CODE_BURST_ACK: return 0;
        case UHD_ASYNC_METADATA_EVENT_CODE_UNDERFLOW:
        case UHD_ASYNC_METADATA_EVENT_CODE_UNDERFLOW_IN_PACKET:
        case UHD_ASYNC_METADATA_EVENT_CODE_SEQ_ERROR:
        case UHD_ASYNC_METADATA_EVENT_CODE_SEQ_ERROR_IN_BURST: return USHIM_TX_UNDERFLOW;
        default: return -1;
    }
}
