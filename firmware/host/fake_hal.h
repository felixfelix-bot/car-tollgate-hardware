/* fake_hal.h — the simulated sensor and test sinks used by the host tier.
 *
 * The fake's design intent (virtual time, modelled FIFO, injectable loss) is
 * documented in fake_hal.c. This header only declares the knobs.
 */
#ifndef IMU_FAKE_HAL_H
#define IMU_FAKE_HAL_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "imu_logger.h"
#include "imu_regs.h"

#ifdef __cplusplus
extern "C" {
#endif

#define IMU_FAKE_MAX_BUF (8u * 1024u * 1024u)

typedef struct {
    const imu_mode_t *mode;
    /* simulate the identity guard: set to something other than mode->who_am_i
     * and begin() returns -2 without producing anything */
    uint8_t who_am_i;
    int bus_absent;          /* begin() returns -1 */
    int bus_error_at_drain;  /* drain() returns -1 on this 1-based call, 0 = never */
    /* Begin() returns -2 on the Nth call only (0 = never), so a test can show
     * that a failed bring-up refused WITHOUT writing a file even when a
     * previous attempt in the same process succeeded. */
    int fail_begin_on_call;
    int begin_calls;
    int records_per_drain;   /* 0 -> use the mode's FIFO watermark */
    /* Deterministic clock jitter: each drain shifts the virtual clock by
     * +jitter_us/2 then -jitter_us/2 alternately. Makes the measured jitter a
     * KNOWN number, so imu_logger_jitter_us() is asserted against it rather than
     * merely printed. 0 = a perfectly regular fake clock. */
    uint32_t jitter_us;
    int jitter_phase;
    /* inject FIFO overflow: every N drains, `lost_per_event` records vanish */
    unsigned lost_every_n_drains;
    unsigned lost_per_event;
    /* virtual clock */
    uint64_t virtual_us;
    uint64_t next_record_us;
    uint64_t slept_us;
    /* observability */
    uint64_t produced;
    unsigned lost_total;
    unsigned drain_calls;
    int brought_up;
    int led;                 /* last LED pattern the loop asked for */
    uint32_t rng;
} imu_fake_t;

void imu_fake_init(imu_fake_t *f, const imu_mode_t *mode);
imu_hal_t imu_fake_hal(imu_fake_t *f);

/* Reset only the per-run state (clock, counters, LED), keeping the knobs. A
 * test that runs several captures in one process calls this between them. */
void imu_fake_reset_run(imu_fake_t *f);

/* A byte sink that keeps the whole capture in memory, for decoding with the
 * Python codec in the same process. */
typedef struct {
    uint8_t *buf;
    size_t cap;
    size_t len;
    size_t bytes_offered;
    unsigned writes;
    unsigned short_writes;
    unsigned flushes;
    unsigned closes;
    /* return a short write on every Nth call (0 = never) */
    unsigned short_write_every;
    /* Give up on the Nth write call with -1 (0 = never), so a test can show the
     * loop reports a failing sink instead of hanging. */
    unsigned fail_write_at;
    unsigned fail_writes;
    char path[512];
} imu_memsink_t;

imu_sink_t imu_memsink(imu_memsink_t *s);

/* A byte sink that writes real files, for the rotation test and for the
 * self-test's actual capture on disk. */
typedef struct {
    FILE *fh;
    char path[512];
} imu_filesink_t;

imu_sink_t imu_filesink(imu_filesink_t *s);

#ifdef __cplusplus
}
#endif

#endif /* IMU_FAKE_HAL_H */
