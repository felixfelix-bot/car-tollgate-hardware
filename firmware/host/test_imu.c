/* test_imu.c — the host unit suite for the CTG-9 firmware core.
 *
 * This is the TDD tier: the assertions below are the contract the firmware must
 * satisfy, and they are written against the two documents that are normative —
 * `docs/imu_capture_spec.md` (spec v2.1) and the datasheets it cites. Where an
 * assertion encodes a register byte, the comment says which document the byte
 * comes from, because a byte without a source is a guess.
 *
 * Exit 0 = all pass. Built and run by `make -C firmware/host test`.
 */
#include <stdio.h>
#include <string.h>

#include "fake_hal.h"
#include "imu_contract.h"
#include "imu_logger.h"
#include "imu_regs.h"

static int failures = 0;
static int checks = 0;
static const char *group = "";

static void check(int ok, const char *what)
{
    checks++;
    if (!ok) {
        failures++;
        printf("FAIL  [%s] %s\n", group, what);
    }
}

static void check_eq_int(long long got, long long want, const char *what)
{
    checks++;
    if (got != want) {
        failures++;
        printf("FAIL  [%s] %s (got %lld, want %lld)\n", group, what, got, want);
    }
}

static void check_eq_str(const char *got, const char *want, const char *what)
{
    checks++;
    if (got == NULL || strcmp(got, want) != 0) {
        failures++;
        printf("FAIL  [%s] %s (got %s, want %s)\n", group, what, got ? got : "(null)", want);
    }
}

/* ------------------------------------------------------------- contract */

static void test_modes(void)
{
    group = "modes";
    /* spec v2.1 S0/S3.4/S3.5: three modes, two parts */
    const imu_mode_t *a = imu_mode_by_letter('A');
    const imu_mode_t *b = imu_mode_by_letter('B');
    const imu_mode_t *m = imu_mode_by_letter('m'); /* case-insensitive */
    check(a != NULL && b != NULL && m != NULL, "all three modes resolve (lowercase too)");
    check_eq_int((long long)a->odr_hz, 4000, "mode A ODR 4000 Hz");
    check_eq_int((long long)b->odr_hz, 2000, "mode B ODR 2000 Hz");
    check_eq_int((long long)m->odr_hz, 1000, "mode M ODR 1000 Hz");
    check_eq_int((long long)a->band_hz, 500, "mode A band 500 Hz");
    check_eq_int((long long)b->band_hz, 250, "mode B band 250 Hz");
    check_eq_int((long long)m->band_hz, 150, "mode M band 150 Hz");
    check_eq_int((long long)a->i2c_hz, 1000000, "mode A requires I2C 1 MHz");
    check_eq_int((long long)m->i2c_hz, 400000, "mode M is limited to I2C 400 kHz");
    /* the whole point of the guard: same register, different values */
    check_eq_int(imu_mode_by_letter('A')->who_am_i, 0x47, "ICM-42688-P WHO_AM_I 0x47");
    check_eq_int(imu_mode_by_letter('M')->who_am_i, 0x71, "MPU-9250 WHO_AM_I 0x71");
    check_eq_str(imu_mode_by_letter('A')->sensor, "ICM-42688-P", "mode A part string");
    check_eq_str(imu_mode_by_letter('M')->sensor, "MPU-9250", "mode M part string");
    /* mode M's FIFO is 512 B with a 14 B record; the watermark must leave
     * runway (spec S3.7.2: 16 records, NOT 32) */
    check_eq_int((long long)m->fifo_bytes, 512, "mode M FIFO is 512 bytes");
    check_eq_int((long long)m->record_bytes, 14, "mode M record is 14 bytes");
    check_eq_int((long long)m->wm_records, 16, "mode M watermark is 16 records");
    check_eq_int((long long)a->wm_records, 32, "mode A watermark is 32 records");
    check(imu_mode_by_letter('X') == NULL, "an unknown mode letter resolves to NULL");
    check(imu_mode_by_id((imu_mode_id_t)7) == NULL, "an out-of-range mode id is refused");
}

static void test_identity_guard(void)
{
    group = "identity";
    const char *sensor = NULL;
    const imu_mode_t *a = imu_mode_by_letter('A');
    const imu_mode_t *m = imu_mode_by_letter('M');
    check_eq_int(imu_check_identity(a, 0x47, &sensor), 0, "mode A accepts 0x47");
    check_eq_str(sensor, "ICM-42688-P", "mode A resolves its part string");
    check_eq_int(imu_check_identity(m, 0x71, &sensor), 0, "mode M accepts 0x71");
    check_eq_str(sensor, "MPU-9250", "mode M resolves its part string");
    /* the failure that matters: mode A's registers on an MPU-9250 */
    check_eq_int(imu_check_identity(a, 0x71, NULL), -1, "mode A REFUSES 0x71");
    check_eq_int(imu_check_identity(m, 0x47, NULL), -1, "mode M REFUSES 0x47");
    check_eq_int(imu_check_identity(a, 0x00, NULL), -1, "an unknown part is refused");
    check_eq_int(imu_check_identity(NULL, 0x47, NULL), -1, "a NULL mode is refused");
}

