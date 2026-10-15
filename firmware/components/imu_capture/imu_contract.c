/* imu_contract.c — the CTG-9 capture contract in C.
 *
 * No allocation, no I/O, no libc timezone machinery. The only libc use is
 * snprintf/strtod for number formatting.
 *
 * TWO HARD REQUIREMENTS drive the shape of this file:
 *
 * 1. The JSON header this writer emits must be BYTE-IDENTICAL to the Python
 *    reference codec's `_pack_header`, i.e. `json.dumps(hdr, separators=(",",
 *    ":"), sort_keys=True)` — sorted keys at every level, no whitespace, floats
 *    in Python's shortest round-tripping repr. `tests/test_firmware_contract.py`
 *    calls both writers with the same inputs and diffs the bytes, so a drift in
 *    either writer fails the suite rather than producing a subtly different file.
 *    That is why the mode's `config` object is emitted from a literal string
 *    (copied from the reference dump) instead of being re-assembled from
 *    constants: the int-vs-float distinction inside it (`"aaf_bw_hz":785` is an
 *    int, `"odr_hz":4000.0` is not) is part of the bytes, and a literal is the
 *    only form that cannot quietly change.
 *
 * 2. The frame is written by hand, field by field, because the spec fixes the
 *    little-endian layout and a `struct`-style pack would have to be trusted to
 *    match it. `imu_pack_frame` is the one place bytes are laid down.
 */
#include "imu_contract.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ modes */

static const imu_mode_t IMU_MODES[IMU_MODE_COUNT] = {
    {
        IMU_MODE_A, 'A', 1, "ICM-42688-P", IMU_WHO_AM_I_ICM42688P,
        4000.0, 500.0, 1000000u, 16, 32, 2048,
        /* aaf */ 785.0, 17, 288, 7, /* ui */ 2, 4, 1048.1,
        /* mpu */ 0, 0.0, 0, 0.0, 0,
    },
    {
        IMU_MODE_B, 'B', 1, "ICM-42688-P", IMU_WHO_AM_I_ICM42688P,
        2000.0, 250.0, 1000000u, 16, 32, 2048,
        /* aaf */ 394.0, 9, 81, 9, /* ui */ 2, 4, 524.0,
        /* mpu */ 0, 0.0, 0, 0.0, 0,
    },
    {
        IMU_MODE_M, 'M', 0, "MPU-9250", IMU_WHO_AM_I_MPU9250,
        1000.0, 150.0, 400000u, 14, 16, 512,
        /* aaf */ 0.0, 0, 0, 0, /* ui */ 0, 0, 0.0,
        /* mpu */ 7, 420.0, 1, 184.0, 0,
    },
};

/* The mode's `notes.config` object, verbatim from the reference codec's dump of
 * MODE_CONFIG[mode]. Kept as text so the int/float typing cannot drift. */
static const char *mode_config_json(const imu_mode_t *m)
{
    switch (m->id) {
    case IMU_MODE_A:
        return "{\"aaf_bw_hz\":785,\"accel_aaf\":{\"bitshift\":7,\"bw_hz\":785"
               ",\"delt\":17,\"deltsqr\":288},\"accel_fsr_g\":16,\"band_hz\":500.0"
               ",\"fifo_packet_bytes\":16,\"fifo_wm_records\":32,"
               "\"gyro_aaf\":{\"bitshift\":7,\"bw_hz\":785,\"delt\":17,\"deltsqr\":288}"
               ",\"gyro_fsr_dps\":2000,\"low_noise\":true,\"odr_hz\":4000.0"
               ",\"ui_bw_hz\":1048.1,\"ui_filt_bw\":4,\"ui_filt_ord\":2}";
    case IMU_MODE_B:
        return "{\"aaf_bw_hz\":394,\"accel_aaf\":{\"bitshift\":9,\"bw_hz\":394"
               ",\"delt\":9,\"deltsqr\":81},\"accel_fsr_g\":16,\"band_hz\":250.0"
               ",\"fifo_packet_bytes\":16,\"fifo_wm_records\":32,"
               "\"gyro_aaf\":{\"bitshift\":9,\"bw_hz\":394,\"delt\":9,\"deltsqr\":81}"
               ",\"gyro_fsr_dps\":2000,\"low_noise\":true,\"odr_hz\":2000.0"
               ",\"ui_bw_hz\":524.0,\"ui_filt_bw\":4,\"ui_filt_ord\":2}";
    case IMU_MODE_M:
        return "{\"accel_fsr_g\":16,\"band_hz\":150.0,\"dlpf_bw_hz\":420.0,\"dlpf_cfg\":7"
               ",\"fifo_bytes\":512,\"fifo_packet_bytes\":14,\"fifo_wm_records\":16"
               ",\"gyro_bw_hz\":184.0,\"gyro_dlpf_cfg\":1,\"gyro_fsr_dps\":2000"
               ",\"low_noise\":true,\"odr_hz\":1000.0,\"smplrt_div\":0}";
    default:
        return NULL;
    }
}

