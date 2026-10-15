# CTG-9 — IMU capture specification (interface contract)

Card: `t_9ae1a5c7` (board `car-tollgate`) · supersedes the open question in
`t_7c5a719c` §4 "Candidate modules — AWAITING PHOTO IDENTIFICATION"
Status: **FROZEN — spec version 2.0.** Firmware and analysis both implement this
document; neither may re-derive it.

This is the contract every downstream card depends on:

| Consumer | Card | What it must take from here |
|---|---|---|
| on-car logger firmware | `t_752a0d0a` | §4 register configuration, §5 frame format, §6 file policy |
| FFT / imbalance pipeline | `t_00f126bd` | §5 frame format, §7 units and axis mapping |
| ContextVM exposure | `t_6fc86c46` | §5 header fields, §8 provenance |
| bench validation | `t_fbd7baa6` | §3 band, §7 mounting, §8.3 tolerances |
| carrier PCB (CTG-11) | `t_64d238d1` / `t_eaba2c97` | §2 wiring, §5 sensor configuration |

---

## 0. Summary of the frozen decisions

There are **two sensors in this project, and they are not interchangeable.** That
is the central result of this card, and everything below is organised around it.

| | **Bench tier — what exists today** | **Carrier tier — what gets built** |
|---|---|---|
| Part | **TDK InvenSense MPU-9250**, 6-axis (+AK8963 mag) | **TDK InvenSense ICM-42688-P**, 6-axis |
| Status | physically in hand, identified from photographs | ratified for the CTG-11 carrier PCB (D4) |
| `WHO_AM_I` (reg 117 / 0x75) | **0x71** | **0x47** |
| Modes | **M** — 1000 Hz, band 0–150 Hz | **A** (4000 Hz, 0–500 Hz), **B** (2000 Hz, 0–250 Hz) |
| Source | `~/repos/tollgate-esp32/docs/HARDWARE-INVENTORY.md` §6 + datasheets | `DECISIONS.md` D4 / `decisions.json` |

| # | Decision | Value | Basis |
|---|---|---|---|
| 1 | Carrier sensor (D4) | **ICM-42688-P**, 6-axis, I2C | ratified decision record `t_eaba2c97` | §2 |
| 2 | Bench sensor (photo-identified) | **MPU-9250**, 6-axis + AK8963 | hardware inventory, 13 photos, vision model | §1.1 |
| 3 | Bus / address | I2C, 7-bit **0x68** (`AD0`→GND); **1 MHz** on an ICM-42688-P, **400 kHz** on an MPU-9250 | §2.2, §3.6, §4.6 |
| 4 | Carrier mode **A** (survey/burst, 0–500 Hz) | **ODR 4000 Hz**, accel ±16 g, gyro ±2000 dps, AAF 785 Hz (2nd order), UI 3rd-order 1048.1 Hz | §3.4 |
| 5 | Carrier mode **B** (long run, 0–250 Hz) | **ODR 2000 Hz**, accel ±16 g, gyro ±2000 dps, AAF 394 Hz (2nd order), UI 3rd-order 524.0 Hz | §3.4 |
| 6 | Bench mode **M** (0–150 Hz) | **ODR 1000 Hz**, accel ±16 g, gyro ±2000 dps, DLPF `A_DLPFCFG`=7 (Dec2, 420 Hz) | §3.6 |
| 7 | Timestamps | **mandatory**; uint32 microseconds from a monotonic host clock, zeroed at capture start | §3.5 |
| 8 | Container | **IMUCAP2** — one JSON header line + fixed 24-byte frames | §5 |
| 9 | Mount | rigid to the chassis, **not** the unsprung mass; +X forward, +Y right, +Z down | §7 |
| 10 | Wheel model | `f_rot = v / (π·D)`, orders, not raw Hz | §3.3 |

**The single most important consequence:** the card's implied target — a 0–500 Hz
band with "at least 1–2 kHz" ODR — is **achievable on the carrier part and
impossible on the bench part**. An MPU-9250 cannot offer a documented anti-alias
guarantee at 500 Hz (§3.6). The spec therefore freezes **three** modes, not two,
and requires the firmware to refuse to start if `WHO_AM_I` disagrees with the mode
(§2.4). Silently recording a 0–500 Hz run from an MPU-9250 would produce a
plausible, wrong answer.

### Assumptions I had to make (marked ⚠ everywhere they matter)

* ⚠ **The photographs are not on this host, but their analysis is.** No image file
  and no camera device exist here (§1.2), so I could not re-inspect the pixels.
  What *does* exist is the **durable record of that analysis** — a committed
  inventory that names each module, its chip, its pins, its I²C address and the
  exact photo filenames it was derived from. I treat that record as the
  identification and cite it; I flag the one entry where the record itself is
  internally inconsistent (§1.1). *An earlier draft of this spec wrongly asserted
  that no photographs existed at all — see §12.*
* ⚠ **MPU-9250 vs MPU-6500 cannot be resolved from the record, and I assumed
  MPU-9250.** The inventory says *"MPU-9250 (or MPU-6500 variant)"*. The two are
  register-compatible and share `WHO_AM_I` = 0x71, sensitivity and ODR behaviour;
  they differ in the AK8963 magnetometer (9250 only) and in some noise figures. The
  assumption is labelled in §1.1 and changes nothing in the capture contract,
  because the magnetometer is not in scope (§7.4) and the magnetometer-affected
  specifications are not used. The MPU-6500 datasheets are **not retrievable from
  the Web Archive** — recorded honestly in §10.
* ⚠ **The card's stated tyre diameter and its stated rotation frequency are
  mutually inconsistent.** 100 km/h on a 0.60 m wheel is **14.74 Hz**, not ~7 Hz.
  The card's band conclusion still holds because the real frequency is *higher* than
  assumed; §3.2 records the discrepancy and the fence-post analysis.

---

## 1. Module identification

The card says *"Candidate modules awaiting photo identification"* and instructs:
*"if the photos are missing or illegible, state exactly which photo/angle or
measurement is needed and stop rather than guessing."*

**The identification is not open.** The photographs were taken and analysed on
2026-07-06, and the result was committed to the CTG-1 repo as a durable,
per-module inventory. The module sitting on the bench today is an **MPU-9250**.

### 1.1 The bench sensor is an MPU-9250 (photo-identified)

Source: `~/repos/tollgate-esp32/docs/HARDWARE-INVENTORY.md`, whose header reads
*"Updated 2026-07-06 — all 13 photos analyzed via GLM-4.6V vision model.
Cross-referenced with user's Google Search AI identifications."* Commits
`b9559be` → `83b9c56` → `e2fe737`. The IMU entry (§6 of that file) reads:

| Field | Value |
|---|---|
| Chip | **MPU-9250** (6-axis: 3-axis accel + 3-axis gyro, **plus** AK8963 magnetometer) |
| Photos it was derived from | `img_2dc5a67d07db.jpg`, `img_c7b0e63a1258.jpg` |
| Pins | VCC, GND, SCL, SDA, FSYNC, NCS, INT, **ADO**, ECL, EDA |
| Interface | I2C (**0x68** with ADO→GND, **0x69** with ADO→VCC) **and** SPI |
| Inventory verdict | *"BEST option for vibration analysis — high ODR, good noise performance"* |

That pin list is diagnostic on its own: `ADO`, `NCS` and `ECL`/`EDA` are the
MPU-92x5 family's pin names, and a breakout carrying them is a GY-521-class
MPU-9250/6500 board, not an ICM-42688-P or an ADXL345.

The same file lists two further IMU-class modules in the operator's parts bin:

| Module | Chip | Interface | Note |
|---|---|---|---|
| ADXL345 (also GY-291) | ADXL345 | I2C 0x53 / SPI | accelerometer only, no gyro |
| GY-271 / HW-246 | HMC5883L/QMC5883L | I2C | magnetometer (compass) only |

Neither is a 6-axis IMU, so neither can be the primary vibration sensor; they are
recorded here only so a later reader does not re-litigate them.

⚠ **Recorded inconsistency (reported, not papered over).** The inventory's own
header says *"12 modules identified from 13 photos"* in one commit and *"17 items
identified via vision"* in another, and its §8 entry for the ADXL345/GY-291 pair
carries the annotation *"Google AI says this is a BMP180 barometric pressure
sensor. Vision model says same as ADXL345. **Need user confirmation.**"* That
uncertainty is about the **ADXL345/GY-291** entry, **not** the MPU-9250 entry — the
MPU-9250 identification is what the inventory *recommends for exactly this task*.
I flag it because the card asks for assumptions to be marked, and because a future
reader will otherwise find that comment and wonder whether it taints §1.1. It does
not; but it does mean the inventory is a human-reviewed record, not a datasheet.

⚠ **MPU-9250 vs MPU-6500: unresolved, assumed MPU-9250.** The inventory writes
*"MPU-9250 (or MPU-6500 variant)"* because the two dies are pin- and
register-compatible (the 9250 adds the AK8963 magnetometer on an auxiliary I²C
bus). This assumption is safe for this contract:

* both parts answer `WHO_AM_I` (reg 117 / 0x75) with **0x71**;
* both share the accel/gyro ODR, DLPF, FSR and sensitivity tables used in §3.6;
* the magnetometer is **not** in CTG-9 scope (the diagnosis is wheel imbalance on
  raw accel), so the one real functional difference is unused;
* the MPU-9250's published noise figure (300 µg/√Hz) is the *worse* of the two, so
  using it makes the mode-M band decision conservative rather than optimistic.

### 1.2 What could **not** be done here, and what would close it

No image file and no camera exist on this host. Verified, so that this is a
measured statement and not an assumption:

| Where | Result |
|---|---|
| `task_attachments` for `t_9ae1a5c7` | empty |
| `task_attachments` for every board under `~/.hermes/kanban/boards/` | no image attachments of any kind |
| `~/Pictures`, `~/Documents`, `~/Desktop` | absent |
| `~/Downloads` | 7 files, all Semtech LR11xx datasheets — no images |
| every `*.jpg/.jpeg/.png/.heic/.webp` under `~/.hermes`, `~/repos`, `~` (bounded) and `/mnt` (depth ≤ 7) | web assets, CI frames, RF plots, brand logos — **no** IMU, breakout or PCB photos |
| camera hardware | **absent**: no `/dev/video*`, no `/sys/class/video4linux`, no `v4l2-ctl`; `lsusb` shows hubs, a BT radio, a microSD reader and a USB audio/HID device only |

So the *pixels* of the 13 photographs are not retrievable from this host — but the
**analysis of them is**, and that is what the identification rests on. To close the
remaining physical gap (which is a *confirmation*, not an open question) one of
these is enough, and neither blocks firmware:

1. **A photo of each side of the board actually on the bench**, ≥120 px across the
   largest chip, all silkscreen legible (board name, pin labels, chip marking).
   Look for the words `MPU-9250`, `MPU-6500` or a `GY-521`-style board name.
2. **A 30-second measurement**: power the module and read register 117 (`0x75`).
   `0x71` confirms the MPU-92x5 family. Then read `ACCEL_CONFIG2` (`0x1D`) and
   `CONFIG` (`0x1A`) reset values to confirm the register map is the 9250/6500 one.

Until then, firmware must **read `WHO_AM_I` and refuse to run if it is not the
value the selected mode expects** — `0x71` for mode M, `0x47` for modes A/B (§2.4).
That converts a wrong part into a loud startup failure instead of a capture whose
LSB scales are the wrong part's.

### 1.3 The carrier sensor is separately decided (and is a different part)

`~/repos/car-tollgate-hardware/DECISIONS.md` D4 (commit `33af301`, ratified
2026-09-27) and the machine-readable `decisions.json` say, for the **custom CTG-11
carrier PCB**:

> **D4 — IMU: ICM-42688-P, 6-axis, I2C** … SPI left unpopulated; carrier straps I2C
> addr. Alt (not chosen): `LSM6DS3TR-C` (cheaper); `LSM9DS1` (9-axis) only if
> magnetometer heading enters CTG-9 scope — *"CTG-9 is wheel-imbalance FFT on raw
> accel, which does not need a compass"*.

That record is enforced by `tests/test_decisions_ledger.py` and
`tools/check_decisions.py`, and it is the same repo the CTG-11 PCB will be built
from. So the project has, correctly, **two** sensors: an MPU-9250 on the bench
today and an ICM-42688-P on the board that will be built. The earlier draft of this
spec adopted D4 as *the* answer and closed the identification question on that
basis. That was half right — D4 is the carrier answer — but it is **not** the
answer to *"what is on the bench"*, and the difference is material: the bench part
cannot carry the 0–500 Hz band the card asks for (§3.6). This spec now covers both
tiers and names them explicitly.

---

## 2. Sensor contract

### 2.1 Parts, datasheets and electrical

Two parts are supported, one per tier (§0). Datasheets used:

| Tier | Part | Document | Revision |
|---|---|---|---|
| Carrier | **TDK InvenSense ICM-42688-P** | `DS-000347` | 1.8 (Rev. Date 07/27/2023), 112 pp. |
| Bench | **TDK InvenSense MPU-9250** | `PS-MPU-9250A-01` (product spec) + `RM-MPU-9250A-00` (register map) | 1.1 (06/20/2016) + 1.6 (01/07/2015) |

§10 gives the exact source and re-fetch instructions for each.

#### 2.1.1 ICM-42688-P (carrier tier — modes A and B)

| Parameter | Value | Source |
|---|---|---|
| Package | 2.5 × 3.0 × 0.91 mm, 14-pin LGA | Ordering Information, p.1 |
| VDD | 1.71 – 3.6 V (typ. 1.8 V) | Table 3, p.13 |
| VDDIO | 1.71 – 3.6 V (typ. 1.8 V) — **independent IO supply** | Table 3, p.13 |
| Absolute max VDD/VDDIO | −0.5 to +4 V | Table 9, p.19 |
| Current, 6-axis low-noise | 0.88 mA typ. | Table 3, p.13 |
| Current, sleep | 7.5 µA typ. | Table 3, p.13 |
| Temperature range | −40 to +85 °C (specified) | Table 3, p.13 |
| Decoupling | 0.1 µF (VDD), 2.2 µF (VDDIO), 10 nF | Fig. 6, §4.2 |
| Host interfaces | I3C<sup>SM</sup> 12.5 MHz, **I2C 1 MHz**, SPI 24 MHz | p.1, §3.4/§3.5 |
| I2C pull-ups | required; 10 kΩ suggested | §4.2 note |

#### 2.1.2 MPU-9250 (bench tier — mode M)

| Parameter | Value | Source |
|---|---|---|
| Package | 3 × 3 × 0.9 mm, 24-pin QFN (GY-521-class breakout on the bench) | PS §4.1 |
| VDD | **2.4 – 3.6 V** (typ. 2.5 V) — *narrower than the ICM's 1.71 V floor* | PS Table 3, §3.4.1 |
| VDDIO | 1.71 V – VDD | PS Table 3, §3.4.1 |
| Current, 6-axis (accel+gyro, no DMP) | **3.4 mA** typ. @ 1 kHz gyro / 4 kHz accel — **~3.9× the ICM's 0.88 mA** | PS Table 3, §3.4.1 |
| Current, gyro only 1 kHz | 3.2 mA typ.; accel only at 4 kHz = 450 µA | PS Table 3, §3.4.1 |
| Temperature range | −40 to +85 °C | PS Table 3, §3.4.1 |
| Host interfaces | **I2C 400 kHz max**, SPI 1 MHz (to 20 MHz with timing caveats) | PS §6.2, §3 |
| I2C address | `AD0`=0 → **0x68**, `AD0`=1 → 0x69 | PS Table 4 / §6.1 |
| Accel noise | 300 µg/√Hz; 8 mg-rms @ `DLPFCFG=2` (94 Hz) | PS Table 2, §3.2 |
| Gyro noise | 0.01 °/s/√Hz; 0.1 °/s-rms @ `DLPFCFG=2` | PS Table 1, §3.1 |
| FIFO | **512 bytes** (vs 2048 on the ICM) | PS §4.17, p.27 |
| Magnetometer (AK8963) | on the auxiliary I²C bus, addr 0x0C — **out of scope** (§7.4) | PS §3.3, §6.3 |

> **Bus-speed difference, wired in from the start.** The MPU-9250's datasheet I²C
> ceiling is **400 kHz**; the ICM-42688-P's is **1 MHz**. Mode A *requires* 1 MHz to
> carry its 4 kHz FIFO (§3.6), so a bench MPU-9250 can never be asked to reproduce
> a mode-A rate over I²C even in principle — the link, not just the filter, forbids
> it. `MODES`/`MODE_I2C_HZ` in the reference codec carry both numbers so a board
> cannot be configured past the part's own limit.

### 2.2 Identity and address

| Item | ICM-42688-P | MPU-9250 | Source |
|---|---|---|---|
| `WHO_AM_I` register | 0x75 | 117 (**0x75**) | ICM §14.58; MPU RM §4.38 |
| **`WHO_AM_I` reset value** | **0x47** | **0x71** | ICM §14.58; MPU RM §4.38 |
| I2C addr, AD0/AP_AD0 = 0 | **0x68** (1101000b) | **0x68** (1101000b) | ICM Table 3; MPU PS Table 4 |
| I2C addr, AD0/AP_AD0 = 1 | 0x69 | 0x69 | same |
| Selecting I2C over SPI | `AP_CS` (pin 12) → VDDIO | `NCS` → high | ICM Table 10; MPU PS §6.1 |
| Forcing I2C mode in-register | `INTF_CONFIG0` (0x4C) `UI_SIFS_CFG` ≠ 00 | not required (host-interface strap only) | ICM §14; MPU RM §6 |

> **Firmware rule — the identity guard, mandatory in every mode (§2.4).** The two
> parts answer the *same register address* with *different values*. Read
> `WHO_AM_I` on start-up, compare it with the value the selected mode expects, and
> **abort the capture on a mismatch**. Never continue with an unknown part: the
> header's LSB scales would be the other part's and every amplitude downstream
> would be wrong by a constant factor with nothing to flag it. The reference codec
> exposes this as `imu_capture.check_identity(mode, who_am_i)`, which raises
> `IdentityMismatch` rather than warning.

### 2.3 Pin mapping

#### 2.3.1 ICM-42688-P (this is the CTG-11 wiring contract, carrier tier)

| Pin | Name | Use here |
|---|---|---|
| 1 | `AP_SDO` / `AP_AD0` | **tie to GND → address 0x68** (or VDDIO → 0x69) |
| 2, 3, 10, 11 | `RESV` | no connect or GND |
| 4 | `INT1` / `INT` | **FIFO watermark interrupt → ESP32-S3 GPIO** |
| 5 | `VDDIO` | 3.3 V logic rail (0.1 µF + 2.2 µF) |
| 6 | `GND` | ground |
| 7 | `RESV` | **connect to GND** |
| 8 | `VDD` | 1.8–3.3 V (10 nF) |
| 9 | `INT2` / `FSYNC` / `CLKIN` | **leave unconnected or GND** — see §3.5 on CLKIN |
| 12 | `AP_CS` | **tie to VDDIO** (I2C selected) |
| 13 | `AP_SCL` | I2C SCL |
| 14 | `AP_SDA` | I2C SDA |

Two nets go to the ESP32-S3: SCL/SDA and `INT1`. No SPI nets are populated
(consistent with D4: *"SPI left unpopulated"*).

#### 2.3.2 MPU-9250 breakout (bench tier)

Wire the breakout's labelled pins; the inventory confirms this pin set exists on
the physical board (§1.1):

| Breakout pin | Use here |
|---|---|
| `VCC` | 3.3 V (respect the 2.4 V VDD floor; 3.3 V is in range) |
| `GND` | ground |
| `SCL` / `SDA` | I2C, **pull-ups required**, bus ≤ **400 kHz** |
| `ADO` | **tie to GND → 0x68** |
| `INT` | data-ready / FIFO interrupt → host GPIO |
| `NCS` | leave high (I2C selected) |
| `FSYNC` | leave unconnected |
| `ECL` / `EDA` | auxiliary I²C to the AK8963 — **leave unconnected** (mag out of scope) |

`WHO_AM_I` must read **0x71** before recording (mode M).

### 2.4 The identity guard (normative, all modes)

```
read  WHO_AM_I            (reg 0x75 on both parts)
want  = 0x47 for modes A, B      (ICM-42688-P)
        0x71 for mode  M         (MPU-9250)
if read != want:  log the actual value; ABORT the capture; do not write frames.
```

Rationale, stated once so it is not re-argued per mode. Three independent reasons,
any one of which is sufficient:

1. **The register maps differ.** Mode A's sequence writes `PWR_MGMT0` (0x4E),
   `GYRO_CONFIG0` (0x4F), `ACCEL_CONFIG0` (0x50), a `TMST_CONFIG` (0x54) and an AAF
   triplet in banks 1/2. On an MPU-9250 those addresses mean something else entirely
   (0x4E is not a power register, there are no banks, there is no AAF). Writing mode
   A's configuration to an MPU-9250 does not fail cleanly — it configures unrelated
   registers and then reads data that is not what the header claims.
