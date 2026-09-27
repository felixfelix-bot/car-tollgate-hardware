#!/usr/bin/env python3
"""check_decisions.py — single-source-of-truth check for the CTG-11 model.

Reads `decisions.json` (the machine-readable ledger the SKiDL source will read),
verifies it against `DECISIONS.md` (the document humans read and the operator
overrides from), and refuses to let the repo claim work it has not produced.

Exit 0 + "OK" on success; exit 1 with the first disagreement on failure.
Usage:  python3 tools/check_decisions.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "docs" / "PCB-DESIGN-PLAN.md"
PLAN_MD5 = "41c53b94bc331519ffa9c39ccbc52fb3"


def _strip_banner(text: str) -> str:
    """Drop the RATIFIED banner (the FIRST blockquote run + its trailing blank).

    Later blockquotes in the original document must survive verbatim.
    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.startswith(">")), None)
    if start is None:
        return text
    end = start
    while end < len(lines) and lines[end].startswith(">"):
        end += 1
    if end < len(lines) and lines[end].strip() == "":
        end += 1
    return "".join(lines[:start] + lines[end:])


def fail(msg: str) -> "None":
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    led_path = REPO / "decisions.json"
    if not led_path.is_file():
        fail("decisions.json missing")
    led = json.loads(led_path.read_text())

    # 1. every decision present, in order
    decisions = led.get("decisions") or {}
    if sorted(decisions) != [f"D{i}" for i in range(1, 8)]:
        fail(f"decisions set is {sorted(decisions)}, expected D1..D7")

    # 2. the Markdown must carry a section per decision
    md = (REPO / "DECISIONS.md").read_text()
    for key in decisions:
        if not re.search(rf"^## {key}\b", md, re.M):
            fail(f"DECISIONS.md has no section for {key}")

    # 3. region must be resolved with evidence, not "TBD"
    region = (led.get("region") or {}).get("value")
    if region != "EU":
        fail(f"region is {region!r}; expected EU")
    if "TBD" in json.dumps(led.get("region")):
        fail("region still carries an unresolved TBD")
    if "SIM7600E" not in json.dumps(decisions["D2"]):
        fail("D2 does not pin SIM7600E")
    if "SIM7600A" not in json.dumps(led.get("rejected", [])):
        fail("D2 rejection of the Americas variant is not recorded")

    # 4. the shipped plan body is byte-identical to the card's attachment
    #    (only the RATIFIED banner may sit on top; later blockquotes survive)
    if not PLAN.is_file():
        fail("docs/PCB-DESIGN-PLAN.md missing")
    body = _strip_banner(PLAN.read_text())
    got = hashlib.md5(body.encode()).hexdigest()
    if got != PLAN_MD5:
        fail(f"plan body md5 {got} != {PLAN_MD5}")

    # 5. no fabricated fab output
    status = led.get("status") or {}
    for step in ("netlist", "layout", "drc_erc", "gerber", "order"):
        if status.get(step) != "not_started":
            fail(f"status.{step} is {status.get(step)!r} but nothing was produced")
    fab = [p for p in (REPO / "fab").rglob("*") if p.is_file()]
    if any(p.suffix.lower() in (".gbr", ".zip", ".drl") for p in fab):
        fail("fab/ contains output while status says not_started")

    # 6. provenance pins the card that owns the decision
    prov = led.get("provenance") or {}
    if prov.get("decision_card") != "t_eaba2c97" or prov.get("unblocks") != "t_64d238d1":
        fail(f"provenance is {prov!r}")

    print("OK: decisions.json agrees with DECISIONS.md, plan md5 matches, "
          "no fabricated fab output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
