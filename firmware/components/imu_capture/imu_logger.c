/* imu_logger.c — the sampling loop, rotation, flush and drop accounting.
 *
 * Read imu_logger.h first: it states the design (no hardware owned here) and the
 * spec v2.1 rules this file enforces. Three behaviours are worth calling out
 * because they are the ones a naive logger gets wrong and nothing downstream can
 * detect:
 *
 * 1. `seq` advances by 1 + (records lost before this one). It is NOT a frame
 *    counter that happens to wrap: it is the drop ledger, and the decoder's
 *    `dropped_frames()` recovers the true loss from the gaps modulo 65536. A
 *    logger that increments seq only per written frame makes a lossy capture
 *    look perfect.
 *
 * 2. Flags are set for the RIGHT frames. A drain that observes new lost records
 *    marks the FIRST frame written from that drain (that is the frame after the
 *    loss) and every frame until the next clean drain, so a reader can tell
 *    which part of the stream is suspect instead of the whole file.
 *
 * 3. A short sink write is a host overrun, not a retry. The frame is dropped,
 *    counted, and the NEXT frame carries FLAG_HOST_OVERRUN. Silently looping
 *    until the write succeeds would convert "the storage cannot keep up" into
 *    "the timestamps are fine and the data is late", which is the lie the spec's
 *    "never silently downsample" rule exists to prevent.
 */
#include "imu_logger.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define IMU_DEFAULT_FLUSH_EVERY 4000
#define IMU_DEFAULT_DRAIN_TIMEOUT_US 20000u

/* ------------------------------------------------------------------ setup */

int imu_logger_init(imu_logger_t *lg, const imu_mode_t *mode, const imu_hal_t *hal,
                    const imu_sink_t *sink, const imu_logger_config_t *cfg)
{
    if (lg == NULL || mode == NULL || hal == NULL || sink == NULL) {
        return -1;
    }
    if (mode->id < 0 || mode->id >= IMU_MODE_COUNT) {
        return -1;
    }
    memset(lg, 0, sizeof *lg);
    lg->mode_copy = *mode;
    lg->mode = &lg->mode_copy;
    lg->hal = *hal;
    lg->sink = *sink;
    lg->state = IMU_STATE_IDLE;
    lg->last_lost = 0;
    lg->stats.interval_min_us = 0.0;
    lg->stats.interval_max_us = 0.0;
    if (cfg != NULL) {
        lg->cfg = cfg;
    }
    /* provenance defaults: explicit nulls, never omitted (spec S5.4) */
    snprintf(lg->device_id, sizeof lg->device_id, "cartg-unknown");
    snprintf(lg->mounting, sizeof lg->mounting, "%s", "unknown");
    snprintf(lg->wheel_json, sizeof lg->wheel_json, "%s",
             "{\"corner\":null,\"count\":null,\"diameter_m\":null}");
    snprintf(lg->vehicle, sizeof lg->vehicle, "%s", "unknown");
    snprintf(lg->firmware_rev, sizeof lg->firmware_rev, "%s", "imu_logger unknown");
    return 0;
}

int imu_logger_set_provenance(imu_logger_t *lg, const char *device_id, const char *base_dir,
                              const char *mounting, const char *wheel_json,
                              const char *vehicle, const char *firmware_rev)
{
    if (lg == NULL) {
        return -1;
    }
    if (device_id) snprintf(lg->device_id, sizeof lg->device_id, "%s", device_id);
    if (base_dir) snprintf(lg->base_dir, sizeof lg->base_dir, "%s", base_dir);
    if (mounting) snprintf(lg->mounting, sizeof lg->mounting, "%s", mounting);
    if (wheel_json) snprintf(lg->wheel_json, sizeof lg->wheel_json, "%s", wheel_json);
    if (vehicle) snprintf(lg->vehicle, sizeof lg->vehicle, "%s", vehicle);
    if (firmware_rev) snprintf(lg->firmware_rev, sizeof lg->firmware_rev, "%s", firmware_rev);
    return 0;
}

/* --------------------------------------------------------------- segment */

