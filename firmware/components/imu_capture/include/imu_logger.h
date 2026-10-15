/* imu_logger.h — the sampling loop, its HAL seam, and the control state machine.
 *
 * DESIGN: the loop owns no hardware. Everything it needs arrives through
 * `imu_hal_t` (a sensor, a sink, a clock, an LED) so the same bytes that run on
 * an ESP32-S3 are exercised on the host by a simulated sensor — which is the
 * only way to test drop accounting, rotation, and the flush window at all.
 *
 * The rules this file exists to enforce, all from spec v2.1:
 *
 *  S3.6  every frame carries a host-monotonic microsecond timestamp; a frame's
 *        time is never inferred from a nominal rate on the reading side
 *  S4.1  a whole FIFO block drained in one burst gets interpolated per-frame
 *        times and FLAG_TIME_ESTIMATED
 *  S4.1  > "Do not silently downsample": an un-sustainable rate raises
 *        FLAG_FIFO_OVERRUN / FLAG_HOST_OVERRUN and is counted, never hidden
 *  S5.3  `seq` is a per-recording counter that WALPS AT 65536 and advances over
 *        lost records too, which is what makes it the true drop count
 *  S6    64 MiB segments, rotated only on a frame boundary; no file outlives the
 *        uint32 microsecond wrap (71.6 min)
 *  S6    flush every `flush_every` frames so a power cut costs at most that much
 *  S2.4  a WHO_AM_I mismatch ABORTS before any frame is written
 */
#ifndef IMU_LOGGER_H
#define IMU_LOGGER_H

#include <stddef.h>
#include <stdint.h>

#include "imu_contract.h"

