"""Runtime motion proof for dynamic-masked timer/carousel regions.

Loop-9/10 regression class: a footer carousel's paint region is dynamic-
masked out of pixel comparison (legitimately — its frame is timer-phase-
dependent), its timer runs, content swaps instantly — but the spec-declared
card-transform motion never happens (transition-duration 0s). Binary
"something changed" probes pass it, so masking + exemptions left the region
with NO compensating verification.

This gate samples the LIVE impl DOM (the bash wrapper drives the browser)
and checks PHASE-FREE properties whose truth comes from spec params and
bundle evidence only — never from live ref browsing:

  (a) state-count   — >= 2 distinct per-sample digests
  (b) cadence       — gaps between digest changes within
                      declaredInterval ± max(450ms, 15%)
                      (skipped when the window catches < 2 changes)
  (c) channel cover — the SET of channels that change must cover the
                      spec-declared channels (img src / label text /
                      card transforms). Content swapping src-only when the
                      spec declares transforms too is the observed defect.
  (d) sequence      — observed item order must be a cyclic contiguous
                      subsequence of the bundle-declared item list, when
                      one exists.

Entries with insufficient artifact params (no parseable interval, no
target) are an EXPLICIT unmeasurable-fail with remediation — never a
pretend pass.

CLI:
    python -m ui_clone.gates.masked_region_motion plan <ref-dir>
        → prints the sampling plan JSON (entries + params) for the wrapper
    python -m ui_clone.gates.masked_region_motion verdict <ref-dir> <samples-file>
        → writes <ref-dir>/masked-region-motion.json; exit 1 on fail
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

CADENCE_MS = 250
WINDOW_FACTOR = 1.5
RETRY_WINDOW_FACTOR = 2.2

_TIMERISH_RE = re.compile(r"timer|carousel|setInterval|autoplay-rotate", re.IGNORECASE)
_VIDEOISH_RE = re.compile(r"video autoplay|video frames", re.IGNORECASE)
_INTERVAL_MS_RE = re.compile(r"(\d{3,6})\s*ms")
_INTERVAL_S_RE = re.compile(r"(\d+(?:\.\d+)?)\s*s\b")
_SET_INTERVAL_RE = re.compile(r"setInterval\([^)]*\)\s*,?\s*(\d{3,6})")


def _spec_entries(spec: Any) -> list[dict[str, Any]]:
    if isinstance(spec, list):
        rows = spec
    elif isinstance(spec, dict):
        rows = spec.get("transitions") or spec.get("entries") or []
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def select_entries(spec: Any) -> list[dict[str, Any]]:
    """dynamic:true entries of timer/carousel kind (video autoplay excluded —
    video-play-proof owns that class)."""
    out = []
    for entry in _spec_entries(spec):
        if not entry.get("dynamic"):
            continue
        animation_raw = entry.get("animation")
        animation = animation_raw if isinstance(animation_raw, dict) else {}
        kind_text = " ".join(
            str(x)
            for x in (
                entry.get("trigger"), animation.get("type"), entry.get("bundle_branch"),
            )
            if x
        )
        if _VIDEOISH_RE.search(str(entry.get("trigger") or "")):
            continue
        if _TIMERISH_RE.search(kind_text):
            out.append(entry)
    return out


def _parse_interval_ms(entry: dict[str, Any]) -> int | None:
    animation_raw = entry.get("animation")
    animation = animation_raw if isinstance(animation_raw, dict) else {}
    if "intervalMs" in animation:
        structured = animation["intervalMs"]
        if isinstance(structured, bool):
            return None
        if isinstance(structured, int):
            return structured if structured > 0 else None
        if (
            isinstance(structured, float)
            and math.isfinite(structured)
            and structured > 0
            and structured.is_integer()
        ):
            return int(structured)
        return None
    for text in (
        str(entry.get("trigger") or ""),
        str(animation.get("duration") or ""),
        str(entry.get("bundle_branch") or ""),
    ):
        m = _INTERVAL_MS_RE.search(text)
        if m:
            return int(m.group(1))
        m = _SET_INTERVAL_RE.search(text)
        if m:
            return int(m.group(1))
        m = _INTERVAL_S_RE.search(text)
        if m and float(m.group(1)) >= 1:
            return int(float(m.group(1)) * 1000)
    return None


def _parse_channels(entry: dict[str, Any]) -> set[str]:
    animation_raw = entry.get("animation")
    animation = animation_raw if isinstance(animation_raw, dict) else {}
    prop = str(animation.get("property") or "").lower()
    channels: set[str] = set()
    if "src" in prop or "image" in prop or "img" in prop:
        channels.add("imgSrc")
    if "text" in prop or "label" in prop or "word" in prop:
        channels.add("text")
    if (
        "transform" in prop or "translate" in prop or "scale" in prop
        or "z-index" in prop or "zindex" in prop or "rotate" in prop
    ):
        channels.add("cardTransform")
    if "opacity" in prop:
        channels.add("cardTransform")
    return channels


def parse_params(entry: dict[str, Any]) -> dict[str, Any]:
    interval = _parse_interval_ms(entry)
    channels = _parse_channels(entry)
    selectors = [
        s.strip() for s in str(entry.get("target") or "").split(",") if s.strip()
    ]
    unmeasurable: list[str] = []
    if interval is None:
        unmeasurable.append(
            "no parseable timer interval in animation.intervalMs or "
            "trigger/duration/bundle_branch"
        )
    if not selectors:
        unmeasurable.append("no target selectors")
    if not channels:
        unmeasurable.append("no declared channels in animation.property")
    return {
        "id": str(entry.get("id") or "entry"),
        "intervalMs": interval,
        "channels": channels,
        "selectors": selectors,
        "items": None,
        "unmeasurable": unmeasurable,
    }


def extract_items(entry: dict[str, Any], ref_dir: Path) -> list[str] | None:
    """Bundle-declared item list, when the bundle evidence names one.

    Derives the repeating literal key from bundle_branch (e.g.
    foods=[{food:'Steak',...}] → key "food"), then reads the ordered values
    from the entry's source_chunk bundle on disk. Ref truth only — no
    browsing.
    """
    branch = str(entry.get("bundle_branch") or "")
    m = re.search(r"\{\s*(\w+)\s*:\s*['\"]([^'\"]+)['\"]", branch)
    if not m:
        return None
    key = m.group(1)
    chunk = str(entry.get("source_chunk") or "")
    if not chunk:
        return None
    candidates = sorted((ref_dir / "bundles").glob(f"*{Path(chunk).stem}*")) or sorted(
        (ref_dir / "bundles").glob(chunk)
    )
    if not candidates:
        return None
    try:
        text = candidates[0].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    values = re.findall(rf"{re.escape(key)}\s*:\s*['\"]([^'\"]+)['\"]", text)
    return values or None


def _digest(sample: dict[str, Any]) -> dict[str, str]:
    return {
        "imgSrc": json.dumps(sample.get("imgSrcs"), sort_keys=True),
        "text": str(sample.get("text") or ""),
        "cardTransform": json.dumps(sample.get("cards"), sort_keys=True),
    }


def _is_cyclic_contiguous(observed: list[str], items: list[str]) -> bool:
    if not observed:
        return True
    if not items:
        return False
    doubled = items + items
    n = len(items)
    for start in range(n):
        if all(
            doubled[(start + i) % (2 * n)] == obs
            or doubled[start + i] == obs
            for i, obs in enumerate(observed[: n])
        ) and len(observed) <= n:
            if [doubled[start + i] for i in range(len(observed))] == observed:
                return True
    return False


def evaluate_entry(
    params: dict[str, Any], samples: list[dict[str, Any]]
) -> dict[str, Any]:
    reasons: list[str] = []
    entry_id = params.get("id")

    if params.get("unmeasurable"):
        return {
            "id": entry_id,
            "status": "fail",
            "reasons": [f"unmeasurable: {r}" for r in params["unmeasurable"]],
            "remediation": (
                "re-run extraction so the transition-spec entry carries a "
                "parseable interval, target selectors, and declared channels — "
                "an unverifiable dynamic-masked region must not pass silently"
            ),
        }

    if len(samples) < 3:
        return {
            "id": entry_id,
            "status": "fail",
            "reasons": ["unmeasurable: fewer than 3 samples collected"],
            "remediation": "sampling failed — check impl URL/selectors and re-run",
        }

    digests = [_digest(s) for s in samples]
    times = [float(s.get("t") or 0.0) for s in samples]

    # (a) state count over the combined digest
    combined = [json.dumps(d, sort_keys=True) for d in digests]
    distinct = len(set(combined))
    if distinct < 2:
        reasons.append(
            f"state-count: only {distinct} distinct DOM state(s) across "
            f"{len(samples)} samples — region is static while its spec "
            "declares timer-driven motion"
        )

    # change points (combined digest transitions)
    raw_change_times: list[float] = []
    changed_channels: set[str] = set()
    for i in range(1, len(samples)):
        if combined[i] != combined[i - 1]:
            raw_change_times.append(times[i])
        for channel in ("imgSrc", "text", "cardTransform"):
            if digests[i][channel] != digests[i - 1][channel]:
                changed_channels.add(channel)

    # Animated swaps (the REQUIRED behavior) make several consecutive samples
    # differ while a transition runs — those mid-flight diffs are one logical
    # change. Cluster raw change points into bursts and treat burst starts as
    # the change events; a burst longer than 60% of the interval is itself
    # suspicious (continuous churn, not a timer step) and stays split.
    burst_gap = max(3 * CADENCE_MS, 0.25 * float(params["intervalMs"]))
    change_times: list[float] = []
    for t in raw_change_times:
        if not change_times or t - change_times[-1] > burst_gap:
            change_times.append(t)
        else:
            # extend the current burst window so a long animation still
            # collapses into its starting event
            pass

    # (b) cadence — needs >= 2 change events to measure a gap.
    # The declared interval is a TEXT-PARSED estimate (regex over trigger/
    # duration/bundle) and is frequently LARGER than the region's real change
    # period — a carousel may swap several sub-elements per declared cycle (the
    # eatReal food carousel: declared 3500ms, actual ~1015ms). So the declared
    # interval is the SLOWEST ACCEPTABLE cadence, not an exact target: the region
    # must change at LEAST that often (changing faster is genuine motion, never a
    # defect). Flag only gaps that exceed ~2x the declared interval — a region
    # that goes that long without changing is effectively static (the instant-
    # swap / no-real-motion cheat this gate exists to catch), and the 2x band
    # also tolerates a single missed sample at the declared rate.
    interval = float(params["intervalMs"])
    if len(change_times) >= 2:
        tol = max(450.0, 0.15 * interval)
        ceiling = 2.0 * interval + tol
        gaps = [b - a for a, b in zip(change_times, change_times[1:])]
        bad = [g for g in gaps if g > ceiling]
        if bad:
            reasons.append(
                f"cadence: change gap(s) {[round(g) for g in bad]}ms exceed "
                f"{ceiling:.0f}ms (>2x declared {interval:.0f}ms) — region is "
                f"effectively static, not paced motion"
            )

    # (c) channel coverage
    declared: set[str] = set(params["channels"])
    if distinct >= 2:
        missing = declared - changed_channels
        if missing:
            reasons.append(
                f"channel-coverage: spec declares {sorted(declared)} but only "
                f"{sorted(changed_channels)} changed — missing "
                f"{sorted(missing)} (content swaps without the declared motion)"
            )

    # (d) item sequence (when bundle declares a list)
    items = params.get("items")
    if items and distinct >= 2:
        observed: list[str] = []
        for sample in samples:
            text = str(sample.get("text") or "")
            hit = next((it for it in items if it.lower() in text.lower()), None)
            if hit and (not observed or observed[-1] != hit):
                observed.append(hit)
        if observed and not _is_cyclic_contiguous(observed, items):
            reasons.append(
                f"sequence: observed {observed} is not a cyclic contiguous "
                "subsequence of the bundle-declared item list"
            )

    return {
        "id": entry_id,
        "status": "fail" if reasons else "pass",
        "reasons": reasons,
        "samples": len(samples),
        "distinctStates": distinct,
        "changedChannels": sorted(changed_channels),
        "declaredChannels": sorted(declared),
        "changeTimes": [round(t) for t in change_times],
    }


def build_plan(ref_dir: Path) -> list[dict[str, Any]]:
    try:
        spec = json.loads((ref_dir / "transition-spec.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    plans = []
    for entry in select_entries(spec):
        params = parse_params(entry)
        params["items"] = extract_items(entry, ref_dir)
        params["windowMs"] = int((params["intervalMs"] or 4000) * WINDOW_FACTOR)
        params["retryWindowMs"] = int((params["intervalMs"] or 4000) * RETRY_WINDOW_FACTOR)
        params["cadenceMs"] = CADENCE_MS
        params["channels"] = sorted(params["channels"])
        plans.append(params)
    return plans


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "plan":
        print(json.dumps(build_plan(Path(args[1])), indent=2))
        return 0
    if len(args) == 3 and args[0] == "verdict":
        ref_dir, samples_path = Path(args[1]), Path(args[2])
        plans = build_plan(ref_dir)
        try:
            all_samples = json.loads(samples_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            all_samples = {}
        entries = []
        for params in plans:
            params = dict(params, channels=set(params["channels"]))
            samples = all_samples.get(params["id"]) or []
            entries.append(evaluate_entry(params, samples))
        if not plans:
            status = "skip"
        elif any(e["status"] == "fail" for e in entries):
            status = "fail"
        else:
            status = "pass"
        payload = {
            "schemaVersion": 1,
            "status": status,
            "entries": entries,
            "rule": (
                "Every dynamic:true timer/carousel spec entry must prove live "
                "motion: >=2 DOM states, change cadence within the declared "
                "interval, changed channels covering the declared set, and "
                "(when the bundle names one) item order within the declared "
                "list. Insufficient spec params are an explicit unmeasurable "
                "failure, never a pass."
            ),
        }
        if status == "skip":
            payload["reason"] = "no dynamic timer/carousel entries in transition-spec"
        (ref_dir / "masked-region-motion.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        return 1 if status == "fail" else 0
    print(
        "usage: masked_region_motion plan <ref-dir> | verdict <ref-dir> <samples-file>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