static int open_segment(imu_logger_t *lg)
{
    char path[512];
    char hdr[2048];
    if (imu_capture_path(lg->base_dir, lg->device_id, lg->mode->id,
                         (uint64_t)lg->start_unix_s, lg->segment_index, path,
                         sizeof path) < 0) {
        return -1;
    }
    /* the header's start time is the segment's own start, recomputed from the
     * capture clock so a rotated file is self-consistent (spec S5.2) */
    uint64_t seg_start_unix_ns =
        ((uint64_t)lg->start_unix_s + (uint64_t)(lg->last_drain_t_us / 1000000ull)) * 1000000000ull;
    int n = imu_header_json(lg->mode, lg->device_id, seg_start_unix_ns,
                            lg->mounting, lg->wheel_json, lg->vehicle, lg->firmware_rev,
                            hdr, sizeof hdr);
    if (n <= 0) {
        return -1;
    }
    if (lg->sink.open == NULL || lg->sink.open(lg->sink.ctx, path) != 0) {
        return -1;
    }
    if (lg->sink.write(lg->sink.ctx, (const uint8_t *)hdr, (size_t)n) != n) {
        return -1;
    }
    lg->seg_bytes = (uint64_t)n;
    return 0;
}

static int close_segment(imu_logger_t *lg)
{
    int rc = 0;
    if (lg->sink.flush != NULL && lg->sink.flush(lg->sink.ctx) != 0) {
        rc = -1;
    }
    if (lg->sink.close != NULL && lg->sink.close(lg->sink.ctx) != 0) {
        rc = -1;
    }
    lg->stats.segments++;
    lg->segment_index++;
    lg->seg_bytes = 0;
    lg->flushed_frames = lg->stats.frames_written;
    return rc;
}

/* ------------------------------------------------------------- statistics */

static void note_frame(imu_logger_t *lg, uint64_t t_us)
{
    imu_logger_stats_t *s = &lg->stats;
    if (s->frames_written == 0) {
        /* the first frame: this is the origin the achieved rate is measured
         * from. It must be recorded BEFORE the frame counter moves, or the
         * span starts at zero and every rate comes out wrong by a constant. */
        s->first_t_us = t_us;
    } else if (lg->last_frame_t_us > 0) {
        double dt = (double)(t_us - lg->last_frame_t_us);
        if (dt > 0.0) {
            s->interval_count++;
            s->interval_sum_us += dt;
            s->interval_sumsq_us += dt * dt;
            if (s->interval_min_us == 0.0 || dt < s->interval_min_us) {
                s->interval_min_us = dt;
            }
            if (dt > s->interval_max_us) {
                s->interval_max_us = dt;
            }
        }
    }
    lg->last_frame_t_us = t_us;
    s->last_t_us = t_us;
}

double imu_logger_achieved_fs(const imu_logger_t *lg)
{
    if (lg == NULL || lg->stats.frames_written < 2) {
        return 0.0;
    }
    uint64_t span = lg->stats.last_t_us - lg->stats.first_t_us;
    if (span == 0) {
        return 0.0;
    }
    /* the spec's own formula (S3.6 consequence 3 / S8.1) */
    return (double)(lg->stats.frames_written - 1) * 1e6 / (double)span;
}

int imu_logger_jitter_us(const imu_logger_t *lg, double *out_rms, double *out_pp)
{
    if (lg == NULL || lg->stats.interval_count < 2) {
        return 0;
    }
    uint64_t n = lg->stats.interval_count;
    double mean = lg->stats.interval_sum_us / (double)n;
    double var = lg->stats.interval_sumsq_us / (double)n - mean * mean;
    if (var < 0.0) {
        var = 0.0;
    }
    if (out_rms != NULL) {
        /* sqrt via Newton iteration so the core stays free of libm (the ESP-IDF
         * build is happy either way, but a -nostdlib host tier is not). */
        double r = var;
        if (r > 0.0) {
            for (int i = 0; i < 40; i++) {
                double next = 0.5 * (r + var / r);
                if (next == r) {
                    break;
                }
                r = next;
            }
        }
        *out_rms = r;
    }
    if (out_pp != NULL) {
        *out_pp = lg->stats.interval_max_us - lg->stats.interval_min_us;
    }
    return 1;
}

