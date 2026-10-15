/* imu_regs.h — the two normative bring-up sequences from spec v2.1 S4.
 *
 * The sequences are DATA, not code, so the host test can assert them op by op
 * against the spec text, and so a future part revision only touches this file.
 *
 * Each entry is one I2C write. `bank` selects the ICM-42688-P register bank
 * first (MPU-9250 entries use -1: that part has no banks). `field` is the
 * normative description the spec gives for the byte, quoted in short form, so
 * a reader can audit the packing without the datasheet open.
 *
 * HONESTY MARKERS. The spec prints exact bit positions for most fields (e.g.
 * `ACCEL_CONFIG0[7:5]`, `0x53[4:3]`); those packings are arithmetic. For a few
 * registers the spec states the FIELD VALUES but not the bit positions
 * `INTF_CONFIG0`, `INTF_CONFIG1`, `TMST_CONFIG`, `FIFO_CONFIG1`, `INT_CONFIG`,
 * `INT_SOURCE0`, `PWR_MGMT0` modes, MPU `CLKSEL`). Those entries carry the
 * marker "pos:unverified" in their `field` string and their positions come from
 * the part's register-map tables rather than from spec v2.1.
 * `tests/test_firmware_contract.py` asserts
 * the field value at the declared position so the packing is internally
 * consistent, but the POSITION itself is the one thing in the firmware that a
 * bench session with a logic analyser should confirm before a road run.
 */
#ifndef IMU_REGS_H
#define IMU_REGS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- ICM-42688-P register addresses (DS-000347 S14) --------------------- */
#define ICM_REG_DEVICE_CONFIG 0x11
#define ICM_REG_INT_CONFIG 0x14
#define ICM_REG_FIFO_CONFIG1 0x5F
#define ICM_REG_FIFO_CONFIG2 0x60
#define ICM_REG_FIFO_CONFIG3 0x61
#define ICM_REG_INT_SOURCE0 0x65
#define ICM_REG_INTF_CONFIG0 0x4C
#define ICM_REG_INTF_CONFIG1 0x4D
#define ICM_REG_PWR_MGMT0 0x4E
#define ICM_REG_GYRO_CONFIG0 0x4F
#define ICM_REG_ACCEL_CONFIG0 0x50
#define ICM_REG_GYRO_CONFIG1 0x51
#define ICM_REG_GYRO_ACCEL_CONFIG0 0x52
#define ICM_REG_ACCEL_CONFIG1 0x53
#define ICM_REG_TMST_CONFIG 0x54
#define ICM_REG_REG_BANK_SEL 0x76
#define ICM_REG_FIFO_COUNTH 0x2E
#define ICM_REG_FIFO_COUNTL 0x2F
#define ICM_REG_FIFO_DATA 0x30
#define ICM_REG_FIFO_LOST_PKT0 0x6C
#define ICM_REG_FIFO_LOST_PKT1 0x6D
#define ICM_REG_INT_STATUS 0x2D

/* ICM-42688-P AAF registers, per bank (spec S4.1 step 6). */
#define ICM_BANK1_GYRO_AAF_DELT 0x0C
#define ICM_BANK1_GYRO_AAF_DELTSQR 0x0D
#define ICM_BANK1_GYRO_AAF_DELTSQR_HI 0x0E
#define ICM_BANK2_ACCEL_AAF_DELT 0x03
#define ICM_BANK2_ACCEL_AAF_DELTSQR 0x04
#define ICM_BANK2_ACCEL_AAF_DELTSQR_HI 0x05

/* ---- MPU-9250 register addresses (RM-MPU-9250A-00 S4) ------------------- */
#define MPU_REG_SMPLRT_DIV 0x19
#define MPU_REG_CONFIG 0x1A
#define MPU_REG_GYRO_CONFIG 0x1B
#define MPU_REG_ACCEL_CONFIG 0x1C
#define MPU_REG_ACCEL_CONFIG2 0x1D
#define MPU_REG_FIFO_EN 0x23
#define MPU_REG_I2C_MST_CTRL 0x24
#define MPU_REG_INT_ENABLE 0x38
#define MPU_REG_INT_STATUS 0x3A
#define MPU_REG_USER_CTRL 0x6A
#define MPU_REG_PWR_MGMT_1 0x6B
#define MPU_REG_PWR_MGMT_2 0x6C
#define MPU_REG_FIFO_COUNTH 0x72
#define MPU_REG_FIFO_COUNTL 0x73
#define MPU_REG_FIFO_R_W 0x74

/* Bank selector value for the ICM-42688-P. */
#define ICM_BANK_SEL_0 0x00
#define ICM_BANK_SEL_1 0x01
#define ICM_BANK_SEL_2 0x02
#define ICM_NO_BANK (-1)

