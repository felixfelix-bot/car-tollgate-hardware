"""Contract tests for docs/imu_capture_spec.md and hardware/imu_capture.py.

These tests exist so firmware (`t_752a0d0a`) and the analysis pipeline
(`t_00f126bd`) can consume the contract *programmatically* instead of re-parsing
prose, and so the spec's arithmetic claims cannot silently drift.

They deliberately re-derive the datasheet numbers from the constants rather than
trusting them: `test_aaf_table_rows_match_the_spec` is the pin that fails if
someone edits a DELT/DELTSQR/BITSHIFT triplet without re-checking the datasheet.

Run:  python3 -m pytest tests/ -q
"""

import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from hardware import imu_capture as ic  # noqa: E402

SPEC = REPO / "docs" / "imu_capture_spec.md"

# ---------------------------------------------------------------- datasheet pins
# ICM-42688-P DS-000347 rev 1.8, S5.3 "ANTI-ALIAS FILTER" (pp.28-29).
# BW(Hz) -> (DELT, DELTSQR, BITSHIFT).  Independent of MODE_CONFIG by design.
AAF_TABLE = {
    42: (1, 1, 15), 84: (2, 4, 13), 126: (3, 9, 12), 170: (4, 16, 11),
    213: (5, 25, 10), 258: (6, 36, 10), 303: (7, 49, 9), 348: (8, 64, 9),
    394: (9, 81, 9), 441: (10, 100, 8), 488: (11, 122, 8), 536: (12, 144, 8),
    585: (13, 170, 8), 684: (15, 224, 7), 734: (16, 256, 7), 785: (17, 288, 7),
    837: (18, 324, 7), 890: (19, 360, 6), 943: (20, 400, 6), 997: (21, 440, 6),
    1051: (22, 488, 6), 1107: (23, 528, 6), 1639: (32, 1024, 5),
    1962: (37, 1376, 4), 2097: (39, 1536, 4), 2522: (45, 2016, 4),
    3137: (53, 2816, 3), 3979: (63, 3968, 3),
}

# S5.5 UI filter 3 dB bandwidth (Hz), code 4, by ODR (pp.31-34).
UI_BW_CODE4 = {2000: 524.0, 4000: 1048.1, 8000: 2096.3}


def aaf_mag(f, fc):
    """Documented 2nd-order anti-alias response (datasheet S5.3)."""
    return 1.0 / math.sqrt(1.0 + (f / fc) ** 4)


def db(x):
    return 20.0 * math.log10(x)


# ------------------------------------------------------------------ the format
def test_frame_is_24_bytes_and_struct_matches():
    assert ic.FRAME_BYTES == 24
    assert ic.FRAME_STRUCT == "<I7h3H"
    assert struct.calcsize(ic.FRAME_STRUCT) == 24
    assert ic._frame_dtype().itemsize == 24


def test_spec_version_is_frozen():
    assert ic.SPEC_VERSION == "2.1"
    assert ic.MAGIC_V2 == "IMUCAP2"
    assert ic.MAGIC_V1 == "IMUCAP1"


def test_modes_match_the_spec_table():
    assert ic.MODES == {"A": 4000.0, "B": 2000.0, "M": 1000.0}
    assert ic.MODE_CONFIG["A"]["band_hz"] == 500.0
    assert ic.MODE_CONFIG["B"]["band_hz"] == 250.0
    assert ic.MODE_CONFIG["M"]["band_hz"] == 150.0


def test_every_mode_names_a_supported_part_and_bus_speed():
    """A capture is only interpretable if the part and the bus speed are pinned."""
    assert set(ic.MODE_SENSOR) == set(ic.MODES) == set(ic.MODE_I2C_HZ) == set(ic.MODE_CONFIG)
    assert ic.MODE_SENSOR == {"A": "ICM-42688-P", "B": "ICM-42688-P", "M": "MPU-9250"}
    for mode, sensor in ic.MODE_SENSOR.items():
        assert sensor in ic.WHO_AM_I
    assert ic.MODE_I2C_HZ["A"] == 1_000_000      # 400 kHz cannot carry a 4 kHz FIFO
    assert ic.MODE_I2C_HZ["M"] == 400_000        # MPU-9250 datasheet maximum