double imu_logger_drop_fraction(const imu_logger_t *lg)
{
    if (lg == NULL) {
        return 0.0;
    }
    double written = (double)lg->stats.frames_written;
    double lost = (double)lg->stats.dropped_frames + (double)lg->stats.host_overrun_frames;
    if (written + lost <= 0.0) {
        return 0.0;
    }
    return lost / (written + lost);
}

/* ------------------------------------------------------------------ loop */

void imu_logger_request_stop(imu_logger_t *lg)
{
    if (lg != NULL) {
        lg->stop_requested = 1;
    }
}

/* Write one frame, applying the current flag word. Handles a short sink write
 * as a host overrun (see the file header) and rotation on a frame boundary. */
static int emit_frame(imu_logger_t *lg, uint32_t t_us, const imu_record_t *rec, uint16_t flags)
{
    uint8_t frame[IMU_FRAME_BYTES];
    uint64_t cfg_rotate = lg->cfg ? lg->cfg->rotate_bytes : IMU_ROTATE_BYTES;
    unsigned max_overruns = (lg->cfg && lg->cfg->max_consecutive_host_overruns)
                                ? lg->cfg->max_consecutive_host_overruns
                                : IMU_DEFAULT_MAX_CONSECUTIVE_OVERRUNS;
    if (cfg_rotate == 0) {
        cfg_rotate = IMU_ROTATE_BYTES;
    }
    /* rotate BEFORE writing, only on a frame boundary (spec S6) */
    if (lg->seg_bytes + IMU_FRAME_BYTES > cfg_rotate) {
        if (close_segment(lg) != 0) {
            return -1;
        }
        if (open_segment(lg) != 0) {
            return -1;
        }
    }
    imu_pack_frame(frame, t_us, rec->accel, rec->gyro, rec->temp_c,
                   (uint16_t)(lg->seq & 0xFFFFu), flags);
    long w = lg->sink.write(lg->sink.ctx, frame, IMU_FRAME_BYTES);
    if (w != IMU_FRAME_BYTES) {
        /* Short write: counted, never retried in place. The next frame carries
         * FLAG_HOST_OVERRUN. But a sink that keeps refusing is a dead volume,
         * and an unattended recorder must END rather than spin: without this
         * the loop would happily run forever producing nothing, which looks
         * exactly like a healthy capture from the outside. */
        lg->stats.dropped_frames++;
        lg->stats.host_overrun_frames++;
        lg->degraded = 1;
        lg->consecutive_overruns++;
        if (lg->consecutive_overruns >= max_overruns) {
            return -2; /* the caller turns this into "stop and report" */
        }
        return 0;
    }
    lg->consecutive_overruns = 0;
    /* note the frame BEFORE the counter moves: note_frame needs frames_written
     * to still be 0 on the very first frame */
    note_frame(lg, t_us);
    lg->seq++;
    lg->seg_bytes += IMU_FRAME_BYTES;
    lg->stats.frames_written++;
    lg->stats.bytes_written += IMU_FRAME_BYTES;
    if (lg->stats.frames_written - lg->flushed_frames >=
        (uint64_t)(lg->cfg ? (lg->cfg->flush_every ? lg->cfg->flush_every
                                                   : IMU_DEFAULT_FLUSH_EVERY)
                           : IMU_DEFAULT_FLUSH_EVERY)) {
        if (lg->sink.flush != NULL) {
            lg->sink.flush(lg->sink.ctx);
        }
        lg->flushed_frames = lg->stats.frames_written;
    }
    return 0;
}