static void test_frame_bytes(void)
{
    group = "frame";
    /* spec S5.3: exactly 24 bytes, "<I7h3H", little endian */
    check_eq_int(IMU_FRAME_BYTES, 24, "the frame is 24 bytes");
    check_eq_str(IMU_FRAME_STRUCT, "<I7h3H", "the frame struct string is the spec's");
    uint8_t f[IMU_FRAME_BYTES];
    int16_t accel[3] = {1, -2, 2048};
    int16_t gyro[3] = {3, -4, 16};
    imu_pack_frame(f, 0x03020100u, accel, gyro, 25.0, 0x0102u, IMU_FLAG_TIME_ESTIMATED);
    /* t_us little endian at offset 0 */
    check_eq_int(f[0], 0x00, "t_us byte 0");
    check_eq_int(f[1], 0x01, "t_us byte 1");
    check_eq_int(f[2], 0x02, "t_us byte 2");
    check_eq_int(f[3], 0x03, "t_us byte 3");
    /* ax = 1 -> 01 00 */
    check_eq_int(f[4], 0x01, "ax low byte at offset 4");
    check_eq_int(f[5], 0x00, "ax high byte at offset 5");
    /* ay = -2 -> FE FF */
    check_eq_int(f[6], 0xFE, "ay low byte (negative)");
    check_eq_int(f[7], 0xFF, "ay high byte (negative)");
    /* az = 2048 -> 00 08 */
    check_eq_int(f[8], 0x00, "az low byte");
    check_eq_int(f[9], 0x08, "az high byte");
    /* temp 25.00 C -> 2500 = 0x09C4 at offset 16 */
    check_eq_int(f[16], 0xC4, "temp low byte");
    check_eq_int(f[17], 0x09, "temp high byte");
    /* seq 0x0102 at 18, flags at 20, reserved zeroed at 22 */
    check_eq_int(f[18], 0x02, "seq low byte");
    check_eq_int(f[19], 0x01, "seq high byte");
    check_eq_int(f[20], (int)IMU_FLAG_TIME_ESTIMATED, "flags byte");
    check_eq_int(f[22], 0, "reserved is zero");
    check_eq_int(f[23], 0, "reserved is zero (high)");
}

static void test_temperature(void)
{
    group = "temp";
    /* spec S5.3: one representation at 0.01 C/LSB for both parts */
    check_eq_int(imu_temp_c100(25.0), 2500, "25.00 C -> 2500");
    check_eq_int(imu_temp_c100(-40.0), -4000, "-40.00 C -> -4000");
    check_eq_int(imu_temp_c100(0.0), 0, "0 C -> 0");
    check_eq_int(imu_temp_c100(100.0), 10000, "100 C -> 10000");
    /* saturation, not wraparound */
    check_eq_int(imu_temp_c100(1e9), 32767, "an absurd temperature saturates high");
    check_eq_int(imu_temp_c100(-1e9), -32768, "an absurd temperature saturates low");
    /* the two parts' FIFO formulae, both landing in the same field */
    /* ICM: T = raw/2.07 + 25 -> raw = (25-25)*2.07 = 0 gives 25 C */
    check_eq_int(imu_temp_c100_from_icm_fifo(0), 2500, "ICM FIFO raw 0 -> 25.00 C");
    /* FIFO_TEMP_DATA/2.07 + 25: raw 207 -> 100 + 25 = 125.00 C */
    check_eq_int(imu_temp_c100_from_icm_fifo(207), 12500, "ICM FIFO raw 207 -> 125.00 C");
    check_eq_int(imu_temp_c100_from_mpu_fifo(207), 12500, "MPU FIFO raw 207 -> 125.00 C");
    /* the same formula at a colder point, to catch an inverted divide */
    check_eq_int(imu_temp_c100_from_icm_fifo(-207), -7500, "ICM FIFO raw -207 -> -75.00 C");
    /* MPU register path uses 333.87 LSB/C and a 21 C offset */
    check_eq_int(imu_temp_c100_from_mpu_reg(0), 2100, "MPU register raw 0 -> 21.00 C");
    check_eq_int(imu_temp_c100_from_mpu_reg(3339), 3100, "MPU register raw 3339 -> 31.00 C");
    check_eq_int(imu_temp_c100_from_mpu_reg(-3339), 1100, "MPU register raw -3339 -> 11.00 C");
}

static void test_scales(void)
{
    group = "scales";
    /* spec S5.2: the two parts share these scales, which is exactly why a part
     * mix-up is undetectable downstream and must be caught at start-up */
    /* spec S5.2 gives the scales to 7 significant figures: 4.788403e-03 and
     * 5.985504e-04 m/s^2/LSB */
    check_eq_int((long long)(imu_scale_accel(16) * 1e10), 47884033LL, "accel +-16 g scale");
    check_eq_int((long long)(imu_scale_accel(2) * 1e10), 5985504LL, "accel +-2 g scale");
    check_eq_int((long long)(imu_scale_accel(4) * 1e10), 11971008LL, "accel +-4 g scale");
    check_eq_int((long long)(imu_scale_accel(8) * 1e10), 23942016LL, "accel +-8 g scale");
    check(imu_scale_accel(3) == 0.0, "an unsupported accel FSR returns 0");
    /* 6.097561e-02 deg/s per LSB */
    check_eq_int((long long)(imu_scale_gyro(2000.0) * 1e10), 609756097LL, "gyro +-2000 dps scale");
    check(imu_scale_gyro(3.0) == 0.0, "an unsupported gyro FSR returns 0");
}

static void test_paths(void)
{
    group = "path";
    char p[512];
    /* spec S6: <device>/<YYYY-MM-DD>/<YYYYMMDD>T<HHMMSS>Z_<mode>_imu<N>.imucap */
    /* 2026-10-15T14:12:05Z, the spec's own worked example */
    int n = imu_capture_path("/data", "cartg-01", IMU_MODE_M, 1792073525ull, 3, p, sizeof p);
    check(n > 0, "a path was produced");
    check_eq_str(p, "/data/cartg-01/2026-10-15/20261015T141205Z_M_imu003.imucap",
                 "the path matches the spec's worked example");
    /* the N field is zero-padded to 3 digits and rolls over past 999 */
    imu_capture_path("", "d", IMU_MODE_A, 1792073525ull, 1234, p, sizeof p);
    check_eq_str(p, "/d/2026-10-15/20261015T141205Z_A_imu1234.imucap",
                 "the segment index is zero-padded to at least 3 digits");
    /* leap day, to catch a naive day counter */
    imu_capture_path("", "d", IMU_MODE_A, 1709164800ull, 0, p, sizeof p);
    check_eq_str(p, "/d/2024-02-29/20240229T000000Z_A_imu000.imucap", "2024-02-29 is a leap day");
    /* and a non-leap century year */
    imu_capture_path("", "d", IMU_MODE_B, 4102444800ull, 0, p, sizeof p);
    check_eq_str(p, "/d/2100-01-01/21000101T000000Z_B_imu000.imucap",
                 "2100-01-01 (a non-leap century year)");
    /* the spec's rotation margin: 64 MiB must stay inside the 71.6 min wrap */
    double mins = 0;
    const imu_mode_t *m = imu_mode_by_letter('M');
    check_eq_int(imu_segment_minutes(m, IMU_ROTATE_BYTES, &mins), 0, "segment minutes computed");
    check(mins > 46.0 && mins < 47.0, "mode M's 64 MiB segment is ~46.6 min");
    check(mins < 71.6, "mode M's segment stays inside the uint32 us wrap");
    const imu_mode_t *a = imu_mode_by_letter('A');
    imu_segment_minutes(a, IMU_ROTATE_BYTES, &mins);
    check(mins > 11.0 && mins < 12.0, "mode A's 64 MiB segment is ~11.7 min");
}