2. **The bands differ.** Mode A measures 0–500 Hz and mode M 0–150 Hz (§3.5), so a
   capture analysed under the wrong mode's assumptions is interpreted over the wrong
   band — and "no peak above order 7" means something different in each (§3.5.1).
3. **The parts are near-identical where they are not different**, which is the trap.
   Both use 2048 LSB/g at ±16 g and 16.4 LSB/dps at ±2000 dps (§5.2), so a
   *mis-read* capture does **not** look obviously wrong — the numbers scale the same
   way. Nothing downstream can detect the mistake from the sample values alone; only
   `WHO_AM_I` can, at start-up, for free.

So the guard is not a formality: it is the only thing standing between a
configuration mix-up and a plausible, wrong diagnosis. It also catches the case that
actually happens — someone writes mode A for the carrier board and runs it on the
bench module without changing the mode.

> **Do not "auto-detect and continue".** Selecting the mode from the detected part
> is *not* equivalent: mode A and mode M measure different bands (0–500 Hz vs
> 0–150 Hz), so auto-selecting would silently change what the run means. Detect,
> compare, abort on mismatch, and make the *operator* choose the mode.

---

## 3. Bandwidth, ODR and filter decision

### 3.1 The target band (the requirement)

Wheel imbalance and its harmonics must be resolvable in a band starting well below
the wheel rotation frequency and running past the orders the diagnosis uses. The
card's own sizing is *"a usable band up to ~200–500 Hz and therefore an ODR of at
least 1–2 kHz with anti-alias filtering"*.

**Frozen requirement:** the primary measurement must resolve **0–500 Hz**, and a
reduced **0–250 Hz** monitor band is acceptable for long runs. §3.2 corrects the
card's arithmetic without changing that conclusion; §3.4 shows what it costs to
meet; §3.6 shows why the bench part cannot, which is the reason a third mode
exists.

### 3.2 The card's rotation-frequency arithmetic is wrong, and why that is harmless

The card's premise: *"at 100 km/h with a 0.6 m wheel the rotation frequency is
~7 Hz, so plan for a usable band up to ~200–500 Hz and therefore an ODR of at least
1–2 kHz with anti-alias filtering."*

The first clause is wrong; the conclusion still stands.

```
f_rot = v / (pi * D)
```

| Speed, D = 0.60 m | f_rot (order 1) | 4th order | 10th order |
|---|---|---|---|
| 30 km/h | 4.42 Hz | 17.7 Hz | 44.2 Hz |
| 50 km/h | **7.37 Hz** | 29.5 Hz | 73.7 Hz |
| 70 km/h | 10.32 Hz | 41.3 Hz | 103.2 Hz |
| 100 km/h | **14.74 Hz** | 58.9 Hz | 147.4 Hz |
| 130 km/h | 19.16 Hz | 76.6 Hz | 191.6 Hz |

* 100 km/h on a 0.60 m wheel is **14.74 Hz**, not ~7 Hz.
* ~7 Hz at 100 km/h would need a **1.26 m** wheel.
* 0.60 m at 7 Hz is **47.5 km/h**.

So the card's "7 Hz" is the 50 km/h figure. **Why the band decision survives:** the
card's conclusion is driven by the *upper* bound, and the real `f_rot` is *higher*
than assumed, so the sizing is conservative in the safe direction. The fence posts:

* the **lowest** 1x across the plausible driving range (30 km/h, 0.60 m) is 4.42 Hz;
* the **highest** speed worth planning for is ≈130 km/h → 1x = 19.16 Hz;
* at 130 km/h, order **26×** lands at 498 Hz — a 0–500 Hz band resolves the first 26
  orders of every wheel. Tyre/rim non-uniformity below order ~10 is the deepest
  anyone reasons about in practice, so 0–500 Hz is a generous margin.

**Freeze:** design band = **0–500 Hz** (mode A, carrier tier); **0–250 Hz** is the
accepted reduced band for the long-run monitor mode B; **0–150 Hz** is the bench
tier's achievable band (mode M, §3.6). Reconfirm `D` and the true top speed at the
first real road test (`t_fbd7baa6`); the fence posts scale as `1/D`, and the ODR
choice only needs revisiting if `D` falls below ~0.5 m *and* the top speed exceeds
~150 km/h.

### 3.3 Why orders, not raw Hz

A wheel imbalance is a rotating mass, so it pushes at the wheel's rotation frequency;
runout, flat spots and tyre non-uniformity show at 2×, 3×, 4×. Working in **orders**
(`order = f / f_rot`) makes a diagnosis independent of vehicle speed, which matters
because `f_rot` sweeps continuously in a real run. The capture therefore has to store
the **wheel geometry as provenance**, not just samples (§5.4, §7.3).

### 3.4 Carrier tier: capability vs the requirement (modes A, B)

This section covers the **ICM-42688-P** only. The bench part cannot meet it, and
§3.5 explains why.

| Need | ICM-42688-P | Source |
|---|---|---|
| ODR ≥ 1–2 kHz | **1.5625 Hz … 32000 Hz**, discrete steps incl. 1 k, 2 k, 4 k, 8 k, 500 Hz | §5.6 tables, pp.34–36 |
| Accel range | ±2 / 4 / 8 / 16 g | Table 2, p.12 |
| Gyro range | ±15.625 / 31.25 / 62.5 / 125 / 250 / 500 / 1000 / 2000 dps | Table 1, p.11 |
| Anti-alias filter | 2nd-order programmable **42 – 3979 Hz**, 63 settings | §5.3, pp.28–29 |
| Additional digital filter | UI filter block, 1st/2nd/3rd order, 3 dB BW per ODR | §5.5, pp.30–34 |
| Noise (accel) | 65 µg/√Hz (X/Y), 70 (Z); 0.65 mg-rms @ 100 Hz NBW (X/Y) | Table 2, p.12 |
| Noise (gyro) | 2.8 mdps/√Hz; 0.028 dps-rms @ 100 Hz | Table 1, p.11 |
| FIFO | **2048 bytes**, 16-byte or 20-byte packets, watermark + full interrupts | §6, pp.37–39 |
| Timestamp | 1 µs resolution, 16-bit FIFO field, or CLKIN for 50 ppm | §12.5, §12.7, §14.42 |
| ODR accuracy (internal clock) | **±8 % RCOSC**, ±1 % PLL; **±50 ppm with CLKIN** | §12.5, p.58 |
| Temp sensor | 16-bit, 132.48 LSB/°C; in FIFO at 2.07 LSB/°C | Table 3, §14.5 |

Every requirement is met. **Note the ±8 % ODR accuracy** — it is the single most
important consequence for this task and is what makes timestamps non-optional (§3.6).

#### 3.4.1 The ODR and filter decision (frozen)

Constraints (all resolvable by lookup in the datasheet's own tables, so this is
arithmetic and not taste):

1. **Flat passband** — a filter that droops inside the band corrupts the very
   amplitude the diagnosis compares. Budget: **≤ 1 dB** droop at the band edge.
2. **Alias rejection** — content just above Nyquist must not fold into the band and
   create a phantom imbalance peak. Target: **≥ 25 dB** at the lowest fold-in
   frequency (`ODR − band`).
3. **The bus must carry the FIFO** (I2C at 1 MHz; §3.6).

**Method, stated so it can be audited.** For each standard ODR, each of the 63
documented AAF settings (§5.3) and each UI filter order/code (§5.5), compute the
cascade response; keep only settings meeting both constraints. The rejection target
is judged **on the AAF alone**, because the AAF's transfer function is *published*
(datasheet §5.3: `U = (f/fc)²`, `|H| = 1/√(1+U²)`, 2nd order) whereas the UI filter
block is tabulated only by 3 dB bandwidth, noise bandwidth and group delay — its
stopband is **not** published. Deciding on the documented stage alone makes the
verdict independent of any modelling assumption I would otherwise have to invent.

| ODR | fold-in `ODR−500` | best AAF setting (≤1 dB droop, AAF-alone rejection) | droop @500 Hz | rejection @fold-in | bus |
|---|---|---|---|---|---|
| 1000 Hz | **500 Hz** | **none** — the fold-in *is* the band edge | — | — | 14.4 % |
| 2000 Hz | 1500 Hz | **none** ≤1 dB (best −0.85 dB droop buys only −12.7 dB) | — | — | 28.8 % |
| **4000 Hz** | 3500 Hz | **AAF 785 Hz** + UI 3rd order 1048.1 Hz | **−0.66 dB** | **−25.98 dB** | 57.6 % |
| 8000 Hz | 7500 Hz | AAF 1766 Hz + UI 3rd order 2096.3 Hz | −0.03 dB | −25.14 dB | **115 % — impossible on I2C** |

This is why "*at least 1–2 kHz*" is not enough for a 500 Hz band:

* **1000 Hz cannot work.** The first frequency folding into the band is
  `1000 − 500 = 500 Hz` — exactly the band edge. No setting keeps 500 Hz and rejects it.
* **2000 Hz cannot work either**, which is the non-obvious result. Reaching 25 dB at
  1500 Hz needs an AAF corner well below 500 Hz, whose own droop there exceeds the
  entire flatness budget (at 1 dB droop the best achievable rejection is −12.7 dB).
* **4000 Hz** clears both with the documented filter alone, and is the lowest
  standard ODR that does.
* **8000 Hz** would be better still but needs 115 % of an I2C link, i.e. it requires
  SPI, which D4 leaves unpopulated.

**Frozen mode A — survey / burst, band 0–500 Hz (the imbalance measurement):**

| Setting | Value | Register |
|---|---|---|
| ODR | **4000 Hz** | `ACCEL_CONFIG0[3:0]` / `GYRO_CONFIG0[3:0]` = `0100` |
| Accel FSR | **±16 g** | `ACCEL_CONFIG0[7:5]` = `000` |
| Gyro FSR | **±2000 dps** | `GYRO_CONFIG0[7:5]` = `000` |
| Anti-alias filter | **785 Hz** (2nd order) | `ACCEL_AAF_DELT`=17, `ACCEL_AAF_DELTSQR`=288, `ACCEL_AAF_BITSHIFT`=7 (bank 2, regs 0x03/0x04/0x05); gyro equivalents at bank 1, regs 0x0C/0x0D/0x0E |
| UI filter order | **3rd order** | `ACCEL_UI_FILT_ORD` (0x53[4:3]) / `GYRO_UI_FILT_ORD` (0x51[3:2]) = `10` |
| UI filter BW | code 4 → **1048.1 Hz** 3 dB | `ACCEL_UI_FILT_BW` (0x52[7:4]) / `GYRO_UI_FILT_BW` (0x52[3:0]) = `0100` |
| Power mode | low-noise, both | `PWR_MGMT0` (0x4E): `GYRO_MODE`=11, `ACCEL_MODE`=11 |

