"""Wheel/vehicle model: rotation frequencies, sides, and which corner a peak is.

The arithmetic is spec §3.3 / §7.3: ``f_rot = v / (pi * D)``, and a rotating
imbalance shows a peak at its own ``f_rot`` (order 1) with 2x/3x harmonics.

The **honest** part is :func:`candidate_corners`.  With four identical rolling
diameters every wheel shares one ``f_rot``, so a single IMU genuinely cannot
separate the four corners by frequency — the spec says so explicitly (§7.3) and
that is what this module reports, instead of picking a corner to look decisive.
What a single IMU *can* do:

* if the wheels have **different** rolling diameters (or the car is **turning**),
  each corner has its own ``f_rot`` and the peak frequency names it;
* independently, the **axis/phase signature** separates left from right
  (see :func:`side_from_signature`), because the lateral reaction is 90 deg out
  of phase on ``ay`` and flips sign with the side;
* a corner is only named when one candidate is uniquely consistent.  Otherwise
  the answer is the candidate list plus the per-corner amplitudes, which is the
  most a single sensor can honestly claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

G0 = 9.80665


@dataclass
class WheelSpec:
    """One wheel: a position label and a rolling diameter (any consistent unit)."""

    position: str
    diameter_m: float
    side: Optional[str] = None          # "L" | "R" | None -> derive from position
    f_rot_hz: Optional[float] = None    # measured/supplied rotation frequency

    def side_of(self) -> Optional[str]:
        if self.side in ("L", "R"):
            return self.side
        p = (self.position or "").lower()
        if "left" in p or p.endswith("-l") or p.startswith("l"):
            return "L"
        if "right" in p or p.endswith("-r") or p.startswith("r"):
            return "R"
        return None

    def rotation_hz(self, speed_mps: Optional[float]) -> Optional[float]:
        """Rotation frequency from the supplied data, diameter, or neither."""
        if self.f_rot_hz and self.f_rot_hz > 0:
            return float(self.f_rot_hz)
        if speed_mps is not None and self.diameter_m and self.diameter_m > 0:
            return float(speed_mps) / (math.pi * float(self.diameter_m))
        return None


def vehicle_speed_mps(f_rot_hz: float, diameter_m: float) -> float:
    """``v = pi * D * f`` — the inverse of the spec's speed->order mapping."""
    if diameter_m <= 0:
        raise ValueError("diameter_m must be > 0")
    return math.pi * float(diameter_m) * float(f_rot_hz)


def implied_speed(f_rot_hz: float, wheel_diameters_m: Sequence[float]) -> Optional[float]:
    """Speed implied by a peak, given one or more candidate diameters (mean)."""
    ds = [d for d in wheel_diameters_m if d and d > 0]
    if not ds:
        return None
    return float(np.mean([vehicle_speed_mps(f_rot_hz, d) for d in ds]))


def order_of(f_hz: float, f_rot_hz: Optional[float]) -> Optional[float]:
    if not f_rot_hz or f_rot_hz <= 0:
        return None
    return float(f_hz) / float(f_rot_hz)


def side_from_signature(phase_deg: float, ay_amp: float, az_amp: float,
                        min_ay_az_ratio: float = 0.05) -> Tuple[Optional[str], str]:
    """Left/right from the ``ay``/``az`` phase + amplitude signature.

    A rotating imbalance pushes down on ``az`` and sideways on ``ay``, the lateral
    term a quarter cycle (90 deg) out of phase.  The *sign* of the lateral term is
    fixed by which side the mass sits on, so the observed relative phase
    ``phi_ay - phi_az`` lands near +90 deg for one side and -90 deg for the other.
    Which physical side that is depends on the mounting sign convention recorded
    in ``notes.mounting`` (spec §7.2) — so this returns a *side label relative to
    the recorded convention* and says so, rather than asserting "left" outright.
    """
    if az_amp <= 0 or ay_amp <= 0:
        return None, "no lateral component measured (ay is empty)"
    ratio = ay_amp / az_amp
    if ratio < min_ay_az_ratio:
        return None, (f"lateral/vertical amplitude ratio {ratio:.3f} is below "
                      f"{min_ay_az_ratio:g}: no usable phase signature")
    d = (phase_deg + 180.0) % 360.0 - 180.0
    if abs(abs(d) - 90.0) > 45.0:
        return None, (f"ay-az phase difference {d:+.1f} deg is not close to the "
                      f"+/-90 deg an imbalance produces (this looks like a "
                      f"longitudinal or non-rotating excitation)")
    side = "L" if d > 0 else "R"
    return side, (f"ay-az phase difference {d:+.1f} deg ~ {'+' if d > 0 else '-'}90 deg "
                  f"-> side {side} under the mounting convention in notes.mounting")


