"""Diagnosis: peak detection -> rotation model -> corner hypothesis + confidence.

This is the module the card is really about.  It takes a decoded capture, runs
the spec-mandated preprocessing (`imbalance.preprocess`), the Welch spectrum
(`imbalance.spectrum`) and the wheel model (`imbalance.wheels`), and produces a
:class:`Diagnosis`.

The hard rule, taken from spec §7.3 and enforced in code: **never invent a
corner.**  If the vehicle speed is not available, the report says so and gives the
peak frequency plus the *implied* vehicle speed instead.  If the four wheels share
a rolling diameter, the report names the peak's order and lists all four corners
as candidates.  Only when one corner is uniquely consistent — different rolling
diameters, or a unique side signature — is a corner named.

Rotation-frequency sources, in order of strength:

1. ``rotational_channel`` — an explicit channel/scale pair (e.g. ``gz`` scaled to
   yaw rate, turning into ``omega = psi_dot`` with a known track width).  The
   strongest evidence, because it is a *different physical quantity* from the
   vibration being diagnosed.
2. ``speed_mps`` — a supplied speed (logged channel or operator note).
3. ``wheels[i].f_rot_hz`` — a measured rotation frequency per wheel.
4. **a channel already in the capture whose DC level is gravity** (§7.2/§7.3):
   the accelerometer's own output with gravity subtracted, held at a constant
   pitch angle, is a direct readout of longitudinal acceleration/drag; where it
   is stationary it anchors the speed.  Used only when it *agrees* with the
   dominant peak's frequency, otherwise it is reported and not trusted.
5. nothing -> peak frequency + implied vehicle speed only.
"""

from __future__ import annotations

import math
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import contract
from .preprocess import PreprocessConfig, PreprocessResult, preprocess
from .spectrum import Peak, Spectrum, harmonic_support, welch
from .wheels import (CornerResolution, WheelSpec, candidate_corners,
                     default_wheels, harmonic_orders, implied_speed, order_of,
                     side_from_signature, vehicle_speed_mps)

G0 = 9.80665

#: order -> (label, explanation).  Order 1 is the imbalance case this card targets.
ORDER_CAUSES = {
    1: ("wheel imbalance", "centrifugal force from an unevenly distributed mass "
        "rotating once per wheel revolution"),
    2: ("runout / flat spot", "radial runout, a flat-spotted tyre, or the 2nd "
        "harmonic of a strong imbalance"),
    3: ("tyre non-uniformity", "3rd order: ply steer, carcass non-uniformity, "
        "tyre harmonics"),
    4: ("tyre / rim pattern", "4th order: tread pattern or rim/tyre distortion"),
}


# --------------------------------------------------------------------------- phase
def complex_amplitude_at(x: np.ndarray, fs: float, f_hz: float,
                         window: bool = True) -> complex:
    """Complex Fourier amplitude of ``x`` at exactly ``f_hz`` (zero-pad-free).

    Evaluated off-bin so a peak that falls between FFT bins still gets a
    meaningful phase.  The preprocessing chain is zero-phase (forward-backward),
    so a phase measured here is the phase the sensor actually saw — which is what
    makes the ``ay``/``az`` side signature meaningful.
    """
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    if n == 0:
        return 0j
    w = np.hanning(n) if window else np.ones(n)
    k = np.arange(n, dtype=np.float64)
    ph = np.exp(-2j * math.pi * f_hz * k / fs)
    return complex(2.0 * np.sum(x * w * ph) / float(np.sum(w)))


def phase_deg(z: complex) -> float:
    return float(math.degrees(math.atan2(z.imag, z.real)))


@dataclass
class AxisSignature:
    """Which axis a peak lives in, and the ay/az phase relation driving the side."""

    amp_x: float = 0.0
    amp_y: float = 0.0
    amp_z: float = 0.0
    phase_yz_deg: Optional[float] = None
    phase_xz_deg: Optional[float] = None
    dominant_axis: str = "?"
    ratio_y_over_z: Optional[float] = None


