"""State-driven reveal end-state proof for active-swap / state-machine regions.

Loop-10/11 regression class: scrolling to the next section swaps the nav pill's
active state, but the newly-active button's label never reveals — the label
container is baked width:0 with no active-state expansion. The hover-fallback
gate (c1651d2) only covers HOVER-triggered reveals; an active-state (scroll)
reveal has no compensating verification.

The bundle declares the reveal as `initial:{width:0} -> animate:{width:<active
flag>?"auto":0}` (extracted as bundle-extraction `activeStateExpansions`). This
gate drives the state change on the LIVE impl (scrolling through the page so each
section becomes active in turn) and asserts the declared end-state delta
actually occurs: across the sweep the revealed element must expand past the
collapsed width at least once. A faithful impl reveals the active label
(pass); the loop-11 impl keeps every label at width:0 (fail).

Honest-unmeasurable (never a silent pass) when no per-state delta is declared:
the remediation is to re-run extraction so the bundle's active-state expansion
is captured (the extraction side was extended for exactly this — see
scripts/extract/_bundle_extraction.py `_extract_active_state_expansions`).

Ref truth comes from bundle params + extraction artifacts only — no live ref
browsing (state-independent end-state, like the hover/motion proofs).

CLI:
    python -m ui_clone.gates.state_reveal plan <ref-dir>
        -> prints {selectors, props, collapsedPx, scrollSamples} for the wrapper
    python -m ui_clone.gates.state_reveal verdict <ref-dir> <observed-file>
        -> writes <ref-dir>/state-reveal.json; exit 1 on fail
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ui_clone.gates.visible_identity import (
    MIN_FONT_PX,
    is_on_screen,
    is_rendered,
    paints_text,
    settled_state,
)

DEFAULT_SCROLL_SAMPLES = 8
# A label is "revealed" when its rendered box shows at least this fraction of its
# own content width (scrollWidth). This is site-independent: a collapsed label
# (width:0 + padding) shows ~0 of its text, a revealed one shows ~all of it. The
# loop-11 defect — active "FAQs" label box 8px vs content 74px (ratio 0.11) —
# fails; a faithful reveal (box ≈ content, ratio ≈ 1) passes.
DEFAULT_REVEAL_RATIO = 0.5
# Ignore labels with negligible content (genuinely empty / icon-only) — no text
# to reveal, so no proof to make.
DEFAULT_MIN_CONTENT_PX = 12.0

# Env-tunability is itself a bypass surface (batch-7 ITEM 4): RATIO=0.01 or
# MIN_CONTENT_PX=100 softened the gate below the loop-11 defect's real ratio.
# Clamp every override to a sane band; the defaults sit inside their bands so
# normal config is a clamp no-op. The EFFECTIVE (clamped) value is recorded in
# the artifact and re-validated by the consumer.
REVEAL_RATIO_MIN, REVEAL_RATIO_MAX = 0.4, 0.95
MIN_CONTENT_PX_MIN, MIN_CONTENT_PX_MAX = 4.0, 40.0

_STATE_MACHINE_RE = ("state-machine", "active", "scroll", "nav")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def requested_reveal_ratio() -> float:
    """The raw env override (recorded for transparency), unclamped."""
    try:
        return float(os.environ.get("UI_CLONE_STATE_REVEAL_RATIO", DEFAULT_REVEAL_RATIO))
    except (TypeError, ValueError):
        return DEFAULT_REVEAL_RATIO


def reveal_ratio() -> float:
    return _clamp(requested_reveal_ratio(), REVEAL_RATIO_MIN, REVEAL_RATIO_MAX)


def requested_min_content_px() -> float:
    try:
        return float(os.environ.get("UI_CLONE_STATE_REVEAL_MIN_CONTENT_PX", DEFAULT_MIN_CONTENT_PX))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CONTENT_PX


def min_content_px() -> float:
    return _clamp(requested_min_content_px(), MIN_CONTENT_PX_MIN, MIN_CONTENT_PX_MAX)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def select_active_expansions(extraction: Any) -> list[dict[str, Any]]:
    """activeStateExpansions entries with a resolvable selector."""
    if not isinstance(extraction, dict):
        return []
    rows = (extraction.get("extractions") or {}).get("activeStateExpansions")
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cls = r.get("resolvedClassName") or r.get("classToken")
        if not cls:
            continue
        out.append(
            {
                "selector": f".{cls}" if not str(cls).startswith(".") else str(cls),
                "property": r.get("property") or "width",
                "from": r.get("from") or "0",
                "to": r.get("to") or "auto",
                "stateFlag": r.get("stateFlag"),
            }
        )
    return out


def _has_state_machine_entry(spec: Any) -> bool:
    rows = spec.get("transitions") if isinstance(spec, dict) else spec
    for e in rows or []:
        if not isinstance(e, dict):
            continue
        blob = " ".join(
            str(e.get(k) or "") for k in ("id", "trigger", "description", "bundle_branch")
        ).lower()
        if any(tok in blob for tok in _STATE_MACHINE_RE):
            return True
    return False


def build_plan(ref_dir: Path) -> dict[str, Any]:
    extraction = _load(ref_dir / "bundle-extraction.json")
    expansions = select_active_expansions(extraction)
    spec = _load(ref_dir / "transition-spec.json")
    return {
        "selectors": [e["selector"] for e in expansions],
        "expansions": expansions,
        "revealRatio": reveal_ratio(),
        "revealRatioRequested": requested_reveal_ratio(),
        "minContentPx": min_content_px(),
        "minContentPxRequested": requested_min_content_px(),
        "scrollSamples": DEFAULT_SCROLL_SAMPLES,
        "hasStateMachine": _has_state_machine_entry(spec) if spec is not None else False,
    }


def evaluate(plan: dict[str, Any], observations: Any) -> dict[str, Any]:
    """observations: list of ACTIVE-state label measurements collected while
    scrolling the impl so each section becomes active, each:
        {selector, pct, text, box, content}
    where `box` is the rendered width and `content` is scrollWidth. The active
    button's label is "revealed" when box >= revealRatio * content.
    """
    expansions = plan.get("expansions") or []
    # Explicit-None (not falsy-`or`) so a directly-constructed plan with 0.0 is
    # not silently swapped for the default, then re-clamp to the band — evaluate
    # is the final clamp point even for CLI/test plans (batch-7 ITEM 4).
    _rv = plan.get("revealRatio")
    ratio = _clamp(
        float(_rv) if _rv is not None else DEFAULT_REVEAL_RATIO,
        REVEAL_RATIO_MIN, REVEAL_RATIO_MAX,
    )
    _mc = plan.get("minContentPx")
    min_content = _clamp(
        float(_mc) if _mc is not None else DEFAULT_MIN_CONTENT_PX,
        MIN_CONTENT_PX_MIN, MIN_CONTENT_PX_MAX,
    )
    obs = [o for o in observations if isinstance(o, dict)] if isinstance(observations, list) else []

    if not expansions:
        return {
            "schemaVersion": 1,
            "status": "skip",
            "reason": (
                "no per-state width/maxWidth reveal declared in bundle-extraction "
                "activeStateExpansions — nothing to prove"
            ),
            "rows": [],
            "unmeasured": [],
        }

    min_font = MIN_FONT_PX
    rows: list[dict[str, Any]] = []
    # `measurable` drives the honest-unmeasurable reason: an observation only
    # counts as measurable when it is on-screen, painted, and carries enough
    # text content to measure a reveal. An off-screen decoy or a transparent /
    # font-size:0 label is NOT measurable-clean — it is a defect, not debt.
    measurable: list[dict[str, Any]] = []
    for o in obs:
        if not _obs_onscreen(o):
            # off-screen decoy: cannot mint a passing observation, and is not
            # what the user sees — exclude it entirely.
            continue
        has_text = _obs_has_text(o)
        painted = _obs_painted(o, min_font)
        content = _num(o.get("content"))
        # Settle (batch-7 ITEM 2): when the probe samples the active label until
        # quiescence past a floor, it records the box-width series; use the
        # SETTLED (final) box so a reveal that flashes open then collapses after
        # the window is measured at its collapsed end-state, not the transient.
        box = float(_num(o.get("box")) or 0.0)
        box_samples = o.get("boxSamples")
        if isinstance(box_samples, list) and box_samples:
            settled_box, _settled = settled_state(box_samples)
            box = float(_num(settled_box) or 0.0)
        if has_text and not painted:
            # Paint-blindness defence (Attacks 1 / 1b): the label occupies layout
            # but renders no visible text — an empty pill. A declared reveal that
            # paints nothing is a fail, never an unmeasurable warn.
            rows.append(
                {
                    "selector": o.get("selector"),
                    "pct": o.get("pct"),
                    "activeText": str(o.get("text") or "")[:40],
                    "boxPx": round(box, 1),
                    "contentPx": round(content, 1) if content is not None else None,
                    "revealRatio": 0.0,
                    "status": "fail",
                    "_transient": True,
                    "reason": (
                        "active label occupies layout but paints no visible text "
                        "(color:transparent / opacity:0 / font-size:0) — the "
                        "active-state reveal shows an empty pill"
                    ),
                }
            )
            continue
        if content is None or content <= min_content:
            # genuinely empty / icon-only painted label — nothing to prove
            continue
        if not is_rendered(o):
            # batch-9 ITEM 3: the active label expands in layout and paints text,
            # but an opaque node is the topmost paint at its rect (the shared
            # multi-point paint-aware hit-test reads "blocked"), or it is clipped /
            # filtered / ancestor-hidden — the reveal is invisible to the user.
            # Routing through is_rendered (the same predicate the masked-region and
            # hover gates use) makes an occluded reveal a fail, never a silent pass.
            rows.append(
                {
                    "selector": o.get("selector"),
                    "pct": o.get("pct"),
                    "activeText": str(o.get("text") or "")[:40],
                    "boxPx": round(box, 1),
                    "contentPx": round(float(content), 1),
                    "revealRatio": 0.0,
                    "status": "fail",
                    "reason": (
                        "active label occupies layout and paints text but is "
                        "occluded — an opaque node covers it or it is clipped / "
                        "filtered (hit-test blocked); the active-state reveal is "
                        "not visible to the user"
                    ),
                }
            )
            continue
        measurable.append(o)
        content_f = float(content)
        rr = box / content_f if content_f else 0.0
        revealed = rr >= ratio
        rows.append(
            {
                "selector": o.get("selector"),
                "pct": o.get("pct"),
                "activeText": str(o.get("text") or "")[:40],
                "boxPx": round(box, 1),
                "contentPx": round(content_f, 1),
                "revealRatio": round(rr, 2),
                "status": "ok" if revealed else "fail",
                "_transient": True,
                "reason": None
                if revealed
                else (
                    f"active label shows only {round(rr * 100)}% of its text "
                    f"({round(box)}px box vs {round(content_f)}px content) — the "
                    "active-state reveal did not fire (label stayed collapsed "
                    "when its section became active)"
                ),
            }
        )

    # batch-12 ITEM 6: section-transition tolerance, keyed on (selector, label
    # TEXT). The probe samples fixed scroll percentages; some land on a SECTION
    # BOUNDARY where the active label is legitimately collapsing (its section is
    # becoming inactive while the next expands), so a single position shows that
    # label collapsed/empty even though its reveal fires at the position where its
    # section is stably active. A label IDENTITY (same selector + same text) that
    # reveals+paints at ANY sampled position has a working reveal — its
    # collapse/empty-pill rows at OTHER positions are scroll-transition artifacts.
    # A label that NEVER reveals at any position (the loop-11 FAQs defect: baked
    # collapsed at its active position; a baked width:0 label) has 0 ok rows for
    # its text -> stays FAILED, so a real per-label non-revealing defect is still
    # caught. Keyed on text (not just selector), so one revealing label cannot
    # excuse a DIFFERENT label that never reveals. An OCCLUDED reveal (covered) is
    # structural and is NEVER tolerated (no _transient marker). Derived from the
    # observations' own per-label outcomes, so it cannot mint a pass for a reveal
    # that never fires.
    _revealed_labels: set[tuple[Any, Any]] = {
        (r.get("selector"), r.get("activeText"))
        for r in rows
        if r["status"] == "ok"
    }
    for r in rows:
        if (
            r["status"] == "fail"
            and r.get("_transient")
            and (r.get("selector"), r.get("activeText")) in _revealed_labels
        ):
            r["status"] = "transition"
            base = (r.get("reason") or "").rstrip()
            r["reason"] = (
                base + " — TOLERATED: this label (same selector+text) reveals+paints "
                "at another scroll position; the collapse here is a section-transition "
                "artifact, not a non-revealing label"
            )
    for r in rows:
        r.pop("_transient", None)

    fail_rows = [r for r in rows if r["status"] == "fail"]

    # Per-selector aggregation (batch-4 review MAJOR 3): the gate must not pass
    # when only a SUBSET of the declared active-state expansions fired. EVERY
    # declared selector needs its own measurable PASSING observation; a declared
    # selector with no passing observation is unmeasured/fail per the honesty
    # convention (never a silent pass on the strength of a different selector).
    declared = [str(e["selector"]) for e in expansions if e.get("selector")]
    passing_sels = {r["selector"] for r in rows if r["status"] == "ok"}
    unmeasured: list[dict[str, Any]] = []
    for sel in declared:
        if sel in passing_sels:
            continue
        if not measurable:
            reason = (
                "no active-state label with measurable text content was observed "
                "across the scroll sweep — the probe could not engage the active "
                "state (re-run with a reachable impl URL) or the labels are "
                "genuinely empty"
            )
        else:
            reason = (
                f"declared active-state reveal {sel} never produced a measurable "
                "PASSING observation across the scroll sweep — its active state "
                "was not engaged (a different selector revealing does not cover it)"
            )
        unmeasured.append({"selector": sel, "reason": reason})

    if fail_rows:
        status = "fail"
    elif unmeasured:
        # one or more declared selectors lack a passing observation — honest-
        # unmeasurable, never a silent pass.
        status = "warn"
    elif declared and all(sel in passing_sels for sel in declared):
        status = "pass"
    else:
        status = "skip"

    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "status": status,
        "revealRatio": ratio,
        "minContentPx": min_content,
        # Effective (clamped) thresholds actually used + the raw requested values
        # — the consumer band-check reads these and rejects an out-of-band mint.
        "effectiveRevealRatio": ratio,
        "effectiveMinContentPx": min_content,
        "revealRatioRequested": plan.get("revealRatioRequested", ratio),
        "minContentPxRequested": plan.get("minContentPxRequested", min_content),
        "rows": rows,
        "unmeasured": unmeasured,
        "rule": (
            "Every bundle-declared active-state reveal must fire on the live "
            "impl: when a section becomes active, its nav button's label must "
            "expand to show its text (box >= revealRatio * content). A label "
            "that stays collapsed when active is the loop-11 defect (baked "
            "width:0, reveal only on the initial active state)."
        ),
    }
    if unmeasured:
        payload["remediation"] = (
            "re-run extraction so the active-state expansion is captured, or fix "
            "the impl so the active nav label reveals on scroll"
        )
    return payload


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ── paint / on-screen assertions (tools batch-6 ITEM 2) ──────────────────────
def _obs_has_text(o: dict[str, Any]) -> bool:
    if "hasText" in o:
        return bool(o["hasText"])
    return bool(str(o.get("text") or "").strip())


def _obs_painted(o: dict[str, Any], min_font: float) -> bool:
    """Whether the active label actually paints readable text. A box that fills
    its content width proves nothing if the text is color:transparent,
    opacity:0, font-size:0 (Attacks 1 / 1b), OR painted in a colour identical to
    its effective background — white-on-white (batch-7 ITEM 1, proven false-PASS).
    Delegates the paint decision to the shared visible-identity primitive
    (alpha floor + strict font + contrast vs effective background); missing
    paint fields default to painted, so legacy observations are unaffected."""
    op = _num(o.get("opacity"))
    if op is not None and op <= 0.0:
        return False
    rec = dict(o)
    rec["hasText"] = _obs_has_text(o)
    return paints_text(rec, min_font=min_font)


def _obs_onscreen(o: dict[str, Any]) -> bool:
    """Whether the measured label is on-screen. A collapsed width:0 label is
    still on-screen; an off-screen decoy (left:-99999) is not, so it cannot mint
    a passing observation (Attack 3). Missing geometry defaults to on-screen."""
    if "onScreen" in o:
        return bool(o["onScreen"])
    rect = o.get("rect")
    if isinstance(rect, dict) and rect:
        return is_on_screen(o)
    return True


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "plan":
        print(json.dumps(build_plan(Path(args[1])), indent=2))
        return 0
    if len(args) == 3 and args[0] == "verdict":
        ref_dir, observed_path = Path(args[1]), Path(args[2])
        plan = build_plan(ref_dir)
        observed = _load(observed_path)
        payload = evaluate(plan, observed if isinstance(observed, list) else [])
        # batch-9 ITEM 3 (Codex BLOCKER): provenance must rest on a LIVE scan, not
        # merely on this CLI having run. The probe writes a scan receipt inside the
        # impl tree only in the browser path (state-reveal-proof-check.sh) and
        # passes UI_CLONE_STATE_REVEAL_RUNTIME_SCANNED=1 + the receipt path here;
        # runtimeScanned is true only when the env flag AND that receipt file both
        # exist, and the consumer binds the receipt to impl_root + mtime
        # (PATH_CHECK) — so a hand-authored observed-file run through `verdict`, or
        # a self-attested env flag with no receipt, can no longer mint a pass
        # (mirror hover-fallback).
        scan_receipt = os.environ.get("UI_CLONE_STATE_REVEAL_SCAN_RECEIPT", "").strip()
        receipt_ok = bool(scan_receipt) and Path(scan_receipt).is_file()
        payload["runtimeScanned"] = (
            os.environ.get("UI_CLONE_STATE_REVEAL_RUNTIME_SCANNED") == "1" and receipt_ok
        )
        payload["scanReceipt"] = scan_receipt if receipt_ok else None
        (ref_dir / "state-reveal.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        # F12: a "warn" is honest-unmeasurable (a declared active-state reveal
        # produced no passing observation), deliberately non-blocking because it can
        # be a probe/environment issue (unreachable impl URL), not necessarily an
        # impl defect. Keep exit 0 — but SURFACE it on stderr so an exit-code-only
        # consumer does not read it as a clean pass identical to a real pass.
        if payload["status"] == "warn":
            n = len(payload.get("unmeasured") or [])
            print(
                f"⚠️  state-reveal: WARN — {n} declared active-state reveal(s) "
                "unverified (see state-reveal.json 'unmeasured'). Exit 0 is "
                "non-blocking by design, but this is NOT a clean pass.",
                file=sys.stderr,
            )
        return 1 if payload["status"] == "fail" else 0
    print(
        "usage: state_reveal plan <ref-dir> | verdict <ref-dir> <observed-file>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