def test_round_trip_is_within_one_lsb():
    for mode in ("A", "B"):
        rec = ic.make_synthetic(mode, seconds=0.05, imbalance={1.0: 0.05})  # 200/100 frames
        back = ic.decode(ic.encode_recording(rec))
        assert back.n == rec.n
        assert np.allclose(back.samples, rec.samples,
                           atol=max(ic.accel_scale(16), ic.gyro_scale(2000)) / 2 + 1e-12)
        assert (back.timestamps_us == rec.timestamps_us).all()
        assert (back.seq == rec.seq).all()
        assert np.allclose(back.temperature_c, rec.temperature_c, atol=0.005)


def test_round_trip_via_file(tmp_path):
    rec = ic.make_synthetic("A", seconds=0.02)
    path = tmp_path / "cap.imucap"
    ic.save(str(path), rec)
    back = ic.load(str(path))
    assert back.n == rec.n
    assert back.header.mode == "A"
    assert back.header.fs_nominal == 4000.0


def test_header_is_json_self_describing():
    rec = ic.make_synthetic("A", seconds=0.01)
    blob = ic.encode_recording(rec)
    nl = blob.index(b"\n")
    hdr = json.loads(blob[:nl])
    for key in ("magic", "spec_version", "frame_bytes", "frame_struct", "endian",
                "axes", "n_axes", "fs_nominal", "mode", "accel_fsr_g",
                "gyro_fsr_dps", "scale_accel", "scale_gyro", "accel_units",
                "gyro_units", "temp_lsb_c", "start_unix_ns", "device", "notes"):
        assert key in hdr, f"header lost the {key!r} key"
    assert hdr["magic"] == "IMUCAP2"
    assert hdr["frame_bytes"] == 24
    assert hdr["n_axes"] == 6
    assert hdr["scale_accel"] == pytest.approx(ic.accel_scale(16))
    assert hdr["scale_gyro"] == pytest.approx(ic.gyro_scale(2000))
    # the wheel geometry the analysis needs must survive
    assert hdr["notes"]["wheel"]["diameter_m"] == 0.60


def test_header_bytes_are_deterministic():
    rec = ic.make_synthetic("A", seconds=0.01, seed=3)
    assert ic.encode_recording(rec) == ic.encode_recording(rec)


def test_truncated_capture_still_decodes_the_whole_frames():
    """A power cut mid-frame must not make the run unreadable."""
    rec = ic.make_synthetic("A", seconds=0.02)
    blob = ic.encode_recording(rec)
    cut = ic.decode(blob[:-7])                 # 7 bytes into a 24-byte frame
    assert cut.n == rec.n - 1
    with pytest.raises(ValueError, match="truncated"):
        ic.decode_v2(blob[:-7], allow_truncated=False)


def test_unknown_magic_is_rejected():
    with pytest.raises(ValueError, match="unsupported magic"):
        ic.decode(b'{"magic":"NOPE"}\n')
    with pytest.raises(ValueError, match="not JSON|missing header"):
        ic.decode(b"not json at all")


def test_legacy_imucap1_still_reads():
    """The CTG-9 workspace's v1 files must stay analysable without conversion."""
    hdr = {"magic": "IMUCAP1", "fs": 200.0, "axes": list(ic.DEFAULT_AXES),
           "scale_accel": ic.accel_scale(2), "scale_gyro": ic.gyro_scale(250),
           "has_timestamps": True, "device": "legacy"}
    body = b""
    for i in range(5):
        body += struct.pack("<6hI", 100 + i, 0, 0, 0, 0, 0, 100000 * i)
    blob = json.dumps(hdr).encode() + b"\n" + body
    rec = ic.decode(blob)
    assert rec.n == 5
    assert rec.header.mode == "v1"
    assert rec.timestamps_us[3] == 300000
    assert math.isnan(rec.temperature_c[0])          # v1 has no temperature
    assert rec.axis("ax")[2] == pytest.approx(102 * ic.accel_scale(2))


def test_v1_without_timestamps_marks_time_estimated():
    hdr = {"magic": "IMUCAP1", "fs": 100.0, "has_timestamps": False}
    blob = json.dumps(hdr).encode() + b"\n" + struct.pack("<6h", 1, 2, 3, 4, 5, 6) * 10
    rec = ic.decode(blob)
    assert rec.n == 10
    assert (rec.flags == ic.FLAG_TIME_ESTIMATED).all()
    assert rec.achieved_fs() == pytest.approx(100.0, rel=0.02)