# --------------------------------------------------------------------------- peaks
@dataclass
class PeakDiagnosis:
    """One peak, mapped to the rotation model."""

    freq_hz: float
    freq_refined_hz: float
    amplitude: float                       # m/s^2 as measured through the chain
    amplitude_corrected: float              # chain + passband corrected (pre-filter)
    amplitude_g: float
    psd: float
    snr_db: Optional[float]
    prominence_db: float
    consistency: float
    order: Optional[float]
    order_label: str
    harmonics: List[Optional[float]] = field(default_factory=list)   # 1x,2x,3x m/s^2
    implied_speed_mps: Optional[float] = None
    signature: AxisSignature = field(default_factory=AxisSignature)
    corner: Optional[str] = None
    corner_confidence: str = "none"
    candidates: List[str] = field(default_factory=list)
    confidence: str = "low"
    confidence_score: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WheelRow:
    """One wheel's place in the rotation model, as reported."""

    position: str
    diameter_m: float = 0.0
    side: Optional[str] = None
    f_rot_hz: Optional[float] = None


@dataclass
class Diagnosis:
    """Everything the per-run report needs, and nothing it cannot support."""

    source: str
    analysed_at: str
    mode: Optional[str]
    device: str
    band_hz: Optional[float]
    band_source: str
    fs: float
    fs_nominal: float
    n_frames: int
    n_analysed: int
    duration_s: float
    df_hz: float
    nperseg: int
    n_averages: int
    window: str
    #: peak detection: threshold used, the band scanned, and the bin count behind it
    threshold_db: float = 0.0
    band_scanned_hz: Tuple[float, float] = (0.0, 0.0)
    bins_scanned: int = 0
    #: the smallest 1x amplitude this run could have detected, at a corner's own
    #: frequency, given the measured noise floor.  Below this, "no peak" is a
    #: statement about sensitivity, not about the car.
    detection_limit_mps2: Optional[float] = None
    detection_limit_mg: Optional[float] = None
    noise_floor_asd: Optional[float] = None     # (m/s^2)/sqrt(Hz) in-band
    noise_floor_unit: str = "(m/s^2)/sqrt(Hz)"
    unit: str = "m/s^2"
    severity: str = "unknown"
    headline: str = ""
    # rotation model
    f_rot_hz: Optional[float] = None
    f_rot_source: str = "none"
    f_rot_confidence: str = "none"
    speed_mps: Optional[float] = None
    speed_source: str = "unavailable"
    wheel_diameter_m: Optional[float] = None
    wheels: List["WheelRow"] = field(default_factory=list)
    per_corner: List[CornerRow] = field(default_factory=list)
    peaks: List[PeakDiagnosis] = field(default_factory=list)
    dominant: Optional[PeakDiagnosis] = None
    orders: Dict[str, float] = field(default_factory=dict)
    side: Optional[str] = None
    side_note: str = ""
    contaminated_orders: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    quality: Dict[str, object] = field(default_factory=dict)
    analysis_notes: List[str] = field(default_factory=list)
    library_versions: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def summary_line(self) -> str:
        return self.headline


# --------------------------------------------------------------------------- speed
@dataclass
class SpeedResolution:
    speed_mps: Optional[float]
    source: str
    f_rot_hz: Optional[float]
    f_rot_confidence: str
    notes: List[str] = field(default_factory=list)


def _gyro_channel_speed(res: PreprocessResult, name: str, scale: float,
                        wheels: Sequence[WheelSpec]) -> Optional[SpeedResolution]:
    """Speed from a rate channel: ``omega`` -> ``v = omega * r`` (rigid-body yaw).

    ``scale`` converts the channel's stored unit to rad/s (gyro is deg/s per the
    contract, so 2*pi/360).  The effective radius uses the supplied wheels'
    diameters (mean if they differ) and is reported, because ``v = omega*r`` is
    exact only for a known, constant reference.
    """
    if name not in res.signals:
        return None
    ds = [w.diameter_m for w in wheels if w.diameter_m and w.diameter_m > 0]
    if not ds:
        return None
    r = float(np.mean(ds)) / 2.0
    omega = np.asarray(res.signals[name], dtype=np.float64) * float(scale)
    v = omega * r
    if not np.all(np.isfinite(v)) or v.size == 0:
        return None
    v = v - np.mean(v) + abs(float(np.mean(v)))       # sign convention is unknown
    v = np.clip(v, 0.0, None)
    mean_v = float(np.mean(v))
    spread = (float(np.max(v)) - float(np.min(v))) / mean_v if mean_v > 0 else math.inf
    if mean_v <= 0.5 or spread > 1.0:
        return None
    return SpeedResolution(
        speed_mps=mean_v,
        source=f"rotational channel {name!r} scaled x{scale:g} rad/s -> v = omega*r "
               f"(r = {r:.4f} m from the supplied wheel diameters)",
        f_rot_hz=mean_v / (math.pi * 2.0 * r) if r > 0 else None,
        f_rot_confidence="low",
        notes=[f"assumes a rigid-body yaw/roll about a {2 * r:.3f} m reference; "
               f"variation over the run was {spread * 100:.0f} % of the mean"])