Measured cascade response, mode A:

| f | 10 Hz | 50 Hz | 100 Hz | 200 Hz | 300 Hz | 400 Hz | 441 Hz | **500 Hz** |
|---|---|---|---|---|---|---|---|---|
| droop | −0.00 | −0.00 | −0.00 | −0.02 | −0.09 | −0.30 | −0.44 | **−0.71 dB** |
| rejection at 3500 Hz | **−57.4 dB** (AAF alone −25.98 dB) | | | | | | | |

**Frozen mode B — monitor / long run, band 0–250 Hz:**

| Setting | Value | Register |
|---|---|---|
| ODR | **2000 Hz** | `ACCEL_ODR` / `GYRO_ODR` = `0101` |
| Accel / gyro FSR | ±16 g / ±2000 dps | as above |
| Anti-alias filter | **394 Hz** (2nd order) | `DELT`=9, `DELTSQR`=81, `BITSHIFT`=9 |
| UI filter | 3rd order, code 4 → **524.0 Hz** | `UI_FILT_ORD`=`10`, `UI_FILT_BW`=`0100` |

Mode B response: −0.00 dB at 100 Hz, −0.09 dB at 150 Hz, −0.28 dB at 200 Hz,
**−0.65 dB at 250 Hz**, and −25.9 dB (AAF alone) at the 1750 Hz fold-in. Its band is
half of mode A's and it costs half the storage (2.9 MB/min vs 5.8), which is the
right trade when the run has to be long. It still resolves the **first 13 orders at
130 km/h**, which covers every order the diagnosis uses.

**Why ±16 g and ±2000 dps (not the low-noise end of the range).** Quantisation has
to be negligible against the sensor's own noise, and headroom has to cover real
events. Datasheet noise over 500 Hz NBW is 1.453 mg-rms (accel X/Y, from 65 µg/√Hz)
and 0.0626 dps-rms (gyro, from 2.8 mdps/√Hz):

| FSR | LSB/g | 1 LSB | quantisation RMS | added to sensor noise |
|---|---|---|---|---|
| ±2 g | 16384 | 5.99e-4 m/s² (61.0 µg) | 1.73e-4 m/s² | +0.007 % |
| ±4 g | 8192 | 1.20e-3 m/s² (122 µg) | 3.46e-4 m/s² | +0.029 % |
| ±8 g | 4096 | 2.39e-3 m/s² (244 µg) | 6.91e-4 m/s² | +0.117 % |
| **±16 g** | 2048 | 4.79e-3 m/s² (488 µg) | 1.38e-3 m/s² | **+0.469 %** |
| ±2000 dps | 16.4 LSB/(°/s) | 6.10e-2 °/s | 1.76e-2 °/s | +3.9 % |

At ±16 g, quantisation adds **under half a percent** to the noise floor — an order of
magnitude below the few-percent repeatability of the diagnosis — while giving
157 m/s² (16 g) of headroom for potholes, kerbs, speed humps and a hard stop, all of
which would clip at ±2 g and, by clipping, generate broadband harmonics that *look
like* imbalance. Likewise ±2000 dps tolerates a 1.2 m/s speed step or a sharp swerve,
while adding 3.9 % quantisation noise to a gyro signal that is a *secondary* check here.

> **Honest limitation of the 785 Hz choice.** The AAF-alone criterion means the
> −25.98 dB at the fold-in is supplied by a filter whose corner sits at 785 Hz, only
> 1.57× the band edge, so **the passband does droop −0.66 dB at 500 Hz** (and
> −0.44 dB at 441 Hz, −0.30 dB at 400 Hz) rather than being flat. That is inside the
> 1 dB budget and it does not affect peak *frequency* at all — but it does bias the
> peak *amplitude* at the very top of the band by up to ~7 % (0.66 dB) relative to
> mid-band. Consequences, both frozen:
> * amplitude comparisons **between orders/channels within one capture** are
>   unaffected, because both sides see the same response;
> * an absolute amplitude quoted at 300–500 Hz must be corrected by the mode's
>   response table above, and `t_00f126bd` must apply that correction before
>   reporting absolute g;
> * if a future run needs a flat 500 Hz, raise `AAF_DELT` toward 63 (3979 Hz) and
>   accept the weaker documented-stage rejection, relying on the UI stage — but that
>   decision needs the stopband confirmed by measurement, not by my model.

### 3.5 Bench tier: the MPU-9250 **cannot** meet the 0–500 Hz requirement (mode M)

This is the finding that changes the shape of the contract, so it is stated with
its evidence rather than asserted.

The MPU-9250's accelerometer offers exactly two documented rate regimes
(`RM-MPU-9250A-00` rev 1.6, *"Accelerometer Data Rates and Bandwidths (Normal
Mode)"*, p.15):

| `ACCEL_FCHOICE` | `A_DLPFCFG` | 3 dB BW | ODR | filter block |
|---|---|---|---|---|
| **0** | x | 1046 Hz | **4000 Hz** | `Dec1` (DLPF **bypassed**) |
| 1 | 0 / 1 | 218.1 Hz | 1000 Hz | DLPF |
| 1 | 2 | 99 Hz | 1000 Hz | DLPF |
| 1 | 3 | 44.8 Hz | 1000 Hz | DLPF |
| 1 | 7 | **420 Hz** | 1000 Hz | `Dec2` |

Two independent walls, either of which is fatal to a documented 0–500 Hz band:

1. **The 4 kHz rate exists only with the DLPF bypassed.** `FCHOICE=0` selects the
   `Dec1` block and the register map publishes **only its 3 dB bandwidth (1046 Hz)**
   — no transfer function, no stopband. At 4 kHz the first frequency folding into a
   0–500 Hz band is `4000 − 500 = 3500 Hz`, and nothing in the datasheet lets anyone
   claim what the response is there. An unquantified anti-alias stage is not an
   anti-alias guarantee; a folded 3.5 kHz component would appear as a phantom peak
   *inside* the imbalance band, which is precisely the failure this task exists to
   prevent.
2. **At the documented 1 kHz rate, the fold-in is the band edge.** `1000 − 500 =
   500 Hz`. No filter can both pass 500 Hz (the requirement) and reject 500 Hz (the
   alias). Every `A_DLPFCFG` setting with a documented response has a 3 dB bandwidth
   **below** 500 Hz (218.1 Hz at best), so the band edge is already 2.3 dB down at the
   widest documented setting.

So the card's instruction — *band to ~500 Hz, therefore ODR ≥ 1–2 kHz* — **cannot be
satisfied by the module that is physically on the bench.** That is a property of the
part, not of the wiring, and no register setting escapes it.

> ⚠ **What this means for the card's acceptance criteria.** The card asks for an
> ODR/sample-rate decision justified against the imbalance band. The justified
> decision here is: **0–500 Hz requires the carrier part (mode A); the bench part
> gets mode M at 0–150 Hz and is explicitly a reduced-capability tier, not a
> substitute.** Recording a 0–500 Hz run from an MPU-9250 would produce a
> well-formed file that aliases — the worst kind of wrong answer, because nothing in
> the file would look anomalous.

#### 3.5.1 Frozen mode M — the bench tier's band and filters

Given the wall above, the largest band the MPU-9250 can serve with a **documented**
anti-alias response at its 1 kHz documented rate is bounded by the DLPF's 3 dB
bandwidth. `A_DLPFCFG = 7` (`Dec2`, 420 Hz) is the widest, so it is the choice:

| Setting | Value | Register |
|---|---|---|
| ODR | **1000 Hz** | `SMPLRT_DIV` (0x19) = 0 with DLPF on |
| Accelerometer DLPF | `A_DLPFCFG` = **7** (`Dec2`, 420 Hz 3 dB) | `ACCEL_CONFIG2` (0x1D)[2:0]; `ACCEL_FCHOICE_B` = 0 |
| Gyro DLPF | `DLPF_CFG` = **1** (184 Hz 3 dB, 1 kHz Fs) | `CONFIG` (0x1A)[2:0]; `FCHOICE_B` = 00 |
| Accel FSR | **±16 g** | `ACCEL_CONFIG` (0x1C)[4:3] = `11` |
| Gyro FSR | **±2000 dps** | `GYRO_CONFIG` (0x1B)[4:3] = `11` |
| FIFO | accel + gyro + temp, **512-byte** buffer | `FIFO_EN` (0x23), `USER_CTRL` (0x6A)[6] |
| **Band** | **0–150 Hz** | derived below |

**Why 0–150 Hz and not higher.** The DLPF is documented only by its 3 dB point, so
the flat region is set by how much droop the diagnosis can tolerate. The 1 dB
flatness budget from §3.4.1 applies unchanged, and a 2nd-order response whose 3 dB
point is `fc` is 1 dB down at `f = fc·(10^0.1 − 1)^0.25 = 0.7071·fc`:

| `A_DLPFCFG` | `fc` | −1 dB point | droop at 150 Hz | droop at 200 Hz |
|---|---|---|---|---|
| 0/1 | 218.1 Hz | 154.2 Hz | −0.88 dB | −2.32 dB |
| **7** | **420.0 Hz** | **297.0 Hz** | **−0.07 dB** | −0.22 dB |
| 2 | 99.0 Hz | 70.0 Hz | −8.0 dB | −12.5 dB |

With `A_DLPFCFG = 7` the band edge at 150 Hz is only **−0.07 dB** down, and the
response stays inside the 1 dB budget all the way to ~297 Hz — so 150 Hz is chosen
with a 2× margin, deliberately, because the DLPF's real response is not published
and the model is second-order-approximate (⚠ assumption).

**What mode M still delivers**, on the same fence-post logic as §3.2, at the
130 km/h worst case (`f_rot = 19.16 Hz`):

| Band | Highest order resolved at 130 km/h |
|---|---|
| 0–150 Hz (mode M) | **7×** |
| 0–250 Hz (mode B) | 13× |
| 0–500 Hz (mode A) | 26× |

Order 7 is enough for the diagnosis's core cases — order 1 (imbalance), 2 (runout /
flat spot), 3–4 (tyre non-uniformity) — with margin. It is **not** enough to claim
parity with mode A, and the report from `t_00f126bd` must say which mode a capture
came from, because "no peak above order 7" means something different in each.

> ⚠ **Stated limitation of mode M.** The 420 Hz `Dec2` response is *not* a
> documented stopband, so mode M rests on a 2nd-order model plus generous margin,
> not on a published transfer function like mode A does. At the 1 kHz `ODR` the
> first fold-in for a 150 Hz band is 850 Hz, where the model gives ≈ −25 dB — the
> same order of rejection mode A reaches with its AAF, but *modelled* rather than
> *tabulated*. Bench measurement (`t_fbd7baa6`) should confirm the real stopband
> before any quantitative amplitude comparison is made across the two tiers.

