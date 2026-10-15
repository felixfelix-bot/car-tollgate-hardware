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

## Cluster 3-6 landed

* `firmware/components/imu_capture/imu_logger.[ch]` — the sampling loop, HAL seam,
  rotation, flush, drop ledger, LED/state machine.
* `firmware/host/` — `fake_hal.[ch]` (simulated sensor with virtual time,
  injectable FIFO loss and a known jitter), `test_imu.c` (252 assertions),
  `selftest.c` (the 60 s capture + assertions), `Makefile`.
* `make -C firmware test` → **252 checks, 0 failed**, built with
  `-Wall -Wextra -Werror -Wshadow -Wconversion`.

### Bugs this cluster found and fixed (each was real, not a test artefact)

1. **A permanently failing sink spun forever.** `emit_frame` counted a short
   write and returned success, so a dead volume produced an infinitely long run
   that wrote nothing — indistinguishable from a healthy capture. Added
   `max_consecutive_host_overruns` (default one second of samples) which ends the
   run with `-8` and the error LED.
2. **`first_t_us` was never set**, so every achieved rate was wrong (973.7 Hz
   reported for a 1000 Hz capture). `note_frame` gated on `frames_written == 0`
   but was called after the counter incremented.
3. **The seq drop ledger was applied after the burst**, putting the gap on the
   wrong frame. Now advanced before the burst so the first frame after the loss
   carries both the gap and `FLAG_FIFO_OVERRUN`.
4. **`INTF_CONFIG0` had the endianny bits inverted** (`FIFO_COUNT_ENDIAN` is
   bit 5, `SENSOR_DATA_ENDIAN` bit 4 — not the reverse). Corrected byte is
   `0x70`: little sensor data, little FIFO count, REC count, I2C.
5. **The register sequences never wrote `REG_BANK_SEL`.** They carried the bank
   in the op's `bank` field, which a driver applies implicitly; a sequence that
   does not emit the selector cannot be audited. Now the selector is an explicit
   write and the test rejects any entry carrying an implicit bank.
6. **`TMST_CONFIG` was `0x0F`; the correct byte is `0x15`** (TMST_EN bit 0,
   DELTA bit 2, TO_REGS bit 4 — bit 3 is TMST_RES and 0 means 1 us).
7. **The C float formatter emitted `1e+03` for `1000.0`.** `%g` switches to
   exponential once the exponent reaches the precision, so the header would not
   have matched the Python codec's bytes. `fmt_double` now finds the shortest
   round-tripping significant-digit count and renders it with `%.*f`.
8. **NULL provenance was written as `"null"` rather than the JSON literal** and
   the wheel fragment was double-escaped into a string. Split into
   `fmt_json_string` (raw text -> escaped string, NULL -> null) and
   `fmt_json_fragment` (validated JSON object, NULL -> null).
