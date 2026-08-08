"""Tests for ui_clone.policies.canvas_replay_auto — the AUTOMATIC technical
route (detection → plan) that decides whether a WebGL/canvas hero must fall
back to a recorded <video> replay because a live re-embed is impossible.

This is distinct from canvas_replay.py (the manual operator-attestation
closeout policy). The auto module is the deterministic routing signal: given
ref canvas detection + impl canvas count + a re-embed origin-lock probe, it
decides replay vs. none and emits canvas-replay-plan.json.
"""
from __future__ import annotations

from ui_clone.policies import canvas_replay_auto as cra


def _webgl_detection(canvas_count: int = 1, render: str = "webgl") -> dict:
    return {
        "schemaVersion": 1,
        "url": "https://www.raviklaassens.com/",
        "primaryRenderType": render,
        "hasCanvas": canvas_count > 0,
        "hasWebGL": render == "webgl",
        "canvasCount": canvas_count,
        "canvases": [{"index": 0, "width": 1440, "height": 900, "hasWebGL": True}],
    }


# ── needs_canvas_replay decision ──────────────────────────────────────────


def test_origin_locked_webgl_hero_needs_replay() -> None:
    needed, reason = cra.needs_canvas_replay(
        _webgl_detection(), impl_canvas_count=1, reembed_blocked=True
    )
    assert needed is True
    assert reason == "origin-lock"


def test_blank_impl_against_webgl_ref_needs_replay() -> None:
    needed, reason = cra.needs_canvas_replay(
        _webgl_detection(), impl_canvas_count=0, reembed_blocked=False
    )
    assert needed is True
    assert reason == "blank"


def test_reembed_ok_does_not_need_replay() -> None:
    needed, reason = cra.needs_canvas_replay(
        _webgl_detection(), impl_canvas_count=1, reembed_blocked=False
    )
    assert needed is False
    assert reason == "reembed-ok"


def test_dom_ref_never_needs_replay() -> None:
    detection = _webgl_detection(canvas_count=0, render="DOM")
    needed, reason = cra.needs_canvas_replay(
        detection, impl_canvas_count=0, reembed_blocked=True
    )
    assert needed is False
    assert reason == "ref-not-canvas"


def test_canvas2d_ref_origin_locked_needs_replay() -> None:
    detection = _webgl_detection(canvas_count=2, render="canvas")
    needed, reason = cra.needs_canvas_replay(
        detection, impl_canvas_count=2, reembed_blocked=True
    )
    assert needed is True
    assert reason == "origin-lock"


# ── reembed_blocked_from_status: origin-lock probe ────────────────────────


def test_403_cross_origin_is_blocked() -> None:
    assert cra.reembed_blocked_from_status(403) is True


def test_401_and_404_and_5xx_blocked() -> None:
    assert cra.reembed_blocked_from_status(401) is True
    assert cra.reembed_blocked_from_status(404) is True
    assert cra.reembed_blocked_from_status(503) is True


def test_network_error_status_zero_blocked() -> None:
    # status 0 == fetch threw (CORS / network) → not re-embeddable
    assert cra.reembed_blocked_from_status(0) is True


def test_200_is_not_blocked() -> None:
    assert cra.reembed_blocked_from_status(200) is False


# ── build_replay_plan: artifact emission ──────────────────────────────────


def test_plan_declares_replayed_section_when_needed() -> None:
    plan = cra.build_replay_plan(
        url="https://www.raviklaassens.com/",
        detection=_webgl_detection(),
        impl_canvas_count=0,
        reembed_blocked=True,
        section="sec-2",
        ref_canvas_selector="canvas",
        region={"x": 0, "y": 0, "width": 1440, "height": 900},
        replay_asset="public/canvas-replay/hero.webm",
        poster="public/canvas-replay/hero-poster.png",
    )
    assert plan["decision"] == "canvas-replay"
    assert plan["schemaVersion"] == 1
    assert plan["url"] == "https://www.raviklaassens.com/"
    assert len(plan["sections"]) == 1
    s = plan["sections"][0]
    assert s["section"] == "sec-2"
    assert s["reason"] == "origin-lock"
    assert s["refCanvasSelector"] == "canvas"
    assert s["region"]["width"] == 1440
    assert s["replayAsset"] == "public/canvas-replay/hero.webm"
    assert s["poster"] == "public/canvas-replay/hero-poster.png"


def test_plan_decision_none_when_reembed_ok() -> None:
    plan = cra.build_replay_plan(
        url="https://www.raviklaassens.com/",
        detection=_webgl_detection(),
        impl_canvas_count=1,
        reembed_blocked=False,
        section="sec-2",
        ref_canvas_selector="canvas",
        region={"x": 0, "y": 0, "width": 1440, "height": 900},
        replay_asset="public/canvas-replay/hero.webm",
        poster="public/canvas-replay/hero-poster.png",
    )
    assert plan["decision"] == "none"
    assert plan["sections"] == []


def test_replay_video_satisfies_blank_hero_when_declared_and_advancing() -> None:
    plan = cra.build_replay_plan(
        url="x", detection=_webgl_detection(), impl_canvas_count=0,
        reembed_blocked=True, section="sec-2", ref_canvas_selector="canvas",
        region={"x": 0, "y": 0, "width": 1440, "height": 900},
        replay_asset="public/canvas-replay/hero.webm",
        poster="public/canvas-replay/hero-poster.png",
    )
    # A declared replay whose <video> advanced frames → hero is NOT blank.
    assert cra.replay_satisfies_blank_hero(plan, video_advanced=1) is True


