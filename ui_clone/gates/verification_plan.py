"""Verification-Plan gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def _check_verification_plan(self: Gate) -> list[CheckResult]:
    """Honor tmp/ref/<c>/verification-plan.json — declared site-specific checks.

    Schema:
      { "schemaVersion": 1,
        "requiredChecks": [{
          "id": "<short-id>",
          "script": "<path/to/script.sh>",
          "produces": "<artifact relative to ref-dir>",
          "reason": "<why required>",
          "severity": "block" | "warn"
        }] }

    Missing file → returns []. Each required check artifact must exist
    and contain `"status": "pass"`. Severity "warn" emits a warning that
    does not block; "block" (default) fails the gate.
    """
    plan_path = self.ref_dir / "verification-plan.json"
    if not plan_path.is_file():
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                "verification-plan.json — MISSING. post-implement cannot infer required text/DOM/asset/motion checks without it.",
                fix="Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>",
            )
        ]

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                f"verification-plan.json — unreadable ({e}). "
                "post-implement cannot enforce required checks against "
                "an unparseable plan. Regenerate before retrying.",
                fix="Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>",
            )
        ]

    schema_version = plan.get("schemaVersion")
    vp_fix = "Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>"
    if "schemaVersion" not in plan:
        # Hand-written / hallucinated verification-plan.json (e.g. agent
        # inventing {component, checks} keys instead of running
        # verification-plan.sh) used to slip through as a silent warn —
        # making every declared required check unenforceable. Hard-fail
        # when no version is declared so the agent must actually run the
        # script. (Known future versions still degrade gracefully below.)
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                "verification-plan.json — missing `schemaVersion`. "
                "The file is hand-written; declared checks would be silently ignored.",
                fix=vp_fix,
            )
        ]
    if schema_version != 1:
        return [
            CheckResult(
                "verification-plan.json",
                "warn",
                f"verification-plan.json — schemaVersion {schema_version!r} not supported; ignoring",
            )
        ]

    if "requiredChecks" not in plan:
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                "verification-plan.json — missing `requiredChecks` key "
                "(wrong schema; required by verification-plan.sh output).",
                fix=vp_fix,
            )
        ]
    checks = plan.get("requiredChecks") or []
    if not isinstance(checks, list):
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                f"verification-plan.json — `requiredChecks` must be a list, "
                f"got {type(checks).__name__}.",
                fix=vp_fix,
            )
        ]
    if not checks:
        # Empty list is rare-but-legitimate (static site with no JS/scroll
        # signals). Surface it as a warn so the operator sees that NO
        # site-specific checks fired, rather than silently passing.
        return [
            CheckResult(
                "verification-plan.json",
                "warn",
                "verification-plan.json — `requiredChecks` is empty "
                "(verification-plan.sh detected no site-specific checks).",
            )
        ]

    # Two-phase mode (option A) — when UI_CLONE_PHASE=rapid the
    # agent is in initial visual-iteration mode. Non-anti-cheat
    # block checks are downgraded to warn so the agent can build
    # quickly and iterate visually first. Anti-cheat gates and
    # the must-stay-strict set remain block regardless.
    #
    # Promotion to strict: set UI_CLONE_PHASE=strict (default)
    # before declaring done. The strict run enforces every block.
    import os as _os
    phase = (_os.environ.get("UI_CLONE_PHASE") or "strict").lower()
    # Anti-cheat gates that ALWAYS stay block, even in rapid
    # mode — these catch cheating and must never downgrade.
    #
    STRICT_ALWAYS = {
        # Anti-cheat (block cheating regardless of phase).
        "ref-screenshot-asset", "invalidation", "scaffold-warn",
        "remote-asset-ref", "html-paste", "proxy-mirror-check",
        "hidden-children", "monolithic-impl", "entry-coherence",
        "text-fidelity-check", "dom-mirror-check",
        "required-media-coverage", "css-mirror",
        "runtime-dom-parity", "svg-dom-parity", "motion-coverage",
        "scroll-engine-parity",
        # runtime-image-validity — HTML-fallback-as-image is a
        # fundamental cheat (Vite serving index.html for missing
        # assets). Must stay strict.
        "runtime-image-validity",
        # reveal-trigger — IO+overflow:hidden reveals that never
        # fire are a core completion-blocker (catches "stuck
        # reveal" patterns that visually show empty space where
        # ref has content). Must stay strict.
        "reveal-trigger",
        # transition-fires — the RUNTIME source-of-truth for motion
        # fidelity. Each transition-spec entry must produce a measured
        # runtime delta when its trigger is driven; class-name presence
        # is not motion. Anti-gaming, so it must never downgrade.
        "transition-fires",
    }

    out: list[CheckResult] = []
    for entry in checks:
        if not isinstance(entry, dict):
            continue
        check_id = str(entry.get("id") or "?")
        produces = entry.get("produces")
        script = entry.get("script") or ""
        reason = entry.get("reason") or ""
        severity = entry.get("severity") or "block"
        # Rapid-mode downgrade: block→warn for non-anti-cheat
        # checks so the agent can iterate visually without
        # consuming the iteration budget on fidelity gates.
        if phase == "rapid" and severity == "block" and check_id not in STRICT_ALWAYS:
            severity = "warn"

        if not produces:
            continue
        artifact = self.ref_dir / produces
        label = f"required: {check_id}"
        fix = f"Run: bash {script}" if script else ""

        if not artifact.is_file():
            msg = (
                f"MISSING_ARTIFACT {check_id} — produces "
                f"{produces}. Reason: {reason}. "
                "Run scripts/verify/run-required-checks.sh "
                "<session> <ref-url> <impl-url> <ref-dir> to "
                "produce every missing required-check artifact "
                "in one shell call."
            )
            if severity == "warn":
                out.append(CheckResult(label, "warn", msg))
            else:
                out.append(CheckResult(label, "fail", msg, fix=fix))
            continue

        try:
            raw = artifact.read_text(encoding="utf-8")
        except OSError as e:
            msg = (
                f"{check_id} — artifact unreadable ({e}). "
                "Cannot verify; re-run the producing script."
            )
            if severity == "warn":
                out.append(CheckResult(label, "warn", msg))
            else:
                out.append(CheckResult(label, "fail", msg, fix=fix))
            continue

        # If artifact is JSON with a `status` field, enforce status: "pass".
        # Non-JSON artifacts (e.g. transitions/result.txt) are scanned for
        # ❌ FAIL markers — presence-only would let real failures slip past
        # this gate when section-compare's dedicated parser only watches
        # sections/result.txt, not transitions/result.txt.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            fail_lines = sum(1 for line in raw.splitlines() if "❌" in line)
            if fail_lines > 0:
                msg = f"{check_id} — {fail_lines} FAIL line(s) in {produces}. Reason: {reason}"
                if severity == "warn":
                    out.append(CheckResult(label, "warn", msg))
                else:
                    out.append(CheckResult(label, "fail", msg, fix=fix))
            else:
                # transition-compare / video-motion-compare / hover-state /
                # keyframes-diff etc — when transition-spec declares any
                # transitions, the corresponding result.txt MUST contain
                # actual measurement rows (✅ or ❌). An empty / near-empty
                # text artifact is the "transition-compare never actually
                # ran" gaming pattern (vacuous PASS because no ❌ exists).
                if check_id in {
                    "transition-compare",
                    "video-motion-compare",
                    "hover-state-compare",
                    "keyframes-diff",
                    "scroll-anim-temporal",
                }:
                    measurement_rows = sum(
                        1 for line in raw.splitlines()
                        if ("✅" in line or "❌" in line)
                        and "result:" not in line.lower()
                    )
                    spec_has_transitions = self._transition_spec_count() > 0
                    if spec_has_transitions and measurement_rows == 0:
                        msg = (
                            f"{check_id} — {produces} contains 0 measurement rows "
                            f"(no ✅/❌ lines) despite transition-spec.json declaring "
                            f"{self._transition_spec_count()} transition(s). The check "
                            f"didn't actually run."
                        )
                        if severity == "warn":
                            out.append(CheckResult(label, "warn", msg))
                        else:
                            out.append(CheckResult(label, "fail", msg, fix=fix))
                        continue
                out.append(CheckResult(label, "pass", f"{check_id} (text artifact, no FAIL markers)"))
            continue

        status = data.get("status") if isinstance(data, dict) else None
        # tree-diff floor — a `status=pass` with `elements_walked` below
        # the floor is the 5199dd9 gaming pattern: agent ships a near-
        # empty impl (11 elements walked vs ref's 200) and tree-diff
        # vacuously passes "0 critical mismatches" because there's
        # nothing to mismatch. Floor cross-references section-map.json
        # so a 4-section page doesn't trip the gate.
        if (
            check_id == "tree-diff"
            and status == "pass"
            and isinstance(data, dict)
        ):
            walked = int(data.get("elements_walked") or 0)
            floor = self._tree_diff_floor()
            if walked < floor:
                msg = (
                    f"tree-diff — only {walked} elements walked (floor: {floor}). "
                    f"This is the 'near-empty impl' gaming pattern: with so few "
                    f"elements to pair, tree-diff vacuously reports 0 mismatches. "
                    f"Generate real impl content."
                )
                out.append(CheckResult(label, "fail", msg, fix=fix))
                continue
            counts = data.get("counts") or {}
            if isinstance(counts, dict):
                unpaired = int(counts.get("unpaired") or 0)
                ok = int(counts.get("ok") or 0)
                if unpaired >= 3 and unpaired > ok:
                    msg = (
                        f"tree-diff — unpaired majority "
                        f"(unpaired={unpaired}, ok={ok}). "
                        "elementFromPoint pairing failed, so status=pass "
                        "is not convergence evidence. Fix DOM/layout "
                        "structure until most walked elements pair."
                    )
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                    continue
        #
        PATH_CHECK_IDS = {
            "asset-transfer", "asset-utilization", "asset-placement", "image-fidelity",
            "proxy-mirror-check", "lottie-runtime",
            "bundle-impl-coverage",
            "ref-screenshot-asset",
            # Common cheat pattern A1/A2/A3 — all emit implRoot.
            "entry-coherence", "scaffold-residue", "html-paste",
            # Diagnosis B — required-media coverage emits implRoot.
            "required-media-coverage",
            # Common cheat pattern A4/A5 + fix #2 — css-mirror emits
            # implRoot, runtime-dom-parity and hidden-children
            # are URL-based (no implRoot path to validate, but
            # listing here makes intent explicit; the PATH_CHECK
            # block is skipped when the recorded path field is
            # absent so this is safe).
            "css-mirror",
            # Signal 1 — scaffold-warn placeholders (impl source scan).
            "scaffold-warn",
            # validation run findings — monolithic-impl + motion-coverage
            # both emit implRoot for cross-loop protection.
            "monolithic-impl", "motion-coverage",
            "scroll-engine-parity",
        }
        if (
            check_id in PATH_CHECK_IDS
            and isinstance(data, dict)
            and status == "pass"
        ):
            def _nz(v: object) -> str | None:
                if isinstance(v, str) and v.strip():
                    return v
                return None

            recorded = (
                _nz(data.get("implPublicDir"))
                or _nz(data.get("implSrcDir"))
                or _nz(data.get("implDir"))
                or _nz(data.get("implRoot"))
                or _nz(data.get("implPkgJson"))
            )
            impl_root = self._find_impl_root()
            if (
                impl_root is not None
                and recorded is None
            ):
                msg = (
                    f"{check_id} — path-check artifact must emit "
                    "implRoot/implDir/implSrcDir/implPublicDir/"
                    "implPkgJson. None present, so cross-loop "
                    "contamination cannot be ruled out. Re-run the "
                    "check (newer scripts emit the field)."
                )
                out.append(CheckResult(label, "fail", msg, fix=fix))
                continue
            if recorded and impl_root is not None:
                rec_path = Path(str(recorded)).resolve()
                impl_resolved = impl_root.resolve()
                expected_roots = {
                    impl_resolved,
                    (impl_root / "public").resolve(),
                    (impl_root / "src").resolve(),
                }
                if rec_path not in expected_roots:
                    try:
                        rec_path.relative_to(impl_resolved)
                        recorded_inside = True
                    except ValueError:
                        recorded_inside = False
                    if not recorded_inside:
                        msg = (
                            f"{check_id} — loop path contamination. "
                            f"artifact recorded {recorded}, but current "
                            f"impl_root is {impl_root}. Run the check "
                            "against the active loop's impl tree."
                        )
                        out.append(CheckResult(label, "fail", msg, fix=fix))
                        continue
                # Stale-relative check — artifact in-tree but older than
                try:
                    artifact_path = self.ref_dir / produces
                    if artifact_path.is_file():
                        artifact_mtime = artifact_path.stat().st_mtime
                        newest_impl = 0.0
                        for sub in ("src", "public"):
                            sub_dir = impl_root / sub
                            if sub_dir.is_dir():
                                for p in sub_dir.rglob("*"):
                                    try:
                                        if p.is_file():
                                            m = p.stat().st_mtime
                                            if m > newest_impl:
                                                newest_impl = m
                                    except OSError:
                                        continue
                        if newest_impl > artifact_mtime + 1.0:
                            msg = (
                                f"{check_id} — stale artifact. "
                                f"{produces} mtime is older than "
                                "newest impl source/public file by "
                                f"{newest_impl - artifact_mtime:.0f}s. "
                                "Re-run the check against the current impl."
                            )
                            out.append(CheckResult(label, "fail", msg, fix=fix))
                            continue
                except OSError:
                    pass
        STATUS_REQUIRED = {
            "asset-transfer", "asset-utilization", "asset-placement", "image-fidelity",
            "font-parity", "dom-mirror-check", "text-fidelity",
            "hydration-check", "transition-spec-coverage",
            "spec-implementation-coverage", "runtime-spec-coverage",
            "tree-diff", "scroll-end-completion", "reveal-trigger",
            "transition-fires",
            "boundary",
            "tailwind-transform-conflict", "proxy-mirror-check",
            "lottie-runtime", "bundle-impl-coverage", "scroll-coverage",
            "runtime-image-validity", "remote-asset-ref",
            "capture-artifact-inventory",
            "ref-screenshot-asset",
            # Common cheat pattern A1/A2/A3 anti-cheat — entry-coherence
            # (stack/entry consistency), scaffold-residue (orphan
            # components), html-paste (structural/script/CSS theft).
            "entry-coherence", "scaffold-residue", "html-paste",
            # Diagnosis B — required-media (video/Lottie) coverage.
            "required-media-coverage",
            # Common cheat pattern A4/A5 + fix #2 anti-cheat —
            # css-mirror (static), runtime-dom-parity (runtime
            # positive parity), hidden-children (runtime hidden
            # DOM with screenshot background overlay).
            "css-mirror", "runtime-dom-parity", "hidden-children",
            "invalidation",
            # Signal 1 — scaffold-warn placeholders.
            "scaffold-warn",
            "svg-dom-parity",
            # validation run findings — monolithic-impl + motion-coverage.
            "monolithic-impl", "motion-coverage",
            "scroll-engine-parity",
        }
        if status == "pass":
            out.append(CheckResult(label, "pass", f"{check_id} (status: pass)"))
        elif status is None:
            if check_id in STATUS_REQUIRED:
                msg = (
                    f"{check_id} — artifact present but `status` field "
                    "is absent. Known checks must declare status; missing "
                    "status is the audit incident 'check produced JSON but never "
                    "ran the assertion' gaming pattern."
                )
                out.append(CheckResult(label, "fail", msg, fix=fix))
                continue
            out.append(CheckResult(label, "pass", f"{check_id} (artifact present, no status field)"))
        elif str(status).lower() == "skip":
            # A check reports skip when its prerequisites aren't met
            # (no signal in ref, below floor, gate does not apply).
            # That's a no-op verdict, not a block — treat as pass with
            # the skip reason preserved so it's visible in the gate
            # output. Setup-error skips ("impl_root not found",
            # "agent-browser missing") would otherwise have already
            # failed earlier in run-required-checks.sh.
            skip_msg = f"{check_id} (skipped: {reason})" if reason else f"{check_id} (skipped)"
            out.append(CheckResult(label, "pass", skip_msg))
        else:
            count = (data.get("errorCount") or data.get("failureCount") or
                     data.get("totalStuck") or "?") if isinstance(data, dict) else "?"
            msg = f"{check_id} — status: {status} ({count} issue(s)). Reason: {reason}"
            if str(status).lower() == "warn":
                out.append(CheckResult(label, "warn", msg))
            elif severity == "warn":
                out.append(CheckResult(label, "warn", msg))
            else:
                out.append(CheckResult(label, "fail", msg, fix=fix))

    return out


def _transition_spec_count(self: Gate) -> int:
    """Number of declared transitions in transition-spec.json.

    Used to gate "transition-compare must have measurement rows" — only
    applies when the spec actually declared transitions to compare.
    """
    spec = self.ref_dir / "transition-spec.json"
    if not spec.is_file():
        return 0
    try:
        data = json.loads(spec.read_text(encoding="utf-8"))
        transitions = data.get("transitions") if isinstance(data, dict) else None
        if isinstance(transitions, list):
            return len(transitions)
    except (json.JSONDecodeError, OSError):
        pass
    return 0


def _tree_diff_floor(self: Gate) -> int:
    """Minimum elements_walked tree-diff must achieve to be meaningful.

    Cross-reference section-map.json. The floor is max(30, sections * 5):
    a real page averages ≥5 visible elements per section (heading, sub-
    head, paragraph, button, image at minimum). Below 30 absolute, any
    page is too sparse to call tree-diff a real measurement.
    """
    section_map = self.ref_dir / "section-map.json"
    section_count = 0
    if section_map.is_file():
        try:
            data = json.loads(section_map.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                sections = data.get("sections") or []
                if isinstance(sections, list):
                    section_count = len(sections)
            elif isinstance(data, list):
                section_count = len(data)
        except (json.JSONDecodeError, OSError):
            section_count = 0
    return max(30, section_count * 5)