const imu_mode_t *imu_mode_by_id(imu_mode_id_t id)
{
    if ((int)id < 0 || (int)id >= IMU_MODE_COUNT) {
        return NULL;
    }
    return &IMU_MODES[id];
}

const imu_mode_t *imu_mode_by_letter(char letter)
{
    if (letter >= 'a' && letter <= 'z') {
        letter = (char)(letter - 'a' + 'A');
    }
    for (int i = 0; i < IMU_MODE_COUNT; i++) {
        if (IMU_MODES[i].letter == letter) {
            return &IMU_MODES[i];
        }
    }
    return NULL;
}

double imu_scale_accel(int fsr_g)
{
    double lsb;
    switch (fsr_g) {
    case 2: lsb = 16384.0; break;
    case 4: lsb = 8192.0; break;
    case 8: lsb = 4096.0; break;
    case 16: lsb = 2048.0; break;
    default: return 0.0;
    }
    return IMU_G0 / lsb;
}

double imu_scale_gyro(double fsr_dps)
{
    double lsb;
    if (fsr_dps >= 2000.0) lsb = 16.4;
    else if (fsr_dps >= 1000.0) lsb = 32.8;
    else if (fsr_dps >= 500.0) lsb = 65.5;
    else if (fsr_dps >= 250.0) lsb = 131.0;
    else if (fsr_dps >= 125.0) lsb = 262.0;
    else if (fsr_dps >= 62.5) lsb = 524.3;
    else if (fsr_dps >= 31.25) lsb = 1048.6;
    else if (fsr_dps >= 15.625) lsb = 2097.2;
    else return 0.0;
    return 1.0 / lsb;
}

int imu_check_identity(const imu_mode_t *mode, uint8_t who_am_i, const char **out_sensor)
{
    if (mode == NULL) {
        return -1;
    }
    if (who_am_i != mode->who_am_i) {
        /* Deliberately NOT "auto-detect and continue": modes A/B and M measure
         * different bands (0-500 vs 0-150 Hz), so reinterpreting the capture
         * would silently change what it means (spec S2.4). */
        return -1;
    }
    if (out_sensor != NULL) {
        *out_sensor = mode->sensor;
    }
    return 0;
}

/* ------------------------------------------------------- number formatting */

/* Shortest decimal that round-trips, with an integral value rendered as "N.0" —
 * the form Python's repr() produces for every value this contract writes. The
 * cross-language test checks this against Python repr() value by value.
 *
 * Implementation note: this deliberately does NOT format with `%g`. `%g` falls
 * back to exponential notation as soon as the exponent reaches the precision, so
 * a naive shortest-precision search renders 1000.0 as "1e+03" where Python says
 * "1000.0" — which is exactly the class of mismatch that makes two writers of
 * "the same" file differ. Instead: find how many significant digits it takes to
 * round-trip, then render those digits in fixed notation with `%.*f`. */
