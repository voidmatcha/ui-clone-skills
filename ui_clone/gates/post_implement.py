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

from ui_clone import state as _state_mod

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

_CSS_MODULE_CLASS_RE = re.compile(r"\b[A-Za-z][\w-]*__[A-Za-z0-9_-]{4,}\b")
_FORENSIC_SOURCE_SUFFIXES = (".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".astro")


def _check_forensic_preservation_compliance(self: Gate) -> CheckResult | None:
    """Enforce generation-plan forensic preservation after implementation.

    When generation-plan.json marks a CSS-module-heavy reference as requiring
    forensic preservation, freehand class systems are not a valid first pass.
    The implementation must copy the reference CSS chunks into src/ref-css,
    import them, and render JSX that preserves a meaningful slice of the
    original CSS-module className tokens.
    """
    plan_path = self.ref_dir / "generation-plan.json"
    if not plan_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    forensic = plan.get("forensicPreservation")
    if not isinstance(forensic, dict) or forensic.get("required") is not True:
        return None

    if forensic.get("blockedUntilCssArtifacts") is True or forensic.get("missingCssArtifacts") is True:
        return CheckResult(
            "forensic-preservation-compliance",
            "fail",
            "generation-plan.json requires forensicPreservation, but ref CSS artifacts "
            "were missing/incomplete when the plan was created.",
            fix=(
                "Do not bypass this with handwritten local CSS or a Tailwind rebuild. "
                "Rerun CSS capture or recover bundle CSS into tmp/ref/<component>/css/, "
                "then rerun generation-plan.sh so cssFiles/cssBytes prove the source "
                "CSS is available before generating the implementation."
            ),
        )

    impl_root = self._find_impl_root()
    if impl_root is None:
        return CheckResult(
            "forensic-preservation-compliance",
            "fail",
            "generation-plan.json requires forensicPreservation, but impl root "
            "could not be resolved.",
            fix=(
                "Write the implementation under the resolved impl/ root or set "
                "UI_CLONE_IMPL_ROOT, then preserve ref-derived JSX + local CSS."
            ),
        )

    src_dir = impl_root / "src"
    copy_to = forensic.get("copyCssTo")
    copy_to_str = copy_to if isinstance(copy_to, str) and copy_to.strip() else "src/ref-css"
    css_dir = impl_root / copy_to_str
    css_files = sorted(css_dir.glob("*.css")) if css_dir.is_dir() else []

    code_texts: list[str] = []
    import_texts: list[str] = []
    if src_dir.is_dir():
        for path in src_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in _FORENSIC_SOURCE_SUFFIXES:
                try:
                    code_texts.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
            if path.suffix.lower() in (*_FORENSIC_SOURCE_SUFFIXES, ".css"):
                try:
                    import_texts.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue

    class_tokens = {
        token
        for text in code_texts
        for token in _CSS_MODULE_CLASS_RE.findall(text)
    }
    ref_count_raw = forensic.get("classSignatureCount")
    ref_count = ref_count_raw if isinstance(ref_count_raw, int) and ref_count_raw > 0 else 0
    required_token_count = max(10, int(ref_count * 0.25)) if ref_count else 10
    has_ref_css_import = any("ref-css" in text for text in import_texts)

    failures: list[str] = []
    if not css_files:
        failures.append(f"missing copied reference CSS files under {copy_to_str}")
    if not has_ref_css_import:
        failures.append("missing src/ref-css CSS import before local overrides")
    if len(class_tokens) < required_token_count:
        failures.append(
            f"preserved CSS-module className tokens {len(class_tokens)} "
            f"< required {required_token_count} (ref signatures={ref_count})"
        )

    if failures:
        return CheckResult(
            "forensic-preservation-compliance",
            "fail",
            "generation-plan.json requires forensicPreservation, but the impl "
            "looks like a freehand rebuild: " + "; ".join(failures) + ".",
            fix=(
                "Regenerate the first pass from dom-scaffold.json: copy "
                f"tmp/ref/<component>/css/*.css into impl/{copy_to_str}, import "
                "those CSS files before local overrides, and keep the ref's "
                "CSS-module className tokens in JSX. Add transitions only after "
                "the preserved scaffold renders."
            ),
        )

    return CheckResult(
        "forensic-preservation-compliance",
        "pass",
        f"✓ forensicPreservation satisfied: {len(css_files)} local CSS file(s), "
        f"{len(class_tokens)} preserved CSS-module className token(s).",
    )


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
    # P is real pixel passes only; STRUCTURAL_ONLY (substituted fonts/images)
    # is its own field and also counts as visual evidence.
    m = re.search(
        r"Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL"
        r"(?:,\s*\d+\s+SKIP)?(?:,\s*(\d+)\s+STRUCTURAL_ONLY)?",
        text,
    )
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
    structural_count = int(m.group(3) or 0)
    # Phase 2 — genuine-fidelity convergence: require >=1 genuine pixel PASS.
    # pass_count is real ✅ passes only (STRUCTURAL_ONLY is its own field since
    # 33a7f8f). pass_count == 0 blocks every non-genuine shape: all-FAIL,
    # empty-pipeline (0/0/0), AND pure-substitution (0 PASS / N STRUCTURAL_ONLY —
    # the gaming vector). A genuine pass alongside legitimate substitution passes.
    if pass_count == 0 or fail_count > 0:
        return CheckResult(
            label="sections/result.txt visual health",
            status="fail",
            message=(
                f"section-compare reports {pass_count} PASS / {fail_count} FAIL "
                f"/ {structural_count} STRUCTURAL_ONLY — the "
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
    """Fail post-implement when required hover/end-state compare evidence is bad.

    ``transition-compare.sh`` measures idle/hover end states. Scroll/splash/IO
    motion is covered by transition-proof/video-motion/transition-fires, so do
    not require ``transitions/result.txt`` merely because transition-spec.json
    exists.
    """
    if self._transition_spec_count() <= 0:
        return None
    if _sections_result_evidence_count(self) == 0:
        return None
    result_path = self.ref_dir / "transitions" / "result.txt"
    result_required = _verification_plan_requires_produces(
        self, "transitions/result.txt"
    )
    fix = (
        "bash $PLUGIN_ROOT/skills/visual-debug/scripts/transition-compare.sh "
        f"<orig-url> <impl-url> <session> {self.ref_dir}"
    )
    if not result_path.is_file():
        if not result_required:
            return None
        return CheckResult(
            label="transitions/result.txt visual health",
            status="fail",
            message=(
                "transitions/result.txt — MISSING. post-implement cannot pass "
                "until required hover/end-state transition-compare evidence exists."
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
            if pass_count == 0 and fail_count == 0 and not result_required:
                return None
            return CheckResult(
                label="transitions/result.txt visual health",
                status="fail",
                message=(
                    f"transition-compare reports {pass_count} PASS / {fail_count} FAIL — "
                    "hover/end-state evidence is failing or empty."
                ),
                fix=fix,
            )

    measurement_rows = sum(
        1 for line in text.splitlines()
        if ("✅" in line or "❌" in line) and "result:" not in line.lower()
    )
    if measurement_rows == 0:
        if not result_required:
            return None
        return CheckResult(
            label="transitions/result.txt visual health",
            status="fail",
            message=(
                "transitions/result.txt contains 0 measurement rows despite "
                "verification-plan.json requiring transition-compare."
            ),
            fix=fix,
        )
    return None


def _verification_plan_requires_produces(self: Gate, produces: str) -> bool:
    plan_path = self.ref_dir / "verification-plan.json"
    if not plan_path.is_file():
        return False
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    checks = plan.get("requiredChecks") or []
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(row, dict) and row.get("produces") == produces
        for row in checks
    )


def _sections_result_evidence_count(self: Gate) -> int | None:
    """Return real PASS + STRUCTURAL_ONLY (total visual evidence) from the
    sections footer, or None when no footer exists. Used to decide whether
    there is enough section evidence to bother checking transitions — a
    structural-only converged site has 0 real PASS but is still evidence."""
    result_path = self.ref_dir / "sections" / "result.txt"
    if not result_path.is_file():
        return None
    try:
        text = result_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(
        r"Result:\s*(\d+)\s+PASS,\s*\d+\s+FAIL"
        r"(?:,\s*\d+\s+SKIP)?(?:,\s*(\d+)\s+STRUCTURAL_ONLY)?",
        text,
    )
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2) or 0)


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
    # Provisional handling (Fix 1, codex review 2026-05-27): auto-verify
    # writes a provisional stamp at the start of its run so post-implement
    # gate can pass DURING that same run (chicken-and-egg). A crashed/
    # orphaned auto-verify leaves provisional=true behind, which without
    # this guard would falsely satisfy the next post-implement check.
    # The provisional stamp carries `invocationId` correlating to the
    # exporter's env var; only accept provisional when both match.
    if stamp.get("provisional"):
        inflight_id = os.environ.get("UI_RE_AUTOVERIFY_INFLIGHT", "")
        stamp_id = stamp.get("invocationId", "")
        if inflight_id and stamp_id and inflight_id == stamp_id:
            return None  # in-flight: trust the current auto-verify run
        return CheckResult(
            "visual-debug-stamp.json",
            "fail",
            "visual-debug-stamp.json is provisional (auto-verify.sh started "
            "but did not write the final verdict). The previous auto-verify "
            "run likely crashed or was killed mid-execution; the stale "
            "provisional stamp cannot be trusted. Re-run auto-verify.sh.",
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


# Spec-bundle grounding — F from docs/claude-fidelity-analysis.md (Q-A
# pair on improving ref-data utilization). Forces transition-spec.json
# entries to reference real bundle/css/html artifacts so the spec cannot
# be hand-waved without grounding in actual ref source.

# Sentinels that mark "no bundle file expected" (legit absence).
_SPEC_CHUNK_SENTINELS = ("inline init", "inline", "n/a", "none")


def _parse_source_chunk(raw: str) -> list[str]:
    """Parse a free-form source_chunk into candidate file basenames.

    Real schemas observed in production transition-spec.json files:
      - "gsap.min.js"
      - "ScrollTrigger.min.js + gsap.min.js"
      - "webflow.js (IX2 actions) + gsap.min.js + ScrollTrigger.min.js"
      - "webflow.js (custom code) or inline init"
      - "bundles/main.js"
      - "css/<component>.webflow.css"

    Returns the basename for each non-sentinel chunk. Sentinels
    ("inline init", etc.) produce no candidates — they're legit absences.
    """
    if not raw:
        return []
    # Split on `+` and case-insensitive ` or `.
    pieces = re.split(r"\s*\+\s*|\s+or\s+", raw, flags=re.IGNORECASE)
    out: list[str] = []
    for piece in pieces:
        # Strip `(...)` annotations.
        piece = re.sub(r"\s*\([^)]*\)", "", piece).strip()
        if not piece or piece.lower() in _SPEC_CHUNK_SENTINELS:
            continue
        # Accept path-prefixed forms by taking the basename.
        out.append(piece.split("/")[-1])
    return out


def _check_spec_bundle_grounding(self: Gate) -> CheckResult | None:
    """Fail when transition-spec.json declares source_chunk files that are
    not present anywhere in the ref artifacts (bundles/, css/, html/).

    Forces spec extraction to be grounded in real ref source. Hand-waved
    spec entries (e.g. "I think this is in webflow-fake.js") fail the
    grounding check and force re-extraction.

    Skips silently when transition-spec.json is missing or malformed —
    other checks (`_check_transitions_result_health`) handle those.
    """
    spec_path = self.ref_dir / "transition-spec.json"
    if not spec_path.is_file():
        return None
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = spec.get("transitions") or spec.get("entries") or []
    if not isinstance(entries, list) or not entries:
        return None

    # Pre-collect available basenames across the three artifact dirs.
    available: set[str] = set()
    for sub in ("bundles", "css", "html"):
        d = self.ref_dir / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file():
                available.add(f.name)

    missing: list[tuple[str, str]] = []  # (entry_id, missing_basename)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("source_chunk")
        if not isinstance(raw, str):
            continue
        for basename in _parse_source_chunk(raw):
            if basename not in available:
                missing.append((str(entry.get("id", "?")), basename))

    if not missing:
        return None
    sample = "\n".join(f"    - entry {eid}: source_chunk references {f!r}" for eid, f in missing[:5])
    extra = f"\n    ... and {len(missing) - 5} more" if len(missing) > 5 else ""
    return CheckResult(
        label="spec-bundle-grounding",
        status="fail",
        message=(
            f"{len(missing)} transition-spec entry(s) reference files not "
            f"present in ref artifacts (bundles/, css/, html/):\n"
            f"{sample}{extra}"
        ),
        fix=(
            "Each source_chunk in transition-spec.json must point at a real "
            "file captured into tmp/ref/<c>/bundles/, css/, or html/. "
            "If a chunk is genuinely inline init, use the sentinel "
            "\"inline init\" instead. Re-run capture to refresh bundle "
            "downloads if a referenced file was renamed by the ref."
        ),
    )


# E1: bundle-grep context inject — claude fidelity analysis 2026-05-25,
# codex review of D+E1 staged design.
# When fix iterations stall (any active gate has failed 2+ times) AND
# sections/result.txt has failing rows, auto-inject ref-source snippets
# for the worst-AE selectors so the next fix iteration is grounded in
# the ref's actual code instead of guessed. Cost: 0 (greps captured ref
# artifacts, no LLM call).

# Codex review item (a): mark_failed bumps the counter AFTER the gate
# runs, so an in-gate check reads the previous count. "fail 2-3" target
# becomes effective threshold >= 2.
_E1_FAIL_THRESHOLD = 2
_E1_WORST_N = 3
_E1_LINES_PER_SELECTOR = 5
_E1_BUNDLE_GREP_TIMEOUT_S = 15


def _parse_failing_section_rows(text: str) -> list[tuple[str, int]]:
    """Return [(label, ae_per_mpx)] for rows marked ❌ or 🌑.

    Codex review item (c): saturated rows use 🌑 not ❌ but still count
    as fail and must be included for worst-N selection.
    """
    out: list[tuple[str, int]] = []
    for line in text.splitlines():
        if "❌" not in line and "🌑" not in line:
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) < 4:
            continue
        label = cells[0]
        if not label or label.lower() == "section":
            continue  # header row
        try:
            ae_per_mpx = int(cells[2])
        except ValueError:
            continue
        out.append((label, ae_per_mpx))
    return out


def _resolve_repo_root_for_bundle_grep() -> Path | None:
    """Locate the plugin root so we can call scripts/extract/bundle-grep.sh.

    Mirrors _find_impl_root's resolution: env vars first, then walk up
    from this file.
    """
    env_root = os.environ.get("PLUGIN_ROOT") or os.environ.get(
        "CLAUDE_PLUGIN_ROOT"
    )
    if env_root:
        cand = Path(env_root) / "scripts" / "extract" / "bundle-grep.sh"
        if cand.is_file():
            return Path(env_root)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts" / "extract" / "bundle-grep.sh").is_file():
            return parent
    return None


def _check_bundle_grep_context_inject(self: Gate) -> CheckResult | None:
    """E1 sub-check: when fix-loop is stuck, inject ref-source context for
    the worst-AE failing sections.

    Decision: this is `status="warn"`, not "fail". The intent is to enrich
    the goal-card with ref-source hits — the existing post-implement
    failures already block. A warn surfaces the snippets without adding
    another fail count.

    Codex review item (f): post_implement.py must not auto-DISPATCH expensive
    LLM calls (visual-judge auto-run = D). bundle-grep is cheap text grep,
    so consuming/injecting it here is in scope. The D dispatcher belongs
    in driver/goal-card territory, not in the gate.
    """
    # Active-gate counter — codex review item (d): use max() across
    # post-implement and section-compare because mark_failed only bumps the
    # current_gate counter, and visual fails accruing under post-implement
    # leave the section-compare counter stale.
    try:
        state = _state_mod.PipelineState.load(self.ref_dir)
    except (OSError, json.JSONDecodeError):
        return None
    counter = max(
        state.gate_fail_counts.get("post-implement", 0),
        state.gate_fail_counts.get("section-compare", 0),
    )
    if counter < _E1_FAIL_THRESHOLD:
        return None

    result_path = self.ref_dir / "sections" / "result.txt"
    if not result_path.is_file():
        return None
    try:
        text = result_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    failing = _parse_failing_section_rows(text)
    if not failing:
        return None

    # Worst-N by AE/Mpx (descending).
    failing.sort(key=lambda row: row[1], reverse=True)
    worst = failing[:_E1_WORST_N]

    repo_root = _resolve_repo_root_for_bundle_grep()
    if repo_root is None:
        return None
    grep_script = repo_root / "scripts" / "extract" / "bundle-grep.sh"

    snippets: list[str] = []
    for label, ae in worst:
        try:
            proc = subprocess.run(
                ["bash", str(grep_script), str(self.ref_dir), label],
                capture_output=True,
                text=True,
                timeout=_E1_BUNDLE_GREP_TIMEOUT_S,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        output = (proc.stdout or "").strip()
        if not output:
            snippets.append(
                f"  • {label} (AE/Mpx={ae}): no ref-source matches for selector"
            )
            continue
        top_lines = output.splitlines()[:_E1_LINES_PER_SELECTOR]
        snippets.append(f"  • {label} (AE/Mpx={ae}):")
        for line in top_lines:
            snippets.append(f"      {line[:180]}")

    if not snippets:
        return None
    body = "\n".join(snippets)
    return CheckResult(
        label="bundle-grep-context-inject",
        status="warn",
        message=(
            f"Fix-loop stuck (active-gate fail_count={counter}). Worst-AE "
            f"section ref-source for next fix iteration:\n{body}"
        ),
        fix=(
            "Use these ref-source hits as the ground truth for your next "
            "implementation pass. Match the ref's animation params (duration, "
            "ease, transform) — don't invent. If bundle-grep returned no hits, "
            "the selector spelling may be wrong or the ref's animation is in "
            "a chunk that capture missed (re-extract bundles)."
        ),
    )


# Anti-cheat pattern detection — F1 from docs/claude-fidelity-analysis.md.
# Patterns observed in the 26-site loop (2026-05-24/25): claude under
# auto mode generates hidden stub elements (1px×1px, display:none, empty
# containers with check-required attributes) to satisfy static gate
# selectors without rendering the actual component. These pass the
# selector check but fail the user's visual fidelity expectation.

# Pattern 1: className with -stub / -shim / -placeholder suffix on the
# element carrying the check-required selector. High-signal: ui-clone-skills
# generated impl has no legitimate use for these suffixes.
_ANTI_CHEAT_NAME_RE = re.compile(
    r"className\s*=\s*[\"'][^\"']*-(?:stub|shim|placeholder)\b",
    re.IGNORECASE,
)

# Pattern 2: zero-visible-area markers. Any single marker is suspicious in
# combination with a check-required attribute on the same element.
_ZERO_AREA_MARKERS = (
    "width: 0", "width:0",
    "height: 0", "height:0",
    'width: "1px"', "width: '1px'",
    'height: "1px"', "height: '1px'",
    "width: 1, ", "height: 1, ",  # bare integer 1 in JSX style
    "display: 'none'", 'display: "none"', "display:'none'", 'display:"none"',
    "clipPath: 'inset(50%)'", 'clipPath: "inset(50%)"',
    "clip-path: inset(50%)",
    "clip: rect(0",
)

# Attributes used by ui-clone-skills static checks. An element carrying any
# of these MUST be a real rendered component, not a hidden stub. List grows
# as new static checks add selector requirements.
_CHECK_REQUIRED_ATTRS = (
    "data-lottie",
    "data-hero-composite",
    "data-transition",
    "data-motion",
    "data-reveal",
    "data-scroll-trigger",
    "data-parallax",
)


def _find_all_offsets(haystack: str, needle: str) -> list[int]:
    """All start offsets of needle in haystack (non-overlapping fine for keywords)."""
    out: list[int] = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        out.append(idx)
        start = idx + 1
    return out


def _check_anti_cheat_patterns(self: Gate) -> CheckResult | None:
    """Fail when impl source contains stub elements that satisfy a static
    check's selector requirement but render to zero visible area.

    See docs/claude-fidelity-analysis.md for the 4-site evidence behind
    these patterns. Skips silently when impl_root is unresolvable
    (capture-phase runs) or impl/src is absent.
    """
    impl_root = self._find_impl_root()
    if impl_root is None or not impl_root.is_dir():
        return None
    src = impl_root / "src"
    if not src.is_dir():
        return None

    hits: list[tuple[str, str]] = []
    for path in src.rglob("*"):
        if path.suffix.lower() not in (".tsx", ".jsx", ".ts", ".js"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(impl_root))

        # Pattern 1: stub/shim/placeholder className
        for m in _ANTI_CHEAT_NAME_RE.finditer(text):
            hits.append((rel, f"stub-className: {m.group(0)[:60]}"))

        # Pattern 2: check-required attr within 200 chars of a zero-area marker.
        # Bidirectional window catches both "attr then style" and "style then attr".
        for attr in _CHECK_REQUIRED_ATTRS:
            for attr_pos in _find_all_offsets(text, attr):
                window_lo = max(0, attr_pos - 200)
                window_hi = min(len(text), attr_pos + 200)
                window = text[window_lo:window_hi]
                for marker in _ZERO_AREA_MARKERS:
                    if marker in window:
                        hits.append((rel, f"{attr} + zero-area ({marker})"))
                        break
                else:
                    continue
                break  # one hit per attr occurrence is enough

    if not hits:
        return None
    sample = "\n".join(f"    - {p}: {pat}" for p, pat in hits[:5])
    extra = f"\n    ... and {len(hits) - 5} more" if len(hits) > 5 else ""
    return CheckResult(
        label="anti-cheat-pattern-detection",
        status="fail",
        message=(
            f"Detected {len(hits)} anti-cheat shim(s) in impl/src — elements "
            f"that satisfy a static check's selector requirement but render "
            f"to zero visible area:\n{sample}{extra}"
        ),
        fix=(
            "Replace stub/shim/placeholder elements with the actual rendered "
            "component from the ref. A static check satisfied by a hidden "
            "1px×1px element is not a real fix — the rendered UI must "
            "contain the real component. "
            "See docs/claude-fidelity-analysis.md for the patterns and rationale."
        ),
    )


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
    forensic_preservation = _check_forensic_preservation_compliance(self)
    if forensic_preservation is not None:
        results.append(forensic_preservation)
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
    anti_cheat = _check_anti_cheat_patterns(self)
    if anti_cheat is not None:
        results.append(anti_cheat)
    spec_grounding = _check_spec_bundle_grounding(self)
    if spec_grounding is not None:
        results.append(spec_grounding)
    bundle_inject = _check_bundle_grep_context_inject(self)
    if bundle_inject is not None:
        results.append(bundle_inject)
    return results
