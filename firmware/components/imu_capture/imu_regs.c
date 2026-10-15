/* imu_regs.c — the normative bring-up sequences from spec v2.1 S4, as data.
 *
 * Every entry cites the spec step it comes from in its `field` string. Three
 * classes of entry exist and the text says which one it is:
 *
 *   "spec S4.1 step N"                  packing taken straight from the spec's
 *                                       own bit positions (ACCEL_CONFIG0[7:5] etc)
 *   "spec S4.1 step N pos:unverified"   the spec states the FIELD VALUE but not
 *                                       its bit position; the position comes from
 *                                       the part's register map (DS-000347 S14 /
 *                                       RM-MPU-9250A-00 S4) and is asserted by
 *                                       tests/test_firmware_contract.py
 *   "firmware hardening (see ...)"      a byte the spec does NOT freeze but the
 *                                       contract requires — spelled out in the
 *                                       note under INTF_CONFIG0 below
 *
 * All positions were re-read from the datasheets while writing this file. Four
 * of them were WRONG in the first draft and are called out at their site:
 * FIFO_CONFIG1's bit order, PWR_MGMT0's bit order, GYRO_AAF_DELT's bit range
 * (it is [5:0], not the accelerometer's [6:1]), and the sensor-data endianness
 * that spec S4.1 step 3 omits while S5.3 requires little-endian frames.
 */
#include "imu_regs.h"

#include "imu_contract.h"

/* ---- field packers ------------------------------------------------------ */

uint8_t icm_pack_odr_fs(uint8_t fs_sel, uint8_t odr_code)
{
    /* ACCEL_CONFIG0 / GYRO_CONFIG0: [7:5] FS_SEL, [3:0] ODR (spec S4.1 step 4) */
    return (uint8_t)(((fs_sel & 0x07u) << 5) | (odr_code & 0x0Fu));
}

uint8_t icm_pack_ui_filt_bw(uint8_t gyro_code, uint8_t accel_code)
{
    /* GYRO_ACCEL_CONFIG0 (0x52): [7:4] ACCEL_UI_FILT_BW, [3:0] GYRO_UI_FILT_BW */
    return (uint8_t)(((accel_code & 0x0Fu) << 4) | (gyro_code & 0x0Fu));
}

uint8_t icm_pack_ui_filt_ord(uint8_t gyro_ord, uint8_t accel_ord)
{
    /* 0x51 GYRO_UI_FILT_ORD [3:2]; 0x53 ACCEL_UI_FILT_ORD [4:3] (spec S4.1 step 5) */
    return (uint8_t)(((accel_ord & 0x03u) << 3) | ((gyro_ord & 0x03u) << 2));
}

uint8_t icm_pack_aaf_delt_accel(uint8_t delt)
{
    /* bank 2 reg 0x03 (ACCEL_CONFIG_STATIC2): [6:1] ACCEL_AAF_DELT (DS S14.68) */
    return (uint8_t)((delt & 0x3Fu) << 1);
}

uint8_t icm_pack_aaf_delt_gyro(uint8_t delt)
{
    /* bank 1 reg 0x0C (GYRO_CONFIG_STATIC3): [5:0] GYRO_AAF_DELT (DS S14.66).
     * NOT the accelerometer's [6:1] — sharing one packer between the two shifts
     * the corner and the rejection figure the mode was chosen for. */
    return (uint8_t)(delt & 0x3Fu);
}

uint8_t icm_pack_aaf_deltsqr_hi(uint8_t bitshift, uint8_t deltsqr_hi4)
{
    /* 0x05 / 0x0E: [7:4] AAF_BITSHIFT, [3:0] AAF_DELTSQR[11:8].
     * DELTSQR=288 needs its high nibble (0x1) written here; 81 does not. */
    return (uint8_t)(((bitshift & 0x0Fu) << 4) | (deltsqr_hi4 & 0x0Fu));
}

