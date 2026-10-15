/* imu_contract.h — the frozen CTG-9 capture contract, in C.
 *
 * One source of truth for the firmware half. Every constant here is copied from
 * docs/imu_capture_spec.md (spec v2.1) and mirrors hardware/imu_capture.py, the
 * Python reference codec. `tests/test_firmware_contract.py` cross-checks this
 * file's `imu_contract_dump()` output against the Python module, so the two
 * cannot drift silently.
 *
 * Conventions:
 *   * all timestamps are uint32 microseconds from a monotonic host clock,
 *     zeroed at capture start (spec S3.6);
 *   * all samples on the wire are raw sensor LSB; scaling to SI is a reader
 *     concern driven by the header (spec S5.2);
 *   * nothing here allocates or does I/O.
 */
#ifndef IMU_CONTRACT_H
#define IMU_CONTRACT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define IMU_SPEC_VERSION "2.1"
#define IMU_MAGIC_V2 "IMUCAP2"
#define IMU_MAGIC_V1 "IMUCAP1"

/* Frame: 24 bytes, little-endian, no padding (spec S5.3).
 *   uint32 t_us | int16 ax,ay,az | int16 gx,gy,gz | int16 temp_c100
 *   uint16 seq | uint16 flags | uint16 reserved                     */
#define IMU_FRAME_STRUCT "<I7h3H"
#define IMU_FRAME_BYTES 24
#define IMU_FRAME_N_AXES 6

/* Per-frame flag bits (spec S5.3). */
#define IMU_FLAG_FIFO_OVERRUN (1u << 0)  /* the sensor FIFO lost records     */
#define IMU_FLAG_HOST_OVERRUN (1u << 1)  /* the host dropped frames itself  */
#define IMU_FLAG_TIME_ESTIMATED (1u << 2) /* t_us interpolated, not measured */

/* Rotation bound: 64 MiB for every mode. The mandatory bound is the uint32
 * microsecond wrap (71.6 min); 64 MiB keeps mode M at 46.6 min, a 1.5x margin
 * (spec S6). Do not raise it per-mode. */
#define IMU_ROTATE_BYTES (64ull * 1024ull * 1024ull)

/* WHO_AM_I register address coincides on both parts (117 / 0x75); the reset
 * VALUE does not, which is what makes the identity guard cheap (spec S2.2). */
#define IMU_REG_WHO_AM_I 0x75
#define IMU_WHO_AM_I_ICM42688P 0x47
#define IMU_WHO_AM_I_MPU9250 0x71

#define IMU_G0 9.80665
#define IMU_ACCEL_FSR_G 16
#define IMU_GYRO_FSR_DPS 2000.0
#define IMU_ACCEL_LSB_PER_G 2048.0    /* +-16 g */
#define IMU_GYRO_LSB_PER_DPS 16.4     /* +-2000 dps */
#define IMU_TEMP_LSB_C 0.01           /* temp_c100 field: 0.01 degC per LSB */

/* Sensor-side FIFO temperature formulae, both re-quantised into temp_c100 so
 * the file has exactly one representation (spec S5.3). */
#define IMU_TEMP_ICM_FIFO_DIV 2.07
#define IMU_TEMP_ICM_FIFO_OFF 25.0
#define IMU_TEMP_MPU_FIFO_DIV 2.07
#define IMU_TEMP_MPU_FIFO_OFF 25.0
#define IMU_TEMP_MPU_REG_DIV 333.87
#define IMU_TEMP_MPU_REG_OFF 21.0

typedef enum {
    IMU_MODE_A = 0, /* ICM-42688-P, 4000 Hz, band 0-500 Hz, I2C 1 MHz */
    IMU_MODE_B = 1, /* ICM-42688-P, 2000 Hz, band 0-250 Hz, I2C 1 MHz */
    IMU_MODE_M = 2, /* MPU-9250,    1000 Hz, band 0-150 Hz, I2C 400 kHz */
    IMU_MODE_COUNT = 3
} imu_mode_id_t;

