/* fake_hal.c — a simulated IMU for the host tier.
 *
 * This is not a mock that returns canned bytes: it models the part's FIFO, its
 * ODR, its settle delays and its loss counter, so the firmware's drop
 * accounting, burst interpolation and rotation logic are exercised against
 * something that can actually be late or lossy. Bindings:
 *
 *   - `imu_fake_config_t.records_per_odr_tick` makes the "sensor" produce one
 *     record every 1/ODR seconds of VIRTUAL time;
 *   - `lost_every_n_drains` injects FIFO overflow at a known rate, so the seq
 *     ledger can be checked exactly (drops are computable, not just plausible);
 *   - `short_write_every` makes the SINK refuse a write, so the host-overrun path
 *     is exercised too.
 *
 * VIRTUAL TIME. The fake clock is deliberately NOT the wall clock: at 4000 Hz a
 * 60 s capture is 240 000 frames and would take 60 real seconds through the
 * filesystem, which is not a budget this bench has. `virtual_time` advances by
 * exactly one ODR period per produced record, so the capture's own timestamps,
 * achieved rate, jitter and drop fraction are all measurable exactly and
 * instantly. The mapping to wall time is reported, and the report says plainly
 * that the *file* was produced at simulated rate — see REPORT.md "what this
 * does not prove".
 */
#include "fake_hal.h"

#include <stdio.h>
#include <string.h>

void imu_fake_init(imu_fake_t *f, const imu_mode_t *mode)
{
    memset(f, 0, sizeof *f);
    f->mode = mode;
    f->who_am_i = mode != NULL ? mode->who_am_i : 0;
    f->rng = 0x12345678u;
}

void imu_fake_reset_run(imu_fake_t *f)
{
    f->virtual_us = 0;
    f->slept_us = 0;
    f->next_record_us = 0;
    f->produced = 0;
    f->lost_total = 0;
    f->drain_calls = 0;
    f->brought_up = 0;
    f->led = IMU_LED_OFF;
    f->jitter_phase = 0;
}

static void fake_settle(imu_fake_t *f, uint32_t us)
{
    f->virtual_us += us;
    f->slept_us += us;
}

/* Deterministic pseudo-noise: a 32-bit LCG, so a capture is byte-reproducible. */
static uint32_t fake_rand(uint32_t *state)
{
    *state = (*state * 1664525u) + 1013904223u;
    return *state >> 8;
}

int imu_fake_begin(void *ctx, const imu_mode_t *mode)
{
    imu_fake_t *f = (imu_fake_t *)ctx;
    if (f == NULL || mode == NULL) {
        return -1;
    }
    f->begin_calls++;
    if (f->fail_begin_on_call > 0 && f->begin_calls == f->fail_begin_on_call) {
        f->brought_up = 0;
        return -1; /* a bring-up that failed: no file may be created */
    }
    f->mode = mode;
    f->virtual_us = 0;
    f->slept_us = 0;
    f->next_record_us = 0;
    f->produced = 0;
    f->lost_total = 0;
    f->drain_calls = 0;
    f->brought_up = 1;

    /* model the datasheet settle delays the sequence in imu_regs.c asks for, so
     * a 60 s capture is not silently shortened by ignoring them */
    if (mode->icm) {
        fake_settle(f, IMU_RESET_DELAY_ICM_US + IMU_LEAVE_OFF_DELAY_ICM_US +
                           IMU_GYRO_SETTLE_ICM_US);
    } else {
        fake_settle(f, IMU_RESET_DELAY_MPU_US + IMU_GYRO_SETTLE_MPU_US);
    }

    if (f->who_am_i != mode->who_am_i) {
        return -2; /* the identity guard's case: refuse before any file exists */
    }
    if (f->bus_absent) {
        return -1;
    }
    f->next_record_us = f->virtual_us;
    return 0;
}