def _channel_implied_speed(res: PreprocessResult, wheels: Sequence[WheelSpec],
                           dominant_hz: Optional[float]) -> Optional[SpeedResolution]:
    """Speed from a *gravity-referenced* channel already in the capture (§7.2/§7.3).

    The accelerometer output with gravity removed is a longitudinal specific
    force; held at a constant pitch that is the road-plane acceleration, so where
    it is stationary the implied speed can anchor the rotation model.  This is
    only accepted when its own frequency agrees with the dominant peak, which
    keeps a noisy channel from inventing a rotation frequency out of nothing.
    """
    ds = [w.diameter_m for w in wheels if w.diameter_m and w.diameter_m > 0]
    if not ds:
        return None
    d = float(np.mean(ds))
    best: Optional[Tuple[float, SpeedResolution]] = None
    for name in ("az", "ay", "ax"):
        if name not in res.signals or res.signals[name].size < 16:
            continue
        for sign in (1.0, -1.0):
            v = sign * np.asarray(res.signals[name], dtype=np.float64) \
                * (math.pi * d / G0)
            v = v - np.mean(v) + abs(float(np.mean(v)))
            v = np.clip(v, 0.0, None)
            mean_v = float(np.mean(v))
            if mean_v <= 1.0 or not np.isfinite(mean_v):
                continue
            spread = (float(np.max(v)) - float(np.min(v))) / mean_v
            if spread > 0.6:
                continue
            f_rot = mean_v / (math.pi * d)
            if dominant_hz and f_rot > 0:
                ratios = [abs(dominant_hz / (k * f_rot) - 1.0) for k in (1, 2, 3)]
                agree = min(ratios)
                if agree > 0.05:
                    continue
            else:
                agree = 0.0
            score = 1.0 / (1.0 + spread) + (0.5 if agree < 0.02 else 0.0)
            if best is None or score > best[0]:
                best = (score, SpeedResolution(
                    speed_mps=mean_v,
                    source=(f"capture channel {name!r} (gravity-referenced "
                            f"specific force, sign {'+' if sign > 0 else '-'}) read as "
                            f"speed via v = (pi*D/g)*a; D = {d:.3f} m"),
                    f_rot_hz=f_rot, f_rot_confidence="low",
                    notes=[f"stationary to within {spread * 100:.0f} % over the run and "
                           f"consistent with the dominant peak (min order mismatch "
                           f"{agree * 100:.1f} %)"]))
    return best[1] if best else None


def resolve_speed(
    res: PreprocessResult,
    wheels: Sequence[WheelSpec],
    speed_mps: Optional[float],
    rotational_channel: Optional[Tuple[str, float]],
    dominant_hz: Optional[float],
) -> SpeedResolution:
    notes: List[str] = []
    if rotational_channel:
        g = _gyro_channel_speed(res, rotational_channel[0], rotational_channel[1], wheels)
        if g:
            return g
        notes.append(f"rotational channel {rotational_channel[0]!r} was unusable "
                     f"(missing channel, empty signal, or non-stationary)")
    if speed_mps is not None:
        ds = [w.diameter_m for w in wheels if w.diameter_m and w.diameter_m > 0]
        f_rot = float(speed_mps) / (math.pi * float(np.mean(ds))) if ds else None
        return SpeedResolution(float(speed_mps), "supplied speed_mps (operator/log)",
                               f_rot, "high" if f_rot else "none", notes)
    measured = [w for w in wheels if w.f_rot_hz and w.f_rot_hz > 0]
    if measured:
        f_rot = float(np.mean([float(w.f_rot_hz) for w in measured if w.f_rot_hz]))
        ds = [w.diameter_m for w in measured if w.diameter_m and w.diameter_m > 0]
        v = vehicle_speed_mps(f_rot, float(np.mean(ds))) if ds else None
        return SpeedResolution(v, "measured per-wheel f_rot in the capture metadata",
                               f_rot, "high", notes)
    inferred = _channel_implied_speed(res, wheels, dominant_hz)
    if inferred:
        inferred.notes = notes + inferred.notes
        return inferred
    if dominant_hz:
        ds = [w.diameter_m for w in wheels if w.diameter_m and w.diameter_m > 0]
        v = implied_speed(dominant_hz, ds) if ds else None
        notes.append("no speed data anywhere in the capture or the invocation; the "
                     "dominant peak is assumed to be 1x (the imbalance case) to get a "
                     "rotation frequency, and any corner named from it is a HYPOTHESIS")
        return SpeedResolution(v, "inferred: dominant peak assumed to be order 1",
                               float(dominant_hz), "low", notes)
    notes.append("no speed data and no dominant peak: rotation model unavailable")
    return SpeedResolution(None, "unavailable", None, "none", notes)