@dataclass
class CornerCandidate:
    """How well one wheel explains one peak."""

    position: str
    side: Optional[str]
    f_rot_hz: Optional[float]
    f_expected_hz: Optional[float]
    delta_hz: Optional[float]
    delta_bins: Optional[float]
    order: Optional[float]
    score: float
    gives_rotation: bool
    side_consistent: Optional[bool] = None
    note: str = ""


@dataclass
class CornerResolution:
    """The resolved corner, or an explicit statement of why it is unresolved."""

    corner: Optional[str]
    confidence: str                      # high | medium | low | none
    candidates: List[CornerCandidate] = field(default_factory=list)
    contested: List[str] = field(default_factory=list)
    side_known: Optional[str] = None
    side_confidence: str = "none"
    side_note: str = ""
    reason: str = ""

    def names(self) -> List[str]:
        return [c.position for c in self.candidates]


def candidate_corners(
    peak_freq_hz: float,
    wheels: Sequence[WheelSpec],
    speed_mps: Optional[float],
    df: float,
    tol_bins: float = 2.0,
    tol_frac: float = 0.03,
    side: Optional[str] = None,
    side_known: bool = False,
) -> CornerResolution:
    """Match a peak frequency against each wheel's rotation frequency.

    Four outcomes, all honest:

    * exactly one wheel's ``f_rot`` is within tolerance -> that corner, named;
    * several are (four identical diameters) -> **contested**, listed, no corner;
    * none are, but the diameters are equal so they *share* a rotation frequency
      and this peak sits on a harmonic of it -> all four corners are candidates
      (an imbalanced wheel's 2x looks like somebody's 1x); also no corner;
    * the peak is not near any wheel -> no candidates (something else is vibrating).
    """
    if peak_freq_hz <= 0:
        raise ValueError("peak_freq_hz must be > 0")
    cands: List[CornerCandidate] = []
    gives_rotation: List[CornerCandidate] = []
    for w in wheels:
        f = w.rotation_hz(speed_mps)
        if f is None or f <= 0:
            cands.append(CornerCandidate(w.position, w.side_of(), None, None, None,
                                         None, None, 0.0, False,
                                         note="no diameter and no measured f_rot"))
            gives_rotation.append(cands[-1])
            continue
        delta = abs(peak_freq_hz - f)
        bins = (delta / df) if df > 0 else float("inf")
        order = peak_freq_hz / f
        within = (bins <= tol_bins) or (delta <= tol_frac * f)
        # a peak at an integer order of this wheel is explained by this wheel too
        nearest = round(order) if order > 0 else 0
        is_harmonic = nearest >= 1 and abs(order - nearest) <= max(0.08, tol_frac) and within is False
        if within:
            score = 1.0 - min(1.0, bins / tol_bins)
            cands.append(CornerCandidate(
                w.position, w.side_of(), f, f, -delta if peak_freq_hz >= f else delta,
                bins, order, score, True,
                note="peak sits on this wheel's own rotation frequency"))
            gives_rotation.append(cands[-1])
        elif is_harmonic:
            cands.append(CornerCandidate(
                w.position, w.side_of(), f, f, peak_freq_hz - f, bins, order,
                max(0.0, 0.5 * (1.0 - min(1.0, abs(order - nearest) / 0.08))),
                False,
                note=f"peak is order {order:.2f}x of this wheel (a harmonic, not 1x)"))
        else:
            cands.append(CornerCandidate(
                w.position, w.side_of(), f, f, peak_freq_hz - f, bins, order, 0.0, False,
                note=f"order {order:.2f}x, {bins:.1f} bins away"))
    # side filter (independent evidence: it can break a frequency tie)
    if side_known and side in ("L", "R"):
        for c in cands:
            if c.gives_rotation or c.score > 0:
                c.side_consistent = (c.side is None) or (c.side == side)
    winners = [c for c in gives_rotation if c.score > 0]
    harmonic_only = [c for c in cands if (not c.gives_rotation) and c.score > 0]
    f_rots = {round(c.f_rot_hz, 6) for c in gives_rotation if c.f_rot_hz}
    identical_diameters = len(f_rots) <= 1

    if len(winners) > 1:
        sided = [c for c in winners if c.side_consistent]
        if side_known and len(sided) == 1:
            w = sided[0]
            return CornerResolution(
                corner=w.position, confidence="medium", candidates=winners,
                contested=[c.position for c in winners if c.position != w.position],
                side_known=side, side_confidence="medium",
                reason=(f"frequency alone leaves {len(winners)} candidates "
                        f"(identical or near-identical rolling diameters, so they share "
                        f"f_rot = {w.f_rot_hz:.4f} Hz); the ay/az side signature, which is "
                        f"independent evidence, narrows it to {w.position}"))
        return CornerResolution(
            corner=None, confidence="none", candidates=winners,
            contested=[c.position for c in winners],
            side_known=side if side_known else None,
            side_confidence="medium" if side_known else "none",
            reason=(f"{len(winners)} wheels share this rotation frequency "
                    f"(f_rot = {winners[0].f_rot_hz:.4f} Hz, diameter "
                    f"{wheels[0].diameter_m:.3f} m); one IMU cannot separate corners by "
                    f"frequency (spec S7.3). Peak sits on the 1x of all of them."))
    if len(winners) == 1:
        w = winners[0]
        if len(wheels) == 1:
            # A single supplied wheel cannot be "localised": there was nothing to
            # choose between, so the match is a consistency check, not an
            # identification.  Reported as such rather than as a finding.
            return CornerResolution(
                corner=w.position, confidence="low", candidates=[w],
                side_known=side if side_known else None,
                side_confidence="medium" if side_known else "none",
                reason=("only one wheel was supplied, so this is a consistency check "
                        "(the peak does sit on its rotation frequency) and NOT a "
                        "localisation — supply all four corners before reading this as "
                        "'which wheel' (spec S7.3)"))
        return CornerResolution(
            corner=w.position,
            confidence="medium" if (w.delta_bins is not None and w.delta_bins <= 1.0) else "low",
            candidates=[w], side_known=side if side_known else None,
            side_confidence="medium" if side_known else "none",
            reason=("exactly one corner's rotation frequency is this peak; rolling "
                    "diameters differ, which is what makes a single-IMU localisation "
                    "possible (spec S7.3)"))
    if harmonic_only and identical_diameters:
        return CornerResolution(
            corner=None, confidence="none", candidates=harmonic_only,
            contested=[c.position for c in harmonic_only],
            side_known=side if side_known else None,
            side_confidence="medium" if side_known else "none",
            reason=("the peak is a harmonic of the shared rotation frequency, so it is "
                    "explained by the same wheel every time — it cannot name a corner"))
    return CornerResolution(
        corner=None, confidence="none", candidates=[], side_known=side if side_known else None,
        side_confidence="medium" if side_known else "none",
        reason="peak is not near any wheel's rotation frequency (not a wheel order)")


