# CTG-11 — Car TollGate Hub PCB — Design Plan & JLCPCB Order Specification

> **[RATIFIED 2026-09-27]** The §9 decision matrix below has been answered — see
> [`../DECISIONS.md`](../DECISIONS.md) in this repository, which is now the
> authority for every footprint choice (D1–D7, region EU, target repo,
> toolchain host). The `PENDING CONFIRMATION` markers in §3 and the "TBD" cells
> in §9 are kept verbatim below as the historical record of what was asked; read
> them as *resolved by DECISIONS.md*. Two defaults were changed on evidence:
> D2 region → EU (`SIM7600E`, not `SIM7600A`) and D3 → `ATGM336H` over
> `NEO-M8N`. D6 was fixed at 80x60 mm. Nothing has been netlisted, routed, or
> ordered yet.

Card: `t_64d238d1` (board `car-tollgate`)
Author: worker-tollgate (kanban run 11, 2026-09-27)
Status: **DRAFT — blocked on hardware model confirmation (§9). No netlist, no
Gerbers, no order placed.** This document is the named deliverable
(`docs/PCB-DESIGN-PLAN.md`) and is the durable artifact for the card.

> **Read this first.** Every part number below is an *engineering-recommended
> default*, marked **PENDING CONFIRMATION**. A footprint is a literal reference
> to a confirmed part number — generating the SKiDL source or routing the board
> before §9 is answered would be fabrication, not design. The card body itself
> states this blocker ("Blocked on hardware model confirmation for exact
> footprints"); §9 makes it concrete and answerable.

---

## 0. Card-title scope vs what is actually executable

| Requested | Executable by this worker? | Status |
|---|---|---|
| Design the PCB | partially — architecture + plan yes; netlist/layout needs confirmed footprints | plan delivered, netlist blocked |
| Order from JLCPCB | **no** — needs account login, payment method, shipping address | human step (§10) |

---

## 1. Requirements

A car-mounted telematics hub ("TollGate Hub") integrating:

- **ESP32-S3** — main MCU (Wi-Fi/BLE, dual-core, UART/SPI/I2C headroom).
- **GSM/LTE modem** — cellular uplink (data; SMS optional).
- **GPS** — position / NMEA.
- **IMU** — motion, vibration, heading.
- **Dedicated buck converter** — sized for the modem's ~2 A transmit bursts so TX
  does not brown-out the 3.3 V logic rail (this is the single most important
  power-design constraint on the board).

Environment: automotive supply (12 V nominal, hostile transients) unless §9-D5
selects a USB-only 5 V path. Mounted in a moving vehicle → vibration,
temperature swing, and a charging source (Anker Solix) in the same system.

## 2. Block diagram

```
             +---------------- 12 V automotive (or 5 V USB, TBD §9-D5) ----------------+
             |                                                                          |
             v                                                                          v
  [Fuse/PTC -> reverse-polarity -> load-dump TVS -> CM choke + pi filter + bulk C]
             |
     +-------+--------+
     |                |
     v                v
  BUCK #1 MODEM   BUCK #2 / LDO LOGIC
  3.8 V @ 2 A pk  3.3 V @ 0.6 A
  (isolated)      (ESP32-S3 + GPS + IMU)
     |                |
     v                |
  [GSM/LTE modem]     |
     |  UART + PWRKEY/RST/STATUS
     +----------------+-----> [ESP32-S3] <--- I2C --- [IMU]
                                    ^
                                    +-------- UART (NMEA) --- [GPS]
```

## 3. Component selection — RECOMMENDED DEFAULTS (PENDING CONFIRMATION)

### 3.1 MCU — ESP32-S3
- **Rec:** ESP32-S3-WROOM-1 **N16R8** (16 MB flash, 8 MB PSRAM), onboard PCB
  antenna. Most common variant, best JLCPCB assembly support; PSRAM helps
  TLS/cellular stacks.
- **Alt:** WROOM-1**U** (external U.FL antenna) or ESP32-S3-MINI-1 if area- or
  height-constrained.
- **Dual-footprint (task body: "dev header + bare module"):**
  - **Production build** — populate the WROOM-1 castellated module footprint.
  - **Prototype build** — leave WROOM-1 unpopulated; populate a female 2x18
    header footprint mirroring ESP32-S3-DevKitC-1 and plug in a dev board.
  - Shared GPIO net names + 0 Ω / solder-bridge strapping select the active path,
    so firmware is identical across both builds. (Solder-bridge footprint exists
    in the balloon tracker project — reuse
    `tracker/hardware/hub_board_diy/custom.pretty/SolderBridge_2Pad.kicad_mod`.)
- Strapping pins to avoid for peripherals on ESP32-S3: **GPIO0, 3, 45, 46, 39–42**
  (boot / JTAG / USB-JTAG); reserve GPIO19/20 for native USB D-/D+.

### 3.2 Cellular modem — "GSM shield"
- The card body says "GSM shield" and CTG-1 says "SIM800L/SIM7600" — ambiguous
  between (a) a bare SMD module on our PCB and (b) a prefabricated modem board
  on a socket. **Footprint differs fundamentally — §9-D2 must pin this down.**
- **Rec:** SIMCom **SIM7600** (LTE Cat-4) as a **bare SMD module**; **2G
  (SIM800/SIM900) is EOL** in most regions and should not be designed in.
- Low-power/low-data alt: **SIM7080G** (Cat-M1/NB-IoT).
- Sub-variant (SIM7600A/E/H/SA) depends on deployment region LTE bands (§9-D2).
- Requires: U.FL/SMA antenna footprint, nano-SIM holder (e.g. LCSC
  C7091106-class), PWRKEY/RESET/STATUS nets, and bulk capacitance at the module.

### 3.3 GPS
- **Rec:** u-blox **NEO-M8N**/**M9N** bare SMD, or the cheaper, fully
  JLC-stockable **ATGM336H** (M10-class). If an integrated patch antenna is
  wanted: Quectel **L80/L86**.
- Bare-SMD vs breakout footprint and integrated-vs-external antenna are §9-D3.

### 3.4 IMU
- **Rec:** **ICM-42688-P** (6-axis, low noise) or **LSM6DS3TR-C**. If heading /
  compass is in scope, 9-axis **LSM9DS1**.
- 6- vs 9-axis, exact part, and bus (I2C vs SPI) are §9-D4.

### 3.5 Power — dedicated buck for the modem (2 A bursts)
- **Rec:** synchronous buck rated **≥3 A continuous** so a 2 A burst sits inside
  thermal limits. Candidates (verify LCSC basic/extended status at order time):
  - 12 V in: **TPS5430** (3 A) or a 4 A sync-buck; **avoid MP1584** for a 2 A
    burst (realistic continuous ≈1.5–2 A, marginal at temperature).
  - Output 3.8–4.0 V; burst-capable inductor (e.g. 4.7 µH low-DCR).
- Logic rail: separate 3.3 V buck (or LDO) off the same protected front-end.

## 4. Power budget (design constraint for the modem buck)

Worst-case simultaneous loads (order-of-magnitude, datasheet-derived):

| Rail | Load | Peak | Notes |
|---|---|---|---|
| 3.8 V | SIM7600 LTE burst | **2.0 A** | class-4 TX slot; the design driver |
| 3.3 V | ESP32-S3 (Wi-Fi/BLE TX) | 0.5 A | ~350 mA avg, 500 mA peak |
| 3.3 V | GPS (NEO-M8N / ATGM336H) | 0.05 A | steady |
| 3.3 V | IMU | 0.01 A | steady |
| 3.3 V | logic total | **≈0.6 A** | |

Buck #1 (modem): 3.8 V x 2.0 A = **7.6 W** out; at ~85% eff ≈ **9 W** in →
≈ 2 A x 0.75 → **~0.75 A** from a 12 V rail. Rail decoupling: bulk **≥470 µF
low-ESR** + ceramics at the modem; keep the burst loop physically tight.
Buck #2 (logic): 3.3 V x 0.6 A ≈ 2 W out → ~0.2 A at 12 V.
Total 12 V input budget ≈ **1 A continuous, 2 A transient** → 2 A fuse class.

## 5. Automotive input protection (required if §9-D5 = 12 V)

- Fuse / PTC on the 12 V input (2 A class).
- Reverse-polarity: ideal-diode (LM74610 + N-FET) or P-MOSFET high-side.
- Load-dump / transient TVS: SMBJ/SMCJ ~24–33 V class per ISO 7637; series R +
  bulk cap to ride cranks down to ~6 V.
- Common-mode choke + pi-filter to keep modem TX bursts off the supply line.
- **If §9-D5 = USB 5 V**, this collapses to a polyfuse + TVS — a major
  simplification of cost and area. Flagged as a decision.

## 6. Dual-footprint build matrix

| Build | WROOM-1 footprint | DevKit header | Modem | Notes |
|---|---|---|---|---|
| Production | populated | unpopulated | populated (SMD) | SMD everything |
| Prototype | unpopulated | populated (DevKitC-1 2x18) | socket | hand-swappable |

Selection via 0 Ω / solder-bridge strapping so one PCB serves both.

## 7. Toolchain — SKiDL + KiCad 9.0 (VERIFIED ABSENT on this host)

The card body references "the same toolchain as the balloon tracker". Findings
from this run (2026-09-27):

- **KiCad is NOT installed** here (`kicad-cli` absent, no `/usr/share/kicad*`).
- **SKiDL is NOT installed** (`pip show skidl` → not found).
- The **balloon tracker reference project IS present** at `~/repos/balloon` —
  real, working KiCad + SKiDL-adjacent artifacts to model the car board on:
  - `tracker/hardware/hub_board_diy/hub_board_diy.kicad_sch` (+ `.kicad_pcb`,
    `.kicad_pro`)
  - `tracker/hardware/hub_board_f33_jlcpcb/custom.pretty/` — custom footprints
    (`SolderBridge_2Pad`, `ESP32-C3_Mini_V1_Header`, `LoRa2021_Castellated`, …)
  - `tracker/hardware/hub_board_v1_routed.kicad_pcb` (routed reference board)
  - `docs/PCB-HANDOVER-FOR-JLCPCB.md` — the JLCPCB handover pattern to mirror
  - `docs/PLAN-PCB-GERBER-GENERATION.md`
- To proceed past the plan, the working host needs KiCad 9.0 + `skidl` installed
  (or a different host/pipeline), plus a target repo for the car board (no
  `car-tollgate` repo exists; the car board is not in `~/repos` — §11).

## 8. Workflow (once §9 + toolchain are resolved)

1. Answer §9-D1..D7 (each may be "use the recommended default").
2. Install / verify KiCad 9.0 + SKiDL; choose the target repo.
3. Write the SKiDL source (`hub.py`) with the confirmed KiCad library refs.
4. Generate netlist → KiCad 9.0 → place → route.
5. DRC + ERC clean.
6. Export Gerbers + BOM + CPL (pick-and-place) for JLCPCB assembly.
7. **Human step:** place the JLCPCB order (§10).

## 9. Decision matrix — THE BLOCKER (these ARE the "exact footprints")

| ID | Decision | Recommended default | Impact of a wrong choice |
|----|----------|---------------------|--------------------------|
| D1 | ESP32-S3 variant + flash/PSRAM + devkit header layout | WROOM-1 N16R8, DevKitC-1 2x18 | wrong footprint; prototyping blocked |
| D2 | bare modem module **vs** shield-socket + exact model + LTE bands/region | SIM7600 bare SMD, region TBD | wrong footprint; EOL 2G; wrong bands |
| D3 | GPS module + integrated vs external antenna | NEO-M8N bare SMD, external ant | wrong footprint; antenna mismatch |
| D4 | IMU 6- vs 9-axis + exact part + bus | ICM-42688-P, I2C, 6-axis | wrong footprint; missing compass |
| D5 | input power path: 12 V automotive vs USB 5 V | 12 V automotive (it is a car hub) | adds/omits §5 protection; cost + area |
| D6 | board size / mounting / enclosure target | 50x50 mm or 80x60 mm TBD | DRC, panelization, mechanical |
| D7 | extra interfaces (CAN? RS232? SD? RTC?) | none beyond spec | missing nets found after layout |

**Minimum to unblock:** D1–D5. Each is answerable with "use recommended default"
or a specific override.

## 10. Ordering from JLCPCB — HUMAN step

Placing the order cannot be done by an agent: it requires a logged-in JLCPCB
account with a valid **payment method** and **shipping address**; spending real
money is an external side-effect that only the human initiates.

What the agent CAN produce once §9 + toolchain are resolved: Gerbers, BOM, CPL,
an upload-ready ZIP, and the exact order parameters (below), leaving only the
checkout click to the human.

Reference JLCPCB parameters (from the balloon tracker's handover, re-confirm at
order time): 2-layer FR-4, 1.6 mm, HASL (or lead-free), 1 oz copper, min trace
0.15 mm / min clearance 0.15 mm, standard SMT assembly with LCSC **basic parts
preferred**, dark-green mask. Board outline + panelization per §9-D6.

## 11. Open questions for the human / operator

1. Confirm or override D1–D7 (defaults accepted by default unless overridden).
2. Confirm deployment region (drives modem LTE bands, D2).
3. Desired programming path: native USB on the hub, or UART-only?
4. **Where should the car PCB live?** No `car-tollgate` repo exists locally or on
   `felixfelix-bot`; the balloon hub board lives in `~/repos/balloon/tracker/hardware`.
   Nominate a target repo (e.g. a new `car-tollgate-hardware` repo, or
   `esp32-tollgate`).
5. Confirm the KiCad 9.0 + SKiDL toolchain will be installed on a working host.

## 12. Prior-run note (dedup)

A previous run of this same card (2026-07-05) drafted an equivalent plan in the
scratch workspace and blocked on this identical decision matrix; the scratch
workspace was reaped, so this document is now written to the card's durable
attachment store instead. A sibling card `t_b5d349e5` ("CTG-11: Custom carrier
PCB — ESP32-S3 + GSM shield + GPS + IMU integration board", worker-plebeian) is
marked `done` but left no result, attachment, or traceable artifact — nothing
from it can be cited as delivered. See the card comment thread for the run-11
handoff.