typedef struct {
    imu_mode_id_t id;
    char letter;              /* 'A' | 'B' | 'M' — filename + header       */
    int icm;                  /* 1 = ICM-42688-P tier, 0 = MPU-9250 tier    */
    const char *sensor;       /* part string, must match Python MODE_SENSOR */
    uint8_t who_am_i;         /* expected WHO_AM_I, the identity guard      */
    double odr_hz;            /* configured output data rate                */
    double band_hz;           /* band the capture is valid over (S5.4)      */
    uint32_t i2c_hz;          /* bus speed this mode's config requires      */
    unsigned record_bytes;    /* bytes per FIFO record                      */
    unsigned wm_records;      /* FIFO watermark in records                  */
    unsigned fifo_bytes;      /* total FIFO depth                           */
    /* ICM-42688-P tier filters (unused, and not emitted, on mode M) */
    double aaf_bw_hz;
    int aaf_delt;
    int aaf_deltsqr;
    int aaf_bitshift;
    int ui_filt_ord;          /* 2 == 3rd order, matching the spec's codes  */
    int ui_filt_bw;           /* UI_FILT_BW code                            */
    double ui_bw_hz;
    /* MPU-9250 tier filters (unused on modes A/B) */
    int dlpf_cfg;             /* ACCEL_CONFIG2 A_DLPFCFG                    */
    double dlpf_bw_hz;
    int gyro_dlpf_cfg;        /* CONFIG DLPF_CFG                            */
    double gyro_bw_hz;
    int smplrt_div;
} imu_mode_t;

/* Mode table, spec S3.4/S3.5 + S3.7. Returns NULL for an unknown letter. */
const imu_mode_t *imu_mode_by_letter(char letter);
const imu_mode_t *imu_mode_by_id(imu_mode_id_t id);

/* m/s^2 per accel LSB and deg/s per gyro LSB, from the datasheet sensitivities. */
double imu_scale_accel(int fsr_g);
double imu_scale_gyro(double fsr_dps);

/* The identity guard (spec S2.4). Returns 0 and sets *out_sensor on a match;
 * returns -1 on a mismatch. Callers must ABORT the capture on -1 — never
 * auto-detect and continue, because the modes measure different bands. */
int imu_check_identity(const imu_mode_t *mode, uint8_t who_am_i, const char **out_sensor);

/* Header JSON, byte-identical to the Python writer's `_pack_header`
 * (sorted keys, no whitespace). Returns bytes written, or -1 if it does not fit.
 * `mounting`, `wheel` and `vehicle` are provenance strings supplied by the
 * caller; pass NULL for any that are unknown (the key is still emitted, as an
 * explicit null, so an absent key and an unknown value stay distinguishable). */
int imu_header_json(const imu_mode_t *mode, const char *device_id, uint64_t start_unix_ns,
                    const char *mounting, const char *wheel_json, const char *vehicle,
                    const char *firmware_rev, char *out, size_t out_cap);

/* `<device_id>/<YYYY-MM-DD>/<YYYYMMDD>T<HHMMSS>Z_<mode>_imu<N>.imucap` (spec S6).
 * Returns bytes written, or -1 if it does not fit. */
int imu_capture_path(const char *base_dir, const char *device_id, imu_mode_id_t mode,
                     uint64_t unix_s, unsigned index, char *out, size_t out_cap);

/* True when a capture file name would exceed the wrap margin: the segment size
 * is only legal while the mode's segment duration stays under 71.6 min (S6). */
int imu_segment_minutes(const imu_mode_t *mode, uint64_t rotate_bytes, double *out_minutes);

/* Machine-readable dump of every constant above, for the cross-language
 * contract test. Returns bytes written or -1. */
int imu_contract_dump(char *out, size_t out_cap);

/* Pack one frame into 24 bytes. `flags` is masked to 16 bits, `reserved` = 0. */
void imu_pack_frame(uint8_t out[IMU_FRAME_BYTES], uint32_t t_us, const int16_t accel[3],
                    const int16_t gyro[3], double temp_c, uint16_t seq, uint16_t flags);

/* Convert a raw FIFO temperature field to 0.01 degC units, saturating. */
int16_t imu_temp_c100(double temp_c);
int16_t imu_temp_c100_from_icm_fifo(int raw);
int16_t imu_temp_c100_from_mpu_fifo(int raw);
int16_t imu_temp_c100_from_mpu_reg(int raw);

#ifdef __cplusplus
}
#endif

#endif /* IMU_CONTRACT_H */