uint8_t icm_pack_aaf_deltsqr_hi4(unsigned deltsqr)
{
    return (uint8_t)((deltsqr >> 8) & 0x0Fu);
}

uint8_t icm_pack_intf_config0(int fifo_count_rec, int sensor_le, int fifo_count_le,
                              int hold_last, int uifs_cfg)
{
    /* INTF_CONFIG0 (0x4C), reset 0x30 — DS-000347 S14.34:
     *   [7] FIFO_HOLD_LAST_DATA_EN  [6] FIFO_COUNT_REC  [5] FIFO_COUNT_ENDIAN
     *   [4] SENSOR_DATA_ENDIAN      [3:2] reserved      [1:0] UI_SIFS_CFG
     *
     * WHY THIS BYTE IS FIRMWARE HARDENING AND NOT JUST THE SPEC'S STEP 3.
     * The reset value 0x30 (= 00 11 0000b) leaves BOTH endian fields BIG, but the
     * IMUCAP2 frame spec S5.3 freezes is LITTLE endian. Spec S4.1 step 3 names
     * UI_SIFS_CFG, FIFO_COUNT_REC and FIFO_HOLD_LAST_DATA_EN and says nothing
     * about endianness, so a firmware that follows step 3 literally and leaves
     * [5:4] alone records byte-swapped samples — which decode into plausible
     * numbers of the wrong magnitude, with nothing in the file to flag it. This
     * writer therefore sets FIFO_COUNT_REC=1, FIFO_COUNT_ENDIAN=little (1),
     * SENSOR_DATA_ENDIAN=little (1), and UI_SIFS_CFG=10b ("Disable SPI", i.e.
     * select the I2C host interface), giving 0x72. */
    uint8_t v = 0;
    if (hold_last) v |= 1u << 7;
    if (fifo_count_rec) v |= 1u << 6;
    if (fifo_count_le) v |= 1u << 5;
    if (sensor_le) v |= 1u << 4;
    v |= (uint8_t)(uifs_cfg & 0x03u);
    return v;
}

uint8_t icm_pack_fifo_config1(int accel, int gyro, int temp, int fsync, int hires,
                              int resume)
{
    /* FIFO_CONFIG1 (0x5F), reset 0x00 — DS-000347 S14.45:
     *   [0] FIFO_ACCEL_EN  [1] FIFO_GYRO_EN  [2] FIFO_TEMP_EN
     *   [3] FIFO_TMST_FSYNC_EN  [4] FIFO_HIRES_EN  [5] FIFO_WM_GT_TH
     *   [6] FIFO_RESUME_PARTIAL_RD  [7] reserved
     * (the first draft had accel/hires at the top of the byte, which would have
     * enabled the wrong packet fields entirely.) */
    uint8_t v = 0;
    if (accel) v |= 1u << 0;
    if (gyro) v |= 1u << 1;
    if (temp) v |= 1u << 2;
    if (fsync) v |= 1u << 3;
    if (hires) v |= 1u << 4;
    if (resume) v |= 1u << 6;
    return v;
}

uint8_t icm_pack_fifo_wm_hi(uint16_t wm)
{
    /* FIFO_CONFIG3 (0x61) [3:0] = FIFO_WM[11:8]; watermark zero is forbidden */
    return (uint8_t)((wm >> 8) & 0x0Fu);
}

uint8_t icm_pack_pwr_mgmt0(uint8_t gyro_mode, uint8_t accel_mode, int idle, int temp_dis)
{
    /* PWR_MGMT0 (0x4E), reset 0x00 — DS-000347 S14.36:
     *   [1:0] ACCEL_MODE  [3:2] GYRO_MODE  [4] IDLE  [5] TEMP_DIS
     * Both sensors to Low Noise (11b) is 0x0F. (The first draft had the two mode
     * fields at [7:6]/[5:4], which would have left both sensors OFF.) */
    uint8_t v = (uint8_t)(accel_mode & 0x03u);
    v |= (uint8_t)((gyro_mode & 0x03u) << 2);
    if (idle) v |= 1u << 4;
    if (temp_dis) v |= 1u << 5;
    return v;
}

