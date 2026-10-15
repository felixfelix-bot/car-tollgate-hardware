"""Per-run reports: JSON (machine) and markdown (human), plus the plot set.

The JSON is the contract for anything downstream (`t_6fc86c46` ContextVM, the
bench validation in `t_fbd7baa6`); the markdown is what a person reads.  Both are
generated from the same :class:`imbalance.diagnose.Diagnosis`, so they cannot
disagree.  Every number that could be misread carries its unit and, where it
matters, the resolution it is valid to.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .diagnose import Diagnosis

#: Report schema version.  Bump when a field changes meaning; additive fields do
#: not need a bump (consumers must tolerate unknown keys).
REPORT_SCHEMA_VERSION = "1.0"


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, complex):
        return [obj.real, obj.imag]
    return obj


def diagnosis_to_dict(diag: Diagnosis) -> Dict[str, Any]:
    d = _jsonable(diag)
    d["schema_version"] = REPORT_SCHEMA_VERSION
    return d


def write_json(diag: Diagnosis, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(diagnosis_to_dict(diag), fh, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def _fmt_optional(v: Optional[float], spec: str = ".3f", dash: str = "n/a") -> str:
    return dash if v is None else format(v, spec)


def render_markdown(diag: Diagnosis, captions: Optional[Sequence[str]] = None,
                    plot_paths: Sequence[str] = ()) -> str:
    """Human report.  States the mode/band and the resolution before any number."""
    L: List[str] = []
    A = L.append
    A(f"# Wheel-imbalance analysis — {os.path.basename(diag.source)}")
    A("")
    A(f"* analysed at {diag.analysed_at} (UTC) with the CTG-9 pipeline; "
      f"schema {REPORT_SCHEMA_VERSION}")
    A(f"* capture: mode **{diag.mode or 'unknown'}**, device `{diag.device}`, "
      f"band **{('0–%.0f Hz' % diag.band_hz) if diag.band_hz else 'unrecorded'}** "
      f"({diag.band_source})")
    A(f"* {diag.n_frames} frames, {diag.n_analysed} analysed, "
      f"{diag.duration_s:.3f} s, achieved rate **{diag.fs:.3f} Hz** "
      f"(nominal {diag.fs_nominal:.0f} Hz)")
    A(f"* resolution actually achieved: **{diag.df_hz:.4f} Hz** "
      f"({diag.n_averages} Welch averages of {diag.nperseg} samples, {diag.window} window)")
    A(f"* detection: peaks must clear **{diag.threshold_db:.1f} dB** over the local noise "
      f"floor across {diag.bins_scanned} scanned bins "
      f"({diag.band_scanned_hz[0]:.2f}–{diag.band_scanned_hz[1]:.1f} Hz), i.e. a total "
      f"false-alarm probability of 1 %")
    if diag.detection_limit_mps2 is not None:
        A(f"* sensitivity: the smallest 1x amplitude this run could have detected at a "
          f"wheel's own frequency is **{diag.detection_limit_mps2:.4f} m/s² "
          f"({diag.detection_limit_mg:.2f} mg)**; in-band noise floor "
          f"{diag.noise_floor_asd:.3e} {diag.noise_floor_unit}")
    A("")

    A("## Verdict")
    A("")
    A(f"> {diag.headline}")
    A("")
    if not diag.peaks and diag.detection_limit_mps2 is not None:
        A(f"* read this as a **bound**, not an all-clear: the run rules out a 1x "
          f"imbalance above {diag.detection_limit_mg:.2f} mg at the wheel frequencies; it "
          f"does not rule out anything smaller, nor anything above "
          f"{diag.band_scanned_hz[1]:.0f} Hz.")
        A("")
    if diag.severity != "unknown":
        A(f"* severity (1x amplitude in g): **{diag.severity}**")
    A(f"* rotation frequency: **{_fmt_optional(diag.f_rot_hz, '.4f')} Hz** "
      f"({diag.f_rot_source}; confidence {diag.f_rot_confidence})")
    A(f"* vehicle speed: **{_fmt_optional(diag.speed_mps, '.3f')} m/s** "
      f"({diag.speed_source})" + (f", i.e. {diag.speed_mps * 3.6:.1f} km/h"
                                 if diag.speed_mps else ""))
    if diag.side:
        A(f"* side signature: **{diag.side}** — {diag.side_note}")
    else:
        A(f"* side signature: none — {diag.side_note}")
    A("")

    A("## Peaks (top %d)" % len(diag.peaks))
    A("")
    A("`amplitude` is what the sensor chain delivered; `corrected` additionally removes "
      "the analysis-chain response and the mode's passband droop (spec §3.4.1/§3.5.1/§8.1).")
    A("")
    A("| # | freq Hz | refined Hz | amp m/s² | corrected m/s² | mg | order | corner | conf |")
    A("|---|---------|-----------|----------|----------------|----|-------|--------|------|")
    for i, p in enumerate(diag.peaks, 1):
        corner = p.corner or (f"unresolved ({', '.join(p.candidates)})" if p.candidates else "—")
        A(f"| {i} | {p.freq_hz:.4f} | {p.freq_refined_hz:.4f} | {p.amplitude:.5f} | "
          f"{p.amplitude_corrected:.5f} | {p.amplitude_corrected / 9.80665 * 1000:.3f} | "
          f"{_fmt_optional(p.order, '.3f')} | {corner} | {p.confidence} |")
    A("")
    for i, p in enumerate(diag.peaks, 1):
        A(f"**Peak {i} — {p.order_label}**")
        A("")
        A(f"* bin {p.freq_hz:.4f} Hz, refined {p.freq_refined_hz:.4f} Hz "
          f"(sub-bin parabolic interpolation), resolution {diag.df_hz:.4f} Hz "
          f"→ the true tone is within ±{diag.df_hz / 2:.4f} Hz of the bin")
        A(f"* amplitude {p.amplitude:.6f} m/s² as measured, "
          f"{p.amplitude_corrected:.6f} m/s² chain+passband corrected "
          f"({p.amplitude_corrected / 9.80665 * 1000:.3f} mg)")
        A(f"* {p.snr_db:.1f} dB above the local noise floor, prominence {p.prominence_db:.1f} dB, "
          f"present in {p.consistency * 100:.0f} % of averages")
        A(f"* axis: {p.signature.dominant_axis} "
          f"(ax {p.signature.amp_x:.5f}, ay {p.signature.amp_y:.5f}, "
          f"az {p.signature.amp_z:.5f} m/s²; ay−az phase "
          f"{_fmt_optional(p.signature.phase_yz_deg, '+.1f')}°)")
        if p.harmonics:
            A("* harmonics of the rotation frequency: " + ", ".join(
                f"{k}x={v:.5f}" if v is not None else f"{k}x<floor"
                for k, v in enumerate(p.harmonics, 1)))
        if p.implied_speed_mps is not None:
            A(f"* if this peak is 1x, the implied vehicle speed is "
              f"{p.implied_speed_mps:.3f} m/s ({p.implied_speed_mps * 3.6:.1f} km/h)")
        A(f"* confidence: **{p.confidence}** (score {p.confidence_score})")
        for r in p.reasons:
            A(f"  - {r}")
        A("")

    A("## Rotation model")
    A("")
    A(f"`f_rot = v / (π·D)` (spec §3.3). Wheel rows come from "
      f"`notes.wheel` in the capture header unless overridden on the command line.")
    A("")
    A("| corner | diameter m | side | f_rot Hz | 1x amp m/s² | strongest |")
    A("|--------|-----------|------|----------|-------------|-----------|")
    dia_by_pos = {w.position: w.diameter_m for w in diag.wheels}
    for r in diag.per_corner:
        d = dia_by_pos.get(r.position)
        A(f"| {r.position} | {('%.3f' % d) if d else 'unknown'} "
          f"| {r.side or '—'} | {_fmt_optional(r.f_rot_hz, '.4f')} | "
          f"{_fmt_optional(r.amplitude_1x, '.5f')} | "
          f"{'yes' if r.strongest_1x else ''} |")
    A("")
    if diag.per_corner:
        sep = diag.per_corner[0].separated_by_frequency
        if sep:
            A("The corners' rotation frequencies are separated by more than 2 FFT bins, so "
              "the peak frequency **can** name a corner — and the largest 1x amplitude is "
              "marked `strongest` above.")
        else:
            A("The corners share one rotation frequency to within 2 FFT bins (equal rolling "
              "diameters). **A single IMU cannot separate the four corners by frequency** "
              "(spec §7.3): the peak fixes *that* there is an imbalance and at which order, "
              "not which corner. Add rolling-diameter differences, a turning manoeuvre, or "
              "one IMU per corner.")
        A("")
    if diag.orders:
        A("Order amplitudes at the rotation frequency (m/s²): " + ", ".join(
            f"{k}={v:.5f}" for k, v in sorted(diag.orders.items(),
                                              key=lambda kv: float(kv[0].rstrip('x')))))
        A("")

    A("## Quality and honesty")
    A("")
    q = diag.quality
    def _num(key: str, default: float = 0.0) -> float:
        v = q.get(key, default)
        return float(v) if isinstance(v, (int, float)) else default

    fs_err = q.get("fs_error_pct")
    A(f"* achieved rate {_num('achieved_fs'):.3f} Hz vs nominal "
      f"{_num('fs_nominal'):.0f} Hz"
      + (f" ({float(fs_err):.3f} % error)" if isinstance(fs_err, (int, float)) else ""))
    A(f"* flagged frames {_num('flagged_fraction') * 100:.4f} % "
      f"(spec §8.1 threshold 0.1 %), sequence-gap fraction "
      f"{_num('gap_fraction') * 100:.4f} %, samples near the ±16 g rail "
      f"{_num('saturated_fraction') * 100:.4f} %")
    dc = q.get("dc_offsets") or {}
    A("* DC/gravity offsets removed per axis (m/s²): " + ", ".join(
        f"{k}={float(v):.3f}" for k, v in dc.items()) if isinstance(dc, dict) else
      "* DC/gravity offsets removed per axis: n/a")
    A(f"* analysed to {_num('fmax_analysed_hz'):.1f} Hz"
      + (f" (the capture's band edge; a mode-M capture is valid only to 150 Hz, "
         f"spec §3.5.1)" if (diag.band_hz or 0) <= 150 else ""))
    if diag.contaminated_orders:
        A(f"* orders excluded at the caller's request: {diag.contaminated_orders}")
    A("")
    if diag.warnings:
        A("**Warnings**")
        A("")
        for w in diag.warnings:
            A(f"* {w}")
        A("")
    A("**Analysis notes**")
    A("")
    for n in diag.analysis_notes:
        A(f"* {n}")
    A("")
    A("**What this report cannot say**")
    A("")
    A("* It cannot compare absolute amplitude with a capture taken in a different mode "
      "(different part, different filter, different noise density — spec §8.1), or with a "
      "capture taken on a different mount.")
    if diag.band_hz and diag.band_hz < 500:
        A(f"* This is a mode-{diag.mode} capture: above {diag.band_hz:.0f} Hz nothing was "
          f"measured, so \"no peak at order N\" is only meaningful inside the band.")
    if not diag.speed_mps:
        A("* No vehicle speed was available, so the peak frequency and the implied speed are "
          "reported instead of a named corner (spec §7.3).")
    A("")

    A("## Plots")
    A("")
    for p, c in zip(plot_paths, captions or []):
        A(f"![{c}]({os.path.basename(p)})")
        A("")
        A(f"*`{p}` — {c}*")
        A("")
    A("## Provenance")
    A("")
    A(f"* library versions: " + ", ".join(f"{k} {v}" for k, v in diag.library_versions.items()))
    A("")
    return "\n".join(L)


__all__ = ["diagnosis_to_dict", "render_markdown", "write_json", "REPORT_SCHEMA_VERSION"]
