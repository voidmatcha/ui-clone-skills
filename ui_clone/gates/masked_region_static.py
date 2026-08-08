"""Static-style parity for dynamic-masked regions.

Loop-10/11 regression class: the eatReal "Eat Real" h2 sits under a
`dynamic:true` mask selector, so section-compare (pixel), video-motion, and
masked-region-motion all mask it out — and the motion proof checks MOTION
only. None of them check STATIC style. The impl rendered the h2 left-aligned
(its inline style carries no `text-align`) while the ref centers it, and every
gate passed: the mask, meant to absorb timer-phase MOTION, also erased the
region's static style from every check.

This gate compares phase-free computed styles between the extraction-time ref
ground truth (`dom-scaffold.json`, captured before the mask is applied) and the
live impl DOM. Styles are state-independent, so no pixel capture or phase
sampling is needed — a pure artifact-vs-DOM comparison.

The default property set is deliberately viewport-INDEPENDENT (text-align,
justify-content, align-items, font-family, font-weight, color) so that probing
the impl at any viewport still ref-self-passes against the single-viewport
scaffold. Responsive props (e.g. font-size) can be added via
UI_CLONE_MRS_PROPS for callers that probe at the extraction viewport.

Rect/center-offset geometry of masked elements is intentionally NOT handled
here — it is viewport-dependent (a px-vs-% transform defect is invisible at the
extraction viewport) and is closed by alignment-parity, which already measures
ref+impl geometry across the fan-out viewports once masked elements are no
longer excluded from the enumeration.

CLI:
    python -m ui_clone.gates.masked_region_static plan <ref-dir>
        → prints {props, selectors, refEntries} for the wrapper to probe impl
    python -m ui_clone.gates.masked_region_static verdict <ref-dir> <impl-file>
        → writes <ref-dir>/masked-region-static.json; exit 1 on fail
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from ui_clone.gates.visible_identity import is_visible, settled_state

DEFAULT_STYLE_PROPS = [
    "text-align",
    "justify-content",
    "align-items",
    "font-family",
    "font-weight",
    "color",
]

# The shell wrapper intentionally runs the host's `python3`, which is still
# Python 3.9 on supported macOS systems. Keep runtime isinstance arguments
# compatible even though project lint otherwise prefers PEP 604 unions.
_NUMBER_TYPES = (int, float)

# dom-scaffold abbreviates a handful of style keys; everything else is stored
# under its full CSS property name.
_SCAFFOLD_ABBREV = {
    "font-family": "ff",
    "font-size": "fs",
    "font-weight": "fw",
    "line-height": "lh",
    "letter-spacing": "ls",
    "background-color": "bg",
}

_COMPOUND_CLASS_RE = re.compile(r"\.([A-Za-z0-9_-]+)")
_COMPOUND_TAG_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9]*)")

# justify-content / align-items only take effect on flex/grid containers — on a
# block element they compute to an inert initial value, so comparing them there
# is noise (and would false-fail a faithful clone).
_FLEXY_DISPLAYS = {"flex", "inline-flex", "grid", "inline-grid"}
_FLEX_ONLY_PROPS = {"justify-content", "align-items"}


def partition_selectors(selectors: list[str]) -> tuple[list[str], list[str]]:
    """Split selectors into (scaffold-resolvable, unresolvable).

    dom-scaffold stores tag + class only, not attributes or runtime pseudo
    state. A selector carrying an attribute (`[aria-live]`) or pseudo (`:hover`)
    cannot be resolved against the scaffold with the same cardinality the
    browser uses, so matching it permissively (e.g. every `div`) against the
    browser's strict match yields spurious "element absent" rows AND breaks
    ref-self. Such selectors are reported unmeasured, never compared.
    """
    resolvable, unresolvable = [], []
    for sel in selectors:
        if "[" in sel or ":" in sel:
            unresolvable.append(sel)
        else:
            resolvable.append(sel)
    return resolvable, unresolvable


def style_props() -> list[str]:
    raw = os.environ.get("UI_CLONE_MRS_PROPS", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return list(DEFAULT_STYLE_PROPS)


# ── spec selectors ──────────────────────────────────────────────────────────
def _spec_entries(spec: Any) -> list[dict[str, Any]]:
    if isinstance(spec, list):
        rows = spec
    elif isinstance(spec, dict):
        rows = spec.get("transitions") or spec.get("entries") or []
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def select_masked_selectors(spec: Any) -> list[str]:
    """Flat, de-duplicated selector list from every dynamic:true spec entry."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in _spec_entries(spec):
        if not entry.get("dynamic"):
            continue
        for sel in str(entry.get("target") or "").split(","):
            sel = sel.strip()
            if sel and sel not in seen:
                seen.add(sel)
                out.append(sel)
    return out