### 3.6 Timestamps (mandatory — a consequence of ±8 % ODR accuracy)

The datasheet is explicit: internal-clock ODR uncertainty *"can be as high as ±8 % in
RCOSC mode and ±1 % in PLL mode"*, improvable to ±50 ppm only with an external CLKIN
(§12.5, p.58).

What that costs, for a 1x peak at 14.74 Hz against a 1 Hz FFT bin (120 s window):

| Clock | error at 1x | in FFT bins |
|---|---|---|
| Internal RCOSC, ±8 % | **1.179 Hz** | **1.18 bins** |
| Internal PLL, ±1 % | 0.147 Hz | 0.15 bins |
| Ignoring the FIFO `32/30` timestamp scaling (§12.7) | 0.98 Hz (+6.67 %) | 0.98 bins |

An ±8 % clock error moves the imbalance peak by more than a whole FFT bin — enough to
be mistaken for a different rotation order, i.e. the wrong conclusion, which is
exactly the failure this task exists to avoid. Three consequences, all frozen:

1. **Every frame carries a timestamp** from a monotonic host clock
   (`esp_timer_get_time()` on the ESP32-S3), µs resolution, zeroed at capture start.
   `uint32` wraps after **71.6 min** — the rotation policy (§6) keeps every file well
   inside that: 11.7 min (mode A), 23.3 min (mode B), 46.6 min (mode M) at a 64 MiB
   segment.
2. **Do not use the sensor's 16-bit FIFO timestamp field as the primary timebase.**
   Its span is only ~70 ms at 1 µs LSB (~61 ms if `TMST_RES` = 16 µs), far short of a
   run, and it needs the `32/30` (or `32.768/RTC`) rescale of §12.7. Use it — or
   CLKIN — only as a *cross-check*, and if it disagrees with the host clock by more
   than 1 %, raise `FLAG_TIME_ESTIMATED`.
3. **The analyser must use the timestamps, not `n/fs`.** Rate = `(n−1)/(t_us[-1] −
   t_us[0])`, exactly as the reference decoder does. Any pipeline assuming a nominal
   rate inherits the ±8 %.

CLKIN (pin 9) *is* the datasheet-quality fix and would drop the error to ±50 ppm. It
is deliberately **not required**: the header carries `fs_nominal` *and* the real rate
is measured from the timestamps, so the sample-rate error is already removed from the
frequency estimate. Leave pin 9 unconnected (or to GND) per §2.3.1; adding a 32.768 kHz
oscillator is a future PCB revision, not a blocker. If one is fitted, set
`INTF_CONFIG5` (bank 1, 0x7B) bits 2:1 = `10` and `RTC_MODE` (0x4D bit 2), then apply
the `32.768/RTC frequency` rescale of §12.7.

> **The MPU-9250 has no equivalent.** It offers a `CLK_SEL` internal-oscillator choice
> and an FSYNC input, but not a documented ppm-grade external-clock mode, so mode M's
> timebase accuracy is whatever the host clock gives. The same rule applies and is
> even more important there: **always measure the rate from the timestamps**, never
> assume 1000 Hz.

### 3.7 Bus and FIFO budget

#### 3.7.1 Carrier tier (ICM-42688-P), I2C

| Mode | ODR | FIFO packet | FIFO traffic | I2C @ 400 kHz | I2C @ **1 MHz** | SPI @ 24 MHz |
|---|---|---|---|---|---|---|
| B | 2000 Hz | 16 B (Packet 3) | 32 000 B/s | 72.0 % | **28.8 %** | 1.07 % |
| A | 4000 Hz | 16 B (Packet 3) | 64 000 B/s | **144 % — impossible** | **57.6 %** | 2.13 % |