def test_replay_does_not_satisfy_when_video_not_advancing() -> None:
    plan = cra.build_replay_plan(
        url="x", detection=_webgl_detection(), impl_canvas_count=0,
        reembed_blocked=True, section="sec-2", ref_canvas_selector="canvas",
        region={"x": 0, "y": 0, "width": 1440, "height": 900},
        replay_asset="public/canvas-replay/hero.webm",
        poster="public/canvas-replay/hero-poster.png",
    )
    # Declared but the <video> is stalled / not playing → still a blank hero.
    assert cra.replay_satisfies_blank_hero(plan, video_advanced=0) is False


def test_replay_does_not_satisfy_without_declared_plan() -> None:
    # No plan (or decision none) → a bare <video> must NOT silence the gate.
    assert cra.replay_satisfies_blank_hero(None, video_advanced=5) is False
    assert cra.replay_satisfies_blank_hero({"decision": "none"}, 5) is False


def test_plan_declares_asset_substitution_entry() -> None:
    # The replay asset must be declared so anti-cheat understands it is the
    # ref's OWN recorded motion, not a static screenshot-as-background cheat.
    plan = cra.build_replay_plan(
        url="https://www.raviklaassens.com/",
        detection=_webgl_detection(),
        impl_canvas_count=0,
        reembed_blocked=True,
        section="sec-2",
        ref_canvas_selector="canvas",
        region={"x": 0, "y": 0, "width": 1440, "height": 900},
        replay_asset="public/canvas-replay/hero.webm",
        poster="public/canvas-replay/hero-poster.png",
    )
    decl = cra.asset_substitution_entry(plan)
    assert decl is not None
    assert decl["kind"] == "canvas-replay-video"
    assert "recorded" in decl["reason"].lower()
    assert decl["replacementSrc"] == "public/canvas-replay/hero.webm"


# ── interactive-physics behavioral-repro route (Wave 6) ────────────────────


def _physics_detection(engine: str = "matter-js", live: dict | None = None) -> dict:
    return {
        "schemaVersion": 1,
        "url": "https://brm.io/matter-js/",
        "primaryRenderType": "canvas",
        "renderKind": "interactive-physics",
        "hasCanvas": True,
        "hasWebGL": False,
        "hasPhysics": True,
        "physicsEngine": {
            "name": engine,
            "version": "0.20.0",
            "source": "runtime-global",
            "liveEngine": live,
        },
        "canvasCount": 1,
    }


def _plan(det: dict, *, impl: int = 0, blocked: bool = True) -> dict:
    return cra.build_replay_plan(
        url=det.get("url", "http://x"),
        detection=det,
        impl_canvas_count=impl,
        reembed_blocked=blocked,
        section="hero",
        ref_canvas_selector="canvas",
        region={},
        replay_asset="public/canvas-replay/hero.webm",
        poster="public/canvas-replay/hero-poster.png",
    )


def test_physics_canvas_routes_to_behavioral_repro() -> None:
    plan = _plan(_physics_detection())
    assert plan["decision"] == "behavioral-repro"
    assert plan["reason"] == "interactive-physics"
    assert plan["behavioralRepro"]["engine"] == "matter-js"
    # No video-replay section is declared for physics.
    assert plan["sections"] == []


def test_behavioral_repro_denied_blank_hero_pass() -> None:
    # A physics canvas must NOT get the blank-hero video relief: a blank impl
    # means the physics never ran, which has to fail — even if a <video> is
    # somehow advancing.
    plan = _plan(_physics_detection())
    assert cra.replay_satisfies_blank_hero(plan, video_advanced=9) is False


def test_behavioral_repro_declares_no_replay_asset() -> None:
    plan = _plan(_physics_detection())
    assert cra.asset_substitution_entry(plan) is None


def test_physics_repro_reads_runtime_gravity() -> None:
    live = {"handle": "engine", "gravity": {"x": 0, "y": 1, "scale": 0.001}, "bodyCount": 42}
    plan = _plan(_physics_detection(live=live))
    repro = plan["behavioralRepro"]
    assert repro["constantsSource"] == "runtime-engine"
    assert repro["constants"]["gravity"]["y"] == 1
    assert repro["constants"]["bodyCount"] == 42


def test_physics_repro_none_without_engine_descriptor() -> None:
    # hasPhysics true but no engine name → not a usable physics descriptor,
    # falls through to the normal replay/none decision (no false behavioral).
    det = _physics_detection()
    det["physicsEngine"] = {"name": None}
    assert cra.physics_repro(det) is None


def test_shader_canvas_still_video_replays_not_bricked() -> None:
    # BRICK GUARD: a decorative WebGL shader (no physics engine) keeps the
    # existing video-replay route — behavioral-repro must not swallow it.
    det = _webgl_detection(render="webgl")
    det["hasPhysics"] = False
    det["physicsEngine"] = None
    plan = _plan(det, impl=0, blocked=True)
    assert plan["decision"] == "canvas-replay"
    assert plan["reason"] == "origin-lock"


def test_physics_without_canvas_surface_not_routed() -> None:
    # BRICK GUARD: hasPhysics set but no canvas surface (physics-lib bundled,
    # DOM/SVG-driven) must NOT route to behavioral-repro.
    det = _physics_detection()
    det["hasCanvas"] = False
    det["canvasCount"] = 0
    det["primaryRenderType"] = "DOM"
    assert cra.physics_repro(det) is None