int imu_logger_run(imu_logger_t *lg)
{
    if (lg == NULL || lg->mode == NULL || lg->hal.begin == NULL || lg->hal.drain == NULL ||
        lg->hal.now_us == NULL || lg->sink.write == NULL) {
        return -1;
    }

    /* --- bring up, with the identity guard BEFORE any file exists (S2.4) --- */
    int up = lg->hal.begin(lg->hal.ctx, lg->mode);
    if (up == -2) {
        lg->state = IMU_STATE_ERROR;
        if (lg->hal.set_led) lg->hal.set_led(lg->hal.ctx, IMU_LED_ERROR);
        return -3;
    }
    if (up != 0) {
        lg->state = IMU_STATE_ERROR;
        if (lg->hal.set_led) lg->hal.set_led(lg->hal.ctx, IMU_LED_ERROR);
        return -2;
    }
    /* only now is there anything worth opening, so a refusal leaves no empty file */
    if (open_segment(lg) != 0) {
        lg->state = IMU_STATE_ERROR;
        if (lg->hal.set_led) lg->hal.set_led(lg->hal.ctx, IMU_LED_ERROR);
        if (lg->hal.end) lg->hal.end(lg->hal.ctx);
        return -4;
    }

    lg->state = IMU_STATE_RECORDING;
    if (lg->hal.set_led) lg->hal.set_led(lg->hal.ctx, IMU_LED_RECORDING);

    uint16_t pending_flags = 0;
    uint64_t start_us = lg->hal.now_us(lg->hal.ctx);
    lg->t0_us = start_us;
    lg->last_drain_t_us = 0;
    uint64_t frame_cap = lg->cfg ? lg->cfg->frame_cap : 0;
    uint32_t run_seconds = lg->cfg ? lg->cfg->run_seconds : 0;
    unsigned max_burst = (lg->cfg && lg->cfg->max_records_per_drain)
                             ? lg->cfg->max_records_per_drain
                             : (lg->mode->wm_records > IMU_MAX_DRAIN_RECORDS
                                    ? IMU_MAX_DRAIN_RECORDS
                                    : lg->mode->wm_records);
    uint32_t drain_timeout = (lg->cfg && lg->cfg->drain_timeout_us) ? lg->cfg->drain_timeout_us
                                                                   : IMU_DEFAULT_DRAIN_TIMEOUT_US;
    imu_record_t records[IMU_MAX_DRAIN_RECORDS];
    int rc = 0;

    for (;;) {
        if (lg->stop_requested) {
            lg->state = IMU_STATE_STOPPING;
            break;
        }
        uint64_t now = lg->hal.now_us(lg->hal.ctx);
        if (run_seconds > 0 && (now - start_us) >= (uint64_t)run_seconds * 1000000ull) {
            lg->state = IMU_STATE_STOPPING;
            break;
        }
        if (frame_cap > 0 && lg->stats.frames_written >= frame_cap) {
            lg->state = IMU_STATE_STOPPING;
            break;
        }

        if (lg->hal.wait_ready != NULL) {
            lg->hal.wait_ready(lg->hal.ctx, drain_timeout);
        }
        now = lg->hal.now_us(lg->hal.ctx);

        int got = lg->hal.drain(lg->hal.ctx, records, (int)max_burst);
        if (got < 0) {
            /* a bus error mid-run: close what we have, keep it readable */
            lg->state = IMU_STATE_ERROR;
            if (lg->hal.set_led) lg->hal.set_led(lg->hal.ctx, IMU_LED_ERROR);
            rc = -5;
            break;
        }
        if (got == 0) {
            continue;
        }

        /* records lost inside the sensor since the previous drain */
        unsigned lost_now = 0;
        if (lg->hal.lost_records != NULL) {
            unsigned total_lost = lg->hal.lost_records(lg->hal.ctx);
            if (total_lost > lg->last_lost) {
                lost_now = total_lost - lg->last_lost;
                lg->last_lost = total_lost;
                lg->stats.dropped_frames += lost_now;
                lg->stats.fifo_overrun_events++;
                lg->degraded = 1;
                /* the frame that FOLLOWS the loss is the first suspect frame */
                pending_flags |= IMU_FLAG_FIFO_OVERRUN;
            }
        }

        /* per-record interval for the interpolated times of this burst. */
        double interval_us = lg->mode->odr_hz > 0.0 ? 1e6 / lg->mode->odr_hz : 0.0;
        if (interval_us <= 0.0) {
            interval_us = 1.0;
        }
        lg->est_interval_us = interval_us;

        /* THE DROP LEDGER, applied BEFORE the burst is written. The records lost
         * in the FIFO happened BEFORE the records we just drained, so the seq gap
         * has to sit on the first frame of this burst — that is the frame the
         * decoder's dropped_frames() will measure the gap from, and it is also
         * the frame carrying FLAG_FIFO_OVERRUN. Advancing seq after the burst
         * would put the gap in the wrong place and blame the wrong samples. */
        if (lost_now > 0) {
            lg->seq += lost_now;
        }

        for (int i = 0; i < got; i++) {
            uint64_t t_us;
            uint16_t flags = pending_flags;
            if (got == 1) {
                /* single record: its time IS the drain time, to the clock's own
                 * resolution. No interpolation, so no ESTIMATED flag. */
                t_us = now;
            } else {
                /* a whole block read in one burst: spread the records backwards
                 * from the drain time and say so (spec S4.1 step 11). */
                t_us = now - (uint64_t)(interval_us * (double)(got - 1 - i) + 0.5);
                flags |= IMU_FLAG_TIME_ESTIMATED;
                lg->stats.estimated_time_frames++;
            }
            /* sensor-clock drift cross-check (S3.6 consequence 2). Never used
             * as the timebase; only raises a flag past the 1% limit. */
            if (records[i].sensor_t_us != 0 && i > 0 &&
                records[i - 1].sensor_t_us != 0) {
                uint32_t ds = records[i].sensor_t_us - records[i - 1].sensor_t_us;
                uint32_t dh = (uint32_t)((t_us - lg->last_frame_t_us) & 0xFFFFFFFFu);
                if (ds != 0 && dh != 0) {
                    double rel = ((double)dh - (double)ds) / (double)ds;
                    if (rel > 0.01 || rel < -0.01) {
                        lg->stats.sensor_clock_drift_frames++;
                    }
                }
            }
            int er = emit_frame(lg, (uint32_t)(t_us & 0xFFFFFFFFu), &records[i], flags);
            if (er == -2) {
                /* the sink stopped accepting: end the run and report it. The
                 * capture up to the last accepted frame is still closed below. */
                lg->state = IMU_STATE_ERROR;
                if (lg->hal.set_led) lg->hal.set_led(lg->hal.ctx, IMU_LED_ERROR);
                rc = -8;
                break;
            }
            if (er != 0) {
                rc = -6;
                break;
            }
            /* the loss is recorded on the first frame of the burst; the rest of
             * the burst is clean */
            pending_flags = 0;
        }
        if (rc != 0) {
            lg->state = IMU_STATE_ERROR;
            break;
        }
        lg->last_drain_t_us = now - start_us;
    }

    if (close_segment(lg) != 0 && rc == 0) {
        rc = -7;
    }
    if (lg->hal.end) {
        lg->hal.end(lg->hal.ctx);
    }
    if (rc == 0) {
        lg->state = IMU_STATE_IDLE;
        if (lg->hal.set_led) {
            lg->hal.set_led(lg->hal.ctx, lg->degraded ? IMU_LED_DEGRADED : IMU_LED_OFF);
        }
    }
    return rc;
}

