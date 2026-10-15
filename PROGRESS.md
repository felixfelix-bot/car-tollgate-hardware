# PROGRESS — t_00f126bd (FFT wheel-imbalance analysis pipeline)

Workspace: `/home/c03rad0r/worktrees/t_00f126bd` (git worktree, branch `worker-base/t_00f126bd`,
based on `worker-base/t_9ae1a5c7` @ cc72291 — the frozen spec v2.1 branch).

venv: `~/.venvs/ctg9` (python3.13, numpy 2.5.4, pytest 9.1.1, pytest-cov 7.1.0, matplotlib 3.11.2).
System python has numpy+pytest but NO matplotlib/scipy.

## Contract (re-verified against source, not the handoff prose)

`docs/imu_capture_spec.md` v2.1 — `hardware/imu_capture.py` is the codec and is imported, never
re-implemented. What the pipeline must honour and does:

- `load()` for reading; `t_us` for time; `rate = (n-1)/(t_us[-1]-t_us[0])`, never `fs_nominal` (§8.1).
- read `mode` + `notes.band_hz` and pass them through to the report (§8.1/§5.4).
- `scale_accel`/`scale_gyro` from the header (the codec already applies them on decode).
- flag frames with FIFO/HOST overrun; warn when >0.1 % (§8.1).
- report the achieved frequency resolution next to every peak (§8.1).
- apply the mode's passband correction before quoting an absolute amplitude; never compare
  amplitude across modes (§3.4.1 / §3.5.1 / §8.1).
- wheel model `f_rot = v/(pi*D)`; **no speed => emit peak frequency + implied speed, not a corner**
  (§7.3). Four equal diameters => one IMU cannot name a corner (§7.3).

## Log

- (done) worktree + venv + deps; contract read and pinned.
- (done) `imbalance/` package written: contract.py, preprocess.py, spectrum.py, wheels.py,
  diagnose.py, report.py, plots.py, analyze.py, __init__.py.
- (done) end-to-end smoke on synthetic: injected 14.740 Hz recovered at **14.7392 Hz** (bin
  14.75 Hz, df 0.25 Hz) at **0.4898 m/s^2 vs 0.4903 injected** (−0.1 %). Mode A and mode M both.
- (done) honest-reporting knobs verified: no-speed case reports implied speed not a corner; equal
  diameters => corner unresolved + candidates; differing diameters => corner named; balanced car
  => no peak, plus an explicit detection limit in mg.
- (done) CLI `python -m imbalance.analyze` writes JSON + markdown + 3 PNGs; exit 0.
- (done) **detection threshold calibrated** (`Spectrum.detection_threshold_db`): a fixed 6 dB
  prominence rule fires on noise on a real capture (thousands of bins), so the threshold is
  derived from the noise's own measured tail for a stated 1 % total false-alarm probability.
- (next) test suite `tests/test_imbalance.py` (synthetic sine at a known frequency; known-imbalance
  scenario; corner localisation with differing diameters; false-positive baseline; phase signature;
  variable length; flagged frames; legacy v1; report/JSON/CLI; contract conformance).
- (next) README + evidence + PROGRESS/REPORT; commit + push.
