from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ._helpers import (
    _project_root,
)


def _write_runtime_rollup_fixture(ref: Path, runtime_frame: dict) -> None:
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "no-signals-justified.txt").write_text("test fixture")
    for name in [
        "lottie-runtime.json",
        "runtime-image-validity.json",
        "blank-viewport.json",
        "runtime-dom-parity.json",
        "motion-coverage.json",
        "runtime-spec-coverage.json",
        "scroll-completion.json",
        "reveal-trigger.json",
        "hidden-children.json",
        "svg-provenance.json",
    ]:
        (ref / name).write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    (ref / "runtime-frame-proof.json").write_text(json.dumps(runtime_frame))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))


def _run_runtime_spec_selector_fixture(
    tmp_path: Path,
    runtime_targets: list[str],
) -> tuple[subprocess.CompletedProcess[str], dict]:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps({
            "scrollTrigger": [],
            "gsapTimelines": [
                {"kind": "Timeline", "targets": runtime_targets},
            ],
        }),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps({
            "transitions": [
                {
                    "id": "hero-load",
                    "trigger": "page-load",
                    "selector": ".hero > *",
                },
            ],
        }),
        encoding="utf-8",
    )
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-spec-coverage.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    artifact = json.loads(
        (ref / "runtime-spec-coverage.json").read_text(encoding="utf-8")
    )
    return proc, artifact


@pytest.mark.parametrize("runtime_target", [".hero .title", ".hero > .title"])
def test_runtime_spec_group_selector_accepts_direct_child(
    tmp_path: Path,
    runtime_target: str,
) -> None:
    proc, artifact = _run_runtime_spec_selector_fixture(tmp_path, [runtime_target])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert artifact["status"] == "pass"
    assert artifact["gsapTimelineTargetCoveredCount"] == 1


def test_runtime_spec_group_selector_rejects_nested_descendant(
    tmp_path: Path,
) -> None:
    proc, artifact = _run_runtime_spec_selector_fixture(
        tmp_path,
        [".hero .card .title"],
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert artifact["status"] == "fail"
    assert artifact["gsapTimelineTargetCoveredCount"] == 0
    assert any(".hero .card .title" in message for message in artifact["missing"])


def test_ui_reverse_engineering_skill_frontloads_hard_done_criteria() -> None:
    """The skill must teach completion criteria before long details can distract."""
    skill = _project_root() / "skills" / "ui-reverse-engineering" / "SKILL.md"
    first_50 = "\n".join(skill.read_text(encoding="utf-8").splitlines()[:50]).lower()

    for phrase in (
        "build pass is not done",
        "spot check is not done",
        "pipeline verify pass",
        "missing artifact is failure",
    ):
        assert phrase in first_50, f"{phrase!r} missing from first 50 lines"



def test_module_invocation_help_works() -> None:
    """`python -m ui_clone.measure --help` exits 0 with usage on stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.measure", "--help"],
        capture_output=True, text=True,
        cwd=_project_root(),
    )
    assert proc.returncode == 0
    assert "section-compare" in proc.stdout
    assert "asset-utilization" in proc.stdout
    assert "bundle-impl-coverage" in proc.stdout



def test_fix8_dom_scaffold_script_present() -> None:
    """Fix 8 — dom-scaffold.sh produces the source-of-truth scaffold for
    Phase 4 generation. Locks the script + its key responsibilities so a
    future refactor can't silently remove the determinism layer that
    closed the V4 (avg ~463k AE) → expected-V5 fidelity gap.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"
    assert script.is_file(), "dom-scaffold.sh missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    # Reads the three Phase-2 artifacts.
    for input_name in ("structure.json", "styles.json", "section-map.json"):
        assert input_name in body, f"dom-scaffold.sh must read {input_name}"
    # Writes the canonical output path.
    assert "dom-scaffold.json" in body, "dom-scaffold.sh must write dom-scaffold.json"
    # Style keys carried through to the scaffold tree.
    for key in ("bg", "color", "ff", "fs", "fw", "lh"):
        assert f'"{key}"' in body, f"dom-scaffold.sh must carry styles.{key}"



def test_fix8_text_fidelity_check_script_present() -> None:
    """Fix 8 — text-fidelity-check.sh is the post-Phase-4 gate that blocks
    JSX text-position strings not present in the scaffold allowlist. Locks
    the script + the canonical fabrication-detection regex patterns.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    helper = script.with_name("text_fidelity_check.py")
    assert script.is_file(), "text-fidelity-check.sh missing — Fix 8 incomplete"
    assert helper.is_file(), "text_fidelity_check.py missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    helper_body = helper.read_text(encoding="utf-8")
    # Reads dom-scaffold as the allowlist source.
    assert "dom-scaffold.json" in body
    # Emits the canonical output artifact.
    assert "text-fidelity-check" in body  # appears in OUT name + identity
    # Has the fabrication-detection logic ("status": "fail" branch).
    assert "fabrications" in helper_body, "must enumerate fabrications"


def test_text_fidelity_shell_wrapper_avoids_python_heredoc() -> None:
    """Homebrew Bash can block while writing a large heredoc to Python."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    body = script.read_text(encoding="utf-8")

    assert "<<" not in body
    assert "python3 -" not in body
    assert 'python3 "$SCRIPT_DIR/text_fidelity_check.py"' in body



def test_text_fidelity_check_fails_when_scaffold_text_is_omitted(tmp_path: Path) -> None:
    """A clone that renders only some scaffold text is still wrong.

    The original Fix 8 gate blocked fabricated text, but an impl could omit
    meaningful source copy and still pass. Scratch clone outputs must preserve
    the user-provided public page's visible text, not just avoid new text.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Original Brand Headline", "children": []},
                {"tag": "p", "text": "People creating seasonal recipes", "children": []},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        "export default function App() { return <main><h1>Original Brand Headline</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"omitted scaffold text must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "fail"
    assert artifact["missing_count"] == 1
    assert artifact["missing"][0]["text"] == "People creating seasonal recipes"


@pytest.mark.parametrize(
    ("reference_copy", "wrong_copy"),
    [
        ("새로운 가능성을 발견하세요", "오늘의 특별한 소식을 만나보세요"),
        ("새로운 가능성을 발견하세요", "가능성을 새로운 발견하세요"),
        ("新しい可能性を見つけよう", "今日のおすすめを確認しよう"),
        ("探索全新的可能性", "查看今天的精彩内容"),
    ],
)
def test_text_fidelity_check_rejects_wrong_cjk_copy(
    tmp_path: Path,
    reference_copy: str,
    wrong_copy: str,
) -> None:
    """Visible CJK copy must be checked rather than discarded as non-meaningful."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(
        json.dumps({
            "tree": {
                "tag": "main",
                "children": [
                    {"tag": "h1", "text": reference_copy, "children": []},
                ],
            },
        }),
        encoding="utf-8",
    )
    (src / "App.tsx").write_text(
        f"export default function App() {{ return <h1>{wrong_copy}</h1>; }}\n",
        encoding="utf-8",
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"wrong CJK copy must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["required_meaningful_strings"] == 1
    assert artifact["fabrications"] == [{"component": "App.tsx", "text": wrong_copy}]
    assert artifact["missing"] == [{"text": reference_copy}]


@pytest.mark.parametrize(
    ("reference_copy", "leading_copy", "trailing_copy"),
    [
        ("새로운 가능성을 발견하세요", "새로운 가능성을", "발견하세요"),
        ("새로운 가능성을 발견하세요!", "새로운 가능성을 발견하세요", "!"),
        ("새로운 가능성을 발견하세요✨", "새로운 가능성을 발견하세요", "✨"),
        (
            "새로운 가능성을 발견하세요?",
            '{"새로운 가능성을 발견하세요"}',
            "?",
        ),
        ("新しい可能性を見つけよう", "新しい可能性を", "見つけよう"),
        ("探索全新的可能性", "探索全新的", "可能性"),
        ("哈哈", "哈", "哈"),
    ],
)
def test_text_fidelity_check_accepts_cjk_copy_split_across_jsx_nodes(
    tmp_path: Path,
    reference_copy: str,
    leading_copy: str,
    trailing_copy: str,
) -> None:
    """Adjacent JSX nodes may split faithful CJK copy without adding source spaces."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(
        json.dumps({
            "tree": {
                "tag": "main",
                "children": [
                    {"tag": "p", "text": reference_copy, "children": []},
                ],
            },
        }),
        encoding="utf-8",
    )
    (src / "App.tsx").write_text(
        "export default function App() { return "
        f"<p><span>{leading_copy}</span><span>{trailing_copy}</span></p>; }}\n",
        encoding="utf-8",
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"split faithful CJK copy must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["required_meaningful_strings"] == 1
    assert artifact["missing_count"] == 0
    assert artifact["fabrications"] == []


def test_text_fidelity_cjk_dom_order_ignores_intervening_attribute(
    tmp_path: Path,
) -> None:
    """Accessible attributes are evidence, not rendered DOM-text fragments."""
    reference_copy = "새로운 가능성을 발견하세요"
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(
        json.dumps({
            "tree": {
                "tag": "main",
                "children": [
                    {"tag": "p", "text": reference_copy, "children": []},
                ],
            },
        }),
        encoding="utf-8",
    )
    (src / "App.tsx").write_text(
        "export default function App() { return "
        '<p><span>새로운</span><img alt="가능성을" />'
        "<span> 가능성을 발견하세요</span></p>; }\n",
        encoding="utf-8",
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    assert artifact["missing"] == []
    assert artifact["fabrications"] == []


def test_text_fidelity_intervening_attribute_remains_fabrication_evidence(
    tmp_path: Path,
) -> None:
    reference_copy = "새로운 가능성을 발견하세요"
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(
        json.dumps({
            "tree": {
                "tag": "main",
                "children": [
                    {"tag": "p", "text": reference_copy, "children": []},
                ],
            },
        }),
        encoding="utf-8",
    )
    (src / "App.tsx").write_text(
        "export default function App() { return "
        '<p><span>새로운</span><img aria-label="지금 시작하세요" />'
        "<span> 가능성을 발견하세요</span></p>; }\n",
        encoding="utf-8",
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["missing"] == []
    assert artifact["fabrications"] == [
        {"component": "App.tsx", "text": "지금 시작하세요"},
    ]


def test_text_fidelity_cjk_dom_order_ignores_intervening_custom_prop(
    tmp_path: Path,
) -> None:
    """Custom-component props are evidence, not rendered DOM-text fragments."""
    reference_copy = "새로운 가능성을 발견하세요"
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(
        json.dumps({
            "tree": {
                "tag": "main",
                "children": [
                    {"tag": "p", "text": reference_copy, "children": []},
                ],
            },
        }),
        encoding="utf-8",
    )
    (src / "App.tsx").write_text(
        "function Meta(_props: { heading: string }) { return null; }\n"
        "export default function App() { return "
        '<p><span>새로운</span><Meta heading="가능성을" />'
        "<span> 가능성을 발견하세요</span></p>; }\n",
        encoding="utf-8",
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    assert artifact["missing"] == []
    assert artifact["fabrications"] == []


def test_text_fidelity_intervening_custom_prop_remains_fabrication_evidence(
    tmp_path: Path,
) -> None:
    """Prop-only invented copy must still participate in fabrication checks."""
    reference_copy = "새로운 가능성을 발견하세요"
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(
        json.dumps({
            "tree": {
                "tag": "main",
                "children": [
                    {"tag": "p", "text": reference_copy, "children": []},
                ],
            },
        }),
        encoding="utf-8",
    )
    (src / "App.tsx").write_text(
        "function Meta(_props: { heading: string }) { return null; }\n"
        "export default function App() { return "
        '<p><span>새로운</span><Meta heading="지금 시작하세요" />'
        "<span> 가능성을 발견하세요</span></p>; }\n",
        encoding="utf-8",
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["missing"] == []
    assert artifact["fabrications"] == [
        {"component": "App.tsx", "text": "지금 시작하세요"},
    ]


@pytest.mark.parametrize(
    "implementation_copy",
    [
        "새로운 가능성을 발견하세요 지금 시작하세요",
        "지금 시작하세요 새로운 가능성을 발견하세요",
    ],
)
def test_text_fidelity_check_rejects_extra_cjk_copy_around_reference(
    tmp_path: Path,
    implementation_copy: str,
) -> None:
    """Matching reference copy does not authorize extra visible CJK text."""
    reference_copy = "새로운 가능성을 발견하세요"
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(
        json.dumps({
            "tree": {
                "tag": "main",
                "children": [
                    {"tag": "h1", "text": reference_copy, "children": []},
                ],
            },
        }),
        encoding="utf-8",
    )
    (src / "App.tsx").write_text(
        f"export default function App() {{ return <h1>{implementation_copy}</h1>; }}\n",
        encoding="utf-8",
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"extra CJK copy must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["fabrications"] == [
        {"component": "App.tsx", "text": implementation_copy},
    ]
    assert artifact["missing"] == []


# ── mid-text-span scaffold artifact (loop-e2e-9 self-fail evidence) ─────────
#
# tmp/ref/realfood-e2e-9/brief/new-gate-self-fail-evidence.json: structure
# extraction stores a paragraph's DIRECT text nodes joined ("treating —much")
# while the live ref renders "treating chronic disease—much" — the inline
# <span> sits MID-TEXT. Enforcing the joined string verbatim is unsatisfiable
# by any faithful impl. The extractor now stores the live-rendered order as
# `textFull`; text-fidelity requires textFull (live order) and keeps the
# fragment-joined `text` as allowlist-only evidence.

_E2E9_ARTIFACT_TEXT = (
    "90% of U.S. healthcare spending goes to treating "
    "—much of which is linked to diet and lifestyle"
)
_E2E9_LIVE_TEXT = (
    "90% of U.S. healthcare spending goes to treating chronic disease"
    "—much of which is linked to diet and lifestyle"
)


def _mid_text_span_ref(tmp_path: Path) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [{
                "tag": "p",
                "text": _E2E9_ARTIFACT_TEXT,
                "textFull": _E2E9_LIVE_TEXT,
                "children": [
                    {"tag": "span", "text": "chronic disease", "children": []},
                ],
            }],
        },
    }, ensure_ascii=False))
    return ref, impl


def test_text_fidelity_mid_text_span_live_order_passes(tmp_path: Path) -> None:
    """A faithful impl renders the LIVE order (text + <span> + tail). The
    gate must pass: textFull is the required string, and the leading JSX
    fragment ending in whitespace before the inline child must be captured."""
    ref, impl = _mid_text_span_ref(tmp_path)
    (impl / "src" / "App.tsx").write_text(
        "export default function App() { return (<main>"
        "<p> 90% of U.S. healthcare spending goes to treating "
        "<span>chronic disease</span>—much of which is linked to diet and lifestyle</p>"
        "</main>); }\n",
        encoding="utf-8",
    )
    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    artifact = json.loads(out.read_text())
    assert proc.returncode == 0, f"faithful live-order impl must pass: {json.dumps(artifact, ensure_ascii=False)}"
    assert artifact["status"] == "pass"


def test_text_fidelity_textfull_still_required_when_absent(tmp_path: Path) -> None:
    """Anti-weakening guard: an impl that omits the sentence entirely must
    still fail — textFull is required, not advisory."""
    ref, impl = _mid_text_span_ref(tmp_path)
    (impl / "src" / "App.tsx").write_text(
        "export default function App() { return (<main>"
        "<p>Some entirely different filler paragraph copy</p>"
        "</main>); }\n",
        encoding="utf-8",
    )
    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 1, proc.stdout
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "fail"
    assert any("healthcare spending" in m["text"] for m in artifact["missing"])


def test_text_fidelity_captures_fragment_with_trailing_space(tmp_path: Path) -> None:
    """JSX text positions ending in whitespace before an inline child tag
    (`treating <span>`) must be captured — the old regex required a
    non-space char immediately before `<` and silently dropped the whole
    leading fragment from both the fabrication and missing-side word sets."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "p", "text": "Seasonal harvest recipes everyone loves", "children": []},
            ],
        },
    }))
    (impl / "src" / "App.tsx").write_text(
        "export default function App() { return ("
        "<p>Seasonal harvest recipes everyone loves <span>!</span></p>); }\n"
    )
    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    artifact = json.loads(out.read_text())
    assert proc.returncode == 0, json.dumps(artifact)
    assert artifact["status"] == "pass"