int imu_fake_drain(void *ctx, imu_record_t *out, int max)
{
    imu_fake_t *f = (imu_fake_t *)ctx;
    if (f == NULL || out == NULL || max <= 0 || !f->brought_up) {
        return -1;
    }
    f->drain_calls++;
    if (f->bus_error_at_drain > 0 && (int)f->drain_calls == f->bus_error_at_drain) {
        return -1;
    }
    /* inject FIFO overflow before this burst */
    if (f->lost_every_n_drains > 0 && (f->drain_calls % f->lost_every_n_drains) == 0) {
        f->lost_total += f->lost_per_event;
    }
    /* this drain only ever sees what the FIFO holds: `n` records, at most the
     * watermark the mode configured, and at most `max` */
    int n = f->records_per_drain;
    if (n <= 0) {
        n = (int)f->mode->wm_records;
    }
    if (n > (int)f->mode->fifo_bytes / (int)f->mode->record_bytes) {
        n = (int)f->mode->fifo_bytes / (int)f->mode->record_bytes;
    }
    if (n > max) {
        n = max;
    }
    double period_us = 1e6 / f->mode->odr_hz;
    for (int i = 0; i < n; i++) {
        imu_record_t *r = &out[i];
        uint32_t g = fake_rand(&f->rng);
        int16_t n1 = (int16_t)((g % 65u) - 32);
        /* +Z down under 1 g, per the spec's mounting frame (S7.2) */
        r->accel[0] = n1;
        r->accel[1] = (int16_t)((int16_t)((g >> 8) % 65u) - 32);
        r->accel[2] = (int16_t)(2048 + n1);
        r->gyro[0] = (int16_t)(((g >> 16) % 33u) - 16);
        r->gyro[1] = (int16_t)(((g >> 4) % 33u) - 16);
        r->gyro[2] = (int16_t)(((g >> 12) % 33u) - 16);
        r->temp_c = 25.0 + (double)(g % 100u) / 100.0;
        r->sensor_t_us = (uint32_t)(f->next_record_us + (uint64_t)(period_us * i));
        f->produced++;
    }
    /* the fake clock advances by exactly the time the records represent */
    f->next_record_us += (uint64_t)(period_us * n);
    f->virtual_us = f->next_record_us;
    return n;
}

unsigned imu_fake_lost_records(void *ctx)
{
    imu_fake_t *f = (imu_fake_t *)ctx;
    return f ? f->lost_total : 0;
}

void imu_fake_end(void *ctx)
{
    imu_fake_t *f = (imu_fake_t *)ctx;
    if (f) {
        f->brought_up = 0;
    }
}

uint64_t imu_fake_now_us(void *ctx)
{
    imu_fake_t *f = (imu_fake_t *)ctx;
    return f ? f->virtual_us : 0;
}

int imu_fake_wait_ready(void *ctx, uint32_t timeout_us)
{
    imu_fake_t *f = (imu_fake_t *)ctx;
    if (f == NULL) {
        return -1;
    }
    /* a real part would block; the fake jumps the clock to the next record */
    if (f->virtual_us < f->next_record_us) {
        f->virtual_us = f->next_record_us;
    }
    /* Deterministic jitter. Every SECOND drain completes `jitter_us` late; the
     * odd ones complete on time. The loop reads the clock immediately after
     * this call and a single-record drain stamps the frame with that instant, so
     * with a period P the intervals come out as:
     *
     *     P - J, P + J, P - J, P + J, ...
     *
     * i.e. mean exactly P, peak-to-peak exactly 2J and RMS exactly J. Those are
     * three closed-form numbers a test can assert, which is the whole point of
     * injecting jitter instead of printing whatever the host happens to do.
     *
     * The offset is only ever POSITIVE. A symmetric +-J/2 would make every other
     * timestamp EARLIER than the previous one, which is not a case the firmware
     * should be asked to survive: a monotonic clock is a precondition of the
     * spec (S3.6), so a harness that violated it would be testing its own bug.
     * The price is that the sawtooth's mean sits exactly on P while its
     * endpoints do not — which is also why the JITTERED capture's rate is
     * asserted against the spec's 1% tolerance and not against exactness. */
    if (f->jitter_us > 0 && (f->drain_calls % 2) == 0) {
        f->virtual_us += f->jitter_us;
    }
    (void)timeout_us;
    return 0;
}

