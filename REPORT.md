# REPORT — t_9ae1a5c7: identify IMU module from photos, lock the capture spec

Board `car-tollgate` · assignee `worker-base` · run 12 · 2026-10-15

**Branch:** `worker-base/t_9ae1a5c7` on `felixfelix-bot/car-tollgate-hardware`
**Commit:** `f595d05` — 9 files, +2660/−6. Verified pushed
(`git ls-remote origin refs/heads/worker-base/t_9ae1a5c7` → `f595d05311415b85e8b971d0ce24f907ff5866df`).
Working tree clean; 0 unpushed commits.

---

## TL;DR

The card's open question was *"candidate modules awaiting photo identification"*.
**The identification is not open, and the answer is not the part the project had
assumed.** The 13 photographs were taken and analysed on 2026-07-06 and the result
was committed: the module physically on the bench is an **MPU-9250**. The project
therefore has **two** IMU parts, and they are not interchangeable:

| tier | part | `WHO_AM_I` | I²C ceiling | modes | band |
|---|---|---|---|---|---|
| **bench** (in hand today) | **MPU-9250** | **0x71** | **400 kHz** | **M** | **0–150 Hz** |
| **carrier** (ratified D4, to be built) | **ICM-42688-P** | **0x47** | 1 MHz | A, B | 0–500 / 0–250 Hz |

The consequence, which is what makes this card worth its budget: **the card's implied
target — a 0–500 Hz band with "at least 1–2 kHz" ODR and anti-alias filtering — is
impossible on the part that is actually in hand.** The MPU-9250's 4 kHz rate exists
only with its DLPF bypassed (`Dec1`: a 3 dB bandwidth and *no published stopband*),
and at its documented 1 kHz rate the first fold-in into a 0–500 Hz band is
`1000 − 500 = 500 Hz`, i.e. the band edge itself. No register setting escapes either
wall. The contract therefore freezes **three** modes, with a `WHO_AM_I` guard that
aborts a capture on a part mismatch.

## Deliverables (all committed, all verified)

| File | What it is |
|---|---|
| `docs/imu_capture_spec.md` | **spec v2.1** — the frozen interface contract (1252 lines) |
| `hardware/imu_capture.py` | the reference codec, now sensor-aware (611 lines) |
| `tests/test_imu_capture_spec.py` | 65 contract tests (was 48), incl. 17 new ones |
| `README.md` | corrected: two-sensor framing, false claim removed |
| `PROGRESS.md` | crash-recovery map |
| `evidence/*.txt` | pytest+coverage, decision gate, self-check, live acceptance run |

## The card's five steps, and where each landed

| # | Card step | Result |
|---|---|---|
| 1 | Inspect photos, identify part(s), find datasheets | Photos not on this host, **but their analysis is** — inventoried and cited (§1.1). Bench = MPU-9250, carrier = ICM-42688-P. Datasheets obtained and read (§10) |
| 2 | Record bus, supply, ODR, ranges, noise, FIFO; justify ODR vs the imbalance band | §2.1 (per-part electrical), §3.4 (carrier ODR/filter, with the arithmetic), §3.5 (**bench impossibility proof**), §3.7 (bus + FIFO budgets) |
| 3 | Physical mounting, orientation → vehicle frame | §7.1–7.3, incl. the per-part triad caveat and the magnetometer exclusion (§7.4) |
| 4 | Data contract: format, fields, naming/rotation, bytes/s | §5 (**IMUCAP2**, 24-byte frames, versioned header), §6 (naming, rotation, storage sizing) |
| 5 | Write `docs/imu_capture_spec.md`, mark assumptions | Written; assumptions marked ⚠ throughout and collected in §0 |

**Acceptance criteria — each met:**

* **Datasheet-backed part number confirmed** → yes, for *both* parts, from three
  vendor documents read here: `DS-000347` rev 1.8 (sha256 reproduced:
  `7f904aec…d3fc9`), `PS-MPU-9250A-01` rev 1.1, `RM-MPU-9250A-00` rev 1.6.
