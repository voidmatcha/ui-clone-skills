from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from ui_clone import measure

from ._helpers import (
    _project_root,
)


def test_section_compare_locks_exclude_dynamic_and_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    """`measure section-compare` MUST invoke bash with EXCLUDE_DYNAMIC=1
    and SECTION_THRESHOLD=2000, even when the parent shell sets them to
    permissive values. Locks down the d19e28d gaming pattern where the
    agent set SECTION_THRESHOLD=250000 to re-classify critical→minor.
    """
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
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

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
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
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "structuralDescendantCount" in text, (
        "section-compare.sh must count nested section/main descendants, not just "
        "direct structural children"
    )
    assert "hasWrappedStructuralDescendants" in text, (
        "section-compare.sh must descend <main> wrapper divs that contain real "
        "section/main descendants"
    )
    assert "structuralDescendantCount >= 2" in text, (
        "the wrapper descent must require multiple nested structural sections so "
        "ordinary one-section mains are not over-split"
    )


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
