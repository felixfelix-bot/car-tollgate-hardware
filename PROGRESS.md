# CTG-9 on-car IMU logger — progress

Card `t_752a0d0a` (board `car-tollgate`). Worktree `~/worktrees/t_752a0d0a`,
branch `worker-base/t_752a0d0a`.

## Resume map

| # | Cluster | State |
|---|---|---|
| 1 | Read spec v2.1 + reference codec, freeze firmware numbers | done |
| 2 | Portable contract core (`imu_contract.[ch]`, `imu_regs.[ch]`) | in progress |
| 3 | Stream loop + buffered sink + rotation + drop detection | pending |
| 4 | Start/stop control + LED status state machine | pending |
| 5 | ESP32-S3 app (i2c_master bus, gpio) + IDF build with ELF | pending |
| 6 | Host test harness: fake device asserts the exact register sequence | pending |
| 7 | 60 s self-test: achieved rate / jitter / drops, decoded by the Python codec | pending |
| 8 | `tools/imu_decode.py` decoder + pytest contract test | pending |
| 9 | Wiring notes, README, REPORT, commit + push | pending |

## Findings so far

* **No IMU hardware on this host.** `sudo` I2C sweep of all 14 `/dev/i2c-*` buses
  finds no device at 0x68/0x69 and no part answering 0x75; the only hits are
  0x40/0x4C on i2c-1 (the SMBus/I801, not an IMU). No `/dev/ttyUSB*`/`ttyACM*`,
  no `/dev/video*`, no ESP32 attached. So the 60 s bench capture at the spec'd
  rate cannot be run against real silicon here — it is run against the firmware's
  own host-buildable core with a simulated sensor, and the report says exactly
  that. Bus identification for the real bench module is carried as an explicit
  open item, not assumed.
* **The bench module's bus speed is unbounded by evidence.** Spec S2.3.2 says the
  MPU-9250 breakout's I2C must be <= 400 kHz, but nothing on this host can
  confirm which bus/header the module is on, so `MODE_I2C_HZ` for mode M is used
  only to configure the ESP32-S3 master for that tier; the host simulator does not
  exercise bus timing at all.
* **Four register-position bugs found by re-reading the datasheets** (the spec
  states field VALUES for these, not positions). Each is called out at its site in
  `firmware/components/imu_capture/imu_regs.c`:
  1. `FIFO_CONFIG1` bit order was inverted — accel is bit 0, hires bit 4.
  2. `PWR_MGMT0` bit order was wrong — accel mode at [1:0], gyro at [3:2]; the
     first draft wrote both sensors OFF.
  3. `GYRO_AAF_DELT` is [5:0] on bank 1, not the accelerometer's [6:1] on bank 2.
  4. **`SENSOR_DATA_ENDIAN` is not specified at all**: INTF_CONFIG0's reset value
     0x30 leaves both endian fields BIG, while IMUCAP2 frames (spec S5.3) are
     LITTLE endian. Spec S4.1 step 3 never mentions endianness, so following it
     literally produces byte-swapped samples that decode into plausible wrong
     numbers. The firmware writes 0x72 (little sensor + little FIFO count +
     REC count + UI_SIFS_CFG=10b) and the deviation is documented as firmware
     hardening.
* GYRO_CONFIG's FCHOICE_B is [1:0] (so 0x18, not 0x58) and CONFIG's DLPF_CFG=1
  is 0x01 — both as the spec's prose implies once the positions are checked.

## Evidence commands (as run)

```
python3 /home/c03rad0r/tmp/icm/gen_seq.py      # field packing recomputed from the corrected packers
sha256sum ds-000347 (ICM-42688-P rev 1.8)      # 7f904aec7d6b0b4a68b5038da1c29ee2919d895c07a1fd2c697346159e5d3fc9
```