# --------------------------------------------------------------------------- helpers
def _severity(amp_mps2: float) -> str:
    """Imbalance severity in g (1x amplitude), the vibration-analysis convention."""
    frac = abs(amp_mps2) / G0
    if frac < 0.005:
        return "negligible"
    if frac < 0.02:
        return "low"
    if frac < 0.05:
        return "moderate"
    if frac < 0.15:
        return "high"
    return "severe"


def _order_label(order: Optional[float], tol: float = 0.1) -> str:
    if order is None:
        return "unknown (no rotation reference)"
    nearest = round(order)
    if nearest >= 1 and abs(order - nearest) <= tol * max(1, nearest):
        label, _ = ORDER_CAUSES.get(nearest, (f"{nearest}x harmonic", ""))
        return f"order {nearest}x — {label}"
    if order < 1 and abs(order - 0.5) <= tol:
        return "order 0.5x — a half-order (e.g. a once-per-two-revolutions event)"
    return "non-synchronous — not a wheel harmonic (belt, bearing, engine, road, or speed wobble)"


def _confidence(peak: Peak, resolution: CornerResolution, order: Optional[float],
                f_rot_conf: str) -> Tuple[str, float, List[str]]:
    """Confidence from measured evidence only, with the reasons spelled out."""
    reasons: List[str] = []
    score = 0.0
    snr = peak.snr_db or 0.0
    if snr >= 20:
        score += 2.0
        reasons.append(f"peak-floor SNR {snr:.1f} dB (strong)")
    elif snr >= 12:
        score += 1.4
        reasons.append(f"peak-floor SNR {snr:.1f} dB (clear)")
    elif snr >= 6:
        score += 0.7
        reasons.append(f"peak-floor SNR {snr:.1f} dB (marginal)")
    else:
        reasons.append(f"peak-floor SNR {snr:.1f} dB (weak)")
    if peak.consistency >= 0.8:
        score += 1.0
        reasons.append(f"present in {peak.consistency * 100:.0f} % of averaged segments "
                       f"(a sustained tone, not a transient)")
    elif peak.consistency >= 0.5:
        score += 0.5
        reasons.append(f"present in {peak.consistency * 100:.0f} % of segments")
    else:
        reasons.append(f"present in only {peak.consistency * 100:.0f} % of segments "
                       f"(intermittent — could be road/transient)")
    if order is not None and abs(order - round(order)) <= 0.1 * max(1, round(order)):
        score += 1.0
        reasons.append(f"frequency is order {order:.2f}x of the rotation reference "
                       f"(integer, so it tracks the wheel)")
    elif order is not None:
        score += 0.2
        reasons.append(f"order {order:.2f}x is not a wheel harmonic")
    if resolution.corner:
        if resolution.confidence == "medium":
            score += 1.0
            reasons.append(f"corner {resolution.corner} uniquely matches: {resolution.reason}")
        else:
            score += 0.25
            reasons.append(f"corner {resolution.corner} (weak match): {resolution.reason}")
    elif resolution.candidates:
        reasons.append(f"corner not resolved: {resolution.reason}")
        score -= 0.5
    if f_rot_conf == "high":
        score += 0.5
    elif f_rot_conf == "low":
        reasons.append("the rotation frequency itself is only estimated, so the order "
                       "and any corner are hypotheses")
        score -= 0.5
    level = ("high" if score >= 4.0 else "medium" if score >= 2.5
             else "low" if score >= 1.0 else "none")
    return level, round(score, 2), reasons


