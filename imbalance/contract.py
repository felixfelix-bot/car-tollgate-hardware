"""Mode/band contract helpers — the analysis side of spec v2.1 §8.1.

The pipeline must never assume a band: it reads `mode` and `notes.band_hz` from
the capture header and reports which band it analysed over.  This module is the
single place those rules live.

Passband correction: spec §3.4.1 (modes A/B, tabulated cascade response) and
§3.5.1 (mode M, a documented 2nd-order model of the MPU-9250 `Dec2` DLPF).
`gain()` returns the *magnitude* response, so a measured amplitude can be
corrected to a pre-filter value by dividing by it.  Never compare absolute
amplitudes across two modes (different parts, different filters, different
noise densities).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

#: Band each mode's capture is valid over, Hz (spec §3.4.1 / §3.5.1 / §5.4).
MODE_BAND_HZ: Dict[str, Optional[float]] = {"A": 500.0, "B": 250.0, "M": 150.0, "v1": None}

#: Tabulated passband droop, dB, for the carrier part (spec §3.4.1 tables).
#: These are the spec's own numbers, re-derived by its tests, not re-measured here.
_DROOP_TABLE_DB: Dict[str, List[Tuple[float, float]]] = {
    "A": [(0.0, 0.00), (10.0, 0.00), (50.0, 0.00), (100.0, 0.00), (200.0, 0.02),
          (300.0, 0.09), (400.0, 0.30), (441.0, 0.44), (500.0, 0.71)],
    "B": [(0.0, 0.00), (100.0, 0.00), (150.0, 0.09), (200.0, 0.28), (250.0, 0.65)],
}

#: Mode M: 2nd-order model with 3 dB at 420 Hz (spec §3.5.1, A_DLPFCFG=7 -> Dec2).
#: droop_dB(f) = -10*log10(1 + (f/420)^4); -0.07 dB at 150 Hz as the spec states.
_MODE_M_FC_HZ = 420.0

#: Where each mode's droop number comes from (printed in the report).
DROOP_SOURCE = {
    "A": "spec S3.4.1 measured cascade table (tabulated)",
    "B": "spec S3.4.1 measured cascade table (tabulated)",
    "M": "spec S3.5.1 2nd-order Dec2 model, fc=420 Hz (modelled, not tabulated)",
}


def band_for(mode: Optional[str], notes: Optional[dict] = None,
             fs: float = 0.0) -> Tuple[Optional[float], str]:
    """Return ``(band_hz, provenance)`` for a capture.

    ``notes.band_hz`` (spec §5.4) wins over the mode table, because it travels
    with the file.  A legacy/unknown mode with no recorded band yields ``None``
    and the caller must say so rather than invent 500 Hz.
    """
    notes = notes or {}
    b = notes.get("band_hz")
    if isinstance(b, (int, float)) and b > 0:
        return float(b), "capture header notes.band_hz"
    b = MODE_BAND_HZ.get(str(mode or ""), None)
    if b:
        return float(b), f"mode table (mode {mode!r}) — header carried no notes.band_hz"
    return None, "no band recorded and mode is unknown (legacy capture)"


def droop_db(mode: Optional[str], f_hz: float) -> float:
    """Passband droop in dB at ``f_hz`` for ``mode`` (negative = attenuated)."""
    m = str(mode or "")
    if m == "M":
        if f_hz <= 0:
            return 0.0
        return -10.0 * float(math.log10(1.0 + (f_hz / _MODE_M_FC_HZ) ** 4))
    table = _DROOP_TABLE_DB.get(m)
    if not table:
        return 0.0
    pts = table
    if f_hz <= pts[0][0]:
        return pts[0][1]
    for (f0, d0), (f1, d1) in zip(pts, pts[1:]):
        if f_hz <= f1:
            span = f1 - f0
            w = 0.0 if span <= 0 else (f_hz - f0) / span
            return -(d0 + w * (d1 - d0))   # table is positive droop; return negative dB
    return -pts[-1][1]


def gain(mode: Optional[str], f_hz: float) -> float:
    """Linear passband magnitude response; divide a measured amplitude by it."""
    return float(10.0 ** (droop_db(mode, f_hz) / 20.0))


def correct_amplitude(mode: Optional[str], f_hz: float, amp: float) -> float:
    """Passband-corrected (pre-filter) amplitude. Spec §8.1."""
    g = gain(mode, f_hz)
    return float(amp / g) if g > 0 else float(amp)