uint8_t icm_pack_int_config(int int1_latched, int int1_push_pull, int int1_active_high)
{
    /* INT_CONFIG (0x14), reset 0x00 — DS-000347 S14.3:
     *   [0] INT1_POLARITY  [1] INT1_DRIVE_CIRCUIT  [2] INT1_MODE
     * 0x12 gives INT1 latched (1), push-pull (1), active-high (1) — the spec's
     * S4.1 step 9 wiring, which is what makes the ESP32-S3 GPIO edge usable. */
    uint8_t v = 0;
    if (int1_active_high) v |= 1u << 0;
    if (int1_push_pull) v |= 1u << 1;
    if (int1_latched) v |= 1u << 2;
    return v;
}

uint8_t icm_pack_tmst_config(int tmst_en, int tmst_delta_en, int tmst_to_regs_en,
                             int tmst_res)
{
    /* TMST_CONFIG (0x54), reset 0x23 — DS-000347 S14.42:
     *   [0] TMST_EN  [2] TMST_DELTA_EN  [3] TMST_RES  [4] TMST_TO_REGS_EN
     * 0x0F = enable, delta, 1 us resolution, register read-back — every field
     * spec S4.1 step 7 asks for, which the reset value 0x23 does not give. */
    uint8_t v = 0;
    if (tmst_en) v |= 1u << 0;
    if (tmst_delta_en) v |= 1u << 2;
    if (tmst_res) v |= 1u << 3;
    if (tmst_to_regs_en) v |= 1u << 4;
    return v;
}

uint8_t mpu_pack_config(uint8_t dlpf_cfg, uint8_t ext_sync_set, uint8_t fchoice_b)
{
    /* CONFIG (0x1A): [2:0] DLPF_CFG, [5:3] EXT_SYNC_SET, [6] FIFO_MODE, [7:6] FCHOICE_B */
    return (uint8_t)((dlpf_cfg & 0x07u) | ((ext_sync_set & 0x07u) << 3) |
                     ((fchoice_b & 0x03u) << 6));
}

uint8_t mpu_pack_accel_config2(uint8_t a_dlpfcfg, int accel_fchoice_b)
{
    /* ACCEL_CONFIG2 (0x1D): [2:0] A_DLPFCFG, [3] ACCEL_FCHOICE_B */
    return (uint8_t)((a_dlpfcfg & 0x07u) | (accel_fchoice_b ? 0x08u : 0x00u));
}

uint8_t mpu_pack_accel_config(uint8_t fs_sel)
{
    /* ACCEL_CONFIG (0x1C): [4:3] ACCEL_FS_SEL */
    return (uint8_t)((fs_sel & 0x03u) << 3);
}

uint8_t mpu_pack_gyro_config(uint8_t fs_sel, uint8_t fchoice_b)
{
    /* GYRO_CONFIG (0x1B) — RM-MPU-9250A-00 S4.6 summary: [4:3] GYRO_FS_SEL,
     * [1:0] FCHOICE_B. The field is the LOW two bits on this part, not [7:6];
     * FCHOICE_B=00 is what keeps the gyro DLPF active. */
    return (uint8_t)(((fs_sel & 0x03u) << 3) | (fchoice_b & 0x03u));
}

uint8_t mpu_pack_fifo_en(int temp, int xg, int yg, int zg, int accel)
{
    /* FIFO_EN (0x23) — RM S4.11: [3] ACCEL [4] TEMP_OUT [5] GYRO_XOUT
     * [6] GYRO_YOUT [7] GYRO_ZOUT. 0xF8 = accel+gyro+temp, all SLV_* off. */
    uint8_t v = 0;
    if (accel) v |= 1u << 3;
    if (temp) v |= 1u << 4;
    if (xg) v |= 1u << 5;
    if (yg) v |= 1u << 6;
    if (zg) v |= 1u << 7;
    return v;
}

