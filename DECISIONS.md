# CTG-11 — Ratified hardware decisions (D1–D7)

Decision card: `t_eaba2c97` (board `car-tollgate`) — owner: manager
Supersedes the `PENDING CONFIRMATION` markers in `docs/PCB-DESIGN-PLAN.md` §3/§9.
Unblocks: `t_64d238d1` (CTG-11 PCB design + JLCPCB order).

Ratified: 2026-09-27, by the manager profile, under the card's own rule —
*"Every row accepts 'use the recommended default'"* — after the operator
clarify timed out with no answer. **These are defaults, not operator
preferences. Any row may be overridden by the operator before fab.**

Region is not a guess: the TollGate firmware this board serves pins the
regulatory domain (`esp_wifi_set_country_code("DE")` in the ESP32-S3 firmware,
`~/repos/balloon/AGENTS.md`), and SilentLink is an EU-issued IoT SIM. So
`region TBD` resolves to **EU**.

---

## D1 — MCU: ESP32-S3-WROOM-1 N16R8, dual-footprint

| | |
|---|---|
| Part | `ESP32-S3-WROOM-1-N16R8` (16 MB flash / 8 MB PSRAM) |
| Footprint | Castellated WROOM-1 (production) **+** 2x18 2.54 mm female header mirroring `ESP32-S3-DevKitC-1` (prototype) |
| Path select | 0 Ω / solder-bridge strapping; identical net names + firmware on both builds |
| Reserved | GPIO19/20 = native USB D-/D+ |
| Avoid for peripherals | GPIO0, 3, 45, 46, 39–42 (boot / JTAG / USB-JTAG strapping) |

Reuse the solder-bridge footprint already proven in the balloon tracker:
`tracker/hardware/hub_board_diy/custom.pretty/SolderBridge_2Pad.kicad_mod`.

## D2 — Cellular: SIM7600E **bare SMD primary** + socketed breakout path

The card body says "GSM shield" (a socketed board) while a footprint is a
literal reference to a part. Both are honoured: **production populates the bare
SMD module, prototype accepts a socketed breakout over the same nets.**

| | |
|---|---|
| Part (production) | `SIM7600E` (LTE Cat-4, Europe/EU bands) bare SMD — NOT `SIM7600A` (Americas) |
| Region / bands | **EU** — LTE B1/B3/B7/B8/B20 (quad-band GSM fallback as fitted) |
| Part (prototype) | 2x10 2.54 mm header carrying a `SIM7600E` breakout daughterboard on the same UART/PWRKEY/RST/STATUS nets |
| Rejected | **SIM800/SIM900 (2G)** — EOL in the EU; do not design in |
| Low-power alt (not chosen) | `SIM7080G` (Cat-M1/NB-IoT) — revisit only if a data-only, low-bandwidth cost profile is wanted |
| Supporting parts | U.FL antenna pad (external antenna), nano-SIM holder (LCSC C7091106-class), ≥470 µF low-ESR bulk at the module, PWRKEY/RESET/STATUS nets |

Rationale for socket: this is the only RF-sensitive, 2 A-burst, mains-costly
subsystem. Bare SMD removes a daughterboard height/socket sourcing risk but
requires a controlled-impedance antenna feed and tight burst loop. The socket
path lets a prototype be brought up without committing to that layout.

## D3 — GNSS: ATGM336H, bare SMD, external antenna

| | |
|---|---|
| Part | `ATGM336H-5N` (M10-class, u-blox NMEA-compatible) |
| Footprint | Bare SMD module, 4-pin control + UART2 to the ESP32-S3 |
| Antenna | External active antenna via U.FL/SMA (no integrated patch) |
| Alt (not chosen) | u-blox `NEO-M8N` / `M9N` — pick only if sub-2 m accuracy or u-blox-specific timing features become a hard requirement |
| Why changed from plan default | Cost and JLCPCB basic/extended stockability; the plan offered ATGM336H as an equally valid alternative, and no requirement in CTG-8/CTG-11 demands u-blox accuracy class |

## D4 — IMU: ICM-42688-P, 6-axis, I2C

| | |
|---|---|
| Part | `ICM-42688-P` |
| Axes | 6 (accel + gyro) |
| Bus | I2C (SPI left unpopulated; carrier straps I2C addr) |
| Alt (not chosen) | `LSM6DS3TR-C` (cheaper); `LSM9DS1` (9-axis) **only if magnetometer heading is added to CTG-9 scope** — CTG-9 is wheel-imbalance FFT on raw accel, which does not need a compass |

## D5 — Input power: 12 V automotive

| | |
|---|---|
| Input | 12 V automotive (nominal), 6–18 V ride-through |
| Protection (per plan §5) | 2 A fuse/PTC → reverse-polarity (LM74610 ideal-diode + N-FET, or P-MOSFET high-side) → load-dump TVS (SMBJ/SMCJ 24–33 V class, ISO 7637-2) → CM choke + pi-filter + bulk cap |
| Rails | Buck #1 `3.8 V @ ≥3 A` rated for the modem (2 A bursts); Buck #2 `3.3 V @ 0.6 A` for logic |
| Rejected | USB 5 V-only — this is a vehicle-mounted hub; the plan's §5 protection stays in scope |

## D6 — Board: **80 x 60 mm**, 2-layer FR-4, 4x M3

Sized to `80 x 60 mm` rather than `50 x 50 mm`: 50x50 cannot host the
WROOM-1 + SIM7600 bare SMD **and** the DevKitC-1 2x18 header **and** the modem
socket breakout **and** the SIM holder with the required antenna keep-outs.
Mounting: 4x M3 corner holes, 3.5 mm from edge; no enclosure CAD in scope for
this card (enclosure is a separate decision if one is wanted).

## D7 — No extra interfaces

No CAN, no RS232 transceiver, no SD, no RTC beyond the ESP32-S3 and GNSS time.
Rationale: none is referenced by CTG-11's requirements nor by CTG-8/CTG-9/CTG-10;
adding nets after layout is the expensive failure mode the plan warns about.
If CAN bus is later wanted (OBD-II telemetry), that is a **v2 respin**, not a
silent addition — say so and it gets its own card.

---

## §11 open questions — resolved

| # | Question | Resolution |
|---|---|---|
| 1 | Confirm/override D1–D7 | Ratified above; operator override still allowed before fab |
| 2 | Deployment region | **EU** (evidence: firmware country code `DE`, EU IoT SIM) → `SIM7600E` |
| 3 | Programming path | Native USB (GPIO19/20 reserved) **and** UART0 header pads, since the DevKitC-1 prototype path already exposes both |
| 4 | **Target repository** | **`felixfelix-bot/car-tollgate-hardware`** — created 2026-09-27, this repo. Clone at `~/repos/car-tollgate-hardware`. Not inside `~/repos/balloon` (that repo carries committed nsec material in its history and is unpushable — see the fleet note) and not a subtree of it |
| 5 | KiCad 9.0 + SKiDL host | **This host (DQ05)** — `kicad` 9.0.3 is in the Ubuntu 25.10 default repos, so no PPA is required. See `docs/TOOLCHAIN.md` |

## Human-only boundary (unchanged)

Placing the JLCPCB order requires account login, a payment method, and a
shipping address, and spends real money. **No agent can or should do it.** The
worker's terminal deliverable is: SKiDL source → netlist → placed/routed board →
DRC/ERC clean → Gerbers + BOM + CPL + upload-ready ZIP + the exact order
parameters (plan §10). Then the operator clicks checkout.
