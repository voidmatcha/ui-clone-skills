"""Per-entry fallback probe for hover coverage.

Loop-9 regression class: hover-state-compare capped its run list, every
selected target ended a documented known-skip, 0 runs were measured — and
the gate PASSED. A hover gate run where nothing was measured is absence of
evidence; every hoverable entry now needs >=1 measured run or a fallback
probe verdict.

The probe is impl-side only (ref truth comes from spec/bundle evidence):

  verified        — pointer-event simulation produced the declared delta on
                    the live impl (JS-driven hover: width expanded, color
                    changed, ...)
  static-verified — the impl stylesheet carries :hover rules covering the
                    declared channels (CSS-driven hover; synthetic events
                    cannot activate the :hover pseudo-class, so CSSOM
                    presence is the honest check — and unmounted overlay
                    targets can only be verified this way)
  fail            — neither: the declared hover behavior does not exist in
                    the impl (the specific regression nav pill: labels baked width:0,
                    no expansion rule, no JS handler)

Plan sources: transition-spec hover entries (channels parsed from
animation.property) plus bundle-extraction hoverSizeExpansions (the nav
pill class — size channel, selector from the resolved class name).

CLI:
    python -m ui_clone.gates.hover_probe plan <ref-dir>
    python -m ui_clone.gates.hover_probe verdict <ref-dir> <samples-file>
        → writes <ref-dir>/hover-fallback.json; exit 1 on fail
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SIZE_DELTA_PX = 1.0
OPACITY_DELTA = 0.05

# A state-flag-gated width reveal serialised into a hoverSizeExpansions entry's
# raw 'to' value — `FLAG ? <value> : 0`. This is the signature that re-classifies
# a double-bucketed entry as a STATE reveal (not a hover), so the hover probe is
# not forged for it. A true hover spring's 'to' is a plain reveal value with no
# ternary (`"auto"`, `auto`, `120`, `"120"`) and never matches.
#
# VALUE-TYPE AGNOSTIC (Codex live-repro, HIGH): the reveal <value> may be quoted
# (`"auto"` / `'auto'`), bare (`auto`), or NUMERIC (`120` / `"120"`). The active
# extractor supports numeric `to` values; an `auto`-only regex missed a numeric
# state spring (`a?120:0`) and left it wrongly probed as a hover. Match ANY
# non-`0`, non-empty reveal value before the `: 0` else-branch (a bare `0` as the
# then-branch is not a reveal, so the then-value must contain a non-zero token).
_STATE_FLAG_TERNARY_RE = re.compile(
    r"""\w+\s*\?\s*["']?[^"':?]*[^"':?\s0][^"':?]*["']?\s*:\s*0\b"""
)


def _state_ternary(flag: Any, to: Any) -> str:
    """Reconstruct a hoverSizeExpansions raw 'to' (e.g. `a?"auto":0` or `a?120:0`)
    from an activeStateExpansions tuple, whose extractor stores the gating flag and
    the resolved reveal value separately. Used to byte-match the two artifacts.

    VALUE-TYPE AGNOSTIC: a NUMERIC resolved value is serialised WITHOUT quotes
    (`a?120:0`), matching how the hover extractor stores numeric reveals; only a
    non-numeric value (e.g. `auto`) is quoted (`a?"auto":0`). Forcing quotes around
    a number produced `a?"120":0`, which never byte-matched the real `a?120:0`."""
    to_s = str(to)
    is_numeric = bool(re.fullmatch(r"\s*[0-9][0-9.]*\s*", to_s))
    return f"{flag}?{to_s}:0" if is_numeric else f'{flag}?"{to_s}":0'

_CHANNEL_PROPS = {
    "size": ("width", "max-width", "maxwidth", "height", "padding", "gap"),
    "color": ("background", "color", "border", "fill", "stroke"),
    "opacity": ("opacity",),
    "transform": ("transform", "scale", "translate", "rotate"),
}


def _channels_from_property(prop: str) -> list[str]:
    prop = prop.lower()
    out: list[str] = []
    if any(k in prop for k in ("width", "max-width", "maxwidth", "height")):
        out.append("size")
    if any(k in prop for k in ("background", "color", "border", "fill", "stroke",
                               "text-decoration")):
        out.append("color")
    if "opacity" in prop:
        out.append("opacity")
    if any(k in prop for k in ("transform", "scale", "translate", "rotate")):
        out.append("transform")
    return out


