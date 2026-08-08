import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_extraction_does_not_require_transition_coverage(tmp_path: Path) -> None:
    """gate_extraction must pass without transition-coverage.json.

    transition-coverage.json is produced at Step 6d, after bundle (5c) and spec (5d).
    Requiring it at the extraction gate (which runs after Step 2-3) would deadlock
    the pipeline — extraction can never advance until 6d, but 6d depends on bundle,
    which depends on extraction having passed. Coverage of transition-coverage.json
    belongs to gate_pre_generate (see test_gate_pre_generate_*).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    for fname in [
        "structure.json",
        "head.json",
        "styles.json",
        "fonts.json",
        "visible-images.json",
        "inline-svgs.json",
        "body-state.json",
        "design-bundles.json",
    ]:
        (ref / fname).write_text(json.dumps({}))
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "variables.txt").write_text(":root {}")
    # transition-coverage.json intentionally omitted

    gate = Gate(ref)
    results = gate.gate_extraction()
    failures = [r for r in results if r.status == "fail"]
    labels = [r.label for r in failures]
    assert not any("transition-coverage" in lbl for lbl in labels), (
        "gate_extraction must not require transition-coverage.json (Step 6d artifact)"
    )


def test_gate_extraction_finalizes_explicit_empty_phase2_artifacts(tmp_path: Path) -> None:
    """Loop-03 regression: explicit absence must not look like skipped extraction.

    A Phase 2 fast path can have enough source evidence to prove there are no
    inline SVGs or CSS custom properties, while still leaving `inline-svgs.json`
    as a double-encoded empty array and `css/variables.txt` at zero bytes. The
    extraction gate should normalize those into canonical sentinel artifacts and
    derive body/design summaries from structure/CSS before checking file size.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body",
        "class": "home scrolled",
        "styles": {
            "transition": "background-color 0.3s ease",
            "background-color": "rgb(255, 255, 255)",
            "color": "rgb(0, 0, 0)",
        },
        "children": [
            {
                "tag": "button",
                "class": "cta",
                "styles": {
                    "background-color": "rgb(0, 199, 60)",
                    "border-radius": "999px",
                    "padding": "12px 24px",
                    "font-size": "16px",
                    "font-weight": "700",
                    "font-family": "Arial",
                },
            },
            {
                "tag": "button",
                "class": "cta",
                "styles": {
                    "background-color": "rgb(0, 199, 60)",
                    "border-radius": "999px",
                    "padding": "12px 24px",
                    "font-size": "16px",
                    "font-weight": "700",
                    "font-family": "Arial",
                },
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "head.json").write_text(json.dumps({"title": "Test"}))
    (ref / "styles.json").write_text(json.dumps({"body": {"color": "rgb(0, 0, 0)"}}))
    (ref / "fonts.json").write_text(json.dumps({"faces": []}))
    (ref / "visible-images.json").write_text(json.dumps({"images": []}))
    # Matches the loop-03 artifact shape: JSON string whose value is an empty array.
    (ref / "inline-svgs.json").write_text(json.dumps("[]"))
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "app.css").write_text(
        "body.scrolled { background: #000; color: #fff; } .cta { color: #111; }"
    )
    (css_dir / "variables.txt").write_text("")

    results = Gate(ref).gate_extraction()
    failures = [r for r in results if r.status == "fail"]

    assert failures == []
    inline_svgs = json.loads((ref / "inline-svgs.json").read_text())
    assert inline_svgs["observation"] == "no-inline-svgs"
    assert inline_svgs["svgs"] == []
    variables_txt = (css_dir / "variables.txt").read_text()
    assert "no CSS custom properties observed" in variables_txt
    variables_json = json.loads((css_dir / "variables.json").read_text())
    assert variables_json["observation"] == "no-css-custom-properties"
    body_state = json.loads((ref / "body-state.json").read_text())
    assert body_state["currentBodyClasses"] == "home scrolled"
    assert any("body.scrolled" in row["selector"] for row in body_state["bodyClassRules"])
    design_bundles = json.loads((ref / "design-bundles.json").read_text())
    assert design_bundles["summary"]["bundleCount"] >= 1