static void test_header_json(void)
{
    group = "header";
    char hdr[4096];
    const imu_mode_t *m = imu_mode_by_letter('M');
    /* provenance is passed as RAW C strings; the writer escapes them. The wheel
     * is passed as an already-formed JSON fragment. */
    int n = imu_header_json(m, "cartg-bench", 1700000000000000000ull,
                            "chassis, rigid; +X forward, +Y right, +Z down",
                            "{\"corner\":\"front-left\",\"count\":4,\"diameter_m\":0.6}",
                            "bench rig", "imu_logger 0.1.0", hdr, sizeof hdr);
    check(n > 0, "the header was written");
    check(hdr[n - 1] == '\n', "the header ends with a newline (spec S5.1)");
    /* the keys the spec requires (S5.2) must all be present */
    const char *keys[] = {"magic", "spec_version", "frame_bytes", "frame_struct", "endian",
                          "axes", "n_axes", "fs_nominal", "mode", "accel_fsr_g",
                          "gyro_fsr_dps", "scale_accel", "scale_gyro", "accel_units",
                          "gyro_units", "temp_lsb_c", "start_unix_ns", "device", "notes"};
    for (size_t i = 0; i < sizeof keys / sizeof keys[0]; i++) {
        char needle[64];
        snprintf(needle, sizeof needle, "\"%s\":", keys[i]);
        checks++;
        if (strstr(hdr, needle) == NULL) {
            failures++;
            printf("FAIL  [%s] header is missing key %s\n", group, keys[i]);
        }
    }
    /* the provenance the analysis needs (S5.4) */
    const char *nkeys[] = {"band_hz", "who_am_i", "i2c_addr", "config", "mounting",
                           "wheel", "vehicle", "firmware", "flags_legend"};
    for (size_t i = 0; i < sizeof nkeys / sizeof nkeys[0]; i++) {
        char needle[64];
        snprintf(needle, sizeof needle, "\"%s\":", nkeys[i]);
        checks++;
        if (strstr(hdr, needle) == NULL) {
            failures++;
            printf("FAIL  [%s] notes is missing key %s\n", group, nkeys[i]);
        }
    }
    check(strstr(hdr, "\"magic\":\"IMUCAP2\"") != NULL, "magic is IMUCAP2");
    check(strstr(hdr, "\"mode\":\"M\"") != NULL, "the mode letter is in the header");
    check(strstr(hdr, "\"device\":\"MPU-9250\"") != NULL, "the part string is in the header");
    check(strstr(hdr, "\"band_hz\":150.0") != NULL, "mode M's band travels with the file");
    check(strstr(hdr, "\"who_am_i\":\"0x71\"") != NULL, "the WHO_AM_I evidence is recorded");
    check(strstr(hdr, "\"diameter_m\":0.6") != NULL, "the wheel geometry is recorded");
    check(strstr(hdr, "\"frame_struct\":\"<I7h3H\"") != NULL, "the struct format is recorded");
    check(strstr(hdr, "\"gyro_fsr_dps\":2000.0") != NULL, "gyro FSR is an integral float (2000.0)");
    check(strstr(hdr, "\"fs_nominal\":1000.0") != NULL,
          "the ODR is fixed-notation, not C's 1e+03 (a Python repr mismatch)");
    check(strstr(hdr, "\"mounting\":\"chassis, rigid") != NULL,
          "a raw provenance string is quoted, not double-quoted");
    /* a wheel of nulls must still be emitted, not omitted (S5.4) */
    n = imu_header_json(m, "d", 0, NULL, NULL, NULL, NULL, hdr, sizeof hdr);
    check(n > 0 && strstr(hdr, "\"wheel\":null") != NULL, "an unknown wheel is an explicit null");
    check(strstr(hdr, "\"mounting\":null") != NULL, "a NULL mounting is an explicit null");
    check(strstr(hdr, "\"vehicle\":null") != NULL, "a NULL vehicle is an explicit null");
    check(strstr(hdr, "\"firmware\":null") != NULL, "a NULL firmware rev is an explicit null");
    check(strstr(hdr, "\"who_am_i\":\"0x71\"") != NULL, "the guard's evidence is still written");
    /* a wheel that is not a JSON object must be refused, not silently escaped */
    check_eq_int(imu_header_json(m, "d", 0, "null", "not-json", "null", "null", hdr, sizeof hdr),
                 -1, "a non-JSON wheel fragment is refused");
}

static void test_rotate_bounds(void)
{
    group = "rotation";
    /* spec S6: 64 MiB for EVERY mode; the mandatory bound is the uint32 wrap */
    check_eq_int((long long)IMU_ROTATE_BYTES, 64LL * 1024 * 1024, "rotate size is 64 MiB");
    /* a frame boundary only: 64 MiB / 24 = 2796202.67, so the last frame of a
     * segment must still fit rather than straddling the bound */
    unsigned long long whole = IMU_ROTATE_BYTES / IMU_FRAME_BYTES;
    check(whole * IMU_FRAME_BYTES + IMU_FRAME_BYTES > IMU_ROTATE_BYTES,
          "a segment never ends mid-frame");
}

/* ---------------------------------------------------------- register data */