#ifdef __cplusplus
extern "C" {
#endif

/* --------------------------------------------------------------- the sink */

/* A byte sink. `write` must return the number of bytes accepted (a short write
 * is how back-pressure reaches the loop). */
typedef struct imu_sink {
    void *ctx;
    int (*open)(void *ctx, const char *path);
    long (*write)(void *ctx, const uint8_t *data, size_t len);
    int (*flush)(void *ctx);
    int (*close)(void *ctx);
} imu_sink_t;

/* --------------------------------------------------------------- the sensor */

/* One decoded FIFO record, already in raw LSB. */
typedef struct {
    int16_t accel[3];
    int16_t gyro[3];
    double temp_c;
    /* the sensor's own timestamp for this record, in microseconds, or 0 when the
     * part/mode does not provide one. Used only as the >1% drift cross-check of
     * spec S3.6 consequence 2 — never as the timebase. */
    uint32_t sensor_t_us;
} imu_record_t;

typedef struct imu_hal {
    void *ctx;
    /* Bring the part up per spec S4 and run the identity guard. Returns 0 on
     * success, -1 if the part is absent/unconfigured, -2 on a WHO_AM_I mismatch.
     * On -2 NOTHING must have been written to the sink. */
    int (*begin)(void *ctx, const imu_mode_t *mode);
    /* Drain up to `max` records. Returns the count, or -1 on a bus error.
     * A return of 0 means "nothing ready yet", which is normal. */
    int (*drain)(void *ctx, imu_record_t *out, int max);
    /* Records lost inside the sensor since the previous call (FIFO_LOST_PKT on
     * the ICM-42688-P, FIFO_OFLOW on the MPU-9250). Monotonic. */
    unsigned (*lost_records)(void *ctx);
    void (*end)(void *ctx);
    /* Monotonic microseconds. Must never go backwards and must NOT wrap inside a
     * segment (the uint32 frame field wraps instead, by design, at 71.6 min). */
    uint64_t (*now_us)(void *ctx);
    /* Block until at least one record is ready or `timeout_us` elapses.
     * Returns 0 when data is ready, -1 on timeout. */
    int (*wait_ready)(void *ctx, uint32_t timeout_us);
    /* Status indication. `pattern` is an IMU_LED_* value; a HAL with no LED may
     * leave this NULL. */
    void (*set_led)(void *ctx, int pattern);
} imu_hal_t;

/* Status patterns. The values are stable so a wiring note can name them. */
#define IMU_LED_OFF 0       /* idle: not recording                     */
#define IMU_LED_IDLE_BLINK 1 /* ready, waiting for a start request      */
#define IMU_LED_RECORDING 2 /* solid on while recording                */
#define IMU_LED_ERROR 3     /* solid fast blink: identity/bus failure  */
#define IMU_LED_DEGRADED 4  /* double-blink: drops detected this run    */

/* Largest FIFO drain burst the loop will service in one go. The ICM-42688-P's
 * 2048-byte FIFO holds at most 128 16-byte records; 256 covers it plus slack. */
#define IMU_MAX_DRAIN_RECORDS 256

typedef enum {
    IMU_STATE_IDLE = 0,
    IMU_STATE_RECORDING,
    IMU_STATE_STOPPING,
    IMU_STATE_ERROR
} imu_state_t;

/* ---------------------------------------------------------------- the loop */

typedef struct {
    unsigned flush_every;      /* frames between flushes (spec S6) */
    uint64_t rotate_bytes;     /* segment size, IMU_ROTATE_BYTES */
    unsigned max_records_per_drain; /* FIFO drain burst cap; 0 -> mode watermark */
    uint32_t drain_timeout_us; /* wait_ready timeout before a serviced poll */
    /* STOP AFTER THIS MANY SECONDS. 0 = unbounded (the real on-car behaviour,
     * where the operator stops it). The self-test sets it so a bench run ends
     * by itself. */
    uint32_t run_seconds;
    /* Stop as soon as this many frames have been written; 0 = no cap. Used by
     * the self-test so a rate assertion is made over an exact frame count. */
    uint64_t frame_cap;
    /* Give up after this many CONSECUTIVE frames the sink refused. A storage
     * volume that is gone (card pulled, filesystem read-only) must end the run
     * and say so, not spin forever writing nothing: an unattended recorder in a
     * moving car has no operator to notice. 0 selects the default below. */
    unsigned max_consecutive_host_overruns;
} imu_logger_config_t;

/* Default for max_consecutive_host_overruns: one second of samples at the
 * highest mode's rate (4000 Hz), i.e. a brief hiccup is tolerated and a dead
 * volume is not. */
#define IMU_DEFAULT_MAX_CONSECUTIVE_OVERRUNS 4000

typedef struct {
    uint64_t frames_written;
    uint64_t bytes_written;
    uint64_t dropped_frames;        /* records lost in the sensor (seq gaps)   */
    uint64_t fifo_overrun_events;   /* drains that observed new lost records   */
    uint64_t host_overrun_frames;   /* frames the sink refused                 */
    uint64_t segments;              /* files closed                             */
    uint64_t estimated_time_frames; /* frames carrying FLAG_TIME_ESTIMATED     */
    uint64_t sensor_clock_drift_frames; /* frames flagged for >1% clock drift   */
    uint64_t first_t_us;
    uint64_t last_t_us;
    /* inter-frame interval statistics, for the self-test's jitter figure */
    uint64_t interval_count;
    double interval_sum_us;
    double interval_sumsq_us;
    double interval_min_us;
    double interval_max_us;
} imu_logger_stats_t;

typedef struct {
    imu_mode_t mode_copy;      /* the mode table entry, copied not borrowed */
    const imu_mode_t *mode;
    imu_hal_t hal;
    imu_sink_t sink;
    const imu_logger_config_t *cfg;

    /* capture identity, written into every header */
    char device_id[64];
    char base_dir[192];
    char mounting[256];
    char wheel_json[192];
    char vehicle[128];
    char firmware_rev[64];

    imu_state_t state;
    unsigned segment_index;
    uint32_t start_unix_s;
    uint64_t seq;              /* advances over lost records too (see S5.3) */
    uint64_t t0_us;
    uint64_t flushed_frames;
    uint64_t seg_bytes;
    uint64_t last_drain_t_us;
    double est_interval_us;    /* per-record interval used for interpolation */
    unsigned last_lost;
    uint64_t last_frame_t_us;
    unsigned consecutive_overruns;
    volatile int stop_requested;
    int degraded;
    imu_logger_stats_t stats;
} imu_logger_t;

/* Initialise. `mode` must outlive the logger or be one of the static table
 * entries. Returns 0, or -1 on a NULL/unknown argument. */
int imu_logger_init(imu_logger_t *lg, const imu_mode_t *mode, const imu_hal_t *hal,
                    const imu_sink_t *sink, const imu_logger_config_t *cfg);

/* Set the provenance stamped into every file header. Any argument may be NULL,
 * which is written as an explicit JSON null (spec S5.4). Returns 0/-1. */
int imu_logger_set_provenance(imu_logger_t *lg, const char *device_id, const char *base_dir,
                              const char *mounting, const char *wheel_json,
                              const char *vehicle, const char *firmware_rev);

/* Start a capture: bring up the sensor, run the identity guard, open the first
 * segment and record until a stop request, `run_seconds`, or `frame_cap`.
 *
 * Returns:
 *    0  a capture ran and was closed cleanly
 *   -1  bad configuration
 *   -2  the sensor refused to come up (absent / bus error)
 *   -3  IDENTITY MISMATCH: aborted, and NO file was created
 *   -4  the sink could not be opened
 */
int imu_logger_run(imu_logger_t *lg);

/* Ask a running capture to stop at the next frame boundary. Safe to call from
 * an ISR-free context only; the loop checks it once per drain. */
void imu_logger_request_stop(imu_logger_t *lg);

/* Achieved sample rate from the frames' own timestamps (0.0 if undecidable). */
double imu_logger_achieved_fs(const imu_logger_t *lg);

/* RMS and peak-to-peak of the inter-frame intervals, microseconds. Either output
 * may be NULL. Returns 0 when there are not yet two frames. */
int imu_logger_jitter_us(const imu_logger_t *lg, double *out_rms, double *out_peak_to_peak);

/* Frames carrying any overrun flag, and that count as a fraction of the
 * significant frames (written + lost). The spec's 0.1% budget (S8.3) is judged
 * on this. Returns 0.0 when nothing was captured. */
double imu_logger_drop_fraction(const imu_logger_t *lg);

/* Human-readable one-line result, for the self-test and the serial console. */
int imu_logger_summary(const imu_logger_t *lg, char *out, size_t out_cap);

#ifdef __cplusplus
}
#endif

#endif /* IMU_LOGGER_H */