# --------------------------------------------------------------------------- main
def analyze_recording(
    rec,
    *,
    wheels: Optional[Sequence[WheelSpec]] = None,
    speed_mps: Optional[float] = None,
    rotational_channel: Optional[Tuple[str, float]] = None,
    axes: Sequence[str] = ("ax", "ay", "az"),
    primary_axis: str = "az",
    fmax: Optional[float] = None,
    top_n: int = 5,
    min_prominence_db: float = 6.0,
    window: str = "hann",
    method: str = "mean",
    target_averages: int = 8,
    cfg: Optional[PreprocessConfig] = None,
    source: str = "<memory>",
    harmonics_used: Sequence[float] = (1.0, 2.0, 3.0),
    exclude_orders: Sequence[int] = (),
) -> Diagnosis:
    """Full pipeline: capture -> preprocess -> Welch PSD -> peaks -> wheel model.

    ``exclude_orders`` marks rotation orders that are known to be contaminated by
    something other than the wheels (a card-specified "exclude this order" input);
    they are reported as contaminated and not used for the verdict.
    """
    res = preprocess(rec, cfg)
    band = res.band_hz
    if fmax is None:
        fmax = min(band if band else 0.45 * res.fs, 0.45 * res.fs)
    if fmax <= 0:
        raise ValueError("fmax must be > 0")

    spec_all = compute_spectra(res, axes=axes, primary_axis=primary_axis, window=window,
                               method=method, target_averages=target_averages)
    spec = spec_all[primary_axis]

    peaks_raw = spec.find_peaks(min_prominence_db=min_prominence_db,
                                fmin=max(2.0, 2.0 * spec.df),
                                fmax=fmax, max_peaks=max(top_n * 4, 12))
    dom_hz = max(peaks_raw, key=lambda p: p.amplitude).freq_refined_hz if peaks_raw else None

    # --- wheel model ------------------------------------------------------
    wheel_list = list(wheels) if wheels else []
    if not wheel_list:
        nw = rec.header.notes.get("wheel") or {}
        dia = nw.get("diameter_m")
        corner = nw.get("corner")
        count = int(nw.get("count") or 4)
        if dia:
            wheel_list = default_wheels(corner=corner, diameter_m=float(dia), count=count)
    speed = resolve_speed(res, wheel_list, speed_mps, rotational_channel, dom_hz)
    f_rot = speed.f_rot_hz
    notes: List[str] = list(speed.notes)
    if not wheel_list:
        notes.append("no wheel geometry in the capture header and none supplied: the "
                     "rotation model is f_rot = v/(pi*D) with no D, so only the peak "
                     "frequency and the implied speed can be reported (spec S7.3)")

    # --- per-peak diagnosis ----------------------------------------------
    peak_diags: List[PeakDiagnosis] = []
    for p in peaks_raw[:max(top_n * 2, top_n)]:
        sig = _signature_at(res, spec_all, p.freq_refined_hz)
        order = order_of(p.freq_refined_hz, f_rot)
        harm: List[Optional[float]] = []
        if f_rot and order is not None and abs(order - 1.0) <= 0.15:
            harm = harmonic_support(spec, f_rot,
                                    n_harmonics=int(max(harmonics_used)), tol_frac=0.02)
        side, side_note = (None, "")
        if sig is not None and sig.phase_yz_deg is not None:
            side, side_note = side_from_signature(sig.phase_yz_deg, sig.amp_y, sig.amp_z)
        res_corner = candidate_corners(
            p.freq_refined_hz, wheel_list, speed.speed_mps, spec.df,
            side=side, side_known=side is not None,
        ) if wheel_list else CornerResolution(None, "none", [], [], None, "none", "", 
                                             "no wheel geometry available")
        amp_corr = _chain_and_passband_corrected(res, primary_axis, p.freq_refined_hz, p.amplitude)
        conf, score, reasons = _confidence(p, res_corner, order, speed.f_rot_confidence)
        if f_rot and order is not None and any(
                abs(order - k) <= 0.15 * max(1, k) for k in exclude_orders):
            reasons.append(f"order {round(order)}x is on the caller's exclusion list, so it "
                           f"is reported but not used for the verdict")
            conf, score = "none", 0.0
        peak_diags.append(PeakDiagnosis(
            freq_hz=float(p.freq_hz), freq_refined_hz=float(p.freq_refined_hz),
            amplitude=float(p.amplitude), amplitude_corrected=float(amp_corr),
            amplitude_g=float(amp_corr / G0), psd=float(p.psd), snr_db=p.snr_db,
            prominence_db=float(p.prominence_db), consistency=float(p.consistency),
            order=order, order_label=_order_label(order), harmonics=harm,
            implied_speed_mps=implied_speed(p.freq_refined_hz,
                                            [w.diameter_m for w in wheel_list]),
            signature=sig if sig is not None else AxisSignature(),
            corner=res_corner.corner, corner_confidence=res_corner.confidence,
            candidates=res_corner.names(), confidence=conf, confidence_score=score,
            reasons=reasons + ([side_note] if side_note else []),
        ))
    peak_diags.sort(key=lambda q: q.amplitude, reverse=True)
    peak_diags = peak_diags[:top_n]
    dominant = peak_diags[0] if peak_diags else None

    # --- order table at the rotation frequency ---------------------------
    orders: Dict[str, float] = {}
    if f_rot:
        for k, v in harmonic_orders(spec, f_rot, max_order=4.0).items():
            orders[f"{k:g}x"] = float(v)

    contaminated = sorted({int(round(o)) for o in exclude_orders}) if exclude_orders else []
    headline = _headline(dominant, res, speed, contaminated, spec.last_threshold_db)
    diag = Diagnosis(
        source=source,
        analysed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        mode=res.mode, device=res.device, band_hz=band, band_source=res.band_source,
        fs=res.fs, fs_nominal=float(rec.header.fs_nominal or 0.0), n_frames=res.n_in,
        n_analysed=res.n_out, duration_s=res.duration_s, df_hz=spec.df,
        nperseg=spec.nperseg, n_averages=spec.n_averages, window=window,
        threshold_db=float(spec.last_threshold_db),
        band_scanned_hz=(float(max(2.0, 2.0 * spec.df)), float(fmax)),
        bins_scanned=int(max(0, (fmax - max(2.0, 2.0 * spec.df)) / spec.df)),
        severity=_severity(dominant.amplitude_corrected) if dominant else "unknown",
        headline=headline, f_rot_hz=f_rot, f_rot_source=speed.source,
        f_rot_confidence=speed.f_rot_confidence, speed_mps=speed.speed_mps,
        speed_source=speed.source if speed.speed_mps is not None else "unavailable",
        wheel_diameter_m=(float(np.mean([w.diameter_m for w in wheel_list]))
                          if wheel_list and all(w.diameter_m for w in wheel_list) else None),
        wheels=[WheelRow(position=w.position, diameter_m=float(w.diameter_m),
                         side=w.side_of(), f_rot_hz=w.rotation_hz(speed.speed_mps))
                for w in wheel_list],
        per_corner=_per_corner(spec, wheel_list, speed.speed_mps),
        peaks=peak_diags, dominant=dominant, orders=orders,
        side=None, warnings=res.warnings,
        quality=dict(
            achieved_fs=res.fs, fs_nominal=float(rec.header.fs_nominal or 0.0),
            fs_error_pct=(abs(res.fs - float(rec.header.fs_nominal)) /
                          float(rec.header.fs_nominal) * 100.0
                          if rec.header.fs_nominal else None),
            df_hz=spec.df, enbw_hz=spec.enbw_hz, n_averages=spec.n_averages,
            nperseg=spec.nperseg, duration_s=res.duration_s,
            flagged_fraction=res.flagged_fraction, gap_fraction=res.gap_fraction,
            saturated_fraction=res.saturated_fraction, interpolated_samples=res.interpolated_samples,
            dropped_flagged=res.dropped_flagged, degraded=res.degraded,
            time_wrapped=res.time_wrapped, non_monotonic_timestamps=res.non_monotonic_timestamps,
            dc_offsets=res.dc_offsets, band_hz=band, fmax_analysed_hz=float(fmax),
        ),
        analysis_notes=notes + [
            f"window {window}, {spec.n_averages} averages of {spec.nperseg} samples "
            f"(Welch, {100 * (spec.noverlap / spec.nperseg):.0f} % overlap); resolution "
            f"{spec.df:.4f} Hz, Hann noise bandwidth {spec.enbw_hz:.4f} Hz",
            f"amplitudes are m/s^2 after a zero-phase high-pass at "
            f"{cfg.highpass_hz if cfg else 2.0} Hz and a low-pass at the band edge "
            f"({band:.0f} Hz)" if band else "amplitudes are m/s^2 after high-pass + detrend",
            "`amplitude` is what the sensor chain delivered; `amplitude_corrected` "
            "additionally removes the analysis chain response and the mode's passband "
            "droop (spec S3.4.1 / S3.5.1 / S8.1)",
            "absolute amplitudes are comparable only between captures with the same "
            "mount, and NEVER across modes (spec S8.1)",
        ],
        library_versions=dict(
            python=platform.python_version(), numpy=np.__version__,
            spec=__import__("hardware.imu_capture", fromlist=["SPEC_VERSION"]).SPEC_VERSION,
        ),
    )
    diag.side, diag.side_note = _overall_side(peak_diags)
    diag.noise_floor_asd = float(spec.noise_floor_asd(
        lo=diag.band_scanned_hz[0], hi=diag.band_scanned_hz[1]))
    diag.detection_limit_mps2 = _detection_limit(res, spec, primary_axis, wheel_list,
                                                 speed.speed_mps, diag.threshold_db)
    diag.detection_limit_mg = (None if diag.detection_limit_mps2 is None
                               else diag.detection_limit_mps2 / G0 * 1000.0)
    return diag