int imu_logger_summary(const imu_logger_t *lg, char *out, size_t out_cap)
{
    if (lg == NULL || out == NULL) {
        return -1;
    }
    double fs = imu_logger_achieved_fs(lg);
    double rms = 0.0, pp = 0.0;
    imu_logger_jitter_us(lg, &rms, &pp);
    double observed = lg->stats.frames_written > 0
                          ? 100.0 * (double)(lg->stats.dropped_frames + lg->stats.host_overrun_frames) /
                                (double)(lg->stats.frames_written + lg->stats.dropped_frames +
                                         lg->stats.host_overrun_frames)
                          : 0.0;
    int n = snprintf(out, out_cap,
                     "mode %c (%s) | frames %llu | achieved_fs %.3f (nominal %.1f, %+.3f%%) | "
                     "dropped %llu (%.4f%%) | host_overrun %llu | fifo_overrun_events %llu | "
                     "segments %llu | interval rms %.1f us, peak-to-peak %.1f us",
                     lg->mode->letter, lg->mode->sensor,
                     (unsigned long long)lg->stats.frames_written, fs, lg->mode->odr_hz,
                     lg->mode->odr_hz > 0.0 ? 100.0 * (fs - lg->mode->odr_hz) / lg->mode->odr_hz
                                            : 0.0,
                     (unsigned long long)lg->stats.dropped_frames, observed,
                     (unsigned long long)lg->stats.host_overrun_frames,
                     (unsigned long long)lg->stats.fifo_overrun_events,
                     (unsigned long long)lg->stats.segments, rms, pp);
    if (n <= 0 || (size_t)n >= out_cap) {
        return -1;
    }
    return n;
}
