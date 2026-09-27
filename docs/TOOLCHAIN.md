# EDA toolchain — KiCad 9.0 + SKiDL on DQ05 (this host)

Host: `c03rad0r-DQ05proplus` — Ubuntu 25.10 (questing), passwordless sudo
available. Verified 2026-09-27.

## State as measured (before install)

- KiCad: **absent** — no `kicad-cli`, no `/usr/share/kicad*`.
- `skidl`: **absent** — `pip show skidl` → not found.
- `kicad` **is available in the Ubuntu 25.10 default archive at 9.0.3** —
  confirmed via `apt-get -s install kicad`:

```
Conf kicad-footprints (9.0.3-1 Ubuntu:25.10/questing [all])
Conf kicad-symbols    (9.0.3-1 Ubuntu:25.10/questing [all])
Conf kicad-templates  (9.0.0-1 Ubuntu:25.10/questing [all])
Conf kicad-libraries  (9.0.3+dfsg-1 Ubuntu:25.10/questing [all])
```

**No PPA is needed** on 25.10 (the `kicad/kicad-9.0-releases` PPA is only
required on older Ubuntu releases).

## Install

```bash
sudo apt-get update
sudo apt-get install -y kicad kicad-symbols kicad-footprints kicad-libraries
kicad-cli version          # expect: 9.0.3
```

SKiDL must run under the **system python3**, not the Hermes venv: it imports
`pcbnew`, which the `kicad` package installs into
`/usr/lib/python3/dist-packages/`.

```bash
/usr/bin/python3 -c "import pcbnew; print(pcbnew.GetBuildVersion())"
/usr/bin/python3 -m pip install --user --break-system-packages skidl
/usr/bin/python3 -c "import skidl; print(skidl.__version__)"
```

Disk: allow ~2.5 GB; `/` had 40 GB free at decision time (92% used) — check
`df -h /` before installing and clean up after Gerber export if it gets tight.

## Headless usage (no GUI)

```bash
# SKiDL → netlist
/usr/bin/python3 hub.py                       # writes hub.net
# Netlist → board, then DRC + Gerbers headlessly
kicad-cli pcb drc    --output drc.rpt board.kicad_pcb
kicad-cli pcb export gerbers      --output fab/ board.kicad_pcb
kicad-cli pcb export pos          --format csv --units mm --output fab/board-pos.csv board.kicad_pcb
kicad-cli sch export bom          --output fab/board-bom.csv board.kicad_sch
```

## Reference project (model to follow)

`~/repos/balloon/tracker/hardware/` — real, working KiCad project to copy
conventions from:

- `hub_board_diy/hub_board_diy.kicad_sch` (+ `.kicad_pcb`, `.kicad_pro`)
- `hub_board_f33_jlcpcb/custom.pretty/` — custom footprints
  (`SolderBridge_2Pad`, `ESP32-C3_Mini_V1_Header`, `LoRa2021_Castellated`, …)
- `hub_board_v1_routed.kicad_pcb` — routed reference board
- `docs/PCB-HANDOVER-FOR-JLCPCB.md`, `docs/PLAN-PCB-GERBER-GENERATION.md`

**Read only — do not commit into `~/repos/balloon`.** That repository carries
committed nsec material in its history and its pre-push hook blocks publishing
(see the fleet note in the manager's memory). This repo is the car board's home.
