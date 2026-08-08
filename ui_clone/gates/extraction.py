"""Extraction gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


# Unclonable-preflight thresholds. Require corroborating structural signals so
# large ordinary product pages do not trip the auth-gated or DRM-canvas
# shortcuts.
_AUTH_GATED_REQUIRES_FORM = True       # require <form> wrapping the password input
_DRM_CANVAS_AREA_RATIO = 0.5           # canvas must cover >=50% of a 1440x900 ref viewport
_DRM_CANVAS_TEXT_CHARS_MAX = 200       # body text < 200 chars → DOM-poor
_DRM_VIEWPORT_AREA = 1440 * 900


def _check_unclonable_preflight(self: Gate) -> CheckResult | None:
    """Detect terminal-unclonable shapes from structure.json and short-circuit
    via `record_unclonable` BEFORE the pipeline burns iterations.

    Per the fail-closed architecture review: this is a SUBCHECK inside the
    existing extraction gate, NOT a new GATE_ORDER entry — adding a gate
    would fragment the closeout-policy logic (canonical / structural-only /
    canvas-replay) that is centralized around `record_unclonable`.

    Categories detected:
    - auth-gated: <form> containing <input type="password"> in the baseline
      structure → URL points at a login wall, not the target product
    - drm-canvas: <canvas> covering >=50% of a 1440x900 viewport equivalent
      AND body text content < 200 chars → DOM-clone strategy is structurally
      wrong; user should switch to canvas-replay closeoutPolicy or embed
      the same widget runtime in impl

    Returns None when neither shape is detected; the existing file-presence
    checks in gate_extraction continue normally.
    """
    import json

    from ui_clone.state import PipelineState

    structure_path = self.ref_dir / "structure.json"
    if not structure_path.is_file():
        return None
    try:
        data = json.loads(structure_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    counts = {
        "total": 0,
        "canvas": 0,
        "password_input": 0,
        "form": 0,
        "text_chars": 0,
    }
    canvas_areas: list[int] = []

    def _node_area(node: dict) -> int:
        styles = node.get("styles") or {}
        w_raw = str(styles.get("width") or "").strip().rstrip("px")
        h_raw = str(styles.get("height") or "").strip().rstrip("px")
        try:
            w = int(float(w_raw)) if w_raw else 0
            h = int(float(h_raw)) if h_raw else 0
        except ValueError:
            return 0
        return max(0, w) * max(0, h)

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        tag = (node.get("tag") or "").lower()
        counts["total"] += 1
        if tag == "canvas":
            counts["canvas"] += 1
            area = _node_area(node)
            if area > 0:
                canvas_areas.append(area)
        elif tag == "input":
            t = node.get("type") or ((node.get("attrs") or {}).get("type") or "")
            if str(t).lower() == "password":
                counts["password_input"] += 1
        elif tag == "form":
            counts["form"] += 1
        text = node.get("text") or node.get("textContent") or ""
        if isinstance(text, str):
            counts["text_chars"] += len(text.strip())
        for c in node.get("children") or []:
            walk(c)

    walk(data)

    # auth-gated detection
    auth_gated = counts["password_input"] >= 1 and (
        not _AUTH_GATED_REQUIRES_FORM or counts["form"] >= 1
    )
    if auth_gated:
        try:
            state = PipelineState.load(self.ref_dir)
            state.record_unclonable(
                gate="extraction",
                reason=(
                    f"Baseline page contains a login wall: "
                    f"{counts['password_input']} password input(s) "
                    f"inside {counts['form']} form(s). The visible content "
                    f"is the auth surface, not the target product."
                ),
                ref_dir=self.ref_dir,
                category="auth-gated",
                detail={
                    "password_input_count": counts["password_input"],
                    "form_count": counts["form"],
                    "total_nodes": counts["total"],
                },
                fallback_suggestions=[
                    "Use a URL that renders the target product without auth.",
                    "Capture the post-login state externally (e.g. via "
                    "agent-browser with session cookies pre-loaded) and "
                    "re-run the pipeline on the captured static export.",
                ],
            )
        except Exception:
            # Recording is best-effort; the FAIL CheckResult below is the
            # primary signal even if state I/O failed.
            pass
        return CheckResult(
            "unclonable-preflight",
            "fail",
            (
                f"auth-gated: baseline structure.json shows "
                f"{counts['password_input']} password input(s) in "
                f"{counts['form']} form(s). Recorded as unclonable."
            ),
            fix=(
                "Use a public URL, or pre-capture the post-login state and "
                "re-run on the static export."
            ),
        )

    # drm-canvas detection (canvas-dominant + text-poor)
    canvas_dominant = (
        counts["canvas"] >= 1
        and any(a > _DRM_VIEWPORT_AREA * _DRM_CANVAS_AREA_RATIO for a in canvas_areas)
        and counts["text_chars"] < _DRM_CANVAS_TEXT_CHARS_MAX
    )
    if canvas_dominant:
        try:
            state = PipelineState.load(self.ref_dir)
            state.record_unclonable(
                gate="extraction",
                reason=(
                    f"Baseline page is canvas-dominant: {counts['canvas']} "
                    f"<canvas> element(s), only {counts['text_chars']} text "
                    f"chars in DOM. A static-DOM clone strategy cannot "
                    f"match a canvas-rendered surface."
                ),
                ref_dir=self.ref_dir,
                category="drm-canvas",
                detail={
                    "canvas_count": counts["canvas"],
                    "text_chars": counts["text_chars"],
                    "canvas_max_area": max(canvas_areas) if canvas_areas else 0,
                    "total_nodes": counts["total"],
                },
                fallback_suggestions=[
                    "Switch closeoutPolicy to 'canvas-replay' so the gate "
                    "validates via canvas-replay proof instead of section-"
                    "compare AE — see skills/ui-reverse-engineering/"
                    "canvas-replay-mode.md.",
                    "If the canvas is rendered by a known SaaS widget "
                    "(Spline, Rive, UnicornStudio, Lottie), embed the same "
                    "widget runtime in the impl and let it render natively.",
                ],
            )
        except Exception:
            pass
        return CheckResult(
            "unclonable-preflight",
            "fail",
            (
                f"drm-canvas: {counts['canvas']} canvas element(s) "
                f"dominate the page (max area >{int(_DRM_VIEWPORT_AREA * _DRM_CANVAS_AREA_RATIO)} px²) "
                f"with only {counts['text_chars']} text chars. "
                f"Recorded as unclonable."
            ),
            fix=(
                "Switch closeoutPolicy to 'canvas-replay', or embed the "
                "same canvas-rendering widget in the impl."
            ),
        )

    return None


def gate_extraction(self: Gate) -> list[CheckResult]:
    results = []
    try:
        from ui_clone.extraction_artifacts import finalize_extraction_artifacts

        finalize_extraction_artifacts(self.ref_dir)
    except Exception as exc:  # pragma: no cover - defensive gate hardening
        results.append(
            CheckResult(
                "extraction-artifact-finalizer",
                "warn",
                f"extraction artifact finalizer skipped: {exc}",
            )
        )

    for filename, label in [
        ("structure.json", "structure.json (DOM hierarchy)"),
        ("head.json", "head.json (metadata)"),
        ("styles.json", "styles.json (computed styles)"),
        ("fonts.json", "fonts.json (font faces)"),
        ("visible-images.json", "visible-images.json"),
        ("inline-svgs.json", "inline-svgs.json"),
        ("body-state.json", "body-state.json"),
        ("design-bundles.json", "design-bundles.json"),
    ]:
        results.append(
            self.check_file(
                self.ref_dir / filename,
                label,
                allow_empty_array=filename == "inline-svgs.json",
            )
        )

    results.append(
        self.check_file(
            self.ref_dir / "css" / "variables.txt", "css/variables.txt (CSS custom properties)"
        )
    )

    # Viewport-scaled font em-conversion gate
    typo = self._load_json("typography.json")
    if typo:
        scaling = typo.get("scalingSystem", "")
        if scaling and any(k in scaling.lower() for k in ("viewport-scaled", "em-based")):
            results.append(
                self.check_file(
                    self.ref_dir / "em-conversion.json",
                    f"em-conversion.json (REQUIRED: scalingSystem={scaling})",
                )
            )

    # Unclonable-preflight fix: early unclonable preflight. Detects auth-
    # gated and DRM-canvas shapes from structure.json so the pipeline
    # short-circuits to a canonical unclonable_reason entry instead of
    # burning iterations on a structurally-unmatchable target.
    preflight = _check_unclonable_preflight(self)
    if preflight is not None:
        results.append(preflight)

    return results
