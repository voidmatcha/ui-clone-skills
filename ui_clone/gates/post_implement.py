"""Post-Implement gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def _check_generation_completeness(self: Gate) -> list[CheckResult]:
    """Reject components with empty function bodies / no JSX return.

    audit incident (2026-05-19): agent produced 11 component functions in a
    single `App.tsx`; some had real bodies, but the gaming pattern is
    easy — write the signature, skip the body, let section-compare's
    STRUCTURAL_ONLY rows mark it as 'good enough'. This static check
    catches the trivial stub case before visual evidence is consulted.

    Heuristic (simple + low false-positive):
      - For each `function NAME()` or `const NAME = ()` where NAME is
        CapitalizedIdentifier (React component convention), find its
        closing brace.
      - If the body contains zero `<` characters between the opening
        and closing brace AND zero `return ` statement → stub.

    Inline-App layout (audit incident) and separate-component-files layout
    (audit incident) both pass when their components have real bodies.
    """
    results: list[CheckResult] = []
    impl_root = self._find_impl_root()
    if impl_root is None:
        return results
    src_dir = impl_root / "src"
    if not src_dir.is_dir():
        return results
    stubs: list[str] = []
    for tsx in src_dir.rglob("*.tsx"):
        try:
            text = tsx.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Match either `function NAME(` or `const NAME = (` where NAME starts uppercase.
        for m in re.finditer(
            r"(?:^|\n)(?:export\s+)?(?:function\s+([A-Z][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{|"
            r"const\s+([A-Z][A-Za-z0-9_]*)\s*=\s*\([^)]*\)\s*=>\s*\{)",
            text,
        ):
            name = m.group(1) or m.group(2)
            start = m.end()
            # Walk balanced braces to find the matching closer.
            depth = 1
            i = start
            while i < len(text) and depth > 0:
                ch = text[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                i += 1
            body = text[start:i]
            # A component body must contain either a JSX tag or `return (` at minimum.
            if "<" not in body and "return " not in body:
                rel = tsx.relative_to(impl_root)
                stubs.append(f"{rel}:{name}")
    if stubs:
        preview = ", ".join(stubs[:5])
        more = f" (+{len(stubs) - 5} more)" if len(stubs) > 5 else ""
        results.append(
            CheckResult(
                "generation-completeness",
                "fail",
                f"❌ {len(stubs)} component(s) are stubs (empty body, no "
                f"JSX, no return): {preview}{more}",
                fix="Fill bodies or remove unused stubs before re-running post-implement.",
            )
        )
    else:
        results.append(
            CheckResult(
                "generation-completeness",
                "pass",
                "✓ No stub components found in impl/src/**/*.tsx.",
            )
        )
    return results


def _check_componentization(self: Gate) -> list[CheckResult]:
    """Fail when `impl/src/app/page.tsx` > 200 LOC AND `impl/src/components/`
    has < 3 files. The c9b638d benchmark showed the agent could pass every
    other check while shipping a 214-line monolithic page.tsx with no
    per-section components. Splitting first localizes future fixes.

    Skipped silently when:
      - impl/ can't be located (regular tmp/ref/ flow with no co-located impl),
      - page.tsx doesn't exist yet (pre-generation pipelines),
      - page.tsx exists but is small (≤ 200 LOC).
    """
    impl_root = self._find_impl_root()
    if impl_root is None:
        return []
    page = impl_root / "src" / "app" / "page.tsx"
    if not page.is_file():
        return []
    try:
        page_loc = sum(1 for _ in page.open(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    if page_loc <= 200:
        return []
    components_dir = impl_root / "src" / "components"
    if components_dir.is_dir():
        try:
            tsx_files = [
                p for p in components_dir.rglob("*.tsx")
                if p.is_file() and not p.name.startswith(".")
            ]
        except OSError:
            tsx_files = []
    else:
        tsx_files = []
    if len(tsx_files) >= 3:
        return []
    return [
        CheckResult(
            "componentization",
            "fail",
            f"impl/src/app/page.tsx is {page_loc} LOC and impl/src/components/ "
            f"has {len(tsx_files)} .tsx file(s) — site is monolithic. The "
            "benchmark surfaced this exact pattern (c9b638d: 214 LOC / 0 "
            "components) producing a wall of inline sections that resists "
            "targeted markup fixes. Split each ref section into its own "
            "file under src/components/ and import them from page.tsx.",
            fix=(
                "Move each ref section (Hero, StateOfHealth, …) into "
                "impl/src/components/<Section>.tsx and render via "
                "`<Section />` in page.tsx. Target ≥ 3 component files and "
                "page.tsx ≤ 200 lines (mostly imports + layout glue)."
            ),
        )
    ]


def _find_impl_root(self: Gate) -> Path | None:
    """Locate the impl/ root co-located with this ref_dir.

    Delegates to `scripts/extract/find-impl-root.sh` so this gate and all
    shell-side checks (bundle-impl-coverage, transition-spec-coverage,
    verify-loop) share one resolver. audit incident surfaced a split-brain risk
    where a Python and a shell heuristic could diverge — passing one
    while failing the other was a real escape vector. A single canonical
    implementation closes that gap (Codex audit issue 1).

    Returns the impl ROOT (containing src/ and public/), not impl/public/.
    None when the resolver exits non-zero. Stdout shape from resolver
    (3 lines): impl_root, impl_src, impl_package_json — we use line 1.
    """
    env_root = os.environ.get("PLUGIN_ROOT") or os.environ.get(
        "CLAUDE_PLUGIN_ROOT"
    )
    resolver: Path | None = None
    if env_root:
        cand = Path(env_root) / "scripts" / "extract" / "find-impl-root.sh"
        if cand.is_file():
            resolver = cand
    if resolver is None:
        # Walk up from this file so in-repo tests work without env vars.
        here = Path(__file__).resolve()
        for parent in here.parents:
            cand = parent / "scripts" / "extract" / "find-impl-root.sh"
            if cand.is_file():
                resolver = cand
                break
    if resolver is None:
        return None
    try:
        proc = subprocess.run(
            ["bash", str(resolver), str(self.ref_dir)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            p = Path(line)
            if p.is_dir():
                return p
    return None


def _check_sections_result_health(self: Gate) -> CheckResult | None:
    """Fail post-implement when sections/result.txt is missing or dirty.

    Loop-23 finding: auxiliary gates (asset-transfer, hydration,
    image-fidelity, bundle-impl-coverage) reported `pass` while the
    canonical visual-diff result was 0 PASS / 12 FAIL / 3 SKIP — i.e. the
    clone was visually broken but the gate let it through. section-compare.sh
    already exit-1's on FAIL_COUNT > 0, but gate_post_implement never read
    its output. This adds the aggregate read.

    Loop-30 finding: Codex runs could skip section-compare entirely, leaving
    no result.txt for this aggregate check to read. Treat missing/unparseable
    canonical visual evidence as a post-implement failure.
    """
    result_path = self.ref_dir / "sections" / "result.txt"
    if not result_path.is_file():
        return CheckResult(
            label="sections/result.txt visual health",
            status="fail",
            message=(
                "sections/result.txt — MISSING. post-implement cannot pass "
                "until section-compare has produced canonical visual evidence."
            ),
            fix=(
                "bash $PLUGIN_ROOT/skills/visual-debug/scripts/section-compare.sh "
                f"<orig-url> <impl-url> <session> {self.ref_dir}"
            ),
        )
    try:
        text = result_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return CheckResult(
            label="sections/result.txt visual health",
            status="fail",
            message=f"sections/result.txt — unreadable ({e}). Re-run section-compare.",
            fix=(
                "bash $PLUGIN_ROOT/skills/visual-debug/scripts/section-compare.sh "
                f"<orig-url> <impl-url> <session> {self.ref_dir}"
            ),
        )
    # Footer format produced by section-compare.sh:
    # "**Result: <P> PASS, <F> FAIL, <S> SKIP, <Q> STRUCTURAL_ONLY**"
    m = re.search(r"Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL", text)
    if not m:
        return CheckResult(
            label="sections/result.txt visual health",
            status="fail",
            message=(
                "sections/result.txt — missing parseable Result footer. "
                "post-implement cannot aggregate visual pass/fail evidence."
            ),
            fix=(
                "bash $PLUGIN_ROOT/skills/visual-debug/scripts/section-compare.sh "
                f"<orig-url> <impl-url> <session> {self.ref_dir}"
            ),
        )
    pass_count = int(m.group(1))
    fail_count = int(m.group(2))
    # Universalised check: pass_count == 0 catches both the all-FAIL
    # shape (0 PASS / N FAIL) AND the empty-pipeline shape (0 PASS /
    # 0 FAIL — section-compare emitted but produced no rows, also a
    # non-completion state).
    if pass_count == 0 or fail_count > 0:
        return CheckResult(
            label="sections/result.txt visual health",
            status="fail",
            message=(
                f"section-compare reports {pass_count} PASS / {fail_count} FAIL — the "
                "auxiliary gates passing while the canonical visual diff "
                "is failing or has no successful section. Re-run "
                "section-compare or fix the missing impl sections before "
                "declaring done."
            ),
            fix=(
                "bash $PLUGIN_ROOT/skills/visual-debug/scripts/section-compare.sh "
                f"<orig-url> <impl-url> <session> {self.ref_dir}"
            ),
        )
    return None


def _check_transitions_result_health(self: Gate) -> CheckResult | None:
    """Fail post-implement when transition-spec exists but compare evidence is missing.

    verification-plan.json can omit transition-compare under lower tiers or be
    bypassed by direct post-implement invocations. This direct aggregate keeps
    transitions/result.txt on the same contract as sections/result.txt.
    """
    if self._transition_spec_count() <= 0:
        return None
    if _sections_result_pass_count(self) == 0:
        return None
    result_path = self.ref_dir / "transitions" / "result.txt"
    fix = (
        "bash $PLUGIN_ROOT/skills/visual-debug/scripts/transition-compare.sh "
        f"<orig-url> <impl-url> <session> {self.ref_dir}"
    )
    if not result_path.is_file():
        return CheckResult(
            label="transitions/result.txt visual health",
            status="fail",
            message=(
                "transitions/result.txt — MISSING. post-implement cannot pass "
                "until transition-compare has produced canonical motion evidence."
            ),
            fix=fix,
        )
    try:
        text = result_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return CheckResult(
            label="transitions/result.txt visual health",
            status="fail",
            message=f"transitions/result.txt — unreadable ({e}). Re-run transition-compare.",
            fix=fix,
        )

    fail_lines = sum(1 for line in text.splitlines() if "❌" in line)
    if fail_lines > 0:
        return CheckResult(
            label="transitions/result.txt visual health",
            status="fail",
            message=f"transition-compare reports {fail_lines} FAIL line(s).",
            fix=fix,
        )

    summary = re.search(r"Transition compare:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL", text)
    if summary:
        pass_count = int(summary.group(1))
        fail_count = int(summary.group(2))
        if pass_count == 0 or fail_count > 0:
            return CheckResult(
                label="transitions/result.txt visual health",
                status="fail",
                message=(
                    f"transition-compare reports {pass_count} PASS / {fail_count} FAIL — "
                    "motion evidence is failing or empty."
                ),
                fix=fix,
            )

    measurement_rows = sum(
        1 for line in text.splitlines()
        if ("✅" in line or "❌" in line) and "result:" not in line.lower()
    )
    if measurement_rows == 0:
        return CheckResult(
            label="transitions/result.txt visual health",
            status="fail",
            message=(
                "transitions/result.txt contains 0 measurement rows despite "
                f"transition-spec.json declaring {self._transition_spec_count()} transition(s)."
            ),
            fix=fix,
        )
    return None


def _sections_result_pass_count(self: Gate) -> int | None:
    """Return the parsed section PASS count, or None when no footer exists."""
    result_path = self.ref_dir / "sections" / "result.txt"
    if not result_path.is_file():
        return None
    try:
        text = result_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL", text)
    if not m:
        return None
    return int(m.group(1))


def _sections_result_exists(self: Gate) -> bool:
    """True iff sections/result.txt exists at all (parsable or not)."""
    return (self.ref_dir / "sections" / "result.txt").is_file()


def _check_visual_debug_stamp(self: Gate) -> CheckResult | None:
    """Require visual-debug-stamp.json when sections/result.txt EXISTS.

    Closes the "ran bare section-compare.sh without going through
    auto-verify" path: when section-compare.sh runs directly, it
    produces sections/result.txt but skips the universal anti-cheat
    baseline (html-paste / ref-screenshot-asset / proxy-mirror) that
    auto-verify.sh chains together. The stamp is written only by
    auto-verify.sh at the end of its run, so its presence proves the
    canonical entry was used regardless of whether the visual diff
    passed, failed, or all-failed.

    Trigger condition is "result.txt exists" (not "≥1 PASS") because
    the anti-cheat baseline must run on any site that reached section-
    compare, including 0-PASS visual-fidelity-failure runs — codex
    must not be able to skip the baseline by intentionally failing
    sections.

    Returns None when no sections/result.txt exists yet (pre-section-
    compare runs shouldn't be blocked here). Returns fail when result.txt
    exists but the stamp is missing or marks the run as failed.
    """
    if not _sections_result_exists(self):
        return None
    stamp_path = self.ref_dir / "visual-debug-stamp.json"
    if not stamp_path.is_file():
        return CheckResult(
            "visual-debug-stamp.json",
            "fail",
            "sections/result.txt reports ≥1 PASS but visual-debug-stamp.json "
            "is missing. The canonical `scripts/verify/auto-verify.sh` entry "
            "was not used — bare section-compare.sh runs cannot clear the "
            "post-implement gate because the HTML-paste / screenshot-asset "
            "cheat paths bypassed the universal anti-cheat baseline that "
            "auto-verify chains together.",
            fix=(
                "bash $PLUGIN_ROOT/scripts/verify/auto-verify.sh <session> "
                "<orig-url> <impl-url> <ref-dir>"
            ),
        )
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            "visual-debug-stamp.json",
            "fail",
            "visual-debug-stamp.json is unreadable / malformed. Re-run "
            "auto-verify.sh to regenerate it.",
            fix=(
                "bash $PLUGIN_ROOT/scripts/verify/auto-verify.sh <session> "
                "<orig-url> <impl-url> <ref-dir>"
            ),
        )
    if not stamp.get("passed", False):
        return CheckResult(
            "visual-debug-stamp.json",
            "fail",
            f"visual-debug-stamp.json reports passed=false "
            f"(totalFail={stamp.get('totalFail', '?')}/"
            f"{stamp.get('totalChecks', '?')}). Cannot clear post-implement "
            "until auto-verify.sh exits 0.",
            fix=(
                "bash $PLUGIN_ROOT/scripts/verify/auto-verify.sh <session> "
                "<orig-url> <impl-url> <ref-dir>"
            ),
        )
    return None


def _check_phase_e_result(self: Gate) -> CheckResult | None:
    """When `phase-e-result.json` exists, enforce its verdict.

    Phase E (LLM semantic review via visual-judge.sh) is OPTIONAL and
    expensive (~44K vision tokens). When NOT run, this check is silent.
    When run, the artifact's `passed` field is binding — Phase E catches
    the cheat classes pixel-diff misses (e.g. "impl is a static paste
    of ref DOM" is visually identical at pixel level but semantically
    obvious to a vision-LLM reviewer). Schema follows visual-judge.sh's
    JSON output.
    """
    path = self.ref_dir / "phase-e-result.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            "phase-e-result.json",
            "fail",
            "phase-e-result.json is unreadable / malformed.",
            fix=(
                "Re-run Phase E LLM review: bash $PLUGIN_ROOT/skills/"
                "visual-debug/scripts/visual-judge.sh ..."
            ),
        )
    verdict = data.get("passed")
    if verdict is False:
        reason = data.get("reason") or data.get("summary") or "no reason given"
        return CheckResult(
            "phase-e-result.json",
            "fail",
            f"Phase E semantic review rejected the impl: {str(reason)[:200]}",
            fix=(
                "Fix the issues Phase E flagged, then re-run "
                "visual-judge.sh. If you believe Phase E is wrong, "
                "investigate before overriding — Phase E catches HTML-paste "
                "/ screenshot-substitution cheats that pixel-diff misses."
            ),
        )
    return None


def _check_html_paste_required(self: Gate) -> CheckResult | None:
    plan_path = self.ref_dir / "verification-plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if plan.get("schemaVersion") != 1:
        return None
    checks = plan.get("requiredChecks")
    if not isinstance(checks, list):
        return None
    ids = {
        str(entry.get("id"))
        for entry in checks
        if isinstance(entry, dict) and entry.get("id") is not None
    }
    if "html-paste" not in ids and "html-paste-check" not in ids:
        return CheckResult(
            "verification-plan.json",
            "fail",
            "verification-plan.json is missing required anti-cheat check: "
            "html-paste. Regenerate the verification plan.",
            fix="Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>",
        )
    return None


def gate_post_implement(self: Gate) -> list[CheckResult]:
    results = []
    results.append(self.check_file(self.ref_dir / "extracted.json", "extracted.json"))
    results.append(
        self.check_file(self.ref_dir / "transition-spec.json", "transition-spec.json")
    )
    results.append(
        self.check_dir(self.ref_dir / "static" / "ref", "static/ref screenshots", min_files=5)
    )
    results.extend(self._check_verification_plan())
    html_paste_required = _check_html_paste_required(self)
    if html_paste_required is not None:
        results.append(html_paste_required)
    results.extend(self._check_componentization())
    results.extend(self._check_generation_completeness())
    section_health = _check_sections_result_health(self)
    if section_health is not None:
        results.append(section_health)
    transition_health = _check_transitions_result_health(self)
    if transition_health is not None:
        results.append(transition_health)
    visual_debug_stamp = _check_visual_debug_stamp(self)
    if visual_debug_stamp is not None:
        results.append(visual_debug_stamp)
    phase_e = _check_phase_e_result(self)
    if phase_e is not None:
        results.append(phase_e)
    return results
