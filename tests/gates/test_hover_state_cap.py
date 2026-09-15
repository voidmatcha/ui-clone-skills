"""Regression: MAX_HOVER_TARGETS must not drop transition-spec hover obligations.

hover-state-compare builds its target list from hover-css-rules.json
activations in rule order, dedups, and caps at MAX_HOVER_TARGETS (default 5).
On navercorp.com/tech/innovation the deduped activation order puts
`.header .nav__link` at index 8, so the cap dropped it before
`affected_selector_for_hover` ever ran, and the `affectedTargetAbsent`
handling added for that rule was inert: `.header .nav__link:hover
{font-weight:600}` was never compared. Four of the nine live-capture promoted
spec entries were dropped the same way, the result file did not say so, and
the fallback probe does not plan a `font-weight` channel, so nothing caught it.

A hover entry of a non-placeholder transition-spec.json is an obligation the
implementation must reproduce, not a speculative candidate; the cap must never
drop one. The cap still bounds the speculative pool, and what it drops must be
visible in the result file.

Every test drives the real script end to end (stubbed browser and inner
compare) and asserts which selectors it scheduled, not what the script's text
says.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"


def _fixture_ref() -> Path:
    """tmp/ref is untracked and lives in the main checkout; a linked worktree
    shares it through the common git dir."""
    local = ROOT / "tmp" / "ref" / "navercorp-tech-innovation"
    if local.is_dir():
        return local
    try:
        common = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return local
    common_dir = Path(common) if os.path.isabs(common) else ROOT / common
    return common_dir.resolve().parent / "tmp" / "ref" / "navercorp-tech-innovation"


FIXTURE_REF = _fixture_ref()

NAV_LINK = ".header .nav__link"
NAV_LINK_DESCENDANT = ".header .nav__link .en"
LIVE_CAPTURE_BRANCH = "live-capture: agent-browser CDP hover plus DOM hover events"

# The pre-fix gate scheduled exactly these five on the fixture (first five
# deduped hover-css-rules activations). The fix must keep every one of them.
PRISTINE_FIVE = [
    "a",
    ".navercorp .card-contents .card-contents__item-inner",
    ".header.thema-black .nav__link2",
    ".header.thema-black .btn-selected",
    ".header.thema-black .btn-lang__list button",
]

# Deduped activation order of the fixture's hover-css-rules.json, reproduced
# inline so the ordering defect is exercised on hosts without tmp/ref.
FIXTURE_ACTIVATION_ORDER = [
    "a",
    ".navercorp .card-contents .card-contents__item-inner",
    ".header.thema-black .nav__link2",
    ".header.thema-black .btn-selected",
    ".header.thema-black .btn-lang__list button",
    '.header.thema-black .btn-lang button[class^="btn-"]',
    ".header.thema-black .search-tab__box",
    ".header .nav__item.is-arrow .nav__link",
    NAV_LINK,
    ".header .nav__intro__link",
    ".footer__menu .menu__link2",
]

# The fixture's nine promoted live-capture spec targets, in spec order.
FIXTURE_SPEC_TARGETS = [
    ".navercorp .card-contents .card-contents__item-inner",
    ".header.thema-black .nav__link2",
    ".header.thema-black .btn-selected",
    ".header.thema-black .btn-lang__list button",
    '.header.thema-black .btn-lang button[class^="btn-"]',
    ".header.thema-black .search-tab__box",
    ".header .nav__item.is-arrow .nav__link",
    ".header .nav__depth2 .text__underline .nav__link2",
    ".header .nav__link2",
]


def _hover_css_for(activations: list[str]) -> dict:
    rules = []
    for activation in activations:
        rules.append({"selector": f"{activation}:hover", "activation": activation,
                      "affected": activation})
        if activation == NAV_LINK:
            rules.append({"selector": f"{activation}:hover .en", "activation": activation,
                          "affected": NAV_LINK_DESCENDANT})
    return {"rules": rules}


def _live_spec(targets: list[str], *, placeholder: bool = False, source: str | None = None) -> dict:
    transitions = []
    for index, target in enumerate(targets):
        entry = {
            "id": f"{index:02d}-auto-hover-{index + 1}",
            "target": target,
            "trigger": "hover",
            "bundle_branch": LIVE_CAPTURE_BRANCH,
            "animation": {"type": "transition", "property": "font-weight"},
        }
        if target == NAV_LINK:
            entry["affectedTargetAbsent"] = NAV_LINK_DESCENDANT
        transitions.append(entry)
    spec = {
        "schemaVersion": 2,
        "source": source or "scripts/extract/capture-region-artifacts.py",
        "placeholder": placeholder,
        "provenance": {"kind": "live-capture", "url": "https://ref.example"},
        "transitions": transitions,
    }
    if placeholder:
        spec.pop("provenance")
    return spec


def _nav_link_absent_entry() -> dict:
    """The entry the bridge writes for `.header .nav__link` once it observes
    the activation in its own right (descendant `.en` rendered nowhere)."""
    return {
        "id": "09-auto-hover-12",
        "target": NAV_LINK,
        "trigger": "hover",
        "affectedTargetAbsent": NAV_LINK_DESCENDANT,
        "bundle_branch": LIVE_CAPTURE_BRANCH,
        "source_chunk": "css/0.CQ3BoULZ.css",
        "animation": {"type": "transition", "property": "font-weight"},
    }


def _run_gate(tmp_path: Path, ref: Path, *, max_targets: int | None = None) -> tuple[list[str], str, int]:
    """Run hover-state-compare.sh against `ref` with a stubbed browser and a
    stubbed inner compare; return (scheduled hover targets in order, result
    file text, exit code)."""
    plugin_root = tmp_path / "plugin"
    verify = plugin_root / "scripts" / "verify"
    verify.mkdir(parents=True, exist_ok=True)
    invocations = tmp_path / "compare-invocations.txt"
    invocations.write_text("", encoding="utf-8")
    compare = verify / "video-transition-compare.sh"
    compare.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$5" >> "$HOVER_STUB_INVOCATIONS"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    compare.chmod(0o755)
    cleanup = verify / "cleanup-sessions.sh"
    cleanup.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    cleanup.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    # No browser on this host: `open` fails, so the presence pre-filter keeps
    # every target and the fallback probe cannot run — the schedule is the
    # thing under test.
    (fake_bin / "agent-browser").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "agent-browser").chmod(0o755)
    (fake_bin / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "sleep").chmod(0o755)
    temp_area = tmp_path / "tmp"
    temp_area.mkdir(exist_ok=True)

    env = {k: v for k, v in os.environ.items() if k not in {"VIEWPORTS", "MAX_HOVER_TARGETS"}}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "HOVER_STUB_INVOCATIONS": str(invocations),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(temp_area),
    })
    if max_targets is not None:
        env["MAX_HOVER_TARGETS"] = str(max_targets)
    proc = subprocess.run(
        ["bash", str(SCRIPT), "https://ref.example", "https://impl.example", "cap-test", str(ref)],
        capture_output=True, text=True, timeout=180, env=env,
    )
    result_path = ref / "transitions" / "hover-state-result.txt"
    result = result_path.read_text(encoding="utf-8") if result_path.exists() else ""
    scheduled = []
    for line in invocations.read_text(encoding="utf-8").splitlines():
        prefix, _, selector = line.partition(":")
        assert prefix in {"hover", "hover-and-out"}, line
        scheduled.append(selector)
    return scheduled, result, proc.returncode


def _write_ref(tmp_path: Path, hover_css: dict, spec: dict | None) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir(exist_ok=True)
    (ref / "hover-css-rules.json").write_text(json.dumps(hover_css), encoding="utf-8")
    (ref / "regions.json").write_text(json.dumps({"regions": []}), encoding="utf-8")
    if spec is not None:
        (ref / "transition-spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return ref


@pytest.mark.skipif(
    not (FIXTURE_REF / "hover-css-rules.json").is_file()
    or not (FIXTURE_REF / "transition-spec.json").is_file(),
    reason="tmp/ref/navercorp-tech-innovation capture is not present on this host (tmp/ is untracked)",
)
def test_real_fixture_nav_link_survives_the_cap(tmp_path: Path) -> None:
    """Against the real navercorp-tech-innovation hover-css-rules.json,
    `.header .nav__link` must be in the capped target list.

    The fixture's transition-spec.json carries no `.header .nav__link` entry
    (the pre-fix bridge retired it); the entry appended here is the one the
    fixed bridge writes on re-capture, so the whole chain — cap, then
    `affected_selector_for_hover` honouring `affectedTargetAbsent` — runs on
    the real rule inventory. A live re-capture is still needed to prove the
    bridge half end to end.
    """
    hover_css = json.loads((FIXTURE_REF / "hover-css-rules.json").read_text(encoding="utf-8"))
    spec = json.loads((FIXTURE_REF / "transition-spec.json").read_text(encoding="utf-8"))
    spec_targets = [
        t["target"] for t in spec["transitions"]
        if isinstance(t, dict) and "hover" in str(t.get("trigger") or "").lower()
    ]
    assert len(spec_targets) > 5, "fixture no longer exercises the cap; pick another"
    assert NAV_LINK not in spec_targets, (
        "the fixture now carries a .header .nav__link entry — the bridge half was "
        "re-captured; drop the synthetic append below"
    )
    spec["transitions"].append(_nav_link_absent_entry())
    ref = _write_ref(tmp_path, hover_css, spec)

    scheduled, result, _code = _run_gate(tmp_path, ref)

    assert NAV_LINK in scheduled, f"cap dropped {NAV_LINK}: scheduled={scheduled}"
    # Every promoted spec obligation is scheduled — none truncated.
    missing = [t for t in spec_targets if t not in scheduled]
    assert not missing, f"spec obligations dropped by the cap: {missing}"
    # Never looser than pristine: its five are all still measured.
    assert all(sel in scheduled for sel in PRISTINE_FIVE), scheduled
    # The chain ran: the affectedTargetAbsent entry is measured as the
    # activation alone, not as the unrendered `.en` descendant.
    heading = result.index(f"selector: {NAV_LINK}\n")
    block = result[heading: result.index("\n\n", heading)]
    assert "affected-selector:" not in block, block
    # The truncation is on the record, by selector.
    assert "# cap: MAX_HOVER_TARGETS=5" in result
    assert ".footer__menu .menu__link2" in result.split("# cap:", 1)[1].splitlines()[0]


def test_spec_obligations_are_exempt_from_the_cap(tmp_path: Path) -> None:
    """Inline reproduction of the fixture ordering: nine promoted spec entries
    plus `.header .nav__link` (affectedTargetAbsent) against a cap of 5. All
    ten must be scheduled, and pristine's five must still be scheduled."""
    spec = _live_spec(FIXTURE_SPEC_TARGETS)
    spec["transitions"].append(_nav_link_absent_entry())
    ref = _write_ref(tmp_path, _hover_css_for(FIXTURE_ACTIVATION_ORDER), spec)

    scheduled, result, _code = _run_gate(tmp_path, ref)

    assert NAV_LINK in scheduled, scheduled
    for target in FIXTURE_SPEC_TARGETS:
        assert target in scheduled, f"obligation {target} dropped; scheduled={scheduled}"
    assert all(sel in scheduled for sel in PRISTINE_FIVE), scheduled
    assert len(scheduled) == len(set(scheduled)), "a target was scheduled twice"
    assert "# spec obligations: 10 hover target(s)" in result
    heading = result.index(f"selector: {NAV_LINK}\n")
    block = result[heading: result.index("\n\n", heading)]
    assert "affected-selector:" not in block, block


