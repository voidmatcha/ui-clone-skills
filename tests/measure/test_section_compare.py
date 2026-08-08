from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from ui_clone import measure

from ._helpers import (
    _project_root,
)


def _is_bash_major_probe(cmd: list[str]) -> bool:
    return len(cmd) >= 3 and cmd[1:3] == ["-c", 'printf %s "${BASH_VERSION%%.*}"']


def test_section_compare_locks_exclude_dynamic_and_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    """`measure section-compare` MUST invoke bash with EXCLUDE_DYNAMIC=1
    and SECTION_THRESHOLD=2000, even when the parent shell sets them to
    permissive values. Locks down the d19e28d gaming pattern where the
    agent set SECTION_THRESHOLD=250000 to re-classify critical→minor.
    """
    captured_env: dict[str, str] = {}

    def fake_run(
        cmd: list[str],
        env: dict[str, str] | None = None,
        **kw: object,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        if _is_bash_major_probe(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="5")
        assert env is not None, f"script subprocess must pass locked env: {cmd}"
        captured_env.update(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir="/tmp/fake-ref",
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
    )
    with mock.patch.dict(os.environ, {
        "EXCLUDE_DYNAMIC": "0",         # caller's permissive default
        "SECTION_THRESHOLD": "250000",  # caller's gaming attempt
    }, clear=False), mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_section_compare(args)

    assert captured_env["EXCLUDE_DYNAMIC"] == "1", (
        f"EXCLUDE_DYNAMIC must be locked to 1, got: {captured_env.get('EXCLUDE_DYNAMIC')!r}"
    )
    assert captured_env["SECTION_THRESHOLD"] == "2000", (
        f"SECTION_THRESHOLD must be locked to 2000, got: {captured_env.get('SECTION_THRESHOLD')!r}"
    )
    # Status JSON on stdout
    out = capsys.readouterr().out.strip().splitlines()
    status = json.loads(out[-1])
    assert status["step"] == "section-compare"
    assert status["locked_env"] == {"EXCLUDE_DYNAMIC": "1", "SECTION_THRESHOLD": "2000"}



def test_all_runs_section_compare_first(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """The `all` subcommand orders section-compare BEFORE transition-compare —
    static fidelity is measured first so motion noise doesn't contaminate
    the structural verdict.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # Make transition-spec.json exist so transition-compare is included
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))

    call_order: list[str] = []

    def fake_run(
        cmd: list[str],
        env: dict[str, str] | None = None,
        **kw: object,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        if _is_bash_major_probe(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="5")
        assert env is not None, f"script subprocess must pass locked env: {cmd}"
        # Extract script name from the bash invocation
        for c in cmd:
            if "section-compare.sh" in c:
                call_order.append("section-compare")
            elif "transition-compare.sh" in c:
                call_order.append("transition-compare")
            elif "asset-utilization-check.sh" in c:
                call_order.append("asset-utilization")
            elif "bundle-impl-coverage-check.sh" in c:
                call_order.append("bundle-impl-coverage")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir=str(ref_dir),
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
        impl_src=None,
        impl_pkg=None,
    )
    with mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_all(args)

    assert call_order[0] == "section-compare", (
        f"section-compare must run FIRST in the canonical sequence: {call_order}"
    )
    assert "transition-compare" in call_order
    assert call_order.index("section-compare") < call_order.index("transition-compare")



def test_section_compare_synthesis_uses_correct_section_map_keys() -> None:
    """Regression: section-compare.sh synthesizes ref-sections from
    section-map.json when ENUMERATE_SECTIONS comes back too lean. The
    synthesis code MUST read `top`/`cls` keys (the actual schema written by
    extraction) — not just `y`/`class` (older fallback). The 3-round benchmark
    hit `gate_fail_counts[post-implement] == 632` because the synthesis only
    read `y`, collapsed every section's rect.top to 0, and produced phantom-ref
    coords that triggered uniform AE/Mpx ~950k across all sections. This is
    the data-key bug that made the prior three benchmark rounds' AE numbers
    meaningless.

    Locks the key-name reads in section-compare.sh as a guard. If a future
    refactor drops `s.get("top")` or `s.get("cls")` from the synthesis block,
    this test fires before the script is re-deployed.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    # The synthesis block must read both schemas (top/y, cls/class) so the
    # script tolerates whichever the upstream produced. Lock the canonical
    # patterns; either form fires test failure if dropped.
    assert 's.get("top") or s.get("y")' in text, (
        "section-compare.sh synthesis must read s.get('top') with fallback to "
        "s.get('y') — the 632-retry bug came from reading only 'y'"
    )
    assert 's.get("cls") or s.get("className") or s.get("class")' in text, (
        "section-compare.sh synthesis must read s.get('cls') with fallback to "
        "s.get('className') / s.get('class') — section-map.json writes 'cls', "
        "not just 'class'"
    )


def test_section_compare_synthesis_uses_active_viewport_width() -> None:
    """Full-bleed section-map rows must not stay frozen at 1440px in fan-out."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert 'active_view_width = int(os.environ.get("VIEW_W") or 1440)' in text
    assert 's.get("width") or s.get("w") or active_view_width' in text
    assert "ref-semantic-candidates.json" in text
    assert (
        'cp "$DIR/sections/ref-sections.json" '
        '"$DIR/sections/ref-runtime-sections.json"'
    ) in text
    assert '"$DIR/sections/ref-runtime-sections.json"' in text
    assert "synthesize-ref" in text


def test_section_compare_semantic_candidates_include_short_landmarks() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "const isLandmark = landmarkTags.has(el.tagName.toLowerCase());" in text
    assert "rect.height < (isLandmark ? 24 : 50)" in text
    assert '[id], [class]' in text


def test_section_compare_frozen_reuse_preserves_expanded_ref_sections() -> None:
    """Frozen passes must not collapse the pass-1 ref baseline back to section-map rows."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    guarded_legacy_synthesis = (
        'if [ "$REUSE_FROZEN_REF" != "1" ]; then\n'
        '    python3 - "$SECTION_MAP_FILE" "$DIR/sections/ref-sections.json"'
    )
    assert guarded_legacy_synthesis in text, (
        "the legacy section-map synthesis must not rewrite ref-sections.json "
        "during frozen calibration/measurement passes; pass 1 already merged "
        "the authoritative map with live runtime descendants"
    )


def test_section_compare_augments_impl_with_section_map_semantic_wrappers() -> None:
    """When ref sections are synthesized from section-map.json, impl sections
    need matching semantic wrappers too. Otherwise a ref `main#home` row can
    be paired to its first child section even though the impl DOM contains the
    matching `main#home` wrapper.
    """
    from ui_clone.section_compare_sections import augment_impl_sections_from_section_map

    section_map = {
        "sections": [
            {
                "index": 5,
                "tag": "main",
                "id": "home",
                "className": "page_main__abc",
                "top": 0,
                "height": 8000,
                "childCount": 6,
            }
        ]
    }
    runtime_impl_sections = [
        {
            "index": 0,
            "tag": "section",
            "id": "first",
            "className": "page_first__def",
            "fingerprint": "first child",
            "rect": {"top": 900, "left": 0, "width": 1440, "height": 600},
            "display": "block",
            "gridCols": None,
            "childCount": 1,
        }
    ]
    semantic_candidates = [
        {
            "index": 0,
            "tag": "main",
            "id": "home",
            "className": "page_main__abc",
            "fingerprint": "full page text",
            "hasSvgText": False,
            "rect": {"top": 0, "left": 0, "width": 1440, "height": 7600},
            "display": "block",
            "gridCols": None,
            "childCount": 6,
        }
    ]

    augmented = augment_impl_sections_from_section_map(
        section_map,
        runtime_impl_sections,
        semantic_candidates,
    )

    assert any(
        row["tag"] == "main"
        and row["id"] == "home"
        and row["className"] == "page_main__abc"
        for row in augmented
    )
    assert any(row["id"] == "first" for row in augmented), (
        "augmentation must preserve child sections; it only restores the "
        "semantic wrapper needed for section-map pairing"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert "impl-semantic-candidates.json" in text, (
        "section-compare.sh must probe impl semantic wrappers before matching"
    )
    assert "augment-impl" in text, (
        "section-compare.sh must augment impl-sections.json from section-map candidates"
    )



def test_section_compare_descends_main_wrappers_with_section_descendants() -> None:
    """Loop-56 regression: a `<main>` with only a few color-band wrapper
    `<div>` children must still be treated as a layout wrapper when those
    children contain real section descendants. Otherwise section-compare pairs
    one giant main element and agents can add invisible sentinel children to
    game enumeration.
    """
    # The enumerator JS is single-sourced in lib/enumerate-sections.js
    # (consumed by both section-compare.sh and alignment-sweep-check.sh).
    enum_js = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "lib"
        / "enumerate-sections.js"
    )
    text = enum_js.read_text(encoding="utf-8")

    assert "structuralDescendantCount" in text, (
        "the section enumerator must count nested section/main descendants, not "
        "just direct structural children"
    )
    assert "hasWrappedStructuralDescendants" in text, (
        "the section enumerator must descend <main> wrapper divs that contain "
        "real section/main descendants"
    )
    assert "structuralDescendantCount >= 2" in text, (
        "the wrapper descent must require multiple nested structural sections so "
        "ordinary one-section mains are not over-split"
    )
    depth_match = re.search(r"const MAX_COLLECT_DEPTH = (\d+);", text)
    assert depth_match and int(depth_match.group(1)) >= 10, (
        "deep React/Next wrapper stacks must not truncate real sections on the "
        "reference side while a shallower generated DOM still enumerates them"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    assert "lib/enumerate-sections.js" in script.read_text(encoding="utf-8"), (
        "section-compare.sh must load the single-sourced enumerator"
    )


def test_section_enumerator_ignores_anonymous_capture_overlays() -> None:
    """Capture instrumentation must not become reference-only sections.

    Some browser full-page capture paths append an anonymous, aria-hidden,
    pointer-inert absolute wrapper whose 150vh chunk children span the page.
    Those chunks have section-sized geometry but are not site content.
    """
    enum_js = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "lib"
        / "enumerate-sections.js"
    )
    text = enum_js.read_text(encoding="utf-8")

    assert "isAnonymousCaptureOverlay" in text
    assert 'node.getAttribute("aria-hidden") !== "true"' in text
    assert 'style.pointerEvents === "none"' in text
    assert 'style.position === "absolute" || style.position === "fixed"' in text
    assert "if (isAnonymousCaptureOverlay(el)) return;" in text


def test_section_compare_script_has_viewport_fanout_wrapper() -> None:
    """Static guard for the opt-in multi-viewport wrapper.

    VIEWPORTS must be additive; the single-viewport body remains the inner
    runner, while the wrapper calls it once per viewport and aggregates into
    the canonical sections/result.txt.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "SECTION_COMPARE_INNER_CMD" in text
    assert "SECTION_COMPARE_INNER" in text
    assert "sections/viewports" in text


def test_section_compare_protects_motion_sections_from_structural_only() -> None:
    """Motion-critical sections must not be hidden behind STRUCTURAL_ONLY."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "MOTION_STRUCTURAL_ONLY_PATTERNS" in text
    assert "motion-critical section cannot use STRUCTURAL_ONLY" in text
    assert "transition-spec.json" in text
    assert "required-media.json" in text


def test_section_compare_fans_out_per_viewport_with_stub_inner(tmp_path: Path) -> None:
    """VIEWPORTS runs section-compare once per viewport and aggregates result.txt."""
    ref = tmp_path / "ref"
    ref.mkdir()
    stub = tmp_path / "stub-section-compare.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "out=\"${4:?out}\"\n"
        "mkdir -p \"$out/sections/ref\" \"$out/sections/impl\" \"$out/sections/diff\"\n"
        "printf '%sx%s\\n' \"$VIEW_W\" \"$VIEW_H\" > \"$out/viewport.txt\"\n"
        "cat > \"$out/sections/result.txt\" <<'EOF'\n"
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| Hero Section | 0 | 0 | ok | ✅ |\n"
        "\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
        "EOF\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    env = {
        **os.environ,
        "VIEWPORTS": "375x812,1280x800",
        "SECTION_COMPARE_INNER_CMD": str(stub),
    }
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "mv-session", str(ref)],
        capture_output=True, text=True, timeout=20, env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = (ref / "sections" / "result.txt").read_text(encoding="utf-8")
    assert "viewports: 375x812,1280x800" in result
    assert "viewport: 375x812" in result
    assert "viewport: 1280x800" in result
    assert "| [375x812] Hero Section |" in result
    assert "| [1280x800] Hero Section |" in result
    assert (ref / "sections" / "viewports" / "375x812" / "viewport.txt").read_text().strip() == "375x812"
    assert (ref / "sections" / "viewports" / "1280x800" / "viewport.txt").read_text().strip() == "1280x800"


