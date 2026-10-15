"""Welch (windowed, averaged) spectra with honest units and peak picking.

Why Welch and not a single FFT: road data is noisy and non-stationary, so one
periodogram of a 30 s run has a noise floor with ~100 % standard deviation per
bin and a peak can sit on a noise lobe.  Splitting the run into overlapping
segments, transforming each and averaging gives a **stable** estimate at the cost
of frequency resolution — and the resolution actually achieved is reported
(`df`, plus the Hann main-lobe width) rather than assumed.

Units: the input is m/s^2 (the capture contract's accel unit), so

* ``amp`` — amplitude spectrum, m/s^2; a bin-centred sinusoid of amplitude A
  yields ``amp == A`` (window coherent-gain corrected on the *peak* convention);
* ``psd`` — one-sided PSD, (m/s^2)^2/Hz (energy convention, window noise-power
  corrected);
* ``asd`` — sqrt(psd), m/s^2/sqrt(Hz) — what a "noise density" figure (e.g. the
  spec's 65 ug/sqrt(Hz)) is quoted in.

Both conventions are kept because they answer different questions: ``amp`` is
what "how strong is the imbalance" means, ``psd``/``asd`` is what "is this above
the noise floor" means.  Conflating them is the classic way to misquote a
vibration amplitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

G0 = 9.80665  # m/s^2 per g

#: Window name -> (generator, ENBW in bins).  Hann is the default: -31 dB
#: sidelobes, and the 1.5-bin ENBW is a small price for the leakage control that
#: makes a small imbalance visible next to a large one.
_WINDOWS: Dict[str, Tuple[Callable[[int], np.ndarray], float]] = {
    "hann": (np.hanning, 1.5),
    "hamming": (lambda n: np.hamming(n), 1.3628),
    "blackman": (lambda n: np.blackman(n), 1.7269),
    "rect": (lambda n: np.ones(n), 1.0),
    "none": (lambda n: np.ones(n), 1.0),
}


def _rolling(x: np.ndarray, w: int, pct: float) -> np.ndarray:
    """Rolling percentile of ``x`` over a window of ``w`` bins (edges reflected)."""
    x = np.asarray(x, dtype=np.float64)
    w = int(w)
    if w <= 1 or x.size <= 2:
        return x.copy()
    pad = w // 2
    padded = np.pad(x, pad, mode="reflect")
    view = np.lib.stride_tricks.sliding_window_view(padded, w)
    return np.percentile(view, pct, axis=1)


@dataclass
class Peak:
    """One detected spectral peak."""

    freq_hz: float
    amplitude: float            # m/s^2, as measured through the analysis chain
    freq_refined_hz: float = 0.0   # parabolic interpolation, sub-bin
    snr_db: Optional[float] = None  # vs the local noise floor
    sigma: Optional[float] = None   # (peak - floor) / floor MAD, in dB
    prominence_db: float = 0.0
    consistency: float = 1.0    # fraction of segments where the bin is a local max
    psd: float = 0.0
    corrected_amplitude: Optional[float] = None   # chain + passband corrected
    implied_order: Optional[float] = None
    implied_speed_mps: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.freq_refined_hz:
            self.freq_refined_hz = self.freq_hz


@dataclass
class Spectrum:
    """A Welch-averaged spectrum plus the resolution it is valid at."""

    freqs: np.ndarray
    amp: np.ndarray
    fs: float
    nperseg: int
    noverlap: int
    window: str
    n_averages: int
    df: float                   # Hz, bin spacing (the achievable resolution)
    enbw_hz: float              # Hz, noise-equivalent bandwidth (Hann: 1.5*df)
    unit: str = "m/s^2"
    segment_amps: Optional[np.ndarray] = None   # (n_averages, n_bins)
    method: str = "mean"        # "mean" (Welch) | "median" (robust to bursts)
    x_rms: float = 0.0
    #: detection threshold (dB over the local floor) last used by `find_peaks`
    last_threshold_db: float = 0.0

    @property
    def psd(self) -> np.ndarray:
        """One-sided PSD, unit^2/Hz (from the segment mean square)."""
        if self.segment_amps is None:
            return self.amp ** 2 / self.enbw_hz / 2.0
        return np.mean(self.segment_amps ** 2, axis=0) / self.enbw_hz / 2.0

    @property
    def asd(self) -> np.ndarray:
        return np.sqrt(np.maximum(self.psd, 0.0))

    def fmax(self) -> float:
        return float(self.freqs[-1])

    def peak_amplitude_at(self, f_hz: float, half_width_hz: Optional[float] = None) -> float:
        """Max amplitude within a band — robust to a tone falling between bins."""
        hw = self.df if half_width_hz is None else float(half_width_hz)
        m = (self.freqs >= f_hz - hw) & (self.freqs <= f_hz + hw)
        return float(np.max(self.amp[m])) if m.any() else 0.0

    def _floor(self) -> np.ndarray:
        """Local noise floor in **dB** (the 25th percentile of the local spectrum).

        Two things make this statistic the right one:

        * a **low percentile, not the median**, so a tone's own window-leakage
          skirt (a 50 mg tone's first sidelobe is only ~30 dB down and spreads
          several bins) cannot drag the floor up and make a strong peak look
          marginal;
        * it is taken on the **dB** spectrum, matching how the peak height is
          expressed, so the subtraction that follows is a ratio.
        """
        return self._noise()[0]

    def _noise(self) -> Tuple[np.ndarray, np.ndarray]:
        """``(floor_db, spread_db)`` — local floor level and noise-bin dispersion.

        ``spread_db`` is the scaled median absolute deviation of the residual
        (``db - floor``) with the top 5 % trimmed, i.e. how much a *noise* bin
        typically deviates.  Dividing a peak's height above the floor by it gives
        a significance in sigmas, and that is the test that matters: a capture at
        mode A has thousands of analysed bins, so the single largest noise bin is
        routinely 4-5 sigma high, and a fixed "6 dB above the floor" rule alone
        would report noise as an imbalance.  With ``M`` Welch averages the
        per-bin level wanders by roughly ``10*log10(1 +/- 1/sqrt(M))`` dB, which
        is what this measures rather than assumes.
        """
        db = 20.0 * np.log10(np.maximum(self.amp, 1e-300))
        w = self.floor_window()
        floor = _rolling(db, w, 25)
        resid = db - floor
        hi = float(np.percentile(resid, 95))
        keep = resid <= hi
        if np.count_nonzero(keep) < 8:
            keep = np.ones_like(keep, dtype=bool)
        med = float(np.median(resid[keep]))
        spread = 1.4826 * float(np.median(np.abs(resid[keep] - med)))
        spread = float(min(max(spread, 0.5), 12.0))   # dB; guard both tails
        return floor, np.full_like(db, spread)

    def noise_floor_asd(self, lo: float = 0.0, hi: Optional[float] = None) -> float:
        """Median noise-floor amplitude density in ``unit``/sqrt(Hz) over a band.

        Quoting the floor in the sensor's own units is what lets a reader check
        the measurement against a datasheet noise density (spec §3.4.1 gives
        65 ug/sqrt(Hz) for the carrier part, 300 for the bench part).
        """
        floor_db = self._floor()
        if hi is None:
            hi = self.fmax()
        m = (self.freqs > max(lo, self.df)) & (self.freqs <= hi)
        if not m.any():
            return 0.0
        amp_floor = float(np.median(10.0 ** (floor_db[m] / 20.0)))
        return amp_floor / math.sqrt(self.enbw_hz)

    def residual_db(self) -> Tuple[np.ndarray, np.ndarray]:
        """``(residual_db, floor_db)`` — each bin's height above its local floor."""
        db = 20.0 * np.log10(np.maximum(self.amp, 1e-300))
        floor = self._floor()
        return db - floor, floor

    def detection_threshold_db(self, lo: float, hi: float, alpha: float = 0.01,
                               floor_db: Optional[np.ndarray] = None) -> float:
        """Peak height (dB over the local floor) for a *calibrated* detection.

        Why a fixed 6 dB threshold is wrong on real captures: the analysis band
        contains thousands of bins (mode A: ~4000), and a Welch-averaged noise
        bin is a random draw, so *something* is always 6-15 dB above the median.
        A rule that ignores the bin count would call noise an imbalance.

        So the threshold is set from the noise's own tail, for a stated total
        false-alarm probability ``alpha`` over the ``N`` bins actually scanned:

        * the residual ``db - floor`` is fitted as an exponential (Gumbel) tail,
          whose scale ``beta`` follows from the interquartile range
          (``beta = IQR / ln 3``) — measured, not assumed;
        * the threshold is ``median + beta * ln(N / alpha)``.

        The exponential model has a *heavier* tail than the true
        chi-square-of-``2M`` distribution, so the result is **conservative**: it
        costs a little sensitivity in exchange for not reporting noise.  The
        value used is always reported, so a reader can judge it.
        """
        resid, _ = self.residual_db()
        m = (self.freqs >= lo) & (self.freqs <= hi)
        r = resid[m] if np.count_nonzero(m) >= 16 else resid
        # fit on the bulk: a strong peak must not inflate the noise scale
        hi_cut = float(np.percentile(r, 85.0))
        bulk = r[r <= hi_cut]
        if bulk.size < 8:
            bulk = r
        med = float(np.median(bulk))
        iqr = float(np.percentile(bulk, 75.0) - np.percentile(bulk, 25.0))
        beta = max(iqr / math.log(3.0), 0.15)
        n_bins = int(np.count_nonzero(m))
        thr = med + beta * math.log(max(n_bins, 2) / max(alpha, 1e-6))
        return float(thr)

    def floor_window(self) -> int:
        """Floor-estimation window in bins, ~20 Hz wide (at least 9 bins).

        The window trades two error sources: it must be wide enough to contain
        undisturbed noise bins next to a tone (a 50 mg tone's window leakage plus
        the preprocessor's filter skirts spread over ~10-15 Hz), and narrow
        enough that the floor still tracks a genuinely changing noise level —
        the anti-alias stopband above the band edge is a *different* noise regime,
        and a single global floor would be wrong there.  20 Hz is the value that
        keeps a symmetric sample of noise either side of a peak while staying far
        below the width of any filter transition here.
        """
        w = max(9, int(round(20.0 / max(self.df, 1e-9))))
        return min(w | 1, max(9, (self.amp.size // 4) | 1))

    def find_peaks(
        self,
        min_prominence_db: float = 6.0,
        min_sigma: float = 5.0,
        alpha: float = 0.01,
        auto_threshold: bool = True,
        fmin: float = 0.0,
        fmax: Optional[float] = None,
        max_peaks: int = 10,
        exclude_dc: bool = True,
        main_lobe_bins: int = 3,
    ) -> List[Peak]:
        """Significant local maxima, in two tests.

        A candidate must be a local maximum and clear

        * ``min_prominence_db`` above the local noise floor, and
        * the calibrated detection threshold
          (`detection_threshold_db`, unless ``auto_threshold=False``), and
        * ``min_sigma`` times the local noise dispersion.

        ``min_prominence_db`` alone is a weak test on a real capture: it is the
        floor that ``auto_threshold`` exists to raise.  ``alpha`` is the total
        false-alarm probability over the scanned band (`detection_threshold_db`).

        A ``main_lobe_bins``-wide neighbourhood around a stronger accepted peak is
        suppressed, so a strong tone's own leakage tails cannot be reported as
        separate peaks.
        """
        if fmax is None:
            fmax = self.fmax()
        floor, spread = self._noise()
        db = 20.0 * np.log10(np.maximum(self.amp, 1e-300))
        threshold = max(min_prominence_db, self.detection_threshold_db(
            max(fmin, self.df), fmax, alpha=alpha, floor_db=floor))
        self.last_threshold_db = float(threshold)
        amp = self.amp
        lo = max(0, int(math.ceil(fmin / self.df)))
        hi = min(amp.size - 1, int(math.floor(fmax / self.df)))
        if exclude_dc:
            lo = max(lo, 1)
        cands: List[int] = []
        for i in range(lo + 1, hi):
            if not (amp[i] > amp[i - 1] and amp[i] >= amp[i + 1]):
                continue
            if amp[i] <= 0:
                continue
            prom = db[i] - floor[i]
            if prom < threshold:
                continue
            sigma = prom / spread[i]
            if sigma < min_sigma:
                continue
            cands.append(i)
        cands.sort(key=lambda i: amp[i], reverse=True)
        accepted: List[int] = []
        for i in cands:
            if any(abs(i - j) <= main_lobe_bins for j in accepted):
                continue
            accepted.append(i)
            if len(accepted) >= max_peaks:
                break
        out: List[Peak] = []
        for i in accepted:
            prom = float(db[i] - floor[i])
            out.append(Peak(
                freq_hz=float(self.freqs[i]),
                freq_refined_hz=_refine(self.freqs, amp, i),
                amplitude=float(amp[i]),
                snr_db=prom,
                sigma=float(prom / spread[i]),
                prominence_db=float(prom),
                consistency=self._consistency(i),
                psd=float(self.psd[i]),
            ))
        out.sort(key=lambda p: p.amplitude, reverse=True)
        out = out[:max_peaks]
        return sorted(out, key=lambda p: p.freq_hz)

    def _consistency(self, i: int, ratio_db: float = 6.0) -> float:
        """Fraction of segments in which bin ``i`` is within 6 dB of its mean.

        A genuine wheel tone is present in every revolution, so it appears in
        every segment; a road pothole does not.  This is the cheapest honest
        confidence signal available without a ground truth.
        """
        if self.segment_amps is None or self.segment_amps.shape[0] < 2:
            return 1.0
        col = self.segment_amps[:, i]
        ref = float(self.amp[i])
        if ref <= 0:
            return 0.0
        thr = ref * 10 ** (-ratio_db / 20.0)
        return float(np.count_nonzero(col >= thr) / col.size)


def _refine(freqs: np.ndarray, amp: np.ndarray, i: int) -> float:
    """Parabolic interpolation of the log amplitude around a peak, sub-bin."""
    if i <= 0 or i >= amp.size - 1:
        return float(freqs[i])
    y0, y1, y2 = (20.0 * math.log10(max(amp[j], 1e-300)) for j in (i - 1, i, i + 1))
    denom = (y0 - 2.0 * y1 + y2)
    if denom == 0:
        return float(freqs[i])
    delta = 0.5 * (y0 - y2) / denom
    if abs(delta) > 1.0:
        return float(freqs[i])
    return float(freqs[i] + delta * (freqs[1] - freqs[0]))


def choose_nperseg(n: int, fs: float, target_averages: int = 8,
                   df_max: float = 0.25, df_min: float = 0.02) -> int:
    """Segment length: enough averages to be stable, at usable resolution.

    ``df_max`` bounds how coarse the resolution may get (0.25 Hz keeps a 2nd
    harmonic of a 14 Hz wheel well separated); ``df_min`` bounds how much of the
    record one segment may consume.
    """
    l_max = int(fs / df_min)
    l_min = int(fs / df_max)
    l = max(l_min, min(l_max, n // max(1, target_averages)))
    l = int(min(l, n))
    return max(16, l)


def welch(
    x: np.ndarray,
    fs: float,
    nperseg: Optional[int] = None,
    overlap: float = 0.5,
    window: str = "hann",
    method: str = "mean",
    detrend: str = "constant",
    unit: str = "m/s^2",
    target_averages: int = 8,
) -> Spectrum:
    """Welch-averaged amplitude spectrum + PSD of ``x``.

    ``amp[k]`` is the peak amplitude of a sinusoid at ``freqs[k]`` (same unit as
    ``x``) after coherent-gain correction; ``psd[k]`` is the one-sided power
    spectral density after noise-power correction.  ``df`` is the resolution that
    was actually achieved, not a request.
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    n = x.size
    if n < 16:
        raise ValueError(f"need at least 16 samples, got {n}")
    if window not in _WINDOWS:
        raise ValueError(f"unknown window {window!r}; choose from {sorted(_WINDOWS)}")
    if method not in ("mean", "median"):
        raise ValueError("method must be mean (Welch) or median (robust)")

    seg = nperseg or choose_nperseg(n, fs, target_averages)
    seg = int(min(seg, n))
    step = max(1, int(round(seg * (1.0 - overlap))))
    starts = list(range(0, max(1, n - seg + 1), step))
    win, enbw_bins = _WINDOWS[window]
    w = np.asarray(win(seg), dtype=np.float64)
    cg = float(np.sum(w))                       # coherent gain (peak convention)
    nw = float(np.sum(w ** 2))                  # noise power gain (PSD convention)
    scale_amp = 2.0 / cg
    if seg % 2 == 0:
        nyq_idx = seg // 2
    else:
        nyq_idx = None

    cols = []
    for s in starts:
        chunk = x[s:s + seg].copy()
        if chunk.size < seg:
            continue
        if detrend == "constant":
            chunk = chunk - chunk.mean()
        elif detrend == "linear":
            k = np.arange(seg, dtype=np.float64)
            slope, intercept = np.polyfit(k, chunk, 1)
            chunk = chunk - (slope * k + intercept)
        X = np.fft.rfft(chunk * w)
        a = np.abs(X) * scale_amp
        a[0] /= 2.0                                    # DC not mirrored
        if nyq_idx is not None and a.size > nyq_idx:
            a[nyq_idx] /= 2.0                          # Nyquist not mirrored
        cols.append(a)
    if not cols:
        raise ValueError("no complete segments; increase the capture length")
    seg_amps = np.vstack(cols)
    amp = np.median(seg_amps, axis=0) if method == "median" else np.mean(seg_amps, axis=0)
    freqs = np.fft.rfftfreq(seg, d=1.0 / fs)
    df = float(fs) / float(seg)
    xs = x - x.mean() if detrend != "none" else x
    return Spectrum(
        freqs=freqs, amp=amp, fs=float(fs), nperseg=seg, noverlap=seg - step,
        window=window, n_averages=seg_amps.shape[0], df=df, enbw_hz=df * enbw_bins,
        unit=unit, segment_amps=seg_amps, method=method,
        x_rms=float(np.sqrt(np.mean(xs ** 2))),
    )


def harmonic_support(
    spectrum: Spectrum,
    f1: float,
    n_harmonics: int = 3,
    tol_frac: float = 0.03,
    min_prominence_db: float = 6.0,
    min_sigma: float = 5.0,
    rel_db: float = 25.0,
    alpha: float = 0.01,
) -> List[Optional[float]]:
    """Amplitude of each harmonic of ``f1`` present in the spectrum.

    Returns a list of length ``n_harmonics``: the measured amplitude at ``k*f1``
    (``None`` where the harmonic falls outside the analysed band **or** does not
    clear the significance test there).  A genuine rotating imbalance shows 1x
    plus, often, 2x and 3x; a resonance or road feature usually does not.

    ``rel_db`` additionally requires a harmonic to be within that many dB of the
    fundamental: a component 60 dB below the 1x peak is not evidence of a
    harmonic, it is leakage.
    """
    floor_db, spread_db = spectrum._noise()
    db = 20.0 * np.log10(np.maximum(spectrum.amp, 1e-300))
    thr = spectrum.detection_threshold_db(spectrum.df, spectrum.fmax(),
                                          alpha=alpha, floor_db=floor_db)
    ref = spectrum.peak_amplitude_at(f1, max(spectrum.df, 0.02 * f1))
    out: List[Optional[float]] = []
    for k in range(1, n_harmonics + 1):
        f = k * f1
        if f > spectrum.fmax():
            out.append(None)
            continue
        idx = int(round(f / spectrum.df))
        lo, hi = max(0, idx - max(1, int(tol_frac * f / spectrum.df))), \
            min(spectrum.amp.size - 1, idx + max(1, int(tol_frac * f / spectrum.df)))
        j = lo + int(np.argmax(spectrum.amp[lo:hi + 1]))
        a = float(spectrum.amp[j])
        prom = float(db[j] - floor_db[j])
        ok = (prom >= max(min_prominence_db, thr)
              and (prom / float(spread_db[j])) >= min_sigma
              and (ref <= 0 or a >= ref * 10.0 ** (-rel_db / 20.0)))
        out.append(a if ok else None)
    return out


def band_rms(spectrum: Spectrum, lo: float, hi: float) -> float:
    """RMS in a band, in the spectrum's unit (from the PSD, so it is energy-correct)."""
    m = (spectrum.freqs >= lo) & (spectrum.freqs <= hi)
    if not m.any():
        return 0.0
    return float(np.sqrt(np.sum(spectrum.psd[m]) * spectrum.df))