/* Delays the datasheet (not taste) requires, in microseconds. */
#define IMU_RESET_DELAY_ICM_US 1000      /* DEVICE_CONFIG reset, then poll RESET_DONE */
#define IMU_LEAVE_OFF_DELAY_ICM_US 200   /* S4.1 step 4: >= 200 us before further writes */
#define IMU_GYRO_SETTLE_ICM_US 45000     /* S4.1 step 4: >= 45 ms gyro drive settle */
#define IMU_RESET_DELAY_MPU_US 100000    /* PS: 100 ms start-up */
#define IMU_GYRO_SETTLE_MPU_US 35000     /* PS: 35 ms gyro start-up */

typedef struct {
    int bank;          /* ICM bank to select first, or IMU_NO_BANK          */
    uint8_t reg;
    uint8_t value;
    uint32_t delay_us; /* wait this long AFTER the write                    */
    const char *field; /* normative field description / honesty marker       */
} imu_reg_op_t;

/* The two sequences. `*_n` are the entry counts; the arrays are NULL-terminated
 * as well so a test can walk them without the count. */
extern const imu_reg_op_t imu_seq_icm_a[];
extern const imu_reg_op_t imu_seq_icm_b[];
extern const imu_reg_op_t imu_seq_mpu_m[];
extern const size_t imu_seq_icm_a_n;
extern const size_t imu_seq_icm_b_n;
extern const size_t imu_seq_mpu_m_n;

/* Sequence for a mode, with its length. Returns NULL for an unknown mode. */
const imu_reg_op_t *imu_seq_for_mode(int mode_id, size_t *out_n);

/* Sentinel the bring-up loop uses in place of a real WHO_AM_I read: the
 * sequences carry a 0x00/0x00 entry where the identity guard runs, and any
 * record WITH a delay is a settle wait, not a register write. */
#define IMU_OP_IS_READ(op_reg, op_delay_us) ((op_reg) == 0x00 && (op_delay_us) == 0)
#define IMU_OP_IS_DELAY(op_reg, op_delay_us) ((op_reg) == 0x00 && (op_delay_us) != 0)

/* Field packers, so the literals above are computed rather than hand-added.
 * Each returns the packed byte for the field value(s) the spec names. */
uint8_t icm_pack_odr_fs(uint8_t fs_sel, uint8_t odr_code);
uint8_t icm_pack_ui_filt_bw(uint8_t gyro_code, uint8_t accel_code);
uint8_t icm_pack_ui_filt_ord(uint8_t gyro_ord, uint8_t accel_ord);
uint8_t icm_pack_aaf_delt_accel(uint8_t delt);    /* bank2 0x03[6:1] */
uint8_t icm_pack_aaf_delt_gyro(uint8_t delt);     /* bank1 0x0C[5:0] */
uint8_t icm_pack_aaf_deltsqr_hi(uint8_t bitshift, uint8_t deltsqr_hi4);
uint8_t icm_pack_aaf_deltsqr_hi4(unsigned deltsqr);
uint8_t icm_pack_intf_config0(int fifo_count_rec, int sensor_le, int fifo_count_le,
                              int hold_last, int uifs_cfg);
uint8_t icm_pack_fifo_config1(int accel, int gyro, int temp, int fsync, int hires, int resume);
uint8_t icm_pack_fifo_wm_hi(uint16_t wm);
uint8_t icm_pack_pwr_mgmt0(uint8_t gyro_mode, uint8_t accel_mode, int idle, int temp_dis);
uint8_t icm_pack_int_config(int int1_latched, int int1_push_pull, int int1_active_high);
uint8_t icm_pack_tmst_config(int tmst_en, int tmst_delta_en, int tmst_to_regs_en, int tmst_res);
uint8_t mpu_pack_config(uint8_t dlpf_cfg, uint8_t ext_sync_set, uint8_t fchoice_b);
uint8_t mpu_pack_accel_config2(uint8_t a_dlpfcfg, int accel_fchoice_b);
uint8_t mpu_pack_accel_config(uint8_t fs_sel);
uint8_t mpu_pack_gyro_config(uint8_t fs_sel, uint8_t fchoice_b);
uint8_t mpu_pack_fifo_en(int temp, int xg, int yg, int zg, int accel);
uint8_t mpu_pack_user_ctrl(int fifo_en, int fifo_reset, int i2c_mst_en);
uint8_t mpu_pack_pwr_mgmt_1(int sleep, uint8_t clksel, int device_reset);
uint8_t mpu_pack_int_enable(int wom_en, int fifo_oflow_en);

#ifdef __cplusplus
}
#endif

#endif /* IMU_REGS_H */
