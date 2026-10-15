# car-tollgate-hardware

Car TollGate Hub PCB — CTG-11. A 12 V automotive carrier board integrating an
ESP32-S3, an LTE Cat-4 modem, GNSS, and an IMU: the vehicle-side hub that
TollGate uses to sell/bridge mobile data from a car.

Board: **80 x 60 mm**, 2-layer FR-4, 1.6 mm, JLCPCB standard assembly.

| Subsystem | Part | Decided in |
|---|---|---|
| MCU | ESP32-S3-WROOM-1 **N16R8** (+ DevKitC-1 2x18 header on the prototype build) | D1 |
| Cellular | **SIM7600E** bare SMD (EU bands) + socket header for a breakout on the prototype build | D2 |
| GNSS | **ATGM336H-5N** bare SMD, external antenna | D3 |
| IMU | **ICM-42688-P**, 6-axis, I2C | D4 |
| Input | **12 V automotive**, fused + reverse-polarity + load-dump TVS + CM choke | D5 |
| Board | **80 x 60 mm**, 4x M3 | D6 |
| Extras | none (no CAN/RS232/SD/RTC) | D7 |

`DECISIONS.md` is the authority for every footprint choice and carries the
rationale plus what was rejected. `decisions.json` is the **machine-readable**
form of the same table — the SKiDL source (`hardware/`) reads that, not the
Markdown — and `tools/check_decisions.py` fails if the two ever disagree.
`docs/PCB-DESIGN-PLAN.md` is the full architecture, power budget (2 A LTE
burst), automotive protection design, and JLCPCB order parameters.
`docs/TOOLCHAIN.md` is the KiCad 9.0 + SKiDL setup for this host.

`docs/imu_capture_spec.md` is the **CTG-9 IMU capture contract** (spec v2.1):
sensor configuration, the ODR/filter decision, mounting frame, and the
`IMUCAP2` on-disk format. `hardware/imu_capture.py` is its reference codec, so
firmware and analysis share one definition instead of re-parsing prose.

It covers **two sensors**, because the project has two: the **MPU-9250** on the
bench (photo-identified) and the **ICM-42688-P** this board will carry (D4).
Mode M serves the former at 0–150 Hz; modes A/B serve the latter at 0–500/0–250 Hz.
The bench part *cannot* do 0–500 Hz with a documented anti-alias filter, which is
why the distinction is in the contract and not in a footnote.

## Verification

The decisions and the capture contract are checked, not trusted:

```bash
python3 -m pytest tests/ -q --cov=. --cov-report=term-missing   # 65 tests
python3 tools/check_decisions.py                                # standalone gate
python3 hardware/imu_capture.py                                 # codec self-check
```

`tests/test_decisions_ledger.py` asserts the ratified tokens independently of
both the ledger and the Markdown, that every decision has a section in
`DECISIONS.md`, that the region carries its evidence, that the shipped design
plan is byte-identical to the card's attachment (modulo the RATIFIED banner),
and that no netlist/Gerber/order is claimed that was not produced.
`tests/test_imu_capture_spec.py` independently re-derives the datasheet
constants, the AAF/UI filter settings, the wheel-frequency arithmetic and the
storage/bus budget, round-trips the codec, and pins the spec's claims (including
the fact that a 1 kHz ODR cannot carry a 500 Hz band). CI runs the same
commands (`.github/workflows/docs-contract.yml`).

## Status — honest

| Step | State |
|---|---|
| Architecture + power budget + part selection | **done** — `docs/PCB-DESIGN-PLAN.md` |
| Hardware model decisions D1–D7 | **ratified** — `DECISIONS.md` |
| IMU capture contract (CTG-9) | **frozen** — `docs/imu_capture_spec.md` + `hardware/imu_capture.py` |
| Toolchain install (KiCad 9.0.3 + SKiDL) | **pending** — apt package confirmed available, not yet installed |
| SKiDL source (`hardware/hub.py`) | **not started** |
| Netlist → place → route | **not started** |
| DRC / ERC clean | **not started** |
| Gerbers + BOM + CPL | **not started** |
| JLCPCB order | **human step** — account + payment + shipping address |

Nothing here is fabricated: there is no netlist, no board file, no Gerber, and
no order yet. This repo was created at the moment the hardware decisions were
ratified so the board has a durable home that is not a scratch workspace.

## Layout

```
DECISIONS.md              ratified D1-D7 + rationale + rejected alternatives
decisions.json            machine-readable form of the same decisions (tooling reads this)
README.md                 this file
docs/PCB-DESIGN-PLAN.md   architecture, power budget, protection, JLC parameters
docs/TOOLCHAIN.md         KiCad 9.0.3 + SKiDL install and headless commands
docs/imu_capture_spec.md  CTG-9 IMU capture contract (sensor config, format, mounting)
hardware/imu_capture.py   reference IMUCAP2 codec (writer, reader, synthetic source)
tests/                    docs-contract tests + capture-contract tests
tools/check_decisions.py  standalone gate: ledger, region, plan md5, no fake fab output
fab/                      Gerbers, BOM, CPL, ZIP for JLCPCB (empty — nothing fabricated)
```

## Provenance

- Decision card: `t_eaba2c97`, kanban board `car-tollgate`.
- Design card it unblocks: `t_64d238d1` (CTG-11).
- Capture-contract card: `t_9ae1a5c7` (CTG-9), closes the "candidate modules
  awaiting photo identification" question in `t_7c5a719c`. **Two sensors**, which is
  the point: the module on the bench is an **MPU-9250** (identified from the 13
  photographs recorded in `tollgate-esp32/docs/HARDWARE-INVENTORY.md`), while this
  board carries the ratified D4 **ICM-42688-P**. They are not interchangeable — the
  MPU-9250 cannot carry mode A's 0–500 Hz band — so the spec freezes three modes
  (A/B on the carrier part, M on the bench part) and a `WHO_AM_I` guard.
- Source plan: `~/.hermes/kanban/boards/car-tollgate/attachments/t_64d238d1/PCB-DESIGN-PLAN.md`
  (md5 `41c53b94bc331519ffa9c39ccbc52fb3`), authored by `worker-tollgate` run 11.