def test_dom_scaffold_carries_textfull_through_compaction(tmp_path: Path) -> None:
    """dom-scaffold.sh must not drop the extractor's textFull field — it is
    the live-rendered text order text-fidelity keys on for mid-text-span
    paragraphs."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "children": [{
            "tag": "p",
            "text": "alpha —gamma",
            "textFull": "alpha beta—gamma",
            "children": [{"tag": "span", "text": "beta", "children": []}],
        }],
    }, ensure_ascii=False))
    (ref / "styles.json").write_text("{}")
    (ref / "section-map.json").write_text(json.dumps({"sections": []}))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "DOM_SCAFFOLD_ALLOW_NO_SECTIONS": "1"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    scaffold = json.loads((ref / "dom-scaffold.json").read_text())
    p = scaffold["tree"]["children"][0]
    assert p.get("textFull") == "alpha beta—gamma", p


def test_extract_dom_captures_textfull_for_mid_text_spans() -> None:
    """Contract: the in-browser extractor stores the live-rendered full text
    alongside the joined direct-text fragments when inline element children
    interleave with text (textContent-based, mid-text-span detection)."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"
    body = script.read_text(encoding="utf-8")
    assert "textFull" in body, (
        "extract-dom.sh must store textFull (live-rendered order) for "
        "mid-text-span elements — the joined direct-text string alone is an "
        "extraction artifact no faithful impl can satisfy (loop-e2e-9)"
    )


def test_text_fidelity_check_scans_jsx_components(tmp_path: Path) -> None:
    """Vite/React clones commonly use JSX files, not TSX files."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Original Brand Headline", "children": []},
            ],
        },
    }))
    (src / "App.jsx").write_text(
        "export default function App() { return <main><h1>Original Brand Headline</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"JSX components must be scanned: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["components_checked"] == 1


def test_text_fidelity_check_resolves_static_string_const_jsx_child(
    tmp_path: Path,
) -> None:
    """Hoisted static copy rendered as a JSX child is still visible source text."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    copy = (
        "Animated showcase of the design system color palette, with AA and "
        "AAA labels showing supported contrast levels."
    )
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [{"tag": "p", "text": copy, "children": []}],
        },
    }))
    (src / "App.tsx").write_text(
        f'const DESCRIPTION = "{copy}";\n'
        "const UNUSED = \"Invented but never rendered supporting copy\";\n"
        "export default function App() {\n"
        "  return <main><p>{DESCRIPTION}</p></main>;\n"
        "}\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["missing_count"] == 0
    assert artifact["fabrications_count"] == 0


def test_text_fidelity_check_uses_element_roles_allowlist_without_cookie_overlay(
    tmp_path: Path,
) -> None:
    """Cookie overlays are not clone targets, but element-role text is source evidence."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "body",
            "children": [
                {
                    "tag": "div",
                    "class": "CybotCookiebotDialogContentWrapper",
                    "children": [
                        {"tag": "div", "text": "We use cookies", "children": []},
                        {"tag": "button", "text": "Allow all", "children": []},
                    ],
                }
            ],
        },
    }))
    (ref / "element-roles.json").write_text(json.dumps({
        "elements": [
            {
                "tag": "h1",
                "role": "heading",
                "selector": "h1.view-mode.unstyled",
                "text": "Design and launch outstanding websites",
            }
        ],
    }))
    (src / "App.jsx").write_text(
        "export default function App() { "
        "return <main><h1>Design and launch outstanding websites</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["missing_count"] == 0



def test_text_fidelity_check_ignores_script_style_noscript_template_text(tmp_path: Path) -> None:
    """Loop-61 finding: dom-scaffold.json captures text inside <script>/<style>/
    <noscript>/<template> tags (e.g. Next.js RSC `self.__next_f.push(...)`
    payloads, framework polyfill bodies). The impl is not expected to render
    that text, so the bidirectional fidelity check must skip those tags on
    the ref side too — symmetric to the existing impl-side <script> strip.
    Without this filter post-implement converges only when every framework
    runtime body is impossibly reproduced in JSX.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Real Food Wins", "children": []},
                # RSC payload, framework runtime — should be filtered.
                {"tag": "script", "text": "self.__next_f.push([1, \"long framework runtime body\"])", "children": []},
                {"tag": "style", "text": ".some-class { color: red; }", "children": []},
                {"tag": "noscript", "text": "Long fallback content that exceeds meaningful filter", "children": []},
                {"tag": "template", "text": "Long template literal contents that pass meaningful", "children": []},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        "export default function App() { return <main><h1>Real Food Wins</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, (
        "script/style/noscript/template text must NOT count as missing: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["missing_count"] == 0


def test_text_fidelity_check_flags_degenerate_empty_scaffold(tmp_path: Path) -> None:
    """A JS-heavy reference site can extract a dom-scaffold with
    structure but ZERO text leaves. Generation then has nothing to transcribe
    verbatim and fabricates the body copy. The bidirectional check is vacuous
    here — scaffold requires nothing (0 missing), and if the fabricated strings
    happen to be in the element-roles allowlist there are 0 fabrications too —
    so the gate FALSE-PASSES a clone built on no text ground truth. The
    degenerate-scaffold guard must fail loudly instead (same class as the
    blank-ref refStd guard for the perceptual section gate).
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    # Scaffold: structure only, NO text leaves (mimics the failed extraction).
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "section", "children": [{"tag": "div", "children": []}]},
                {"tag": "section", "children": [{"tag": "div", "children": []}]},
            ],
        },
    }))
    impl_lines = [
        "Whole foods nourish the body every day",
        "Ultra processed products harm long term health",
        "America returns to real food choices",
        "The dietary guidelines were carefully reviewed",
        "Eat real food and spread the word",
        "Designed and engineered in the capital",
    ]
    # element-roles allowlist contains every impl string → 0 fabrications, so
    # without the guard the gate would PASS (0 missing + 0 fabrications).
    (ref / "element-roles.json").write_text(json.dumps({
        "elements": [{"tag": "p", "text": s} for s in impl_lines],
    }))
    body = "".join(f"<p>{s}</p>" for s in impl_lines)
    (src / "App.tsx").write_text(
        f"export default function App() {{ return <main>{body}</main>; }}\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, (
        "degenerate (0-text) scaffold must fail, not false-pass: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "fail"
    assert artifact["degenerate_scaffold"] is True
    assert artifact["required_meaningful_strings"] == 0
    assert artifact["fabrications_count"] == 0  # proves it's the guard, not fabrication, that failed it


def test_text_fidelity_check_healthy_scaffold_not_degenerate(tmp_path: Path) -> None:
    """Guard the guard: a healthy scaffold with real text must NOT trip the
    degenerate-scaffold guard (no false-positive on legitimate clones).
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Real Food Wins", "children": []},
                {"tag": "p", "text": "America is the greatest country on Earth", "children": []},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        "export default function App() { return <main>"
        "<h1>Real Food Wins</h1>"
        "<p>America is the greatest country on Earth</p>"
        "</main>; }\n"
    )
    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"healthy scaffold must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["degenerate_scaffold"] is False