* **ODR/sample-rate decision justified against the imbalance band** → yes, and the
  justification had to be *two* decisions rather than one: 4 kHz for the 500 Hz band
  on the carrier part (§3.4.1), and 1 kHz / 0–150 Hz for the bench part, with the
  proof that 500 Hz is unreachable there (§3.5).
* **A versioned data-format contract firmware and analysis can both implement
  without guessing** → yes: `IMUCAP2` v2.1, and it is executable, not prose —
  `hardware/imu_capture.py` is the shared definition and the tests pin it.
* **Blocker note (missing/illegible photos)** → discharged rather than invoked: the
  photos are not retrievable here, but the durable record of their analysis is, and
  it answers the question. §1.2 states precisely what would *confirm* the physical
  part (one photo, or one register read) and notes that neither blocks firmware
  because the `WHO_AM_I` guard covers it.

## What I changed in the prior attempt's work, and why

Run 5 left high-quality work **staged but uncommitted** (spec v2.0, codec, 48 tests).
Its arithmetic and structure were sound; I kept them. I corrected one **material
false statement** it made as fact:

> *"No photos are attached to this card or to any card on any board … photo
> identification was impossible **and** unnecessary."*

Both halves are wrong, and in a way that matters: the *pixels* are absent, but the
**analysis of the 13 photographs is committed** to `tollgate-esp32/docs/HARDWARE-INVENTORY.md`
(*"all 13 photos analyzed via GLM-4.6V vision model"*, 2026-07-06), and it identifies
the bench module — a different part from the carrier's D4 part. v2.0 reasoned
"photo ID is impossible, therefore adopt D4". D4 is the *carrier* answer; it is not
the answer to *"what is on the bench"*, and the difference decides whether the card's
0–500 Hz requirement is achievable. An anti-regression test now fails if that false
claim returns (`test_spec_distinguishes_the_two_parts_and_names_the_bench_module`).

## Decisive numbers

| | mode A | mode B | mode M |
|---|---|---|---|
| part | ICM-42688-P | ICM-42688-P | **MPU-9250** |
| ODR | 4000 Hz | 2000 Hz | 1000 Hz |
| band | 0–500 Hz | 0–250 Hz | **0–150 Hz** |
| filter | AAF 785 Hz + UI 3rd 1048.1 Hz | AAF 394 Hz + UI 3rd 524.0 Hz | DLPF `A_DLPFCFG`=7 (420 Hz) |
| droop at band edge | −0.71 dB | −0.65 dB | −0.07 dB |
| I²C | **1 MHz** (400 kHz = 144 % duty, impossible) | 1 MHz | **400 kHz** (31.5 % duty) |
| FIFO | 2048 B, watermark IRQ | 2048 B, watermark IRQ | 512 B, **poll** (no watermark IRQ) |
| frame traffic | 96 kB/s | 48 kB/s | 24 kB/s |
| storage | 8.294 GB/day | 4.147 GB/day | 2.074 GB/day |
| 64 MiB segment | 11.7 min | 23.3 min | 46.6 min |
| orders at 130 km/h | 26× | 13× | **7×** |

Rotation is capped at 64 MiB for all three modes because the `uint32` µs timestamp
wraps at 71.6 min; mode M's 46.6-min segment is the largest and keeps a 1.5× margin
(96 MiB would sit inside the wrap by only 2 %, which is a coincidence, not a margin).

## Evidence (reproducible)

```bash
cd ~/worktrees/t_9ae1a5c7
python3 -m pytest tests/ -q --cov=. --cov-report=term-missing   # 65 passed, 95% coverage
python3 tools/check_decisions.py                                # OK (D1-D7 gate still green)
python3 hardware/imu_capture.py                                 # OK: spec v2.1, round trip clean
```

Live acceptance run (`evidence/acceptance-mode-m.txt`) — a real 1 s mode-M capture
written through `RecordingWriter`, reloaded through `load`, then FFT'd:

```
frames 1000  mode M  device MPU-9250  who_am_i 0x71  band 150 Hz
achieved_fs 1000.000 Hz  duration 0.999 s  dropped 0  overruns 0
peak 15.000 Hz (injected 14.740), bin width 1.000 Hz
ALL ACCEPTANCE CHECKS PASSED
```