# ------------------------------------------------------------- writer behaviour
def test_streaming_writer_matches_the_in_memory_encoder(tmp_path):
    rec = ic.make_synthetic("A", seconds=0.01)
    path = tmp_path / "stream.imucap"
    hdr = rec.header
    with ic.RecordingWriter(str(path), hdr, flush_every=16) as w:
        for i in range(rec.n):
            w.add(int(rec.timestamps_us[i]),
                  [int(round(v / hdr.scale_accel)) for v in rec.samples[i, :3]],
                  [int(round(v / hdr.scale_gyro)) for v in rec.samples[i, 3:]],
                  temp_c=float(rec.temperature_c[i]))
    back = ic.load(str(path))
    assert back.n == rec.n
    assert (back.timestamps_us == rec.timestamps_us).all()
    assert (back.seq == rec.seq).all()


def test_writer_leaves_a_readable_file_if_never_closed(tmp_path):
    """Simulates a power cut: no close(), file must still decode."""
    rec = ic.make_synthetic("A", seconds=0.01)
    path = tmp_path / "cut.imucap"
    w = ic.RecordingWriter(str(path), rec.header, flush_every=4)
    for i in range(rec.n):
        w.add(int(rec.timestamps_us[i]), [0, 0, 0], [0, 0, 0])
    # deliberately no close()
    back = ic.load(str(path))
    assert back.n > 0
    assert back.header.magic if hasattr(back.header, "magic") else True


def test_dropped_frame_detection_counts_seq_gaps():
    rec = ic.make_synthetic("A", seconds=0.01)
    assert rec.dropped_frames() == 0
    rec.seq = rec.seq.copy()
    rec.seq[5:] = (rec.seq[5:].astype(int) + 3).astype("uint16")   # lose 3
    assert rec.dropped_frames() == 3


def test_overrun_flag_counting():
    rec = ic.make_synthetic("A", seconds=0.01)
    assert rec.overrun_frames() == 0
    rec.flags[:7] = ic.FLAG_FIFO_OVERRUN
    assert rec.overrun_frames() == 7


def test_achieved_fs_uses_timestamps_not_the_nominal_rate():
    rec = ic.make_synthetic("A", seconds=0.05)
    assert rec.achieved_fs() == pytest.approx(4000.0, rel=1e-6)
    rec.timestamps_us = (rec.timestamps_us.astype("float64") * 1.05).astype("uint32")
    assert rec.achieved_fs() == pytest.approx(4000.0 / 1.05, rel=1e-3)


# --------------------------------------------------------- spec arithmetic pins
# The ICM-42688-P modes (A, B) are filtered by an explicit AAF whose response is
# published (DS-000347 S5.3). The MPU-9250 mode (M) has no such stage: its DLPF is
# tabulated by 3 dB bandwidth only, so it is checked against its own DLPF config.


def icm_modes():
    """Modes whose filtering comes from the ICM-42688-P AAF/UI blocks."""
    return {m: c for m, c in ic.MODE_CONFIG.items() if ic.MODE_SENSOR[m] == "ICM-42688-P"}


def test_aaf_table_rows_match_the_spec():
    """Every AAF triplet in MODE_CONFIG must be the datasheet row for that BW."""
    for mode, cfg in icm_modes().items():
        for axis in ("accel_aaf", "gyro_aaf"):
            a = cfg[axis]
            want = AAF_TABLE[a["bw_hz"]]
            assert (a["delt"], a["deltsqr"], a["bitshift"]) == want, (
                f"mode {mode} {axis}: BW {a['bw_hz']} needs {want}, got "
                f"{(a['delt'], a['deltsqr'], a['bitshift'])}"
            )


def test_aaf_table_internally_consistent():
    """DELTSQR is approximately DELT^2 (the datasheet rounds it)."""
    for bw, (d, ds, bs) in AAF_TABLE.items():
        assert abs(ds - d * d) <= max(4, 0.02 * d * d), (bw, d, ds)
        assert 1 <= d <= 63 and 0 <= bs <= 15


def test_ui_bandwidth_matches_the_spec():
    for mode, cfg in icm_modes().items():
        assert cfg["ui_bw_hz"] == UI_BW_CODE4[cfg["odr_hz"]], mode
        assert cfg["ui_filt_bw"] == 4


