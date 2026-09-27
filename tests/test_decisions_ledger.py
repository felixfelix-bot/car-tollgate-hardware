"""Contract tests for the CTG-11 ratified decision record.

These tests exist so the next card (`t_64d238d1`, SKiDL source + layout) can
consume the hardware model *programmatically* instead of re-parsing Markdown by
hand — and so a silent edit to `DECISIONS.md` cannot drift away from the machine
readable ledger that the tooling reads.

Run:  python3 -m pytest tests/ -q      (from the repo root)
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DECISIONS_MD = REPO / "DECISIONS.md"
LEDGER = REPO / "decisions.json"

PLAN_MD5 = "41c53b94bc331519ffa9c39ccbc52fb3"

# The ratified values, spelled out here INDEPENDENTLY of the ledger and of the
# Markdown: a mutation in either has to disagree with this table to pass.
RATIFIED = {
    "D1": ["ESP32-S3-WROOM-1", "N16R8", "DevKitC-1"],
    "D2": ["SIM7600E", "EU", "breakout"],
    "D3": ["ATGM336H", "external"],
    "D4": ["ICM-42688-P", "6"],
    "D5": ["12 V"],
    "D6": ["80", "60"],
    "D7": [],
}


def load_ledger() -> dict:
    assert LEDGER.is_file(), f"machine-readable decision ledger missing: {LEDGER.name}"
    data = json.loads(LEDGER.read_text())
    assert isinstance(data, dict)
    return data


def test_ledger_has_every_decision():
    led = load_ledger()
    decisions = led.get("decisions") or {}
    assert sorted(decisions) == [f"D{i}" for i in range(1, 8)]


def test_ledger_values_are_the_ratified_ones():
    led = load_ledger()
    for key, needles in RATIFIED.items():
        blob = json.dumps(led["decisions"][key])
        for needle in needles:
            assert needle.lower() in blob.lower(), (
                f"{key} does not carry the ratified token {needle!r}: {blob}"
            )
    # Explicit rejections must be recorded, not implied.
    rejected = json.dumps(led.get("rejected", []))
    for needle in ("SIM800", "USB 5 V", "50 x 50", "NEO-M8N"):
        assert needle.lower() in rejected.lower(), f"rejection not recorded: {needle}"


def test_ledger_agrees_with_decisions_markdown():
    """Every ratified token in RATIFIED must also appear in DECISIONS.md.

    Guards the failure mode where the ledger is edited but the prose (the
    document humans read and the operator overrides from) is not.
    """
    md = DECISIONS_MD.read_text()
    for key, needles in RATIFIED.items():
        # the decision's own section must mention its tokens
        m = re.search(rf"^## {key}\b.*?(?=^## |\Z)", md, re.S | re.M)
        assert m, f"{key} section missing from DECISIONS.md"
        section = m.group(0)
        for needle in needles:
            assert needle.lower() in section.lower(), (
                f"DECISIONS.md {key} section lost the token {needle!r}"
            )


def test_ledger_records_region_and_its_evidence():
    led = load_ledger()
    assert led["region"]["value"] == "EU"
    ev = led["region"]["evidence"].lower()
    assert "esp_wifi_set_country_code" in ev or "country code" in ev
    assert "sim" in ev


def test_unimplemented_fab_is_not_claimed_done():
    """The repo must not claim a netlist/gerber/order it does not have."""
    for claim in ("netlist", "gerber", "order"):
        assert led_placeholder_words(claim)
    fab_files = [p for p in (REPO / "fab").rglob("*") if p.is_file()]
    assert fab_files == [] or not any(
        p.suffix.lower() in (".gbr", ".zip", ".drl") for p in fab_files
    ), "fab/ claims fabricated output"


def led_placeholder_words(claim: str) -> bool:
    led = load_ledger()
    assert led["status"][claim] == "not_started", (
        f"ledger claims {claim!r} but nothing has been produced"
    )
    return True


def test_provenance_pins_the_source_plan_md5():
    led = load_ledger()
    assert led["provenance"]["source_plan_md5"] == PLAN_MD5
    assert led["provenance"]["decision_card"] == "t_eaba2c97"
    assert led["provenance"]["unblocks"] == "t_64d238d1"


def strip_banner(text: str) -> str:
    """Remove the RATIFIED banner added on top of the card's attachment.

    The banner is the FIRST contiguous run of blockquote ('>') lines; the
    original document has its own later blockquote ("Read this first"), which
    must survive verbatim. The repo copy = H1 + blank + banner + blank + the
    attachment, so dropping banner + its trailing blank must reproduce it.
    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.startswith(">")), None)
    if start is None:
        return text
    end = start
    while end < len(lines) and lines[end].startswith(">"):
        end += 1
    if end < len(lines) and lines[end].strip() == "":
        end += 1  # the single blank line that follows the banner run
    return "".join(lines[:start] + lines[end:])


def test_plan_attachment_is_unmodified():
    """The design plan shipped here must be byte-identical to the card's copy.

    Only the RATIFIED banner may differ; the plan body itself must not drift.
    """
    local = REPO / "docs" / "PCB-DESIGN-PLAN.md"
    assert local.is_file()
    md5 = hashlib.md5(strip_banner(local.read_text()).encode()).hexdigest()
    assert md5 == PLAN_MD5, f"plan body drifted: {md5} != {PLAN_MD5}"


def test_readme_status_block_is_honest():
    readme = (REPO / "README.md").read_text()
    assert "human step" in readme.lower()
    assert "not started" in readme.lower()


def test_gatekeeper_script_passes():
    """The repo's own checker agrees with the ledger (single source of truth).

    Called in-process (not via subprocess) so coverage of tools/ is measured,
    AND once as a CLI to prove the shipped entry point works.
    """
    script = REPO / "tools" / "check_decisions.py"
    assert script.is_file(), "tools/check_decisions.py missing"

    spec = importlib.util.spec_from_file_location("check_decisions", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rc = mod.main()
    assert rc == 0, buf.getvalue()
    assert "OK" in buf.getvalue()

    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, cwd=REPO
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_gatekeeper_rejects_a_silent_drift(monkeypatch, tmp_path):
    """The checker must FAIL when the ledger disagrees with the Markdown.

    This is the mutation guard: if the ledger were ever edited alone, the
    checker has to catch it rather than print OK.
    """
    script = REPO / "tools" / "check_decisions.py"
    spec = importlib.util.spec_from_file_location("check_decisions_mut", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    tampered = json.loads(LEDGER.read_text())
    tampered["region"]["value"] = "TBD"
    bad = tmp_path / "decisions.json"
    bad.write_text(json.dumps(tampered))
    monkeypatch.setattr(mod, "REPO", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PCB-DESIGN-PLAN.md").write_text(
        (REPO / "docs" / "PCB-DESIGN-PLAN.md").read_text()
    )
    (tmp_path / "DECISIONS.md").write_text(DECISIONS_MD.read_text())
    (tmp_path / "fab").mkdir()
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        with pytest.raises(SystemExit) as exc:
            mod.main()
    assert exc.value.code == 1
    assert "FAIL" in buf.getvalue()
