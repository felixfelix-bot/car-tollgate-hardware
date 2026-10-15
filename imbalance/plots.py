"""matplotlib plots: PSD, linear amplitude spectrum, and a linear-spectrum lot.

Three figures, each answering a different question:

* **PSD (log y)** — where is the energy and how far above the noise floor is the
  peak?  This is the stability view: Welch averages + PSD weighting.
* **linear amplitude spectrum** — how big is the peak in m/s^2, marked against
  the peak picker's own threshold and the rotation orders.  This is the
  "how much imbalance" view; because it is linear, a small harmonic is visible
  without a log axis hiding it.
* **waterfall / lot** — the per-averaging-segment amplitude spectrum over time,
  which is the honest check on whether a peak is a sustained rotation tone or a
  one-off road event.  A wheel imbalance is a horizontal line; a pothole is a
  vertical smear.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .diagnose import Diagnosis
from .spectrum import Spectrum


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _order_ticks(ax, f_rot: Optional[float], fmax: float) -> None:
    if not f_rot or f_rot <= 0:
        return
    for k in range(1, 9):
        f = k * f_rot
        if f > fmax:
            break
        ax.axvline(f, color="0.75", lw=0.5, ls=":", zorder=0)
        ax.annotate(f"{k}x", (f, ax.get_ylim()[1]), fontsize=7, color="0.4",
                    ha="center", va="bottom")


def plot_psd(diag: Diagnosis, spectra: Dict[str, Spectrum], out: str,
             fmax: Optional[float] = None) -> str:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(11.5, 5.6), dpi=120)
    fig.patch.set_facecolor("white")
    for name, spec in spectra.items():
        m = spec.freqs > 0
        if fmax:
            m &= spec.freqs <= fmax
        ax.semilogy(spec.freqs[m], spec.asd[m], lw=1.0, alpha=0.85,
                    label=f"{name}  (resolution {spec.df:.3f} Hz, "
                          f"{spec.n_averages} avg)")
    if diag.dominant is not None:
        f = diag.dominant.freq_refined_hz
        ax.axvline(f, color="#c0392b", lw=1.2)
        ax.annotate(f"{f:.3f} Hz\n{diag.dominant.order_label.split('—')[0].strip()}",
                    (f, float(np.max(list(spectra.values())[0].asd))),
                    textcoords="offset points", xytext=(6, -4), fontsize=8,
                    color="#c0392b")
    _order_ticks(ax, diag.f_rot_hz, fmax or float(list(spectra.values())[0].freqs[-1]))
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("acceleration spectral density  (m/s²)/√Hz")
    band = f"{diag.band_hz:.0f} Hz" if diag.band_hz else "unrecorded band"
    ax.set_title(f"Welch PSD — {os.path.basename(diag.source)}  "
                 f"[mode {diag.mode or '?'}, capture band 0–{band}, "
                 f"ds = {(diag.fs / 2):.0f} Hz]")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_spectrum(diag: Diagnosis, spectra: Dict[str, Spectrum], out: str,
                  fmax: Optional[float] = None, min_prominence: float = 6.0,
                  max_order: int = 6) -> str:
    plt = _mpl()
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.2), dpi=120,
                             gridspec_kw=dict(height_ratios=[1.0, 1.0]))
    spec = spectra.get("az") or list(spectra.values())[0]
    lim = fmax or float(spec.freqs[-1])
    m = (spec.freqs > 0) & (spec.freqs <= lim)

    ax = axes[0]
    ax.plot(spec.freqs[m], spec.amp[m], lw=1.0, color="#2c3e50", label="az amplitude")
    for name, s in spectra.items():
        if name == "az":
            continue
        mm = (s.freqs > 0) & (s.freqs <= lim)
        ax.plot(s.freqs[mm], s.amp[mm], lw=0.8, alpha=0.6, label=f"{name} amplitude")
    for p in diag.peaks:
        if p.freq_refined_hz > lim:
            continue
        ax.plot([p.freq_refined_hz], [p.amplitude_corrected], "o", ms=5,
                color="#c0392b", zorder=5)
        ax.annotate(f"{p.freq_refined_hz:.3f}\n{p.order_label.split('—')[0].strip()}",
                    (p.freq_refined_hz, p.amplitude_corrected), fontsize=7.5,
                    textcoords="offset points", xytext=(5, 4), color="#c0392b")
    _order_ticks(ax, diag.f_rot_hz, lim)
    ax.set_ylabel("amplitude (m/s²)")
    ax.set_title(f"Amplitude spectrum — {os.path.basename(diag.source)} "
                 f"(marks are chain+passband corrected; resolution {diag.df_hz:.3f} Hz)")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)

    ax2 = axes[1]
    if diag.f_rot_hz:
        orders = np.arange(1, max_order + 1)
        vals = [spec.peak_amplitude_at(k * diag.f_rot_hz, max(spec.df, 0.03 * k * diag.f_rot_hz))
                for k in orders]
        ax2.bar(orders, vals, color="#2980b9", alpha=0.8)
        ax2.set_xlabel(f"rotation order (f_rot = {diag.f_rot_hz:.3f} Hz)")
        ax2.set_ylabel("amplitude (m/s²)")
        ax2.set_title("Order amplitudes at the rotation frequency — "
                      "order 1 is the imbalance case this tool targets")
        ax2.set_xticks(list(orders))
    else:
        ax2.plot(spec.freqs[m], spec.amp[m], lw=1.0, color="#2c3e50")
        ax2.set_xlabel("frequency (Hz)  — no rotation reference available, so no order axis")
        ax2.set_ylabel("amplitude (m/s²)")
        ax2.set_title("(no rotation frequency: peak frequency is the reportable result)")
    ax2.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_segments(diag: Diagnosis, spectra: Dict[str, Spectrum], out: str,
                  fmax: Optional[float] = None, max_segments: int = 40) -> str:
    """Per-segment amplitude spectra — a sustained tone is a horizontal ridge."""
    plt = _mpl()
    spec = spectra.get("az") or list(spectra.values())[0]
    if spec.segment_amps is None:
        raise ValueError("spectrum has no per-segment data (method='median'?)")
    seg = spec.segment_amps
    if seg.shape[0] > max_segments:
        idx = np.linspace(0, seg.shape[0] - 1, max_segments).astype(int)
        seg = seg[idx]
    lim = fmax or float(spec.freqs[-1])
    m = (spec.freqs > 0) & (spec.freqs <= lim)
    fig, ax = plt.subplots(figsize=(11.5, 5.2), dpi=120)
    x = spec.freqs[m]
    y = np.arange(seg.shape[0])
    mesh = ax.pcolormesh(x, y, seg[:, m], shading="nearest", cmap="magma")
    fig.colorbar(mesh, ax=ax, label="amplitude (m/s²)")
    if diag.dominant is not None:
        ax.axvline(diag.dominant.freq_refined_hz, color="#2ecc71", lw=1.0)
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("Welch segment index (time →)")
    ax.set_title(f"Per-segment spectra (az) — a wheel tone is a straight ridge, "
                 f"a road event is a single bright column  [{os.path.basename(diag.source)}]")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_all(diag: Diagnosis, spectra: Dict[str, Spectrum], outdir: str,
             fmax: Optional[float] = None, stem: str = "psd") -> List[Tuple[str, str]]:
    """Write all three figures; returns ``[(path, caption), ...]``."""
    os.makedirs(outdir, exist_ok=True)
    res: List[Tuple[str, str]] = []
    p1 = os.path.join(outdir, f"{stem}-welch-psd.png")
    plot_psd(diag, spectra, p1, fmax=fmax)
    res.append((p1, "Welch PSD (log) with the rotation orders marked; the vertical "
                    "line is the dominant peak."))
    p2 = os.path.join(outdir, f"{stem}-amplitude-and-orders.png")
    plot_spectrum(diag, spectra, p2, fmax=fmax)
    res.append((p2, "Linear amplitude spectrum with detected peaks (top) and the "
                    "amplitude at each integer rotation order (bottom)."))
    p3 = os.path.join(outdir, f"{stem}-per-segment.png")
    try:
        plot_segments(diag, spectra, p3, fmax=fmax)
        res.append((p3, "Per-averaging-segment amplitude spectra: a sustained wheel tone "
                        "shows as a straight ridge, a road transient as a single column."))
    except Exception as exc:                      # never fail a run over a plot
        res.append(("", f"per-segment plot unavailable: {exc}"))
    return res


__all__ = ["plot_all", "plot_psd", "plot_spectrum", "plot_segments"]