def test_section_compare_fanout_result_json_sums_five_viewports(tmp_path: Path) -> None:
    """The canonical JSON summary covers all viewport result blocks."""
    ref = tmp_path / "ref"
    ref.mkdir()
    stub = tmp_path / "stub-section-compare.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "out=\"${4:?out}\"\n"
        "mkdir -p \"$out/sections\"\n"
        "{\n"
        "  echo '| Section | AE | AE/Mpx | Severity | Status |'\n"
        "  echo '|---------|-----|--------|----------|--------|'\n"
        "  i=1\n"
        "  while [ \"$i\" -le 8 ]; do\n"
        "    echo \"| Section $i | 0 | 0 | ok | ✅ |\"\n"
        "    i=$((i + 1))\n"
        "  done\n"
        "  echo '**Result: 8 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**'\n"
        "} > \"$out/sections/result.txt\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    agent_browser = fake_bin / "agent-browser"
    agent_browser.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    agent_browser.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "VIEWPORTS": "375x812,768x1024,1280x800,1600x900,1920x1080",
        "SECTION_COMPARE_INNER_CMD": str(stub),
        "UI_CLONE_SESSION_SETTLE_SEC": "0",
    }
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "five-vp", str(ref)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result_json = json.loads((ref / "sections" / "result.json").read_text(encoding="utf-8"))
    assert result_json["summary"] == {
        "pass": 40,
        "fail": 0,
        "skip": 0,
        "structuralOnly": 0,
    }
    assert len(result_json["sections"]) == 40