def test_header_state_runtime_check_script_present() -> None:
    """2026-05-22 user direction: "Header는 정적 HTML이 아니라 state machine입니다."
    The header-state-runtime-check.sh gate must exist, be executable, and
    declare the canonical assertion: ref header mutation on scroll →
    impl header must mutate too.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "header-state-runtime-check.sh"
    assert script.is_file(), "header-state-runtime-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "is-hide" in body or "thema-" in body or "state machine" in body, (
        "script must reference the failure modes (is-hide / thema-* / state machine)"
    )
    assert "scrollTo" in body, "must probe scroll-driven state"
    assert "header-state-runtime.json" in body, "must write the canonical artifact"



def test_header_state_runtime_dispatcher_wired() -> None:
    """Regression: codex-18 (2026-05-22) shipped hero-composite-check.sh
    without dispatcher SIGNATURES wiring → dispatcher NOSIG-skipped it.
    header-state-runtime-check.sh must NOT repeat that mistake.
    """
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"header-state-runtime-check\.sh":\s*"([^"]+)"', text)
    assert m, "header-state-runtime-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    # The gate needs ref-url AND impl-url to compare mutation between
    # them. Without either arg, the probe falls back to a no-op skip.
    assert "{ref_url}" in recipe and "{impl_url}" in recipe, (
        f"header-state-runtime recipe must pass both {{ref_url}} and {{impl_url}} "
        f"(got: {recipe!r})"
    )



# ---------------------------------------------------------------------------
# 2026-06-05 gate-enforcement fix: header GEOMETRY trajectory parity.
#
# The class/data-attr comparator is blind to headers that animate their
# geometry (height 100->64, padding shrink, transform translateY,
# position fixed->absolute) on scroll WITHOUT toggling any class. Real
# artifacts (realfood-gov + 10 sibling refs) shipped status=pass with
# ref.mutates=true AND classesToggled=[] — the mutation came from
# body/html/fw-root class deltas, not verified header geometry. An impl
# that pins the header via overrides.css
# (.header{position:absolute!important;transform:none!important}) passes
# silently. The geo-trajectory comparator closes that blind spot.
# ---------------------------------------------------------------------------

_HSR_SCRIPT_REL = "skills/visual-debug/scripts/header-state-runtime-check.sh"


def _hsr_script_body() -> str:
    return (_project_root() / _HSR_SCRIPT_REL).read_text(encoding="utf-8")


def test_header_state_runtime_captures_geometry_source() -> None:
    """The snap() probe must read computed geometry (getComputedStyle +
    getBoundingClientRect) and the Python comparator must own a geometry
    failure path. Mirrors test_header_state_runtime_check_script_present.
    """
    body = _hsr_script_body()
    assert "getBoundingClientRect" in body, "snap() must measure layout rect"
    assert "paddingTop" in body and "paddingBottom" in body, (
        "snap() must capture vertical padding trajectory"
    )
    assert "position" in body, "snap() must capture position (fixed/absolute)"
    assert "geo" in body, "snap must expose a geo block for the comparator"
    assert "geometry" in body.lower(), (
        "comparator must surface a geometry-specific fail reason"
    )


def _hsr_snap(height: float, *, padding: str = "16px 0px",
              transform: str = "none", position: str = "fixed",
              top: str = "0px", cls: str = "") -> dict:
    """Build a snap() shape with the new geo block. Class set empty by
    default — this is the class-less geometric header blind spot.
    """
    pt, _, pb = padding.partition(" ")
    return {
        "tag": "header",
        "cls": cls,
        "attrs": {},
        "childTagClasses": [],
        "geo": {
            "height": height,
            "paddingTop": pt,
            "paddingBottom": pb if pb else pt,
            "transform": transform,
            "position": position,
            "top": top,
            "scrollY": 0,
        },
    }


def _hsr_probe(samples_geo: list, *, scroll_tops: tuple[int, ...] = (200, 600, 1200, 1500)) -> dict:
    """Compose a probe JSON. samples_geo is a list of (top, height,
    transform, position) describing each scroll sample's geo. at0 is the
    scroll=0 baseline (first entry's height treated as start).
    """
    at0_height, at0_transform, at0_position = samples_geo[0][1:4]
    at0 = _hsr_snap(at0_height, transform=at0_transform, position=at0_position)
    at0["geo"]["scrollY"] = 0
    samples = []
    deep = at0
    for (top, h, tf, pos) in samples_geo[1:]:
        snap = _hsr_snap(h, transform=tf, position=pos)
        snap["geo"]["scrollY"] = top
        samples.append({"top": top, "snapshot": snap})
        deep = snap
    return {
        "found": True,
        "at0": at0,
        "at600": deep,
        "samples": samples,
        "allRoots0": [{"name": "header", "snap": at0}],
        "allRootsDeep": [{"name": "header", "snap": deep}],
        "scrollHeight": 6000,
    }


def _run_hsr_with_stub(
    tmp_path: Path, ref_probe: dict, impl_probe: dict
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Invoke header-state-runtime-check.sh with a PATH-shimmed
    agent-browser that emits the supplied probe fixtures. The shim picks
    ref vs impl by the --session suffix (-hdr-ref / -hdr-impl).
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    ref_fix = tmp_path / "ref_probe.json"
    impl_fix = tmp_path / "impl_probe.json"
    ref_fix.write_text(json.dumps(ref_probe))
    impl_fix.write_text(json.dumps(impl_probe))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "agent-browser"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "# Test stub: emit the right probe fixture on `eval`, no-op otherwise.\n"
        "session=\"\"\n"
        "is_eval=0\n"
        "js=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --session) session=\"$2\"; shift 2;;\n"
        "    eval) is_eval=1; js=\"${2:-}\"; shift;;\n"
        "    *) shift;;\n"
        "  esac\n"
        "done\n"
        "if [ \"$is_eval\" -eq 1 ]; then\n"
        "  if [[ \"$js\" == *innerWidth* ]]; then\n"
        "    # viewport.sh assert probe — report the requested width\n"
        "    echo 1440\n"
        "  elif [[ \"$js\" == *location.href* ]]; then\n"
        "    case \"$session\" in\n"
        "      *-hdr-ref) echo 'https://ref.example.com/' ;;\n"
        "      *-hdr-impl) echo 'https://impl.example.com/' ;;\n"
        "    esac\n"
        f"  else\n"
        f"  case \"$session\" in\n"
        f"    *-hdr-ref) cat {json.dumps(str(ref_fix))[1:-1]};;\n"
        f"    *-hdr-impl) cat {json.dumps(str(impl_fix))[1:-1]};;\n"
        "  esac\n"
        "  fi\n"
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    script = _project_root() / _HSR_SCRIPT_REL
    proc = subprocess.run(
        ["bash", str(script), "tsess",
         "https://ref.example.com", "https://impl.example.com", str(ref_dir)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    artifact_path = ref_dir / "header-state-runtime.json"
    artifact = json.loads(artifact_path.read_text()) if artifact_path.is_file() else {}
    return proc, artifact


def test_header_state_runtime_fails_when_ref_geo_moves_but_impl_frozen(tmp_path: Path) -> None:
    """Ref header shrinks height 100->64 on scroll while toggling NO class;
    impl header is frozen at 64 (overrides.css suppressor). The gate must
    FAIL with a geometry reason and exit 1 — the class comparator alone
    would silently pass here.
    """
    ref_probe = _hsr_probe([
        (0, 100.0, "none", "fixed"),
        (200, 80.0, "translateY(-8px)", "fixed"),
        (600, 64.0, "translateY(-12px)", "absolute"),
        (1500, 64.0, "translateY(-12px)", "absolute"),
    ])
    impl_probe = _hsr_probe([
        (0, 64.0, "none", "absolute"),
        (200, 64.0, "none", "absolute"),
        (600, 64.0, "none", "absolute"),
        (1500, 64.0, "none", "absolute"),
    ])
    proc, artifact = _run_hsr_with_stub(tmp_path, ref_probe, impl_probe)
    assert artifact, f"no artifact written; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert artifact["status"] == "fail", f"expected fail, got {artifact}"
    blob = json.dumps(artifact).lower()
    assert "geometr" in blob, f"fail reason must mention geometry: {artifact['reasons']}"
    assert proc.returncode == 1, f"geo-fail must exit 1, got {proc.returncode}"
    assert artifact["ref"].get("geoChanges") is True
    assert artifact["impl"].get("geoChanges") is False


def test_header_state_runtime_passes_when_impl_geo_matches(tmp_path: Path) -> None:
    """Negative control: impl header also shrinks 100->64 on scroll, so the
    geometric state machine is reproduced — status must be pass, exit 0.
    """
    ref_probe = _hsr_probe([
        (0, 100.0, "none", "fixed"),
        (200, 80.0, "translateY(-8px)", "fixed"),
        (600, 64.0, "translateY(-12px)", "absolute"),
        (1500, 64.0, "translateY(-12px)", "absolute"),
    ])
    impl_probe = _hsr_probe([
        (0, 100.0, "none", "fixed"),
        (200, 80.0, "translateY(-8px)", "fixed"),
        (600, 64.0, "translateY(-12px)", "absolute"),
        (1500, 64.0, "translateY(-12px)", "absolute"),
    ])
    proc, artifact = _run_hsr_with_stub(tmp_path, ref_probe, impl_probe)
    assert artifact, f"no artifact written; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert artifact["status"] == "pass", f"expected pass, got {artifact}"
    assert proc.returncode == 0, f"geo-match must exit 0, got {proc.returncode}"
    assert artifact["ref"].get("geoChanges") is True
    assert artifact["impl"].get("geoChanges") is True


def test_runtime_proof_rollup_aggregates_source_artifacts(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue audit: the runtime-proof aggregator must
    read source artifacts (not run new probes) and FAIL when any source
    gate has status=pass but no actual measurement payload.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # Seed source artifacts. lottie-runtime gets a measurement-free pass
    # (status=pass but candidateCount=0) — must trigger composite FAIL.
    (ref / "lottie-runtime.json").write_text(json.dumps({
        "schemaVersion": 2,
        "status": "pass",
        "refDetected": True,
        "runtimeProof": {"status": "static-only", "candidateCount": 0, "animatingCount": 0},
    }))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))
    (ref / "svg-provenance.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
    }))
    # Other source artifacts intentionally missing — rollup must record them.

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1, f"measurement-free pass must compose to FAIL: {proc.stdout}"
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    assert artifact["status"] == "fail"
    # lottie's measurement-free pass must be flagged as invalid
    lot = next(c for c in artifact["components"] if c["artifact"] == "lottie-runtime.json")
    assert lot["valid"] is False, "lottie static-only with refDetected must be invalid"
    # missing artifacts must be enumerated
    missing_names = [c["artifact"] for c in artifact["components"] if not c.get("present", False)]
    assert "motion-coverage.json" in missing_names, "missing source must be tracked"



def test_runtime_proof_rollup_all_skip_when_truly_absent(tmp_path: Path) -> None:
    """Aggregator must skip (not fail) when every component reports
    status=skip AND every source artifact is present. This is the
    valid "ref site has none of the signals" scenario, not the
    failure mode where gates didn't run.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # 2026-05-22: rollup now requires either a complete plan (with
    # universal anchors) OR a no-signals-justified.txt marker. Provide
    # the marker for this "truly absent" scenario.
    (ref / "no-signals-justified.txt").write_text("test fixture: no signals on this site")
    # Write every source artifact with status=skip and a valid measurement
    # payload (skip reasons that match the validator's accepted skip cases).
    for name in [
        "lottie-runtime.json", "runtime-image-validity.json",
        "blank-viewport.json",
        "runtime-dom-parity.json", "motion-coverage.json",
        "runtime-spec-coverage.json", "runtime-frame-proof.json", "scroll-completion.json",
        "reveal-trigger.json", "hidden-children.json",
        "svg-provenance.json",
    ]:
        (ref / name).write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip", "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"all-skip with hero-pass must compose to pass/skip: {proc.stdout}"


def test_runtime_proof_rollup_requires_planned_runtime_frame_proof(tmp_path: Path) -> None:
    """Canvas/WebGL/Lottie frame proof must participate in runtime-proof."""
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration.json"},
            {"id": "text-fidelity-check", "produces": "text-fidelity.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
            {"id": "asset-transfer", "produces": "asset-transfer.json"},
            {"id": "scaffold-warn", "produces": "scaffold-warn.json"},
            {"id": "runtime-frame-proof", "produces": "runtime-frame-proof.json"},
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, (
        "planned runtime-frame-proof.json must be required by runtime-proof: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    frame = next(c for c in artifact["components"] if c["artifact"] == "runtime-frame-proof.json")
    assert frame["present"] is False
    assert frame["valid"] is False


def test_runtime_proof_rollup_accepts_declared_canvas_replay_video(tmp_path: Path) -> None:
    """An advancing video counts as frame proof only for declared canvas replay."""
    ref = tmp_path / "ref"
    _write_runtime_rollup_fixture(ref, {
        "schemaVersion": 1,
        "status": "pass",
        "canvasTotal": 0,
        "canvasAdvanced": 0,
        "webglAdvanced": 0,
        "lottieInstances": 0,
        "lottieAdvanced": 0,
        "videoTotal": 1,
        "videoAdvanced": 1,
        "videoFrameProofKind": "canvas-replay-video",
        "videoCountsAsAnimationSurface": True,
        "reasons": ["declared replay video is advancing"],
    })

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, f"declared canvas replay video must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    frame = next(c for c in artifact["components"] if c["artifact"] == "runtime-frame-proof.json")
    assert frame["valid"] is True
    assert "video=1/1" in frame["note"]


def test_runtime_proof_rollup_accepts_explicit_video_surface_boolean(tmp_path: Path) -> None:
    """The rollup should trust the explicit videoCountsAsAnimationSurface field,
    not only a legacy string reason or one specific kind spelling.
    """
    ref = tmp_path / "ref"
    _write_runtime_rollup_fixture(ref, {
        "schemaVersion": 1,
        "status": "pass",
        "canvasTotal": 0,
        "canvasAdvanced": 0,
        "webglAdvanced": 0,
        "lottieInstances": 0,
        "lottieAdvanced": 0,
        "videoTotal": 1,
        "videoAdvanced": 1,
        "videoCountsAsAnimationSurface": True,
        "reasons": ["declared replay video is advancing"],
    })

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, f"explicit video surface boolean must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    frame = next(c for c in artifact["components"] if c["artifact"] == "runtime-frame-proof.json")
    assert frame["valid"] is True


def test_runtime_proof_rollup_rejects_unqualified_video_only_pass(tmp_path: Path) -> None:
    """A generic video on the page is not automatically canvas/Lottie frame proof."""
    ref = tmp_path / "ref"
    _write_runtime_rollup_fixture(ref, {
        "schemaVersion": 1,
        "status": "pass",
        "canvasTotal": 0,
        "canvasAdvanced": 0,
        "webglAdvanced": 0,
        "lottieInstances": 0,
        "lottieAdvanced": 0,
        "videoTotal": 1,
        "videoAdvanced": 1,
        "reasons": ["informational: generic video advanced"],
    })

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, f"unqualified video-only pass must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    frame = next(c for c in artifact["components"] if c["artifact"] == "runtime-frame-proof.json")
    assert frame["valid"] is False
    assert "no animation surface" in frame["note"]


def test_runtime_proof_rollup_accepts_hero_kinds_absent_from_ref_and_impl(tmp_path: Path) -> None:
    """Hero rollup must compare ref-vs-impl kinds, not require all
    possible hero kinds. A ref without video/button should not pressure
    impls into adding invisible stub elements.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "no-signals-justified.txt").write_text("test fixture")
    for name in [
        "lottie-runtime.json", "runtime-image-validity.json",
        "blank-viewport.json",
        "runtime-dom-parity.json", "motion-coverage.json",
        "runtime-spec-coverage.json", "runtime-frame-proof.json", "scroll-completion.json",
        "reveal-trigger.json", "hidden-children.json", "svg-provenance.json",
    ]:
        (ref / name).write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "ref": {"video": False, "button": False, "h1OrH2": True, "label": True},
        "impl": {"video": False, "button": False, "h1OrH2": True, "label": True},
        "missingInImpl": [],
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip", "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, (
        "hero kinds absent from both ref and impl must not fail rollup: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    hero = next(c for c in artifact["components"] if c["artifact"] == "hero-composite.json")
    assert hero["valid"] is True


def test_runtime_dom_parity_ignores_bundle_lottie_when_ref_mounts_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic Webflow bundles can contain Lottie plugin code even when
    the live page mounts no Lottie container. That should not force
    clone impls to add fake Lottie nodes.
    """
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "webflow.js").write_text("function lottiePlugin() {}", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_agent = fake_bin / "agent-browser"
    metrics = {
        "nodeCount": 100,
        "textNodeCount": 30,
        "visibleTextNodeCount": 30,
        "viewportArea": 800000,
        "maxElementArea": 120000,
        "maxElementRatio": 0.15,
        "maxElementTag": "img",
        "maxElementSrc": "",
        "lottieMounted": 0,
        "sectionCount": 8,
        "opaqueOverlayCount": 0,
        "opaqueOverlaySample": [],
    }
    fake_agent.write_text(
        "#!/usr/bin/env bash\n"
        "session=''\n"
        "if [ \"${1:-}\" = '--session' ]; then session=\"$2\"; shift 2; fi\n"
        "cmd=\"${1:-}\"\n"
        "if [ \"$cmd\" = 'eval' ]; then\n"
        f"  printf '%s\\n' '{json.dumps(metrics)}'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_agent.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-dom-parity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), "lottie-fp", "https://example.test", "http://localhost:5173", str(ref)],
        capture_output=True, text=True, timeout=10, cwd=_project_root(),
    )
    assert proc.returncode == 0, (
        "bundle-only Lottie evidence with ref lottieMounted=0 must not fail: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-dom-parity.json").read_text())
    assert artifact["status"] == "pass"



def test_runtime_proof_rollup_skips_missing_conditional_artifacts(tmp_path: Path) -> None:
    """2026-05-22 audit: conditional artifacts (scroll-end-completion,
    reveal-trigger) are only produced when their signal fires. When
    verification-plan.json doesn't list them, missing artifact must NOT
    fail the composite — that would block every site without those
    signals from ever passing.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # Plan lists only the unconditional checks + universal anchors
    # (hydration-check, text-fidelity-check, image-fidelity,
    # asset-transfer, scaffold-warn) — required by the rollup's
    # explicit-anchor verification.
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration-check.json"},
            {"id": "text-fidelity-check", "produces": "text-fidelity-check.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
            {"id": "asset-transfer", "produces": "asset-transfer.json"},
            {"id": "scaffold-warn", "produces": "scaffold-warn.json"},
            {"id": "lottie-runtime", "produces": "lottie-runtime.json"},
            {"id": "runtime-image-validity", "produces": "runtime-image-validity.json"},
            {"id": "runtime-dom-parity", "produces": "runtime-dom-parity.json"},
            {"id": "motion-coverage", "produces": "motion-coverage.json"},
            {"id": "runtime-spec-coverage", "produces": "runtime-spec-coverage.json"},
            {"id": "runtime-frame-proof", "produces": "runtime-frame-proof.json"},
            {"id": "header-state-runtime", "produces": "header-state-runtime.json"},
            {"id": "hidden-children", "produces": "hidden-children.json"},
            {"id": "svg-provenance", "produces": "svg-provenance.json"},
            {"id": "hero-composite-check", "produces": "hero-composite.json"},
            # scroll-end-completion and reveal-trigger intentionally NOT in plan
        ],
    }))
    # Write the unconditional artifacts (status=skip is fine for this test)
    for name in [
        "lottie-runtime.json", "runtime-image-validity.json",
        "runtime-dom-parity.json", "motion-coverage.json",
        "runtime-spec-coverage.json", "runtime-frame-proof.json", "hidden-children.json",
        "svg-provenance.json",
    ]:
        (ref / name).write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip", "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, (
        f"missing conditional artifacts (not in plan) must NOT fail composite: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    # Both conditional artifacts should be marked not-applicable, not invalid
    cond = [c for c in artifact["components"]
            if c["artifact"] in ("scroll-completion.json", "reveal-trigger.json")]
    assert len(cond) == 2, "both conditional artifacts must appear in components"
    for entry in cond:
        assert entry["valid"] is True, (
            f"conditional missing artifact must be valid (not in plan): {entry}"
        )
        assert "not applicable" in entry["note"]



def test_runtime_proof_rollup_fails_on_empty_plan_without_justification(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue meta-review (ac93f1e7) + universality
    audit (a000cd35): empty-plan masking. If verification-plan.json
    lacks the universal anchor checks (hydration / text-fidelity /
    image-fidelity / asset-transfer / scaffold-warn) AND no
    `no-signals-justified.txt` marker exists, rollup must FAIL — an
    empty plan would mask every conditional artifact as "not
    applicable", letting a misconfigured run claim composite pass.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # Plan missing universal anchors — only has hydration + image-fidelity,
    # NOT text-fidelity, asset-transfer, scaffold-warn.
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration-check.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
        ],
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1, (
        f"plan missing anchors must FAIL composite: {proc.stdout}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    assert artifact["status"] == "fail"
    reasons = " ".join(artifact["reasons"])
    assert "anchor" in reasons.lower() or "missing universal" in reasons.lower(), (
        f"reasons must explain the missing-anchor failure: {reasons}"
    )



def test_runtime_proof_rollup_accepts_justified_no_signals(tmp_path: Path) -> None:
    """When the ref site genuinely has no runtime signals, an operator
    can write `no-signals-justified.txt` to bypass the empty-plan guard.
    The marker exists so silent misconfigurations don't pass, but
    legitimate static-only sites aren't permanently blocked.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "text-fidelity-check", "produces": "text-fidelity-check.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
        ],
    }))
    (ref / "no-signals-justified.txt").write_text(
        "Static landing page; no scroll/IO/hover triggers. Verified manually."
    )
    # Still need the unconditional sources for the rollup itself to validate
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip",
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    # Returns 0 (pass or skip) because the marker bypasses the empty-plan guard
    assert proc.returncode == 0, (
        f"justified no-signals marker must bypass empty-plan guard: {proc.stdout}"
    )