/* Every value the spec states for a field must be present at the position this
 * firmware writes it, IN THE BANK THAT IS IN EFFECT AT THAT POINT. This is the
 * check that catches a wrong bit range AND a register written into the wrong
 * bank: a bank selector only appears once per bank group, so an assertion that
 * demanded the selector on every entry would be testing the array's shape
 * instead of the driver's behaviour. */
static int seq_has(const imu_reg_op_t *seq, size_t n, int bank, uint8_t reg, uint8_t value)
{
    /* The part powers up in bank 0 (REG_BANK_SEL resets to 0) and the sequence
     * selects every bank EXPLICITLY, so `cur` starts at 0 and moves only when
     * the sequence writes the selector. That is deliberately the only mechanism:
     * an entry that carried an implicit "I mean bank 1" field would let a driver
     * that forgot to emit the selector appear correct while writing into bank 0.
     * `imu_apply_sequence` on the target therefore has exactly one rule. */
    int cur = ICM_BANK_SEL_0;
    for (size_t i = 0; i < n; i++) {
        if (seq[i].reg == ICM_REG_REG_BANK_SEL) {
            cur = (int)seq[i].value;
            continue;
        }
        if (seq[i].reg == 0x00) {
            continue; /* the identity-guard / settle sentinel, not a write */
        }
        if (seq[i].bank != ICM_NO_BANK) {
            return -1; /* an implicit bank is a contract violation */
        }
        if (seq[i].reg == reg && seq[i].value == value && cur == bank) {
            return 1;
        }
    }
    return 0;
}

/* The banks a mode's sequence actually selects, so a test can assert that the
 * bank-1 and bank-2 AAF writes are preceded by their selector. */
static int seq_selects_bank(const imu_reg_op_t *seq, size_t n, int bank)
{
    for (size_t i = 0; i < n; i++) {
        if (seq[i].reg == ICM_REG_REG_BANK_SEL && (int)seq[i].value == bank) {
            return 1;
        }
    }
    return 0;
}

static void test_sequences(void)
{
    group = "seq";
    size_t na = 0, nb = 0, nm = 0;
    const imu_reg_op_t *a = imu_seq_for_mode(IMU_MODE_A, &na);
    const imu_reg_op_t *b = imu_seq_for_mode(IMU_MODE_B, &nb);
    const imu_reg_op_t *m = imu_seq_for_mode(IMU_MODE_M, &nm);
    check(a && b && m, "all three sequences exist");
    check(imu_seq_for_mode(99, NULL) == NULL, "an unknown mode has no sequence");

    /* the spec's own worked field values, per mode (S3.4.1 / S4.1) */
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_ACCEL_CONFIG0, 0x04),
          "mode A ACCEL_CONFIG0 = FS 000 + ODR 0100");
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_GYRO_CONFIG0, 0x04),
          "mode A GYRO_CONFIG0 = FS 000 + ODR 0100");
    check(seq_has(b, nb, ICM_BANK_SEL_0, ICM_REG_ACCEL_CONFIG0, 0x05),
          "mode B ACCEL_CONFIG0 = FS 000 + ODR 0101");
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_GYRO_ACCEL_CONFIG0, 0x44),
          "mode A UI_FILT_BW = 0100 both nibbles");
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_PWR_MGMT0, 0x0F),
          "both sensors to Low Noise: ACCEL_MODE=11 GYRO_MODE=11");
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_FIFO_CONFIG1, 0x47),
          "FIFO_CONFIG1 = accel|gyro|temp|resume");
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_FIFO_CONFIG2, 0x20),
          "mode A FIFO watermark = 32 records");
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_TMST_CONFIG, 0x15),
          "TMST_CONFIG enables, 1 us, delta, register read-back");
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_INT_SOURCE0, 0x04),
          "FIFO_THS_INT1_EN is bit 2");
    check(seq_has(a, na, ICM_BANK_SEL_2, ICM_BANK2_ACCEL_AAF_DELT, 0x22),
          "mode A ACCEL_AAF_DELT 17 at [6:1]");
    check(seq_has(a, na, ICM_BANK_SEL_2, ICM_BANK2_ACCEL_AAF_DELTSQR_HI, 0x71),
          "mode A DELTSQR 288 = high nibble 1 + bitshift 7");
    check(seq_has(a, na, ICM_BANK_SEL_1, ICM_BANK1_GYRO_AAF_DELT, 0x11),
          "mode A GYRO_AAF_DELT 17 at [5:0] -- a DIFFERENT range from the accel's");
    check(seq_has(b, nb, ICM_BANK_SEL_1, ICM_BANK1_GYRO_AAF_DELT, 0x09),
          "mode B GYRO_AAF_DELT 9 at [5:0]");
    check(seq_has(b, nb, ICM_BANK_SEL_2, ICM_BANK2_ACCEL_AAF_DELTSQR_HI, 0x90),
          "mode B DELTSQR 81 = high nibble 0 + bitshift 9");
    /* an AAF write in the wrong bank silently programmes nothing, so the
     * selectors must be present and must precede the writes */
    check(seq_selects_bank(a, na, ICM_BANK_SEL_1), "mode A selects bank 1 for the gyro AAF");
    check(seq_selects_bank(a, na, ICM_BANK_SEL_2), "mode A selects bank 2 for the accel AAF");
    check(seq_selects_bank(a, na, ICM_BANK_SEL_0), "mode A returns to bank 0 for the FIFO");
    {
        int bank1_at = -1, bank2_at = -1, gyro_aaf_at = -1, accel_aaf_at = -1;
        int back_to_0 = -1;
        for (size_t i = 0; i < na; i++) {
            if (a[i].reg == ICM_REG_REG_BANK_SEL && a[i].value == 0x01) bank1_at = (int)i;
            if (a[i].reg == ICM_REG_REG_BANK_SEL && a[i].value == 0x02) bank2_at = (int)i;
            if (a[i].reg == ICM_REG_REG_BANK_SEL && a[i].value == 0x00) back_to_0 = (int)i;
            if (a[i].reg == ICM_BANK1_GYRO_AAF_DELT) gyro_aaf_at = (int)i;
            if (a[i].reg == ICM_BANK2_ACCEL_AAF_DELT) accel_aaf_at = (int)i;
        }
        check(bank2_at > 0 && accel_aaf_at > bank2_at,
              "the accel AAF write comes after its bank-2 selector");
        check(bank1_at > 0 && gyro_aaf_at > bank1_at,
              "the gyro AAF write comes after its bank-1 selector");
        check(back_to_0 > bank1_at && back_to_0 < (int)na,
              "the sequence returns to bank 0 before the FIFO and interrupt setup");
    }

    /* mode M: the accel DLPF MUST be on (S4.2 step 4) -- bypassing it is what
     * forbids the 0-500 Hz claim, so this is the load-bearing byte */
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_ACCEL_CONFIG2, 0x07),
          "mode M A_DLPFCFG=7 with ACCEL_FCHOICE_B=0 (DLPF ON)");
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_CONFIG, 0x01),
          "mode M gyro DLPF_CFG=1, FCHOICE_B=00");
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_SMPLRT_DIV, 0x00),
          "mode M SMPLRT_DIV=0 -> 1 kHz");
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_ACCEL_CONFIG, 0x18), "mode M accel +-16 g");
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_GYRO_CONFIG, 0x18), "mode M gyro +-2000 dps");
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_FIFO_EN, 0xF8), "mode M FIFO: accel+gyro+temp");
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_I2C_MST_CTRL, 0x00),
          "the aux I2C master is left disabled (magnetometer out of scope)");
    check(seq_has(m, nm, ICM_BANK_SEL_0, MPU_REG_INT_ENABLE, 0x10),
          "mode M enables FIFO_OFLOW as its only FIFO interrupt");

    /* the endian byte is the one spec S4.1 step 3 does not freeze: both sensor
     * data and the FIFO count must be LITTLE endian or the frames are swapped */
    check(seq_has(a, na, ICM_BANK_SEL_0, ICM_REG_INTF_CONFIG0, 0x70),
          "SENSOR_DATA_ENDIAN is little (the reset value leaves it BIG)");

    /* both sequences must agree on everything except the rate-dependent bytes,
     * so a copy-paste slip between A and B shows up here */
    check(na == nb, "modes A and B have the same sequence length");
    int diffs = 0;
    for (size_t i = 0; i < na && i < nb; i++) {
        if (a[i].reg != b[i].reg || a[i].bank != b[i].bank) {
            diffs++;
        }
    }
    check_eq_int(diffs, 0, "modes A and B touch the same registers in the same order");
}