# ── minimal CSS resolver over the dom-scaffold tree ─────────────────────────
def _children(node: Any) -> list[dict]:
    kids = node.get("children") if isinstance(node, dict) else None
    return [k for k in kids if isinstance(k, dict)] if isinstance(kids, list) else []


def _descendants(node: dict) -> Iterator[dict]:
    for child in _children(node):
        yield child
        yield from _descendants(child)


def _class_tokens(node: dict) -> set[str]:
    raw = node.get("class") or node.get("className") or ""
    return {t for t in str(raw).split() if t}


def _matches_compound(node: dict, compound: str) -> bool:
    compound = compound.strip()
    if not compound:
        return False
    # tag (optional, leading)
    tag_match = _COMPOUND_TAG_RE.match(compound)
    if tag_match:
        tag = tag_match.group(1).lower()
        node_tag = str(node.get("tag") or node.get("tagName") or "").lower()
        if node_tag != tag:
            return False
    # required class tokens
    classes = set(_COMPOUND_CLASS_RE.findall(compound))
    if classes and not classes.issubset(_class_tokens(node)):
        return False
    # attribute / pseudo fragments ([aria-live], :hover) are matched
    # best-effort by tag+class only — never a hard requirement, so a masked
    # element behind an attribute selector still gets measured rather than
    # silently skipped.
    return True


def resolve_scaffold(root: dict, selector: str) -> list[dict]:
    """Resolve a (possibly descendant) selector against the scaffold tree."""
    compounds = [c for c in selector.split() if c]
    if not compounds:
        return []
    current = [root]
    first = True
    for compound in compounds:
        matched: list[dict] = []
        seen_ids: set[int] = set()
        for ctx in current:
            scope = [ctx] if (first and ctx is not root) else list(_descendants(ctx))
            for node in scope:
                if _matches_compound(node, compound) and id(node) not in seen_ids:
                    seen_ids.add(id(node))
                    matched.append(node)
        current = matched
        first = False
    return current


def _scaffold_style(styles: dict, prop: str) -> Any:
    if prop in styles:
        return styles[prop]
    abbrev = _SCAFFOLD_ABBREV.get(prop)
    if abbrev and abbrev in styles:
        return styles[abbrev]
    return None