static int fmt_double(char *out, size_t cap, double v)
{
    if (v < 0) {
        if (cap < 2) return -1;
        out[0] = '-';
        int r = fmt_double(out + 1, cap - 1, -v);
        return r < 0 ? -1 : r + 1;
    }
    if (v == 0.0) {
        if (cap < 4) return -1;
        memcpy(out, "0.0", 4);
        return 3;
    }
    /* shortest significant-digit count that survives a round trip */
    int sd = 17;
    for (int p = 1; p <= 17; p++) {
        char probe[64];
        int n = snprintf(probe, sizeof probe, "%.*e", p - 1, v);
        if (n <= 0 || (size_t)n >= sizeof probe) {
            continue;
        }
        if (strtod(probe, NULL) == v) {
            sd = p;
            break;
        }
    }
    /* the decimal exponent, so the digit count can be turned into decimals */
    char sci[64];
    if (snprintf(sci, sizeof sci, "%.*e", sd - 1, v) <= 0) {
        return -1;
    }
    int exp10 = 0;
    const char *e = strchr(sci, 'e');
    if (e == NULL || sscanf(e + 1, "%d", &exp10) != 1) {
        return -1;
    }
    /* decimals needed to show `sd` significant digits at this magnitude, never
     * negative: a value at or above 1e12 would need none */
    int decimals = sd - 1 - exp10;
    if (decimals < 0) {
        decimals = 0;
    }
    char buf[96];
    int n = snprintf(buf, sizeof buf, "%.*f", decimals, v);
    if (n <= 0 || (size_t)n >= sizeof buf) {
        return -1;
    }
    /* Python always shows a decimal point on a float: 1000 -> "1000.0" */
    if (strchr(buf, '.') == NULL) {
        if ((size_t)n + 2 >= sizeof buf) {
            return -1;
        }
        buf[n] = '.';
        buf[n + 1] = '0';
        buf[n + 2] = '\0';
        n += 2;
    }
    if ((size_t)n >= cap) {
        return -1;
    }
    memcpy(out, buf, (size_t)n + 1);
    return n;
}

/* JSON string for a RAW C string, escaping the two characters Python's json
 * module must escape in a provenance string (quote and backslash). A NULL input
 * becomes the JSON literal null, because spec S5.4 requires an unknown value to
 * be present-and-null rather than absent — "we did not know" and "we knew it was
 * nothing" are different facts and a missing key conflates them. */
static int fmt_json_string(char *out, size_t cap, const char *s)
{
    size_t k = 0;
    if (cap < 8) {
        return -1;
    }
    if (s == NULL) {
        memcpy(out, "null", 5);
        return 4;
    }
    out[k++] = '"';
    for (const char *p = s; *p != '\0'; p++) {
        if (k + 2 >= cap) return -1;
        if (*p == '"' || *p == '\\') {
            out[k++] = '\\';
        }
        out[k++] = *p;
    }
    if (k + 1 >= cap) return -1;
    out[k++] = '"';
    out[k] = '\0';
    return (int)k;
}

/* JSON value for an already-formed fragment (the wheel object). Accepts a raw
 * object literal or a NULL; refuses anything else, so a caller cannot smuggle
 * unescaped text into the header through the fragment path. */
static int fmt_json_fragment(char *out, size_t cap, const char *frag, const char *what)
{
    (void)what;
    if (frag == NULL) {
        if (cap < 8) return -1;
        memcpy(out, "null", 5);
        return 4;
    }
    size_t n = strlen(frag);
    if (n == 0 || n + 1 > cap) {
        return -1;
    }
    if (frag[0] != '{' && frag[0] != '[' && strcmp(frag, "null") != 0) {
        return -1; /* not a JSON fragment: escaping it would be a silent lie */
    }
    memcpy(out, frag, n + 1);
    return (int)n;
}

/* ------------------------------------------------------------- header JSON */

/* The one header writer. Argument order mirrors the reference codec's header
 * fields; the emitted key order is the fixed sorted order and must not be
 * reordered without regenerating the Python side. */
