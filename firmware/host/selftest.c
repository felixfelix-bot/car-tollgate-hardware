/* selftest.c — the 60 s bench capture the card asks for, plus its assertions.
 *
 * WHAT IS REAL HERE AND WHAT IS NOT.
 *
 * Real: the firmware's entire capture path — mode selection, register sequence
 * application, the WHO_AM_I identity guard, the drain loop, per-frame
 * timestamps, burst interpolation, the seq drop ledger, flag attribution,
 * flush cadence, 64 MiB rotation, path naming, header bytes — and the resulting
 * .imucap file on disk, which the Python reference codec then decodes.
 *
 * Not real: the sensor. There is no IMU on this host (see REPORT.md), so the
 * records come from `imu_fake_*`, and the fake's clock is VIRTUAL: it advances
 * by exactly one ODR period per record instead of by wall time. A 60 s capture
 * therefore completes in about a second of CPU rather than 60 s of waiting, and
 * every number the capture reports about itself (achieved rate, jitter, drops)
 * is exactly measurable because the fake's behaviour is deterministic and known.
 *
 * What that means for the card's acceptance criteria is stated in REPORT.md
 * under "what this does not prove": the rate/jitter/drop figures demonstrate the
 * FIRMWARE's accounting and streaming are correct, not that an MPU-9250 sustains
 * 1 kHz over a real I2C bus. That requires the module.
 *
 * Exit code 0 = every assertion passed.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fake_hal.h"
#include "imu_logger.h"

static int failures = 0;
static int checks = 0;

static void check(int ok, const char *what)
{
    checks++;
    if (!ok) {
        failures++;
        printf("  FAIL  %s\n", what);
    } else {
        printf("  ok    %s\n", what);
    }
}

static void check_close(double got, double want, double tol, const char *what)
{
    int ok = (got - want) <= tol && (want - got) <= tol;
    checks++;
    if (!ok) {
        failures++;
        printf("  FAIL  %s (got %.6f, want %.6f +- %.6f)\n", what, got, want, tol);
    } else {
        printf("  ok    %s (%.6f vs %.6f)\n", what, got, want);
    }
}

/* One bench capture, written to a real file, decoded afterwards by python3
 * tools/imu_decode.py. Returns 0 on success. */
static int capture_to_file(const imu_mode_t *mode, const char *out_dir, const char *device,
                           uint32_t seconds, uint64_t frame_cap, imu_fake_t *fake_out,
                           imu_logger_stats_t *stats_out, char *path_out, size_t path_cap)
{
    imu_fake_t fake;
    imu_fake_init(&fake, mode);
    fake.records_per_drain = 0; /* the mode's watermark */
    imu_hal_t hal = imu_fake_hal(&fake);

    imu_filesink_t fs;
    memset(&fs, 0, sizeof fs);
    imu_sink_t sink = imu_filesink(&fs);

    imu_logger_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.flush_every = 4000;
    cfg.rotate_bytes = IMU_ROTATE_BYTES;
    cfg.run_seconds = seconds;
    cfg.frame_cap = frame_cap;
    cfg.drain_timeout_us = 20000;

    imu_logger_t lg;
    if (imu_logger_init(&lg, mode, &hal, &sink, &cfg) != 0) {
        return -1;
    }
    char wheel[192];
    snprintf(wheel, sizeof wheel,
             "{\"corner\":\"front-left\",\"count\":4,\"diameter_m\":0.6}");
    if (imu_logger_set_provenance(&lg, device, out_dir,
                                  "chassis, rigid; +X forward, +Y right, +Z down", wheel,
                                  "bench rig (simulated sensor)", "imu_logger 0.1.0+selftest") != 0) {
        return -1;
    }
    int rc = imu_logger_run(&lg);
    if (fake_out) {
        imu_fake_t saved = fake;
        /* keep the knobs, hand back the run's observability */
        saved.bus_absent = fake.bus_absent;
        saved.records_per_drain = fake.records_per_drain;
        *fake_out = saved;
    }
    if (stats_out) {
        *stats_out = lg.stats;
    }
    if (path_out && fs.path[0]) {
        snprintf(path_out, path_cap, "%s", fs.path);
    }
    return rc;
}