def per_wheel_amplitude_rows(
    spectrum_or_rows: Sequence[Tuple[float, float]],
    wheels: Sequence[WheelSpec],
    speed_mps: Optional[float],
    order: float = 1.0,
    half_width_hz: Optional[float] = None,
) -> List[Dict[str, object]]:
    """Per-corner amplitude at a given order — the multi-IMU / per-corner readout.

    With one IMU this is a *model* row (each wheel's own expected frequency); with
    several captures (one per corner) it is the comparison that actually
    localises, and the caller passes one spectrum per corner.
    """
    rows: List[Dict[str, object]] = []
    freqs = [f for f, _ in spectrum_or_rows]
    amps = [a for _, a in spectrum_or_rows]
    for w in wheels:
        f = w.rotation_hz(speed_mps)
        if f is None:
            rows.append(dict(position=w.position, f_hz=None, amplitude=None,
                             note="no f_rot available for this corner"))
            continue
        target = f * order
        hw = half_width_hz if half_width_hz is not None else max(0.25, 0.02 * target)
        sel = [a for fr, a in zip(freqs, amps) if abs(fr - target) <= hw]
        rows.append(dict(position=w.position, f_hz=target,
                         amplitude=float(max(sel)) if sel else None,
                         note=""))
    return rows


def default_wheels(corner: Optional[str] = None, diameter_m: Optional[float] = None,
                   count: int = 4) -> List[WheelSpec]:
    """Build a four-corner wheel set from the capture's own `notes.wheel`."""
    positions = ["front-left", "front-right", "rear-left", "rear-right"][:max(1, count)]
    if corner and corner not in positions and count == 4:
        positions[0] = corner
    dia = float(diameter_m) if (diameter_m and diameter_m > 0) else 0.0
    return [WheelSpec(p, dia, "L" if p.endswith("left") else
                      ("R" if p.endswith("right") else None))
            for p in positions]


def harmonic_orders(spectrum, f_rot_hz: float, max_order: float = 4.0,
                    half_width_hz: Optional[float] = None) -> Dict[float, float]:
    """Amplitude at each integer order of ``f_rot_hz`` (degenerate-safe)."""
    out: Dict[float, float] = {}
    k = 1
    while k <= max_order:
        f = k * f_rot_hz
        if f > spectrum.fmax():
            break
        hw = half_width_hz if half_width_hz is not None else max(spectrum.df, 0.04 * f)
        out[float(k)] = spectrum.peak_amplitude_at(f, hw)
        k += 1
    return out