def _write_minimal_extraction_artifacts(ref: Path) -> None:
    """Plant minimum extraction artifacts so unclonable-preflight tests
    aren't tripped by missing-file failures from earlier checks."""
    for fname in [
        "head.json", "styles.json", "fonts.json",
        "visible-images.json", "inline-svgs.json", "body-state.json",
        "design-bundles.json",
    ]:
        (ref / fname).write_text(json.dumps({}))
    css_dir = ref / "css"
    css_dir.mkdir(exist_ok=True)
    (css_dir / "variables.txt").write_text(":root {}")


def test_unclonable_preflight_detects_auth_gated(tmp_path: Path) -> None:
    """Codex Fix 3 (2026-05-27): a structure.json carrying <form> with
    <input type='password'> is a login wall. Pipeline must short-circuit
    via record_unclonable instead of burning iterations against an
    auth-walled page."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_minimal_extraction_artifacts(ref)
    structure = {
        "tag": "html",
        "children": [{
            "tag": "body",
            "children": [{
                "tag": "form",
                "children": [
                    {"tag": "input", "type": "email"},
                    {"tag": "input", "type": "password"},
                    {"tag": "button", "text": "Sign in"},
                ],
            }],
        }],
    }
    (ref / "structure.json").write_text(json.dumps(structure))

    gate = Gate(ref)
    results = gate.gate_extraction()
    preflight = [r for r in results if r.label == "unclonable-preflight" and r.status == "fail"]
    assert preflight, (
        f"auth-gated structure.json must emit unclonable-preflight fail; "
        f"got: {[(r.label, r.status) for r in results]}"
    )
    assert "auth-gated" in preflight[0].message, "fail message should classify as auth-gated"

    # record_unclonable must have persisted to state.json
    from ui_clone.state import PipelineState
    state = PipelineState.load(ref)
    auth_entries = [
        e for e in state.unclonable_reasons
        if (e.get("category") if isinstance(e, dict) else None) == "auth-gated"
    ]
    assert auth_entries, (
        f"record_unclonable(category='auth-gated') must persist; "
        f"got: {state.unclonable_reasons}"
    )


def test_unclonable_preflight_detects_drm_canvas(tmp_path: Path) -> None:
    """Codex Fix 3: canvas-dominant page with sparse DOM text is a DRM
    surface that section-compare cannot clone. Short-circuit via
    record_unclonable with category 'drm-canvas'."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_minimal_extraction_artifacts(ref)
    structure = {
        "tag": "html",
        "children": [{
            "tag": "body",
            "children": [{
                "tag": "canvas",
                # canvas covering most of a 1440x900 viewport equivalent
                "styles": {"width": "1440", "height": "900"},
            }],
        }],
    }
    (ref / "structure.json").write_text(json.dumps(structure))

    gate = Gate(ref)
    results = gate.gate_extraction()
    preflight = [r for r in results if r.label == "unclonable-preflight" and r.status == "fail"]
    assert preflight, (
        f"DRM-canvas structure.json must emit unclonable-preflight fail; "
        f"got: {[(r.label, r.status) for r in results]}"
    )
    assert "drm-canvas" in preflight[0].message

    from ui_clone.state import PipelineState
    state = PipelineState.load(ref)
    drm_entries = [
        e for e in state.unclonable_reasons
        if (e.get("category") if isinstance(e, dict) else None) == "drm-canvas"
    ]
    assert drm_entries, "record_unclonable(category='drm-canvas') must persist"


def test_unclonable_preflight_quiet_on_normal_page(tmp_path: Path) -> None:
    """Codex Fix 3 false-positive guard: a normal product page with no
    password input and no dominant canvas must NOT emit unclonable-
    preflight. Models a juanmora-shape page (lots of nodes, lots of text,
    no auth or DRM signals)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_minimal_extraction_artifacts(ref)
    structure = {
        "tag": "html",
        "children": [{
            "tag": "body",
            "children": [
                {"tag": "header", "text": "Welcome to the product"},
                {"tag": "main", "children": [
                    {"tag": "section", "text": "Lorem ipsum dolor sit amet " * 30},
                    {"tag": "section", "text": "Consectetur adipiscing elit " * 30},
                ]},
            ],
        }],
    }
    (ref / "structure.json").write_text(json.dumps(structure))

    gate = Gate(ref)
    results = gate.gate_extraction()
    preflight = [r for r in results if r.label == "unclonable-preflight"]
    assert not preflight, (
        f"normal product page must NOT trigger unclonable-preflight; "
        f"got: {[(r.label, r.status, r.message[:60]) for r in preflight]}"
    )
