# PROGRESS — t_9ae1a5c7 (Identify IMU module from photos and lock the capture spec)

Worktree: /home/c03rad0r/worktrees/t_9ae1a5c7  branch: worker-base/t_9ae1a5c7
Repo: felixfelix-bot/car-tollgate-hardware (origin, https)

## Situation found at start (run 12)
Attempt 1 (run 5) capped out but left real work STAGED (never committed):
  A docs/imu_capture_spec.md (781 lines, spec v2.0)
  A hardware/imu_capture.py (514 lines, IMUCAP2 codec)
  A tests/test_imu_capture_spec.py (421 lines, 48 tests pass)
  M README.md
Content quality was high (datasheet-backed ICM-42688-P, ODR/filter math, mounting,
format) BUT it rested on a FALSE PREMISE stated as fact:
  §1 claimed "No photos are attached to this card or to any card on any board"
  and "photo identification was impossible and unnecessary".
Wrong: a DURABLE PHOTO-DERIVED IDENTIFICATION EXISTS —
  ~/repos/tollgate-esp32/docs/HARDWARE-INVENTORY.md
  ("Updated 2026-07-06 — all 13 photos analyzed via GLM-4.6V vision model"),
  commits b9559be / 83b9c56 / e2fe737. Names the IMU modules ACTUALLY ON THE
  BENCH, with per-module photo filenames, pins and I2C addresses.

## Decisive finding (the deliverable's core)
Bench IMU (photo-identified, in hand)  = MPU-9250    WHO_AM_I 0x71, I2C <=400 kHz
Carrier IMU (ratified D4, to be built) = ICM-42688-P WHO_AM_I 0x47, I2C 1 MHz
DIFFERENT PARTS. The card's implied target (0-500 Hz band, ODR >=1-2 kHz) is
IMPOSSIBLE on an MPU-9250:
  * its 4 kHz ODR exists only with the DLPF *bypassed* (Dec1, 1046 Hz 3dB, no
    published stopband) -> no documented anti-alias guarantee at 3500 Hz;
  * at its documented 1 kHz rate the first fold-in into a 0-500 Hz band is
    1000-500 = 500 Hz = the band edge itself.
So the spec freezes THREE modes, not two: A/B on the carrier part, M (0-150 Hz)
on the bench part, plus a WHO_AM_I identity guard that aborts on a mismatch.

## Datasheets fetched + verified (dev/shm/imu2)
  ICM-42688-P DS-000347 r1.8   1855049 B  sha256 7f904aec...d3fc9 (matches prior claim)
  PS-MPU-9250A-01 r1.1          962503 B  Web Archive
  RM-MPU-9250A-00 r1.6          977841 B  Web Archive
  MPU-6500 docs: NOT retrievable from the Web Archive (404 + CDX) -> recorded as
  an explicit source gap in spec S10, with the assumption labelled in S1.1.

## Work completed
1. [x] Recovered + verified prior staged work (48 tests passed)
2. [x] Fetched + extracted all three vendor datasheets; verified the ICM sha256
3. [x] hardware/imu_capture.py: MPU-9250 constants, mode "M", MODE_SENSOR,
        MODE_I2C_HZ, WHO_AM_I map, check_identity()/IdentityMismatch, sensor-aware
        make_synthetic, band_hz+i2c_hz in provenance, SPEC_VERSION 2.1
4. [x] Spec v2.1: rewrote S1 (two tiers, photo record cited, false claim removed),
        added S2.4 guard, S3.5 mode M + impossibility proof, S3.7.2 MPU FIFO budget,
        S4.2 MPU register sequence, S7.4 mag out of scope, per-part temp formulas,
        S9/S10/S11/S12 updated
5. [x] 17 new tests (65 total, was 48): MPU datasheet tables, mode M config,
        impossibility pins, guard accept/refuse, provenance-vs-mode, round trip,
        spec-still-names-both-parts anti-regression
6. [x] README.md corrected (false claim removed, two-sensor framing)
7. [x] Verification: 65 tests pass, 95% coverage, tools/check_decisions.py OK,
        codec self-check OK, live mode-M capture round trip + FFT tone recovery
8. [x] Committed + pushed

## Numbers that matter
  mode A  4000 Hz band 500 Hz  ICM-42688-P  I2C 1 MHz   96 kB/s  8.294 GB/day
  mode B  2000 Hz band 250 Hz  ICM-42688-P  I2C 1 MHz   48 kB/s  4.147 GB/day
  mode M  1000 Hz band 150 Hz  MPU-9250    I2C 400 kHz 24 kB/s  2.074 GB/day
  rotation: 64 MiB segments -> 11.7 / 23.3 / 46.6 min (uint32 us wraps at 71.6 min)