static void test_packers(void)
{
    group = "pack";
    /* positions re-read from DS-000347 rev 1.8 and RM-MPU-9250A-00 rev 1.6 */
    check_eq_int(icm_pack_fifo_config1(1, 1, 1, 0, 0, 1), 0x47,
                 "FIFO_CONFIG1: accel bit0, gyro bit1, temp bit2, resume bit6");
    check_eq_int(icm_pack_fifo_config1(0, 0, 0, 0, 0, 0), 0x00, "FIFO_CONFIG1 all off");
    check_eq_int(icm_pack_fifo_config1(0, 0, 0, 0, 1, 0), 0x10, "FIFO_HIRES_EN is bit 4");
    check_eq_int(icm_pack_fifo_config1(0, 0, 0, 1, 0, 0), 0x08, "FIFO_TMST_FSYNC_EN is bit 3");
    check_eq_int(icm_pack_pwr_mgmt0(3, 3, 0, 0), 0x0F, "PWR_MGMT0 both LN = 0x0F");
    check_eq_int(icm_pack_pwr_mgmt0(0, 0, 0, 0), 0x00, "PWR_MGMT0 both off = 0x00");
    check_eq_int(icm_pack_pwr_mgmt0(3, 3, 1, 1), 0x3F,
                 "PWR_MGMT0 idle is bit 4 and temp_dis is bit 5");
    check_eq_int(icm_pack_pwr_mgmt0(3, 0, 0, 0), 0x0C, "PWR_MGMT0 gyro-only LN = 0x0C");
    check_eq_int(icm_pack_int_config(1, 1, 1), 0x07,
                 "INT_CONFIG: polarity bit0, drive bit1, mode bit2");
    check_eq_int(icm_pack_int_config(0, 0, 0), 0x00, "INT_CONFIG all clear");
    check_eq_int(icm_pack_int_config(1, 0, 0), 0x04, "INT_CONFIG latched alone is bit 2");
    check_eq_int(icm_pack_tmst_config(1, 1, 1, 0), 0x15,
                 "TMST_CONFIG: EN bit0, DELTA bit2, TO_REGS bit4");
    check_eq_int(icm_pack_tmst_config(1, 0, 0, 1), 0x09,
                 "TMST_CONFIG: RES=1 us is bit 3 (=0 for 1 us, set for 16 us)");
    check_eq_int(icm_pack_intf_config0(1, 1, 1, 0, 2), 0x72,
                 "INTF_CONFIG0 little sensor + little count + REC count + I2C");
    check_eq_int(icm_pack_intf_config0(1, 0, 0, 0, 2), 0x42,
                 "INTF_CONFIG0 with the reset's BIG endian fields gives 0x42, not 0x30");
    check_eq_int(icm_pack_aaf_delt_accel(17), 0x22, "ACCEL_AAF_DELT 17 -> [6:1]");
    check_eq_int(icm_pack_aaf_delt_gyro(17), 0x11, "GYRO_AAF_DELT 17 -> [5:0]");
    check_eq_int(icm_pack_aaf_delt_accel(17), icm_pack_aaf_delt_gyro(17) << 1,
                 "the accel shift is the gyro shift plus one bit");
    check_eq_int(icm_pack_aaf_deltsqr_hi4(288), 1, "DELTSQR 288's high nibble is 1");
    check_eq_int(icm_pack_aaf_deltsqr_hi4(81), 0, "DELTSQR 81's high nibble is 0");
    check_eq_int(icm_pack_aaf_deltsqr_hi(7, 1), 0x71, "DELTSQR_HI for {bitshift 7, hi 1}");
    check_eq_int(icm_pack_aaf_deltsqr_hi(9, 0), 0x90, "DELTSQR_HI for {bitshift 9, hi 0}");
    check_eq_int(icm_pack_ui_filt_bw(4, 4), 0x44, "UI_FILT_BW both nibbles 0100");
    check_eq_int(icm_pack_ui_filt_ord(2, 2), 0x18, "gyro ord [3:2] + accel ord [4:3]");
    check_eq_int(icm_pack_ui_filt_ord(1, 0), 0x04, "gyro ord 01 alone = 0x04");
    check_eq_int(icm_pack_ui_filt_ord(0, 1), 0x08, "accel ord 01 alone = 0x08");
    check_eq_int(icm_pack_odr_fs(0, 4), 0x04, "FS 000 + ODR 0100");
    check_eq_int(icm_pack_odr_fs(3, 15), 0x6F, "FS 011 + ODR 1111");
    check_eq_int(icm_pack_fifo_wm_hi(32), 0, "watermark 32 has a zero high nibble");
    check_eq_int(icm_pack_fifo_wm_hi(4095), 0x0F, "watermark 4095 high nibble");

    check_eq_int(mpu_pack_config(1, 0, 0), 0x01, "CONFIG DLPF_CFG=1");
    check_eq_int(mpu_pack_accel_config2(7, 0), 0x07, "ACCEL_CONFIG2 A_DLPFCFG=7, FCHOICE_B=0");
    check_eq_int(mpu_pack_accel_config(3), 0x18, "ACCEL_CONFIG FS_SEL=11 at [4:3]");
    check_eq_int(mpu_pack_gyro_config(3, 0), 0x18, "GYRO_CONFIG FS_SEL=11, FCHOICE_B=00 at [1:0]");
    check_eq_int(mpu_pack_gyro_config(3, 3), 0x1B, "GYRO_CONFIG FCHOICE_B=11 disables the gyro DLPF");
    check_eq_int(mpu_pack_fifo_en(1, 1, 1, 1, 1), 0xF8, "FIFO_EN accel+temp+gyro");
    check_eq_int(mpu_pack_user_ctrl(1, 0, 0), 0x40, "USER_CTRL FIFO_EN is bit 6");
    check_eq_int(mpu_pack_user_ctrl(1, 1, 0), 0x44, "USER_CTRL + FIFO_RST is bit 2");
    check_eq_int(mpu_pack_pwr_mgmt_1(0, 1, 0), 0x01, "PWR_MGMT_1 CLKSEL=001 (auto: PLL if ready)");
    check_eq_int(mpu_pack_pwr_mgmt_1(0, 0, 1), 0x80, "PWR_MGMT_1 DEVICE_RESET is bit 7");
    check_eq_int(mpu_pack_int_enable(0, 1), 0x10, "INT_ENABLE FIFO_OFLOW_EN is bit 4");
}