def _detection_limit(res: PreprocessResult, spec: Spectrum, axis: str,
                     wheels: Sequence[WheelSpec], speed_mps: Optional[float],
                     threshold_db: float) -> Optional[float]:
    """Smallest 1x amplitude detectable at a corner, in m/s^2.

    The most useful single number in the report when *nothing* was found: it
    converts "no imbalance detected" from an unfalsifiable claim into a bound.
    Evaluated at each wheel's own rotation frequency (where a 1x peak would have
    to appear) — the chain and passband responses are frequency-dependent, so a
    limit quoted at one frequency is not valid at another.
    """
    freqs: List[float] = []
    for w in wheels:
        f = w.rotation_hz(speed_mps)
        if f and f > spec.df:
            freqs.append(float(f))
    if not freqs:
        return None
    f0 = float(np.mean(freqs))
    floor_db = spec._floor()
    idx = int(round(f0 / spec.df))
    if not (0 <= idx < spec.amp.size):
        return None
    floor_amp = float(10.0 ** (floor_db[idx] / 20.0))
    detected_amp = floor_amp * 10.0 ** (threshold_db / 20.0)
    return float(_chain_and_passband_corrected(res, axis, f0, detected_amp))


def compute_spectra(res: PreprocessResult, axes: Sequence[str] = ("ax", "ay", "az"),
                    primary_axis: str = "az", window: str = "hann",
                    method: str = "mean",
                    target_averages: int = 8) -> Dict[str, Spectrum]:
    """Welch spectra for the requested axes, all at the *same* segment length.

    Sharing ``nperseg`` across axes is required for the phase signature to mean
    anything: two spectra with different resolutions cannot be phase-compared
    bin for bin.
    """
    if primary_axis not in res.signals:
        raise ValueError(f"axis {primary_axis!r} is not in the capture "
                         f"({', '.join(res.signals)})")
    out: Dict[str, Spectrum] = {}
    seg: Optional[int] = None
    order = [primary_axis] + [a for a in axes if a != primary_axis]
    for a in order:
        if a not in res.signals:
            continue
        s = welch(res.signals[a], res.fs, nperseg=seg, window=window, method=method,
                  unit=res.accel_units, target_averages=target_averages)
        seg = s.nperseg if seg is None else seg
        out[a] = s
    if not out:
        raise ValueError("no analysable axes in this capture")
    return out