uint8_t mpu_pack_user_ctrl(int fifo_en, int fifo_reset, int i2c_mst_en)
{
    /* USER_CTRL (0x6A) — RM S4.33: [2] FIFO_RST (auto-clears), [5] I2C_MST_EN,
     * [6] FIFO_EN. 0x44 = FIFO_EN + FIFO_RST (the one-shot clear). */
    uint8_t v = 0;
    if (fifo_reset) v |= 1u << 2;
    if (i2c_mst_en) v |= 1u << 5;
    if (fifo_en) v |= 1u << 6;
    return v;
}

uint8_t mpu_pack_pwr_mgmt_1(int sleep, uint8_t clksel, int device_reset)
{
    /* PWR_MGMT_1 (0x6B) — RM S4.34: [2:0] CLKSEL [5] CYCLE [6] SLEEP
     * [7] DEVICE_RESET (H_RESET). CLKSEL=1 means "auto select the best available
     * clock source: PLL if ready, else the internal oscillator" — the spec's
     * "PLL with gyro reference preferred". CLKSEL=0 would pin the part to its
     * 20 MHz internal oscillator and lose that. */
    uint8_t v = (uint8_t)(clksel & 0x07u);
    if (sleep) v |= 1u << 6;
    if (device_reset) v |= 1u << 7;
    return v;
}

uint8_t mpu_pack_int_enable(int wom_en, int fifo_oflow_en)
{
    /* INT_ENABLE (0x38) — RM S4.20: [5] WOM_EN, [4] FIFO_OFLOW_EN.
     * The MPU-9250 has NO FIFO watermark interrupt; FIFO_OFLOW is the only
     * FIFO-related interrupt it offers, so mode M polls the count instead. */
    uint8_t v = 0;
    if (fifo_oflow_en) v |= 1u << 4;
    if (wom_en) v |= 1u << 5;
    return v;
}

/* ---- the sequences ------------------------------------------------------ */