def ref_entries_from_scaffold(
    root: dict, selectors: list[str], props: list[str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for selector in selectors:
        nodes = resolve_scaffold(root, selector)
        for index, node in enumerate(nodes):
            raw = node.get("styles")
            styles_raw = raw if isinstance(raw, dict) else {}
            styles = {}
            for prop in props:
                val = _scaffold_style(styles_raw, prop)
                if val is not None:
                    styles[prop] = val
            out.append(
                {
                    "selector": selector,
                    "index": index,
                    "tag": str(node.get("tag") or node.get("tagName") or ""),
                    "classSig": str(node.get("class") or node.get("className") or ""),
                    # display is meta (drives flex-only applicability), never a
                    # compared property unless the caller adds it to props.
                    "display": str(_scaffold_style(styles_raw, "display") or ""),
                    "styles": styles,
                }
            )
    return out


# ── value normalization ─────────────────────────────────────────────────────
_TEXT_ALIGN_EQUIV = {"start": "left", "end": "right"}


def normalize_value(prop: str, value: Any) -> str:
    v = str(value).strip().lower()
    if prop == "text-align":
        return _TEXT_ALIGN_EQUIV.get(v, v)
    if prop == "font-family":
        first = v.split(",")[0]
        return first.replace('"', "").replace("'", "").strip()
    if prop == "color":
        return v.replace(" ", "")
    return v


# ── visible-identity resolution + settle (tools batch-6 ITEM 1) ──────────────
def _impl_visible(entry: dict[str, Any]) -> bool:
    """Whether an impl match is rendered-visible.

    A legacy entry without geometry (no rect) is treated as visible unless it
    affirmatively declares itself hidden — only entries carrying the
    visible-identity collector's rich fields (rect + paint) are subject to the
    full on-screen/area check, so existing artifact-only callers and the
    dom-scaffold ref path are unaffected. Paint is NOT required here: a masked
    heading with the correct style is the real target even if its text colour is
    being animated; the decoy classes we reject are display:none / off-screen /
    zero-area.
    """
    rect = entry.get("rect")
    if isinstance(rect, dict) and rect:
        # below_fold_ok (batch-8 ITEM 10, hardened batch-9 minor): a faithful
        # masked element below the first viewport is measured ONLY with a
        # post-scroll viewport-intersection proof (the probe scrolled it into view
        # and stamped scrolledIntoView). Without that proof the below-fold
        # tolerance does NOT apply, so an unreachable below-fold decoy cannot ride
        # the exemption — it reads off-screen => absent.
        below_fold_ok = bool(entry.get("scrolledIntoView")) or bool(
            entry.get("postScrollIntersects")
        )
        return is_visible(entry, require_paint=False, below_fold_ok=below_fold_ok)
    if str(entry.get("display", "")).lower() == "none":
        return False
    if str(entry.get("visibility", "")).lower() == "hidden":
        return False
    op = entry.get("opacity")
    if op is not None:
        try:
            if float(op) <= 0:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _effective_styles(
    entry: dict[str, Any], props: list[str]
) -> tuple[dict[str, Any], set[str]]:
    """The SETTLED computed style per property, plus the set of props whose
    sample series never reached quiescence.

    A single-instant probe is fooled by a defect that flips in AFTER the wait
    window (Attack D / batch-7 late-flip at 7000ms). The probe now samples until
    MutationObserver/rAF quiescence past a wall-clock floor and records the FULL
    ordered series in `stylesSamples`; for each property we take the settled
    (final) STATE — the value the user is left with — AND flag the prop when the
    trailing frames never agreed (still-changing), so an oscillating series can
    no longer mint a silent pass on a transient. Without samples, fall back to
    `styles` (legacy / single-instant callers).
    """
    raw = entry.get("styles")
    base: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    samples = entry.get("stylesSamples")
    if not isinstance(samples, list) or not samples:
        return base, set()
    eff = dict(base)
    changing: set[str] = set()
    for prop in props:
        seq = [
            s.get(prop)
            for s in samples
            if isinstance(s, dict) and s.get(prop) is not None
        ]
        if seq:
            value, settled = settled_state(seq)
            eff[prop] = value
            if not settled:
                changing.add(prop)
    return eff, changing


def _viewport_of(entry: dict[str, Any]) -> int | None:
    v = entry.get("clientWidth")
    if isinstance(v, _NUMBER_TYPES):
        return int(v)
    return None


def _compare_bucket(
    selector: str,
    ref_list: list[dict[str, Any]],
    impl_matches: list[dict[str, Any]],
    props: list[str],
    rows: list[dict[str, Any]],
    unmeasured: list[dict[str, Any]],
    viewport: int | None,
    ref_expects_here: bool = True,
    not_applicable_reason: str = (
        "masked selector hidden in the ref at this viewport too "
        "(responsive-hidden via @media in the ref capture) — not compared here"
    ),
    ref_rendered_count: int | None = None,
    ref_rendered_style: dict[str, Any] | None = None,
) -> None:
    """Resolve the rendered-visible impl match(es) for one (selector, viewport)
    bucket and compare each against the ref entries (settled style), appending
    rows/unmeasured. Pairs by visible identity, fails loud on >expected visible
    matches, and fails a still-changing (non-quiescent) property.

    batch-12 ITEM 6: when the live-ref probe recorded what it RENDERS at this
    viewport (``ref_rendered_count``/``ref_rendered_style`` from
    ref-viewport-visibility), the EXPECTED instance count and the per-viewport
    reference STYLE are taken from the live ref render, not the static
    dom-scaffold — the scaffold over-counts responsive/stacked DOM duplicates and
    carries a single viewport-independent style the live ref varies via @media, so
    the ref otherwise false-fails against itself. The expectation is derived ONLY
    from the ref (never the impl's own visible set), so a clone rendering FEWER
    instances or WRONG per-viewport styles still fails. Absent the artifact (unit
    tests / uncovered selector) it falls back to the scaffold count + per-entry
    styles — identical to the pre-batch-12 behaviour."""
    expected = ref_rendered_count if ref_rendered_count is not None else len(ref_list)
    visible = [e for e in impl_matches if _impl_visible(e)]
    if not visible and not ref_expects_here:
        # batch-9 ITEM 2: 0 rendered-visible matches in THIS viewport bucket AND
        # the REF target is itself hidden here (display:none at a narrow @media in
        # the ref capture/fanout) => legitimately responsive-hidden, a compact
        # label takes over. Record not-applicable. When the ref SHOWS the target
        # here (the fail-closed default) a 0-visible bucket is an absent defect,
        # not an exemption — an impl that @media-hides content the ref renders can
        # no longer masquerade as responsive (the respbypass attack). The
        # per-viewport expectation is derived from the ref, never the impl's own
        # visible-elsewhere set.
        for ref_e in ref_list:
            unmeasured.append(
                {
                    "selector": selector,
                    "viewport": viewport,
                    "index": ref_e["index"],
                    "reason": not_applicable_reason,
                }
            )
        return
    if len(visible) > expected:
        rows.append(
            {
                "selector": selector,
                "viewport": viewport,
                "status": "fail",
                "reason": (
                    f"ambiguous masked target: {len(visible)} rendered-visible "
                    f"elements match '{selector}' but the ref expects {expected} "
                    "— a decoy/duplicate matching the masked selector cannot be "
                    "disambiguated from the real element (rename or remove it)"
                ),
            }
        )
        return
    for i in range(expected):
        ref_e = ref_list[i] if i < len(ref_list) else {}
        index = ref_e.get("index", i)
        impl_match: dict[str, Any] | None = visible[i] if i < len(visible) else None
        if impl_match is None:
            rows.append(
                {
                    "selector": selector,
                    "viewport": viewport,
                    "index": index,
                    "status": "fail",
                    "reason": (
                        "impl element absent — ref has this masked element but "
                        "no rendered-visible impl element matches the masked "
                        "selector (a display:none/off-screen decoy or a renamed "
                        "real element does not exempt it from style parity)"
                    ),
                }
            )
            continue
        # batch-12 ITEM 6: compare against the live-REF-RENDERED per-viewport style
        # when available (the scaffold's single style value is viewport-independent
        # and false-fails @media-varying props such as text-align against the ref's
        # own render); fall back to the scaffold per-entry style otherwise.
        ref_styles = ref_rendered_style if ref_rendered_style is not None else (ref_e.get("styles") or {})
        impl_styles, changing = _effective_styles(impl_match, props)
        ref_display = str(ref_e.get("display") or "").strip().lower()
        compared = 0
        for prop in props:
            rv = ref_styles.get(prop)
            iv = impl_styles.get(prop)
            if rv is None or iv is None:
                continue
            # justify-content/align-items are inert outside flex/grid — only
            # compare them when the ref element is a flex/grid container.
            if prop in _FLEX_ONLY_PROPS and ref_display not in _FLEXY_DISPLAYS:
                continue
            compared += 1
            # A property whose sample series never reached quiescence is still
            # changing at the end of the settle window — the gate cannot certify
            # a settled state, so it fails (never a silent pass on a transient).
            if prop in changing:
                rows.append(
                    {
                        "selector": selector,
                        "viewport": viewport,
                        "index": index,
                        "property": prop,
                        "refValue": rv,
                        "implValue": iv,
                        "status": "fail",
                        "reason": (
                            "masked style still changing at the end of the "
                            "settle window (no quiescence reached) — the gate "
                            "cannot certify a settled state"
                        ),
                    }
                )
                continue
            ok = normalize_value(prop, rv) == normalize_value(prop, iv)
            rows.append(
                {
                    "selector": selector,
                    "viewport": viewport,
                    "index": index,
                    "property": prop,
                    "refValue": rv,
                    "implValue": iv,
                    "status": "ok" if ok else "fail",
                }
            )
        if compared == 0:
            unmeasured.append(
                {
                    "selector": selector,
                    "viewport": viewport,
                    "index": index,
                    "reason": (
                        "no comparable computed styles captured for this masked "
                        "element on either side — re-run extraction so dom-scaffold "
                        "carries its styles"
                    ),
                }
            )


# ── verdict ──────────────────────────────────────────────────────────────────
def evaluate(
    ref_entries: list[dict[str, Any]],
    impl_entries: list[dict[str, Any]],
    props: list[str] | None = None,
    requested_selectors: list[str] | None = None,
    unresolvable_selectors: list[str] | None = None,
    ref_hidden_viewports: Mapping[str, Iterable[int]] | None = None,
    ref_measured_viewports: Iterable[int] | None = None,
    ref_rendered: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    props = props if props is not None else style_props()
    rows: list[dict[str, Any]] = []
    unmeasured: list[dict[str, Any]] = []
    for sel in unresolvable_selectors or []:
        unmeasured.append(
            {
                "selector": sel,
                "reason": (
                    "selector carries an attribute/pseudo the dom-scaffold "
                    "cannot resolve with matching cardinality — not compared "
                    "(re-run extraction with attribute capture to measure it)"
                ),
            }
        )

    # Pair ref<->impl by the RENDERED-VISIBLE match, not (selector, index): a
    # decoy element matching the masked selector — display:none ahead of the
    # real element (Attack A), or an off-screen clone while the real heading is
    # renamed (Attack C) — must not absorb the only ref comparison. More than
    # `expected` visible matches is ambiguous (an on-screen decoy/duplicate)
    # and fails loud rather than silently picking one.
    ref_by_sel: dict[str, list[dict[str, Any]]] = {}
    for ref_e in ref_entries:
        ref_by_sel.setdefault(str(ref_e["selector"]), []).append(ref_e)

    # Bucket impl matches by (selector, viewport) so an @media-gated defect at a
    # viewport the probe DID enter cannot hide behind another (batch-7 ITEM 3):
    # text-align is viewport-dependent via @media, so a single-viewport probe is
    # blind. Each fan-out viewport bucket is compared against the ref
    # independently; a mismatch in ANY viewport fails. The ambiguity guard
    # counts within a single viewport bucket, so multi-viewport probing of one
    # selector is not mistaken for a decoy.
    impl_by_sel_vp: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
    for impl_e in impl_entries:
        impl_by_sel_vp.setdefault(
            (str(impl_e.get("selector")), _viewport_of(impl_e)), []
        ).append(impl_e)

    # batch-9 ITEM 2: per-viewport expected visibility is derived from the REF,
    # never the impl's own visible-elsewhere set. ref_hidden_viewports maps a
    # masked selector to the viewport widths at which the REF target is itself
    # hidden (display:none at a narrow @media in the ref capture/fanout). A
    # 0-visible impl bucket is excused ONLY at a viewport the ref hides too;
    # everywhere else the ref shows the target, so a missing impl element is an
    # absent defect (fail-closed). This closes the respbypass attack, where an
    # impl @media-hides a heading the ref renders and the old impl-derived excuse
    # recorded it "responsive-hidden".
    ref_hidden: dict[str, set[int]] = {}
    for sel, vps in (ref_hidden_viewports or {}).items():
        ref_hidden[str(sel)] = {int(v) for v in vps}

    # batch-10 ITEM 4: the viewport widths the REF capture actually measured
    # (capturedViewports in ref-viewport-visibility.json). A probed impl viewport
    # NOT in this set has no ref-side evidence, so a 0-visible bucket there is
    # UNMEASURED, never absent — closing the false-fail of an honest responsive
    # clone whose ref simply wasn't captured at that viewport. When no coverage is
    # provided (None) the gate stays fail-closed (and a viewport-less None record
    # is always treated as measured) so the respbypass anti-cheat and the legacy
    # single-viewport behaviour are preserved.
    ref_measured: set[int] | None = None
    if ref_measured_viewports is not None:
        ref_measured = {int(v) for v in ref_measured_viewports}

    for selector, ref_list in ref_by_sel.items():
        viewports = sorted(
            {k[1] for k in impl_by_sel_vp if k[0] == selector},
            key=lambda v: (v is None, v if v is not None else 0),
        )
        if not viewports:
            viewports = [None]
        hidden_vps = ref_hidden.get(selector, set())
        for vp in viewports:
            is_measured = ref_measured is None or vp is None or vp in ref_measured
            ref_expects_here = is_measured and vp not in hidden_vps
            if not is_measured:
                reason = (
                    f"viewport {vp} not captured in the ref — no evidence the ref "
                    f"shows '{selector}' here (unmeasured, not compared)"
                )
            else:
                reason = (
                    "masked selector hidden in the ref at this viewport too "
                    "(responsive-hidden via @media in the ref capture) — not "
                    "compared here"
                )
            # batch-12 ITEM 6: per-(selector, viewport) live-ref-rendered count +
            # settled styles, when the ref-viewport-visibility artifact carries
            # them — the verdict then expects what the ref RENDERS, not the static
            # scaffold count / viewport-independent style.
            rr_count: int | None = None
            rr_style: dict[str, Any] | None = None
            if ref_rendered is not None and vp is not None:
                rr_entry = ref_rendered.get(selector)
                if isinstance(rr_entry, Mapping):
                    bucket = rr_entry.get(str(vp))
                    if isinstance(bucket, Mapping):
                        rc = bucket.get("count")
                        if isinstance(rc, int):
                            rr_count = rc
                        rs = bucket.get("styles")
                        if isinstance(rs, Mapping):
                            rr_style = dict(rs)
            _compare_bucket(
                selector,
                ref_list,
                impl_by_sel_vp.get((selector, vp), []),
                props,
                rows,
                unmeasured,
                vp,
                ref_expects_here,
                reason,
                ref_rendered_count=rr_count,
                ref_rendered_style=rr_style,
            )

    if requested_selectors is not None:
        present = {e["selector"] for e in ref_entries} | {e["selector"] for e in impl_entries}
        for sel in requested_selectors:
            if sel not in present:
                unmeasured.append(
                    {
                        "selector": sel,
                        "reason": (
                            "selector resolved to no element in the ref scaffold "
                            "or the live impl DOM — masked region could not be "
                            "located (recapture or fix the selector)"
                        ),
                    }
                )

    fail_rows = [r for r in rows if r["status"] == "fail"]
    if fail_rows:
        status = "fail"
    elif any(r["status"] == "ok" for r in rows):
        status = "pass"
    elif unmeasured:
        status = "warn"
    else:
        status = "skip"

    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "status": status,
        "props": list(props),
        "checkedRows": len(rows),
        "failCount": len(fail_rows),
        "rows": rows,
        "unmeasured": unmeasured,
        "rule": (
            "Every dynamic:true masked selector must keep the ref's static "
            "computed styles (default: text-align, justify-content, align-items, "
            "font-family, font-weight, color). The mask absorbs MOTION only — a "
            "static style defect under a mask must still fail."
        ),
    }
    if unmeasured:
        payload["remediation"] = (
            "re-run extraction so dom-scaffold carries the masked element's "
            "computed styles, or correct the dynamic:true selector"
        )
    if fail_rows:
        worst = fail_rows[0]
        payload["diagnostic"] = (
            f"{len(fail_rows)} masked-region static style mismatch(es); first: "
            f"{worst.get('selector')} {worst.get('property', '(element)')} "
            f"ref={worst.get('refValue', worst.get('reason'))} "
            f"impl={worst.get('implValue', 'absent')}. A masked region's static "
            "style differs from the ref — look for missing/incorrect "
            "text-align/justify/font on the masked element's implementation."
        )
    return payload


# ── ref-viewport-visibility producer (tools-batch-11 ITEM 1) ─────────────────
def build_ref_viewport_visibility(
    ref_records: list[dict[str, Any]],
    selectors: list[str],
    captured_viewports: Iterable[int],
    props: list[str] | None = None,
) -> dict[str, Any]:
    """Compute ref-viewport-visibility.json from a LIVE-REF probe.

    The verdict consumes this artifact (``hiddenViewports`` + ``capturedViewports``)
    to decide whether a zero-rendered-visible impl bucket at a viewport is the ref
    legitimately responsive/scroll-hiding the masked selector (excused) versus a
    real "impl element absent" defect. Nothing in the pipeline produced it, so the
    gate stayed permanently fail-closed and false-failed the reference against its
    own ground truth (loop-e2e-12: 24 "impl element absent" rows).

    ``ref_records`` are rich visible-identity records collected from the LIVE REF
    by the SAME probe (scroll sweep + per-selector scrollIntoView + per-viewport
    clientWidth stamp) the verdict's impl probe uses. Visibility is decided with
    the SAME :func:`_impl_visible` predicate the verdict applies to the impl, so
    the recorded hidden set exactly mirrors the zero-visible buckets a live-ref-as-
    impl run would see — giving ref-vs-ref self-pass by construction.

    The hidden set is derived ONLY from the ref, never the impl, so the respbypass
    anti-cheat is preserved: an impl that hides a selector the ref SHOWS at a
    viewport is not in ``hiddenViewports`` and still fails "impl element absent".
    """
    captured = sorted({int(v) for v in captured_viewports})
    visible_by: dict[tuple[str, int], bool] = {}
    rendered_recs: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for rec in ref_records:
        if not isinstance(rec, dict):
            continue
        vp = _viewport_of(rec)
        if vp is None:
            continue
        key = (str(rec.get("selector")), vp)
        if _impl_visible(rec):
            visible_by[key] = True
            rendered_recs.setdefault(key, []).append(rec)
        else:
            visible_by.setdefault(key, False)
    hidden: dict[str, list[int]] = {}
    for sel in selectors:
        sel_hidden = [vp for vp in captured if not visible_by.get((sel, vp), False)]
        if sel_hidden:
            hidden[sel] = sel_hidden
    # batch-12 ITEM 6: per-(selector, viewport) REF-RENDERED evidence — how many
    # instances the live REF actually renders visible, and their SETTLED styles —
    # so the verdict can expect what the ref RENDERS rather than the static
    # dom-scaffold (which over-counts responsive/stacked-state DOM duplicates and
    # carries one viewport-independent style value the live ref varies via @media).
    # Derived ONLY from the live ref (same anti-cheat invariant as hiddenViewports):
    # a clone rendering FEWER instances or WRONG per-viewport styles still fails.
    use_props = props if props is not None else style_props()
    rendered: dict[str, dict[str, Any]] = {}
    for (sel, vp), recs in rendered_recs.items():
        eff, _changing = _effective_styles(recs[0], use_props)
        rendered.setdefault(sel, {})[str(vp)] = {
            "count": len(recs),
            "styles": {p: eff[p] for p in use_props if eff.get(p) is not None},
        }
    return {
        "schemaVersion": 2,
        "capturedViewports": captured,
        "hiddenViewports": hidden,
        "renderedByViewport": rendered,
    }


# ── plan / CLI ───────────────────────────────────────────────────────────────
def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_plan(ref_dir: Path) -> dict[str, Any]:
    spec = _load_json(ref_dir / "transition-spec.json")
    all_selectors = select_masked_selectors(spec) if spec is not None else []
    resolvable, unresolvable = partition_selectors(all_selectors)
    props = style_props()
    scaffold = _load_json(ref_dir / "dom-scaffold.json")
    tree = scaffold.get("tree") if isinstance(scaffold, dict) else None
    ref_entries = (
        ref_entries_from_scaffold(tree, resolvable, props)
        if isinstance(tree, dict)
        else []
    )
    return {
        "props": props,
        "selectors": resolvable,
        "unresolvableSelectors": unresolvable,
        "refEntries": ref_entries,
    }


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "plan":
        print(json.dumps(build_plan(Path(args[1])), indent=2))
        return 0
    if len(args) >= 3 and args[0] == "ref-visibility":
        # tools-batch-11 ITEM 1: turn a LIVE-REF probe (same shape as the impl
        # probe) into ref-viewport-visibility.json so the verdict can excuse the
        # ref's own responsive/scroll hiding. Writing via Path.write_text keeps
        # this exempt from the ad-hoc-ref-write hook (same path the verdict uses
        # for masked-region-static.json).
        ref_dir = Path(args[1])
        records = _load_json(Path(args[2]))
        if not isinstance(records, list):
            records = []
        _plan = build_plan(ref_dir)
        selectors = _plan["selectors"]
        plan_props = _plan["props"]
        captured: list[int]
        if len(args) >= 4 and args[3].strip():
            captured = [int(x) for x in args[3].split(",") if x.strip()]
        else:
            seen: set[int] = set()
            for r in records:
                if not isinstance(r, dict):
                    continue
                vp = _viewport_of(r)
                if vp is not None:
                    seen.add(vp)
            captured = sorted(seen)
        payload = build_ref_viewport_visibility(records, selectors, captured, plan_props)
        (ref_dir / "ref-viewport-visibility.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    if len(args) == 3 and args[0] == "verdict":
        ref_dir, impl_path = Path(args[1]), Path(args[2])
        plan = build_plan(ref_dir)
        impl_entries = _load_json(impl_path)
        if not isinstance(impl_entries, list):
            impl_entries = []
        # batch-9 ITEM 2: per-viewport REF visibility. When the extraction
        # captured the ref across the fan-out viewports it records the masked
        # selectors hidden at each one in ref-viewport-visibility.json
        # ({"hiddenViewports": {"<selector>": [390, ...]}}). Absent that
        # evidence the gate is fail-closed (the ref shows the target at every
        # probed viewport), so an impl that @media-hides ref content fails.
        ref_vp_vis = _load_json(ref_dir / "ref-viewport-visibility.json")
        ref_hidden: dict[str, list[int]] | None = None
        # batch-10 ITEM 4: capturedViewports records the viewport widths the ref
        # capture/fanout actually probed. A probed impl viewport absent from this
        # coverage list is UNMEASURED (no ref evidence), not absent. An
        # empty/garbage list carries no signal, so it falls back to None (fail-
        # closed) rather than marking every viewport unmeasured.
        ref_measured: list[int] | None = None
        if isinstance(ref_vp_vis, dict):
            raw = ref_vp_vis.get("hiddenViewports")
            if isinstance(raw, dict):
                ref_hidden = {
                    str(sel): [int(v) for v in vps if isinstance(v, _NUMBER_TYPES)]
                    for sel, vps in raw.items()
                    if isinstance(vps, list)
                }
            cap = ref_vp_vis.get("capturedViewports")
            if isinstance(cap, list):
                ref_measured = [
                    int(v) for v in cap if isinstance(v, _NUMBER_TYPES)
                ] or None
        # batch-12 ITEM 6: per-(selector, viewport) live-ref-rendered count +
        # settled styles (schemaVersion 2). When present, the verdict expects what
        # the ref RENDERS, not the static dom-scaffold count / viewport-independent
        # style. Absent (legacy artifact / unit-test path) the scaffold is used.
        ref_rendered: dict[str, Any] | None = None
        if isinstance(ref_vp_vis, dict):
            rbv = ref_vp_vis.get("renderedByViewport")
            if isinstance(rbv, dict):
                ref_rendered = rbv
        payload = evaluate(
            plan["refEntries"],
            impl_entries,
            plan["props"],
            requested_selectors=plan["selectors"],
            unresolvable_selectors=plan.get("unresolvableSelectors"),
            ref_hidden_viewports=ref_hidden,
            ref_measured_viewports=ref_measured,
            ref_rendered=ref_rendered,
        )
        if not plan["selectors"] and not plan.get("unresolvableSelectors"):
            payload["status"] = "skip"
            payload["reason"] = "no dynamic:true masked selectors in transition-spec"
        (ref_dir / "masked-region-static.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        return 1 if payload["status"] == "fail" else 0
    print(
        "usage: masked_region_static plan <ref-dir> | verdict <ref-dir> <impl-file>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
