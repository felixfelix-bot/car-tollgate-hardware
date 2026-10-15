"""imu_capture — the reference implementation of the CTG-9 IMU capture contract.

This module is the executable definition of ``docs/imu_capture_spec.md``. Both
sides of the system import it instead of re-deriving the format:

* **firmware** writes frames with :func:`encode_recording` / :class:`RecordingWriter`
  (or emits the same 24 bytes over a serial link);
* **analysis** reads them back with :func:`load` and gets physical units.

On-disk container ``IMUCAP2`` (append-friendly, self-describing, no deps)::

    <one line of UTF-8 JSON header>\\n
    <N fixed 24-byte little-endian frames>

Frame layout (``struct`` ``"<I7h3H"``, 24 bytes, no padding)::

    offset size type    field
    0      4    uint32  t_us       monotonic us since capture start
    4      2    int16   ax         accel X, raw LSB (+-2/4/8/16 g per header)
    6      2    int16   ay
    8      2    int16   az
    10     2    int16   gx         gyro X, raw LSB (per header)
    12     2    int16   gy
    14     2    int16   gz
    16     2    int16   temp_c100  temperature, 0.01 degC, saturating
    18     2    uint16  seq        per-recording frame counter, wraps at 65536
    20     2    uint16  flags      bit0 FIFO overrun, bit1 host overrun, bit2 t_us estimated
    22     2    uint16  reserved   write 0, ignore on read

The JSON header carries every constant needed to recover SI units plus the
provenance (sensor part, register configuration, mounting, wheel geometry), so a
file is readable years later without this code.

Reading is deliberately permissive across contract versions: :func:`load`
accepts ``IMUCAP2`` and the older ``IMUCAP1`` (6x int16 + optional uint32 us,
no temperature/sequence) so earlier captures stay analysable.
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

SPEC_VERSION = "2.1"
MAGIC_V2 = "IMUCAP2"
MAGIC_V1 = "IMUCAP1"
FRAME_STRUCT = "<I7h3H"
FRAME_BYTES = struct.calcsize(FRAME_STRUCT)
assert FRAME_BYTES == 24, FRAME_BYTES

DEFAULT_AXES = ("ax", "ay", "az", "gx", "gy", "gz")

#: Flags packed into ``frame.flags``.
FLAG_FIFO_OVERRUN = 1 << 0   # the sensor FIFO hit its watermark limit (FIFO_LOST_PKT incremented)
FLAG_HOST_OVERRUN = 1 << 1   # the host could not drain in time and dropped its own frames
FLAG_TIME_ESTIMATED = 1 << 2 # t_us interpolated from the nominal rate, not measured

#: Nominal frame rate for each named mode, Hz.
#:
#: * ``A`` — survey/burst on an **ICM-42688-P** (the CTG-11 carrier part, D4); full
#:   0-500 Hz imbalance band.
#: * ``B`` — long-run monitor on an ICM-42688-P; 0-250 Hz, half the storage.
#: * ``M`` — the **MPU-9250** that is physically on the bench today (identified from
#:   the hardware-inventory photographs); 0-150 Hz. See the spec S1.1/S3.6: an
#:   MPU-9250 *cannot* carry a 0-500 Hz band, so its mode is genuinely different
#:   and must not be silently treated as a slower mode A.
MODES = {"A": 4000.0, "B": 2000.0, "M": 1000.0}

#: Reset value of the ``WHO_AM_I`` register for each supported part. The register
#: *address* coincides (117 / 0x75) but the value does not, which is exactly what
#: makes an identity guard cheap and a silent wrong-part read catastrophic.
WHO_AM_I = {"ICM-42688-P": 0x47, "MPU-9250": 0x71}

#: Which part each mode's register configuration belongs to. The firmware must
#: read ``WHO_AM_I``, compare against this, and refuse to record on a mismatch
#: (spec S2.2) — otherwise the LSB scales in the header would be wrong and every
#: amplitude downstream would be wrong by a constant factor with no warning.
MODE_SENSOR = {"A": "ICM-42688-P", "B": "ICM-42688-P", "M": "MPU-9250"}

#: The I2C bus speed each mode's register configuration requires (spec S3.6/S4.6).
#: Mode A needs 1 MHz because 400 kHz cannot carry a 4 kHz FIFO on an ICM-42688-P;
#: mode M is limited to 400 kHz because that is the MPU-9250's datasheet maximum.
MODE_I2C_HZ = {"A": 1_000_000, "B": 1_000_000, "M": 400_000}

#: Register configuration per mode. Deliberately duplicated into the shipped JSON
#: header so a capture self-describes even if these constants change.
#: AAF/UI numbers are ICM-42688-P datasheet DS-000347 rev 1.8 S5.3 / S5.5 values.
MODE_CONFIG = {
    "A": dict(
        odr_hz=4000.0,
        band_hz=500.0,
        accel_fsr_g=16,
        gyro_fsr_dps=2000,
        aaf_bw_hz=785,
        accel_aaf=dict(bw_hz=785, delt=17, deltsqr=288, bitshift=7),
        gyro_aaf=dict(bw_hz=785, delt=17, deltsqr=288, bitshift=7),
        ui_filt_ord=2,          # 2 = 3rd order
        ui_filt_bw=4,           # code 4 -> 1048.1 Hz 3 dB at ODR 4000
        ui_bw_hz=1048.1,
        fifo_packet_bytes=16,   # Packet 3: header + accel + gyro + temp + ts
        fifo_wm_records=32,
        low_noise=True,
    ),
    "B": dict(
        odr_hz=2000.0,
        band_hz=250.0,
        accel_fsr_g=16,
        gyro_fsr_dps=2000,
        aaf_bw_hz=394,
        accel_aaf=dict(bw_hz=394, delt=9, deltsqr=81, bitshift=9),
        gyro_aaf=dict(bw_hz=394, delt=9, deltsqr=81, bitshift=9),
        ui_filt_ord=2,
        ui_filt_bw=4,           # code 4 -> 524.0 Hz 3 dB at ODR 2000
        ui_bw_hz=524.0,
        fifo_packet_bytes=16,
        fifo_wm_records=32,
        low_noise=True,
    ),
    # MPU-9250 (PS-MPU-9250A-01 rev 1.1 / RM-MPU-9250A-00 rev 1.6). The band is
    # 0-150 Hz, NOT 0-500: with the DLPF enabled the part offers only 1 kHz ODR and
    # 1 kHz's first fold-in into a 500 Hz band is 500 Hz — the band edge itself.
    # Its 4 kHz rate exists only with the DLPF bypassed (Dec1), whose stopband the
    # datasheet does not publish, so a documented anti-alias guarantee is impossible
    # there. A_DLPFCFG=7 (Dec2, 420 Hz 3 dB) is the flat choice inside 0-150 Hz:
    # -0.07 dB at 150 Hz, and -0.18 dB at 190 Hz for safety margin on the fence post.
    "M": dict(
        odr_hz=1000.0,
        band_hz=150.0,
        accel_fsr_g=16,
        gyro_fsr_dps=2000,
        dlpf_cfg=7,             # ACCEL_CONFIG2 A_DLPFCFG -> Dec2, 420 Hz 3 dB
        dlpf_bw_hz=420.0,
        gyro_dlpf_cfg=1,        # CONFIG DLPF_CFG=1 -> gyro 184 Hz 3 dB
        gyro_bw_hz=184.0,
        smplrt_div=0,           # 1 kHz / (1 + 0) = 1 kHz
        fifo_packet_bytes=14,   # accel(6) + gyro(6) + temp(2), FIFO_EN
        fifo_bytes=512,         # MPU-9250 has a 512-byte FIFO, not 2048
        fifo_wm_records=16,
        low_noise=True,
    ),
}

#: Datasheet sensitivity (ICM-42688-P, DS-000347 rev 1.8, tables 1 and 2).
ACCEL_LSB_PER_G = {2: 16384.0, 4: 8192.0, 8: 4096.0, 16: 2048.0}
GYRO_LSB_PER_DPS = {
    2000: 16.4, 1000: 32.8, 500: 65.5, 250: 131.0, 125: 262.0,
    62.5: 524.3, 31.25: 1048.6, 15.625: 2097.2,
}
G0 = 9.80665
TEMP_LSB_C = 0.01               # temp_c100 is 0.01 degC per LSB


def accel_scale(fsr_g: int) -> float:
    """m/s^2 per LSB for an accelerometer full-scale range in g."""
    return G0 / ACCEL_LSB_PER_G[int(fsr_g)]


def gyro_scale(fsr_dps: float) -> float:
    """deg/s per LSB for a gyroscope full-scale range in deg/s."""
    return 1.0 / GYRO_LSB_PER_DPS[float(fsr_dps)]


class IdentityMismatch(RuntimeError):
    """Raised when the part on the bus is not the part the mode was written for."""


def check_identity(mode: str, who_am_i: int, sensor: Optional[str] = None) -> str:
    """Guard a recording against the wrong part, or refuse to start (spec S2.2).

    The two supported parts answer ``WHO_AM_I`` at the *same register address*
    (117 / 0x75) with *different values* — ``0x47`` for the ICM-42688-P and
    ``0x71`` for the MPU-9250. Recording from the wrong one is not a degraded
    result, it is a silently wrong one: the header's LSB scale would be the other
    part's and every amplitude downstream would be off by a constant factor with
    nothing to flag it. So this raises instead of warning.

    Returns the resolved sensor name so callers can stamp it into the header.
    """
    sensor = sensor or MODE_SENSOR[mode]
    got = int(who_am_i) & 0xFF
    want = WHO_AM_I[sensor]
    if got != want:
        other = next((p for p, v in WHO_AM_I.items() if v == got), None)
        hint = (
            f"that is {other!r}, which this mode's register configuration is not written for"
            if other else
            "that is not a part this contract supports"
        )
        raise IdentityMismatch(
            f"mode {mode!r} requires {sensor} (WHO_AM_I 0x{want:02X}) but the device "
            f"answered 0x{got:02X} — {hint}. Refusing to record rather than write a "
            f"capture with the wrong LSB scales (spec S2.2)."
        )
    return sensor


@dataclass
class CaptureHeader:
    """Everything needed to interpret a recording, plus its provenance."""

    fs_nominal: float
    mode: str = "A"
    axes: List[str] = field(default_factory=lambda: list(DEFAULT_AXES))
    accel_fsr_g: int = 16
    gyro_fsr_dps: float = 2000.0
    scale_accel: float = 0.0        # m/s^2 per LSB, 0 => derive from fsr
    scale_gyro: float = 0.0         # deg/s per LSB, 0 => derive from fsr
    accel_units: str = "m/s^2"
    gyro_units: str = "deg/s"
    temp_lsb_c: float = TEMP_LSB_C
    start_unix_ns: int = 0
    device: str = "unknown"
    # free-form provenance: sensor config, mounting frame, wheel geometry
    notes: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.scale_accel:
            self.scale_accel = accel_scale(self.accel_fsr_g)
        if not self.scale_gyro:
            self.scale_gyro = gyro_scale(self.gyro_fsr_dps)

    def json_header(self) -> dict:
        d = asdict(self)
        d.update(
            magic=MAGIC_V2,
            spec_version=SPEC_VERSION,
            frame_bytes=FRAME_BYTES,
            frame_struct=FRAME_STRUCT,
            endian="little",
            n_axes=len(self.axes),
            gyro_units_note="deg/s as returned by the sensor; convert with *pi/180 for rad/s",
        )
        return d

    @classmethod
    def from_json_header(cls, hdr: dict) -> "CaptureHeader":
        h = dict(hdr)
        for k in ("magic", "spec_version", "frame_bytes", "frame_struct", "endian",
                  "n_axes", "gyro_units_note"):
            h.pop(k, None)
        return cls(**h)


@dataclass
class Recording:
    """Decoded samples in physical units."""

    header: CaptureHeader
    # shape (n, len(axes)); column order == header.axes
    samples: np.ndarray
    timestamps_us: np.ndarray
    temperature_c: np.ndarray
    seq: np.ndarray
    flags: np.ndarray

    @property
    def n(self) -> int:
        return int(self.samples.shape[0])

    @property
    def axes(self) -> List[str]:
        return list(self.header.axes)

    def axis(self, name: str) -> np.ndarray:
        return self.samples[:, self.header.axes.index(name)]

    def duration_s(self) -> float:
        if self.n == 0:
            return 0.0
        return float(self.timestamps_us[-1] - self.timestamps_us[0]) / 1e6

    def achieved_fs(self) -> float:
        """Sample rate actually implied by the timestamps (0.0 if undecidable)."""
        if self.n < 2 or self.timestamps_us[-1] == self.timestamps_us[0]:
            return 0.0
        return (self.n - 1) * 1e6 / float(self.timestamps_us[-1] - self.timestamps_us[0])

    def dropped_frames(self) -> int:
        """Frames missing according to the sequence counter (0 if none).

        ``seq`` wraps at 65536, so a gap is measured modulo 65536.
        """
        if self.n < 2:
            return 0
        s = self.seq.astype(np.int64)
        gap = (np.diff(s) - 1) % 65536
        return int(np.sum(gap))

    def overrun_frames(self) -> int:
        return int(np.count_nonzero(self.flags & FLAG_FIFO_OVERRUN))


def _pack_header(hdr: CaptureHeader) -> bytes:
    return json.dumps(hdr.json_header(), separators=(",", ":"), sort_keys=True).encode() + b"\n"


def _frame_dtype() -> np.dtype:
    """Numpy view of one 24-byte frame (``<I7h3H``); itemsize is asserted at import."""
    return np.dtype([
        ("t_us", "<u4"),
        ("a", "<i2", (3,)),
        ("g", "<i2", (3,)),
        ("temp_c100", "<i2"),
        ("seq", "<u2"),
        ("flags", "<u2"),
        ("reserved", "<u2"),
    ])


assert _frame_dtype().itemsize == FRAME_BYTES, _frame_dtype().itemsize


def encode_recording(rec: Recording) -> bytes:
    """Serialise a Recording to IMUCAP2 bytes (physical units -> raw LSB)."""
    hdr = rec.header
    n = rec.n
    if rec.samples.shape != (n, len(hdr.axes)):
        raise ValueError("sample array shape does not match the axis list")
    if any(len(x) != n for x in (rec.timestamps_us, rec.temperature_c, rec.seq, rec.flags)):
        raise ValueError("per-frame arrays must all be the same length as samples")
    if n == 0:
        return _pack_header(hdr)
    scale = np.array([hdr.scale_accel] * 3 + [hdr.scale_gyro] * 3, dtype=np.float64)
    raw = np.rint(rec.samples[:, :6] / scale)
    raw = np.clip(raw, -32768, 32767).astype(np.int64)
    temp = np.clip(np.rint(np.nan_to_num(rec.temperature_c) / hdr.temp_lsb_c),
                   -32768, 32767).astype(np.int64)
    frame = np.empty(n, dtype=_frame_dtype())
    frame["t_us"] = np.asarray(rec.timestamps_us, dtype=np.uint32)
    frame["a"] = raw[:, :3].astype(np.int16)
    frame["g"] = raw[:, 3:].astype(np.int16)
    frame["temp_c100"] = temp.astype(np.int16)
    frame["seq"] = np.asarray(rec.seq, dtype=np.uint16)
    frame["flags"] = np.asarray(rec.flags, dtype=np.uint16)
    frame["reserved"] = 0
    return _pack_header(hdr) + frame.tobytes()


def decode_v2(blob: bytes, allow_truncated: bool = True) -> Recording:
    """Parse IMUCAP2 bytes into a Recording.

    A capture cut mid-frame by a power loss is still valid up to the last whole
    frame: ``allow_truncated`` (the default) drops the partial tail instead of
    raising, so a run interrupted by a power cut stays analysable.
    """
    nl = blob.find(b"\n")
    if nl < 0:
        raise ValueError("missing JSON header line")
    hdr = json.loads(blob[:nl].decode("utf-8"))
    if hdr.get("magic") != MAGIC_V2:
        raise ValueError(f"not {MAGIC_V2}: magic={hdr.get('magic')!r}")
    body = blob[nl + 1:]
    n, rem = divmod(len(body), FRAME_BYTES)
    if rem and not allow_truncated:
        raise ValueError(f"truncated file: {rem} trailing bytes after {n} whole frames")
    header = CaptureHeader.from_json_header(hdr)
    arr = np.frombuffer(body[:n * FRAME_BYTES], dtype=_frame_dtype(), count=n)
    vals = np.hstack([arr["a"].reshape(n, 3), arr["g"].reshape(n, 3)])
    samples = vals.astype(np.float64) * np.array(
        [header.scale_accel] * 3 + [header.scale_gyro] * 3, dtype=np.float64
    )
    return Recording(
        header=header,
        samples=samples,
        timestamps_us=arr["t_us"].copy(),
        temperature_c=arr["temp_c100"].astype(np.float64) * header.temp_lsb_c,
        seq=arr["seq"].copy(),
        flags=arr["flags"].copy(),
    )


def decode_v1(blob: bytes) -> Recording:
    """Parse the older IMUCAP1 container (6x int16 [+ uint32 us], no temp/seq)."""
    nl = blob.find(b"\n")
    if nl < 0:
        raise ValueError("missing header line")
    hdr = json.loads(blob[:nl].decode("utf-8"))
    if hdr.get("magic") != MAGIC_V1:
        raise ValueError(f"not {MAGIC_V1}: magic={hdr.get('magic')!r}")
    axes = list(hdr.get("axes") or DEFAULT_AXES)
    nax = len(axes)
    has_ts = bool(hdr.get("has_timestamps"))
    body = blob[nl + 1:]
    fmt = f"<{'h' * nax}{'I' if has_ts else ''}"
    rec_size = struct.calcsize(fmt)
    n = len(body) // rec_size
    vals = np.empty((n, nax), dtype=np.int16)
    t_us = np.zeros(n, dtype=np.uint32)
    for i in range(n):
        chunk = struct.unpack(fmt, body[i * rec_size:(i + 1) * rec_size])
        vals[i] = chunk[:nax]
        if has_ts:
            t_us[i] = chunk[nax]
    if not has_ts:
        fs = float(hdr.get("fs") or 0.0)
        if fs <= 0:
            raise ValueError("IMUCAP1 without timestamps and without a usable fs")
        t_us = (np.arange(n, dtype=np.float64) * 1e6 / fs).astype(np.uint32)
    sa = float(hdr.get("scale_accel") or accel_scale(16))
    sg = float(hdr.get("scale_gyro") or gyro_scale(2000))
    header = CaptureHeader(
        fs_nominal=float(hdr.get("fs") or 0.0),
        mode="v1",
        axes=axes,
        scale_accel=sa,
        scale_gyro=sg,
        start_unix_ns=int(float(hdr.get("start_unix") or 0.0) * 1e9),
        device=str(hdr.get("device") or "unknown"),
        notes=dict(hdr.get("notes") or {}),
    )
    return Recording(
        header=header,
        samples=vals.astype(np.float64) * np.array([sa] * min(3, nax) + [sg] * max(0, nax - 3)),
        timestamps_us=t_us,
        temperature_c=np.full(n, np.nan),
        seq=(np.arange(n) % 65536).astype(np.uint16),
        flags=np.full(n, FLAG_TIME_ESTIMATED if not has_ts else 0, dtype=np.uint16),
    )


def decode(blob: bytes) -> Recording:
    """Parse either supported container version, dispatching on the header magic."""
    nl = blob.find(b"\n")
    if nl < 0:
        raise ValueError("missing header line")
    try:
        magic = json.loads(blob[:nl].decode("utf-8")).get("magic")
    except json.JSONDecodeError as exc:
        raise ValueError(f"header is not JSON: {exc}") from exc
    if magic == MAGIC_V2:
        return decode_v2(blob)
    if magic == MAGIC_V1:
        return decode_v1(blob)
    raise ValueError(f"unsupported magic {magic!r}; expected {MAGIC_V2} or {MAGIC_V1}")


def load(path: str) -> Recording:
    """Read a capture file (either container version)."""
    with open(path, "rb") as fh:
        return decode(fh.read())


def save(path: str, rec: Recording) -> None:
    with open(path, "wb") as fh:
        fh.write(encode_recording(rec))


class RecordingWriter:
    """Streaming writer: one frame at a time, so a power cut loses only the tail.

    ``flush_every`` frames are buffered before an fsync-able flush, which bounds
    the loss window (at mode A, 4000 frames == 1 s) and keeps the file readable
    after an unplanned power-down mid-run.
    """

    def __init__(self, path: str, header: CaptureHeader, flush_every: int = 4000):
        self.path = path
        self.header = header
        self.flush_every = int(flush_every)
        self._seq = 0
        self._t0: Optional[int] = None
        self._since_flush = 0
        self._fh = open(path, "wb")
        self._fh.write(_pack_header(header))
        self._fh.flush()

    def add(self, t_us: int, accel_lsb: Sequence[int], gyro_lsb: Sequence[int],
            temp_c: float = 25.0, flags: int = 0) -> None:
        if self._t0 is None:
            self._t0 = int(t_us)
        self._fh.write(struct.pack(
            FRAME_STRUCT,
            int(t_us) & 0xFFFFFFFF,
            *(int(v) for v in accel_lsb),
            *(int(v) for v in gyro_lsb),
            int(round(temp_c / self.header.temp_lsb_c)),
            self._seq & 0xFFFF,
            int(flags) & 0xFFFF,
            0,
        ))
        self._seq += 1
        self._since_flush += 1
        if self._since_flush >= self.flush_every:
            self._flush()

    def _flush(self) -> None:
        self._fh.flush()
        self._since_flush = 0

    def close(self) -> None:
        try:
            self._flush()
        finally:
            self._fh.close()

    def __enter__(self) -> "RecordingWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def make_synthetic(mode: str = "A", seconds: float = 1.0, wheel_hz: float = 14.74,
                   imbalance: Optional[Dict[float, float]] = None,
                   noise_g: float = 0.001453, seed: int = 0,
                   sensor: Optional[str] = None,
                   wheel: Optional[Dict[str, object]] = None) -> Recording:
    """Deterministic synthetic capture, for tests and for pipeline dry runs.

    ``imbalance`` maps rotation order -> amplitude in g, e.g. ``{1.0: 0.05}``.
    The DC term is 1 g on Z (the sensor sitting level, +Z down per the spec's
    mounting frame). Produces a capture that satisfies the documented contract,
    including the wheel geometry the analysis needs (spec S5.4).

    ``sensor`` defaults to the part that owns ``mode`` (``MODE_SENSOR``), so a
    mode-M capture is stamped ``MPU-9250``/``0x71`` and a mode-A one
    ``ICM-42688-P``/``0x47`` — the provenance cannot silently contradict the mode.
    """
    cfg = MODE_CONFIG[mode]
    sensor = sensor or MODE_SENSOR[mode]
    fs = float(cfg["odr_hz"])
    n = int(round(seconds * fs))
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    az = np.full(n, 1.0)
    ay = np.zeros(n)
    for order, amp_g in (imbalance or {}).items():
        w = 2 * np.pi * wheel_hz * order
        az += amp_g * np.cos(w * t)
        ay += 0.35 * amp_g * np.sin(w * t)
    ax = rng.normal(0, noise_g, n)
    ay = ay + rng.normal(0, noise_g, n)
    az = az + rng.normal(0, noise_g, n)
    gyro = rng.normal(0, 0.0028 / 1e3, (n, 3))
    accel_g = np.column_stack([ax, ay, az])
    samples = np.column_stack([accel_g, gyro]).astype(np.float64)
    samples[:, :3] = samples[:, :3] * G0
    header = CaptureHeader(
        fs_nominal=fs, mode=mode, accel_fsr_g=cfg["accel_fsr_g"],
        gyro_fsr_dps=cfg["gyro_fsr_dps"],
        start_unix_ns=int(1_700_000_000 * 1e9), device=sensor,
        notes=dict(
            sensor=sensor, who_am_i=hex(WHO_AM_I[sensor]), i2c_addr="0x68",
            # the band this capture is valid over: mode M's 150 Hz must travel with
            # the file, or a consumer will assume the widest band (spec S5.4/S8.1).
            band_hz=cfg["band_hz"],
            i2c_hz=MODE_I2C_HZ[mode],
            config=cfg,
            mounting=dict(
                location="chassis, rigid", corner="front-left",
                axes="+X forward, +Y right, +Z down",
            ),
            wheel=dict(wheel or {"diameter_m": 0.60, "corner": "front-left", "count": 4}),
            vehicle="synthetic test vehicle",
            firmware="imu_capture.py reference",
            flags_legend={
                "0": "FIFO overrun", "1": "host overrun", "2": "timestamp estimated",
            },
            synthetic=True, wheel_hz=wheel_hz, imbalance=dict(imbalance or {}),
        ),
    )
    return Recording(
        header=header,
        samples=samples,
        timestamps_us=(np.rint(t * 1e6).astype(np.uint64) & 0xFFFFFFFF).astype(np.uint32),
        temperature_c=np.full(n, 25.0),
        seq=(np.arange(n) % 65536).astype(np.uint16),
        flags=np.zeros(n, dtype=np.uint16),
    )


def _selfcheck() -> str:
    """Cheap invariant check; also the entry point for `python3 -m imu_capture`."""
    problems = []
    if FRAME_BYTES != 24:
        problems.append(f"frame is {FRAME_BYTES} bytes, spec says 24")
    for mode, cfg in MODE_CONFIG.items():
        if cfg["odr_hz"] != MODES[mode]:
            problems.append(f"mode {mode} ODR disagrees between MODES and MODE_CONFIG")
    if set(MODE_SENSOR) != set(MODES) or set(MODE_I2C_HZ) != set(MODES):
        problems.append("MODES / MODE_SENSOR / MODE_I2C_HZ do not agree on the mode set")
    for mode, sensor in MODE_SENSOR.items():
        if sensor not in WHO_AM_I:
            problems.append(f"mode {mode} names an unknown part {sensor!r}")
        # the guard must accept the right part and reject the other one
        if check_identity(mode, WHO_AM_I[sensor]) != sensor:
            problems.append(f"identity guard did not resolve mode {mode}")
    if WHO_AM_I["ICM-42688-P"] == WHO_AM_I["MPU-9250"]:
        problems.append("the two parts share a WHO_AM_I value, so the guard is useless")
    for fsr in (2, 4, 8, 16):
        if ACCEL_LSB_PER_G[fsr] != 32768.0 / fsr:
            problems.append(f"accel {fsr}g LSB/g is not 32768/fsr")
    rec = make_synthetic("A", seconds=0.05, imbalance={1.0: 0.05})
    back = decode(encode_recording(rec))
    if back.n != rec.n:
        problems.append(f"round trip changed the frame count ({rec.n} -> {back.n})")
    if not np.allclose(back.samples, rec.samples, atol=accel_scale(16) / 2 + 1e-12):
        problems.append("round trip exceeded one LSB on the sample values")
    if not np.array_equal(back.timestamps_us, rec.timestamps_us):
        problems.append("round trip lost the timestamps")
    return "OK: imu_capture spec v%s, %d-byte frames, round trip clean" % (
        SPEC_VERSION, FRAME_BYTES) if not problems else "FAIL: " + "; ".join(problems)


if __name__ == "__main__":
    print(_selfcheck())