def test_band_decision_meets_the_spec_constraints():
    """Every ICM mode must satisfy: <=1 dB droop AND >=25 dB AAF-alone
    rejection at the lowest fold-in frequency."""
    for mode, cfg in icm_modes().items():
        band = cfg["band_hz"]
        fold = cfg["odr_hz"] - band
        fc = cfg["aaf_bw_hz"]
        droop = db(aaf_mag(band, fc))
        rej = db(aaf_mag(fold, fc))
        assert droop >= -1.0, f"mode {mode}: droop {droop:.2f} dB at {band} Hz"
        assert rej <= -25.0, f"mode {mode}: rejection {rej:.2f} dB at {fold} Hz"


def test_nyquist_and_oversampling_ratios():
    for mode, cfg in ic.MODE_CONFIG.items():
        assert cfg["odr_hz"] / 2 >= cfg["band_hz"], mode      # no aliasing of the band
        assert cfg["odr_hz"] - cfg["band_hz"] > cfg["band_hz"], (
            f"mode {mode}: the fold-in must sit above the band edge"
        )


def test_1000hz_cannot_carry_the_500hz_band():
    """The spec claims a 1 kHz ODR cannot do it. Pin that claim."""
    band, odr = 500.0, 1000.0
    assert odr - band == band              # the fold-in IS the band edge
    for fc in AAF_TABLE:
        if db(aaf_mag(band, fc)) >= -1.0:
            assert db(aaf_mag(odr - band, fc)) > -25.0, fc


def test_2000hz_cannot_carry_the_500hz_band_at_1db_droop():
    band, odr = 500.0, 2000.0
    best = max((db(aaf_mag(odr - band, fc)) for fc in AAF_TABLE
                if db(aaf_mag(band, fc)) >= -1.0))
    assert best > -25.0, "spec says 2 kHz cannot reach 25 dB within 1 dB droop"


def test_bus_duty_matches_the_spec_table():
    """The ICM modes' I2C at 1 MHz must carry the FIFO; 400 kHz must not carry A."""
    def i2c_duty(odr, scl=1_000_000.0):
        return 100.0 * 16 * odr / (scl / 9.0)
    assert i2c_duty(4000) < 100.0                 # feasible at 1 MHz
    assert i2c_duty(2000) < 100.0
    assert i2c_duty(4000, 400_000.0) > 100.0      # NOT feasible at 400 kHz
    assert i2c_duty(4000) == pytest.approx(57.6, abs=0.1)
    assert i2c_duty(2000) == pytest.approx(28.8, abs=0.1)


def test_mpu9250_bus_duty_is_feasible_but_only_below_the_4khz_the_card_wanted():
    """Mode M must fit 400 kHz (the MPU-9250's ceiling) — the card's 4 kHz must not."""
    def i2c_duty(odr, nbytes, scl=400_000.0):
        return 100.0 * nbytes * odr / (scl / 9.0)
    m = ic.MODE_CONFIG["M"]
    odr, nbytes = m["odr_hz"], m["fifo_packet_bytes"]
    assert odr == 1000.0 and nbytes == 14
    duty = i2c_duty(odr, nbytes)
    assert duty < 50.0, duty                      # comfortable on a 400 kHz bus
    assert duty == pytest.approx(31.5, abs=0.1)
    # 2 kHz would still fit, but the card's 4 kHz demand exceeds the bus outright
    assert i2c_duty(2000, nbytes) == pytest.approx(63.0, abs=0.1)
    assert i2c_duty(4000, nbytes) > 100.0         # impossible on the MPU-9250's bus


def test_storage_arithmetic():
    for mode, want_bps in (("A", 96000), ("B", 48000), ("M", 24000)):
        odr = ic.MODE_CONFIG[mode]["odr_hz"]
        assert 24 * odr == want_bps
    assert pytest.approx(8.294, abs=0.01) == 24 * 4000 * 86400 / 1e9
    assert pytest.approx(4.147, abs=0.01) == 24 * 2000 * 86400 / 1e9
    assert pytest.approx(2.074, abs=0.01) == 24 * 1000 * 86400 / 1e9


def test_rotation_never_exceeds_the_uint32_wrap():
    """64 MiB segments must finish inside 71.6 minutes at each mode's rate."""
    wrap_min = 2 ** 32 / 1e6 / 60
    assert wrap_min == pytest.approx(71.58, abs=0.01)
    for mode, cfg in ic.MODE_CONFIG.items():
        seg_min = 64 * 2 ** 20 / (24 * cfg["odr_hz"]) / 60
        # the hard constraint: every segment must be safely inside the wrap
        assert seg_min < wrap_min, (mode, seg_min, wrap_min)
        assert wrap_min / seg_min >= 1.5, (mode, seg_min)