def test_pair_sections_uses_text_content_when_class_signatures_differ() -> None:
    """A faithful Tailwind clone of a CSS-Modules reference shares ZERO class
    tokens with the ref, so class-signature pairing is blind. The ref
    fingerprint is class-derived (synthesis path), the impl fingerprint is real
    innerText — fingerprint Jaccard is also zero. With only same-tag + order to
    go on, the matcher pairs the ref to whichever impl section iterates first,
    which mis-pairs when a decoy precedes the true content match.

    Text-content fingerprinting (`textWords`) must drive the pairing: the ref
    section must pair to the impl section that SAYS the same thing, not the
    decoy that merely comes first in DOM order.
    """
    from ui_clone.section_compare_sections import pair_sections

    ref = [
        {
            "index": 0,
            "tag": "section",
            "id": None,
            "className": "dga_hero__AjMaf",          # CSS-Modules class
            "fingerprint": "dgaheroajmaf",            # class-derived, no real text
            "textWords": "eat real food restores health wins",
            "rect": {"top": 0, "left": 0, "width": 1440, "height": 800},
            "childCount": 4,
        },
    ]
    impl = [
        # Decoy FIRST in DOM order — same tag, unrelated text. Current matcher
        # pairs ref->this by the +0.1 same-tag boost + iteration order.
        {
            "index": 0,
            "tag": "section",
            "id": None,
            "className": "block min-h-[900px] bg-[#1412]",   # Tailwind, zero overlap
            "fingerprint": "frequently asked questions about the pyramid",
            "textWords": "frequently asked questions about the pyramid guidance",
            "rect": {"top": 0, "left": 0, "width": 1440, "height": 900},
            "childCount": 4,
        },
        # True content match SECOND — Tailwind class, zero token overlap with
        # the CSS-Modules ref class, but the SAME visible words as the ref.
        {
            "index": 1,
            "tag": "section",
            "id": None,
            "className": "relative block min-h-[800px] bg-[#f4f1]",
            "fingerprint": "eat real food restores health wins",
            "textWords": "eat real food restores health wins",
            "rect": {"top": 0, "left": 0, "width": 1440, "height": 800},
            "childCount": 4,
        },
    ]

    matches = pair_sections(ref, impl)
    ref_pair = next(m for m in matches if m.get("ref") and m["ref"]["index"] == 0)

    assert ref_pair["impl"] is not None, "ref hero must pair, not be left unmatched"
    assert ref_pair["impl"]["index"] == 1, (
        "ref must pair to the impl section with matching TEXT (index 1), not the "
        f"decoy that comes first in DOM order (index 0); got impl index "
        f"{ref_pair['impl']['index']}"
    )
    # The decoy must not be paired to the ref hero.
    assert not any(
        m.get("ref") and m["ref"]["index"] == 0 and m.get("impl") and m["impl"]["index"] == 0
        for m in matches
    ), "decoy (impl index 0) must not steal the ref hero pairing"


