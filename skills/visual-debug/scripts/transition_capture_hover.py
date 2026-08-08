#!/usr/bin/env python3
"""Capture transition hover states for transition-compare.sh."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SESSION_REF = os.environ["_TC_SESSION_REF"]
SESSION_IMPL = os.environ["_TC_SESSION_IMPL"]
DIR = os.environ["_TC_DIR"]
TRANSITION_WAIT = float(os.environ.get("TRANSITION_WAIT", "500")) / 1000
SCROLL_WAIT = float(os.environ.get("_TC_SCROLL_WAIT", "300")) / 1000
COMPARE_LIMIT = int(os.environ.get("COMPARE_LIMIT", "20"))

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(selector: str) -> str:
    safe = selector.replace("#", "id-").replace(".", "cls-")
    safe = _SAFE_NAME_RE.sub("_", safe)
    return safe[:30] or "el"


def _ab_eval(session: str, js: str) -> subprocess.CompletedProcess[str]:
    """Run agent-browser eval with argv so selectors cannot reach a shell."""
    return subprocess.run(
        ["agent-browser", "--session", session, "eval", js],
        capture_output=True,
        text=True,
        check=False,
    )


def _ab_command(session: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a non-eval agent-browser command without involving a shell."""
    return subprocess.run(
        ["agent-browser", "--session", session, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _unwrap_ab_json(raw: str | None) -> dict[str, Any]:
    """Decode direct, double-encoded, and wrapped agent-browser JSON results."""
    if raw is None:
        return {}
    raw = raw.strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if (
        isinstance(value, dict)
        and isinstance(value.get("data"), dict)
        and "result" in value["data"]
    ):
        value = value["data"]["result"]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _hover_probe(session: str, selector_literal: str) -> dict[str, Any]:
    return _unwrap_ab_json(
        _ab_eval(
            session,
            (
                "(() => {"
                f"const el = document.querySelector({selector_literal});"
                "if (!el) return JSON.stringify({ found: false });"
                "const rect = el.getBoundingClientRect();"
                "return JSON.stringify({"
                "found: true,"
                "hovered: el.matches(':hover'),"
                "rect: {"
                "x: rect.x, y: rect.y, width: rect.width, height: rect.height"
                "},"
                "viewport: { width: innerWidth, height: innerHeight }"
                "});"
                "})()"
            ),
        ).stdout
    )


def _move_to_probe_center(
    session: str,
    selector_literal: str,
    probe: dict[str, Any],
) -> bool:
    rect = probe.get("rect") if isinstance(probe.get("rect"), dict) else {}
    viewport = (
        probe.get("viewport")
        if isinstance(probe.get("viewport"), dict)
        else {}
    )
    try:
        left = max(float(rect.get("x")), 0)
        top = max(float(rect.get("y")), 0)
        right = min(
            float(rect.get("x")) + float(rect.get("width")),
            float(viewport.get("width")),
        )
        bottom = min(
            float(rect.get("y")) + float(rect.get("height")),
            float(viewport.get("height")),
        )
    except (TypeError, ValueError):
        return False
    if right <= left or bottom <= top:
        return False
    _ab_command(
        session,
        "mouse",
        "move",
        f"{(left + right) / 2:.2f}",
        f"{(top + bottom) / 2:.2f}",
    )
    time.sleep(min(max(SCROLL_WAIT, 0.05), 0.25))
    retry = _hover_probe(session, selector_literal)
    return bool(retry.get("found") and retry.get("hovered"))


def _ensure_real_hover(
    session: str,
    selector: str,
    selector_literal: str,
) -> bool:
    """Hover the current target and verify the browser applied ``:hover``.

    Move to an already-visible target's fresh box before selector-based hover,
    because Playwright's implicit scroll can swap sticky/header DOM trees.
    Offscreen targets retain the selector-based scroll fallback.
    """
    probe = _hover_probe(session, selector_literal)
    if probe.get("found") and _move_to_probe_center(
        session,
        selector_literal,
        probe,
    ):
        return True

    _ab_command(session, "hover", selector)
    time.sleep(min(max(SCROLL_WAIT, 0.05), 0.25))
    probe = _hover_probe(session, selector_literal)
    if probe.get("found") and probe.get("hovered"):
        return True

    # Refresh the automation locator after JS-driven scrollIntoView. Some
    # browser sessions retain a pre-scroll box for long/sticky selectors until
    # the locator itself performs a scroll action.
    _ab_command(session, "scrollintoview", selector)
    time.sleep(min(max(SCROLL_WAIT, 0.05), 0.35))
    _ab_command(session, "hover", selector)
    time.sleep(min(max(SCROLL_WAIT, 0.05), 0.25))
    probe = _hover_probe(session, selector_literal)
    if probe.get("found") and probe.get("hovered"):
        return True

    return _move_to_probe_center(session, selector_literal, probe)


def capture_hover_state(
    session: str,
    elements_file: str,
    side: str,
    out_dir: str,
) -> list[dict[str, Any]]:
    elements = json.loads(Path(elements_file).read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []

    for element in elements[:COMPARE_LIMIT]:
        selector = element["selector"]
        safe_name = _safe_name(selector)
        selector_literal = json.dumps(selector)

        # Do not let the previous target's pointer position contaminate this
        # element's idle baseline.
        _ab_command(session, "mouse", "move", "-100", "-100")
        time.sleep(min(max(SCROLL_WAIT, 0.05), 0.15))
        _ab_eval(
            session,
            (
                "(() => {"
                f"const el = document.querySelector({selector_literal});"
                "if (!el) return 'not found';"
                "const rect = el.getBoundingClientRect();"
                "const visible = rect.bottom > 0 && rect.top < innerHeight"
                " && rect.right > 0 && rect.left < innerWidth;"
                "if (visible) return 'already-visible';"
                "el.scrollIntoView({ block: 'center' });"
                "return 'scrolled';"
                "})()"
            ),
        )
        time.sleep(SCROLL_WAIT)

        idle_path = Path(out_dir) / f"{safe_name}-idle.png"
        subprocess.run(
            ["agent-browser", "--session", session, "screenshot", str(idle_path)],
            capture_output=True,
            check=False,
        )

        hover_verified = _ensure_real_hover(session, selector, selector_literal)
        _ab_eval(
            session,
            (
                "(() => {"
                f"const el = document.querySelector({selector_literal});"
                "if (!el) return 'not found';"
                "el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));"
                "el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));"
                "el.focus?.();"
                "return 'hovered';"
                "})()"
            ),
        )
        time.sleep(TRANSITION_WAIT)

        hover_path = Path(out_dir) / f"{safe_name}-hover.png"
        subprocess.run(
            ["agent-browser", "--session", session, "screenshot", str(hover_path)],
            capture_output=True,
            check=False,
        )

        result = _ab_eval(
            session,
            (
                "(() => {"
                f"const el = document.querySelector({selector_literal});"
                "if (!el) return JSON.stringify({ error: 'not found' });"
                "const cs = getComputedStyle(el);"
                "return JSON.stringify({"
                "opacity: cs.opacity,"
                "transform: cs.transform,"
                "backgroundColor: cs.backgroundColor,"
                "color: cs.color,"
                "scale: cs.scale || 'none',"
                "filter: cs.filter,"
                "boxShadow: cs.boxShadow,"
                "borderColor: cs.borderColor,"
                "});"
                "})()"
            ),
        )

        _ab_eval(
            session,
            (
                "(() => {"
                f"const el = document.querySelector({selector_literal});"
                "if (!el) return 'not found';"
                "el.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));"
                "el.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }));"
                "el.blur?.();"
                "return 'left';"
                "})()"
            ),
        )
        subprocess.run(
            ["agent-browser", "--session", session, "hover", "body"],
            capture_output=True,
            text=True,
            check=False,
        )
        time.sleep(SCROLL_WAIT)

        hover_style = _unwrap_ab_json(result.stdout)
        if hover_style.get("error"):
            hover_style = {}
        if not hover_verified:
            hover_style = {}

        results.append(
            {
                "selector": selector,
                "name": safe_name,
                "hoverStyle": hover_style,
                "hoverVerified": hover_verified,
                **(
                    {"captureError": "real pointer did not reach target"}
                    if not hover_verified
                    else {}
                ),
            }
        )
        sys.stdout.write(f"  ✓ {side}/{safe_name}\n")
        sys.stdout.flush()

    return results


def _integrity_note(rows: list[dict[str, Any]]) -> str | None:
    non_empty = sum(1 for row in rows if row.get("hoverStyle"))
    if rows and non_empty == 0:
        return f"all {len(rows)} hover captures empty — capture/parse likely broken"
    return None


def main() -> int:
    ref_results = capture_hover_state(
        SESSION_REF,
        f"{DIR}/transitions/ref-elements.json",
        "ref",
        f"{DIR}/transitions/ref",
    )
    impl_results = capture_hover_state(
        SESSION_IMPL,
        f"{DIR}/transitions/impl-elements.json",
        "impl",
        f"{DIR}/transitions/impl",
    )

    ref_note = _integrity_note(ref_results)
    if ref_note:
        sys.stderr.write(f"⚠ hover-capture integrity (ref): {ref_note}\n")

    payload: dict[str, Any] = {"ref": ref_results, "impl": impl_results}
    if ref_note:
        payload["captureWarning"] = ref_note

    Path(f"{DIR}/transitions/hover-states.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