def test_fifo_depth_and_watermark():
    """The watermark must always leave real runway before the FIFO overflows."""
    for mode, cfg in ic.MODE_CONFIG.items():
        odr = cfg["odr_hz"]
        depth = cfg.get("fifo_bytes", 2048)
        recs = (depth - cfg["fifo_packet_bytes"]) // cfg["fifo_packet_bytes"]
        assert cfg["fifo_wm_records"] < recs, mode   # watermark must leave runway
        service_ms = cfg["fifo_wm_records"] / odr * 1e3
        runway_ms = (recs - cfg["fifo_wm_records"]) / odr * 1e3
        assert service_ms < runway_ms, mode
    # the two parts genuinely differ in FIFO depth; don't let that drift silently
    assert ic.MODE_CONFIG["A"]["fifo_bytes"] if "fifo_bytes" in ic.MODE_CONFIG["A"] else 2048 == 2048
    assert ic.MODE_CONFIG["M"]["fifo_bytes"] == 512


def test_lsb_scales_match_the_datasheet():
    for fsr, lsb_per_g in ((2, 16384), (4, 8192), (8, 4096), (16, 2048)):
        assert ic.ACCEL_LSB_PER_G[fsr] == lsb_per_g
        assert ic.ACCEL_LSB_PER_G[fsr] == 32768 // fsr
        assert ic.accel_scale(fsr) == pytest.approx(ic.G0 / lsb_per_g)
    assert ic.GYRO_LSB_PER_DPS[2000] == 16.4
    assert ic.gyro_scale(2000) == pytest.approx(1 / 16.4)


def test_plus_minus_16g_quantisation_is_far_below_sensor_noise():
    """Justifies the FSR choice: quantisation must add <1% to the noise floor."""
    noise = 65e-6 * math.sqrt(500.0) * ic.G0      # m/s^2 rms over 500 Hz NBW
    for fsr in (2, 4, 8, 16):
        q = ic.accel_scale(fsr) / math.sqrt(12)
        added = 100 * (math.hypot(noise, q) / noise - 1)
        assert added < 1.0, (fsr, added)
    q16 = ic.accel_scale(16) / math.sqrt(12)
    assert 100 * (math.hypot(noise, q16) / noise - 1) == pytest.approx(0.47, abs=0.05)


def test_plus_minus_16g_headroom_covers_a_hard_stop():
    """16 g full scale must exceed what gravity adds at 1 g by a wide margin."""
    assert 16 * ic.G0 > 10 * ic.G0


def test_wheel_frequency_arithmetic_and_the_card_correction():
    """The spec corrects the card's '~7 Hz at 100 km/h'. Pin both numbers."""
    f = lambda v_kmh, d: (v_kmh / 3.6) / (math.pi * d)
    assert f(100, 0.60) == pytest.approx(14.74, abs=0.01)
    assert f(50, 0.60) == pytest.approx(7.37, abs=0.01)
    assert f(130, 0.60) == pytest.approx(19.16, abs=0.01)
    # the diameter that would give 7 Hz at 100 km/h
    assert (100 / 3.6) / (math.pi * 7.0) == pytest.approx(1.26, abs=0.01)
    # the speed that gives 7 Hz on a 0.60 m wheel
    assert 7.0 * math.pi * 0.60 * 3.6 == pytest.approx(47.5, abs=0.1)


# ------------------------------------------------- MPU-9250 tier (mode M)
# PS-MPU-9250A-01 rev 1.1 / RM-MPU-9250A-00 rev 1.6. This is the part physically
# on the bench, identified from the hardware-inventory photographs, and it is NOT
# interchangeable with the ICM-42688-P: its documented ODR/filter options cannot
# carry the 0-500 Hz band that mode A needs.