def test_pair_sections_duplicate_id_disambiguates_by_class_signature() -> None:
    """tools-batch-11 ITEM 4(a): two ref sections both carry id="footer"
    (dga_end CTA + dga_eatReal). With no distinctive text overlap the matcher
    falls to the semantic-key (id-overlap) stage, where the OLD greedy
    index-proximity tiebreak cross-paired ref dga_end(CTA) -> impl blank footer
    (nearest by index) and stranded the real CTA. The id token "footer" overlaps
    EVERY footer, so id alone cannot disambiguate — the class signature (and
    rect/order) must. Each ref footer must pair to the impl footer that shares
    its class signature, not the nearest-by-index blank.
    """
    from ui_clone.section_compare_sections import pair_sections

    ref = [
        {  # dga_end CTA, FIRST in DOM order
            "index": 0, "tag": "footer", "id": "footer",
            "className": "dga_end__cta footer", "fingerprint": "", "textWords": "",
            "rect": {"top": 8000, "left": 0, "width": 1440, "height": 400},
            "childCount": 3,
        },
        {  # dga_eatReal, SECOND
            "index": 1, "tag": "footer", "id": "footer",
            "className": "dga_eatReal__food footer", "fingerprint": "", "textWords": "",
            "rect": {"top": 8400, "left": 0, "width": 1440, "height": 600},
            "childCount": 4,
        },
    ]
    impl = [
        {  # blank generic footer FIRST — nearest by index to ref dga_end (the trap)
            "index": 0, "tag": "footer", "id": "footer",
            "className": "footer", "fingerprint": "", "textWords": "",
            "rect": {"top": 8000, "left": 0, "width": 1440, "height": 400},
            "childCount": 0,
        },
        {  # the real CTA, under a later key — class signature matches ref dga_end
            "index": 1, "tag": "footer", "id": "footer",
            "className": "dga_end__cta footer", "fingerprint": "", "textWords": "",
            "rect": {"top": 8000, "left": 0, "width": 1440, "height": 400},
            "childCount": 3,
        },
        {  # eatReal — class signature matches ref dga_eatReal
            "index": 2, "tag": "footer", "id": "footer",
            "className": "dga_eatReal__food footer", "fingerprint": "", "textWords": "",
            "rect": {"top": 8400, "left": 0, "width": 1440, "height": 600},
            "childCount": 4,
        },
    ]
    matches = pair_sections(ref, impl)
    by_ref = {m["ref"]["index"]: m for m in matches if m.get("ref")}
    assert by_ref[0].get("impl") and by_ref[0]["impl"]["index"] == 1, (
        "ref dga_end(CTA) must pair to the impl footer sharing its class "
        f"signature (index 1), not the nearest-by-index blank footer; got "
        f"{by_ref[0].get('impl')}"
    )
    assert by_ref[1].get("impl") and by_ref[1]["impl"]["index"] == 2, (
        "ref dga_eatReal must pair to its own class-matching footer (index 2); "
        f"got {by_ref[1].get('impl')}"
    )
    # the blank generic footer (impl index 0) must be the unpaired EXTRA, not a
    # cross-pair that strands the CTA.
    assert not any(
        m.get("ref") and m.get("impl") and m["impl"]["index"] == 0 for m in matches
    ), "blank footer (impl 0) must not steal a ref pairing"


def test_pair_sections_single_id_match_still_pairs() -> None:
    """Guard: the global semantic-key assignment must not regress the ordinary
    single-candidate id-overlap pairing (one ref, one impl, shared id token,
    no text)."""
    from ui_clone.section_compare_sections import pair_sections

    ref = [{
        "index": 0, "tag": "section", "id": "resources",
        "className": "dga_resources__deck", "fingerprint": "", "textWords": "",
        "rect": {"top": 2000, "left": 0, "width": 1440, "height": 700}, "childCount": 5,
    }]
    impl = [{
        "index": 0, "tag": "section", "id": "resources",
        "className": "dga_resources__deck", "fingerprint": "", "textWords": "",
        "rect": {"top": 2000, "left": 0, "width": 1440, "height": 700}, "childCount": 5,
    }]
    matches = pair_sections(ref, impl)
    pair = next(m for m in matches if m.get("ref") and m["ref"]["index"] == 0)
    assert pair.get("impl") and pair["impl"]["index"] == 0, pair