* **Mode A requires I2C at 1 MHz** (the part's maximum, §2.1.1). At 400 kHz the link
  physically cannot carry the FIFO — 144 % duty. This is a hard wiring and firmware
  requirement, not a preference. If 1 MHz proves marginal on the real board, the
  fallback is mode B's configuration (2000 Hz / 0–250 Hz) at 28.8 % duty.
* Duty assumes 9 bit-times per byte (8 data + ACK) and ignores read-command overhead;
  budget **+10–15 %** on the real bus.
* FIFO depth at 16-byte packets: `(2048 − 16)/16 = 127` records = **31.8 ms** at
  4000 Hz, **63.5 ms** at 2000 Hz.
* Watermark: `FIFO_WM = 32` records → interrupt every **8.0 ms** (mode A) / 16.0 ms
  (mode B), leaving 23.8 ms / 47.5 ms of runway before overflow — comfortable for an
  ESP32-S3 task that only has to drain 512 bytes.
* Packet choice: **Packet 3** (16 B: header + accel + gyro + temp + 16-bit
  timestamp). Packets 1/2 (6/8 B) halve the traffic but carry either accel *or* gyro,
  never both. `FIFO_HIRES_EN` Packet 4 (20 B) buys 18/19-bit resolution that ±16 g
  does not need and costs 25 % more bus. So: `FIFO_HIRES_EN` = 0, `FIFO_TEMP_EN` = 1.

#### 3.7.2 Bench tier (MPU-9250), I2C — mode M

The MPU-9250's FIFO is **512 bytes** and its packet is the datasheet's
accel(6) + gyro(6) + temp(2) = **14 bytes**, and its bus ceiling is 400 kHz:

| Mode | ODR | FIFO packet | FIFO traffic | I2C @ **400 kHz** (its max) | I2C @ 1 MHz |
|---|---|---|---|---|---|
| **M** | 1000 Hz | 14 B | 14 000 B/s | **31.5 %** | 12.6 % |
| — | 2000 Hz | 14 B | 28 000 B/s | 63.0 % | 25.2 % |
| — | 4000 Hz | 14 B | 56 000 B/s | **126 % — impossible** | 50.4 % |

* Mode M at 31.5 % duty (≈41 % once the +10–15 % read overhead of §3.7.1 is added)
  is comfortable, and it is *not* marginal the way mode A is on the carrier tier.
* FIFO depth at 14-byte packets: `(512 − 14)/14 = 35` records = **36.6 ms** at
  1000 Hz. Watermark `FIFO_WM = 16` records → interrupt every **16.0 ms**, leaving
  **20.6 ms** of runway. A watermark of 32 would leave only 4.6 ms and is **not**
  acceptable on this part — hence mode M's `fifo_wm_records = 16`.
* The 4 kHz row is not a recommendation: it is shown only to demonstrate that the
  card's demanded rate would also have been **bus-impossible** on the bench part, on
  top of being filter-impossible (§3.5).

---

## 4. Firmware configuration sequence (normative)

Two sequences, one per tier. Both end with the identity guard of §2.4.

### 4.1 Carrier tier — ICM-42688-P (modes A, B)

Follow in this order; each step cites its register from DS-000347 §14.

1. **Reset.** Software reset via `DEVICE_CONFIG` (0x11); poll
   `INT_STATUS.RESET_DONE_INT` (0x2D bit 4). Read `WHO_AM_I` (0x75) and abort unless
   `0x47`.
2. **Bank 0** (`REG_BANK_SEL` 0x76 = 0x00).
3. **Host interface.** `INTF_CONFIG0` (0x4C): `UI_SIFS_CFG` to I2C mode,
   `FIFO_COUNT_REC` by preference, `FIFO_HOLD_LAST_DATA_EN` = 0. `INTF_CONFIG1`
   (0x4D): `RTC_MODE` = 0 (no CLKIN), `CLKSEL` = internal-best.
4. **Rates / ranges.** `PWR_MGMT0` (0x4E): gyro and accel to **low-noise** (`11`),
   `IDLE` = 0, `TEMP_DIS` = 0. **Wait ≥ 200 µs** before further writes (datasheet
   requirement on leaving OFF), and ≥ 45 ms for the gyro drive to settle before
   trusting data. Then `GYRO_CONFIG0` (0x4F) = `GYRO_FS_SEL` 000 + `GYRO_ODR` **0100**
   (mode A) / **0101** (mode B); `ACCEL_CONFIG0` (0x50) = `ACCEL_FS_SEL` 000 +
   `ACCEL_ODR` the same code.
5. **UI filter.** `GYRO_CONFIG1` (0x51) `GYRO_UI_FILT_ORD` = `10`;
   `ACCEL_CONFIG1` (0x53) `ACCEL_UI_FILT_ORD` = `10`; `GYRO_ACCEL_CONFIG0` (0x52)
   both `UI_FILT_BW` nibbles = `0100`.
6. **Anti-alias filter.** Bank 2: `ACCEL_AAF_DELT` (0x03[6:1]) = **17** (mode A) /
   **9** (mode B), `ACCEL_AAF_DELTSQR` (0x04[7:0] + 0x05[3:0]) = 288 / 81,
   `ACCEL_AAF_BITSHIFT` (0x05[7:4]) = 7 / 9. Bank 1: `GYRO_AAF_DELT` (0x0C[5:0])
   = 17 / 9, `GYRO_AAF_DELTSQR` (0x0D + 0x0E[3:0]) = 288 / 81, `GYRO_AAF_BITSHIFT`
   (0x0E[7:4]) = 7 / 9.
7. **Timestamp.** `TMST_CONFIG` (0x54): `TMST_EN` = 1, `TMST_RES` = 0 (1 µs),
   `TMST_DELTA_EN` = 1 if a FIFO delta timestamp is wanted, `TMST_TO_REGS_EN` = 1 for
   register reads.
8. **FIFO.** Back to bank 0. `FIFO_CONFIG1` (0x5F): `FIFO_ACCEL_EN` = 1,
   `FIFO_GYRO_EN` = 1, `FIFO_TEMP_EN` = 1, `FIFO_TMST_FSYNC_EN` = 0 (no FSYNC),
   `FIFO_HIRES_EN` = 0, `FIFO_RESUME_PARTIAL_RD` = 1. `FIFO_CONFIG2` (0x60)
   `FIFO_WM[7:0]` = 32, `FIFO_CONFIG3` (0x61) `FIFO_WM[11:8]` = 0
   (**never set the watermark to 0**).
9. **Interrupts.** `INT_CONFIG` (0x14): `INT1_MODE` push-pull, active-high (or per
   board wiring). `INT_SOURCE0` (0x65): `FIFO_THS_INT1_EN` = 1. Configure `INT1`
   (pin 4) on the ESP32-S3 as a rising-edge wake source.
10. **Drain loop.** On each `FIFO_THS_INT`, read `FIFO_COUNTH/L` (0x2E/0x2F) — reading
    the *high* byte latches both — then burst-read `FIFO_DATA` (0x30). Parse the
    1-byte header per record; tolerate partial reads (`FIFO_RESUME_PARTIAL_RD` = 1).
    After every drain, read `FIFO_LOST_PKT0/1` (0x6C/0x6D); if either is non-zero, set
    `FLAG_FIFO_OVERRUN` on the frames that follow and count them.
11. **Timestamp each frame** with the host monotonic clock at drain time. When a whole
    FIFO block is drained in one burst, interpolate the per-frame times from the read
    time and the measured interval, and set `FLAG_TIME_ESTIMATED`; the FIFO's own
    16-bit field (`TMST_DELTA_EN`) is the cross-check for step 2 of §3.6.

> **Do not silently downsample.** If the target rate cannot be sustained, the firmware
> must raise `FLAG_FIFO_OVERRUN` / `FLAG_HOST_OVERRUN` and *report* it, never quietly
> deliver fewer samples. The analysis in `t_00f126bd` must be able to see that a run
> is degraded.

### 4.2 Bench tier — MPU-9250 (mode M)

Each step cites its register from `RM-MPU-9250A-00` rev 1.6 (§4.x of that document).

1. **Reset.** `PWR_MGMT_1` (0x6B) bit 7 `DEVICE_RESET` = 1; wait ≥ 100 ms
   (PS: start-up 100 ms). Read `WHO_AM_I` (117 / 0x75) and **abort unless `0x71`**
   (§2.4).
2. **Wake and clock.** `PWR_MGMT_1` (0x6B): `SLEEP` = 0, `CLKSEL` = best available
   (PLL with gyro reference preferred over the internal oscillator). `PWR_MGMT_2`
   (0x6C) = 0x00 so both accel and gyro axes are on. Wait ≥ 35 ms for the gyro drive
   to settle (PS: gyro startup 35 ms).
3. **Sample rate.** `SMPLRT_DIV` (0x19) = **0** → 1 kHz internal rate / (1 + 0).
   Note the datasheet's constraint: this divider is only effective when the DLPF is
   active (`FCHOICE_B` = 00 and `0 < DLPF_CFG < 7`), which step 4 satisfies.
4. **Filters.** `CONFIG` (0x1A): `DLPF_CFG` = **1** (gyro 184 Hz 3 dB, 1 kHz Fs),
   `FCHOICE_B` = 00, `EXT_SYNC_SET` = 000 (no FSYNC). `ACCEL_CONFIG2` (0x1D):
   `ACCEL_FCHOICE_B` = **0** and `A_DLPFCFG` = **7** (Dec2, 420 Hz 3 dB) — the
   accel DLPF must be *on*; bypassing it is what forbids the 0–500 Hz claim (§3.5).
5. **Ranges.** `ACCEL_CONFIG` (0x1C): `ACCEL_FS_SEL` = **11** (±16 g). `GYRO_CONFIG`
   (0x1B): `GYRO_FS_SEL` = **11** (±2000 dps), `FCHOICE_B` = 00.
6. **FIFO.** `FIFO_EN` (0x23): `ACCEL` = 1, `GYRO_XOUT`/`GYRO_YOUT`/`GYRO_ZOUT` = 1,
   `TEMP_OUT` = 1, all `SLV_*` = 0 (the AK8963 is out of scope). `USER_CTRL` (0x6A):
   `FIFO_EN` = 1, `FIFO_RESET` = 1 once to clear. **The MPU-9250 has no FIFO
   watermark interrupt** — it exposes only `WOM` and `FIFO_OFLOW` in `INT_ENABLE`
   (0x38). Mode M therefore polls: read `FIFO_COUNTH/L` (0x72/0x73) and drain when
   the count crosses the 16-record target (§3.7.2), and enable `FIFO_OFLOW` as an
   error interrupt. Do **not** copy the carrier tier's watermark-interrupt scheme
   across; it does not exist on this part.
7. **Aux I²C.** `I2C_MST_CTRL` (0x24) `I2C_MST_CLK` left at reset and the master left
   **disabled** — no auxiliary transactions, so the magnetometer cannot inject
   `EXT_SENS_DATA` records into the FIFO and desynchronise the 14-byte packet
   assumption.
8. **Drain loop.** Read `FIFO_COUNTH/L` (0x72/0x73), burst-read `FIFO_R_W` (0x74),
   parse 14-byte records (accel 6, gyro 6, temp 2), applying the FIFO temperature
   formula `T = FIFO_TEMP_DATA/2.07 + 25` (§5.3). `FIFO_OFLOW` in
   `INT_STATUS` (0x3A) → raise `FLAG_FIFO_OVERRUN`.
9. **Timestamp each frame** with the host monotonic clock at drain time, exactly as
   §4.1 step 11 — the same interpolation and the same `FLAG_TIME_ESTIMATED` rule.

> **The two sequences are not interchangeable, and the register names alone make that
> checkable.** If the code cannot say which of §4.1/§4.2 it is running, it cannot know
> which part it is talking to — which is the same failure the `WHO_AM_I` guard exists
> to catch.

---

## 5. Data format — IMUCAP2

Frozen. `hardware/imu_capture.py` in this repo is the reference implementation; both
sides should import it or port it byte-for-byte.

### 5.1 Container

```
<one line of UTF-8 JSON header>\n
<N fixed 24-byte little-endian frames>
```

The file is append-only while recording: header first, then frames strictly in
acquisition order. A capture cut off mid-write is valid up to the last whole frame.

### 5.2 JSON header

Keys are emitted sorted and without whitespace, so two writers of the same recording
produce identical bytes.

| Key | Type | Meaning |
|---|---|---|
| `magic` | string | `"IMUCAP2"` — the version discriminator |
| `spec_version` | string | `"2.1"` — the version of *this document* |
| `frame_bytes` | int | `24` |
| `frame_struct` | string | `"<I7h3H"` — normative struct format |
| `endian` | string | `"little"` |
| `axes` | list[str] | `["ax","ay","az","gx","gy","gz"]` — order of the sample fields |
| `n_axes` | int | `6` |
| `fs_nominal` | float | configured ODR in Hz (mode A 4000.0, mode B 2000.0, mode M 1000.0) |
| `mode` | string | `"A"`, `"B"`, `"M"`, or `"v1"` for legacy captures — **required**; see §8.1 |
| `accel_fsr_g` | int | 2 / 4 / 8 / 16 |
| `gyro_fsr_dps` | float | 15.625 … 2000 |
| `scale_accel` | float | **m/s² per accel LSB** — the authoritative conversion |
| `scale_gyro` | float | **deg/s per gyro LSB** — the authoritative conversion |
| `accel_units` | string | `"m/s^2"` |
| `gyro_units` | string | `"deg/s"` |
| `temp_lsb_c` | float | `0.01` (°C per LSB in the frame's temperature field) |
| `start_unix_ns` | int | wall-clock start, ns since the Unix epoch (0 if unknown) |
| `device` | string | sensor part string, `"ICM-42688-P"` or `"MPU-9250"` |
| `notes` | object | free-form provenance — see §5.4 |

`scale_accel` / `scale_gyro` are written explicitly *and* derive from the FSR, so a
reader can use either; the explicit value wins. **Both supported parts use the same
scales**, so a reader is never in doubt about the conversion — but that is also why
a part mix-up is undetectable downstream and must be caught at start-up by
`WHO_AM_I` (§2.4). Datasheet values (identical in `DS-000347` tables 1/2 and
`PS-MPU-9250A-01` tables 1/2):

| Accel FSR | LSB/g | `scale_accel` (m/s²/LSB) |
|---|---|---|
| ±2 g | 16384 | 5.985504e-04 |
| ±4 g | 8192 | 1.197101e-03 |
| ±8 g | 4096 | 2.394202e-03 |
| **±16 g** | 2048 | **4.788403e-03** |

| Gyro FSR | LSB/(°/s) | `scale_gyro` (°/s per LSB) |
|---|---|---|
| ±125 dps | 262.0 | 3.816794e-03 |
| ±250 dps | 131.0 | 7.633588e-03 |
| ±500 dps | 65.5 | 1.526718e-02 |
| ±1000 dps | 32.8 | 3.048780e-02 |
| **±2000 dps** | 16.4 | **6.097561e-02** |

### 5.3 Frame — 24 bytes, `"<I7h3H"`

| Offset | Size | Type | Field | Meaning |
|---|---|---|---|---|
| 0 | 4 | uint32 | `t_us` | **monotonic microseconds since capture start** |
| 4 | 2 | int16 | `ax` | accel X, raw LSB (× `scale_accel` → m/s²) |
| 6 | 2 | int16 | `ay` | accel Y, raw LSB |
| 8 | 2 | int16 | `az` | accel Z, raw LSB |
| 10 | 2 | int16 | `gx` | gyro X, raw LSB (× `scale_gyro` → °/s) |
| 12 | 2 | int16 | `gy` | gyro Y, raw LSB |
| 14 | 2 | int16 | `gz` | gyro Z, raw LSB |
| 16 | 2 | int16 | `temp_c100` | temperature, **0.01 °C per LSB**, two's complement |
| 18 | 2 | uint16 | `seq` | per-recording frame counter, increments by 1, **wraps at 65536** |
| 20 | 2 | uint16 | `flags` | per-frame status, below |
| 22 | 2 | uint16 | `reserved` | write 0, ignore on read |

`flags` bits:

| Bit | Name | Meaning |
|---|---|---|
| 0 | `FLAG_FIFO_OVERRUN` | the sensor FIFO overflowed (`FIFO_LOST_PKT` incremented) before this frame |
| 1 | `FLAG_HOST_OVERRUN` | the host dropped frames of its own (write back-pressure) |
| 2 | `FLAG_TIME_ESTIMATED` | `t_us` was interpolated from the nominal rate, not measured |

`temp_c100`: the two parts report FIFO temperature differently and **both must be
converted to °C and re-quantised into this field at 0.01 °C/LSB**, so the file has
exactly one representation:

| Part | FIFO formula | Register-path formula |
|---|---|---|
| ICM-42688-P | `T = FIFO_TEMP_DATA/2.07 + 25` | `T = TEMP_DATA/132.48 + 25` |
| MPU-9250 | `T = (TEMP_OUT − 0)/333.87 + 21` (PS: 333.87 LSB/°C, 21 °C offset) | same register |

Frame size: 6 × int16 = 12 B samples + 4 B timestamp + 2 B temp + 2 B sequence
+ 2 B flags + 2 B reserved = **24 B**, exactly 3 × 8 B words, so a native or DMA
write needs no repacking. At mode A that is 96 000 B/s (96.00 kB/s, 5.76 MB/min,
345.6 MB/h, **8.294 GB/day**); at mode B, 48 000 B/s (2.88 MB/min, 4.147 GB/day);
at mode M, 24 000 B/s (1.44 MB/min, **2.074 GB/day**).

*Why not CSV:* at mode A, 8 comma-separated decimal fields of ~10 chars is
~0.18 MB/s — **4.4×** the binary frame — plus float-formatting precision loss, and it
is not append-safe on a device that can lose power mid-line. CSV stays available as
an **export** from the decoder, never as the capture format.
*Why not the sensor's own packet layout:* it is firmware-specific, has no monotonic
timestamp, no sequence counter, and no file-level provenance.

### 5.4 `notes` — provenance the analysis depends on

The analysis cannot turn a peak into "which wheel" without the vehicle and mount
geometry, so the header must carry it:

| Key | Meaning |
|---|---|
| `sensor` | part string, `"ICM-42688-P"` or `"MPU-9250"` |
| `who_am_i` | `"0x47"` or `"0x71"` — evidence the identity check ran (§2.4) |
| `i2c_addr` | `"0x68"` |
| `config` | the full register configuration: ODR, FSRs, AAF triplets / DLPF codes, UI order/BW, FIFO packet size and watermark |
| `mounting` | mount description: corner, rigid/soft, and which way each sensor axis points (§7) |
| `wheel` | `{"diameter_m": 0.60, "corner": "front-left", "count": 4}` |
| `vehicle` | free-form: make / model / tyre spec |
| `firmware` | git revision or build string of the logger |
| `flags_legend` | the `flags` bit meanings, so an old file stays self-describing |
| `band_hz` | the band the capture is valid over (**150** for mode M, 250 for B, 500 for A) — so a consumer cannot assume the widest one (§8.1) |

Anything unknown at capture time must be present with an explicit `null` rather than
omitted — an absent key and an unknown value are different facts.

### 5.5 Reading: permissive and version-aware

`decode()` dispatches on `magic`:

* `IMUCAP2` → §5.3;
* **`IMUCAP1`** — the earlier container (JSON header then `6 × int16` frames with an
  optional `uint32` µs timestamp; `CaptureMeta` in the CTG-9 workspace) is still
  accepted, with `temperature_c = NaN`, synthesised `seq`, and
  `FLAG_TIME_ESTIMATED` set when the file carries no timestamps. This is why
  `t_00f126bd` can analyse captures that already exist with no conversion step.

Unknown `magic` → raise; never guess.

---

## 6. File naming, rotation and storage

```
<device_id>/<YYYY-MM-DD>/<YYYYMMDD>T<HHMMSS>Z_<mode>_imu<N>.imucap
```

Example: `cartg-01/2026-10-15/20261015T141205Z_A_imu003.imucap`

* `<device_id>` — stable logger identity (ESP32-S3 MAC or an assigned name).
* `<mode>` — `A`, `B` or `M` (§3.4 / §3.5). The mode is part of the **filename** as
  well as the header, so a directory listing shows the band without opening a file.
* `<N>` — 3-digit, zero-padded, monotonically increasing per device; never reused and
  never reset by a reboot, so a power cut cannot silently overwrite a run.
* Always UTC (`Z`), always ISO-like and lexicographically sortable.
* A **sidecar** `<name>.json` holding the same header is written next to each file, so
  an operator can read the provenance without a decoder (optional; the header inside
  the file is authoritative).

**Rotation policy** — the mandatory bound is that no file may outlive the `uint32`
microsecond wrap (71.6 min):

| Mode | Rotate at | Duration | Files/hour | Wraparound margin |
|---|---|---|---|---|
| A (4000 Hz) | **64 MiB** | 11.7 min | ~5.1 | 6.1× |
| B (2000 Hz) | **64 MiB** | 23.3 min | ~2.6 | 3.1× |
| M (1000 Hz) | **64 MiB** | 46.6 min | ~1.3 | 1.5× |

Mode M's 46.6-min segment is the largest of the three and still leaves a **1.5×**
margin against the 71.6-min `uint32` wrap. That margin is the reason the segment size
is **not** raised for mode M even though its rate would allow a longer file: 96 MiB
would take 70.0 min and sit inside the wrap by only 2 %, which is not a margin, it is
a coincidence. **Keep 64 MiB for every mode** so the rule does not have to be
re-derived per tier.

Also rotate on the first of: discard/finalise command, mode change, or a free-space
threshold on the target volume. Never rotate mid-frame — close on a frame boundary.

**Power-loss safety**: `RecordingWriter` flushes every `flush_every` frames
(default 4000 = 1 s in mode A, 2 s in mode B, 4 s in mode M), so at most that much
data is lost and the file stays readable up to the last flushed frame. Set
`flush_every` to `odr` (one second) on a low-rate mode if the loss window matters
more than the write amplification. Call `close()` (which flushes) before power-down;
a capture that never got a clean close is still decodable.

* Storage sizing: mode A is **8.29 GB** per 24 h, mode B **4.15 GB**, mode M
  **2.07 GB**. Size the volume for ≥ 2× the intended run length plus one rotation
  buffer — which is the practical reason mode M is attractive for a long bench or
  road session even though it is the reduced band.
* If the volume cannot keep up, the firmware must set `FLAG_HOST_OVERRUN` — never
  silently thin the stream.

---

## 7. Physical mounting and axis mapping

### 7.1 Where and how

* **Rigid** mount to the **chassis / sprung mass** — a soft or foam mount low-passes
  exactly the vibration being measured and corrupts the amplitude. A rigid mount has
  a resonance well above 500 Hz; a rubber one does not.
* Mount the board **flat and square** to the vehicle's longitudinal axis, so the axis
  mapping of §7.2 is a pure sign/label change and not a rotation. If the board cannot
  be squared, record the actual angles in `notes.mounting` and accept that the
  analysis must rotate the axes before comparing amplitudes.
* Prefer a location that is stiff in all three axes (a floor pan or a structural
  cross-member) over a large flat panel, which can have a low-frequency drum mode of
  its own that would masquerade as a wheel order.
* Record the mount in the header. **Absolute amplitude is only comparable between
  recordings taken with the same mount** — which is why the header, not the operator's
  memory, carries it.

### 7.2 Sensor axes → vehicle frame (frozen)

The ICM-42688-P's pin diagram (§4.1, Figure 5, p.20) fixes the sensor triad. Mounted
as in §7.1:

| Sensor axis | Points | Vehicle meaning |
|---|---|---|
| `+X` (ax) | forward, along the car's longitudinal axis | longitudinal accel (drive/brake) |
| `+Y` (ay) | to the right, from the driver's seat | **lateral** — left/right imbalance signature |
| `+Z` (az) | down (toward the road) | **vertical** — the primary imbalance axis |
| `gx` | roll rate, about the longitudinal axis | left/right loading |
| `gy` | pitch rate | braking / acceleration pitch |
| `gz` | yaw rate | turning; gates "is the car going straight" |

With this mapping a wheel imbalance appears **predominantly on `az`** (the vertical
centrifugal force) with an **`ay` component 90° out of phase**, which is also how the
side is resolved. A static 1 g offset appears on `az` and must be removed by the
analyser (the CTG-9 pipeline already detrends per axis) — do **not** "fix" it in
firmware: the raw DC level is itself diagnostic of mount tilt.

**Sign convention caveat (⚠ documented assumption):** this table assumes the board is
mounted with the sensor's own arrows as in Figure 5. If the physical board is rotated
(e.g. the LGA sits component-side-down), the *signs* — and possibly an X/Y swap —
invert. Firmware cannot know this; it must be recorded in `notes.mounting` at
installation and the analyser must apply the same convention. Getting it wrong flips
the left/right inference but does **not** affect the primary "which order dominates /
is there an imbalance at all" diagnosis, which is sign-independent.

**The mapping is per-part, and the bench breakout adds an assumption.** For an
MPU-9250 breakout the triad is defined by the module's own silkscreen, and a
GY-521-class board's printed axes need not match the bare die's (PS §4.4). So for
mode M the axes must be read off the physical board and written into
`notes.mounting`; where the label is missing or illegible, note that it is unknown
rather than guessing. This is the same ⚠ caveat as the carrier tier, one step stronger.

### 7.3 Wheel geometry in the header

`notes.wheel` carries `diameter_m`, `corner` and `count`; `notes.vehicle` carries the
type. Without `diameter_m` the analyser can still report a peak in Hz and the implied
`f_rot`, but cannot name a wheel. The pipeline in `t_00f126bd` must therefore **emit
the raw peak frequency and the implied vehicle speed** when geometry is missing,
instead of guessing a corner.

Note the physical limit recorded in the CTG-9 design doc: with four identical tyres
all wheels share one `f_rot`, so a **single** IMU cannot separate them
frequency-wise. One sensor can prove *that* there is an imbalance and at which order;
naming the corner needs different rolling diameters, a turn, or one IMU per corner.
That is a diagnosis-level caveat, not a format one — which is why the format stores
per-corner capability rather than assuming it.

### 7.4 Out of scope: the magnetometer

The MPU-9250 carries an AK8963 magnetometer (addr 0x0C on the sensor's auxiliary I²C
bus). It is **out of scope for CTG-9** and must not be enabled (§4.2 step 7). The D4
decision already recorded the reason: *"CTG-9 is wheel-imbalance FFT on raw accel,
which does not need a compass"*. Three concrete consequences of leaving it off:

* the FIFO packet stays a fixed 14 bytes (accel 6 + gyro 6 + temp 2), so the drain
  loop's record parser cannot desynchronise on an `EXT_SENS_DATA` record;
* the auxiliary I²C master adds no bus traffic to a link that is already the
  constraint at 400 kHz;
* the magnetometer's ~280 µA and its 8 Hz repetition rate stay out of the current
  budget and out of the sample stream.

If compass heading is ever wanted (e.g. to orient the car for localisation), that is
a **new card with new scope** — not a flag to flip.

---

## 8. Consumer contract

### 8.1 Analysis pipeline (`t_00f126bd`)

* Read via `imu_capture.load()`; never re-implement the parse.
* Use `t_us` for time: rate = `(n−1)/(t_us[-1] − t_us[0])`. Never assume `fs_nominal`.
* **Read `mode` and `notes.band_hz` before analysing, and pass them through to the
  report.** A mode-M capture is valid only to 150 Hz; treating it as a mode-A capture
  invents 350 Hz of band that was never measured and where the DLPF's real response is
  unquantified (§3.5.1). The report must name the mode/band it was analysed over.
* Convert with `scale_accel` / `scale_gyro` from the header, not a hard-coded LSB.
* Skip or flag frames with `flags & (FIFO_OVERRUN | HOST_OVERRUN)`; a run with
  > 0.1 % flagged frames is degraded and the report must say so.
* The `seq` counter gives the true drop count; a gap means lost frames and therefore a
  sampled timebase — use the timestamps rather than assuming uniform spacing.
* Degrees/s vs rad/s: `scale_gyro` is °/s. Convert once at load and stay consistent.
* Report the achieved frequency resolution (`fs/n` for the chosen window) alongside
  every peak, as the existing CTG-9 spectrum code does.
* **Apply the mode's passband correction before quoting an absolute amplitude** —
  §3.4.1's cascade table for modes A/B, §3.5.1's DLPF table for mode M — or quote the
  frequency only. **Never compare an absolute amplitude across two different modes**:
  the two parts have different noise densities (65 vs 300 µg/√Hz) as well as different
  filters, so a mode-M amplitude is not a mode-A amplitude.

### 8.2 ContextVM exposure (`t_6fc86c46`)

`imu.raw_window` and `imu.vibration_spectrum` must pass through the header's units and
`fs_nominal`, plus `mode`, `device`, `notes.band_hz` and `notes.wheel`, so a remote
consumer reads the same contract as a local one. Downlink decoded bins rather than raw
bytes: 24 B/frame is fine on a bus but wasteful on a metered link.

### 8.3 Tolerances for the bench validation (`t_fbd7baa6`)

A capture passes when:

| Check | Threshold |
|---|---|
| `WHO_AM_I` | **== the value `MODE_SENSOR[mode]` expects** — 0x47 for A/B, 0x71 for M |
| achieved rate vs `fs_nominal` | within **1 %**, measured from the timestamps |
| dropped frames (`seq` gaps + `flags` bits 0/1) | **< 0.1 %** of frames |
| decoder round-trip | `load(encode(rec))` equal within one LSB |
| injected tone recovery | peak within one FFT bin of the injected frequency |
| passband | droop monotone and ≤ 1 dB across the **mode's** band: 0–500 Hz (A) / 0–250 Hz (B) / 0–150 Hz (M) |
| identity mismatch | recording with the wrong part **must abort**, i.e. `check_identity` raises |

Every threshold above is per-mode, not global. In particular the passband row must be
evaluated against `notes.band_hz` from the capture's own header, never against a
hard-coded 500 Hz — that is exactly the assumption mode M exists to prevent.

---

## 9. Worked example

```python
from hardware.imu_capture import load, MODE_SENSOR

rec = load("cartg-01/2026-10-15/20261015T141205Z_M_imu003.imucap")
h = rec.header
print("mode %s on %s (who_am_i %s) | band %.0f Hz"
      % (h.mode, h.device, h.notes["who_am_i"], h.notes.get("band_hz", 0)))
assert h.device == MODE_SENSOR[h.mode]          # the guard, re-checked on read
print("frames %d | rate %.1f Hz (nominal %.0f)"
      % (rec.n, rec.achieved_fs(), h.fs_nominal))
print("duration %.2f s | dropped %d | overruns %d"
      % (rec.duration_s(), rec.dropped_frames(), rec.overrun_frames()))
az = rec.axis("az")                      # already m/s^2
print("az mean %.3f m/s^2 (gravity + tilt) | peak %.3f m/s^2" % (az.mean(), abs(az).max()))
```

`hardware/imu_capture.py` also ships `make_synthetic(mode, seconds, wheel_hz,
imbalance)`, producing a contract-valid capture with a known injected imbalance — used
by the test suite and available to `t_fbd7baa6` to dry-run the chain without hardware.
It stamps the provenance from the mode (`sensor`/`who_am_i` follow `MODE_SENSOR`), so
a synthetic mode-M capture cannot accidentally look like an ICM-42688-P one.

---

## 10. Sources

| Claim area | Source | How obtained |
|---|---|---|
| **Carrier part**: package, electrical/ODR/FSR/noise/FIFO/filter/timestamp/register facts | **TDK InvenSense ICM-42688-P datasheet, `DS-000347` revision 1.8** (112 pp., Rev. Date 07/27/2023) | the vendor URL `invensense.tdk.com/wp-content/uploads/2020/04/ds-000347_icm-42688-p-datasheet.pdf` now serves an HTML page; the PDF was fetched from the Web Archive (`web.archive.org/web/2024id_/…`), 1 855 049 bytes, sha256 `7f904aec7d6b0b4a68b5038da1c29ee2919d895c07a1fd2c697346159e5d3fc9`, and read with `pdftotext -layout` |
| **Bench part**: electrical, ODR/DLPF tables, FSR/sensitivity, noise, FIFO, I²C limits | **`PS-MPU-9250A-01` revision 1.1** (product spec, 06/20/2016) | Web Archive: `web.archive.org/web/2024id_/https://invensense.tdk.com/wp-content/uploads/2015/02/PS-MPU-9250A-01-v1.1.pdf` → 962 503 bytes; read with `pdftotext -layout` |
| **Bench part**: register map, register addresses, DLPF/FIFO/interrupt bit fields | **`RM-MPU-9250A-00` revision 1.6** (register map, 01/07/2015) | Web Archive: `web.archive.org/web/2023id_/https://invensense.tdk.com/wp-content/uploads/2015/02/RM-MPU-9250A-00-v1.6.pdf` → 977 841 bytes; read with `pdftotext -layout` |
| **Bench module identification** (MPU-9250, photo references, pins, I²C address) | `~/repos/tollgate-esp32/docs/HARDWARE-INVENTORY.md` §6, header *"all 13 photos analyzed via GLM-4.6V vision model"* | repo read; commits `b9559be`, `83b9c56`, `e2fe737` |
| Carrier part choice (ICM-42688-P, 6-axis, I2C, SPI unpopulated) | `~/repos/car-tollgate-hardware/DECISIONS.md` D4 + `decisions.json` (card `t_eaba2c97`, ratified 2026-09-27, commit `33af301`, enforced by `tests/test_decisions_ledger.py`) | repo read |
| Photo *pixels* unavailable on this host; no camera | `task_attachments` across all board DBs (`sqlite3`, `mode=ro`); bounded image sweeps of `~`, `~/repos`, `~/.hermes`, `/mnt`; `/dev/video*`, `/sys/class/video4linux`, `v4l2-ctl`, `lsusb` | executed on this host, 2026-10-15 |
| Wheel-frequency arithmetic, DLPF/AAF filter matrices, LSB scales, storage/bus sizing | computed for this spec; every printed figure is reproducible from the datasheet tables and the formulas in §3 | scratch script `mpu9250_specmath.py` (not part of the deliverable) |

⚠ **Sources that could *not* be obtained, stated so the gap is visible.** The
**MPU-6500** product specification and register map are not in the Web Archive: the
canonical paths (`…/2015/02/PS-MPU-6500A-01-v1.1.pdf`, `…/RM-MPU-6500A-00-v1.0.pdf`)
return 404 from the archive, and a CDX query finds only product-marketing pages for
that part. §1.1's *"assumed MPU-9250"* therefore rests on register/spec compatibility
reasoning and the inventory's own pin evidence, not on a fetched 6500 datasheet. If
the part turns out to be a 6500, the capture contract is **unchanged** (same
`WHO_AM_I`, same scales, same ODR/DLPF tables) — but the noise figures in §2.1.2
would need re-checking, and the magnetometer entry would drop.

⚠ **Re-verify at fab/installation time:** the Web-Archive URLs are snapshots, not
contracts. If either datasheet is re-issued, re-check the register names in §4 and the
AAF/UI/DLPF tables in §3 against the new revision before layout is frozen.

---

## 11. Open items

**None of these block firmware in either tier.** They are listed with the tier they
affect so the ordering is unambiguous.

| # | Item | Tier | Owner | Blocks |
|---|---|---|---|---|
| 1 | Photo or 2-net measurement confirming the bench module and its I2C strap (§1.2) | bench | bench | nothing — the `WHO_AM_I` guard covers it |
| 2 | Confirm `MPU-9250` vs `MPU-6500` from the chip marking (§1.1) | bench | bench | nothing (contract unchanged); only §2.1.2 noise figures |
| 3 | Confirm tyre diameter and top speed; re-run §3.2 if `D < 0.5 m` and top speed > 150 km/h | both | `t_fbd7baa6` | nothing |
| 4 | Confirm the physical board orientation so `notes.mounting` signs are right (§7.2) | both | installation | corner-level attribution only |
| 5 | **Measure the mode-M DLPF stopband** and confirm the 2nd-order model (§3.5.1) | bench | `t_fbd7baa6` | quantitative cross-tier amplitude comparison |
| 6 | Measure the UI filter's real stopband, then decide whether to widen the AAF toward 3979 Hz for a flatter 500 Hz (§3.4.1) | carrier | `t_fbd7baa6` | nothing |
| 7 | Decide whether a 32.768 kHz CLKIN oscillator earns a place on the next PCB revision (§3.6) | carrier | `t_64d238d1` | nothing |
| 8 | Decide whether CTG-11 should fit an MPU-9250-compatible footprint as a fallback | carrier | `t_64d238d1` | nothing — a PCB change, not a contract change |

Item 8 is raised **because of** this card's finding: the two parts are not
interchangeable in band, so a board that can accept only the ICM-42688-P cannot be
used to validate against the bench module. Whether that matters is a fab decision.

## 12. Change log

| Version | Date | Change |
|---|---|---|
| **2.1** | 2026-10-15 | **Corrected identification.** v2.0 asserted "no photographs exist on any board" and adopted the carrier part (D4) as *the* answer. A durable **photo-derived** inventory does exist (`tollgate-esp32/docs/HARDWARE-INVENTORY.md`, 13 photos analysed 2026-07-06) and it identifies the **bench** module as an **MPU-9250** — a different part from the carrier's ICM-42688-P. Added the two-tier framing (§0/§1), the **mode M** contract for the MPU-9250 with the proof that it **cannot** carry the card's 0–500 Hz band (§3.5), its register sequence (§4.2), its bus/FIFO budget (§3.7.2), the `WHO_AM_I` identity guard (§2.4) and its reference-codec implementation + tests, and magnetometer exclusion (§7.4) |
| 2.0 | 2026-10-15 | Frozen contract: ICM-42688-P confirmed from the ratified decision record; modes A (4000 Hz / 0–500 Hz) and B (2000 Hz / 0–250 Hz) with the ODR + filter choice justified on the documented AAF response against the band; **IMUCAP2** 24-byte frame; mounting and axis mapping; rotation and provenance policy; reference codec + tests |
| 1.x (implicit) | — | The CTG-9 workspace's `IMUCAP1` (header + `6×int16` + optional `uint32` µs, no temperature or sequence). **Still readable** by the reference decoder (§5.5) |