int imu_header_json(const imu_mode_t *mode, const char *device_id, uint64_t start_unix_ns,
                    const char *mounting, const char *wheel_json, const char *vehicle,
                    const char *firmware_rev, char *out, size_t out_cap)
{
    if (mode == NULL || out == NULL) {
        return -1;
    }
    const char *cfg = mode_config_json(mode);
    if (cfg == NULL) {
        return -1;
    }
    char dev[256], mount[512], veh[256], fw[256], wheel[256];
    if (fmt_json_string(dev, sizeof dev, device_id) < 0 ||
        fmt_json_string(mount, sizeof mount, mounting) < 0 ||
        fmt_json_string(veh, sizeof veh, vehicle) < 0 ||
        fmt_json_string(fw, sizeof fw, firmware_rev) < 0 ||
        fmt_json_fragment(wheel, sizeof wheel, wheel_json, "wheel") < 0) {
        return -1;
    }
    char odr[64], band[64], dband[64], sa[64], sg[64], gfsr[64];
    if (fmt_double(odr, sizeof odr, mode->odr_hz) < 0 ||
        fmt_double(band, sizeof band, mode->band_hz) < 0 ||
        fmt_double(dband, sizeof dband, 0.01) < 0 ||
        fmt_double(gfsr, sizeof gfsr, IMU_GYRO_FSR_DPS) < 0 ||
        fmt_double(sa, sizeof sa, imu_scale_accel(IMU_ACCEL_FSR_G)) < 0 ||
        fmt_double(sg, sizeof sg, imu_scale_gyro(IMU_GYRO_FSR_DPS)) < 0) {
        return -1;
    }
    /* notes, keys in sorted order; wheel/mounting/vehicle are provenance text.
     * `device_id` is deliberately NOT a notes key: it already names the
     * directory in the capture path (spec S6), and spec S5.4 does not require
     * it in the header. Keeping it out is what lets this writer stay
     * byte-identical to the Python reference for the same provenance. */
    char notes[2048];
    int nn = snprintf(notes, sizeof notes,
        "{\"band_hz\":%s,\"config\":%s,\"firmware\":%s,"
        "\"flags_legend\":{\"0\":\"FIFO overrun\",\"1\":\"host overrun\","
        "\"2\":\"timestamp estimated\"},\"i2c_addr\":\"0x68\",\"i2c_hz\":%u,"
        "\"mounting\":%s,\"sensor\":\"%s\",\"vehicle\":%s,\"wheel\":%s,"
        "\"who_am_i\":\"0x%02X\"}",
        band, cfg, fw, mode->i2c_hz, mount, mode->sensor, veh, wheel,
        (unsigned)mode->who_am_i);
    if (nn <= 0 || (size_t)nn >= sizeof notes) {
        return -1;
    }
    int n = snprintf(out, out_cap,
        "{\"accel_fsr_g\":%d,\"accel_units\":\"m/s^2\","
        "\"axes\":[\"ax\",\"ay\",\"az\",\"gx\",\"gy\",\"gz\"],\"device\":\"%s\","
        "\"endian\":\"little\",\"frame_bytes\":%d,\"frame_struct\":\"%s\","
        "\"fs_nominal\":%s,\"gyro_fsr_dps\":%s,\"gyro_units\":\"deg/s\","
        "\"gyro_units_note\":\"deg/s as returned by the sensor; convert with *pi/180 for rad/s\","
        "\"magic\":\"%s\",\"mode\":\"%c\",\"n_axes\":%d,\"notes\":%s,"
        "\"scale_accel\":%s,\"scale_gyro\":%s,\"spec_version\":\"%s\","
        "\"start_unix_ns\":%llu,\"temp_lsb_c\":%s}\n",
        IMU_ACCEL_FSR_G, mode->sensor, IMU_FRAME_BYTES, IMU_FRAME_STRUCT, odr,
        gfsr, IMU_MAGIC_V2, mode->letter, IMU_FRAME_N_AXES, notes, sa, sg,
        IMU_SPEC_VERSION, (unsigned long long)start_unix_ns, dband);
    if (n <= 0 || (size_t)n >= out_cap) {
        return -1;
    }
    return n;
}

