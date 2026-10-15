#!/usr/bin/env python3
"""``python -m imbalance.analyze`` — reproducible FFT wheel-imbalance analysis.

Examples
--------
Analyse a real capture, with the speed logged separately::

    python -m imbalance.analyze run.imucap --speed 20 \\
        --wheel front-left=0.660 --wheel rear-right=0.610 --outdir out/

No speed available — report the peak frequency and the implied speed instead of
guessing a corner::

    python -m imbalance.analyze run.imucap --outdir out/

Dry-run the whole chain with a synthetic capture whose answer is known::

    python -m imbalance.analyze --synthetic --mode A --f-rot 14.74 \\
        --imbalance 1x=0.05 --wheel front-left=0.660 --wheel rear-right=0.610 \\
        --outdir out/synthetic/

Exit codes: 0 = analysed (a diagnosis, even an inconclusive one, was produced);
2 = the capture could not be analysed at all (bad file, too short, no rotation
reference where one is required).  A "no imbalance found" result is exit 0 —
that is a successful diagnosis, not a failure.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from .diagnose import analyze_recording, compute_spectra
from .preprocess import PreprocessConfig, preprocess
from .report import write_json, render_markdown
from .wheels import WheelSpec

#: degrees/s -> rad/s, for `--rotational-channel gz:deg`.
_RATE_SCALES = {"deg": math.pi / 180.0, "degs": math.pi / 180.0,
                "rad": 1.0, "rads": 1.0, "dps": math.pi / 180.0}


def _parse_wheels(pairs: Optional[Sequence[str]]) -> List[WheelSpec]:
    out: List[WheelSpec] = []
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--wheel needs POSITION=DIAMETER_M, got {p!r}")
        pos, dia = p.split("=", 1)
        out.append(WheelSpec(pos.strip(), float(dia)))
    return out


def _parse_channel(spec: Optional[str]) -> Optional[Tuple[str, float]]:
    """``gz`` / ``gz:deg`` / ``gy:rad`` -> ``(channel, scale_to_rad_per_s)``."""
    if not spec:
        return None
    parts = spec.split(":")
    name = parts[0].strip()
    unit = (parts[1].strip().lower() if len(parts) > 1 else "deg")
    if unit not in _RATE_SCALES:
        raise SystemExit(f"--rotational-channel unit must be one of "
                         f"{sorted(set(_RATE_SCALES))}, got {unit!r}")
    return name, _RATE_SCALES[unit]


def _load_capture(path: str):
    """Read any supported container through the spec's own decoder (spec §8.1)."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from hardware import imu_capture
    return imu_capture.load(path)