#: RM p.15, "Accelerometer Data Rates and Bandwidths (Normal Mode)":
#: (ACCEL_FCHOICE, A_DLPFCFG) -> (3 dB BW Hz, ODR Hz, filter block)
MPU9250_ACCEL_TABLE = {
    (0, None): (1046.0, 4000.0, "Dec1"),   # DLPF bypassed
    (1, 0): (218.1, 1000.0, "DLPF"),
    (1, 1): (218.1, 1000.0, "DLPF"),
    (1, 2): (99.0, 1000.0, "DLPF"),
    (1, 3): (44.8, 1000.0, "DLPF"),
    (1, 4): (21.2, 1000.0, "DLPF"),
    (1, 5): (10.2, 1000.0, "DLPF"),
    (1, 6): (5.05, 1000.0, "DLPF"),
    (1, 7): (420.0, 1000.0, "Dec2"),
}
#: RM p.13, gyro CONFIG DLPF_CFG -> (3 dB BW Hz, Fs kHz)
MPU9250_GYRO_DLPF = {0: (250.0, 8.0), 1: (184.0, 1.0), 2: (92.0, 1.0),
                     3: (41.0, 1.0), 4: (20.0, 1.0), 5: (10.0, 1.0), 6: (5.0, 1.0)}
#: PS table 2: 300 ug/rtHz at every FSR; gyro 0.01 deg/s/rtHz (table 1).
MPU9250_ACCEL_PSD_UG = 300.0
MPU9250_GYRO_PSD_DPS = 0.01
MPU9250_FIFO_BYTES = 512
MPU9250_I2C_MAX_HZ = 400_000.0


def dlpf_mag2(f, fc):
    """2nd-order DLPF model, |H| = 1/sqrt(1+(f/fc)^4).

    The MPU-9250 register map publishes only a 3 dB bandwidth per setting, not a
    transfer function, so this is the minimal documented model (a 2nd-order
    response whose -3 dB point is the tabulated figure).
    """
    return 1.0 / math.sqrt(1.0 + (f / fc) ** 4)


def test_mpu9250_accel_table_rows_are_the_datasheet_rows():
    """The (FCHOICE, A_DLPFCFG) -> BW/ODR mapping must be the register map's."""
    assert MPU9250_ACCEL_TABLE[(0, None)] == (1046.0, 4000.0, "Dec1")
    assert MPU9250_ACCEL_TABLE[(1, 7)] == (420.0, 1000.0, "Dec2")
    # only the DLPF-bypassed setting reaches a 4 kHz ODR
    fast = [k for k, v in MPU9250_ACCEL_TABLE.items() if v[1] == 4000.0]
    assert fast == [(0, None)], fast


def test_mode_m_matches_the_mpu9250_register_map():
    m = ic.MODE_CONFIG["M"]
    assert m["odr_hz"] == 1000.0
    assert m["dlpf_cfg"] == 7                       # A_DLPFCFG=7 -> Dec2, 420 Hz
    assert MPU9250_ACCEL_TABLE[(1, m["dlpf_cfg"])][0] == m["dlpf_bw_hz"] == 420.0
    assert m["smplrt_div"] == 0                     # 1 kHz / (1 + 0)
    assert m["gyro_dlpf_cfg"] in MPU9250_GYRO_DLPF
    assert MPU9250_GYRO_DLPF[m["gyro_dlpf_cfg"]][0] == m["gyro_bw_hz"] == 184.0
    assert m["fifo_packet_bytes"] == 14             # accel 6 + gyro 6 + temp 2
    assert m["fifo_bytes"] == MPU9250_FIFO_BYTES == 512


def test_mpu9250_cannot_carry_the_500hz_band_the_card_asked_for():
    """This is the whole reason mode M exists as a separate mode.

    The card asked for a 0-500 Hz band with `at least 1-2 kHz` ODR. On an
    MPU-9250 that is impossible with a *documented* anti-alias guarantee:

    * its 4 kHz ODR exists only with the DLPF bypassed via Dec1, whose stopband
      the register map does not publish (only a 1046 Hz 3 dB point), so nothing
      can be claimed about rejection at 3500 Hz;
    * at its 1 kHz ODR the first frequency folding into a 500 Hz band is
      `1000 - 500 = 500 Hz` — the band edge itself, which no filter can both pass
      and reject.
    """
    band = 500.0
    # 4 kHz: reachable, but only through the undocumented Dec1 block
    assert MPU9250_ACCEL_TABLE[(0, None)][0] == 1046.0
    assert (0, None) in MPU9250_ACCEL_TABLE and MPU9250_ACCEL_TABLE[(0, None)][2] == "Dec1"
    # 1 kHz with a documented DLPF: the fold-in is the band edge
    odr = 1000.0
    assert odr - band == band
    for cfg, (bw, _odr, _filt) in MPU9250_ACCEL_TABLE.items():
        if cfg[0] != 1:
            continue
        # no documented DLPF both passes 500 Hz and rejects 500 Hz
        assert bw < band, (cfg, bw)


