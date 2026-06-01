"""Canvas-replay AUTO-routing — the automatic technical path.

Distinct from `canvas_replay.py` (the manual operator-attestation *closeout*
policy). This module is the deterministic *routing* signal: it decides whether
a WebGL/canvas hero must fall back to a recorded `<video>` replay because a
live re-embed is impossible.

Two triggers route a section to canvas-replay:

  1. **origin-lock** — the ref's canvas-driving scene is served from the ref's
     own CDN and 403s / fails CORS when loaded cross-origin (e.g. a Unicorn
     Studio scene on ``*.b-cdn.net`` bound to the ref domain). The impl cannot
     re-embed the live engine, so it would ship a blank hero.
  2. **blank** — the impl renders 0 canvases where the ref renders a WebGL/
     canvas surface (engine init failed / SDK not bundled), again leaving a
     blank hero.

In both cases the honest fallback is to record the REFERENCE's own rendered
canvas output to a short looped video and emit a ``<video>`` replay — the
ref's own pixels, declared as a substituted asset (NOT a static
screenshot-as-background cheat). Static, reproducible sections never route
here; this is only for genuinely-unreproducible WebGL/canvas heroes.
"""
from __future__ import annotations

# HTTP statuses that mean the scene cannot be re-embedded cross-origin.
# 0 == fetch threw (CORS / network failure). >=400 covers auth/forbidden/
# not-found/server errors. Anything else (2xx/3xx) is considered loadable.
_BLOCKED_MIN_STATUS = 400


def ref_is_canvas_driven(detection: dict) -> bool:
    """True iff the ref detection artifact shows a genuine canvas/WebGL
    rendering surface — canvasCount>0 or a canvas/webgl primaryRenderType.
    """
    if not isinstance(detection, dict):
        return False
    try:
        if int(detection.get("canvasCount", 0)) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return str(detection.get("primaryRenderType", "")).strip().lower() in {
        "webgl",
        "canvas",
    }


def reembed_blocked_from_status(status_code: int) -> bool:
    """Map a cross-origin probe HTTP status to a re-embed-blocked boolean.

    The plan driver fetches the canvas scene src cross-origin (from a non-ref
    origin). 403/401/CORS-throw (status 0) / 4xx / 5xx all mean the live
    engine cannot be re-embedded in the clone.
    """
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return True
    if code == 0:
        return True
    return code >= _BLOCKED_MIN_STATUS


def needs_canvas_replay(
    detection: dict,
    impl_canvas_count: int | None,
    reembed_blocked: bool,
) -> tuple[bool, str]:
    """Core routing decision.

    Returns (needed, reason). Reasons:
      - "ref-not-canvas": ref has no canvas/WebGL surface → never replay.
      - "origin-lock":   ref is canvas-driven AND re-embed is blocked.
      - "blank":         ref is canvas-driven AND impl renders 0 canvases.
      - "reembed-ok":    ref is canvas-driven, re-embed works, impl has canvas.
    """
    if not ref_is_canvas_driven(detection):
        return (False, "ref-not-canvas")
    if reembed_blocked:
        return (True, "origin-lock")
    if (impl_canvas_count or 0) == 0:
        return (True, "blank")
    return (False, "reembed-ok")


def build_replay_plan(
    *,
    url: str,
    detection: dict,
    impl_canvas_count: int | None,
    reembed_blocked: bool,
    section: str,
    ref_canvas_selector: str,
    region: dict,
    replay_asset: str,
    poster: str,
) -> dict:
    """Build the canvas-replay-plan.json artifact dict.

    When replay is needed, ``sections`` carries one declared entry naming the
    ref canvas selector/region, the trigger reason, and the recorded replay
    asset + poster the generator must emit. When not needed, ``decision`` is
    "none" and ``sections`` is empty.
    """
    needed, reason = needs_canvas_replay(
        detection, impl_canvas_count, reembed_blocked
    )
    sections: list[dict] = []
    if needed:
        sections.append(
            {
                "section": section,
                "refCanvasSelector": ref_canvas_selector,
                "region": region,
                "reason": reason,
                "replayAsset": replay_asset,
                "poster": poster,
            }
        )
    return {
        "schemaVersion": 1,
        "generatedBy": "ui_clone.policies.canvas_replay_auto",
        "url": url,
        "decision": "canvas-replay" if needed else "none",
        "reason": reason,
        "implCanvasCount": int(impl_canvas_count or 0),
        "reembedBlocked": bool(reembed_blocked),
        "sections": sections,
    }


def replay_satisfies_blank_hero(plan: dict | None, video_advanced: int) -> bool:
    """Gate-coherence predicate for runtime-frame-proof / transition-fires.

    A 0-canvas impl is normally a blank-hero FAIL when the ref renders WebGL/
    canvas. But when a canvas-replay plan declares this section AND the impl's
    ``<video>`` replay is actually advancing frames (currentTime moved, non-
    blank), the hero now renders the ref's OWN recorded motion — so it is NOT
    blank and the gate must pass.

    Requires BOTH a declared plan and a demonstrably-playing video — a bare
    ``<video>`` with no plan, or a stalled video, must NOT silence the gate.
    """
    if not isinstance(plan, dict) or plan.get("decision") != "canvas-replay":
        return False
    return int(video_advanced or 0) > 0


def asset_substitution_entry(plan: dict) -> dict | None:
    """Derive the asset-substitution.json declaration for the replay video so
    anti-cheat understands the asset is the ref's OWN recorded motion (a
    declared substituted asset), not a static screenshot-as-CSS-background.

    Returns None when the plan does not route to replay.
    """
    if not isinstance(plan, dict) or plan.get("decision") != "canvas-replay":
        return None
    sections = plan.get("sections") or []
    if not sections:
        return None
    s = sections[0]
    return {
        "kind": "canvas-replay-video",
        "originalSrc": f"{s.get('refCanvasSelector', 'canvas')} (origin-locked "
        f"WebGL/canvas scene on ref CDN)",
        "replacementSrc": s.get("replayAsset", ""),
        "section": s.get("section", ""),
        "reason": (
            "Hero canvas/WebGL scene cannot be re-embedded "
            f"({s.get('reason', 'origin-lock')}). The reference's OWN rendered "
            "canvas output was recorded to a short looped video and replayed "
            "via <video> — declared substituted asset, the ref's own pixels in "
            "motion (like recording a video background), NOT a static "
            "screenshot used as a CSS background."
        ),
    }
