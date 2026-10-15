"""imbalance — offline FFT wheel-imbalance analysis for CTG-9 IMU captures.

Turns a raw capture (the IMU's own `IMUCAP2` container, spec v2.1) into a
diagnosis: which rotation order dominates, at what frequency and amplitude, and —
where the data actually supports it — which wheel.

    python -m imbalance.analyze run.imucap --speed 20 --wheel front-left=0.660

Module map
----------

* :mod:`imbalance.contract`   — the mode/band/passband rules from spec §3/§8.1
* :mod:`imbalance.preprocess` — timebase, flag handling, DC/gravity, band limit,
                               detrend; zero-phase filters with a known response
* :mod:`imbalance.spectrum`   — Welch PSD/ASD + amplitude spectrum, peak picking,
                               harmonic support, resolution accounting
* :mod:`imbalance.wheels`     — `f_rot = v/(pi*D)`, order model, corner hypotheses,
                               left/right phase signature
* :mod:`imbalance.diagnose`   — the pipeline and the `Diagnosis` object
* :mod:`imbalance.report`     — per-run JSON + markdown
* :mod:`imbalance.plots`      — PSD / amplitude / per-segment figures
* :mod:`imbalance.analyze`    — the CLI

Design rule that shapes all of it: **never assert what the data does not
support.**  No speed => report the peak frequency and the implied speed, not a
corner.  Four equal rolling diameters => report the order and list the corners,
because one IMU cannot separate them (spec §7.3).  Every amplitude carries the
correction that produced it, and the frequency resolution achieved is reported
next to every peak.
"""

__version__ = "1.0.0"

from .contract import MODE_BAND_HZ, band_for, correct_amplitude, droop_db, gain
from .diagnose import (AxisSignature, Diagnosis, PeakDiagnosis, analyze_recording,
                       compute_spectra, phase_deg)
from .preprocess import (Biquad, DigitalChain, PreprocessConfig, PreprocessResult,
                         make_highpass, make_lowpass, preprocess)
from .report import REPORT_SCHEMA_VERSION, diagnosis_to_dict, render_markdown, write_json
from .spectrum import Peak, Spectrum, harmonic_support, welch
from .wheels import (CornerCandidate, CornerResolution, WheelSpec, candidate_corners,
                     default_wheels, harmonic_orders, implied_speed, order_of,
                     side_from_signature, vehicle_speed_mps)

__all__ = [
    "__version__",
    # contract
    "MODE_BAND_HZ", "band_for", "droop_db", "gain", "correct_amplitude",
    # preprocess
    "preprocess", "PreprocessConfig", "PreprocessResult", "DigitalChain", "Biquad",
    "make_highpass", "make_lowpass",
    # spectrum
    "welch", "Spectrum", "Peak", "harmonic_support",
    # wheels
    "WheelSpec", "candidate_corners", "CornerResolution", "CornerCandidate",
    "default_wheels", "harmonic_orders", "implied_speed", "order_of",
    "side_from_signature", "vehicle_speed_mps",
    # diagnose
    "analyze_recording", "compute_spectra", "Diagnosis", "PeakDiagnosis",
    "AxisSignature", "phase_deg",
    # report
    "render_markdown", "write_json", "diagnosis_to_dict", "REPORT_SCHEMA_VERSION",
]