/* ------------------------------------------------------------------ path */

int imu_capture_path(const char *base_dir, const char *device_id, imu_mode_id_t mode,
                     uint64_t unix_s, unsigned index, char *out, size_t out_cap)
{
    const imu_mode_t *m = imu_mode_by_id(mode);
    if (m == NULL || device_id == NULL || out == NULL) {
        return -1;
    }
    uint64_t days = unix_s / 86400ull;
    unsigned rem = (unsigned)(unix_s % 86400ull);
    unsigned hh = rem / 3600u, mm = (rem % 3600u) / 60u, ss = rem % 60u;
    int y = 1970;
    for (;;) {
        int leap = ((y % 4 == 0) && (y % 100 != 0)) || (y % 400 == 0);
        uint64_t dy = (uint64_t)(leap ? 366 : 365);
        if (days < dy) break;
        days -= dy;
        y++;
    }
    static const int mdays[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    int leap = ((y % 4 == 0) && (y % 100 != 0)) || (y % 400 == 0);
    int mo = 0;
    while (mo < 12) {
        uint64_t dm = (uint64_t)mdays[mo] + ((mo == 1 && leap) ? 1u : 0u);
        if (days < dm) break;
        days -= dm;
        mo++;
    }
    unsigned day = (unsigned)days + 1u;
    int n = snprintf(out, out_cap,
                     "%s/%s/%04d-%02d-%02d/%04d%02d%02dT%02d%02d%02dZ_%c_imu%03u.imucap",
                     base_dir != NULL ? base_dir : "", device_id, y, mo + 1, day, y, mo + 1,
                     day, hh, mm, ss, m->letter, index);
    if (n <= 0 || (size_t)n >= out_cap) {
        return -1;
    }
    return n;
}

int imu_segment_minutes(const imu_mode_t *mode, uint64_t rotate_bytes, double *out_minutes)
{
    if (mode == NULL) {
        return -1;
    }
    double bytes_per_s = mode->odr_hz * (double)IMU_FRAME_BYTES;
    if (bytes_per_s <= 0.0) {
        return -1;
    }
    if (out_minutes != NULL) {
        *out_minutes = (double)rotate_bytes / bytes_per_s / 60.0;
    }
    return 0;
}

/* ---------------------------------------------------------------- frames */

void imu_pack_frame(uint8_t out[IMU_FRAME_BYTES], uint32_t t_us, const int16_t accel[3],
                    const int16_t gyro[3], double temp_c, uint16_t seq, uint16_t flags)
{
    uint32_t t = t_us;
    out[0] = (uint8_t)(t & 0xFFu);
    out[1] = (uint8_t)((t >> 8) & 0xFFu);
    out[2] = (uint8_t)((t >> 16) & 0xFFu);
    out[3] = (uint8_t)((t >> 24) & 0xFFu);
    size_t k = 4;
    for (int i = 0; i < 3; i++) {
        uint16_t v = (uint16_t)accel[i];
        out[k++] = (uint8_t)(v & 0xFFu);
        out[k++] = (uint8_t)((v >> 8) & 0xFFu);
    }
    for (int i = 0; i < 3; i++) {
        uint16_t v = (uint16_t)gyro[i];
        out[k++] = (uint8_t)(v & 0xFFu);
        out[k++] = (uint8_t)((v >> 8) & 0xFFu);
    }
    uint16_t tc = (uint16_t)imu_temp_c100(temp_c);
    out[k++] = (uint8_t)(tc & 0xFFu);
    out[k++] = (uint8_t)((tc >> 8) & 0xFFu);
    out[k++] = (uint8_t)(seq & 0xFFu);
    out[k++] = (uint8_t)((seq >> 8) & 0xFFu);
    out[k++] = (uint8_t)(flags & 0xFFu);
    out[k++] = (uint8_t)((flags >> 8) & 0xFFu);
    out[k++] = 0; /* reserved */
    out[k++] = 0;
}

int16_t imu_temp_c100(double temp_c)
{
    double v = temp_c / IMU_TEMP_LSB_C;
    if (v >= 32767.0) return 32767;
    if (v <= -32768.0) return -32768;
    /* round half to even, matching numpy's rint() in the reference encoder */
    double fl = (double)(long long)v;
    double frac = v - fl;
    long long r;
    if (frac > 0.5) {
        r = (long long)fl + 1;
    } else if (frac < -0.5) {
        r = (long long)fl - 1;
    } else if (frac == 0.5 || frac == -0.5) {
        long long even = (long long)fl;
        if ((even % 2) != 0) {
            even += (frac > 0) ? 1 : -1;
        }
        r = even;
    } else {
        r = (long long)fl;
    }
    if (r > 32767) r = 32767;
    if (r < -32768) r = -32768;
    return (int16_t)r;
}

int16_t imu_temp_c100_from_icm_fifo(int raw)
{
    return imu_temp_c100((double)raw / IMU_TEMP_ICM_FIFO_DIV + IMU_TEMP_ICM_FIFO_OFF);
}

int16_t imu_temp_c100_from_mpu_fifo(int raw)
{
    return imu_temp_c100((double)raw / IMU_TEMP_MPU_FIFO_DIV + IMU_TEMP_MPU_FIFO_OFF);
}

int16_t imu_temp_c100_from_mpu_reg(int raw)
{
    return imu_temp_c100((double)raw / IMU_TEMP_MPU_REG_DIV + IMU_TEMP_MPU_REG_OFF);
}

/* ------------------------------------------------------------------ dump */

int imu_contract_dump(char *out, size_t out_cap)
{
    int n = snprintf(out, out_cap,
        "{\"spec_version\":\"%s\",\"magic\":\"%s\",\"frame_struct\":\"%s\",\"frame_bytes\":%d,"
        "\"accel_fsr_g\":%d,\"gyro_fsr_dps\":2000,\"temp_lsb_c\":0.01,"
        "\"rotate_bytes\":%llu,\"modes\":{",
        IMU_SPEC_VERSION, IMU_MAGIC_V2, IMU_FRAME_STRUCT, IMU_FRAME_BYTES, IMU_ACCEL_FSR_G,
        (unsigned long long)IMU_ROTATE_BYTES);
    if (n <= 0 || (size_t)n >= out_cap) return -1;
    size_t k = (size_t)n;
    for (int i = 0; i < IMU_MODE_COUNT; i++) {
        const imu_mode_t *m = &IMU_MODES[i];
        char odr[64], band[64];
        if (fmt_double(odr, sizeof odr, m->odr_hz) < 0 ||
            fmt_double(band, sizeof band, m->band_hz) < 0) {
            return -1;
        }
        n = snprintf(out + k, out_cap - k,
            "%s\"%c\":{\"sensor\":\"%s\",\"who_am_i\":%d,\"odr_hz\":%s,\"band_hz\":%s,"
            "\"i2c_hz\":%u,\"record_bytes\":%u,\"wm_records\":%u,\"fifo_bytes\":%u}",
            i ? "," : "", m->letter, m->sensor, (int)m->who_am_i, odr, band, m->i2c_hz,
            m->record_bytes, m->wm_records, m->fifo_bytes);
        if (n <= 0 || (size_t)n >= out_cap - k) return -1;
        k += (size_t)n;
    }
    char sa[64], sg[64];
    if (fmt_double(sa, sizeof sa, imu_scale_accel(IMU_ACCEL_FSR_G)) < 0 ||
        fmt_double(sg, sizeof sg, imu_scale_gyro(IMU_GYRO_FSR_DPS)) < 0) {
        return -1;
    }
    n = snprintf(out + k, out_cap - k, "},\"scale_accel\":%s,\"scale_gyro\":%s}", sa, sg);
    if (n <= 0 || (size_t)n >= out_cap - k) return -1;
    return (int)(k + (size_t)n);
}