def test_pair_sections_disambiguates_duplicate_textwords_by_position() -> None:
    """batch-13 ITEM 1 sub-fix 3: the realfood self-pass swap.

    The ref section-map inherits the LIVE ref's innerText, and on adjacent
    same-class sections it duplicates the SAME text onto two rows (observed:
    the `dga_cta` row and the `faqs` row both carry the faqs paragraph; the
    `dga_eatReal` footer and the `dga_end` footer both carry the dga_end
    paragraph). Two ref rows then text-match ONE impl row at sim=1.0, and the
    impl side enumerates more rows than the ref (food cards / hero-video), so
    DOM indices are OFFSET. The OLD index-distance tiebreak picked the wrong ref
    (nearer index), swapping faqs<->cta and the two footers. rect.top position —
    exact in self-pass, ordered in a faithful clone — is the correct
    disambiguator. Pairing only; the AE/structure compare downstream is
    unchanged, so this never eases a pass.
    """
    from ui_clone.section_compare_sections import pair_sections

    FAQ_TEXT = "frequently asked questions what is the new pyramid avoid sugar"
    END_TEXT = "the government message is simple what we eat shapes health nation"

    ref = [
        {  # faqs
            "index": 0, "tag": "section", "id": "faqs",
            "className": "dga_section__k3uwv", "fingerprint": "faqs",
            "textWords": FAQ_TEXT,
            "rect": {"top": 15824, "left": 0, "width": 1440, "height": 1192},
            "childCount": 1,
        },
        {  # dga_end footer (id=footer #1)
            "index": 1, "tag": "footer", "id": "footer",
            "className": "dga_end___VNIF", "fingerprint": "footer",
            "textWords": END_TEXT,
            "rect": {"top": 17016, "left": 0, "width": 1440, "height": 1149},
            "childCount": 1,
        },
        {  # dga_cta — section-map DUPLICATED the faqs text onto it
            "index": 2, "tag": "section", "id": None,
            "className": "dga_section__k3uwv dga_cta__6_hMx", "fingerprint": "",
            "textWords": FAQ_TEXT,
            "rect": {"top": 18165, "left": 0, "width": 1440, "height": 900},
            "childCount": 1,
        },
        {  # dga_eatReal footer (id=footer #2) — DUPLICATED the dga_end text
            "index": 3, "tag": "footer", "id": "footer",
            "className": "dga_eatReal__hUKXz", "fingerprint": "footer",
            "textWords": END_TEXT,
            "rect": {"top": 19065, "left": 0, "width": 1440, "height": 1068},
            "childCount": 2,
        },
    ]
    # impl enumerates two extra leading rows (food cards) -> DOM indices offset
    # by 2 vs the ref, defeating the index-distance tiebreak.
    impl = [
        {
            "index": 0, "tag": "section", "id": None,
            "className": "dga_sections_section__tSzh_", "fingerprint": "",
            "textWords": "protein dairy healthy fats ending chronic disease",
            "rect": {"top": 8313, "left": 0, "width": 1440, "height": 1062},
            "childCount": 2,
        },
        {
            "index": 1, "tag": "section", "id": None,
            "className": "dga_sections_section__tSzh_", "fingerprint": "",
            "textWords": "vegetables fruits whole grains encouraged daily",
            "rect": {"top": 9375, "left": 0, "width": 1440, "height": 1062},
            "childCount": 2,
        },
        {  # real faqs — same top as ref faqs
            "index": 2, "tag": "section", "id": "faqs",
            "className": "dga_section__k3uwv", "fingerprint": "",
            "textWords": FAQ_TEXT,
            "rect": {"top": 15824, "left": 0, "width": 1440, "height": 1192},
            "childCount": 1,
        },
        {  # real dga_end footer
            "index": 3, "tag": "footer", "id": "footer",
            "className": "dga_end___VNIF", "fingerprint": "",
            "textWords": END_TEXT,
            "rect": {"top": 17016, "left": 0, "width": 1440, "height": 1149},
            "childCount": 1,
        },
        {  # real dga_cta — its OWN (distinct) text
            "index": 4, "tag": "section", "id": None,
            "className": "dga_section__k3uwv dga_cta__6_hMx", "fingerprint": "",
            "textWords": "eat real food spread the word partner with us",
            "rect": {"top": 18165, "left": 0, "width": 1440, "height": 900},
            "childCount": 1,
        },
        {  # real dga_eatReal footer — its OWN (distinct) text
            "index": 5, "tag": "footer", "id": "footer",
            "className": "dga_eatReal__hUKXz", "fingerprint": "",
            "textWords": "designed engineered in dc by national digital",
            "rect": {"top": 19065, "left": 0, "width": 1440, "height": 1068},
            "childCount": 2,
        },
    ]

    matches = pair_sections(ref, impl)
    by_ref = {m["ref"]["index"]: m for m in matches if m.get("ref")}

    def impl_top(ridx: int) -> object:
        m = by_ref[ridx]
        return (m.get("impl") or {}).get("rect", {}).get("top")

    assert impl_top(0) == 15824, f"ref faqs must pair to impl faqs (top 15824); got {impl_top(0)}"
    assert impl_top(1) == 17016, f"ref dga_end must pair to impl dga_end (top 17016); got {impl_top(1)}"
    assert impl_top(2) == 18165, f"ref dga_cta must pair to impl dga_cta (top 18165); got {impl_top(2)}"
    assert impl_top(3) == 19065, f"ref dga_eatReal must pair to impl dga_eatReal (top 19065); got {impl_top(3)}"


