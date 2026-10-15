"""Deterministic preprocessing for IMU captures (spec v2.1 §8.1).

Steps, in the order they are applied:

1. **timestamps** — unwrap the `uint32` µs counter (spec §3.6), derive the
   *achieved* rate from `t_us` (`rate = (n-1)/(t_us[-1]-t_us[0])`, never
   `fs_nominal`), and resample onto a uniform grid so a windowed PSD is valid.
2. **flagged frames** — frames with `FLAG_FIFO_OVERRUN` / `FLAG_HOST_OVERRUN`
   are dropped and interpolated over; a run with > 0.1 % flagged frames is
   degraded and the report must say so (spec §8.1).
3. **DC / gravity removal** per axis — either a plain mean (`gravity="mean"`) or
   a digital high-pass (`gravity="highpass"`, the default).  The raw DC level is
   diagnostic of mount tilt, so it is *recorded* in the result, not thrown away
   silently.
4. **band limiting** — an optional low-pass at the mode's band edge, so
   out-of-band road noise cannot leak into the peak picker.
5. **detrend** — constant or linear, after filtering.

Every digital filter here is **zero-phase** (forward-backward) and its exact
magnitude response is available from `DigitalChain.gain()`; the report divides
measured amplitudes by it, so a corrected amplitude is not biased by the
analysis chain.  This matters because the spec fixes an absolute-amplitude
contract (§3.4.1, §8.1) that a sloppy preprocessing step would silently break.

Deliberate non-choice: **no moving-average and no notch filters**.  A moving
average has nulls at multiples of `1/window` and a notch would sit exactly on a
wheel order; either would delete the tone being measured.  The only shaping is
monotone high-/low-pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .contract import band_for


# --------------------------------------------------------------------------- filters
def butterworth_qs(order: int) -> List[float]:
    """Q of each biquad in a Butterworth cascade of ``order`` (even orders only)."""
    if order % 2:
        raise ValueError("only even Butterworth orders are supported")
    if order not in (2, 4, 6, 8):
        raise ValueError("supported orders: 2, 4, 6, 8")
    return [1.0 / (2.0 * math.sin(math.pi * (2 * k + 1) / (2.0 * order)))
            for k in range(order // 2)]


@dataclass(frozen=True)
class Biquad:
    """RBJ-cookbook biquad: ``y = (b0 x + b1 x1 + b2 x2 - a1 y1 - a2 y2) / a0``."""

    b0: float
    b1: float
    b2: float
    a0: float
    a1: float
    a2: float

    @staticmethod
    def lowpass(fc: float, fs: float, q: float) -> "Biquad":
        w0 = 2.0 * math.pi * fc / fs
        cw, sw = math.cos(w0), math.sin(w0)
        alpha = sw / (2.0 * q)
        b1 = 1.0 - cw
        return Biquad(b0=b1 / 2.0, b1=b1, b2=b1 / 2.0,
                      a0=1.0 + alpha, a1=-2.0 * cw, a2=1.0 - alpha)

    @staticmethod
    def highpass(fc: float, fs: float, q: float) -> "Biquad":
        w0 = 2.0 * math.pi * fc / fs
        cw, sw = math.cos(w0), math.sin(w0)
        alpha = sw / (2.0 * q)
        b0 = (1.0 + cw) / 2.0
        return Biquad(b0=b0, b1=-(1.0 + cw), b2=b0,
                      a0=1.0 + alpha, a1=-2.0 * cw, a2=1.0 - alpha)

    def apply(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        b0, b1, b2, a0, a1, a2 = self.b0 / self.a0, self.b1 / self.a0, self.b2 / self.a0, 1.0, self.a1 / self.a0, self.a2 / self.a0
        y = np.empty_like(x)
        x1 = x2 = y1 = y2 = 0.0
        for i in range(x.size):
            xi = float(x[i])
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            y[i] = yi
            x2, x1 = x1, xi
            y2, y1 = y1, yi
        return y

    def response(self, f_hz: np.ndarray, fs: float) -> np.ndarray:
        """Complex frequency response at ``f_hz`` (shape preserved)."""
        f = np.atleast_1d(np.asarray(f_hz, dtype=np.float64))
        z = np.exp(-2j * math.pi * f / fs)
        num = self.b0 + self.b1 * z + self.b2 * z ** 2
        den = self.a0 + self.a1 * z + self.a2 * z ** 2
        out = num / den
        return out if np.ndim(f_hz) else complex(out[0])


@dataclass
class DigitalChain:
    """A cascade of biquads applied zero-phase (forward-backward)."""

    sections: List[Biquad]
    kind: str
    cutoff_hz: float
    order: int
    pad_frac: float = 0.05

    def apply(self, x: np.ndarray, fs: float) -> np.ndarray:
        """Zero-phase filtering.

        Forward-backward doubles the effective order and squares the magnitude
        response (``|H|^2``), so this chain has **no phase distortion at all** —
        which is what makes the `ay`/`az` phase difference in the diagnosis
        meaningful.  The signal is reflected at both ends for a pad long enough
        to cover the transient, then trimmed.
        """
        x = np.asarray(x, dtype=np.float64)
        n = x.size
        if n < 8:
            return self._cascade(x)
        pad = int(min(n - 1, max(8, self.pad_frac * n)))
        head, tail = x[1:pad + 1][::-1], x[-pad - 1:-1][::-1]
        padded = np.concatenate([head, x, tail])
        fwd = self._cascade(padded)              # forward pass
        back = self._cascade(np.flip(fwd))       # backward pass on the reversed signal
        y = np.flip(back)
        return y[pad:pad + n]

    def _cascade(self, x: np.ndarray) -> np.ndarray:
        y = x
        for s in self.sections:
            y = s.apply(y)
        return y

    def response(self, f_hz, fs: float) -> np.ndarray:
        f = np.atleast_1d(np.asarray(f_hz, dtype=np.float64))
        out = np.ones_like(f, dtype=np.complex128)
        for s in self.sections:
            out = out * s.response(f, fs)
        out = out ** 2                      # forward-backward -> |H|^2
        return out if np.ndim(f_hz) else complex(out[0])

    def gain(self, f_hz, fs: float) -> np.ndarray:
        """Linear magnitude response of the effective (forward-backward) chain."""
        g = np.abs(self.response(f_hz, fs))
        return g if np.ndim(f_hz) else float(g)

    def droop_db(self, f_hz, fs: float) -> np.ndarray:
        g = self.gain(f_hz, fs)
        return 20.0 * np.log10(np.maximum(g, 1e-300))


def make_lowpass(fc: float, fs: float, order: int = 4) -> DigitalChain:
    return DigitalChain([Biquad.lowpass(fc, fs, q) for q in butterworth_qs(order)],
                        kind="lowpass", cutoff_hz=float(fc), order=order)


def make_highpass(fc: float, fs: float, order: int = 4) -> DigitalChain:
    return DigitalChain([Biquad.highpass(fc, fs, q) for q in butterworth_qs(order)],
                        kind="highpass", cutoff_hz=float(fc), order=order)


# --------------------------------------------------------------------------- config
@dataclass
class PreprocessConfig:
    """Knobs for :func:`preprocess`.  Defaults follow spec §8.1."""

    gravity: str = "highpass"        # "none" | "mean" | "highpass"
    highpass_hz: float = 2.0         # wheel orders start above this at any real speed
    detrend: str = "constant"        # "none" | "constant" | "linear"
    lowpass: bool = True             # band-limit to the mode's band edge
    lowpass_margin: float = 1.0      # lp cutoff = band_hz * margin
    filter_order: int = 4
    resample: bool = True
    drop_flagged: bool = True
    min_samples: int = 64

    def validate(self) -> None:
        if self.gravity not in ("none", "mean", "highpass"):
            raise ValueError(f"gravity must be none|mean|highpass, got {self.gravity!r}")
        if self.detrend not in ("none", "constant", "linear"):
            raise ValueError(f"detrend must be none|constant|linear, got {self.detrend!r}")
        if self.gravity == "highpass" and self.highpass_hz <= 0:
            raise ValueError("highpass_hz must be > 0")
        if self.lowpass_margin <= 0:
            raise ValueError("lowpass_margin must be > 0")


# --------------------------------------------------------------------------- result
@dataclass
class PreprocessResult:
    """Preprocessed signals + everything the report needs to be honest about them."""

    signals: Dict[str, np.ndarray]      # axis name -> zero-mean band-limited signal
    fs: float                           # uniform grid rate used (Hz)
    duration_s: float
    n_in: int                           # frames in the file
    n_out: int                          # samples on the uniform grid
    mode: Optional[str]
    device: str
    band_hz: Optional[float]
    band_source: str
    channels: List[str] = field(default_factory=list)
    dc_offsets: Dict[str, float] = field(default_factory=dict)
    chains: Dict[str, List[DigitalChain]] = field(default_factory=dict)
    interpolated_samples: int = 0
    dropped_flagged: int = 0
    flagged_fraction: float = 0.0
    gap_fraction: float = 0.0
    saturated_fraction: float = 0.0
    non_monotonic_timestamps: int = 0
    time_wrapped: bool = False
    warnings: List[str] = field(default_factory=list)
    degraded: bool = False
    accel_units: str = "m/s^2"
    gyro_units: str = "deg/s"
    #: the FFT this preprocessing should be analysed with, and the resolution it
    #: is valid at (Hz).  Zero means "undecided" (no analysis happened here).
    resolution_hz: float = 0.0
    resolution_source: str = ""

    def gain(self, axis: str, f_hz, ) -> np.ndarray:
        """Total analysis-chain magnitude response at ``f_hz`` for ``axis``."""
        f = np.atleast_1d(np.asarray(f_hz, dtype=np.float64))
        out = np.ones_like(f)
        for ch in self.chains.get(axis, []):
            out = out * np.atleast_1d(ch.gain(f, self.fs))
        return out if np.ndim(f_hz) else float(out[0])

    def droop_db(self, axis: str, f_hz) -> np.ndarray:
        g = self.gain(axis, f_hz)
        return 20.0 * np.log10(np.maximum(g, 1e-300))


# --------------------------------------------------------------------------- engine
def _unwrap_us(t_us: np.ndarray) -> Tuple[np.ndarray, int, bool]:
    """Unwrap the uint32 µs counter into a monotonic float64 µs array.

    The counter is valid for 71.6 min (spec §6); a capture that ignored the
    rotation policy would wrap.  Reconstructing the wrap is strictly better than
    analysing a file whose timebase runs backwards, so it is done here and
    reported (`time_wrapped`).
    """
    t = np.asarray(t_us, dtype=np.float64)
    if t.size < 2:
        return t, 0, False
    bad = int(np.count_nonzero(np.diff(t) < 0))
    wrapped = False
    if bad:
        step = np.diff(t)
        pos = step[step > 0]
        big = float(np.median(pos)) if pos.size else 0.0
        if big > 0 and np.all((np.abs(step) < 2 ** 31)):
            t = np.concatenate([[t[0]], t[0] + np.cumsum(np.where(step < 0, step + 2 ** 32, step))])
            wrapped = True
    return t, bad, wrapped


def _resample_uniform(t: np.ndarray, cols: Dict[str, np.ndarray], fs: float
                      ) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Linear-interpolate every axis onto a uniform grid at ``fs``.

    ``t`` is in seconds.  A drop-free capture is already uniform to within the
    clock's own jitter, so the interpolation is a no-op there and only earns its
    keep when frames were dropped (spec §8.1: "use the timestamps rather than
    assuming uniform spacing").
    """
    dur = float(t[-1] - t[0])
    n_u = int(round(dur * fs)) + 1
    grid = t[0] + np.arange(n_u) / fs
    out = {k: np.interp(grid, t, v) for k, v in cols.items()}
    return grid, out