def test_cap_truncation_is_reported_by_selector(tmp_path: Path) -> None:
    """With no spec, the cap keeps the first five activations exactly as
    before (pristine parity) and names every dropped selector in the result
    file — a truncation is never silent."""
    ref = _write_ref(tmp_path, _hover_css_for(FIXTURE_ACTIVATION_ORDER), spec=None)

    scheduled, result, _code = _run_gate(tmp_path, ref)

    assert scheduled == PRISTINE_FIVE, scheduled
    dropped = FIXTURE_ACTIVATION_ORDER[5:]
    cap_lines = [line for line in result.splitlines() if line.startswith("# cap: MAX_HOVER_TARGETS=5")]
    assert len(cap_lines) == 1, result
    for selector in dropped:
        assert selector in cap_lines[0], f"{selector} dropped without being reported: {cap_lines[0]}"
    assert f"kept 5 of {len(FIXTURE_ACTIVATION_ORDER)}" in cap_lines[0]


def test_placeholder_spec_does_not_bypass_the_cap(tmp_path: Path) -> None:
    """Control (passes before and after the fix): an extraction auto-stub
    spec carries no obligation, so its hover entries stay in the speculative
    pool and the cap still bounds the run."""
    spec = _live_spec(FIXTURE_SPEC_TARGETS, placeholder=True, source="ui_clone.extraction_artifacts")
    ref = _write_ref(tmp_path, _hover_css_for(FIXTURE_ACTIVATION_ORDER), spec)

    scheduled, result, _code = _run_gate(tmp_path, ref)

    assert scheduled == PRISTINE_FIVE, scheduled
    assert "# spec obligations:" not in result


def test_spec_obligations_do_not_suppress_the_no_resolvable_target_fail(tmp_path: Path) -> None:
    """hasHover=true + nothing resolvable + a real transition-spec.json → FAIL.

    The cap exemption injects spec-derived rows into the target file, and the
    `no hover targets resolvable` hard FAIL keys on that same file. A non-empty
    non-placeholder transition-spec.json therefore silently satisfied a check
    it was never meant to satisfy: regions.json carried no triggerType, there
    were no hover-css-rules / hover-candidates / states-hover artifacts, yet
    the gate reported ✅ exit 0 — a gate certifying success having measured
    nothing it was scheduled to measure. Pristine f7636fc hard-FAILs this ref
    dir; so must this one.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1440, "height": 20133}]
    }), encoding="utf-8")
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "signals": {"hasHover": True},
    }), encoding="utf-8")
    (ref / "transition-spec.json").write_text(
        json.dumps(_live_spec(FIXTURE_SPEC_TARGETS)), encoding="utf-8"
    )

    scheduled, result, code = _run_gate(tmp_path, ref)

    assert code == 1, f"expected hard FAIL, got exit {code}\n{result}"
    assert "❌" in result, result
    assert "UNVERIFIED" in result, result
    assert scheduled == [], scheduled