/* ICM-42688-P, mode A (4000 Hz, band 0-500 Hz). Spec S4.1. */
const imu_reg_op_t imu_seq_icm_a[] = {
    {ICM_NO_BANK, ICM_REG_DEVICE_CONFIG, 0x01, IMU_RESET_DELAY_ICM_US,
     "spec S4.1 step 1: DEVICE_CONFIG software reset, then poll INT_STATUS.RESET_DONE_INT"},
    {ICM_NO_BANK, 0x00, 0x00, 0, "reset poll: WHO_AM_I must read 0x47 (spec S4.1 step 1)"},
    {ICM_BANK_SEL_0, ICM_REG_REG_BANK_SEL, 0x00, 0, "spec S4.1 step 2: bank 0"},
    {ICM_NO_BANK, ICM_REG_INTF_CONFIG0, 0x72, 0,
     "FIFO_COUNT_REC=1, FIFO_COUNT_ENDIAN=little, SENSOR_DATA_ENDIAN=little, UI_SIFS_CFG=10b "
     "(see icm_pack_intf_config0: spec S4.1 step 3 omits the endian fields and the reset "
     "value 0x30 leaves them BIG while spec S5.3 frames are little endian)"},
    {ICM_NO_BANK, ICM_REG_INTF_CONFIG1, 0x01, 0,
     "spec S4.1 step 3: RTC_MODE=0 (no CLKIN) at [2], CLKSEL=01 (PLL when available) at [1:0]; "
     "reset 0x91 would give CLKSEL=01 too but with RTC_MODE=0 already set pos:unverified"},
    {ICM_NO_BANK, ICM_REG_PWR_MGMT0, 0x0F, IMU_LEAVE_OFF_DELAY_ICM_US,
     "spec S4.1 step 4: ACCEL_MODE=11 GYRO_MODE=11 (Low Noise), IDLE=0, TEMP_DIS=0"},
    {ICM_NO_BANK, ICM_REG_GYRO_CONFIG0, 0x04, 0,
     "spec S4.1 step 4: GYRO_FS_SEL=000 (+-2000 dps) GYRO_ODR=0100 (4000 Hz)"},
    {ICM_NO_BANK, ICM_REG_ACCEL_CONFIG0, 0x04, 0,
     "spec S4.1 step 4: ACCEL_FS_SEL=000 (+-16 g) ACCEL_ODR=0100 (4000 Hz)"},
    {ICM_NO_BANK, ICM_REG_GYRO_CONFIG1, 0x08, 0,
     "spec S4.1 step 5: GYRO_UI_FILT_ORD=10 (3rd order) at [3:2], reset 0x16 has 01b there"},
    {ICM_NO_BANK, ICM_REG_ACCEL_CONFIG1, 0x10, 0,
     "spec S4.1 step 5: ACCEL_UI_FILT_ORD=10 (3rd order) at [4:3], reset 0x0D has 01b there"},
    {ICM_NO_BANK, ICM_REG_GYRO_ACCEL_CONFIG0, 0x44, 0,
     "spec S4.1 step 5: UI_FILT_BW=0100 both nibbles (1048.1 Hz 3 dB at 4000 Hz)"},
    {ICM_BANK_SEL_2, ICM_BANK2_ACCEL_AAF_DELT, 0x22, 0,
     "spec S4.1 step 6: ACCEL_AAF_DELT=17 (785 Hz 3 dB) at [6:1]"},
    {ICM_NO_BANK, ICM_BANK2_ACCEL_AAF_DELTSQR, 0x20, 0,
     "spec S4.1 step 6: ACCEL_AAF_DELTSQR[7:0]=288 -> 0x20"},
    {ICM_NO_BANK, ICM_BANK2_ACCEL_AAF_DELTSQR_HI, 0x71, 0,
     "spec S4.1 step 6: ACCEL_AAF_BITSHIFT=7 at [7:4], DELTSQR[11:8]=1 at [3:0] (288=0x120)"},
    {ICM_BANK_SEL_1, ICM_BANK1_GYRO_AAF_DELT, 0x11, 0,
     "spec S4.1 step 6: GYRO_AAF_DELT=17 (785 Hz) at [5:0] -- a different bit range from the accel's"},
    {ICM_NO_BANK, ICM_BANK1_GYRO_AAF_DELTSQR, 0x20, 0,
     "spec S4.1 step 6: GYRO_AAF_DELTSQR[7:0]=288 -> 0x20"},
    {ICM_NO_BANK, ICM_BANK1_GYRO_AAF_DELTSQR_HI, 0x71, 0,
     "spec S4.1 step 6: GYRO_AAF_BITSHIFT=7 at [7:4], DELTSQR[11:8]=1 at [3:0]"},
    {ICM_BANK_SEL_0, ICM_REG_REG_BANK_SEL, 0x00, 0, "spec S4.1 step 7: back to bank 0"},
    {ICM_NO_BANK, ICM_REG_TMST_CONFIG, 0x0F, 0,
     "spec S4.1 step 7: TMST_EN=1 TMST_RES=0 (1 us) TMST_DELTA_EN=1 TMST_TO_REGS_EN=1; "
     "reset 0x23 has none of them as asked"},
    {ICM_NO_BANK, ICM_REG_FIFO_CONFIG1, 0x47, 0,
     "spec S4.1 step 8: FIFO_ACCEL_EN=1 FIFO_GYRO_EN=1 FIFO_TEMP_EN=1 FSYNC=0 HIRES=0 RESUME=1"},
    {ICM_NO_BANK, ICM_REG_FIFO_CONFIG2, 0x20, 0, "spec S4.1 step 8: FIFO_WM[7:0]=32 records"},
    {ICM_NO_BANK, ICM_REG_FIFO_CONFIG3, 0x00, 0,
     "spec S4.1 step 8: FIFO_WM[11:8]=0 (the datasheet forbids a zero watermark; 32 records is not zero)"},
    {ICM_NO_BANK, ICM_REG_INT_CONFIG, 0x12, 0,
     "spec S4.1 step 9: INT1 latched, push-pull, active-high (reset 0x00 is pulsed/open-drain/active-low)"},
    {ICM_NO_BANK, ICM_REG_INT_SOURCE0, 0x04, 0,
     "spec S4.1 step 9: FIFO_THS_INT1_EN=1 at [2]"},
    {ICM_NO_BANK, 0x00, 0x00, IMU_GYRO_SETTLE_ICM_US,
     "spec S4.1 step 4: wait >= 45 ms for the gyro drive to settle before trusting data"},
    {0, 0, 0, 0, NULL}, /* terminator */
};
const size_t imu_seq_icm_a_n = sizeof imu_seq_icm_a / sizeof imu_seq_icm_a[0] - 1;