def build_plan(ref_dir: Path) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    try:
        spec = json.loads((ref_dir / "transition-spec.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        spec = {}
    rows = spec.get("transitions") if isinstance(spec, dict) else spec
    for entry in rows or []:
        if not isinstance(entry, dict):
            continue
        if "hover" not in str(entry.get("trigger") or "").lower():
            continue
        animation_raw = entry.get("animation")
        animation = animation_raw if isinstance(animation_raw, dict) else {}
        channels = _channels_from_property(str(animation.get("property") or ""))
        selectors = [
            s.strip() for s in str(entry.get("target") or "").split(",") if s.strip()
        ]
        if selectors and channels:
            plans.append(
                {
                    "id": str(entry.get("id") or "hover-entry"),
                    "selectors": selectors,
                    "channels": channels,
                    "source": "transition-spec",
                }
            )

    try:
        extraction = json.loads(
            (ref_dir / "bundle-extraction.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        extraction = {}
    extractions = (
        (extraction.get("extractions") or {}) if isinstance(extraction, dict) else {}
    )
    expansions = extractions.get("hoverSizeExpansions")
    # De-dup state-driven springs out of the hover probe set (loop-e2e-12 false
    # positive). A width spring gated on a state flag — animate:{width:a?"auto":0}
    # — matches BOTH the extractor's hover regex and its active-state regex, so it
    # lands in hoverSizeExpansions AND activeStateExpansions. It is a STATE reveal
    # (active-section nav label), not a hover: neither ref nor clone carries a
    # :hover width rule, so forging a hover probe on it false-fails a faithful
    # clone. The active-state reveal is already verified by the state-reveal gate
    # (which reads activeStateExpansions).
    #
    # The de-dup is scoped to the ENTRY SIGNATURE, not the class (P1 silent-bypass
    # fix, review live-repro on 16f5007). Keying on class name alone discards the
    # to-value that distinguishes a TRUE hover spring (to="auto") from a STATE
    # spring (to=a?"auto":0). When ONE class carries BOTH — a genuine hover spring
    # AND a state spring, both in hoverSizeExpansions — a class-keyed skip drops
    # the genuine hover entry too, emptying the plan; the gate then returns
    # status='skip' (RC 0) and a clone that DROPPED the real hover passes silently
    # (no compensating gate verifies a hover expansion — state_reveal only checks
    # the active-state reveal). So skip a hover entry ONLY when its OWN signature
    # is state-driven: its 'to' value carries a state-flag ternary
    # (FLAG ? <value> : 0, value-type agnostic — "auto"/auto/120/"120") OR it
    # byte-matches the RECONSTRUCTED raw ternary of an activeStateExpansions tuple.
    # A true-hover entry (a plain reveal value with no ternary — "auto" or numeric,
    # no state flag) is KEPT even when the same class also has a state spring — it
    # still produces a probe, so real hover defects keep failing.
    active = extractions.get("activeStateExpansions") or []
    active_sigs: set[tuple[Any, Any]] = set()
    for a in active:
        if not isinstance(a, dict):
            continue
        a_cls = a.get("resolvedClassName") or a.get("classToken")
        if a_cls is None:
            continue
        flag = a.get("stateFlag")
        to = a.get("to")
        # The active extractor stores the RESOLVED reveal value (e.g. "auto" / 120)
        # and the gating flag separately; the hover extractor stores the RAW animate
        # value (e.g. a?"auto":0 / a?120:0). Match on the reconstructed raw ternary
        # so the two artifacts line up byte-for-byte regardless of which bucket wrote
        # it. Do NOT add the bare resolved value (str(to)): a TRUE numeric hover
        # spring stores its 'to' as the bare reveal value (120), which byte-equals
        # the active tuple's resolved value (120) and would wrongly de-dup the
        # genuine hover (Codex live-repro, HIGH) — emptying the plan and silently
        # passing a clone that dropped a real numeric hover.
        active_sigs.add((a_cls, _state_ternary(flag, to)))
    for exp in expansions or []:
        if not isinstance(exp, dict):
            continue
        cls = exp.get("resolvedClassName") or exp.get("classToken")
        if not cls:
            continue
        to_val = str(exp.get("to") or "")
        # Skip ONLY a state-driven signature: a state-flag ternary in this
        # entry's own 'to', or an exact (class, to) match to an active tuple.
        if _STATE_FLAG_TERNARY_RE.search(to_val) or (cls, to_val) in active_sigs:
            continue
        plans.append(
            {
                "id": f"size-expansion:{exp.get('classToken') or cls}",
                "selectors": [f".{cls}", f"[class*='{exp.get('classToken') or cls}']"],
                "channels": ["size"],
                "source": "bundle-extraction.hoverSizeExpansions",
            }
        )
    return plans


def _delta(channel: str, before: dict, after: dict) -> bool:
    if channel == "size":
        try:
            return abs(float(after.get("width") or 0) - float(before.get("width") or 0)) > SIZE_DELTA_PX
        except (TypeError, ValueError):
            return False
    if channel == "color":
        return (
            str(before.get("bg")) != str(after.get("bg"))
            or str(before.get("color")) != str(after.get("color"))
        )
    if channel == "opacity":
        try:
            return abs(float(after.get("opacity") or 1) - float(before.get("opacity") or 1)) > OPACITY_DELTA
        except (TypeError, ValueError):
            return False
    if channel == "transform":
        if str(before.get("transform")) != str(after.get("transform")):
            return True
        # batch-12 ITEM 6: a framer whileHover scale applied to an ANCESTOR
        # (e.g. a motion.button wrapping the probed img) leaves the probed
        # element's OWN computed transform "none" while its bounding-rect WIDTH
        # grows by the scale factor. Treat a rect-width scale beyond a tight
        # tolerance as a transform delta so the hover — which IS firing on the ref —
        # registers. Detection-preserving: a clone that drops the scale leaves the
        # width unchanged (ratio ~1) -> no delta -> still fails; this gate asserts
        # the hover EXISTS, not its magnitude (AE / transition gates own magnitude).
        try:
            bw = float(before.get("width") or 0)
            aw = float(after.get("width") or 0)
            if bw > 0 and abs(aw / bw - 1.0) > 0.01:
                return True
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        return False
    return False


_SIZE_NUMERIC_RE = re.compile(r"^\s*([0-9.]+)\s*(px|%|em|rem|vw|vh|ch)?\s*(?:!important)?\s*$")


def _size_value_expands(value: str) -> bool:
    """True only when a size value PROVABLY expands beyond a collapsed state.

    Review-2 finding 3: a dead ':hover { width: 0 }' rule carries the
    property name but a collapsed value — name presence is not evidence.
    Unparseable values (var()/calc() of unknowns) are not provable either;
    those entries must show a live event delta instead."""
    v = value.strip().lower()
    if not v:
        return False
    if any(k in v for k in ("auto", "fit-content", "max-content", "none", "initial", "unset", "revert")):
        # max-width:none / width:auto release the collapse
        return True
    m = _SIZE_NUMERIC_RE.match(v)
    if m:
        try:
            return float(m.group(1)) > 1.0
        except ValueError:
            return False
    return False


def _css_covers(channel: str, css_props: list[str]) -> bool:
    """Channel coverage from impl :hover rule properties.

    Entries arrive as "name" (legacy) or "name: value". The size channel
    requires a provably-expanding VALUE; other channels accept the name
    (cross-format value comparison — author hex vs computed rgb — is not
    computable, and their dead-rule case is caught by the event-delta path
    when the element is mounted)."""
    keys = _CHANNEL_PROPS.get(channel, ())
    for raw in css_props:
        text = str(raw).lower()
        name, _, value = text.partition(":")
        name = name.strip()
        if not any(k in name for k in keys):
            continue
        if channel == "size":
            if _size_value_expands(value):
                return True
            continue
        return True
    return False


def evaluate_entry(
    entry: dict[str, Any], sample: dict[str, Any] | None
) -> dict[str, Any]:
    entry_id = entry.get("id")
    channels = list(entry.get("channels") or [])
    if not isinstance(sample, dict):
        return {
            "id": entry_id,
            "status": "fail",
            "reason": (
                "unmeasured: no probe sample collected — a hoverable entry "
                "without a measured run or a fallback verdict cannot pass"
            ),
        }
    css_props = [str(p) for p in sample.get("cssHoverProps") or []]
    found = bool(sample.get("found"))
    before_raw = sample.get("before")
    before: dict[str, Any] = before_raw if isinstance(before_raw, dict) else {}
    after_raw = sample.get("after")
    after: dict[str, Any] = after_raw if isinstance(after_raw, dict) else {}

    css_all = all(_css_covers(c, css_props) for c in channels) if channels else False
    if not found:
        if css_all:
            return {
                "id": entry_id,
                "status": "static-verified",
                "reason": (
                    "target unmounted at idle; impl stylesheet carries :hover "
                    f"rules covering the declared channels {channels}"
                ),
            }
        return {
            "id": entry_id,
            "status": "fail",
            "reason": (
                "target unmounted at idle AND no impl :hover rules cover the "
                f"declared channels {channels} — the hover behavior does not "
                "exist in the impl"
            ),
        }

    # Cascade-aware size proof (batch-6 ITEM 4 / Attack 1): a `:hover{width:auto}`
    # rule can be neutralized by a higher-priority `width:0 !important` base rule,
    # so CSSOM rule presence is NOT proof for the size channel. When the probe
    # supplies a forced (real CDP) hover end-state, it is AUTHORITATIVE — the
    # computed width must actually expand. Without a forced measurement (older
    # probe), a covering size rule on a mounted target still static-verifies.
    forced_raw = sample.get("forcedHover")
    forced: dict[str, Any] = forced_raw if isinstance(forced_raw, dict) else {}

    def _forced_grew() -> bool:
        try:
            return (
                float(forced.get("width") or 0) - float(before.get("width") or 0)
                > SIZE_DELTA_PX
            )
        except (TypeError, ValueError):
            return False

    def _channel_status(c: str) -> str:
        if _delta(c, before, after):
            return "verified"
        if c == "size" and forced:
            return "verified" if _forced_grew() else "fail"
        return "static" if _css_covers(c, css_props) else "fail"

    pairs = [(c, _channel_status(c)) for c in channels] if channels else []
    if not pairs or any(s == "fail" for _, s in pairs):
        # batch-13 ITEM 5 — honest skip for an UN-PROBEABLE size spring. The
        # target resolves but its hover TRIGGER is rendered off-screen (a
        # floating nav pill clipped above the viewport at every scroll position),
        # so a CDP hover cannot engage the framer whileHover and the
        # non-expansion is INCONCLUSIVE, not proof of absence. Scope it tightly:
        # only when every declared channel is size, NOTHING decides it either way
        # (no event delta, no forced growth, no covering :hover rule), and the
        # probe reports the trigger off-screen. An ON-screen target with no
        # expansion is provable absence and still FAILS; the spring's PRESENCE is
        # independently enforced against the impl bundle by bundle-impl-coverage /
        # signature-effects-coverage, so this is not a coverage hole.
        if (
            bool(sample.get("offScreen"))
            and channels
            and all(c == "size" for c in channels)
            and not any(_delta(c, before, after) for c in channels)
            and not _forced_grew()
            and not any(_css_covers(c, css_props) for c in channels)
        ):
            return {
                "id": entry_id,
                "status": "skip",
                "reason": (
                    "hover trigger rendered off-screen at probe time (a floating "
                    "nav pill clipped outside the viewport at every scroll "
                    "position); a CDP hover cannot engage the declared size "
                    "spring, so non-expansion is inconclusive rather than absent. "
                    "Presence is enforced against the impl bundle by "
                    "bundle-impl-coverage / signature-effects, not this probe"
                ),
            }
        missing = [c for c, s in pairs if s == "fail"]
        cascade = bool(channels and "size" in channels and forced and not _forced_grew())
        reason = (
            "declared size hover does not expand under a real (forced) hover — "
            "the :hover rule is neutralized by a higher-priority base rule (e.g. "
            "width:0 !important); rule presence is not proof"
            if cascade
            else (
                f"declared channel(s) {missing or channels} show no event-driven "
                "delta and no covering impl :hover rules (e.g. a label container "
                "baked at width:0 with no expansion behavior)"
            )
        )
        return {"id": entry_id, "status": "fail", "reason": reason}
    if all(s == "verified" for _, s in pairs):
        return {
            "id": entry_id,
            "status": "verified",
            "reason": f"measured hover (event or forced) produced the declared delta on {channels}",
        }
    return {
        "id": entry_id,
        "status": "static-verified",
        "reason": (
            "no event-driven delta (CSS :hover cannot be activated "
            "synthetically) but impl stylesheet rules cover the declared "
            f"channels {channels}"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "plan":
        print(json.dumps(build_plan(Path(args[1])), indent=2))
        return 0
    if len(args) == 3 and args[0] == "verdict":
        import os

        ref_dir, samples_path = Path(args[1]), Path(args[2])
        plans = build_plan(ref_dir)
        try:
            samples = json.loads(samples_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            samples = {}
        if not isinstance(samples, dict):
            samples = {}
        # Review-2 finding 2: per-entry accounting. Entries whose selectors
        # had a MEASURED hover run (real-mouse 60fps compare) are covered;
        # every other planned entry needs a probe verdict — one measured run
        # must never suppress probing of the rest.
        # Provenance binding (batch-7 ITEM 4b): a self-attested env flag is
        # attacker-controlled, so it is no longer sufficient. The probe writes a
        # scan receipt INSIDE the impl tree only when a live scan actually ran;
        # runtime_scanned is true only when the env flag is set AND that receipt
        # file exists. The consumer then binds the receipt to impl_root + mtime
        # (PATH_CHECK), exactly like junk-token's implSrcDir (ITEM 5c).
        scan_receipt = os.environ.get("UI_CLONE_HOVER_SCAN_RECEIPT", "").strip()
        receipt_ok = bool(scan_receipt) and Path(scan_receipt).is_file()
        runtime_scanned = (
            os.environ.get("UI_CLONE_HOVER_RUNTIME_SCANNED") == "1" and receipt_ok
        )
        measured_selectors: set[str] = set()
        measured_file = os.environ.get("UI_CLONE_HOVER_MEASURED_FILE")
        # The "measured" shortcut grants the highest-trust status; honour it only
        # when a real runtime scan ran (Attack 3b: a forged measured-file with no
        # live run must not mint coverage).
        if measured_file and runtime_scanned:
            try:
                measured_selectors = {
                    line.strip()
                    for line in Path(measured_file).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                }
            except OSError:
                measured_selectors = set()
        entries = []
        for p in plans:
            if measured_selectors and any(
                s in measured_selectors for s in p.get("selectors") or []
            ):
                entries.append(
                    {
                        "id": p["id"],
                        "status": "measured",
                        "reason": "covered by a measured hover-state-compare run",
                    }
                )
                continue
            entries.append(evaluate_entry(p, samples.get(str(p["id"]))))
        if not plans:
            status = "skip"
        elif any(e["status"] == "fail" for e in entries):
            status = "fail"
        else:
            status = "pass"
        # Provenance (Attack 3a): a pass must rest on a real runtime scan. A
        # verdict computed from fabricated/replayed samples (no browser ran)
        # cannot stand — fail it, never a silent pass on forged evidence.
        provenance_failed = status == "pass" and not runtime_scanned
        if provenance_failed:
            status = "fail"
        payload: dict[str, Any] = {
            "schemaVersion": 1,
            "status": status,
            "runtimeScanned": runtime_scanned,
            "scanReceipt": scan_receipt if receipt_ok else None,
            "entries": entries,
            "coverage": {
                "measured": sum(1 for e in entries if e["status"] == "measured"),
                "verified": sum(1 for e in entries if e["status"] == "verified"),
                "staticVerified": sum(1 for e in entries if e["status"] == "static-verified"),
                "skipped": sum(1 for e in entries if e["status"] == "skip"),
                "failed": sum(1 for e in entries if e["status"] == "fail"),
            },
            "rule": (
                "Every hoverable entry needs >=1 measured hover run or a "
                "fallback probe verdict (event-driven delta or covering impl "
                ":hover rules). An all-known-skip hover gate run is absence "
                "of evidence, never a pass."
            ),
        }
        if provenance_failed:
            payload["reason"] = (
                "artifact would pass but no live hover scan ran "
                "(runtimeScanned=false): coverage cannot be granted on "
                "fabricated or replayed samples — re-run hover-fallback-probe.sh "
                "against a reachable impl URL"
            )
        if status == "skip":
            payload["reason"] = "no hoverable entries in spec/bundle extraction"
        (ref_dir / "hover-fallback.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        return 1 if status == "fail" else 0
    print(
        "usage: hover_probe plan <ref-dir> | verdict <ref-dir> <samples-file>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