def _synthetic(args) -> Tuple[object, str]:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from hardware import imu_capture
    imbalance: Dict[float, float] = {}
    for p in args.imbalance or []:
        if "=" not in p:
            raise SystemExit(f"--imbalance needs ORDERx=AMPLITUDE_G, got {p!r}")
        k, v = p.split("=", 1)
        imbalance[float(k.lower().rstrip("x"))] = float(v)
    wheel = None
    wheels = _parse_wheels(args.wheel)
    if wheels:
        wheel = dict(diameter_m=wheels[0].diameter_m,
                     corner=wheels[0].position, count=len(wheels))
    rec = imu_capture.make_synthetic(
        mode=args.mode, seconds=args.seconds, wheel_hz=args.f_rot,
        imbalance=imbalance or None, noise_g=args.noise_g, seed=args.seed,
        wheel=wheel)
    return rec, "<synthetic:%s,%.0fs,f_rot=%.3fHz>" % (args.mode, args.seconds, args.f_rot)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m imbalance.analyze",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    p.add_argument("capture", nargs="?",
                   help="capture file (IMUCAP2, or legacy IMUCAP1) to analyse")
    p.add_argument("--synthetic", action="store_true",
                   help="analyse a synthetic capture instead of a file (dry run)")
    p.add_argument("--outdir", default="reports",
                   help="where the JSON/markdown report and plots go (default: reports)")
    p.add_argument("--no-plots", action="store_true", help="skip the matplotlib figures")
    p.add_argument("--json-out", default=None, help="explicit JSON report path")
    p.add_argument("--md-out", default=None, help="explicit markdown report path")
    p.add_argument("--quiet", action="store_true", help="print only the headline")

    g = p.add_argument_group("rotation model")
    g.add_argument("--speed", type=float, default=None,
                   help="vehicle speed in m/s (from a log or operator note)")
    g.add_argument("--wheel", action="append", default=[], metavar="POSITION=DIAMETER_M",
                   help="per-wheel rolling diameter; repeat per corner")
    g.add_argument("--rotational-channel", default=None, metavar="CHANNEL[:UNIT]",
                   help="a rate channel in the capture (e.g. gz:deg) to derive speed from: "
                        "v = omega * r")
    g.add_argument("--exclude-order", action="append", default=[], type=int,
                   help="rotation order known to be contaminated (e.g. 3): reported but "
                        "not used for the verdict")

    s = p.add_argument_group("spectrum")
    s.add_argument("--axis", default="az",
                   help="primary axis to diagnose on (default az: vertical, the imbalance axis)")
    s.add_argument("--axes", default="ax,ay,az", help="axes to include in the plots")
    s.add_argument("--fmax", type=float, default=None,
                   help="highest frequency to analyse (default: the capture's band edge)")
    s.add_argument("--top", type=int, default=5, help="how many peaks to report")
    s.add_argument("--window", default="hann", choices=["hann", "hamming", "blackman", "rect"])
    s.add_argument("--method", default="mean", choices=["mean", "median"],
                   help="Welch mean (default) or per-segment median (robust to bursts)")
    s.add_argument("--averages", type=int, default=8,
                   help="target number of Welch averages (sets the resolution)")
    s.add_argument("--prominence-db", type=float, default=6.0,
                   help="peak must stand this far above the local median floor")

    q = p.add_argument_group("preprocessing")
    q.add_argument("--gravity", default="highpass", choices=["highpass", "mean", "none"])
    q.add_argument("--highpass", type=float, default=2.0, help="high-pass corner, Hz")
    q.add_argument("--detrend", default="constant", choices=["constant", "linear", "none"])
    q.add_argument("--no-bandlimit", action="store_true",
                   help="do not low-pass at the mode's band edge")

    y = p.add_argument_group("synthetic capture")
    y.add_argument("--mode", default="A", choices=["A", "B", "M"])
    y.add_argument("--seconds", type=float, default=30.0)
    y.add_argument("--f-rot", type=float, default=14.74, help="injected rotation frequency, Hz")
    y.add_argument("--imbalance", action="append", default=[], metavar="ORDERx=G",
                   help="injected imbalance, e.g. 1x=0.05 (in g)")
    y.add_argument("--noise-g", type=float, default=0.001453, help="white noise sigma, g")
    y.add_argument("--seed", type=int, default=0)
    y.add_argument("--save-capture", default=None,
                   help="also write the synthetic capture to this path")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.capture and not args.synthetic:
        print("error: give a capture file, or --synthetic for a dry run", file=sys.stderr)
        return 2
    wheels = _parse_wheels(args.wheel)
    channel = _parse_channel(args.rotational_channel)

    if args.synthetic:
        rec, source = _synthetic(args)
        if args.save_capture:
            from hardware import imu_capture
            imu_capture.save(args.save_capture, rec)
    else:
        source = args.capture
        try:
            rec = _load_capture(args.capture)
        except Exception as exc:
            print(f"error: cannot read {args.capture!r}: {exc}", file=sys.stderr)
            return 2

    cfg = PreprocessConfig(gravity=args.gravity, highpass_hz=args.highpass,
                           detrend=args.detrend, lowpass=not args.no_bandlimit)
    try:
        diag = analyze_recording(
            rec, wheels=wheels or None, speed_mps=args.speed,
            rotational_channel=channel, axes=tuple(a.strip() for a in args.axes.split(",")),
            primary_axis=args.axis, fmax=args.fmax, top_n=args.top,
            min_prominence_db=args.prominence_db, window=args.window,
            method=args.method, target_averages=args.averages, cfg=cfg,
            source=source, exclude_orders=tuple(args.exclude_order))
    except Exception as exc:
        print(f"error: analysis failed for {source!r}: {exc}", file=sys.stderr)
        return 2

    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.capture or "synthetic"))[0] or "run"
    json_path = args.json_out or os.path.join(args.outdir, f"{stem}.diagnosis.json")
    md_path = args.md_out or os.path.join(args.outdir, f"{stem}.diagnosis.md")
    write_json(diag, json_path)

    plots: List[Tuple[str, str]] = []
    if not args.no_plots:
        try:
            from .plots import plot_all
            res = preprocess(rec, cfg)
            spectra = compute_spectra(res, axes=tuple(a.strip() for a in args.axes.split(",")),
                                      primary_axis=args.axis, window=args.window,
                                      method=args.method, target_averages=args.averages)
            raw_fmax = args.fmax or diag.quality.get("fmax_analysed_hz")
            plots = plot_all(diag, spectra, args.outdir,
                             fmax=(float(raw_fmax) if isinstance(raw_fmax, (int, float))
                                   else None),
                             stem=stem)
        except Exception as exc:
            print(f"warning: plots unavailable ({exc}); the report is still written",
                  file=sys.stderr)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(diag, captions=[c for _, c in plots if c],
                                 plot_paths=[p for p, _ in plots if p]))

    if args.quiet:
        print(diag.headline)
    else:
        print(f"capture     : {diag.source}")
        print(f"mode/band   : {diag.mode} / "
              f"{('0-%.0f Hz' % diag.band_hz) if diag.band_hz else 'unrecorded'}")
        print(f"frames      : {diag.n_frames} ({diag.duration_s:.3f} s @ "
              f"{diag.fs:.3f} Hz achieved, nominal {diag.fs_nominal:.0f} Hz)")
        print(f"resolution  : {diag.df_hz:.4f} Hz "
              f"({diag.n_averages} x {diag.nperseg} samples, {diag.window})")
        print(f"detection   : threshold {diag.threshold_db:.1f} dB over floor, "
              f"{diag.bins_scanned} bins, 1x limit "
              + (f"{diag.detection_limit_mg:.2f} mg" if diag.detection_limit_mg is not None
                 else "n/a (no wheel geometry/speed)"))
        print(f"rotation    : {diag.f_rot_hz if diag.f_rot_hz else 'unknown'} Hz "
              f"({diag.f_rot_source})")
        print(f"speed       : {diag.speed_mps if diag.speed_mps else 'unavailable'} m/s "
              f"({diag.speed_source})")
        print("peaks       :")
        for i, p in enumerate(diag.peaks, 1):
            loc = p.corner or (f"candidates {','.join(p.candidates)}" if p.candidates else "-")
            print(f"  {i}. {p.freq_refined_hz:8.4f} Hz  {p.amplitude_corrected:9.5f} m/s^2 "
                  f"({p.amplitude_corrected / 9.80665 * 1000:7.3f} mg)  "
                  f"order {p.order if p.order is None else round(p.order, 2)}  "
                  f"corner {loc}  conf {p.confidence}")
        print(f"VERDICT     : {diag.headline}")
        print(f"report      : {json_path}")
        print(f"              {md_path}")
        for path, _ in plots:
            if path:
                print(f"plot        : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