def test_runtime_proof_rollup_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"runtime-proof-rollup\.sh":\s*"([^"]+)"', text)
    assert m, "runtime-proof-rollup.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe, f"rollup recipe must include {{ref_dir}} (got: {recipe!r})"



def test_ref_js_loader_fails_when_impl_imports_ref_bundle(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue audit: ref-js-loader must catch the cheat
    where impl loads the ref's compiled JS bundle directly via
    <script src> or dynamic import().
    """
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({
        "url": "https://example.cheat-target.org/",
        "title": "Ref",
    }))
    (impl / "src" / "BadComponent.tsx").write_text(
        "// CHEAT: load ref's compiled vendor bundle to fake runtime\n"
        "import vendor from 'https://example.cheat-target.org/_next/static/chunks/main.js';\n"
        "export default function Bad() { return null; }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, f"ref-host import in impl must FAIL: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "ref-js-loader.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["violations"], "must list the violating file"
    assert "example.cheat-target.org" in str(artifact["violations"]), (
        "violation must name the ref host"
    )



def test_duration_easing_grounding_fails_on_invented_duration(tmp_path: Path) -> None:
    """Impl uses 333ms when ref signals 200ms → outside 50ms tolerance,
    not in allowlist → invented → fail.
    """
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    # Ref artifacts signal a 200ms transition
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero-fade", "duration": 200, "easing": "ease-out"},
        ],
    }))
    # Impl uses 333ms (off-grid, not in allowlist) for the same family
    (impl / "src" / "Hero.tsx").write_text(
        "export default function Hero() {\n"
        "  return <div style={{ transition: 'opacity 333ms ease-out 50ms' }} />;\n"
        "}\n"
    )
    # Also use a duration in src that's far from ref
    (impl / "src" / "extra.css").write_text(
        ".x { transition-duration: 4200ms; }\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=10,
    )
    # 4200ms is well outside 50ms tolerance AND not in allowlist → fail
    artifact = json.loads((ref / "duration-easing-grounding.json").read_text())
    if artifact["status"] == "pass":
        # Soft assertion: at least invented count should be non-zero
        assert len(artifact.get("inventedDurations", [])) > 0, (
            f"4200ms should be detected as invented: {artifact}"
        )
    else:
        assert artifact["status"] == "fail"
        assert 4200 in artifact.get("inventedDurations", []) or any(
            "4200" in r for r in artifact.get("reasons", [])
        ), f"4200ms should appear in invented list: {artifact}"


def test_duration_easing_grounding_reads_nested_animation_fields(tmp_path: Path) -> None:
    """transition-spec entries commonly store timing under animation.*.

    The grounding gate must treat animation.duration/ease as ref-measured
    evidence, not skip the check because top-level duration/easing are absent.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-load",
                "selector": ".hero-title",
                "animation": {"duration": 1.2, "ease": "heroEase"},
            },
        ],
    }))
    (impl / "src" / "Hero.css").write_text(
        ".hero-title { transition-duration: 1200ms; transition-timing-function: heroEase; }\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "duration-easing-grounding.json").read_text())
    assert artifact["status"] == "pass"
    assert 1200 in artifact["refDurations"]
    assert "heroease" in artifact["refEasings"]
    assert "heroease" in artifact["matchedEasings"]



def test_duration_easing_grounding_allows_spring_family_easing(tmp_path: Path) -> None:
    """Impl uses Framer Motion spring with elastic.out easing — must
    NOT be classified as invented when SPRING_PAT matches the source.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero-bounce", "duration": 600, "easing": "ease-out"},
        ],
    }))
    # Impl uses spring (no duration literal) + elastic.out easing
    (impl / "src" / "Hero.tsx").write_text(
        "import { motion } from 'framer-motion';\n"
        "export default function Hero() {\n"
        "  return <motion.div\n"
        "    transition={{ type: 'spring', stiffness: 200, damping: 10 }}\n"
        "    style={{ transitionTimingFunction: 'elastic.out(1, 0.5)' }}\n"
        "  />;\n"
        "}\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=10,
    )
    artifact = json.loads((ref / "duration-easing-grounding.json").read_text())
    # Spring detected in impl source — elastic easing should be auto-allowed
    assert artifact.get("implSpringUses", 0) > 0, (
        f"SPRING_PAT must detect stiffness/damping/type=spring: {artifact}"
    )


def test_duration_easing_grounding_ignores_reference_css_mirrors(
    tmp_path: Path,
) -> None:
    """Captured ref CSS under impl/src is evidence, not authored behavior."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "header", "duration": 200, "easing": "ease-out"},
        ],
    }))
    (impl / "src" / "app.css").write_text(
        ".header { transition-duration: 200ms; transition-timing-function: ease-out; }\n"
    )
    (impl / "src" / "ref-css" / "globals.css").write_text(
        ".captured { transition-duration: 4200ms; "
        "transition-timing-function: copiedCurve; }\n"
    )
    (impl / "src" / "reference.css").write_text(
        ".captured-again { animation-duration: 4700ms; "
        "animation-timing-function: anotherCopiedCurve; }\n"
    )

    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "duration-easing-grounding-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=10, check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "duration-easing-grounding.json").read_text())
    assert artifact["status"] == "pass", artifact
    assert artifact["inventedDurations"] == []
    assert set(artifact["ignoredReferenceMirrorFiles"]) == {
        "src/ref-css/globals.css",
        "src/reference.css",
    }