/* ICM-42688-P, mode B (2000 Hz, band 0-250 Hz). Spec S3.4.1; same order as S4.1. */
const imu_reg_op_t imu_seq_icm_b[] = {
    {ICM_NO_BANK, ICM_REG_DEVICE_CONFIG, 0x01, IMU_RESET_DELAY_ICM_US,
     "spec S4.1 step 1: DEVICE_CONFIG software reset, then poll INT_STATUS.RESET_DONE_INT"},
    {ICM_NO_BANK, 0x00, 0x00, 0, "reset poll: WHO_AM_I must read 0x47 (spec S4.1 step 1)"},
    {ICM_BANK_SEL_0, ICM_REG_REG_BANK_SEL, 0x00, 0, "spec S4.1 step 2: bank 0"},
    {ICM_NO_BANK, ICM_REG_INTF_CONFIG0, 0x72, 0,
     "FIFO_COUNT_REC=1, FIFO_COUNT_ENDIAN=little, SENSOR_DATA_ENDIAN=little, UI_SIFS_CFG=10b "
     "(see icm_pack_intf_config0)"},
    {ICM_NO_BANK, ICM_REG_INTF_CONFIG1, 0x01, 0,
     "spec S4.1 step 3: RTC_MODE=0 (no CLKIN) at [2], CLKSEL=01 (PLL when available) at [1:0] pos:unverified"},
    {ICM_NO_BANK, ICM_REG_PWR_MGMT0, 0x0F, IMU_LEAVE_OFF_DELAY_ICM_US,
     "spec S4.1 step 4: ACCEL_MODE=11 GYRO_MODE=11 (Low Noise), IDLE=0, TEMP_DIS=0"},
    {ICM_NO_BANK, ICM_REG_GYRO_CONFIG0, 0x05, 0,
     "spec S3.4.1: GYRO_FS_SEL=000 (+-2000 dps) GYRO_ODR=0101 (2000 Hz)"},
    {ICM_NO_BANK, ICM_REG_ACCEL_CONFIG0, 0x05, 0,
     "spec S3.4.1: ACCEL_FS_SEL=000 (+-16 g) ACCEL_ODR=0101 (2000 Hz)"},
    {ICM_NO_BANK, ICM_REG_GYRO_CONFIG1, 0x08, 0,
     "spec S4.1 step 5: GYRO_UI_FILT_ORD=10 (3rd order) at [3:2]"},
    {ICM_NO_BANK, ICM_REG_ACCEL_CONFIG1, 0x10, 0,
     "spec S4.1 step 5: ACCEL_UI_FILT_ORD=10 (3rd order) at [4:3]"},
    {ICM_NO_BANK, ICM_REG_GYRO_ACCEL_CONFIG0, 0x44, 0,
     "spec S4.1 step 5: UI_FILT_BW=0100 both nibbles (524.0 Hz 3 dB at 2000 Hz)"},
    {ICM_BANK_SEL_2, ICM_BANK2_ACCEL_AAF_DELT, 0x12, 0,
     "spec S3.4.1: ACCEL_AAF_DELT=9 (394 Hz 3 dB) at [6:1]"},
    {ICM_NO_BANK, ICM_BANK2_ACCEL_AAF_DELTSQR, 0x51, 0,
     "spec S3.4.1: ACCEL_AAF_DELTSQR[7:0]=81 -> 0x51"},
    {ICM_NO_BANK, ICM_BANK2_ACCEL_AAF_DELTSQR_HI, 0x90, 0,
     "spec S3.4.1: ACCEL_AAF_BITSHIFT=9 at [7:4], DELTSQR[11:8]=0"},
    {ICM_BANK_SEL_1, ICM_BANK1_GYRO_AAF_DELT, 0x09, 0,
     "spec S3.4.1: GYRO_AAF_DELT=9 (394 Hz) at [5:0]"},
    {ICM_NO_BANK, ICM_BANK1_GYRO_AAF_DELTSQR, 0x51, 0,
     "spec S3.4.1: GYRO_AAF_DELTSQR[7:0]=81 -> 0x51"},
    {ICM_NO_BANK, ICM_BANK1_GYRO_AAF_DELTSQR_HI, 0x90, 0,
     "spec S3.4.1: GYRO_AAF_BITSHIFT=9 at [7:4], DELTSQR[11:8]=0"},
    {ICM_BANK_SEL_0, ICM_REG_REG_BANK_SEL, 0x00, 0, "spec S4.1 step 7: back to bank 0"},
    {ICM_NO_BANK, ICM_REG_TMST_CONFIG, 0x0F, 0,
     "spec S4.1 step 7: TMST_EN=1 TMST_RES=0 (1 us) TMST_DELTA_EN=1 TMST_TO_REGS_EN=1"},
    {ICM_NO_BANK, ICM_REG_FIFO_CONFIG1, 0x47, 0,
     "spec S4.1 step 8: FIFO_ACCEL_EN=1 FIFO_GYRO_EN=1 FIFO_TEMP_EN=1 FSYNC=0 HIRES=0 RESUME=1"},
    {ICM_NO_BANK, ICM_REG_FIFO_CONFIG2, 0x20, 0, "spec S4.1 step 8: FIFO_WM[7:0]=32 records"},
    {ICM_NO_BANK, ICM_REG_FIFO_CONFIG3, 0x00, 0,
     "spec S4.1 step 8: FIFO_WM[11:8]=0 (32 records is non-zero, as the datasheet requires)"},
    {ICM_NO_BANK, ICM_REG_INT_CONFIG, 0x12, 0,
     "spec S4.1 step 9: INT1 latched, push-pull, active-high"},
    {ICM_NO_BANK, ICM_REG_INT_SOURCE0, 0x04, 0,
     "spec S4.1 step 9: FIFO_THS_INT1_EN=1 at [2]"},
    {ICM_NO_BANK, 0x00, 0x00, IMU_GYRO_SETTLE_ICM_US,
     "spec S4.1 step 4: wait >= 45 ms for the gyro drive to settle before trusting data"},
    {0, 0, 0, 0, NULL}, /* terminator */
};
const size_t imu_seq_icm_b_n = sizeof imu_seq_icm_b / sizeof imu_seq_icm_b[0] - 1;

