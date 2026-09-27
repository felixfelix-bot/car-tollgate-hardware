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
rationale plus what was rejected. `docs/PCB-DESIGN-PLAN.md` is the full
architecture, power budget (2 A LTE burst), automotive protection design, and
JLCPCB order parameters. `docs/TOOLCHAIN.md` is the KiCad 9.0 + SKiDL setup for
this host.

## Status — honest

| Step | State |
|---|---|
| Architecture + power budget + part selection | **done** — `docs/PCB-DESIGN-PLAN.md` |
| Hardware model decisions D1–D7 | **ratified** — `DECISIONS.md` |
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
README.md                 this file
docs/PCB-DESIGN-PLAN.md   architecture, power budget, protection, JLC parameters
docs/TOOLCHAIN.md         KiCad 9.0.3 + SKiDL install and headless commands
hardware/                 SKiDL source and KiCad project (empty — next step)
fab/                      Gerbers, BOM, CPL, ZIP for JLCPCB (empty)
```

## Provenance

- Decision card: `t_eaba2c97`, kanban board `car-tollgate`.
- Design card it unblocks: `t_64d238d1` (CTG-11).
- Source plan: `~/.hermes/kanban/boards/car-tollgate/attachments/t_64d238d1/PCB-DESIGN-PLAN.md`
  (md5 `41c53b94bc331519ffa9c39ccbc52fb3`), authored by `worker-tollgate` run 11.