def test_duration_easing_grounding_script_present() -> None:
    """2026-05-22 user request (#9): duration/easing values must
    trace to ref artifacts. SKILL.md Tier 3.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "transition-spec" in body, "must read transition-spec.json"
    assert "ALLOW_EASINGS" in body and "cubic-bezier" in body
    assert "duration-easing-grounding.json" in body



def test_mobile_viewport_parity_script_present() -> None:
    """2026-05-22 user request (#5): mobile viewport gate."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "mobile-viewport-parity-check.sh"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "375" in body, "default mobile width must be 375"
    assert "812" in body, "default mobile height must be 812"
    assert "overflow" in body.lower(), "must check horizontal overflow"
    assert "mobile-viewport-parity.json" in body



def test_runtime_frame_proof_script_present() -> None:
    """2026-05-22 user request (#6/#7): Lottie/canvas/WebGL true
    frame-delta proof using getImageData + readPixels + instance.
    currentFrame.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-frame-proof-check.sh"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "getImageData" in body, "must sample canvas via getImageData"
    assert "sample2DRegions" in body, "must sample across the canvas, not only top-left"
    assert "readPixels" in body, "must sample WebGL via readPixels"
    assert "currentFrame" in body, "must read Lottie instance.currentFrame"
    assert "runtime-frame-proof.json" in body


def test_runtime_proof_rollup_header_geo_pass_without_impl_geo_is_invalid(
    tmp_path: Path,
) -> None:
    """A header artifact that passes class-mutation parity but whose REF geometry
    moves on scroll while the IMPL stays static (geoChanges mismatch) must be
    flagged invalid by the rollup. Class-toggle parity alone cannot prove the
    motion arc (realfood loop-145: ref nav springs on scrollY, impl pinned)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "ref": {"mutates": True, "geoChanges": True},
        "impl": {"mutates": True, "geoChanges": False},
    }))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "svg-provenance.json").write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    subprocess.run(["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10)
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    hdr = next(c for c in artifact["components"] if c["artifact"] == "header-state-runtime.json")
    assert hdr["valid"] is False, f"header geo-mismatch pass must be invalid: {hdr}"


def test_runtime_proof_rollup_header_geo_match_stays_valid(tmp_path: Path) -> None:
    """Positive: ref + impl geometry both move on scroll -> header valid (no
    false positive from the geometry validity gate)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "ref": {"mutates": True, "geoChanges": True},
        "impl": {"mutates": True, "geoChanges": True},
    }))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "svg-provenance.json").write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    subprocess.run(["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10)
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    hdr = next(c for c in artifact["components"] if c["artifact"] == "header-state-runtime.json")
    assert hdr["valid"] is True, f"matching geometry must stay valid: {hdr}"


def test_runtime_frame_proof_drives_scroll_scrubbed_lottie() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-frame-proof-check.sh"
    body = script.read_text(encoding="utf-8")

    assert "maxScrollForLottie" in body
    assert "window.dispatchEvent(new Event(\"scroll\"))" in body
    assert "scroll-scrubbed refs" in body


def _run_runtime_text_sequence_with_stub(
    tmp_path: Path,
    ref_blocks: list[str],
    impl_blocks: list[str],
    *,
    ref_capture: dict | None = None,
    impl_capture: dict | None = None,
    ref_url: str = "https://ref.example.test/",
    impl_url: str = "https://impl.example.test/",
    ref_actual_url: str | None = None,
    impl_actual_url: str | None = None,
    ref_response_status: int = 200,
    impl_response_status: int = 200,
    ref_error_document: bool = False,
    impl_error_document: bool = False,
    stub_env: dict[str, str] | None = None,
    session: str = "text-seq",
) -> tuple[subprocess.CompletedProcess[str], dict, str]:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    def _batch_payload(
        blocks: list[str],
        capture: dict | None,
        requested_url: str,
        actual_url: str | None,
        response_status: int,
        error_document: bool,
    ) -> list[dict]:
        loaded_url = actual_url or requested_url
        parsed_url = urlsplit(loaded_url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        if capture is None:
            records = [
                {
                    "slot": f"main>p:nth-of-type({index + 1})::run(1)",
                    "text": text,
                    "tag": "P",
                    "initialViewport": False,
                }
                for index, text in enumerate(blocks)
            ]
            captured = {
                "blocks": blocks,
                "blockCount": len(blocks),
                "records": records,
                "samples": [records, records],
                "phaseSampleStartIndex": 0,
            }
        else:
            captured = json.loads(json.dumps(capture))
        captured.update({
            "actualUrl": loaded_url,
            "pageReceipt": {
                "actualUrl": loaded_url,
                "origin": origin,
                "readyState": "complete",
                "navigationType": "navigate",
                "responseStatus": response_status,
                "errorDocument": error_document,
            },
        })
        return [
            {
                "command": ["set", "media", "light", "reduced-motion"],
                "error": None,
                "result": {"media": "light", "reducedMotion": True},
                "success": True,
            },
            {
                "command": ["open", requested_url],
                "error": None,
                "result": {"url": loaded_url},
                "success": True,
            },
            {
                "command": ["wait", "1800"],
                "error": None,
                "result": {"waited": "timeout"},
                "success": True,
            },
            {
                "command": ["eval", "initial-text-analysis"],
                "error": None,
                "result": {
                    "origin": origin,
                    "result": json.dumps(captured)
                },
                "success": True,
            },
            {
                "command": ["eval", "incremental-scroll"],
                "error": None,
                "result": {
                    "origin": origin,
                    "result": '{"steps":4,"quiescent":true}',
                },
                "success": True,
            },
            {
                "command": ["eval", "text-analysis"],
                "error": None,
                "result": {
                    "origin": origin,
                    "result": json.dumps(captured)
                },
                "success": True,
            },
        ]

    ref_fixture = tmp_path / "runtime-text-ref-batch.json"
    impl_fixture = tmp_path / "runtime-text-impl-batch.json"
    open_error_fixture = tmp_path / "runtime-text-open-error-batch.json"
    eval_error_fixture = tmp_path / "runtime-text-eval-error-batch.json"
    incomplete_fixture = tmp_path / "runtime-text-incomplete-batch.json"
    oversized_fixture = tmp_path / "runtime-text-oversized-batch.json"
    object_fixture = tmp_path / "runtime-text-object-batch.json"
    malformed_fixture = tmp_path / "runtime-text-malformed-batch.json"
    top_error_fixture = tmp_path / "runtime-text-top-error-batch.json"
    ref_fixture.write_text(
        json.dumps(_batch_payload(
            ref_blocks,
            ref_capture,
            ref_url,
            ref_actual_url,
            ref_response_status,
            ref_error_document,
        )),
        encoding="utf-8",
    )
    impl_fixture.write_text(
        json.dumps(_batch_payload(
            impl_blocks,
            impl_capture,
            impl_url,
            impl_actual_url,
            impl_response_status,
            impl_error_document,
        )),
        encoding="utf-8",
    )
    open_error_fixture.write_text(
        json.dumps(
            [
                {
                    "command": ["open", "https://loaded.example.test/"],
                    "error": "navigation failed",
                    "result": None,
                    "success": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    eval_error_payload = _batch_payload(
        impl_blocks,
        impl_capture,
        impl_url,
        impl_actual_url,
        impl_response_status,
        impl_error_document,
    )
    eval_error_payload[5] = {
        "command": ["eval", "text-analysis"],
        "error": "eval failed",
        "result": None,
        "success": False,
    }
    eval_error_fixture.write_text(
        json.dumps(eval_error_payload),
        encoding="utf-8",
    )
    incomplete_fixture.write_text(
        json.dumps(
            _batch_payload(
                ref_blocks,
                ref_capture,
                ref_url,
                ref_actual_url,
                ref_response_status,
                ref_error_document,
            )[:5]
        ),
        encoding="utf-8",
    )
    oversized_fixture.write_text(
        json.dumps(
            _batch_payload(
                ref_blocks,
                ref_capture,
                ref_url,
                ref_actual_url,
                ref_response_status,
                ref_error_document,
            )
            + [{"command": ["unexpected"], "success": True, "result": None}]
        ),
        encoding="utf-8",
    )
    object_fixture.write_text(
        json.dumps({"unexpected": _batch_payload(
            ref_blocks,
            ref_capture,
            ref_url,
            ref_actual_url,
            ref_response_status,
            ref_error_document,
        )}),
        encoding="utf-8",
    )
    wrapped_fixture = tmp_path / "batch-wrapped.json"
    wrapped_fixture.write_text(
        json.dumps({"results": _batch_payload(
            ref_blocks,
            ref_capture,
            ref_url,
            ref_actual_url,
            ref_response_status,
            ref_error_document,
        )}),
        encoding="utf-8",
    )
    malformed_fixture.write_text("{not-json\n", encoding="utf-8")
    top_error_fixture.write_text(
        json.dumps({
            "success": False,
            "error": (
                "Session name 'dogfood-docs-canonical-final6-runtime-text' "
                "is too long. Socket path would be 126 bytes (max 103)."
            ),
        }),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "agent-browser.log"
    state_dir = tmp_path / "browser-state"
    state_dir.mkdir()
    stub = bin_dir / "agent-browser"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {str(log_path)!r}\n"
        "session=''\n"
        "if [ \"${1:-}\" = '--session' ]; then session=\"$2\"; shift 2; fi\n"
        "cmd=\"${1:-}\"\n"
        "if [ \"$cmd\" = 'session' ] && [ \"${2:-}\" = 'list' ]; then\n"
        "  echo 'Active sessions:'\n"
        f"  for active in {str(state_dir)!r}/*.active; do\n"
        "    [ -e \"$active\" ] || continue\n"
        "    name=${active##*/}\n"
        "    printf '  %s\\n' \"${name%.active}\"\n"
        "  done\n"
        "  exit 0\n"
        "fi\n"
        f"state={str(state_dir)!r}/$session.closed\n"
        "if [ \"$cmd\" = 'close' ]; then\n"
        f"  close_count_file={str(state_dir)!r}/$session.close-count\n"
        "  close_count=0\n"
        "  [ -f \"$close_count_file\" ] && close_count=$(cat \"$close_count_file\")\n"
        "  close_count=$((close_count + 1))\n"
        "  printf '%s\\n' \"$close_count\" > \"$close_count_file\"\n"
        "  if [[ \"$session\" == \"${CLOSE_ALWAYS_FAIL_SESSION:-}\"-capture-* ]]; then "
        "exit 9; fi\n"
        "  if [[ \"$session\" == \"${CLOSE_FAIL_ONCE_SESSION:-}\"-capture-* "
        "&& \"$close_count\" = '1' ]]; then exit 9; fi\n"
        f"  rm -f {str(state_dir)!r}/$session.active\n"
        "  touch \"$state\"; exit 0\n"
        "fi\n"
        "if [ \"$cmd\" = 'batch' ]; then\n"
        f"  touch {str(state_dir)!r}/$session.active\n"
        "  cat >/dev/null\n"
        f"  count_file={str(state_dir)!r}/$session.batch-count\n"
        "  count=0\n"
        "  [ -f \"$count_file\" ] && count=$(cat \"$count_file\")\n"
        "  count=$((count + 1))\n"
        "  printf '%s\\n' \"$count\" > \"$count_file\"\n"
        "  if [[ \"$session\" == \"${FIRST_BATCH_OVERSIZED_SESSION:-}\"-capture-*-1 "
        "|| ( \"$session\" == rtseq-ref-*-1 && \"${FIRST_BATCH_OVERSIZED_SESSION:-}\" = *-text-ref ) ]]; then\n"
        f"    cat {str(oversized_fixture)!r}; exit 7\n"
        "  fi\n"
        "  if [[ \"$session\" == \"${FIRST_BATCH_OBJECT_SESSION:-}\"-capture-*-1 "
        "|| ( \"$session\" == rtseq-ref-*-1 && \"${FIRST_BATCH_OBJECT_SESSION:-}\" = *-text-ref ) ]]; then\n"
        f"    cat {str(object_fixture)!r}; exit 7\n"
        "  fi\n"
        "  if [[ \"$session\" == \"${FIRST_BATCH_WRAPPED_SESSION:-}\"-capture-*-1 "
        "|| ( \"$session\" == rtseq-ref-*-1 && \"${FIRST_BATCH_WRAPPED_SESSION:-}\" = *-text-ref ) ]]; then\n"
        f"    cat {str(wrapped_fixture)!r}; exit 0\n"
        "  fi\n"
        "  if [[ \"$session\" == \"${FIRST_BATCH_MALFORMED_SESSION:-}\"-capture-*-1 "
        "|| ( \"$session\" == rtseq-ref-*-1 && \"${FIRST_BATCH_MALFORMED_SESSION:-}\" = *-text-ref ) ]]; then\n"
        f"    cat {str(malformed_fixture)!r}; exit 7\n"
        "  fi\n"
        "  if [[ \"$session\" == \"${FIRST_BATCH_TOP_ERROR_SESSION:-}\"-capture-*-1 "
        "|| ( \"$session\" == rtseq-ref-*-1 && \"${FIRST_BATCH_TOP_ERROR_SESSION:-}\" = *-text-ref ) ]]; then\n"
        f"    cat {str(top_error_fixture)!r}; exit 7\n"
        "  fi\n"
        "  if [[ \"$session\" == \"${FIRST_BATCH_INCOMPLETE_SESSION:-}\"-capture-*-1 "
        "|| ( \"$session\" == rtseq-ref-*-1 && \"${FIRST_BATCH_INCOMPLETE_SESSION:-}\" = *-text-ref ) ]]; then\n"
        f"    cat {str(incomplete_fixture)!r}; exit 7\n"
        "  fi\n"
        "  if [ \"${OPEN_FAIL_STALE:-0}\" = '1' ]; then\n"
        f"    cat {str(open_error_fixture)!r}; exit 7\n"
        "  fi\n"
        "  if [[ \"$session\" == \"${EVAL_FAIL_SESSION:-}\"-capture-* ]]; then\n"
        f"    cat {str(eval_error_fixture)!r}; exit 9\n"
        "  fi\n"
        "  case \"$session\" in\n"
        f"    *-text-ref-capture-*|rtseq-ref-*) cat {str(ref_fixture)!r};;\n"
        f"    *-text-impl-capture-*|rtseq-impl-*) cat {str(impl_fixture)!r};;\n"
        "  esac\n"
        "  if [ \"${OPEN_NONZERO_VALID:-0}\" = '1' ]; then exit 7; fi\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env.update(stub_env or {})
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    proc = subprocess.run(
        [
            "bash",
            str(script),
            session,
            ref_url,
            impl_url,
            str(ref_dir),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        cwd=_project_root(),
    )
    artifact_path = ref_dir / "runtime-text-sequence.json"
    artifact = (
        json.loads(artifact_path.read_text(encoding="utf-8"))
        if artifact_path.is_file()
        else {}
    )
    log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    return proc, artifact, log


def test_runtime_text_sequence_probe_contract_is_browser_backed() -> None:
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime-text-sequence-check.sh"
    )
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "NodeFilter.SHOW_TEXT" in body
    assert 'normalize("NFC")' in body
    assert "getComputedStyle" in body
    assert "hasVisibleTextPaint" in body
    assert "webkitTextFillColor" in body
    assert "hasTextBackground" in body
    assert "backgroundClip" in body
    assert body.index("if (hasTextBackground) return true;") < body.index(
        "if (textFill) return colorAlpha(textFill) > 0.01;"
    )
    assert 'display === "none"' in body
    assert 'visibility === "hidden"' in body
    assert "aria-hidden" in body
    assert "blockFor" in body
    assert "grouped" in body
    assert "semanticTags" in body
    assert "activeBlock" in body
    assert "const grouped = new Map()" not in body
    assert '"inline-block", "inline-flex"' not in body
    assert "horizontallyOffCanvas" in body
    assert "clipsDescendants" in body
    assert "elementsFromPoint" in body
    assert "MutationObserver" in body
    assert "__uiCloneRuntimeTextState" in body
    assert "initialBlocks: new WeakSet()" in body
    assert "state.initialBlocks.add(block)" in body
    assert "state.initialBlocks.has(block)" in body
    assert "runtime text measurement failed" in body
    assert "__uiCloneCollectRuntimeText" in body
    assert "activeAnchor = node" in body
    assert "anchor.isConnected" in body
    assert "compareDocumentPosition" in body
    assert '["set", "media", "light", "reduced-motion"]' in body
    assert "maxSteps = 48" in body
    assert "stableNeeded = 3" in body
    assert "phaseSamples = 12" in body
    assert "phaseSampleStartIndex" in body
    assert "scrollHeight / 2" not in body
    assert 'batch --json --bail' in body
    assert 'agent-browser --session "$session" open' not in body
    assert "max_attempts=3" in body
    assert 'attempt_session="$(make_capture_session "$session" "$attempt")"' in body
    assert 'printf \'rtseq-%s-%s-%s\\n\'' in body
    assert "batch result contained {len(payload)}/6 command results" in body
    assert 'payload = raw_payload.get("results")' in body
    assert "batch result object did not contain a results list" in body
    assert "agent-browser batch error: {error}" in body
    assert "retryable=True" in body
    assert 'close_capture_session "$attempt_session"' in body
    assert 'agent-browser --session "$session_name" close' in body
    assert "capture_session_active" in body
    assert '"closed": False' in body
    assert 'parts.join("")' in body
    assert "runtime-text-sequence.json" in body
    assert "(() => {" in body


def test_runtime_text_sequence_comparator_is_standalone_python() -> None:
    scripts = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
    )
    shell = scripts / "runtime-text-sequence-check.sh"
    helper = scripts / "runtime_text_sequence_compare.py"
    assert helper.is_file()
    shell_body = shell.read_text(encoding="utf-8")
    helper_body = helper.read_text(encoding="utf-8")

    assert (
        'python3 - "$REF_TMP" "$IMPL_TMP" "$OUT_PATH" "$REF_URL" '
        '"$IMPL_URL" <<\'PY\''
    ) not in shell_body
    assert (
        'python3 "$SCRIPT_DIR/runtime_text_sequence_compare.py" \\\n'
        '  "$REF_TMP" "$IMPL_TMP" "$OUT_PATH" "$REF_URL" "$IMPL_URL"'
    ) in shell_body
    assert helper_body.startswith("from __future__ import annotations\n")
    assert len(helper_body.encode("utf-8")) > 20_000
    assert "MIN_ORDERED_SIMILARITY = 0.85" in helper_body
    assert '"phaseVariance": phase_variance' in helper_body
    assert 'raise SystemExit(2 if status == "error"' in helper_body


def test_runtime_text_sequence_compare_runs_under_macos_system_python(
    tmp_path: Path,
) -> None:
    """The dispatcher may invoke this helper through macOS /usr/bin/python3."""
    host_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    helper = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "runtime_text_sequence_compare.py"
    )

    def record(slot: str, text: str) -> dict[str, object]:
        return {
            "slot": slot,
            "text": text,
            "tag": "DIV",
            "initialViewport": True,
        }

    def capture(texts: list[str]) -> dict[str, object]:
        records = [record(str(index), text) for index, text in enumerate(texts)]
        return {
            "blocks": texts,
            "records": records,
            "samples": [records, records],
            "phaseSampleStartIndex": 0,
            "actualUrl": "http://example.test/",
            "captureReceipt": {"source": "unit"},
        }

    ref_path = tmp_path / "ref.json"
    impl_path = tmp_path / "impl.json"
    out_path = tmp_path / "out.json"
    ref_path.write_text(json.dumps(capture(["A", "B", "C", "D"])), encoding="utf-8")
    impl_path.write_text(
        json.dumps(capture(["A", "X", "B", "C", "D"])),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            host_python,
            str(helper),
            str(ref_path),
            str(impl_path),
            str(out_path),
            "http://ref.example.test/",
            "http://impl.example.test/",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode in {0, 1}, proc.stdout + proc.stderr
    assert "zip() takes no keyword" not in proc.stdout + proc.stderr


def test_runtime_text_sequence_normalizes_unicode_whitespace_and_zero_width(
    tmp_path: Path,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Café", "새로운 가능성을 발견하세요", "Footer links"],
        [
            "Cafe\u0301",
            "  새로운\u200b 가능성을\n발견하세요  ",
            "Footer\u2060 links",
        ],
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["comparison"]["lcsLength"] == 3
    assert artifact["comparison"]["missingCount"] == 0
    assert "--session text-seq-text-ref" in log
    assert "--session text-seq-text-impl" in log
    assert " close" in log
    ref_batches = [
        line
        for line in log.splitlines()
        if line.startswith("--session text-seq-text-ref-capture-")
        and " batch --json --bail" in line
    ]
    impl_batches = [
        line
        for line in log.splitlines()
        if line.startswith("--session text-seq-text-impl-capture-")
        and " batch --json --bail" in line
    ]
    assert len(ref_batches) == 1
    assert len(impl_batches) == 1


@pytest.mark.parametrize(
    ("reference", "implementation"),
    [
        ("👩\u200d💻", "👩💻"),
        ("می\u200cروم", "میروم"),
    ],
)
def test_runtime_text_sequence_preserves_semantic_unicode_joiners(
    tmp_path: Path,
    reference: str,
    implementation: str,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        [reference],
        [implementation],
    )

    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "fail"
    assert artifact["ref"]["blocks"] == [reference]
    assert artifact["impl"]["blocks"] == [implementation]
    assert "canonical-block-sequence-mismatch" in {
        item["kind"] for item in artifact["violations"]
    }
    assert sum(
        " batch --json --bail" in line
        for line in log.splitlines()
    ) == 2
    assert "retrying with a fresh session" not in proc.stderr


def test_runtime_text_sequence_rejects_redirected_capture_route(
    tmp_path: Path,
) -> None:
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        impl_actual_url="https://ref.example.test/unrequested",
    )

    assert proc.returncode == 2, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "error"
    assert "impl-url-mismatch" in {
        item["kind"] for item in artifact["violations"]
    }


def test_runtime_text_sequence_rejects_ref_as_impl_capture(
    tmp_path: Path,
) -> None:
    shared_url = "https://ref.example.test/same-route"
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        ref_url=shared_url,
        impl_url=shared_url,
    )

    assert proc.returncode == 2, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "error"
    assert artifact["actualRefUrl"] == shared_url
    assert artifact["actualImplUrl"] == shared_url
    assert "ref-impl-url-collision" in {
        item["kind"] for item in artifact["violations"]
    }


@pytest.mark.parametrize(
    ("response_status", "error_document"),
    [(500, False), (200, True)],
)
def test_runtime_text_sequence_rejects_error_document_receipt(
    tmp_path: Path,
    response_status: int,
    error_document: bool,
) -> None:
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        impl_response_status=response_status,
        impl_error_document=error_document,
    )

    assert proc.returncode == 2, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "error"
    assert "impl-http-error" in {
        item["kind"] for item in artifact["violations"]
    }


def test_runtime_text_sequence_fails_conservative_order_and_missing_thresholds(
    tmp_path: Path,
) -> None:
    ref_blocks = [
        "Home",
        "Products",
        "A better workflow",
        "Built for teams",
        "Fast setup",
        "Secure by default",
        "Customer stories",
        "Pricing",
        "Contact",
        "Legal",
    ]
    impl_blocks = [
        "Home",
        "Products",
        "Built for teams",
        "A better workflow",
        "Fast setup",
        "Customer stories",
        "Pricing",
        "Legal",
    ]
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path, ref_blocks, impl_blocks
    )

    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "fail"
    assert artifact["comparison"]["lcsLength"] == 7
    assert artifact["comparison"]["missingCount"] == 3
    assert artifact["comparison"]["missingRatio"] == pytest.approx(0.3)
    kinds = {item["kind"] for item in artifact["violations"]}
    assert "ordered-text-similarity-below-threshold" in kinds
    assert "rendered-text-missing-above-threshold" in kinds


def test_runtime_text_sequence_fails_any_single_copy_difference(
    tmp_path: Path,
) -> None:
    ref_blocks = [f"Canonical copy {index}" for index in range(20)]
    impl_blocks = list(ref_blocks)
    impl_blocks[10] = "Almost canonical copy"

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_blocks,
        impl_blocks,
    )

    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "fail"
    assert artifact["comparison"]["orderedSimilarity"] == pytest.approx(0.95)
    assert {item["kind"] for item in artifact["violations"]} == {
        "canonical-block-sequence-mismatch"
    }


def _slot_variance_capture(
    texts: list[str],
    *,
    tags: list[str] | None = None,
    initial_indexes: set[int] | None = None,
    sample_texts: list[list[str]] | None = None,
) -> dict:
    resolved_tags = tags or ["P"] * len(texts)
    initial = initial_indexes or set()

    def records_for(values: list[str]) -> list[dict]:
        return [
            {
                "slot": (
                    "main:nth-of-type(1)>section:nth-of-type(1)>"
                    f"{tag.lower()}:nth-of-type({index + 1})::run(1)"
                ),
                "text": text,
                "tag": tag,
                "initialViewport": index in initial,
            }
            for index, (text, tag) in enumerate(
                zip(values, resolved_tags, strict=True)
            )
        ]

    records = records_for(texts)
    samples = [
        records_for(values) for values in (sample_texts or [texts, texts])
    ]
    if samples[-1] != records:
        samples.append(records)
    return {
        "blocks": texts,
        "blockCount": len(texts),
        "records": records,
        "samples": samples,
        "phaseSampleStartIndex": 0,
    }


def test_runtime_text_sequence_accepts_block_local_whitespace_boundaries(
    tmp_path: Path,
) -> None:
    ref = ["Before", "AI 원천기술을 도입하여 사용자 맞춤형 서비스", "After"]
    impl = ["Before", "AI 원천기술을 도입하여사용자 맞춤형 서비스", "After"]

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path, ref, impl
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["comparison"]["missingCount"] == 0


def test_runtime_text_sequence_accepts_proven_parallel_dynamic_region(
    tmp_path: Path,
) -> None:
    ref = [f"Stable copy {index}" for index in range(24)]
    impl = list(ref)
    ref[10:13] = ["58", "194,700 KRW", "Reference news"]
    impl[10:13] = ["20", "201,500 KRW", "Implementation news"]
    tags = ["P"] * len(ref)
    tags[10:13] = ["SPAN", "A", "A"]
    earlier_ref = list(ref)
    earlier_ref[10:13] = ["57", "194,700 KRW", "Earlier reference news"]
    earlier_impl = list(impl)
    earlier_impl[10:13] = [
        "19",
        "201,500 KRW",
        "Earlier implementation news",
    ]
    ref_capture = _slot_variance_capture(
        ref,
        tags=tags,
        initial_indexes={10, 11, 12},
        sample_texts=[earlier_ref, ref],
    )
    impl_capture = _slot_variance_capture(
        impl,
        tags=tags,
        initial_indexes={10, 11, 12},
        sample_texts=[earlier_impl, impl],
    )

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref,
        impl,
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["phaseVariance"]["accepted"] is True
    assert artifact["phaseVariance"]["proof"][0]["kind"] == "dynamic-region"


def test_runtime_text_sequence_accepts_structurally_stable_live_card(
    tmp_path: Path,
) -> None:
    ref = [f"Stable copy {index}" for index in range(20)]
    impl = list(ref)
    ref[10:12] = ["Reference banner", "Reference banner detail"]
    impl[10:12] = ["Implementation banner", "Implementation banner detail"]
    tags = ["P"] * len(ref)
    tags[10:12] = ["DIV", "DIV"]
    tags[9] = tags[12] = "A"
    ref_capture = _slot_variance_capture(ref, tags=tags)
    impl_capture = _slot_variance_capture(impl, tags=tags)

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref,
        impl,
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["phaseVariance"]["proof"][0]["kind"] == "live-card-region"


def test_runtime_text_sequence_rejects_reordered_live_card_copy(
    tmp_path: Path,
) -> None:
    ref = [f"Stable copy {index}" for index in range(20)]
    impl = list(ref)
    ref[10:12] = ["Banner title", "Banner detail"]
    impl[10:12] = ["Banner detail", "Banner title"]
    tags = ["P"] * len(ref)
    tags[10:12] = ["DIV", "DIV"]
    tags[9] = tags[12] = "A"
    ref_capture = _slot_variance_capture(ref, tags=tags)
    impl_capture = _slot_variance_capture(impl, tags=tags)

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref,
        impl,
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["accepted"] is False
    assert "canonical-block-sequence-mismatch" in {
        item["kind"] for item in artifact["violations"]
    }


def test_runtime_text_sequence_accepts_clock_counter_and_progressive_reveal(
    tmp_path: Path,
) -> None:
    ref = [f"Stable copy {index}" for index in range(20)]
    impl = list(ref)
    ref[3:6] = [":", "32", ":"]
    impl[3:6] = [":", "33", ":"]
    ref[10] = "Everyday Tech"
    impl[10] = "Innovation for Everyday Tech"
    tags = ["P"] * len(ref)
    tags[3:6] = ["SPAN", "SPAN", "SPAN"]
    tags[10] = "H5"
    partial_ref = list(ref)
    partial_ref[10] = "Tech"
    ref_capture = _slot_variance_capture(
        ref,
        tags=tags,
        initial_indexes={3, 4, 5},
        sample_texts=[partial_ref, ref],
    )
    impl_capture = _slot_variance_capture(
        impl,
        tags=tags,
        initial_indexes={3, 4, 5},
    )

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref,
        impl,
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert {
        proof["kind"] for proof in artifact["phaseVariance"]["proof"]
    } == {"progressive-reveal", "volatile-counter"}


def _phase_capture(
    blocks: list[str],
    *,
    variant: str | None = None,
    variant_index: int = 10,
    observed_variant: str | None = None,
    observed_index: int = 10,
    phase_absent: bool = False,
    phase_recurrent: bool = False,
    protected: bool = False,
    variant_slot: str = "main:nth-of-type(1)>h4:nth-of-type(1)::run(1)",
) -> dict:
    def record(text: str, slot: str, tag: str = "P") -> dict:
        return {
            "slot": slot,
            "text": text,
            "tag": tag,
            "initialViewport": protected and text == variant,
        }

    stable = [
        record(text, f"main:nth-of-type(1)>p:nth-of-type({index + 1})::run(1)")
        for index, text in enumerate(blocks)
    ]
    records = list(stable)
    if variant is not None:
        records.insert(variant_index, record(variant, variant_slot, "H4"))
    phase_variant = observed_variant
    if phase_variant is None and phase_recurrent:
        phase_variant = variant
    if phase_variant is not None:
        phase_variant_index = (
            observed_index if observed_variant is not None else variant_index
        )
        observed = list(stable)
        observed.insert(
            phase_variant_index, record(phase_variant, variant_slot, "H4")
        )
        samples = [observed, stable, stable, observed, records]
    elif phase_absent:
        samples = [records, stable, stable, records]
    else:
        samples = [records, records]
    return {
        "blocks": [item["text"] for item in records],
        "blockCount": len(records),
        "records": records,
        "samples": samples,
        "phaseSampleStartIndex": 0,
    }


def test_runtime_text_sequence_accepts_bounded_proven_rendered_phase(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, observed_variant="Carousel variant")
    impl_capture = _phase_capture(
        stable,
        variant="Carousel variant",
        phase_recurrent=True,
    )

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["phaseVariance"]["accepted"] is True
    assert artifact["phaseVariance"]["gapCount"] == 1
    proof = artifact["phaseVariance"]["proof"][0]
    assert type(artifact["phaseVariance"]["gapCount"]) is int
    assert type(artifact["phaseVariance"]["referenceSampleCount"]) is int
    assert type(artifact["phaseVariance"]["implementationSampleCount"]) is int
    assert type(proof["gapIndex"]) is int
    assert type(proof["matchedReferenceCandidatePresentSample"]) is int
    assert proof["referenceCyclePolarity"] == "present-absent-present"
    assert type(proof["matchedReferenceCandidateAbsentStartSample"]) is int
    assert proof["matchedReferenceCandidateRecurredSample"] == 3
    assert proof["referenceAbsenceRunLength"] == 2
    assert type(proof["matchedImplementationCandidateSample"]) is int
    assert proof["implementationCyclePolarity"] == "present-absent-present"
    assert type(proof["matchedImplementationCandidateAbsentStartSample"]) is int
    assert proof["matchedImplementationCandidateRecurredSample"] == 3
    assert proof["implementationAbsenceRunLength"] == 2
    assert artifact["violations"] == []


def test_runtime_text_sequence_rejects_static_implementation_phase_waiver(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, observed_variant="Carousel variant")
    impl_capture = _phase_capture(stable, variant="Carousel variant")

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"] == {
        "accepted": False,
        "reason": "implementation-phase-nonrecurrent",
        "gapIndex": 0,
    }


def test_runtime_text_sequence_rejects_absence_before_first_presence(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, observed_variant="Carousel variant")
    impl_capture = _phase_capture(
        stable,
        variant="Carousel variant",
        phase_recurrent=True,
    )
    absent = _phase_capture(stable)["records"]
    present = impl_capture["records"]
    impl_capture["samples"] = [absent, absent, present, present]

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["phaseVariance"] == {
        "accepted": False,
        "reason": "implementation-phase-nonrecurrent",
        "gapIndex": 0,
    }


def test_runtime_text_sequence_accepts_absent_present_absent_cycle(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, observed_variant="Carousel variant")
    impl_capture = _phase_capture(
        stable,
        variant="Carousel variant",
        phase_recurrent=True,
    )
    ref_absent = ref_capture["records"]
    ref_present = ref_capture["samples"][0]
    impl_absent = _phase_capture(stable)["records"]
    impl_present = impl_capture["records"]
    ref_capture["samples"] = [
        ref_absent,
        ref_present,
        ref_absent,
    ]
    impl_capture["samples"] = [
        impl_absent,
        impl_present,
        impl_absent,
        impl_present,
    ]

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    proof = artifact["phaseVariance"]["proof"][0]
    assert proof["referenceCyclePolarity"] == "absent-present-absent"
    assert proof["implementationCyclePolarity"] == "absent-present-absent"
    assert (
        proof["matchedReferenceCandidateAbsentStartSample"],
        proof["matchedReferenceCandidatePresentSample"],
        proof["matchedReferenceCandidateRecurredSample"],
    ) == (0, 1, 2)


def test_runtime_text_sequence_rejects_exact_final_with_missing_phase_copy(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(
        stable,
        observed_variant="Transient canonical copy",
    )
    impl_capture = _phase_capture(stable)

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        stable,
        stable,
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"] == {
        "accepted": False,
        "reason": "exact-match",
    }
    assert "phase-window-text-catalog-mismatch" in {
        item["kind"] for item in artifact["violations"]
    }


def test_runtime_text_sequence_rejects_protected_reference_phase_occurrence(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, observed_variant="Carousel variant")
    ref_capture["samples"][0][10]["initialViewport"] = True
    impl_capture = _phase_capture(stable, variant="Carousel variant")

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["accepted"] is False
    assert artifact["phaseVariance"]["reason"] == "reference-phase-nonrecurrent"


def test_runtime_text_sequence_rejects_protected_implementation_phase_occurrence(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(
        stable, variant="Carousel variant", phase_absent=True
    )
    impl_capture = _phase_capture(
        stable, observed_variant="Carousel variant"
    )
    impl_capture["samples"][0][10]["initialViewport"] = True

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["accepted"] is False


@pytest.mark.parametrize("tamper", ["final-duplicate", "sample-duplicate", "coherence"])
def test_runtime_text_sequence_rejects_capture_evidence_integrity_errors(
    tmp_path: Path,
    tamper: str,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, observed_variant="Carousel variant")
    impl_capture = _phase_capture(stable, variant="Carousel variant")
    if tamper == "final-duplicate":
        impl_capture["records"][10]["slot"] = impl_capture["records"][9]["slot"]
    elif tamper == "sample-duplicate":
        ref_capture["samples"][0][10]["slot"] = (
            ref_capture["samples"][0][9]["slot"]
        )
    else:
        ref_capture["samples"][-1] = ref_capture["samples"][0]

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 2
    assert artifact["status"] == "error"


@pytest.mark.parametrize(
    ("candidate_side", "index"),
    [("impl", 0), ("impl", 20), ("ref", 0), ("ref", 20)],
)
def test_runtime_text_sequence_accepts_anchored_boundary_phase(
    tmp_path: Path,
    candidate_side: str,
    index: int,
) -> None:
    stable = [f"Stable copy {item}" for item in range(20)]
    if candidate_side == "impl":
        ref_capture = _phase_capture(
            stable,
            observed_variant="Boundary variant",
            observed_index=index,
        )
        impl_capture = _phase_capture(
            stable,
            variant="Boundary variant",
            variant_index=index,
            phase_recurrent=True,
        )
    else:
        ref_capture = _phase_capture(
            stable,
            variant="Boundary variant",
            variant_index=index,
            phase_absent=True,
        )
        impl_capture = _phase_capture(
            stable,
            observed_variant="Boundary variant",
            observed_index=index,
        )

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    proof = artifact["phaseVariance"]["proof"][0]
    assert proof["candidateSide"] == candidate_side
    assert (proof["beforeSlot"] is None) == (index == 0)
    assert (proof["afterSlot"] is None) == (index == len(stable))


def test_runtime_text_sequence_rejects_candidate_superset_with_sibling_catalog(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]

    def records(prefix: str, texts: list[str]) -> list[dict]:
        return [
            {
                "slot": f"{prefix}:slot:{index}",
                "text": text,
                "tag": "P",
                "initialViewport": False,
            }
            for index, text in enumerate(texts)
        ]

    impl_texts = [*stable[:10], "Carousel variant", *stable[10:]]
    impl_records = records("impl-phase", impl_texts)
    impl_records[10]["tag"] = "H4"
    observed = json.loads(json.dumps(impl_records))
    observed[11:11] = [
        {
            "slot": "impl-phase:catalog:slot:1",
            "text": "Sibling carousel title",
            "tag": "H4",
            "initialViewport": False,
        },
        {
            "slot": "impl-phase:catalog:slot:2",
            "text": "Sibling carousel detail",
            "tag": "LI",
            "initialViewport": False,
        },
    ]
    empty_observed = json.loads(json.dumps(impl_records))
    empty_observed.pop(10)
    ref_capture = {
        "blocks": stable,
        "blockCount": len(stable),
        "records": empty_observed,
        "samples": [observed, empty_observed, empty_observed],
        "phaseSampleStartIndex": 1,
    }
    impl_capture = {
        "blocks": impl_texts,
        "blockCount": len(impl_texts),
        "records": impl_records,
        "samples": [impl_records, impl_records],
        "phaseSampleStartIndex": 0,
    }

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        stable,
        impl_texts,
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["reason"] == "reference-phase-nonrecurrent"


def test_runtime_text_sequence_accepts_proven_reverse_rendered_phase(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(
        stable, variant="Carousel variant", phase_absent=True
    )
    impl_capture = _phase_capture(
        stable, observed_variant="Carousel variant"
    )

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["phaseVariance"]["accepted"] is True
    proof = artifact["phaseVariance"]["proof"][0]
    assert proof["matchedImplementationCandidateSample"] == 0
    assert proof["matchedReferenceCandidatePresentSample"] == 0
    assert proof["matchedReferenceCandidateAbsentStartSample"] == 1
    assert proof["referenceAbsenceRunLength"] == 2
    assert proof["candidate"]["text"] == "Carousel variant"
    assert artifact["violations"] == []


def test_runtime_text_sequence_rejects_static_reverse_copy_waiver(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, variant="Static reference copy")
    impl_capture = _phase_capture(
        stable, observed_variant="Static reference copy"
    )

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["reason"] == "reference-phase-nonrecurrent"


@pytest.mark.parametrize("sequence", ["a-b", "empty-a"])
def test_runtime_text_sequence_rejects_nonrecurrent_reverse_phase(
    tmp_path: Path,
    sequence: str,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(
        stable, variant="Reference variant", phase_absent=True
    )
    impl_capture = _phase_capture(
        stable, observed_variant="Reference variant"
    )
    if sequence == "a-b":
        variant_b = json.loads(json.dumps(ref_capture["records"]))
        variant_b[10]["text"] = "Different reference variant"
        ref_capture["samples"] = [
            ref_capture["records"],
            variant_b,
            ref_capture["records"],
        ]
    else:
        ref_capture["samples"] = [
            _phase_capture(stable)["records"],
            ref_capture["records"],
        ]
        ref_capture["phaseSampleStartIndex"] = 0

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["reason"] == "reference-phase-nonrecurrent"


def test_runtime_text_sequence_rejects_same_slot_in_wrong_anchor_gap(
    tmp_path: Path,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(
        stable,
        observed_variant="Carousel variant",
        observed_index=15,
    )
    impl_capture = _phase_capture(stable, variant="Carousel variant")

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["reason"] == "reference-phase-nonrecurrent"


@pytest.mark.parametrize(
    ("variant", "observed", "protected", "slot"),
    [
        ("Unknown variant", "Known variant", False, "main>h4::run(1)"),
        ("Known variant", None, False, "main>h4::run(1)"),
        ("Known variant", "Known variant", True, "main>h4::run(1)"),
        ("Known variant", "Known variant", False, "footer>h4::run(1)"),
        ("Known variant", "Known variant", False, "main>h4::run(2)"),
    ],
)
def test_runtime_text_sequence_rejects_unproven_or_protected_phase(
    tmp_path: Path,
    variant: str,
    observed: str | None,
    protected: bool,
    slot: str,
) -> None:
    stable = [f"Stable copy {index}" for index in range(20)]
    ref_capture = _phase_capture(stable, observed_variant=observed)
    impl_capture = _phase_capture(
        stable,
        variant=variant,
        protected=protected,
        variant_slot=slot,
    )

    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_capture["blocks"],
        impl_capture["blocks"],
        ref_capture=ref_capture,
        impl_capture=impl_capture,
    )

    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["phaseVariance"]["accepted"] is False
    assert "canonical-block-sequence-mismatch" in {
        item["kind"] for item in artifact["violations"]
    }


@pytest.mark.parametrize(
    ("ref_blocks", "impl_blocks", "expected_kind"),
    [
        ([], ["Implementation copy"], "empty-ref-capture"),
        (["Reference copy"], [], "empty-impl-capture"),
    ],
)
def test_runtime_text_sequence_empty_capture_is_infrastructure_error(
    tmp_path: Path,
    ref_blocks: list[str],
    impl_blocks: list[str],
    expected_kind: str,
) -> None:
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ref_blocks,
        impl_blocks,
    )

    assert proc.returncode == 2, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "error"
    assert expected_kind in {item["kind"] for item in artifact["violations"]}


def test_runtime_text_sequence_closes_stale_session_before_failed_open(
    tmp_path: Path,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Stale reference"],
        ["Stale reference"],
        stub_env={"OPEN_FAIL_STALE": "1"},
    )

    assert proc.returncode == 2, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "error"
    assert "ref-browser-failed" in {
        item["kind"] for item in artifact["violations"]
    }
    ref_batches = [
        line
        for line in log.splitlines()
        if line.startswith("--session text-seq-text-ref-capture-")
        and " batch --json --bail" in line
    ]
    assert len(ref_batches) == 3
    assert "batch result contained 1/6 command results" in proc.stderr
    violation = next(
        item
        for item in artifact["violations"]
        if item["kind"] == "ref-browser-failed"
    )
    assert violation["attempts"] == 3


def test_runtime_text_sequence_tolerates_open_timeout_with_fresh_href(
    tmp_path: Path,
) -> None:
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Loaded reference"],
        ["Loaded reference"],
        stub_env={"OPEN_NONZERO_VALID": "1"},
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"


def test_runtime_text_sequence_accepts_agent_browser_results_wrapper(
    tmp_path: Path,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        stub_env={"FIRST_BATCH_WRAPPED_SESSION": "text-seq-text-ref"},
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["captureReceipt"]["ref"]["attempt"] == 1
    assert artifact["captureReceipt"]["ref"]["retryCount"] == 0
    assert "batch result object did not contain a results list" not in proc.stderr
    assert sum(
        line.startswith("--session text-seq-text-ref-capture-")
        and " batch --json --bail" in line
        for line in log.splitlines()
    ) == 1


@pytest.mark.parametrize(
    "long_session",
    [
        (
            "dogfood-docs-canonical-final6-20260730-394746N-"
            "runtime-text-sequence"
        ),
        "한글세션" * 4,
    ],
)
def test_runtime_text_sequence_shortens_long_dispatcher_session_names(
    tmp_path: Path,
    long_session: str,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        session=long_session,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    ref_batches = [
        line for line in log.splitlines()
        if line.startswith("--session rtseq-ref-") and " batch --json --bail" in line
    ]
    impl_batches = [
        line for line in log.splitlines()
        if line.startswith("--session rtseq-impl-") and " batch --json --bail" in line
    ]
    assert len(ref_batches) == 1
    assert len(impl_batches) == 1
    assert all(
        len(line.split()[1].encode("utf-8")) <= 32
        for line in ref_batches + impl_batches
    )
    assert long_session not in " ".join(ref_batches + impl_batches)


def test_runtime_text_sequence_surfaces_top_level_agent_browser_error(
    tmp_path: Path,
) -> None:
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        stub_env={"FIRST_BATCH_TOP_ERROR_SESSION": "text-seq-text-ref"},
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["captureReceipt"]["ref"]["attempt"] == 2
    assert "agent-browser batch error: Session name" in proc.stderr
    assert "Socket path would be 126 bytes" in proc.stderr


@pytest.mark.parametrize("capture_side", ["ref", "impl"])
def test_runtime_text_sequence_retries_incomplete_side_on_fresh_session(
    tmp_path: Path,
    capture_side: str,
) -> None:
    failed_session_prefix = f"text-seq-text-{capture_side}"
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        stub_env={"FIRST_BATCH_INCOMPLETE_SESSION": failed_session_prefix},
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    other_side = "impl" if capture_side == "ref" else "ref"
    assert artifact["captureReceipt"][capture_side]["attempt"] == 2
    assert artifact["captureReceipt"][capture_side]["retryCount"] == 1
    assert artifact["captureReceipt"][other_side]["attempt"] == 1
    assert artifact["captureReceipt"][other_side]["retryCount"] == 0
    assert "batch result contained 5/6 command results" in proc.stderr
    assert "attempt 1/3; retrying with a fresh session" in proc.stderr
    failed_side_lines = [
        line
        for line in log.splitlines()
        if line.startswith(f"--session {failed_session_prefix}-capture-")
    ]
    failed_side_batches = [
        line
        for line in failed_side_lines
        if " batch --json --bail" in line
    ]
    other_side_batches = [
        line
        for line in log.splitlines()
        if line.startswith(f"--session text-seq-text-{other_side}-capture-")
        and " batch --json --bail" in line
    ]
    assert len(failed_side_batches) == 2
    assert len(other_side_batches) == 1
    attempt_sessions = [line.split()[1] for line in failed_side_batches]
    assert len(set(attempt_sessions)) == 2
    capture_sessions = {
        line.split()[1]
        for line in log.splitlines()
        if line.startswith("--session text-seq-text-")
        and "-capture-" in line
    }
    for capture_session in capture_sessions:
        close_lines = [
            line
            for line in log.splitlines()
            if line == f"--session {capture_session} close"
        ]
        assert len(close_lines) == 1


@pytest.mark.parametrize(
        ("env_name", "diagnostic"),
        [
            ("FIRST_BATCH_OVERSIZED_SESSION", "batch result contained 7/6"),
            (
                "FIRST_BATCH_OBJECT_SESSION",
                "batch result object did not contain a results list",
            ),
            ("FIRST_BATCH_MALFORMED_SESSION", "unparseable batch JSON"),
        ],
    )
def test_runtime_text_sequence_retries_malformed_batch_on_fresh_session(
    tmp_path: Path,
    env_name: str,
    diagnostic: str,
) -> None:
    proc, artifact, _ = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        stub_env={env_name: "text-seq-text-ref"},
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["captureReceipt"]["ref"]["attempt"] == 2
    assert artifact["captureReceipt"]["ref"]["retryCount"] == 1
    assert diagnostic in proc.stderr


def test_runtime_text_sequence_preserves_failed_ref_close_for_exit_cleanup(
    tmp_path: Path,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference copy"],
        ["Reference copy"],
        stub_env={"CLOSE_ALWAYS_FAIL_SESSION": "text-seq-text-ref"},
    )

    assert proc.returncode != 0
    assert artifact["status"] == "error"
    lines = log.splitlines()
    ref_close_lines = [
        line
        for line in lines
        if line.startswith("--session text-seq-text-ref-capture-")
        and line.endswith(" close")
    ]
    assert len(ref_close_lines) == 4
    assert not any("text-seq-text-impl-capture-" in line for line in lines)


def test_runtime_text_sequence_retries_active_ref_close_without_ghost(
    tmp_path: Path,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference copy"],
        ["Reference copy"],
        stub_env={"CLOSE_FAIL_ONCE_SESSION": "text-seq-text-ref"},
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "pass"
    assert artifact["captureReceipt"]["ref"]["closeAttempts"] == 2
    assert artifact["captureReceipt"]["impl"]["closeAttempts"] == 1
    lines = log.splitlines()
    ref_close_lines = [
        line
        for line in lines
        if line.startswith("--session text-seq-text-ref-capture-")
        and line.endswith(" close")
    ]
    assert len(ref_close_lines) == 2


def test_runtime_text_sequence_eval_failure_is_infrastructure_error(
    tmp_path: Path,
) -> None:
    proc, artifact, log = _run_runtime_text_sequence_with_stub(
        tmp_path,
        ["Reference"],
        ["Reference"],
        stub_env={"EVAL_FAIL_SESSION": "text-seq-text-impl"},
    )

    assert proc.returncode == 2, f"{proc.stdout}\n{proc.stderr}"
    assert artifact["status"] == "error"
    assert "impl-browser-failed" in {
        item["kind"] for item in artifact["violations"]
    }
    impl_violation = next(
        item
        for item in artifact["violations"]
        if item["kind"] == "impl-browser-failed"
    )
    assert impl_violation["attempts"] == 1
    impl_batches = [
        line
        for line in log.splitlines()
        if line.startswith("--session text-seq-text-impl-capture-")
        and " batch --json --bail" in line
    ]
    assert len(impl_batches) == 1


def test_runtime_dom_parity_node_count_excludes_non_rendering_tags() -> None:
    """Node-count parity must measure rendered structure only. A Next.js
    production build emits one <script> per route chunk INSIDE <body>
    (286 observed on realfood-v4-harness vs 9 on the ref), which pushed the
    ratio to 1.335 and failed the ±30% tolerance even though every rendered
    element matched. The in-page analysis JS must filter SCRIPT/STYLE/
    NOSCRIPT/TEMPLATE/META/LINK out of the counted set.
    """
    script = (
        _project_root() / "skills" / "visual-debug" / "scripts"
        / "runtime-dom-parity-check.sh"
    )
    src = script.read_text(encoding="utf-8")
    assert 'const allNodes = root.querySelectorAll("*");' not in src, (
        "nodeCount must not count every body descendant — bundler chunk "
        "<script> tags are not rendered structure"
    )
    assert "skipText.has(el.tagName)" in src, (
        "nodeCount filter must reuse the non-rendering tag set (skipText)"
    )