def test_pair_sections_rejects_gross_drift_outlier_mispairs() -> None:
    """Drift-outlier repair: a high-text/identity-score pairing whose vertical
    drift is a gross outlier vs the per-page median is rejected for the
    position-consistent candidate.

    Grounded in the realfood regen mispairs: a CTA section whose section-map
    `textWords` were captured from a shared-base-class FAQ sibling text-matches
    the FAQ impl block (drift -1906) instead of the real CTA impl (drift +375);
    a broken-system section semantic-key-matches a far-away CTA block (drift
    +16192) instead of the anonymous impl div at its own position (drift 0).

    Correct pairings (drift ~ +375, the page's consistent intro offset) are
    untouched. PAIRING only — better position consistency yields more accurate
    measurement, never an easier pass.
    """
    from ui_clone.section_compare_sections import pair_sections

    FAQ = "frequently asked questions what is the new pyramid avoid sugar daily"
    ref = [
        {"index": 0, "tag": "section", "id": None, "className": "ref_broken_system__a",
         "fingerprint": "", "textWords": "1992 food pyramid misled by guidance decades",
         "rect": {"top": 2348, "left": 0, "width": 1440, "height": 1800}, "childCount": 3},
        {"index": 1, "tag": "div", "id": None, "className": "ref_container__b",
         "fingerprint": "", "textWords": "official guidance calls on americans avoid processed",
         "rect": {"top": 5948, "left": 0, "width": 1440, "height": 1011}, "childCount": 2},
        {"index": 2, "tag": "section", "id": "faqs", "className": "ref_section__k",
         "fingerprint": "", "textWords": FAQ,
         "rect": {"top": 15824, "left": 0, "width": 1440, "height": 1192}, "childCount": 1},
        {"index": 3, "tag": "section", "id": None, "className": "ref_section__k ref_cta__c",
         "fingerprint": "", "textWords": FAQ,  # shared-base-class collision: CTA carries FAQ text
         "rect": {"top": 18165, "left": 0, "width": 1440, "height": 900}, "childCount": 1},
    ]
    impl = [
        {"index": 0, "tag": "div", "id": None, "className": "",
         "fingerprint": "", "textWords": "1992 food pyramid misled by guidance decades",
         "rect": {"top": 2348, "left": 0, "width": 1440, "height": 1800}, "childCount": 3},
        {"index": 1, "tag": "div", "id": None, "className": "impl_container__x",
         "fingerprint": "", "textWords": "official guidance calls on americans avoid processed",
         "rect": {"top": 6173, "left": 0, "width": 1440, "height": 1011}, "childCount": 2},
        {"index": 2, "tag": "div", "id": None, "className": "",
         "fingerprint": "", "textWords": FAQ,  # FAQ duplicate-text enumeration artifact
         "rect": {"top": 16259, "left": 0, "width": 1440, "height": 1192}, "childCount": 1},
        {"index": 3, "tag": "section", "id": "faqs", "className": "impl_section__k",
         "fingerprint": "", "textWords": FAQ,
         "rect": {"top": 16259, "left": 0, "width": 1440, "height": 1192}, "childCount": 1},
        {"index": 4, "tag": "section", "id": None, "className": "impl_section__k impl_cta__c",
         "fingerprint": "", "textWords": "eat real food spread the word partner champion",
         "rect": {"top": 18540, "left": 0, "width": 1440, "height": 900}, "childCount": 1},
    ]

    matches = pair_sections(ref, impl)
    by_ref = {m["ref"]["index"]: m for m in matches if m.get("ref")}

    def impl_top(ridx: int) -> object:
        return (by_ref[ridx].get("impl") or {}).get("rect", {}).get("top")

    # broken-system must pair to its own-position impl (drift 0), not a far block.
    assert impl_top(0) == 2348, f"broken-system mispaired; impl top {impl_top(0)}"
    # container is a correct pairing (drift +225) — untouched.
    assert impl_top(1) == 6173, f"container pairing must be untouched; got {impl_top(1)}"
    # faqs is a correct pairing (drift +435) — untouched.
    assert impl_top(2) == 16259, f"faqs pairing must be untouched; got {impl_top(2)}"
    # CTA must pair to the real CTA impl (drift +375), NOT the FAQ block (drift -1906).
    assert impl_top(3) == 18540, (
        f"CTA must re-pair to the real CTA impl at 18540 (drift +375), not the "
        f"FAQ block; got impl top {impl_top(3)}"
    )


def test_pair_sections_drift_repair_is_noop_in_ref_vs_ref_self_pass() -> None:
    """Achievability meta-gate: in ref-vs-ref self-pass impl == ref, so every
    drift is 0. The drift-outlier repair must NEVER fire at zero/near-zero drift
    (no pair can exceed the 300px floor), so the self-pass stays green. No match
    may carry the `position-repaired` label."""
    from ui_clone.section_compare_sections import pair_sections

    ref = [
        {"index": i, "tag": "section", "id": f"s{i}", "className": f"sec_{i}__cls",
         "fingerprint": "", "textWords": f"section number {i} distinctive heading copy",
         "rect": {"top": 1000 * i, "left": 0, "width": 1440, "height": 800}, "childCount": 2}
        for i in range(6)
    ]
    impl = [dict(r) for r in ref]  # identical — every drift is exactly 0

    matches = pair_sections(ref, impl)
    assert not any(m.get("pairing") == "position-repaired" for m in matches), (
        "drift-outlier repair must be a NO-OP in the zero-drift ref-vs-ref self-pass"
    )
    # every ref still pairs to its own-index impl
    by_ref = {m["ref"]["index"]: m for m in matches if m.get("ref")}
    for i in range(6):
        assert (by_ref[i].get("impl") or {}).get("index") == i, by_ref[i]