def _chain_and_passband_corrected(res: PreprocessResult, axis: str, f_hz: float,
                                  amp: float) -> float:
    """Remove the analysis-chain response, then the mode's passband droop."""
    g = float(np.atleast_1d(res.gain(axis, f_hz))[0])
    a = amp / g if g > 0 else amp
    return contract.correct_amplitude(res.mode, f_hz, a)


def _signature_at(res: PreprocessResult, spec_all: Dict[str, Spectrum],
                  f_hz: float) -> Optional[AxisSignature]:
    if not res.signals:
        return None
    amp = {}
    z = {}
    for name in ("ax", "ay", "az"):
        if name not in res.signals:
            continue
        c = complex_amplitude_at(res.signals[name], res.fs, f_hz)
        z[name] = c
        if name in spec_all:
            amp[name] = spec_all[name].peak_amplitude_at(f_hz, 2.0 * spec_all[name].df)
        else:
            amp[name] = abs(c)
    if not amp:
        return None
    dom = max(amp, key=lambda k: amp[k])
    ph_yz = (phase_deg(z["ay"]) - phase_deg(z["az"])) if ("ay" in z and "az" in z) else None
    ph_xz = (phase_deg(z["ax"]) - phase_deg(z["az"])) if ("ax" in z and "az" in z) else None
    ratio = (amp["ay"] / amp["az"]) if ("ay" in amp and amp.get("az") and amp["az"] > 0) else None
    return AxisSignature(
        amp_x=float(amp.get("ax", 0.0)), amp_y=float(amp.get("ay", 0.0)),
        amp_z=float(amp.get("az", 0.0)),
        phase_yz_deg=(None if ph_yz is None else float((ph_yz + 180.0) % 360.0 - 180.0)),
        phase_xz_deg=(None if ph_xz is None else float((ph_xz + 180.0) % 360.0 - 180.0)),
        dominant_axis=dom, ratio_y_over_z=(None if ratio is None else float(ratio)),
    )


