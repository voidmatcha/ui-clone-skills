"""alignment-parity-check.sh — inner-content horizontal alignment gate.

Loop-9 regression class: footer section rects were IDENTICAL ref-vs-impl
(full-bleed left=0) while the inner content column was off-center by
+64px@1280 / -64px@1600 / -204px@1920. Section-box AE crops anchored
self-relative never see an x-axis offset, so this gate compares:

  (a) section-box center offset ref-vs-impl:
      fail when |refCenterOffset - implCenterOffset| > max(16px, 1.25% vpW)
  (b) contentBox gap asymmetry ref-vs-impl (ref-relative, never absolute):
      fail when |(implLeftGap-implRightGap) - (refLeftGap-refRightGap)|/2
                > max(12px, 1% refSectionWidth)

Frozen-ref artifacts lacking contentBox on content-bearing sections must
yield status=warn (unmeasurable) with a "ref recapture needed" remediation —
never a silent pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "alignment-parity-check.sh"


def test_alignment_parity_shell_wrapper_avoids_python_heredoc() -> None:
    """Keep the large Python program out of Bash 5.1+ pipe-backed heredocs."""
    shell = SCRIPT.read_text(encoding="utf-8")
    helper = SCRIPT.with_name("alignment_parity_check.py")

    assert helper.is_file()
    assert "<<" not in shell
    assert "python3 -" not in shell
    assert (
        'python3 "$SCRIPT_DIR/alignment_parity_check.py" "$REF_DIR" "$OUT"'
        in shell
    )


def test_alignment_parity_helper_imports_under_macos_system_python() -> None:
    """The helper must not eagerly evaluate Python 3.10-only annotations."""
    host_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else shutil.which("python3")
    if not host_python:
        import pytest

        pytest.skip("python3 not available")

    proc = subprocess.run(
        [
            host_python,
            "-c",
            (
                "import importlib.util;"
                f"spec=importlib.util.spec_from_file_location('ap', {str(SCRIPT.with_name('alignment_parity_check.py'))!r});"
                "mod=importlib.util.module_from_spec(spec);"
                "spec.loader.exec_module(mod)"
            ),
            "/tmp/ref",
            "/tmp/out.json",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert "unsupported operand type(s) for |" not in proc.stderr


def test_alignment_parity_completes_on_current_bash_without_compat(
    tmp_path: Path,
) -> None:
    """The wrapper must complete with the current default Bash and no compat state."""
    ref = tmp_path / "ref"
    ref.mkdir()
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(SCRIPT), str(ref)],
        capture_output=True,
        env=env,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "alignment-parity.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "skip"


def _run(ref: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        capture_output=True, text=True, timeout=120,
    )


def _row(
    name: str,
    *,
    left: int = 0,
    width: int = 1280,
    client_width: int = 1280,
    content_left: int | None = None,
    content_width: int | None = None,
    text: str = "some visible words",
    child_count: int = 3,
) -> dict:
    row: dict = {
        "index": 0,
        "tag": "section",
        "id": None,
        "className": name,
        "fingerprint": text,
        "textWords": text,
        "rect": {"top": 100, "left": left, "width": width, "height": 600},
        "childCount": child_count,
        "clientWidth": client_width,
        # the live enumerator always emits the field (possibly empty);
        # absence means a stripped/frozen artifact
        "contentGroups": [],
    }
    if content_left is not None and content_width is not None:
        row["contentBox"] = {
            "left": content_left,
            "width": content_width,
            "boxCount": child_count,
        }
        row["leftGap"] = content_left - left
        row["rightGap"] = (left + width) - (content_left + content_width)
    return row


def _write_fixture(ref_dir: Path, viewport: str, matches: list[dict]) -> None:
    d = ref_dir / "sections" / "viewports" / viewport / "sections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")


def _match(name: str, ref: dict | None, impl: dict | None) -> dict:
    return {"name": name, "score": 1.0, "ref": ref, "impl": impl, "pairing": "text-content"}


def _art(ref_dir: Path) -> dict:
    data = json.loads((ref_dir / "alignment-parity.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


# ── pass tier ──────────────────────────────────────────────────────────


def test_identical_centered_sections_pass(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    row = _row("hero", content_left=340, content_width=600)  # gaps 340/340
    _write_fixture(ref_dir, "1280x800", [_match("hero", row, dict(row))])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


def test_ref_asymmetric_impl_matching_passes_relative(tmp_path: Path) -> None:
    """Ref-asymmetric sections compare ref-relative, never absolute: an impl
    reproducing the same asymmetry must pass."""
    ref_dir = tmp_path / "ref"
    # ref gaps 100/500 (asym -400); impl reproduces the same gaps.
    row = _row("sidebar", content_left=100, content_width=680)
    _write_fixture(ref_dir, "1280x800", [_match("sidebar", row, dict(row))])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


# ── fail tier ──────────────────────────────────────────────────────────


def test_contentbox_asymmetry_fails(tmp_path: Path) -> None:
    """Loop-9 footer class: section rects identical, inner content shifted
    +64px (gaps 404/276 vs ref 340/340)."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer", content_left=340, content_width=600)
    impl_row = _row("footer", content_left=404, content_width=600)
    _write_fixture(ref_dir, "1280x800", [_match("footer", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "contentbox-asym" for r in fails)
    asym_row = next(r for r in fails if r["check"] == "contentbox-asym")
    # implAsym=+128, refAsym=0 → delta 64 > max(12, 1% of 1280 = 12.8)
    assert abs(asym_row["deltaPx"] - 64) <= 1


def test_section_center_offset_fails(tmp_path: Path) -> None:
    """Section box itself off-center: ref centered (left=140,w=1000 on 1280),
    impl shifted to left=240."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("cards", left=140, width=1000)
    impl_row = _row("cards", left=240, width=1000)
    _write_fixture(ref_dir, "1280x800", [_match("cards", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "section-center" for r in fails)


def test_small_offset_within_tolerance_passes(tmp_path: Path) -> None:
    """16px tolerance at 1280 (max(16, 1.25% of 1280 = 16)): a 10px shift
    is allowed."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("cards", left=140, width=1000, content_left=340, content_width=600)
    impl_row = _row("cards", left=150, width=1000, content_left=350, content_width=600)
    _write_fixture(ref_dir, "1280x800", [_match("cards", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


def test_closed_horizontal_drawer_is_not_alignment_measurable(tmp_path: Path) -> None:
    """A drawer parked fully right of both viewports is invisible. Responsive
    drawer-width differences must not become inner-alignment failures."""
    ref_dir = tmp_path / "ref"
    ref_row = _row(
        "mo-nav", left=1934, width=626, content_left=1964, content_width=566
    )
    impl_row = _row(
        "mo-nav", left=1934, width=626, content_left=1964, content_width=488
    )
    _write_fixture(ref_dir, "1280x800", [_match("mo-nav", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "skip"
    assert not art["rows"]


def test_one_sided_horizontal_escape_still_fails(tmp_path: Path) -> None:
    """The exemption is symmetric: an on-canvas ref disappearing off-canvas in
    only the implementation remains a visible regression."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("drawer", left=0, width=640)
    impl_row = _row("drawer", left=1280, width=640)
    _write_fixture(ref_dir, "1280x800", [_match("drawer", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert any(
        row["check"] == "section-center" and row["status"] == "fail"
        for row in art["rows"]
    )


def test_fail_reported_per_viewport(tmp_path: Path) -> None:
    """Multi-viewport fan-out: a defect at 1920 only is still a fail, and the
    row names its viewport."""
    ref_dir = tmp_path / "ref"
    ok = _row("footer", content_left=340, content_width=600)
    _write_fixture(ref_dir, "1280x800", [_match("footer", ok, dict(ok))])
    ref19 = _row(
        "footer", width=1920, client_width=1920,
        content_left=660, content_width=600,
    )
    impl19 = _row(
        "footer", width=1920, client_width=1920,
        content_left=456, content_width=600,  # -204px shift
    )
    _write_fixture(ref_dir, "1920x1080", [_match("footer", ref19, impl19)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert fails and all(r["viewport"] == "1920x1080" for r in fails)


def test_content_group_asymmetry_fails(tmp_path: Path) -> None:
    """Loop-9 eatReal class: whole-section contentBox union is diluted by a
    full-width centered h2, but the carousel cards group inside its container
    is off-center (+64px@1280). contentGroups rows must catch it."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    # container 128..1152; ref children union centered (412..868 → gaps
    # 284/284); impl union shifted (412..997 → gaps 284/155, asym +64.5).
    ref_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 412, "unionWidth": 456, "childCount": 3},
    ]
    impl_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 412, "unionWidth": 585, "childCount": 3},
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer-2", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-asym" for r in fails), art["rows"]
    grp = next(r for r in fails if r["check"] == "group-asym")
    assert abs(grp["deltaPx"] - 64.5) <= 1


def test_content_group_pairing_by_name_and_index(tmp_path: Path) -> None:
    """Groups pair by normalized name + occurrence index first; a ref group
    with NO impl counterpart left over after positional fallback is a fail
    (review-1 MAJOR 1 — removal must not exempt a group from verification)."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    ref_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 412, "unionWidth": 456, "childCount": 3},
        {"name": "ref_only", "containerLeft": 0, "containerWidth": 1280,
         "unionLeft": 0, "unionWidth": 100, "childCount": 2},
    ]
    impl_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 412, "unionWidth": 456, "childCount": 3},
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer-2", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    # name-paired dga_cards compares clean; the unpaired ref_only fails
    assert any(r["check"] == "group-missing" and r["group"] == "ref_only[0]" for r in fails)
    assert not any(r["check"] == "group-asym" and r["status"] == "fail" for r in art["rows"])


def test_content_group_ref_asymmetric_relative_pass(tmp_path: Path) -> None:
    """A ref-asymmetric group reproduced exactly must pass (ref-relative)."""
    ref_dir = tmp_path / "ref"
    row = _row("strip", content_left=128, content_width=1024)
    row["contentGroups"] = [
        {"name": "ticker", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 150, "unionWidth": 400, "childCount": 4},
    ]
    _write_fixture(ref_dir, "1280x800", [_match("strip", row, dict(row))])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


def test_group_rename_bypass_closed(tmp_path: Path) -> None:
    """Review-1 MAJOR 1: an impl that RENAMES the misaligned group must not
    skip the group check — unpaired groups fall back to positional pairing."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    ref_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 348, "unionWidth": 585, "childCount": 3},
    ]
    impl_row["contentGroups"] = [
        {"name": "totally_renamed", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 412, "unionWidth": 585, "childCount": 3},  # +64 off-center
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer-2", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-asym" for r in fails), art["rows"]


def test_group_removal_bypass_closed(tmp_path: Path) -> None:
    """Review-1 MAJOR 1: an impl that REMOVES the misaligned group container
    leaves the ref group unverified — that is a fail, not a silent skip."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    ref_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 348, "unionWidth": 585, "childCount": 3},
    ]
    impl_row["contentGroups"] = []
    _write_fixture(ref_dir, "1280x800", [_match("footer-2", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-missing" for r in fails), art["rows"]


def test_generic_tag_leftover_group_warns_not_fails(tmp_path: Path) -> None:
    """batch-13 ITEM 3: a ref-leftover group named after a BARE HTML TAG (a
    layout/carousel container whose count varies across reference loads — the
    eatReal carousel enumerates [content, cards, div] on one load and
    [content, cards] on another) is a non-blocking WARN, not a fail. The
    className-NAMED groups still pair-and-verify, and a NAMED leftover still
    FAILs (test_group_removal_bypass_closed) — only bare-tag layout noise is
    exempted, so this is not a group-strip bypass."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    ref_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 348, "unionWidth": 585, "childCount": 3},
        {"name": "div", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 348, "unionWidth": 585, "childCount": 1},
    ]
    impl_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 348, "unionWidth": 585, "childCount": 3},
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer-2", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert not fails, art["rows"]
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any("div" in u.get("reason", "") for u in art.get("unmeasured", [])), art.get("unmeasured")


def test_dynamic_region_groups_exempt_from_alignment(tmp_path: Path) -> None:
    """batch-13 ITEM 3: a contentGroup inside a DECLARED dynamic:true region
    (timer carousel / scroll-scrub) is exempt from alignment — its inner
    position/count varies across reference loads (the eatReal carousel cards
    translate and re-order every cycle), so a childshift/group-missing on it is a
    non-blocking warn, not a fail. Static groups elsewhere are still checked, and
    a region NOT declared dynamic is NOT exempt (see the bypass-closed tests)."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    # the carousel cards group is +103px shifted between loads — would FAIL
    # group-childshift if not exempted
    ref_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 348, "unionWidth": 585, "childCount": 3,
         "childCenters": [400, 640, 880]},
    ]
    impl_row["contentGroups"] = [
        {"name": "dga_cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 451, "unionWidth": 585, "childCount": 3,
         "childCenters": [503, 743, 983]},
    ]
    _write_fixture(ref_dir, "default", [_match("footer-2", ref_row, impl_row)])
    (ref_dir / "transition-spec.json").write_text(
        json.dumps({"transitions": [
            {"id": "eatreal-food-carousel", "dynamic": True,
             "target": ".dga_cards__vXMHq, .dga_h2_food___9BYt"},
        ]}),
        encoding="utf-8",
    )
    proc = _run(ref_dir)
    art = _art(ref_dir)
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert not fails, art["rows"]
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _grp(
    name: str, cl: int, cw: int, ul: int, uw: int, child_count: int = 3,
    child_centers: list[int] | None = None,
) -> dict:
    g = {
        "name": name, "containerLeft": cl, "containerWidth": cw,
        "unionLeft": ul, "unionWidth": uw, "childCount": child_count,
    }
    if child_centers is not None:
        g["childCenters"] = child_centers
    return g


def test_centered_extra_impl_group_does_not_fail(tmp_path: Path) -> None:
    """A CENTERED impl-only group (a genuinely benign extra wrapper) is not an
    alignment defect — only OFF-CENTER impl-leftovers are flagged (batch-6
    ITEM 3 replaces the old blanket 'impl-only groups never fail' assumption)."""
    ref_dir = tmp_path / "ref"
    row = _row("hero", content_left=340, content_width=600)
    row["contentGroups"] = [_grp("list", 340, 600, 360, 560, child_count=4)]
    impl = dict(row)
    impl["contentGroups"] = [
        _grp("list", 340, 600, 360, 560, child_count=4),
        # extra wrapper centered in its container (lg 490, rg 490, asym 0)
        _grp("extra", 0, 1280, 490, 300, child_count=2),
    ]
    _write_fixture(ref_dir, "1280x800", [_match("hero", row, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


# ── tools batch-6 ITEM 3: off-center impl-leftover groups must fail ──────
# The decisive hole: a decoy impl group carrying the ref's class token (or a
# duplicate same-name group) consumes the ref pairing, leaving the REAL
# off-center group as a silently-dropped impl-leftover. Fixtures recreate
# /tmp/adv-alignment-parity Attacks 4b and 2 at the artifact level.


def test_attack4b_duplicate_same_name_offcenter_leftover_fails(tmp_path: Path) -> None:
    # ref has one centered dga_cards; impl has dga_cards[0]=centered decoy (DOM
    # order first) and dga_cards[1]=real +64.5px off-center. Name+positional
    # pairing matches ref against the decoy; the real off-center group is an
    # impl-leftover and was never evaluated.
    ref_dir = tmp_path / "ref"
    ref = _row("footer", content_left=128, content_width=1024)
    impl = _row("footer", content_left=128, content_width=1024)
    ref["contentGroups"] = [_grp("dga_cards", 128, 1024, 348, 585)]
    impl["contentGroups"] = [
        _grp("dga_cards", 128, 1024, 348, 585),   # decoy, centered
        _grp("dga_cards", 128, 1024, 412, 585),   # real, +64.5px off-center
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-leftover" for r in fails), art["rows"]


def test_attack2_offcenter_leftover_under_new_name_fails(tmp_path: Path) -> None:
    # A full-width wrapper carrying the ref's token absorbs the only pairing;
    # the real off-center cards live under a NEW class name as an impl-leftover.
    ref_dir = tmp_path / "ref"
    ref = _row("footer", content_left=128, content_width=1024)
    impl = _row("footer", content_left=128, content_width=1024)
    ref["contentGroups"] = [_grp("col", 128, 1024, 200, 880)]
    impl["contentGroups"] = [
        _grp("col", 128, 1024, 200, 880),          # paired, centered
        _grp("newcards", 128, 1024, 412, 585),     # real off-center, new name
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-leftover" for r in fails), art["rows"]


# ── unmeasurable tier (frozen refs without contentBox) ─────────────────


def test_missing_ref_contentbox_is_warn_not_pass(tmp_path: Path) -> None:
    """Frozen-ref artifacts lacking contentBox on a content-bearing section
    must surface as unmeasurable warn with a recapture remediation — never a
    silent pass."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer")  # no contentBox
    impl_row = _row("footer", content_left=404, content_width=600)
    _write_fixture(ref_dir, "1280x800", [_match("footer", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "warn"
    assert art["unmeasured"], "content-bearing section without contentBox must be listed"
    assert "recapture" in json.dumps(art).lower()


def test_empty_section_without_contentbox_not_unmeasured(tmp_path: Path) -> None:
    """A section with no text and no children has nothing to center — missing
    contentBox there is not unmeasurable debt."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("spacer", text="", child_count=0)
    impl_row = _row("spacer", text="", child_count=0)
    _write_fixture(ref_dir, "1280x800", [_match("spacer", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"
    assert not art.get("unmeasured")


def test_fail_wins_over_unmeasured(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    frozen = _row("hero")  # unmeasurable
    impl_hero = _row("hero", content_left=340, content_width=600)
    ref_f = _row("footer", content_left=340, content_width=600)
    impl_f = _row("footer", content_left=404, content_width=600)
    _write_fixture(
        ref_dir, "1280x800",
        [_match("hero", frozen, impl_hero), _match("footer", ref_f, impl_f)],
    )
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1
    assert art["status"] == "fail"
    assert art["unmeasured"]


# ── skip / setup tiers ─────────────────────────────────────────────────


def test_no_section_artifacts_skips(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0
    assert art["status"] == "skip"


def test_top_level_matches_fallback(tmp_path: Path) -> None:
    """Single-viewport runs only have sections/matches.json — the check must
    still evaluate them."""
    ref_dir = tmp_path / "ref"
    d = ref_dir / "sections"
    d.mkdir(parents=True)
    ref_row = _row("footer", content_left=340, content_width=600)
    impl_row = _row("footer", content_left=404, content_width=600)
    (d / "matches.json").write_text(
        json.dumps([_match("footer", ref_row, impl_row)]), encoding="utf-8",
    )
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1
    assert art["status"] == "fail"


def test_unmatched_rows_ignored(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    row = _row("hero", content_left=340, content_width=600)
    _write_fixture(
        ref_dir, "1280x800",
        [_match("hero", row, dict(row)), _match("orphan", _row("orphan"), None)],
    )
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


def test_missing_ref_dir_setup_error(tmp_path: Path) -> None:
    proc = _run(tmp_path / "nope")
    assert proc.returncode == 2


def test_loop9_footer_carousel_regression() -> None:
    """Pinned regression: loop-9 eatReal footer carousel (user-verified
    defect — Footer.tsx bakes left:426px/right:426px + matrix ±192px,
    1440-only constants). dga_cards group geometry below was MEASURED from
    the live loop-9 build (localhost:5179) with the contentBox-enabled
    enumerator on 2026-06-12; ref groups are the centered ground truth the
    user confirmed visually on realfood.gov. Expected center offsets:
    +64px@1280 / -64px@1600 / -204px@1920."""
    import tempfile

    measured = {
        # viewport: (containerLeft, containerWidth, unionLeft, unionWidth)
        "1280x800": (128, 1024, 412, 585),
        "1600x900": (160, 1280, 444, 585),
        "1920x1080": (180, 1560, 464, 585),
    }
    expected_delta = {"1280x800": 64.5, "1600x900": 63.5, "1920x1080": 203.5}
    with tempfile.TemporaryDirectory() as td:
        ref_dir = Path(td) / "ref"
        for vp, (c_l, c_w, u_l, u_w) in measured.items():
            w = int(vp.split("x")[0])
            ref_row = _row("footer-2", width=w, client_width=w,
                           content_left=c_l, content_width=c_w)
            impl_row = _row("footer-2", width=w, client_width=w,
                            content_left=c_l, content_width=c_w)
            centered_left = round(c_l + (c_w - u_w) / 2)
            ref_row["contentGroups"] = [
                {"name": "dga_cards", "containerLeft": c_l, "containerWidth": c_w,
                 "unionLeft": centered_left, "unionWidth": u_w, "childCount": 3},
            ]
            impl_row["contentGroups"] = [
                {"name": "dga_cards", "containerLeft": c_l, "containerWidth": c_w,
                 "unionLeft": u_l, "unionWidth": u_w, "childCount": 3},
            ]
            _write_fixture(ref_dir, vp, [_match("footer-2", ref_row, impl_row)])
        proc = _run(ref_dir)
        art = _art(ref_dir)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert art["status"] == "fail"
        fails = {
            r["viewport"]: r for r in art["rows"]
            if r["status"] == "fail" and r["check"] == "group-asym"
        }
        assert set(fails) == set(measured), fails
        for vp, want in expected_delta.items():
            assert abs(fails[vp]["deltaPx"] - want) <= 1.0, (vp, fails[vp])


def test_stripped_contentgroups_on_content_bearing_ref_is_unmeasured(tmp_path: Path) -> None:
    """Review-2 finding 1 (bypass attempt): a content-bearing ref row whose
    contentGroups field is stripped/non-list must surface as unmeasured with
    recapture remediation — never a silent skip of the group prong."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    # contentBox present (prong b measured) but groups stripped
    ref_row.pop("contentGroups", None)
    impl_row["contentGroups"] = [
        {"name": "cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 412, "unionWidth": 585, "childCount": 3},
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer-2", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "warn", art["status"]
    assert any(
        "contentGroups" in u["reason"] for u in art["unmeasured"]
    ), art["unmeasured"]
    assert "recapture" in json.dumps(art).lower()


def test_impl_side_groups_stripped_while_ref_has_them_fails(tmp_path: Path) -> None:
    """Review-2 finding 1 (bypass attempt): impl artifact without a
    contentGroups list while the ref declares groups is evidence absence on
    the impl side — fail, not skip."""
    ref_dir = tmp_path / "ref"
    ref_row = _row("footer-2", content_left=128, content_width=1024)
    impl_row = _row("footer-2", content_left=128, content_width=1024)
    ref_row["contentGroups"] = [
        {"name": "cards", "containerLeft": 128, "containerWidth": 1024,
         "unionLeft": 348, "unionWidth": 585, "childCount": 3},
    ]
    impl_row.pop("contentGroups", None)
    _write_fixture(ref_dir, "1280x800", [_match("footer-2", ref_row, impl_row)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-missing" for r in fails), art["rows"]


def test_loop11_masked_footer_cards_false_pass_is_closed() -> None:
    """Item 6-EXT pin: loop-11 alignment-parity.json reported status=pass while
    Footer.tsx baked the eatReal cards off-center (left:426px / ±192 transform).

    Root cause: the dynamic mask (`visibility:hidden`) is applied BEFORE
    enumerate-sections.js, whose contentGroups builder drops visibility:hidden
    nodes — so the masked `.dga_cards__vXMHq` group was excluded from the
    enumeration alignment-parity consumes. The frozen artifact therefore shows
    the footer with NO card group and 0 fails.

    The fix exempts mask-rooted elements from the visibility filter (geometry is
    preserved under visibility:hidden) so the cards are measured again. This test
    pins BOTH the historical false-pass shape AND the fix wiring, so the exact
    exclusion cannot silently return.
    """
    art_path = ROOT / "tmp" / "ref" / "realfood-e2e-11" / "alignment-parity.json"
    if art_path.is_file():
        art = json.loads(art_path.read_text(encoding="utf-8"))
        footer_rows = [r for r in art.get("rows", []) if "footer" in str(r.get("section", ""))]
        assert footer_rows, "loop-11 artifact has no footer rows to pin"
        # The false-pass: every footer row passed and NO cards group was measured.
        assert all(r["status"] == "ok" for r in footer_rows)
        assert not any(
            "card" in str(r.get("group", "")).lower() for r in footer_rows
        ), "loop-11 artifact already had a card group — fixture stale, re-pin"

    # The fix must remain wired: enumerate-sections.js measures mask-hidden
    # geometry, and section-compare.sh hands it the masked selector list.
    enum_js = (ROOT / "skills" / "visual-debug" / "scripts" / "lib"
               / "enumerate-sections.js").read_text(encoding="utf-8")
    assert "__UI_RE_DYNAMIC_SELECTORS__" in enum_js
    assert "isMaskHidden" in enum_js
    sc_sh = (ROOT / "skills" / "visual-debug" / "scripts"
             / "section-compare.sh").read_text(encoding="utf-8")
    assert "window.__UI_RE_DYNAMIC_SELECTORS__" in sc_sh


def test_lone_masked_group_transform_decenter_fails(tmp_path: Path) -> None:
    # Attack B (deferred from ITEM 1): a lone masked heading with the correct
    # text-align but a parent transform that de-centers it forms no multi-child
    # group, so it escaped the static gate and group asymmetry. The enumerator
    # now emits it as a "masked:<name>" group anchored to the section, so
    # alignment-parity measures its placement ref-relative.
    ref_dir = tmp_path / "ref"
    ref = _row("eatReal", content_left=128, content_width=1024)
    impl = _row("eatReal", content_left=128, content_width=1024)
    ref["contentGroups"] = [_grp("masked:eatReal_h2", 0, 1280, 540, 200, child_count=1)]
    impl["contentGroups"] = [_grp("masked:eatReal_h2", 0, 1280, 636, 200, child_count=1)]
    _write_fixture(ref_dir, "1280x800", [_match("eatReal", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-asym" for r in fails), art["rows"]


def test_enumerator_paint_filter_and_lone_masked_wired() -> None:
    # The enumerator must reject zero-paint spacer leaves (alignment-parity
    # Attack 4 decoy) and emit lone masked content elements as section-anchored
    # groups (Attack B). Pin the wiring so it cannot silently regress.
    enum_js = (ROOT / "skills" / "visual-debug" / "scripts" / "lib"
               / "enumerate-sections.js").read_text(encoding="utf-8")
    assert "paintsNothing" in enum_js
    assert "isVisuallyClipped" in enum_js
    assert "masked:" in enum_js


# ── tools batch-7 ITEM 3: per-child offset (net-symmetric union) ──────────
# A PAIRED, ref-expected, same-named group whose union envelope is symmetric
# because a painting decoy sibling sits off-centre the opposite way, while every
# content child is systematically off-centre. group-asym reads delta 0; the new
# per-child median-offset prong catches it. Recreates /tmp/adv2-align N1.


def test_net_symmetric_union_per_child_shift_fails(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref = _row("footer-eatreal", content_left=128, content_width=1024)
    impl = _row("footer-eatreal", content_left=128, content_width=1024)
    # identical symmetric union on both sides => group-asym delta 0 (ok)
    ref["contentGroups"] = [
        _grp("dga_cards", 128, 1024, 348, 585, child_centers=[490, 640, 790]),
    ]
    impl["contentGroups"] = [
        # union symmetric (348..933) but the real cards are +128 off-centre while
        # a decoy sibling balances the envelope to the far left.
        _grp("dga_cards", 128, 1024, 348, 585, child_centers=[284, 768, 768, 768]),
    ]
    _write_fixture(ref_dir, "1280x800", [_match("footer-eatreal", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-childshift" for r in fails), art["rows"]
    # the union prong itself did NOT fire — proving the new prong is load-bearing
    assert not any(r["check"] == "group-asym" and r["status"] == "fail" for r in art["rows"]), art["rows"]


def test_symmetric_children_per_child_prong_passes(tmp_path: Path) -> None:
    # control: ref-self / honest clone — identical child centres => no childshift.
    ref_dir = tmp_path / "ref"
    ref = _row("footer-eatreal", content_left=128, content_width=1024)
    impl = _row("footer-eatreal", content_left=128, content_width=1024)
    ref["contentGroups"] = [_grp("dga_cards", 128, 1024, 348, 585, child_centers=[490, 640, 790])]
    impl["contentGroups"] = [_grp("dga_cards", 128, 1024, 348, 585, child_centers=[490, 640, 790])]
    _write_fixture(ref_dir, "1280x800", [_match("footer-eatreal", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass", art


def test_symmetric_dispersion_per_child_shift_fails(tmp_path: Path) -> None:
    # batch-8 ITEM 7: 3+ children whose individual offsets cancel at the MEDIAN
    # (and the union envelope) while each is grossly off-centre. The median-only
    # prong read delta 0 and passed; the sorted per-child offset DISTRIBUTION
    # diverges. Container centre = 0 + 1440/2 = 720.
    ref_dir = tmp_path / "ref"
    ref = _row("cards-section", content_left=520, content_width=400)
    impl = _row("cards-section", content_left=320, content_width=800)
    # ref children clustered near centre: offsets [-100, 0, +100] -> median 0
    ref["contentGroups"] = [_grp("cards", 0, 1440, 520, 400, child_centers=[620, 720, 820])]
    # impl children scattered: offsets [-400, 0, +400] -> median ALSO 0, union
    # still symmetric, yet two of three cards are 400px off-centre.
    impl["contentGroups"] = [_grp("cards", 0, 1440, 320, 800, child_centers=[320, 720, 1120])]
    _write_fixture(ref_dir, "1440x900", [_match("cards-section", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-childshift" for r in fails), art["rows"]


def test_symmetric_dispersion_control_passes(tmp_path: Path) -> None:
    # control: honest clone with identical child centres — no childshift fail.
    ref_dir = tmp_path / "ref"
    ref = _row("cards-section", content_left=520, content_width=400)
    impl = _row("cards-section", content_left=520, content_width=400)
    ref["contentGroups"] = [_grp("cards", 0, 1440, 520, 400, child_centers=[620, 720, 820])]
    impl["contentGroups"] = [_grp("cards", 0, 1440, 520, 400, child_centers=[620, 720, 820])]
    _write_fixture(ref_dir, "1440x900", [_match("cards-section", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass", art


def test_enumerator_emits_child_centers() -> None:
    enum_js = (ROOT / "skills" / "visual-debug" / "scripts" / "lib"
               / "enumerate-sections.js").read_text(encoding="utf-8")
    assert "childCenters" in enum_js


def test_enumerator_excludes_boxes_outside_the_section_crop() -> None:
    enum_js = (ROOT / "skills" / "visual-debug" / "scripts" / "lib"
               / "enumerate-sections.js").read_text(encoding="utf-8")
    assert "r.bottom <= rect.top || r.top >= rect.bottom" in enum_js


# ── tools batch-12 ITEM 3: overflow scroll-track exemption ────────────────
# The realfood foods/pyramid grid is a horizontally-OVERFLOWING scroll-track
# (container ~338px, child union ~1400px, clipped by overflow-x:clip). Its
# off-screen overflow extent / start offset is not a visible centering property
# and legitimately differs between two captures (or a faithful flattened-grid
# impl), so the per-group centering prongs must EXEMPT overflow tracks instead of
# scoring the off-screen shift as a 98-158px alignment defect. The visible box
# stays measured by section-center + contentbox-asym; the exemption is gated
# strictly on union > container + tolerance so no centered group (loop-9 class)
# is ever exempted.


def test_overflow_carousel_group_self_passes(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref = _row("foods", left=0, width=375, client_width=375,
               content_left=24, content_width=327)
    impl = _row("foods", left=0, width=375, client_width=375,
                content_left=24, content_width=327)
    # container 338; ref strip spans ~-217..1151 (uw 1368), impl strip captured
    # at a different start ~-148..1309 (uw 1457) — the foods overflow case.
    ref["contentGroups"] = [
        _grp("dga_foods_inner", 24, 338, -217, 1368,
             child_count=5, child_centers=[-150, 100, 350, 600, 900]),
    ]
    impl["contentGroups"] = [
        _grp("dga_foods_inner", 24, 338, -148, 1457,
             child_count=5, child_centers=[-80, 180, 440, 700, 1010]),
    ]
    _write_fixture(ref_dir, "375x812", [_match("foods", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass", art
    # the exemption is RECORDED (not silently dropped) and no centering prong fired
    assert any(r["check"] == "group-overflow" for r in art["rows"]), art["rows"]
    assert not any(
        r["check"] in ("group-asym", "group-childshift") and r["status"] == "fail"
        for r in art["rows"]
    ), art["rows"]


def test_overflow_impl_leftover_group_exempt(tmp_path: Path) -> None:
    # The pyramid foods groups land as impl-leftovers in the corpus; an
    # off-center impl-leftover that is itself an OVERFLOW track is exempt (its
    # centering is undefined), not a group-leftover fail.
    ref_dir = tmp_path / "ref"
    ref = _row("foods", left=0, width=375, client_width=375,
               content_left=24, content_width=327)
    impl = _row("foods", left=0, width=375, client_width=375,
                content_left=24, content_width=327)
    ref["contentGroups"] = [_grp("paired", 24, 338, 30, 320)]  # fits, pairs clean
    impl["contentGroups"] = [
        _grp("paired", 24, 338, 30, 320),
        _grp("dga_pyramid", 24, 338, -200, 1400),  # overflow impl-leftover
    ]
    _write_fixture(ref_dir, "375x812", [_match("foods", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass", art
    assert any(
        r["check"] == "group-overflow" and r["group"] == "dga_pyramid[0]"
        for r in art["rows"]
    ), art["rows"]


def test_near_full_width_offcenter_group_still_fails(tmp_path: Path) -> None:
    """Boundary guard: a wide group whose union still FITS its container (union
    <= container + tol) is NOT an overflow track — a real off-center defect there
    must still FAIL. The overflow exemption must not leak to fitting groups."""
    ref_dir = tmp_path / "ref"
    ref = _row("strip", content_left=0, content_width=1280)
    impl = _row("strip", content_left=0, content_width=1280)
    # container 1280; union 1000 FITS (1000 <= 1280); centered in ref, +120 in impl.
    ref["contentGroups"] = [_grp("cards", 0, 1280, 140, 1000)]
    impl["contentGroups"] = [_grp("cards", 0, 1280, 260, 1000)]
    _write_fixture(ref_dir, "1280x800", [_match("strip", ref, impl)])
    proc = _run(ref_dir)
    art = _art(ref_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    fails = [r for r in art["rows"] if r["status"] == "fail"]
    assert any(r["check"] == "group-asym" for r in fails), art["rows"]
    assert not any(r["check"] == "group-overflow" for r in art["rows"]), art["rows"]