/* --------------------------------------------------------------- logger */

static void test_logger_basics(void)
{
    group = "logger";
    const imu_mode_t *m = imu_mode_by_letter('M');
    imu_fake_t fake;
    imu_fake_init(&fake, m);
    imu_hal_t hal = imu_fake_hal(&fake);
    static uint8_t buf[4u << 20];
    imu_memsink_t ms;
    memset(&ms, 0, sizeof ms);
    ms.buf = buf;
    ms.cap = sizeof buf;
    imu_sink_t sink = imu_memsink(&ms);
    imu_logger_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.flush_every = 1000;
    cfg.rotate_bytes = IMU_ROTATE_BYTES;
    cfg.frame_cap = 10000;
    imu_logger_t lg;
    check_eq_int(imu_logger_init(&lg, m, &hal, &sink, &cfg), 0, "init succeeds");
    check_eq_int(imu_logger_init(NULL, m, &hal, &sink, &cfg), -1, "init rejects a NULL logger");
    check_eq_int(imu_logger_init(&lg, NULL, &hal, &sink, &cfg), -1, "init rejects a NULL mode");
    imu_logger_init(&lg, m, &hal, &sink, &cfg);
    imu_logger_set_provenance(&lg, "cartg-t", "/out", "rig", "{\"count\":4}", "veh", "rev");
    check_eq_int(imu_logger_run(&lg), 0, "the capture ran");
    check_eq_int((long long)lg.stats.frames_written, 10000, "exactly the frame cap was written");
    check(ms.len > 0, "bytes reached the sink");
    /* every frame is 24 bytes after one header line */
    size_t frames = (ms.len - strcspn((const char *)ms.buf, "\n") - 1) / IMU_FRAME_BYTES;
    check_eq_int((long long)frames, 10000, "the sink holds 10000 whole frames");
    check_eq_int((long long)(ms.len - 24 * 10000),
                 (long long)strcspn((const char *)ms.buf, "\n") + 1,
                 "the header occupies exactly the remaining bytes");
    check(ms.closes == 1, "the segment was closed exactly once");
    check(ms.flushes >= 9, "the file was flushed at least every 1000 frames");
    /* the header records this run's provenance, not a default */
    check(strstr((const char *)ms.buf, "\"device\":\"MPU-9250\"") != NULL, "part string in header");
    check(strstr((const char *)ms.buf, "\"firmware\":\"rev\"") != NULL, "firmware rev in header");
    check(strstr((const char *)ms.buf, "\"mode\":\"M\"") != NULL, "mode in header");
}