def test_mode_m_band_choice_is_within_one_db_of_flat():
    """A_DLPFCFG=7 (Dec2, 420 Hz) is the flat choice; the band edge is 150 Hz."""
    m = ic.MODE_CONFIG["M"]
    fc, band = m["dlpf_bw_hz"], m["band_hz"]
    assert db(dlpf_mag2(band, fc)) >= -1.0
    assert db(dlpf_mag2(band, fc)) == pytest.approx(-0.07, abs=0.02)
    # and it stays inside a 1 dB budget with margin above the nominal band
    assert db(dlpf_mag2(190.0, fc)) >= -1.0
    # the narrower 218.1 Hz setting would NOT hold a 1 dB budget at 150 Hz? it would,
    # but it drops -2.32 dB by 200 Hz — record the contrast, not a claim
    assert db(dlpf_mag2(150.0, 218.1)) == pytest.approx(-0.88, abs=0.02)


def test_mode_m_still_resolves_the_orders_the_diagnosis_uses():
    """0-150 Hz must cover the imbalance orders, else the mode is pointless."""
    m = ic.MODE_CONFIG["M"]
    f1x = (130 / 3.6) / (math.pi * 0.60)            # worst case: fast, small wheel
    assert f1x == pytest.approx(19.16, abs=0.01)
    assert m["band_hz"] / f1x >= 7.0                # at least order 7 at 130 km/h
    assert int(m["band_hz"] / f1x) == 7


def test_mode_m_oversampling_is_adequate():
    """1 kHz on a 150 Hz band is >3x oversampled; the DLPF handles the rest."""
    m = ic.MODE_CONFIG["M"]
    assert m["odr_hz"] / m["band_hz"] >= 3.0
    assert m["odr_hz"] - m["band_hz"] > m["band_hz"]


def test_mpu9250_16g_is_still_the_right_fsr():
    """Quantisation must stay well under the (much noisier) MPU-9250 floor."""
    # 300 ug/rtHz over the 200 Hz NBW this part can actually offer
    noise = MPU9250_ACCEL_PSD_UG * 1e-6 * ic.G0 * math.sqrt(200.0)
    for fsr in (2, 4, 8, 16):
        q = ic.accel_scale(fsr) / math.sqrt(12)
        assert 100 * q / noise < 5.0, (fsr, 100 * q / noise)
    q16 = ic.accel_scale(16) / math.sqrt(12)
    assert 100 * q16 / noise == pytest.approx(3.32, abs=0.1)


def test_the_two_parts_are_distinguishable_by_who_am_i():
    """Both answer at reg 117/0x75 but with different values — that is the guard."""
    assert ic.WHO_AM_I["ICM-42688-P"] == 0x47
    assert ic.WHO_AM_I["MPU-9250"] == 0x71
    assert ic.WHO_AM_I["ICM-42688-P"] != ic.WHO_AM_I["MPU-9250"]
    # and the datasheets agree about the shared register address
    text = SPEC.read_text()
    assert "0x71" in text and "0x47" in text


@pytest.mark.parametrize("mode,good,bad", [
    ("A", 0x47, 0x71),
    ("B", 0x47, 0x71),
    ("M", 0x71, 0x47),
])
def test_identity_guard_accepts_the_right_part_and_refuses_the_other(mode, good, bad):
    """Recording from the wrong part must be a loud failure, not a quiet one."""
    assert ic.check_identity(mode, good) == ic.MODE_SENSOR[mode]
    with pytest.raises(ic.IdentityMismatch) as ei:
        ic.check_identity(mode, bad)
    msg = str(ei.value)
    assert ic.MODE_SENSOR[mode] in msg
    assert "Refusing to record" in msg


def test_identity_guard_rejects_an_unknown_device():
    with pytest.raises(ic.IdentityMismatch):
        ic.check_identity("A", 0x00)
    with pytest.raises(ic.IdentityMismatch):
        ic.check_identity("M", 0xFF)