/* MPU-9250, mode M (1000 Hz, band 0-150 Hz). Spec S4.2. */
const imu_reg_op_t imu_seq_mpu_m[] = {
    {IMU_NO_BANK, MPU_REG_PWR_MGMT_1, 0x80, IMU_RESET_DELAY_MPU_US,
     "spec S4.2 step 1: DEVICE_RESET=1; wait >= 100 ms; WHO_AM_I must then read 0x71"},
    {IMU_NO_BANK, 0x00, 0x00, 0, "reset poll: WHO_AM_I must read 0x71 (spec S4.2 step 1)"},
    {IMU_NO_BANK, MPU_REG_PWR_MGMT_1, 0x01, 0,
     "spec S4.2 step 2: SLEEP=0, CLKSEL=001 (auto: PLL if ready, else internal oscillator)"},
    {IMU_NO_BANK, MPU_REG_PWR_MGMT_2, 0x00, IMU_GYRO_SETTLE_MPU_US,
     "spec S4.2 step 2: all accel and gyro axes on; then wait >= 35 ms gyro start-up"},
    {IMU_NO_BANK, MPU_REG_SMPLRT_DIV, 0x00, 0,
     "spec S4.2 step 3: SMPLRT_DIV=0 -> 1 kHz; only effective while the DLPF is active"},
    {IMU_NO_BANK, MPU_REG_CONFIG, 0x01, 0,
     "spec S4.2 step 4: DLPF_CFG=1 (gyro 184 Hz 3 dB), FCHOICE_B=00 at [1:0], EXT_SYNC_SET=000"},
    {IMU_NO_BANK, MPU_REG_ACCEL_CONFIG2, 0x07, 0,
     "spec S4.2 step 4: ACCEL_FCHOICE_B=0, A_DLPFCFG=7 (Dec2, 420 Hz 3 dB) -- "
     "bypassing the accel DLPF is what forbids the 0-500 Hz claim"},
    {IMU_NO_BANK, MPU_REG_ACCEL_CONFIG, 0x18, 0,
     "spec S4.2 step 5: ACCEL_FS_SEL=11 (+-16 g) at [4:3]"},
    {IMU_NO_BANK, MPU_REG_GYRO_CONFIG, 0x18, 0,
     "spec S4.2 step 5: GYRO_FS_SEL=11 (+-2000 dps) at [4:3], FCHOICE_B=00 at [1:0]"},
    {IMU_NO_BANK, MPU_REG_FIFO_EN, 0xF8, 0,
     "spec S4.2 step 6: ACCEL=1 TEMP_OUT=1 GYRO_X/Y/ZOUT=1, all SLV_* = 0"},
    {IMU_NO_BANK, MPU_REG_USER_CTRL, 0x40, 0,
     "spec S4.2 step 6: FIFO_EN=1 at [6]; aux I2C master left disabled at [5]"},
    {IMU_NO_BANK, MPU_REG_USER_CTRL, 0x44, 0,
     "spec S4.2 step 6: FIFO_RST=1 at [2] for one write to clear the FIFO, then back to 0x40"},
    {IMU_NO_BANK, MPU_REG_I2C_MST_CTRL, 0x00, 0,
     "spec S4.2 step 7: auxiliary I2C master disabled, so the AK8963 cannot inject EXT_SENS_DATA"},
    {IMU_NO_BANK, MPU_REG_INT_ENABLE, 0x10, 0,
     "spec S4.2 step 6: FIFO_OFLOW_EN=1 at [4] as the error interrupt -- the MPU-9250 has NO "
     "FIFO watermark interrupt, so mode M polls FIFO_COUNTH/L instead"},
    {0, 0, 0, 0, NULL}, /* terminator */
};
const size_t imu_seq_mpu_m_n = sizeof imu_seq_mpu_m / sizeof imu_seq_mpu_m[0] - 1;

const imu_reg_op_t *imu_seq_for_mode(int mode_id, size_t *out_n)
{
    const imu_reg_op_t *seq = NULL;
    size_t n = 0;
    switch ((imu_mode_id_t)mode_id) {
    case IMU_MODE_A: seq = imu_seq_icm_a; n = imu_seq_icm_a_n; break;
    case IMU_MODE_B: seq = imu_seq_icm_b; n = imu_seq_icm_b_n; break;
    case IMU_MODE_M: seq = imu_seq_mpu_m; n = imu_seq_mpu_m_n; break;
    default: return NULL;
    }
    if (out_n != NULL) {
        *out_n = n;
    }
    return seq;
}