void imu_fake_set_led(void *ctx, int pattern)
{
    imu_fake_t *f = (imu_fake_t *)ctx;
    if (f) {
        f->led = pattern;
    }
}

imu_hal_t imu_fake_hal(imu_fake_t *f)
{
    imu_hal_t h;
    memset(&h, 0, sizeof h);
    h.ctx = f;
    h.begin = imu_fake_begin;
    h.drain = imu_fake_drain;
    h.lost_records = imu_fake_lost_records;
    h.end = imu_fake_end;
    h.now_us = imu_fake_now_us;
    h.wait_ready = imu_fake_wait_ready;
    h.set_led = imu_fake_set_led;
    return h;
}

/* ------------------------------------------------------------------ sinks */

int imu_memsink_open(void *ctx, const char *path)
{
    imu_memsink_t *s = (imu_memsink_t *)ctx;
    if (s == NULL || s->buf == NULL) {
        return -1;
    }
    s->len = 0;
    snprintf(s->path, sizeof s->path, "%s", path);
    s->writes = 0;
    return 0;
}

long imu_memsink_write(void *ctx, const uint8_t *data, size_t len)
{
    imu_memsink_t *s = (imu_memsink_t *)ctx;
    if (s == NULL) {
        return -1;
    }
    s->writes++;
    s->bytes_offered += len;
    if (s->fail_write_at > 0 && s->writes >= s->fail_write_at) {
        s->fail_writes++;
        return -1;
    }
    if (s->short_write_every > 0 && (s->writes % s->short_write_every) == 0) {
        s->short_writes++;
        return (long)(len - 1); /* a short write, exactly as a full FS would give */
    }
    if (s->len + len > s->cap) {
        return -1;
    }
    memcpy(s->buf + s->len, data, len);
    s->len += len;
    return (long)len;
}

int imu_memsink_flush(void *ctx)
{
    imu_memsink_t *s = (imu_memsink_t *)ctx;
    if (s) {
        s->flushes++;
    }
    return 0;
}

int imu_memsink_close(void *ctx)
{
    imu_memsink_t *s = (imu_memsink_t *)ctx;
    if (s) {
        s->closes++;
    }
    return 0;
}

imu_sink_t imu_memsink(imu_memsink_t *s)
{
    imu_sink_t k;
    memset(&k, 0, sizeof k);
    k.ctx = s;
    k.open = imu_memsink_open;
    k.write = imu_memsink_write;
    k.flush = imu_memsink_flush;
    k.close = imu_memsink_close;
    return k;
}

int imu_filesink_open(void *ctx, const char *path)
{
    imu_filesink_t *s = (imu_filesink_t *)ctx;
    if (s == NULL) {
        return -1;
    }
    s->fh = fopen(path, "wb");
    if (s->fh == NULL) {
        return -1;
    }
    snprintf(s->path, sizeof s->path, "%s", path);
    return 0;
}

long imu_filesink_write(void *ctx, const uint8_t *data, size_t len)
{
    imu_filesink_t *s = (imu_filesink_t *)ctx;
    size_t w;
    if (s == NULL || s->fh == NULL) {
        return -1;
    }
    w = fwrite(data, 1, len, s->fh);
    return (long)w;
}

int imu_filesink_flush(void *ctx)
{
    imu_filesink_t *s = (imu_filesink_t *)ctx;
    if (s == NULL || s->fh == NULL) {
        return -1;
    }
    return fflush(s->fh);
}

int imu_filesink_close(void *ctx)
{
    imu_filesink_t *s = (imu_filesink_t *)ctx;
    int rc = 0;
    if (s == NULL || s->fh == NULL) {
        return -1;
    }
    rc = fclose(s->fh);
    s->fh = NULL;
    return rc;
}

imu_sink_t imu_filesink(imu_filesink_t *s)
{
    imu_sink_t k;
    memset(&k, 0, sizeof k);
    k.ctx = s;
    k.open = imu_filesink_open;
    k.write = imu_filesink_write;
    k.flush = imu_filesink_flush;
    k.close = imu_filesink_close;
    return k;
}