def test_section_compare_crop_scale_tolerance_is_bounded_and_keeps_stretch() -> None:
    """tools-batch-11 ITEM 4(c): the crop-scale tolerance must be a BOUNDED,
    env-knobbed cover-fit CANDIDATE — the legacy exact-stretch stays the primary
    resize (so the localized-defect band check stays valid), and the AE step
    takes the min of stretch-vs-cover so it can only lower AE, never raise it."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert "SECTION_CROP_SCALE_TOL" in text, "crop-scale tolerance knob missing"
    # legacy exact-stretch must still be present (primary resize / wrong-size fallback)
    assert '-resize "$REF_SIZE!"' in text, "legacy exact-stretch resize removed"
    # cover-fit candidate uses aspect-preserving fill + centre extent to ref dims
    assert '-extent "${_R_W}x${_R_H}"' in text, "cover-fit extent-to-ref-dims missing"


def test_section_compare_scroll_phase_tolerance_is_bounded_min_ae() -> None:
    """tools-batch-11 ITEM 4(b): the scroll-phase tolerance must be a BOUNDED,
    env-knobbed vertical-offset sweep that keeps the MINIMUM AE (it can only
    lower AE on a global translation; a defect-scale shift is never aligned
    away)."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert "SECTION_SCROLL_PHASE_TOL_PX" in text, "scroll-phase tolerance knob missing"
    # bounded sweep (seq ... PX) keeping the lower AE candidate
    assert 'seq 2 2 "$SCROLL_PHASE_TOL_PX"' in text, "bounded 2px phase sweep missing"
    assert '"$c" -lt "$AE"' in text, "min-AE keep-lower comparison missing"


def test_section_compare_dssim_cap_still_authoritative() -> None:
    """The batch-11 4(b)/4(c) tolerances are MEASUREMENT corrections, not cap
    changes: the dssim_cap (THRESHOLD x SECTION_DSSIM_AE_CAP_MULT) must still
    gate the leniency paths so extreme AE cannot pass without a fresh judge."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert text.count("dssim_cap_allows") >= 2, (
        "dssim_cap_allows must still gate BOTH leniency branches"
    )


def test_section_compare_failure_guidance_avoids_sigpipe_prone_head_pipelines() -> None:
    """Regression: the failure-report path runs with `set -o pipefail`.

    Piping long markdown excerpts through `head` makes the upstream `awk`
    receive SIGPIPE once `head` has enough lines, so section-compare exits 141
    instead of its documented visual-failure status.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    failure_block = text.split('if [ "$FAIL_COUNT" -gt 0 ]; then', 1)[1].split(
        "exit 1", 1,
    )[0]

    assert "| head -" not in failure_block


def test_section_compare_delegates_capture_to_safe_python_module() -> None:
    """Section screenshot capture should not build shell command strings from
    selector-derived names. The shell script delegates to a typed Python module
    that runs argv commands with shell=False.
    """
    root = _project_root()
    script = root / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "python3 -m ui_clone.section_capture" in text
    assert "subprocess.run(cmd_scroll, shell=True" not in text
    assert "subprocess.run(cmd_crop, shell=True" not in text


def test_frozen_section_compare_preserves_reference_mask_geometry() -> None:
    """The measurement pass reuses frozen reference crops and must also reuse
    the mask rectangles captured with them.

    Replacing mask-elements.json with [] in pass 2B made mask coverage appear
    to be zero even when videos/canvases were hidden in every reference crop.
    """
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "section-compare.sh"
    ).read_text(encoding="utf-8")

    assert (
        'elif [ "$REUSE_FROZEN_REF" = "1" ] '
        '&& [ -s "$MASK_ELEMENTS_FILE" ]; then'
    ) in script
    assert "Reusing frozen reference mask geometry" in script


def test_section_capture_sanitizes_section_filenames() -> None:
    from ui_clone.section_capture import safe_section_name

    assert safe_section_name('hero"; touch /tmp/pwned #') == "hero_touch_tmp_pwned"
    assert safe_section_name("../../secret") == "secret"
    assert safe_section_name("" * 50) == "section"


def test_section_capture_finish_js_is_direct_eval_source_not_shell_escaped() -> None:
    from ui_clone.section_capture import _finish_js

    js = _finish_js()

    assert 'typeof document.getAnimations === "function"' in js
    assert '\\"function\\"' not in js