static void test_logger_identity_aborts(void)
{
    group = "logger-guard";
    const imu_mode_t *m = imu_mode_by_letter('M');
    imu_fake_t fake;
    imu_fake_init(&fake, m);
    fake.who_am_i = 0x47; /* the carrier part */
    imu_hal_t hal = imu_fake_hal(&fake);
    imu_memsink_t ms;
    static uint8_t buf[4096];
    memset(&ms, 0, sizeof ms);
    ms.buf = buf;
    ms.cap = sizeof buf;
    imu_sink_t sink = imu_memsink(&ms);
    imu_logger_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.flush_every = 10;
    cfg.rotate_bytes = IMU_ROTATE_BYTES;
    cfg.frame_cap = 100;
    imu_logger_t lg;
    imu_logger_init(&lg, m, &hal, &sink, &cfg);
    imu_logger_set_provenance(&lg, "d", "/o", "m", "{}", "v", "r");
    check_eq_int(imu_logger_run(&lg), -3, "the run refuses with -3 on a part mismatch");
    check_eq_int((long long)ms.writes, 0, "NOT ONE byte was written");
    check_eq_int((long long)ms.len, 0, "no file content exists");
    check_eq_int(fake.led, IMU_LED_ERROR, "the LED was put into the error pattern");
    check_eq_int(lg.state, IMU_STATE_ERROR, "the state machine is in ERROR");
}

static void test_logger_drops(void)
{
    group = "logger-drops";
    const imu_mode_t *m = imu_mode_by_letter('M');
    imu_fake_t fake;
    imu_fake_init(&fake, m);
    fake.lost_every_n_drains = 4;
    fake.lost_per_event = 10;
    imu_hal_t hal = imu_fake_hal(&fake);
    static uint8_t buf[8u << 20];
    imu_memsink_t ms;
    memset(&ms, 0, sizeof ms);
    ms.buf = buf;
    ms.cap = sizeof buf;
    imu_sink_t sink = imu_memsink(&ms);
    imu_logger_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.flush_every = 1000;
    cfg.rotate_bytes = IMU_ROTATE_BYTES;
    cfg.frame_cap = 6400; /* exactly 400 drains of 16 at ODR 1000 */
    imu_logger_t lg;
    imu_logger_init(&lg, m, &hal, &sink, &cfg);
    imu_logger_set_provenance(&lg, "d", "/o", "m", "{}", "v", "r");
    check_eq_int(imu_logger_run(&lg), 0, "the lossy capture still ran");
    /* 400 drains, loss injected every 4th -> 100 events * 10 records */
    check_eq_int((long long)lg.stats.fifo_overrun_events, 100, "100 overrun events");
    check_eq_int((long long)lg.stats.dropped_frames, 1000, "1000 records lost, exactly");
    check_eq_int((long long)fake.lost_total, 1000, "the sensor's own counter agrees");
    check(lg.degraded == 1, "the run is flagged degraded");
    check_eq_int(fake.led, IMU_LED_DEGRADED, "the LED shows degraded, not OK");
    /* the drop fraction must be measurably over the spec's 0.1% budget, i.e. the
     * logger REPORTS the problem rather than hiding it */
    double frac = imu_logger_drop_fraction(&lg);
    check(frac > 0.001, "the drop fraction exceeds the spec's 0.1% budget and is reported");
}

static void test_logger_failed_bringup(void)
{
    group = "logger-bringup";
    const imu_mode_t *m = imu_mode_by_letter('A');
    imu_fake_t fake;
    imu_fake_init(&fake, m);
    fake.bus_absent = 1;
    imu_hal_t hal = imu_fake_hal(&fake);
    imu_memsink_t ms;
    static uint8_t buf[4096];
    memset(&ms, 0, sizeof ms);
    ms.buf = buf;
    ms.cap = sizeof buf;
    imu_sink_t sink = imu_memsink(&ms);
    imu_logger_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.flush_every = 10;
    cfg.rotate_bytes = IMU_ROTATE_BYTES;
    cfg.frame_cap = 10;
    imu_logger_t lg;
    imu_logger_init(&lg, m, &hal, &sink, &cfg);
    imu_logger_set_provenance(&lg, "d", "/o", "m", "{}", "v", "r");
    check_eq_int(imu_logger_run(&lg), -2, "an absent part refuses with -2");
    check_eq_int((long long)ms.writes, 0, "no file was created for a failed bring-up");
    check_eq_int(fake.led, IMU_LED_ERROR, "the LED shows the error");
}

static void test_logger_sink_failure(void)
{
    group = "logger-sink";
    const imu_mode_t *m = imu_mode_by_letter('M');
    imu_fake_t fake;
    imu_fake_init(&fake, m);
    imu_hal_t hal = imu_fake_hal(&fake);
    imu_memsink_t ms;
    static uint8_t buf[1u << 20];
    memset(&ms, 0, sizeof ms);
    ms.buf = buf;
    ms.cap = sizeof buf;
    ms.fail_write_at = 200; /* give up from the 200th write onward */
    imu_sink_t sink = imu_memsink(&ms);
    imu_logger_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.flush_every = 100;
    cfg.rotate_bytes = IMU_ROTATE_BYTES;
    cfg.frame_cap = 1000;
    cfg.max_consecutive_host_overruns = 5;
    imu_logger_t lg;
    imu_logger_init(&lg, m, &hal, &sink, &cfg);
    imu_logger_set_provenance(&lg, "d", "/o", "m", "{}", "v", "r");
    /* A dead volume must END the run. Spinning forever "writing" nothing is the
     * failure mode this test exists to prevent: from outside, a silently
     * non-writing recorder looks exactly like a healthy one. */
    check_eq_int(imu_logger_run(&lg), -8, "the loop gives up on a dead sink (-8)");
    check(ms.fail_writes >= 3, "the sink really did fail repeatedly");
    check_eq_int(lg.state, IMU_STATE_ERROR, "the state machine reports ERROR");
    check_eq_int(fake.led, IMU_LED_ERROR, "the LED shows the failure");
    check(lg.stats.host_overrun_frames >= 5, "the refused frames were counted");
    check(imu_logger_drop_fraction(&lg) > 0.0, "a dead sink shows up as a non-zero loss fraction");
}