@dataclass
class CornerRow:
    """One row of the per-corner 1x table (typed, so it survives a JSON round trip)."""

    position: str
    f_rot_hz: Optional[float] = None
    amplitude_1x: Optional[float] = None
    amplitude_1x_g: Optional[float] = None
    side: Optional[str] = None
    separated_by_frequency: bool = False
    strongest_1x: bool = False


def _per_corner(spec: Spectrum, wheels: Sequence[WheelSpec],
                speed_mps: Optional[float]) -> List[CornerRow]:
    """Each corner's expected 1x frequency and the amplitude measured there.

    With four identical diameters these rows all point at the same frequency —
    which is exactly the statement that one IMU cannot separate them, and why
    this table alone never names a corner.  With *different* rolling diameters
    the rows separate and the largest 1x amplitude is the culprit corner; that is
    the real signal in this table and it is stated explicitly (`separated`).
    """
    rows: List[CornerRow] = []
    for w in wheels:
        f = w.rotation_hz(speed_mps)
        if f is None:
            rows.append(CornerRow(position=w.position, side=w.side_of()))
            continue
        amp = spec.peak_amplitude_at(f, max(spec.df, 0.03 * f))
        rows.append(CornerRow(position=w.position, f_rot_hz=float(f),
                              amplitude_1x=float(amp), amplitude_1x_g=float(amp / G0),
                              side=w.side_of()))
    f_rots = [r.f_rot_hz for r in rows if r.f_rot_hz]
    separated = bool(f_rots) and (max(f_rots) - min(f_rots)) > 2.0 * spec.df
    best_pos: Optional[str] = None
    if separated:
        scored = [(r.amplitude_1x or 0.0, r.position) for r in rows
                  if r.amplitude_1x is not None]
        best_pos = max(scored)[1] if scored else None
    for r in rows:
        r.separated_by_frequency = separated
        r.strongest_1x = bool(separated and r.position == best_pos)
    return rows


def _overall_side(peaks: Sequence[PeakDiagnosis]) -> Tuple[Optional[str], str]:
    for p in peaks:
        if p.signature.phase_yz_deg is None:
            continue
        side, note = side_from_signature(p.signature.phase_yz_deg, p.signature.amp_y,
                                        p.signature.amp_z)
        if side:
            return side, f"from the strongest peak above: {note}"
    return None, ("no peak carried a usable ay/az phase signature (needs a genuine "
                  "lateral reaction, so a pure vertical excitation gives no side)")


def _headline(dominant: Optional[PeakDiagnosis], res: PreprocessResult,
              speed: SpeedResolution, contaminated: Sequence[int],
              threshold_db: float) -> str:
    if dominant is None:
        return (f"no peak in the analysed band cleared the calibrated detection "
                f"threshold ({threshold_db:.1f} dB over the local noise floor); "
                f"no wheel-order vibration was detected")
    amp_g = dominant.amplitude_corrected / G0
    parts = [f"dominant {dominant.freq_refined_hz:.3f} Hz "
             f"({dominant.freq_hz:.3f} Hz bin) at {dominant.amplitude_corrected:.4f} m/s^2 "
             f"({amp_g * 1000:.2f} mg) in the {dominant.signature.dominant_axis} axis"]
    if dominant.order is not None:
        parts.append(f"order {dominant.order:.2f}x of {speed.f_rot_hz:.3f} Hz")
    parts.append(dominant.order_label)
    if dominant.candidates and not dominant.corner:
        parts.append(f"corner unresolved, candidates: {', '.join(dominant.candidates)}")
    elif dominant.corner:
        parts.append(f"suspected corner {dominant.corner}")
    if contaminated:
        parts.append(f"orders {sorted(contaminated)} excluded at the caller's request")
    parts.append(f"confidence {dominant.confidence}")
    if res.mode:
        parts.append(f"mode {res.mode} / band 0-{res.band_hz:.0f} Hz")
    return "; ".join(parts)