def test_section_capture_runs_commands_as_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import subprocess

    from ui_clone import section_capture

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(section_capture.subprocess, "run", fake_run)
    monkeypatch.setattr(section_capture.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("SECTION_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_REF", "ref-session")
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_IMPL", "impl-session")
    monkeypatch.setenv("SECTION_CAPTURE_REF_SCROLLER_SEL", 'main[data-x="quoted"]')
    monkeypatch.setenv("SECTION_CAPTURE_IMPL_SCROLLER_SEL", "__document__")

    (tmp_path / "sections" / "ref").mkdir(parents=True)
    (tmp_path / "sections" / "impl").mkdir(parents=True)

    section_capture.capture_matched_sections([
        {
            "name": 'hero"; touch /tmp/pwned #',
            "ref": {"rect": {"top": 120, "left": 0, "width": 300, "height": 200}},
            "impl": {"rect": {"top": 140, "left": 0, "width": 300, "height": 220}},
        }
    ])

    assert calls, "capture must invoke agent-browser/magick commands"
    assert all(isinstance(cmd, list) for cmd, _kwargs in calls)
    assert all(_kwargs.get("shell") is not True for _cmd, _kwargs in calls)
    joined = "\n".join(" ".join(cmd) for cmd, _kwargs in calls)
    assert "hero_touch_tmp_pwned.png" in joined
    assert 'document.querySelector("main[data-x=\\"quoted\\"]")' in joined
    frozen_matches = json.loads(
        (tmp_path / "sections" / "frozen-capture-matches.json").read_text()
    )
    assert frozen_matches[0]["name"] == 'hero"; touch /tmp/pwned #'
    impl_positions = json.loads(
        (tmp_path / "sections" / "impl-scroll-positions.json").read_text()
    )
    assert "hero_touch_tmp_pwned" in impl_positions


def test_find_large_extra_sections_flags_tall_unpaired_impl() -> None:
    """Fix 94 (A3): a tall EXTRA_IN_IMPL row (a duplicated/misplaced impl block)
    must be flagged so the section-compare gate FAILs; small/legit extras and
    ref-paired rows must not."""
    from ui_clone.section_compare_sections import find_large_extra_sections

    matches = [
        {"name": "hero", "status": "PASS", "ref": {"x": 1}, "impl": {"rect": {"height": 900}}},
        {"name": "dga_eatReal-2", "status": "EXTRA_IN_IMPL", "ref": None,
         "impl": {"rect": {"height": 1062}}},  # tall duplicate -> flagged
        {"name": "skip-link", "status": "EXTRA_IN_IMPL", "ref": None,
         "impl": {"rect": {"height": 24}}},     # tiny chrome -> not flagged
        {"name": "no-rect", "status": "EXTRA_IN_IMPL", "ref": None, "impl": {}},
    ]
    flagged = find_large_extra_sections(matches, 500)
    assert flagged == [("dga_eatReal-2", 1062)]
    # Raising the floor above the dup height clears it (env-tunable knob).
    assert find_large_extra_sections(matches, 2000) == []


def test_pair_sections_off_canvas_ref_pairs_to_synthetic_rect() -> None:
    """loop-e2e-4/5 intro overlay: the ref UNMOUNTS its splash overlay after
    settle, so the impl (faithfully unmounting too, or keeping only a hidden
    shell the enumerator excludes) has NO candidate — the matcher then
    garbage-matches the overlay to the hero video (score 0.1) and the compare
    crops painted content against the ref's off-canvas transparent 1x1.

    A ref row whose stored rect lies entirely above the canvas (top + height
    <= 0) must pair to a SYNTHETIC impl entry carrying the same rect, so both
    sides crop the same off-canvas region (deterministic transparent stubs,
    AE 0) and no real impl section is consumed by a garbage match."""
    from ui_clone.section_compare_sections import pair_sections

    ref = [
        {
            "index": 0,
            "tag": "div",
            "id": None,
            "className": "intro-animation_overlay___QI3A",
            "fingerprint": "introanimationoverlayqi3a",
            "textWords": "intro animation overlay",
            "rect": {"top": -900, "left": 0, "width": 1440, "height": 900},
            "childCount": 1,
        },
    ]
    impl = [
        {
            "index": 0,
            "tag": "div",
            "id": "hero-video",
            "className": "dga_hero_video__SoTy9",
            "fingerprint": "",
            "textWords": "",
            "rect": {"top": 680, "left": 144, "width": 1152, "height": 666},
            "childCount": 2,
        },
    ]
    matches = pair_sections(ref, impl)
    overlay = next(m for m in matches if m.get("ref") and "intro" in str(m["ref"].get("className")))
    assert overlay.get("pairing") == "off-canvas", overlay
    assert overlay["impl"]["rect"] == {"top": -900, "left": 0, "width": 1440, "height": 900}
    assert overlay["impl"].get("offCanvas") is True
    # the real impl section must remain available (EXTRA_IN_IMPL), not consumed
    extras = [m for m in matches if m.get("status") == "EXTRA_IN_IMPL"]
    assert extras, matches


def test_extra_impl_contained_in_matched_section_not_flagged() -> None:
    """Un-consuming a garbage match (off-canvas pre-pass) orphans impl
    sub-blocks that previously absorbed it — e.g. the hero-video block whose
    rect sits fully INSIDE the matched hero/dark section. A contained extra is
    enumeration granularity, not a duplicated/misplaced block; only extras
    outside every matched impl rect signal real structural drift."""
    from ui_clone.section_compare_sections import find_large_extra_sections

    matches = [
        {
            "name": "dga_dark",
            "score": 1.0,
            "ref": {"index": 0, "rect": {"top": 42, "left": 0, "width": 1440, "height": 11152}},
            "impl": {"index": 0, "rect": {"top": 42, "left": 0, "width": 1440, "height": 11190}},
        },
        {
            "name": "hero",
            "score": 1.0,
            "ref": {"index": 1, "rect": {"top": 42, "left": 0, "width": 1440, "height": 638}},
            "impl": {"index": 1, "rect": {"top": 42, "left": 0, "width": 1440, "height": 638}},
        },
        {
            "name": "problem",
            "score": 1.0,
            "ref": {"index": 2, "rect": {"top": 1345, "left": 0, "width": 1440, "height": 1002}},
            "impl": {"index": 2, "rect": {"top": 1345, "left": 0, "width": 1440, "height": 1002}},
        },
        {
            "name": "hero-video",
            "score": 0,
            "ref": None,
            "impl": {"index": 5, "rect": {"top": 680, "left": 144, "width": 1152, "height": 666}},
            "status": "EXTRA_IN_IMPL",
        },
        {
            "name": "stray-bottom-hero",
            "score": 0,
            "ref": None,
            "impl": {"index": 9, "rect": {"top": 30000, "left": 0, "width": 1440, "height": 800}},
            "status": "EXTRA_IN_IMPL",
        },
    ]
    matches.append({
        "name": "dup-over-ref",
        "score": 0,
        "ref": None,
        "impl": {"index": 11, "rect": {"top": 50, "left": 0, "width": 1440, "height": 600}},
        "status": "EXTRA_IN_IMPL",
    })
    out = find_large_extra_sections(matches, floor_px=300)
    names = [n for n, _h in out]
    # in-span ref-coverage gap -> enumeration granularity, suppressed
    assert "hero-video" not in names, out
    # appended past the matched span -> still a duplicated/misplaced block
    assert "stray-bottom-hero" in names, out
    # overlapping an existing ref region -> still flagged (dedup "-2" case)
    assert "dup-over-ref" in names, out