int main(int argc, char **argv)
{
    const char *out_dir = argc > 1 ? argv[1] : "/home/c03rad0r/tmp/imu-bench";
    const char *mode_letter = argc > 2 ? argv[2] : "M";
    uint32_t seconds = argc > 3 ? (uint32_t)strtoul(argv[3], NULL, 10) : 60u;
    const imu_mode_t *mode = imu_mode_by_letter(mode_letter[0]);
    if (mode == NULL) {
        printf("unknown mode %s\n", mode_letter);
        return 2;
    }
    char cmd[512];
    snprintf(cmd, sizeof cmd, "mkdir -p '%s'", out_dir);
    if (system(cmd) != 0) {
        printf("cannot create %s\n", out_dir);
        return 2;
    }

    char wheel[192];
    snprintf(wheel, sizeof wheel, "{\"corner\":\"front-left\",\"count\":4,\"diameter_m\":0.6}");

    printf("=== CTG-9 firmware self-test ===\n");
    printf("mode %c (%s) nominal %.1f Hz, band 0-%.0f Hz, %u s, out %s\n\n",
           mode->letter, mode->sensor, mode->odr_hz, mode->band_hz, seconds, out_dir);

    /* ---------------------------------------------------------------- 1. 60 s */
    printf("[1] %u s bench capture at the spec'd rate\n", seconds);
    {
        imu_fake_t fake;
        imu_logger_stats_t st;
        char path[600];
        memset(&st, 0, sizeof st);
        memset(&fake, 0, sizeof fake);
        memset(path, 0, sizeof path);
        int rc = capture_to_file(mode, out_dir, "cartg-bench", seconds, 0, &fake, &st, path,
                                 sizeof path);
        check(rc == 0, "capture ran to completion");
        check(st.frames_written > 0, "frames were written");
        check(st.segments == 1, "exactly one segment (no rotation at 60 s)");
        if (path[0]) {
            printf("  file: %s\n", path);
        }
        double fs = st.frames_written > 1
                        ? (double)(st.frames_written - 1) * 1e6 /
                              (double)(st.last_t_us - st.first_t_us)
                        : 0.0;
        check_close(fs, mode->odr_hz, mode->odr_hz * 0.01, "achieved rate within 1% of target");
        double drop_pct = 100.0 * (double)(st.dropped_frames + st.host_overrun_frames) /
                          (double)(st.frames_written + st.dropped_frames + st.host_overrun_frames);
        check(drop_pct < 0.1, "dropped samples < 0.1%");
        check(st.host_overrun_frames == 0, "no host overruns on a healthy sink");
        /* the fake drains the mode's full watermark per call, so every frame in a
         * burst gets an interpolated time and must say so (spec S4.1 step 11) */
        check(st.estimated_time_frames == st.frames_written,
              "burst drains: every frame carries FLAG_TIME_ESTIMATED");
        check(st.sensor_clock_drift_frames == 0,
              "the fake's sensor clock tracks the host clock, so no drift flags");
        printf("  frames %llu, achieved_fs %.4f Hz (%+.4f%%), dropped %llu (%.6f%%)\n",
               (unsigned long long)st.frames_written, fs,
               100.0 * (fs - mode->odr_hz) / mode->odr_hz,
               (unsigned long long)(st.dropped_frames + st.host_overrun_frames), drop_pct);
        if (path[0]) {
            snprintf(cmd, sizeof cmd,
                     "python3 tools/imu_decode.py '%s' --json > '%s/decode-60s.json' 2>'%s/decode-60s.err'",
                     path, out_dir, out_dir);
            int drc = system(cmd);
            check(drc == 0, "the Python reference decoder round-tripped the capture");
        }
    }

    /* ------------------------------------------------- 1b. single-record drains */
    printf("\n[1b] one record per drain: times are measured, not interpolated\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        fake.records_per_drain = 1;
        imu_hal_t hal = imu_fake_hal(&fake);
        imu_memsink_t ms;
        static uint8_t buf[1u << 20];
        memset(&ms, 0, sizeof ms);
        ms.buf = buf;
        ms.cap = sizeof buf;
        imu_sink_t sink = imu_memsink(&ms);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 4000;
        cfg.rotate_bytes = IMU_ROTATE_BYTES;
        cfg.frame_cap = 3000;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
        int rc = imu_logger_run(&lg);
        check(rc == 0, "the single-record run finished");
        check(lg.stats.estimated_time_frames == 0, "no frame claims an interpolated time");
        check(lg.stats.frames_written == 3000, "exactly the frame cap was written");
    }

    /* ------------------------------------------------------ 2. identity guard */
    printf("\n[2] the WHO_AM_I identity guard must ABORT and write nothing\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        /* the OTHER part's WHO_AM_I: 0x47 on an MPU-9250 mode and vice versa */
        fake.who_am_i = mode->icm ? IMU_WHO_AM_I_MPU9250 : IMU_WHO_AM_I_ICM42688P;
        imu_hal_t hal = imu_fake_hal(&fake);
        imu_memsink_t ms;
        static uint8_t buf[1 << 16];
        memset(&ms, 0, sizeof ms);
        ms.buf = buf;
        ms.cap = sizeof buf;
        imu_sink_t sink = imu_memsink(&ms);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 4000;
        cfg.rotate_bytes = IMU_ROTATE_BYTES;
        cfg.frame_cap = 1000;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
        int rc = imu_logger_run(&lg);
        check(rc == -3, "run() reported the identity mismatch (-3)");
        check(ms.len == 0 && ms.writes == 0, "no bytes and no file were written");
        check(fake.led == IMU_LED_ERROR, "the LED was set to the error pattern");
        check(lg.stats.frames_written == 0, "no frames were recorded");
    }

    /* ------------------------------------------- 3. injected drops are counted */
    printf("\n[3] injected FIFO loss must be counted and flagged exactly\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        fake.lost_every_n_drains = 8;
        fake.lost_per_event = 25;
        imu_hal_t hal = imu_fake_hal(&fake);
        imu_memsink_t ms;
        static uint8_t buf[16u << 20];
        memset(&ms, 0, sizeof ms);
        ms.buf = buf;
        ms.cap = sizeof buf;
        imu_sink_t sink = imu_memsink(&ms);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 4000;
        cfg.rotate_bytes = IMU_ROTATE_BYTES;
        cfg.frame_cap = 20000;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
        int rc = imu_logger_run(&lg);
        check(rc == 0, "capture finished despite the loss");
        unsigned expected = (unsigned)lg.stats.fifo_overrun_events * 25u;
        check(lg.stats.dropped_frames == expected,
              "dropped_frames equals the injected loss exactly");
        check(lg.stats.dropped_frames == fake.lost_total,
              "the logger's ledger agrees with the sensor's own counter");
        check(lg.stats.fifo_overrun_events > 0, "overrun events were counted");
        check(lg.degraded == 1, "the run is marked degraded");
        check(fake.led == IMU_LED_DEGRADED, "the LED shows the degraded pattern");
        printf("  frames %llu, injected+counted drops %llu, events %llu\n",
               (unsigned long long)lg.stats.frames_written,
               (unsigned long long)lg.stats.dropped_frames,
               (unsigned long long)lg.stats.fifo_overrun_events);
        /* the same loss must be recoverable from the FILE by the seq-gap rule,
         * which is what the decoder's dropped_frames() does. Write it out and let
         * the python side assert it (see evidence/). */
        {
            char dpath[600];
            snprintf(dpath, sizeof dpath, "%s/drops-M.imucap", out_dir);
            imu_filesink_t fs2;
            memset(&fs2, 0, sizeof fs2);
            imu_fake_t f2;
            imu_fake_init(&f2, mode);
            f2.lost_every_n_drains = 8;
            f2.lost_per_event = 25;
            imu_hal_t h2 = imu_fake_hal(&f2);
            imu_sink_t s2 = imu_filesink(&fs2);
            imu_logger_config_t c2;
            memset(&c2, 0, sizeof c2);
            c2.flush_every = 4000;
            c2.rotate_bytes = IMU_ROTATE_BYTES;
            c2.frame_cap = 20000;
            imu_logger_t l2;
            imu_logger_init(&l2, mode, &h2, &s2, &c2);
            imu_logger_set_provenance(&l2, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
            imu_logger_run(&l2);
            printf("  wrote %s (logger counted %llu dropped)\n", dpath,
                   (unsigned long long)l2.stats.dropped_frames);
            check(l2.stats.dropped_frames == f2.lost_total,
                  "the written file's run agrees with the sensor's counter");
        }
    }

    /* ------------------------------------------------ 4. short write = overrun */
    printf("\n[4] a short sink write is a counted host overrun, never a retry\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        imu_hal_t hal = imu_fake_hal(&fake);
        imu_memsink_t ms;
        static uint8_t buf[2u << 20];
        memset(&ms, 0, sizeof ms);
        ms.buf = buf;
        ms.cap = sizeof buf;
        ms.short_write_every = 500;
        imu_sink_t sink = imu_memsink(&ms);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 4000;
        cfg.rotate_bytes = IMU_ROTATE_BYTES;
        cfg.frame_cap = 5000;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
        int rc = imu_logger_run(&lg);
        check(rc == 0, "the run survived the sink back-pressure");
        check(ms.short_writes > 0, "the sink really did short-write");
        check(lg.stats.host_overrun_frames == ms.short_writes,
              "every short write became exactly one counted host overrun");
    }

    /* --------------------------------------------------------- 5. bus failure */
    printf("\n[5] a mid-run bus error closes the file and reports failure\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        fake.bus_error_at_drain = 5;
        imu_hal_t hal = imu_fake_hal(&fake);
        imu_memsink_t ms;
        static uint8_t buf[1u << 20];
        memset(&ms, 0, sizeof ms);
        ms.buf = buf;
        ms.cap = sizeof buf;
        imu_sink_t sink = imu_memsink(&ms);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 100;
        cfg.rotate_bytes = IMU_ROTATE_BYTES;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
        int rc = imu_logger_run(&lg);
        check(rc == -5, "run() reported the bus error (-5)");
        check(ms.closes == 1, "the segment was closed, so the file stays readable");
        check(lg.stats.frames_written > 0, "the frames before the error survived");
        check(fake.led == IMU_LED_ERROR, "the LED shows the error pattern");
    }

    /* ------------------------------------------------- 6. rotation on a bound */
    printf("\n[6] rotation happens on a frame boundary and never mid-frame\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        imu_hal_t hal = imu_fake_hal(&fake);
        /* a deliberately tiny segment so several rotations happen in one run */
        imu_filesink_t fs;
        memset(&fs, 0, sizeof fs);
        imu_sink_t sink = imu_filesink(&fs);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 4000;
        cfg.rotate_bytes = 4u * 1024u; /* ~170 frames per segment at 24 B */
        cfg.frame_cap = 1000;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-rot", out_dir, "x", wheel, "x", "selftest");
        int rc = imu_logger_run(&lg);
        check(rc == 0, "the rotating capture finished");
        check(lg.stats.segments >= 5, "several segments were produced");
        check(lg.stats.segments == lg.segment_index, "segment index matches segments closed");
        printf("  %llu segments for %llu frames\n",
               (unsigned long long)lg.stats.segments,
               (unsigned long long)lg.stats.frames_written);
    }

    /* --------------------------------------------------- 7. control + LED state */
    printf("\n[7] start/stop control and the LED state machine\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        imu_hal_t hal = imu_fake_hal(&fake);
        imu_memsink_t ms;
        static uint8_t buf[1u << 20];
        memset(&ms, 0, sizeof ms);
        ms.buf = buf;
        ms.cap = sizeof buf;
        imu_sink_t sink = imu_memsink(&ms);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 100;
        cfg.rotate_bytes = IMU_ROTATE_BYTES;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
        imu_logger_request_stop(&lg); /* a stop before the loop starts */
        int rc = imu_logger_run(&lg);
        check(rc == 0, "a pre-requested stop still closes cleanly");
        check(lg.stats.frames_written == 0, "nothing was recorded after the stop request");
        check(lg.state == IMU_STATE_IDLE, "the state machine returned to IDLE");
    }

    /* ---------------------------------------------------------------- 8. jitter */
    printf("\n[8] measured jitter matches the injected jitter\n");
    {
        imu_fake_t fake;
        imu_fake_init(&fake, mode);
        uint32_t jit = 200; /* us, peak-to-peak */
        fake.jitter_us = jit;
        imu_hal_t hal = imu_fake_hal(&fake);
        imu_memsink_t ms;
        static uint8_t buf[1u << 20];
        memset(&ms, 0, sizeof ms);
        ms.buf = buf;
        ms.cap = sizeof buf;
        imu_sink_t sink = imu_memsink(&ms);
        imu_logger_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.flush_every = 4000;
        cfg.rotate_bytes = IMU_ROTATE_BYTES;
        cfg.frame_cap = 5000;
        imu_logger_t lg;
        imu_logger_init(&lg, mode, &hal, &sink, &cfg);
        imu_logger_set_provenance(&lg, "cartg-bench", out_dir, "x", wheel, "x", "selftest");
        int rc = imu_logger_run(&lg);
        check(rc == 0, "the jittered capture finished");
        double rms = 0, pp = 0;
        imu_logger_jitter_us(&lg, &rms, &pp);
        double interval = 1e6 / mode->odr_hz;
        check_close(pp, (double)jit - interval, 2.0, "interval peak-to-peak");
        check_close(rms, ((double)jit - interval) / 2.0, 2.0, "interval RMS");
    }

    /* ------------------------------------------------- 9. every mode's sequence */
    printf("\n[9] every mode has a register sequence that packs its documented values\n");
    for (int i = 0; i < IMU_MODE_COUNT; i++) {
        const imu_mode_t *m = imu_mode_by_id((imu_mode_id_t)i);
        size_t n = 0;
        const imu_reg_op_t *seq = imu_seq_for_mode(i, &n);
        char what[128];
        snprintf(what, sizeof what, "mode %c has a non-empty sequence (%zu ops)", m->letter, n);
        check(seq != NULL && n >= 10, what);
    }

    printf("\n=== %d checks, %d failed ===\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