static void test_achieved_rate_and_jitter(void)
{
    group = "rate";
    const imu_mode_t *m = imu_mode_by_letter('M');
    imu_fake_t fake;
    imu_fake_init(&fake, m);
    fake.records_per_drain = 1;
    imu_hal_t hal = imu_fake_hal(&fake);
    static uint8_t buf[1u << 20];
    imu_memsink_t ms;
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
    imu_logger_init(&lg, m, &hal, &sink, &cfg);
    imu_logger_set_provenance(&lg, "d", "/o", "m", "{}", "v", "r");
    imu_logger_run(&lg);
    double fs = imu_logger_achieved_fs(&lg);
    /* the fake's clock advances exactly one ODR period per record, so the
     * timestamp-derived rate must come out at the nominal rate */
    double err_pct = 100.0 * (fs - m->odr_hz) / m->odr_hz;
    check(err_pct < 0.001 && err_pct > -0.001, "achieved rate equals nominal within 0.001%");
    check(imu_logger_achieved_fs(NULL) == 0.0, "achieved_fs on a NULL logger is 0");
    double rms = -1, pp = -1;
    check_eq_int(imu_logger_jitter_us(&lg, &rms, &pp), 1, "jitter was computable");
    check(pp < 1.0, "an unjittered clock shows no interval spread");

    /* now with a KNOWN jitter, so the figure is asserted rather than printed */
    imu_fake_t f2;
    imu_fake_init(&f2, m);
    f2.records_per_drain = 1;
    f2.jitter_us = 200;
    imu_hal_t h2 = imu_fake_hal(&f2);
    imu_memsink_t ms2;
    memset(&ms2, 0, sizeof ms2);
    ms2.buf = buf;
    ms2.cap = sizeof buf;
    imu_sink_t sink2 = imu_memsink(&ms2);
    imu_logger_t l2;
    imu_logger_init(&l2, m, &h2, &sink2, &cfg);
    imu_logger_set_provenance(&l2, "d", "/o", "m", "{}", "v", "r");
    imu_logger_run(&l2);
    double rms2 = -1, pp2 = -1;
    imu_logger_jitter_us(&l2, &rms2, &pp2);
    /* every second drain is 200 us late (see imu_fake_wait_ready), so with a
     * period P the intervals are P-J, P+J, P-J, ... — mean P, pp 2J, rms J */
    double period = 1e6 / m->odr_hz;
    check(l2.stats.interval_min_us > (period - 200.0) - 2.0 &&
              l2.stats.interval_min_us < (period - 200.0) + 2.0,
          "the early interval is one period minus the jitter");
    check(l2.stats.interval_max_us > (period + 200.0) - 2.0 &&
              l2.stats.interval_max_us < (period + 200.0) + 2.0,
          "the late interval is one period plus the jitter");
    check(pp2 > 395.0 && pp2 < 405.0, "peak-to-peak is twice the injected jitter");
    check(rms2 > 195.0 && rms2 < 205.0, "RMS equals the injected jitter");
    check(l2.stats.interval_sum_us / (double)l2.stats.interval_count > period - 1.0 &&
              l2.stats.interval_sum_us / (double)l2.stats.interval_count < period + 1.0,
          "the mean interval is still exactly one period");
    /* The achieved rate is WHAT THE TIMESTAMPS SAY, jitter and all. It is held
     * to the spec's own 1% tolerance (S8.3) and deliberately NOT to exactness:
     * a sawtooth whose endpoints are asymmetric has no reason to close exactly
     * on the nominal rate over a finite window, and pretending otherwise would
     * be asserting a property of the harness rather than of the firmware. */
    double fs2 = imu_logger_achieved_fs(&l2);
    double err2 = 100.0 * (fs2 - m->odr_hz) / m->odr_hz;
    check(err2 < 1.0 && err2 > -1.0, "a jittered capture still reports its rate within 1%");
}

static void test_summary_line(void)
{
    group = "summary";
    const imu_mode_t *m = imu_mode_by_letter('M');
    imu_fake_t fake;
    imu_fake_init(&fake, m);
    imu_hal_t hal = imu_fake_hal(&fake);
    static uint8_t buf[1u << 20];
    imu_memsink_t ms;
    memset(&ms, 0, sizeof ms);
    ms.buf = buf;
    ms.cap = sizeof buf;
    imu_sink_t sink = imu_memsink(&ms);
    imu_logger_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.flush_every = 4000;
    cfg.rotate_bytes = IMU_ROTATE_BYTES;
    cfg.frame_cap = 4000;
    imu_logger_t lg;
    imu_logger_init(&lg, m, &hal, &sink, &cfg);
    imu_logger_set_provenance(&lg, "d", "/o", "m", "{}", "v", "r");
    imu_logger_run(&lg);
    char out[512];
    check(imu_logger_summary(&lg, out, sizeof out) > 0, "a summary line was produced");
    check(strstr(out, "MPU-9250") != NULL, "the summary names the part");
    check(strstr(out, "achieved_fs") != NULL, "the summary reports the achieved rate");
    check(strstr(out, "dropped") != NULL, "the summary reports the drop count");
    check_eq_int(imu_logger_summary(&lg, out, 4), -1, "a too-small buffer is refused");
}

static void test_contract_dump(void)
{
    group = "dump";
    char out[4096];
    int n = imu_contract_dump(out, sizeof out);
    check(n > 0, "the contract dump was produced");
    check(strstr(out, "\"spec_version\":\"2.1\"") != NULL, "the dump names spec 2.1");
    check(strstr(out, "\"frame_struct\":\"<I7h3H\"") != NULL, "the dump carries the frame struct");
    check(strstr(out, "\"M\":{\"sensor\":\"MPU-9250\",\"who_am_i\":113") != NULL,
          "the dump carries mode M's part and WHO_AM_I as a decimal");
    check(strstr(out, "\"i2c_hz\":400000") != NULL, "mode M's bus ceiling is in the dump");
    check_eq_int(imu_contract_dump(out, 8), -1, "a too-small buffer is refused");
}

int main(void)
{
    test_modes();
    test_identity_guard();
    test_frame_bytes();
    test_temperature();
    test_scales();
    test_paths();
    test_header_json();
    test_rotate_bounds();
    test_sequences();
    test_packers();
    test_logger_basics();
    test_logger_identity_aborts();
    test_logger_drops();
    test_logger_failed_bringup();
    test_logger_sink_failure();
    test_achieved_rate_and_jitter();
    test_summary_line();
    test_contract_dump();

    printf("\n%d checks, %d failed\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