def preprocess(rec, cfg: Optional[PreprocessConfig] = None) -> PreprocessResult:
    """Run the preprocessing chain over a decoded :class:`imu_capture.Recording`."""
    cfg = cfg or PreprocessConfig()
    cfg.validate()
    hdr = rec.header
    axes = list(rec.axes)
    band_hz, band_src = band_for(hdr.mode, hdr.notes, hdr.fs_nominal)
    warnings: List[str] = []

    n_in = rec.n
    if n_in < cfg.min_samples:
        raise ValueError(f"capture has {n_in} frames; need at least {cfg.min_samples}")

    # --- 1. timebase ------------------------------------------------------
    t_us, non_mono, wrapped = _unwrap_us(np.asarray(rec.timestamps_us))
    if wrapped:
        warnings.append("uint32 timestamp counter wrapped; timebase reconstructed "
                        "(spec S6 says a capture must not outlive the 71.6 min wrap)")
    if non_mono:
        warnings.append(f"{non_mono} non-monotonic timestamp steps (repaired)")

    # work in seconds from here on: µs only mattered for the unwrap
    t_s = t_us / 1e6
    raw_fs = 0.0
    if n_in > 1 and t_s[-1] > t_s[0]:
        raw_fs = (n_in - 1) / float(t_s[-1] - t_s[0])
    if raw_fs <= 0:
        raw_fs = float(hdr.fs_nominal) or 0.0
    if raw_fs <= 0:
        raise ValueError("cannot determine a sample rate from the timestamps or header")
    fs = raw_fs
    nominal = float(hdr.fs_nominal or 0.0)
    if nominal and abs(fs - nominal) / nominal > 0.01:
        warnings.append(f"achieved rate {fs:.3f} Hz differs from fs_nominal "
                        f"{nominal:.3f} Hz by >1 %; using the timestamps (spec S8.1)")

    # --- 2. flag handling -------------------------------------------------
    flags = np.asarray(rec.flags)
    bad_mask = np.zeros(n_in, dtype=bool)
    from hardware.imu_capture import FLAG_FIFO_OVERRUN, FLAG_HOST_OVERRUN
    bad_mask |= (flags & FLAG_FIFO_OVERRUN).astype(bool)
    bad_mask |= (flags & FLAG_HOST_OVERRUN).astype(bool)
    n_bad = int(np.count_nonzero(bad_mask))
    flagged_fraction = n_bad / float(n_in)
    if flagged_fraction > 0.001:
        warnings.append(f"{flagged_fraction * 100:.3f} % of frames carry an overrun flag "
                        f"(spec S8.1 threshold 0.1 %): run is degraded")
    dropped = 0
    if cfg.drop_flagged and n_bad:
        dropped = n_bad
        bad_mask = bad_mask if n_bad < n_in else np.ones(n_in, dtype=bool)

    # --- 3. drop flagged frames, then interpolate over them ---------------
    keep = np.nonzero(~bad_mask)[0] if cfg.drop_flagged else np.arange(n_in)
    if keep.size < cfg.min_samples:
        raise ValueError(f"only {keep.size} unflagged frames; nothing to analyse")
    t_keep = t_s[keep]
    raw: Dict[str, np.ndarray] = {}
    for name in axes:
        v = np.asarray(rec.axis(name), dtype=np.float64)
        raw[name] = v[keep]

    interpolated = 0
    if cfg.resample:
        grid_s, cols = _resample_uniform(t_keep, raw, fs)
        interpolated = max(0, grid_s.size - keep.size)
    else:
        grid_s = t_keep
        cols = raw
    n_out = int(next(iter(cols.values())).size)

    # saturation: a clipped accelerometer generates harmonics that look like
    # imbalance (spec §3.4.1), so it is measured and reported.
    sat = 0
    for name in axes[:3]:
        lim = 15.0  # m/s^2, i.e. ~1.53 g below the +-16 g rail: anything this
        sat += int(np.count_nonzero(np.abs(cols[name]) > lim))  # near the rail is suspect
    saturated_fraction = sat / float(3 * n_out)

    # --- 4/5. DC-gravity removal, band limiting, detrend -------------------
    chains: Dict[str, List[DigitalChain]] = {}
    signals: Dict[str, np.ndarray] = {}
    dc: Dict[str, float] = {}
    lp_cut = None
    if band_hz and cfg.lowpass:
        lp_cut = min(float(band_hz) * cfg.lowpass_margin, 0.45 * fs)
    hp_cut = cfg.highpass_hz if cfg.gravity == "highpass" else None
    for name in axes:
        x = np.asarray(cols[name], dtype=np.float64)
        chain: List[DigitalChain] = []
        dc[name] = float(np.mean(x))
        if cfg.gravity == "mean":
            x = x - dc[name]
        elif cfg.gravity == "none":
            pass
        if cfg.gravity == "highpass":
            if hp_cut and hp_cut < 0.45 * fs:
                c = make_highpass(hp_cut, fs, cfg.filter_order)
                chain.append(c)
                x = c.apply(x, fs)
            elif hp_cut:
                warnings.append(f"not applying a {hp_cut} Hz high-pass: it is above "
                                f"0.45*fs ({0.45 * fs:.3f} Hz)")
        if lp_cut:
            c = make_lowpass(lp_cut, fs, cfg.filter_order)
            chain.append(c)
            x = c.apply(x, fs)
        if cfg.detrend == "constant":
            x = x - np.mean(x)
        elif cfg.detrend == "linear":
            n = x.size
            k = np.arange(n, dtype=np.float64)
            slope, intercept = np.polyfit(k, x, 1)
            x = x - (slope * k + intercept)
        signals[name] = x
        chains[name] = chain

    gap_fraction = 0.0
    if keep.size > 1:
        seq = np.asarray(rec.seq)[keep].astype(np.int64)
        gap_fraction = float(np.sum((np.diff(seq) - 1) % 65536)) / float(keep.size)

    degraded = flagged_fraction > 0.001
    if saturated_fraction > 1e-4:
        warnings.append(f"{saturated_fraction * 100:.4f} % of samples are near the "
                        f"+-16 g rail; clipping harmonics can masquerade as imbalance")
        degraded = degraded or saturated_fraction > 0.01
    if band_hz is None:
        warnings.append("no band recorded in the header and the mode is unknown; "
                        "analysis band is unbounded (legacy capture)")

    return PreprocessResult(
        signals=signals, fs=float(fs), duration_s=float(grid_s[-1] - grid_s[0]),
        n_in=n_in, n_out=n_out, mode=hdr.mode, device=str(hdr.device),
        band_hz=band_hz, band_source=band_src, channels=axes, dc_offsets=dc,
        chains=chains, interpolated_samples=interpolated, dropped_flagged=dropped,
        flagged_fraction=flagged_fraction, gap_fraction=gap_fraction,
        saturated_fraction=saturated_fraction, non_monotonic_timestamps=non_mono,
        time_wrapped=wrapped, warnings=warnings, degraded=degraded,
        accel_units=str(hdr.accel_units), gyro_units=str(hdr.gyro_units),
    )