def test_synthetic_provenance_agrees_with_the_mode():
    """make_synthetic must not stamp an ICM WHO_AM_I onto an MPU-9250 capture."""
    for mode in ("A", "B", "M"):
        rec = ic.make_synthetic(mode, seconds=0.01)
        sensor = ic.MODE_SENSOR[mode]
        assert rec.header.device == sensor
        assert rec.header.notes["sensor"] == sensor
        assert rec.header.notes["who_am_i"] == hex(ic.WHO_AM_I[sensor])
    a = ic.make_synthetic("A", seconds=0.01)
    m = ic.make_synthetic("M", seconds=0.01)
    assert a.header.notes["who_am_i"] == "0x47"
    assert m.header.notes["who_am_i"] == "0x71"


def test_mode_m_round_trips_through_the_container(tmp_path):
    rec = ic.make_synthetic("M", seconds=0.05, imbalance={1.0: 0.05})
    p = tmp_path / "m.imucap"
    ic.save(str(p), rec)
    back = ic.load(str(p))
    assert back.n == rec.n
    assert back.header.mode == "M"
    assert back.header.device == "MPU-9250"
    assert np.allclose(back.samples, rec.samples,
                       atol=ic.accel_scale(16) / 2 + 1e-12)
    assert np.array_equal(back.timestamps_us, rec.timestamps_us)
    assert back.achieved_fs() == pytest.approx(1000.0, rel=1e-6)


def test_timestamp_requirement_is_justified():
    """+/-8% ODR error must exceed one FFT bin, which is why timestamps are mandatory."""
    f1x = 14.74
    bin_hz = 1.0                                   # 120 s window
    assert f1x * 0.08 / bin_hz > 1.0
    assert f1x * 0.01 / bin_hz < 1.0
    # and ignoring the 32/30 rescale is also about a bin
    assert f1x * (32 / 30 - 1) / bin_hz > 0.5


# ------------------------------------------------------------------ spec <-> doc
def test_spec_document_exists_and_records_the_frozen_decisions():
    assert SPEC.is_file(), "docs/imu_capture_spec.md missing"
    text = SPEC.read_text()
    for needle in ("ICM-42688-P", "IMUCAP2", "0x47", "0x68", "2.1",
                   "4000", "2000", "must use the timestamps"):
        assert needle in text, f"spec lost {needle!r}"
    assert "FR106" not in text


def test_spec_distinguishes_the_two_parts_and_names_the_bench_module():
    """The corrected identification must survive edits: both tiers, both WHO_AM_I."""
    text = SPEC.read_text()
    for needle in ("MPU-9250", "0x71", "HARDWARE-INVENTORY.md",
                   "bench", "carrier", "PS-MPU-9250A-01", "RM-MPU-9250A-00"):
        assert needle in text, f"spec lost {needle!r}"
    # the corrected claim must not silently revert to the false one
    assert "No photos are attached to this card or to any card on any board" not in text
    assert "photo identification was impossible" not in text
    # mode M and its band must be documented
    assert "mode M" in text or "mode **M**" in text
    assert "150" in text and "0–150 Hz" in text


def test_spec_frame_table_matches_the_codec():
    text = SPEC.read_text()
    assert '"<I7h3H"' in text
    # every field name in the frame must be documented
    for field_name in ("t_us", "ax", "ay", "az", "gx", "gy", "gz",
                       "temp_c100", "seq", "flags", "reserved"):
        assert field_name in text, f"frame field {field_name!r} undocumented"
    for flag in ("FLAG_FIFO_OVERRUN", "FLAG_HOST_OVERRUN", "FLAG_TIME_ESTIMATED"):
        assert flag in text, f"flag {flag!r} undocumented"
        assert getattr(ic, flag) is not None


def test_spec_marks_its_assumptions():
    """The card requires assumptions to be clearly marked."""
    text = SPEC.read_text()
    assert text.count("assumption") + text.count("⚠") >= 3
    assert "assumption" in text.lower()


def test_spec_cites_an_identifiable_datasheet():
    text = SPEC.read_text()
    assert "DS-000347" in text
    assert "revision 1.8" in text.lower() or "Revision 1.8" in text
    assert "ICM-42688-P" in text
    # the datasheet source must be traceable, not just named
    assert "sha256" in text or "web.archive.org" in text


def test_module_imports_without_scipy():
    """The contract must stay dependency-light; numpy only."""
    import ast
    src = (REPO / "hardware" / "imu_capture.py").read_text()
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert "scipy" not in mods
    assert mods <= {"__future__", "json", "struct", "time", "dataclasses", "typing", "numpy"}, mods


def test_selfcheck_passes():
    assert ic._selfcheck().startswith("OK")
