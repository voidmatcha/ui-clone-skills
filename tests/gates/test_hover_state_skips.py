"""Regression: hover-state-compare's documented overlay-gated skip derivation.

loop-e2e-5: ref-side recording reported 'Element not found' for lightbox /
mobile-nav hover targets — conditionally MOUNTED UI that exists only after
opening the owning overlay. transition-fires honors the documented
asset-substitution skips for those ids; hover-state-compare must consult the
SAME narrow evidence (exact spec-target selectors of skip-listed ids with a
conditionally-mounted reason) — never the broad substitution/origin-lock set
(codex review: bypass risk), never wildcards.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"


def _run_derivation(tmp_path: Path, spec: dict, sub: dict) -> list[str]:
    code = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"<<'PY' \|\| true\n(.*?)\nPY\n", code, re.S)
    assert m, "skip-derivation heredoc not found in hover-state-compare.sh"
    (tmp_path / "transition-spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (tmp_path / "asset-substitution.json").write_text(json.dumps(sub), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", m.group(1),
         str(tmp_path / "transition-spec.json"),
         str(tmp_path / "asset-substitution.json")],
        capture_output=True, text=True, check=True,
    )
    return [line for line in proc.stdout.strip().splitlines() if line]


def _run_presence_filter(
    tmp_path: Path,
    ref_presence: dict[str, object],
    impl_presence: dict[str, object],
) -> tuple[list[str], str]:
    code = SCRIPT.read_text(encoding="utf-8")
    marker = (
        'python3 - "$TARGETS_FILE" "$REF_PRESENT" "$IMPL_PRESENT" '
        '"$RESULT" "$VP_LABEL" > "$VP_TARGETS" <<\'PY\'\n'
    )
    start = code.index(marker) + len(marker)
    body = code[start : code.index("\nPY\n", start)]
    targets = tmp_path / "targets.tsv"
    result = tmp_path / "result.txt"
    targets.write_text("nav\thover\t.nav__link2\n", encoding="utf-8")
    result.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            body,
            str(targets),
            json.dumps(ref_presence),
            json.dumps(impl_presence),
            str(result),
            "desktop",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    kept = [line for line in proc.stdout.splitlines() if line]
    return kept, result.read_text(encoding="utf-8")


def test_overlay_gated_skip_selectors_derived_exactly(tmp_path: Path) -> None:
    spec = {"transitions": [
        {"id": "hover-lightbox-controls",
         "target": ".lightbox_close_button__hmMKH, .lightbox_nav_button__oC4Mw"},
        {"id": "hover-nav-buttons", "target": ".nav_dot_button__kZB4V"},
    ]}
    sub = {"skips": [{"id": "hover-lightbox-controls",
                      "reason": "conditionally MOUNTED UI: lightbox appears after click"}]}
    out = _run_derivation(tmp_path, spec, sub)
    assert ".lightbox_close_button__hmMKH" in out
    assert ".lightbox_nav_button__oC4Mw" in out
    assert ".nav_dot_button__kZB4V" not in out


def test_skip_without_mounted_reason_not_derived(tmp_path: Path) -> None:
    # a skip documented for an unrelated reason (paid lib) must NOT exempt
    # its selectors from hover verification
    spec = {"transitions": [{"id": "hover-x", "target": ".paid_widget__abc"}]}
    sub = {"skips": [{"id": "hover-x", "reason": "paid library substitution"}]}
    assert _run_derivation(tmp_path, spec, sub) == []


def test_wildcard_selectors_rejected(tmp_path: Path) -> None:
    spec = {"transitions": [{"id": "hover-y", "target": "[class*=button], .real_one__x"}]}
    sub = {"skips": [{"id": "hover-y", "reason": "conditionally mounted"}]}
    out = _run_derivation(tmp_path, spec, sub)
    assert "[class*=button]" not in out
    assert ".real_one__x" in out


def test_both_hidden_selector_is_delegated_to_fallback(tmp_path: Path) -> None:
    kept, result = _run_presence_filter(
        tmp_path,
        {".nav__link2": "hidden"},
        {".nav__link2": "hidden"},
    )

    assert kept == []
    assert "all selector matches hidden on BOTH" in result
    assert "delegated to per-entry fallback probe" in result


def test_both_absent_selector_keeps_existing_known_skip(tmp_path: Path) -> None:
    kept, result = _run_presence_filter(
        tmp_path,
        {".nav__link2": False},
        {".nav__link2": False},
    )

    assert kept == []
    assert "selector absent on BOTH" in result


def test_one_visible_side_keeps_selector_for_divergence_measurement(
    tmp_path: Path,
) -> None:
    kept, result = _run_presence_filter(
        tmp_path,
        {".nav__link2": "hidden"},
        {".nav__link2": "rendered"},
    )

    assert kept == ["nav\thover\t.nav__link2"]
    assert result == ""


def test_presence_probe_checks_all_rendered_matches() -> None:
    code = SCRIPT.read_text(encoding="utf-8")
    probe = code.split('PROBE_JS="', 1)[1].split('"\n  PROBE_SESSION=', 1)[0]

    assert "document.querySelectorAll" in probe
    assert "matches.some(rendered)" in probe
    assert "document.querySelector(s)" not in probe