The guard is exercised in both directions: each mode accepts its own part and
**raises `IdentityMismatch`** on the other, including `mode A` on `0x71`.

## Assumptions, stated plainly

1. ⚠ **The photo pixels are not on this host.** I could not re-inspect them; I relied
   on the committed record of their analysis. §1.2 lists what would confirm it, and
   the `WHO_AM_I` guard makes the confirmation non-blocking.
2. ⚠ **`MPU-9250` vs `MPU-6500` is unresolved; I assumed MPU-9250.** The record says
   *"MPU-9250 (or MPU-6500 variant)"*. Shown not to affect the contract (§1.1): same
   `WHO_AM_I`, same scales, same ODR/DLPF tables; only the AK8963 magnetometer (out
   of scope) and some noise figures differ, and the 9250's figure is the worse one,
   so the mode-M decision is conservative either way.
3. ⚠ **The MPU-6500 datasheets are not retrievable** — Web Archive returns 404 for
   both canonical paths and a CDX query finds only marketing pages. Recorded as an
   explicit source gap in §10 rather than left implicit.
4. ⚠ **Mode M's DLPF response is modelled, not tabulated.** The register map publishes
   a 3 dB bandwidth per setting, not a transfer function, so §3.5.1 uses a 2nd-order
   model and buys a 2× margin (band edge at −0.07 dB, 1 dB budget not reached until
   ~297 Hz). Flagged as an open item to measure on the bench before any quantitative
   cross-tier amplitude comparison.
5. ⚠ **The card's rotation arithmetic is wrong** (100 km/h on a 0.60 m wheel is
   14.74 Hz, not ~7 Hz). Harmless — the real frequency is *higher* than assumed, so
   the band sizing is conservative — but recorded in §3.2 rather than silently fixed.

## Open items (none block firmware in either tier)

1. A photo or a register read confirming the bench module and its I²C strap (§1.2).
2. Confirm 9250 vs 6500 from the chip marking (§1.1) — affects only §2.1.2 noise.
3. Confirm tyre diameter and top speed; revisit §3.2 if `D < 0.5 m` and >150 km/h.
4. **Measure the mode-M DLPF stopband** (§3.5.1) — blocks quantitative cross-tier
   amplitude comparison.
5. Physical board orientation for `notes.mounting` signs (§7.2).
6. Carrier-tier: UI filter stopband, CLKIN oscillator, and whether CTG-11 should fit
   an MPU-9250-compatible footprint as a fallback (§11 items 6–8).

## Notes for the downstream cards

* `t_752a0d0a` (firmware): implement §4.1 for the carrier part and §4.2 for the bench
  part; they are **not** interchangeable. Call `check_identity()` before recording.
  Note mode M has **no FIFO watermark interrupt** — poll the FIFO count.
* `t_00f126bd` (analysis): read `mode` and `notes.band_hz` from the header and report
  which band the diagnosis was made over. **Never compare absolute amplitudes across
  modes** — different filters *and* different noise densities (65 vs 300 µg/√Hz).
* `t_6fc86c46` (ContextVM): pass through `mode`, `notes.band_hz`, `notes.i2c_hz`.
* `t_fbd7baa6` (bench validation): tolerances are per-mode (§8.3); evaluate the
  passband row against the capture's own `notes.band_hz`, never a hard-coded 500 Hz.

## Environment note (affects future runs in this repo, not this deliverable)

This repo's chained repo-level hook `.git/hooks/pre-commit` is a stale duplicate of
the fleet hook with none of its sibling files present, so **every commit through it
failed closed** ("credential literal gate missing"). I resolved it additively by
symlinking the fleet hook's own files (`cred_gate.sh`, `secret-patterns.txt`,
`secret-allowlist.txt`, `cred-gate.conf`, `cred-needles.txt`,
`cred-needles-exclude.txt`, `build-cred-needles.py`) into `.git/hooks/`. All four
gates still run (verified: `cred_gate.sh --staged --quiet` → rc 0, and the commit
proceeded through the full hook). The hook itself was **not** bypassed, disabled, or
edited.
